from __future__ import annotations

import hashlib
from typing import Any

from ..sizing import size_position
from .base import Decision, StrategyBase, bucket_center, bucket_sort_key, optional_float


class LadderGridStrategy(StrategyBase):
    strategy_name = "ladder_grid"
    min_edge = 0.03

    def __init__(self, parameters: dict[str, Any] | None = None):
        self.parameters = dict(parameters or {})
        self.min_edge = float(self.parameters.get("min_edge", self.min_edge))
        self.group_exposure_multiplier = float(self.parameters.get("group_exposure_multiplier", 0.60))

    def evaluate(
        self,
        bucket: dict[str, Any],
        probability: dict[str, Any],
        prediction: dict[str, Any],
        context: dict[str, Any],
    ) -> Decision | None:
        return None

    def evaluate_many(
        self,
        buckets: list[dict[str, Any]],
        probabilities: dict[str, dict[str, Any]],
        prediction: dict[str, Any],
        context: dict[str, Any],
    ) -> list[Decision]:
        selected = self._select_ladder_buckets(buckets, prediction)
        if len(selected) != 3:
            return []
        required_min_edge = max(self.min_edge, float(context.get("paper_min_trade_edge") or 0.05))
        enriched: list[tuple[dict[str, Any], dict[str, Any], float, float]] = []
        for bucket in selected:
            probability = probabilities.get(str(bucket.get("bucket_key") or ""), {})
            model_probability = optional_float(probability.get("probability"))
            ask = optional_float(bucket.get("best_ask"))
            if model_probability is None or ask is None:
                return []
            edge = model_probability - ask
            if edge < required_min_edge:
                return []
            enriched.append((bucket, probability, model_probability, ask))

        center_bucket, center_probability, center_prob, center_ask = enriched[1]
        center_size = size_position(
            center_prob,
            center_ask,
            bankroll=float(context.get("paper_bankroll") or context.get("bankroll") or 0.0),
            max_per_trade_usd=float(context.get("paper_max_per_trade_usd") or context.get("max_per_trade_usd") or 0.0),
            kelly_multiplier=float(context.get("paper_kelly_multiplier") or context.get("kelly_multiplier") or 0.25),
            bankroll_fraction_cap=float(context.get("paper_bankroll_fraction_cap") or context.get("bankroll_fraction_cap") or 0.125),
        )
        total_size = round(center_size.capped_position_size_usd * self.group_exposure_multiplier, 4)
        if total_size <= 0:
            return []
        total_probability = sum(item[2] for item in enriched)
        if total_probability <= 0:
            return []
        group_id = self._ladder_group_id(enriched, prediction, context)
        decisions: list[Decision] = []
        for bucket, probability, model_probability, _ask in enriched:
            allocation = total_size * (model_probability / total_probability)
            decision = self.build_decision(
                bucket,
                probability,
                prediction,
                context,
                min_edge=required_min_edge,
                ladder_group_id=group_id,
                position_size_override=allocation,
            )
            decision["ladder_group"] = {
                "ladder_group_id": group_id,
                "group_position_size_usd": total_size,
                "allocation_weight": round(model_probability / total_probability, 8),
                "bucket_count": len(enriched),
                "center_bucket_key": center_bucket.get("bucket_key"),
                "center_model_probability": center_probability.get("probability"),
                "center_kelly_fraction": center_size.kelly_fraction,
            }
            decisions.append(decision)
        return decisions

    def _select_ladder_buckets(self, buckets: list[dict[str, Any]], prediction: dict[str, Any]) -> list[dict[str, Any]]:
        mu = optional_float(prediction.get("mu"))
        if mu is None:
            return []
        ordered = sorted(buckets, key=bucket_sort_key)
        if len(ordered) < 3:
            return []
        center_index = min(
            range(len(ordered)),
            key=lambda index: abs((bucket_center(ordered[index]) if bucket_center(ordered[index]) is not None else mu) - mu),
        )
        if center_index <= 0 or center_index >= len(ordered) - 1:
            return []
        return ordered[center_index - 1:center_index + 2]

    def _ladder_group_id(
        self,
        enriched: list[tuple[dict[str, Any], dict[str, Any], float, float]],
        prediction: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        raw = "|".join([
            self.strategy_name,
            str(prediction.get("city_key") or enriched[1][0].get("city") or ""),
            str(prediction.get("target_date") or enriched[1][0].get("target_date") or ""),
            str(prediction.get("issued_at") or ""),
            ",".join(str(item[0].get("bucket_key") or "") for item in enriched),
            str(context.get("decision_version") or "signal-decision-v2"),
            str(context.get("strategy_revision_id") or "legacy_unversioned"),
        ])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
