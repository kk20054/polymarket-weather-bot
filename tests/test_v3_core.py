import asyncio
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from weatherbot_v3.ai_review import AIReviewer
from weatherbot_v3.db import bulk_settlement_contract_verification, connect, init_v3_db, insert_forecast_run, insert_orderbook, list_settlement_contracts, set_settlement_contract_verification, upsert_market_rule, upsert_market_rules, upsert_settlement_contracts
from weatherbot_v3.executor import PaperExecutor
from weatherbot_v3.polymarket import estimate_buy_fill, quote_from_market_payload, validate_order_constraints
from weatherbot_v3.distribution import build_event_distribution
from weatherbot_v3.forecast_archive import import_forecast_archive
from weatherbot_v3.model_dataset import build_model_dataset_audit, is_settlement_pending
from weatherbot_v3.qualification import build_data_readiness
from weatherbot_v3.registry import SETTLEMENT_REGISTRY
from weatherbot_v3.migration import repair_truth_temporal_mismatches
from weatherbot_v3.truth import _parse_time, infer_settlement_rule, settlement_contract_from_rule
from weatherbot_v3.db import truth_coverage_summary, upsert_truth_observation
from dashboard_server import AutoSimulationUpdate, _augment_strategy_replay_record, _auto_simulation_state, _bucket_probability_f, _bucket_value_in_range, _bulk_simulation_skip_reason, _build_policy_candidates, _build_temperature_fit, _entry_snapshot_features, _fit_trade_readiness, _live_gate, _metric_summary, _position_from_signal, _refresh_signal_orderbooks, _save_auto_simulation_state, update_auto_simulation
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
        self.assertEqual(readiness["summary"]["market_rules"], 1)
        self.assertEqual(readiness["production_phase"]["id"], "phase1_5")
        blocker_codes = {item["code"] for item in readiness["blockers"]}
        self.assertIn("settlement_rule_not_manually_verified", blocker_codes)
        self.assertIn("versioned_forecast_runs_missing", blocker_codes)

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
        self.assertIn("逐条人工核验", readiness["production_phase"]["operator_action"])

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
        self.assertIsNotNone(rule_row["manual_verified_at"])

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
            mature_rule = infer_settlement_rule(
                {
                    "market_id": "nyc-mature-1",
                    "city": "nyc",
                    "city_name": "New York City",
                    "unit": "F",
                    "event_url": "https://polymarket.com/event/nyc-mature",
                    "question": "Will the highest temperature in NYC be between 80-81°F on June 23?",
                    "description": "Resolves using Wunderground station KLGA history.",
                    "resolutionSource": "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA/date/2026-6-23",
                    "date": "2026-06-23",
                }
            )
            pending_rule = infer_settlement_rule(
                {
                    "market_id": "nyc-pending-1",
                    "city": "nyc",
                    "city_name": "New York City",
                    "unit": "F",
                    "event_url": "https://polymarket.com/event/nyc-pending",
                    "question": "Will the highest temperature in NYC be between 80-81°F on June 28?",
                    "description": "Resolves using Wunderground station KLGA history.",
                    "resolutionSource": "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA/date/2026-6-28",
                    "date": "2026-06-28",
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
                run = conn.execute("SELECT city, source, horizon, mean_high, training_eligible FROM forecast_runs").fetchone()
                member_count = conn.execute("SELECT COUNT(*) FROM forecast_members").fetchone()[0]

        self.assertEqual(summary["valid"], 1)
        self.assertEqual(summary["imported"], 1)
        self.assertEqual(summary["by_city"], {"nyc": 1})
        self.assertEqual(run["city"], "nyc")
        self.assertEqual(run["source"], "ecmwf")
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
        self.assertIn("forecast_runs/forecast_members", forecast_action["command"])
        self.assertNotIn("forecast-backfill", forecast_action["command"])

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
