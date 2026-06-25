import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from weatherbot_v3.ai_review import AIReviewer
from weatherbot_v3.db import connect, init_v3_db, insert_forecast_run, insert_orderbook, upsert_market_rule
from weatherbot_v3.executor import PaperExecutor
from weatherbot_v3.polymarket import estimate_buy_fill, quote_from_market_payload, validate_order_constraints
from weatherbot_v3.distribution import build_event_distribution
from weatherbot_v3.qualification import build_data_readiness
from weatherbot_v3.registry import SETTLEMENT_REGISTRY
from weatherbot_v3.truth import infer_settlement_rule
from weatherbot_v3.db import truth_coverage_summary, upsert_truth_observation
from dashboard_server import AutoSimulationUpdate, _augment_strategy_replay_record, _auto_simulation_state, _bucket_probability_f, _bucket_value_in_range, _bulk_simulation_skip_reason, _build_policy_candidates, _build_temperature_fit, _entry_snapshot_features, _fit_trade_readiness, _live_gate, _metric_summary, _position_from_signal, _save_auto_simulation_state, update_auto_simulation
from bot_v2 import bucket_prob, calibrated_bucket_probability, calibration_metric, persist_forecast_batches, target_dates_for_city
from datetime import datetime, timezone


TEST_DB_DIR = Path(__file__).resolve().parents[1] / ".tmp-tests"


def test_db_path(name: str) -> Path:
    TEST_DB_DIR.mkdir(exist_ok=True)
    path = TEST_DB_DIR / f"{name}.db"
    path.unlink(missing_ok=True)
    return path


class V3CoreTests(unittest.TestCase):
    def test_target_dates_follow_airport_local_day_not_utc_day(self):
        now_utc = datetime(2026, 6, 25, 2, 0, tzinfo=timezone.utc)
        self.assertEqual(target_dates_for_city("nyc", 2, now_utc), ["2026-06-24", "2026-06-25"])
        self.assertEqual(target_dates_for_city("shanghai", 2, now_utc), ["2026-06-25", "2026-06-26"])

    def test_auto_simulation_state_persists_and_clamps_interval(self):
        TEST_DB_DIR.mkdir(exist_ok=True)
        state_path = TEST_DB_DIR / "auto-simulation.json"
        state_path.unlink(missing_ok=True)
        self.addCleanup(lambda: state_path.unlink(missing_ok=True))
        with patch("dashboard_server.AUTO_SIMULATION_PATH", state_path):
            initial = _auto_simulation_state()
            self.assertFalse(initial["enabled"])
            saved = _save_auto_simulation_state(enabled=True, interval_seconds=10)
            self.assertTrue(saved["enabled"])
            self.assertEqual(_auto_simulation_state()["interval_seconds"], 60)

    def test_auto_simulation_api_enables_without_running_real_orders(self):
        TEST_DB_DIR.mkdir(exist_ok=True)
        state_path = TEST_DB_DIR / "auto-simulation-api.json"
        state_path.unlink(missing_ok=True)
        self.addCleanup(lambda: state_path.unlink(missing_ok=True))
        with (
            patch("dashboard_server.AUTO_SIMULATION_PATH", state_path),
            patch("dashboard_server._ensure_auto_simulation_task") as ensure_task,
            patch("dashboard_server.log_event"),
        ):
            result = asyncio.run(update_auto_simulation(
                AutoSimulationUpdate(enabled=True, interval_seconds=300)
            ))
        self.assertTrue(result["ok"])
        self.assertTrue(result["enabled"])
        self.assertEqual(result["interval_seconds"], 300)
        ensure_task.assert_called_once()

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

    def test_clob_quote_uses_true_depth_and_estimates_partial_fill(self):
        quote = quote_from_market_payload({
            "id": "1",
            "yes_token_id": "yes",
            "snapshot_type": "clob",
            "bids": [{"price": "0.18", "size": "20"}, {"price": "0.20", "size": "10"}],
            "asks": [{"price": "0.25", "size": "20"}, {"price": "0.22", "size": "5"}],
            "min_order_size": "5",
            "tick_size": "0.01",
            "enableOrderBook": True,
        })
        self.assertEqual(quote.best_bid, 0.20)
        self.assertEqual(quote.best_ask, 0.22)
        fill = estimate_buy_fill(quote, 2.0, 0.22)
        self.assertFalse(fill["fully_filled"])
        self.assertEqual(fill["filled_shares"], 5.0)
        self.assertEqual(fill["filled_amount"], 1.1)

    def test_orderbook_timestamp_blocks_stale_execution(self):
        quote = quote_from_market_payload({
            "id": "1",
            "yes_token_id": "yes",
            "snapshot_type": "clob",
            "timestamp": "1609459200000",
            "bids": [{"price": "0.20", "size": "10"}],
            "asks": [{"price": "0.22", "size": "10"}],
            "min_order_size": "5",
            "tick_size": "0.01",
            "enableOrderBook": True,
        })
        self.assertIn("orderbook_stale", validate_order_constraints(quote, 2.0, 0.22))

    def test_orderbook_store_deduplicates_clob_hash_and_keeps_depth(self):
        db_path = test_db_path("orderbook_store")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        payload = {
            "yes_token_id": "yes",
            "snapshot_type": "clob",
            "timestamp": "1782355609949",
            "hash": "book-hash",
            "bids": [{"price": "0.20", "size": "10"}],
            "asks": [{"price": "0.22", "size": "5"}],
            "min_order_size": "5",
            "tick_size": "0.01",
        }
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            first_id = insert_orderbook("1", payload)
            second_id = insert_orderbook("1", payload)
            with connect(db_path) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) n, MAX(best_bid) bid, MAX(best_ask) ask, MAX(ask_depth) depth "
                    "FROM orderbooks"
                ).fetchone()
        self.assertEqual(first_id, second_id)
        self.assertEqual(row["n"], 1)
        self.assertEqual(row["bid"], 0.20)
        self.assertEqual(row["ask"], 0.22)
        self.assertEqual(row["depth"], 5.0)

    def test_dashboard_position_uses_actual_partial_fill_not_requested_amount(self):
        position = _position_from_signal(
            {
                "market_id": "1",
                "limit_price": 0.22,
                "bid_price": 0.20,
                "question": "test",
                "raw_json": "{}",
            },
            1.10,
            "2026-06-25T00:00:00+00:00",
            {
                "status": "paper_partial",
                "average_fill_price": 0.22,
                "shares": 5.0,
                "fill": {
                    "filled_amount": 1.10,
                    "remaining_amount": 0.90,
                    "fills": [{"price": 0.22, "shares": 5.0, "amount": 1.10}],
                },
            },
        )
        self.assertEqual(position["cost"], 1.10)
        self.assertEqual(position["shares"], 5.0)
        self.assertEqual(position["unfilled_amount"], 0.90)
        self.assertEqual(position["fill_status"], "paper_partial")

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
        self.assertIn("market_rules", tables)
        self.assertIn("truth_observations", tables)
        self.assertIn("event_distributions", tables)
        self.assertIn("signal_decisions", tables)
        self.assertIn("data_qualification_audits", tables)

    def test_settlement_registry_has_station_and_timezone_for_all_cities(self):
        self.assertEqual(len(SETTLEMENT_REGISTRY), 20)
        for city, profile in SETTLEMENT_REGISTRY.items():
            self.assertEqual(city, profile.city)
            self.assertTrue(profile.station_id)
            self.assertNotEqual(profile.timezone, "UTC")
            self.assertIn(profile.unit, {"F", "C"})

    def test_settlement_rule_infers_station_and_wunderground_confidence(self):
        rule = infer_settlement_rule(
            {
                "city": "nyc",
                "city_name": "New York City",
                "unit": "F",
                "station": "KLGA",
                "event_url": "https://polymarket.com/event/highest-temperature-in-nyc-on-june-23-2026",
                "question": "Will the highest temperature in NYC be between 80-81°F on June 23?",
                "description": "This market resolves according to Wunderground station history.",
            }
        )
        self.assertEqual(rule.station_id, "KLGA")
        self.assertEqual(rule.event_slug, "highest-temperature-in-nyc-on-june-23-2026")
        self.assertEqual(rule.bucket_low, 80)
        self.assertEqual(rule.bucket_high, 81)
        self.assertGreaterEqual(rule.truth_confidence, 0.8)
        self.assertEqual(rule.registry_version, "airport-settlement-registry-v1")
        self.assertEqual(rule.timezone, "America/New_York")

    def test_data_readiness_blocks_unverified_rules_and_missing_forecast_runs(self):
        db_path = test_db_path("data_readiness")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            upsert_market_rule(infer_settlement_rule(
                {
                    "market_id": "nyc-1",
                    "city": "nyc",
                    "city_name": "New York City",
                    "unit": "F",
                    "event_url": "https://polymarket.com/event/nyc-test",
                    "question": "Will the highest temperature in NYC be between 80-81°F on June 23?",
                    "description": "Resolves according to Wunderground station history.",
                    "date": "2026-06-23",
                }
            ).to_dict())
            readiness = build_data_readiness(db_path)
        self.assertFalse(readiness["live_allowed"])
        self.assertEqual(readiness["summary"]["market_rules"], 1)
        blocker_codes = {item["code"] for item in readiness["blockers"]}
        self.assertIn("settlement_rule_not_manually_verified", blocker_codes)
        self.assertIn("versioned_forecast_runs_missing", blocker_codes)

    def test_forecast_run_store_deduplicates_response_and_keeps_hourly_members(self):
        db_path = test_db_path("forecast_store")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        run = {
            "run_key": "gfs:nyc:2026-06-25:hash1",
            "city": "nyc",
            "target_date": "2026-06-25",
            "source": "gfs_ensemble",
            "provider": "open_meteo",
            "model": "gfs_seamless",
            "model_version": "provider_current",
            "run_type": "forecast",
            "retrieved_at": "2026-06-25T00:00:00+00:00",
            "valid_at": "2026-06-25T16:00:00+00:00",
            "lead_hours": 16,
            "latitude": 40.7772,
            "longitude": -73.8726,
            "station_id": "KLGA",
            "timezone": "America/New_York",
            "unit": "F",
            "mean_high": 80,
            "std_high": 1.5,
            "member_count": 2,
            "source_url": "https://ensemble-api.open-meteo.com/v1/ensemble",
            "raw_response_hash": "hash1",
            "data_license": "CC-BY-4.0",
            "quality_flags": ["provider_run_time_unavailable"],
        }
        members = [
            {
                "member_id": "member01",
                "high_temp": 79,
                "hourly": [{"valid_at": "2026-06-25T12:00", "temperature_2m": 79}],
            },
            {
                "member_id": "member02",
                "high_temp": 81,
                "hourly": [{"valid_at": "2026-06-25T12:00", "temperature_2m": 81}],
            },
        ]
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            first_id = insert_forecast_run(run, members)
            second_id = insert_forecast_run(run, members)
            with connect(db_path) as conn:
                run_count = conn.execute("SELECT COUNT(*) FROM forecast_runs").fetchone()[0]
                member_count = conn.execute("SELECT COUNT(*) FROM forecast_members").fetchone()[0]
                hourly_json = conn.execute(
                    "SELECT hourly_json FROM forecast_members WHERE member_id = 'member01'"
                ).fetchone()[0]
        self.assertEqual(first_id, second_id)
        self.assertEqual(run_count, 1)
        self.assertEqual(member_count, 2)
        self.assertIn("temperature_2m", hourly_json)

    def test_scanner_batch_persistence_records_deterministic_and_ensemble_sources(self):
        db_path = test_db_path("scanner_forecast_store")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        retrieved_at = "2026-06-25T00:00:00+00:00"
        deterministic = {
            "2026-06-25": 79,
            "__meta__": {
                "provider": "open_meteo",
                "model": "ecmwf_ifs025",
                "model_version": "provider_current",
                "source": "ecmwf",
                "retrieved_at": retrieved_at,
                "source_url": "https://api.open-meteo.com/v1/forecast",
                "raw_response_hash": "ecmwf-hash",
                "data_license": "CC-BY-4.0",
                "quality_flags": ["provider_run_time_unavailable"],
                "hourly_by_date": {
                    "2026-06-25": [{"valid_at": "2026-06-25T12:00", "temperature_2m": 79}]
                },
            },
        }
        ensemble = {
            "2026-06-25": {
                "mean": 80,
                "std": 1,
                "members": [79, 81],
                "member_paths": [
                    {"member_id": "member01", "high_temp": 79, "hourly": []},
                    {"member_id": "member02", "high_temp": 81, "hourly": []},
                ],
            },
            "__meta__": {
                "provider": "open_meteo",
                "model": "gfs_seamless",
                "model_version": "provider_current",
                "source": "gfs_ensemble",
                "retrieved_at": retrieved_at,
                "source_url": "https://ensemble-api.open-meteo.com/v1/ensemble",
                "raw_response_hash": "gfs-hash",
                "data_license": "CC-BY-4.0",
                "quality_flags": ["provider_run_time_unavailable"],
            },
        }
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            run_ids = persist_forecast_batches("nyc", ["2026-06-25"], [ensemble, deterministic])
            with connect(db_path) as conn:
                sources = {
                    row[0]: row[1]
                    for row in conn.execute(
                        "SELECT source, member_count FROM forecast_runs ORDER BY source"
                    ).fetchall()
                }
        self.assertEqual(len(run_ids), 2)
        self.assertEqual(sources["ecmwf"], 1)
        self.assertEqual(sources["gfs_ensemble"], 2)

    def test_event_distribution_normalizes_all_buckets(self):
        dist = build_event_distribution(
            [
                {"market_id": "low", "range": (76, 77), "ask": 0.35, "bid": 0.33, "spread": 0.02},
                {"market_id": "mid", "range": (78, 79), "ask": 0.27, "bid": 0.25, "spread": 0.02},
                {"market_id": "tail", "range": (80, 81), "ask": 0.07, "bid": 0.04, "spread": 0.03},
            ],
            76.6,
            unit="F",
            sigma_f=3.2,
            signal_market_id="tail",
        )
        self.assertTrue(dist["normalized"])
        self.assertAlmostEqual(sum(item["probability"] for item in dist["items"]), 1.0, places=3)
        tail = next(item for item in dist["items"] if item["market_id"] == "tail")
        self.assertTrue(tail["is_signal"])
        self.assertLess(tail["probability"], 0.5)

    def test_truth_coverage_summary_marks_open_meteo_fallback(self):
        db_path = test_db_path("truth_coverage")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            upsert_truth_observation({
                "city": "nyc",
                "city_name": "New York City",
                "target_date": "2026-06-23",
                "station_id": "KLGA",
                "station_name": "New York City",
                "unit": "F",
                "actual_temp": 77.0,
                "provider": "open_meteo_archive",
                "source_url": "https://archive-api.open-meteo.com/v1/archive",
                "observation_count": 1,
                "source_confidence": 0.45,
                "calibration_eligible": False,
                "reason_if_ineligible": "fallback",
            })
            summary = truth_coverage_summary()
        self.assertEqual(summary["total_observations"], 1)
        self.assertEqual(summary["eligible_observations"], 0)
        self.assertEqual(summary["open_meteo_fallbacks"], 1)

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
            None,
        )
        self.assertEqual(
            _bulk_simulation_skip_reason(
                {"status": "signal", "date": "2026-06-22"},
                {"paper_position": False, "actionable": True, "edge": 0.2, "live_pre_strategy_allowed": False, "live_block_reasons": ["spread_cost_too_high", "strategy_not_ready"]},
                "2026-06-22",
            ),
            "risk_gate:spread_cost_too_high",
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

    def test_temperature_fit_counts_independent_days_not_scanner_snapshots(self):
        fit = _build_temperature_fit([
            {
                "city": "chicago",
                "city_name": "Chicago",
                "date": "2026-06-24",
                "unit": "F",
                "actual_temp": 74.0,
                "actual_provider": "nws_station",
                "actual_station": "KORD",
                "actual_confidence": 0.95,
                "actual_calibration_eligible": True,
                "forecast_snapshots": [
                    {"ts": "2026-06-23T00:00:00+00:00", "hours_left": 40.0, "best": 70.0},
                    {"ts": "2026-06-23T16:00:00+00:00", "hours_left": 24.0, "best": 73.0},
                    {"ts": "2026-06-24T04:00:00+00:00", "hours_left": 12.0, "best": 75.0},
                ],
            }
        ])
        self.assertEqual(fit["summary"]["snapshot_samples"], 3)
        self.assertEqual(fit["summary"]["observed_samples"], 1)
        self.assertEqual(len(fit["records"]), 1)
        self.assertEqual(fit["records"][0]["hours_left"], 24.0)
        self.assertEqual(fit["records"][0]["forecast"], 73.0)

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
