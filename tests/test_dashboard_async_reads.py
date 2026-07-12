import asyncio
import inspect
import time
import unittest
from unittest.mock import patch

import dashboard_server


class DashboardAsyncReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_hourly_consensus_does_not_block_event_loop(self):
        def slow_summary(_city, _target_date):
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

    async def test_layer7_read_routes_delegate_to_thread_pool(self):
        for route in (
            dashboard_server.dashboard,
            dashboard_server.production_refresh_status,
            dashboard_server.paper_validation_status_api,
            dashboard_server.forecast_archive_manifest,
            dashboard_server.forecasts,
            dashboard_server.hourly_consensus,
            dashboard_server.market_buckets,
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
        ):
            payload = await dashboard_server.dashboard("")

        self.assertEqual(len(payload["weather_city_series"]), 2)
        self.assertTrue(all(row["hourly_points"] == [] for row in payload["weather_city_series"]))
        self.assertEqual(payload["city_evidence"], [])


if __name__ == "__main__":
    unittest.main()
