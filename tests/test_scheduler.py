from tests import ensure_test_environment

ensure_test_environment()

import asyncio
import os
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from weatherbot_v3.db import connect, init_v3_db, list_data_fetch_logs
from weatherbot_v3.metar import fetch_recent_hours
from weatherbot_v3.scheduler import (
    FORECAST_INTERVAL_SECONDS,
    HISTORICAL_INTERVAL_SECONDS,
    WeatherBotScheduler,
    _bias_refresh_due,
    _compact_city_payload,
    _ensemble_due_by_city,
    _remaining_cycle_delay,
    _tiered_refresh_rows,
)
from weatherbot_v3.stations import list_stations, set_station_enabled, sync_station_registry


TEST_DB_DIR = Path(__file__).resolve().parents[1] / ".tmp-tests"


def test_db_path(name: str) -> Path:
    TEST_DB_DIR.mkdir(exist_ok=True)
    path = TEST_DB_DIR / f"{name}.db"
    path.unlink(missing_ok=True)
    return path


def configure_enabled_cities(cities: list[str]) -> None:
    sync_station_registry()
    for row in list_stations():
        set_station_enabled(str(row["city_key"]), False)
    for city in cities:
        set_station_enabled(city, True)


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_forecast_and_historical_default_to_ten_minute_cadence(self):
        self.assertEqual(FORECAST_INTERVAL_SECONDS, 600)
        self.assertEqual(HISTORICAL_INTERVAL_SECONDS, 600)

    async def test_cycle_delay_is_start_to_start(self):
        self.assertEqual(_remaining_cycle_delay(600, 277), 323)
        self.assertEqual(_remaining_cycle_delay(60, 92), 60)

    async def test_poll_loop_reports_fixed_cadence_next_run(self):
        scheduler = WeatherBotScheduler(city_concurrency=1)
        state = scheduler.pollers["forecast_poller"]
        state.interval_seconds = 10
        state.initial_delay_seconds = 0

        async def fake_run_once(_poller_key: str):
            await asyncio.sleep(0.02)
            scheduler.stop_event.set()
            return {"ok": True}

        scheduler.run_once = fake_run_once  # type: ignore[method-assign]
        await scheduler._poll_loop("forecast_poller")

        next_run = state.next_run_at
        self.assertIsNotNone(next_run)
        scheduled = datetime.fromisoformat(str(next_run))
        remaining = (scheduled - datetime.now(timezone.utc)).total_seconds()
        self.assertGreater(remaining, 8.5)
        self.assertLessEqual(remaining, 10.0)

    async def test_background_poller_does_not_starve_critical_refresh(self):
        scheduler = WeatherBotScheduler(
            city_concurrency=2,
            poller_concurrency=1,
            critical_poller_concurrency=1,
            background_poller_concurrency=1,
        )
        background_started = asyncio.Event()
        release_background = asyncio.Event()
        critical_completed = asyncio.Event()

        async def fake_background():
            background_started.set()
            await release_background.wait()
            return {"ok": True, "cities": 0, "results": []}

        async def fake_critical():
            critical_completed.set()
            return {"ok": True, "cities": 0, "results": []}

        scheduler._run_nwp_poller = fake_background  # type: ignore[method-assign]
        scheduler._run_metar_poller = fake_critical  # type: ignore[method-assign]
        with patch("weatherbot_v3.scheduler.log_data_fetch"):
            background_task = asyncio.create_task(scheduler.run_once("nwp_poller"))
            await asyncio.wait_for(background_started.wait(), timeout=1)
            await asyncio.wait_for(scheduler.run_once("metar_poller"), timeout=1)
            self.assertTrue(critical_completed.is_set())
            self.assertFalse(background_task.done())
            release_background.set()
            await background_task

        status = scheduler.status()
        self.assertEqual(status["poller_concurrency"], 1)
        self.assertEqual(status["critical_poller_concurrency"], 1)
        self.assertEqual(status["background_poller_concurrency"], 1)
        self.assertEqual(status["active_pollers"], [])
        self.assertEqual(status["poller_groups"]["metar_poller"], "critical")
        self.assertEqual(status["poller_groups"]["nwp_poller"], "background")

    async def test_restart_reapplies_each_pollers_initial_delay(self):
        scheduler = WeatherBotScheduler(city_concurrency=1)
        state = scheduler.pollers["forecast_poller"]
        state.initial_delay_seconds = 0.04
        state.run_count = 3

        async def fake_run_once(_poller_key: str):
            scheduler.stop_event.set()
            return {"ok": True}

        scheduler.run_once = fake_run_once  # type: ignore[method-assign]
        started = time.perf_counter()
        await scheduler._poll_loop("forecast_poller")

        self.assertGreaterEqual(time.perf_counter() - started, 0.03)

    async def test_scheduler_compacts_collector_payload_before_retention(self):
        compact = _compact_city_payload({
            "ok": True,
            "city": "atlanta",
            "weathercom": {
                "ok": True,
                "rows_upserted": 24,
                "raw_response": "x" * 1_000_000,
                "results": [{"hourly": list(range(1000))}],
            },
        })

        self.assertEqual(compact["city"], "atlanta")
        self.assertEqual(compact["rows_upserted"], 24)
        self.assertNotIn("raw_response", compact)
        self.assertLess(len(str(compact)), 1000)

    async def test_tiered_refresh_keeps_active_markets_on_fast_cycle(self):
        rows = [
            {
                "city_key": "chicago",
                "raw_json": {"latest_market_probe": {"status": "active_market"}},
            },
            {
                "city_key": "ankara",
                "raw_json": {"latest_market_probe": {"status": "no_active_market"}},
            },
            {"city_key": "london", "raw_json": "{}"},
        ]

        full_rows, full_meta = _tiered_refresh_rows(rows, run_count=0, baseline_multiplier=3)
        fast_rows, fast_meta = _tiered_refresh_rows(rows, run_count=1, baseline_multiplier=3)

        self.assertEqual(len(full_rows), 3)
        self.assertEqual(full_meta["refresh_scope"], "full_watchlist")
        self.assertEqual([row["city_key"] for row in fast_rows], ["chicago"])
        self.assertEqual(fast_meta["refresh_scope"], "active_markets")
        self.assertEqual(fast_meta["deferred_cities"], ["ankara", "london"])

    async def test_forecast_poller_exposes_auditable_refresh_scope(self):
        rows = [
            {
                "city_key": "chicago",
                "station_id": "KORD",
                "raw_json": {"latest_market_probe": {"active_market": True}},
            },
            {
                "city_key": "ankara",
                "station_id": "LTAC",
                "raw_json": {"latest_market_probe": {"active_market": False}},
            },
        ]
        scheduler = WeatherBotScheduler(city_concurrency=1)
        scheduler.pollers["forecast_poller"].run_count = 1
        with patch("weatherbot_v3.scheduler._collection_rows", return_value=rows), patch(
            "weatherbot_v3.scheduler.run_weathercom_fetch",
            return_value={"ok": True, "rows_upserted": 24},
        ) as fetch:
            result = await scheduler.run_once("forecast_poller")

        self.assertTrue(result["ok"])
        self.assertEqual(fetch.call_count, 1)
        self.assertFalse(fetch.call_args.kwargs["refresh_readiness"])
        self.assertEqual(result["refresh_scope"], "active_markets")
        self.assertEqual(result["deferred_cities"], ["ankara"])
        status = scheduler.status()["pollers"]["forecast_poller"]
        self.assertEqual(status["last_result"]["refresh_scope"], "active_markets")

    async def test_nwp_poller_skips_per_city_readiness_refresh(self):
        rows = [{"city_key": "chicago", "station_id": "KORD"}]
        with patch("weatherbot_v3.scheduler._enabled_rows", return_value=rows), patch(
            "weatherbot_v3.scheduler._ensemble_due_by_city", return_value={"chicago": False}
        ), patch(
            "weatherbot_v3.scheduler.run_openmeteo_fetch",
            return_value={"ok": True, "runs_upserted": 6},
        ) as fetch:
            result = await WeatherBotScheduler(city_concurrency=1)._run_nwp_poller()

        self.assertTrue(result["ok"])
        fetch.assert_called_once()
        self.assertFalse(fetch.call_args.kwargs["refresh_readiness"])

    async def test_nwp_poller_persists_real_gfs_ensemble_on_bounded_cadence(self):
        rows = [{"city_key": "chicago", "station_id": "KORD", "tier": 1}]
        scheduler = WeatherBotScheduler(city_concurrency=1)
        with patch("weatherbot_v3.scheduler._collection_rows", return_value=rows), patch(
            "weatherbot_v3.scheduler._active_market_city_keys", return_value={"chicago"}
        ), patch(
            "weatherbot_v3.scheduler._ensemble_due_by_city", return_value={"chicago": True}
        ), patch(
            "weatherbot_v3.scheduler.run_openmeteo_fetch",
            side_effect=[
                {"ok": True, "runs_upserted": 6},
                {"ok": True, "runs_upserted": 3, "members_upserted": 93},
            ],
        ) as fetch:
            result = await scheduler._run_nwp_poller()

        self.assertTrue(result["ok"])
        self.assertTrue(result["ensemble_due"])
        self.assertEqual(fetch.call_count, 2)
        self.assertFalse(fetch.call_args_list[0].kwargs["ensemble"])
        self.assertTrue(fetch.call_args_list[1].kwargs["ensemble"])
        self.assertEqual(fetch.call_args_list[1].kwargs["models_arg"], "gfs_seamless")
        self.assertFalse(fetch.call_args_list[1].kwargs["refresh_readiness"])

    def test_ensemble_due_uses_persisted_age_across_scheduler_restarts(self):
        path = test_db_path("scheduler_ensemble_age")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        init_v3_db(path)
        now = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
        with connect(path) as conn:
            conn.execute(
                """
                INSERT INTO forecast_runs (
                    run_key, city, target_date, source, model, retrieved_at,
                    available_at, unit, mean_high, training_eligible, parse_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "ensemble:fresh",
                    "chicago",
                    "2026-07-25",
                    "openmeteo_ensemble_gfs_seamless",
                    "gfs_seamless",
                    (now - timedelta(hours=2)).isoformat(),
                    (now - timedelta(hours=2)).isoformat(),
                    "C",
                    28.0,
                    1,
                    "parsed",
                    now.isoformat(),
                ),
            )

        rows = [
            {"city_key": "chicago"},
            {"city_key": "shanghai"},
        ]
        due = _ensemble_due_by_city(
            rows,
            now=now,
            max_age_seconds=6 * 3600,
            path=path,
        )

        self.assertFalse(due["chicago"])
        self.assertTrue(due["shanghai"])

    async def test_pws_poller_skips_per_city_readiness_refresh(self):
        rows = [{"city_key": "chicago", "station_id": "KORD"}]
        with patch.dict(os.environ, {"WUNDERGROUND_API_KEY": "test-pws-key"}, clear=False), patch(
            "weatherbot_v3.scheduler._enabled_rows", return_value=rows
        ), patch(
            "weatherbot_v3.scheduler.run_pws_fetch",
            return_value={"ok": True, "rows_upserted": 1},
        ) as fetch:
            result = await WeatherBotScheduler(city_concurrency=1)._run_pws_poller()

        self.assertTrue(result["ok"])
        fetch.assert_called_once()
        self.assertFalse(fetch.call_args.kwargs["refresh_readiness"])

    async def test_historical_poller_keeps_hong_kong_as_display_evidence(self):
        rows = [
            {"city_key": "shanghai", "station_id": "ZSPD", "timezone": "Asia/Shanghai"},
            {"city_key": "hong-kong", "station_id": "VHHH", "timezone": "Asia/Hong_Kong"},
        ]
        seen: list[str] = []
        sync_flags: list[bool] = []
        active = 0
        max_active = 0
        lock = threading.Lock()

        def fake_fetch(city: str, **kwargs):
            nonlocal active, max_active
            seen.append(city)
            sync_flags.append(bool(kwargs.get("sync_registry", True)))
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return {"ok": True, "city": city, "rows_upserted": 1}

        with patch("weatherbot_v3.scheduler._enabled_rows", return_value=rows), patch(
            "weatherbot_v3.scheduler._local_today", return_value="2026-07-11"
        ), patch("weatherbot_v3.scheduler.run_wunderground_hourly_fetch", side_effect=fake_fetch), patch(
            "weatherbot_v3.scheduler.run_wunderground_daily_rollup",
            return_value={"ok": True, "results": []},
        ) as rollup, patch("weatherbot_v3.scheduler._bias_refresh_due", return_value=False):
            result = await WeatherBotScheduler(city_concurrency=2)._run_historical_poller()

        self.assertTrue(result["ok"])
        self.assertEqual(sorted(seen), ["hong-kong", "shanghai"])
        self.assertEqual(max_active, 1)
        self.assertEqual(sync_flags, [False, False])
        self.assertEqual(rollup.call_count, 2)
        self.assertEqual({call.kwargs["target_date"] for call in rollup.call_args_list}, {"2026-07-10"})

    async def test_historical_badge_stays_healthy_when_only_daily_truth_rollup_is_incomplete(self):
        rows = [{"city_key": "amsterdam", "station_id": "EHAM", "timezone": "Europe/Amsterdam"}]
        scheduler = WeatherBotScheduler(city_concurrency=1)
        with patch("weatherbot_v3.scheduler._enabled_rows", return_value=rows), patch(
            "weatherbot_v3.scheduler._local_today", return_value="2026-07-22"
        ), patch(
            "weatherbot_v3.scheduler.run_wunderground_hourly_fetch",
            return_value={"ok": True, "rows_upserted": 30},
        ), patch(
            "weatherbot_v3.scheduler.run_wunderground_daily_rollup",
            return_value={"ok": False, "failed": 1, "results": [{"reason": "insufficient_hourly_coverage"}]},
        ), patch("weatherbot_v3.scheduler._bias_refresh_due", return_value=False):
            result = await scheduler._run_historical_poller()

        self.assertTrue(result["ok"])
        self.assertEqual(result["failed_cities"], 0)
        self.assertEqual(result["daily_truth_failed_cities"], 1)
        payload = result["results"][0]["payload"]
        self.assertTrue(payload["daily_truth_ok"] is False)
        self.assertEqual(payload["daily_truth_failed"], 1)

    async def test_historical_poller_refreshes_due_bias_table_once(self):
        rows = [{"city_key": "chicago", "station_id": "KORD", "timezone": "America/Chicago"}]
        trained = {
            "generated_at": "2026-07-23T00:00:00+00:00",
            "row_count": 6,
            "city_count": 1,
            "runtime_eligible_rows": 2,
            "rows": [{"sample_dates": ["2026-07-21", "2026-07-22"]}],
        }
        with patch("weatherbot_v3.scheduler._enabled_rows", return_value=rows), patch(
            "weatherbot_v3.scheduler._local_today", return_value="2026-07-23"
        ), patch(
            "weatherbot_v3.scheduler.run_wunderground_hourly_fetch",
            return_value={"ok": True, "rows_upserted": 24},
        ), patch(
            "weatherbot_v3.scheduler.run_wunderground_daily_rollup",
            return_value={"ok": True, "rows_upserted": 1},
        ), patch(
            "weatherbot_v3.scheduler._bias_refresh_due", return_value=True
        ), patch(
            "weatherbot_v3.scheduler.train_bias_table", return_value=trained
        ) as train:
            result = await WeatherBotScheduler(city_concurrency=1)._run_historical_poller()

        train.assert_called_once_with()
        self.assertEqual(result["calibration_refresh"]["status"], "updated")
        self.assertEqual(result["calibration_refresh"]["latest_sample_date"], "2026-07-22")

    async def test_bias_refresh_due_uses_generated_at_not_poller_count(self):
        TEST_DB_DIR.mkdir(exist_ok=True)
        path = TEST_DB_DIR / "bias_refresh_due.json"
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        path.write_text('{"generated_at":"2026-07-22T12:00:00+00:00"}', encoding="utf-8")

        self.assertFalse(_bias_refresh_due(
            output_path=path,
            now=datetime(2026, 7, 23, 7, 0, tzinfo=timezone.utc),
            max_age_hours=20,
        ))
        self.assertTrue(_bias_refresh_due(
            output_path=path,
            now=datetime(2026, 7, 23, 9, 0, tzinfo=timezone.utc),
            max_age_hours=20,
        ))

    async def test_metar_run_once_limits_concurrency_and_logs_per_city(self):
        db_path = test_db_path("scheduler_concurrency")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        active = 0
        max_active = 0
        seen_hours: list[float] = []
        lock = threading.Lock()

        def fake_recent(city: str, *, hours: float = 6.0):
            seen_hours.append(hours)
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return {
                "ok": True,
                "city": city,
                "station_id": city.upper(),
                "reports_fetched": 1,
                "reports_upserted": 1,
            }

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db()
            configure_enabled_cities(["chicago", "tokyo", "atlanta"])
            scheduler = WeatherBotScheduler(city_concurrency=2)
            with patch("weatherbot_v3.scheduler.fetch_recent_hours", side_effect=fake_recent), patch(
                "weatherbot_v3.scheduler.run_pws_fetch",
                return_value={"ok": True, "source": "wunderground_pws", "skipped": 1, "rows_upserted": 0},
            ) as pws_fetch:
                result = await scheduler.run_once("metar_poller")

            self.assertTrue(result["ok"])
            self.assertGreaterEqual(result["cities"], 3)
            self.assertLessEqual(max_active, 2)
            self.assertEqual(pws_fetch.call_count, 0)
            self.assertTrue(seen_hours)
            self.assertTrue(all(hours == 24.0 for hours in seen_hours))
            status = scheduler.status()["pollers"]["metar_poller"]
            self.assertEqual(status["last_result"]["result_count"], result["cities"])
            self.assertEqual(len(status["last_result"]["city_results"]), result["cities"])
            logs = list_data_fetch_logs(limit=20)
            self.assertTrue(any(row.get("source") == "scheduler" and row.get("stage") == "metar_poller" for row in logs))
            self.assertGreaterEqual(sum(1 for row in logs if row.get("stage") == "city_refresh"), result["cities"])

    async def test_poller_lock_skip_and_failure_backoff(self):
        db_path = test_db_path("scheduler_backoff")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db()
            configure_enabled_cities(["chicago"])
            scheduler = WeatherBotScheduler(city_concurrency=2)
            state = scheduler.pollers["metar_poller"]
            async with state.lock:
                skipped = await scheduler.run_once("metar_poller")
            self.assertTrue(skipped["skipped"])
            self.assertEqual(skipped["reason"], "already_running")

            with patch("weatherbot_v3.scheduler.fetch_recent_hours", side_effect=RuntimeError("boom")):
                result = await scheduler.run_once("metar_poller")

            self.assertFalse(result["ok"])
            status = scheduler.status()["pollers"]["metar_poller"]
            self.assertEqual(status["last_status"], "WARN")
            self.assertEqual(status["consecutive_failures"], 1)
            self.assertEqual(status["fails_last_hour"], 1)
            self.assertIsNotNone(status["next_run_at"])

    async def test_metar_poller_does_not_backoff_for_optional_pws_failure(self):
        db_path = test_db_path("scheduler_pws_optional")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))

        def fake_recent(city: str, *, hours: float = 6.0):
            return {
                "ok": True,
                "city": city,
                "station_id": city.upper(),
                "reports_fetched": 1,
                "reports_upserted": 1,
            }

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db()
            configure_enabled_cities(["chicago"])
            scheduler = WeatherBotScheduler(city_concurrency=1)
            with patch("weatherbot_v3.scheduler.fetch_recent_hours", side_effect=fake_recent), patch(
                "weatherbot_v3.scheduler.run_pws_fetch",
                side_effect=RuntimeError("pws unavailable"),
            ):
                result = await scheduler.run_once("metar_poller")

            self.assertTrue(result["ok"])
            status = scheduler.status()["pollers"]["metar_poller"]
            self.assertEqual(status["last_status"], "OK")
            city_result = status["last_result"]["city_results"][0]
            self.assertTrue(city_result["ok"])
            logs = list_data_fetch_logs(limit=20)
            self.assertTrue(any(
                row.get("source") == "scheduler"
                and row.get("stage") == "metar_poller"
                and row.get("status") == "OK"
                for row in logs
            ))

    async def test_metar_poller_cools_down_rejected_pws_credentials(self):
        db_path = test_db_path("scheduler_pws_auth_cooldown")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))

        def fake_recent(city: str, *, hours: float = 6.0):
            return {"ok": True, "city": city, "reports_upserted": 1}

        rejected = {
            "ok": False,
            "failed": 1,
            "results": [{"city": "chicago", "error": "401 Unauthorized"}],
        }
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db()
            configure_enabled_cities(["chicago", "atlanta"])
            scheduler = WeatherBotScheduler(city_concurrency=1)
            with patch.dict(os.environ, {"WUNDERGROUND_API_KEY": "test-pws-key"}, clear=False), patch(
                "weatherbot_v3.scheduler.run_pws_fetch", return_value=rejected
            ) as pws_fetch:
                result = await scheduler.run_once("pws_poller")

        self.assertFalse(result["ok"])
        self.assertEqual(pws_fetch.call_count, 1)
        city_results = [row["payload"] for row in result["results"]]
        self.assertTrue(any(row.get("reason") == "pws_auth_cooldown" for row in city_results))

    async def test_metar_city_timeout_is_reported_without_blocking_batch_forever(self):
        db_path = test_db_path("scheduler_city_timeout")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))

        def slow_recent(_city: str, *, hours: float = 6.0):
            time.sleep(0.2)
            return {"ok": True, "reports_upserted": 1, "hours": hours}

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db()
            configure_enabled_cities(["chicago"])
            scheduler = WeatherBotScheduler(city_concurrency=1)
            with patch("weatherbot_v3.scheduler.METAR_CITY_TIMEOUT_SECONDS", 0.01), patch(
                "weatherbot_v3.scheduler.fetch_recent_hours",
                side_effect=slow_recent,
            ):
                started = time.perf_counter()
                result = await scheduler.run_once("metar_poller")
                elapsed = time.perf_counter() - started

            self.assertFalse(result["ok"])
            self.assertEqual(result["cities"], 1)
            self.assertEqual(result["failed_cities"], 1)
            self.assertLess(elapsed, 1.0)
            status = scheduler.status()["pollers"]["metar_poller"]
            self.assertIn("metar_timeout", status["last_result"]["city_results"][0]["error"])

    async def test_start_stop_are_idempotent_without_running_collectors(self):
        scheduler = WeatherBotScheduler(city_concurrency=2)

        async def fake_loop(_key: str) -> None:
            await scheduler.stop_event.wait()

        scheduler._poll_loop = fake_loop  # type: ignore[method-assign]
        first = await scheduler.start()
        second = await scheduler.start()
        self.assertTrue(first["running"])
        self.assertTrue(second["running"])
        stopped = await scheduler.stop()
        again = await scheduler.stop()
        self.assertFalse(stopped["running"])
        self.assertFalse(again["running"])

    async def test_scheduler_status_api_shape(self):
        from dashboard_server import scheduler_status

        payload = await scheduler_status()
        self.assertIn("pollers", payload)
        self.assertIn("source_health", payload)
        self.assertIn("overall_status", payload["source_health"])
        self.assertIn("required_blockers", payload["source_health"])
        for key in ("forecast_poller", "metar_poller", "china_live_poller", "derive_poller", "gamma_orderbook_poller", "gamma_discovery_poller", "paper_settlement_poller", "paper_execution_poller"):
            self.assertIn(key, payload["pollers"])
            for field in ("last_run_at", "age_seconds", "last_duration_ms", "fails_last_hour", "next_run_at", "initial_delay_seconds"):
                self.assertIn(field, payload["pollers"][key])
        self.assertGreaterEqual(payload["poller_concurrency"], 2)
        self.assertIn("critical_poller_concurrency", payload)
        self.assertIn("background_poller_concurrency", payload)
        self.assertEqual(payload["poller_groups"]["gamma_orderbook_poller"], "critical")
        self.assertEqual(payload["poller_groups"]["derive_poller"], "background")
        self.assertIn("active_pollers", payload)
        self.assertIn("waiting_pollers", payload)

    async def test_scheduler_status_is_memory_only(self):
        scheduler = WeatherBotScheduler(city_concurrency=1)
        started = time.perf_counter()
        payload = scheduler.status()
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.1)
        self.assertEqual(payload["source_health"]["overall_status"], "warming")

    async def test_paper_settlement_poller_is_controlled_and_reports_counts(self):
        scheduler = WeatherBotScheduler(city_concurrency=1)
        with patch(
            "weatherbot_v3.scheduler.settle_open_paper_orders",
            return_value={
                "ok": True,
                "resolved_now": 2,
                "provisional_now": 1,
                "pending_now": 3,
                "results": [],
            },
        ) as settle:
            result = await scheduler.run_once("paper_settlement_poller")

        self.assertTrue(result["ok"])
        settle.assert_called_once_with(limit=1000, refresh_gamma=True, apply=True)
        status = scheduler.status()["pollers"]["paper_settlement_poller"]
        self.assertIn("2 resolved", status["last_message"])

    async def test_paper_execution_poller_is_inactive_without_explicit_cohort(self):
        scheduler = WeatherBotScheduler(city_concurrency=1)
        with patch(
            "weatherbot_v3.scheduler.run_paper_validation_tick",
            return_value={
                "ok": True,
                "status": "inactive",
                "skipped": True,
                "reason": "no_active_paper_validation_run",
            },
        ) as tick:
            result = await scheduler.run_once("paper_execution_poller")

        tick.assert_called_once_with(apply=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "inactive")
        status = scheduler.status()["pollers"]["paper_execution_poller"]
        self.assertEqual(status["last_status"], "OK")
        self.assertIn("inactive", status["last_message"])

    async def test_derive_poller_consumes_pre_refreshed_market_buckets(self):
        rows = [
            {"city_key": "chicago", "station_id": "KORD"},
            {"city_key": "atlanta", "station_id": "KATL"},
        ]
        dates = ["2026-07-10", "2026-07-11"]

        with patch("weatherbot_v3.scheduler._enabled_rows", return_value=rows), patch(
            "weatherbot_v3.scheduler._target_dates_for_station", return_value=dates
        ), patch(
            "weatherbot_v3.scheduler.run_market_buckets_sync"
        ) as market_sync, patch(
            "weatherbot_v3.scheduler.run_hourly_consensus_build", return_value={"ok": True, "rows_upserted": 24}
        ), patch(
            "weatherbot_v3.scheduler.run_daily_max_build", return_value={"ok": True, "stored": 1}
        ), patch(
            "weatherbot_v3.scheduler.run_signal_decisions_build",
            return_value={"ok": True, "stored": 11, "results": [{"bucket_count": 11}]},
        ) as decisions:
            result = await WeatherBotScheduler(city_concurrency=2)._run_derive_poller()

        self.assertTrue(result["ok"])
        market_sync.assert_not_called()
        self.assertEqual(result["ok_cities"], 2)
        self.assertFalse(result["readiness_refreshed"])
        self.assertEqual(result["readiness_reason"], "explicit_audit_only")
        self.assertEqual(decisions.call_count, 4)
        for call in decisions.call_args_list:
            self.assertFalse(call.kwargs["refresh_readiness"])

    async def test_gamma_poller_refreshes_cached_books_in_one_batch(self):
        rows = [
            {"city_key": "chicago", "station_id": "KORD"},
            {"city_key": "atlanta", "station_id": "KATL"},
        ]
        dates = ["2026-07-10", "2026-07-11"]

        def fake_cached_refresh(**kwargs):
            return {
                "ok": True,
                "cached_buckets": 44,
                "tokens_requested": 44,
                "quotes_refreshed": 44,
                "quotes_missing": 0,
            }

        with patch("weatherbot_v3.scheduler._collection_rows", return_value=rows), patch(
            "weatherbot_v3.scheduler._target_dates_for_station", return_value=dates
        ), patch(
            "weatherbot_v3.scheduler._active_market_city_keys", return_value={"chicago", "atlanta"}
        ), patch(
            "weatherbot_v3.scheduler.refresh_cached_market_bucket_orderbooks", side_effect=fake_cached_refresh
        ) as cached_refresh:
            result = await WeatherBotScheduler(city_concurrency=2)._run_gamma_orderbook_poller()

        self.assertTrue(result["ok"])
        cached_refresh.assert_called_once()
        self.assertEqual(
            cached_refresh.call_args.kwargs["targets_by_city"],
            {"chicago": dates, "atlanta": dates},
        )
        self.assertEqual(result["quotes_refreshed"], 44)

    async def test_gamma_discovery_treats_missing_structured_books_as_gaps_not_poller_failure(self):
        with patch("weatherbot_v3.scheduler._collection_rows", return_value=[]), patch(
            "weatherbot_v3.scheduler.run_gamma_structured_sync",
            return_value={
                "ok": False,
                "events_stored": 1,
                "markets_stored": 2,
                "orderbooks_stored": 1,
                "failures": [{"market_id": "missing", "error": "clob_batch_book_missing"}],
            },
        ):
            result = await WeatherBotScheduler()._run_gamma_discovery_poller()

        self.assertTrue(result["ok"])
        self.assertEqual(result["failures"], [])
        self.assertEqual(len(result["book_gaps"]), 1)

    async def test_china_live_poller_runs_all_supported_registry_cities(self):
        db_path = test_db_path("scheduler_china_live")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        seen: list[str] = []

        def fake_china(city: str, *, dry_run: bool = False):
            seen.append(city)
            return {
                "ok": True,
                "city": city,
                "station_id": "HKO" if city == "hong-kong" else "101020100",
                "rows_upserted": 1,
                "rows_inserted": 1,
                "source_observation_new": True,
            }

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db()
            configure_enabled_cities(["chicago", "shanghai", "hong-kong"])
            scheduler = WeatherBotScheduler(city_concurrency=2)
            with patch("weatherbot_v3.scheduler.fetch_china_weather_city", side_effect=fake_china):
                result = await scheduler.run_once("china_live_poller")

        self.assertTrue(result["ok"])
        self.assertEqual(sorted(seen), [
            "beijing",
            "chengdu",
            "chongqing",
            "guangzhou",
            "hong-kong",
            "qingdao",
            "shanghai",
            "shenzhen",
            "wuhan",
        ])
        status = scheduler.status()["pollers"]["china_live_poller"]
        self.assertEqual(status["last_result"]["result_count"], 9)


class RecentMetarTests(unittest.TestCase):
    def test_fetch_recent_hours_is_station_time_idempotent(self):
        db_path = test_db_path("fetch_recent_hours_idempotent")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        item = {
            "stationId": "KORD",
            "obsTime": "2026-07-04T10:51:00Z",
            "rawOb": "METAR KORD 041051Z 18010KT 10SM FEW040 25/10 A2992",
            "temp": 25.0,
            "dewp": 10.0,
            "wdir": 180,
            "wspd": 10,
            "visib": 10,
            "clouds": [{"cover": "FEW", "base": 4000}],
        }

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db()
            with patch("weatherbot_v3.metar.fetch_awc_metars", return_value=[item]):
                first = fetch_recent_hours("chicago", hours=6)
                second = fetch_recent_hours("chicago", hours=6)
            with connect(db_path) as conn:
                rows = conn.execute("SELECT report_key, raw_text FROM metar_reports").fetchall()

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(first["recent_hours"], 6.0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["report_key"]), 32)
        self.assertNotIn(":", rows[0]["report_key"])
        self.assertIn("METAR KORD", rows[0]["raw_text"])


if __name__ == "__main__":
    unittest.main()
