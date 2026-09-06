from tests import ensure_test_environment

ensure_test_environment()

from copy import deepcopy
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from weatherbot_v3.db import connect, init_v3_db
from weatherbot_v3.live_execution import LiveExecutionService, _build_order, _decision, _reserve, get_live_order, list_live_orders


class FakeTransport:
    def __init__(self, path, snapshot):
        self.path, self.snapshot = path, snapshot
        self.posts = 0
        self.fail_post = False

    def preflight(self, market_id, token_id):
        return self.snapshot

    def prepare_limit_buy(self, *args, **kwargs):
        return SimpleNamespace(order_hash="signed-order-hash")

    def submit_prepared(self, prepared):
        rows = list_live_orders(path=self.path)
        assert rows[0]["status"] == "submitting"
        assert rows[0]["clob_order_id"] == "signed-order-hash"
        self.posts += 1
        if self.fail_post:
            raise TimeoutError("ambiguous network failure")
        return {"status": "open", "clob_order_id": "signed-order-hash"}

    def cancel_order(self, order_id):
        return {"ok": True, "cancelled": [order_id]}

    def get_order(self, order_id):
        return {"ok": True, "status": "found", "exchange_status": "CANCELED", "size_matched": "0", "original_size": "5.5", "asset_id": "YES", "side": "BUY", "price": ".2", "trade_ids": []}


class LiveExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.db"
        init_v3_db(self.path)
        self.cfg = SimpleNamespace(
            live_trading=True, live_dry_run=False, ai_required_for_live=False,
            live_daily_max_usd=2, live_max_open_positions=4, live_daily_loss_limit=20,
            live_max_drawdown_pct=0.5, bankroll_usd=40, max_bet=2, max_per_trade_usd=2,
            live_max_order_usd=2, canary_max_order_usd=2, min_price=.05, max_price=.8,
            max_slippage=.05, orderbook_max_age_minutes=5,
        )
        self.decision = {
            "decision_id": "d1", "strategy_revision_id": "r1", "strategy_params_hash": "sha",
            "yes_token_id": "YES", "market_id": "m1", "city_key": "shanghai",
            "target_date": "2026-09-07", "model_probability": .6, "market_ask": .2,
            "neg_risk": True, "strategy_name": "core_modal_v1",
        }
        self.profile = {"parameters": {
            "decision_policy": {"min_live_trade_edge": .08, "stale_book_seconds": 300, "max_spread_bps": 500},
            "sizing": {"live_kelly_multiplier": .15, "max_live_bankroll_fraction_per_trade": .05},
            "strategies": {"core_modal_v1": {"min_live_effective_edge": .08}},
        }}
        self.snapshot = {"balance_usd": 40., "allowance_usd": 40., "quote": {
            "asset_id": "YES", "best_bid": .195, "best_ask": .2, "tick_size": .001,
            "min_order_size": 5, "neg_risk": True, "closed": False, "accepting_orders": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bids": [{"price": .195, "size": 100}], "asks": [{"price": .2, "size": 100}],
        }}
        self.transport = FakeTransport(self.path, self.snapshot)
        self.service = LiveExecutionService(path=self.path, transport=self.transport)
        patcher = patch("weatherbot_v3.live_execution.load_config", return_value=self.cfg)
        patcher.start()
        self.addCleanup(patcher.stop)
        ready = patch("weatherbot_v3.executor.LIVE_EXECUTION_PRODUCTION_READY", True)
        ready.start()
        self.addCleanup(ready.stop)

    def execute(self, preview=False):
        with patch("weatherbot_v3.live_execution._decision", return_value=(self.decision, self.profile, [])):
            return self.service.execute("d1", "r1", amount=1.1, preview=preview)

    def test_disabled_never_fetches_signs_or_persists(self):
        self.cfg.live_trading = False
        with patch.object(self.transport, "preflight", side_effect=AssertionError("no network")):
            self.assertEqual(self.execute()["reason"], "live_trading_disabled")
        self.assertEqual(list_live_orders(path=self.path), [])

    def test_preview_does_not_create_order_or_submit(self):
        self.assertEqual(self.execute(preview=True)["status"], "preview")
        self.assertEqual(list_live_orders(path=self.path), [])
        self.assertEqual(self.transport.posts, 0)

    def test_reservation_precedes_post_and_repeat_does_not_post(self):
        first = self.execute()
        self.assertEqual(first["status"], "open")
        self.assertEqual(self.execute()["status"], "duplicate")
        self.assertEqual(self.transport.posts, 1)

    def test_timeout_remains_unknown_and_does_not_retry(self):
        self.transport.fail_post = True
        with self.assertRaises(TimeoutError):
            self.execute()
        self.assertEqual(list_live_orders(path=self.path)[0]["status"], "unknown")
        self.assertEqual(self.execute()["status"], "duplicate")
        self.assertEqual(self.transport.posts, 1)

    def test_pending_orders_count_toward_daily_budget(self):
        order, _ = _build_order(self.decision, self.profile, self.snapshot, 1.1, self.cfg)
        self.assertEqual(_reserve(order, self.cfg, path=self.path)["status"], "reserved")
        other = {**order, "idempotency_key": "another", "yes_token_id": "YES2"}
        self.assertEqual(_reserve(other, self.cfg, path=self.path)["reason"], "live_daily_budget_exceeded")

    def test_cancellation_requires_reconciliation_before_releasing_exposure(self):
        order_id = self.execute()["order_id"]
        self.assertEqual(self.service.cancel(order_id)["status"], "cancel_pending")
        self.assertEqual(self.service.reconcile(order_id)["status"], "cancelled_unfilled")
        self.assertEqual(get_live_order(order_id, path=self.path)["status"], "cancelled_unfilled")

    def test_quote_failures_are_not_coerced_into_valid_orders(self):
        for field, value, reason in [
            ("asset_id", "NO", "live_token_contract_mismatch"),
            ("timestamp", "2000-01-01T00:00:00Z", "stale_book"),
            ("timestamp", "2100-01-01T00:00:00Z", "stale_book"),
            ("min_order_size", 100, "below_order_min_size"),
            ("accepting_orders", False, "market_not_accepting_orders"),
            ("asks", [], "insufficient_ask_depth"),
        ]:
            with self.subTest(field=field):
                snapshot = deepcopy(self.snapshot)
                snapshot["quote"][field] = value
                _, reasons = _build_order(self.decision, self.profile, snapshot, 1.1, self.cfg)
                self.assertIn(reason, reasons)
        snapshot = deepcopy(self.snapshot)
        snapshot["quote"]["best_bid"] = None
        with self.assertRaises(TypeError):
            _build_order(self.decision, self.profile, snapshot, 1.1, self.cfg)

    def test_nonfinite_or_above_kelly_amount_is_never_accepted(self):
        with self.assertRaises(ValueError):
            _build_order(self.decision, self.profile, self.snapshot, float("nan"), self.cfg)
        _, reasons = _build_order(self.decision, self.profile, self.snapshot, 100, self.cfg)
        self.assertIn("above_live_risk_size", reasons)

    def test_refreshed_quote_rechecks_canonical_core_effective_edge(self):
        decision = {**self.decision, "model_probability": .281}
        _, reasons = _build_order(decision, self.profile, self.snapshot, .5, self.cfg)
        self.assertIn("core_effective_edge_below_live_min", reasons)
        self.assertNotIn("live_price_moved_or_edge_insufficient", reasons)

    def test_revision_mismatch_and_gate_block_fail_before_transport(self):
        decision = {**self.decision, "live_allowed": False, "live_decision": "blocked", "live_gate_reasons": ["truth_unqualified"]}
        with patch("weatherbot_v3.live_execution.list_signal_decisions", return_value=[decision]), \
             patch("weatherbot_v3.live_execution.get_active_strategy_profile", return_value={"revision_id": "r2", "content_sha256": "other"}), \
             patch("weatherbot_v3.live_execution.signal_decision_prediction_cohort_status", return_value={"ok": True}):
            _, _, reasons = _decision("d1", "r1", path=self.path)
        self.assertIn("live_strategy_revision_mismatch", reasons)
        self.assertIn("live_strategy_parameters_mismatch", reasons)
        self.assertIn("truth_unqualified", reasons)
        self.assertIn("live_decision_expired", reasons)


class LiveApiBoundaryTests(unittest.TestCase):
    @staticmethod
    def request(host="127.0.0.1:8765"):
        from starlette.requests import Request
        return Request({"type": "http", "method": "POST", "path": "/api/v3/live-order",
                        "headers": [(b"host", host.encode())], "client": ("127.0.0.1", 12345)})

    def test_public_hostname_rejected_even_from_tunnel_loopback(self):
        import dashboard_server as api
        with self.assertRaises(api.HTTPException) as caught:
            asyncio.run(api.live_execution_status(self.request("api.polywxx.org")))
        self.assertEqual(caught.exception.status_code, 403)

    def test_submit_requires_explicit_confirmation_and_decision_revision(self):
        import dashboard_server as api
        with self.assertRaises(api.HTTPException) as caught:
            asyncio.run(api.v3_live_order(api.LiveOrderUpdate(), self.request()))
        self.assertEqual(caught.exception.status_code, 409)
        result = asyncio.run(api.v3_live_order(api.LiveOrderUpdate(confirm=True, signal_id=1), self.request()))
        self.assertEqual(result["reason"], "revision_bound_decision_required")

    def test_disabled_submit_cannot_construct_trading_transport(self):
        import dashboard_server as api
        with patch("weatherbot_v3.live_execution.load_config", return_value=SimpleNamespace(live_trading=False)), \
             patch("weatherbot_v3.clob_trading.ClobTradingTransport", side_effect=AssertionError("no credentials or network")):
            result = asyncio.run(api.v3_live_order(api.LiveOrderUpdate(confirm=True, decision_id="d", strategy_revision_id="r"), self.request()))
        self.assertEqual(result["reason"], "live_trading_disabled")


if __name__ == "__main__":
    unittest.main()
