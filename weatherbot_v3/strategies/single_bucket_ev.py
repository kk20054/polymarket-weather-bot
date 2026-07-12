from __future__ import annotations

from typing import Any

from .base import Decision, StrategyBase


class SingleBucketEVStrategy(StrategyBase):
    strategy_name = "single_bucket_ev"
    min_edge = 0.05

    def __init__(self, parameters: dict[str, Any] | None = None):
        self.parameters = dict(parameters or {})
        self.min_edge = float(self.parameters.get("min_edge", self.min_edge))

    def evaluate(
        self,
        bucket: dict[str, Any],
        probability: dict[str, Any],
        prediction: dict[str, Any],
        context: dict[str, Any],
    ) -> Decision:
        return self.build_decision(bucket, probability, prediction, context, min_edge=self.min_edge)
