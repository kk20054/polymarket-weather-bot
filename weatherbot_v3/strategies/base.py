from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

from ..config import load_config
from ..sizing import size_position


Decision = dict[str, Any]


class StrategyBase:
    strategy_name = "base"
    min_edge = 0.0

    def evaluate(
        self,
        bucket: dict[str, Any],
        probability: dict[str, Any],
        prediction: dict[str, Any],
        context: dict[str, Any],
    ) -> Decision | None:
        raise NotImplementedError

    def evaluate_many(
        self,
        buckets: list[dict[str, Any]],
        probabilities: dict[str, dict[str, Any]],
        prediction: dict[str, Any],
        context: dict[str, Any],
    ) -> list[Decision]:
        decisions: list[Decision] = []
        for bucket in buckets:
            decision = self.evaluate(bucket, probabilities.get(str(bucket.get("bucket_key") or ""), {}), prediction, context)
            if decision is not None:
                decisions.append(decision)
        return decisions

    def build_decision(
        self,
        bucket: dict[str, Any],
        probability_item: dict[str, Any],
        prediction: dict[str, Any],
        context: dict[str, Any],
        *,
        min_edge: float,
        allow_low_price_tail: bool = False,
        extra_gate_reasons: list[str] | None = None,
        extra_hard_blocks: list[str] | None = None,
        force_skip_reasons: list[str] | None = None,
        ladder_group_id: str = "",
        position_size_override: float | None = None,
        kelly_fraction_override: float | None = None,
        position_size_multiplier: float = 1.0,
    ) -> Decision:
        cfg = load_config()
        model_probability = optional_float(probability_item.get("probability"))
        market_bid = optional_float(bucket.get("best_bid"))
        market_ask = optional_float(bucket.get("best_ask"))
        price = optional_float(bucket.get("price"))
        market_mid = (market_bid + market_ask) / 2.0 if market_bid is not None and market_ask is not None else None
        market_probability = market_ask if market_ask is not None else (price if price is not None else market_mid)
        edge = None if model_probability is None or market_probability is None else model_probability - market_probability
        edge_percent = None
        if edge is not None and market_probability and market_probability > 0:
            edge_percent = edge / market_probability
        spread = optional_float(bucket.get("spread"))
        spread_bps = spread_bps_value(spread, market_ask, market_bid)
        book_age_seconds = book_age_seconds_value(
            bucket.get("quote_timestamp"),
            as_of=context.get("decision_time"),
        )

        gate_reasons: list[str] = []
        cautions: list[str] = []
        hard_blocks: list[str] = []
        skip_reasons: list[str] = []
        if bucket.get("strict_match_status") != "matched":
            hard_blocks.append("bucket_not_strict_match")
            gate_reasons.extend(as_list(bucket.get("strict_match_reasons")) or ["bucket_not_strict_match"])
        if not bucket.get("yes_token_id"):
            hard_blocks.append("yes_token_missing")
        if optional_float(bucket.get("tick_size")) is None:
            hard_blocks.append("tick_size_missing")
        if optional_float(bucket.get("order_min_size")) is None:
            hard_blocks.append("order_min_size_missing")
        if not bool(bucket.get("enable_order_book")):
            hard_blocks.append("orderbook_disabled")
        if market_ask is None or not (0 < market_ask < 1):
            hard_blocks.append("invalid_best_ask")
        if market_bid is None or not (0 < market_bid < 1):
            hard_blocks.append("invalid_best_bid")
        if market_bid is not None and market_ask is not None and market_bid > market_ask:
            hard_blocks.append("crossed_orderbook")
        if market_probability is None:
            hard_blocks.append("market_probability_missing")
        if model_probability is None:
            hard_blocks.append("model_probability_missing")
        low_price_tail_ask = context_number(context, "low_price_tail_ask", 0.05)
        global_min_trade_edge = context_number(context, "min_trade_edge", 0.08)
        required_min_edge = max(float(min_edge), global_min_trade_edge)
        max_spread_bps = context_number(context, "max_spread_bps", 500.0)
        stale_book_seconds = context_number(context, "stale_book_seconds", 300.0)
        min_bias_sample_days = int(context_number(context, "min_bias_sample_days", 7.0))
        kelly_multiplier = context_number(context, "kelly_multiplier", 0.15)
        bankroll_fraction_cap = context_number(context, "bankroll_fraction_cap", 0.05)
        if not allow_low_price_tail and is_low_price_tail(bucket, market_ask, threshold=low_price_tail_ask):
            hard_blocks.append("low_price_tail_bucket")
        if spread_bps is not None and spread_bps > max_spread_bps + 1e-9:
            hard_blocks.append("spread_too_wide")
        if book_age_seconds is None:
            cautions.append("book_timestamp_missing")
        elif book_age_seconds > stale_book_seconds:
            cautions.append("stale_book")
        forecast_algo = str(prediction.get("forecast_algo") or prediction.get("method") or prediction.get("deb_version") or "")
        if forecast_algo not in {"weatherbot-deb-v2", "ensemble_v1", "polywx_aligned_deb_v1"}:
            hard_blocks.append("forecast_algo_not_supported")
        if int(prediction.get("bias_sample_count") or 0) < min_bias_sample_days:
            gate_reasons.append("insufficient_bias_samples")
        if edge is None or edge < required_min_edge:
            skip_reasons.append("edge_below_min")
        skip_reasons.extend(force_skip_reasons or [])
        hard_blocks.extend(extra_hard_blocks or [])
        gate_reasons.extend(extra_gate_reasons or [])

        sizing = size_position(
            model_probability,
            market_ask,
            bankroll=float(context.get("bankroll") or 0.0),
            max_per_trade_usd=float(context.get("max_per_trade_usd") or 0.0),
            kelly_multiplier=kelly_multiplier,
            bankroll_fraction_cap=bankroll_fraction_cap,
        )
        clean_position_size_multiplier = min(1.0, max(0.0, float(position_size_multiplier)))
        kelly_fraction = sizing.kelly_fraction if kelly_fraction_override is None else round(max(0.0, float(kelly_fraction_override)), 8)
        position_size = (
            round(sizing.capped_position_size_usd * clean_position_size_multiplier, 4)
            if position_size_override is None
            else round(max(0.0, float(position_size_override)), 4)
        )
        sizing_snapshot = sizing.snapshot()
        sizing_snapshot.update({
            "unscaled_final_position_size_usd": sizing.capped_position_size_usd,
            "position_size_multiplier": clean_position_size_multiplier,
            "final_position_size_usd": position_size,
        })
        order_min_size = optional_float(bucket.get("order_min_size"))
        if (
            market_ask is not None
            and market_ask > 0
            and order_min_size is not None
            and order_min_size > 0
            and position_size / market_ask + 1e-9 < order_min_size
        ):
            skip_reasons.append("below_order_min_size")
        if position_size <= 0:
            skip_reasons.append("non_positive_kelly_size")

        gate_reasons.extend(hard_blocks)
        gate_reasons.extend(skip_reasons)
        gate_reasons = unique(gate_reasons)
        hard_blocks = unique(hard_blocks)
        skip_reasons = unique(skip_reasons)
        paper_allowed = not hard_blocks and not skip_reasons
        paper_decision = "buy" if paper_allowed else ("blocked" if hard_blocks else "skip")
        live_allowed = False
        live_decision = "blocked"
        live_reasons = []
        if int(prediction.get("bias_sample_count") or 0) < min_bias_sample_days:
            live_reasons.append("insufficient_bias_samples")
        live_reasons.extend(context.get("station_live_reasons") or [])
        if not paper_allowed:
            live_reasons.append("paper_gate_not_passed")
        if not getattr(cfg, "live_trading", False):
            live_reasons.append("live_trading_disabled")
        gate_reasons = unique(gate_reasons + live_reasons)
        gate_status = "paper_allowed" if paper_allowed else ("paper_blocked" if hard_blocks else "skip")
        primary_reason = (hard_blocks or skip_reasons or live_reasons or gate_reasons or [""])[0]
        distribution = context.get("distribution") or {}
        evidence = context.get("evidence") or {}
        decision = {
            "decision_id": decision_id(bucket, prediction, self.strategy_name, context),
            "bucket_id": bucket.get("id"),
            "bucket_key": bucket.get("bucket_key") or "",
            "city_key": bucket.get("city") or prediction.get("city_key") or "",
            "target_date": bucket.get("target_date") or prediction.get("target_date") or "",
            "issued_at": prediction.get("issued_at") or "",
            "market_id": bucket.get("market_id") or "",
            "token_id": bucket.get("yes_token_id") or bucket.get("token_id") or "",
            "yes_token_id": bucket.get("yes_token_id") or "",
            "bucket_direction": bucket.get("bucket_direction") or "",
            "bucket_lower": bucket.get("bucket_low"),
            "bucket_upper": bucket.get("bucket_high"),
            "mu": prediction.get("mu"),
            "sigma": prediction.get("sigma"),
            "deb_version": prediction.get("deb_version") or prediction.get("method") or "",
            "forecast_algo": forecast_algo,
            "model_probability": model_probability,
            "market_ask": market_ask,
            "market_bid": market_bid,
            "market_mid": market_mid,
            "market_implied_probability": market_probability,
            "edge": edge,
            "edge_percent": edge_percent,
            "strategy_name": self.strategy_name,
            "kelly_fraction": kelly_fraction,
            "position_size_usd": position_size,
            "position_size_multiplier": clean_position_size_multiplier,
            "ladder_group_id": ladder_group_id,
            "strategy_revision_id": str(context.get("strategy_revision_id") or ""),
            "strategy_params_hash": str(context.get("strategy_params_hash") or ""),
            "strategy_params_snapshot": context.get("strategy_params_snapshot") or {},
            "sizing_bankroll_usd": float(context.get("bankroll") or 0.0),
            "sizing_max_per_trade_usd": float(context.get("max_per_trade_usd") or 0.0),
            "kelly_multiplier": kelly_multiplier,
            "bankroll_fraction_cap": bankroll_fraction_cap,
            "sizing_snapshot": sizing_snapshot,
            "orderbook_snapshot": {
                "best_bid": market_bid,
                "best_ask": market_ask,
                "spread": spread,
                "bid_depth": bucket.get("bid_depth"),
                "ask_depth": bucket.get("ask_depth"),
                "best_bid_size": bucket.get("best_bid_size"),
                "best_ask_size": bucket.get("best_ask_size"),
                "bids": bucket.get("bids") or [],
                "asks": bucket.get("asks") or [],
                "depth_basis": bucket.get("depth_basis") or "legacy_total_depth",
                "quote_timestamp": bucket.get("quote_timestamp"),
                "source": bucket.get("orderbook_source"),
                "snapshot_key": bucket.get("orderbook_snapshot_key"),
            },
            "tick_size": bucket.get("tick_size"),
            "order_min_size": bucket.get("order_min_size"),
            "neg_risk": bool(bucket.get("neg_risk")),
            "book_age_seconds": book_age_seconds,
            "spread_bps": spread_bps,
            "gate_status": gate_status,
            "gate_reasons": gate_reasons,
            "paper_allowed": paper_allowed,
            "live_allowed": live_allowed,
            "paper_decision": paper_decision,
            "live_decision": live_decision,
            "blocked_reason_primary": primary_reason,
            "reasons": gate_reasons,
            "cautions": unique(cautions),
            "action": "buy_yes" if paper_allowed else "observe",
            "decision_version": context.get("decision_version") or "signal-decision-v2",
            "model_distribution": distribution_summary(distribution),
            "model_bucket_probs": probability_item,
            "market_bucket_probs": [{
                "bucket_key": bucket.get("bucket_key"),
                "market_probability": market_probability,
                "best_bid": market_bid,
                "best_ask": market_ask,
                "price": price,
            }],
            "edge_by_bucket": {
                str(bucket.get("bucket_key") or bucket.get("market_id") or ""): {
                    "model_probability": model_probability,
                    "market_probability": market_probability,
                    "edge": edge,
                    "edge_percent": edge_percent,
                }
            },
            "evidence_links": {
                **evidence,
                "event_url": bucket.get("event_url") or evidence.get("event_url") or "",
                "market_bucket_id": bucket.get("id"),
                "daily_max_prediction_id": prediction.get("id"),
            },
        }
        return decision


def decision_id(bucket: dict[str, Any], prediction: dict[str, Any], strategy_name: str, context: dict[str, Any]) -> str:
    id_version = context.get("single_bucket_id_version") if strategy_name == "single_bucket_ev" else context.get("decision_version")
    parts = [
        str(bucket.get("city") or prediction.get("city_key") or ""),
        str(bucket.get("target_date") or prediction.get("target_date") or ""),
        str(bucket.get("yes_token_id") or bucket.get("token_id") or ""),
        hour_key(prediction.get("issued_at")),
        str(id_version or "signal-decision-v1"),
        strategy_name,
        str(context.get("strategy_revision_id") or "legacy_unversioned"),
    ]
    key = "|".join(parts)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def distribution_summary(distribution: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": distribution.get("method"),
        "mu": distribution.get("mu"),
        "sigma": distribution.get("sigma"),
        "unit": distribution.get("unit"),
        "sum_probability": distribution.get("sum_probability"),
        "normalized": distribution.get("normalized"),
        "item_count": len(distribution.get("items") or []),
        "probability_mu_basis": distribution.get("probability_mu_basis"),
        "model_mu": distribution.get("model_mu"),
        "effective_mu": distribution.get("effective_mu"),
        "observed_floor": distribution.get("observed_floor"),
    }


def bucket_sort_key(bucket: dict[str, Any]) -> tuple[float, float, str]:
    low = optional_float(bucket.get("bucket_low"))
    high = optional_float(bucket.get("bucket_high"))
    if low is None:
        low = -math.inf
    if high is None:
        high = math.inf
    return (low, high, str(bucket.get("bucket_key") or ""))


def bucket_center(bucket: dict[str, Any]) -> float | None:
    low = optional_float(bucket.get("bucket_low"))
    high = optional_float(bucket.get("bucket_high"))
    if low is not None and high is not None:
        return (low + high) / 2.0
    if low is not None:
        return low
    if high is not None:
        return high
    return None


def optional_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def spread_bps_value(spread: float | None, ask: float | None, bid: float | None) -> float | None:
    if spread is None and ask is not None and bid is not None:
        spread = ask - bid
    if spread is None or ask is None or ask <= 0:
        return None
    return max(0.0, float(spread) / float(ask) * 10_000.0)


def book_age_seconds_value(value: Any, *, as_of: Any = None) -> float | None:
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    reference = parse_datetime(as_of) or datetime.now(timezone.utc)
    age_seconds = (reference - parsed).total_seconds()
    if age_seconds < -5.0:
        return None
    return max(0.0, age_seconds)


def parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        numeric = float(text)
        if math.isfinite(numeric) and numeric > 0:
            if numeric >= 1_000_000_000_000:
                numeric /= 1000.0
            if numeric >= 1_000_000_000:
                return datetime.fromtimestamp(numeric, tz=timezone.utc)
    except Exception:
        pass
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def hour_key(value: Any) -> str:
    parsed = parse_datetime(value)
    if parsed is None:
        return str(value or "")
    return parsed.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()


def context_number(context: dict[str, Any], key: str, default: float) -> float:
    value = optional_float(context.get(key))
    return default if value is None else value


def is_low_price_tail(bucket: dict[str, Any], ask: float | None, *, threshold: float = 0.05) -> bool:
    direction = str(bucket.get("bucket_direction") or "").lower()
    if direction not in {"or_above", "or_below", "above", "below", "under", "over", "at_or_above", "at_or_below"}:
        return False
    return ask is not None and ask < max(0.0, float(threshold))


def unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result
