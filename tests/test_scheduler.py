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
        lock = threading.Lock()

        def fake_recent(city: str, *, hours: float = 6.0):
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
        for key in ("forecast_poller", "metar_poller", "china_live_poller", "derive_poller"):
            self.assertIn(key, payload["pollers"])
            for field in ("last_run_at", "age_seconds", "last_duration_ms", "fails_last_hour", "next_run_at"):
                self.assertIn(field, payload["pollers"][key])

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
