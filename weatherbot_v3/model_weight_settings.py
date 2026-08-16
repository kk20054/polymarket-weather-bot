from __future__ import annotations

import json
import math
from typing import Any

from .env_utils import env_value, set_env_value


MODE_ENV = "DEB_WEIGHT_POLICY"
WEIGHTS_ENV = "DEB_MANUAL_WEIGHTS_JSON"
AVAILABLE_FAMILIES = ("weathercom_v3", "gfs", "ecmwf", "icon", "gem", "jma")
DEFAULT_MANUAL_WEIGHTS = {
    "weathercom_v3": 0.484,
    "gfs": 0.152,
    "ecmwf": 0.104,
    "icon": 0.095,
    "gem": 0.0,
    "jma": 0.0,
}


def model_weight_policy() -> str:
    return "manual" if env_value(MODE_ENV, "dynamic").strip().lower() == "manual" else "dynamic"


def manual_model_weights() -> dict[str, float]:
    stored = env_value(WEIGHTS_ENV, "")
    values: dict[str, Any] = {}
    if stored:
        try:
            decoded = json.loads(stored)
            if isinstance(decoded, dict):
                values = decoded
        except (TypeError, ValueError, json.JSONDecodeError):
            values = {}
    weights = {
        family: _valid_weight(values.get(family, DEFAULT_MANUAL_WEIGHTS[family]))
        for family in AVAILABLE_FAMILIES
    }
    if sum(weights.values()) <= 0:
        weights = dict(DEFAULT_MANUAL_WEIGHTS)
    return _normalized(weights)


def model_weight_settings() -> dict[str, Any]:
    mode = model_weight_policy()
    return {
        "ok": True,
        "mode": mode,
        "weights": manual_model_weights(),
        "available_families": list(AVAILABLE_FAMILIES),
        "method": "manual_override_v1" if mode == "manual" else "prior_inverse_mae_shrinkage_v1",
    }


def update_model_weight_settings(mode: str, weights: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in {"dynamic", "manual"}:
        raise ValueError("unsupported_model_weight_mode")
    if normalized_mode == "manual":
        supplied = weights or {}
        unknown = sorted(set(supplied) - set(AVAILABLE_FAMILIES))
        if unknown:
            raise ValueError("unsupported_model_weight_family")
        merged = {
            family: _valid_weight(supplied.get(family, DEFAULT_MANUAL_WEIGHTS[family]))
            for family in AVAILABLE_FAMILIES
        }
        normalized = _normalized(merged)
        set_env_value(WEIGHTS_ENV, json.dumps(normalized, separators=(",", ":"), sort_keys=True))
    set_env_value(MODE_ENV, normalized_mode)
    return model_weight_settings()


def _valid_weight(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number) or number < 0:
        return 0.0
    return min(number, 1_000_000.0)


def _normalized(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("manual_model_weights_require_positive_total")
    return {family: round(value / total, 8) for family, value in weights.items()}
