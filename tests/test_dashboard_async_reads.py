from tests import ensure_test_environment

ensure_test_environment()

import asyncio
import inspect
import time
import unittest
from unittest.mock import patch

import dashboard_server


class DashboardAsyncReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_hourly_consensus_does_not_block_event_loop(self):
        def slow_summary(_city, _target_date, **_kwargs):
            time.sleep(0.12)
            return {"ok": True, "series": {"forecast": []}}

        async def heartbeat():
            await asyncio.sleep(0.01)
            return time.perf_counter()

        with patch("dashboard_server.hourly_consensus_summary", side_effect=slow_summary):
            started = time.perf_counter()
            query_task = asyncio.create_task(
                dashboard_server.hourly_consensus("shanghai", "2026-07-12")
            )
            heartbeat_at = await heartbeat()
            payload = await query_task

        self.assertLess(heartbeat_at - started, 0.08)
        self.assertTrue(payload["ok"])
        self.assertIn("ensure_schema=False", inspect.getsource(dashboard_server.hourly_consensus))

    async def test_layer7_read_routes_delegate_to_thread_pool(self):
        for route in (
            dashboard_server.dashboard,
            dashboard_server.production_refresh_status,
            dashboard_server.paper_validation_status_api,
            dashboard_server.forecast_archive_manifest,
            dashboard_server.forecasts,
            dashboard_server.hourly_consensus,
            dashboard_server.market_buckets,
            dashboard_server.bucket_probabilities_api,
            dashboard_server.daily_max_predictions,
            dashboard_server.signal_decisions,
            dashboard_server.model_reprice_events,
            dashboard_server.contracts,
        ):
            self.assertIn("asyncio.to_thread", inspect.getsource(route))

    async def test_dashboard_keeps_city_summaries_but_scopes_heavy_evidence(self):
        cached = {
            "weather_city_series": [
                {"city_key": "shanghai", "station_id": "ZSPD", "hourly_points": [{"hour": 1}]},
                {"city_key": "chicago", "station_id": "KORD", "hourly_points": [{"hour": 2}]},
            ],
            "city_evidence": [
                {"city_key": "shanghai", "station_id": "ZSPD"},
                {"city_key": "chicago", "station_id": "KORD"},
            ],
        }
        with patch.object(dashboard_server, "dashboard_payload_cache", cached), patch(
            "dashboard_server._read_json", return_value=None
        ), patch(
            "dashboard_server._ensure_dashboard_refresh"
        ), patch(
            "dashboard_server._cached_recommendations", return_value={"focus_items": []}
        ), patch.object(
            dashboard_server, "DASHBOARD_AUTO_BUILD", False
        ):
            payload = await dashboard_server.dashboard("shanghai")

        self.assertEqual(len(payload["weather_city_series"]), 2)
        shanghai, chicago = payload["weather_city_series"]
        self.assertEqual(shanghai["hourly_points"], [{"hour": 1}])
        self.assertEqual(chicago["hourly_points"], [])
        self.assertEqual([row["city_key"] for row in payload["city_evidence"]], ["shanghai"])

    async def test_dashboard_without_city_returns_summaries_only(self):
        cached = {
            "weather_city_series": [
                {"city_key": "shanghai", "hourly_points": [{"hour": 1}]},
                {"city_key": "chicago", "hourly_points": [{"hour": 2}]},
            ],
            "city_evidence": [{"city_key": "shanghai"}, {"city_key": "chicago"}],
        }
        with patch.object(dashboard_server, "dashboard_payload_cache", cached), patch(
            "dashboard_server._read_json", return_value=None
        ), patch(
            "dashboard_server._ensure_dashboard_refresh"
        ), patch(
            "dashboard_server._cached_recommendations", return_value={"focus_items": []}
        ), patch.object(
            dashboard_server, "DASHBOARD_AUTO_BUILD", False
        ):
            payload = await dashboard_server.dashboard("")

        self.assertEqual(len(payload["weather_city_series"]), 2)
        self.assertTrue(all(row["hourly_points"] == [] for row in payload["weather_city_series"]))
        self.assertEqual(payload["city_evidence"], [])

    async def test_dashboard_overlays_fresh_observation_on_cached_city_summary(self):
        cached = {
            "weather_city_series": [{
                "city_key": "shanghai",
                "current_temp": None,
                "current_temp_timestamp": None,
                "last_refreshed_at": "2026-08-06T17:22:00+00:00",
                "hourly_points": [],
            }],
            "city_evidence": [],
        }
        fresh = {
            "shanghai": {
                "current_temp": 30.9,
                "current_temp_source": "china_live",
                "current_temp_timestamp": "2026-08-07T00:20:00+00:00",
                "last_refreshed_at": "2026-08-07T00:20:00+00:00",
            }
        }
        with patch.object(dashboard_server, "dashboard_payload_cache", cached), patch(
            "dashboard_server._latest_city_observation_summaries", return_value=fresh
        ), patch(
            "dashboard_server._read_json", return_value=None
        ), patch(
            "dashboard_server._cached_recommendations", return_value={"focus_items": []}
        ), patch.object(
            dashboard_server, "DASHBOARD_AUTO_BUILD", False
        ):
            payload = await dashboard_server.dashboard("")

        shanghai = payload["weather_city_series"][0]
        self.assertEqual(shanghai["current_temp"], 30.9)
        self.assertEqual(shanghai["current_temp_source"], "china_live")
        self.assertEqual(shanghai["last_refreshed_at"], "2026-08-07T00:20:00+00:00")

    async def test_dashboard_only_refreshes_full_cache_when_explicitly_enabled(self):
        cached = {
            "weather_city_series": [],
            "city_evidence": [],
            "recommendations": {"focus_items": [], "generated_at": "old"},
        }
        current = {
            "focus_items": [{"city_key": "wellington", "type": "weather_focus"}],
            "generated_at": "current",
        }
        with patch.object(dashboard_server, "dashboard_payload_cache", cached), patch(
            "dashboard_server._read_json", return_value=None
        ), patch(
            "dashboard_server._ensure_dashboard_refresh"
        ) as ensure_refresh, patch(
            "dashboard_server._cached_recommendations", return_value=current
        ) as recommendations, patch.object(
            dashboard_server, "DASHBOARD_AUTO_BUILD", True
        ):
            payload = await dashboard_server.dashboard("")

        ensure_refresh.assert_called_once_with()
        recommendations.assert_called_once()
        self.assertEqual(payload["recommendations"], current)

    async def test_dashboard_default_read_does_not_start_full_build(self):
        cached = {"weather_city_series": [], "city_evidence": []}
        with patch.object(dashboard_server, "dashboard_payload_cache", cached), patch(
            "dashboard_server._read_json", return_value=None
        ), patch(
            "dashboard_server._ensure_dashboard_refresh"
        ) as ensure_refresh, patch(
            "dashboard_server._cached_recommendations", return_value={"focus_items": []}
        ), patch.object(
            dashboard_server, "DASHBOARD_AUTO_BUILD", False
        ):
            await dashboard_server.dashboard("")

        ensure_refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
