from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..config import DATA_DIR
from ..db import connect, init_v3_db, upsert_model_reprice_event, utc_now
from ..deb import bucket_bounds_in_prediction_unit, sigma_with_floor
from ..env_utils import env_value
from ..forecast_time import (
    DEFAULT_COMPONENT_MAX_SKEW_HOURS,
    FORECAST_COMPONENT_COHORT_VERSION,
    apply_forecast_component_cohort,
    assess_forecast_run,
    forecast_component_cohort_as_of,
    forecast_snapshot_selection_mode,
    historical_build_requires_explicit_as_of,
    parse_utc,
)
from ..registry import CitySettlementProfile, forecast_source_matches_profile_location, get_city_profile


ALGO = "ensemble_v1"
POLYWX_ALIGNED_ALGO = "polywx_aligned_deb_v1"
BIAS_TABLE_PATH = DATA_DIR / "bias_table.json"
MIN_FAMILIES_FOR_ENSEMBLE = 2
MIN_MEMBER_COUNT_FOR_SINGLE_FAMILY = 5
SIGMA_FLOOR_C = 0.5
UNCALIBRATED_SIGMA_C = 1.2
BIAS_MIN_SAMPLE_COUNT = 20
BIAS_PAPER_MIN_SAMPLE_COUNT = 10
BIAS_SHRINKAGE_PRIOR_SAMPLES = 10
BIAS_MAX_ABS_C = 2.5
BIAS_RUNTIME_METHOD = "zero_prior_shrinkage_v1"
FORECAST_SNAPSHOT_SELECTION_VERSION = "forecast-snapshot-selection-v2"
# Dynamic weighting starts as soon as a leakage-free forecast/truth pair is
# available. Sparse models keep their prior share instead of disappearing;
# live maturity remains on the stricter 20-sample contract.
DYNAMIC_WEIGHT_MIN_SAMPLES = 20
DYNAMIC_WEIGHT_FULL_SAMPLES = 40
DYNAMIC_WEIGHT_PERFORMANCE_BLEND_MAX = 0.75
DYNAMIC_WEIGHT_MAX_SHARE = 0.45
DYNAMIC_WEIGHT_ERROR_FLOOR_C = 0.25
DYNAMIC_WEIGHT_METHOD = "prior_inverse_mae_shrinkage_v1"

REGION_MODEL_WEIGHTS = {
    "us": {"gfs": 0.40, "ecmwf": 0.50, "hrrr": 0.10},
    "china_hk": {"gfs": 0.20, "ecmwf": 0.50, "cma": 0.30},
    "japan_korea_taipei": {"gfs": 0.20, "ecmwf": 0.40, "jma": 0.40},
    "singapore": {"gfs": 0.40, "ecmwf": 0.60},
    "global": {"gfs": 0.30, "ecmwf": 0.45, "icon": 0.25},
}
POLYWX_ALIGNED_MODEL_WEIGHTS = {
    "weathercom_v3": 0.484,
    "gfs": 0.152,
    "ecmwf": 0.104,
    "icon": 0.095,
    # Retained as diagnostic model families. Historical evidence rejects
    # fixed production priors for both, especially across China stations.
    "gem": 0.0,
    "jma": 0.0,
}


def fetch_ensemble(
    icao: str,
    target_date: str,
    models: list[str] | tuple[str, ...] | None = None,
    *,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Read already-persisted forecast members as member/hour/temp_c rows.

    This intentionally does not call Open-Meteo. The collector layer owns network
    fetches; this layer consumes the persisted Layer 3 archive.
    """
    station = str(icao or "").strip().upper()
    wanted_models = {str(model).strip().lower() for model in (models or []) if str(model).strip()}
    init_v3_db(path)
    with connect(path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT fr.id AS run_id, fr.city, fr.target_date, fr.source, fr.model,
                       fr.unit, fr.station_id, fr.run_at, fr.retrieved_at,
                       fr.available_at, fr.availability_basis, fr.horizon, fr.lead_hours,
                       fr.timezone, fr.training_eligible, fr.parse_status,
                       fm.member_id,
                       fm.high_temp, fm.hourly_json
                FROM forecast_runs fr
                JOIN forecast_members fm ON fm.run_id = fr.id
                WHERE UPPER(COALESCE(fr.station_id, '')) = ?
                  AND fr.target_date = ?
                  AND COALESCE(fr.training_eligible, 0) = 1
                  AND COALESCE(fr.parse_status, 'parsed') = 'parsed'
                ORDER BY COALESCE(fr.available_at, fr.retrieved_at) DESC, fr.id DESC, fm.member_id
                """,
                (station, str(target_date)),
            ).fetchall()
        ]
    rows = [
        row for row in rows
        if assess_forecast_run(
            row,
            target_date=target_date,
            timezone_name=str(row.get("timezone") or "UTC"),
            require_training=True,
        )["ok"]
    ]
    latest_by_source: dict[str, int] = {}
    for row in rows:
        source = str(row.get("source") or "").lower()
        model = str(row.get("model") or "").lower()
        if wanted_models and model not in wanted_models and source not in wanted_models:
            continue
        latest_by_source.setdefault(source, int(row.get("run_id") or 0))

    points: list[dict[str, Any]] = []
    for row in rows:
        source = str(row.get("source") or "").lower()
        run_id = int(row.get("run_id") or 0)
        if latest_by_source.get(source) != run_id:
            continue
        unit = str(row.get("unit") or "C").upper()
        hourly = _loads(row.get("hourly_json"), [])
        member = str(row.get("member_id") or "deterministic")
        for point in hourly:
            if not isinstance(point, dict):
                continue
            temp = _first_number(point.get("temperature_2m_c"), point.get("temperature_c"))
            if temp is None:
                temp = _first_number(point.get("temperature_2m"), point.get("temp"))
                if temp is not None:
                    temp = convert_temperature(temp, unit, "C")
            valid_at = str(point.get("valid_at") or point.get("hour") or "")
            if temp is None or not valid_at:
                continue
            points.append({
                "member": f"{source}:{member}",
                "source": source,
                "model": row.get("model") or source.replace("openmeteo_", ""),
                "hour": valid_at,
                "temp_c": round(float(temp), 3),
            })
    return points


def daily_max_distribution(rows: list[dict[str, Any]], tz: str) -> list[dict[str, Any]]:
    zone = _zone(tz)
    highs: dict[str, dict[str, Any]] = {}
    for row in rows:
        member = str(row.get("member") or "")
        if not member:
            continue
        parsed = _parse_time(str(row.get("hour") or row.get("valid_at") or ""))
        temp_c = _first_number(row.get("temp_c"), row.get("temperature_c"), row.get("temperature_2m_c"))
        if parsed is None or temp_c is None:
            continue
        local = parsed.astimezone(zone)
        value = float(temp_c)
        current = highs.get(member)
        if current is None or value > float(current.get("daily_max_c") or -999):
            highs[member] = {
                "member": member,
                "daily_max_c": value,
                "peak_hour_local": local.strftime("%H:00"),
                "source": row.get("source") or "",
                "model": row.get("model") or "",
            }
    return list(highs.values())


def bucket_probabilities(samples: list[Any], bucket_edges: list[dict[str, Any]]) -> dict[str, Any]:
    weighted = _weighted_samples(samples)
    total_weight = sum(weight for _, weight in weighted)
    items: list[dict[str, Any]] = []
    if total_weight <= 0:
        return {"ok": False, "method": "ensemble-sample-v1", "items": [], "sum_probability": 0.0, "notes": ["empty_samples"]}
    for bucket in bucket_edges:
        label = str(bucket.get("label") or bucket.get("bucket_label") or bucket.get("bucket_key") or "")
        low = _first_number(bucket.get("lower_c"), bucket.get("bucket_lower_c"), bucket.get("bucket_low"))
        high = _first_number(bucket.get("upper_c"), bucket.get("bucket_upper_c"), bucket.get("bucket_high"))
        mass = 0.0
        for value, weight in weighted:
            if _sample_in_bucket(value, low, high):
                mass += weight
        probability = mass / total_weight
        items.append({
            "bucket_key": bucket.get("bucket_key") or label,
            "bucket_label": label,
            "bucket_low": low,
            "bucket_high": high,
            "probability": probability,
            "probability_raw": probability,
        })
    total = sum(float(item["probability"]) for item in items)
    if items and abs(total - 1.0) > 1e-6 and total > 0:
        for item in items:
            item["probability"] = float(item["probability"]) / total
            item["probability_raw"] = item["probability"]
        total = 1.0
    return {
        "ok": bool(items),
        "method": "ensemble-sample-v1",
        "items": items,
        "sum_probability": total,
        "normalized": bool(items),
        "notes": [],
    }


def build_ensemble_prediction(
    city_key: str,
    target_date: str,
    *,
    issued_at: str | None = None,
    path: Path | None = None,
    bias_table: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    profile = get_city_profile(city_key)
    if not profile:
        return {"ok": False, "city_key": city_key, "target_date": target_date, "reasons": ["unknown_city"]}
    if issued_at is None and historical_build_requires_explicit_as_of(target_date, profile.timezone):
        return {
            "ok": False,
            "city_key": city_key,
            "target_date": target_date,
            "reasons": ["historical_build_requires_explicit_issued_at"],
        }
    init_v3_db(path)
    algo = _deb_algo()
    requested_as_of = issued_at or utc_now()
    selection_mode = forecast_snapshot_selection_mode(
        target_date,
        profile.timezone,
        as_of=requested_as_of,
    )
    if selection_mode == "invalid":
        return {
            "ok": False,
            "city_key": city_key,
            "target_date": target_date,
            "reasons": ["forecast_as_of_invalid"],
        }
    rows = _latest_forecast_members(
        profile,
        target_date,
        path,
        as_of=requested_as_of,
        selection_mode=selection_mode,
    )
    components = _components_from_rows(
        profile,
        rows,
        bias_table if bias_table is not None else load_bias_table(),
        path,
        target_date=target_date,
        selection_mode=selection_mode,
    )
    cohort_as_of, _historical_rebase = forecast_component_cohort_as_of(
        components,
        requested_as_of=requested_as_of,
        target_date=target_date,
        timezone_name=profile.timezone,
    )
    cohort = apply_forecast_component_cohort(
        components,
        as_of=cohort_as_of,
    )
    components = list(cohort.get("components") or [])
    usable = [
        component
        for component in components
        if component["member_count"] > 0
        and component["family"] in region_model_weights(profile)
    ]
    _normalize_component_weights(usable, algo)
    active_components = [component for component in usable if float(component.get("weight") or 0.0) > 0.0]
    # Source sufficiency and fusion participation are separate contracts.
    # During V3 cold start, other valid model families remain diagnostic-only;
    # they still prove that the input cohort is complete even though their
    # production weight is zero.
    available_families = {component["family"] for component in usable}
    active_families = {component["family"] for component in active_components}
    available_members = sum(int(component["member_count"]) for component in usable)
    total_members = sum(int(component["member_count"]) for component in active_components)
    if (
        len(available_families) < MIN_FAMILIES_FOR_ENSEMBLE
        and available_members < MIN_MEMBER_COUNT_FOR_SINGLE_FAMILY
    ):
        return {
            "ok": False,
            "city_key": city_key,
            "target_date": target_date,
            "reasons": [
                "insufficient_ensemble_sources",
                *(cohort.get("warnings") or []),
            ],
            "families": sorted(available_families),
            "active_families": sorted(active_families),
            "member_count": available_members,
            "active_member_count": total_members,
            "components": usable,
            "excluded_components": cohort.get("excluded") or [],
            "cohort_as_of": cohort.get("cohort_as_of") or "",
        }

    weighted = _weighted_member_highs(profile, active_components)
    if not weighted:
        return {"ok": False, "city_key": city_key, "target_date": target_date, "reasons": ["empty_weighted_samples"]}
    values = [value for value, _weight, _meta in weighted]
    weights = [weight for _value, weight, _meta in weighted]
    mu = _weighted_mean(values, weights)
    sigma_from_spread_c = _weighted_std(values, weights)
    residual_terms = [
        (float(component.get("effective_mae_c")), float(component.get("weight") or 0.0))
        for component in active_components
        if _first_number(component.get("effective_mae_c")) is not None
    ]
    residual_weight = sum(weight for _value, weight in residual_terms)
    sigma_from_history_c = math.sqrt(
        sum((value ** 2) * weight for value, weight in residual_terms) / residual_weight
    ) if residual_weight > 0 else UNCALIBRATED_SIGMA_C
    # Independent model spread and recent forecast error are orthogonal
    # uncertainty terms. Combining them prevents deterministic source means
    # from producing unrealistically narrow one-degree market distributions.
    sigma_c = math.sqrt(sigma_from_spread_c ** 2 + sigma_from_history_c ** 2)
    sigma_floor = convert_temperature_delta(SIGMA_FLOOR_C, "C", profile.unit)
    sigma = sigma_with_floor(sigma_c if profile.unit == "C" else convert_temperature_delta(sigma_c, "C", profile.unit), sigma_floor)
    issued = requested_as_of
    samples_unit = [
        {
            "value": round(convert_temperature(value, "C", profile.unit), 4),
            "weight": round(weight, 8),
            **meta,
        }
        for value, weight, meta in weighted
    ]
    peak = _weighted_peak_hour(active_components)
    weighted_bias_c = sum(
        float(component.get("bias_correction_c") or 0.0) * float(component.get("weight") or 0.0)
        for component in active_components
    )
    calibration_coverage_weight = sum(
        float(component.get("weight") or 0.0)
        for component in active_components
        if not component.get("mae_imputed")
    )
    return {
        "ok": True,
        "city_key": profile.city,
        "target_date": target_date,
        "issued_at": issued,
        "mu": round(convert_temperature(mu, "C", profile.unit), 4),
        "sigma": round(sigma, 4),
        "unit": profile.unit,
        "method": algo,
        "deb_version": algo,
        "forecast_algo": algo,
        "algo": algo,
        "weight_method": DYNAMIC_WEIGHT_METHOD if algo == POLYWX_ALIGNED_ALGO else "fixed_region_prior_v1",
        "model_weights": {component["source"]: component["weight"] for component in usable},
        "member_count": len(samples_unit),
        "available_model_families": sorted(available_families),
        "active_model_families": sorted(active_families),
        "components": usable,
        "excluded_components": cohort.get("excluded") or [],
        "cohort_as_of": cohort.get("cohort_as_of") or issued,
        "cohort_contract_version": FORECAST_COMPONENT_COHORT_VERSION,
        "snapshot_selection_mode": selection_mode,
        "snapshot_selection_version": FORECAST_SNAPSHOT_SELECTION_VERSION,
        "calibration_coverage_weight": round(calibration_coverage_weight, 6),
        "source_run_ids": sorted({
            int(run_id)
            for component in active_components
            for run_id in (component.get("source_run_ids") or [component["run_id"]])
            if int(run_id or 0) > 0
        }),
        "member_daily_highs": {
            component["source"]: [
                round(convert_temperature(value, "C", profile.unit), 4)
                for value in component["adjusted_daily_highs_c"]
            ]
            for component in usable
        },
        "sigma_from_spread": round(convert_temperature_delta(sigma_from_spread_c, "C", profile.unit), 4),
        "sigma_from_history": round(convert_temperature_delta(sigma_from_history_c, "C", profile.unit), 4),
        "bias_correction": round(convert_temperature_delta(weighted_bias_c, "C", profile.unit), 4),
        "bias_sample_count": min((int(component.get("bias_sample_count") or 0) for component in active_components), default=0),
        "observed_floor": None,
        "sigma_floor": sigma_floor,
        "time_decay_factor": 1.0,
        "mu_observed_floor_applied": False,
        "peak_hour": peak.get("peak_hour") or "",
        "peak_temp": convert_temperature(peak["peak_temp_c"], "C", profile.unit) if peak.get("peak_temp_c") is not None else None,
        "peak_source": "ensemble_weighted",
        "ensemble_samples": samples_unit,
        "ensemble_sample_weights": [row["weight"] for row in samples_unit],
        "build_warnings": sorted(set([
            *_source_warnings(usable, algo),
            *(cohort.get("warnings") or []),
            *(["mae_imputed_for_uncalibrated_sources"] if calibration_coverage_weight < 1.0 - 1e-9 else []),
            *(["low_calibration_coverage"] if calibration_coverage_weight < 0.5 else []),
            *([] if residual_weight > 0 else ["uncalibrated_sigma_default"]),
        ])),
    }


def distribution_for_prediction(prediction: dict[str, Any], buckets: list[dict[str, Any]]) -> dict[str, Any] | None:
    samples = prediction.get("ensemble_samples") or []
    unit = str(prediction.get("unit") or "C").upper()
    if not samples:
        return None
    converted: list[dict[str, Any]] = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        value = _first_number(sample.get("value"), sample.get("daily_max"), sample.get("daily_max_c"))
        if value is None:
            continue
        converted.append({"value": convert_temperature(float(value), unit, "C"), "weight": _first_number(sample.get("weight")) or 1.0})
    bucket_edges = []
    for bucket in buckets:
        bounds = bucket_bounds_in_prediction_unit(bucket, "C")
        if bounds is None:
            continue
        low, high = bounds
        bucket_edges.append({
            "bucket_key": bucket.get("bucket_key") or "",
            "bucket_label": bucket.get("bucket_label") or "",
            "lower_c": None if math.isinf(low) and low < 0 else low,
            "upper_c": None if math.isinf(high) and high > 0 else high,
        })
    result = bucket_probabilities(converted, bucket_edges)
    for item, bucket in zip(result.get("items") or [], buckets):
        item["market_id"] = bucket.get("market_id") or ""
        item["yes_token_id"] = bucket.get("yes_token_id") or ""
        item["bucket_direction"] = bucket.get("bucket_direction") or ""
        item["bucket_unit"] = unit
        market_probability = _market_probability(bucket)
        item["market_probability"] = market_probability
        item["best_bid"] = _first_number(bucket.get("best_bid"))
        item["best_ask"] = _first_number(bucket.get("best_ask"))
        item["price"] = _first_number(bucket.get("price"))
        item["edge"] = None if market_probability is None else float(item.get("probability") or 0.0) - market_probability
    ranked = sorted(result.get("items") or [], key=lambda row: float(row.get("probability") or 0.0), reverse=True)
    result.update({
        "mu": prediction.get("mu"),
        "sigma": prediction.get("sigma"),
        "unit": unit,
        "method": "ensemble-sample-v1",
        "top_model": ranked[:5],
    })
    return result


def family_mixture_distribution_for_prediction(
    prediction: dict[str, Any],
    buckets: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Blend real ensemble members with calibrated deterministic family kernels."""

    if str(prediction.get("forecast_algo") or prediction.get("method") or "") != POLYWX_ALIGNED_ALGO:
        return None
    components = [
        component
        for component in (prediction.get("components") or [])
        if isinstance(component, dict) and float(component.get("weight") or 0.0) > 0.0
    ]
    has_member_ensemble = any(
        "ensemble" in str(component.get("source") or "").lower()
        and len(component.get("adjusted_daily_highs_c") or []) >= MIN_MEMBER_COUNT_FOR_SINGLE_FAMILY
        for component in components
    )
    if not has_member_ensemble:
        return None

    rows: list[dict[str, Any]] = []
    family_methods: dict[str, str] = {}
    for bucket in buckets:
        bounds = bucket_bounds_in_prediction_unit(bucket, "C")
        if bounds is None:
            continue
        low, high = bounds
        probability = 0.0
        family_breakdown: dict[str, float] = {}
        for component in components:
            family = str(component.get("family") or component.get("source") or "unknown")
            family_weight = float(component.get("weight") or 0.0)
            highs = [
                float(value)
                for value in (component.get("adjusted_daily_highs_c") or [])
                if _first_number(value) is not None
            ]
            is_real_ensemble = (
                "ensemble" in str(component.get("source") or "").lower()
                and len(highs) >= MIN_MEMBER_COUNT_FOR_SINGLE_FAMILY
            )
            if is_real_ensemble:
                family_probability = sum(
                    1.0 for value in highs if _sample_in_bucket(value, low, high)
                ) / len(highs)
                family_methods[family] = "empirical_members"
            else:
                center = _first_number(component.get("model_daily_high_c"))
                if center is None:
                    continue
                sigma_c = max(
                    SIGMA_FLOOR_C,
                    _first_number(component.get("effective_mae_c"), component.get("mae_7d"))
                    or UNCALIBRATED_SIGMA_C,
                )
                family_probability = _gaussian_mass(float(center), float(sigma_c), low, high)
                family_methods[family] = "calibrated_gaussian_kernel"
            contribution = family_weight * family_probability
            family_breakdown[family] = contribution
            probability += contribution
        market_probability = _market_probability(bucket)
        rows.append({
            "bucket_key": bucket.get("bucket_key") or "",
            "bucket_label": bucket.get("bucket_label") or "",
            "bucket_low": None if math.isinf(low) and low < 0 else low,
            "bucket_high": None if math.isinf(high) and high > 0 else high,
            "bucket_direction": bucket.get("bucket_direction") or "",
            "bucket_unit": str(prediction.get("unit") or "C").upper(),
            "market_id": bucket.get("market_id") or "",
            "yes_token_id": bucket.get("yes_token_id") or "",
            "probability": probability,
            "probability_raw": probability,
            "family_contributions": family_breakdown,
            "market_probability": market_probability,
            "best_bid": _first_number(bucket.get("best_bid")),
            "best_ask": _first_number(bucket.get("best_ask")),
            "price": _first_number(bucket.get("price")),
            "edge": None if market_probability is None else probability - market_probability,
        })
    total = sum(float(row.get("probability") or 0.0) for row in rows)
    if total <= 0:
        return None
    for row in rows:
        row["probability"] = float(row["probability"]) / total
        row["probability_raw"] = row["probability"]
        market_probability = row.get("market_probability")
        row["edge"] = (
            None
            if market_probability is None
            else float(row["probability"]) - float(market_probability)
        )
        row["family_contributions"] = {
            family: value / total
            for family, value in row["family_contributions"].items()
        }
    ranked = sorted(rows, key=lambda row: float(row.get("probability") or 0.0), reverse=True)
    return {
        "ok": True,
        "method": "family-mixture-v1",
        "items": rows,
        "sum_probability": sum(float(row["probability"]) for row in rows),
        "normalized": True,
        "mu": prediction.get("mu"),
        "sigma": prediction.get("sigma"),
        "unit": str(prediction.get("unit") or "C").upper(),
        "family_methods": family_methods,
        "top_model": ranked[:5],
    }


def _gaussian_mass(mu: float, sigma: float, low: float, high: float) -> float:
    lower = 0.0 if math.isinf(low) and low < 0 else _normal_cdf((low - mu) / sigma)
    upper = 1.0 if math.isinf(high) and high > 0 else _normal_cdf((high - mu) / sigma)
    return max(0.0, upper - lower)


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def previous_run_samples(
    city_key: str,
    target_date: str,
    *,
    path: Path | None = None,
    models: list[str] | tuple[str, ...] | None = None,
    max_lead_days: int = 7,
) -> list[dict[str, Any]]:
    """Return diagnostic fixed-lead slices; never use these as single-run training rows."""
    wanted_models = {str(model).strip().lower() for model in (models or []) if str(model).strip()}
    max_lead = max(1, min(int(max_lead_days or 7), 7))
    with connect(path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT fr.id AS run_id, fr.city, fr.target_date, fr.source, fr.model,
                       fr.unit, fr.lead_hours, fr.run_at, fr.retrieved_at,
                       fr.available_at, fr.availability_basis, fr.horizon,
                       fr.timezone, fr.training_eligible, fr.parse_status,
                       fm.member_id, fm.high_temp
                FROM forecast_runs fr
                JOIN forecast_members fm ON fm.run_id = fr.id
                WHERE fr.city = ?
                  AND fr.target_date = ?
                  AND COALESCE(fr.parse_status, 'parsed') = 'parsed'
                  AND fr.source LIKE 'openmeteo_previous_%'
                ORDER BY fr.lead_hours ASC, fr.model, fm.member_id
                """,
                (str(city_key), str(target_date)),
            ).fetchall()
        ]
    samples: list[dict[str, Any]] = []
    for row in rows:
        model = str(row.get("model") or "").lower()
        source = str(row.get("source") or "").lower()
        if wanted_models and model not in wanted_models and source not in wanted_models:
            continue
        lead_hours = _first_number(row.get("lead_hours")) or 0.0
        if lead_hours > max_lead * 24:
            continue
        value = _first_number(row.get("high_temp"))
        if value is None:
            continue
        samples.append({
            "value": float(value),
            "weight": 1.0,
            "source": row.get("source"),
            "model": row.get("model"),
            "member": row.get("member_id"),
            "lead_hours": lead_hours,
            "run_at": row.get("run_at"),
            "retrieved_at": row.get("retrieved_at"),
            "diagnostic_only": True,
            "time_semantics": "fixed_lead_slice_not_single_model_run",
        })
    return samples


def previous_run_distribution_for_buckets(
    city_key: str,
    target_date: str,
    buckets: list[dict[str, Any]],
    *,
    path: Path | None = None,
    models: list[str] | tuple[str, ...] | None = None,
    max_lead_days: int = 7,
) -> dict[str, Any]:
    samples = previous_run_samples(
        city_key,
        target_date,
        path=path,
        models=models,
        max_lead_days=max_lead_days,
    )
    result = bucket_probabilities(samples, buckets)
    result["method"] = "openmeteo-previous-runs-v1"
    result["sample_count"] = len(samples)
    result["diagnostic_only"] = True
    return result


def build_model_reprice_events(
    *,
    cities: list[str] | None = None,
    days: int = 2,
    dry_run: bool = False,
    path: Path | None = None,
) -> dict[str, Any]:
    init_v3_db(path)
    city_filter = {str(city).strip().lower() for city in (cities or []) if str(city).strip()}
    triggered_at = utc_now()
    with connect(path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT pm.city, pm.target_date, pm.market_id, pm.bucket_label,
                       pm.bucket_lower_c, pm.bucket_upper_c, po.best_bid, po.best_ask
                FROM polymarket_markets pm
                LEFT JOIN (
                    SELECT market_id, MAX(ts) AS latest_ts
                    FROM polymarket_orderbook
                    GROUP BY market_id
                ) latest ON latest.market_id = pm.market_id
                LEFT JOIN polymarket_orderbook po
                  ON po.market_id = pm.market_id AND po.ts = latest.latest_ts
                ORDER BY pm.city, pm.target_date, pm.bucket_lower_c
                """
            ).fetchall()
        ]
        previous_rows = {
            (row["city_key"], row["target_date"], row["market_id"]): row
            for row in conn.execute(
                """
                SELECT city_key, target_date, market_id, model_prob
                FROM model_reprice_events
                WHERE model_source = ?
                ORDER BY triggered_at DESC, id DESC
                """,
                (ALGO,),
            ).fetchall()
        }
    today = datetime.now(timezone.utc).date()
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        city = str(row.get("city") or "").lower()
        if city_filter and city not in city_filter:
            continue
        try:
            target = datetime.fromisoformat(str(row.get("target_date"))).date()
        except Exception:
            continue
        if target < today or (target - today).days >= max(1, int(days or 2)):
            continue
        grouped.setdefault((city, str(row.get("target_date"))), []).append(row)

    events: list[dict[str, Any]] = []
    for (city, target_date), bucket_rows in grouped.items():
        prediction = build_ensemble_prediction(city, target_date, issued_at=triggered_at, path=path)
        if not prediction.get("ok"):
            continue
        distribution = distribution_for_prediction(prediction, [
            {
                "bucket_key": str(row.get("market_id") or row.get("bucket_label") or ""),
                "bucket_label": row.get("bucket_label") or "",
                "bucket_low": row.get("bucket_lower_c"),
                "bucket_high": row.get("bucket_upper_c"),
                "unit": "C",
                "best_bid": row.get("best_bid"),
                "best_ask": row.get("best_ask"),
            }
            for row in bucket_rows
        ])
        if not distribution:
            continue
        by_key = {str(item.get("bucket_key") or ""): item for item in distribution.get("items") or []}
        for row in bucket_rows:
            market_id = str(row.get("market_id") or "")
            item = by_key.get(market_id)
            if not item:
                continue
            bid = _first_number(row.get("best_bid"))
            ask = _first_number(row.get("best_ask"))
            market_mid = (bid + ask) / 2 if bid is not None and ask is not None else None
            model_prob = float(item.get("probability") or 0.0)
            previous = previous_rows.get((city, target_date, market_id))
            previous_prob = _first_number(previous["model_prob"]) if previous else None
            delta = None if previous_prob is None else model_prob - previous_prob
            edge = None if market_mid is None else model_prob - market_mid
            event = {
                "event_key": f"{ALGO}:{city}:{target_date}:{market_id}:{triggered_at[:16]}",
                "city_key": city,
                "target_date": target_date,
                "market_id": market_id,
                "bucket_key": market_id,
                "triggered_at": triggered_at,
                "model_source": ALGO,
                "previous_model_prob": previous_prob,
                "model_prob": model_prob,
                "delta_prob": delta,
                "market_mid": market_mid,
                "edge": edge,
                "alpha_candidate": delta is not None and edge is not None and abs(delta) > 0.05 and abs(edge) > 0.08,
            }
            if not dry_run:
                event["id"] = upsert_model_reprice_event(event, path=path)
            events.append(event)
    return {"ok": True, "dry_run": dry_run, "stored": 0 if dry_run else len(events), "events": events}


def load_bias_table(path: Path | None = None) -> list[dict[str, Any]]:
    bias_path = path or BIAS_TABLE_PATH
    try:
        value = json.loads(Path(bias_path).read_text(encoding="utf-8"))
        if isinstance(value, dict):
            value = value.get("rows") or value.get("biases") or []
        return value if isinstance(value, list) else []
    except Exception:
        return []


def region_model_weights(profile: CitySettlementProfile) -> dict[str, float]:
    if _deb_algo() == POLYWX_ALIGNED_ALGO:
        return POLYWX_ALIGNED_MODEL_WEIGHTS
    if profile.region == "us":
        return REGION_MODEL_WEIGHTS["us"]
    if profile.city in {"shanghai", "beijing", "wuhan", "qingdao", "shenzhen", "hong-kong"}:
        return REGION_MODEL_WEIGHTS["china_hk"]
    if profile.city in {"tokyo", "seoul", "taipei"}:
        return REGION_MODEL_WEIGHTS["japan_korea_taipei"]
    if profile.city == "singapore":
        return REGION_MODEL_WEIGHTS["singapore"]
    return REGION_MODEL_WEIGHTS["global"]


def model_family(name: str) -> str:
    raw = str(name or "").lower()
    if "weathercom" in raw or "weather.com" in raw:
        return "weathercom_v3"
    if "hrrr" in raw:
        return "hrrr"
    if "nbm" in raw:
        return "nbm"
    if "ecmwf" in raw or "ifs" in raw or "aifs" in raw:
        return "ecmwf"
    if "gfs" in raw:
        return "gfs"
    if "grapes" in raw or "cma" in raw:
        return "cma"
    if "jma" in raw:
        return "jma"
    if "icon" in raw:
        return "icon"
    if "gem" in raw:
        return "gem"
    return raw.replace("openmeteo_ensemble_", "").replace("openmeteo_", "")


def convert_temperature_delta(value: float, source_unit: str, target_unit: str) -> float:
    source = str(source_unit or "C").upper()
    target = str(target_unit or "C").upper()
    if source == target:
        return float(value)
    if source == "C" and target == "F":
        return float(value) * 9.0 / 5.0
    if source == "F" and target == "C":
        return float(value) * 5.0 / 9.0
    return float(value)


def convert_temperature(value: float, source_unit: str, target_unit: str) -> float:
    source = str(source_unit or "C").upper()
    target = str(target_unit or "C").upper()
    numeric = float(value)
    if source == target:
        return numeric
    if source == "F" and target == "C":
        return (numeric - 32.0) * 5.0 / 9.0
    if source == "C" and target == "F":
        return numeric * 9.0 / 5.0 + 32.0
    return numeric


def _latest_forecast_members(
    profile: CitySettlementProfile,
    target_date: str,
    path: Path | None,
    *,
    as_of: str | None = None,
    selection_mode: str = "latest_run",
) -> list[dict[str, Any]]:
    with connect(path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT fr.id AS run_id, fr.city, fr.target_date, fr.station_id,
                       fr.source, fr.provider, fr.model, fr.model_version,
                       fr.run_at, fr.retrieved_at, fr.available_at,
                       fr.availability_basis, fr.valid_at, fr.horizon, fr.lead_hours,
                       fr.timezone, fr.training_eligible, fr.parse_status,
                       fr.ineligibility_reason,
                       fr.parser_version, fr.snapshot_key, fr.raw_response_hash,
                       fr.unit, fr.mean_high,
                       fr.std_high, fr.member_count, fr.source_url, fm.member_id, fm.high_temp,
                       fm.hourly_json
                FROM forecast_runs fr
                JOIN forecast_members fm ON fm.run_id = fr.id
                WHERE fr.city = ?
                  AND fr.target_date = ?
                  AND UPPER(COALESCE(fr.station_id, '')) = ?
                  AND COALESCE(fr.parse_status, 'parsed') IN ('parsed', 'partial')
                  AND (fr.source LIKE 'openmeteo_%' OR fr.source = 'weathercom_v3_forecast')
                ORDER BY COALESCE(fr.available_at, fr.retrieved_at) DESC, fr.id DESC, fm.member_id
                """,
                (profile.city, target_date, profile.station_id.upper()),
            ).fetchall()
        ]
    eligible_rows = []
    for row in rows:
        training_eligible = bool(row.get("training_eligible"))
        ineligibility_reason = str(row.get("ineligibility_reason") or "")
        if selection_mode == "stitch_local_day":
            # A D+0 snapshot can have a negative aggregate lead once its
            # predicted daily-high hour has passed. Keep the run for point-level
            # stitching, where only hours still in the future at retrieval time
            # are eligible. Other ineligibility reasons remain fail-closed.
            if not training_eligible and ineligibility_reason not in {
                "forecast_lead_negative",
                "incomplete_ensemble_local_day",
            }:
                continue
        assessment_row = row
        if (
            selection_mode == "stitch_local_day"
            and str(row.get("parse_status") or "").lower() == "partial"
            and ineligibility_reason in {
                "forecast_lead_negative",
                "incomplete_ensemble_local_day",
            }
        ):
            # D+0 reconstruction evaluates each member-hour against its own
            # availability time, so an incomplete aggregate day can be safely
            # consumed without treating its partial max as a full-day max.
            assessment_row = {**row, "parse_status": "parsed"}
        assessment = assess_forecast_run(
            assessment_row,
            as_of=as_of,
            target_date=target_date,
            timezone_name=profile.timezone,
            require_training=selection_mode != "stitch_local_day",
        )
        if assessment["ok"]:
            row["available_at"] = assessment["available_at"]
            row["availability_basis"] = assessment["availability_basis"]
            row["assessed_horizon"] = assessment["horizon_bucket"]
            eligible_rows.append(row)
    rows = eligible_rows
    rows = [
        row
        for row in rows
        if forecast_source_matches_profile_location(row.get("source_url"), profile)
    ]
    if selection_mode == "stitch_local_day":
        return rows
    latest_by_source: dict[str, int] = {}
    for row in rows:
        source = str(row.get("source") or "")
        latest_by_source.setdefault(source, int(row.get("run_id") or 0))
    return [
        row
        for row in rows
        if latest_by_source.get(str(row.get("source") or "")) == int(row.get("run_id") or 0)
    ]


def _components_from_rows(
    profile: CitySettlementProfile,
    rows: list[dict[str, Any]],
    bias_table: list[dict[str, Any]],
    path: Path | None,
    *,
    target_date: str,
    selection_mode: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        family = model_family(row.get("source") or row.get("model") or "")
        grouped.setdefault(family, []).append(row)
    weights = region_model_weights(profile)
    components: list[dict[str, Any]] = []
    for family, group in grouped.items():
        if family not in weights:
            continue
        best_source = _best_source_group(group)
        if not best_source:
            continue
        first = best_source[0]
        unit = str(first.get("unit") or profile.unit or "C").upper()
        lead_bucket = str(
            first.get("assessed_horizon")
            or first.get("horizon")
            or "unknown"
        ).strip().lower()
        bias_c, sample_count = _bias_for(
            bias_table,
            profile.station_id,
            family,
            profile=profile,
            lead_bucket=lead_bucket,
        )
        bias_unit = convert_temperature_delta(bias_c, "C", unit)
        archive = (
            _archived_daily_high(best_source, profile, target_date)
            if selection_mode == "stitch_local_day"
            else {}
        )
        if archive:
            highs_c = [float(value) for value in archive["member_highs_c"]]
            adjusted_c = [float(value) - float(bias_c) for value in archive["member_highs_c"]]
        else:
            highs_unit = [_first_number(row.get("high_temp")) for row in best_source]
            highs_c = [
                convert_temperature(float(value), unit, "C")
                for value in highs_unit
                if value is not None and math.isfinite(float(value))
            ]
            adjusted_c = [
                convert_temperature(float(value) - bias_unit, unit, "C")
                for value in highs_unit
                if value is not None and math.isfinite(float(value))
            ]
        if not adjusted_c:
            continue
        peak_hour = {
            "peak_hour": archive.get("peak_hour"),
            "peak_temp_c": (
                float(archive["peak_temp_c"]) - float(bias_c)
                if archive.get("peak_temp_c") is not None
                else None
            ),
        } if archive else _peak_hour_from_members(best_source, unit, bias_unit, profile.timezone)
        effective_available_at = (
            archive.get("latest_contributing_available_at")
            or first.get("available_at")
            or first.get("retrieved_at")
            or ""
        )
        components.append({
            "source": str(first.get("source") or ""),
            "family": family,
            "role": _component_role(family),
            "run_id": int(first.get("run_id") or 0),
            "model": str(first.get("model") or ""),
            "weight_raw": float(weights.get(family) or 0.0),
            "weight": 0.0,
            "weight_prior": float(weights.get(family) or 0.0),
            "weight_after_mae": 0.0,
            "member_count": len(adjusted_c),
            "raw_daily_highs_c": [round(value, 4) for value in highs_c],
            "adjusted_daily_highs_c": [round(value, 4) for value in adjusted_c],
            "model_daily_high_c": round(sum(adjusted_c) / len(adjusted_c), 4),
            "bias_correction_c": round(bias_c, 4),
            "bias_sample_count": int(sample_count),
            "bias_lead_bucket": lead_bucket,
            "bias_method": BIAS_RUNTIME_METHOD,
            "bias_status": (
                "shrinkage_active"
                if sample_count >= BIAS_PAPER_MIN_SAMPLE_COUNT
                else ("collecting" if sample_count > 0 else "missing")
            ),
            "bias_shrinkage_factor": round(
                sample_count / (sample_count + BIAS_SHRINKAGE_PRIOR_SAMPLES),
                4,
            ) if sample_count >= BIAS_PAPER_MIN_SAMPLE_COUNT else 0.0,
            "bias_applied_before_probability": True,
            "mae_7d": _mae_for(
                bias_table,
                profile.station_id,
                family,
                profile=profile,
                lead_bucket=lead_bucket,
            ),
            "truth_basis": _truth_basis(profile, target_date, path),
            "retrieved_at": str(first.get("retrieved_at") or ""),
            "available_at": str(first.get("available_at") or ""),
            "effective_available_at": str(effective_available_at),
            "availability_basis": str(first.get("availability_basis") or ""),
            "peak_hour": peak_hour.get("peak_hour") or "",
            "peak_temp_c": peak_hour.get("peak_temp_c"),
            "peak_member_id": str(archive.get("peak_member_id") or first.get("member_id") or ""),
            "peak_run_id": int(archive.get("peak_run_id") or first.get("run_id") or 0),
            "peak_available_at": str(archive.get("peak_available_at") or effective_available_at),
            "source_run_ids": archive.get("source_run_ids") or [int(first.get("run_id") or 0)],
            "source_snapshot_keys": archive.get("source_snapshot_keys") or (
                [str(first.get("snapshot_key"))] if first.get("snapshot_key") else []
            ),
            "snapshot_count": int(archive.get("snapshot_count") or 1),
            "candidate_snapshot_count": int(archive.get("candidate_snapshot_count") or 1),
            "archive_hour_count": int(archive.get("hour_count") or 0),
            "archive_member_hour_count": int(archive.get("member_hour_count") or 0),
            "archive_member_ids": archive.get("member_ids") or [],
            "archive_expected_hour_count": int(archive.get("expected_hour_count") or 0),
            "archive_coverage": archive.get("coverage"),
            "snapshot_selection_hash": str(archive.get("selection_hash") or first.get("snapshot_key") or ""),
            "archive_oldest_point_available_at": str(archive.get("oldest_contributing_available_at") or ""),
            "archive_latest_snapshot_available_at": str(archive.get("latest_contributing_available_at") or ""),
            "archive_run_hour_counts": archive.get("run_hour_counts") or {},
            "snapshot_selection_mode": selection_mode,
            "snapshot_selection_version": FORECAST_SNAPSHOT_SELECTION_VERSION,
            "daily_high_basis": archive.get("basis") or "latest_forecast_run",
            "point_availability_contract": (
                "valid_at_gte_snapshot_available_at"
                if selection_mode == "stitch_local_day"
                else "run_level_training_eligible"
            ),
        })
    if _deb_algo() == POLYWX_ALIGNED_ALGO:
        _apply_mae_adjusted_weights(components)
        return components
    raw_sum = sum(component["weight_raw"] for component in components) or 1.0
    for component in components:
        component["weight"] = component["weight_raw"] / raw_sum
        component["weight_after_mae"] = component["weight"]
    return components


def _best_source_group(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_source: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_source.setdefault(str(row.get("source") or ""), []).append(row)

    def source_stats(group: list[dict[str, Any]]) -> tuple[datetime, int, int]:
        latest = max(
            group,
            key=lambda row: (
                parse_utc(row.get("available_at") or row.get("retrieved_at"))
                or datetime.min.replace(tzinfo=timezone.utc),
                int(row.get("run_id") or 0),
            ),
        )
        available_at = (
            parse_utc(latest.get("available_at") or latest.get("retrieved_at"))
            or datetime.min.replace(tzinfo=timezone.utc)
        )
        latest_run_id = int(latest.get("run_id") or 0)
        latest_member_count = sum(
            1 for row in group if int(row.get("run_id") or 0) == latest_run_id
        )
        return available_at, latest_member_count, latest_run_id

    ranked = [(group, source_stats(group)) for group in by_source.values() if group]
    if not ranked:
        return []
    newest = max(stats[0] for _group, stats in ranked)
    freshness_floor = newest - timedelta(hours=DEFAULT_COMPONENT_MAX_SKEW_HOURS)
    fresh_enough = [item for item in ranked if item[1][0] >= freshness_floor]
    # Prefer member-rich ensembles only when they are in the same freshness
    # cohort. A stale ensemble must not shadow a current deterministic run.
    return max(
        fresh_enough,
        key=lambda item: (item[1][1], item[1][0], item[1][2]),
    )[0]


def _archived_daily_high(
    rows: list[dict[str, Any]],
    profile: CitySettlementProfile,
    target_date: str,
) -> dict[str, Any]:
    """Rebuild an auditable local-day curve per member without future leakage."""

    latest_by_member_hour: dict[tuple[str, str], dict[str, Any]] = {}
    candidate_run_ids: set[int] = set()
    zone = _zone(profile.timezone)
    for row in rows:
        row_unit = str(row.get("unit") or profile.unit or "C").upper()
        run_id = int(row.get("run_id") or 0)
        if run_id > 0:
            candidate_run_ids.add(run_id)
        member_id = str(row.get("member_id") or "deterministic")
        available_at = str(row.get("available_at") or row.get("retrieved_at") or "")
        available = _parse_time(available_at)
        if available is None:
            continue
        hourly = _loads(row.get("hourly_json"), [])
        if not isinstance(hourly, list):
            continue
        for point in hourly:
            if not isinstance(point, dict):
                continue
            valid_at = _parse_time(str(point.get("valid_at") or point.get("time") or point.get("timestamp") or ""))
            if valid_at is None:
                continue
            if valid_at < available:
                # A snapshot retrieved after an hour occurred is a revision, not
                # a forecast that was knowable at that hour.
                continue
            local_time = valid_at.astimezone(zone)
            if local_time.date().isoformat() != target_date:
                continue
            temperature = _first_number(point.get("temperature_2m"), point.get("temperature"), point.get("temp"))
            if temperature is None or not math.isfinite(float(temperature)):
                continue
            temperature_c = convert_temperature(float(temperature), row_unit, "C")
            valid_hour_utc = valid_at.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()
            key = (member_id, valid_hour_utc)
            candidate = {
                "temperature_c": temperature_c,
                "member_id": member_id,
                "valid_hour_utc": valid_hour_utc,
                "local_hour": local_time.strftime("%H:%M"),
                "run_id": run_id,
                "snapshot_key": str(row.get("snapshot_key") or ""),
                "available_at": available.isoformat(),
            }
            current = latest_by_member_hour.get(key)
            if current is None or (available, run_id) > (
                _parse_time(str(current.get("available_at") or "")) or datetime.min.replace(tzinfo=timezone.utc),
                int(current.get("run_id") or 0),
            ):
                latest_by_member_hour[key] = candidate

    if not latest_by_member_hour:
        return {}
    by_member: dict[str, list[dict[str, Any]]] = {}
    for point in latest_by_member_hour.values():
        by_member.setdefault(str(point["member_id"]), []).append(point)
    member_high_rows = [
        max(points, key=lambda point: (float(point["temperature_c"]), str(point["valid_hour_utc"])))
        for _member_id, points in sorted(by_member.items())
        if points
    ]
    peak = max(
        latest_by_member_hour.values(),
        key=lambda point: (float(point["temperature_c"]), str(point["valid_hour_utc"])),
    )
    contributing_run_ids = {
        int(point["run_id"])
        for point in latest_by_member_hour.values()
        if int(point.get("run_id") or 0) > 0
    }
    snapshot_keys = sorted({
        str(point.get("snapshot_key") or "")
        for point in latest_by_member_hour.values()
        if str(point.get("snapshot_key") or "")
    })
    available_times = [
        _parse_time(str(point.get("available_at") or ""))
        for point in latest_by_member_hour.values()
    ]
    available_times = [value for value in available_times if value is not None]
    run_hour_counts: dict[str, int] = {}
    for point in latest_by_member_hour.values():
        key = str(int(point.get("run_id") or 0))
        run_hour_counts[key] = run_hour_counts.get(key, 0) + 1
    distinct_hours = {str(point["valid_hour_utc"]) for point in latest_by_member_hour.values()}
    expected_hours = _expected_local_day_hours(target_date, zone)
    selection_rows = sorted(
        (
            str(point["member_id"]),
            str(point["valid_hour_utc"]),
            int(point.get("run_id") or 0),
            round(float(point["temperature_c"]), 6),
        )
        for point in latest_by_member_hour.values()
    )
    selection_hash = hashlib.sha256(
        json.dumps(selection_rows, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "member_highs_c": [float(point["temperature_c"]) for point in member_high_rows],
        "member_ids": [str(point["member_id"]) for point in member_high_rows],
        "peak_hour": str(peak["local_hour"]),
        "peak_temp_c": float(peak["temperature_c"]),
        "peak_member_id": str(peak["member_id"]),
        "peak_run_id": int(peak.get("run_id") or 0),
        "peak_available_at": str(peak.get("available_at") or ""),
        "hour_count": len(distinct_hours),
        "member_hour_count": len(latest_by_member_hour),
        "expected_hour_count": expected_hours,
        "coverage": round(len(distinct_hours) / expected_hours, 6) if expected_hours else None,
        "snapshot_count": len(contributing_run_ids),
        "candidate_snapshot_count": len(candidate_run_ids),
        "source_run_ids": sorted(contributing_run_ids),
        "source_snapshot_keys": snapshot_keys,
        "selection_hash": selection_hash,
        "oldest_contributing_available_at": min(available_times).isoformat() if available_times else "",
        "latest_contributing_available_at": max(available_times).isoformat() if available_times else "",
        "run_hour_counts": run_hour_counts,
        "basis": "latest_snapshot_per_member_valid_hour_as_of",
    }


def _expected_local_day_hours(target_date: str, zone: ZoneInfo) -> int:
    try:
        local_date = datetime.strptime(target_date, "%Y-%m-%d").date()
    except ValueError:
        return 0
    start = datetime.combine(local_date, time.min, tzinfo=zone)
    end = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=zone)
    return int(round((end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds() / 3600.0))


def _weighted_member_highs(profile: CitySettlementProfile, components: list[dict[str, Any]]) -> list[tuple[float, float, dict[str, Any]]]:
    weighted: list[tuple[float, float, dict[str, Any]]] = []
    for component in components:
        highs = component.get("adjusted_daily_highs_c") or []
        count = len(highs) or 1
        member_weight = float(component.get("weight") or 0.0) / count
        for index, value in enumerate(highs):
            weighted.append((float(value), member_weight, {
                "source": component.get("source"),
                "family": component.get("family"),
                "member": f"{component.get('source')}:{index}",
            }))
    return weighted


def _weighted_peak_hour(components: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [
        component for component in components
        if component.get("peak_hour") and component.get("peak_temp_c") is not None
    ]
    if not candidates:
        return {}
    best = max(candidates, key=lambda row: (float(row.get("peak_temp_c") or -999), str(row.get("peak_hour") or "")))
    return {"peak_hour": best.get("peak_hour"), "peak_temp_c": best.get("peak_temp_c")}


def _peak_hour_from_members(rows: list[dict[str, Any]], unit: str, bias_unit: float, tz: str) -> dict[str, Any]:
    zone = _zone(tz)
    best: dict[str, Any] = {}
    for row in rows:
        hourly = _loads(row.get("hourly_json"), [])
        for point in hourly:
            if not isinstance(point, dict):
                continue
            temp = _first_number(point.get("temperature_2m"), point.get("temp"))
            valid = _parse_time(str(point.get("valid_at") or ""))
            if temp is None or valid is None:
                continue
            temp_c = convert_temperature(float(temp) - bias_unit, unit, "C")
            local_hour = valid.astimezone(zone).strftime("%H:00")
            if not best or temp_c > float(best.get("peak_temp_c") or -999) or (
                abs(temp_c - float(best.get("peak_temp_c") or -999)) < 1e-9 and local_hour > str(best.get("peak_hour") or "")
            ):
                best = {"peak_hour": local_hour, "peak_temp_c": temp_c}
    return best


def _bias_for(
    bias_table: list[dict[str, Any]],
    station_id: str,
    family: str,
    *,
    profile: CitySettlementProfile | None = None,
    lead_bucket: str | None = None,
) -> tuple[float, int]:
    station = str(station_id or "").upper()
    fam = str(family or "").lower()
    for row in bias_table:
        if str(row.get("icao") or row.get("station_id") or "").upper() == station and str(row.get("model") or "").lower() == fam:
            if profile is not None and int(row.get("location_version") or 1) != int(profile.location_version):
                continue
            calibration = _lead_calibration(row, lead_bucket)
            sample_count = int(calibration.get("sample_count") or 0)
            if sample_count < BIAS_PAPER_MIN_SAMPLE_COUNT:
                return 0.0, sample_count
            raw_bias = float(calibration.get("additive_bias_c") or 0.0)
            shrinkage = sample_count / (sample_count + BIAS_SHRINKAGE_PRIOR_SAMPLES)
            effective_bias = max(
                -BIAS_MAX_ABS_C,
                min(BIAS_MAX_ABS_C, raw_bias * shrinkage),
            )
            return round(effective_bias, 4), sample_count
    return 0.0, 0


def _deb_algo() -> str:
    mode = env_value("DEB_WEIGHT_MODE", "polywx_aligned").strip().lower()
    return POLYWX_ALIGNED_ALGO if mode in {"polywx", "polywx_aligned", "polywx_aligned_deb_v1"} else ALGO


def _component_role(family: str) -> str:
    if family == "weathercom_v3":
        return "weather.com/WU-style v3 forecast"
    if family in {"gfs", "ecmwf", "icon", "gem", "jma", "cma", "hrrr", "nbm"}:
        return "NWP forecast"
    return "forecast"


def _mae_for(
    bias_table: list[dict[str, Any]],
    station_id: str,
    family: str,
    *,
    profile: CitySettlementProfile | None = None,
    lead_bucket: str | None = None,
) -> float | None:
    station = str(station_id or "").upper()
    fam = str(family or "").lower()
    for row in bias_table:
        if str(row.get("icao") or row.get("station_id") or "").upper() != station:
            continue
        if str(row.get("model") or "").lower() != fam:
            continue
        if profile is not None and int(row.get("location_version") or 1) != int(profile.location_version):
            continue
        calibration = _lead_calibration(row, lead_bucket)
        # Paper weighting learns from the first leakage-free forecast/truth
        # pair. Sample thresholds describe maturity and live eligibility; they
        # must not make a real sparse error metric disappear.
        if int(calibration.get("sample_count") or 0) < BIAS_PAPER_MIN_SAMPLE_COUNT:
            return None
        for key in (
            "walk_forward_mae_7d_c",
            "walk_forward_mae_c",
            "mae_7d_c",
            "mae_c",
            "mae",
            "rmse_c",
            "rmse",
        ):
            value = _first_number(calibration.get(key))
            if value is not None:
                return round(float(value), 4)
        bias = _first_number(calibration.get("additive_bias_c"))
        if bias is not None:
            return round(abs(float(bias)), 4)
    return None


def _lead_calibration(row: dict[str, Any], lead_bucket: str | None) -> dict[str, Any]:
    bucket = str(lead_bucket or "").strip().lower()
    calibrations = row.get("lead_calibrations")
    if bucket and isinstance(calibrations, dict):
        selected = calibrations.get(bucket)
        if isinstance(selected, dict):
            return selected
    return row


def _truth_basis(profile: CitySettlementProfile, target_date: str, path: Path | None) -> str:
    with connect(path) as conn:
        wu = conn.execute(
            "SELECT COUNT(*) FROM truth_wunderground_daily WHERE UPPER(icao) = ? AND date_local <= ? AND high_c IS NOT NULL",
            (str(profile.station_id or "").upper(), target_date),
        ).fetchone()[0]
        if int(wu or 0) > 0:
            return "wunderground_daily"
        if profile.city == "hong-kong":
            hko = conn.execute(
                "SELECT COUNT(*) FROM truth_hko_daily WHERE date_local <= ? AND high_c IS NOT NULL",
                (target_date,),
            ).fetchone()[0]
            if int(hko or 0) > 0:
                return "hong_kong_observatory_daily_extract"
        iem = conn.execute(
            "SELECT COUNT(*) FROM truth_iem_daily WHERE UPPER(icao) = ? AND date_local <= ? AND high_c IS NOT NULL",
            (str(profile.station_id or "").upper(), target_date),
        ).fetchone()[0]
        if int(iem or 0) > 0:
            return "iem_asos_approximation"
    return "none"


def _apply_mae_adjusted_weights(components: list[dict[str, Any]]) -> None:
    participants: list[dict[str, Any]] = []
    calibrated: list[dict[str, Any]] = []
    for component in components:
        prior = max(0.0, float(component.get("weight_prior") or component.get("weight_raw") or 0.0))
        mae = _first_number(component.get("mae_7d"))
        sample_count = int(component.get("bias_sample_count") or 0)
        has_calibration = bool(
            mae is not None
            and math.isfinite(float(mae))
            and sample_count > 0
        )
        component["weight_method"] = DYNAMIC_WEIGHT_METHOD
        component["calibration_progress"] = round(min(1.0, sample_count / BIAS_MIN_SAMPLE_COUNT), 4)
        component["weight_eligible"] = prior > 0.0
        component["performance_eligible"] = has_calibration
        if prior <= 0.0:
            component["mae_imputed"] = not has_calibration
            component["effective_mae_c"] = (
                round(max(float(mae), DYNAMIC_WEIGHT_ERROR_FLOOR_C), 4)
                if has_calibration
                else UNCALIBRATED_SIGMA_C
            )
            component["weight_status"] = "excluded"
            component["weight_exclusion_reason"] = "model_prior_not_configured"
            component["weight_caution"] = ""
            component["weight_raw"] = 0.0
            component["weight"] = 0.0
            component["weight_after_mae"] = 0.0
            continue

        component["mae_imputed"] = not has_calibration
        component["weight_exclusion_reason"] = ""
        if not has_calibration:
            component["effective_mae_c"] = UNCALIBRATED_SIGMA_C
            component["weight_status"] = "prior_only"
            component["weight_caution"] = "calibration_mae_missing_using_prior"
        else:
            component["effective_mae_c"] = round(max(float(mae), DYNAMIC_WEIGHT_ERROR_FLOOR_C), 4)
            if sample_count >= BIAS_MIN_SAMPLE_COUNT:
                component["weight_status"] = "active"
                component["weight_caution"] = ""
            elif sample_count >= DYNAMIC_WEIGHT_MIN_SAMPLES:
                component["weight_status"] = "provisional"
                component["weight_caution"] = "calibration_sample_is_provisional"
            else:
                component["weight_status"] = "collecting"
                component["weight_caution"] = "calibration_sample_is_sparse"
            calibrated.append(component)
        participants.append(component)

    if not participants:
        return

    v3 = next(
        (component for component in participants if component.get("family") == "weathercom_v3"),
        None,
    )
    if v3 is not None and int(v3.get("bias_sample_count") or 0) < BIAS_MIN_SAMPLE_COUNT:
        for component in participants:
            selected = component is v3
            component["weight_raw"] = 1.0 if selected else 0.0
            component["weight"] = 1.0 if selected else 0.0
            component["weight_after_mae"] = component["weight"]
            component["weight_status"] = "cold_start_v3_only" if selected else "diagnostic_only"
            component["weight_caution"] = (
                "station_lead_calibration_collecting" if selected else "excluded_during_v3_cold_start"
            )
        return

    prior_total = sum(float(component.get("weight_prior") or 0.0) for component in participants) or 1.0
    calibrated_prior_mass = sum(
        float(component.get("weight_prior") or 0.0) / prior_total
        for component in calibrated
    )
    accuracy_scores = [1.0 / float(component["effective_mae_c"]) for component in calibrated]
    accuracy_total = sum(accuracy_scores) or 1.0
    accuracy_by_id = {
        id(component): calibrated_prior_mass * accuracy_score / accuracy_total
        for component, accuracy_score in zip(calibrated, accuracy_scores)
    }
    combined_scores: list[float] = []
    for component in participants:
        prior_share = max(0.0, float(component.get("weight_prior") or 0.0)) / prior_total
        accuracy_share = accuracy_by_id.get(id(component), prior_share)
        sample_count = int(component.get("bias_sample_count") or 0)
        sample_maturity = (
            min(1.0, sample_count / DYNAMIC_WEIGHT_FULL_SAMPLES)
            if bool(component.get("performance_eligible"))
            else 0.0
        )
        performance_blend = DYNAMIC_WEIGHT_PERFORMANCE_BLEND_MAX * sample_maturity
        score = ((1.0 - performance_blend) * prior_share) + (performance_blend * accuracy_share)
        component["dynamic_prior_share"] = round(prior_share, 8)
        component["dynamic_accuracy_share"] = round(accuracy_share, 8)
        component["sample_maturity"] = round(sample_maturity, 4)
        component["performance_blend"] = round(performance_blend, 4)
        combined_scores.append(score)

    normalized_priors = [
        max(0.0, float(component.get("weight_prior") or 0.0)) / prior_total
        for component in participants
    ]
    weights = _normalize_capped_weights(
        combined_scores,
        max(DYNAMIC_WEIGHT_MAX_SHARE, max(normalized_priors, default=0.0)),
    )
    for component, score, weight in zip(participants, combined_scores, weights):
        component["weight_raw"] = score
        component["weight"] = weight
        component["weight_after_mae"] = weight


def _normalize_capped_weights(scores: list[float], max_share: float) -> list[float]:
    if not scores:
        return []
    cap = max(float(max_share), 1.0 / len(scores))
    weights = [0.0 for _score in scores]
    remaining = set(range(len(scores)))
    remaining_mass = 1.0
    while remaining:
        score_total = sum(max(0.0, scores[index]) for index in remaining)
        allocations = {
            index: remaining_mass * (
                max(0.0, scores[index]) / score_total if score_total > 0 else 1.0 / len(remaining)
            )
            for index in remaining
        }
        capped = [index for index, value in allocations.items() if value > cap + 1e-12]
        if not capped:
            for index, value in allocations.items():
                weights[index] = value
            break
        for index in capped:
            weights[index] = cap
            remaining.remove(index)
            remaining_mass -= cap
        if remaining_mass <= 1e-12:
            break
    total = sum(weights) or 1.0
    return [weight / total for weight in weights]


def _normalize_component_weights(components: list[dict[str, Any]], algo: str) -> None:
    if algo == POLYWX_ALIGNED_ALGO:
        _apply_mae_adjusted_weights(components)
        return
    total = sum(max(0.0, float(component.get("weight_prior") or component.get("weight_raw") or 0.0)) for component in components) or 1.0
    for component in components:
        weight = max(0.0, float(component.get("weight_prior") or component.get("weight_raw") or 0.0)) / total
        component["weight"] = weight
        component["weight_after_mae"] = weight
        component["mae_imputed"] = _first_number(component.get("mae_7d")) is None
        component["effective_mae_c"] = _first_number(component.get("mae_7d")) or UNCALIBRATED_SIGMA_C


def _source_warnings(components: list[dict[str, Any]], algo: str) -> list[str]:
    warnings: list[str] = []
    if algo == POLYWX_ALIGNED_ALGO and not any(component.get("family") == "weathercom_v3" for component in components):
        warnings.append("missing_weathercom_v3")
    if not any(component.get("truth_basis") in {"wunderground_daily", "hong_kong_observatory_daily_extract"} for component in components):
        warnings.append("truth_basis_uses_approximation_or_none")
    if algo == POLYWX_ALIGNED_ALGO and any(component.get("weight_status") == "prior_only" for component in components):
        warnings.append("dynamic_weight_uses_prior_only_components")
    if algo == POLYWX_ALIGNED_ALGO and any(component.get("weight_status") == "collecting" for component in components):
        warnings.append("dynamic_weight_uses_sparse_calibration_components")
    if algo == POLYWX_ALIGNED_ALGO and any(component.get("weight_status") == "provisional" for component in components):
        warnings.append("dynamic_weight_uses_provisional_calibration_components")
    return warnings


def _weighted_samples(samples: list[Any]) -> list[tuple[float, float]]:
    weighted: list[tuple[float, float]] = []
    for sample in samples:
        if isinstance(sample, dict):
            value = _first_number(sample.get("value"), sample.get("daily_max_c"), sample.get("daily_max"))
            weight = _first_number(sample.get("weight")) or 1.0
        else:
            value = _first_number(sample)
            weight = 1.0
        if value is None or not math.isfinite(float(value)):
            continue
        weighted.append((float(value), max(0.0, float(weight))))
    return weighted


def _sample_in_bucket(value: float, low: float | None, high: float | None) -> bool:
    if low is None and high is None:
        return False
    if low is None:
        return value < float(high)
    if high is None:
        return value >= float(low)
    return value >= float(low) and value < float(high)


def _weighted_mean(values: list[float], weights: list[float]) -> float:
    total = sum(weights) or 1.0
    return sum(value * weight for value, weight in zip(values, weights)) / total


def _weighted_std(values: list[float], weights: list[float]) -> float:
    if not values:
        return 0.0
    mean = _weighted_mean(values, weights)
    total = sum(weights) or 1.0
    variance = sum(weight * (value - mean) ** 2 for value, weight in zip(values, weights)) / total
    return math.sqrt(max(0.0, variance))


def _weighted_spread(values: list[float]) -> float:
    return max(values) - min(values) if values else 0.0


def _market_probability(bucket: dict[str, Any]) -> float | None:
    bid = _first_number(bucket.get("best_bid"))
    ask = _first_number(bucket.get("best_ask"))
    price = _first_number(bucket.get("price"))
    if ask is not None:
        return ask
    if price is not None:
        return price
    if bid is not None:
        return bid
    return None


def _parse_time(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        value = raw.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _loads(raw: Any, default: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if not raw:
        return default
    try:
        return json.loads(str(raw))
    except Exception:
        return default


def _first_number(*values: Any) -> float | None:
    for value in values:
        try:
            if value is None or value == "":
                continue
            parsed = float(value)
            if math.isfinite(parsed):
                return parsed
        except Exception:
            continue
    return None
