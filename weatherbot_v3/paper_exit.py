from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import (
    apply_paper_exit_record,
    connect,
    latest_paper_exit_evaluation,
    list_paper_orders,
    record_paper_exit_evaluation,
)
from .deb import bucket_excluded_by_observed_floor
from .strategy_profiles import DEFAULT_PARAMETERS


PAPER_EXIT_VERSION = "paper-exit-v1"


def evaluate_open_paper_exits(
    *,
    apply: bool = True,
    limit: int = 1000,
    path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate model-guarded paper exits without touching live orders."""
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    orders = [
        order
        for order in list_paper_orders(limit=limit, path=path)
        if str(order.get("lifecycle_status") or "") == "open"
    ]
    results: list[dict[str, Any]] = []
    for order in orders:
        policy = _exit_policy(order)
        if policy["mode"] != "model_guarded":
            continue
        results.append(
            _evaluate_order(
                order,
                policy=policy,
                apply=apply,
                path=path,
                checked_at=checked_at,
            )
        )
    return {
        "ok": all(result.get("ok", True) for result in results),
        "version": PAPER_EXIT_VERSION,
        "apply": bool(apply),
        "open_orders": len(orders),
        "guarded_orders": len(results),
        "exited_now": sum(1 for result in results if result.get("action") == "exited"),
        "held_now": sum(1 for result in results if result.get("action") == "hold"),
        "results": results,
    }


def _evaluate_order(
    order: dict[str, Any],
    *,
    policy: dict[str, Any],
    apply: bool,
    path: Path | None,
    checked_at: datetime,
) -> dict[str, Any]:
    order_id = int(order.get("id") or 0)
    context = _load_exit_context(order, path=path)
    prediction = context.get("prediction") or {}
    decision = context.get("decision") or {}
    quote = context.get("quote") or {}
    bucket = context.get("bucket") or {}
    previous = latest_paper_exit_evaluation(order_id, path=path)

    observed_high = _number(prediction.get("observed_floor"))
    prediction_unit = str(prediction.get("unit") or bucket.get("unit") or "C")
    hard_breach = bucket_excluded_by_observed_floor(
        bucket,
        prediction_unit=prediction_unit,
        observed_floor=observed_high,
    )
    model_probability = _number(decision.get("model_probability"))
    model_invalid = (
        model_probability is not None
        and model_probability <= float(policy["model_probability_threshold"]) + 1e-12
    )
    source_prediction_id = int(prediction.get("id") or 0) or None
    confirmation_count = _confirmation_count(
        previous,
        model_invalid=model_invalid,
        source_prediction_id=source_prediction_id,
    )

    best_bid = _number(quote.get("best_bid"))
    best_bid_size = _best_bid_size(quote)
    quote_time = _parse_timestamp(quote.get("quote_timestamp") or quote.get("created_at"))
    quote_age = (checked_at - quote_time).total_seconds() if quote_time else None
    opened_at = _parse_timestamp(order.get("opened_at") or order.get("created_at"))
    held_minutes = (checked_at - opened_at).total_seconds() / 60.0 if opened_at else None
    shares = _number(order.get("filled_shares") or order.get("shares")) or 0.0
    trigger = "observed_bucket_breach" if hard_breach else (
        "model_probability_invalidated" if model_invalid else "none"
    )
    reasons: list[str] = []

    if not hard_breach and not model_invalid:
        reasons.append("exit_condition_not_met")
    if best_bid is None or best_bid <= 0:
        reasons.append("sell_bid_missing")
    if quote_age is None:
        reasons.append("sell_quote_timestamp_missing")
    elif quote_age > float(policy["max_quote_age_seconds"]):
        reasons.append("sell_quote_stale")
    if shares <= 0:
        reasons.append("position_shares_missing")
    elif best_bid_size is None:
        reasons.append("best_bid_depth_missing")
    elif best_bid_size + 1e-9 < shares:
        reasons.append("insufficient_best_bid_depth")

    if model_invalid and not hard_breach:
        if confirmation_count < int(policy["confirmations_required"]):
            reasons.append("model_exit_waiting_for_confirmation")
        if held_minutes is None or held_minutes < float(policy["min_hold_minutes"]):
            reasons.append("model_exit_minimum_hold_not_met")
        if best_bid is not None and model_probability is not None:
            required_bid = model_probability + float(policy["min_bid_over_model_edge"])
            if best_bid + 1e-12 < required_bid:
                reasons.append("sell_bid_below_model_fair_value")

    actionable = hard_breach or model_invalid
    action = "exit" if actionable and not reasons else "hold"
    evaluation = {
        "paper_order_id": order_id,
        "checked_at": checked_at.isoformat(),
        "policy_mode": "model_guarded",
        "source_decision_id": str(decision.get("decision_id") or ""),
        "source_prediction_id": source_prediction_id,
        "trigger": trigger,
        "action": action,
        "confirmation_count": confirmation_count,
        "model_probability": model_probability,
        "best_bid": best_bid,
        "best_bid_size": best_bid_size,
        "observed_high": observed_high,
        "quote_timestamp": str(quote.get("quote_timestamp") or quote.get("created_at") or ""),
        "quote_age_seconds": quote_age,
        "held_minutes": held_minutes,
        "reasons": reasons,
        "policy": policy,
        "bucket": bucket,
        "version": PAPER_EXIT_VERSION,
    }
    evaluation["evaluation_key"] = _evaluation_key(evaluation)

    if not apply:
        return {"ok": True, "action": action, "evaluation": evaluation}
    stored = record_paper_exit_evaluation(evaluation, path=path)
    if action != "exit":
        return {"ok": True, "action": "hold", "evaluation": stored}
    exited = apply_paper_exit_record(
        order_id,
        {
            **evaluation,
            "exit_price": best_bid,
            "closed_at": checked_at.isoformat(),
        },
        path=path,
    )
    final_action = "exited" if exited.get("ok") and exited.get("status") == "exited" else "hold"
    record_paper_exit_evaluation(
        {**evaluation, "action": final_action, "exit_result": exited},
        path=path,
    )
    return {
        "ok": bool(exited.get("ok")),
        "action": final_action,
        "evaluation": evaluation,
        "exit": exited,
    }


def _load_exit_context(order: dict[str, Any], *, path: Path | None) -> dict[str, Any]:
    revision_id = str(order.get("strategy_revision_id") or "")
    with connect(path) as conn:
        bucket_row = conn.execute(
            "SELECT * FROM market_buckets WHERE bucket_key = ? ORDER BY id DESC LIMIT 1",
            (str(order.get("bucket_key") or ""),),
        ).fetchone()
        prediction_row = conn.execute(
            """
            SELECT * FROM daily_max_predictions
            WHERE city_key = ? AND target_date = ?
              AND COALESCE(validity_status, 'valid') = 'valid'
            ORDER BY issued_at DESC, id DESC LIMIT 1
            """,
            (str(order.get("city_key") or ""), str(order.get("target_date") or "")),
        ).fetchone()
        if revision_id:
            decision_row = conn.execute(
                """
                SELECT * FROM signal_decisions
                WHERE bucket_key = ? AND strategy_name = ? AND strategy_revision_id = ?
                ORDER BY issued_at DESC, id DESC LIMIT 1
                """,
                (
                    str(order.get("bucket_key") or ""),
                    str(order.get("strategy_name") or "single_bucket_ev"),
                    revision_id,
                ),
            ).fetchone()
        else:
            decision_row = conn.execute(
                """
                SELECT * FROM signal_decisions
                WHERE bucket_key = ? AND strategy_name = ?
                ORDER BY issued_at DESC, id DESC LIMIT 1
                """,
                (str(order.get("bucket_key") or ""), str(order.get("strategy_name") or "single_bucket_ev")),
            ).fetchone()
        quote_row = conn.execute(
            """
            SELECT * FROM orderbooks
            WHERE yes_token_id = ? AND best_bid IS NOT NULL
            ORDER BY id DESC LIMIT 1
            """,
            (str(order.get("yes_token_id") or ""),),
        ).fetchone()
    bucket = dict(bucket_row) if bucket_row else {
        "bucket_key": order.get("bucket_key"),
        "unit": "C",
    }
    if decision_row:
        decision = dict(decision_row)
        bucket.setdefault("bucket_direction", decision.get("bucket_direction"))
        bucket.setdefault("bucket_low", decision.get("bucket_lower"))
        bucket.setdefault("bucket_high", decision.get("bucket_upper"))
    else:
        decision = {}
    return {
        "bucket": bucket,
        "prediction": dict(prediction_row) if prediction_row else {},
        "decision": decision,
        "quote": dict(quote_row) if quote_row else {},
    }


def _exit_policy(order: dict[str, Any]) -> dict[str, Any]:
    configured = (
        (((order.get("strategy_params_snapshot") or {}).get("parameters") or {}).get("exit_policy"))
        or {}
    )
    return {**DEFAULT_PARAMETERS["exit_policy"], **configured}


def _confirmation_count(
    previous: dict[str, Any] | None,
    *,
    model_invalid: bool,
    source_prediction_id: int | None,
) -> int:
    if not model_invalid:
        return 0
    if not previous:
        return 1
    previous_source = int(previous.get("source_prediction_id") or 0) or None
    previous_count = int(previous.get("confirmation_count") or 0)
    if previous_source == source_prediction_id:
        return max(1, previous_count)
    if str(previous.get("trigger") or "") == "model_probability_invalidated":
        return max(1, previous_count) + 1
    return 1


def _best_bid_size(quote: dict[str, Any]) -> float | None:
    best_bid = _number(quote.get("best_bid"))
    if best_bid is None:
        return None
    try:
        levels = json.loads(str(quote.get("bids_json") or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    sizes = [
        _number(level.get("size"))
        for level in levels
        if isinstance(level, dict) and _number(level.get("price")) is not None
        and abs(float(_number(level.get("price"))) - best_bid) <= 1e-9
    ]
    valid = [size for size in sizes if size is not None and size >= 0]
    return sum(valid) if valid else None


def _evaluation_key(evaluation: dict[str, Any]) -> str:
    parts = [
        str(evaluation.get("paper_order_id") or ""),
        str(evaluation.get("source_prediction_id") or ""),
        str(evaluation.get("source_decision_id") or ""),
        str(evaluation.get("quote_timestamp") or ""),
        str(evaluation.get("trigger") or ""),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
