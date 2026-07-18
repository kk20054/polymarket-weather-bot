from tests import ensure_test_environment

ensure_test_environment()

import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from weatherbot_v3.db import connect, init_v3_db
from weatherbot_v3.stations import DEFAULT_ENABLED_CITY_KEYS, enabled_station_rows, list_stations, set_station_enabled, sync_station_registry


TEST_DB_DIR = Path(__file__).resolve().parents[1] / ".tmp-tests"


def test_db_path(name: str) -> Path:
    TEST_DB_DIR.mkdir(exist_ok=True)
    path = TEST_DB_DIR / f"{name}.db"
    path.unlink(missing_ok=True)
    return path


class WatchlistEnabledTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_enabled_watchlist_is_limited_to_existing_defaults(self):
        db_path = test_db_path("watchlist_defaults")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db()
            sync_station_registry()
            rows = list_stations()
            all_keys = {str(row["city_key"]) for row in rows}
            displayed_keys = {str(row["city_key"]) for row in rows if row["display_enabled"]}
            enabled_keys = {str(row["city_key"]) for row in enabled_station_rows()}
            expected = DEFAULT_ENABLED_CITY_KEYS & all_keys

        self.assertEqual(len(all_keys), 51)
        self.assertEqual(displayed_keys, all_keys)
        self.assertEqual(enabled_keys, expected)
        self.assertLessEqual(len(enabled_keys), len(DEFAULT_ENABLED_CITY_KEYS))
        self.assertIn("wellington", enabled_keys)
        self.assertNotIn("manila", enabled_keys)

    async def test_station_enable_disable_cli_helper_updates_flags(self):
        db_path = test_db_path("watchlist_toggle")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db()
            sync_station_registry()
            disabled = set_station_enabled("chicago", False)
            enabled = set_station_enabled("chicago", True, tier=2)
            with connect(db_path) as conn:
                row = conn.execute("SELECT enabled, tier FROM stations WHERE city_key='chicago'").fetchone()

        self.assertTrue(disabled["ok"])
        self.assertFalse(disabled["enabled"])
        self.assertTrue(enabled["ok"])
        self.assertTrue(enabled["enabled"])
        self.assertEqual(enabled["tier"], 2)
        self.assertEqual(int(row["enabled"]), 1)
        self.assertEqual(int(row["tier"]), 2)

    async def test_station_enabled_api_returns_updated_station(self):
        db_path = test_db_path("watchlist_api")
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db()
            sync_station_registry()
            from dashboard_server import StationEnabledUpdate, station_enabled

            with patch("dashboard_server._refresh_dashboard_cache_once", new=AsyncMock()):
                result = await station_enabled("chicago", StationEnabledUpdate(enabled=False))

        self.assertTrue(result["ok"])
        self.assertEqual(result["city_key"], "chicago")
        self.assertFalse(result["enabled"])
        self.assertIn("station", result)


if __name__ == "__main__":
    unittest.main()
