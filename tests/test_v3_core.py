from tests import ensure_test_environment

ensure_test_environment()

import asyncio
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import requests

from weatherbot_v3.ai_review import AIReviewer
from weatherbot_v3.china_weather import WEATHERCN_STATION_CODES, _weathercom_current_observation, hko_rhrread_observation, weathercn_sk2d_observation
from weatherbot_v3.db import bulk_settlement_contract_verification, connect, dashboard_summary, forecast_summary, init_v3_db, insert_forecast_run, insert_forecast_runs, insert_orderbook, list_data_fetch_logs, list_market_buckets, list_paper_orders, list_settlement_contracts, list_signal_decisions, log_data_fetch, market_bucket_summary, model_reprice_event_summary, paper_execution_summary, set_settlement_contract_verification, truth_delta_audit_summary, upsert_daily_max_prediction, upsert_hourly_consensus, upsert_market_bucket, upsert_market_rule, upsert_market_rules, upsert_mesonet_observation, upsert_metar_report, upsert_metar_reports, upsert_model_reprice_event, upsert_settlement_contracts, upsert_signal_decision_record, weather_evidence_summary
from weatherbot_v3.executor import PaperExecutor
from weatherbot_v3.env_utils import redact_secret_text, redact_secrets
from weatherbot_v3.polymarket import estimate_buy_fill, quote_from_market_payload, validate_order_constraints
from weatherbot_v3.polymarket_probe import parse_settlement_rule_text, probe_polymarket_markets
from weatherbot_v3.paper import execute_paper_decision
from weatherbot_v3.production_actions import list_production_actions, run_production_action
from weatherbot_v3.distribution import build_event_distribution
from weatherbot_v3.forecast_archive import build_forecast_archive_manifest, import_forecast_archive, write_forecast_archive_manifest
from weatherbot_v3.forecast import ingest_polywx_forecasts, forecast_run_from_polywx_rows
from weatherbot_v3.history import fetch_open_meteo_historical_backfill, open_meteo_historical_rows_from_response
from weatherbot_v3.hourly import _forecast_peak_marker, _peak_marker_from_forecast_revisions, build_hourly_consensus, build_metar_hourly_consensus, forecast_hourly_points, forecast_revision_history, hourly_consensus_points, hourly_consensus_summary, source_series_summary
from weatherbot_v3.deb import bucket_probabilities, build_and_store_daily_max_prediction, build_daily_max_prediction
from weatherbot_v3.market_buckets import ingest_market_buckets, market_bucket_from_payload, parse_temperature_bucket, sync_active_weather_market_buckets
from weatherbot_v3.model_dataset import build_model_dataset_audit, is_settlement_pending
from weatherbot_v3.openmeteo import fetch_openmeteo_forecasts, model_allowlist_for_city, openmeteo_runs_from_response
from weatherbot_v3.mesonet import ingest_mesonet_observations, mesonet_observation_from_pws_row
from weatherbot_v3.pws import aggregate_pws_observations, fetch_wunderground_pws_city, parse_pws_current_payload
from weatherbot_v3.qualification import build_data_readiness, persist_data_readiness
from weatherbot_v3.registry import SETTLEMENT_REGISTRY, forecast_source_matches_profile_location
from weatherbot_v3.signals import build_signal_decisions, signal_decisions_summary
from weatherbot_v3.source_health import build_source_health_matrix
from weatherbot_v3.stations import apply_market_probe_result, list_stations, reconcile_station_verification_status, station_row_from_profile, sync_station_registry
from weatherbot_v3.migration import repair_truth_temporal_mismatches
from weatherbot_v3.metar import backfill_iem_asos_metars, ingest_iem_asos_csv, parse_iem_asos_csv, probe_iem_stations, fetch_awc_metars, metar_report_from_awc, refresh_metar_reports
from weatherbot_v3.truth import _parse_time, infer_settlement_rule, settlement_contract_from_rule
from weatherbot_v3.truth.wunderground import _country_from_icao, fetch_wunderground_daily_result, fetch_wunderground_hourly_result, persist_wunderground_hourly
from weatherbot_v3.validation import _compact_action, build_production_validation_report
from weatherbot_v3.weathercom import weathercom_request_units, weathercom_runs_from_response
from weatherbot_v3.db import truth_coverage_summary, upsert_truth_observation
from weatherbot_v3.cli import _stage_result, default_orderbook_start_date, run_china_weather_fetch, run_daily_max_build, run_hourly_consensus_build, run_iem_asos_truth_fetch, run_market_buckets_sync, run_openmeteo_fetch, run_orderbook_backfill, run_paper_execute, run_polymarket_market_probe, run_production_refresh, run_signal_decisions_build, select_orderbook_backfill_markets
from dashboard_server import AutoSimulationUpdate, ProductionActionRequest, ProductionRefreshRequest, _augment_strategy_replay_record, _auto_simulation_state, _bucket_probability_f, _bucket_value_in_range, _bulk_simulation_skip_reason, _build_city_evidence_payload, _build_policy_candidates, _build_temperature_fit, _build_weather_city_series, _city_evidence_matches, _combined_fetch_log_payload, _diff_stats_summary, _entry_snapshot_features, _fit_trade_readiness, _forecast_archive_manifest_payload, _live_gate, _merge_hourly_points, _metric_summary, _position_from_signal, _recommendations_payload, _refresh_signal_orderbooks, _run_paper_validation_action, _save_auto_simulation_state, forecasts as forecasts_api, hourly_consensus as hourly_consensus_api, market_buckets as market_buckets_api, observations as observations_api, production_refresh, production_refresh_lock, update_auto_simulation
from dashboard_server import signal_decision_detail as signal_decision_detail_api
from dashboard_server import signal_decisions as signal_decisions_api
from dashboard_server import paper_orders as paper_orders_api
from dashboard_server import paper_orders_execute as paper_orders_execute_api
from dashboard_server import PaperExecutionRequest
from dashboard_server import stations as stations_api
from bot_v2 import bucket_prob, calibrated_bucket_probability, calibration_metric, persist_forecast_batches, target_dates_for_city
from datetime import date, datetime, timedelta, timezone


TEST_DB_DIR = Path(__file__).resolve().parents[1] / ".tmp-tests"


def test_db_path(name: str) -> Path:
    TEST_DB_DIR.mkdir(exist_ok=True)
    path = TEST_DB_DIR / f"{name}.db"
    path.unlink(missing_ok=True)
    return path


class FakeHTTPResponse:
    def __init__(self, payload, url: str = "https://example.test", status_code: int = 200):
        self._payload = payload
        self.url = url
        self.status_code = status_code
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            exc = Exception(f"HTTP {self.status_code}")
            exc.response = self
            raise exc


class FakePolymarketSession:
    def __init__(self, event_payload: dict, book_payloads: dict[str, dict]):
        self.event_payload = event_payload
        self.book_payloads = book_payloads
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if "/events/slug/" in url:
            return FakeHTTPResponse(self.event_payload, url=url)
        token_id = (params or {}).get("token_id")
        payload = self.book_payloads.get(str(token_id), {})
        return FakeHTTPResponse(payload, url=f"{url}?token_id={token_id}")

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        payload = []
        for item in json or []:
            token_id = str(item.get("token_id") or "")
            book = dict(self.book_payloads.get(token_id, {}))
            if book:
                book.setdefault("asset_id", token_id)
                payload.append(book)
        return FakeHTTPResponse(payload, url=url)


def openmeteo_hourly_run(
    city: str,
    target_date: str,
    source: str,
    temps: list[float],
    *,
    valid_times: list[str] | None = None,
    retrieved_at: str | None = None,
) -> tuple[dict, list[dict]]:
    profile = SETTLEMENT_REGISTRY[city]
    retrieved_at = retrieved_at or f"{target_date}T00:00:00+00:00"
    target_start_utc = datetime.combine(
        date.fromisoformat(target_date),
        datetime.min.time(),
        tzinfo=ZoneInfo(profile.timezone),
    ).astimezone(timezone.utc)
    retrieved_dt = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
    horizon = "d0" if retrieved_dt >= target_start_utc else "d1"
    times = valid_times or [f"{target_date}T{hour:02d}:00:00+00:00" for hour in range(len(temps))]
    hourly = [
        {
            "valid_at": valid_at,
            "temperature_2m": temp,
            "relative_humidity_2m": 50,
            "cloud_cover": 20,
        }
        for valid_at, temp in zip(times, temps)
    ]
    return (
        {
            "run_key": f"{source}:{city}:{target_date}:{retrieved_at}:{','.join(str(t) for t in temps)}",
            "city": city,
            "target_date": target_date,
            "source": source,
            "provider": "open-meteo" if source.startswith("openmeteo_") else "test",
            "model": source.replace("openmeteo_", ""),
            "model_version": "test",
            "run_type": "forecast",
            "retrieved_at": retrieved_at,
            "valid_at": times[-1] if times else "",
            "horizon": horizon,
            "station_id": profile.station_id,
            "timezone": profile.timezone,
            "unit": profile.unit,
            "mean_high": max(temps) if temps else 0,
            "std_high": 0,
            "member_count": 1,
            "parser_version": "openmeteo-test",
            "parse_status": "parsed",
            "training_eligible": True,
        },
        [{"member_id": "deterministic", "high_temp": max(temps), "hourly": hourly}],
    )


class V3CoreTests(unittest.TestCase):
    def test_forecast_runs_have_dashboard_lookup_indexes(self):
        db_path = test_db_path("forecast_dashboard_indexes")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        init_v3_db(db_path)
        with connect(db_path) as conn:
            indexes = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'forecast_runs'"
                ).fetchall()
            }

        self.assertIn("idx_forecast_runs_city_date_source_retrieved", indexes)
        self.assertIn("idx_forecast_runs_city_date_type_retrieved", indexes)

    def test_forecast_peak_marker_matches_polywx_revision_history_contract(self):
        marker = _peak_marker_from_forecast_revisions([
            {"local_hour": "10:00", "temperature": 29.0, "retrieved_at": "2026-07-10T00:00:00Z"},
            {"local_hour": "13:00", "temperature": 29.0, "retrieved_at": "2026-07-11T00:00:00Z"},
            {"local_hour": "15:00", "temperature": 29.0, "retrieved_at": "2026-07-11T06:00:00Z"},
            {"local_hour": "16:00", "temperature": 29.0, "retrieved_at": "2026-07-10T12:00:00Z"},
            {"local_hour": "17:00", "temperature": 28.0, "retrieved_at": "2026-07-12T00:00:00Z"},
        ], "2026-07-12")

        self.assertEqual(marker["source_hour"], "16:00")
        self.assertEqual(marker["local_time"], "16:00:00")
        self.assertEqual(marker["hour_float"], 16.0)
        self.assertEqual(marker["temperature"], 29.0)
        self.assertEqual(marker["method"], "forecast_revision_peak_v1")
        self.assertEqual(marker["snapshot_count"], 5)

    def test_current_forecast_peak_marker_keeps_latest_max_hour_without_offset(self):
        marker = _forecast_peak_marker([{"local_hour": "23:00", "best": 31.0}], "2026-07-12")

        self.assertEqual(marker["date"], "2026-07-12")
        self.assertEqual(marker["local_time"], "23:00:00")
        self.assertEqual(marker["hour_float"], 23.0)

    def test_secret_redaction_cleans_api_keys_in_nested_errors(self):
        secret = "0123456789abcdef0123456789abcdef"
        with patch.dict(os.environ, {"WEATHER_COM_API_KEY": secret}, clear=False):
            payload = redact_secrets({
                "error": f"401 for https://api.weather.com/path?apiKey={secret}&units=m",
                "nested": [f"Authorization: Bearer {secret}"],
            })
            text = json.dumps(payload)

        self.assertNotIn(secret, text)
        self.assertIn("apiKey=***", payload["error"])
        self.assertEqual(redact_secret_text(f"apiKey={secret}"), "apiKey=***")

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
        day = next(item for item in payload[0]["dates"] if item["target_date"] == "2026-06-29")
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

    def test_round5_truth_delta_and_alpha_summaries_support_dashboard(self):
        path = test_db_path("round5-dashboard-readonly")
        with patch.dict(os.environ, {"WEATHERBOT_DB_PATH": str(path)}):
            init_v3_db(path)
            with connect(path) as conn:
                conn.execute(
                    """
                    INSERT INTO truth_delta_audit (
                        audit_key, icao, city, date_local, wu_high_c, iem_high_c,
                        hko_high_c, delta_wu_minus_iem, notes, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "chicago-2026-07-04",
                        "KORD",
                        "chicago",
                        "2026-07-04",
                        31.1,
                        30.2,
                        None,
                        0.9,
                        "unit test",
                        "2026-07-05T00:00:00+00:00",
                        "2026-07-05T00:00:00+00:00",
                    ),
                )
            delta_summary = truth_delta_audit_summary("chicago", path=path)
            self.assertEqual(delta_summary["count"], 1)
            self.assertEqual(delta_summary["rows"][0]["icao"], "KORD")
            self.assertEqual(delta_summary["histogram"][0]["count"], 1)

            upsert_model_reprice_event(
                {
                    "event_key": "alpha-chicago",
                    "city_key": "chicago",
                    "target_date": "2026-07-04",
                    "market_id": "market-92",
                    "bucket_key": "bucket-92",
                    "triggered_at": "2026-07-05T06:01:00+00:00",
                    "model_source": "ecmwf_06z",
                    "previous_model_prob": 0.21,
                    "model_prob": 0.31,
                    "delta_prob": 0.10,
                    "market_mid": 0.22,
                    "edge": 0.09,
                    "alpha_candidate": True,
                },
                path=path,
            )
            alpha_summary = model_reprice_event_summary("chicago", "2026-07-04", alpha_only=True, path=path)
            self.assertEqual(alpha_summary["count"], 1)
            self.assertEqual(alpha_summary["alpha_count"], 1)
            self.assertTrue(alpha_summary["rows"][0]["alpha_candidate"])

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

    def test_auto_simulation_api_retires_legacy_execution_path(self):
        TEST_DB_DIR.mkdir(exist_ok=True)
        state_path = TEST_DB_DIR / "auto-simulation-api.json"
        state_path.unlink(missing_ok=True)
        self.addCleanup(lambda: state_path.unlink(missing_ok=True))
        with (
            patch("dashboard_server.AUTO_SIMULATION_PATH", state_path),
            patch("dashboard_server._ensure_auto_simulation_task") as ensure_task,
            patch("dashboard_server._refresh_dashboard_cache_once") as refresh_cache,
            patch("dashboard_server.log_event"),
        ):
            result = asyncio.run(update_auto_simulation(
                AutoSimulationUpdate(enabled=True, interval_seconds=300)
            ))
        self.assertFalse(result["ok"])
        self.assertFalse(result["enabled"])
        self.assertEqual(result["reason"], "legacy_auto_simulation_retired_use_paper_validation")
        self.assertEqual(result["interval_seconds"], 300)
        ensure_task.assert_not_called()
        refresh_cache.assert_not_called()

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

    def test_market_bucket_parser_handles_temperature_shapes(self):
        between = parse_temperature_bucket(
            "Will the highest temperature in Seattle be between 70-71°F on June 16, 2026?"
        )
        self.assertEqual(between["direction"], "range")
        self.assertEqual(between["low"], 70.0)
        self.assertEqual(between["high"], 71.0)
        self.assertEqual(between["unit"], "F")

        below = parse_temperature_bucket(
            "Will the highest temperature in Dallas be 79°F or below on June 16, 2026?"
        )
        self.assertEqual(below["direction"], "or_below")
        self.assertEqual(below["high"], 79.0)

        exact = parse_temperature_bucket(
            "Will the highest temperature in Paris be 30°C on June 17, 2026?"
        )
        self.assertEqual(exact["direction"], "exact")
        self.assertEqual(exact["low"], 30.0)
        self.assertEqual(exact["high"], 30.0)
        self.assertEqual(exact["unit"], "C")

    def test_market_buckets_ingest_persists_strict_matching_metadata(self):
        path = test_db_path("market-buckets")
        init_v3_db(path)
        payload = {
            "id": "market-1",
            "conditionId": "condition-1",
            "eventSlug": "highest-temperature-in-chicago-on-july-1-2026",
            "question": "Will the highest temperature in Chicago be between 90-91°F on July 1, 2026?",
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.22", "0.78"]',
            "clobTokenIds": '["yes-token", "no-token"]',
            "orderMinSize": "5",
            "orderPriceMinTickSize": "0.01",
            "enableOrderBook": True,
            "negRisk": True,
            "bestBid": "0.20",
            "bestAsk": "0.22",
            "spread": "0.02",
            "volume": "1200",
            "liquidity": "300",
            "source_url": "https://gamma-api.polymarket.com/markets/market-1",
        }
        with patch("weatherbot_v3.db.load_config", return_value=SimpleNamespace(v3_db_path=path)):
            result = ingest_market_buckets(payload, city="chicago", city_name="Chicago", station_id="KORD")
            self.assertEqual(result["stored"], 1)
            self.assertEqual(result["matched"], 1)
            rows = list_market_buckets(city="chicago", target_date="2026-07-01")
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["market_id"], "market-1")
            self.assertEqual(row["bucket_direction"], "range")
            self.assertEqual(row["bucket_low"], 90.0)
            self.assertEqual(row["bucket_high"], 91.0)
            self.assertEqual(row["yes_token_id"], "yes-token")
            self.assertEqual(row["order_min_size"], 5.0)
            self.assertEqual(row["tick_size"], 0.01)
            self.assertTrue(row["neg_risk"])
            self.assertTrue(row["enable_order_book"])
            self.assertEqual(row["strict_match_status"], "matched")
            self.assertEqual(row["strict_match_reasons"], [])
            summary = market_bucket_summary("chicago", "2026-07-01")
            self.assertEqual(summary["bucket_count"], 1)
            self.assertEqual(summary["matched_bucket_count"], 1)

    def test_market_bucket_strict_matching_blocks_missing_metadata(self):
        row = market_bucket_from_payload({
            "id": "market-2",
            "question": "Will the highest temperature in Paris be 30°C on June 17, 2026?",
            "outcomePrices": '["0.12", "0.88"]',
            "enableOrderBook": False,
        })
        self.assertEqual(row["strict_match_status"], "blocked")
        self.assertIn("yes_token_missing", row["strict_match_reasons"])
        self.assertIn("tick_size_missing", row["strict_match_reasons"])
        self.assertIn("order_min_size_missing", row["strict_match_reasons"])
        self.assertIn("orderbook_disabled", row["strict_match_reasons"])

    def test_market_bucket_parser_handles_gamma_degree_encoding(self):
        between = parse_temperature_bucket(
            "Will the highest temperature in Chicago be between 88-89\u00c2\u00b0F on July 2?"
        )
        self.assertEqual(between["direction"], "range")
        self.assertEqual(between["low"], 88.0)
        self.assertEqual(between["high"], 89.0)
        self.assertEqual(between["unit"], "F")

        below = parse_temperature_bucket(
            "Will the highest temperature in Chicago be 87\u00c2\u00b0F or below on July 2?"
        )
        self.assertEqual(below["direction"], "or_below")
        self.assertEqual(below["high"], 87.0)
        self.assertEqual(below["unit"], "F")

    def test_active_weather_market_sync_ingests_gamma_and_clob(self):
        path = test_db_path("active-weather-market-buckets")
        event_payload = {
            "id": "event-1",
            "slug": "highest-temperature-in-chicago-on-july-2-2026",
            "title": "Highest temperature in Chicago on July 2?",
            "active": True,
            "closed": False,
            "markets": [
                {
                    "id": "market-active-1",
                    "conditionId": "condition-active-1",
                    "question": "Will the highest temperature in Chicago be between 88-89\u00c2\u00b0F on July 2?",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.22", "0.78"]',
                    "clobTokenIds": '["yes-active-1", "no-active-1"]',
                    "orderMinSize": 5,
                    "orderPriceMinTickSize": 0.001,
                    "enableOrderBook": True,
                    "negRisk": True,
                    "bestBid": 0.20,
                    "bestAsk": 0.22,
                    "spread": 0.02,
                    "volume": "1200",
                    "liquidity": "300",
                }
            ],
        }
        book_payloads = {
            "yes-active-1": {
                "asset_id": "yes-active-1",
                "timestamp": "1782975365751",
                "hash": "book-hash-1",
                "bids": [{"price": "0.21", "size": "10"}],
                "asks": [{"price": "0.23", "size": "12"}, {"price": "0.22", "size": "8"}],
                "min_order_size": "5",
                "tick_size": "0.001",
                "neg_risk": True,
            }
        }
        session = FakePolymarketSession(event_payload, book_payloads)
        with patch.dict(os.environ, {"V3_DB_PATH": str(path)}, clear=False):
            init_v3_db(path)
            sync_station_registry(path=path)
            payload = sync_active_weather_market_buckets(
                cities=["chicago"],
                target_dates=["2026-07-02"],
                limit_cities=1,
                limit=5,
                fetch_orderbooks=True,
                dry_run=False,
                session=session,
                sleep_seconds=0,
            )
            rows = list_market_buckets(city="chicago", target_date="2026-07-02", path=path)
            logs = list_data_fetch_logs(5)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["stored"], 1)
        self.assertEqual(payload["matched"], 1)
        self.assertEqual(payload["orderbook_ok"], 1)
        self.assertEqual(sum(1 for call in session.calls if str(call["url"]).endswith("/books")), 1)
        self.assertEqual(sum(1 for call in session.calls if str(call["url"]).endswith("/book")), 0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["market_id"], "market-active-1")
        self.assertEqual(rows[0]["bucket_low"], 88.0)
        self.assertEqual(rows[0]["bucket_high"], 89.0)
        self.assertEqual(rows[0]["best_bid"], 0.21)
        self.assertEqual(rows[0]["best_ask"], 0.22)
        self.assertEqual(rows[0]["tick_size"], 0.001)
        self.assertEqual(rows[0]["order_min_size"], 5.0)
        self.assertEqual(rows[0]["orderbook_source"], "clob")
        self.assertIn("polymarket.com/event/highest-temperature-in-chicago-on-july-2-2026", rows[0]["event_url"])
        self.assertEqual(logs[0]["source"], "polymarket_gamma")

    def test_active_weather_market_sync_dry_run_does_not_write_rows(self):
        path = test_db_path("active-weather-market-buckets-dry-run")
        event_payload = {
            "slug": "highest-temperature-in-chicago-on-july-2-2026",
            "active": True,
            "closed": False,
            "markets": [{
                "id": "market-dry-1",
                "question": "Will the highest temperature in Chicago be 88\u00c2\u00b0F on July 2?",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.22", "0.78"]',
                "clobTokenIds": '["yes-dry-1", "no-dry-1"]',
                "orderMinSize": 5,
                "orderPriceMinTickSize": 0.001,
                "enableOrderBook": True,
            }],
        }
        session = FakePolymarketSession(event_payload, {})
        with patch.dict(os.environ, {"V3_DB_PATH": str(path)}, clear=False):
            init_v3_db(path)
            sync_station_registry(path=path)
            payload = sync_active_weather_market_buckets(
                cities=["chicago"],
                target_dates=["2026-07-02"],
                limit_cities=1,
                limit=5,
                fetch_orderbooks=False,
                dry_run=True,
                session=session,
                sleep_seconds=0,
            )
            rows = list_market_buckets(city="chicago", target_date="2026-07-02", path=path)

        self.assertEqual(payload["stored"], 0)
        self.assertEqual(payload["matched"], 1)
        self.assertEqual(rows, [])

    def test_market_buckets_api_returns_read_only_summary(self):
        path = test_db_path("market-buckets-api")
        init_v3_db(path)
        with patch("weatherbot_v3.db.load_config", return_value=SimpleNamespace(v3_db_path=path)):
            upsert_market_bucket({
                "market_id": "market-3",
                "question": "Will the highest temperature in Chicago be 92°F on July 1, 2026?",
                "city": "chicago",
                "city_name": "Chicago",
                "target_date": "2026-07-01",
                "unit": "F",
                "bucket_label": "92F",
                "bucket_direction": "exact",
                "bucket_low": 92,
                "bucket_high": 92,
                "yes_token_id": "yes-token-3",
                "order_min_size": 5,
                "tick_size": 0.01,
                "enable_order_book": True,
                "price": 0.18,
                "strict_match_status": "matched",
                "strict_match_reasons": [],
            })
            payload = asyncio.run(market_buckets_api(city="chicago", target_date="2026-07-01"))
        self.assertEqual(payload["bucket_count"], 1)
        self.assertEqual(payload["matched_bucket_count"], 1)
        self.assertEqual(payload["latest"][0]["market_id"], "market-3")

    def test_clob_quote_uses_true_depth_and_estimates_partial_fill(self):
        quote = quote_from_market_payload({
            "id": "1",
            "yes_token_id": "yes",
            "spread": "0.01",
            "snapshot_type": "clob",
            "bids": [{"price": "0.18", "size": "20"}, {"price": "0.20", "size": "10"}],
            "asks": [{"price": "0.25", "size": "20"}, {"price": "0.22", "size": "5"}],
            "min_order_size": "5",
            "tick_size": "0.01",
            "enableOrderBook": True,
        })
        self.assertEqual(quote.best_bid, 0.20)
        self.assertEqual(quote.best_ask, 0.22)
        self.assertEqual(quote.spread, 0.02)
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
            patch("weatherbot_v3.cli.run_openmeteo_fetch", return_value={"runs_upserted": 6, "members_upserted": 24}) as openmeteo,
            patch("weatherbot_v3.cli.run_weathercom_fetch", return_value={"ok": True, "runs_upserted": 2, "members_upserted": 2}) as weathercom,
            patch("weatherbot_v3.cli._run_recent_metar_refresh", return_value={"reports_upserted": 8}) as metar,
            patch("weatherbot_v3.cli.run_hourly_consensus_build", return_value={"rows_upserted": 24}) as hourly,
            patch("weatherbot_v3.cli.run_daily_max_build", return_value={"stored": 1}) as daily_max,
            patch("weatherbot_v3.cli.run_market_buckets_sync", return_value={"stored": 11}) as buckets,
            patch("weatherbot_v3.cli.run_signal_decisions_build", return_value={"stored": 11}) as decisions,
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
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["failed_stages"], ["orderbook_backfill"])
        self.assertFalse(payload["scan_signals"])
        self.assertEqual(payload["readiness"]["status"], "blocked")
        self.assertEqual(payload["readiness"]["blocked_keys"], ["settlement_contracts", "orderbooks"])
        self.assertEqual([stage["name"] for stage in payload["stages"]], [
            "contracts_sync",
            "forecast_backfill",
            "openmeteo_fetch",
            "weathercom_fetch",
            "metar_refresh",
            "hourly_consensus",
            "daily_max_build",
            "market_buckets_sync",
            "signal_decisions_build",
            "signal_scan",
            "signal_migration",
            "orderbook_backfill",
        ])
        self.assertTrue(payload["stages"][9]["skipped"])
        forecast.assert_called_once_with("nyc", 2)
        openmeteo.assert_called_once_with("nyc", forecast_days=4, limit_cities=5)
        weathercom.assert_called_once_with("nyc", forecast_days=4, limit_cities=5)
        metar.assert_called_once_with("nyc", 48.0)
        hourly.assert_called_once_with("nyc", target_date="2026-06-27", days_arg=None)
        daily_max.assert_called_once_with("nyc", target_date="2026-06-27", days_arg=None)
        buckets.assert_called_once()
        decisions.assert_called_once_with("nyc", target_date="2026-06-27", days_arg=None, limit=5)
        signal_scan.assert_not_called()
        migrate.assert_called_once()
        orderbooks.assert_called_once_with(5, "2026-06-27", "2026-06-27")
        persist.assert_called_once_with(readiness)

    def test_stage_result_respects_nested_payload_failure(self):
        result = _stage_result("openmeteo_fetch", lambda: {"ok": False, "failed": 7})

        self.assertFalse(result["ok"])
        self.assertEqual(result["payload"]["failed"], 7)

    def test_stage_result_treats_zero_requested_zero_failed_as_success(self):
        result = _stage_result("orderbook_backfill", lambda: {"requested": 0, "ok": 0, "failed": 0})

        self.assertTrue(result["ok"])
        self.assertEqual(result["payload"]["requested"], 0)

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
        with connect(db_path) as conn:
            forecast_columns = {row["name"] for row in conn.execute("PRAGMA table_info(forecast_runs)").fetchall()}
        self.assertIn("parser_version", forecast_columns)
        self.assertIn("parse_status", forecast_columns)
        self.assertIn("parse_warnings", forecast_columns)
        self.assertIn("source_unit", forecast_columns)
        with connect(db_path) as conn:
            hourly_columns = {row["name"] for row in conn.execute("PRAGMA table_info(hourly_consensus)").fetchall()}
        self.assertIn("forecast_source", hourly_columns)
        self.assertIn("observation_sources_json", hourly_columns)
        self.assertIn("source_mix_json", hourly_columns)
        self.assertIn("consensus_version", hourly_columns)
        self.assertIn("build_status", hourly_columns)
        self.assertIn("forecast_spread", hourly_columns)
        self.assertIn("forecast_member_count", hourly_columns)
        self.assertIn("consensus_method", hourly_columns)
        with connect(db_path) as conn:
            daily_columns = {row["name"] for row in conn.execute("PRAGMA table_info(daily_max_predictions)").fetchall()}
        self.assertIn("member_daily_highs_json", daily_columns)
        self.assertIn("sigma_from_spread", daily_columns)
        self.assertIn("sigma_from_history", daily_columns)
        self.assertIn("bias_correction", daily_columns)
        self.assertIn("bias_sample_count", daily_columns)
        self.assertIn("deb_version", daily_columns)
        with connect(db_path) as conn:
            decision_columns = {row["name"] for row in conn.execute("PRAGMA table_info(signal_decisions)").fetchall()}
        self.assertIn("decision_id", decision_columns)
        self.assertIn("forecast_algo", decision_columns)
        self.assertIn("model_probability", decision_columns)
        self.assertIn("market_implied_probability", decision_columns)
        self.assertIn("edge", decision_columns)
        self.assertIn("strategy_name", decision_columns)
        self.assertIn("kelly_fraction", decision_columns)
        self.assertIn("position_size_usd", decision_columns)
        self.assertIn("ladder_group_id", decision_columns)
        self.assertIn("gate_status", decision_columns)
        self.assertIn("paper_decision", decision_columns)
        self.assertIn("live_decision", decision_columns)
        self.assertIn("evidence_links_json", decision_columns)
        with connect(db_path) as conn:
            reprice_columns = {row["name"] for row in conn.execute("PRAGMA table_info(model_reprice_events)").fetchall()}
        self.assertIn("event_key", reprice_columns)
        self.assertIn("alpha_candidate", reprice_columns)
        with connect(db_path) as conn:
            paper_columns = {row["name"] for row in conn.execute("PRAGMA table_info(paper_orders)").fetchall()}
        self.assertIn("decision_id", paper_columns)
        self.assertIn("requested_amount", paper_columns)
        self.assertIn("filled_amount", paper_columns)
        self.assertIn("average_fill_price", paper_columns)
        self.assertIn("unrealized_pnl", paper_columns)
        self.assertIn("lifecycle_status", paper_columns)
        self.assertIn("risk_reasons_json", paper_columns)
        self.assertIn("orderbook_snapshot_json", paper_columns)
        with connect(db_path) as conn:
            fill_columns = {row["name"] for row in conn.execute("PRAGMA table_info(fills)").fetchall()}
        self.assertIn("idempotency_key", fill_columns)
        self.assertIn("decision_id", fill_columns)
        self.assertIn("fill_status", fill_columns)
        with connect(db_path) as conn:
            mesonet_columns = {row["name"] for row in conn.execute("PRAGMA table_info(mesonet_observations)").fetchall()}
        self.assertIn("parser_version", mesonet_columns)
        self.assertIn("parse_status", mesonet_columns)
        self.assertIn("parse_warnings", mesonet_columns)
        self.assertIn("raw_unit", mesonet_columns)

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
                "parser_version": "pws-observation-row-v1",
                "parse_status": "parsed",
                "parse_warnings": [],
                "raw_unit": "F",
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
        self.assertEqual(evidence["latest_mesonet_observations"][0]["parser_version"], "pws-observation-row-v1")
        self.assertEqual(evidence["latest_mesonet_observations"][0]["parse_status"], "parsed")
        self.assertEqual(evidence["latest_mesonet_observations"][0]["raw_unit"], "F")

    def test_metar_bulk_upsert_is_idempotent_in_one_transaction(self):
        db_path = test_db_path("metar_bulk_upsert")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        reports = [
            {
                "city": "chicago",
                "station_id": "KORD",
                "report_time": f"2026-07-10T{hour:02d}:00:00+00:00",
                "raw_text": f"METAR KORD {hour:02d}00Z",
                "temperature": 20.0 + hour,
                "parse_status": "parsed",
            }
            for hour in range(3)
        ]
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            first = upsert_metar_reports(reports, db_path)
            corrected = [dict(report, raw_text=f"{report['raw_text']} COR") for report in reports]
            second = upsert_metar_reports(corrected, db_path)
            with connect(db_path) as conn:
                rows = conn.execute("SELECT raw_text FROM metar_reports ORDER BY report_time").fetchall()

        self.assertEqual(first, 3)
        self.assertEqual(second, 3)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(str(row["raw_text"]).endswith(" COR") for row in rows))

    def test_metar_natural_key_replaces_source_specific_report_key(self):
        db_path = test_db_path("metar_natural_key")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        base = {
            "city": "chicago",
            "station_id": "KORD",
            "report_time": "2026-07-13T12:51:00+00:00",
            "temperature": 25.0,
            "parse_status": "parsed",
        }
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            first_id = upsert_metar_report(
                dict(base, report_key="awc:KORD:20260713T1251Z", raw_text="METAR KORD 131251Z 25010KT")
            )
            second_id = upsert_metar_report(
                dict(base, report_key="iem_asos:KORD:20260713T1251Z", raw_text="METAR KORD 131251Z 25010KT COR")
            )
            with connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT id, raw_text FROM metar_reports WHERE station_id = ? AND report_time = ?",
                    ("KORD", base["report_time"]),
                ).fetchall()

        self.assertEqual(first_id, second_id)
        self.assertEqual(len(rows), 1)
        self.assertTrue(str(rows[0]["raw_text"]).endswith(" COR"))

    def test_weather_natural_keys_reject_incomplete_identity(self):
        db_path = test_db_path("weather_natural_key_validation")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            with self.assertRaisesRegex(ValueError, "station_id and report_time"):
                upsert_metar_report({"station_id": "KORD", "raw_text": "INVALID"})
            with self.assertRaisesRegex(ValueError, "city, target_date and local_hour"):
                upsert_hourly_consensus({"city": "chicago", "target_date": "2026-07-13"})

    def test_hourly_consensus_natural_key_replaces_legacy_source_key(self):
        db_path = test_db_path("hourly_consensus_natural_key")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        base = {
            "city": "chicago",
            "target_date": "2026-07-13",
            "local_hour": "08:00",
            "valid_time": "2026-07-13T08:00:00-05:00",
            "forecast_temp": 24.0,
        }
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            first_id = upsert_hourly_consensus(
                dict(base, consensus_key="metar:KORD:2026-07-13:08:00", observed_temp=23.0)
            )
            second_id = upsert_hourly_consensus(
                dict(base, consensus_key="hourly:chicago:2026-07-13:08:00", observed_temp=24.5)
            )
            with connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT id, observed_temp FROM hourly_consensus WHERE city = ? AND target_date = ? AND local_hour = ?",
                    ("chicago", "2026-07-13", "08:00"),
                ).fetchall()

        self.assertEqual(first_id, second_id)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(float(rows[0]["observed_temp"]), 24.5)

    def test_canonical_weather_key_migration_deduplicates_legacy_rows(self):
        db_path = test_db_path("canonical_weather_key_migration")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db(db_path)
            with connect(db_path) as conn:
                conn.execute("DROP INDEX idx_metar_reports_station_report_time_unique")
                conn.execute("DROP INDEX idx_hourly_consensus_city_date_hour_unique")
                conn.execute(
                    "DELETE FROM schema_migrations WHERE migration_id = '20260713_01_canonical_weather_keys'"
                )
                conn.execute(
                    """
                    INSERT INTO metar_reports (
                        report_key, station_id, report_time, raw_text, parse_status, created_at, updated_at
                    ) VALUES
                        ('legacy-awc', 'KORD', '2026-07-13T12:51:00+00:00', 'OLD', 'partial', '2026-07-13T13:00:00+00:00', '2026-07-13T13:00:00+00:00'),
                        ('legacy-iem', 'KORD', '2026-07-13T12:51:00+00:00', 'CORRECTED', 'parsed', '2026-07-13T13:05:00+00:00', '2026-07-13T13:05:00+00:00')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO hourly_consensus (
                        consensus_key, city, target_date, local_hour, observed_temp, created_at, updated_at
                    ) VALUES
                        ('legacy-met', 'chicago', '2026-07-13', '08:00', 23.0, '2026-07-13T13:00:00+00:00', '2026-07-13T13:00:00+00:00'),
                        ('legacy-hourly', 'chicago', '2026-07-13', '08:00', 24.5, '2026-07-13T13:05:00+00:00', '2026-07-13T13:05:00+00:00')
                    """
                )

            init_v3_db(db_path)
            with connect(db_path) as conn:
                metar = conn.execute(
                    "SELECT raw_text FROM metar_reports WHERE station_id = 'KORD' AND report_time = '2026-07-13T12:51:00+00:00'"
                ).fetchall()
                consensus = conn.execute(
                    "SELECT observed_temp FROM hourly_consensus WHERE city = 'chicago' AND target_date = '2026-07-13' AND local_hour = '08:00'"
                ).fetchall()
                migration = conn.execute(
                    "SELECT details_json FROM schema_migrations WHERE migration_id = '20260713_01_canonical_weather_keys'"
                ).fetchone()

            init_v3_db(db_path)
            with connect(db_path) as conn:
                migration_count = conn.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE migration_id = '20260713_01_canonical_weather_keys'"
                ).fetchone()[0]

        self.assertEqual([row["raw_text"] for row in metar], ["CORRECTED"])
        self.assertEqual([float(row["observed_temp"]) for row in consensus], [24.5])
        self.assertIsNotNone(migration)
        details = json.loads(str(migration["details_json"]))
        self.assertEqual(details["metar_rows_deleted"], 1)
        self.assertEqual(details["hourly_consensus_rows_deleted"], 1)
        self.assertEqual(migration_count, 1)

    def test_forecast_bulk_insert_preserves_corrected_snapshot_in_one_transaction(self):
        db_path = test_db_path("forecast_bulk_insert")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        first_run = openmeteo_hourly_run(
            "chicago", "2026-07-10", "openmeteo_gfs", [20.0, 21.0]
        )
        second_run = openmeteo_hourly_run(
            "chicago", "2026-07-11", "openmeteo_ecmwf", [22.0, 23.0]
        )
        items = [first_run, second_run]

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            first_ids = insert_forecast_runs(items, db_path)
            corrected_items = []
            for run, members in items:
                corrected_members = [dict(member, high_temp=99.0) for member in members]
                corrected_items.append((run, corrected_members))
            second_ids = insert_forecast_runs(corrected_items, db_path)
            with connect(db_path) as conn:
                run_count = conn.execute("SELECT COUNT(*) FROM forecast_runs").fetchone()[0]
                member_count = conn.execute("SELECT COUNT(*) FROM forecast_members").fetchone()[0]
                highs = [
                    row["high_temp"]
                    for row in conn.execute("SELECT high_temp FROM forecast_members ORDER BY id").fetchall()
                ]

        self.assertNotEqual(first_ids, second_ids)
        self.assertEqual(run_count, 4)
        self.assertEqual(member_count, 4)
        self.assertEqual(highs, [21.0, 23.0, 99.0, 99.0])

    def test_pws_mesonet_rows_parse_and_persist_polywx_xhr_shape(self):
        db_path = test_db_path("mesonet_pws")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        row = {
            "observation_time": "2026-07-01T12:15:00Z",
            "station_id": "KILROSEM4",
            "temperature_c": 30.5,
            "humidity": 51,
            "fetched_at": "2026-07-01T12:16:00Z",
        }
        parsed = mesonet_observation_from_pws_row(row, SETTLEMENT_REGISTRY["chicago"], source_url="https://api.weather.polywx.xyz/api/pws")
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            result = ingest_mesonet_observations(
                {"chicago": [row]},
                network="pws",
                source_url="https://api.weather.polywx.xyz/api/pws",
            )
            evidence = weather_evidence_summary("chicago")

        self.assertEqual(parsed["station_id"], "KILROSEM4")
        self.assertEqual(parsed["raw_unit"], "C")
        self.assertEqual(parsed["parse_status"], "parsed")
        self.assertAlmostEqual(parsed["temperature"], 86.9, places=1)
        self.assertIn("nearby_station", parsed["quality_flags"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["rows_upserted"], 1)
        self.assertEqual(evidence["mesonet_observations"], 1)
        self.assertEqual(evidence["latest_mesonet_observations"][0]["network"], "pws")
        self.assertEqual(evidence["latest_mesonet_observations"][0]["parser_version"], "pws-observation-row-v1")

    def test_openmeteo_historical_backfill_persists_display_only_mesonet_rows(self):
        db_path = test_db_path("openmeteo_historical_backfill")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        payload = {
            "hourly": {
                "time": ["2026-07-01T00:00", "2026-07-01T01:00"],
                "temperature_2m": [77.0, 78.0],
                "relative_humidity_2m": [55, 56],
                "dew_point_2m": [60.0, 61.0],
                "cloud_cover": [20, 30],
                "wind_speed_10m": [10, 11],
                "wind_direction_10m": [180, 190],
                "wind_gusts_10m": [20, 21],
                "surface_pressure": [1000, 1001],
                "pressure_msl": [1012, 1013],
                "precipitation": [0, 0.1],
                "weather_code": [1, 2],
            },
            "daily": {
                "time": ["2026-07-01"],
                "temperature_2m_max": [81.0],
            },
        }

        class FakeResponse:
            status_code = 200
            url = "https://archive-api.open-meteo.com/v1/archive?city=chicago"

            def json(self):
                return payload

            def raise_for_status(self):
                return None

        class FakeSession:
            trust_env = True

            def get(self, url, params, headers, timeout):
                self.url = url
                self.params = params
                return FakeResponse()

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            result = fetch_open_meteo_historical_backfill(
                ["chicago"],
                start_date="2026-07-01",
                end_date="2026-07-01",
                session=FakeSession(),
                history_cache_path=db_path.with_suffix(".history.json"),
            )
            evidence = weather_evidence_summary("chicago")
            with connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT network, raw_unit, quality_flags, temperature, humidity, dew_point, pressure FROM mesonet_observations ORDER BY observed_at"
                ).fetchall()

        self.assertTrue(result["ok"])
        self.assertEqual(result["hourly_rows_upserted"], 2)
        self.assertEqual(evidence["mesonet_observations"], 2)
        self.assertEqual(rows[0]["network"], "open_meteo_historical")
        self.assertEqual(rows[0]["raw_unit"], "F")
        self.assertIn("not_settlement_truth", rows[0]["quality_flags"])
        self.assertAlmostEqual(rows[0]["temperature"], 77.0)
        self.assertEqual(rows[0]["humidity"], 55)
        self.assertAlmostEqual(rows[0]["dew_point"], 60.0)
        self.assertEqual(rows[0]["pressure"], 1012)

    def test_hourly_consensus_exposes_historical_and_pws_without_metar_aliasing(self):
        db_path = test_db_path("hourly_historical_pws_sources")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            upsert_mesonet_observation({
                "city": "chicago",
                "city_name": "Chicago",
                "network": "open_meteo_historical",
                "station_id": "KORD",
                "observed_at": "2026-07-01T16:00:00+00:00",
                "temperature": 84.0,
                "humidity": 50,
                "parser_version": "openmeteo-historical-v1",
                "parse_status": "parsed",
                "raw_unit": "F",
                "quality_flags": ["display_only", "research_truth", "not_settlement_truth"],
            })
            upsert_mesonet_observation({
                "city": "chicago",
                "city_name": "Chicago",
                "network": "wunderground_pws",
                "station_id": "KORD-PWS",
                "observed_at": "2026-07-01T16:10:00+00:00",
                "temperature": 86.0,
                "parser_version": "pws-wunderground-current-v1",
                "parse_status": "parsed",
                "raw_unit": "F",
                "quality_flags": ["display_only", "not_settlement_truth"],
            })
            build_hourly_consensus(["chicago"], target_date="2026-07-01", db_path=db_path)
            points = hourly_consensus_points({"chicago": {"2026-07-01"}}, db_path=db_path)

        point = points["chicago"][0]
        self.assertIsNone(point["metar"])
        self.assertAlmostEqual(point["historical"], 84.0)
        self.assertAlmostEqual(point["pws"], 86.0)

    def test_wunderground_pws_payload_aggregates_as_display_only_mesonet(self):
        payload = {
            "observations": [
                {
                    "stationID": "KILCHICA1",
                    "neighborhood": "Near KORD 1",
                    "obsTimeUtc": "2026-07-04T18:10:00Z",
                    "humidity": 51,
                    "winddir": 210,
                    "metric": {"temp": 30.0, "dewpt": 20.0, "windSpeed": 12, "windGust": 18, "pressure": 1012.3},
                },
                {
                    "stationID": "KILCHICA2",
                    "neighborhood": "Near KORD 2",
                    "obsTimeUtc": "2026-07-04T18:12:00Z",
                    "humidity": 55,
                    "winddir": 220,
                    "metric": {"temp": 31.0, "dewpt": 21.0, "windSpeed": 14, "windGust": 19, "pressure": 1011.9},
                },
            ]
        }
        rows = parse_pws_current_payload(payload, station_id="KILCHICA1")
        aggregate = aggregate_pws_observations(rows, SETTLEMENT_REGISTRY["chicago"], source_url="https://api.weather.com/v2/pws/observations/current")

        self.assertEqual(len(rows), 2)
        self.assertIsNotNone(aggregate)
        self.assertEqual(aggregate["network"], "wunderground_pws")
        self.assertEqual(aggregate["station_id"], "WU_PWS_KORD")
        self.assertAlmostEqual(aggregate["temperature"], 86.9, places=1)
        self.assertEqual(aggregate["raw_unit"], "F")
        self.assertIn("display_only", aggregate["quality_flags"])
        self.assertIn("not_settlement_truth", aggregate["quality_flags"])

    def test_wunderground_pws_discovers_and_fetches_asian_city(self):
        db_path = test_db_path("pws_asian_city")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))

        class FakePwsSession:
            def __init__(self):
                self.calls = []

            def get(self, url, *, params, timeout, headers):
                self.calls.append((url, dict(params)))
                if url.endswith("/v3/location/near"):
                    return FakeHTTPResponse({"location": {"stationId": ["ISHANG123"]}}, url)
                return FakeHTTPResponse({
                    "observations": [{
                        "stationID": "ISHANG123",
                        "neighborhood": "Near ZSPD",
                        "obsTimeUtc": "2026-07-11T08:10:00Z",
                        "humidity": 70,
                        "metric": {"temp": 31.5, "dewpt": 25.0},
                    }],
                }, url)

        session = FakePwsSession()
        with patch.dict(
            os.environ,
            {"V3_DB_PATH": str(db_path), "WUNDERGROUND_API_KEY": "fixture-key"},
            clear=False,
        ):
            init_v3_db(db_path)
            result = fetch_wunderground_pws_city("shanghai", dry_run=True, session=session)

        self.assertTrue(result["ok"])
        self.assertFalse(result.get("skipped", False))
        self.assertEqual(result["station_id"], "WU_PWS_ZSPD")
        self.assertEqual(result["source_station_ids"], ["ISHANG123"])
        self.assertEqual(session.calls[0][1]["geocode"], "31.1443,121.8083")
        self.assertEqual(session.calls[1][1]["stationId"], "ISHANG123")

    def test_wunderground_pws_discovery_404_is_optional_no_coverage(self):
        db_path = test_db_path("pws_asian_no_coverage")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))

        class NoCoverageSession:
            def get(self, url, *, params, timeout, headers):
                response = FakeHTTPResponse({}, url, status_code=200)
                response.status_code = 404

                def raise_for_status():
                    raise requests.HTTPError("404 no PWS coverage", response=response)

                response.raise_for_status = raise_for_status
                return response

        with patch.dict(
            os.environ,
            {"V3_DB_PATH": str(db_path), "WUNDERGROUND_API_KEY": "fixture-key"},
            clear=False,
        ):
            init_v3_db(db_path)
            result = fetch_wunderground_pws_city("shanghai", dry_run=True, session=NoCoverageSession())

        self.assertTrue(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "pws_discovery_not_available")

    def test_hko_china_live_parser_converts_hong_kong_record_time_to_utc(self):
        payload = {
            "updateTime": "2026-07-04T10:02:00+08:00",
            "temperature": {
                "recordTime": "2026-07-04T10:00:00+08:00",
                "data": [
                    {"place": "Hong Kong Observatory", "value": 30, "unit": "C"},
                    {"place": "Chek Lap Kok", "value": 31, "unit": "C"},
                ],
            },
            "humidity": {
                "recordTime": "2026-07-04T10:00:00+08:00",
                "data": [{"place": "Hong Kong Observatory", "value": 80, "unit": "percent"}],
            },
        }
        row = hko_rhrread_observation(payload, raw_response=json.dumps(payload), fetched_at="2026-07-04T02:03:00+00:00")

        self.assertEqual(row["city"], "hong-kong")
        self.assertEqual(row["station_id"], "HKO")
        self.assertEqual(row["network"], "china_live")
        self.assertEqual(row["observed_at"], "2026-07-04T02:00:00+00:00")
        self.assertEqual(row["temperature"], 30.0)
        self.assertEqual(row["humidity"], 80.0)
        self.assertEqual(row["parse_status"], "parsed")
        self.assertIn("not_settlement_truth", row["quality_flags"])

    def test_weathercn_sk2d_parser_reads_shanghai_jsonp_and_preserves_hash(self):
        raw = 'var dataSK={"city":"101020100","cityname":"上海","temp":"31.2","SD":"70%","qy":"1007","wde":"NW","wse":"3km/h","time":"10:00","date":"07月04日(星期六)"}'
        row = weathercn_sk2d_observation(
            raw,
            station_code="101020100",
            source_url="http://d1.weather.com.cn/sk_2d/101020100.html?_=1",
            fetched_at="2026-07-04T02:05:00+00:00",
        )

        self.assertEqual(row["city"], "shanghai")
        self.assertEqual(row["station_id"], "101020100")
        self.assertEqual(row["network"], "china_live")
        self.assertEqual(row["observed_at"], "2026-07-04T02:00:00+00:00")
        self.assertEqual(row["temperature"], 31.2)
        self.assertEqual(row["humidity"], 70.0)
        self.assertEqual(row["wind_direction"], 315)
        self.assertEqual(row["wind_speed"], 3.0)
        self.assertEqual(len(row["raw_response_hash"]), 32)
        self.assertEqual(row["parse_status"], "parsed")

    def test_weathercn_html_structure_failure_is_failed_not_fake_zero(self):
        row = weathercn_sk2d_observation(
            "<html><body>loading</body></html>",
            fetched_at="2026-07-04T02:05:00+00:00",
        )

        self.assertEqual(row["parse_status"], "failed")
        self.assertIn("weathercn_datask_not_found", row["parse_warnings"])
        self.assertIsNone(row["temperature"])

    def test_weathercom_current_fallback_is_labeled_and_redacts_api_key(self):
        payload = {
            "validTimeUtc": 1784341081,
            "temperature": 36,
            "relativeHumidity": 54,
            "windSpeed": 12,
            "windDirection": 180,
            "pressureMeanSeaLevel": 1004,
        }
        secret = "weathercom-current-test-key"
        with patch.dict(os.environ, {"WEATHER_COM_API_KEY": secret}, clear=False), patch(
            "weatherbot_v3.china_weather._http_get",
            return_value=json.dumps(payload),
        ):
            row = _weathercom_current_observation(
                SETTLEMENT_REGISTRY["shanghai"],
                primary_failure="http_502",
            )

        self.assertEqual(row["network"], "china_live")
        self.assertEqual(row["station_id"], "ZSPD")
        self.assertEqual(row["temperature"], 36.0)
        self.assertEqual(row["raw_json"]["provider"], "weathercom_v3_current")
        self.assertIn("weathercn_primary_unavailable:http_502", row["parse_warnings"])
        self.assertNotIn(secret, row["source_url"])
        self.assertIn("apiKey=***", row["source_url"])
        self.assertIn("not_settlement_truth", row["quality_flags"])

    def test_weathercom_current_fallback_is_exposed_as_shanghai_china_live_series(self):
        db_path = test_db_path("weathercom_current_china_live_series")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db()
            upsert_mesonet_observation({
                "city": "shanghai",
                "city_name": "Shanghai",
                "station_id": "ZSPD",
                "station_name": "Weather.com current near ZSPD",
                "network": "china_live",
                "observed_at": "2026-07-18T03:00:00+00:00",
                "temperature": 36.0,
                "humidity": 54.0,
                "source_url": "https://api.weather.com/v3/wx/observations/current?apiKey=***",
                "raw_unit": "C",
                "parser_version": "china-live-v2",
                "parse_status": "partial",
                "parse_warnings": ["weathercn_primary_unavailable:http_502"],
                "quality_flags": ["china_live", "display_only", "not_settlement_truth"],
            })
            series = source_series_summary("shanghai", "2026-07-18", db_path=db_path)

        self.assertEqual(len(series["china_live"]), 1)
        self.assertEqual(series["china_live"][0]["station_id"], "ZSPD")
        self.assertEqual(series["china_live"][0]["temperature"], 36.0)

    def test_china_live_mesonet_upsert_is_station_time_idempotent(self):
        db_path = test_db_path("china_live_idempotent")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        raw = 'var dataSK={"city":"101020100","cityname":"上海","temp":"31.2","SD":"70%","time":"10:00","date":"07月04日(星期六)"}'
        row = weathercn_sk2d_observation(raw, fetched_at="2026-07-04T02:05:00+00:00")
        updated = weathercn_sk2d_observation(raw.replace("31.2", "31.5"), fetched_at="2026-07-04T02:08:00+00:00")
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db()
            first_id = upsert_mesonet_observation(row)
            second_id = upsert_mesonet_observation(updated)
            with connect(db_path) as conn:
                rows = conn.execute("SELECT * FROM mesonet_observations").fetchall()

        self.assertEqual(first_id, second_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["network"], "china_live")
        self.assertEqual(rows[0]["temperature"], 31.5)
        self.assertIsNotNone(rows[0]["raw_response_hash"])

    def test_china_weather_cli_dry_run_does_not_write_mesonet_rows(self):
        db_path = test_db_path("china_weather_cli_dry_run")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        hko_payload = {
            "updateTime": "2026-07-04T10:02:00+08:00",
            "temperature": {"recordTime": "2026-07-04T10:00:00+08:00", "data": [{"place": "Hong Kong Observatory", "value": 30, "unit": "C"}]},
            "humidity": {"data": [{"place": "Hong Kong Observatory", "value": 80, "unit": "percent"}]},
        }
        raw_hko = json.dumps(hko_payload)
        raw_sh = 'var dataSK={"city":"101020100","temp":"31.2","SD":"70%","time":"10:00","date":"07月04日(星期六)"}'

        def fake_http_get(url, **_kwargs):
            return raw_hko if "data.weather.gov.hk" in url else raw_sh

        with patch.dict(
            os.environ,
            {"V3_DB_PATH": str(db_path), "WEATHER_COM_API_KEY": ""},
            clear=False,
        ):
            init_v3_db()
            with patch("weatherbot_v3.china_weather._http_get", side_effect=fake_http_get):
                result = run_china_weather_fetch("shanghai,hongkong", dry_run=True)
            with connect(db_path) as conn:
                count = conn.execute("SELECT COUNT(*) FROM mesonet_observations").fetchone()[0]

        self.assertTrue(result["ok"])
        self.assertEqual(result["cities"], 2)
        self.assertEqual(result["rows_upserted"], 0)
        self.assertEqual(count, 0)

    def test_china_weather_batch_isolates_one_city_http_failure(self):
        db_path = test_db_path("china_weather_batch_failure")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        hko_payload = {
            "updateTime": "2026-07-04T10:02:00+08:00",
            "temperature": {
                "recordTime": "2026-07-04T10:00:00+08:00",
                "data": [{"place": "Hong Kong Observatory", "value": 30, "unit": "C"}],
            },
            "humidity": {"data": [{"place": "Hong Kong Observatory", "value": 80, "unit": "percent"}]},
        }

        def fake_http_get(url, **_kwargs):
            if "weather.com.cn" in url:
                raise OSError("upstream 502")
            return json.dumps(hko_payload)

        with patch.dict(
            os.environ,
            {"V3_DB_PATH": str(db_path), "WEATHER_COM_API_KEY": ""},
            clear=False,
        ):
            init_v3_db()
            with patch("weatherbot_v3.china_weather.env_value", return_value=""), patch(
                "weatherbot_v3.china_weather._http_get", side_effect=fake_http_get
            ):
                result = run_china_weather_fetch(
                    "shanghai,hongkong",
                    dry_run=False,
                    refresh_readiness=False,
                )
            with connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT city, network FROM mesonet_observations ORDER BY city"
                ).fetchall()

        self.assertFalse(result["ok"])
        self.assertEqual(result["cities"], 2)
        self.assertEqual(result["ok_cities"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["rows_upserted"], 1)
        self.assertEqual(result["results"][0]["error"], "china_live_fetch_failed")
        self.assertEqual([(row["city"], row["network"]) for row in rows], [("hong-kong", "china_live")])

    def test_hourly_consensus_exposes_china_live_without_overwriting_metar(self):
        db_path = test_db_path("hourly_china_live")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db()
            upsert_mesonet_observation(weathercn_sk2d_observation(
                'var dataSK={"city":"101020100","temp":"31.2","SD":"70%","time":"10:00","date":"07月04日(星期六)"}',
                fetched_at="2026-07-04T02:05:00+00:00",
            ))
            result = build_hourly_consensus(["shanghai"], target_date="2026-07-04", db_path=db_path)
            points = hourly_consensus_points({"shanghai": {"2026-07-04"}}, db_path=db_path)
            with connect(db_path) as conn:
                raw_json = conn.execute("SELECT raw_json FROM hourly_consensus WHERE city = 'shanghai'").fetchone()["raw_json"]

        self.assertTrue(result["ok"])
        self.assertEqual(points["shanghai"][0]["china_live"], 31.2)
        self.assertIsNone(points["shanghai"][0]["metar"])
        persisted = json.loads(raw_json)
        self.assertIn("china_live", persisted["raw_json"]["observation"]["source_temperatures"])

    def test_observations_api_returns_layer2_evidence_without_refreshing(self):
        db_path = test_db_path("observations_api")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            upsert_metar_report({
                "city": "chicago",
                "city_name": "Chicago",
                "station_id": "KORD",
                "report_time": "2026-06-29T21:51:00Z",
                "raw_text": "METAR KORD 292151Z 20017G26KT 10SM 33/23 A2987",
                "temperature": 91.94,
            })
            payload = asyncio.run(observations_api(city="chicago", target_date="2026-06-29"))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["city"], "chicago")
        self.assertEqual(payload["evidence"]["metar_reports"], 1)

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
        self.assertEqual(len(SETTLEMENT_REGISTRY), 51)
        self.assertIn("hong-kong", SETTLEMENT_REGISTRY)
        self.assertEqual(len({profile.station_id for profile in SETTLEMENT_REGISTRY.values()}), 51)
        for city, profile in SETTLEMENT_REGISTRY.items():
            self.assertEqual(city, profile.city)
            self.assertTrue(profile.station_id)
            self.assertNotEqual(profile.timezone, "UTC")
            self.assertIn(profile.unit, {"F", "C"})

        tokyo = SETTLEMENT_REGISTRY["tokyo"]
        self.assertEqual(tokyo.station_id, "RJTT")
        self.assertAlmostEqual(tokyo.latitude, 35.553, places=3)
        self.assertAlmostEqual(tokyo.longitude, 139.781, places=3)
        self.assertEqual(tokyo.location_version, 2)

        for city, station in {
            "manila": "RPLL",
            "guangzhou": "ZGGG",
            "busan": "RKPK",
            "los-angeles": "KLAX",
            "cape-town": "FACT",
        }.items():
            profile = SETTLEMENT_REGISTRY[city]
            self.assertEqual(profile.station_id, station)
            self.assertEqual(profile.city_scope, "observation_only")

    def test_weathercom_geocode_must_match_settlement_station(self):
        tokyo = SETTLEMENT_REGISTRY["tokyo"]
        self.assertTrue(forecast_source_matches_profile_location(
            "https://api.weather.com/v3/wx/forecast/hourly/15day?geocode=35.553%2C139.781",
            tokyo,
        ))
        self.assertFalse(forecast_source_matches_profile_location(
            "https://api.weather.com/v3/wx/forecast/hourly/15day?geocode=35.7647%2C140.3864",
            tokyo,
        ))
        self.assertTrue(forecast_source_matches_profile_location(
            "https://api.open-meteo.com/v1/forecast?latitude=35.553&longitude=139.781",
            tokyo,
        ))
        self.assertFalse(forecast_source_matches_profile_location(
            "https://api.open-meteo.com/v1/forecast?latitude=35.7647&longitude=140.3864",
            tokyo,
        ))

    def test_station_registry_sync_persists_layer1_station_rows(self):
        db_path = test_db_path("stations_registry")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            result = sync_station_registry(db_path)
            rows = list_stations(db_path)
            summary = dashboard_summary()

        chicago = next(row for row in rows if row["city_key"] == "chicago")
        expected_count = len(SETTLEMENT_REGISTRY)
        self.assertTrue(result["ok"])
        self.assertEqual(result["synced"], expected_count)
        self.assertEqual(len(rows), expected_count)
        self.assertEqual(summary["stations"], expected_count)
        self.assertEqual(chicago["station_id"], "KORD")
        self.assertEqual(chicago["icao_id"], "KORD")
        self.assertEqual(chicago["timezone"], "America/Chicago")
        self.assertEqual(chicago["provider_station_ids"]["aviationweather"], "KORD")
        self.assertIn("METAR", chicago["nearby_observation_networks"])
        self.assertIn("requires rule/source verification", chicago["settlement_rule_text"])
        self.assertTrue(chicago["display_enabled"])
        self.assertEqual(chicago["city_scope"], "market_candidate")

        manila = next(row for row in rows if row["city_key"] == "manila")
        self.assertTrue(manila["display_enabled"])
        self.assertFalse(manila["enabled"])
        self.assertEqual(manila["city_scope"], "observation_only")

    def test_station_sync_reconciles_verified_timestamp_before_registry_upsert(self):
        db_path = test_db_path("stations_verification_reconcile")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        rule = "Polymarket resolves Chicago from Wunderground KORD local-day history."
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            sync_station_registry(db_path)
            with connect(db_path) as conn:
                conn.execute(
                    """
                    UPDATE stations
                    SET settlement_rule_text = ?, settlement_rule_verified_at = ?,
                        primary_settlement_source = 'wunderground',
                        verification_status = 'provisional'
                    WHERE city_key = 'chicago'
                    """,
                    (rule, "2026-07-04T00:00:00+00:00"),
                )
            result = sync_station_registry(db_path)
            chicago = list_stations(db_path, city="chicago")[0]

        self.assertEqual(chicago["verification_status"], "verified")
        self.assertEqual(chicago["settlement_rule_text"], rule)
        self.assertEqual(chicago["primary_settlement_source"], "wunderground")
        self.assertEqual(result["verification_reconciliation"]["repaired_count"], 1)

    def test_station_reconcile_recovers_probe_evidence_overwritten_by_legacy_sync(self):
        db_path = test_db_path("stations_probe_evidence_recovery")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        rule = "This market resolves from Wunderground KORD highest temperature."
        details = {
            "active_market": True,
            "city_key": "chicago",
            "verification_status": "verified",
            "settlement_rule_text": rule,
            "settlement_station_id": "KORD",
            "settlement_station_name": "Chicago O'Hare International Airport",
            "settlement_timezone": "America/Chicago",
            "settlement_unit": "F",
            "settlement_time_basis": "local_day",
            "primary_settlement_source": "wunderground",
            "source_url": "https://www.wunderground.com/history/daily/us/il/chicago/KORD",
        }
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            sync_station_registry(db_path)
            with connect(db_path) as conn:
                conn.execute(
                    """
                    UPDATE stations
                    SET settlement_rule_verified_at = ?, verification_status = 'provisional'
                    WHERE city_key = 'chicago'
                    """,
                    ("2026-07-04T00:00:00+00:00",),
                )
            log_data_fetch(
                source="polymarket_gamma",
                stage="settlement_rule_probe",
                status="OK",
                message="active_market",
                details=details,
            )
            result = reconcile_station_verification_status(db_path)
            chicago = list_stations(db_path, city="chicago")[0]

        self.assertEqual(result["repaired_count"], 1)
        self.assertEqual(chicago["verification_status"], "verified")
        self.assertEqual(chicago["settlement_rule_text"], rule)
        self.assertEqual(chicago["primary_settlement_source"], "wunderground")
        self.assertEqual(json.loads(chicago["raw_json"])["latest_market_probe"]["source_url"], details["source_url"])

    def test_station_reconcile_downgrades_terminal_status_without_timestamp(self):
        db_path = test_db_path("stations_terminal_without_timestamp")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            sync_station_registry(db_path)
            with connect(db_path) as conn:
                conn.execute(
                    """
                    UPDATE stations
                    SET verification_status = 'verified',
                        settlement_rule_verified_at = ''
                    WHERE city_key = 'chicago'
                    """
                )
            result = reconcile_station_verification_status(db_path)
            chicago = list_stations(db_path, city="chicago")[0]

        self.assertTrue(result["ok"])
        self.assertEqual(result["repaired_count"], 1)
        self.assertEqual(chicago["verification_status"], "unverified")
        self.assertEqual(chicago["settlement_rule_verified_at"], "")

    def test_station_reconcile_clears_invalid_verified_timestamp(self):
        db_path = test_db_path("stations_invalid_verified_timestamp")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            sync_station_registry(db_path)
            with connect(db_path) as conn:
                conn.execute(
                    """
                    UPDATE stations
                    SET verification_status = 'provisional',
                        settlement_rule_verified_at = '2026-07-04T00:00:00+00:00'
                    WHERE city_key = 'chicago'
                    """
                )
            result = reconcile_station_verification_status(db_path)
            chicago = list_stations(db_path, city="chicago")[0]

        self.assertTrue(result["ok"])
        self.assertEqual(result["repaired_count"], 1)
        self.assertEqual(chicago["verification_status"], "provisional")
        self.assertEqual(chicago["settlement_rule_verified_at"], "")

    def test_source_health_matrix_reports_fresh_core_sources_and_truth_blockers(self):
        db_path = test_db_path("source_health_matrix")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        now = datetime.now(timezone.utc)
        with patch.dict(
            os.environ,
            {"V3_DB_PATH": str(db_path), "WEATHER_COM_FORECAST_ENABLED": "false"},
            clear=False,
        ):
            sync_station_registry(db_path)
            with connect(db_path) as conn:
                conn.execute("UPDATE stations SET enabled = 0")
                conn.execute("UPDATE stations SET enabled = 1 WHERE city_key = 'chicago'")
            upsert_metar_report({
                "city": "chicago",
                "city_name": "Chicago",
                "station_id": "KORD",
                "report_time": now.isoformat(),
                "raw_text": "KORD TEST",
                "temperature": 25.0,
                "parse_status": "parsed",
            })
            insert_forecast_run({
                "run_key": "health-openmeteo-chicago",
                "city": "chicago",
                "target_date": now.date().isoformat(),
                "source": "openmeteo_gfs_seamless",
                "provider": "open_meteo",
                "model": "gfs_seamless",
                "retrieved_at": now.isoformat(),
                "parse_status": "parsed",
                "training_eligible": True,
            })
            matrix = build_source_health_matrix(db_path, now_utc=now)

        sources = {row["key"]: row for row in matrix["sources"]}
        self.assertEqual(sources["metar"]["status"], "healthy")
        self.assertEqual(sources["forecast_openmeteo"]["status"], "healthy")
        self.assertTrue(sources["forecast_weathercom_v3"]["required"])
        self.assertEqual(sources["truth_wunderground_daily"]["status"], "missing")
        self.assertIn("truth_wunderground_daily", matrix["required_blockers"])
        self.assertEqual(matrix["overall_status"], "blocked")
        self.assertEqual(matrix["version"], "source-health-v2")
        self.assertIn("weather_com_configured", matrix["config"])
        self.assertIn("wunderground_pws_configured", matrix["config"])
        chicago = next(row for row in matrix["city_matrix"] if row["city_key"] == "chicago")
        self.assertEqual(chicago["sources"]["metar"]["status"], "healthy")
        self.assertEqual(chicago["sources"]["forecast_openmeteo"]["status"], "healthy")
        self.assertEqual(chicago["sources"]["truth_wunderground_daily"]["status"], "missing")

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
        self.assertEqual(payload["sync"]["synced"], len(SETTLEMENT_REGISTRY))

    def test_polymarket_resolution_text_parser_extracts_station_source_unit_and_time_basis(self):
        parsed = parse_settlement_rule_text(
            city_key="chicago",
            event_slug="highest-temperature-in-chicago-on-july-4-2026",
            source_url="https://www.wunderground.com/history/daily/us/il/chicago/KORD",
            description=(
                "This market will resolve to the temperature range that contains the highest temperature recorded at "
                "the Chicago O'Hare Intl Airport Station in degrees Fahrenheit on 4 Jul '26. The resolution source "
                "for this market will be information from Wunderground, specifically the highest temperature recorded "
                "for all times on this day for the Chicago O'Hare Intl Airport Station."
            ),
        )

        self.assertEqual(parsed["settlement_station_id"], "KORD")
        self.assertEqual(parsed["settlement_station_name"], "Chicago O'Hare Intl Airport")
        self.assertEqual(parsed["primary_settlement_source"], "wunderground")
        self.assertEqual(parsed["settlement_unit"], "F")
        self.assertEqual(parsed["settlement_timezone"], "America/Chicago")
        self.assertEqual(parsed["settlement_time_basis"], "local_day")

    def test_polymarket_resolution_text_parser_handles_hong_kong_observatory(self):
        parsed = parse_settlement_rule_text(
            city_key="hong-kong",
            event_slug="highest-temperature-in-hong-kong-on-july-4-2026",
            source_url="https://www.weather.gov.hk/en/cis/climat.htm",
            description=(
                "This market will resolve to the temperature range that contains the highest temperature recorded "
                "by the Hong Kong Observatory in degrees Celsius on 4 Jul '26. The resolution source for this market "
                "will be information from the Hong Kong Observatory, specifically the \"Absolute Daily Max (deg. C)\" "
                "the specified date once information is finalized in the relevant \"Daily Extract\"."
            ),
        )

        self.assertEqual(parsed["settlement_station_id"], "HKO")
        self.assertEqual(parsed["settlement_station_name"], "Hong Kong Observatory")
        self.assertEqual(parsed["primary_settlement_source"], "hong_kong_observatory")
        self.assertEqual(parsed["settlement_unit"], "C")
        self.assertEqual(parsed["settlement_timezone"], "Asia/Hong_Kong")

    def test_polymarket_resolution_text_parser_accepts_w_prefix_icao(self):
        parsed = parse_settlement_rule_text(
            city_key="singapore",
            event_slug="highest-temperature-in-singapore-on-july-11-2026",
            source_url="https://www.wunderground.com/history/daily/sg/singapore/WSSS",
            description=(
                "This market resolves to the highest temperature recorded at the Singapore Changi Airport "
                "Station in degrees Celsius. Wunderground reports all times on this day."
            ),
        )

        self.assertEqual(parsed["settlement_station_id"], "WSSS")
        self.assertEqual(parsed["primary_settlement_source"], "wunderground")
        self.assertEqual(parsed["settlement_time_basis"], "local_day")

    def test_active_market_with_incomplete_contract_stays_unverified(self):
        db_path = test_db_path("polymarket_market_probe_incomplete")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        event_payload = {
            "slug": "highest-temperature-in-singapore-on-july-11-2026",
            "title": "Highest temperature in Singapore on July 11?",
            "active": True,
            "closed": False,
            "markets": [{
                "id": "market-singapore-incomplete",
                "active": True,
                "closed": False,
                "description": "This market resolves to a daily temperature range.",
                "resolutionSource": "",
            }],
        }

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            result = probe_polymarket_markets(
                ["singapore"],
                today=date(2026, 7, 11),
                fetch_json=lambda _url: event_payload,
                path=db_path,
                sleep_seconds=0,
            )
            row = list_stations(db_path, city="singapore")[0]

        self.assertEqual(result["results"][0]["verification_status"], "unverified")
        self.assertEqual(row["verification_status"], "unverified")
        self.assertEqual(row["settlement_rule_verified_at"], "")

    def test_polymarket_market_probe_writes_verified_station_rule_idempotently(self):
        db_path = test_db_path("polymarket_market_probe")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        slug = "highest-temperature-in-shanghai-on-july-4-2026"
        event_payload = {
            "slug": slug,
            "title": "Highest temperature in Shanghai on July 4?",
            "active": True,
            "closed": False,
            "markets": [{
                "id": "market-shanghai-1",
                "active": True,
                "closed": False,
                "description": (
                    "This market will resolve to the temperature range that contains the highest temperature recorded "
                    "at the Shanghai Pudong International Airport Station in degrees Celsius on 4 Jul '26. The resolution "
                    "source for this market will be information from Wunderground, specifically the highest temperature "
                    "recorded for all times on this day for the Shanghai Pudong International Airport Station."
                ),
                "resolutionSource": "https://www.wunderground.com/history/daily/cn/shanghai/ZSPD",
            }],
        }

        def fake_fetch(url: str):
            self.assertIn(slug, url)
            return event_payload

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db(db_path)
            sync_station_registry(path=db_path)
            first = probe_polymarket_markets(["shanghai"], today=date(2026, 7, 4), fetch_json=fake_fetch, path=db_path, sleep_seconds=0)
            second = probe_polymarket_markets(["shanghai"], today=date(2026, 7, 4), fetch_json=fake_fetch, path=db_path, sleep_seconds=0)
            rows = list_stations(db_path, city="shanghai")
            logs = list_data_fetch_logs(10)

        self.assertEqual(first["active"], 1)
        self.assertEqual(second["active"], 1)
        self.assertEqual(first["mismatches"], 0)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["station_id"], "ZSPD")
        self.assertEqual(row["settlement_station_id"], "ZSPD")
        self.assertEqual(row["settlement_timezone"], "Asia/Shanghai")
        self.assertEqual(row["settlement_unit"], "C")
        self.assertEqual(row["verification_status"], "verified")
        self.assertEqual(sum(1 for log in logs if log["stage"] == "settlement_rule_probe"), 1)

    def test_polymarket_market_probe_marks_hong_kong_settlement_mismatch(self):
        db_path = test_db_path("polymarket_market_probe_hk")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        event_payload = {
            "slug": "highest-temperature-in-hong-kong-on-july-4-2026",
            "title": "Highest temperature in Hong Kong on July 4?",
            "active": True,
            "closed": False,
            "markets": [{
                "id": "market-hk-1",
                "active": True,
                "closed": False,
                "description": (
                    "This market will resolve to the temperature range that contains the highest temperature recorded "
                    "by the Hong Kong Observatory in degrees Celsius on 4 Jul '26. The resolution source for this market "
                    "will be information from the Hong Kong Observatory, specifically the \"Absolute Daily Max (deg. C)\" "
                    "the specified date once information is finalized in the relevant \"Daily Extract\"."
                ),
            }],
        }

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db(db_path)
            sync_station_registry(path=db_path)
            result = probe_polymarket_markets(["hong-kong"], today=date(2026, 7, 4), fetch_json=lambda _url: event_payload, path=db_path, sleep_seconds=0)
            row = list_stations(db_path, city="hong-kong")[0]
            readiness = build_data_readiness(db_path)

        self.assertEqual(result["mismatches"], 1)
        self.assertEqual(row["station_id"], "VHHH")
        self.assertEqual(row["settlement_station_id"], "HKO")
        self.assertEqual(row["verification_status"], "settlement_mismatch")
        stations_stage = next(stage for stage in readiness["stages"] if stage["key"] == "stations")
        self.assertIn("settlement_mismatch", {reason["code"] for reason in stations_stage["reasons"]})

    def test_polymarket_market_probe_records_no_active_market_without_fabricating_station(self):
        db_path = test_db_path("polymarket_market_probe_no_active")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))

        def empty_event(_url: str):
            return {"slug": "highest-temperature-in-chicago-on-july-4-2026", "active": False, "closed": False, "markets": []}

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db(db_path)
            sync_station_registry(path=db_path)
            result = probe_polymarket_markets(["chicago"], today=date(2026, 7, 4), fetch_json=empty_event, path=db_path, sleep_seconds=0)
            row = list_stations(db_path, city="chicago")[0]

        self.assertEqual(result["no_active_market"], 1)
        self.assertEqual(row["station_id"], "KORD")
        self.assertEqual(row["settlement_station_id"], "KORD")
        self.assertEqual(row["verification_status"], "no_active_market")

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
        observations_stage = next(stage for stage in readiness["stages"] if stage["key"] == "observations")
        self.assertEqual(stations_stage["status"], "blocked")
        self.assertEqual(stations_stage["metrics"]["stations"], len(SETTLEMENT_REGISTRY))
        self.assertIn("settlement_rule_unverified", {item["code"] for item in stations_stage["reasons"]})
        self.assertEqual(observations_stage["status"], "blocked")
        self.assertIn("metar_reports_missing", {item["code"] for item in observations_stage["reasons"]})
        self.assertEqual(readiness["summary"]["market_rules"], 1)
        self.assertEqual(readiness["summary"]["station_rows"], len(SETTLEMENT_REGISTRY))
        self.assertEqual(readiness["production_phase"]["id"], "phase1_5")
        blocker_codes = {item["code"] for item in readiness["blockers"]}
        self.assertIn("metar_reports_missing", blocker_codes)
        self.assertIn("settlement_rule_not_manually_verified", blocker_codes)
        self.assertIn("versioned_forecast_runs_missing", blocker_codes)

    def test_data_readiness_history_is_bounded(self):
        db_path = test_db_path("data_readiness_retention")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch("weatherbot_v3.qualification.AUDIT_HISTORY_RETENTION", 3):
            for index in range(5):
                persist_data_readiness(
                    {
                        "audit_version": "test-readiness-v1",
                        "status": "blocked",
                        "score": 0.1,
                        "live_allowed": False,
                        "generated_at": f"2026-07-12T00:00:0{index}+00:00",
                    },
                    db_path,
                )
        with connect(db_path) as conn:
            rows = conn.execute(
                "SELECT created_at FROM data_qualification_audits ORDER BY id"
            ).fetchall()

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["created_at"], "2026-07-12T00:00:02+00:00")

    def test_data_readiness_exposes_market_bucket_stage(self):
        db_path = test_db_path("data_readiness_market_buckets")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db(db_path)
            upsert_market_bucket({
                "market_id": "blocked-market",
                "question": "Will the highest temperature in Chicago be 90°F on July 1, 2026?",
                "city": "chicago",
                "target_date": "2026-07-01",
                "unit": "F",
                "bucket_label": "90F",
                "bucket_direction": "exact",
                "bucket_low": 90,
                "bucket_high": 90,
                "strict_match_status": "blocked",
                "strict_match_reasons": ["yes_token_missing", "tick_size_missing"],
            })
            readiness = build_data_readiness(db_path)
        stage = next(stage for stage in readiness["stages"] if stage["key"] == "market_buckets")
        self.assertEqual(stage["status"], "blocked")
        self.assertEqual(stage["metrics"]["buckets"], 1)
        self.assertEqual(stage["metrics"]["matched_buckets"], 0)
        codes = {reason["code"] for reason in stage["reasons"]}
        self.assertIn("market_bucket_strict_matches_missing", codes)
        self.assertIn("market_bucket_yes_token_missing", codes)
        self.assertIn("market_bucket_tick_size_missing", codes)
        self.assertEqual(readiness["summary"]["market_buckets"], 1)

    def _seed_signal_decision_fixture(
        self,
        db_path: Path,
        *,
        strict_match_status: str = "matched",
        bucket_direction: str = "range",
        best_ask: float = 0.20,
        best_bid: float = 0.195,
        token_suffix: str = "mid",
        bias_sample_count: int = 0,
        target_date: str = "2026-07-02",
    ) -> None:
        quote_timestamp = datetime.now(timezone.utc).isoformat()
        compact_date = target_date.replace("-", "")
        upsert_daily_max_prediction({
            "city_key": "chicago",
            "target_date": target_date,
            "issued_at": "2026-07-02T12:00:00+00:00",
            "mu": 90.0,
            "sigma": 2.0,
            "unit": "F",
            "method": "polywx_aligned_deb_v1",
            "deb_version": "polywx_aligned_deb_v1",
            "forecast_algo": "polywx_aligned_deb_v1",
            "cohort_contract_version": "forecast-component-cohort-v1",
            "cohort_as_of": "2026-07-02T12:00:00+00:00",
            "sigma_floor": 0.5,
            "bias_sample_count": bias_sample_count,
            "source_run_ids": [101, 102],
            "components": [{
                "source": "openmeteo_ncep_hrrr_conus",
                "weight": 0.5,
                "source_age_ok": True,
                "source_skew_ok": True,
            }],
        }, path=db_path)
        for bucket in [
            {
                "bucket_key": f"chicago-{compact_date}-low",
                "bucket_direction": "or_below",
                "bucket_low": None,
                "bucket_high": 88.0,
                "yes_token_id": "yes-low",
                "best_ask": 0.20,
                "best_bid": 0.195,
            },
            {
                "bucket_key": f"chicago-{compact_date}-{token_suffix}",
                "bucket_direction": bucket_direction,
                "bucket_low": 89.0,
                "bucket_high": 91.0,
                "yes_token_id": f"yes-{token_suffix}",
                "best_ask": best_ask,
                "best_bid": best_bid,
            },
            {
                "bucket_key": f"chicago-{compact_date}-high",
                "bucket_direction": "or_above",
                "bucket_low": 92.0,
                "bucket_high": None,
                "yes_token_id": "yes-high",
                "best_ask": 0.20,
                "best_bid": 0.195,
            },
        ]:
            upsert_market_bucket({
                "bucket_key": bucket["bucket_key"],
                "event_url": "https://polymarket.com/event/highest-temperature-in-chicago-on-july-2-2026",
                "market_id": bucket["bucket_key"],
                "question": "Will the highest temperature in Chicago match this test bucket?",
                "city": "chicago",
                "city_name": "Chicago",
                "target_date": target_date,
                "station_id": "KORD",
                "unit": "F",
                "bucket_label": bucket["bucket_key"],
                "bucket_direction": bucket["bucket_direction"],
                "bucket_low": bucket["bucket_low"],
                "bucket_high": bucket["bucket_high"],
                "outcome_name": "Yes",
                "yes_token_id": bucket["yes_token_id"],
                "token_id": bucket["yes_token_id"],
                "best_ask": bucket["best_ask"],
                "best_bid": bucket["best_bid"],
                "spread": round(bucket["best_ask"] - bucket["best_bid"], 4),
                "order_min_size": 5.0,
                "tick_size": 0.01,
                "neg_risk": False,
                "enable_order_book": True,
                "quote_timestamp": quote_timestamp,
                "bid_depth": 50,
                "ask_depth": 50,
                "strict_match_status": strict_match_status if token_suffix in bucket["bucket_key"] else "matched",
                "strict_match_reasons": [] if strict_match_status == "matched" else ["test_strict_mismatch"],
            }, path=db_path)

    def test_signal_decisions_build_edge_and_keep_live_locked_for_bias_samples(self):
        db_path = test_db_path("signal_decision_build")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "LIVE_TRADING": "false"}, clear=False):
            init_v3_db(db_path)
            self._seed_signal_decision_fixture(db_path)
            result = build_signal_decisions("chicago", "2026-07-02", path=db_path)
            rows = list_signal_decisions(city_key="chicago", target_date="2026-07-02", path=db_path)
            mid = next(row for row in rows if row["bucket_key"] == "chicago-20260702-mid")

        self.assertTrue(result["ok"])
        self.assertEqual(result["stored"], 3)
        self.assertEqual(len(rows), 3)
        self.assertEqual(mid["paper_decision"], "buy")
        self.assertEqual(mid["gate_status"], "paper_allowed")
        self.assertEqual(mid["live_decision"], "blocked")
        self.assertIn("insufficient_bias_samples", mid["gate_reasons"])
        self.assertEqual(mid["blocked_reason_primary"], "insufficient_bias_samples")
        self.assertGreater(mid["model_probability"], mid["market_implied_probability"])
        self.assertGreater(mid["edge"], 0.03)
        self.assertEqual(mid["decision_version"], "signal-decision-v3")
        self.assertTrue(mid["strategy_revision_id"].startswith("spr_"))
        self.assertIn("daily_max_prediction_id", mid["evidence_links"])

    def test_polywx_aligned_decisions_integrate_gaussian_instead_of_model_weight_spikes(self):
        db_path = test_db_path("signal_decision_polywx_gaussian")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "LIVE_TRADING": "false"}, clear=False):
            init_v3_db(db_path)
            self._seed_signal_decision_fixture(db_path)
            upsert_daily_max_prediction({
                "city_key": "chicago",
                "target_date": "2026-07-02",
                "issued_at": "2026-07-02T13:00:00+00:00",
                "mu": 90.0,
                "sigma": 2.0,
                "unit": "F",
                "method": "polywx_aligned_deb_v1",
                "deb_version": "polywx_aligned_deb_v1",
                "forecast_algo": "polywx_aligned_deb_v1",
                "sigma_floor": 0.9,
                "source_run_ids": [201, 202],
                "ensemble_samples": [
                    {"value": 84.0, "weight": 0.55, "source": "weathercom_v3_forecast"},
                    {"value": 96.0, "weight": 0.45, "source": "openmeteo_gfs_seamless"},
                ],
            }, path=db_path)
            result = build_signal_decisions("chicago", "2026-07-02", path=db_path)
            rows = list_signal_decisions(city_key="chicago", target_date="2026-07-02", path=db_path)
            mid = next(row for row in rows if row["bucket_key"] == "chicago-20260702-mid")

        self.assertTrue(result["ok"])
        self.assertEqual(mid["model_distribution"]["method"], "gaussian-cdf-v1")
        self.assertGreater(mid["model_probability"], 0.50)
        self.assertLess(mid["model_probability"], 0.60)

    def test_signal_decisions_are_idempotent_by_decision_id(self):
        db_path = test_db_path("signal_decision_idempotent")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "LIVE_TRADING": "false"}, clear=False):
            init_v3_db(db_path)
            self._seed_signal_decision_fixture(db_path)
            first = build_signal_decisions("chicago", "2026-07-02", path=db_path)
            second = build_signal_decisions("chicago", "2026-07-02", path=db_path)
            rows = list_signal_decisions(city_key="chicago", target_date="2026-07-02", path=db_path)
            decision_ids = {row["decision_id"] for row in rows}

        self.assertEqual(first["stored"], 3)
        self.assertEqual(second["stored"], 3)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(decision_ids), 3)

    def test_signal_decisions_block_non_strict_bucket_match(self):
        db_path = test_db_path("signal_decision_strict_block")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "LIVE_TRADING": "false"}, clear=False):
            init_v3_db(db_path)
            self._seed_signal_decision_fixture(db_path, strict_match_status="blocked")
            build_signal_decisions("chicago", "2026-07-02", path=db_path)
            rows = list_signal_decisions(city_key="chicago", target_date="2026-07-02", path=db_path)
            mid = next(row for row in rows if row["bucket_key"] == "chicago-20260702-mid")

        self.assertEqual(mid["paper_decision"], "blocked")
        self.assertEqual(mid["gate_status"], "paper_blocked")
        self.assertEqual(mid["blocked_reason_primary"], "bucket_not_strict_match")
        self.assertIn("bucket_not_strict_match", mid["gate_reasons"])

    def test_signal_decisions_keep_paper_but_block_live_on_settlement_mismatch(self):
        db_path = test_db_path("signal_decision_settlement_mismatch")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "LIVE_TRADING": "true"}, clear=False):
            init_v3_db(db_path)
            sync_station_registry(path=db_path)
            apply_market_probe_result({
                "city_key": "chicago",
                "active_market": True,
                "settlement_rule_text": "fixture rule says this market settles from station KMDW",
                "settlement_station_id": "KMDW",
                "settlement_station_name": "Chicago Midway International Airport",
                "settlement_timezone": "America/Chicago",
                "settlement_unit": "F",
                "settlement_time_basis": "local_day",
                "primary_settlement_source": "wunderground",
            }, path=db_path)
            self._seed_signal_decision_fixture(db_path, bias_sample_count=10)
            build_signal_decisions("chicago", "2026-07-02", path=db_path)
            rows = list_signal_decisions(city_key="chicago", target_date="2026-07-02", path=db_path)
            mid = next(row for row in rows if row["bucket_key"] == "chicago-20260702-mid")

        self.assertEqual(mid["paper_decision"], "buy")
        self.assertEqual(mid["gate_status"], "paper_allowed")
        self.assertEqual(mid["live_decision"], "blocked")
        self.assertIn("settlement_mismatch", mid["gate_reasons"])

    def test_signal_decisions_api_and_readiness_expose_layer6_without_refreshing(self):
        db_path = test_db_path("signal_decision_api_readiness")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "LIVE_TRADING": "false"}, clear=False):
            init_v3_db(db_path)
            self._seed_signal_decision_fixture(db_path)
            build_signal_decisions("chicago", "2026-07-02", path=db_path)
            summary = signal_decisions_summary("chicago", "2026-07-02", path=db_path)
            api_payload = asyncio.run(signal_decisions_api(city="chicago", target_date="2026-07-02"))
            detail_payload = asyncio.run(signal_decision_detail_api(api_payload["decisions"][0]["decision_id"]))
            readiness = build_data_readiness(db_path)

        self.assertTrue(summary["ok"])
        self.assertEqual(summary["count"], 3)
        self.assertEqual(api_payload["count"], 3)
        self.assertTrue(detail_payload["ok"])
        stage = next(stage for stage in readiness["stages"] if stage["key"] == "signal_decisions")
        self.assertEqual(stage["metrics"]["decisions"], 3)
        self.assertEqual(stage["metrics"]["live_blocked_insufficient_bias_samples"], 3)
        self.assertEqual(readiness["summary"]["signal_decisions"], 3)

    def _store_trusted_signal_decision(self, db_path: Path, decision: dict) -> int:
        issued_at = str(decision.get("issued_at") or datetime.now(timezone.utc).isoformat())
        prediction_id = upsert_daily_max_prediction({
            "city_key": decision.get("city_key") or "chicago",
            "target_date": decision.get("target_date") or issued_at[:10],
            "issued_at": issued_at,
            "mu": 82.0,
            "sigma": 1.5,
            "unit": "F",
            "method": "polywx_aligned_deb_v1",
            "forecast_algo": "polywx_aligned_deb_v1",
            "cohort_contract_version": "forecast-component-cohort-v1",
            "cohort_as_of": issued_at,
            "components": [{
                "source": "fixture_forecast",
                "source_age_ok": True,
                "source_skew_ok": True,
            }],
        }, path=db_path)
        return upsert_signal_decision_record({
            **decision,
            "forecast_algo": "polywx_aligned_deb_v1",
            "deb_version": "polywx_aligned_deb_v1",
            "evidence_links": {
                **(decision.get("evidence_links") or {}),
                "daily_max_prediction_id": prediction_id,
            },
        }, path=db_path)

    def test_dashboard_recommendations_use_fresh_verified_signal_decisions(self):
        db_path = test_db_path("dashboard_recommendations")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        target = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "LIVE_TRADING": "false"}, clear=False):
            init_v3_db(db_path)
            sync_station_registry(path=db_path)
            apply_market_probe_result({
                "city_key": "chicago",
                "active_market": True,
                "settlement_rule_text": "fixture rule settles from KORD",
                "settlement_station_id": "KORD",
                "settlement_station_name": "Chicago O'Hare International Airport",
                "settlement_timezone": "America/Chicago",
                "settlement_unit": "F",
                "settlement_time_basis": "local_day",
                "primary_settlement_source": "wunderground",
            }, path=db_path)
            self._seed_signal_decision_fixture(db_path, target_date=target)
            upsert_metar_report({
                "city": "chicago",
                "city_name": "Chicago",
                "station_id": "KORD",
                "report_time": datetime.now(timezone.utc).isoformat(),
                "raw_text": "KORD fixture METAR",
                "temperature": 33.0,
                "parser_version": "fixture",
            })

            build_signal_decisions("chicago", target, path=db_path)
            payload = _recommendations_payload(scheduler_status={"running": True}, path=db_path)

        self.assertEqual(payload["count"], 1)
        item = payload["items"][0]
        self.assertEqual(item["type"], "trade_candidate")
        self.assertEqual(item["city_key"], "chicago")
        self.assertEqual(item["verification_status"], "verified")
        self.assertLess(item["metar_age_seconds"], 30 * 60)
        self.assertTrue(item["paper_allowed"])
        self.assertIn("polymarket.com", item["polymarket_url"])
        self.assertIn("bucket_label", item)

    def test_dashboard_recommendations_keep_spread_only_watch_candidate(self):
        db_path = test_db_path("dashboard_recommendations_spread")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        target = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "LIVE_TRADING": "false"}, clear=False):
            init_v3_db(db_path)
            sync_station_registry(path=db_path)
            apply_market_probe_result({
                "city_key": "chicago",
                "active_market": True,
                "settlement_rule_text": "fixture rule settles from KORD",
                "settlement_station_id": "KORD",
                "settlement_station_name": "Chicago O'Hare International Airport",
                "settlement_timezone": "America/Chicago",
                "settlement_unit": "F",
                "settlement_time_basis": "local_day",
                "primary_settlement_source": "wunderground",
            }, path=db_path)
            self._seed_signal_decision_fixture(db_path, best_ask=0.20, best_bid=0.0, target_date=target)
            upsert_metar_report({
                "city": "chicago",
                "city_name": "Chicago",
                "station_id": "KORD",
                "report_time": datetime.now(timezone.utc).isoformat(),
                "raw_text": "KORD fixture METAR",
                "temperature": 33.0,
                "parser_version": "fixture",
            })
            build_signal_decisions("chicago", target, path=db_path)
            payload = _recommendations_payload(scheduler_status={"running": True}, path=db_path)

        self.assertEqual(payload["count"], 1)
        item = payload["items"][0]
        self.assertFalse(item["paper_allowed"])
        self.assertIn("spread_too_wide", item["blocked_reasons"])

    def test_dashboard_recommendations_use_forecast_freshness_for_lead_dates(self):
        db_path = test_db_path("dashboard_recommendations_forecast_lead")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        target = (date.today() + timedelta(days=1)).isoformat()
        now = datetime.now(timezone.utc)
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "LIVE_TRADING": "false"}, clear=False):
            init_v3_db(db_path)
            sync_station_registry(path=db_path)
            apply_market_probe_result({
                "city_key": "chicago",
                "active_market": True,
                "settlement_rule_text": "fixture rule settles from KORD",
                "settlement_station_id": "KORD",
                "settlement_station_name": "Chicago O'Hare International Airport",
                "settlement_timezone": "America/Chicago",
                "settlement_unit": "F",
                "settlement_time_basis": "local_day",
                "primary_settlement_source": "wunderground",
            }, path=db_path)
            self._seed_signal_decision_fixture(db_path, target_date=target)
            upsert_metar_report({
                "city": "chicago",
                "city_name": "Chicago",
                "station_id": "KORD",
                "report_time": (now - timedelta(hours=2)).isoformat(),
                "raw_text": "KORD stale fixture METAR",
                "temperature": 33.0,
                "parser_version": "fixture",
            })
            insert_forecast_run({
                "run_key": f"fixture:forecast-lead:{target}",
                "city": "chicago",
                "target_date": target,
                "source": "fixture_forecast",
                "provider": "fixture",
                "model": "fixture",
                "run_type": "deterministic",
                "run_at": now.isoformat(),
                "retrieved_at": now.isoformat(),
                "valid_at": f"{target}T12:00:00+00:00",
                "horizon": "D+1",
                "mean_high": 90.0,
                "std_high": 1.0,
                "member_count": 1,
                "parser_version": "fixture",
                "parse_status": "ok",
                "training_eligible": True,
            }, [{"member_id": "deterministic", "member_name": "deterministic", "high_temp": 90.0}])
            build_signal_decisions("chicago", target, path=db_path)
            payload = _recommendations_payload(scheduler_status={"running": True}, path=db_path)

        self.assertEqual(payload["count"], 1)
        item = payload["items"][0]
        self.assertEqual(item["recommendation_class"], "forecast_lead")
        self.assertGreater(item["metar_age_seconds"], 30 * 60)
        self.assertLess(item["forecast_age_seconds"], 90 * 60)
        self.assertTrue(item["paper_allowed"])

    def test_dashboard_recommendations_render_no_active_market_as_observation_only(self):
        db_path = test_db_path("dashboard_recommendations_no_market")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        target = date.today().isoformat()
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "LIVE_TRADING": "false"}, clear=False):
            init_v3_db(db_path)
            sync_station_registry(path=db_path)
            apply_market_probe_result({
                "city_key": "shanghai",
                "active_market": False,
                "settlement_unit": "C",
                "settlement_timezone": "Asia/Shanghai",
            }, path=db_path)
            upsert_daily_max_prediction({
                "city_key": "shanghai",
                "target_date": target,
                "issued_at": datetime.now(timezone.utc).isoformat(),
                "mu": 35.2,
                "sigma": 1.1,
                "unit": "C",
                "method": "weatherbot-deb-v2",
                "deb_version": "weatherbot-deb-v2",
                "sigma_floor": 0.5,
                "bias_sample_count": 8,
            }, path=db_path)
            upsert_mesonet_observation({
                "city": "shanghai",
                "city_name": "Shanghai",
                "station_id": "101020100",
                "station_name": "Shanghai",
                "network": "china_live",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "temperature": 34.6,
                "humidity": 60,
                "raw_response": "{}",
                "parser_version": "fixture",
            })
            payload = _recommendations_payload(scheduler_status={"running": True}, path=db_path)

        self.assertEqual(payload["observation_only_count"], 1)
        item = payload["items"][0]
        self.assertEqual(item["type"], "observation_only")
        self.assertEqual(item["badge"], "仅观测分析（无市场）")
        self.assertEqual(item["verification_status"], "no_active_market")
        self.assertNotIn("polymarket_url", item)
        self.assertEqual(item["china_live_temp"], 34.6)

    def test_dashboard_weather_focus_is_separate_from_trade_candidates(self):
        db_path = test_db_path("dashboard_weather_focus")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        target = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "LIVE_TRADING": "false"}, clear=False):
            init_v3_db(db_path)
            sync_station_registry(path=db_path)
            upsert_daily_max_prediction({
                "city_key": "chicago",
                "target_date": target,
                "issued_at": datetime.now(timezone.utc).isoformat(),
                "mu": 30.0,
                "sigma": 1.0,
                "unit": "C",
                "method": "weatherbot-deb-v2",
                "deb_version": "weatherbot-deb-v2",
                "sigma_floor": 0.5,
                "model_mu": 29.0,
                "effective_mu": 30.0,
            }, path=db_path)
            upsert_metar_report({
                "city": "chicago",
                "city_name": "Chicago",
                "station_id": "KORD",
                "report_time": datetime.now(timezone.utc).isoformat(),
                "raw_text": "KORD fixture METAR",
                "temperature": 29.5,
                "parser_version": "fixture",
            })
            payload = _recommendations_payload(scheduler_status={"running": True}, path=db_path)

        self.assertEqual(payload["weather_focus_count"], 1)
        self.assertEqual(payload["trade_candidate_count"], 0)
        focus = payload["focus_items"][0]
        self.assertEqual(focus["type"], "weather_focus")
        self.assertEqual(focus["city_key"], "chicago")
        self.assertAlmostEqual(focus["deb_mu"], 84.2, places=1)
        self.assertAlmostEqual(focus["deb_effective_mu"], 86.0, places=1)
        self.assertEqual(focus["focus_reason"], "near_predicted_daily_max")
        self.assertNotIn("market_id", focus)
        self.assertFalse(payload["filters"]["weather_focus"]["trade_claim"])

    def test_layer8_paper_execution_fills_decision_and_marks_spread_pnl(self):
        db_path = test_db_path("paper_execution_fill")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "LIVE_TRADING": "false", "MAX_BET": "2.0"}, clear=False):
            init_v3_db(db_path)
            self._seed_signal_decision_fixture(db_path)
            build_signal_decisions("chicago", "2026-07-02", path=db_path)
            decision = next(
                row for row in list_signal_decisions(city_key="chicago", target_date="2026-07-02", path=db_path)
                if row["bucket_key"] == "chicago-20260702-mid"
            )
            result = execute_paper_decision(
                decision["decision_id"], amount=2.0, path=db_path, cohort_run_id="test-cohort"
            )
            orders = list_paper_orders(decision_id=decision["decision_id"], path=db_path)
            summary = paper_execution_summary("chicago", "2026-07-02", path=db_path)
            readiness = build_data_readiness(db_path)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "paper_filled")
        self.assertEqual(len(orders), 1)
        order = orders[0]
        self.assertEqual(order["lifecycle_status"], "open")
        self.assertEqual(order["fill_status"], "filled")
        self.assertAlmostEqual(order["average_fill_price"], decision["market_ask"])
        self.assertLess(order["unrealized_pnl"], 0)
        self.assertAlmostEqual(order["unrealized_pnl"], (decision["market_bid"] - decision["market_ask"]) * order["filled_shares"], places=5)
        self.assertEqual(summary["count"], 1)
        self.assertEqual(summary["open_orders"], 1)
        stage = next(stage for stage in readiness["stages"] if stage["key"] == "paper_execution")
        self.assertEqual(stage["metrics"]["orders"], 1)
        self.assertEqual(stage["metrics"]["fills"], 1)

    def test_layer8_paper_execution_is_idempotent_by_decision(self):
        db_path = test_db_path("paper_execution_idempotent")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "LIVE_TRADING": "false", "MAX_BET": "2.0"}, clear=False):
            init_v3_db(db_path)
            self._seed_signal_decision_fixture(db_path)
            build_signal_decisions("chicago", "2026-07-02", path=db_path)
            decision = next(
                row for row in list_signal_decisions(city_key="chicago", target_date="2026-07-02", path=db_path)
                if row["bucket_key"] == "chicago-20260702-mid"
            )
            first = execute_paper_decision(
                decision["decision_id"], amount=2.0, path=db_path, cohort_run_id="test-cohort"
            )
            second = execute_paper_decision(
                decision["decision_id"], amount=2.0, path=db_path, cohort_run_id="test-cohort"
            )
            orders = list_paper_orders(decision_id=decision["decision_id"], path=db_path)

        self.assertTrue(first["ok"])
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(len(orders), 1)

    def test_layer8_paper_execution_rejects_missing_or_future_quote_time(self):
        db_path = test_db_path("paper_execution_quote_time")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        now = datetime.now(timezone.utc)
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "LIVE_TRADING": "false", "MAX_BET": "2.0"}, clear=False):
            init_v3_db(db_path)
            for suffix, quote_timestamp in (
                ("missing", None),
                ("future", (now + timedelta(minutes=5)).isoformat()),
            ):
                snapshot = {
                    "best_ask": 0.2,
                    "best_bid": 0.19,
                    "spread": 0.01,
                    "ask_depth": 100,
                }
                if quote_timestamp:
                    snapshot["quote_timestamp"] = quote_timestamp
                self._store_trusted_signal_decision(db_path, {
                    "decision_id": f"quote-{suffix}",
                    "bucket_key": f"bucket-{suffix}",
                    "city_key": "chicago",
                    "target_date": now.date().isoformat(),
                    "issued_at": now.isoformat(),
                    "market_id": f"market-{suffix}",
                    "yes_token_id": f"yes-{suffix}",
                    "token_id": f"yes-{suffix}",
                    "model_probability": 0.3,
                    "market_ask": 0.2,
                    "market_bid": 0.19,
                    "market_implied_probability": 0.2,
                    "edge": 0.1,
                    "strategy_name": "single_bucket_ev",
                    "kelly_fraction": 0.125,
                    "position_size_usd": 2.0,
                    "tick_size": 0.01,
                    "order_min_size": 5.0,
                    "paper_allowed": True,
                    "paper_decision": "buy",
                    "live_allowed": False,
                    "live_decision": "blocked",
                    "gate_status": "paper_allowed",
                    "gate_reasons": ["live_trading_disabled"],
                    "orderbook_snapshot": snapshot,
                })

            missing = execute_paper_decision(
                "quote-missing", amount=2.0, path=db_path, cohort_run_id="test-cohort"
            )
            future = execute_paper_decision(
                "quote-future", amount=2.0, path=db_path, cohort_run_id="test-cohort"
            )

        self.assertFalse(missing["ok"])
        self.assertFalse(future["ok"])
        self.assertIn("orderbook_timestamp_missing_or_invalid", missing["reason"])
        self.assertIn("orderbook_timestamp_missing_or_invalid", future["reason"])

    def test_layer8_paper_execution_runs_ladder_group_atomically(self):
        db_path = test_db_path("paper_execution_ladder")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "LIVE_TRADING": "false", "MAX_BET": "10.0"}, clear=False):
            init_v3_db(db_path)
            for index, bucket in enumerate(("low", "mid", "high")):
                self._store_trusted_signal_decision(db_path, {
                    "decision_id": f"ladder-{bucket}",
                    "bucket_key": bucket,
                    "city_key": "chicago",
                    "target_date": "2026-07-02",
                    "issued_at": "2026-07-02T12:00:00+00:00",
                    "market_id": f"market-{bucket}",
                    "yes_token_id": f"yes-{bucket}",
                    "token_id": f"yes-{bucket}",
                    "bucket_lower": 88 + index,
                    "bucket_upper": 89 + index,
                    "model_probability": 0.25,
                    "market_ask": 0.2,
                    "market_bid": 0.195,
                    "market_implied_probability": 0.2,
                    "edge": 0.05,
                    "strategy_name": "ladder_grid",
                    "kelly_fraction": 0.0625,
                    "position_size_usd": 5.0,
                    "ladder_group_id": "ladder-group-1",
                    "tick_size": 0.01,
                    "order_min_size": 5.0,
                    "book_age_seconds": 0,
                    "spread_bps": 250,
                    "paper_allowed": True,
                    "paper_decision": "buy",
                    "live_allowed": False,
                    "live_decision": "blocked",
                    "gate_status": "paper_allowed",
                    "gate_reasons": ["live_trading_disabled"],
                    "orderbook_snapshot": {
                        "best_ask": 0.2,
                        "best_bid": 0.195,
                        "spread": 0.005,
                        "ask_depth": 100,
                        "quote_timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                })

            result = execute_paper_decision("ladder-mid", path=db_path, cohort_run_id="test-cohort")
            orders = list_paper_orders(city_key="chicago", target_date="2026-07-02", path=db_path)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "paper_ladder_filled")
        self.assertEqual(len(orders), 3)
        self.assertEqual({order["fill_status"] for order in orders}, {"filled"})
        self.assertEqual({order["strategy_name"] for order in orders}, {"ladder_grid"})
        self.assertEqual({order["ladder_group_id"] for order in orders}, {"ladder-group-1"})

    def test_layer8_paper_execution_rejects_ladder_group_without_partial_orders(self):
        db_path = test_db_path("paper_execution_ladder_reject")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "LIVE_TRADING": "false", "MAX_BET": "10.0"}, clear=False):
            init_v3_db(db_path)
            for index, bucket in enumerate(("low", "mid", "high")):
                self._store_trusted_signal_decision(db_path, {
                    "decision_id": f"bad-ladder-{bucket}",
                    "bucket_key": bucket,
                    "city_key": "chicago",
                    "target_date": "2026-07-02",
                    "issued_at": "2026-07-02T12:00:00+00:00",
                    "market_id": f"market-{bucket}",
                    "yes_token_id": f"bad-yes-{bucket}",
                    "token_id": f"bad-yes-{bucket}",
                    "bucket_lower": 88 + index,
                    "bucket_upper": 89 + index,
                    "model_probability": 0.25,
                    "market_ask": 0.2,
                    "market_bid": 0.195,
                    "market_implied_probability": 0.2,
                    "edge": 0.05,
                    "strategy_name": "ladder_grid",
                    "kelly_fraction": 0.0625,
                    "position_size_usd": 5.0,
                    "ladder_group_id": "bad-ladder-group-1",
                    "tick_size": 0.01,
                    "order_min_size": 5.0,
                    "book_age_seconds": 0,
                    "spread_bps": 250,
                    "paper_allowed": True,
                    "paper_decision": "buy",
                    "live_allowed": False,
                    "live_decision": "blocked",
                    "gate_status": "paper_allowed",
                    "gate_reasons": ["live_trading_disabled"],
                    "orderbook_snapshot": {
                        "best_ask": 0.2,
                        "best_bid": 0.195,
                        "spread": 0.005,
                        "ask_depth": 0 if bucket == "high" else 100,
                    },
                })

            result = execute_paper_decision("bad-ladder-mid", path=db_path, cohort_run_id="test-cohort")
            orders = list_paper_orders(city_key="chicago", target_date="2026-07-02", path=db_path)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "ladder_group_atomic_precheck_failed")
        self.assertEqual(len(orders), 0)

    def test_layer8_paper_execution_rejects_ladder_when_one_leg_cannot_fully_fill(self):
        db_path = test_db_path("paper_execution_ladder_partial_depth")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "LIVE_TRADING": "false", "MAX_BET": "10.0"}, clear=False):
            init_v3_db(db_path)
            for index, bucket in enumerate(("low", "mid", "high")):
                self._store_trusted_signal_decision(db_path, {
                    "decision_id": f"thin-ladder-{bucket}",
                    "bucket_key": bucket,
                    "city_key": "chicago",
                    "target_date": "2026-07-02",
                    "issued_at": "2026-07-02T12:00:00+00:00",
                    "market_id": f"thin-market-{bucket}",
                    "yes_token_id": f"thin-yes-{bucket}",
                    "token_id": f"thin-yes-{bucket}",
                    "bucket_lower": 88 + index,
                    "bucket_upper": 89 + index,
                    "model_probability": 0.25,
                    "market_ask": 0.2,
                    "market_bid": 0.195,
                    "market_implied_probability": 0.2,
                    "edge": 0.05,
                    "strategy_name": "ladder_grid",
                    "kelly_fraction": 0.0625,
                    "position_size_usd": 5.0,
                    "ladder_group_id": "thin-ladder-group-1",
                    "tick_size": 0.01,
                    "order_min_size": 5.0,
                    "book_age_seconds": 0,
                    "spread_bps": 250,
                    "paper_allowed": True,
                    "paper_decision": "buy",
                    "live_allowed": False,
                    "live_decision": "blocked",
                    "gate_status": "paper_allowed",
                    "gate_reasons": ["live_trading_disabled"],
                    "orderbook_snapshot": {
                        "best_ask": 0.2,
                        "best_bid": 0.195,
                        "spread": 0.005,
                        "ask_depth": 6 if bucket == "high" else 100,
                    },
                })

            result = execute_paper_decision("thin-ladder-mid", path=db_path, cohort_run_id="test-cohort")
            orders = list_paper_orders(city_key="chicago", target_date="2026-07-02", path=db_path)
            with connect(db_path) as conn:
                fills = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "ladder_group_atomic_precheck_failed")
        self.assertIn(
            "insufficient_depth_for_atomic_ladder",
            result["risk_reasons_by_decision"]["thin-ladder-high"],
        )
        self.assertEqual(orders, [])
        self.assertEqual(fills, 0)

    def test_layer8_paper_execution_rejects_blocked_decision_without_fill(self):
        db_path = test_db_path("paper_execution_reject")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "LIVE_TRADING": "false", "MAX_BET": "2.0"}, clear=False):
            init_v3_db(db_path)
            self._seed_signal_decision_fixture(db_path, strict_match_status="blocked")
            build_signal_decisions("chicago", "2026-07-02", path=db_path)
            decision = next(
                row for row in list_signal_decisions(city_key="chicago", target_date="2026-07-02", path=db_path)
                if row["bucket_key"] == "chicago-20260702-mid"
            )
            result = execute_paper_decision(
                decision["decision_id"], amount=2.0, path=db_path, cohort_run_id="test-cohort"
            )
            orders = list_paper_orders(decision_id=decision["decision_id"], path=db_path)
            with connect(db_path) as conn:
                fills = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "rejected")
        self.assertIn("paper_gate_not_passed", result["reason"])
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["lifecycle_status"], "rejected")
        self.assertEqual(fills, 0)

    def test_layer8_paper_execution_api_requires_active_cohort_for_writes(self):
        db_path = test_db_path("paper_execution_api_cli")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "LIVE_TRADING": "false", "MAX_BET": "2.0"}, clear=False):
            init_v3_db(db_path)
            self._seed_signal_decision_fixture(db_path)
            build_signal_decisions("chicago", "2026-07-02", path=db_path)
            decision = next(
                row for row in list_signal_decisions(city_key="chicago", target_date="2026-07-02", path=db_path)
                if row["bucket_key"] == "chicago-20260702-mid"
            )
            dry_run_cli = run_paper_execute(decision_id=decision["decision_id"], amount=2.0, apply=False)
            self.assertTrue(dry_run_cli["dry_run"])
            self.assertEqual(len(list_paper_orders(path=db_path)), 0)
            api_dry_run = asyncio.run(paper_orders_execute_api(PaperExecutionRequest(
                decision_id=decision["decision_id"],
                amount=2.0,
                dry_run=True,
            )))
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(paper_orders_execute_api(PaperExecutionRequest(
                    decision_id=decision["decision_id"],
                    amount=2.0,
                    dry_run=False,
                )))
            api_summary = asyncio.run(paper_orders_api(city="chicago", target_date="2026-07-02"))

        self.assertTrue(api_dry_run["ok"])
        self.assertTrue(api_dry_run["dry_run"])
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(api_summary["count"], 0)

    def test_signal_decisions_cli_runner_calls_layer6_builder(self):
        with patch("weatherbot_v3.cli.build_data_readiness", return_value={"stages": [{"key": "signal_decisions", "status": "ready"}]}), \
             patch("weatherbot_v3.cli.persist_data_readiness"), \
             patch("weatherbot_v3.cli._signal_decision_targets_from_db", return_value=[("chicago", "2026-07-02")]), \
             patch("weatherbot_v3.signals.build_signal_decisions_for_targets", return_value={
                 "ok": True,
                 "requested": 1,
                 "decision_count": 3,
                 "stored": 3,
                 "results": [],
             }) as mocked:
            payload = run_signal_decisions_build("chicago", days_arg=1)

        mocked.assert_called_once()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["signal_decisions_stage"]["status"], "ready")

    def test_signal_decisions_dry_run_does_not_persist_readiness(self):
        with patch("weatherbot_v3.cli.build_data_readiness") as readiness, \
             patch("weatherbot_v3.cli.persist_data_readiness") as persist, \
             patch("weatherbot_v3.cli._signal_decision_targets_from_db", return_value=[("chicago", "2026-07-13")]), \
             patch("weatherbot_v3.signals.build_signal_decisions_for_targets", return_value={
                 "ok": True,
                 "requested": 1,
                 "decision_count": 3,
                 "stored": 0,
                 "results": [],
             }):
            payload = run_signal_decisions_build("chicago", days_arg=1, dry_run=True)

        self.assertTrue(payload["ok"])
        self.assertNotIn("signal_decisions_stage", payload)
        readiness.assert_not_called()
        persist.assert_not_called()

    def test_signal_decisions_default_to_all_enabled_stations(self):
        db_path = test_db_path("signal_decision_enabled_cities")
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db(db_path)
            with connect(db_path) as conn:
                conn.executemany(
                    """
                    INSERT INTO stations (
                        city_key, city_name, station_id, station_name, timezone, unit,
                        enabled, tier, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ("chicago", "Chicago", "KORD", "O'Hare", "America/Chicago", "F", 1, 1, "2026-07-13T00:00:00+00:00"),
                        ("shanghai", "Shanghai", "ZSPD", "Pudong", "Asia/Shanghai", "C", 1, 1, "2026-07-13T00:00:00+00:00"),
                        ("disabled", "Disabled", "TEST", "Disabled", "UTC", "C", 0, 1, "2026-07-13T00:00:00+00:00"),
                    ],
                )
                conn.commit()
            with patch("weatherbot_v3.cli._signal_decision_targets_from_db", return_value=[]) as targets, \
                 patch("weatherbot_v3.signals.build_signal_decisions_for_targets", return_value={
                     "ok": True,
                     "requested": 0,
                     "decision_count": 0,
                     "stored": 0,
                     "results": [],
                 }):
                run_signal_decisions_build("", days_arg=1, dry_run=True)

        targets.assert_called_once_with(["chicago", "shanghai"], 1)

    def test_signal_decisions_targets_prefer_daily_max_market_bucket_overlap(self):
        from weatherbot_v3.cli import _signal_decision_targets_from_db

        db_path = test_db_path("signal_decision_target_overlap")
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db(db_path)
            upsert_daily_max_prediction({
                "city_key": "chicago",
                "target_date": "2026-07-02",
                "issued_at": "2026-07-02T05:00:00+00:00",
                "mu": 92.0,
                "sigma": 2.0,
                "unit": "F",
                "method": "weatherbot-deb-v2",
                "deb_version": "weatherbot-deb-v2",
            })
            upsert_daily_max_prediction({
                "city_key": "chicago",
                "target_date": "2026-07-08",
                "issued_at": "2026-07-02T05:00:00+00:00",
                "mu": 87.0,
                "sigma": 3.0,
                "unit": "F",
                "method": "weatherbot-deb-v2",
                "deb_version": "weatherbot-deb-v2",
            })
            upsert_market_bucket({
                "market_id": "market-overlap",
                "city": "chicago",
                "city_name": "Chicago",
                "target_date": "2026-07-02",
                "unit": "F",
                "bucket_label": "92F",
                "bucket_direction": "exact",
                "bucket_low": 92,
                "bucket_high": 92,
                "yes_token_id": "yes-overlap",
                "order_min_size": 5,
                "tick_size": 0.001,
                "enable_order_book": True,
                "price": 0.2,
                "best_ask": 0.2,
                "strict_match_status": "matched",
                "strict_match_reasons": [],
            })
            targets = _signal_decision_targets_from_db(["chicago"], 2)

        self.assertEqual(targets, [("chicago", "2026-07-02")])

    def test_signal_decision_targets_use_latest_dates_first(self):
        from weatherbot_v3.cli import _signal_decision_targets_from_db

        db_path = test_db_path("signal_decision_latest_targets")
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db(db_path)
            for target_date in ("2026-07-02", "2026-07-08", "2026-07-13"):
                upsert_daily_max_prediction({
                    "city_key": "chicago",
                    "target_date": target_date,
                    "issued_at": f"{target_date}T05:00:00+00:00",
                    "mu": 90.0,
                    "sigma": 2.0,
                    "unit": "F",
                    "method": "weatherbot-deb-v2",
                    "deb_version": "weatherbot-deb-v2",
                })
                upsert_market_bucket({
                    "market_id": f"market-{target_date}",
                    "city": "chicago",
                    "city_name": "Chicago",
                    "target_date": target_date,
                    "unit": "F",
                    "bucket_label": "90F",
                    "bucket_direction": "exact",
                    "bucket_low": 90,
                    "bucket_high": 90,
                    "yes_token_id": f"yes-{target_date}",
                    "order_min_size": 5,
                    "tick_size": 0.001,
                    "enable_order_book": True,
                    "price": 0.2,
                    "best_ask": 0.2,
                    "strict_match_status": "matched",
                    "strict_match_reasons": [],
                })
            targets = _signal_decision_targets_from_db(["chicago"], 2)

        self.assertEqual(targets, [("chicago", "2026-07-13"), ("chicago", "2026-07-08")])

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

    def test_awc_metar_fetch_retries_transient_request_error(self):
        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return [{"stationId": "WSSS"}]

        class FakeSession:
            def __init__(self):
                self.calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise requests.Timeout("temporary timeout")
                return FakeResponse()

        session = FakeSession()
        rows = fetch_awc_metars(["WSSS"], session=session, retries=2, retry_backoff_seconds=0)

        self.assertEqual(session.calls, 2)
        self.assertEqual(rows, [{"stationId": "WSSS"}])

    def test_awc_metar_fetch_retries_transient_request_error(self):
        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return [{"stationId": "WSSS"}]

        class FakeSession:
            def __init__(self):
                self.calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise requests.Timeout("temporary timeout")
                return FakeResponse()

        session = FakeSession()
        rows = fetch_awc_metars(["WSSS"], session=session, retries=2, retry_backoff_seconds=0)

        self.assertEqual(session.calls, 2)
        self.assertEqual(rows, [{"stationId": "WSSS"}])

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

    def test_iem_asos_csv_parser_converts_fahrenheit_to_celsius_and_preserves_raw(self):
        csv_text = (
            "station,valid,tmpf,dwpf,drct,sknt,gust,vsby,alti,mslp,p01i,skyc1,skyl1,metar\n"
            "ORD,2026-06-01 00:51,77.0,50.0,180,12,18,10.0,29.92,1013.2,0.00,FEW,4200,"
            "\"METAR KORD 010051Z 18012G18KT 10SM FEW042 25/10 A2992\"\n"
        )
        station_row = station_row_from_profile(SETTLEMENT_REGISTRY["chicago"])

        parsed = parse_iem_asos_csv(csv_text, station_row, source_url="https://mesonet.example/asos.csv")
        report = parsed["reports"][0]

        self.assertEqual(parsed["reports_seen"], 1)
        self.assertEqual(report["station_id"], "KORD")
        self.assertEqual(report["parser_version"], "iem-asos-csv-v1")
        self.assertAlmostEqual(report["temperature"], 25.0, places=1)
        self.assertAlmostEqual(report["dew_point"], 10.0, places=1)
        self.assertEqual(report["wind_direction"], 180)
        self.assertEqual(report["wind_speed"], 12)
        self.assertEqual(report["raw_text"], "METAR KORD 010051Z 18012G18KT 10SM FEW042 25/10 A2992")
        self.assertEqual(report["raw_json"]["raw_unit"], "F")
        self.assertEqual(report["raw_json"]["normalized_temperature_unit"], "C")

    def test_iem_asos_upsert_is_station_time_idempotent_and_overwrites_corrections(self):
        db_path = test_db_path("iem_upsert_idempotent")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        station_row = station_row_from_profile(SETTLEMENT_REGISTRY["chicago"])
        first_csv = (
            "station,valid,tmpf,dwpf,drct,sknt,vsby,metar\n"
            "ORD,2026-06-01 00:51,77.0,50.0,180,12,10.0,"
            "\"METAR KORD 010051Z 18012KT 10SM 25/10 A2992\"\n"
        )
        correction_csv = (
            "station,valid,tmpf,dwpf,drct,sknt,vsby,metar\n"
            "ORD,2026-06-01 00:51,78.8,51.8,190,13,10.0,"
            "\"METAR KORD 010051Z COR 19013KT 10SM 26/11 A2991\"\n"
        )

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            first = ingest_iem_asos_csv(first_csv, station_row, source_url="https://mesonet.example/first.csv")
            second = ingest_iem_asos_csv(correction_csv, station_row, source_url="https://mesonet.example/correction.csv")
            with connect(db_path) as conn:
                rows = conn.execute("SELECT * FROM metar_reports").fetchall()

        self.assertEqual(first["reports_upserted"], 1)
        self.assertEqual(second["reports_upserted"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["report_key"]), 32)
        self.assertNotIn(":", rows[0]["report_key"])
        self.assertIn("COR", rows[0]["raw_text"])
        self.assertAlmostEqual(rows[0]["temperature"], 26.0, places=1)

    def test_iem_asos_parser_marks_partial_failed_and_skips_empty_rows(self):
        db_path = test_db_path("iem_partial_failed")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        station_row = station_row_from_profile(SETTLEMENT_REGISTRY["chicago"])
        csv_text = (
            "station,valid,tmpf,dwpf,drct,sknt,vsby,metar\n"
            "ORD,2026-06-01 00:51,77.0,50.0,180,12,10.0,\n"
            "ORD,2026-06-01 01:51,null,null,null,null,null,\n"
            "ORD,2026-06-01 02:51,78.0,51.0,190,10,10.0,\"BAD RAW REPORT\"\n"
        )

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            result = ingest_iem_asos_csv(csv_text, station_row, source_url="https://mesonet.example/bad.csv")
            with connect(db_path) as conn:
                rows = conn.execute("SELECT parse_status, parse_warnings FROM metar_reports ORDER BY report_time").fetchall()

        self.assertEqual(result["reports_seen"], 2)
        self.assertEqual(result["skipped_empty_rows"], 1)
        self.assertEqual(result["partial_rows"], 1)
        self.assertEqual(result["failed_rows"], 1)
        self.assertEqual([row["parse_status"] for row in rows], ["partial", "failed"])
        self.assertIn("no_raw_metar_from_iem", rows[0]["parse_warnings"])
        self.assertIn("unrecognized_raw_metar", rows[1]["parse_warnings"])

    def test_iem_station_probe_uses_real_probe_result_without_presuming_fallback(self):
        db_path = test_db_path("iem_probe")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        probe_path = TEST_DB_DIR / "probe_report.json"
        probe_path.unlink(missing_ok=True)

        class FakeResponse:
            def __init__(self, text, url):
                self.status_code = 200
                self.text = text
                self.url = url

            def raise_for_status(self):
                return None

        class FakeSession:
            def __init__(self):
                self.calls = []

            def get(self, url, params, headers, timeout):
                station_values = [value for key, value in params if key == "station"]
                station = station_values[0]
                self.calls.append((url, params, headers, timeout))
                if station == "KORD":
                    return FakeResponse("", f"{url}?station=KORD")
                return FakeResponse(
                    "77.00,50.00,180.00,12.00,10.00,FEW,4200.00,KORD 010051Z 18012KT 10SM 25/10 A2992\n",
                    f"{url}?station={station}",
                )

        session = FakeSession()
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            sync_station_registry(db_path)
            result = probe_iem_stations(["chicago"], output_path=probe_path, session=session)

        self.assertTrue(result["ok"])
        self.assertEqual(result["selected"]["chicago"], "ORD")
        self.assertTrue(probe_path.exists())
        first_params = dict(session.calls[0][1])
        self.assertEqual(first_params["nometa"], "yes")

    def test_iem_backfill_writes_fetch_log_and_hour_coverage_without_auto_start(self):
        db_path = test_db_path("iem_backfill")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        raw_dir = TEST_DB_DIR / "iem_raw"
        probe_path = TEST_DB_DIR / "iem_probe_for_backfill.json"
        probe_path.write_text(json.dumps({"selected": {"chicago": "ORD"}}, ensure_ascii=False), encoding="utf-8")

        class FakeResponse:
            status_code = 200
            url = "https://mesonet.example/asos.csv?station=ORD"
            text = (
                "station,valid,tmpf,dwpf,drct,sknt,gust,vsby,alti,mslp,p01i,skyc1,skyl1,metar\n"
                "ORD,2026-07-02 00:51,77.0,50.0,180,12,18,10.0,29.92,1013.2,0.00,FEW,4200,"
                "\"METAR KORD 020051Z 18012G18KT 10SM FEW042 25/10 A2992\"\n"
            )

            def raise_for_status(self):
                return None

        class FakeSession:
            def get(self, url, params, headers, timeout):
                return FakeResponse()

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            sync_station_registry(db_path)
            result = backfill_iem_asos_metars(
                ["chicago"],
                days=1,
                session=FakeSession(),
                raw_dir=raw_dir,
                probe_report_path=probe_path,
            )
            logs = list_data_fetch_logs(5)
            evidence = weather_evidence_summary("chicago")

        self.assertTrue(result["ok"])
        self.assertEqual(result["reports_upserted"], 1)
        self.assertEqual(evidence["metar_reports"], 1)
        self.assertEqual(result["results"][0]["coverage"]["definition"], "distinct_utc_hours_with_any_report / (24 * days)")
        self.assertEqual(logs[0]["source"], "iem_asos")
        self.assertEqual(logs[0]["stage"], "refresh_metar_reports")
        self.assertTrue(Path(result["results"][0]["raw_path"]).exists())

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
        with patch("weatherbot_v3.production_actions.build_hourly_consensus") as mocked_build:
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

    def test_hourly_consensus_cli_runner_calls_layer4_builder(self):
        with patch("weatherbot_v3.hourly.build_hourly_consensus") as mocked_build:
            mocked_build.return_value = {"ok": True, "rows_upserted": 2}
            with patch("weatherbot_v3.cli.build_data_readiness") as mocked_readiness:
                mocked_readiness.return_value = {
                    "stages": [{"key": "hourly_consensus", "status": "ready"}],
                }
                with patch("weatherbot_v3.cli.persist_data_readiness"):
                    payload = run_hourly_consensus_build("chicago", "2026-07-01")

        mocked_build.assert_called_once_with(["chicago"], target_date="2026-07-01")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["hourly_consensus_stage"]["status"], "ready")

    def test_daily_max_cli_runner_calls_deb_builder(self):
        with patch("weatherbot_v3.deb.build_daily_max_predictions") as mocked_build:
            mocked_build.return_value = {"ok": True, "requested": 1, "stored": 1, "failed": 0}
            with patch("weatherbot_v3.cli.build_data_readiness") as mocked_readiness:
                mocked_readiness.return_value = {"stages": []}
                with patch("weatherbot_v3.cli.persist_data_readiness"):
                    payload = run_daily_max_build(
                        "chicago",
                        "2026-07-01",
                        dry_run=False,
                        issued_at="2026-07-01T18:30:00Z",
                    )

        mocked_build.assert_called_once_with(
            city="chicago",
            target_date="2026-07-01",
            limit=50,
            dry_run=False,
            issued_at="2026-07-01T18:30:00Z",
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["stored"], 1)
        self.assertEqual(payload["issued_at"], "2026-07-01T18:30:00Z")

    def test_market_buckets_cli_runner_ingests_local_market_payloads(self):
        db_path = test_db_path("market_buckets_cli")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        market_payload = {
            "id": "market-cli",
            "eventSlug": "highest-temperature-in-chicago-on-july-1-2026",
            "question": "Will the highest temperature in Chicago be 90°F on July 1, 2026?",
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.20", "0.80"]',
            "clobTokenIds": '["yes-cli", "no-cli"]',
            "orderMinSize": "5",
            "orderPriceMinTickSize": "0.01",
            "enableOrderBook": True,
        }
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db(db_path)
            with connect(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO markets (
                        market_id, event_slug, event_url, question, city, city_name,
                        target_date, bucket_label, yes_token_id, no_token_id,
                        order_min_size, tick_size, enable_order_book, raw_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "market-cli",
                        "highest-temperature-in-chicago-on-july-1-2026",
                        "",
                        market_payload["question"],
                        "chicago",
                        "Chicago",
                        "2026-07-01",
                        "90F",
                        "yes-cli",
                        "no-cli",
                        5,
                        0.01,
                        1,
                        json.dumps(market_payload),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            with patch("weatherbot_v3.cli.persist_data_readiness"):
                payload = run_market_buckets_sync(10)
            rows = list_market_buckets(city="Chicago", target_date="2026-07-01")
        self.assertEqual(payload["stored"], 1)
        self.assertEqual(payload["matched"], 1)
        self.assertEqual(rows[0]["market_id"], "market-cli")

    def test_production_action_executes_whitelisted_market_buckets_sync(self):
        with patch("weatherbot_v3.production_actions.run_market_buckets_sync") as mocked_sync:
            mocked_sync.return_value = {"ok": True, "stored": 3}
            with patch("weatherbot_v3.production_actions.build_data_readiness") as mocked_readiness:
                mocked_readiness.return_value = {
                    "status": "blocked",
                    "score": 0.4,
                    "live_allowed": False,
                    "production_phase": {"blocked_keys": ["market_buckets"]},
                }
                with patch("weatherbot_v3.production_actions.persist_data_readiness"):
                    result = run_production_action(
                        "sync_market_buckets",
                        apply=True,
                        limit=3,
                    )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "executed")
        mocked_sync.assert_called_once_with(3)
        self.assertEqual(result["payload"]["stored"], 3)

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
        with patch("dashboard_server.run_paper_validation_tick") as mocked_tick:
            mocked_tick.return_value = {
                "ok": True,
                "status": "executed",
                "executed": 1,
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
        self.assertEqual(result["payload"]["executed"], 1)
        mocked_tick.assert_called_once_with(apply=True)

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

    def test_polywx_forecast_rows_parse_and_persist_layer3_runs(self):
        db_path = test_db_path("polywx_forecast_rows")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        rows = [
            {
                "hour": "09:00",
                "temperature_c": 26.0,
                "cloud_cover_pct": 55,
                "precip_chance_pct": 10,
                "wind_dir_deg": 210,
                "wind_kph": 13,
                "pressure_hpa": 1012,
                "dew_point_c": 18,
                "condition_phrase": "Partly cloudy",
                "fetched_at": "2026-07-01T12:00:00Z",
            },
            {
                "hour": "15:00",
                "temperature_c": 31.0,
                "cloud_cover_pct": 20,
                "precip_chance_pct": 0,
                "wind_dir_deg": 230,
                "wind_kph": 18,
                "pressure_hpa": 1010,
                "dew_point_c": 19,
                "condition_phrase": "Clear",
                "fetched_at": "2026-07-01T12:00:00Z",
            },
        ]
        run, members = forecast_run_from_polywx_rows(
            "chicago",
            "2026-07-01",
            rows,
            source_url="https://api.weather.polywx.xyz/api/forecast?city=chicago-kord&date=2026-07-01",
        )
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            result = ingest_polywx_forecasts(
                {"chicago": {"2026-07-01": rows}},
                source_url="https://api.weather.polywx.xyz/api/forecast?city=chicago-kord&date=2026-07-01",
            )
            summary = forecast_summary("chicago", "2026-07-01")
            points = forecast_hourly_points({"chicago": {"2026-07-01"}}, db_path=db_path)

        self.assertEqual(run["source"], "polywx_forecast")
        self.assertEqual(run["provider"], "polywx")
        self.assertEqual(run["parser_version"], "polywx-hourly-forecast-v1")
        self.assertEqual(run["parse_status"], "parsed")
        self.assertEqual(run["source_unit"], "C")
        self.assertFalse(run["training_eligible"])
        self.assertEqual(run["ineligibility_reason"], "polywx_model_source_and_run_time_not_disclosed")
        self.assertAlmostEqual(run["mean_high"], 87.8, places=1)
        self.assertEqual(members[0]["member_id"], "polywx_deterministic")
        self.assertEqual(result["runs_upserted"], 1)
        self.assertEqual(summary["runs"], 1)
        self.assertEqual(summary["members"], 1)
        self.assertEqual(summary["latest_runs"][0]["parser_version"], "polywx-hourly-forecast-v1")
        self.assertEqual(len(points["chicago"]), 2)
        self.assertAlmostEqual(points["chicago"][1]["best"], 87.8, places=1)

    def test_forecasts_api_returns_layer3_runs_without_refreshing(self):
        db_path = test_db_path("forecasts_api")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            insert_forecast_run(
                {
                    "run_key": "ecmwf:chicago:2026-07-01:test",
                    "city": "chicago",
                    "target_date": "2026-07-01",
                    "source": "ecmwf",
                    "provider": "archive",
                    "model": "ifs",
                    "model_version": "test",
                    "run_type": "forecast",
                    "retrieved_at": "2026-06-30T12:00:00+00:00",
                    "valid_at": "2026-07-01T20:00:00+00:00",
                    "station_id": "KORD",
                    "timezone": "America/Chicago",
                    "unit": "F",
                    "mean_high": 88,
                    "std_high": 1.2,
                    "member_count": 1,
                    "parser_version": "archive-record-v1",
                    "parse_status": "parsed",
                    "training_eligible": True,
                },
                [{"member_id": "control", "high_temp": 88, "hourly": []}],
            )
            payload = asyncio.run(forecasts_api(city="chicago", target_date="2026-07-01"))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["city"], "chicago")
        self.assertEqual(payload["target_date"], "2026-07-01")
        self.assertEqual(payload["runs"], 1)
        self.assertEqual(payload["members"], 1)
        self.assertEqual(payload["latest_runs"][0]["source"], "ecmwf")

    def test_openmeteo_fetch_ingests_models_with_local_day_high_and_conus_short_range(self):
        db_path = test_db_path("openmeteo_models")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))

        class FakeResponse:
            status_code = 200

            def __init__(self, payload, url):
                self._payload = payload
                self.text = json.dumps(payload)
                self.url = url

            def json(self):
                return self._payload

        class FakeSession:
            def __init__(self):
                self.models = []

            def get(self, url, params, headers, timeout):
                model = params["models"]
                self.models.append(model)
                payload = {
                    "latitude": params["latitude"],
                    "longitude": params["longitude"],
                    "generationtime_ms": 0.1,
                    "timezone": "GMT",
                    "hourly": {
                        "time": [
                            "2026-07-01T03:00",
                            "2026-07-01T15:00",
                            "2026-07-01T21:00",
                            "2026-07-02T04:00",
                            "2026-07-02T06:00",
                        ],
                        "temperature_2m": [40.0, 20.0, 30.0, 25.0, 45.0],
                        "dew_point_2m": [10.0, 11.0, 12.0, 13.0, 14.0],
                        "relative_humidity_2m": [50, 51, 52, 53, 54],
                        "cloud_cover": [10, 20, 30, 40, 50],
                        "wind_speed_10m": [5, 6, 7, 8, 9],
                        "wind_gusts_10m": [9, 10, 11, 12, 13],
                        "precipitation": [0, 0, 0, 0, 0],
                        "precipitation_probability": [0, 1, 2, 3, 4],
                    },
                }
                return FakeResponse(payload, f"{url}?models={model}")

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            result = fetch_openmeteo_forecasts(
                ["chicago"],
                session=FakeSession(),
                retrieved_at="2026-07-01T12:15:00+00:00",
                sleep_seconds=0,
            )
            with connect(db_path) as conn:
                runs = conn.execute(
                    """
                    SELECT source, model, run_at, mean_high, unit, source_unit, training_eligible, raw_json
                    FROM forecast_runs
                    WHERE city = 'chicago' AND target_date = '2026-07-01'
                    ORDER BY source
                    """
                ).fetchall()
                members = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM forecast_members fm
                    JOIN forecast_runs fr ON fr.id = fm.run_id
                    WHERE fr.city = 'chicago' AND fr.target_date = '2026-07-01'
                    """
                ).fetchone()[0]

        models = model_allowlist_for_city("chicago")
        self.assertIn("ncep_hrrr_conus", models)
        self.assertIn("ncep_nbm_conus", models)
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["runs_upserted"], len(models))
        sources = {row["source"] for row in runs}
        self.assertIn("openmeteo_ncep_hrrr_conus", sources)
        self.assertIn("openmeteo_ncep_nbm_conus", sources)
        self.assertEqual(len(runs), len(models))
        self.assertEqual(members, len(models))
        for row in runs:
            self.assertEqual(row["run_at"], "")
            self.assertEqual(row["unit"], "F")
            self.assertEqual(row["source_unit"], "C")
            self.assertEqual(row["training_eligible"], 1)
            self.assertAlmostEqual(row["mean_high"], 86.0, places=1)
            meta = json.loads(row["raw_json"])["meta"]
            self.assertTrue(meta["run_at_inferred"])
            self.assertIn("inferred_run_at", meta)

        self.assertIn("jma_seamless", model_allowlist_for_city("shanghai"))

    def test_openmeteo_ensemble_members_are_persistable(self):
        payload = {
            "generationtime_ms": 0.2,
            "hourly": {
                "time": ["2026-07-01T15:00", "2026-07-01T21:00"],
                "temperature_2m": [20.0, 21.0],
                "temperature_2m_member01": [20.0, 22.0],
                "temperature_2m_member02": [21.0, 23.0],
            },
        }

        runs, members = openmeteo_runs_from_response(
            "chicago",
            "gfs_seamless",
            payload,
            source_url="https://ensemble-api.open-meteo.com/v1/ensemble",
            retrieved_at="2026-07-01T12:00:00+00:00",
            endpoint_kind="ensemble",
        )

        target_run = next(run for run in runs if run["target_date"] == "2026-07-01")
        target_members = members[runs.index(target_run)]
        self.assertEqual(target_run["source"], "openmeteo_ensemble_gfs_seamless")
        self.assertEqual(target_run["member_count"], 2)
        self.assertEqual([member["member_id"] for member in target_members], ["member01", "member02"])
        self.assertAlmostEqual(target_run["mean_high"], 72.5, places=1)
        self.assertGreater(target_run["std_high"], 0)

    def test_openmeteo_snapshots_preserve_distinct_retrievals_within_same_hour(self):
        db_path = test_db_path("openmeteo_idempotent")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        payload = {
            "generationtime_ms": 0.1,
            "hourly": {
                "time": ["2026-07-01T15:00", "2026-07-01T21:00"],
                "temperature_2m": [20.0, 30.0],
            },
        }

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            first_runs, first_members = openmeteo_runs_from_response(
                "chicago",
                "ecmwf_ifs025",
                payload,
                retrieved_at="2026-07-01T12:15:00+00:00",
            )
            for run, members in zip(first_runs, first_members):
                if run["target_date"] == "2026-07-01":
                    insert_forecast_run(run, members)
            second_payload = {**payload, "generationtime_ms": 0.9}
            second_runs, second_members = openmeteo_runs_from_response(
                "chicago",
                "ecmwf_ifs025",
                second_payload,
                retrieved_at="2026-07-01T12:45:00+00:00",
            )
            for run, members in zip(second_runs, second_members):
                if run["target_date"] == "2026-07-01":
                    insert_forecast_run(run, members)
            with connect(db_path) as conn:
                same_hour_count = conn.execute("SELECT COUNT(*) FROM forecast_runs").fetchone()[0]
            third_runs, third_members = openmeteo_runs_from_response(
                "chicago",
                "ecmwf_ifs025",
                payload,
                retrieved_at="2026-07-01T13:01:00+00:00",
            )
            for run, members in zip(third_runs, third_members):
                if run["target_date"] == "2026-07-01":
                    insert_forecast_run(run, members)
            with connect(db_path) as conn:
                next_hour_count = conn.execute("SELECT COUNT(*) FROM forecast_runs").fetchone()[0]

        self.assertEqual(same_hour_count, 2)
        self.assertEqual(next_hour_count, 3)

    def test_openmeteo_missing_temperature_records_failed_parse_without_exception(self):
        db_path = test_db_path("openmeteo_failed")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        payload = {"hourly": {"time": ["2026-07-01T15:00"]}}

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            runs, members = openmeteo_runs_from_response(
                "chicago",
                "ecmwf_ifs025",
                payload,
                retrieved_at="2026-07-01T12:00:00+00:00",
            )
            insert_forecast_run(runs[0], members[0])
            with connect(db_path) as conn:
                row = conn.execute("SELECT parse_status, parse_warnings, training_eligible FROM forecast_runs").fetchone()

        self.assertEqual(row["parse_status"], "failed")
        self.assertIn("missing_hourly_temperature_2m", row["parse_warnings"])
        self.assertEqual(row["training_eligible"], 0)

    def test_openmeteo_cli_dry_run_plans_requests_without_writing(self):
        db_path = test_db_path("openmeteo_cli_dry_run")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            payload = run_openmeteo_fetch("chicago", dry_run=True, limit_cities=1)
            init_v3_db()
            with connect(db_path) as conn:
                run_count = conn.execute("SELECT COUNT(*) FROM forecast_runs").fetchone()[0]

        self.assertTrue(payload["dry_run"])
        self.assertGreaterEqual(len(payload["requests_planned"]), 5)
        self.assertEqual(run_count, 0)

    def test_openmeteo_previous_runs_range_request_covers_local_dates_once(self):
        from weatherbot_v3.openmeteo import build_previous_runs_range_request

        profile = SETTLEMENT_REGISTRY["singapore"]
        request = build_previous_runs_range_request(
            profile,
            "gfs_seamless",
            ["2026-07-01", "2026-07-02"],
            previous_days=[1, 2, 3],
        )

        self.assertEqual(request["start_date"], "2026-06-30")
        self.assertEqual(request["end_date"], "2026-07-02")
        self.assertEqual(request["models"], "gfs_seamless")
        self.assertIn("temperature_2m_previous_day1", request["hourly"])
        self.assertIn("temperature_2m_previous_day3", request["hourly"])

    def test_openmeteo_previous_runs_fetches_date_range_once_per_model(self):
        from weatherbot_v3.openmeteo import fetch_openmeteo_previous_runs

        db_path = test_db_path("openmeteo_previous_range")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        payload = {
            "hourly": {
                "time": ["2026-07-01T12:00", "2026-07-02T12:00"],
                "temperature_2m_previous_day1": [20.0, 21.0],
            }
        }

        class CountingSession:
            def __init__(self):
                self.calls = 0

            def get(self, *args, **kwargs):
                self.calls += 1
                return FakeHTTPResponse(payload, url="https://previous-runs-api.open-meteo.com/v1/forecast")

        session = CountingSession()
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            result = fetch_openmeteo_previous_runs(
                ["chicago"],
                target_dates=["2026-07-01", "2026-07-02"],
                models=["gfs_seamless"],
                previous_days=[1],
                session=session,
                sleep_seconds=0,
                retrieved_at="2026-07-11T00:00:00+00:00",
            )
            with connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT target_date, run_at, mean_high, parse_status FROM forecast_runs ORDER BY target_date"
                ).fetchall()

        self.assertEqual(session.calls, 1)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["requests_planned"]), 1)
        self.assertEqual(result["runs_upserted"], 2)
        self.assertEqual([row["target_date"] for row in rows], ["2026-07-01", "2026-07-02"])
        self.assertEqual([row["parse_status"] for row in rows], ["parsed", "parsed"])
        self.assertAlmostEqual(float(rows[0]["mean_high"]), 68.0, places=1)
        self.assertTrue(all(row["run_at"] for row in rows))

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
        self.assertEqual(point["timestamp"], "2026-06-29T07:00:00-05:00")
        self.assertEqual(point["local_hour"], "07:00")
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

    def test_forecast_hourly_points_keep_supplemental_runs_when_primary_is_partial(self):
        db_path = test_db_path("forecast_hourly_partial_primary")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            primary_run, primary_members = openmeteo_hourly_run(
                "chicago",
                "2026-07-02",
                "openmeteo_ncep_hrrr_conus",
                [90.0],
                valid_times=["2026-07-03T00:00:00+00:00"],
                retrieved_at="2026-07-03T03:20:00+00:00",
            )
            insert_forecast_run(primary_run, primary_members)
            legacy_run, legacy_members = openmeteo_hourly_run(
                "chicago",
                "2026-07-02",
                "ecmwf",
                [84.0, 86.0],
                valid_times=["2026-07-02T00:00:00", "2026-07-02T19:00:00"],
                retrieved_at="2026-07-02T12:00:00+00:00",
            )
            insert_forecast_run(legacy_run, legacy_members)

            points = forecast_hourly_points({"chicago": {"2026-07-02"}}, db_path=db_path)

        by_hour = {point["local_hour"]: point for point in points["chicago"]}
        self.assertIn("00:00", by_hour)
        self.assertIn("19:00", by_hour)
        self.assertAlmostEqual(by_hour["00:00"]["best"], 84.0)
        self.assertAlmostEqual(by_hour["19:00"]["best"], 88.0)
        self.assertIn("ecmwf", by_hour["00:00"]["forecast_sources"])
        self.assertIn("openmeteo_ncep_hrrr_conus", by_hour["19:00"]["forecast_sources"])

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

    def test_layer4_hourly_consensus_builds_forecast_observation_residual(self):
        db_path = test_db_path("layer4_hourly_consensus")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        forecast_rows = [
            {
                "hour": "16:00",
                "temperature_c": 31.0,
                "cloud_cover_pct": 25,
                "wind_kph": 18,
                "wind_dir_deg": 220,
                "pressure_hpa": 1010,
                "dew_point_c": 19,
                "fetched_at": "2026-07-01T12:00:00Z",
            }
        ]
        forecast_run, forecast_members = forecast_run_from_polywx_rows(
            "chicago",
            "2026-07-01",
            forecast_rows,
            source_url="https://api.weather.polywx.xyz/api/forecast?city=chicago-kord&date=2026-07-01",
        )
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            insert_forecast_run(forecast_run, forecast_members)
            upsert_metar_report({
                "city": "chicago",
                "city_name": "Chicago",
                "station_id": "KORD",
                "report_time": "2026-07-01T21:51:00Z",
                "raw_text": "METAR KORD 012151Z 21012KT 10SM -RA FEW042 BKN090 33/23 A2987",
                "temperature": 91.4,
                "dew_point": 73.4,
                "wind_direction": 210,
                "wind_speed": 12,
                "visibility": 10,
                "cloud_layers": [{"cover": "FEW"}, {"cover": "BKN"}],
                "pressure": 1011,
            })
            upsert_mesonet_observation({
                "observation_key": "pws:chicago:2026-07-01T21:20:00Z",
                "city": "chicago",
                "city_name": "Chicago",
                "station_id": "KILROSEM4",
                "network": "pws",
                "observed_at": "2026-07-01T21:20:00Z",
                "temperature": 90.0,
                "humidity": 48,
                "source_url": "https://api.weather.polywx.xyz/api/pws",
                "quality_flags": ["nearby_station"],
            })
            result = build_hourly_consensus(["chicago"], target_date="2026-07-01", db_path=db_path)
            summary = hourly_consensus_summary("chicago", "2026-07-01", db_path=db_path)
            with connect(db_path) as conn:
                row = conn.execute(
                    "SELECT * FROM hourly_consensus WHERE city = ? AND target_date = ? AND local_hour = ?",
                    ("chicago", "2026-07-01", "16:00"),
                ).fetchone()

        self.assertTrue(result["ok"])
        self.assertEqual(result["forecast_points"], 1)
        self.assertEqual(result["observation_points"], 1)
        self.assertEqual(result["rows_upserted"], 1)
        self.assertEqual(summary["rows"], 1)
        self.assertIsNotNone(row)
        self.assertEqual(row["consensus_version"], "hourly-consensus-v2")
        self.assertEqual(row["build_status"], "fallback_only")
        self.assertEqual(row["forecast_source"], "polywx_fallback")
        self.assertIn("fallback_polywx_only", row["build_warnings"])
        self.assertIn("metar", row["observation_sources_json"])
        self.assertIn("pws", row["observation_sources_json"])
        self.assertAlmostEqual(row["forecast_temp"], 87.8, places=1)
        self.assertAlmostEqual(row["observed_temp"], 91.4, places=1)
        self.assertAlmostEqual(row["cloud_cover"], 100.0, places=1)
        self.assertAlmostEqual(row["residual"], 3.6, places=1)
        self.assertEqual(summary["points"][0]["build_status"], "fallback_only")
        self.assertAlmostEqual(summary["points"][0]["visibility"], 10.0, places=1)
        self.assertAlmostEqual(summary["points"][0]["cloud_cover"], 100.0, places=1)
        self.assertAlmostEqual(summary["points"][0]["forecast_cloud_cover"], 25.0, places=1)
        self.assertEqual(summary["points"][0]["condition"], "-RA")

    def test_layer4_openmeteo_primary_excludes_polywx_and_uses_median_spread(self):
        db_path = test_db_path("layer4_openmeteo_primary")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        valid_time = "2026-07-01T21:00:00+00:00"
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            for source, temp in [
                ("openmeteo_ncep_hrrr_conus", 70.0),
                ("openmeteo_ncep_nbm_conus", 75.0),
                ("openmeteo_ecmwf_ifs025", 80.0),
                ("openmeteo_gfs_seamless", 85.0),
            ]:
                run, members = openmeteo_hourly_run("chicago", "2026-07-01", source, [temp], valid_times=[valid_time])
                run["retrieved_at"] = "2026-06-30T12:00:00+00:00"
                run["horizon"] = "d1"
                insert_forecast_run(run, members)
            polywx_run, polywx_members = forecast_run_from_polywx_rows(
                "chicago",
                "2026-07-01",
                [{"hour": "16:00", "temperature_c": 48.9, "fetched_at": "2026-07-01T12:00:00Z"}],
                source_url="https://api.weather.polywx.xyz/api/forecast?city=chicago-kord&date=2026-07-01",
            )
            insert_forecast_run(polywx_run, polywx_members)
            upsert_metar_report({
                "city": "chicago",
                "station_id": "KORD",
                "report_time": "2026-07-01T21:51:00Z",
                "temperature": 25.56,
                "parser_version": "iem-asos-csv-v1",
                "raw_json": {"normalized_temperature_unit": "C"},
                "raw_text": "KORD 012151Z 21012KT 10SM 26/21 A2987",
            })

            result = build_hourly_consensus(["chicago"], target_date="2026-07-01", db_path=db_path)
            with connect(db_path) as conn:
                row = conn.execute(
                    "SELECT * FROM hourly_consensus WHERE city = ? AND target_date = ? AND local_hour = ?",
                    ("chicago", "2026-07-01", "16:00"),
                ).fetchone()

        self.assertTrue(result["ok"])
        self.assertIsNotNone(row)
        self.assertEqual(row["forecast_source"], "openmeteo_multi_model")
        self.assertNotIn("polywx_forecast", row["forecast_sources_json"])
        self.assertAlmostEqual(row["forecast_temp"], 77.5, places=2)
        self.assertAlmostEqual(row["observed_temp"], 78.0, places=1)
        self.assertAlmostEqual(row["residual"], 0.5, places=1)
        self.assertAlmostEqual(row["forecast_spread"], 7.5, places=2)
        self.assertEqual(row["forecast_member_count"], 4)
        self.assertEqual(row["consensus_method"], "median_primary_v1")

    def test_layer4_polywx_fallback_is_explicit(self):
        db_path = test_db_path("layer4_polywx_fallback")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        forecast_run, forecast_members = forecast_run_from_polywx_rows(
            "chicago",
            "2026-07-01",
            [{"hour": "16:00", "temperature_c": 31.0, "fetched_at": "2026-07-01T12:00:00Z"}],
            source_url="https://api.weather.polywx.xyz/api/forecast?city=chicago-kord&date=2026-07-01",
        )
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            insert_forecast_run(forecast_run, forecast_members)
            build_hourly_consensus(["chicago"], target_date="2026-07-01", db_path=db_path)
            with connect(db_path) as conn:
                row = conn.execute(
                    "SELECT forecast_source, build_status, build_warnings FROM hourly_consensus WHERE city = ?",
                    ("chicago",),
                ).fetchone()

        self.assertEqual(row["forecast_source"], "polywx_fallback")
        self.assertEqual(row["build_status"], "fallback_only")
        self.assertIn("fallback_polywx_only", row["build_warnings"])

    def test_daily_max_v2_uses_station_local_day_window(self):
        db_path = test_db_path("daily_max_local_day")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        run, members = openmeteo_hourly_run(
            "tokyo",
            "2026-07-02",
            "openmeteo_jma_seamless",
            [30.0, 33.0, 40.0],
            valid_times=[
                "2026-07-01T16:00:00+00:00",
                "2026-07-02T14:00:00+00:00",
                "2026-07-02T16:00:00+00:00",
            ],
        )
        with patch.dict(os.environ, {
            "V3_DB_PATH": str(db_path),
            "DEB_WEIGHT_MODE": "legacy",
            "WEATHERBOT_ENSEMBLE_DEB_ENABLED": "false",
        }, clear=False):
            insert_forecast_run(run, members)
            prediction = build_daily_max_prediction("tokyo", "2026-07-02", issued_at="2026-07-02T12:34:00+00:00", path=db_path)

        self.assertTrue(prediction["ok"])
        self.assertEqual(prediction["deb_version"], "weatherbot-deb-v2")
        self.assertAlmostEqual(prediction["mu"], 33.0, places=2)
        self.assertNotIn(40.0, prediction["member_daily_highs"]["openmeteo_jma_seamless"])

    def test_daily_max_v2_applies_observed_floor_and_bias_threshold(self):
        db_path = test_db_path("daily_max_v2_floor_bias")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        run, members = openmeteo_hourly_run(
            "chicago",
            "2026-07-10",
            "openmeteo_ncep_hrrr_conus",
            [88.0],
            valid_times=["2026-07-10T21:00:00+00:00"],
        )
        with patch.dict(os.environ, {
            "V3_DB_PATH": str(db_path),
            "DEB_WEIGHT_MODE": "legacy",
            "WEATHERBOT_ENSEMBLE_DEB_ENABLED": "false",
        }, clear=False):
            insert_forecast_run(run, members)
            upsert_metar_report({
                "city": "chicago",
                "station_id": "KORD",
                "report_time": "2026-07-10T11:51:00Z",
                "temperature": 33.33,
                "parser_version": "iem-asos-csv-v1",
                "raw_json": {"normalized_temperature_unit": "C"},
                "raw_text": "KORD 102151Z 21012KT 10SM 33/21 A2987",
            })
            first = build_daily_max_prediction("chicago", "2026-07-10", issued_at="2026-07-10T12:00:00Z", path=db_path)
            for day in range(1, 8):
                upsert_hourly_consensus({
                    "city": "chicago",
                    "city_name": "Chicago",
                    "target_date": f"2026-07-{day:02d}",
                    "local_hour": "16:00",
                    "valid_time": f"2026-07-{day:02d}T16:00:00-05:00",
                    "station_id": "KORD",
                    "forecast_temp": 80.0,
                    "observed_temp": 82.0,
                    "observation_source": "metar",
                    "forecast_source": "openmeteo_multi_model",
                    "source_count": 2,
                })
            second = build_daily_max_prediction("chicago", "2026-07-10", issued_at="2026-07-10T13:00:00Z", path=db_path)

        self.assertTrue(first["mu_observed_floor_applied"])
        self.assertGreaterEqual(first["mu"], 91.0)
        self.assertEqual(first["bias_correction"], 0.0)
        self.assertIn("insufficient_settlement_days", first["build_warnings"])
        self.assertEqual(second["bias_sample_count"], 7)
        self.assertAlmostEqual(second["bias_correction"], 2.0, places=2)
        self.assertGreaterEqual(first["sigma"], first["sigma_floor"])

    def test_observed_floor_rejects_display_only_historical_and_future_metar(self):
        db_path = test_db_path("daily_max_floor_source_contract")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        run, members = openmeteo_hourly_run(
            "chicago",
            "2026-07-10",
            "openmeteo_ncep_hrrr_conus",
            [80.0],
            valid_times=["2026-07-10T21:00:00+00:00"],
        )
        with patch.dict(os.environ, {
            "V3_DB_PATH": str(db_path),
            "DEB_WEIGHT_MODE": "legacy",
            "WEATHERBOT_ENSEMBLE_DEB_ENABLED": "false",
        }, clear=False):
            insert_forecast_run(run, members)
            upsert_mesonet_observation({
                "observation_key": "history:future-hot",
                "city": "chicago",
                "network": "open_meteo_historical",
                "station_id": "KORD",
                "observed_at": "2026-07-10T10:00:00+00:00",
                "temperature": 110.0,
                "raw_unit": "F",
                "parse_status": "parsed",
                "quality_flags": ["display_only", "not_settlement_truth"],
            })
            upsert_metar_report({
                "city": "chicago",
                "station_id": "KORD",
                "report_time": "2026-07-10T14:00:00+00:00",
                "temperature": 100.0,
                "parser_version": "test-fahrenheit-v1",
                "raw_json": {"normalized_temperature_unit": "F"},
                "raw_text": "KORD future",
            })
            prediction = build_daily_max_prediction(
                "chicago",
                "2026-07-10",
                issued_at="2026-07-10T12:00:00+00:00",
                path=db_path,
            )

        self.assertTrue(prediction["ok"])
        self.assertIsNone(prediction["observed_floor"])
        self.assertFalse(prediction["mu_observed_floor_applied"])
        self.assertLess(float(prediction["mu"]), 100.0)

    def test_daily_max_v2_preserves_distinct_cutoffs_within_same_hour(self):
        db_path = test_db_path("daily_max_v2_idempotent")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        run, members = openmeteo_hourly_run(
            "chicago",
            "2026-07-10",
            "openmeteo_ncep_hrrr_conus",
            [88.0],
            valid_times=["2026-07-10T21:00:00+00:00"],
        )
        with patch.dict(os.environ, {
            "V3_DB_PATH": str(db_path),
            "DEB_WEIGHT_MODE": "legacy",
            "WEATHERBOT_ENSEMBLE_DEB_ENABLED": "false",
        }, clear=False):
            insert_forecast_run(run, members)
            build_and_store_daily_max_prediction("chicago", "2026-07-10", issued_at="2026-07-10T12:10:00Z", path=db_path)
            build_and_store_daily_max_prediction("chicago", "2026-07-10", issued_at="2026-07-10T12:50:00Z", path=db_path)
            with connect(db_path) as conn:
                count = conn.execute("SELECT COUNT(*) FROM daily_max_predictions").fetchone()[0]
                row = conn.execute("SELECT deb_version, bias_sample_count FROM daily_max_predictions").fetchone()

        self.assertEqual(count, 2)
        self.assertEqual(row["deb_version"], "weatherbot-deb-v2")

    def test_daily_max_peak_hour_uses_mixed_curve_and_latest_tie(self):
        db_path = test_db_path("daily_max_mixed_peak")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        valid_times = [f"2026-07-02T{hour:02d}:00:00+00:00" for hour in range(24)]
        run, members = openmeteo_hourly_run(
            "chicago",
            "2026-07-02",
            "openmeteo_ncep_hrrr_conus",
            [88.0] * 24,
            valid_times=valid_times,
            retrieved_at="2026-07-02T12:00:00+00:00",
        )
        with patch.dict(os.environ, {
            "V3_DB_PATH": str(db_path),
            "DEB_WEIGHT_MODE": "legacy",
            "WEATHERBOT_ENSEMBLE_DEB_ENABLED": "false",
        }, clear=False):
            insert_forecast_run(run, members)
            for hour in range(24):
                local_hour = f"{hour:02d}:00"
                forecast = 94.5 if hour == 15 else 90.0
                observed = 93.92 if hour in {13, 14, 15, 16} else 88.0
                upsert_hourly_consensus({
                    "city": "chicago",
                    "city_name": "Chicago",
                    "target_date": "2026-07-02",
                    "local_hour": local_hour,
                    "valid_time": f"2026-07-02T{hour:02d}:00:00-05:00",
                    "station_id": "KORD",
                    "forecast_temp": forecast,
                    "observed_temp": observed,
                    "observation_source": "metar",
                    "forecast_source": "openmeteo_multi_model",
                    "source_count": 2,
                })
            prediction = build_daily_max_prediction("chicago", "2026-07-02", issued_at="2026-07-03T05:00:00Z", path=db_path)

        self.assertTrue(prediction["ok"])
        self.assertEqual(prediction["peak_hour"], "16:00")
        self.assertEqual(prediction["peak_source"], "metar")
        self.assertAlmostEqual(prediction["peak_temp"], 93.92, places=2)

    def test_weathercom_v3_forecast_can_feed_polywx_aligned_deb(self):
        db_path = test_db_path("weathercom_v3_polywx_deb")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        profile = SETTLEMENT_REGISTRY["shanghai"]
        payload = {
            "validTimeUtc": [
                1783296000,  # 2026-07-06T00:00:00Z
                1783306800,
                1783317600,
            ],
            "temperature": [27.0, 33.0, 35.0],
            "cloudCover": [40, 55, 70],
            "relativeHumidity": [80, 68, 60],
            "temperatureDewPoint": [24, 25, 25],
            "precipChance": [10, 20, 30],
            "windSpeed": [8, 10, 12],
            "windDirection": [180, 200, 220],
            "pressureMeanSeaLevel": [1006, 1007, 1007],
            "wxPhraseLong": ["Cloudy", "Partly Cloudy", "Thunderstorms"],
        }
        weather_runs, weather_members = weathercom_runs_from_response(
            profile,
            payload,
            source_url="https://api.weather.com/v3/wx/forecast/hourly/15day?apiKey=***",
            retrieved_at="2026-07-05T12:00:00+00:00",
            forecast_days=2,
        )
        gfs_run, gfs_members = openmeteo_hourly_run(
            "shanghai",
            "2026-07-06",
            "openmeteo_gfs_seamless",
            [28.0, 34.0],
            valid_times=["2026-07-06T00:00:00+00:00", "2026-07-06T06:00:00+00:00"],
            retrieved_at="2026-07-05T12:00:00+00:00",
        )
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "DEB_WEIGHT_MODE": "polywx_aligned"}, clear=False):
            for run, members in zip(weather_runs, weather_members):
                insert_forecast_run(run, members)
            insert_forecast_run(gfs_run, gfs_members)
            prediction = build_daily_max_prediction(
                "shanghai",
                "2026-07-06",
                issued_at="2026-07-05T13:00:00Z",
                path=db_path,
                bias_table=[{
                    "icao": "ZSPD",
                    "model": "gfs",
                    "sample_count": 20,
                    "mae_7d_c": 0.8,
                    "location_version": profile.location_version,
                }],
            )

        self.assertTrue(prediction["ok"])
        self.assertEqual(prediction["deb_version"], "polywx_aligned_deb_v1")
        families = {component["family"] for component in prediction["components"]}
        self.assertIn("weathercom_v3", families)
        self.assertIn("gfs", families)
        self.assertAlmostEqual(sum(prediction["model_weights"].values()), 1.0, places=6)
        v3_component = next(component for component in prediction["components"] if component["family"] == "weathercom_v3")
        self.assertEqual(v3_component["role"], "weather.com/WU-style v3 forecast")
        self.assertIn("truth_basis", v3_component)
        self.assertTrue(v3_component["mae_imputed"])
        self.assertGreaterEqual(v3_component["effective_mae_c"], 1.2)
        self.assertLess(v3_component["weight"], 0.484 / (0.484 + 0.152))
        self.assertNotIn("missing_weathercom_v3", prediction["build_warnings"])

    def test_weathercom_v3_deb_rebuilds_elapsed_hours_from_forecast_snapshots(self):
        db_path = test_db_path("weathercom_v3_snapshot_daily_high")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        profile = SETTLEMENT_REGISTRY["shanghai"]

        def epoch(local_value: str) -> int:
            return int(datetime.fromisoformat(local_value).timestamp())

        full_day_payload = {
            "validTimeUtc": [
                epoch("2026-07-14T00:00:00+08:00"),
                epoch("2026-07-14T15:00:00+08:00"),
                epoch("2026-07-14T20:00:00+08:00"),
                epoch("2026-07-14T23:00:00+08:00"),
            ],
            "temperature": [28.0, 35.0, 31.0, 30.0],
        }
        partial_evening_payload = {
            "validTimeUtc": [
                epoch("2026-07-14T20:00:00+08:00"),
                epoch("2026-07-14T23:00:00+08:00"),
            ],
            "temperature": [30.0, 29.0],
        }
        old_runs, old_members = weathercom_runs_from_response(
            profile,
            full_day_payload,
            source_url="https://api.weather.com/v3/wx/forecast/hourly/15day?apiKey=***",
            retrieved_at="2026-07-14T02:00:00+00:00",
            forecast_days=3,
        )
        new_runs, new_members = weathercom_runs_from_response(
            profile,
            partial_evening_payload,
            source_url="https://api.weather.com/v3/wx/forecast/hourly/15day?apiKey=***",
            retrieved_at="2026-07-14T12:00:00+00:00",
            forecast_days=2,
        )
        gfs_run, gfs_members = openmeteo_hourly_run(
            "shanghai",
            "2026-07-14",
            "openmeteo_gfs_seamless",
            [30.0, 34.0],
            valid_times=["2026-07-14T00:00:00+08:00", "2026-07-14T15:00:00+08:00"],
            retrieved_at="2026-07-14T02:00:00+00:00",
        )

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "DEB_WEIGHT_MODE": "polywx_aligned"}, clear=False):
            for run, members in [*zip(old_runs, old_members), *zip(new_runs, new_members)]:
                insert_forecast_run(run, members)
            insert_forecast_run(gfs_run, gfs_members)
            prediction = build_daily_max_prediction(
                "shanghai",
                "2026-07-14",
                issued_at="2026-07-14T13:00:00Z",
                path=db_path,
                bias_table=[],
            )

        self.assertTrue(prediction["ok"])
        v3_component = next(component for component in prediction["components"] if component["family"] == "weathercom_v3")
        self.assertAlmostEqual(v3_component["model_daily_high_c"], 35.0, places=2)
        # The 00:00 point was already in the past when the first snapshot was
        # retrieved, so it is a revision rather than knowable forecast input.
        self.assertEqual(v3_component["archive_hour_count"], 3)
        self.assertEqual(v3_component["daily_high_basis"], "latest_snapshot_per_member_valid_hour_as_of")
        self.assertGreaterEqual(v3_component["snapshot_count"], 2)

    def test_openmeteo_d0_stitches_latest_hour_per_member_without_future_leakage(self):
        db_path = test_db_path("openmeteo_d0_member_snapshot_stitch")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        target_date = "2026-07-15"
        model = "gfs_seamless"
        times = [
            "2026-07-15T12:00:00Z",
            "2026-07-15T18:00:00Z",
            "2026-07-15T23:00:00Z",
        ]

        def payload(values_by_member: list[list[float]], selected_times: list[str]) -> dict:
            hourly = {"time": selected_times}
            for index, values in enumerate(values_by_member, start=1):
                hourly[f"temperature_2m_member{index:02d}"] = values
            return {"hourly": hourly}

        old_values = [
            [25.0 + offset, 35.0 + offset, 40.0 + offset]
            for offset in (0.0, 0.1, 0.2, 0.3, 0.4)
        ]
        new_values = [
            [30.0 + offset]
            for offset in (0.0, 0.1, 0.2, 0.3, 0.4)
        ]
        future_values = [[50.0 + offset] for offset in (0.0, 0.1, 0.2, 0.3, 0.4)]

        def persist(snapshot_payload: dict, retrieved_at: str) -> int:
            runs, members_by_run = openmeteo_runs_from_response(
                "chicago",
                model,
                snapshot_payload,
                source_url="https://ensemble-api.open-meteo.com/v1/ensemble",
                retrieved_at=retrieved_at,
                endpoint_kind="ensemble",
            )
            for run, members in zip(runs, members_by_run):
                if run["target_date"] == target_date:
                    return insert_forecast_run(run, members, path=db_path)
            self.fail("target date run was not produced")

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "DEB_WEIGHT_MODE": "polywx_aligned"}, clear=False):
            old_id = persist(payload(old_values, times), "2026-07-15T14:00:00Z")
            new_id = persist(payload(new_values, [times[-1]]), "2026-07-15T21:00:00Z")
            future_id = persist(payload(future_values, [times[1]]), "2026-07-15T23:00:00Z")
            prediction = build_daily_max_prediction(
                "chicago",
                target_date,
                issued_at="2026-07-15T22:00:00Z",
                path=db_path,
                bias_table=[],
            )

        self.assertTrue(prediction["ok"])
        self.assertEqual(prediction["snapshot_selection_mode"], "stitch_local_day")
        component = next(component for component in prediction["components"] if component["family"] == "gfs")
        self.assertEqual(component["daily_high_basis"], "latest_snapshot_per_member_valid_hour_as_of")
        self.assertEqual(component["archive_hour_count"], 2)
        self.assertEqual(component["archive_member_hour_count"], 10)
        self.assertEqual(component["archive_member_ids"], ["member01", "member02", "member03", "member04", "member05"])
        self.assertEqual(component["member_count"], 5)
        self.assertEqual(component["snapshot_count"], 2)
        self.assertEqual(component["source_run_ids"], [old_id, new_id])
        self.assertNotIn(future_id, component["source_run_ids"])
        self.assertEqual(component["effective_available_at"], "2026-07-15T21:00:00+00:00")
        self.assertEqual(component["peak_available_at"], "2026-07-15T14:00:00+00:00")
        self.assertEqual(component["peak_run_id"], old_id)
        self.assertEqual(component["peak_member_id"], "member05")
        self.assertEqual(component["raw_daily_highs_c"], [35.0, 35.1, 35.2, 35.3, 35.4])
        self.assertEqual(len(component["snapshot_selection_hash"]), 64)
        self.assertLess(max(component["raw_daily_highs_c"]), 40.0)
        self.assertTrue(component["source_age_ok"])

    def test_openmeteo_d1_uses_latest_run_without_stitching(self):
        db_path = test_db_path("openmeteo_d1_latest_run")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        target_date = "2026-07-16"
        model = "gfs_seamless"
        times = ["2026-07-16T12:00:00Z", "2026-07-16T18:00:00Z"]

        def payload(high: float) -> dict:
            hourly = {"time": times}
            for index in range(1, 6):
                hourly[f"temperature_2m_member{index:02d}"] = [high - 1.0, high]
            return {"hourly": hourly}

        def persist(snapshot_payload: dict, retrieved_at: str) -> int:
            runs, members_by_run = openmeteo_runs_from_response(
                "chicago",
                model,
                snapshot_payload,
                source_url="https://ensemble-api.open-meteo.com/v1/ensemble",
                retrieved_at=retrieved_at,
                endpoint_kind="ensemble",
            )
            for run, members in zip(runs, members_by_run):
                if run["target_date"] == target_date:
                    return insert_forecast_run(run, members, path=db_path)
            self.fail("target date run was not produced")

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "DEB_WEIGHT_MODE": "polywx_aligned"}, clear=False):
            old_id = persist(payload(45.0), "2026-07-14T12:00:00Z")
            new_id = persist(payload(31.0), "2026-07-15T10:00:00Z")
            prediction = build_daily_max_prediction(
                "chicago",
                target_date,
                issued_at="2026-07-15T12:00:00Z",
                path=db_path,
                bias_table=[],
            )

        self.assertTrue(prediction["ok"])
        self.assertEqual(prediction["snapshot_selection_mode"], "latest_run")
        component = next(component for component in prediction["components"] if component["family"] == "gfs")
        self.assertEqual(component["daily_high_basis"], "latest_forecast_run")
        self.assertEqual(component["archive_hour_count"], 0)
        self.assertEqual(component["snapshot_count"], 1)
        self.assertEqual(component["source_run_ids"], [new_id])
        self.assertNotIn(old_id, component["source_run_ids"])
        self.assertAlmostEqual(component["model_daily_high_c"], 31.0, places=2)

    def test_openmeteo_d0_uses_future_points_from_negative_aggregate_lead_snapshot(self):
        db_path = test_db_path("openmeteo_d0_negative_aggregate_lead")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        target_date = "2026-07-17"
        model = "gfs_seamless"
        old_times = [
            "2026-07-16T16:00:00Z",
            "2026-07-17T04:00:00Z",
            "2026-07-17T13:00:00Z",
        ]

        def payload(times: list[str], values_by_member: list[list[float]]) -> dict:
            hourly = {"time": times}
            for index, values in enumerate(values_by_member, start=1):
                hourly[f"temperature_2m_member{index:02d}"] = values
            return {"hourly": hourly}

        def persist(snapshot_payload: dict, retrieved_at: str) -> int:
            runs, members_by_run = openmeteo_runs_from_response(
                "shanghai",
                model,
                snapshot_payload,
                source_url="https://ensemble-api.open-meteo.com/v1/ensemble",
                retrieved_at=retrieved_at,
                endpoint_kind="ensemble",
            )
            for run, members in zip(runs, members_by_run):
                if run["target_date"] == target_date:
                    return insert_forecast_run(run, members, path=db_path)
            self.fail("target date run was not produced")

        old_values = [
            [30.0 + offset, 34.0 + offset, 31.0 + offset]
            for offset in (0.0, 0.1, 0.2, 0.3, 0.4)
        ]
        # The snapshot's hottest point is already in the past, so its aggregate
        # run is training-ineligible. Its 13Z point is still a valid forecast and
        # must refresh that hour without revising the past 04Z peak.
        new_values = [
            [38.0 + offset, 33.0 + offset]
            for offset in (0.0, 0.1, 0.2, 0.3, 0.4)
        ]

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "DEB_WEIGHT_MODE": "polywx_aligned"}, clear=False):
            old_id = persist(payload(old_times, old_values), "2026-07-16T00:00:00Z")
            new_id = persist(
                payload([old_times[1], old_times[2]], new_values),
                "2026-07-17T12:00:00Z",
            )
            with connect(db_path) as conn:
                new_run = conn.execute(
                    "SELECT training_eligible, ineligibility_reason FROM forecast_runs WHERE id = ?",
                    (new_id,),
                ).fetchone()
            prediction = build_daily_max_prediction(
                "shanghai",
                target_date,
                issued_at="2026-07-17T12:30:00Z",
                path=db_path,
                bias_table=[],
            )

        self.assertEqual(int(new_run["training_eligible"]), 0)
        self.assertEqual(new_run["ineligibility_reason"], "forecast_lead_negative")
        self.assertTrue(prediction["ok"])
        component = next(component for component in prediction["components"] if component["family"] == "gfs")
        self.assertEqual(component["source_run_ids"], [old_id, new_id])
        self.assertEqual(component["effective_available_at"], "2026-07-17T12:00:00+00:00")
        self.assertEqual(component["point_availability_contract"], "valid_at_gte_snapshot_available_at")
        self.assertEqual(component["peak_run_id"], old_id)
        self.assertEqual(component["raw_daily_highs_c"], [34.0, 34.1, 34.2, 34.3, 34.4])
        self.assertLess(max(component["raw_daily_highs_c"]), 38.0)

    def test_polywx_deb_does_not_fall_back_when_all_components_are_stale(self):
        db_path = test_db_path("polywx_stale_components_fail_closed")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        profile = SETTLEMENT_REGISTRY["shanghai"]
        valid_time = int(datetime.fromisoformat("2026-07-14T15:00:00+08:00").timestamp())
        weather_runs, weather_members = weathercom_runs_from_response(
            profile,
            {"validTimeUtc": [valid_time], "temperature": [35.0]},
            retrieved_at="2026-07-12T12:00:00+00:00",
            forecast_days=3,
        )
        gfs_run, gfs_members = openmeteo_hourly_run(
            "shanghai",
            "2026-07-14",
            "openmeteo_gfs_seamless",
            [34.0],
            valid_times=["2026-07-14T15:00:00+08:00"],
            retrieved_at="2026-07-12T12:00:00+00:00",
        )

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "DEB_WEIGHT_MODE": "polywx_aligned"}, clear=False):
            for run, members in zip(weather_runs, weather_members):
                insert_forecast_run(run, members)
            insert_forecast_run(gfs_run, gfs_members)
            prediction = build_daily_max_prediction(
                "shanghai",
                "2026-07-14",
                issued_at="2026-07-14T13:00:00Z",
                path=db_path,
                bias_table=[],
            )

        self.assertFalse(prediction["ok"])
        self.assertEqual(prediction["deb_version"], "polywx_aligned_deb_v1")
        self.assertTrue(any(reason.startswith("forecast_component_stale") for reason in prediction["reasons"]))

    def test_weathercom_request_units_follow_city_settlement_unit(self):
        self.assertEqual(weathercom_request_units(SETTLEMENT_REGISTRY["shanghai"]), ("m", "C"))
        self.assertEqual(weathercom_request_units(SETTLEMENT_REGISTRY["chicago"]), ("e", "F"))

    def test_weathercom_forecast_history_separates_snapshots_from_integer_revisions(self):
        db_path = test_db_path("weathercom_v3_forecast_history")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        profile = SETTLEMENT_REGISTRY["shanghai"]

        def epoch(local_value: str) -> int:
            return int(datetime.fromisoformat(local_value).timestamp())

        snapshots = [
            ("2026-07-12T00:00:00+00:00", 34.0),
            ("2026-07-12T01:00:00+00:00", 34.444),
            ("2026-07-12T02:00:00+00:00", 35.0),
        ]
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            for retrieved_at, value in snapshots:
                runs, members_by_run = weathercom_runs_from_response(
                    profile,
                    {
                        "validTimeUtc": [epoch("2026-07-14T15:00:00+08:00")],
                        "temperature": [value],
                    },
                    source_url="https://api.weather.com/v3/wx/forecast/hourly/15day?apiKey=***",
                    retrieved_at=retrieved_at,
                    forecast_days=3,
                )
                for run, members in zip(runs, members_by_run):
                    insert_forecast_run(run, members)

            partial_runs, partial_members = weathercom_runs_from_response(
                profile,
                {
                    "validTimeUtc": [epoch("2026-07-14T20:00:00+08:00")],
                    "temperature": [30.0],
                },
                source_url="https://api.weather.com/v3/wx/forecast/hourly/15day?apiKey=***",
                retrieved_at="2026-07-14T12:00:00+00:00",
                forecast_days=1,
            )
            for run, members in zip(partial_runs, partial_members):
                insert_forecast_run(run, members)

            history = forecast_revision_history(
                "shanghai",
                "2026-07-14",
                "15:00",
                db_path=db_path,
            )
            source_series = source_series_summary("shanghai", "2026-07-14", db_path=db_path)

        self.assertTrue(history["ok"])
        self.assertEqual(history["snapshot_count"], 3)
        self.assertEqual(history["revision_count"], 1)
        self.assertEqual(history["distinct_count"], 2)
        self.assertEqual(history["unchanged_snapshot_count"], 1)
        self.assertEqual([row["display_temperature"] for row in history["revisions"]], [34.0, 35.0])
        self.assertEqual(history["revisions"][1]["delta_from_previous"], 1.0)
        self.assertTrue(history["revisions"][0]["fetched_at_local"].endswith("+08:00"))
        point = next(row for row in source_series["forecast"] if row["local_hour"] == "15:00")
        self.assertEqual(point["snapshot_count"], 3)
        self.assertEqual(point["revision_count"], 1)
        self.assertEqual(point["distinct_count"], 2)

    def test_weathercom_forecast_fields_survive_hourly_consensus(self):
        db_path = test_db_path("weathercom_v3_hourly_consensus_fields")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        profile = SETTLEMENT_REGISTRY["shanghai"]
        payload = {
            "validTimeUtc": [1783296000],
            "temperature": [27.0],
            "cloudCover": [100],
            "relativeHumidity": [98],
            "temperatureDewPoint": [26],
            "precipChance": [94],
            "qpf": [1.6],
            "windSpeed": [59],
            "windDirection": [76],
            "pressureMeanSeaLevel": [994],
            "wxPhraseLong": ["Rain/Wind"],
        }
        runs, members_by_run = weathercom_runs_from_response(
            profile,
            payload,
            source_url="https://api.weather.com/v3/wx/forecast/hourly/15day?apiKey=***",
            retrieved_at="2026-07-05T12:00:00+00:00",
            forecast_days=2,
        )
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            for run, members in zip(runs, members_by_run):
                insert_forecast_run(run, members)
            result = build_hourly_consensus(["shanghai"], target_date="2026-07-06", db_path=db_path)
            summary = hourly_consensus_summary("shanghai", "2026-07-06", db_path=db_path)

        self.assertTrue(result["ok"])
        self.assertEqual(summary["rows"], 1)
        point = summary["points"][0]
        self.assertEqual(point["condition"], "Rain/Wind")
        self.assertEqual(point["precipitation_probability"], 94)
        self.assertAlmostEqual(point["precipitation"], 1.6, places=1)
        self.assertEqual(point["forecast_cloud_cover"], 100)
        self.assertEqual(point["retrieved_at"], "2026-07-05T12:00:00+00:00")

    def test_weathercom_imperial_payload_preserves_temperature_precision_and_normalizes_units(self):
        profile = SETTLEMENT_REGISTRY["shanghai"]
        payload = {
            "validTimeUtc": [1783296000],
            "temperature": [87.0],
            "temperatureDewPoint": [70.0],
            "qpf": [0.1],
            "windSpeed": [10.0],
            "windGust": [15.0],
            "pressureMeanSeaLevel": [29.5],
        }
        runs, members_by_run = weathercom_runs_from_response(
            profile,
            payload,
            source_url="https://api.weather.com/v3/wx/forecast/hourly/15day?units=e&apiKey=***",
            retrieved_at="2026-07-05T12:00:00+00:00",
            forecast_days=2,
            source_unit="F",
        )

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["source_unit"], "F")
        point = members_by_run[0][0]["hourly"][0]
        self.assertAlmostEqual(point["temperature_2m"], 30.556, places=3)
        self.assertAlmostEqual(point["temperature_2m_c"], 30.556, places=3)
        self.assertAlmostEqual(point["dew_point_2m"], 21.111, places=3)
        self.assertAlmostEqual(point["wind_speed_10m"], 16.093, places=3)
        self.assertAlmostEqual(point["wind_gusts_10m"], 24.14, places=2)
        self.assertAlmostEqual(point["pressure_msl"], 998.985, places=3)
        self.assertAlmostEqual(point["precipitation"], 2.54, places=2)

    def test_weathercom_failed_imperial_payload_keeps_raw_unit_provenance(self):
        runs, members_by_run = weathercom_runs_from_response(
            SETTLEMENT_REGISTRY["shanghai"],
            {},
            retrieved_at="2026-07-05T12:00:00+00:00",
            source_unit="F",
        )

        self.assertEqual(members_by_run, [[]])
        self.assertEqual(runs[0]["parse_status"], "failed")
        self.assertEqual(runs[0]["source_unit"], "F")

    def test_weathercom_forecast_excludes_explicit_wrong_station_geocode(self):
        db_path = test_db_path("weathercom_station_geocode")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        profile = SETTLEMENT_REGISTRY["tokyo"]
        valid_time = int(datetime(2026, 7, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo")).timestamp())
        correct_runs, correct_members = weathercom_runs_from_response(
            profile,
            {"validTimeUtc": [valid_time], "temperature": [26.0], "cloudCover": [75]},
            source_url="https://api.weather.com/v3/wx/forecast/hourly/15day?geocode=35.553%2C139.781",
            retrieved_at="2026-07-11T12:00:00+00:00",
            forecast_days=2,
        )
        wrong_runs, wrong_members = weathercom_runs_from_response(
            profile,
            {"validTimeUtc": [valid_time], "temperature": [24.0], "cloudCover": [33]},
            source_url="https://api.weather.com/v3/wx/forecast/hourly/15day?geocode=35.7647%2C140.3864",
            retrieved_at="2026-07-11T13:00:00+00:00",
            forecast_days=2,
        )
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            for run, members in zip([*correct_runs, *wrong_runs], [*correct_members, *wrong_members]):
                insert_forecast_run(run, members)
            points = forecast_hourly_points({"tokyo": {"2026-07-12"}}, db_path=db_path)["tokyo"]

        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["best"], 26.0)
        self.assertEqual(points[0]["cloud_cover"], 75.0)

    def test_deb_records_missing_weathercom_warning_in_polywx_mode(self):
        db_path = test_db_path("polywx_missing_weathercom")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        gfs_run, gfs_members = openmeteo_hourly_run(
            "shanghai",
            "2026-07-06",
            "openmeteo_gfs_seamless",
            [34.0, 35.0, 35.5],
            valid_times=[
                "2026-07-06T00:00:00+00:00",
                "2026-07-06T06:00:00+00:00",
                "2026-07-06T08:00:00+00:00",
            ],
            retrieved_at="2026-07-05T12:00:00+00:00",
        )
        ecmwf_run, ecmwf_members = openmeteo_hourly_run(
            "shanghai",
            "2026-07-06",
            "openmeteo_ecmwf_ifs025",
            [33.0, 34.5],
            valid_times=["2026-07-06T00:00:00+00:00", "2026-07-06T06:00:00+00:00"],
            retrieved_at="2026-07-05T12:00:00+00:00",
        )
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "DEB_WEIGHT_MODE": "polywx_aligned"}, clear=False):
            insert_forecast_run(gfs_run, gfs_members)
            insert_forecast_run(ecmwf_run, ecmwf_members)
            prediction = build_daily_max_prediction("shanghai", "2026-07-06", issued_at="2026-07-05T13:00:00Z", path=db_path)

        self.assertTrue(prediction["ok"])
        self.assertEqual(prediction["deb_version"], "polywx_aligned_deb_v1")
        self.assertIn("missing_weathercom_v3", prediction["build_warnings"])

    def test_pws_peak_lock_is_recorded_as_evidence_only(self):
        db_path = test_db_path("pws_peak_lock_deb")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        run, members = openmeteo_hourly_run(
            "chicago",
            "2026-07-10",
            "openmeteo_ncep_hrrr_conus",
            [89.8, 90.0, 89.5],
            valid_times=[
                "2026-07-10T18:00:00+00:00",
                "2026-07-10T19:00:00+00:00",
                "2026-07-10T20:00:00+00:00",
            ],
            retrieved_at="2026-07-10T06:00:00+00:00",
        )
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "WEATHERBOT_ENSEMBLE_DEB_ENABLED": "false"}, clear=False):
            insert_forecast_run(run, members)
            for minute, temp in [(0, 91.0), (5, 90.4), (10, 89.8)]:
                upsert_mesonet_observation({
                    "observation_key": f"pws:test:{minute}",
                    "city": "chicago",
                    "city_name": "Chicago",
                    "station_id": "WU_PWS_KORD",
                    "station_name": "PWS test",
                    "network": "wunderground_pws",
                    "observed_at": f"2026-07-10T19:{minute:02d}:00+00:00",
                    "temperature": temp,
                    "parser_version": "test",
                    "parse_status": "parsed",
                    "quality_flags": ["display_only", "not_settlement_truth"],
                })
            for hour, forecast, observed in [("14:00", 90.0, 90.0), ("15:00", 89.5, 90.0), ("16:00", 89.0, 89.7)]:
                upsert_hourly_consensus({
                    "city": "chicago",
                    "city_name": "Chicago",
                    "target_date": "2026-07-10",
                    "local_hour": hour,
                    "valid_time": f"2026-07-10T{hour}:00-05:00",
                    "station_id": "KORD",
                    "forecast_temp": forecast,
                    "observed_temp": observed,
                    "observation_source": "metar",
                    "forecast_source": "openmeteo_multi_model",
                    "source_count": 2,
                })
            prediction = build_daily_max_prediction("chicago", "2026-07-10", issued_at="2026-07-10T19:30:00Z", path=db_path)

        self.assertTrue(prediction["ok"])
        self.assertTrue(prediction["peak_lock_candidate"]["candidate"])
        self.assertEqual(prediction["peak_lock_candidate"]["role"], "trend_confirmation_only_not_settlement_truth")
        self.assertIn("pws_peak_lock_candidate", prediction["build_warnings"])

    def test_wunderground_daily_truth_success_and_failure_are_explicit(self):
        db_path = test_db_path("wunderground_daily_truth")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))

        class FakeSession:
            def __init__(self, payload):
                self.payload = payload

            def get(self, url, params=None, headers=None, timeout=None):
                return FakeHTTPResponse(self.payload, url=f"{url}?ok=1")

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "WEATHER_COM_API_KEY": "test-key"}, clear=False):
            success = fetch_wunderground_daily_result(
                "ZBAA",
                "2026-07-06",
                session=FakeSession({"observations": [{"metric": {"tempHigh": 35.2, "tempLow": 24.1}}]}),
            )
            list_success = fetch_wunderground_daily_result(
                "ZSPD",
                "2026-07-06",
                session=FakeSession({"observations": [{"temp": 26}, {"temp": 36}, {"temp": 31}]}),
            )
            failure = fetch_wunderground_daily_result(
                "ZBAA",
                "2026-07-06",
                session=FakeSession({"observations": [{}]}),
            )

        self.assertTrue(success["ok"])
        self.assertEqual(success["settlement_truth_type"], "wunderground_daily")
        self.assertAlmostEqual(success["high_c"], 35.2)
        self.assertTrue(list_success["ok"])
        self.assertEqual(list_success["method"], "weather_com_v3_historical_daily")
        self.assertAlmostEqual(list_success["high_c"], 36.0)
        self.assertAlmostEqual(list_success["low_c"], 26.0)
        self.assertFalse(failure["ok"])
        self.assertIn("no_daily_high_in_payload", failure["skip_reasons"])

    def test_wunderground_daily_truth_derives_from_hourly_using_local_day(self):
        class FakeSession:
            def get(self, url, params=None, headers=None, timeout=None):
                return FakeHTTPResponse(
                    {
                        "observations": [
                            {
                                "validTimeUtc": "2026-07-05T15:00:00Z",
                                "temp": 99,
                                "wx_phrase": "Fair",
                            },
                            {
                                "validTimeUtc": "2026-07-06T04:00:00Z",
                                "temp": 35,
                                "wx_phrase": "Fair",
                            },
                        ]
                    },
                    url=f"{url}?apiKey=redacted",
                )

        with patch("weatherbot_v3.truth.wunderground.env_value", return_value=""):
            result = fetch_wunderground_daily_result(
                "ZSPD",
                "2026-07-06",
                timezone_name="Asia/Shanghai",
                session=FakeSession(),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["method"], "weather_com_location_historical_json_daily_from_hourly")
        self.assertEqual(result["high_c"], 35.0)
        self.assertEqual(result["hourly_row_count"], 1)
        self.assertIn("derived_from_wunderground_hourly_history", result["skip_reasons"])

    def test_wunderground_hourly_history_persists_and_feeds_historical_line(self):
        db_path = test_db_path("wunderground_hourly_history")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))

        class FakeSession:
            def get(self, url, params=None, headers=None, timeout=None):
                return FakeHTTPResponse(
                    {
                        "observations": [
                            {
                                "validTimeUtc": "2026-07-06T04:00:00Z",
                                "metric": {
                                    "temp": 35.4,
                                    "dewPt": 25.1,
                                    "pressure": 1007.1,
                                    "vis": 10.0,
                                    "wspd": 18.0,
                                    "gust": 24.0,
                                },
                                "rh": 62,
                                "wdir": 224,
                                "wx_phrase": "Partly Cloudy",
                                "clds": "SCT",
                            }
                        ]
                    },
                    url=f"{url}?apiKey=redacted",
                )

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path), "WEATHER_COM_API_KEY": "test-key"}, clear=False):
            result = fetch_wunderground_hourly_result(
                "ZSPD",
                "2026-07-06",
                timezone_name="Asia/Shanghai",
                session=FakeSession(),
            )
            persisted = persist_wunderground_hourly(result, path=db_path)
            build_hourly_consensus(["shanghai"], target_date="2026-07-06", db_path=db_path)
            points = hourly_consensus_points({"shanghai": {"2026-07-06"}}, db_path=db_path)
            native_series = source_series_summary("shanghai", "2026-07-06", db_path=db_path)
            with connect(db_path) as conn:
                row = conn.execute("SELECT * FROM truth_wunderground_hourly").fetchone()

        self.assertTrue(result["ok"])
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(persisted["rows_upserted"], 1)
        self.assertAlmostEqual(float(row["temp_c"]), 35.4)
        self.assertAlmostEqual(float(row["dew_point_c"]), 25.1)
        self.assertAlmostEqual(float(row["cloud_cover_pct"]), 50.0)
        self.assertTrue(str(row["observed_at_local"]).startswith("2026-07-06T12:00:00"))
        self.assertIn("shanghai", points)
        self.assertAlmostEqual(points["shanghai"][0]["historical"], 35.4)
        self.assertIsNone(points["shanghai"][0]["metar"])
        self.assertEqual(len(native_series["historical"]), 1)
        self.assertEqual(native_series["historical"][0]["local_time"], "12:00")

    def test_awc_visibility_uses_city_display_convention(self):
        item = {
            "stationId": "ZSPD",
            "obsTime": "2026-07-11T00:00:00Z",
            "rawOb": "METAR ZSPD 110000Z 12010KT 6+SM 28/27 Q1006",
            "temp": 28,
            "dewp": 27,
            "visib": "6+",
        }
        shanghai = metar_report_from_awc(item, SETTLEMENT_REGISTRY["shanghai"])
        chicago = metar_report_from_awc({**item, "stationId": "KORD"}, SETTLEMENT_REGISTRY["chicago"])

        self.assertAlmostEqual(shanghai["visibility"], 9.7, places=1)
        self.assertAlmostEqual(chicago["visibility"], 6.0, places=1)

    def test_wunderground_country_mapping_keeps_hong_kong_out_of_us_fallback(self):
        self.assertEqual(_country_from_icao("VHHH"), "HK")

    def test_shanghai_china_live_uses_pudong_station(self):
        self.assertEqual(WEATHERCN_STATION_CODES["shanghai"], "101020600")

    def test_celsius_bucket_probability_uses_truncation_not_rounding_window(self):
        result = bucket_probabilities(
            23.9,
            0.1,
            [{
                "bucket_key": "tokyo-23c",
                "unit": "C",
                "bucket_low": 23,
                "bucket_high": 23,
                "bucket_direction": "range",
            }],
            unit="C",
            sigma_floor=0.01,
            normalize=False,
        )

        item = result["items"][0]
        self.assertEqual(item["bucket_low"], 23.0)
        self.assertEqual(item["bucket_high"], 24.0)
        self.assertGreater(item["probability"], 0.80)

    def test_hourly_consensus_api_returns_layer4_rows_without_refreshing(self):
        db_path = test_db_path("hourly_consensus_api")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            upsert_hourly_consensus({
                "city": "chicago",
                "city_name": "Chicago",
                "target_date": "2026-07-01",
                "local_hour": "16:00",
                "valid_time": "2026-07-01T16:00:00-05:00",
                "station_id": "KORD",
                "forecast_temp": 88,
                "observed_temp": 91,
                "observation_source": "metar",
                "forecast_source": "ecmwf",
                "consensus_version": "hourly-consensus-v2",
                "build_status": "built",
                "source_count": 2,
            })
            payload = asyncio.run(hourly_consensus_api(city="chicago", target_date="2026-07-01"))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["city"], "chicago")
        self.assertEqual(payload["target_date"], "2026-07-01")
        self.assertEqual(payload["rows"], 1)
        self.assertEqual(payload["points"][0]["forecast_source"], "ecmwf")
        self.assertAlmostEqual(payload["points"][0]["diff"], 3.0)

    def test_hourly_consensus_api_falls_back_to_forecast_members_read_only(self):
        db_path = test_db_path("hourly_consensus_api_forecast_fallback")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        run, members = openmeteo_hourly_run(
            "chicago",
            "2026-07-01",
            "openmeteo_ecmwf_ifs025",
            [86.0],
            valid_times=["2026-07-01T18:00:00+00:00"],
        )
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            insert_forecast_run(run, members)
            payload = asyncio.run(hourly_consensus_api(city="chicago", target_date="2026-07-01"))
            with connect(db_path) as conn:
                persisted = conn.execute("SELECT COUNT(*) FROM hourly_consensus").fetchone()[0]

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "forecast_members_transient")
        self.assertEqual(payload["rows"], 1)
        self.assertEqual(persisted, 0)
        self.assertFalse(payload["points"][0]["hourly_consensus"])
        self.assertTrue(payload["points"][0]["transient"])
        self.assertAlmostEqual(payload["points"][0]["best"], 86.0)

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
        day = next(item for item in payload[0]["dates"] if item["target_date"] == "2026-06-29")
        self.assertEqual(day["modules"]["hourly_temperature"]["rows"], 1)
        self.assertEqual(day["modules"]["metar"]["rows"], 1)
        self.assertEqual(day["modules"]["forecast"]["rows"], 0)

    def test_weather_city_series_reads_db_hourly_consensus_without_market_snapshots(self):
        db_path = test_db_path("weather_city_series_hourly_db")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            upsert_hourly_consensus({
                "city": "chicago",
                "city_name": "Chicago",
                "target_date": "2026-07-03",
                "local_hour": "13:00",
                "valid_time": "2026-07-03T13:00:00-05:00",
                "station_id": "KORD",
                "forecast_temp": 90.0,
                "forecast_source": "openmeteo_ecmwf",
                "consensus_version": "hourly-consensus-v2",
                "build_status": "built",
                "source_count": 1,
            })
            upsert_hourly_consensus({
                "city": "chicago",
                "city_name": "Chicago",
                "target_date": "2026-07-03",
                "local_hour": "14:00",
                "valid_time": "2026-07-03T14:00:00-05:00",
                "station_id": "KORD",
                "forecast_temp": 92.0,
                "forecast_source": "openmeteo_ecmwf",
                "consensus_version": "hourly-consensus-v2",
                "build_status": "built",
                "source_count": 1,
            })
            rows = _build_weather_city_series([])
            payload = _build_city_evidence_payload(rows, [], [])

        chicago = next(row for row in rows if row["city_key"] == "chicago")
        self.assertEqual(chicago["hourly_count"], 2)
        self.assertEqual(chicago["forecast_count"], 2)
        self.assertAlmostEqual(chicago["latest_best"], 92.0)
        day = next(day for day in payload[0]["dates"] if day["target_date"] == "2026-07-03")
        self.assertEqual(day["modules"]["hourly_temperature"]["rows"], 2)
        self.assertEqual(day["modules"]["forecast"]["rows"], 2)

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
        self.assertEqual(audit["leakage_flags"]["forecast_lead_negative"], 1)
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

    def test_truth_iem_asos_daily_parser_persists_daily_and_hourly(self):
        from weatherbot_v3.truth.iem_asos import parse_iem_asos_daily_csv, persist_iem_asos_daily

        path = test_db_path("truth_iem_asos_daily")
        init_v3_db(path)
        csv_text = "\n".join([
            "station,valid,tmpf,metar",
            "ZBAA,2026-06-27 00:00,77.0,METAR ZBAA 270000Z 00000KT 9999 SKC 25/12 Q1010",
            "ZBAA,2026-06-27 14:00,95.0,METAR ZBAA 271400Z 00000KT 9999 SKC 35/12 Q1008",
            "ZBAA,2026-06-27 23:00,89.6,METAR ZBAA 272300Z 00000KT 9999 SKC 32/12 Q1007",
        ])
        result = parse_iem_asos_daily_csv(
            csv_text,
            "ZBAA",
            "2026-06-27",
            "Asia/Shanghai",
            source_url="https://mesonet.example/asos.csv",
        )

        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["high_c"], 35.0, places=1)
        self.assertAlmostEqual(result["all_hourly"][0]["temp_c"], 25.0, places=1)
        self.assertEqual(result["settlement_truth_type"], "iem_asos_approximation")
        persist_iem_asos_daily(result, path=path)

        with connect(path) as conn:
            daily = conn.execute("SELECT high_c, obs_count FROM truth_iem_daily WHERE icao='ZBAA'").fetchone()
            hourly_count = conn.execute("SELECT COUNT(*) FROM truth_iem_hourly WHERE icao='ZBAA'").fetchone()[0]
        self.assertAlmostEqual(float(daily["high_c"]), 35.0, places=1)
        self.assertEqual(int(daily["obs_count"]), 3)
        self.assertEqual(hourly_count, 3)

    def test_bias_truth_priority_uses_wu_and_hko_before_iem(self):
        from weatherbot_v3.bias import _truth_by_date

        path = test_db_path("bias_truth_priority")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        init_v3_db(path)
        now = "2026-07-11T00:00:00+00:00"
        with connect(path) as conn:
            conn.execute(
                "INSERT INTO truth_iem_daily (truth_key, icao, date_local, high_c, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("iem:KORD:2026-07-01", "KORD", "2026-07-01", 29.0, now, now),
            )
            conn.execute(
                "INSERT INTO truth_wunderground_daily (truth_key, icao, date_local, high_c, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("wu:KORD:2026-07-01", "KORD", "2026-07-01", 30.0, now, now),
            )
            conn.execute(
                "INSERT INTO truth_iem_daily (truth_key, icao, date_local, high_c, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("iem:VHHH:2026-07-01", "VHHH", "2026-07-01", 31.0, now, now),
            )
            conn.execute(
                "INSERT INTO truth_hko_daily (truth_key, date_local, high_c, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                ("hko:2026-07-01", "2026-07-01", 32.0, now, now),
            )

        chicago = _truth_by_date("KORD", "chicago", path)
        hong_kong = _truth_by_date("VHHH", "hong-kong", path)

        self.assertEqual(chicago["2026-07-01"]["high_c"], 30.0)
        self.assertEqual(chicago["2026-07-01"]["basis"], "wunderground_daily")
        self.assertEqual(hong_kong["2026-07-01"]["high_c"], 32.0)
        self.assertEqual(hong_kong["2026-07-01"]["basis"], "hong_kong_observatory_daily_extract")

    def test_bias_training_excludes_target_day_leakage_and_reports_real_mae(self):
        from weatherbot_v3.bias import train_bias_table

        path = test_db_path("bias_leakage")
        output = path.with_name(f"{path.stem}-bias.json")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        self.addCleanup(lambda: output.unlink(missing_ok=True))
        init_v3_db(path)
        now = "2026-07-11T00:00:00+00:00"
        with connect(path) as conn:
            conn.execute(
                "INSERT INTO truth_wunderground_daily (truth_key, icao, date_local, high_c, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("wu:KORD:2026-07-01", "KORD", "2026-07-01", 30.0, now, now),
            )
            for run_key, retrieved_at, mean_high in (
                ("pre-day", "2026-06-30T12:00:00+00:00", 32.0),
                ("leaked", "2026-07-01T12:00:00+00:00", 40.0),
            ):
                conn.execute(
                    """
                    INSERT INTO forecast_runs (
                        run_key, city, target_date, source, model, retrieved_at,
                        available_at, availability_basis, valid_at, horizon, lead_hours,
                        timezone, unit, mean_high, training_eligible, parse_status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_key,
                        "chicago",
                        "2026-07-01",
                        "openmeteo_ecmwf_ifs025",
                        "ecmwf_ifs025",
                        retrieved_at,
                        retrieved_at,
                        "retrieved_at",
                        "2026-07-01T20:00:00+00:00",
                        "D+0" if run_key == "leaked" else "D+1",
                        8 if run_key == "leaked" else 32,
                        "America/Chicago",
                        "C",
                        mean_high,
                        1,
                        "parsed",
                        now,
                    ),
                )

        payload = train_bias_table(cities=["chicago"], days=30, path=path, output_path=output)
        row = next(item for item in payload["rows"] if item["model"] == "ecmwf")

        self.assertEqual(row["sample_count"], 1)
        self.assertEqual(row["leakage_excluded_rows"], 1)
        self.assertEqual(row["truth_basis_counts"], {"wunderground_daily": 1})
        self.assertAlmostEqual(row["additive_bias_c"], 2.0)
        self.assertAlmostEqual(row["raw_mae_c"], 2.0)
        self.assertAlmostEqual(row["mae_c"], 0.0)
        self.assertFalse(row["runtime_eligible"])

    def test_bias_training_prefers_fixed_previous_day1_over_later_current_snapshot(self):
        from weatherbot_v3.bias import train_bias_table

        path = test_db_path("bias_previous_day1")
        output = path.with_name(f"{path.stem}-bias.json")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        self.addCleanup(lambda: output.unlink(missing_ok=True))
        init_v3_db(path)
        now = "2026-07-11T00:00:00+00:00"
        with connect(path) as conn:
            conn.execute(
                "INSERT INTO truth_wunderground_daily (truth_key, icao, date_local, high_c, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("wu:KORD:2026-07-01", "KORD", "2026-07-01", 30.0, now, now),
            )
            runs = (
                ("previous-day1", "openmeteo_previous_ecmwf_ifs025_day1", "2026-06-30T05:00:00+00:00", "2026-07-11T00:00:00+00:00", "D+1", 31.0),
                ("later-current", "openmeteo_ecmwf_ifs025", "", "2026-07-01T04:00:00+00:00", "D+0", 35.0),
            )
            for run_key, source, run_at, retrieved_at, horizon, mean_high in runs:
                conn.execute(
                    """
                    INSERT INTO forecast_runs (
                        run_key, city, target_date, source, model, run_at, retrieved_at,
                        available_at, availability_basis, valid_at, lead_hours, timezone,
                        horizon, unit, mean_high, training_eligible, parse_status, quality_flags, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_key, "chicago", "2026-07-01", source, "ecmwf_ifs025",
                        run_at, retrieved_at,
                        run_at or retrieved_at,
                        "archive_run_at" if run_at else "retrieved_at",
                        "2026-07-01T20:00:00+00:00",
                        39 if run_at else 16,
                        "America/Chicago",
                        horizon, "C", mean_high, 1, "parsed",
                        '["trusted_forecast_archive"]' if run_at else "[]",
                        now,
                    ),
                )

        payload = train_bias_table(cities=["chicago"], days=30, path=path, output_path=output)
        row = next(item for item in payload["rows"] if item["model"] == "ecmwf")

        self.assertEqual(row["sample_count"], 1)
        self.assertEqual(row["archived_previous_day1_samples"], 1)
        self.assertAlmostEqual(row["additive_bias_c"], 1.0)

    def test_bias_training_replay_excludes_truth_on_or_after_target_date(self):
        from weatherbot_v3.bias import train_bias_table

        path = test_db_path("bias_replay_cutoff")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        init_v3_db(path)
        now = "2026-07-11T00:00:00+00:00"
        with connect(path) as conn:
            for target_date, truth, forecast in (
                ("2026-06-30", 30.0, 31.0),
                ("2026-07-01", 30.0, 40.0),
            ):
                conn.execute(
                    "INSERT INTO truth_wunderground_daily (truth_key, icao, date_local, high_c, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (f"wu:KORD:{target_date}", "KORD", target_date, truth, now, now),
                )
                conn.execute(
                    """
                    INSERT INTO forecast_runs (
                        run_key, city, target_date, source, model, run_at, retrieved_at,
                        available_at, availability_basis, valid_at, lead_hours, timezone,
                        horizon, unit, mean_high, training_eligible, parse_status, quality_flags, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"run:{target_date}", "chicago", target_date,
                        "openmeteo_previous_ecmwf_ifs025_day1", "ecmwf_ifs025",
                        (
                            datetime.fromisoformat(target_date)
                            .replace(tzinfo=timezone.utc)
                            .__sub__(timedelta(hours=19))
                            .isoformat()
                        ),
                        now,
                        (
                            datetime.fromisoformat(target_date)
                            .replace(tzinfo=timezone.utc)
                            .__sub__(timedelta(hours=19))
                            .isoformat()
                        ),
                        "archive_run_at",
                        f"{target_date}T20:00:00+00:00",
                        39,
                        "America/Chicago",
                        "D+1", "C", forecast,
                        1, "parsed", '["trusted_forecast_archive"]', now,
                    ),
                )

        payload = train_bias_table(
            cities=["chicago"],
            days=30,
            path=path,
            as_of_date_exclusive="2026-07-01",
            persist=False,
        )
        row = next(item for item in payload["rows"] if item["model"] == "ecmwf")

        self.assertEqual(row["sample_dates"], ["2026-06-30"])
        self.assertAlmostEqual(row["additive_bias_c"], 1.0)
        self.assertEqual(row["location_version"], 1)
        self.assertEqual(row["location_mismatch_excluded_rows"], 0)
        self.assertEqual(payload["training_policy"]["as_of_date_exclusive"], "2026-07-01")

    def test_low_sample_bias_mae_cannot_change_runtime_weights(self):
        from weatherbot_v3.forecasts.ensemble import _bias_for, _mae_for

        low_sample = [{"icao": "KORD", "model": "ecmwf", "sample_count": 7, "mae_7d_c": 0.2}]
        mature = [{"icao": "KORD", "model": "ecmwf", "sample_count": 20, "mae_7d_c": 0.4}]

        self.assertIsNone(_mae_for(low_sample, "KORD", "ecmwf"))
        self.assertAlmostEqual(_mae_for(mature, "KORD", "ecmwf"), 0.4)
        stale_tokyo = [{"icao": "RJTT", "model": "ecmwf", "sample_count": 30, "additive_bias_c": -1.5}]
        self.assertEqual(_bias_for(stale_tokyo, "RJTT", "ecmwf", profile=SETTLEMENT_REGISTRY["tokyo"]), (0.0, 0))

    def test_truth_hko_daily_extract_parser_handles_hko_json_payload(self):
        from weatherbot_v3.truth.hko import parse_hko_daily_extract, persist_hko_daily

        path = test_db_path("truth_hko_daily")
        init_v3_db(path)
        payload = {
            "stn": {
                "data": [{
                    "month": 7,
                    "dayData": [
                        ["03", "1007.5", "32.1", "29.8", "27.9", "26.0", "82", "88", "0.0"],
                        ["04", "1008.0", "33.5", "30.3", "28.2", "26.4", "80", "87", "Trace"],
                    ],
                }]
            }
        }

        result = parse_hko_daily_extract(json.dumps(payload), "2026-07-04", source_url="https://hko.example/daily.json")

        self.assertTrue(result["ok"])
        self.assertEqual(result["settlement_truth_type"], "hong_kong_observatory_daily_extract")
        self.assertAlmostEqual(result["high_c"], 33.5)
        persist_hko_daily(result, path=path)
        with connect(path) as conn:
            row = conn.execute("SELECT high_c, parser_version FROM truth_hko_daily WHERE date_local='2026-07-04'").fetchone()
        self.assertAlmostEqual(float(row["high_c"]), 33.5)
        self.assertEqual(row["parser_version"], "truth-hko-daily-extract-v1")

    def test_hko_truth_batch_fetches_each_month_once_and_persists_compact_raw_rows(self):
        from weatherbot_v3.truth.hko import fetch_hko_daily_extract_many

        path = test_db_path("truth_hko_month_batch")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        payload = {
            "stn": {
                "data": [{
                    "month": 6,
                    "dayData": [
                        ["28", "1005.3", "30.0", "27.9", "26.1"],
                        ["29", "1006.9", "31.6", "28.7", "26.3"],
                        ["30", "1008.2", "32.1", "28.7", "26.4"],
                    ],
                }]
            }
        }

        class CountingSession:
            def __init__(self):
                self.calls = 0

            def get(self, *args, **kwargs):
                self.calls += 1
                return FakeHTTPResponse(payload, url="https://hko.example/dailyExtract_202606.xml")

        session = CountingSession()
        results = fetch_hko_daily_extract_many(
            ["2026-06-28", "2026-06-29", "2026-06-30"],
            session=session,
            path=path,
        )

        self.assertEqual(session.calls, 1)
        self.assertEqual([row["high_c"] for row in results], [30.0, 31.6, 32.1])
        with connect(path) as conn:
            rows = conn.execute("SELECT raw_json FROM truth_hko_daily ORDER BY date_local").fetchall()
        self.assertEqual(len(rows), 3)
        for row in rows:
            raw = json.loads(row["raw_json"])
            self.assertIn("row", raw)
            self.assertIn("payload_sha256", raw)
            self.assertNotIn("payload", raw)

    def test_truth_delta_rebuild_tracks_hko_against_vhhh_observation_station(self):
        from weatherbot_v3.truth.delta import rebuild_truth_delta_from_tables

        path = test_db_path("truth_delta_hko_vhhh")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        init_v3_db(path)
        now = "2026-07-10T00:00:00+00:00"
        with connect(path) as conn:
            conn.execute(
                """
                INSERT INTO stations (
                    city_key, city_name, station_id, station_name, timezone, unit,
                    settlement_station_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("hong-kong", "Hong Kong", "VHHH", "Hong Kong International", "Asia/Hong_Kong", "C", "HKO", now),
            )
            conn.execute(
                """
                INSERT INTO truth_iem_daily (
                    truth_key, icao, date_local, timezone, high_c, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("iem_asos:VHHH:2026-07-04", "VHHH", "2026-07-04", "Asia/Hong_Kong", 31.0, now, now),
            )
            conn.execute(
                """
                INSERT INTO truth_hko_daily (
                    truth_key, date_local, high_c, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                ("hko:2026-07-04", "2026-07-04", 32.2, now, now),
            )

        result = rebuild_truth_delta_from_tables(path=path)
        self.assertTrue(result["ok"])
        with connect(path) as conn:
            row = conn.execute(
                """
                SELECT city, iem_high_c, hko_high_c, delta_hko_minus_iem, delta_wu_minus_iem
                FROM truth_delta_audit
                WHERE icao = 'VHHH' AND date_local = '2026-07-04'
                """
            ).fetchone()
        self.assertEqual(row["city"], "hong-kong")
        self.assertAlmostEqual(float(row["iem_high_c"]), 31.0)
        self.assertAlmostEqual(float(row["hko_high_c"]), 32.2)
        self.assertAlmostEqual(float(row["delta_hko_minus_iem"]), 1.2)
        self.assertIsNone(row["delta_wu_minus_iem"])

    def test_iem_truth_fetch_uses_hong_kong_observation_station_not_hko_settlement_authority(self):
        station_row = {
            "city_key": "hong-kong",
            "station_id": "VHHH",
            "settlement_station_id": "HKO",
            "timezone": "Asia/Hong_Kong",
            "settlement_timezone": "Asia/Hong_Kong",
        }
        captured: list[tuple[str, str, str]] = []

        def fake_fetch(station, start_target, end_target, timezone_name, *, persist):
            captured.append((station, start_target, end_target, timezone_name))
            return [{"ok": True, "icao": station, "date_local": start_target}]

        with patch("weatherbot_v3.cli.sync_station_registry"), patch(
            "weatherbot_v3.cli.list_stations", return_value=[station_row]
        ), patch("weatherbot_v3.truth.iem_asos.fetch_iem_asos_range", side_effect=fake_fetch):
            result = run_iem_asos_truth_fetch("hong-kong", target_date="2026-07-04", dry_run=True)

        self.assertTrue(result["ok"])
        self.assertEqual(captured, [("VHHH", "2026-07-04", "2026-07-04", "Asia/Hong_Kong")])

    def test_iem_truth_range_fetches_station_once_and_splits_local_days(self):
        from weatherbot_v3.truth.iem_asos import fetch_iem_asos_range

        path = test_db_path("truth_iem_range")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        csv_text = "\n".join([
            "station,valid,tmpf,metar",
            "VHHH,2026-06-28 00:00,86.0,VHHH 271600Z 20008KT 9999 FEW010 30/25 Q1005",
            "VHHH,2026-06-28 12:00,80.6,VHHH 280400Z 18006KT 1500 SHRA 27/25 Q1006",
            "VHHH,2026-06-29 00:00,84.2,VHHH 281600Z 18005KT 9999 FEW010 29/25 Q1005",
            "VHHH,2026-06-29 13:00,89.6,VHHH 290500Z 22008KT 9999 FEW015 32/25 Q1004",
        ])

        class CountingSession:
            def __init__(self):
                self.calls = 0

            def get(self, *args, **kwargs):
                self.calls += 1
                return FakeHTTPResponse(csv_text, url="https://mesonet.example/asos.csv")

        session = CountingSession()
        results = fetch_iem_asos_range(
            "VHHH",
            "2026-06-28",
            "2026-06-29",
            "Asia/Hong_Kong",
            session=session,
            path=path,
        )

        self.assertEqual(session.calls, 1)
        self.assertEqual([result["high_c"] for result in results], [30.0, 32.0])
        with connect(path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM truth_iem_daily").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM truth_iem_hourly").fetchone()[0], 4)
            logs = conn.execute(
                "SELECT target_date, status, details_json FROM data_fetch_logs WHERE source='iem_asos'"
            ).fetchall()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["target_date"], "2026-06-28..2026-06-29")
        self.assertEqual(logs[0]["status"], "OK")
        self.assertEqual(json.loads(logs[0]["details_json"])["ok_days"], 2)

    def test_wunderground_truth_cli_passes_force_rebuild(self):
        from io import StringIO
        from weatherbot_v3 import cli

        output = StringIO()
        with patch("weatherbot_v3.cli.run_wunderground_truth_fetch", return_value={"ok": True}) as fetch:
            with patch.object(
                cli.sys,
                "argv",
                ["weatherbot_v3.cli", "wunderground-truth-fetch", "--city", "chicago", "--force-rebuild"],
            ), patch.object(cli.sys, "stdout", output):
                cli.main()

        self.assertTrue(fetch.call_args.kwargs["force_rebuild"])

    def test_iem_truth_cli_does_not_forward_wunderground_force_rebuild_option(self):
        from io import StringIO
        from weatherbot_v3 import cli

        output = StringIO()
        with patch("weatherbot_v3.cli.run_iem_asos_truth_fetch", return_value={"ok": True}) as fetch:
            with patch.object(
                cli.sys,
                "argv",
                ["weatherbot_v3.cli", "iem-asos-fetch", "--city", "hong-kong", "--force-rebuild"],
            ), patch.object(cli.sys, "stdout", output):
                cli.main()

        self.assertNotIn("force_rebuild", fetch.call_args.kwargs)

    def test_truth_wunderground_returns_structured_skip_when_no_endpoint_has_daily_high(self):
        from weatherbot_v3.truth.wunderground import fetch_wunderground_daily_result

        class EmptyResponse:
            status_code = 200
            text = "{}"
            url = "https://api.weather.com/empty"

        class EmptySession:
            def get(self, *args, **kwargs):
                return EmptyResponse()

        with patch.dict(os.environ, {"WEATHER_COM_API_KEY": "", "WUNDERGROUND_API_KEY": ""}, clear=False):
            result = fetch_wunderground_daily_result("ZBAA", "2026-06-27", country_code="CN", session=EmptySession())

        self.assertFalse(result["ok"])
        self.assertEqual(result["icao"], "ZBAA")
        self.assertIn("no_daily_high_in_payload", result["skip_reasons"])
        self.assertTrue(result["attempts"])

    def test_gamma_celsius_bucket_parser_supports_dynamic_asian_bucket_shapes(self):
        from weatherbot_v3.polymarket_gamma import parse_celsius_bucket_boundary

        labels_11 = [
            "Will the highest temperature in Shanghai be 27°C or lower on July 5?",
            *[f"Will the highest temperature in Shanghai be {value}°C on July 5?" for value in range(28, 37)],
            "Will the highest temperature in Shanghai be 37°C or higher on July 5?",
        ]
        parsed_11 = [parse_celsius_bucket_boundary(label) for label in labels_11]
        self.assertEqual(len(parsed_11), 11)
        self.assertEqual(parsed_11[0]["lower_c"], None)
        self.assertEqual(parsed_11[0]["upper_c"], 28)
        self.assertTrue(parsed_11[0]["is_tail"])
        self.assertEqual(parsed_11[1]["lower_c"], 28)
        self.assertEqual(parsed_11[1]["upper_c"], 29)
        self.assertEqual(parsed_11[-1]["lower_c"], 37)
        self.assertEqual(parsed_11[-1]["upper_c"], None)
        self.assertTrue(parsed_11[-1]["is_tail"])

        labels_6 = [
            "Will the highest temperature in Beijing be 23°C or below on July 5?",
            "Will the highest temperature in Beijing be 24°C on July 5?",
            "Will the highest temperature in Beijing be 25°C on July 5?",
            "Will the highest temperature in Beijing be 26°C on July 5?",
            "Will the highest temperature in Beijing be 27°C on July 5?",
            "Will the highest temperature in Beijing be 28°C or above on July 5?",
        ]
        parsed_6 = [parse_celsius_bucket_boundary(label) for label in labels_6]
        self.assertEqual(len(parsed_6), 6)
        self.assertEqual(parsed_6[0]["upper_c"], 24)
        self.assertEqual(parsed_6[-1]["lower_c"], 28)
        self.assertEqual(
            parse_celsius_bucket_boundary("highest-temperature-in-shanghai-on-july-6-2026-37corhigher")["upper_c"],
            None,
        )
        self.assertEqual(
            parse_celsius_bucket_boundary("highest-temperature-in-shanghai-on-july-6-2026-27corlower")["upper_c"],
            28,
        )

    def test_gamma_structured_sync_persists_events_markets_and_orderbooks(self):
        from weatherbot_v3.polymarket_gamma import sync_asian_weather_markets

        path = test_db_path("gamma_structured_sync")
        init_v3_db(path)
        sync_station_registry(path)
        event_payload = {
            "id": "event-shanghai-20260706",
            "slug": "highest-temperature-in-shanghai-on-july-6-2026",
            "active": True,
            "closed": False,
            "volume24hr": 1234.5,
            "openInterest": 678.9,
            "resolutionSource": "https://www.wunderground.com/history/daily/cn/shanghai/ZSPD/date/2026-7-6",
            "markets": [
                {
                    "id": "market-shanghai-28",
                    "slug": "will-the-highest-temperature-in-shanghai-be-28c-on-july-6",
                    "question": "Will the highest temperature in Shanghai be 28°C on July 6?",
                    "active": True,
                    "closed": False,
                    "outcomePrices": json.dumps(["0.21", "0.79"]),
                    "clobTokenIds": json.dumps(["token-yes-28", "token-no-28"]),
                    "orderMinSize": "5",
                    "orderPriceMinTickSize": "0.01",
                    "enableOrderBook": True,
                },
                {
                    "id": "market-shanghai-29",
                    "slug": "will-the-highest-temperature-in-shanghai-be-29c-on-july-6",
                    "question": "Will the highest temperature in Shanghai be 29°C on July 6?",
                    "active": True,
                    "closed": False,
                    "outcomePrices": json.dumps(["0.32", "0.68"]),
                    "clobTokenIds": json.dumps(["token-yes-29", "token-no-29"]),
                    "orderMinSize": "5",
                    "orderPriceMinTickSize": "0.01",
                    "enableOrderBook": True,
                },
            ],
        }
        session = FakePolymarketSession(
            event_payload,
            {
                "token-yes-28": {"bids": [{"price": "0.20", "size": "10"}], "asks": [{"price": "0.22", "size": "11"}]},
                "token-yes-29": {"bids": [{"price": "0.31", "size": "9"}], "asks": [{"price": "0.33", "size": "12"}]},
            },
        )

        result = sync_asian_weather_markets(
            cities=["shanghai"],
            target_dates=["2026-07-06"],
            fetch_orderbooks=True,
            session=session,
            path=path,
        )

        self.assertTrue(result["events_seen"])
        self.assertEqual(result["events_stored"], 1)
        self.assertEqual(result["markets_stored"], 2)
        self.assertEqual(result["orderbooks_stored"], 2)
        self.assertEqual(sum(1 for call in session.calls if str(call["url"]).endswith("/books")), 1)
        with connect(path) as conn:
            event_count = conn.execute("SELECT COUNT(*) FROM polymarket_events").fetchone()[0]
            market = conn.execute("SELECT bucket_lower_c, bucket_upper_c FROM polymarket_markets WHERE market_id='market-shanghai-28'").fetchone()
            book_count = conn.execute("SELECT COUNT(*) FROM polymarket_orderbook").fetchone()[0]
        self.assertEqual(event_count, 1)
        self.assertEqual(float(market["bucket_lower_c"]), 28.0)
        self.assertEqual(float(market["bucket_upper_c"]), 29.0)
        self.assertEqual(book_count, 2)


if __name__ == "__main__":
    unittest.main()
