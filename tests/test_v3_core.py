import os
import unittest
from pathlib import Path
from unittest.mock import patch

from weatherbot_v3.ai_review import AIReviewer
from weatherbot_v3.db import connect, init_v3_db
from weatherbot_v3.executor import PaperExecutor
from weatherbot_v3.polymarket import quote_from_market_payload, validate_order_constraints
from dashboard_server import _augment_strategy_replay_record, _bucket_probability_f, _bucket_value_in_range, _entry_snapshot_features
from bot_v2 import bucket_prob, calibrated_bucket_probability


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


if __name__ == "__main__":
    unittest.main()
