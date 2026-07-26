from tests import ensure_test_environment

ensure_test_environment()

import inspect
import unittest
from pathlib import Path
from unittest.mock import patch

import dashboard_server
from weatherbot_v3 import cli, history, market_buckets, metar, openmeteo, polymarket_gamma, pws, signals, weathercom


ROOT = Path(__file__).resolve().parents[1]


def _station(city_key: str, *, enabled: bool, tier: int = 1) -> dict:
    return {
        "city_key": city_key,
        "city": city_key,
        "city_name": city_key.title(),
        "station_id": "NZWN" if city_key == "wellington" else "KORD",
        "enabled": enabled,
        "tier": tier,
    }


class EnabledCityCoverageTests(unittest.TestCase):
    def test_default_collectors_follow_enabled_station_rows(self):
        rows = [
            _station("chicago", enabled=False),
            _station("wellington", enabled=True),
        ]
        expected = ["wellington"]

        for module, selector in (
            (openmeteo, lambda: openmeteo._select_profiles(None, limit_cities=0)),
            (weathercom, lambda: weathercom._select_profiles(None, limit_cities=0)),
            (history, lambda: history._select_history_profiles(None, limit_cities=0)),
            (pws, lambda: pws._select_profiles(None, limit_cities=0)),
            (metar, lambda: metar._select_profiles(None)),
        ):
            with self.subTest(module=module.__name__), patch.object(module, "list_stations", return_value=rows):
                self.assertEqual([profile.city for profile in selector()], expected)

        with patch.object(market_buckets, "list_stations", return_value=rows):
            selected = market_buckets._selected_stations([], limit_cities=0)
        self.assertEqual([row["city_key"] for row in selected], expected)

        with patch.object(polymarket_gamma, "list_stations", return_value=rows):
            selected = polymarket_gamma._selected_station_rows(None)
        self.assertEqual([row["city_key"] for row in selected], expected)

        with patch.object(metar, "list_stations", return_value=rows):
            selected = metar.station_rows_for_metar_backfill(None, limit=0)
        self.assertEqual([row["city_key"] for row in selected], expected)

    def test_default_city_caps_do_not_silently_recreate_a_whitelist(self):
        functions = (
            cli._default_layer_city_keys,
            cli.run_hourly_consensus_build,
            cli.run_daily_max_build,
            cli.run_openmeteo_fetch,
            cli.run_weathercom_fetch,
            cli.run_market_buckets_sync,
            cli.run_signal_decisions_build,
            openmeteo.fetch_openmeteo_forecasts,
            weathercom.fetch_weathercom_forecasts,
            history.fetch_open_meteo_historical_backfill,
            market_buckets.sync_active_weather_market_buckets,
            pws.fetch_wunderground_pws,
            cli.run_iem_asos_truth_fetch,
            cli.run_wunderground_truth_fetch,
            cli.run_wunderground_hourly_fetch,
            cli.run_wunderground_daily_rollup,
        )
        for function in functions:
            with self.subTest(function=function.__qualname__):
                parameter = inspect.signature(function).parameters["limit_cities"]
                self.assertEqual(parameter.default, 0)

        self.assertEqual(dashboard_server.MarketBucketsSyncRequest().limit_cities, 0)

    def test_legacy_hardcoded_city_whitelists_are_absent(self):
        paths = [
            ROOT / "weatherbot_v3" / "cli.py",
            ROOT / "weatherbot_v3" / "history.py",
            ROOT / "weatherbot_v3" / "metar.py",
            ROOT / "weatherbot_v3" / "openmeteo.py",
            ROOT / "weatherbot_v3" / "weathercom.py",
            ROOT / "weatherbot_v3" / "market_buckets.py",
            ROOT / "weatherbot_v3" / "polymarket_gamma.py",
            ROOT / "weatherbot_v3" / "pws.py",
        ]
        source = "\n".join(path.read_text(encoding="utf-8") for path in paths)

        self.assertNotIn('["chicago", "tokyo", "atlanta", "nyc", "dallas"]', source)
        self.assertNotIn("DEFAULT_BACKFILL_CITY_PRIORITY", source)
        self.assertNotIn("ASIAN_CITY_KEYS", source)
        self.assertNotIn('priority = ("chicago", "tokyo", "atlanta"', source)

    def test_signal_batch_initializes_database_once(self):
        targets = [("wellington", "2026-07-26"), ("chongqing", "2026-07-26")]
        result_rows = [
            {"ok": True, "stored": 1, "decision_count": 1},
            {"ok": True, "stored": 2, "decision_count": 2},
        ]
        db_path = Path("test-weatherbot.db")

        with patch.object(signals, "init_v3_db") as init_db, patch.object(
            signals,
            "build_signal_decisions",
            side_effect=result_rows,
        ) as build_one:
            result = signals.build_signal_decisions_for_targets(targets, path=db_path)

        init_db.assert_called_once_with(db_path)
        self.assertEqual(result["stored"], 3)
        self.assertEqual(build_one.call_count, 2)
        for call in build_one.call_args_list:
            self.assertFalse(call.kwargs["initialize_db"])

        build_source = inspect.getsource(signals.build_signal_decisions)
        self.assertIn(
            "upsert_signal_decision_record(decision, path=path, initialize_db=False)",
            build_source,
        )


if __name__ == "__main__":
    unittest.main()
