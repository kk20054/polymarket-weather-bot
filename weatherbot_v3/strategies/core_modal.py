from __future__ import annotations

from statistics import fmean
from typing import Any

from .base import (
    Decision,
    StrategyBase,
    book_age_seconds_value,
    bucket_sort_key,
    optional_float,
    spread_bps_value,
    unique,
)


class CoreModalStrategy(StrategyBase):
    """Trade at most one liquid bucket from the event's two model modes."""

    strategy_name = "core_modal_v1"
    min_effective_edge = 0.08
    min_model_probability = 0.25
    max_model_rank = 2
    min_market_ask = 0.10
    min_independent_settlement_days = 20
    require_authoritative_truth = True
    min_component_calibration_days = 20
    min_calibration_coverage = 0.80
    min_model_families = 4
    max_model_spread_c = 1.50

    def __init__(self, parameters: dict[str, Any] | None = None):
        self.parameters = dict(parameters or {})
        self.min_effective_edge = float(self.parameters.get("min_effective_edge", self.min_effective_edge))
        self.min_model_probability = float(self.parameters.get("min_model_probability", self.min_model_probability))
        self.max_model_rank = int(self.parameters.get("max_model_rank", self.max_model_rank))
        self.min_market_ask = float(self.parameters.get("min_market_ask", self.min_market_ask))
        self.min_independent_settlement_days = int(
            self.parameters.get("min_settlement_days", self.min_independent_settlement_days)
        )
        self.require_authoritative_truth = bool(
            self.parameters.get("require_authoritative_truth", self.require_authoritative_truth)
        )
        self.min_component_calibration_days = int(
            self.parameters.get("min_component_calibration_days", self.min_component_calibration_days)
        )
        self.min_calibration_coverage = float(
            self.parameters.get("min_calibration_coverage", self.min_calibration_coverage)
        )
        self.min_model_families = int(self.parameters.get("min_model_families", self.min_model_families))
        self.max_model_spread_c = float(self.parameters.get("max_model_spread_c", self.max_model_spread_c))

    def evaluate(
        self,
        bucket: dict[str, Any],
        probability: dict[str, Any],
        prediction: dict[str, Any],
        context: dict[str, Any],
    ) -> Decision | None:
        # Ranking and the no-fallback contract require the complete event.
        return None

    def evaluate_many(
        self,
        buckets: list[dict[str, Any]],
        probabilities: dict[str, dict[str, Any]],
        prediction: dict[str, Any],
        context: dict[str, Any],
    ) -> list[Decision]:
        ranked = self._ranked_buckets(buckets, probabilities)
        if not ranked:
            return []

        quality = self._prediction_quality(prediction, context)
        modal = ranked[0]
        modal_execution_reasons = self._execution_reasons(modal["bucket"], context)
        event_reasons = self._quality_reasons(quality)
        if event_reasons or modal_execution_reasons:
            decision = self._build_audit_decision(
                modal,
                prediction,
                context,
                force_skip_reasons=event_reasons,
                extra_hard_blocks=(
                    unique([*modal_execution_reasons, "core_modal_not_executable"])
                    if modal_execution_reasons
                    else []
                ),
            )
            self._attach_metadata(decision, modal, modal, ranked, quality)
            return [decision]

        candidates: list[dict[str, Any]] = []
        rejection_reasons: list[str] = []
        for ranked_item in ranked[: self.max_model_rank]:
            candidate_reasons = self._candidate_reasons(ranked_item, context)
            if candidate_reasons:
                rejection_reasons.extend(candidate_reasons)
                continue
            candidates.append(ranked_item)

        if not candidates:
            decision = self._build_audit_decision(
                modal,
                prediction,
                context,
                force_skip_reasons=unique(["core_no_qualified_top_bucket", *rejection_reasons]),
            )
            self._attach_metadata(decision, modal, modal, ranked, quality)
            return [decision]

        chosen = max(
            candidates,
            key=lambda item: (float(item["effective_edge"]), float(item["probability"]), -int(item["rank"])),
        )
        decision = self.build_decision(
            chosen["bucket"],
            chosen["probability_item"],
            prediction,
            context,
            min_edge=0.0,
        )
        self._attach_metadata(decision, chosen, modal, ranked, quality)
        return [decision]

    def _ranked_buckets(
        self,
        buckets: list[dict[str, Any]],
        probabilities: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for bucket in buckets:
            probability_item = probabilities.get(str(bucket.get("bucket_key") or ""), {})
            probability = optional_float(probability_item.get("probability"))
            if probability is None:
                continue
            rows.append({
                "bucket": bucket,
                "probability_item": probability_item,
                "probability": probability,
            })
        rows.sort(key=lambda item: (-float(item["probability"]), bucket_sort_key(item["bucket"])))
        for rank, item in enumerate(rows, start=1):
            item["rank"] = rank
            item.update(self._edge_metrics(item["bucket"], float(item["probability"])))
        return rows

    def _edge_metrics(self, bucket: dict[str, Any], probability: float) -> dict[str, float | None]:
        ask = optional_float(bucket.get("best_ask"))
        bid = optional_float(bucket.get("best_bid"))
        tick = optional_float(bucket.get("tick_size"))
        spread = optional_float(bucket.get("spread"))
        if spread is None and ask is not None and bid is not None:
            spread = max(0.0, ask - bid)
        execution_buffer = None
        raw_edge = None
        effective_edge = None
        if ask is not None:
            raw_edge = probability - ask
            execution_buffer = max(tick or 0.0, (spread or 0.0) * 0.5)
            effective_edge = raw_edge - execution_buffer
        return {
            "market_ask": ask,
            "market_bid": bid,
            "raw_edge": raw_edge,
            "execution_buffer": execution_buffer,
            "effective_edge": effective_edge,
        }

    def _candidate_reasons(self, item: dict[str, Any], context: dict[str, Any]) -> list[str]:
        reasons = self._execution_reasons(item["bucket"], context)
        if float(item["probability"]) + 1e-12 < self.min_model_probability:
            reasons.append("core_probability_below_min")
        ask = optional_float(item.get("market_ask"))
        if ask is None or ask + 1e-12 < self.min_market_ask:
            reasons.append("core_price_below_min")
        effective_edge = optional_float(item.get("effective_edge"))
        if effective_edge is None or effective_edge + 1e-12 < self.min_effective_edge:
            reasons.append("core_effective_edge_below_min")
        return unique(reasons)

    def _execution_reasons(self, bucket: dict[str, Any], context: dict[str, Any]) -> list[str]:
        reasons: list[str] = []
        ask = optional_float(bucket.get("best_ask"))
        bid = optional_float(bucket.get("best_bid"))
        tick = optional_float(bucket.get("tick_size"))
        spread = optional_float(bucket.get("spread"))
        if bucket.get("strict_match_status") != "matched":
            reasons.append("bucket_not_strict_match")
        if not bucket.get("yes_token_id"):
            reasons.append("yes_token_missing")
        if tick is None:
            reasons.append("tick_size_missing")
        if optional_float(bucket.get("order_min_size")) is None:
            reasons.append("order_min_size_missing")
        if not bool(bucket.get("enable_order_book")):
            reasons.append("orderbook_disabled")
        if ask is None or not 0 < ask < 1:
            reasons.append("invalid_best_ask")
        if bid is None or not 0 < bid < 1:
            reasons.append("invalid_best_bid")
        if ask is not None and bid is not None and bid > ask:
            reasons.append("crossed_orderbook")
        max_spread_bps = float(context.get("max_spread_bps") or 500.0)
        spread_bps = spread_bps_value(spread, ask, bid)
        if spread_bps is None or spread_bps > max_spread_bps + 1e-9:
            reasons.append("spread_too_wide")
        age = book_age_seconds_value(
            bucket.get("quote_timestamp"),
            as_of=context.get("decision_time"),
        )
        stale_seconds = float(context.get("stale_book_seconds") or 300.0)
        if age is None:
            reasons.append("book_timestamp_missing")
        elif age > stale_seconds + 1e-9:
            reasons.append("stale_book")
        if ask is not None and ask + 1e-12 < self.min_market_ask:
            reasons.append("core_price_below_min")
        return unique(reasons)

    def _prediction_quality(self, prediction: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        components = [
            component for component in (prediction.get("components") or [])
            if (
                isinstance(component, dict)
                and int(component.get("member_count") or 0) > 0
                and float(component.get("weight") or 0.0) > 0.0
            )
        ]
        families = sorted({str(component.get("family") or component.get("source") or "") for component in components if component.get("family") or component.get("source")})
        weight_total = sum(max(0.0, float(component.get("weight") or 0.0)) for component in components)
        calibrated_weight = sum(
            max(0.0, float(component.get("weight") or 0.0))
            for component in components
            if not bool(component.get("mae_imputed"))
            and int(component.get("bias_sample_count") or 0) >= self.min_component_calibration_days
        )
        calibration_coverage = calibrated_weight / weight_total if weight_total > 0 else 0.0
        family_highs_c = [
            fmean(float(value) for value in component.get("adjusted_daily_highs_c") or [])
            for component in components
            if component.get("adjusted_daily_highs_c")
        ]
        model_spread_c = max(family_highs_c) - min(family_highs_c) if len(family_highs_c) >= 2 else None
        return {
            "family_count": len(families),
            "families": families,
            "calibration_coverage": round(calibration_coverage, 8),
            "calibrated_weight": round(calibrated_weight, 8),
            "weight_total": round(weight_total, 8),
            "model_spread_c": None if model_spread_c is None else round(model_spread_c, 4),
            "independent_settlement_days": int(context.get("independent_settlement_days") or 0),
            "independent_settlement_basis": str(context.get("independent_settlement_basis") or "missing"),
            "independent_settlement_authoritative": bool(context.get("independent_settlement_authoritative")),
        }

    def _quality_reasons(self, quality: dict[str, Any]) -> list[str]:
        reasons: list[str] = []
        if int(quality["family_count"]) < self.min_model_families:
            reasons.append("core_model_family_count_below_min")
        if float(quality["calibration_coverage"]) + 1e-12 < self.min_calibration_coverage:
            reasons.append("core_calibration_coverage_below_min")
        spread = optional_float(quality.get("model_spread_c"))
        if spread is None:
            reasons.append("core_model_spread_unavailable")
        elif spread > self.max_model_spread_c + 1e-12:
            reasons.append("core_model_spread_too_wide")
        if int(quality["independent_settlement_days"]) < self.min_independent_settlement_days:
            reasons.append("core_independent_settlement_days_below_min")
        if self.require_authoritative_truth and not bool(quality["independent_settlement_authoritative"]):
            reasons.append("core_settlement_truth_not_authoritative")
        return reasons

    def _build_audit_decision(
        self,
        item: dict[str, Any],
        prediction: dict[str, Any],
        context: dict[str, Any],
        *,
        force_skip_reasons: list[str] | None = None,
        extra_hard_blocks: list[str] | None = None,
    ) -> Decision:
        return self.build_decision(
            item["bucket"],
            item["probability_item"],
            prediction,
            context,
            min_edge=0.0,
            force_skip_reasons=force_skip_reasons,
            extra_hard_blocks=extra_hard_blocks,
        )

    def _attach_metadata(
        self,
        decision: Decision,
        chosen: dict[str, Any],
        modal: dict[str, Any],
        ranked: list[dict[str, Any]],
        quality: dict[str, Any],
    ) -> None:
        decision["core_modal"] = {
            "model_rank": int(chosen["rank"]),
            "modal_bucket_key": str(modal["bucket"].get("bucket_key") or ""),
            "chosen_bucket_key": str(chosen["bucket"].get("bucket_key") or ""),
            "ranked_bucket_count": len(ranked),
            "raw_edge": chosen.get("raw_edge"),
            "execution_buffer": chosen.get("execution_buffer"),
            "effective_edge": chosen.get("effective_edge"),
            "quality": quality,
            "thresholds": {
                "max_model_rank": self.max_model_rank,
                "min_model_probability": self.min_model_probability,
                "min_effective_edge": self.min_effective_edge,
                "min_market_ask": self.min_market_ask,
                "min_independent_settlement_days": self.min_independent_settlement_days,
                "require_authoritative_truth": self.require_authoritative_truth,
                "min_component_calibration_days": self.min_component_calibration_days,
                "min_calibration_coverage": self.min_calibration_coverage,
                "min_model_families": self.min_model_families,
                "max_model_spread_c": self.max_model_spread_c,
            },
        }
