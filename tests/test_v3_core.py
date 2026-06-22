import os
import unittest
from pathlib import Path
from unittest.mock import patch

from weatherbot_v3.ai_review import AIReviewer
from weatherbot_v3.db import connect, init_v3_db
from weatherbot_v3.executor import PaperExecutor
from weatherbot_v3.polymarket import quote_from_market_payload, validate_order_constraints
from dashboard_server import _augment_strategy_replay_record, _bucket_probability_f, _bucket_value_in_range, _bulk_simulation_skip_reason, _build_policy_candidates, _entry_snapshot_features, _fit_trade_readiness, _live_gate, _metric_summary
from bot_v2 import bucket_prob, calibrated_bucket_probability, calibration_metric


TEST_DB_DIR = Path(__file__).resolve().parents[1] / ".tmp-tests"


def test_db_path(name: str) -> Path:
    TEST_DB_DIR.mkdir(exist_ok=True)
    path = TEST_DB_DIR / f"{name}.db"
    path.unlink(missing_ok=True)
    return path


class V3CoreTests(unittest.TestCase):
    def test_quote_uses_best_bid_ask_and_constraints(self):
        quote = quote_from_market_payload({
            "id": "1",
            "outcomePrices": '["0.20", "0.80"]',
            "bestBid": "0.19",
            "bestAsk": "0.21",
            "spread": "0.02",
            "volume": "1000",
            "orderMinSize": "5",
            "orderPriceMinTickSize": "0.01",
            "enableOrderBook": True,
            "clobTokenIds": '["yes", "no"]',
        })
        self.assertEqual(quote.best_bid, 0.19)
        self.assertEqual(quote.best_ask, 0.21)
        self.assertEqual(validate_order_constraints(quote, 5.0, 0.21), [])
        self.assertIn("below_order_min_size", validate_order_constraints(quote, 1.0, 0.21))

    def test_ai_disabled_default_allows_quant_flow(self):
        db_path = test_db_path("ai_disabled")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"AI_REVIEW_ENABLED": "false", "V3_DB_PATH": str(db_path)}, clear=False):
            review = AIReviewer().review(0, {"market_id": "1"})
        self.assertTrue(review["approve"])
        self.assertEqual(review["provider"], "none")

    def test_v3_db_schema_initializes(self):
        db_path = test_db_path("schema")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        init_v3_db(db_path)
        with connect(db_path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("signals", tables)
        self.assertIn("paper_orders", tables)
        self.assertIn("live_orders", tables)

    def test_paper_executor_rejects_bad_orderbook(self):
        db_path = test_db_path("paper_reject")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            signal = {"id": 1, "market_id": "1", "amount": 1.0, "limit_price": 0.21, "created_at": "now"}
            quote = quote_from_market_payload({
                "id": "1",
                "outcomePrices": '["0.20", "0.80"]',
                "bestBid": "0.19",
                "bestAsk": "0.21",
                "spread": "0.20",
                "volume": "1000",
                "orderMinSize": "5",
                "orderPriceMinTickSize": "0.01",
                "enableOrderBook": True,
                "clobTokenIds": '["yes", "no"]',
            })
            with patch("weatherbot_v3.executor.PolymarketDataClient") as client_cls:
                client_cls.return_value.quote.return_value = quote
                result = PaperExecutor().place_order(signal, 1.0)
        self.assertFalse(result.ok)
        self.assertIn("spread_above_max_slippage", result.reason)

    def test_near_lock_replay_detects_metar_gap(self):
        market = {
            "unit": "F",
            "created_at": "2026-06-16T07:00:00+00:00",
            "forecast_snapshots": [
                {
                    "ts": "2026-06-16T09:00:00+00:00",
                    "horizon": "D+0",
                    "hours_left": 3.0,
                    "best": 70,
                    "metar": 64,
                    "ensemble_std": 1.0,
                }
            ],
        }
        item = {
            "opened_at": "2026-06-16T09:10:00+00:00",
            "bucket_low": 70,
            "bucket_high": 71,
            "forecast_temp": 70,
        }
        features = _entry_snapshot_features(market, item)
        self.assertTrue(features["near_lock_8h"])
        self.assertTrue(features["near_lock_gap_risk"])
        self.assertFalse(features["near_lock_metar_aligned"])
        self.assertTrue(features["raw_forecast_in_bucket"])
        self.assertTrue(_bucket_value_in_range(70, 70, 71))

    def test_calibrated_probability_uses_wider_error_sigma(self):
        narrow = _bucket_probability_f(70, 70, 71, 1.5)
        wide = _bucket_probability_f(70, 70, 71, 4.0)
        self.assertGreater(narrow, wide)

        record = {
            "forecast_temp_f": 70,
            "bucket_low_f": 70,
            "bucket_high_f": 71,
            "entry_price": 0.30,
            "entry_ensemble_std_f": 0.8,
        }
        fit = {"bias_f": 0.0, "mae_f": 4.0, "rmse_f": 4.5}
        _augment_strategy_replay_record(record, fit)
        self.assertEqual(record["calibrated_sigma_f"], 4.0)
        self.assertLess(record["calibrated_probability"], round(narrow, 4))
        self.assertIn("calibrated_ev", record)

    def test_scanner_uses_calibrated_sigma_for_signal_probability(self):
        raw = bucket_prob(70, 70, 71, 2.0)
        calibration = {
            "cities": {"seattle": {"samples": 20, "mae_f": 4.0, "bias_f": 0.0, "rmse_f": 4.5}},
            "sources": {"GFS_ENSEMBLE": {"samples": 20, "mae_f": 3.5, "bias_f": 0.0, "rmse_f": 4.0}},
        }
        calibrated = calibrated_bucket_probability(
            "seattle",
            "gfs_ensemble",
            70,
            70,
            71,
            "F",
            0.6,
            calibration,
        )
        self.assertEqual(calibrated["sigma_f"], 4.0)
        self.assertLess(calibrated["p"], raw)
        self.assertEqual(calibrated["city_fit_samples"], 20)

    def test_calibration_metric_tracks_decayed_bias(self):
        metric = calibration_metric([4.0, 4.0, -2.0])
        self.assertIn("decayed_bias_f", metric)
        self.assertNotEqual(round(metric["decayed_bias_f"], 3), round(metric["bias_f"], 3))
        self.assertLess(metric["decayed_bias_f"], 4.0)

    def test_metric_summary_adds_mos_linear_fit(self):
        records = [
            {
                "forecast_f": 60.0 + i,
                "actual_f": 62.0 + i,
                "error_f": -2.0,
                "target_date": f"2026-06-{i + 1:02d}",
            }
            for i in range(20)
        ]
        metric = _metric_summary(records)
        self.assertAlmostEqual(metric["mos_slope"], 1.0)
        self.assertAlmostEqual(metric["mos_intercept_f"], 2.0)
        self.assertEqual(metric["mos_mae_f"], 0.0)
        self.assertGreater(metric["mos_improvement_f"], 0)

    def test_replay_record_adds_mos_probability_and_ev(self):
        record = {
            "forecast_temp_f": 70,
            "bucket_low_f": 72,
            "bucket_high_f": 73,
            "entry_price": 0.30,
            "entry_ensemble_std_f": 0.8,
        }
        fit = {"bias_f": 0.0, "mae_f": 2.0, "rmse_f": 2.5, "mos_slope": 1.0, "mos_intercept_f": 2.0}
        _augment_strategy_replay_record(record, fit)
        self.assertEqual(record["mos_adjusted_forecast_f"], 72.0)
        self.assertTrue(record["mos_adjusted_in_bucket"])
        self.assertIn("mos_ev", record)
        self.assertGreater(record["mos_probability"], 0)

    def test_policy_candidates_include_calibrated_threshold_grid(self):
        records = [
            {
                "market_id": "m1",
                "resolved": True,
                "result": "win",
                "pnl": 1.0,
                "cost": 2.0,
                "entry_price": 0.20,
                "live_allowed_replay": True,
                "calibrated_ev": 0.30,
                "calibrated_prob_edge": 0.13,
                "mos_ev": 0.28,
                "mos_prob_edge": 0.12,
                "mos_positive_edge": True,
                "city_fit_samples": 12,
                "source": "ECMWF",
            },
            {
                "market_id": "m2",
                "resolved": True,
                "result": "loss",
                "pnl": -2.0,
                "cost": 2.0,
                "entry_price": 0.20,
                "live_allowed_replay": True,
                "calibrated_ev": -0.10,
                "calibrated_prob_edge": -0.02,
                "mos_ev": -0.08,
                "mos_prob_edge": -0.01,
                "mos_positive_edge": False,
                "city_fit_samples": 12,
                "source": "ECMWF",
            },
        ]
        candidates = _build_policy_candidates(records)
        names = {row["name"] for row in candidates}
        self.assertIn("cal_ev10_edge8_s0", names)
        self.assertIn("mos_ev10_edge8_s0", names)
        self.assertIn("mos_positive_edge", names)
        self.assertTrue(any(name.startswith("cal_ev") for name in names))
        self.assertTrue(any(name.startswith("mos_ev") for name in names))
        self.assertNotIn("cal_ev50_edge18_s10", names)

    def test_bulk_simulation_skip_reason_explains_duplicates_and_calibration(self):
        self.assertEqual(
            _bulk_simulation_skip_reason(
                {"status": "signal", "date": "2026-06-22"},
                {"paper_position": True, "actionable": True, "edge": 0.3},
                "2026-06-22",
            ),
            "already_paper_position",
        )
        self.assertEqual(
            _bulk_simulation_skip_reason(
                {"status": "signal", "date": "2026-06-22"},
                {"paper_position": False, "actionable": True, "edge": -0.1},
                "2026-06-22",
            ),
            "calibrated_ev_nonpositive",
        )
        self.assertEqual(
            _bulk_simulation_skip_reason(
                {"status": "signal", "date": "2026-06-22"},
                {"paper_position": False, "actionable": True, "edge": 0.2, "live_pre_strategy_allowed": False, "live_block_reasons": ["fit_missing", "strategy_not_ready"]},
                "2026-06-22",
            ),
            "risk_gate:fit_missing",
        )

    def test_temperature_fit_readiness_gates_live_candidates(self):
        eligible = _fit_trade_readiness({"samples": 30, "mae_f": 2.0, "bias_f": 0.2, "rmse_f": 2.5}, 20)
        self.assertEqual(eligible["fit_status"], "eligible")
        self.assertGreater(eligible["trade_score"], 0.4)

        watch = _fit_trade_readiness({"samples": 25, "mae_f": 2.4, "bias_f": 0.5, "rmse_f": 3.0}, 12)
        self.assertEqual(watch["fit_status"], "watch")
        self.assertIn("fit_independent_days_low", watch["fit_reasons"])

        blocked = _fit_trade_readiness({"samples": 4, "mae_f": 5.1, "bias_f": 4.0, "rmse_f": 5.5}, 1)
        self.assertEqual(blocked["fit_status"], "blocked")
        self.assertIn("fit_independent_days_too_low", blocked["fit_reasons"])
        self.assertIn("fit_samples_too_low", blocked["fit_reasons"])
        self.assertIn("fit_mae_block", blocked["fit_reasons"])

    def test_live_gate_blocks_thin_independent_days_and_spread_cost(self):
        thin = _live_gate(
            {"limit_price": 0.20, "spread": 0.01, "date": "2026-06-22", "status": "signal"},
            ["fit_independent_days_low"],
            {"strategy_score": 0.8, "strategy_tags": []},
        )
        self.assertFalse(thin["live_allowed"])
        self.assertIn("fit_independent_days_low", thin["live_block_reasons"])

        spread_cost = _live_gate(
            {"limit_price": 0.07, "spread": 0.03, "date": "2026-06-22", "status": "signal"},
            [],
            {"strategy_score": 0.8, "strategy_tags": ["cheap_tail_candidate"]},
        )
        self.assertFalse(spread_cost["live_allowed"])
        self.assertIn("spread_cost_too_high", spread_cost["live_block_reasons"])


if __name__ == "__main__":
    unittest.main()
