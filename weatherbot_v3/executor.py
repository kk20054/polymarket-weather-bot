from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .config import load_config
from .db import insert_order, log_risk, upsert_signal
from .notifier import FeishuNotifier
from .polymarket import PolymarketDataClient, MarketQuote, estimate_buy_fill, round_price_to_tick, validate_order_constraints


LIVE_EXECUTION_VERSION = "live-execution-v2-canary"
LIVE_EXECUTION_PRODUCTION_READY = False


@dataclass(frozen=True)
class ExecutionResult:
    ok: bool
    mode: str
    status: str
    order_id: int
    reason: str | None
    payload: dict[str, Any]


class BaseExecutor:
    mode = "base"

    def place_order(self, signal: dict[str, Any], amount: float | None = None) -> ExecutionResult:
        raise NotImplementedError

    def _prepare(self, signal: dict[str, Any], amount: float | None) -> tuple[int, MarketQuote, dict[str, Any], list[str]]:
        cfg = load_config()
        signal_id = upsert_signal(signal, _legacy_id(signal))
        quote = PolymarketDataClient().quote(str(signal.get("market_id") or ""))
        requested = amount if amount is not None else _num(signal.get("sim_amount"), _num(signal.get("amount"), _num(signal.get("cost"), 0.0)))
        requested = min(float(requested), cfg.max_bet)
        limit = round_price_to_tick(min(_num(signal.get("limit_price"), _num(signal.get("entry_price"), quote.best_ask)), quote.best_ask), quote.tick_size)
        shares = round(requested / limit, 4) if limit > 0 else 0.0
        order = {
            "signal_id": signal_id,
            "idempotency_key": _idempotency_key(self.mode, signal, limit, requested),
            "market_id": quote.market_id,
            "yes_token_id": quote.yes_token_id or str(signal.get("yes_token_id") or ""),
            "side": "BUY",
            "limit_price": limit,
            "amount": round(requested, 2),
            "shares": shares,
            "status": "created",
            "failure_reason": None,
        }
        errors = validate_order_constraints(quote, requested, limit)
        if quote.book_source != "clob":
            errors.append("orderbook_not_clob")
        return signal_id, quote, order, errors


class PaperExecutor(BaseExecutor):
    mode = "paper"

    def place_order(self, signal: dict[str, Any], amount: float | None = None) -> ExecutionResult:
        signal_id, quote, order, errors = self._prepare(signal, amount)
        if errors:
            order["status"] = "rejected"
            order["failure_reason"] = ",".join(errors)
            order_id = insert_order("paper_orders", order)
            log_risk("paper_order_rejected", order["failure_reason"], payload=order)
            return ExecutionResult(False, self.mode, "rejected", order_id, order["failure_reason"], order)
        fill = estimate_buy_fill(quote, order["amount"], order["limit_price"])
        if fill["filled_shares"] <= 0:
            order["status"] = "rejected"
            order["failure_reason"] = "insufficient_ask_depth"
            order["fill"] = fill
            order_id = insert_order("paper_orders", order)
            log_risk("paper_order_rejected", order["failure_reason"], payload=order)
            return ExecutionResult(False, self.mode, "rejected", order_id, order["failure_reason"], order)
        order["status"] = "paper_filled" if fill["fully_filled"] else "paper_partial"
        order["amount"] = round(fill["filled_amount"], 2)
        order["shares"] = round(fill["filled_shares"], 4)
        order["average_fill_price"] = fill["average_price"]
        order["fill"] = fill
        order["raw_quote"] = quote.raw
        order_id = insert_order("paper_orders", order)
        FeishuNotifier().send(
            "paper_order",
            "WeatherBot 模拟买入",
            [
                f"Market: {signal.get('question') or signal.get('market_id')}",
                f"Limit: ${order['limit_price']:.3f}",
                f"Amount: ${order['amount']:.2f}",
                f"Shares: {order['shares']:.2f}",
            ],
            order,
        )
        return ExecutionResult(True, self.mode, order["status"], order_id, None, order)


class LiveExecutor(BaseExecutor):
    mode = "live"

    def place_order(
        self,
        signal: dict[str, Any],
        amount: float | None = None,
        *,
        force_dry_run: bool = False,
    ) -> ExecutionResult:
        cfg = load_config()
        requested_live_submit = bool(
            cfg.live_trading
            and not cfg.live_dry_run
            and not force_dry_run
        )
        if requested_live_submit and not LIVE_EXECUTION_PRODUCTION_READY:
            return ExecutionResult(
                False,
                self.mode,
                "blocked",
                0,
                "live_executor_not_production_ready",
                {
                    "dry_run": False,
                    "execution_version": LIVE_EXECUTION_VERSION,
                    "production_ready": False,
                },
            )
        decision_id = str(signal.get("decision_id") or "")
        revision_id = str(signal.get("strategy_revision_id") or "")
        if not decision_id or not revision_id:
            return ExecutionResult(False, self.mode, "blocked", 0, "revision_bound_decision_required", {})
        from .live_execution import run_live_operation
        result = run_live_operation(
            "execute", decision_id, revision_id, amount=amount, preview=not requested_live_submit,
        )
        return ExecutionResult(bool(result["ok"]), self.mode, result["status"], int(result.get("order_id") or 0), result.get("reason"), result)


def _idempotency_key(mode: str, signal: dict[str, Any], price: float, amount: float) -> str:
    raw = f"{mode}:{signal.get('market_id')}:{signal.get('yes_token_id')}:{price}:{amount}:{signal.get('created_at')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _legacy_id(signal: dict[str, Any]) -> int | None:
    try:
        return int(signal.get("id"))
    except Exception:
        return None


def _num(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default
