import asyncio
import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from weatherbot_v3.db import connect, init_v3_db, list_data_fetch_logs
from weatherbot_v3.metar import fetch_recent_hours
from weatherbot_v3.scheduler import WeatherBotScheduler
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
            self.assertEqual(pws_fetch.call_count, result["cities"])
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
        for key in ("forecast_poller", "metar_poller", "china_live_poller", "derive_poller", "gamma_orderbook_poller"):
            self.assertIn(key, payload["pollers"])
            for field in ("last_run_at", "age_seconds", "last_duration_ms", "fails_last_hour", "next_run_at", "initial_delay_seconds"):
                self.assertIn(field, payload["pollers"][key])

    async def test_derive_poller_batches_market_refresh_by_target_date(self):
        rows = [
            {"city_key": "chicago", "station_id": "KORD"},
            {"city_key": "atlanta", "station_id": "KATL"},
        ]
        dates = ["2026-07-10", "2026-07-11"]

        def fake_market_sync(limit, **kwargs):
            cities = kwargs["cities_arg"].split(",")
            target_date = kwargs["target_date"]
            return {
                "ok": True,
                "results": [
                    {"ok": True, "city": city, "target_date": target_date, "stored": 11}
                    for city in cities
                ],
            }

        with patch("weatherbot_v3.scheduler._enabled_rows", return_value=rows), patch(
            "weatherbot_v3.scheduler._target_dates_for_station", return_value=dates
        ), patch(
            "weatherbot_v3.scheduler.run_market_buckets_sync", side_effect=fake_market_sync
        ) as market_sync, patch(
            "weatherbot_v3.scheduler.run_hourly_consensus_build", return_value={"ok": True, "rows_upserted": 24}
        ), patch(
            "weatherbot_v3.scheduler.run_daily_max_build", return_value={"ok": True, "stored": 1}
        ), patch(
            "weatherbot_v3.scheduler.run_signal_decisions_build", return_value={"ok": True, "stored": 11}
        ):
            result = await WeatherBotScheduler(city_concurrency=2)._run_derive_poller()

        self.assertTrue(result["ok"])
        self.assertEqual(market_sync.call_count, 2)
        self.assertTrue(all(call.kwargs["cities_arg"] == "chicago,atlanta" for call in market_sync.call_args_list))
        self.assertEqual(result["ok_cities"], 2)

    async def test_china_live_poller_only_runs_supported_enabled_cities(self):
        db_path = test_db_path("scheduler_china_live")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        seen: list[str] = []

        def fake_china(cities_arg: str, *, dry_run: bool = False):
            seen.append(cities_arg)
            return {
                "ok": True,
                "city": cities_arg,
                "station_id": "HKO" if cities_arg == "hong-kong" else "101020100",
                "rows_upserted": 1,
            }

        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db()
            configure_enabled_cities(["chicago", "shanghai", "hong-kong"])
            scheduler = WeatherBotScheduler(city_concurrency=2)
            with patch("weatherbot_v3.scheduler.run_china_weather_fetch", side_effect=fake_china):
                result = await scheduler.run_once("china_live_poller")

        self.assertTrue(result["ok"])
        self.assertEqual(sorted(seen), ["hong-kong", "shanghai"])
        status = scheduler.status()["pollers"]["china_live_poller"]
        self.assertEqual(status["last_result"]["result_count"], 2)


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
        self.assertEqual(rows[0]["report_key"], "awc:KORD:2026-07-04T10:51:00+00:00")
        self.assertIn("METAR KORD", rows[0]["raw_text"])


if __name__ == "__main__":
    unittest.main()
