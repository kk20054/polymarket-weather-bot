from __future__ import annotations

from statistics import fmean
from typing import Any

from .base import (
    Decision,
    StrategyBase,
    book_age_seconds_value,
    bucket_sort_key,
    orderbook_execution_reasons,
    optional_float,
    spread_bps_value,
    unique,
)


CORE_MODAL_PROVISIONAL_CAUTION = "core_independent_samples_provisional"
CORE_MODAL_WIDE_SPREAD_CAUTION = "core_model_spread_high"
CORE_MODAL_LIVE_MATURITY_REASON = "core_live_maturity_below_min"


class CoreModalStrategy(StrategyBase):
    """Trade at most one liquid bucket from the event's two model modes."""

    strategy_name = "core_modal_v1"
    min_effective_edge = 0.08
    min_model_probability = 0.25
    max_model_rank = 2
    min_market_ask = 0.10
    min_paper_independent_settlement_days = 0
    min_live_independent_settlement_days = 20
    require_authoritative_truth = True
    min_paper_component_calibration_days = 0
    min_live_component_calibration_days = 20
    min_calibration_coverage = 0.80
    min_model_families = 4
    max_paper_model_spread_c = 4.50
    max_live_model_spread_c = 1.50
    max_model_spread_c = max_paper_model_spread_c
    provisional_position_multiplier = 1.00

    def __init__(self, parameters: dict[str, Any] | None = None):
        self.parameters = dict(parameters or {})
        self.min_effective_edge = float(self.parameters.get("min_effective_edge", self.min_effective_edge))
        self.min_model_probability = float(self.parameters.get("min_model_probability", self.min_model_probability))
        self.max_model_rank = int(self.parameters.get("max_model_rank", self.max_model_rank))
        self.min_market_ask = float(self.parameters.get("min_market_ask", self.min_market_ask))
        (
            self.min_paper_independent_settlement_days,
            self.min_live_independent_settlement_days,
        ) = _split_thresholds(
            self.parameters,
            paper_key="min_paper_settlement_days",
            live_key="min_live_settlement_days",
            legacy_key="min_settlement_days",
            paper_default=self.min_paper_independent_settlement_days,
            live_default=self.min_live_independent_settlement_days,
        )
        self.min_independent_settlement_days = self.min_paper_independent_settlement_days
        self.require_authoritative_truth = bool(
            self.parameters.get("require_authoritative_truth", self.require_authoritative_truth)
        )
        (
            self.min_paper_component_calibration_days,
            self.min_live_component_calibration_days,
        ) = _split_thresholds(
            self.parameters,
            paper_key="min_paper_component_calibration_days",
            live_key="min_live_component_calibration_days",
            legacy_key="min_component_calibration_days",
            paper_default=self.min_paper_component_calibration_days,
            live_default=self.min_live_component_calibration_days,
        )
        self.min_component_calibration_days = self.min_paper_component_calibration_days
        self.min_calibration_coverage = float(
            self.parameters.get("min_calibration_coverage", self.min_calibration_coverage)
        )
        self.min_model_families = int(self.parameters.get("min_model_families", self.min_model_families))
        legacy_spread = float(self.parameters.get("max_model_spread_c", self.max_live_model_spread_c))
        self.max_paper_model_spread_c = float(
            self.parameters.get("max_paper_model_spread_c", self.max_paper_model_spread_c)
        )
        self.max_live_model_spread_c = float(
            self.parameters.get("max_live_model_spread_c", legacy_spread)
        )
        self.max_model_spread_c = self.max_paper_model_spread_c
        self.provisional_position_multiplier = min(1.0, max(0.0, float(
            self.parameters.get("provisional_position_multiplier", self.provisional_position_multiplier)
        )))

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
        event_reasons = self._quality_reasons(quality)
        if event_reasons:
            decision = self._build_audit_decision(
                modal,
                prediction,
                context,
                force_skip_reasons=event_reasons,
                position_size_multiplier=self._position_multiplier(quality),
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
                position_size_multiplier=self._position_multiplier(quality),
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
            position_size_multiplier=self._position_multiplier(quality),
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
        required_min_edge = max(
            self.min_effective_edge,
            float(context.get("min_trade_edge") or 0.08),
        )
        if effective_edge is None or effective_edge + 1e-12 < required_min_edge:
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
        reasons.extend(orderbook_execution_reasons(bucket, bid, ask))
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
        evidence_components = [
            component for component in (prediction.get("components") or [])
            if (
                isinstance(component, dict)
                and str(component.get("weight_status") or "").strip().lower() != "excluded"
                and self._component_highs_c(component)
            )
        ]
        weighted_components = [
            component for component in evidence_components
            if float(component.get("weight") or 0.0) > 0.0
        ]
        families = sorted({
            str(component.get("family") or component.get("source") or "")
            for component in evidence_components
            if component.get("family") or component.get("source")
        })
        weight_total = sum(max(0.0, float(component.get("weight") or 0.0)) for component in weighted_components)
        paper_calibrated_weight = sum(
            max(0.0, float(component.get("weight") or 0.0))
            for component in weighted_components
            if self._paper_component_eligible(component)
        )
        live_calibrated_weight = sum(
            max(0.0, float(component.get("weight") or 0.0))
            for component in weighted_components
            if not bool(component.get("mae_imputed"))
            and int(component.get("bias_sample_count") or 0) >= self.min_live_component_calibration_days
        )
        calibration_coverage = paper_calibrated_weight / weight_total if weight_total > 0 else 0.0
        live_calibration_coverage = live_calibrated_weight / weight_total if weight_total > 0 else 0.0
        family_highs_c = [fmean(self._component_highs_c(component)) for component in evidence_components]
        model_spread_c = max(family_highs_c) - min(family_highs_c) if len(family_highs_c) >= 2 else None
        independent_settlement_days = int(context.get("independent_settlement_days") or 0)
        if (
            independent_settlement_days < self.min_live_independent_settlement_days
            or live_calibration_coverage + 1e-12 < self.min_calibration_coverage
            or (
                model_spread_c is not None
                and model_spread_c > self.max_live_model_spread_c + 1e-12
            )
        ):
            maturity_status = "provisional"
        else:
            maturity_status = "mature"
        return {
            "family_count": len(families),
            "families": families,
            "calibration_coverage": round(calibration_coverage, 8),
            "calibrated_weight": round(paper_calibrated_weight, 8),
            "live_calibration_coverage": round(live_calibration_coverage, 8),
            "live_calibrated_weight": round(live_calibrated_weight, 8),
            "weight_total": round(weight_total, 8),
            "model_spread_c": None if model_spread_c is None else round(model_spread_c, 4),
            "independent_settlement_days": independent_settlement_days,
            "independent_settlement_basis": str(context.get("independent_settlement_basis") or "missing"),
            "independent_settlement_authoritative": bool(context.get("independent_settlement_authoritative")),
            "maturity_status": maturity_status,
        }

    @staticmethod
    def _component_highs_c(component: dict[str, Any]) -> list[float]:
        """Return model evidence independently from ensemble-member availability."""
        for key in (
            "adjusted_daily_highs_c",
            "member_daily_highs_c",
            "member_daily_highs",
        ):
            values = [
                number
                for value in (component.get(key) or [])
                if (number := optional_float(value)) is not None
            ]
            if values:
                return values
        for key in (
            "model_daily_high_c",
            "model_daily_high",
            "daily_high_c",
            "mu",
        ):
            value = optional_float(component.get(key))
            if value is not None:
                return [value]
        return []

    def _paper_component_eligible(self, component: dict[str, Any]) -> bool:
        sample_count = int(component.get("bias_sample_count") or 0)
        if sample_count < self.min_paper_component_calibration_days:
            return False
        return not bool(component.get("mae_imputed"))

    def _quality_reasons(self, quality: dict[str, Any]) -> list[str]:
        reasons: list[str] = []
        if int(quality["family_count"]) < self.min_model_families:
            reasons.append("core_model_family_count_below_min")
        spread = optional_float(quality.get("model_spread_c"))
        if spread is None:
            reasons.append("core_model_spread_unavailable")
        return reasons

    def _build_audit_decision(
        self,
        item: dict[str, Any],
        prediction: dict[str, Any],
        context: dict[str, Any],
        *,
        force_skip_reasons: list[str] | None = None,
        extra_hard_blocks: list[str] | None = None,
        position_size_multiplier: float = 1.0,
    ) -> Decision:
        return self.build_decision(
            item["bucket"],
            item["probability_item"],
            prediction,
            context,
            min_edge=0.0,
            force_skip_reasons=force_skip_reasons,
            extra_hard_blocks=extra_hard_blocks,
            position_size_multiplier=position_size_multiplier,
        )

    def _position_multiplier(self, quality: dict[str, Any]) -> float:
        return self.provisional_position_multiplier if quality.get("maturity_status") == "provisional" else 1.0

    def _attach_metadata(
        self,
        decision: Decision,
        chosen: dict[str, Any],
        modal: dict[str, Any],
        ranked: list[dict[str, Any]],
        quality: dict[str, Any],
    ) -> None:
        maturity_status = str(quality.get("maturity_status") or "insufficient")
        position_size_multiplier = self._position_multiplier(quality)
        decision["core_modal"] = {
            "model_rank": int(chosen["rank"]),
            "modal_bucket_key": str(modal["bucket"].get("bucket_key") or ""),
            "chosen_bucket_key": str(chosen["bucket"].get("bucket_key") or ""),
            "ranked_bucket_count": len(ranked),
            "raw_edge": chosen.get("raw_edge"),
            "execution_buffer": chosen.get("execution_buffer"),
            "effective_edge": chosen.get("effective_edge"),
            "quality": quality,
            "maturity_status": maturity_status,
            "position_size_multiplier": position_size_multiplier,
            "thresholds": {
                "max_model_rank": self.max_model_rank,
                "min_model_probability": self.min_model_probability,
                "min_effective_edge": self.min_effective_edge,
                "min_market_ask": self.min_market_ask,
                "min_independent_settlement_days": self.min_paper_independent_settlement_days,
                "min_paper_independent_settlement_days": self.min_paper_independent_settlement_days,
                "min_live_independent_settlement_days": self.min_live_independent_settlement_days,
                "require_authoritative_truth": self.require_authoritative_truth,
                "min_component_calibration_days": self.min_paper_component_calibration_days,
                "min_paper_component_calibration_days": self.min_paper_component_calibration_days,
                "min_live_component_calibration_days": self.min_live_component_calibration_days,
                "min_calibration_coverage": self.min_calibration_coverage,
                "min_model_families": self.min_model_families,
                "max_model_spread_c": self.max_paper_model_spread_c,
                "max_paper_model_spread_c": self.max_paper_model_spread_c,
                "max_live_model_spread_c": self.max_live_model_spread_c,
                "provisional_position_multiplier": self.provisional_position_multiplier,
            },
        }
        if maturity_status == "provisional":
            decision["cautions"] = unique([*(decision.get("cautions") or []), CORE_MODAL_PROVISIONAL_CAUTION])
            decision["gate_reasons"] = unique([
                *(decision.get("gate_reasons") or []),
                CORE_MODAL_LIVE_MATURITY_REASON,
            ])
            decision["reasons"] = list(decision["gate_reasons"])
        spread = optional_float(quality.get("model_spread_c"))
        if spread is not None and spread > self.max_paper_model_spread_c + 1e-12:
            decision["cautions"] = unique([
                *(decision.get("cautions") or []),
                CORE_MODAL_WIDE_SPREAD_CAUTION,
            ])


def _split_thresholds(
    parameters: dict[str, Any],
    *,
    paper_key: str,
    live_key: str,
    legacy_key: str,
    paper_default: int,
    live_default: int,
) -> tuple[int, int]:
    legacy_value = parameters.get(legacy_key)
    if legacy_value is None:
        legacy_paper = paper_default
        legacy_live = live_default
    else:
        legacy_days = int(legacy_value)
        legacy_paper = paper_default if legacy_days == live_default else legacy_days
        legacy_live = max(live_default, legacy_days)
    paper_days = int(parameters.get(paper_key, legacy_paper))
    live_days = max(live_default, paper_days, int(parameters.get(live_key, legacy_live)))
    return paper_days, live_days
