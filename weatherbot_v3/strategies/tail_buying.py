from __future__ import annotations

from typing import Any

from .base import Decision, StrategyBase, optional_float


class TailBuyingStrategy(StrategyBase):
    strategy_name = "tail_buying"
    min_edge = 0.10
    max_ask = 0.15
    min_independent_settlement_days = 20
    max_order_usd = 50.0
    daily_candidate_cap = 5

    def __init__(self, parameters: dict[str, Any] | None = None):
        self.parameters = dict(parameters or {})
        self.min_edge = float(self.parameters.get("min_edge", self.min_edge))
        self.max_ask = float(self.parameters.get("max_ask", self.max_ask))
        self.min_independent_settlement_days = int(
            self.parameters.get("min_settlement_days", self.min_independent_settlement_days)
        )
        self.max_order_usd = float(self.parameters.get("max_order_usd", self.max_order_usd))
        self.daily_candidate_cap = int(self.parameters.get("daily_candidate_cap", self.daily_candidate_cap))

    def evaluate(
        self,
        bucket: dict[str, Any],
        probability: dict[str, Any],
        prediction: dict[str, Any],
        context: dict[str, Any],
    ) -> Decision | None:
        ask = optional_float(bucket.get("best_ask"))
        model_probability = optional_float(probability.get("probability"))
        if ask is None or model_probability is None:
            return None
        if ask > self.max_ask:
            return None
        if model_probability - ask < self.min_edge:
            return None
        extra_reasons = []
        independent_days = int(context.get("independent_settlement_days") or 0)
        if independent_days < self.min_independent_settlement_days:
            extra_reasons.append("tail_independent_settlement_days_below_20")
        decision = self.build_decision(
            bucket,
            probability,
            prediction,
            context,
            min_edge=self.min_edge,
            allow_low_price_tail=True,
            extra_gate_reasons=extra_reasons,
            force_skip_reasons=extra_reasons,
        )
        if decision.get("position_size_usd") is not None:
            decision["position_size_usd"] = min(float(decision["position_size_usd"] or 0.0), self.max_order_usd)
        decision["tail_buying"] = {
            "max_ask": self.max_ask,
            "min_edge": self.min_edge,
            "independent_settlement_days": independent_days,
            "min_independent_settlement_days": self.min_independent_settlement_days,
            "single_order_cap_usd": self.max_order_usd,
            "daily_candidate_cap": self.daily_candidate_cap,
        }
        return decision

    def evaluate_many(
        self,
        buckets: list[dict[str, Any]],
        probabilities: dict[str, dict[str, Any]],
        prediction: dict[str, Any],
        context: dict[str, Any],
    ) -> list[Decision]:
        candidates = [
            decision for bucket in buckets
            if (decision := self.evaluate(bucket, probabilities.get(str(bucket.get("bucket_key") or ""), {}), prediction, context)) is not None
        ]
        candidates.sort(key=lambda row: float(row.get("edge") or -999), reverse=True)
        selected = candidates[:self.daily_candidate_cap]
        for skipped in candidates[self.daily_candidate_cap:]:
            skipped["paper_allowed"] = False
            skipped["paper_decision"] = "skip"
            skipped["gate_status"] = "skip"
            skipped["blocked_reason_primary"] = "tail_daily_candidate_cap"
            skipped["gate_reasons"] = list(dict.fromkeys([*(skipped.get("gate_reasons") or []), "tail_daily_candidate_cap"]))
        return selected
