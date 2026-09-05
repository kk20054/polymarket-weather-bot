from __future__ import annotations

import math
from typing import Any


BIAS_SHRINKAGE_PRIOR_SAMPLES = 10
BIAS_MAX_ABS_C = 2.5
BIAS_RUNTIME_METHOD = "zero_prior_shrinkage_v1"
PREDICTIVE_ERROR_VERSION = "runtime-bias-walk-forward-v2"
PREDICTIVE_ERROR_BASIS = "prior_only_runtime_bias"


def runtime_bias_c(raw_bias_c: float, sample_count: int) -> float:
    if sample_count <= 0:
        return 0.0
    shrinkage = sample_count / (sample_count + BIAS_SHRINKAGE_PRIOR_SAMPLES)
    return round(max(-BIAS_MAX_ABS_C, min(BIAS_MAX_ABS_C, raw_bias_c * shrinkage)), 4)


def predictive_error_metadata(calibration: dict[str, Any]) -> dict[str, Any]:
    version = str(calibration.get("predictive_error_version") or "")
    result = {
        "predictive_error_c": None,
        "predictive_error_basis": "legacy_unverified" if version != PREDICTIVE_ERROR_VERSION else "unavailable",
        "predictive_error_version": version,
        "predictive_error_sample_count": 0,
    }
    if version != PREDICTIVE_ERROR_VERSION or calibration.get("predictive_error_basis") != PREDICTIVE_ERROR_BASIS:
        return result
    count = int(calibration.get("predictive_error_sample_count") or 0)
    value = calibration.get("walk_forward_mae_7d_c")
    if count > 0 and value is not None and math.isfinite(float(value)) and float(value) >= 0:
        result.update({
            "predictive_error_c": round(float(value), 4),
            "predictive_error_basis": PREDICTIVE_ERROR_BASIS,
            "predictive_error_sample_count": count,
        })
    return result
