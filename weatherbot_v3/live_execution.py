"""Revision-bound BUY YES canary execution; never scheduled automatically."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any

from .config import env_value, load_config
from .db import connect, init_v3_db, list_signal_decisions, signal_decision_prediction_cohort_status
from .orderbook_replay import executable_ask_shares
from .paper import _age_seconds
from .polymarket import price_matches_tick
from .sizing import size_position
from .strategy_profiles import get_active_strategy_profile


EXECUTION_VERSION = "live-execution-v2-canary"
TERMINAL_NO_EXPOSURE = {"rejected", "cancelled_unfilled"}


def _number(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non_finite_execution_value")
    return result


def _blocked(reason: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "mode": "live", "status": "blocked", "reason": reason, **extra}


def _decode(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["details"] = json.loads(result.pop("raw_json") or "{}")
    return result


def list_live_orders(*, path: Path | None = None, limit: int = 100) -> list[dict[str, Any]]:
    init_v3_db(path)
    with connect(path) as conn:
        return [_decode(row) for row in conn.execute(
            "SELECT * FROM live_orders ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)
        )]


def get_live_order(order_id: int, *, path: Path | None = None) -> dict[str, Any] | None:
    init_v3_db(path)
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM live_orders WHERE id=?", (order_id,)).fetchone()
    return _decode(row) if row else None


def _reserve(order: dict[str, Any], cfg: Any, *, path: Path | None) -> dict[str, Any]:
    """Reserve once before POST; in-flight and unknown submissions consume budget."""
    init_v3_db(path)
    now = datetime.now(timezone.utc).isoformat()
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        duplicate = conn.execute("SELECT * FROM live_orders WHERE idempotency_key=?", (order["idempotency_key"],)).fetchone()
        if duplicate:
            return {"ok": True, "status": "duplicate", "order": _decode(duplicate)}
        previous = [_decode(row) for row in conn.execute("SELECT * FROM live_orders WHERE COALESCE(dry_run,0)=0")]
        exposed = [row for row in previous if row["status"] not in TERMINAL_NO_EXPOSURE]
        if any(row["yes_token_id"] == order["yes_token_id"] for row in exposed):
            return _blocked("live_token_already_exposed")
        if len(exposed) >= cfg.live_max_open_positions:
            return _blocked("live_max_open_positions")
        daily_committed = sum(float(row["amount"]) for row in exposed if row["created_at"][:10] == now[:10])
        if daily_committed + order["amount"] > cfg.live_daily_max_usd + 1e-9:
            return _blocked("live_daily_budget_exceeded")
        # Until account-wide marked equity is reconciled, use worst-case loss of
        # every outstanding canary. Never treat a missing PnL observation as zero.
        at_risk = sum(float(row["amount"]) for row in exposed)
        if at_risk + order["amount"] > cfg.live_daily_loss_limit + 1e-9:
            return _blocked("live_worst_case_loss_limit")
        if at_risk + order["amount"] > cfg.bankroll_usd * cfg.live_max_drawdown_pct + 1e-9:
            return _blocked("live_worst_case_drawdown_limit")
        pending_cash = sum(float(row["amount"]) for row in exposed if row["status"] not in {"filled", "matched"})
        if pending_cash + order["amount"] > min(order["balance_usd"], order["allowance_usd"]) + 1e-9:
            return _blocked("live_balance_reserved")
        cursor = conn.execute(
            """INSERT INTO live_orders
            (signal_id,idempotency_key,market_id,yes_token_id,side,limit_price,amount,shares,
             status,dry_run,clob_order_id,raw_json,created_at,updated_at)
            VALUES (?,?,?,?, 'BUY',?,?,?, 'reserved',0,?,?,?,?)""",
            (order.get("signal_id"), order["idempotency_key"], order["market_id"], order["yes_token_id"],
             order["limit_price"], order["amount"], order["shares"], order.get("clob_order_id"),
             json.dumps(order, allow_nan=False), now, now),
        )
        return {"ok": True, "status": "reserved", "order_id": cursor.lastrowid}


def _update(order_id: int, result: dict[str, Any], *, path: Path | None) -> dict[str, Any]:
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM live_orders WHERE id=?", (order_id,)).fetchone()
        if not row:
            raise ValueError("live_order_not_found")
        details = json.loads(row["raw_json"] or "{}")
        details.update(result)
        conn.execute(
            "UPDATE live_orders SET status=?,clob_order_id=?,failure_reason=?,raw_json=?,updated_at=? WHERE id=?",
            (result.get("status", row["status"]), result.get("clob_order_id") or row["clob_order_id"],
             result.get("reason"), json.dumps(details, allow_nan=False), datetime.now(timezone.utc).isoformat(), order_id),
        )
    return get_live_order(order_id, path=path) or {}


def _decision(decision_id: str, revision_id: str, *, path: Path | None) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    rows = list_signal_decisions(decision_id=decision_id, path=path, limit=1)
    if not rows:
        return {}, {}, ["signal_decision_not_found"]
    decision = rows[0]
    profile = get_active_strategy_profile("live_default", path=path) or {}
    reasons = []
    if not revision_id or revision_id != decision.get("strategy_revision_id") or revision_id != profile.get("revision_id"):
        reasons.append("live_strategy_revision_mismatch")
    if not decision.get("strategy_params_hash") or decision["strategy_params_hash"] != profile.get("content_sha256"):
        reasons.append("live_strategy_parameters_mismatch")
    if not decision.get("live_allowed") or decision.get("live_decision") != "buy":
        reasons.extend(decision.get("live_gate_reasons") or ["live_decision_not_allowed"])
    if decision.get("ladder_group_id"):
        reasons.append("live_atomic_ladder_not_supported")
    age = _age_seconds(str(decision.get("issued_at") or ""), datetime.now(timezone.utc).isoformat())
    if age is None or age > 30 * 60:
        reasons.append("live_decision_expired")
    cohort = signal_decision_prediction_cohort_status(decision, path=path)
    if not cohort.get("ok", False):
        reasons.append("prediction_source_cohort_invalid")
    with connect(path) as conn:
        bucket = conn.execute("SELECT * FROM market_buckets WHERE bucket_key=?", (decision.get("bucket_key"),)).fetchone()
    if not bucket or bucket["strict_match_status"] != "matched":
        reasons.append("bucket_not_strict_match")
    elif bucket["yes_token_id"] != decision.get("yes_token_id") or bucket["market_id"] != decision.get("market_id"):
        reasons.append("live_token_contract_mismatch")
    return decision, profile, list(dict.fromkeys(reasons))


def _build_order(decision: dict[str, Any], profile: dict[str, Any], snapshot: dict[str, Any], amount: float | None, cfg: Any) -> tuple[dict[str, Any], list[str]]:
    quote = snapshot["quote"]
    policy = profile["parameters"]["decision_policy"]
    sizing_policy = profile["parameters"]["sizing"]
    bid, ask = _number(quote["best_bid"]), _number(quote["best_ask"])
    tick, minimum = _number(quote["tick_size"]), _number(quote["min_order_size"])
    probability = _number(decision["model_probability"])
    reasons = []
    if not 0 < bid <= ask < 1:
        reasons.append("live_invalid_orderbook")
    if str(quote["asset_id"]) != decision["yes_token_id"] or bool(quote["neg_risk"]) != bool(decision["neg_risk"]):
        reasons.append("live_token_contract_mismatch")
    if quote["closed"] or not quote["accepting_orders"]:
        reasons.append("market_not_accepting_orders")
    age = _age_seconds(str(quote["timestamp"]), datetime.now(timezone.utc).isoformat())
    if age is None or age > min(policy["stale_book_seconds"], cfg.orderbook_max_age_minutes * 60):
        reasons.append("stale_book")
    if not cfg.min_price <= ask <= cfg.max_price:
        reasons.append("live_ask_outside_limits")
    if ask <= 0 or (ask - bid) / ask * 10000 > policy["max_spread_bps"] or ask - bid > cfg.max_slippage:
        reasons.append("spread_too_wide")
    strategy = profile["parameters"]["strategies"].get(decision["strategy_name"], {})
    required_edge = max(policy["min_live_trade_edge"], strategy.get("min_edge", 0))
    if probability - ask < required_edge or ask > _number(decision["market_ask"]):
        reasons.append("live_price_moved_or_edge_insufficient")
    if decision["strategy_name"] == "core_modal_v1":
        from .strategies.core_modal import CoreModalStrategy
        core = CoreModalStrategy(strategy)
        metrics = core._edge_metrics(quote, probability)
        if ask < core.min_live_market_ask:
            reasons.append("core_price_below_live_min")
        if metrics["effective_edge"] + 1e-12 < max(core.min_live_effective_edge, required_edge):
            reasons.append("core_effective_edge_below_live_min")
    size = size_position(probability, ask, bankroll=cfg.bankroll_usd,
        max_per_trade_usd=min(cfg.max_bet, cfg.max_per_trade_usd, cfg.live_max_order_usd, cfg.canary_max_order_usd),
        kelly_multiplier=sizing_policy["live_kelly_multiplier"],
        bankroll_fraction_cap=sizing_policy["max_live_bankroll_fraction_per_trade"])
    budget = size.capped_position_size_usd if amount is None else _number(amount)
    if budget <= 0 or budget > size.capped_position_size_usd + 1e-9:
        reasons.append("above_live_risk_size")
    shares = float((Decimal(str(max(0, budget))) / Decimal(str(ask))).quantize(Decimal("0.01"), rounding=ROUND_DOWN)) if ask > 0 else 0
    if tick <= 0 or not price_matches_tick(ask, tick):
        reasons.append("price_not_on_tick")
    if minimum <= 0 or shares < minimum:
        reasons.append("below_order_min_size")
    if executable_ask_shares(quote["asks"], ask) + 1e-9 < shares:
        reasons.append("insufficient_ask_depth")
    balance, allowance = _number(snapshot["balance_usd"]), _number(snapshot["allowance_usd"])
    if budget > min(balance, allowance):
        reasons.append("insufficient_balance_or_allowance")
    identity = f"{EXECUTION_VERSION}:{decision['decision_id']}:{decision['strategy_revision_id']}:{decision['yes_token_id']}"
    order = {
        "execution_version": EXECUTION_VERSION, "idempotency_key": hashlib.sha256(identity.encode()).hexdigest(),
        "decision_id": decision["decision_id"], "strategy_revision_id": decision["strategy_revision_id"],
        "strategy_params_hash": decision["strategy_params_hash"], "signal_id": decision.get("signal_id"),
        "market_id": decision["market_id"], "yes_token_id": decision["yes_token_id"],
        "city_key": decision["city_key"], "target_date": decision["target_date"],
        "side": "BUY", "order_type": "GTC", "limit_price": ask, "shares": shares,
        "amount": float(Decimal(str(shares)) * Decimal(str(ask))),
        "neg_risk": bool(quote["neg_risk"]), "tick_size": str(quote["tick_size"]),
        "execution_quote": quote, "balance_usd": balance, "allowance_usd": allowance,
        "sizing": size.snapshot(),
    }
    return order, reasons


class LiveExecutionService:
    def __init__(self, *, path: Path | None = None, transport: Any = None):
        self.path = path
        self._transport = transport

    @property
    def transport(self):
        if self._transport is None:
            from .clob_trading import ClobTradingTransport, TradingCredentials
            self._transport = ClobTradingTransport(TradingCredentials(
                private_key=env_value("POLY_PRIVATE_KEY"), api_key=env_value("POLY_API_KEY"),
                api_secret=env_value("POLY_API_SECRET"), api_passphrase=env_value("POLY_API_PASSPHRASE"),
                funder=env_value("POLY_FUNDER"), signature_type=int(env_value("POLY_SIGNATURE_TYPE") or "0"),
            ), max_book_age_seconds=load_config().orderbook_max_age_minutes * 60,
                future_skew_seconds=5)
        return self._transport

    def close(self):
        if self._transport is not None:
            self._transport.close()

    def execute(self, decision_id: str, revision_id: str, *, amount: float | None = None, preview: bool = True) -> dict[str, Any]:
        from .executor import LIVE_EXECUTION_PRODUCTION_READY
        cfg = load_config()
        if not preview:
            if not cfg.live_trading or cfg.live_dry_run:
                return _blocked("live_trading_disabled")
            if not LIVE_EXECUTION_PRODUCTION_READY:
                return _blocked("live_executor_not_production_ready")
        decision, profile, reasons = _decision(decision_id, revision_id, path=self.path)
        if reasons:
            return _blocked(reasons[0], reasons=reasons, preview=preview)
        snapshot = self.transport.preflight(decision["market_id"], decision["yes_token_id"])
        if snapshot.get("ok") is False:
            return _blocked(snapshot["reason"], preview=preview)
        order, reasons = _build_order(decision, profile, snapshot, amount, cfg)
        if reasons:
            return _blocked(reasons[0], reasons=reasons, preview=preview)
        if cfg.ai_required_for_live:
            from .ai_review import AIReviewer
            review = AIReviewer().review(int(decision.get("signal_id") or 0), decision, snapshot["quote"])
            if not review.get("approve") or _number(review.get("confidence", 0)) < 0.5:
                return _blocked("ai_rejected")
        if preview:
            return {"ok": True, "mode": "live", "status": "preview", "order": order, "submitted": False}
        # No authorization is inferred from preview, an SDK install, or a UI mode.
        reservation = _reserve(order, cfg, path=self.path)
        if reservation["status"] != "reserved":
            return reservation
        order_id = int(reservation["order_id"])
        try:
            prepared = self.transport.prepare_limit_buy(order["yes_token_id"], order["limit_price"], order["shares"], neg_risk=order["neg_risk"], tick_size=order["tick_size"])
        except Exception as exc:
            # Before submission only: a signing failure cannot have placed an order.
            _update(order_id, {"status": "rejected", "reason": f"signing_failed:{type(exc).__name__}"}, path=self.path)
            raise
        _, _, changed_reasons = _decision(decision_id, revision_id, path=self.path)
        current_cfg = load_config()
        if changed_reasons or not current_cfg.live_trading or current_cfg.live_dry_run:
            reason = changed_reasons[0] if changed_reasons else "live_trading_disabled"
            _update(order_id, {"status": "rejected", "reason": reason}, path=self.path)
            return _blocked(reason, order_id=order_id)
        age = _age_seconds(str(order["execution_quote"]["timestamp"]), datetime.now(timezone.utc).isoformat())
        if age is None or age > profile["parameters"]["decision_policy"]["stale_book_seconds"]:
            _update(order_id, {"status": "rejected", "reason": "stale_book_before_submit"}, path=self.path)
            return _blocked("stale_book_before_submit", order_id=order_id)
        _update(order_id, {"status": "submitting", "clob_order_id": prepared.order_hash}, path=self.path)
        try:
            result = self.transport.submit_prepared(prepared)
        except Exception as exc:
            # Once POST may have started, even an unexpected error is ambiguous.
            _update(order_id, {"status": "unknown", "reason": f"submit_unknown:{type(exc).__name__}"}, path=self.path)
            raise
        if result.get("status") == "blocked":
            # Transport distinguishes a pre-POST block from an ambiguous POST.
            result = {**result, "status": "rejected"}
        stored = _update(order_id, result, path=self.path)
        return {"ok": stored["status"] in {"open", "submitted", "matched", "filled"}, "mode": "live", "status": stored["status"], "order_id": order_id, "order": stored, "reason": result.get("reason")}

    def reconcile(self, order_id: int) -> dict[str, Any]:
        order = get_live_order(order_id, path=self.path)
        if not order or order["dry_run"]:
            return _blocked("live_order_not_found")
        if not order.get("clob_order_id"):
            return _blocked("submission_unknown_manual_reconciliation_required", order=order)
        result = self.transport.get_order(order["clob_order_id"])
        if not result.get("ok") or result.get("status") != "found":
            return _blocked("order_reconciliation_unavailable", order=order)
        if (result.get("asset_id") != order["yes_token_id"] or result.get("side") != "BUY"
                or _number(result["original_size"]) != _number(order["shares"])
                or _number(result["price"]) != _number(order["limit_price"])):
            return _blocked("order_reconciliation_identity_mismatch", order=order)
        matched = _number(result["size_matched"])
        exchange_status = str(result["exchange_status"]).upper()
        if exchange_status in {"CANCELED", "CANCELLED"}:
            status = "cancelled_unfilled" if matched == 0 and not result["trade_ids"] else "cancelled_partial"
        elif exchange_status == "MATCHED":
            status = "matched"
        elif exchange_status == "LIVE":
            status = "partial" if matched > 0 else "open"
        else:
            return _blocked("order_exchange_status_unresolved", order=order)
        result = {**result, "status": status, "filled_shares": matched}
        result["clob_order_id"] = order["clob_order_id"]
        stored = _update(order_id, result, path=self.path)
        return {"ok": True, "order": stored, "status": stored["status"]}

    def cancel(self, order_id: int) -> dict[str, Any]:
        order = get_live_order(order_id, path=self.path)
        if not order or order["dry_run"] or not order.get("clob_order_id"):
            return _blocked("live_order_id_required_for_cancel")
        result = self.transport.cancel_order(order["clob_order_id"])
        # A cancellation acknowledgment is not proof that zero shares filled.
        status = "cancel_pending" if result.get("ok") else order["status"]
        stored = _update(order_id, {"status": status, "cancel_result": result}, path=self.path)
        return {"ok": bool(result.get("ok")), "status": status, "reason": result.get("reason"), "order": stored}


def run_live_operation(operation: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    if operation not in {"execute", "reconcile", "cancel"}:
        raise ValueError("unsupported_live_operation")
    service = LiveExecutionService()
    try:
        return getattr(service, operation)(*args, **kwargs)
    finally:
        service.close()
