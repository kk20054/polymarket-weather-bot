from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from weatherbot_v3.db import connect_readonly, init_v3_db
from weatherbot_v3.executor import ExecutionResult
from weatherbot_v3.paper import execute_paper_decisions
from weatherbot_v3.sizing import calculate_kelly_fraction
from weatherbot_v3.verification_agents import build_project_verification_report, probe_local_runtime


class ProjectVerificationTests(unittest.TestCase):
    def test_missing_database_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = build_project_verification_report(
                path=Path(tmp) / "missing.db",
                probe_runtime=False,
            )
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["readiness"]["observation"]["ready"])

    def test_empty_database_exposes_machine_gate_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "weatherbot.db"
            init_v3_db(path)
            report = build_project_verification_report(
                path=path,
                source_health={"sources": [], "city_matrix": []},
                runtime={"probed": False, "available": None},
            )
        check_ids = {
            check["id"]
            for agent in report["agents"]
            for check in agent["checks"]
        }
        self.assertIn("temporal_no_leak", check_ids)
        self.assertIn("matched_market_executable", check_ids)
        self.assertFalse(report["readiness"]["observation"]["ready"])

    def test_readonly_connection_rejects_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "weatherbot.db"
            init_v3_db(path)
            with connect_readonly(path) as conn:
                with self.assertRaises(Exception):
                    conn.execute("INSERT INTO risk_events(event_type, reason, created_at) VALUES('x', 'x', 'x')")

    def test_runtime_probe_accepts_masked_api_settings_only(self):
        responses = iter([
            {"stats": {}, "auto_simulation": {}, "live_trading": {}, "paper_validation": {}},
            {"providers": [{
                "key": "weather_com",
                "label": "Weather.com",
                "description": "forecast",
                "configured": True,
                "masked_value": "********",
                "docs_url": "https://example.com",
                "test_label": "test",
                "test_has_side_effect": False,
            }]},
            {"running": False},
        ])
        with patch("weatherbot_v3.verification_agents._get_json", side_effect=lambda *_args, **_kwargs: next(responses)):
            runtime = probe_local_runtime()
        self.assertTrue(runtime["available"])
        self.assertTrue(runtime["api_settings_safe"])


class ExecutionBoundaryTests(unittest.TestCase):
    def test_bulk_paper_execution_is_bound_to_revision_and_batch(self):
        decisions = [
            {"decision_id": "visible", "strategy_name": "single_bucket_ev", "strategy_revision_id": "r2", "issued_at": "2026-07-13T01:00:00+00:00", "paper_allowed": True, "paper_decision": "buy"},
            {"decision_id": "old-batch", "strategy_name": "single_bucket_ev", "strategy_revision_id": "r2", "issued_at": "2026-07-13T00:00:00+00:00", "paper_allowed": True, "paper_decision": "buy"},
            {"decision_id": "old-revision", "strategy_name": "single_bucket_ev", "strategy_revision_id": "r1", "issued_at": "2026-07-13T01:00:00+00:00", "paper_allowed": True, "paper_decision": "buy"},
        ]
        with (
            patch("weatherbot_v3.paper.list_signal_decisions", return_value=decisions),
            patch("weatherbot_v3.paper.execute_paper_decision_record", side_effect=lambda row, **_: {"ok": True, "status": "paper_filled", "decision_id": row["decision_id"]}),
            patch("weatherbot_v3.paper.paper_execution_summary", return_value={"count": 0}),
        ):
            result = execute_paper_decisions(
                city_key="chicago",
                target_date="2026-07-13",
                strategy_revision_id="r2",
                decision_batch_issued_at="2026-07-13T01:00:00+00:00",
                dry_run=True,
            )
        self.assertEqual(result["requested"], 1)
        self.assertEqual(result["results"][0]["decision_id"], "visible")

    def test_canary_endpoint_forces_dry_run_and_safe_default_amount(self):
        captured = {}

        class FakeExecutor:
            def place_order(self, signal, amount, *, force_dry_run=False):
                captured.update(amount=amount, force_dry_run=force_dry_run)
                return ExecutionResult(True, "live", "dry_run", 1, None, {"amount": amount})

        import dashboard_server

        with (
            patch.object(dashboard_server, "list_signals", return_value=[{"id": 7, "market_id": "m1"}]),
            patch.object(dashboard_server, "load_v3_config", return_value=SimpleNamespace(canary_max_order_usd=2.0, live_max_order_usd=2.0)),
            patch.object(dashboard_server, "LiveExecutor", return_value=FakeExecutor()),
            patch.object(dashboard_server, "log_event"),
            patch.object(dashboard_server, "_clear_production_validation_cache"),
        ):
            result = asyncio.run(dashboard_server.canary_dry_run(dashboard_server.CanaryDryRunUpdate(signal_id=7)))
        self.assertTrue(result["ok"])
        self.assertEqual(captured["amount"], 1.0)
        self.assertTrue(captured["force_dry_run"])

    def test_bulk_paper_api_requires_visible_revision_and_batch(self):
        import dashboard_server
        from fastapi import HTTPException

        request = dashboard_server.PaperExecutionRequest(
            city="chicago",
            target_date="2026-07-13",
        )
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(dashboard_server.paper_orders_execute(request))
        self.assertEqual(raised.exception.status_code, 400)

    def test_kelly_rejects_out_of_range_probability(self):
        self.assertEqual(calculate_kelly_fraction(-0.1, 0.2), 0.0)
        self.assertEqual(calculate_kelly_fraction(1.1, 0.2), 0.0)


if __name__ == "__main__":
    unittest.main()
