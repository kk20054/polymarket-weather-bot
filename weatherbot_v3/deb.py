from __future__ import annotations

import json
import math
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python always provides zoneinfo in supported runtimes.
    ZoneInfo = None  # type: ignore

from .db import connect, init_v3_db, list_daily_max_predictions, list_market_buckets, upsert_daily_max_prediction
from .registry import get_city_profile


METHOD = "weatherbot-deb-v2"
DEFAULT_SIGMA_FLOOR_C = 0.3
DEFAULT_SIGMA_FLOOR_F = 0.5
DEFAULT_RMSE_BY_UNIT = {"C": 2.0, "F": 3.6}
MIN_BIAS_SAMPLE_DAYS = 7

CONUS_DEB_WEIGHTS = {
    "openmeteo_ncep_hrrr_conus": 0.25,
    "openmeteo_ncep_nbm_conus": 0.25,
    "openmeteo_ecmwf_ifs025": 0.25,
    "openmeteo_gfs_seamless": 0.15,
    "openmeteo_icon_seamless": 0.10,
}
TOKYO_DEB_WEIGHTS = {
    "openmeteo_jma_seamless": 0.35,
    "openmeteo_ecmwf_ifs025": 0.35,
    "openmeteo_gfs_seamless": 0.20,
    "openmeteo_icon_seamless": 0.10,
}
GLOBAL_DEB_WEIGHTS = {
    "openmeteo_ecmwf_ifs025": 0.45,
    "openmeteo_gfs_seamless": 0.30,
    "openmeteo_icon_seamless": 0.25,
}


def normal_cdf(value: float, mu: float, sigma: float, sigma_floor: float | None = None) -> float:
    sigma_safe = sigma_with_floor(sigma, sigma_floor or 0.0)
    z = (float(value) - float(mu)) / sigma_safe
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def sigma_with_floor(sigma: float | None, sigma_floor: float) -> float:
    try:
        value = float(sigma) if sigma is not None else 0.0
    except Exception:
        value = 0.0
    floor = max(float(sigma_floor or 0.0), 1e-9)
    if not math.isfinite(value) or value <= floor:
        return floor
    return value


def bucket_probabilities(
    mu: float,
    sigma: float,
    buckets: list[dict[str, Any]],
    *,
    unit: str = "C",
    sigma_floor: float | None = None,
    normalize: bool = True,
) -> dict[str, Any]:
    prediction_unit = _clean_unit(unit)
    floor = float(sigma_floor) if sigma_floor is not None else sigma_floor_for_unit(prediction_unit)
    sigma_safe = sigma_with_floor(sigma, floor)
    items: list[dict[str, Any]] = []
    raw_sum = 0.0
    notes: list[str] = []

    for index, bucket in enumerate(buckets):
        bounds = _bucket_bounds_in_prediction_unit(bucket, prediction_unit)
        if bounds is None:
            notes.append(f"bucket_{index}_missing_bounds")
            continue
        low, high = bounds
        probability = _bounded_probability(float(mu), sigma_safe, low, high)
        raw_sum += probability
        market_probability = _market_probability(bucket)
        item = {
            "bucket_key": bucket.get("bucket_key") or "",
            "market_id": bucket.get("market_id") or "",
            "yes_token_id": bucket.get("yes_token_id") or "",
            "bucket_label": bucket.get("bucket_label") or bucket.get("outcome_name") or "",
            "bucket_direction": bucket.get("bucket_direction") or "",
            "bucket_low": low,
            "bucket_high": high,
            "bucket_unit": prediction_unit,
            "probability_raw": probability,
            "probability": probability,
            "market_probability": market_probability,
            "edge": None if market_probability is None else probability - market_probability,
            "best_bid": _optional_float(bucket.get("best_bid")),
            "best_ask": _optional_float(bucket.get("best_ask")),
            "price": _optional_float(bucket.get("price")),
        }
        items.append(item)

    if normalize and raw_sum > 0:
        for item in items:
            probability = float(item["probability_raw"]) / raw_sum
            market_probability = item.get("market_probability")
            item["probability"] = probability
            item["edge"] = None if market_probability is None else probability - float(market_probability)
    elif normalize and raw_sum <= 0:
        notes.append("zero_probability_mass")

    total = sum(float(item.get("probability") or 0.0) for item in items)
    if normalize and items and abs(total - 1.0) > 1e-6:
        correction = total or 1.0
        for item in items:
            probability = float(item.get("probability") or 0.0) / correction
            market_probability = item.get("market_probability")
            item["probability"] = probability
            item["edge"] = None if market_probability is None else probability - float(market_probability)
        total = sum(float(item.get("probability") or 0.0) for item in items)

    ranked = sorted(items, key=lambda row: float(row.get("probability") or 0.0), reverse=True)
    return {
        "ok": bool(items),
        "method": "gaussian-cdf-v1",
        "mu": float(mu),
        "sigma": sigma_safe,
        "unit": prediction_unit,
        "sigma_floor": floor,
        "sigma_floor_applied": sigma_safe != float(sigma or 0.0),
        "normalized": bool(normalize and items and raw_sum > 0),
        "sum_probability": total,
        "raw_sum_probability": raw_sum,
        "items": items,
        "top_model": ranked[:5],
        "notes": notes,
    }


def build_daily_max_prediction(
    city_key: str,
    target_date: str,
    *,
    issued_at: str | None = None,
    sigma_floor_c: float = DEFAULT_SIGMA_FLOOR_C,
    residual_days: int = 14,
    path: Path | None = None,
) -> dict[str, Any]:
    init_v3_db(path)
    profile = get_city_profile(city_key)
    unit = _clean_unit(profile.unit if profile else "C")
    sigma_floor = sigma_floor_for_unit(unit) if sigma_floor_c == DEFAULT_SIGMA_FLOOR_C else convert_sigma(float(sigma_floor_c), "C", unit)
    issued = _floor_issued_at(issued_at)

    if os.getenv("WEATHERBOT_ENSEMBLE_DEB_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}:
        try:
            from .forecasts.ensemble import build_ensemble_prediction

            ensemble = build_ensemble_prediction(city_key, target_date, issued_at=issued, path=path)
        except Exception as exc:
            ensemble = {"ok": False, "reasons": [f"ensemble_error:{exc}"]}
        if ensemble.get("ok"):
            observed_floor = _observed_floor(city_key, target_date, unit, path)
            floor_applied = False
            if observed_floor is not None and observed_floor > float(ensemble.get("mu") or -999):
                ensemble["mu"] = observed_floor
                floor_applied = True
            mixed_peak = _mixed_curve_peak(
                city_key,
                target_date,
                unit,
                profile.timezone if profile else "UTC",
                issued,
                path,
            )
            ensemble.update({
                "observed_floor": observed_floor,
                "mu_observed_floor_applied": floor_applied,
                "peak_hour": mixed_peak.get("peak_hour") or ensemble.get("peak_hour") or "",
                "peak_temp": mixed_peak.get("peak_temp") if mixed_peak.get("peak_temp") is not None else ensemble.get("peak_temp"),
                "peak_source": mixed_peak.get("peak_source") or ensemble.get("peak_source") or "",
                "mixed_peak": mixed_peak,
            })
            return ensemble

    forecast_components, component_meta = _forecast_components(city_key, target_date, unit, residual_days, path)
    if not forecast_components:
        return {
            "ok": False,
            "city_key": city_key,
            "target_date": target_date,
            "issued_at": issued,
            "unit": unit,
            "method": METHOD,
            "deb_version": METHOD,
            "sigma_floor": sigma_floor,
            "mu_observed_floor_applied": False,
            "reasons": ["missing_forecast_runs"],
        }

    weight_sum = sum(component["weight_raw"] for component in forecast_components) or 1.0
    for component in forecast_components:
        component["weight"] = component["weight_raw"] / weight_sum

    bias_info = _bias_correction(city_key, target_date, residual_days, path)
    warnings = list(component_meta.get("warnings") or [])
    if bias_info["sample_count"] < MIN_BIAS_SAMPLE_DAYS:
        warnings.append("insufficient_settlement_days")
    bias_correction = bias_info["bias"] if bias_info["sample_count"] >= MIN_BIAS_SAMPLE_DAYS else 0.0

    mu_raw = sum(component["model_daily_high"] * component["weight"] for component in forecast_components)
    mu_bias_adjusted = mu_raw + bias_correction
    member_count = sum(int(component["member_count"]) for component in forecast_components)
    source_run_ids = sorted({run_id for component in forecast_components for run_id in component["run_ids"]})
    model_weights = {component["source"]: component["weight"] for component in forecast_components}
    member_daily_highs = {component["source"]: component["member_daily_highs"] for component in forecast_components}

    model_highs = [float(component["model_daily_high"]) for component in forecast_components]
    sigma_from_spread = (max(model_highs) - min(model_highs)) if len(model_highs) > 1 else 0.0
    sigma_from_history = bias_info["residual_std"] if bias_info["sample_count"] >= MIN_BIAS_SAMPLE_DAYS else DEFAULT_RMSE_BY_UNIT.get(unit, 2.0)
    sigma_raw = math.sqrt(sigma_from_spread**2 + sigma_from_history**2) / 2.0
    observed_floor = _observed_floor(city_key, target_date, unit, path)
    time_decay = _time_decay_factor(target_date, profile.timezone if profile else "UTC", observed_floor is not None)
    sigma = sigma_with_floor(sigma_raw * time_decay, sigma_floor)
    mixed_peak = _mixed_curve_peak(
        city_key,
        target_date,
        unit,
        profile.timezone if profile else "UTC",
        issued,
        path,
    )

    mu = mu_bias_adjusted
    floor_applied = False
    if observed_floor is not None and observed_floor > mu:
        mu = observed_floor
        floor_applied = True

    prediction = {
        "ok": True,
        "city_key": city_key,
        "target_date": target_date,
        "issued_at": issued,
        "mu": mu,
        "sigma": sigma,
        "unit": unit,
        "method": METHOD,
        "deb_version": METHOD,
        "model_weights": model_weights,
        "member_count": member_count,
        "components": forecast_components,
        "source_run_ids": source_run_ids,
        "member_daily_highs": member_daily_highs,
        "sigma_from_spread": sigma_from_spread,
        "sigma_from_history": sigma_from_history,
        "bias_correction": bias_correction,
        "bias_sample_count": bias_info["sample_count"],
        "observed_floor": observed_floor,
        "sigma_floor": sigma_floor,
        "time_decay_factor": time_decay,
        "mu_observed_floor_applied": floor_applied,
        "mu_raw": mu_raw,
        "mu_bias_adjusted": mu_bias_adjusted,
        "sigma_raw": sigma_raw,
        "build_warnings": sorted(set(warnings)),
        "peak_hour": mixed_peak.get("peak_hour") or "",
        "peak_temp": mixed_peak.get("peak_temp"),
        "peak_source": mixed_peak.get("peak_source") or "",
        "mixed_peak": mixed_peak,
    }
    return prediction


def build_and_store_daily_max_prediction(
    city_key: str,
    target_date: str,
    *,
    issued_at: str | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    prediction = build_daily_max_prediction(city_key, target_date, issued_at=issued_at, path=path)
    if prediction.get("ok"):
        prediction["id"] = upsert_daily_max_prediction(prediction, path=path)
    return prediction


def build_daily_max_predictions(
    *,
    city: str | None = None,
    target_date: str | None = None,
    limit: int = 50,
    dry_run: bool = False,
    path: Path | None = None,
) -> dict[str, Any]:
    init_v3_db(path)
    where: list[str] = []
    params: list[Any] = []
    if city:
        where.append("city = ?")
        params.append(city)
    if target_date:
        where.append("target_date = ?")
        params.append(target_date)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    bounded_limit = max(1, min(int(limit or 50), 200))
    with connect(path) as conn:
        targets = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT city, target_date, MAX(COALESCE(retrieved_at, run_at, created_at)) AS latest_input_at
                FROM forecast_runs
                {clause}
                GROUP BY city, target_date
                ORDER BY latest_input_at DESC
                LIMIT ?
                """,
                (*params, bounded_limit),
            ).fetchall()
        ]
    results = []
    for row in targets:
        if dry_run:
            results.append(build_daily_max_prediction(str(row["city"]), str(row["target_date"]), path=path))
        else:
            results.append(build_and_store_daily_max_prediction(str(row["city"]), str(row["target_date"]), path=path))
    return {
        "ok": True,
        "dry_run": dry_run,
        "requested": len(targets),
        "stored": 0 if dry_run else sum(1 for item in results if item.get("ok")),
        "failed": sum(1 for item in results if not item.get("ok")),
        "predictions": results,
    }


def latest_bucket_probabilities(
    city_key: str,
    target_date: str,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    predictions = list_daily_max_predictions(city_key=city_key, target_date=target_date, limit=1, path=path)
    if not predictions:
        return {
            "ok": False,
            "city_key": city_key,
            "target_date": target_date,
            "reasons": ["missing_daily_max_prediction"],
            "items": [],
        }
    buckets = list_market_buckets(city=city_key, target_date=target_date, limit=1000)
    if not buckets:
        return {
            "ok": False,
            "city_key": city_key,
            "target_date": target_date,
            "prediction": predictions[0],
            "reasons": ["missing_market_buckets"],
            "items": [],
        }
    prediction = predictions[0]
    distribution = bucket_probabilities(
        float(prediction["mu"]),
        float(prediction["sigma"]),
        buckets,
        unit=str(prediction.get("unit") or "C"),
        sigma_floor=_optional_float(prediction.get("sigma_floor")),
        normalize=True,
    )
    distribution.update({
        "city_key": city_key,
        "target_date": target_date,
        "prediction": prediction,
    })
    return distribution


def decision_skeleton_from_distribution(
    signal_id: int,
    market_id: str,
    model_distribution: dict[str, Any],
) -> dict[str, Any]:
    edge_by_bucket = {
        str(item.get("market_id") or item.get("bucket_key") or index): {
            "model_probability": item.get("probability"),
            "market_probability": item.get("market_probability"),
            "edge": item.get("edge"),
        }
        for index, item in enumerate(model_distribution.get("items") or [])
    }
    return {
        "signal_id": signal_id,
        "market_id": market_id,
        "action": "observe",
        "paper_allowed": False,
        "live_allowed": False,
        "reasons": ["probability_layer_skeleton_only"],
        "cautions": [],
        "gate_reasons": ["layer_6_not_connected_to_execution"],
        "model_distribution": {
            "mu": model_distribution.get("mu"),
            "sigma": model_distribution.get("sigma"),
            "unit": model_distribution.get("unit"),
            "method": model_distribution.get("method"),
            "sum_probability": model_distribution.get("sum_probability"),
        },
        "model_bucket_probs": model_distribution,
        "market_bucket_probs": [
            {
                "bucket_key": item.get("bucket_key"),
                "market_id": item.get("market_id"),
                "market_probability": item.get("market_probability"),
                "best_bid": item.get("best_bid"),
                "best_ask": item.get("best_ask"),
            }
            for item in model_distribution.get("items") or []
        ],
        "edge_by_bucket": edge_by_bucket,
    }


def sigma_floor_for_unit(unit: str) -> float:
    return DEFAULT_SIGMA_FLOOR_F if _clean_unit(unit) == "F" else DEFAULT_SIGMA_FLOOR_C


def convert_temp(value: float, from_unit: str, to_unit: str) -> float:
    source = _clean_unit(from_unit)
    target = _clean_unit(to_unit)
    if source == target:
        return float(value)
    if source == "C" and target == "F":
        return float(value) * 9.0 / 5.0 + 32.0
    if source == "F" and target == "C":
        return (float(value) - 32.0) * 5.0 / 9.0
    return float(value)


def convert_sigma(value: float, from_unit: str, to_unit: str) -> float:
    source = _clean_unit(from_unit)
    target = _clean_unit(to_unit)
    if source == target:
        return abs(float(value))
    if source == "C" and target == "F":
        return abs(float(value)) * 9.0 / 5.0
    if source == "F" and target == "C":
        return abs(float(value)) * 5.0 / 9.0
    return abs(float(value))


def _forecast_components(
    city_key: str,
    target_date: str,
    unit: str,
    residual_days: int,
    path: Path | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    profile = get_city_profile(city_key)
    source_weights = _deb_weights_for_profile(profile)
    with connect(path) as conn:
        runs = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM forecast_runs
                WHERE city = ? AND target_date = ?
                ORDER BY COALESCE(retrieved_at, run_at, created_at) DESC, id DESC
                """,
                (city_key, target_date),
            ).fetchall()
        ]
        members_by_run: dict[int, list[dict[str, Any]]] = {}
        for row in conn.execute(
            """
            SELECT fm.run_id, fm.member_id, fm.high_temp, fm.hourly_json
            FROM forecast_members fm
            JOIN forecast_runs fr ON fr.id = fm.run_id
            WHERE fr.city = ? AND fr.target_date = ?
            """,
            (city_key, target_date),
        ).fetchall():
            members_by_run.setdefault(int(row["run_id"]), []).append(dict(row))

    latest_by_source: dict[str, dict[str, Any]] = {}
    for run in runs:
        if str(run.get("parse_status") or "parsed") != "parsed":
            continue
        source = str(run.get("source") or run.get("provider") or run.get("model") or "unknown")
        if source == "polywx_forecast":
            continue
        if source.startswith("openmeteo_ensemble_"):
            continue
        if source.startswith("openmeteo_") and source not in source_weights:
            continue
        if not _run_training_eligible(run):
            continue
        if source not in latest_by_source:
            latest_by_source[source] = run

    warnings: list[str] = []
    if not latest_by_source:
        for run in runs:
            source = str(run.get("source") or run.get("provider") or run.get("model") or "unknown")
            if source == "polywx_forecast" or source.startswith("openmeteo_ensemble_"):
                continue
            if source.startswith("openmeteo_") and source not in source_weights:
                continue
            if source not in latest_by_source:
                latest_by_source[source] = run
        if latest_by_source:
            warnings.append("legacy_typed_forecast_fallback")
    if not latest_by_source:
        polywx = next((run for run in runs if str(run.get("source") or "") == "polywx_forecast"), None)
        if polywx:
            latest_by_source["polywx_forecast"] = polywx
            warnings.append("fallback_polywx_only")

    grouped: dict[str, dict[str, Any]] = {}
    for source, run in latest_by_source.items():
        run_unit = _clean_unit(run.get("unit") or run.get("source_unit") or unit)
        member_daily_highs: list[float] = []
        for member in members_by_run.get(int(run["id"])) or []:
            high = _member_daily_high(member, target_date, profile.timezone if profile else "UTC", run_unit, unit)
            if high is not None:
                member_daily_highs.append(high)
        if not member_daily_highs:
            mean_high = _optional_float(run.get("mean_high"))
            if mean_high is not None and _plausible_temp(mean_high):
                member_daily_highs = [convert_temp(mean_high, run_unit, unit)]
        if not member_daily_highs:
            continue
        bucket = grouped.setdefault(source, {"source": source, "values": [], "run_ids": [], "run_keys": []})
        bucket["values"].extend(member_daily_highs)
        bucket["run_ids"].append(int(run["id"]))
        bucket["run_keys"].append(str(run.get("run_key") or ""))

    components: list[dict[str, Any]] = []
    for source, bucket in grouped.items():
        values = [float(value) for value in bucket["values"]]
        model_daily_high = statistics.median(values)
        member_std = statistics.pstdev(values) if len(values) > 1 else 0.0
        components.append({
            "source": source,
            "model_daily_high": model_daily_high,
            "member_daily_highs": values,
            "member_std": member_std,
            "member_count": len(values),
            "weight_raw": float(source_weights.get(source, 0.01)),
            "run_ids": bucket["run_ids"],
            "run_keys": bucket["run_keys"][:10],
        })
    return components, {"warnings": warnings}


def _deb_weights_for_profile(profile) -> dict[str, float]:
    if profile and profile.region == "us":
        return CONUS_DEB_WEIGHTS
    if profile and profile.city == "tokyo":
        return TOKYO_DEB_WEIGHTS
    return GLOBAL_DEB_WEIGHTS


def _run_training_eligible(run: dict[str, Any]) -> bool:
    value = run.get("training_eligible")
    if value is None:
        return str(run.get("source") or "") != "polywx_forecast"
    try:
        return bool(int(value))
    except Exception:
        return bool(value)


def _member_daily_high(
    member: dict[str, Any],
    target_date: str,
    timezone_name: str,
    run_unit: str,
    output_unit: str,
) -> float | None:
    hourly = _loads(member.get("hourly_json"), [])
    values: list[float] = []
    if isinstance(hourly, list):
        for item in hourly:
            if not isinstance(item, dict):
                continue
            valid_at = item.get("valid_at") or item.get("time") or item.get("timestamp")
            if _local_date(valid_at, timezone_name) != str(target_date):
                continue
            value = _optional_float(item.get("temperature_2m") or item.get("temperature") or item.get("temp"))
            if value is not None and _plausible_temp(value):
                values.append(convert_temp(value, run_unit, output_unit))
    if values:
        return max(values)
    value = _optional_float(member.get("high_temp"))
    if value is not None and _plausible_temp(value):
        return convert_temp(value, run_unit, output_unit)
    return None


def _bias_correction(city_key: str, target_date: str, residual_days: int, path: Path | None) -> dict[str, Any]:
    profile = get_city_profile(city_key)
    unit = _clean_unit(profile.unit if profile else "C")
    residual_limit = 18.0 if unit == "F" else 10.0
    with connect(path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT target_date, AVG(residual) AS day_bias
                FROM hourly_consensus
                WHERE city = ?
                  AND target_date < ?
                  AND residual IS NOT NULL
                  AND forecast_source = 'openmeteo_multi_model'
                  AND ABS(residual) <= ?
                GROUP BY target_date
                ORDER BY target_date DESC
                LIMIT ?
                """,
                (city_key, target_date, residual_limit, max(1, int(residual_days or 14))),
            ).fetchall()
        ]
    residuals = [
        float(row["day_bias"])
        for row in rows
        if _optional_float(row.get("day_bias")) is not None
    ]
    if not residuals:
        return {"bias": 0.0, "residual_std": DEFAULT_RMSE_BY_UNIT.get(_clean_unit(getattr(get_city_profile(city_key), "unit", "C")), 2.0), "sample_count": 0}
    bias = statistics.median(residuals)
    residual_std = statistics.pstdev(residuals) if len(residuals) > 1 else DEFAULT_RMSE_BY_UNIT.get(_clean_unit(getattr(get_city_profile(city_key), "unit", "C")), 2.0)
    return {"bias": bias, "residual_std": residual_std, "sample_count": len(residuals)}


def _mixed_curve_peak(
    city_key: str,
    target_date: str,
    unit: str,
    timezone_name: str,
    issued_at: str,
    path: Path | None,
) -> dict[str, Any]:
    """Return the daily peak hour from a forecast/observation mixed curve.

    Past hours prefer observed temperature when available; future hours use the
    forecast curve. Ties intentionally choose the latest local hour, matching
    the visual "peak marker" behavior users expect on an intraday chart.
    """
    issued = _parse_datetime(issued_at) or datetime.now(timezone.utc)
    default_unit = _clean_unit((get_city_profile(city_key).unit if get_city_profile(city_key) else unit) or unit)
    with connect(path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT local_hour, valid_time, forecast_temp, observed_temp,
                       observation_source, forecast_source
                FROM hourly_consensus
                WHERE city = ? AND target_date = ?
                ORDER BY local_hour
                """,
                (city_key, target_date),
            ).fetchall()
        ]
    curve: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    for row in rows:
        local_hour = str(row.get("local_hour") or "")
        if not local_hour:
            continue
        valid = _parse_datetime(row.get("valid_time")) or _local_hour_datetime(target_date, local_hour, timezone_name)
        is_past = bool(valid and valid <= issued)
        observed = _optional_float(row.get("observed_temp"))
        forecast = _optional_float(row.get("forecast_temp"))
        source = "forecast"
        value = forecast
        if is_past and observed is not None:
            source = str(row.get("observation_source") or "observation")
            value = observed
        if value is None or not _plausible_temp(value):
            continue
        converted = convert_temp(value, default_unit, unit)
        point = {
            "local_hour": local_hour,
            "valid_time": row.get("valid_time") or "",
            "value": converted,
            "source": source,
            "is_past": is_past,
            "observed_temp": None if observed is None else convert_temp(observed, default_unit, unit),
            "forecast_temp": None if forecast is None else convert_temp(forecast, default_unit, unit),
        }
        curve.append(point)
        if best is None:
            best = point
            continue
        best_value = float(best.get("value") or -math.inf)
        if converted > best_value + 1e-9 or (abs(converted - best_value) <= 1e-9 and local_hour >= str(best.get("local_hour") or "")):
            best = point
    if not best:
        return {"ok": False, "reason": "missing_hourly_consensus", "curve_points": 0}
    return {
        "ok": True,
        "method": "mixed_curve_argmax_v1",
        "issued_at": issued.isoformat(),
        "peak_hour": best["local_hour"],
        "peak_temp": best["value"],
        "peak_source": best["source"],
        "tie_policy": "latest_local_hour",
        "curve_points": len(curve),
        "curve": curve,
    }


def _metar_temperature_unit(row: dict[str, Any], default_unit: str) -> str:
    data = dict(row) if not isinstance(row, dict) else row
    parser_version = str(data.get("parser_version") or "").lower()
    if parser_version.startswith("iem-asos-csv"):
        return "C"
    raw = _loads(data.get("raw_json"), {})
    if isinstance(raw, dict):
        unit = raw.get("normalized_temperature_unit") or raw.get("temperature_unit")
        if unit:
            return str(unit)
        payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
        unit = payload.get("normalized_temperature_unit") if isinstance(payload, dict) else None
        if unit:
            return str(unit)
    return default_unit


def _floor_issued_at(value: str | None) -> str:
    parsed = _parse_datetime(value) if value else datetime.now(timezone.utc)
    parsed = parsed.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return parsed.isoformat()


def _local_date(value: Any, timezone_name: str) -> str | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    try:
        tz = ZoneInfo(timezone_name) if ZoneInfo else timezone.utc
    except Exception:
        tz = timezone.utc
    return parsed.astimezone(tz).date().isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _local_hour_datetime(target_date: str, local_hour: str, timezone_name: str) -> datetime | None:
    try:
        hour = int(str(local_hour).split(":", 1)[0])
        local = datetime.fromisoformat(str(target_date)).replace(
            hour=hour,
            minute=0,
            second=0,
            microsecond=0,
            tzinfo=ZoneInfo(timezone_name) if ZoneInfo else timezone.utc,
        )
        return local.astimezone(timezone.utc)
    except Exception:
        return None


def _loads(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default


def _observed_floor(city_key: str, target_date: str, unit: str, path: Path | None) -> float | None:
    profile = get_city_profile(city_key)
    default_unit = _clean_unit(profile.unit if profile else unit)
    timezone_name = profile.timezone if profile else "UTC"
    observed_values: list[float] = []
    with connect(path) as conn:
        for row in conn.execute(
            """
            SELECT temperature, report_time, parser_version, raw_json
            FROM metar_reports
            WHERE city = ?
            """,
            (city_key,),
        ).fetchall():
            if _local_date(row["report_time"], timezone_name) != str(target_date):
                continue
            value = _optional_float(row["temperature"])
            if value is not None and _plausible_temp(value):
                observed_values.append(convert_temp(value, _metar_temperature_unit(row, default_unit), unit))
        for row in conn.execute(
            """
            SELECT temperature, raw_unit, observed_at
            FROM mesonet_observations
            WHERE city = ?
            """,
            (city_key,),
        ).fetchall():
            if _local_date(row["observed_at"], timezone_name) != str(target_date):
                continue
            value = _optional_float(row["temperature"])
            raw_unit = _clean_unit(row["raw_unit"] or default_unit)
            if value is not None and _plausible_temp(value):
                observed_values.append(convert_temp(value, raw_unit, unit))
        for row in conn.execute(
            """
            SELECT observed_temp
            FROM hourly_consensus
            WHERE city = ? AND target_date = ? AND observed_temp IS NOT NULL
            """,
            (city_key, target_date),
        ).fetchall():
            value = _optional_float(row["observed_temp"])
            if value is not None and _plausible_temp(value):
                observed_values.append(convert_temp(value, default_unit, unit))
    if not observed_values:
        return None
    return max(observed_values)


def _bucket_bounds_in_prediction_unit(bucket: dict[str, Any], prediction_unit: str) -> tuple[float, float] | None:
    bucket_unit = _clean_unit(bucket.get("unit") or prediction_unit)
    low_raw = _optional_float(bucket.get("bucket_low"))
    high_raw = _optional_float(bucket.get("bucket_high"))
    direction = str(bucket.get("bucket_direction") or "").lower()
    truncates_celsius = bucket_unit == "C"

    if direction in {"or_below", "below", "under", "at_or_below"}:
        if truncates_celsius and high_raw is not None:
            high_raw += 1.0
        low_raw = None
    if direction in {"or_above", "above", "over", "at_or_above"}:
        high_raw = None
    if low_raw is not None and low_raw <= -900:
        low_raw = None
    if high_raw is not None and high_raw >= 900:
        high_raw = None

    if low_raw is None and high_raw is None:
        value = _optional_float(bucket.get("bucket_value"))
        if value is None:
            return None
        if truncates_celsius:
            low_raw = value
            high_raw = value + 1.0
        else:
            low_raw = value - 0.5
            high_raw = value + 0.5
    elif low_raw is not None and high_raw is not None and low_raw == high_raw:
        if truncates_celsius:
            high_raw += 1.0
        else:
            low_raw -= 0.5
            high_raw += 0.5
    elif truncates_celsius and low_raw is not None and high_raw is not None:
        high_raw += 1.0

    low = -math.inf if low_raw is None else convert_temp(low_raw, bucket_unit, prediction_unit)
    high = math.inf if high_raw is None else convert_temp(high_raw, bucket_unit, prediction_unit)
    if high < low:
        low, high = high, low
    return low, high


def _bounded_probability(mu: float, sigma: float, low: float, high: float) -> float:
    if math.isinf(low) and low < 0 and math.isinf(high) and high > 0:
        return 1.0
    if math.isinf(low) and low < 0:
        return max(0.0, min(1.0, normal_cdf(high, mu, sigma)))
    if math.isinf(high) and high > 0:
        return max(0.0, min(1.0, 1.0 - normal_cdf(low, mu, sigma)))
    return max(0.0, min(1.0, normal_cdf(high, mu, sigma) - normal_cdf(low, mu, sigma)))


def _market_probability(bucket: dict[str, Any]) -> float | None:
    for key in ("best_ask", "price", "market_probability"):
        value = _optional_float(bucket.get(key))
        if value is not None and 0.0 <= value <= 1.0:
            return value
    return None


def _time_decay_factor(target_date: str, timezone_name: str, has_observed_floor: bool = False) -> float:
    try:
        date_value = datetime.fromisoformat(str(target_date)).date()
    except Exception:
        return 1.0
    try:
        tz = ZoneInfo(timezone_name) if ZoneInfo else timezone.utc
        now_local = datetime.now(tz)
    except Exception:
        now_local = datetime.now(timezone.utc)
    if date_value > now_local.date():
        return 1.0
    if date_value < now_local.date():
        return 0.35
    if has_observed_floor and now_local.hour >= 15:
        return 0.5
    remaining_hours = max(0.0, 24.0 - (now_local.hour + now_local.minute / 60.0))
    return max(0.35, min(1.0, math.sqrt(remaining_hours / 24.0)))


def _clean_unit(value: Any) -> str:
    text = str(value or "").strip().upper()
    return "F" if text.startswith("F") else "C"


def _optional_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _plausible_temp(value: float) -> bool:
    return -100.0 <= float(value) <= 180.0
