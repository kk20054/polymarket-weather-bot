from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any

from .ai_review import AIReviewer
from .config import load_config
from .db import insert_order, log_risk, upsert_signal
from .notifier import FeishuNotifier
from .polymarket import PolymarketDataClient, MarketQuote, estimate_buy_fill, round_price_to_tick, validate_order_constraints


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

    def place_order(self, signal: dict[str, Any], amount: float | None = None) -> ExecutionResult:
        cfg = load_config()
        signal_id, quote, order, errors = self._prepare(signal, amount)
        order["dry_run"] = cfg.live_dry_run or not cfg.live_trading

        ai_review = AIReviewer().review(signal_id, signal, quote.raw)
        if cfg.ai_required_for_live and (not ai_review.get("approve") or float(ai_review.get("confidence") or 0) < 0.5):
            errors.append("ai_rejected")
        if not cfg.live_trading:
            errors.append("live_trading_disabled")
        if order["amount"] > cfg.live_max_order_usd:
            errors.append("above_live_max_order_usd")
        risk_errors = self._risk_errors(order)
        errors.extend(risk_errors)

        if errors:
            order["status"] = "dry_run" if order["dry_run"] and errors == ["live_trading_disabled"] else "rejected"
            order["failure_reason"] = ",".join(errors)
            order_id = insert_order("live_orders", order)
            log_risk("live_order_blocked", order["failure_reason"], payload=order)
            return ExecutionResult(False, self.mode, order["status"], order_id, order["failure_reason"], order)

        if order["dry_run"]:
            order["status"] = "dry_run"
            order_id = insert_order("live_orders", order)
            return ExecutionResult(True, self.mode, "dry_run", order_id, None, order)

        result = self._submit_clob_order(order)
        order.update(result)
        order_id = insert_order("live_orders", order)
        FeishuNotifier().send(
            "live_order",
            "WeatherBot 实盘下单",
            [
                f"Market: {signal.get('question') or signal.get('market_id')}",
                f"Limit: ${order['limit_price']:.3f}",
                f"Amount: ${order['amount']:.2f}",
                f"Status: {order['status']}",
            ],
            order,
        )
        return ExecutionResult(order["status"] in {"submitted", "open"}, self.mode, order["status"], order_id, order.get("failure_reason"), order)

    def _risk_errors(self, order: dict[str, Any]) -> list[str]:
        # The first implementation keeps hard per-order protection here. Daily
        # and drawdown limits are persisted in the DB and surfaced for audit; a
        # follow-up worker can compute them before live enablement.
        cfg = load_config()
        errors = []
        if order["amount"] > cfg.live_daily_max_usd:
            errors.append("above_live_daily_max_usd")
        return errors

    def _submit_clob_order(self, order: dict[str, Any]) -> dict[str, Any]:
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import OrderArgs
            from py_clob_client.order_builder.constants import BUY
        except Exception as exc:
            return {"status": "rejected", "failure_reason": f"py_clob_client_missing:{exc}"}

        private_key = os.getenv("POLY_PRIVATE_KEY", "")
        if not private_key:
            return {"status": "rejected", "failure_reason": "missing_POLY_PRIVATE_KEY"}
        try:
            host = os.getenv("POLY_CLOB_HOST", "https://clob.polymarket.com")
            chain_id = int(os.getenv("POLY_CHAIN_ID", "137"))
            client = ClobClient(host, key=private_key, chain_id=chain_id)
            creds = client.create_or_derive_api_creds()
            client.set_api_creds(creds)
            signed = client.create_order(
                OrderArgs(
                    price=float(order["limit_price"]),
                    size=float(order["shares"]),
                    side=BUY,
                    token_id=str(order["yes_token_id"]),
                )
            )
            response = client.post_order(signed)
            return {"status": "submitted", "clob_order_id": str(response.get("orderID") or response.get("id") or ""), "raw_response": response}
        except Exception as exc:
            return {"status": "rejected", "failure_reason": f"clob_error:{exc}"}


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
