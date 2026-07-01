import asyncio
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from weatherbot_v3.ai_review import AIReviewer
from weatherbot_v3.db import bulk_settlement_contract_verification, connect, dashboard_summary, init_v3_db, insert_forecast_run, insert_orderbook, list_data_fetch_logs, list_settlement_contracts, log_data_fetch, set_settlement_contract_verification, upsert_hourly_consensus, upsert_market_rule, upsert_market_rules, upsert_mesonet_observation, upsert_metar_report, upsert_settlement_contracts, weather_evidence_summary
from weatherbot_v3.executor import PaperExecutor
from weatherbot_v3.polymarket import estimate_buy_fill, quote_from_market_payload, validate_order_constraints
from weatherbot_v3.production_actions import list_production_actions, run_production_action
from weatherbot_v3.distribution import build_event_distribution
from weatherbot_v3.forecast_archive import build_forecast_archive_manifest, import_forecast_archive, write_forecast_archive_manifest
from weatherbot_v3.hourly import build_metar_hourly_consensus, forecast_hourly_points, hourly_consensus_points
from weatherbot_v3.model_dataset import build_model_dataset_audit, is_settlement_pending
from weatherbot_v3.qualification import build_data_readiness
from weatherbot_v3.registry import SETTLEMENT_REGISTRY
from weatherbot_v3.stations import list_stations, station_row_from_profile, sync_station_registry
from weatherbot_v3.migration import repair_truth_temporal_mismatches
from weatherbot_v3.metar import fetch_awc_metars, refresh_metar_reports
from weatherbot_v3.truth import _parse_time, infer_settlement_rule, settlement_contract_from_rule
from weatherbot_v3.validation import _compact_action, build_production_validation_report
from weatherbot_v3.db import truth_coverage_summary, upsert_truth_observation
from weatherbot_v3.cli import default_orderbook_start_date, run_orderbook_backfill, run_production_refresh, select_orderbook_backfill_markets
from dashboard_server import AutoSimulationUpdate, ProductionActionRequest, ProductionRefreshRequest, _augment_strategy_replay_record, _auto_simulation_state, _bucket_probability_f, _bucket_value_in_range, _bulk_simulation_skip_reason, _build_city_evidence_payload, _build_policy_candidates, _build_temperature_fit, _build_weather_city_series, _city_evidence_matches, _combined_fetch_log_payload, _diff_stats_summary, _entry_snapshot_features, _fit_trade_readiness, _forecast_archive_manifest_payload, _live_gate, _merge_hourly_points, _metric_summary, _position_from_signal, _refresh_signal_orderbooks, _run_paper_validation_action, _save_auto_simulation_state, production_refresh, production_refresh_lock, update_auto_simulation
from dashboard_server import stations as stations_api
from bot_v2 import bucket_prob, calibrated_bucket_probability, calibration_metric, persist_forecast_batches, target_dates_for_city
from datetime import datetime, timedelta, timezone


TEST_DB_DIR = Path(__file__).resolve().parents[1] / ".tmp-tests"


def test_db_path(name: str) -> Path:
    TEST_DB_DIR.mkdir(exist_ok=True)
    path = TEST_DB_DIR / f"{name}.db"
    path.unlink(missing_ok=True)
    return path


class V3CoreTests(unittest.TestCase):
    def test_city_evidence_payload_counts_polywx_modules(self):
        city_series = [{
            "city_key": "chicago-kord",
            "city_name": "Chicago",
            "station_id": "KORD",
            "unit": "F",
            "hourly_points": [{
                "target_date": "2026-06-29",
                "timestamp": "2026-06-29T18:00:00Z",
                "best": 82.0,
                "metar": 80.0,
                "cloud_cover": 40,
            }],
            "forecast_points": [],
            "history_points": [{
                "target_date": "2026-06-29",
                "actual_high": 83.0,
                "provider": "station_truth",
            }],
        }]
        signals = [{
            "city_key": "chicago-kord",
            "target_date": "2026-06-29",
            "id": 101,
            "market_id": "market-101",
            "actionable": True,
            "limit_price": 0.34,
            "bid_price": 0.31,
            "spread": 0.03,
            "probability_edge": 0.10,
            "event_url": "https://polymarket.com/event/highest-temperature-in-chicago-on-june-29-2026",
            "decision": {
                "paper_allowed": True,
                "live_allowed": False,
                "reasons": [],
                "cautions": ["spread_watch"],
            },
            "live_allowed": False,
            "live_block_reasons": ["truth_independent_days_low"],
            "distribution": {
                "normalized": True,
                "items": [
                    {"bucket": "80-81", "probability": 0.32, "ask": 0.25, "probability_edge": 0.07},
                    {"bucket": "82-83", "probability": 0.44, "ask": 0.34, "probability_edge": 0.10, "is_signal": True},
                    {"bucket": "84 or above", "bucket_low": 84, "bucket_high": 999, "probability": 0.05, "ask": 0.07, "probability_edge": -0.02},
                ],
            },
        }]
        fetch_log = [{
            "source": "weather",
            "stage": "weather",
            "message": "chicago-kord 2026-06-29 refresh complete",
        }]

        payload = _build_city_evidence_payload(city_series, signals, fetch_log)

        self.assertEqual(len(payload), 1)
        day = payload[0]["dates"][0]
        modules = day["modules"]
        self.assertEqual(day["target_date"], "2026-06-29")
        self.assertEqual(modules["hourly_temperature"]["rows"], 1)
        self.assertEqual(modules["metar"]["rows"], 1)
        self.assertEqual(modules["historical"]["rows"], 1)
        self.assertEqual(modules["diff_stats"]["rows"], 1)
        self.assertEqual(modules["diff_stats"]["summary"]["count"], 1)
        self.assertEqual(modules["diff_stats"]["summary"]["avg_delta"], -2.0)
        self.assertEqual(modules["diff_stats"]["summary"]["mae"], 2.0)
        self.assertEqual(modules["probability_buckets"]["rows"], 3)
        probability_summary = modules["probability_buckets"]["probability_summary"]
        self.assertEqual(probability_summary["bucket_count"], 3)
        self.assertEqual(probability_summary["signal_count"], 1)
        self.assertEqual(probability_summary["normalized_count"], 1)
        self.assertEqual(probability_summary["actionable_signal_count"], 1)
        self.assertEqual(probability_summary["highest_bucket"], "82-83")
        self.assertAlmostEqual(probability_summary["highest_probability"], 0.44)
        self.assertEqual(probability_summary["top_buckets"][0]["edge"], 0.10)
        self.assertEqual(modules["fetch_log"]["rows"], 1)
        self.assertTrue(modules["market_buckets"]["strict_matching_required"])
        market_summary = modules["market_buckets"]["market_summary"]
        self.assertEqual(market_summary["bucket_count"], 3)
        self.assertEqual(market_summary["matched_bucket_count"], 1)
        self.assertEqual(market_summary["open_tail_count"], 1)
        self.assertEqual(market_summary["low_price_tail_count"], 1)
        self.assertEqual(market_summary["paper_allowed_count"], 1)
        self.assertEqual(market_summary["live_allowed_count"], 0)
        self.assertEqual(market_summary["reason_counts"][0]["reason"], "truth_independent_days_low")
        self.assertEqual(market_summary["top_blocked"][0]["bucket"], "82-83")
        self.assertTrue(_city_evidence_matches(payload[0], "chicago-kord"))
        self.assertTrue(_city_evidence_matches(payload[0], "chicago"))

    def test_diff_stats_summary_reports_polywx_metrics(self):
        summary = _diff_stats_summary(
            [
                {
                    "target_date": "2026-06-29",
                    "timestamp": "2026-06-29T15:00:00-05:00",
                    "local_hour": "15:00",
                    "best": 92.0,
                    "metar": 91.0,
                    "source": "metar",
                },
                {
                    "target_date": "2026-06-29",
                    "timestamp": "2026-06-29T16:00:00-05:00",
                    "local_hour": "16:00",
                    "ensemble_mean": 94.0,
                    "metar": 95.0,
                    "source": "metar",
                },
                {
                    "target_date": "2026-06-29",
                    "timestamp": "2026-06-29T17:00:00-05:00",
                    "local_hour": "17:00",
                    "best": 93.0,
                },
            ],
            [
                {"target_date": "2026-06-29", "timestamp": "2026-06-29T15:51:00-05:00"},
                {"target_date": "2026-06-29", "timestamp": "2026-06-29T16:51:00-05:00"},
            ],
        )

        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["avg_delta"], 0.0)
        self.assertEqual(summary["mae"], 1.0)
        self.assertEqual(summary["metar_hours"], 2)
        self.assertEqual(summary["forecast_hours"], 3)
        self.assertEqual(summary["overlap_count"], 2)
        self.assertAlmostEqual(summary["overlap_ratio"], 2 / 3, places=4)
        self.assertEqual(summary["historical_metar_overlap_count"], 2)
        self.assertEqual(len(summary["rows"]), 2)
        self.assertIsNotNone(summary["pearson_r"])

    def test_target_dates_follow_airport_local_day_not_utc_day(self):
        now_utc = datetime(2026, 6, 25, 2, 0, tzinfo=timezone.utc)
        self.assertEqual(target_dates_for_city("nyc", 2, now_utc), ["2026-06-24", "2026-06-25"])
        self.assertEqual(target_dates_for_city("shanghai", 2, now_utc), ["2026-06-25", "2026-06-26"])

    def test_truth_time_parser_accepts_epoch_seconds_and_milliseconds(self):
        seconds = _parse_time(1782356400)
        milliseconds = _parse_time("1782356400000")
        self.assertIsNotNone(seconds)
        self.assertEqual(seconds, milliseconds)
        self.assertEqual(seconds.tzinfo, timezone.utc)

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

    def test_orderbook_refresh_deduplicates_signal_markets(self):
        with patch("dashboard_server.PolymarketDataClient") as client_cls:
            client_cls.return_value.quote.side_effect = [
                type("Quote", (), {"book_source": "clob"})(),
                type("Quote", (), {"book_source": "gamma_fallback"})(),
            ]
            result = _refresh_signal_orderbooks([
                {"market_id": "1"},
                {"market_id": "1"},
                {"market_id": "2"},
                {"market_id": ""},
            ])
        self.assertEqual(result, {"requested": 2, "refreshed": 1, "failed": 1})
        self.assertEqual(client_cls.return_value.quote.call_count, 2)

    def test_orderbook_backfill_selects_current_unresolved_markets(self):
        db_path = test_db_path("orderbook_selection")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        init_v3_db(db_path)
        now = "2026-06-26T00:00:00+00:00"
        rows = [
            ("old", "old-market", "2026-06-20", "signal"),
            ("future", "future-market", "2026-06-28", "signal"),
            ("recent", "recent-market", "2026-06-27", "open"),
            ("closed", "closed-market", "2026-06-29", "closed"),
            ("empty-date", "empty-date-market", "", "signal"),
        ]
        with connect(db_path) as conn:
            conn.executemany(
                """
                INSERT INTO signals (
                    signal_key, market_id, target_date, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [(key, market_id, target_date, status, now, now) for key, market_id, target_date, status in rows],
            )
            selected = select_orderbook_backfill_markets(
                conn,
                limit=10,
                start_date="2026-06-27",
            )
        self.assertEqual([row["market_id"] for row in selected], ["future-market", "recent-market"])

    def test_orderbook_backfill_default_start_keeps_global_settlement_window(self):
        now = datetime(2026, 6, 28, 0, 30, tzinfo=timezone.utc)
        self.assertEqual(default_orderbook_start_date(now), "2026-06-27")

    def test_orderbook_backfill_reports_structured_blocker_reasons(self):
        db_path = test_db_path("orderbook_reason_counts")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        init_v3_db(db_path)
        now = "2026-06-26T00:00:00+00:00"
        with connect(db_path) as conn:
            conn.executemany(
                """
                INSERT INTO signals (
                    signal_key, market_id, target_date, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    ("clob-ok", "1", "2026-06-28", "signal", now, now),
                    ("clob-empty", "2", "2026-06-28", "signal", now, now),
                    ("fallback", "3", "2026-06-28", "signal", now, now),
                ],
            )
        quotes = [
            SimpleNamespace(
                book_source="clob",
                best_bid=0.2,
                best_ask=0.22,
                spread=0.02,
                bids=({"price": 0.2, "size": 10.0},),
                asks=({"price": 0.22, "size": 10.0},),
                quote_age_seconds=1.0,
            ),
            SimpleNamespace(
                book_source="clob",
                best_bid=0.0,
                best_ask=0.0,
                spread=0.0,
                bids=(),
                asks=(),
                quote_age_seconds=1.0,
            ),
            SimpleNamespace(
                book_source="gamma_fallback",
                best_bid=0.1,
                best_ask=0.12,
                spread=0.02,
                bids=(),
                asks=(),
                quote_age_seconds=None,
            ),
        ]
        with (
            patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False),
            patch("weatherbot_v3.polymarket.PolymarketDataClient") as client_cls,
            patch("weatherbot_v3.cli.time.sleep"),
        ):
            client_cls.return_value.quote.side_effect = quotes
            payload = run_orderbook_backfill(10, "2026-06-28", "")

        self.assertEqual(payload["requested"], 3)
        self.assertEqual(payload["ok"], 1)
        self.assertEqual(payload["failed"], 2)
        self.assertEqual(payload["reason_counts"]["fresh_clob_depth_available"], 1)
        self.assertEqual(payload["reason_counts"]["empty_clob_depth"], 1)
        self.assertEqual(payload["reason_counts"]["no_clob_orderbook"], 1)
        self.assertEqual(
            [row["reason"] for row in payload["results"]],
            ["fresh_clob_depth_available", "empty_clob_depth", "no_clob_orderbook"],
        )

    def test_production_refresh_summarizes_pipeline_without_signal_scan(self):
        db_path = test_db_path("production_refresh")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        readiness = {
            "status": "blocked",
            "score": 0.5,
            "live_allowed": False,
            "production_phase": {
                "id": "phase1_5",
                "blocked_keys": ["settlement_contracts", "orderbooks"],
            },
            "next_actions": [{"key": "refresh_clob_orderbooks"}],
        }
        with (
            patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False),
            patch("weatherbot_v3.cli.sync_settlement_contracts", return_value={"settlement_contracts": 2}),
            patch("weatherbot_v3.cli.run_forecast_backfill", return_value={"ok": 1, "failed": 0}) as forecast,
            patch("weatherbot_v3.cli.run_legacy_signal_scan") as signal_scan,
            patch("weatherbot_v3.cli.migrate_legacy_signals", return_value={"imported": 3, "skipped": 0}) as migrate,
            patch("weatherbot_v3.cli.run_orderbook_backfill", return_value={"requested": 2, "ok": 1, "failed": 1}) as orderbooks,
            patch("weatherbot_v3.cli.build_data_readiness", return_value=readiness),
            patch("weatherbot_v3.cli.persist_data_readiness") as persist,
        ):
            payload = run_production_refresh(
                cities="nyc",
                days=2,
                limit=5,
                start_date="2026-06-27",
                scan_signals=False,
            )
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["scan_signals"])
        self.assertEqual(payload["readiness"]["status"], "blocked")
        self.assertEqual(payload["readiness"]["blocked_keys"], ["settlement_contracts", "orderbooks"])
        self.assertEqual([stage["name"] for stage in payload["stages"]], [
            "contracts_sync",
            "forecast_backfill",
            "signal_scan",
            "signal_migration",
            "orderbook_backfill",
        ])
        self.assertTrue(payload["stages"][2]["skipped"])
        forecast.assert_called_once_with("nyc", 2)
        signal_scan.assert_not_called()
        migrate.assert_called_once()
        orderbooks.assert_called_once_with(5, "2026-06-27", "")
        persist.assert_called_once_with(readiness)

    def test_dashboard_production_refresh_endpoint_persists_result(self):
        state_path = TEST_DB_DIR / "production-refresh-state.json"
        state_path.unlink(missing_ok=True)
        self.addCleanup(lambda: state_path.unlink(missing_ok=True))
        payload = {
            "refresh_version": "production-refresh-v1",
            "ok": True,
            "failed_stages": [],
            "scan_signals": False,
            "stages": [{"name": "contracts_sync", "ok": True}],
            "readiness": {"status": "blocked", "blocked_keys": ["orderbooks"]},
        }
        with (
            patch("dashboard_server.PRODUCTION_REFRESH_PATH", state_path),
            patch("dashboard_server.run_production_refresh", return_value=payload) as refresh,
            patch("dashboard_server.log_event") as log_event,
        ):
            result = asyncio.run(production_refresh(ProductionRefreshRequest(
                cities=["shanghai"],
                days=1,
                limit=2,
                skip_signal_scan=True,
            )))
        self.assertTrue(result["ok"])
        self.assertEqual(result["request"]["cities"], ["shanghai"])
        self.assertTrue(result["request"]["skip_signal_scan"])
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["refresh_version"], "production-refresh-v1")
        self.assertEqual(saved["request"]["limit"], 2)
        self.assertEqual(len(saved["history"]), 1)
        self.assertEqual(saved["history"][0]["ok_stage_count"], 1)
        refresh.assert_called_once()
        _, kwargs = refresh.call_args
        self.assertEqual(kwargs["cities"], "shanghai")
        self.assertFalse(kwargs["scan_signals"])
        log_event.assert_called_once()

    def test_dashboard_production_refresh_rejects_concurrent_run(self):
        state_path = TEST_DB_DIR / "production-refresh-running.json"
        state_path.unlink(missing_ok=True)
        self.addCleanup(lambda: state_path.unlink(missing_ok=True))
        state_path.write_text(json.dumps({
            "refresh_version": "production-refresh-v1",
            "ok": True,
            "failed_stages": [],
            "history": [],
        }), encoding="utf-8")

        async def run_locked():
            await production_refresh_lock.acquire()
            try:
                with (
                    patch("dashboard_server.PRODUCTION_REFRESH_PATH", state_path),
                    patch("dashboard_server.run_production_refresh") as refresh,
                ):
                    result = await production_refresh(ProductionRefreshRequest())
                refresh.assert_not_called()
                return result
            finally:
                production_refresh_lock.release()

        result = asyncio.run(run_locked())
        self.assertTrue(result["running"])
        self.assertIn("already_running", result["failed_stages"])

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
        self.assertIn("settlement_contracts", tables)
        self.assertIn("truth_observation_versions", tables)
        self.assertIn("metar_reports", tables)
        self.assertIn("mesonet_observations", tables)
        self.assertIn("hourly_consensus", tables)
        self.assertIn("data_fetch_logs", tables)

    def test_weather_evidence_tables_upsert_and_summarize_polywx_core_sources(self):
        db_path = test_db_path("weather_evidence_sources")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            metar_id = upsert_metar_report({
                "city": "chicago",
                "city_name": "Chicago",
                "station_id": "KORD",
                "report_type": "METAR",
                "report_time": "2026-06-29T16:00:00",
                "raw_text": "METAR KORD 292051Z 18014G25KT 10SM FEW042 SCT200 BKN250 33/23 A2988",
                "temperature": 91.94,
                "dew_point": 73.0,
                "wind_direction": 180,
                "wind_speed": 14,
                "wind_gust": 25,
                "visibility": 10,
                "cloud_layers": [{"cover": "FEW", "base_ft": 4200}],
                "altimeter": 29.88,
                "parse_warnings": [],
            })
            same_metar_id = upsert_metar_report({
                "city": "chicago",
                "city_name": "Chicago",
                "station_id": "KORD",
                "report_type": "METAR",
                "report_time": "2026-06-29T16:00:00",
                "raw_text": "METAR KORD 292051Z 18014G25KT 10SM FEW042 SCT200 BKN250 33/23 A2988",
                "temperature": 91.94,
                "parse_status": "parsed",
            })
            mesonet_id = upsert_mesonet_observation({
                "city": "chicago",
                "city_name": "Chicago",
                "network": "pws",
                "station_id": "KILROSEM4",
                "station_name": "Rosemont PWS",
                "observed_at": "2026-06-29T16:04:48",
                "temperature": 90.2,
                "humidity": 52,
                "quality_flags": ["nearby_station"],
            })
            consensus_id = upsert_hourly_consensus({
                "city": "chicago",
                "city_name": "Chicago",
                "target_date": "2026-06-29",
                "local_hour": "16:00",
                "valid_time": "2026-06-29T16:00:00",
                "station_id": "KORD",
                "forecast_temp": 92.9,
                "observed_temp": 91.94,
                "observation_source": "metar",
                "cloud_cover": 75,
                "humidity": 50,
                "source_count": 3,
                "source_weights": {"metar": 0.6, "pws": 0.2, "forecast": 0.2},
                "peak_marker": "observed_peak",
            })
            evidence = weather_evidence_summary("chicago", "2026-06-29")
            summary = dashboard_summary()

        self.assertGreater(metar_id, 0)
        self.assertEqual(metar_id, same_metar_id)
        self.assertGreater(mesonet_id, 0)
        self.assertGreater(consensus_id, 0)
        self.assertEqual(evidence["metar_reports"], 1)
        self.assertEqual(evidence["mesonet_observations"], 1)
        self.assertEqual(evidence["hourly_consensus"], 1)
        self.assertAlmostEqual(evidence["latest_hourly_consensus"][0]["residual"], -0.96, places=2)
        self.assertEqual(summary["metar_reports"], 1)
        self.assertEqual(summary["mesonet_observations"], 1)
        self.assertEqual(summary["hourly_consensus"], 1)

    def test_data_fetch_logs_persist_polywx_fetch_log_shape(self):
        db_path = test_db_path("data_fetch_logs")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            log_id = log_data_fetch(
                source="metar",
                stage="refresh_metar_reports",
                status="OK",
                duration_ms=2446,
                city="tokyo",
                target_date="2026-06-29",
                message="RJTT reports fetched",
                details={"rows": 48, "station": "RJTT"},
                started_at="2026-07-01T01:19:48+00:00",
                finished_at="2026-07-01T01:19:51+00:00",
            )
            rows = list_data_fetch_logs(10)
            summary = dashboard_summary()

        self.assertGreater(log_id, 0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "metar")
        self.assertEqual(rows[0]["status"], "OK")
        self.assertEqual(rows[0]["city"], "tokyo")
        self.assertIn("RJTT", rows[0]["details_json"])
        self.assertEqual(summary["data_fetch_logs"], 1)
        self.assertEqual(summary["latest_data_fetch_logs"][0]["stage"], "refresh_metar_reports")

    def test_dashboard_fetch_log_prefers_persisted_data_fetch_logs(self):
        db_path = test_db_path("dashboard_fetch_logs")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            log_data_fetch(
                source="forecast",
                stage="production_action",
                status="OK",
                duration_ms=94,
                city="tokyo",
                target_date="2026-06-29",
                message="processed 4 dates",
            )
            rows = _combined_fetch_log_payload([{
                "id": 7,
                "timestamp": "2026-07-01T00:00:00+00:00",
                "type": "weather",
                "message": "legacy refresh event",
                "data": {"source": "legacy", "duration_ms": 10},
            }], limit=10)

        self.assertEqual(rows[0]["source"], "forecast")
        self.assertEqual(rows[0]["status"], "OK")
        self.assertEqual(rows[0]["duration"], 94)
        self.assertEqual(rows[0]["event_type"], "data_fetch_log")
        self.assertTrue(any(row["source"] == "legacy" for row in rows))

    def test_metar_reports_build_station_local_hourly_consensus(self):
        db_path = test_db_path("metar_hourly_consensus")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            upsert_metar_report({
                "city": "chicago",
                "city_name": "Chicago",
                "station_id": "KORD",
                "report_time": "2026-06-29T21:20:00Z",
                "raw_text": "METAR KORD 292120Z 19012KT 10SM 32/23 A2988",
                "temperature": 89.6,
                "dew_point": 73.4,
            })
            upsert_metar_report({
                "city": "chicago",
                "city_name": "Chicago",
                "station_id": "KORD",
                "report_time": "2026-06-29T21:51:00Z",
                "raw_text": "METAR KORD 292151Z 20017G26KT 10SM 33/23 A2987",
                "temperature": 91.94,
                "dew_point": 73.0,
            })
            result = build_metar_hourly_consensus(["chicago"], target_date="2026-06-29")
            evidence = weather_evidence_summary("chicago", "2026-06-29")
            with connect(db_path) as conn:
                row = conn.execute(
                    "SELECT * FROM hourly_consensus WHERE city = ? AND target_date = ? AND local_hour = ?",
                    ("chicago", "2026-06-29", "16:00"),
                ).fetchone()

        self.assertTrue(result["ok"])
        self.assertEqual(result["reports_seen"], 2)
        self.assertEqual(result["rows_built"], 1)
        self.assertEqual(result["rows_upserted"], 1)
        self.assertEqual(evidence["hourly_consensus"], 1)
        self.assertIsNotNone(row)
        self.assertEqual(row["station_id"], "KORD")
        self.assertEqual(row["observation_source"], "metar")
        self.assertEqual(row["source_count"], 2)
        self.assertIsNone(row["forecast_temp"])
        self.assertIsNone(row["residual"])
        self.assertAlmostEqual(row["observed_temp"], 91.94, places=2)
        self.assertEqual(row["local_hour"], "16:00")

    def test_metar_hourly_consensus_accepts_epoch_report_time(self):
        db_path = test_db_path("metar_hourly_epoch")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            upsert_metar_report({
                "city": "chicago",
                "city_name": "Chicago",
                "station_id": "KORD",
                "report_time": "1782874260",
                "raw_text": "METAR KORD 010251Z 22010G20KT 10SM CLR 31/24 A2992",
                "temperature": 87.08,
            })
            result = build_metar_hourly_consensus(["chicago"])
            evidence = weather_evidence_summary("chicago", "2026-06-30")

        self.assertTrue(result["ok"])
        self.assertEqual(result["reports_seen"], 1)
        self.assertEqual(result["rows_built"], 1)
        self.assertEqual(evidence["hourly_consensus"], 1)
        self.assertEqual(evidence["latest_hourly_consensus"][0]["local_hour"], "21:00")
        self.assertAlmostEqual(evidence["latest_hourly_consensus"][0]["observed_temp"], 87.08, places=2)

    def test_settlement_registry_has_station_and_timezone_for_all_cities(self):
        self.assertEqual(len(SETTLEMENT_REGISTRY), 20)
        for city, profile in SETTLEMENT_REGISTRY.items():
            self.assertEqual(city, profile.city)
            self.assertTrue(profile.station_id)
            self.assertNotEqual(profile.timezone, "UTC")
            self.assertIn(profile.unit, {"F", "C"})

    def test_station_registry_sync_persists_layer1_station_rows(self):
        db_path = test_db_path("stations_registry")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            result = sync_station_registry(db_path)
            rows = list_stations(db_path)
            summary = dashboard_summary()

        chicago = next(row for row in rows if row["city_key"] == "chicago")
        self.assertTrue(result["ok"])
        self.assertEqual(result["synced"], 20)
        self.assertEqual(len(rows), 20)
        self.assertEqual(summary["stations"], 20)
        self.assertEqual(chicago["station_id"], "KORD")
        self.assertEqual(chicago["icao_id"], "KORD")
        self.assertEqual(chicago["timezone"], "America/Chicago")
        self.assertEqual(chicago["provider_station_ids"]["aviationweather"], "KORD")
        self.assertIn("METAR", chicago["nearby_observation_networks"])
        self.assertIn("requires rule/source verification", chicago["settlement_rule_text"])

    def test_station_row_parser_keeps_wmo_field_without_fabricating_ids(self):
        row = station_row_from_profile(SETTLEMENT_REGISTRY["tokyo"])
        provider_ids = json.loads(row["provider_station_ids_json"])
        networks = json.loads(row["nearby_observation_networks_json"])

        self.assertEqual(row["station_id"], "RJTT")
        self.assertEqual(row["icao_id"], "RJTT")
        self.assertEqual(row["wmo_id"], "")
        self.assertEqual(provider_ids["metar"], "RJTT")
        self.assertIn("AviationWeather", networks)

    def test_stations_api_exposes_layer1_station_surface(self):
        db_path = test_db_path("stations_api")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            payload = asyncio.run(stations_api(city="chicago", sync_registry=True))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["stations"][0]["city_key"], "chicago")
        self.assertEqual(payload["stations"][0]["station_id"], "KORD")
        self.assertEqual(payload["sync"]["synced"], 20)

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

    def test_settlement_rule_url_station_overrides_legacy_city_mapping(self):
        rule = infer_settlement_rule({
            "city": "paris",
            "city_name": "Paris",
            "date": "2026-06-25",
            "event_url": "https://polymarket.com/event/highest-temperature-in-paris-on-june-25-2026",
            "question": "Will the highest temperature in Paris be 30°C on June 25?",
            "settlement_rule": {
                "resolution_source_text": "Resolves from Wunderground station LFPB.",
                "source_url": "https://www.wunderground.com/history/daily/fr/bonneuil-en-france/LFPB",
                "station_id": "LFPG",
            },
        })
        contract = settlement_contract_from_rule(rule)
        self.assertEqual(rule.station_id, "LFPB")
        self.assertEqual(rule.station_name, "Paris-Le Bourget Airport")
        self.assertEqual(rule.contract_id, "highest-temperature-in-paris-on-june-25-2026")
        self.assertIsNotNone(contract["auto_verified_at"])

    def test_market_rule_batch_keeps_duplicate_exchange_market_ids_as_separate_buckets(self):
        db_path = test_db_path("market_rule_duplicate_keys")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            rules = []
            for question in (
                "Will the highest temperature in NYC be between 76-77掳F on June 23?",
                "Will the highest temperature in NYC be between 78-79掳F on June 23?",
            ):
                rules.append(
                    infer_settlement_rule(
                        {
                            "market_id": "shared-event-market",
                            "city": "nyc",
                            "city_name": "New York City",
                            "unit": "F",
                            "event_url": "https://polymarket.com/event/highest-temperature-in-nyc-on-june-23-2026",
                            "question": question,
                            "date": "2026-06-23",
                        }
                    ).to_dict()
                )
            upsert_market_rules(rules)
            with connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT market_id, exchange_market_id, question FROM market_rules ORDER BY question"
                ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["exchange_market_id"] for row in rows}, {"shared-event-market"})
        self.assertEqual(len({row["market_id"] for row in rows}), 2)
        self.assertTrue(all(str(row["market_id"]).startswith("rule:") for row in rows))

    def test_data_readiness_blocks_unverified_rules_and_missing_forecast_runs(self):
        db_path = test_db_path("data_readiness")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            rule = infer_settlement_rule(
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
            )
            upsert_market_rule(rule.to_dict())
            upsert_settlement_contracts([settlement_contract_from_rule(rule)])
            readiness = build_data_readiness(db_path)
        self.assertFalse(readiness["live_allowed"])
        stations_stage = next(stage for stage in readiness["stages"] if stage["key"] == "stations")
        self.assertEqual(stations_stage["status"], "ready")
        self.assertEqual(stations_stage["metrics"]["stations"], 20)
        self.assertEqual(readiness["summary"]["market_rules"], 1)
        self.assertEqual(readiness["summary"]["station_rows"], 20)
        self.assertEqual(readiness["production_phase"]["id"], "phase1_5")
        blocker_codes = {item["code"] for item in readiness["blockers"]}
        self.assertIn("settlement_rule_not_manually_verified", blocker_codes)
        self.assertIn("versioned_forecast_runs_missing", blocker_codes)

    def test_production_validation_report_keeps_live_locked_until_all_layers_pass(self):
        db_path = test_db_path("production_validation")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            report = build_production_validation_report(
                db_path,
                dashboard_runtime={
                    "scanner_status": "stopped",
                    "is_running": False,
                    "auto_simulation_enabled": False,
                },
            )

        self.assertEqual(report["validation_version"], "production-validation-v1")
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["live_allowed"])
        self.assertEqual(report["total_layers"], 5)
        self.assertEqual(
            [layer["key"] for layer in report["layers"]],
            [
                "data_foundation",
                "leakage_free_model",
                "realistic_paper_execution",
                "production_dashboard",
                "small_live_canary",
            ],
        )
        self.assertIn("data_foundation_not_ready", report["hard_blockers"])
        dashboard_layer = next(layer for layer in report["layers"] if layer["key"] == "production_dashboard")
        self.assertEqual(dashboard_layer["status"], "ready")
        self.assertTrue(report["next_actions"])

    def test_production_validation_action_targets_are_compact_by_default(self):
        action = {
            "key": "backfill_official_truth",
            "label": "Backfill truth",
            "targets": [{"city": f"city-{idx}"} for idx in range(8)],
        }

        compact = _compact_action(action, include_targets=False, preview_limit=3)
        verbose = _compact_action(action, include_targets=True, preview_limit=3)

        self.assertNotIn("targets", compact)
        self.assertEqual(compact["targets_count"], 8)
        self.assertEqual(compact["targets_preview"], [{"city": "city-0"}, {"city": "city-1"}, {"city": "city-2"}])
        self.assertIn("targets", verbose)
        self.assertEqual(len(verbose["targets"]), 8)

    def test_production_actions_are_whitelisted_and_dry_run_by_default(self):
        actions = {action["key"] for action in list_production_actions()}
        self.assertIn("refresh_clob_orderbooks", actions)
        self.assertIn("refresh_metar_reports", actions)
        self.assertIn("build_hourly_consensus", actions)
        self.assertIn("backfill_official_truth", actions)
        self.assertIn("backfill_forecast_members", actions)

        unknown = run_production_action("shell_anything", apply=True)
        self.assertFalse(unknown["ok"])
        self.assertEqual(unknown["reason"], "unsupported_production_action")

        dry_run = run_production_action("refresh_clob_orderbooks", limit=3)
        self.assertTrue(dry_run["ok"])
        self.assertEqual(dry_run["status"], "dry_run")
        self.assertEqual(dry_run["params"]["limit"], 3)

    def test_awc_metar_fetch_uses_scoped_json_request(self):
        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return [{
                    "stationId": "KORD",
                    "obsTime": "2026-06-29T16:00:00Z",
                    "rawOb": "METAR KORD 291600Z 18014KT 10SM 33/23 A2988",
                    "temp": 33,
                    "dewp": 23,
                }]

        class FakeSession:
            def __init__(self):
                self.calls = []

            def get(self, url, params, headers, timeout):
                self.calls.append((url, params, headers, timeout))
                return FakeResponse()

        session = FakeSession()
        rows = fetch_awc_metars(["kord", "KORD", "KLGA"], hours=48, session=session)

        self.assertEqual(len(rows), 1)
        _, params, headers, timeout = session.calls[0]
        self.assertEqual(params["ids"], "KLGA,KORD")
        self.assertEqual(params["format"], "json")
        self.assertEqual(params["hours"], 48.0)
        self.assertIn("WeatherBot", headers["User-Agent"])
        self.assertEqual(timeout, 20.0)

    def test_metar_refresh_persists_registry_station_reports(self):
        db_path = test_db_path("metar_refresh")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))

        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return [
                    {
                        "stationId": "KORD",
                        "obsTime": "1782874260",
                        "reportType": "METAR",
                        "rawOb": "METAR KORD 291600Z 18014KT 10SM 33/23 A2988",
                        "temp": 33,
                        "dewp": 23,
                        "wdir": 180,
                        "wspd": 14,
                        "wgst": 25,
                        "visib": 10,
                        "altim": 29.88,
                        "clouds": [{"cover": "FEW", "base": 4200}],
                    }
                ]

        class FakeSession:
            def get(self, url, params, headers, timeout):
                return FakeResponse()

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            result = refresh_metar_reports(["chicago"], hours=24, session=FakeSession())
            evidence = weather_evidence_summary("chicago")

        self.assertTrue(result["ok"])
        self.assertEqual(result["stations"], ["KORD"])
        self.assertEqual(result["reports_fetched"], 1)
        self.assertEqual(result["reports_upserted"], 1)
        self.assertEqual(evidence["metar_reports"], 1)
        self.assertEqual(evidence["latest_metar_reports"][0]["station_id"], "KORD")
        self.assertIn("+00:00", evidence["latest_metar_reports"][0]["report_time"])
        self.assertAlmostEqual(evidence["latest_metar_reports"][0]["temperature"], 91.4, places=1)

    def test_production_action_executes_whitelisted_metar_refresh(self):
        db_path = test_db_path("production_action_fetch_log")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            with patch("weatherbot_v3.production_actions.refresh_metar_reports") as mocked_refresh:
                mocked_refresh.return_value = {"ok": True, "reports_upserted": 2}
                with patch("weatherbot_v3.production_actions.build_data_readiness") as mocked_readiness:
                    mocked_readiness.return_value = {
                        "status": "blocked",
                        "score": 0.3,
                        "live_allowed": False,
                        "production_phase": {"blocked_keys": ["metar"]},
                    }
                    with patch("weatherbot_v3.production_actions.persist_data_readiness"):
                        result = run_production_action(
                            "refresh_metar_reports",
                            apply=True,
                            cities=["chicago"],
                            days=2,
                        )
            fetch_logs = list_data_fetch_logs(5)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "executed")
        mocked_refresh.assert_called_once_with(cities=["chicago"], hours=48.0)
        self.assertEqual(result["payload"]["reports_upserted"], 2)
        self.assertEqual(fetch_logs[0]["source"], "refresh_metar_reports")
        self.assertEqual(fetch_logs[0]["stage"], "production_action")
        self.assertEqual(fetch_logs[0]["status"], "OK")
        self.assertEqual(fetch_logs[0]["city"], "chicago")

    def test_production_action_executes_whitelisted_hourly_consensus_build(self):
        with patch("weatherbot_v3.production_actions.build_metar_hourly_consensus") as mocked_build:
            mocked_build.return_value = {"ok": True, "rows_upserted": 4}
            with patch("weatherbot_v3.production_actions.build_data_readiness") as mocked_readiness:
                mocked_readiness.return_value = {
                    "status": "blocked",
                    "score": 0.35,
                    "live_allowed": False,
                    "production_phase": {"blocked_keys": ["hourly_consensus"]},
                }
                with patch("weatherbot_v3.production_actions.persist_data_readiness"):
                    result = run_production_action(
                        "build_hourly_consensus",
                        apply=True,
                        cities=["chicago"],
                        start_date="2026-06-29",
                    )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "executed")
        mocked_build.assert_called_once_with(cities=["chicago"], target_date="2026-06-29")
        self.assertEqual(result["payload"]["rows_upserted"], 4)

    def test_production_action_requires_operator_confirmation_for_bulk_review(self):
        result = run_production_action(
            "review_mature_auto_contracts",
            apply=True,
            operator_confirmed=False,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "operator_confirmation_required")

    def test_dashboard_paper_validation_action_is_dry_run_by_default(self):
        result = asyncio.run(_run_paper_validation_action(
            ProductionActionRequest(action_key="run_paper_validation", apply=False, limit=3)
        ))

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["params"]["limit"], 3)

    def test_dashboard_paper_validation_action_requires_operator_confirmation(self):
        result = asyncio.run(_run_paper_validation_action(
            ProductionActionRequest(
                action_key="run_paper_validation",
                apply=True,
                operator_confirmed=False,
            )
        ))

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "operator_confirmation_required")

    def test_dashboard_paper_validation_action_executes_confirmed_paper_pass(self):
        with patch("dashboard_server._bulk_simulate_signals_once") as mocked_simulate:
            mocked_simulate.return_value = {
                "ok": True,
                "count": 1,
                "spent": 2.0,
                "remaining": 38.0,
                "skipped": 0,
            }
            result = asyncio.run(_run_paper_validation_action(
                ProductionActionRequest(
                    action_key="run_paper_validation",
                    apply=True,
                    operator_confirmed=True,
                    limit=4,
                )
            ))

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "executed")
        self.assertEqual(result["payload"]["count"], 1)
        mocked_simulate.assert_called_once_with(False, 4)

    def test_production_action_executes_whitelisted_orderbook_backfill(self):
        with patch("weatherbot_v3.production_actions.run_orderbook_backfill") as mocked_backfill:
            mocked_backfill.return_value = {"ok": 2, "failed": 0}
            with patch("weatherbot_v3.production_actions.build_data_readiness") as mocked_readiness:
                mocked_readiness.return_value = {
                    "status": "blocked",
                    "score": 0.25,
                    "live_allowed": False,
                    "production_phase": {"blocked_keys": ["orderbooks"]},
                }
                with patch("weatherbot_v3.production_actions.persist_data_readiness"):
                    result = run_production_action(
                        "refresh_clob_orderbooks",
                        apply=True,
                        limit=7,
                        start_date="2026-06-28",
                    )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "executed")
        mocked_backfill.assert_called_once_with(7, "2026-06-28", "")
        self.assertEqual(result["readiness"]["blocked_keys"], ["orderbooks"])

    def test_production_action_executes_whitelisted_truth_backfill(self):
        with patch("weatherbot_v3.production_actions.run_truth_backfill") as mocked_backfill:
            mocked_backfill.return_value = {"ok": 4, "eligible": 3, "requested": 5}
            with patch("weatherbot_v3.production_actions.build_data_readiness") as mocked_readiness:
                mocked_readiness.return_value = {
                    "status": "blocked",
                    "score": 0.4,
                    "live_allowed": False,
                    "production_phase": {"blocked_keys": ["truth"]},
                }
                with patch("weatherbot_v3.production_actions.persist_data_readiness"):
                    result = run_production_action(
                        "backfill_official_truth",
                        apply=True,
                        cities=["nyc", "seattle"],
                        limit=9,
                        start_date="2026-06-20",
                        end_date="2026-06-28",
                    )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "executed")
        mocked_backfill.assert_called_once_with("nyc,seattle", 9, "2026-06-20", "2026-06-28")
        self.assertEqual(result["payload"]["eligible"], 3)
        self.assertEqual(result["readiness"]["blocked_keys"], ["truth"])

    def test_production_action_forecast_archive_import_handles_missing_file(self):
        result = run_production_action(
            "backfill_forecast_members",
            apply=True,
            archive_path=str(TEST_DB_DIR / "missing-forecast-archive.jsonl"),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "executed")
        self.assertEqual(result["payload"]["reason"], "forecast_archive_missing")

    def test_production_action_executes_whitelisted_forecast_archive_import(self):
        archive_path = TEST_DB_DIR / "production-action-forecast-archive.jsonl"
        archive_path.write_text("{}", encoding="utf-8")
        self.addCleanup(lambda: archive_path.unlink(missing_ok=True))
        with patch("weatherbot_v3.production_actions.import_forecast_archive") as mocked_import:
            mocked_import.return_value = {"ok": True, "requested": 1, "imported": 1}
            with patch("weatherbot_v3.production_actions.build_data_readiness") as mocked_readiness:
                mocked_readiness.return_value = {
                    "status": "blocked",
                    "score": 0.5,
                    "live_allowed": False,
                    "production_phase": {"blocked_keys": ["forecast_runs"]},
                }
                with patch("weatherbot_v3.production_actions.persist_data_readiness"):
                    result = run_production_action(
                        "backfill_forecast_members",
                        apply=True,
                        archive_path=str(archive_path),
                    )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "executed")
        mocked_import.assert_called_once_with(archive_path, apply=True)
        self.assertEqual(result["payload"]["imported"], 1)

    def test_data_readiness_operator_action_when_auto_contracts_are_not_mature(self):
        db_path = test_db_path("data_readiness_future_auto")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            rule = infer_settlement_rule(
                {
                    "market_id": "nyc-future-auto",
                    "city": "nyc",
                    "city_name": "New York City",
                    "unit": "F",
                    "event_url": "https://polymarket.com/event/nyc-future-auto",
                    "question": "Will the highest temperature in NYC be between 80-81掳F on January 1?",
                    "description": "Resolves using Wunderground station KLGA history.",
                    "resolutionSource": "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA",
                    "date": "2099-01-01",
                }
            )
            upsert_market_rule(rule.to_dict())
            upsert_settlement_contracts([settlement_contract_from_rule(rule)])
            readiness = build_data_readiness(db_path)
        contract_metrics = next(
            stage["metrics"]
            for stage in readiness["stages"]
            if stage["key"] == "settlement_contracts"
        )
        self.assertEqual(contract_metrics["auto_verified_contracts"], 1)
        self.assertEqual(contract_metrics["mature_auto_verified_unreviewed_contracts"], 0)
        self.assertEqual(
            contract_metrics["contract_review_queue"]["future_auto_verified_unreviewed"],
            1,
        )
        self.assertEqual(
            contract_metrics["contract_review_queue"]["mature_auto_verified_unreviewed"],
            0,
        )
        self.assertIn("逐条人工核验", readiness["production_phase"]["operator_action"])

    def test_data_readiness_next_actions_explain_phase1_5_recovery(self):
        db_path = test_db_path("data_readiness_actions")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            rule = infer_settlement_rule(
                {
                    "market_id": "nyc-action-1",
                    "city": "nyc",
                    "city_name": "New York City",
                    "unit": "F",
                    "event_url": "https://polymarket.com/event/nyc-action",
                    "question": "Will the highest temperature in NYC be between 80-81°F on June 23?",
                    "description": "Resolves using Wunderground station KLGA history.",
                    "resolutionSource": "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA/date/2026-6-23",
                    "date": "2026-06-23",
                }
            )
            upsert_market_rule(rule.to_dict())
            upsert_settlement_contracts([settlement_contract_from_rule(rule)])
            readiness = build_data_readiness(db_path)

        actions = readiness["next_actions"]
        contract_metrics = next(
            stage["metrics"]
            for stage in readiness["stages"]
            if stage["key"] == "settlement_contracts"
        )
        self.assertEqual(
            contract_metrics["contract_review_queue"]["mature_auto_verified_unreviewed"],
            1,
        )
        self.assertEqual(
            contract_metrics["contract_review_targets"]["mature_auto_verified_unreviewed"][0]["city"],
            "nyc",
        )
        self.assertEqual(actions[0]["key"], "review_mature_auto_contracts")
        self.assertTrue(actions[0]["requires_operator"])
        self.assertIn("contracts-bulk-verify", actions[0]["command"])
        self.assertIn("--apply", actions[0]["apply_command"])
        self.assertIn("--note", actions[0]["apply_command"])
        self.assertIn("readiness queue", actions[0]["apply_command"])
        self.assertEqual(actions[0]["targets"][0]["city"], "nyc")
        self.assertEqual(actions[0]["targets"][0]["target_date"], "2026-06-23")
        self.assertEqual(actions[0]["targets"][0]["station_id"], "KLGA")
        action_keys = {action["key"] for action in actions}
        self.assertIn("refresh_forecast_runs", action_keys)
        self.assertIn("refresh_clob_orderbooks", action_keys)
        self.assertIn("backfill_official_truth", action_keys)
        self.assertEqual(actions[-1]["key"], "rerun_data_readiness")

    def test_data_readiness_requires_minimum_fresh_clob_depth(self):
        db_path = test_db_path("data_readiness_clob_min")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        now = datetime.now(timezone.utc).isoformat()
        with patch.dict(os.environ, {
            "V3_DB_PATH": str(db_path),
            "MIN_FRESH_CLOB_ORDERBOOKS": "5",
        }, clear=False):
            for index in range(4):
                insert_orderbook(f"market-{index}", {
                    "snapshot_key": f"market-{index}:fresh",
                    "snapshot_type": "clob",
                    "quote_timestamp": now,
                    "bids": [{"price": "0.40", "size": "10"}],
                    "asks": [{"price": "0.42", "size": "10"}],
                })
            readiness = build_data_readiness(db_path)
            orderbook_stage = next(stage for stage in readiness["stages"] if stage["key"] == "orderbooks")
            insert_orderbook("market-4", {
                "snapshot_key": "market-4:fresh",
                "snapshot_type": "clob",
                "quote_timestamp": now,
                "bids": [{"price": "0.40", "size": "10"}],
                "asks": [{"price": "0.42", "size": "10"}],
            })
            ready = build_data_readiness(db_path)
            ready_orderbook_stage = next(stage for stage in ready["stages"] if stage["key"] == "orderbooks")

        self.assertEqual(orderbook_stage["status"], "blocked")
        self.assertEqual(orderbook_stage["metrics"]["fresh_clob_snapshots"], 4)
        self.assertEqual(orderbook_stage["metrics"]["fresh_clob_with_depth_snapshots"], 4)
        self.assertEqual(orderbook_stage["metrics"]["minimum_fresh_clob_snapshots"], 5)
        self.assertEqual(orderbook_stage["metrics"]["fresh_clob_snapshot_gap"], 1)
        self.assertIn(
            {"code": "fresh_clob_depth_below_min", "count": 1},
            orderbook_stage["reasons"],
        )
        self.assertEqual(ready_orderbook_stage["status"], "ready")

    def test_data_readiness_does_not_count_empty_clob_arrays_as_depth(self):
        db_path = test_db_path("data_readiness_empty_clob")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        now = datetime.now(timezone.utc).isoformat()
        with patch.dict(os.environ, {
            "V3_DB_PATH": str(db_path),
            "MIN_FRESH_CLOB_ORDERBOOKS": "1",
        }, clear=False):
            insert_orderbook("market-empty", {
                "snapshot_key": "market-empty:fresh",
                "snapshot_type": "clob",
                "quote_timestamp": now,
                "bids": [],
                "asks": [],
            })
            readiness = build_data_readiness(db_path)
            orderbook_stage = next(stage for stage in readiness["stages"] if stage["key"] == "orderbooks")

        self.assertEqual(orderbook_stage["status"], "blocked")
        self.assertEqual(orderbook_stage["metrics"]["fresh_clob_snapshots"], 1)
        self.assertEqual(orderbook_stage["metrics"]["fresh_clob_with_depth_snapshots"], 0)
        self.assertIn(
            {"code": "fresh_clob_depth_missing", "count": 1},
            orderbook_stage["reasons"],
        )

    def test_settlement_contract_manual_verification_updates_contract_and_rules(self):
        db_path = test_db_path("contract_verification")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            rule = infer_settlement_rule(
                {
                    "market_id": "nyc-verify-1",
                    "city": "nyc",
                    "city_name": "New York City",
                    "unit": "F",
                    "event_url": "https://polymarket.com/event/nyc-verify",
                    "question": "Will the highest temperature in NYC be between 80-81掳F on June 23?",
                    "description": "Resolves according to Wunderground station history.",
                    "date": "2026-06-23",
                }
            )
            upsert_market_rule(rule.to_dict())
            upsert_settlement_contracts([settlement_contract_from_rule(rule)])
            before = list_settlement_contracts(status="unverified", limit=10)
            verified = set_settlement_contract_verification("nyc-verify", True, reviewer="test", note="station checked")
            after = list_settlement_contracts(status="unverified", limit=10)
            with connect(db_path) as conn:
                rule_row = conn.execute("SELECT manual_verified_at FROM market_rules WHERE market_id = ?", ("nyc-verify-1",)).fetchone()
        self.assertEqual(before["summary"]["unverified"], 1)
        self.assertEqual(after["summary"]["manual_verified"], 1)
        self.assertEqual(after["summary"]["unverified"], 0)
        self.assertEqual(verified["manual_verified_by"], "test")
        self.assertEqual(verified["manual_verification_note"], "station checked")
        self.assertEqual(verified["manual_verification_snapshot"]["snapshot_version"], "manual-contract-review-v1")
        self.assertEqual(verified["manual_verification_snapshot"]["reviewer"], "test")
        self.assertEqual(verified["manual_verification_snapshot"]["note"], "station checked")
        self.assertEqual(verified["manual_verification_snapshot"]["review_status_before"], "manual-required")
        self.assertIn("manual_required", verified["manual_verification_snapshot"]["review_tags_before"])
        self.assertIn("event_slug_present", verified["manual_verification_snapshot"]["verification_evidence"])
        self.assertIsNotNone(rule_row["manual_verified_at"])

    def test_settlement_contract_manual_verification_requires_note(self):
        db_path = test_db_path("contract_verification_requires_note")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            rule = infer_settlement_rule(
                {
                    "market_id": "nyc-note-required-1",
                    "city": "nyc",
                    "city_name": "New York City",
                    "unit": "F",
                    "event_url": "https://polymarket.com/event/nyc-note-required",
                    "question": "Will the highest temperature in NYC be between 80-81掳F on June 23?",
                    "description": "Resolves according to Wunderground station history.",
                    "date": "2026-06-23",
                }
            )
            upsert_settlement_contracts([settlement_contract_from_rule(rule)])
            with self.assertRaisesRegex(ValueError, "manual verification note is required"):
                set_settlement_contract_verification("nyc-note-required", True, reviewer="test", note=" ")
            with self.assertRaisesRegex(ValueError, "manual verification note is required"):
                bulk_settlement_contract_verification(["nyc-note-required"], reviewer="test", note="", apply=True)
            dry_run = bulk_settlement_contract_verification(["nyc-note-required"], reviewer="test", note="", apply=False)

        self.assertFalse(dry_run["applied"])

    def test_contract_list_supports_review_queue_statuses(self):
        db_path = test_db_path("contract_review_statuses")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            mature_auto = infer_settlement_rule({
                "market_id": "nyc-mature-auto-1",
                "city": "nyc",
                "city_name": "New York City",
                "unit": "F",
                "event_url": "https://polymarket.com/event/nyc-mature-auto",
                "question": "Will the highest temperature in NYC be between 80-81°F on June 23?",
                "description": "Resolves using Wunderground station KLGA history.",
                "resolutionSource": "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA/date/2026-6-23",
                "date": "2026-06-23",
            })
            future_auto = infer_settlement_rule({
                "market_id": "nyc-future-auto-status-1",
                "city": "nyc",
                "city_name": "New York City",
                "unit": "F",
                "event_url": "https://polymarket.com/event/nyc-future-auto-status",
                "question": "Will the highest temperature in NYC be between 80-81°F on January 1?",
                "description": "Resolves using Wunderground station KLGA history.",
                "resolutionSource": "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA",
                "date": "2099-01-01",
            })
            manual_required = infer_settlement_rule({
                "market_id": "nyc-manual-required-1",
                "city": "nyc",
                "city_name": "New York City",
                "unit": "F",
                "event_url": "https://polymarket.com/event/nyc-manual-required",
                "question": "Will the highest temperature in NYC be between 82-83°F on June 24?",
                "description": "Resolves using weather history.",
                "date": "2026-06-24",
            })
            source_missing = settlement_contract_from_rule(manual_required)
            source_missing = {
                **source_missing,
                "contract_id": "nyc-source-missing",
                "event_slug": "nyc-source-missing",
                "source_url": "",
                "resolution_source_text": "",
            }
            upsert_settlement_contracts([
                settlement_contract_from_rule(mature_auto),
                settlement_contract_from_rule(future_auto),
                settlement_contract_from_rule(manual_required),
                source_missing,
            ])

            mature_rows = list_settlement_contracts("mature-auto")["contracts"]
            future_rows = list_settlement_contracts("future-auto")["contracts"]
            manual_rows = list_settlement_contracts("manual-required")["contracts"]
            missing_rows = list_settlement_contracts("source-missing")["contracts"]
            low_confidence_rows = list_settlement_contracts("low-confidence")["contracts"]
            mature_ids = {row["contract_id"] for row in mature_rows}
            future_ids = {row["contract_id"] for row in future_rows}
            manual_ids = {row["contract_id"] for row in manual_rows}
            missing_ids = {row["contract_id"] for row in missing_rows}
            low_confidence_ids = {row["contract_id"] for row in low_confidence_rows}
            mature_row = next(row for row in mature_rows if row["contract_id"] == "nyc-mature-auto")
            future_row = next(row for row in future_rows if row["contract_id"] == "nyc-future-auto-status")
            manual_row = next(row for row in manual_rows if row["contract_id"] == "nyc-manual-required")
            missing_row = next(row for row in missing_rows if row["contract_id"] == "nyc-source-missing")

        self.assertIn("nyc-mature-auto", mature_ids)
        self.assertIn("nyc-future-auto-status", future_ids)
        self.assertIn("nyc-manual-required", manual_ids)
        self.assertIn("nyc-source-missing", manual_ids)
        self.assertIn("nyc-source-missing", missing_ids)
        self.assertIn("nyc-manual-required", low_confidence_ids)
        self.assertEqual("mature-auto", mature_row["review_status"])
        self.assertIn("auto_verified", mature_row["review_tags"])
        self.assertIn("mature", mature_row["review_tags"])
        self.assertEqual("future-auto", future_row["review_status"])
        self.assertIn("pending_settlement", future_row["review_tags"])
        self.assertEqual("manual-required", manual_row["review_status"])
        self.assertIn("manual_required", manual_row["review_tags"])
        self.assertIn("source_missing", missing_row["review_tags"])

    def test_bulk_contract_verification_only_applies_auto_verified_contracts(self):
        db_path = test_db_path("bulk_contract_verification")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            strong_rule = infer_settlement_rule(
                {
                    "market_id": "nyc-bulk-strong-1",
                    "city": "nyc",
                    "city_name": "New York City",
                    "unit": "F",
                    "event_url": "https://polymarket.com/event/nyc-bulk-strong",
                    "question": "Will the highest temperature in NYC be between 80-81°F on June 23?",
                    "description": "Resolves using Wunderground station KLGA history.",
                    "resolutionSource": "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA/date/2026-6-23",
                    "date": "2026-06-23",
                }
            )
            weak_rule = infer_settlement_rule(
                {
                    "market_id": "nyc-bulk-weak-1",
                    "city": "nyc",
                    "city_name": "New York City",
                    "unit": "F",
                    "event_url": "https://polymarket.com/event/nyc-bulk-weak",
                    "question": "Will the highest temperature in NYC be between 82-83°F on June 24?",
                    "description": "Resolves using weather history.",
                    "date": "2026-06-24",
                }
            )
            upsert_market_rules([strong_rule.to_dict(), weak_rule.to_dict()])
            upsert_settlement_contracts([
                settlement_contract_from_rule(strong_rule),
                settlement_contract_from_rule(weak_rule),
            ])
            dry_run = bulk_settlement_contract_verification(
                ["nyc-bulk-strong", "nyc-bulk-weak"],
                reviewer="test",
                note="bulk checked",
                apply=False,
            )
            applied = bulk_settlement_contract_verification(
                ["nyc-bulk-strong", "nyc-bulk-weak"],
                reviewer="test",
                note="bulk checked",
                apply=True,
            )
            with connect(db_path) as conn:
                strong = conn.execute(
                    "SELECT manual_verified_at FROM market_rules WHERE market_id = ?",
                    ("nyc-bulk-strong-1",),
                ).fetchone()
                weak = conn.execute(
                    "SELECT manual_verified_at FROM market_rules WHERE market_id = ?",
                    ("nyc-bulk-weak-1",),
                ).fetchone()

        self.assertFalse(dry_run["applied"])
        self.assertEqual(dry_run["selected"], 1)
        self.assertEqual(dry_run["verified"], 0)
        self.assertTrue(applied["applied"])
        self.assertEqual(applied["selected"], 1)
        self.assertEqual(applied["verified"], 1)
        self.assertIn("nyc-bulk-weak", applied["skipped_requested"])
        self.assertIsNotNone(strong["manual_verified_at"])
        self.assertIsNone(weak["manual_verified_at"])

    def test_bulk_contract_verification_mature_only_skips_pending_contracts(self):
        db_path = test_db_path("bulk_contract_mature_only")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            today = datetime.now(timezone.utc).date()
            mature_date = today - timedelta(days=7)
            pending_date = today + timedelta(days=7)
            mature_label = f"{mature_date.strftime('%B')} {mature_date.day}"
            pending_label = f"{pending_date.strftime('%B')} {pending_date.day}"
            mature_rule = infer_settlement_rule(
                {
                    "market_id": "nyc-mature-1",
                    "city": "nyc",
                    "city_name": "New York City",
                    "unit": "F",
                    "event_url": "https://polymarket.com/event/nyc-mature",
                    "question": f"Will the highest temperature in NYC be between 80-81°F on {mature_label}?",
                    "description": "Resolves using Wunderground station KLGA history.",
                    "resolutionSource": f"https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA/date/{mature_date.year}-{mature_date.month}-{mature_date.day}",
                    "date": mature_date.isoformat(),
                }
            )
            pending_rule = infer_settlement_rule(
                {
                    "market_id": "nyc-pending-1",
                    "city": "nyc",
                    "city_name": "New York City",
                    "unit": "F",
                    "event_url": "https://polymarket.com/event/nyc-pending",
                    "question": f"Will the highest temperature in NYC be between 80-81°F on {pending_label}?",
                    "description": "Resolves using Wunderground station KLGA history.",
                    "resolutionSource": f"https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA/date/{pending_date.year}-{pending_date.month}-{pending_date.day}",
                    "date": pending_date.isoformat(),
                }
            )
            upsert_market_rules([mature_rule.to_dict(), pending_rule.to_dict()])
            upsert_settlement_contracts([
                settlement_contract_from_rule(mature_rule),
                settlement_contract_from_rule(pending_rule),
            ])
            result = bulk_settlement_contract_verification(limit=10, mature_only=True, apply=False)

        self.assertTrue(result["mature_only"])
        self.assertEqual(result["selected"], 1)
        self.assertEqual(result["contracts"][0]["contract_id"], "nyc-mature")

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

    def test_forecast_hourly_points_use_latest_source_run(self):
        db_path = test_db_path("forecast_hourly_points")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        base_run = {
            "city": "chicago",
            "target_date": "2026-06-29",
            "source": "ecmwf",
            "provider": "archive",
            "model": "ifs",
            "run_type": "forecast",
            "station_id": "KORD",
            "timezone": "America/Chicago",
            "unit": "F",
            "mean_high": 90,
            "std_high": 1,
            "member_count": 2,
            "training_eligible": True,
        }
        older = {
            **base_run,
            "run_key": "ecmwf:chicago:2026-06-29:old",
            "retrieved_at": "2026-06-28T00:00:00+00:00",
        }
        newer = {
            **base_run,
            "run_key": "ecmwf:chicago:2026-06-29:new",
            "retrieved_at": "2026-06-28T12:00:00+00:00",
        }
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            insert_forecast_run(older, [
                {"member_id": "old01", "high_temp": 70, "hourly": [{"valid_at": "2026-06-29T12:00:00+00:00", "temperature_2m": 70}]},
            ])
            insert_forecast_run(newer, [
                {"member_id": "new01", "high_temp": 80, "hourly": [{
                    "valid_at": "2026-06-29T12:00:00+00:00",
                    "temperature_2m": 80,
                    "relative_humidity_2m": 40,
                    "cloud_cover": 70,
                    "precipitation": 0.1,
                    "precipitation_probability": 20,
                    "wind_speed_10m": 8,
                    "wind_direction_10m": 350,
                    "pressure_msl": 1012,
                    "dew_point_2m": 70,
                    "weather_code": 0,
                }]},
                {"member_id": "new02", "high_temp": 82, "hourly": [{
                    "valid_at": "2026-06-29T12:00:00+00:00",
                    "temperature_2m": 82,
                    "relative_humidity_2m": 60,
                    "cloud_cover": 90,
                    "precipitation": 0.3,
                    "precipitation_probability": 40,
                    "wind_speed_10m": 10,
                    "wind_direction_10m": 10,
                    "pressure_msl": 1014,
                    "dew_point_2m": 72,
                    "weather_code": 2,
                }]},
            ])
            points = forecast_hourly_points({"chicago": {"2026-06-29"}}, db_path=db_path)

        self.assertIn("chicago", points)
        self.assertEqual(len(points["chicago"]), 1)
        point = points["chicago"][0]
        self.assertEqual(point["timestamp"], "2026-06-29T12:00:00+00:00")
        self.assertEqual(point["best"], 81)
        self.assertEqual(point["humidity"], 50)
        self.assertEqual(point["cloud_cover"], 80)
        self.assertAlmostEqual(point["precipitation"], 0.2)
        self.assertEqual(point["precipitation_probability"], 30)
        self.assertEqual(point["wind_speed"], 9)
        self.assertTrue(point["wind_direction"] < 1 or point["wind_direction"] > 359)
        self.assertEqual(point["pressure"], 1013)
        self.assertEqual(point["dew_point"], 71)
        self.assertEqual(point["condition"], "Clear")
        self.assertEqual(point["member_count"], 2)
        self.assertTrue(point["archive"])

    def test_hourly_consensus_points_read_metar_observations(self):
        db_path = test_db_path("hourly_consensus_points")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            upsert_hourly_consensus({
                "city": "chicago",
                "city_name": "Chicago",
                "target_date": "2026-06-29",
                "local_hour": "16:00",
                "valid_time": "2026-06-29T16:51:00-05:00",
                "station_id": "KORD",
                "observed_temp": 91.94,
                "forecast_temp": 92.0,
                "humidity": 75,
                "cloud_cover": 55,
                "observation_source": "metar",
                "source_count": 1,
                "peak_marker": "daily_high_so_far",
            })
            points = hourly_consensus_points({"chicago": {"2026-06-29"}}, db_path=db_path)

        self.assertIn("chicago", points)
        self.assertEqual(len(points["chicago"]), 1)
        point = points["chicago"][0]
        self.assertEqual(point["local_hour"], "16:00")
        self.assertEqual(point["station_id"], "KORD")
        self.assertEqual(point["source"], "metar")
        self.assertAlmostEqual(point["metar"], 91.94, places=2)
        self.assertAlmostEqual(point["best"], 92.0, places=2)
        self.assertTrue(point["hourly_consensus"])

    def test_hourly_merge_preserves_forecast_and_adds_metar(self):
        rows = _merge_hourly_points(
            [{
                "target_date": "2026-06-29",
                "timestamp": "2026-06-29T16:00:00-05:00",
                "local_hour": "16:00",
                "best": 92.0,
                "ensemble_mean": 92.0,
                "source": "forecast",
            }],
            [{
                "target_date": "2026-06-29",
                "timestamp": "2026-06-29T16:00:00-05:00",
                "local_hour": "16:00",
                "best": None,
                "ensemble_mean": None,
                "metar": 91.0,
                "source": "metar",
                "hourly_consensus": True,
            }],
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["best"], 92.0)
        self.assertEqual(rows[0]["ensemble_mean"], 92.0)
        self.assertEqual(rows[0]["metar"], 91.0)
        self.assertEqual(rows[0]["source"], "metar")
        self.assertTrue(rows[0]["hourly_consensus"])

    def test_weather_city_series_uses_hourly_consensus_without_forecast_snapshots(self):
        db_path = test_db_path("weather_city_series_hourly_consensus")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            upsert_hourly_consensus({
                "city": "chicago",
                "city_name": "Chicago",
                "target_date": "2026-06-29",
                "local_hour": "16:00",
                "valid_time": "2026-06-29T16:51:00-05:00",
                "station_id": "KORD",
                "observed_temp": 91.94,
                "forecast_temp": None,
                "humidity": 75,
                "cloud_cover": 55,
                "observation_source": "metar",
                "source_count": 1,
            })
            rows = _build_weather_city_series([{
                "city": "chicago",
                "city_name": "Chicago",
                "date": "2026-06-29",
                "unit": "F",
                "station": "KORD",
                "forecast_snapshots": [],
            }])
            payload = _build_city_evidence_payload(rows, [], [])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["hourly_count"], 1)
        self.assertAlmostEqual(rows[0]["latest_metar"], 91.94, places=2)
        self.assertEqual(rows[0]["humidity_status"], "available")
        day = payload[0]["dates"][0]
        self.assertEqual(day["modules"]["hourly_temperature"]["rows"], 1)
        self.assertEqual(day["modules"]["metar"]["rows"], 1)
        self.assertEqual(day["modules"]["forecast"]["rows"], 0)

    def test_forecast_archive_import_persists_no_leak_members(self):
        db_path = test_db_path("forecast_archive_import")
        archive_path = TEST_DB_DIR / "forecast-archive-import.json"
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        self.addCleanup(lambda: archive_path.unlink(missing_ok=True))
        archive_path.write_text(json.dumps({
            "runs": [
                {
                    "city": "nyc",
                    "target_date": "2026-06-23",
                    "source": "ecmwf",
                    "provider": "ecmwf_archive",
                    "model": "ecmwf_ifs",
                    "model_version": "archive-test",
                    "run_at": "2026-06-22T12:00:00+00:00",
                    "retrieved_at": "2026-06-22T12:10:00+00:00",
                    "valid_at": "2026-06-23T18:00:00+00:00",
                    "lead_hours": 30,
                    "members": [
                        {"member_id": "m01", "high_temp": 80.0},
                        {"member_id": "m02", "high_temp": 82.0},
                    ],
                }
            ]
        }), encoding="utf-8")

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            summary = import_forecast_archive(archive_path, apply=True)
            with connect(db_path) as conn:
                run = conn.execute("SELECT city, source, run_type, horizon, mean_high, training_eligible FROM forecast_runs").fetchone()
                member_count = conn.execute("SELECT COUNT(*) FROM forecast_members").fetchone()[0]

        self.assertEqual(summary["valid"], 1)
        self.assertEqual(summary["imported"], 1)
        self.assertEqual(summary["by_city"], {"nyc": 1})
        self.assertEqual(run["city"], "nyc")
        self.assertEqual(run["source"], "ecmwf")
        self.assertEqual(run["run_type"], "forecast")
        self.assertEqual(run["horizon"], "d1")
        self.assertEqual(run["mean_high"], 81.0)
        self.assertEqual(run["training_eligible"], 1)
        self.assertEqual(member_count, 2)

    def test_forecast_archive_dry_run_does_not_write(self):
        db_path = test_db_path("forecast_archive_dry_run")
        archive_path = TEST_DB_DIR / "forecast-archive-dry-run.jsonl"
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        self.addCleanup(lambda: archive_path.unlink(missing_ok=True))
        archive_path.write_text(json.dumps({
            "city": "nyc",
            "target_date": "2026-06-23",
            "source": "gfs_ensemble",
            "provider": "noaa_archive",
            "model": "gefs",
            "model_version": "archive-test",
            "run_at": "2026-06-22T00:00:00+00:00",
            "valid_at": "2026-06-23T18:00:00+00:00",
            "lead_hours": 42,
            "members": [{"member_id": "p01", "high_temp": 79.5}],
        }) + "\n", encoding="utf-8")

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            summary = import_forecast_archive(archive_path, apply=False)
            init_v3_db()
            with connect(db_path) as conn:
                run_count = conn.execute("SELECT COUNT(*) FROM forecast_runs").fetchone()[0]

        self.assertEqual(summary["valid"], 1)
        self.assertEqual(summary["imported"], 0)
        self.assertEqual(run_count, 0)

    def test_forecast_archive_rejects_leaky_d1_run(self):
        db_path = test_db_path("forecast_archive_leaky")
        archive_path = TEST_DB_DIR / "forecast-archive-leaky.json"
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        self.addCleanup(lambda: archive_path.unlink(missing_ok=True))
        archive_path.write_text(json.dumps([
            {
                "city": "nyc",
                "target_date": "2026-06-23",
                "source": "ecmwf",
                "provider": "ecmwf_archive",
                "model": "ecmwf_ifs",
                "model_version": "archive-test",
                "run_at": "2026-06-23T05:00:00+00:00",
                "valid_at": "2026-06-23T18:00:00+00:00",
                "lead_hours": 30,
                "members": [{"member_id": "m01", "high_temp": 80.0}],
            }
        ]), encoding="utf-8")

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            summary = import_forecast_archive(archive_path, apply=True)
            with connect(db_path) as conn:
                run_count = conn.execute("SELECT COUNT(*) FROM forecast_runs").fetchone()[0]

        self.assertEqual(summary["valid"], 0)
        self.assertEqual(summary["imported"], 0)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["errors"][0]["reason"], "run_at_after_target_start")
        self.assertEqual(run_count, 0)

    def test_forecast_archive_rejects_station_mismatch(self):
        db_path = test_db_path("forecast_archive_station_mismatch")
        archive_path = TEST_DB_DIR / "forecast-archive-station-mismatch.json"
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        self.addCleanup(lambda: archive_path.unlink(missing_ok=True))
        archive_path.write_text(json.dumps([
            {
                "city": "dallas",
                "target_date": "2026-06-23",
                "station_id": "KDFW",
                "unit": "F",
                "source": "ecmwf",
                "provider": "ecmwf_archive",
                "model": "ecmwf_ifs",
                "model_version": "archive-test",
                "run_at": "2026-06-22T12:00:00+00:00",
                "valid_at": "2026-06-23T18:00:00+00:00",
                "lead_hours": 30,
                "members": [{"member_id": "m01", "high_temp": 95.0}],
            }
        ]), encoding="utf-8")

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            summary = import_forecast_archive(archive_path, apply=True)
            with connect(db_path) as conn:
                run_count = conn.execute("SELECT COUNT(*) FROM forecast_runs").fetchone()[0]

        self.assertEqual(summary["valid"], 0)
        self.assertEqual(summary["errors"][0]["reason"], "station_id_mismatch")
        self.assertEqual(run_count, 0)

    def test_forecast_archive_rejects_unit_mismatch(self):
        db_path = test_db_path("forecast_archive_unit_mismatch")
        archive_path = TEST_DB_DIR / "forecast-archive-unit-mismatch.json"
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        self.addCleanup(lambda: archive_path.unlink(missing_ok=True))
        archive_path.write_text(json.dumps([
            {
                "city": "paris",
                "target_date": "2026-06-23",
                "station_id": "LFPB",
                "unit": "F",
                "source": "gfs_ensemble",
                "provider": "noaa_archive",
                "model": "gefs",
                "model_version": "archive-test",
                "run_at": "2026-06-22T00:00:00+00:00",
                "valid_at": "2026-06-23T12:00:00+00:00",
                "lead_hours": 36,
                "members": [{"member_id": "p01", "high_temp": 80.0}],
            }
        ]), encoding="utf-8")

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            summary = import_forecast_archive(archive_path, apply=True)
            with connect(db_path) as conn:
                run_count = conn.execute("SELECT COUNT(*) FROM forecast_runs").fetchone()[0]

        self.assertEqual(summary["valid"], 0)
        self.assertEqual(summary["errors"][0]["reason"], "unit_mismatch")
        self.assertEqual(run_count, 0)

    def test_model_dataset_audit_requires_no_leak_forecasts_and_verified_contract(self):
        db_path = test_db_path("model_dataset_audit")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            rule = infer_settlement_rule(
                {
                    "market_id": "nyc-dataset-1",
                    "city": "nyc",
                    "city_name": "New York City",
                    "unit": "F",
                    "event_url": "https://polymarket.com/event/nyc-dataset",
                    "question": "Will the highest temperature in NYC be between 80-81°F on June 23?",
                    "description": "Resolves using Wunderground station KLGA history.",
                    "resolutionSource": "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA/date/2026-6-23",
                    "date": "2026-06-23",
                }
            )
            upsert_market_rule(rule.to_dict())
            upsert_settlement_contracts([settlement_contract_from_rule(rule)])
            set_settlement_contract_verification("nyc-dataset", True, reviewer="test", note="station checked")
            upsert_truth_observation({
                "city": "nyc",
                "city_name": "New York City",
                "target_date": "2026-06-23",
                "station_id": "KLGA",
                "station_name": "LaGuardia Airport",
                "unit": "F",
                "actual_temp": 80,
                "provider": "nws_station",
                "source_url": "https://example.test/noaa",
                "observation_count": 24,
                "source_confidence": 0.95,
                "calibration_eligible": True,
                "reason_if_ineligible": "",
            })
            for source in ("ecmwf", "gfs_ensemble"):
                insert_forecast_run(
                    {
                        "run_key": f"{source}:nyc:2026-06-23:no-leak",
                        "city": "nyc",
                        "target_date": "2026-06-23",
                        "source": source,
                        "provider": "open_meteo",
                        "model": source,
                        "model_version": "test",
                        "run_type": "forecast",
                        "run_at": "2026-06-22T12:00:00+00:00",
                        "retrieved_at": "2026-06-22T12:05:00+00:00",
                        "valid_at": "2026-06-23T18:00:00+00:00",
                        "lead_hours": 30,
                        "station_id": "KLGA",
                        "timezone": "America/New_York",
                        "unit": "F",
                        "mean_high": 80,
                        "std_high": 2,
                        "training_eligible": True,
                    },
                    [{"member_id": "m1", "high_temp": 80.2}],
                )
            insert_forecast_run(
                {
                    "run_key": "ecmwf:nyc:2026-06-23:future",
                    "city": "nyc",
                    "target_date": "2026-06-23",
                    "source": "ecmwf",
                    "provider": "open_meteo",
                    "model": "ecmwf",
                    "model_version": "test",
                    "run_type": "forecast",
                    "run_at": "2026-06-24T12:00:00+00:00",
                    "retrieved_at": "2026-06-24T12:05:00+00:00",
                    "valid_at": "2026-06-23T18:00:00+00:00",
                    "lead_hours": 30,
                    "station_id": "KLGA",
                    "timezone": "America/New_York",
                    "unit": "F",
                    "mean_high": 81,
                    "std_high": 1,
                    "training_eligible": True,
                },
                [{"member_id": "future", "high_temp": 81.0}],
            )
            insert_orderbook("nyc-dataset-1", {
                "snapshot_key": "nyc-dataset-ob",
                "bids": [{"price": "0.30", "size": "50"}],
                "asks": [{"price": "0.33", "size": "40"}],
                "quote_timestamp": "2026-06-22T12:10:00+00:00",
            })
            audit = build_model_dataset_audit(db_path, min_samples=1)

        self.assertEqual(audit["status"], "ready")
        self.assertEqual(audit["summary"]["training_eligible_samples"], 1)
        self.assertEqual(audit["summary"]["baseline_ready_samples"], 1)
        self.assertEqual(audit["summary"]["replay_ready_samples"], 1)
        self.assertEqual(audit["leakage_flags"]["forecast_after_target_start"], 1)
        self.assertEqual(audit["samples"][0]["no_leak_forecast_runs"], 2)

    def test_model_dataset_audit_next_actions_prioritize_contract_review(self):
        db_path = test_db_path("model_dataset_actions")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            rule = infer_settlement_rule(
                {
                    "market_id": "nyc-action-1",
                    "city": "nyc",
                    "city_name": "New York City",
                    "unit": "F",
                    "event_url": "https://polymarket.com/event/nyc-action",
                    "question": "Will the highest temperature in NYC be between 80-81°F on June 23?",
                    "description": "Resolves using Wunderground station KLGA history.",
                    "resolutionSource": "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA/date/2026-6-23",
                    "date": "2026-06-23",
                }
            )
            upsert_market_rule(rule.to_dict())
            upsert_settlement_contracts([settlement_contract_from_rule(rule)])
            audit = build_model_dataset_audit(db_path, min_samples=1)

        self.assertEqual(audit["next_actions"][0]["key"], "review_auto_verified_contracts")
        self.assertTrue(audit["next_actions"][0]["requires_operator"])
        self.assertIn("contracts-bulk-verify", audit["next_actions"][0]["command"])
        self.assertIn("--mature-only", audit["next_actions"][0]["command"])
        self.assertIn("--apply", audit["next_actions"][0]["apply_command"])
        self.assertIn("--note", audit["next_actions"][0]["apply_command"])
        self.assertIn("model dataset audit", audit["next_actions"][0]["apply_command"])

    def test_model_dataset_forecast_gap_requires_historical_archive(self):
        db_path = test_db_path("model_dataset_forecast_archive")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            rule = infer_settlement_rule(
                {
                    "market_id": "nyc-archive-1",
                    "city": "nyc",
                    "city_name": "New York City",
                    "unit": "F",
                    "event_url": "https://polymarket.com/event/nyc-archive",
                    "question": "Will the highest temperature in NYC be between 80-81掳F on June 23?",
                    "description": "Resolves using Wunderground station KLGA history.",
                    "resolutionSource": "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA/date/2026-6-23",
                    "date": "2026-06-23",
                }
            )
            upsert_market_rule(rule.to_dict())
            upsert_settlement_contracts([settlement_contract_from_rule(rule)])
            set_settlement_contract_verification("nyc-archive", True, reviewer="test", note="station checked")
            upsert_truth_observation({
                "city": "nyc",
                "city_name": "New York City",
                "target_date": "2026-06-23",
                "station_id": "KLGA",
                "station_name": "LaGuardia Airport",
                "unit": "F",
                "actual_temp": 81.0,
                "provider": "nws_station",
                "source_url": "https://api.weather.gov/stations/KLGA/observations",
                "observation_count": 24,
                "source_confidence": 0.95,
                "calibration_eligible": True,
                "reason_if_ineligible": "",
                "is_final": True,
                "is_preliminary": False,
                "quality_flags": ["official_station"],
            })
            audit = build_model_dataset_audit(db_path, min_samples=1)
        forecast_action = next(action for action in audit["next_actions"] if action["key"] == "backfill_forecast_members")
        self.assertTrue(forecast_action["historical_archive_required"])
        self.assertIn("历史 forecast", forecast_action["label"])
        self.assertIn("forecast-archive-import", forecast_action["command"])
        self.assertIn("--archive-path", forecast_action["command"])
        self.assertIn("--apply", forecast_action["apply_command"])
        self.assertEqual(forecast_action["schema_doc"], "FORECAST_ARCHIVE_IMPORT_CN.md")
        self.assertIn("run_at", forecast_action["required_fields"])
        self.assertIn("D+1/D+2", forecast_action["leakage_gate"])
        self.assertNotIn("forecast-backfill", forecast_action["command"])

    def test_forecast_archive_manifest_templates_missing_sources(self):
        db_path = test_db_path("forecast_archive_manifest")
        manifest_path = TEST_DB_DIR / "forecast-archive-manifest.jsonl"
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        self.addCleanup(lambda: manifest_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            rule = infer_settlement_rule(
                {
                    "market_id": "nyc-manifest-1",
                    "city": "nyc",
                    "city_name": "New York City",
                    "unit": "F",
                    "event_url": "https://polymarket.com/event/nyc-manifest",
                    "question": "Will the highest temperature in NYC be between 80-81°F on June 23?",
                    "description": "Resolves using Wunderground station KLGA history.",
                    "resolutionSource": "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA/date/2026-6-23",
                    "date": "2026-06-23",
                }
            )
            upsert_market_rule(rule.to_dict())
            upsert_settlement_contracts([settlement_contract_from_rule(rule)])
            set_settlement_contract_verification("nyc-manifest", True, reviewer="test", note="station checked")
            upsert_truth_observation({
                "city": "nyc",
                "city_name": "New York City",
                "target_date": "2026-06-23",
                "station_id": "KLGA",
                "station_name": "LaGuardia Airport",
                "unit": "F",
                "actual_temp": 81.0,
                "provider": "nws_station",
                "source_url": "https://api.weather.gov/stations/KLGA/observations",
                "observation_count": 24,
                "source_confidence": 0.95,
                "calibration_eligible": True,
                "reason_if_ineligible": "",
            })
            audit = build_model_dataset_audit(db_path, min_samples=1)
            manifest = build_forecast_archive_manifest(audit)
            write_forecast_archive_manifest(manifest, manifest_path)

        self.assertEqual(manifest["record_count"], 2)
        self.assertEqual(manifest["by_source"], {"ecmwf": 1, "gfs_ensemble": 1})
        self.assertEqual({record["station_id"] for record in manifest["records"]}, {"KLGA"})
        self.assertTrue(manifest_path.exists())
        text = manifest_path.read_text(encoding="utf-8")
        self.assertIn("run_at", text)
        self.assertIn("no_leak_rule", text)

    def test_forecast_archive_manifest_payload_is_dashboard_ready(self):
        audit = {
            "summary": {"baseline_ready_samples": 0},
            "reason_counts": {"no_no_leak_forecast_run": 1},
            "samples": [
                {
                    "city": "nyc",
                    "city_name": "New York City",
                    "target_date": "2026-06-23",
                    "timezone": "America/New_York",
                    "settlement_pending": False,
                    "sources": [],
                    "reasons": ["no_no_leak_forecast_run", "forecast_members_missing"],
                    "warnings": ["core_source_coverage_incomplete"],
                }
            ],
        }
        with patch("dashboard_server.build_model_dataset_audit", return_value=audit):
            payload = _forecast_archive_manifest_payload(limit=10, sources=["ecmwf"], include_jsonl=False)
            payload_with_jsonl = _forecast_archive_manifest_payload(limit=10, sources=["ecmwf"], include_jsonl=True)

        self.assertEqual(payload["record_count"], 1)
        self.assertEqual(payload["by_source"], {"ecmwf": 1})
        self.assertEqual(payload["records"][0]["station_id"], "KLGA")
        self.assertEqual(payload["schema_doc"], "FORECAST_ARCHIVE_IMPORT_CN.md")
        self.assertIn("forecast-archive-manifest", payload["template_command"])
        self.assertIn("forecast-archive-import", payload["import_dry_run_command"])
        self.assertNotIn("jsonl", payload)
        self.assertIn('"city": "nyc"', payload_with_jsonl["jsonl"])

    def test_model_dataset_audit_treats_future_truth_as_pending_not_missing(self):
        db_path = test_db_path("model_dataset_pending")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            rule = infer_settlement_rule(
                {
                    "market_id": "nyc-future-1",
                    "city": "nyc",
                    "city_name": "New York City",
                    "unit": "F",
                    "event_url": "https://polymarket.com/event/nyc-future",
                    "question": "Will the highest temperature in NYC be between 80-81°F on June 28?",
                    "description": "Resolves using Wunderground station KLGA history.",
                    "resolutionSource": "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA/date/2026-6-28",
                    "date": "2026-06-28",
                }
            )
            upsert_market_rule(rule.to_dict())
            upsert_settlement_contracts([settlement_contract_from_rule(rule)])
            audit = build_model_dataset_audit(
                db_path,
                min_samples=1,
                now=datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(audit["summary"]["pending_settlement_samples"], 1)
        self.assertEqual(audit["summary"]["mature_event_days"], 0)
        self.assertNotIn("eligible_truth_missing", audit["reason_counts"])
        self.assertNotIn("contract_not_manually_verified", audit["training_reason_counts"])
        self.assertEqual(audit["operational_counts"]["unverified_contract_event_days"], 1)
        self.assertEqual(audit["operational_counts"]["auto_verified_unreviewed_contracts"], 1)
        self.assertEqual(audit["operational_counts"]["mature_auto_verified_unreviewed_contracts"], 0)
        self.assertIn("settlement_pending", audit["samples"][0]["warnings"])
        action_keys = {action["key"] for action in audit["next_actions"]}
        self.assertNotIn("review_auto_verified_contracts", action_keys)
        self.assertNotIn("backfill_official_truth", action_keys)
        self.assertNotIn("backfill_forecast_members", action_keys)
        self.assertNotIn("backfill_orderbooks", action_keys)

    def test_settlement_pending_helper_uses_local_day_end(self):
        self.assertTrue(
            is_settlement_pending(
                "2026-06-28",
                "America/New_York",
                datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc),
            )
        )
        self.assertFalse(
            is_settlement_pending(
                "2026-06-28",
                "America/New_York",
                datetime(2026, 6, 29, 5, 0, tzinfo=timezone.utc),
            )
        )

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

    def test_truth_revisions_append_versions_and_keep_latest_materialized_row(self):
        db_path = test_db_path("truth_versions")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        base = {
            "city": "nyc",
            "city_name": "New York City",
            "target_date": "2026-06-23",
            "station_id": "KLGA",
            "station_name": "LaGuardia Airport",
            "unit": "F",
            "actual_temp": 77.0,
            "provider": "nws_station",
            "source_url": "https://api.weather.gov/stations/KLGA/observations",
            "observation_count": 24,
            "source_confidence": 0.9,
            "calibration_eligible": True,
            "reason_if_ineligible": "",
            "is_final": True,
            "is_preliminary": False,
            "quality_flags": ["official_station"],
        }
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            upsert_truth_observation(base)
            upsert_truth_observation({**base, "actual_temp": 78.0, "observation_count": 25})
            with connect(db_path) as conn:
                version_rows = conn.execute(
                    "SELECT id, supersedes_truth_id FROM truth_observation_versions ORDER BY id"
                ).fetchall()
                latest = conn.execute(
                    "SELECT actual_temp, supersedes_truth_id FROM truth_observations"
                ).fetchone()
        self.assertEqual(len(version_rows), 2)
        self.assertEqual(version_rows[1]["supersedes_truth_id"], version_rows[0]["id"])
        self.assertEqual(latest["actual_temp"], 78.0)
        self.assertEqual(latest["supersedes_truth_id"], version_rows[0]["id"])

    def test_truth_temporal_audit_invalidates_wrong_day_metar(self):
        db_path = test_db_path("truth_temporal_audit")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        payload = {
            "city": "nyc",
            "city_name": "New York City",
            "target_date": "2026-06-16",
            "station_id": "KLGA",
            "station_name": "LaGuardia Airport",
            "unit": "F",
            "actual_temp": 82.0,
            "provider": "aviationweather_station",
            "source_url": "https://aviationweather.gov/api/data/metar",
            "observation_count": 1,
            "source_confidence": 0.74,
            "calibration_eligible": True,
            "reason_if_ineligible": "",
            "observed_at": "2026-06-24T18:00:00+00:00",
            "is_final": True,
            "is_preliminary": False,
            "quality_flags": ["official_metar"],
        }
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            upsert_truth_observation(payload)
            audit = repair_truth_temporal_mismatches()
            with connect(db_path) as conn:
                latest = conn.execute(
                    "SELECT actual_temp, calibration_eligible, reason_if_ineligible "
                    "FROM truth_observations"
                ).fetchone()
                versions = conn.execute(
                    "SELECT COUNT(*) FROM truth_observation_versions"
                ).fetchone()[0]
        self.assertEqual(audit["invalidated"], 1)
        self.assertIsNone(latest["actual_temp"])
        self.assertEqual(latest["calibration_eligible"], 0)
        self.assertIn("observation_date_mismatch", latest["reason_if_ineligible"])
        self.assertEqual(versions, 2)

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
