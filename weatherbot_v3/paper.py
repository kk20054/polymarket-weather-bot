from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import load_config
from .db import (
    connect,
    get_paper_order_by_idempotency_key,
    insert_fill_record,
    list_paper_orders,
    list_signal_decisions,
    log_risk,
    open_paper_order_for_token,
    paper_execution_summary,
    persist_paper_order_fill_group,
    upsert_paper_order_record,
)
from .polymarket import price_matches_tick


PAPER_EXECUTION_VERSION = "paper-execution-v2"
MAX_QUOTE_CLOCK_SKEW_SECONDS = 5.0


def execute_paper_decision(
    decision_id: str,
    *,
    amount: float | None = None,
    dry_run: bool = False,
    path: Path | None = None,
) -> dict[str, Any]:
    decision_key = str(decision_id or "").strip()
    if not decision_key:
        return {"ok": False, "status": "blocked", "reason": "decision_id_required"}
    rows = list_signal_decisions(decision_id=decision_key, limit=1, path=path)
    if not rows:
        return {
            "ok": False,
            "status": "blocked",
            "reason": "signal_decision_not_found",
            "decision_id": decision_key,
        }
    return execute_paper_decision_record(rows[0], amount=amount, dry_run=dry_run, path=path)


def execute_paper_decision_record(
    decision: dict[str, Any],
    *,
    amount: float | None = None,
    dry_run: bool = False,
    path: Path | None = None,
    cohort_run_id: str = "",
    max_per_trade_usd: float | None = None,
    sizing_snapshot: dict[str, Any] | None = None,
    max_book_age_seconds: float | None = None,
    max_spread_bps: float | None = None,
) -> dict[str, Any]:
    ladder_group_id = str(decision.get("ladder_group_id") or "").strip()
    if ladder_group_id:
        return execute_paper_ladder_group(
            ladder_group_id,
            amount=amount,
            dry_run=dry_run,
            path=path,
            seed_decision=decision,
            cohort_run_id=cohort_run_id,
            max_per_trade_usd=max_per_trade_usd,
            sizing_snapshot=sizing_snapshot,
            max_book_age_seconds=max_book_age_seconds,
            max_spread_bps=max_spread_bps,
        )
    return _execute_single_paper_decision_record(
        decision,
        amount=amount,
        dry_run=dry_run,
        path=path,
        cohort_run_id=cohort_run_id,
        max_per_trade_usd=max_per_trade_usd,
        sizing_snapshot=sizing_snapshot,
        max_book_age_seconds=max_book_age_seconds,
        max_spread_bps=max_spread_bps,
    )


def _execute_single_paper_decision_record(
    decision: dict[str, Any],
    *,
    amount: float | None = None,
    dry_run: bool = False,
    path: Path | None = None,
    cohort_run_id: str = "",
    max_per_trade_usd: float | None = None,
    sizing_snapshot: dict[str, Any] | None = None,
    max_book_age_seconds: float | None = None,
    max_spread_bps: float | None = None,
) -> dict[str, Any]:
    cfg = load_config()
    now = datetime.now(timezone.utc).isoformat()
    execution_decision = _decision_with_latest_quote(decision, path=path, now=now)
    order = _base_order(
        execution_decision,
        amount,
        now,
        cohort_run_id=cohort_run_id,
        max_per_trade_usd=max_per_trade_usd,
        sizing_snapshot=sizing_snapshot,
    )
    existing = get_paper_order_by_idempotency_key(order["idempotency_key"], path=path)
    if existing:
        return {
            "ok": True,
            "status": "duplicate",
            "reason": "paper_order_already_exists",
            "dry_run": dry_run,
            "decision_id": order["decision_id"],
            "order_id": existing.get("id"),
            "order": existing,
        }

    risk_reasons = _risk_reasons(
        execution_decision,
        order,
        cfg,
        path=path,
        max_book_age_seconds=max_book_age_seconds,
        max_spread_bps=max_spread_bps,
    )
    if risk_reasons:
        order.update({
            "status": "rejected",
            "lifecycle_status": "rejected",
            "fill_status": "rejected",
            "failure_reason": ",".join(risk_reasons),
            "risk_reasons": risk_reasons,
        })
        if dry_run:
            return {
                "ok": False,
                "status": "rejected",
                "reason": order["failure_reason"],
                "dry_run": True,
                "decision_id": order["decision_id"],
                "order": order,
            }
        order_id = upsert_paper_order_record(order, path=path)
        log_risk("paper_order_rejected", order["failure_reason"], payload=order)
        stored = list_paper_orders(decision_id=order["decision_id"], limit=1, path=path)
        return {
            "ok": False,
            "status": "rejected",
            "reason": order["failure_reason"],
            "dry_run": False,
            "decision_id": order["decision_id"],
            "order_id": order_id,
            "order": stored[0] if stored else order,
        }

    filled = _simulate_fill(execution_decision, order)
    order.update(filled)
    if dry_run:
        return {
            "ok": True,
            "status": order["status"],
            "reason": None,
            "dry_run": True,
            "decision_id": order["decision_id"],
            "order": order,
            "fill": filled.get("fill"),
        }

    order_id = upsert_paper_order_record(order, path=path)
    fill = dict(filled["fill"])
    fill.update({
        "idempotency_key": f"{order['idempotency_key']}:fill:0",
        "order_id": order_id,
        "order_type": "paper",
        "decision_id": order["decision_id"],
        "market_id": order["market_id"],
        "yes_token_id": order["yes_token_id"],
        "fill_status": order["fill_status"],
        "source": PAPER_EXECUTION_VERSION,
    })
    fill_id = insert_fill_record(fill, path=path)
    stored = list_paper_orders(decision_id=order["decision_id"], limit=1, path=path)
    return {
        "ok": True,
        "status": order["status"],
        "reason": None,
        "dry_run": False,
        "decision_id": order["decision_id"],
        "order_id": order_id,
        "fill_id": fill_id,
        "order": stored[0] if stored else order,
        "fill": fill,
    }


def execute_paper_decisions(
    *,
    city_key: str | None = None,
    target_date: str | None = None,
    limit: int = 20,
    amount: float | None = None,
    strategies: list[str] | None = None,
    strategy_revision_id: str = "",
    decision_batch_issued_at: str = "",
    dry_run: bool = True,
    path: Path | None = None,
) -> dict[str, Any]:
    allowed_strategies = set(strategies or [])
    selected: list[dict[str, Any]] = []
    seen_ladder_groups: set[str] = set()
    for row in list_signal_decisions(city_key=city_key, target_date=target_date, limit=limit, path=path):
        if strategy_revision_id and str(row.get("strategy_revision_id") or "") != str(strategy_revision_id):
            continue
        if decision_batch_issued_at and str(row.get("issued_at") or "") != str(decision_batch_issued_at):
            continue
        if allowed_strategies and str(row.get("strategy_name") or "single_bucket_ev") not in allowed_strategies:
            continue
        if not bool(row.get("paper_allowed")) or str(row.get("paper_decision") or "") != "buy":
            continue
        ladder_group_id = str(row.get("ladder_group_id") or "")
        if ladder_group_id:
            if ladder_group_id in seen_ladder_groups:
                continue
            seen_ladder_groups.add(ladder_group_id)
        selected.append(row)
    rows = selected
    results = [
        execute_paper_decision_record(row, amount=amount, dry_run=dry_run, path=path)
        for row in rows[: max(1, min(int(limit or 20), 100))]
    ]
    return {
        "ok": all(item.get("ok") or item.get("status") == "duplicate" for item in results),
        "dry_run": dry_run,
        "execution_version": PAPER_EXECUTION_VERSION,
        "strategy_revision_id": strategy_revision_id,
        "decision_batch_issued_at": decision_batch_issued_at,
        "requested": len(rows),
        "executed": sum(1 for item in results if item.get("ok") and item.get("status") != "duplicate"),
        "duplicates": sum(1 for item in results if item.get("status") == "duplicate"),
        "rejected": sum(1 for item in results if item.get("status") == "rejected"),
        "results": results,
        "summary": paper_execution_summary(city_key=city_key, target_date=target_date, path=path),
    }


def execute_paper_ladder_group(
    ladder_group_id: str,
    *,
    amount: float | None = None,
    dry_run: bool = False,
    path: Path | None = None,
    seed_decision: dict[str, Any] | None = None,
    cohort_run_id: str = "",
    max_per_trade_usd: float | None = None,
    sizing_snapshot: dict[str, Any] | None = None,
    max_book_age_seconds: float | None = None,
    max_spread_bps: float | None = None,
) -> dict[str, Any]:
    group_id = str(ladder_group_id or "").strip()
    if not group_id:
        return {"ok": False, "status": "blocked", "reason": "ladder_group_id_required"}
    all_rows = list_signal_decisions(
        city_key=(seed_decision or {}).get("city_key"),
        target_date=(seed_decision or {}).get("target_date"),
        limit=1000,
        path=path,
    )
    rows = [row for row in all_rows if str(row.get("ladder_group_id") or "") == group_id]
    rows.sort(key=lambda row: (_num(row.get("bucket_lower"), -9999.0) or -9999.0, _num(row.get("bucket_upper"), 9999.0) or 9999.0))
    if len(rows) != 3:
        return {
            "ok": False,
            "status": "rejected",
            "reason": "ladder_group_incomplete",
            "dry_run": dry_run,
            "ladder_group_id": group_id,
            "group_size": len(rows),
        }

    cfg = load_config()
    now = datetime.now(timezone.utc).isoformat()
    rows = [_decision_with_latest_quote(row, path=path, now=now) for row in rows]
    allocations = _ladder_allocations(rows, amount)
    orders = [
        _base_order(
            row,
            allocations.get(str(row.get("decision_id") or ""), None),
            now,
            cohort_run_id=cohort_run_id,
            max_per_trade_usd=max_per_trade_usd,
            sizing_snapshot=sizing_snapshot,
        )
        for row in rows
    ]
    existing = [get_paper_order_by_idempotency_key(order["idempotency_key"], path=path) for order in orders]
    existing_count = sum(1 for item in existing if item)
    if existing_count == len(rows):
        return {
            "ok": True,
            "status": "duplicate",
            "reason": "paper_ladder_group_already_exists",
            "dry_run": dry_run,
            "ladder_group_id": group_id,
            "orders": [item for item in existing if item],
        }
    if existing_count:
        return {
            "ok": False,
            "status": "rejected",
            "reason": "paper_ladder_group_partially_exists",
            "dry_run": dry_run,
            "ladder_group_id": group_id,
            "existing": existing_count,
        }

    grouped_reasons: dict[str, list[str]] = {}
    for row, order in zip(rows, orders):
        reasons = _risk_reasons(
            row,
            order,
            cfg,
            path=path,
            max_book_age_seconds=max_book_age_seconds,
            max_spread_bps=max_spread_bps,
        )
        requested_shares = _num((order.get("derived") or {}).get("requested_shares"), 0.0) or 0.0
        ask_depth = _num((row.get("orderbook_snapshot") or {}).get("ask_depth"), 0.0) or 0.0
        if ask_depth + 1e-9 < requested_shares:
            reasons.append("insufficient_depth_for_atomic_ladder")
        if reasons:
            grouped_reasons[str(row.get("decision_id") or row.get("bucket_key") or "")] = _unique(reasons)
    if grouped_reasons:
        payload = {
            "ladder_group_id": group_id,
            "risk_reasons_by_decision": grouped_reasons,
            "decision_ids": [row.get("decision_id") for row in rows],
        }
        if not dry_run:
            log_risk("paper_ladder_group_rejected", "ladder_group_atomic_precheck_failed", payload=payload)
        return {
            "ok": False,
            "status": "rejected",
            "reason": "ladder_group_atomic_precheck_failed",
            "dry_run": dry_run,
            "ladder_group_id": group_id,
            "risk_reasons_by_decision": grouped_reasons,
        }

    simulated: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row, order in zip(rows, orders):
        order.update(_simulate_fill(row, order))
        if order["fill_status"] != "filled":
            return {
                "ok": False,
                "status": "rejected",
                "reason": "ladder_group_atomic_fill_failed",
                "dry_run": dry_run,
                "ladder_group_id": group_id,
            }
        fill = {
            **dict(order["fill"]),
            "idempotency_key": f"{order['idempotency_key']}:fill:0",
            "order_type": "paper",
            "decision_id": order["decision_id"],
            "market_id": order["market_id"],
            "yes_token_id": order["yes_token_id"],
            "fill_status": order["fill_status"],
            "source": PAPER_EXECUTION_VERSION,
        }
        simulated.append((order, fill))

    stored_ids = [] if dry_run else persist_paper_order_fill_group(simulated, path=path)
    results = []
    for index, (order, fill) in enumerate(simulated):
        ids = stored_ids[index] if index < len(stored_ids) else {}
        stored = (
            list_paper_orders(decision_id=order["decision_id"], limit=1, path=path)
            if not dry_run
            else []
        )
        results.append({
            "ok": True,
            "status": order["status"],
            "reason": None,
            "dry_run": dry_run,
            "decision_id": order["decision_id"],
            "order_id": ids.get("order_id"),
            "fill_id": ids.get("fill_id"),
            "order": stored[0] if stored else order,
            "fill": fill,
        })
    return {
        "ok": True,
        "status": "paper_ladder_filled" if not dry_run else "paper_ladder_dry_run",
        "reason": None,
        "dry_run": dry_run,
        "ladder_group_id": group_id,
        "requested": len(rows),
        "executed": sum(1 for result in results if result.get("ok") and result.get("status") != "duplicate"),
        "results": results,
    }


def _base_order(
    decision: dict[str, Any],
    amount: float | None,
    opened_at: str,
    *,
    cohort_run_id: str = "",
    max_per_trade_usd: float | None = None,
    sizing_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = load_config()
    market_ask = _num(decision.get("market_ask"))
    market_bid = _num(decision.get("market_bid"))
    tick_size = _num(decision.get("tick_size"))
    order_min_size = _num(decision.get("order_min_size"))
    default_amount = decision.get("position_size_usd") if amount is None else amount
    explicit_cap = float(max_per_trade_usd) if max_per_trade_usd is not None else float(cfg.max_per_trade_usd)
    requested_amount, cap_reasons = _requested_amount(default_amount, explicit_cap)
    effective_sizing_snapshot = sizing_snapshot or {
        "mode": "manual_override" if amount is not None else "decision_suggestion",
        "strategy_revision_id": decision.get("strategy_revision_id") or "legacy_unversioned",
        "requested_before_cap_usd": _num(default_amount, 0.0) or 0.0,
        "explicit_max_per_trade_usd": explicit_cap,
        "final_position_size_usd": requested_amount,
        "cap_reasons": cap_reasons,
    }
    limit_price = market_ask if market_ask is not None else 0.0
    shares = requested_amount / limit_price if limit_price > 0 else 0.0
    decision_id = str(decision.get("decision_id") or "")
    return {
        "decision_id": decision_id,
        "signal_id": decision.get("signal_id"),
        "idempotency_key": _idempotency_key(
            decision,
            requested_amount,
            limit_price,
            cohort_run_id=cohort_run_id,
        ),
        "market_id": str(decision.get("market_id") or ""),
        "yes_token_id": str(decision.get("yes_token_id") or decision.get("token_id") or ""),
        "bucket_key": str(decision.get("bucket_key") or ""),
        "strategy_name": str(decision.get("strategy_name") or "single_bucket_ev"),
        "ladder_group_id": str(decision.get("ladder_group_id") or ""),
        "strategy_revision_id": str(decision.get("strategy_revision_id") or ""),
        "strategy_params_hash": str(decision.get("strategy_params_hash") or ""),
        "strategy_params_snapshot": decision.get("strategy_params_snapshot") or {},
        "sizing_snapshot": effective_sizing_snapshot,
        "execution_quote": decision.get("execution_quote") or {},
        "cap_reasons": cap_reasons or list((sizing_snapshot or {}).get("cap_reasons") or []),
        "city_key": str(decision.get("city_key") or ""),
        "target_date": str(decision.get("target_date") or ""),
        "event_url": str((decision.get("evidence_links") or {}).get("event_url") or ""),
        "side": "BUY",
        "limit_price": limit_price,
        "requested_amount": requested_amount,
        "amount": 0.0,
        "shares": 0.0,
        "filled_amount": 0.0,
        "filled_shares": 0.0,
        "unfilled_amount": requested_amount,
        "average_fill_price": None,
        "mark_price": market_bid,
        "unrealized_pnl": 0.0,
        "realized_pnl": 0.0,
        "status": "created",
        "lifecycle_status": "created",
        "fill_status": "created",
        "order_version": PAPER_EXECUTION_VERSION,
        "model_probability": decision.get("model_probability"),
        "market_probability": decision.get("market_implied_probability"),
        "edge": decision.get("edge"),
        "gate_status": decision.get("gate_status"),
        "risk_reasons": [],
        "failure_reason": None,
        "orderbook_snapshot": decision.get("orderbook_snapshot") or {},
        "evidence_links": decision.get("evidence_links") or {},
        "opened_at": opened_at,
        "closed_at": "",
        "cohort_run_id": str(cohort_run_id or ""),
        "derived": {
            "market_ask": market_ask,
            "market_bid": market_bid,
            "tick_size": tick_size,
            "order_min_size": order_min_size,
            "requested_shares": shares,
        },
    }


def _decision_with_latest_quote(
    decision: dict[str, Any],
    *,
    path: Path | None,
    now: str,
) -> dict[str, Any]:
    result = dict(decision)
    token = str(decision.get("yes_token_id") or decision.get("token_id") or "")
    market_id = str(decision.get("market_id") or "")
    latest = None
    if token or market_id:
        with connect(path) as conn:
            latest = conn.execute(
                """
                SELECT *
                FROM orderbooks
                WHERE (yes_token_id=? AND ? != '') OR (market_id=? AND ? != '')
                ORDER BY COALESCE(NULLIF(quote_timestamp, ''), created_at) DESC, id DESC
                LIMIT 1
                """,
                (token, token, market_id, market_id),
            ).fetchone()
    original_snapshot = dict(decision.get("orderbook_snapshot") or {})
    if latest:
        row = dict(latest)
        snapshot = {
            **original_snapshot,
            "snapshot_key": row.get("snapshot_key"),
            "best_bid": row.get("best_bid"),
            "best_ask": row.get("best_ask"),
            "spread": row.get("spread"),
            "bid_depth": row.get("bid_depth"),
            "ask_depth": row.get("ask_depth"),
            "quote_timestamp": row.get("quote_timestamp") or row.get("created_at"),
            "source": row.get("snapshot_type") or "stored_orderbook",
        }
        result["market_bid"] = row.get("best_bid")
        result["market_ask"] = row.get("best_ask")
        result["market_mid"] = (
            (float(row["best_bid"]) + float(row["best_ask"])) / 2.0
            if row.get("best_bid") is not None and row.get("best_ask") is not None
            else result.get("market_mid")
        )
        result["tick_size"] = row.get("tick_size") or result.get("tick_size")
        result["order_min_size"] = row.get("order_min_size") or result.get("order_min_size")
    else:
        snapshot = original_snapshot
    quote_timestamp = str(
        snapshot.get("quote_timestamp")
        or decision.get("quote_timestamp")
        or ""
    )
    age_seconds = _age_seconds(quote_timestamp, now)
    result["book_age_seconds"] = age_seconds
    result["orderbook_snapshot"] = snapshot
    result["execution_quote"] = {
        "snapshot_key": snapshot.get("snapshot_key"),
        "quote_timestamp": quote_timestamp,
        "age_seconds": age_seconds,
        "best_bid": result.get("market_bid"),
        "best_ask": result.get("market_ask"),
        "spread": snapshot.get("spread"),
        "bid_depth": snapshot.get("bid_depth"),
        "ask_depth": snapshot.get("ask_depth"),
        "source": snapshot.get("source") or "signal_snapshot_fallback",
        "fallback": not bool(latest),
    }
    return result


def refresh_paper_decision_quote(
    decision: dict[str, Any],
    *,
    path: Path | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Return a decision bound to the latest locally persisted orderbook quote."""
    return _decision_with_latest_quote(
        decision,
        path=path,
        now=now or datetime.now(timezone.utc).isoformat(),
    )


def _age_seconds(value: str, now: str) -> float | None:
    try:
        text = str(value or "").strip()
        numeric = float(text)
        if math.isfinite(numeric) and numeric > 0:
            if numeric >= 1_000_000_000_000:
                numeric /= 1000.0
            parsed = datetime.fromtimestamp(numeric, tz=timezone.utc)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None
    try:
        current = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        age_seconds = (current - parsed).total_seconds()
        if age_seconds < -MAX_QUOTE_CLOCK_SKEW_SECONDS:
            return None
        return max(0.0, age_seconds)
    except Exception:
        return None


def _risk_reasons(
    decision: dict[str, Any],
    order: dict[str, Any],
    cfg: Any,
    *,
    path: Path | None,
    max_book_age_seconds: float | None = None,
    max_spread_bps: float | None = None,
) -> list[str]:
    reasons: list[str] = []
    if not bool(decision.get("paper_allowed")) or str(decision.get("paper_decision") or "") != "buy":
        reasons.append("paper_gate_not_passed")
    if str(decision.get("gate_status") or "") != "paper_allowed":
        reasons.append("decision_gate_not_paper_allowed")
    if not order["market_id"]:
        reasons.append("market_id_missing")
    if not order["yes_token_id"]:
        reasons.append("yes_token_missing")
    market_ask = _num(decision.get("market_ask"))
    market_bid = _num(decision.get("market_bid"))
    tick_size = _num(decision.get("tick_size"))
    order_min_size = _num(decision.get("order_min_size"))
    ask_depth = _num((decision.get("orderbook_snapshot") or {}).get("ask_depth"))
    spread = _num((decision.get("orderbook_snapshot") or {}).get("spread"))
    book_age_seconds = _num((order.get("execution_quote") or {}).get("age_seconds"), _num(decision.get("book_age_seconds")))
    if market_ask is None or market_ask <= 0 or market_ask >= 1:
        reasons.append("best_ask_missing_or_invalid")
    if market_bid is None or market_bid < 0:
        reasons.append("best_bid_missing_or_invalid")
    if market_ask is not None and market_ask > cfg.max_price:
        reasons.append("ask_above_max_price")
    if market_ask is not None and market_ask < cfg.min_price:
        reasons.append("ask_below_min_price")
    if spread is not None and spread > cfg.max_slippage:
        reasons.append("spread_above_max_slippage")
    if (
        max_spread_bps is not None
        and market_ask is not None
        and market_ask > 0
        and spread is not None
        and spread / market_ask * 10_000.0 > float(max_spread_bps) + 1e-9
    ):
        reasons.append("spread_too_wide")
    if tick_size is None or tick_size <= 0:
        reasons.append("tick_size_missing")
    elif market_ask is not None and not price_matches_tick(market_ask, tick_size):
        reasons.append("price_not_on_tick")
    if order["requested_amount"] <= 0:
        reasons.append("non_positive_amount")
    requested_shares = _num((order.get("derived") or {}).get("requested_shares"), 0.0) or 0.0
    if order_min_size is None or order_min_size <= 0:
        reasons.append("order_min_size_missing")
    elif requested_shares + 1e-9 < order_min_size:
        reasons.append("below_order_min_size")
    if ask_depth is None or ask_depth <= 0:
        reasons.append("ask_depth_missing")
    if book_age_seconds is None:
        reasons.append("orderbook_timestamp_missing_or_invalid")
    elif book_age_seconds > (
        float(max_book_age_seconds)
        if max_book_age_seconds is not None
        else cfg.orderbook_max_age_minutes * 60
    ):
        reasons.append("orderbook_stale")
    duplicate = open_paper_order_for_token(order["yes_token_id"], path=path)
    if duplicate and duplicate.get("idempotency_key") != order["idempotency_key"]:
        reasons.append("duplicate_open_paper_order_for_token")
    return _unique(reasons + [str(reason) for reason in decision.get("gate_reasons") or [] if reason in {
        "bucket_not_strict_match",
        "yes_token_missing",
        "tick_size_missing",
        "order_min_size_missing",
        "orderbook_disabled",
        "market_probability_missing",
        "model_probability_missing",
        "low_price_tail_bucket",
        "spread_too_wide",
    }])


def _simulate_fill(decision: dict[str, Any], order: dict[str, Any]) -> dict[str, Any]:
    market_ask = _num(decision.get("market_ask"), 0.0) or 0.0
    market_bid = _num(decision.get("market_bid"), 0.0) or 0.0
    ask_depth = _num((decision.get("orderbook_snapshot") or {}).get("ask_depth"), 0.0) or 0.0
    requested_shares = _num((order.get("derived") or {}).get("requested_shares"), 0.0) or 0.0
    fill_shares = min(requested_shares, ask_depth)
    fill_amount = fill_shares * market_ask
    unfilled_amount = max(0.0, order["requested_amount"] - fill_amount)
    fill_status = "filled" if unfilled_amount <= 0.01 else "partial"
    unrealized = (market_bid - market_ask) * fill_shares
    return {
        "status": "paper_filled" if fill_status == "filled" else "paper_partial",
        "lifecycle_status": "open",
        "fill_status": fill_status,
        "amount": round(fill_amount, 4),
        "shares": round(fill_shares, 6),
        "filled_amount": round(fill_amount, 4),
        "filled_shares": round(fill_shares, 6),
        "unfilled_amount": round(unfilled_amount, 4),
        "average_fill_price": round(market_ask, 6),
        "mark_price": round(market_bid, 6),
        "unrealized_pnl": round(unrealized, 6),
        "realized_pnl": 0.0,
        "risk_reasons": [],
        "fill": {
            "price": round(market_ask, 6),
            "shares": round(fill_shares, 6),
            "amount": round(fill_amount, 4),
            "mark_price": round(market_bid, 6),
            "unrealized_pnl": round(unrealized, 6),
        },
    }


def _requested_amount(amount: float | None, max_per_trade_usd: float) -> tuple[float, list[str]]:
    if amount is None:
        return round(max(0.0, float(max_per_trade_usd)), 2), []
    try:
        clean = max(0.0, float(amount))
        cap = max(0.0, float(max_per_trade_usd))
        return round(min(clean, cap), 2), (["explicit_max_per_trade_usd"] if clean > cap else [])
    except Exception:
        return round(max(0.0, float(max_per_trade_usd)), 2), ["invalid_requested_amount"]


def _idempotency_key(
    decision: dict[str, Any],
    amount: float,
    limit_price: float,
    *,
    cohort_run_id: str = "",
) -> str:
    raw = "|".join([
        PAPER_EXECUTION_VERSION,
        str(decision.get("decision_id") or ""),
        str(decision.get("yes_token_id") or decision.get("token_id") or ""),
        f"{amount:.2f}",
        f"{limit_price:.6f}",
        str(cohort_run_id or "manual"),
        str(decision.get("strategy_revision_id") or "legacy_unversioned"),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _ladder_allocations(rows: list[dict[str, Any]], amount: float | None) -> dict[str, float | None]:
    if amount is None:
        return {
            str(row.get("decision_id") or ""): _num(row.get("position_size_usd"))
            for row in rows
        }
    total = sum(max(0.0, _num(row.get("position_size_usd"), 0.0) or 0.0) for row in rows)
    if total <= 0:
        each = max(0.0, float(amount)) / max(1, len(rows))
        return {str(row.get("decision_id") or ""): each for row in rows}
    return {
        str(row.get("decision_id") or ""): max(0.0, float(amount)) * max(0.0, _num(row.get("position_size_usd"), 0.0) or 0.0) / total
        for row in rows
    }


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result
