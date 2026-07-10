from __future__ import annotations

import hashlib
import json
import math
import os
import time
from datetime import datetime, time as datetime_time, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from .db import insert_forecast_runs, log_data_fetch, utc_now
from .registry import SETTLEMENT_REGISTRY, CitySettlementProfile


OPENMETEO_FORECAST_URL = os.getenv("OPENMETEO_FORECAST_URL", "https://api.open-meteo.com/v1/forecast")
OPENMETEO_ENSEMBLE_URL = os.getenv("OPENMETEO_ENSEMBLE_URL", "https://ensemble-api.open-meteo.com/v1/ensemble")
OPENMETEO_PREVIOUS_RUNS_URL = os.getenv("OPENMETEO_PREVIOUS_RUNS_URL", "https://previous-runs-api.open-meteo.com/v1/forecast")
OPENMETEO_PARSER_VERSION = "openmeteo-forecast-v1"
OPENMETEO_PREVIOUS_PARSER_VERSION = "openmeteo-previous-runs-v1"
OPENMETEO_STAGE = "refresh_forecast_runs"
OPENMETEO_PREVIOUS_STAGE = "refresh_forecast_runs"
OPENMETEO_HOURLY_FIELDS = (
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "dew_point_2m",
    "cloud_cover",
    "wind_speed_10m",
    "wind_gusts_10m",
    "precipitation",
    "precipitation_probability",
)
OPENMETEO_DEFAULT_CITY_PRIORITY = ("chicago", "tokyo", "atlanta", "nyc", "dallas")

CONUS_MODELS = (
    "ecmwf_ifs025",
    "gfs_seamless",
    "ncep_hrrr_conus",
    "ncep_nbm_conus",
    "icon_seamless",
    "gem_seamless",
    "ecmwf_aifs025_single",
)
TOKYO_MODELS = (
    "ecmwf_ifs025",
    "gfs_seamless",
    "jma_seamless",
    "icon_seamless",
    "gem_seamless",
    "ecmwf_aifs025_single",
)
CHINA_MODELS = (
    "ecmwf_ifs025",
    "gfs_seamless",
    "cma_grapes_global",
    "icon_seamless",
    "gem_seamless",
    "ecmwf_aifs025_single",
)
GLOBAL_MODELS = (
    "ecmwf_ifs025",
    "gfs_seamless",
    "icon_seamless",
    "gem_seamless",
    "ecmwf_aifs025_single",
)
ENSEMBLE_CAPABLE_MODELS = (
    "ecmwf_ifs025",
    "gfs_seamless",
    "icon_seamless",
)
MODEL_UPDATE_CADENCE_HOURS = {
    "gfs_seamless": 1,
    "gfs_hrrr": 1,
    "ncep_hrrr_conus": 1,
    "ncep_nbm_conus": 1,
    "ecmwf_ifs025": 6,
    "ecmwf_aifs025_single": 6,
    "icon_seamless": 3,
    "jma_seamless": 3,
    "cma_grapes_global": 6,
    "gem_seamless": 12,
}


def model_allowlist_for_city(city_key: str) -> list[str]:
    profile = SETTLEMENT_REGISTRY.get(str(city_key or "").strip().lower())
    if not profile:
        return list(GLOBAL_MODELS)
    if profile.region == "us":
        return list(CONUS_MODELS)
    if profile.city in {"shanghai", "beijing", "wuhan", "qingdao", "shenzhen", "hong-kong"}:
        return list(CHINA_MODELS)
    if profile.city == "tokyo":
        return list(TOKYO_MODELS)
    return list(GLOBAL_MODELS)


def ensemble_model_allowlist_for_city(city_key: str) -> list[str]:
    base = model_allowlist_for_city(city_key)
    return [model for model in base if model in ENSEMBLE_CAPABLE_MODELS]


def fetch_openmeteo_forecasts(
    cities: list[str] | None = None,
    *,
    ensemble: bool = False,
    dry_run: bool = False,
    limit_cities: int = 5,
    forecast_days: int = 7,
    session: requests.Session | None = None,
    retrieved_at: str | None = None,
    sleep_seconds: float | None = None,
) -> dict[str, Any]:
    profiles = _select_profiles(cities, limit_cities=limit_cities)
    retrieved = _parse_time(retrieved_at) or _parse_time(utc_now())
    client = session or requests.Session()
    results: list[dict[str, Any]] = []
    requests_planned: list[dict[str, Any]] = []
    total_runs = 0
    total_members = 0
    failures = 0
    endpoint_kind = "ensemble" if ensemble else "deterministic"
    endpoint_url = OPENMETEO_ENSEMBLE_URL if ensemble else OPENMETEO_FORECAST_URL
    delay = float(os.getenv("OPENMETEO_REQUEST_DELAY_SECONDS", "1.5")) if sleep_seconds is None else float(sleep_seconds)

    for profile in profiles:
        models = ensemble_model_allowlist_for_city(profile.city) if ensemble else model_allowlist_for_city(profile.city)
        city_result = {
            "city": profile.city,
            "station_id": profile.station_id,
            "endpoint": endpoint_kind,
            "models_requested": models,
            "models": [],
            "runs_upserted": 0,
            "members_upserted": 0,
            "failures": [],
        }
        for model in models:
            request = build_openmeteo_request(profile, model, endpoint_url=endpoint_url, forecast_days=forecast_days)
            requests_planned.append({
                "city": profile.city,
                "model": model,
                "endpoint": endpoint_kind,
                "url": _preview_url(endpoint_url, request),
            })
            if dry_run:
                continue
            started = utc_now()
            started_perf = time.perf_counter()
            response = _request_json(client, endpoint_url, request)
            duration_ms = round((time.perf_counter() - started_perf) * 1000)
            if not response.get("ok"):
                failures += 1
                error = {
                    "model": model,
                    "reason": "openmeteo_http_error",
                    "status_code": response.get("status_code"),
                    "message": response.get("error") or response.get("text", "")[:200],
                }
                city_result["failures"].append(error)
                log_data_fetch(
                    source="openmeteo",
                    stage=OPENMETEO_STAGE,
                    status="WARN",
                    duration_ms=duration_ms,
                    city=profile.city,
                    message=f"Open-Meteo fetch failed for {profile.city}/{model}",
                    details={**error, "endpoint": endpoint_kind, "url": response.get("url")},
                    started_at=started,
                    finished_at=utc_now(),
                )
                continue
            runs, members = openmeteo_runs_from_response(
                profile.city,
                model,
                response.get("json") or {},
                source_url=response.get("url") or _preview_url(endpoint_url, request),
                retrieved_at=retrieved.isoformat() if retrieved else None,
                endpoint_kind=endpoint_kind,
            )
            run_items = list(zip(runs, members))
            run_ids = insert_forecast_runs(run_items)
            city_result["runs_upserted"] += len(run_ids)
            city_result["members_upserted"] += sum(len(run_members) for _, run_members in run_items)
            total_runs += len(run_ids)
            total_members += sum(len(item) for item in members)
            model_status = "OK" if all(run.get("parse_status") == "parsed" for run in runs) else "WARN"
            model_summary = {
                "model": model,
                "run_ids": run_ids,
                "runs": len(run_ids),
                "members": sum(len(item) for item in members),
                "parse_statuses": sorted({str(run.get("parse_status") or "") for run in runs}),
            }
            city_result["models"].append(model_summary)
            log_data_fetch(
                source="openmeteo",
                stage=OPENMETEO_STAGE,
                status=model_status,
                duration_ms=duration_ms,
                city=profile.city,
                message=f"Open-Meteo fetched {profile.city}/{model}",
                details={
                    "endpoint": endpoint_kind,
                    "model": model,
                    "runs": len(run_ids),
                    "members": model_summary["members"],
                    "parse_statuses": model_summary["parse_statuses"],
                    "url": response.get("url"),
                },
                started_at=started,
                finished_at=utc_now(),
            )
            if session is None and delay > 0:
                time.sleep(delay)
        results.append(city_result)
    return {
        "ok": failures == 0,
        "source": "openmeteo",
        "endpoint": endpoint_kind,
        "dry_run": dry_run,
        "cities": [profile.city for profile in profiles],
        "requests_planned": requests_planned,
        "runs_upserted": 0 if dry_run else total_runs,
        "members_upserted": 0 if dry_run else total_members,
        "failed": failures,
        "results": results,
    }


def fetch_openmeteo_previous_runs(
    cities: list[str] | None = None,
    *,
    target_dates: list[str] | None = None,
    models: list[str] | None = None,
    previous_days: list[int] | None = None,
    dry_run: bool = False,
    limit_cities: int = 5,
    session: requests.Session | None = None,
    retrieved_at: str | None = None,
    sleep_seconds: float | None = None,
) -> dict[str, Any]:
    """Fetch archived Open-Meteo previous-run forecasts into Layer 3.

    Previous Runs expose deterministic lead-time snapshots such as
    ``temperature_2m_previous_day1``. Each previous-day series is persisted as
    a separate forecast run so later calibration can evaluate lead-time quality
    without mixing D+1, D+2, and D+3 forecasts.
    """
    profiles = _select_profiles(cities, limit_cities=limit_cities)
    targets = [str(item) for item in (target_dates or []) if str(item).strip()]
    if not targets:
        now_utc = _parse_time(utc_now()) or datetime.now(timezone.utc)
        targets = [now_utc.date().isoformat()]
    lead_days = _normalize_previous_days(previous_days)
    client = session or requests.Session()
    delay = float(os.getenv("OPENMETEO_REQUEST_DELAY_SECONDS", "1.5")) if sleep_seconds is None else float(sleep_seconds)
    retrieved = _parse_time(retrieved_at) or _parse_time(utc_now())

    results: list[dict[str, Any]] = []
    requests_planned: list[dict[str, Any]] = []
    total_runs = 0
    total_members = 0
    failures = 0
    for profile in profiles:
        selected_models = [str(model).strip() for model in (models or previous_run_models_for_city(profile.city)) if str(model).strip()]
        city_result = {
            "city": profile.city,
            "station_id": profile.station_id,
            "target_dates": targets,
            "models_requested": selected_models,
            "previous_days": lead_days,
            "runs_upserted": 0,
            "members_upserted": 0,
            "failures": [],
            "models": [],
        }
        for target_date in targets:
            for model in selected_models:
                request = build_previous_runs_request(profile, model, target_date, previous_days=lead_days)
                preview_url = _preview_url(OPENMETEO_PREVIOUS_RUNS_URL, request)
                requests_planned.append({
                    "city": profile.city,
                    "target_date": target_date,
                    "model": model,
                    "endpoint": "previous_runs",
                    "url": preview_url,
                })
                if dry_run:
                    continue
                started = utc_now()
                started_perf = time.perf_counter()
                response = _request_json(client, OPENMETEO_PREVIOUS_RUNS_URL, request)
                duration_ms = round((time.perf_counter() - started_perf) * 1000)
                if not response.get("ok"):
                    failures += 1
                    error = {
                        "target_date": target_date,
                        "model": model,
                        "reason": "openmeteo_previous_runs_http_error",
                        "status_code": response.get("status_code"),
                        "message": response.get("error") or response.get("text", "")[:200],
                    }
                    city_result["failures"].append(error)
                    log_data_fetch(
                        source="openmeteo",
                        stage=OPENMETEO_PREVIOUS_STAGE,
                        status="WARN",
                        duration_ms=duration_ms,
                        city=profile.city,
                        message=f"Open-Meteo previous-runs fetch failed for {profile.city}/{model}/{target_date}",
                        details={**error, "url": response.get("url")},
                        started_at=started,
                        finished_at=utc_now(),
                    )
                    continue
                runs, members = openmeteo_previous_runs_from_response(
                    profile.city,
                    model,
                    target_date,
                    response.get("json") or {},
                    previous_days=lead_days,
                    source_url=response.get("url") or preview_url,
                    retrieved_at=retrieved.isoformat() if retrieved else None,
                )
                run_items = list(zip(runs, members))
                run_ids = insert_forecast_runs(run_items)
                city_result["runs_upserted"] += len(run_ids)
                city_result["members_upserted"] += sum(len(run_members) for _, run_members in run_items)
                total_runs += len(run_ids)
                total_members += sum(len(item) for item in members)
                parse_statuses = sorted({str(run.get("parse_status") or "") for run in runs})
                city_result["models"].append({
                    "target_date": target_date,
                    "model": model,
                    "run_ids": run_ids,
                    "runs": len(run_ids),
                    "members": sum(len(item) for item in members),
                    "parse_statuses": parse_statuses,
                })
                log_data_fetch(
                    source="openmeteo",
                    stage=OPENMETEO_PREVIOUS_STAGE,
                    status="OK" if parse_statuses == ["parsed"] else "WARN",
                    duration_ms=duration_ms,
                    city=profile.city,
                    message=f"Open-Meteo previous-runs fetched {profile.city}/{model}/{target_date}",
                    details={
                        "endpoint": "previous_runs",
                        "target_date": target_date,
                        "model": model,
                        "previous_days": lead_days,
                        "runs": len(run_ids),
                        "members": sum(len(item) for item in members),
                        "parse_statuses": parse_statuses,
                        "url": response.get("url"),
                    },
                    started_at=started,
                    finished_at=utc_now(),
                )
                if session is None and delay > 0:
                    time.sleep(delay)
        results.append(city_result)
    return {
        "ok": failures == 0,
        "source": "openmeteo",
        "endpoint": "previous_runs",
        "dry_run": dry_run,
        "cities": [profile.city for profile in profiles],
        "target_dates": targets,
        "previous_days": lead_days,
        "requests_planned": requests_planned,
        "runs_upserted": 0 if dry_run else total_runs,
        "members_upserted": 0 if dry_run else total_members,
        "failed": failures,
        "results": results,
    }


def build_openmeteo_request(
    profile: CitySettlementProfile,
    model: str,
    *,
    endpoint_url: str = OPENMETEO_FORECAST_URL,
    forecast_days: int = 7,
) -> dict[str, Any]:
    return {
        "latitude": profile.latitude,
        "longitude": profile.longitude,
        "hourly": ",".join(OPENMETEO_HOURLY_FIELDS if endpoint_url == OPENMETEO_FORECAST_URL else ("temperature_2m",)),
        "timezone": "UTC",
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
        "forecast_days": max(1, min(int(forecast_days or 7), 16)),
        "past_days": 0,
        "models": model,
    }


def previous_run_models_for_city(city_key: str) -> list[str]:
    profile = SETTLEMENT_REGISTRY.get(str(city_key or "").strip().lower())
    if not profile:
        return ["gfs_seamless", "ecmwf_ifs025"]
    if profile.region == "us":
        return ["ecmwf_ifs025", "gfs_seamless", "ncep_hrrr_conus"]
    if profile.city in {"shanghai", "beijing", "wuhan", "qingdao", "shenzhen", "hong-kong"}:
        return ["ecmwf_ifs025", "gfs_seamless", "cma_grapes_global"]
    if profile.city in {"tokyo", "seoul", "taipei"}:
        return ["ecmwf_ifs025", "gfs_seamless", "jma_seamless"]
    return ["ecmwf_ifs025", "gfs_seamless"]


def build_previous_runs_request(
    profile: CitySettlementProfile,
    model: str,
    target_date: str,
    *,
    previous_days: list[int] | tuple[int, ...] | None = None,
) -> dict[str, Any]:
    start_date, end_date = _utc_date_window_for_local_day(target_date, profile.timezone)
    fields = [f"temperature_2m_previous_day{day}" for day in _normalize_previous_days(previous_days)]
    return {
        "latitude": profile.latitude,
        "longitude": profile.longitude,
        "hourly": ",".join(fields),
        "start_date": start_date,
        "end_date": end_date,
        "timezone": "UTC",
        "temperature_unit": "celsius",
        "models": model,
    }


def openmeteo_runs_from_response(
    city_key: str,
    model: str,
    payload: dict[str, Any],
    *,
    source_url: str = "",
    retrieved_at: str | None = None,
    endpoint_kind: str = "deterministic",
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    profile = SETTLEMENT_REGISTRY.get(str(city_key or "").strip().lower())
    if not profile:
        raise ValueError("unknown_city")
    retrieved = _parse_time(retrieved_at) or _parse_time(utc_now())
    raw_hash = _stable_hash({"model": model, "endpoint": endpoint_kind, "payload": payload})
    hourly = payload.get("hourly") if isinstance(payload, dict) else None
    times = hourly.get("time") if isinstance(hourly, dict) else None
    warnings: list[str] = ["missing_model_run_time_from_openmeteo"]
    if not isinstance(times, list) or not times:
        return _failed_openmeteo_run(
            profile,
            model,
            raw_hash,
            source_url,
            retrieved,
            endpoint_kind,
            ["missing_hourly_time"],
            payload,
        )

    if endpoint_kind == "ensemble":
        member_series = _ensemble_member_series(hourly, model)
        if not member_series:
            return _failed_openmeteo_run(
                profile,
                model,
                raw_hash,
                source_url,
                retrieved,
                endpoint_kind,
                warnings + ["missing_ensemble_temperature_members"],
                payload,
            )
        return _runs_from_member_series(profile, model, member_series, times, payload, raw_hash, source_url, retrieved, endpoint_kind, warnings)

    series = _deterministic_series(hourly, model)
    if not series.get("temperature_2m"):
        return _failed_openmeteo_run(
            profile,
            model,
            raw_hash,
            source_url,
            retrieved,
            endpoint_kind,
            warnings + ["missing_hourly_temperature_2m"],
            payload,
        )
    member_series = {"deterministic": series}
    return _runs_from_member_series(profile, model, member_series, times, payload, raw_hash, source_url, retrieved, endpoint_kind, warnings)


def openmeteo_previous_runs_from_response(
    city_key: str,
    model: str,
    target_date: str,
    payload: dict[str, Any],
    *,
    previous_days: list[int] | None = None,
    source_url: str = "",
    retrieved_at: str | None = None,
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    profile = SETTLEMENT_REGISTRY.get(str(city_key or "").strip().lower())
    if not profile:
        raise ValueError("unknown_city")
    retrieved = _parse_time(retrieved_at) or _parse_time(utc_now())
    raw_hash = _stable_hash({"model": model, "endpoint": "previous_runs", "target_date": target_date, "payload": payload})
    hourly = payload.get("hourly") if isinstance(payload, dict) else None
    times = hourly.get("time") if isinstance(hourly, dict) else None
    if not isinstance(times, list) or not times:
        return _failed_openmeteo_run(
            profile,
            model,
            raw_hash,
            source_url,
            retrieved,
            "previous_runs",
            ["missing_hourly_time"],
            payload,
        )

    runs: list[dict[str, Any]] = []
    members_by_run: list[list[dict[str, Any]]] = []
    for day in _normalize_previous_days(previous_days):
        field = f"temperature_2m_previous_day{day}"
        series = hourly.get(field) if isinstance(hourly, dict) else None
        if not isinstance(series, list) or not series:
            runs.append(_failed_previous_run(profile, model, target_date, day, raw_hash, source_url, retrieved, payload, [f"missing_{field}"]))
            members_by_run.append([])
            continue
        hourly_points = _previous_run_hourly_points(profile, model, target_date, day, times, series)
        if not hourly_points:
            runs.append(_failed_previous_run(profile, model, target_date, day, raw_hash, source_url, retrieved, payload, ["no_points_for_target_local_day"]))
            members_by_run.append([])
            continue
        temps = [float(point["temperature_2m"]) for point in hourly_points if point.get("temperature_2m") is not None]
        high = max(temps)
        high_point = max(hourly_points, key=lambda item: float(item.get("temperature_2m") or -999))
        run_at = _previous_run_at(target_date, profile.timezone, day)
        run = {
            "run_key": f"openmeteo-previous:{profile.city}:{target_date}:{model}:day{day}",
            "city": profile.city,
            "target_date": target_date,
            "source": f"openmeteo_previous_{model}_day{day}",
            "provider": "open-meteo",
            "model": model,
            "model_version": "previous-runs",
            "run_type": "forecast",
            "run_at": run_at.isoformat(),
            "retrieved_at": retrieved.isoformat() if retrieved else utc_now(),
            "valid_at": str(high_point.get("valid_at") or ""),
            "horizon": f"D+{day}",
            "lead_hours": day * 24.0,
            "latitude": profile.latitude,
            "longitude": profile.longitude,
            "station_id": profile.station_id,
            "timezone": profile.timezone,
            "unit": profile.unit,
            "mean_high": high,
            "std_high": 0.0,
            "member_count": 1,
            "source_url": source_url,
            "raw_response_hash": raw_hash,
            "data_license": "open-meteo-free-api",
            "quality_flags": ["openmeteo_previous_runs", "archived_model_output", f"previous_day{day}"],
            "parser_version": OPENMETEO_PREVIOUS_PARSER_VERSION,
            "parse_status": "parsed",
            "parse_warnings": [],
            "source_unit": "C",
            "training_eligible": True,
            "ineligibility_reason": "",
            "meta": {
                "provider": "open-meteo",
                "endpoint": "previous_runs",
                "model": model,
                "previous_day": day,
                "target_local_date": target_date,
                "raw_response_hash": raw_hash,
            },
        }
        member = {
            "member_id": f"previous_day{day}",
            "member_name": f"{model} previous day {day}",
            "high_temp": high,
            "hourly": hourly_points,
            "source_unit": "C",
            "previous_day": day,
        }
        runs.append(run)
        members_by_run.append([member])
    return runs, members_by_run


def _runs_from_member_series(
    profile: CitySettlementProfile,
    model: str,
    member_series: dict[str, dict[str, list[Any]]],
    times: list[Any],
    payload: dict[str, Any],
    raw_hash: str,
    source_url: str,
    retrieved: datetime | None,
    endpoint_kind: str,
    warnings: list[str],
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    by_date: dict[str, list[dict[str, Any]]] = {}
    for member_id, series in member_series.items():
        points = _hourly_points_from_series(profile, model, member_id, series, times)
        for point in points:
            local_date = _local_date(point["valid_at"], profile.timezone)
            if local_date:
                by_date.setdefault(local_date, []).append({"member_id": member_id, **point})
    if not by_date:
        return _failed_openmeteo_run(
            profile,
            model,
            raw_hash,
            source_url,
            retrieved,
            endpoint_kind,
            warnings + ["no_points_in_local_day_windows"],
            payload,
        )

    runs: list[dict[str, Any]] = []
    members_by_run: list[list[dict[str, Any]]] = []
    for target_date in sorted(by_date):
        grouped: dict[str, list[dict[str, Any]]] = {}
        for point in by_date[target_date]:
            grouped.setdefault(point["member_id"], []).append({k: v for k, v in point.items() if k != "member_id"})
        members: list[dict[str, Any]] = []
        highs: list[float] = []
        peak_valid_at = ""
        for member_id, hourly_points in sorted(grouped.items()):
            temps = [float(point["temperature_2m"]) for point in hourly_points if point.get("temperature_2m") is not None]
            if not temps:
                continue
            high = max(temps)
            high_point = max(hourly_points, key=lambda item: float(item.get("temperature_2m") or -999))
            peak_valid_at = max(peak_valid_at, str(high_point.get("valid_at") or ""))
            highs.append(high)
            members.append({
                "member_id": member_id if endpoint_kind == "ensemble" else "deterministic",
                "member_name": f"Open-Meteo {model} {member_id}",
                "high_temp": round(high, 2),
                "hourly": hourly_points,
                "parser_version": OPENMETEO_PARSER_VERSION,
                "source_unit": "C",
                "raw_model": model,
                "endpoint": endpoint_kind,
            })
        if not highs:
            continue
        retrieved_hour = _floor_hour(retrieved)
        source = f"openmeteo_{model}" if endpoint_kind == "deterministic" else f"openmeteo_ensemble_{model}"
        meta = {
            "provider": "open-meteo",
            "endpoint": endpoint_kind,
            "model": model,
            "generationtime_ms": payload.get("generationtime_ms"),
            "inferred_run_at": _infer_run_at(model, retrieved).isoformat() if retrieved else "",
            "run_at_inferred": True,
            "retrieved_hour": retrieved_hour,
            "raw_response_hash": raw_hash,
            "temperature_storage": "converted_to_city_unit",
            "raw_temperature_unit": "C",
        }
        run = {
            "run_key": f"openmeteo:{endpoint_kind}:{profile.city}:{target_date}:{model}:{retrieved_hour}",
            "city": profile.city,
            "target_date": target_date,
            "source": source,
            "provider": "open-meteo",
            "model": model,
            "model_version": "provider_current",
            "run_type": "forecast",
            "run_at": "",
            "retrieved_at": retrieved.isoformat() if retrieved else "",
            "valid_at": peak_valid_at,
            "horizon": _horizon(profile, target_date, retrieved),
            "lead_hours": _lead_hours(retrieved, peak_valid_at),
            "latitude": profile.latitude,
            "longitude": profile.longitude,
            "station_id": profile.station_id,
            "timezone": profile.timezone,
            "unit": profile.unit,
            "mean_high": round(sum(highs) / len(highs), 2),
            "std_high": round(_population_std(highs), 3),
            "member_count": len(members),
            "source_url": source_url,
            "raw_response_hash": raw_hash,
            "data_license": "open-meteo-free-api",
            "quality_flags": ["openmeteo_model_output", "run_time_inferred"],
            "parser_version": OPENMETEO_PARSER_VERSION,
            "parse_status": "parsed",
            "parse_warnings": sorted(set(warnings)),
            "source_unit": "C",
            "training_eligible": True,
            "ineligibility_reason": "",
            "meta": meta,
            "raw_response_summary": {
                "latitude": payload.get("latitude"),
                "longitude": payload.get("longitude"),
                "timezone": payload.get("timezone"),
                "timezone_abbreviation": payload.get("timezone_abbreviation"),
                "hourly_units": payload.get("hourly_units"),
            },
        }
        runs.append(run)
        members_by_run.append(members)
    return runs, members_by_run


def _failed_openmeteo_run(
    profile: CitySettlementProfile,
    model: str,
    raw_hash: str,
    source_url: str,
    retrieved: datetime | None,
    endpoint_kind: str,
    warnings: list[str],
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    target_date = _local_date(retrieved.isoformat() if retrieved else utc_now(), profile.timezone) or ""
    retrieved_hour = _floor_hour(retrieved)
    if endpoint_kind == "deterministic":
        source = f"openmeteo_{model}"
    elif endpoint_kind == "previous_runs":
        source = f"openmeteo_previous_{model}"
    else:
        source = f"openmeteo_ensemble_{model}"
    parser_version = OPENMETEO_PREVIOUS_PARSER_VERSION if endpoint_kind == "previous_runs" else OPENMETEO_PARSER_VERSION
    run = {
        "run_key": f"openmeteo:{endpoint_kind}:{profile.city}:{target_date}:{model}:{retrieved_hour}:failed",
        "city": profile.city,
        "target_date": target_date,
        "source": source,
        "provider": "open-meteo",
        "model": model,
        "model_version": "provider_current",
        "run_type": "forecast",
        "run_at": "",
        "retrieved_at": retrieved.isoformat() if retrieved else "",
        "valid_at": "",
        "horizon": _horizon(profile, target_date, retrieved),
        "lead_hours": 0,
        "latitude": profile.latitude,
        "longitude": profile.longitude,
        "station_id": profile.station_id,
        "timezone": profile.timezone,
        "unit": profile.unit,
        "mean_high": 0,
        "std_high": 0,
        "member_count": 0,
        "source_url": source_url,
        "raw_response_hash": raw_hash,
        "data_license": "open-meteo-free-api",
        "quality_flags": ["openmeteo_model_output", "parse_failed"],
        "parser_version": parser_version,
        "parse_status": "failed",
        "parse_warnings": sorted(set(warnings)),
        "source_unit": "C",
        "training_eligible": False,
        "ineligibility_reason": "openmeteo_parse_failed",
        "meta": {
            "provider": "open-meteo",
            "endpoint": endpoint_kind,
            "model": model,
            "raw_response_hash": raw_hash,
            "payload_keys": sorted(payload.keys()) if isinstance(payload, dict) else [],
        },
    }
    return [run], [[]]


def _failed_previous_run(
    profile: CitySettlementProfile,
    model: str,
    target_date: str,
    previous_day: int,
    raw_hash: str,
    source_url: str,
    retrieved: datetime | None,
    payload: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    run_at = _previous_run_at(target_date, profile.timezone, previous_day)
    return {
        "run_key": f"openmeteo-previous:{profile.city}:{target_date}:{model}:day{previous_day}:failed",
        "city": profile.city,
        "target_date": target_date,
        "source": f"openmeteo_previous_{model}_day{previous_day}",
        "provider": "open-meteo",
        "model": model,
        "model_version": "previous-runs",
        "run_type": "forecast",
        "run_at": run_at.isoformat(),
        "retrieved_at": retrieved.isoformat() if retrieved else utc_now(),
        "valid_at": "",
        "horizon": f"D+{previous_day}",
        "lead_hours": previous_day * 24.0,
        "latitude": profile.latitude,
        "longitude": profile.longitude,
        "station_id": profile.station_id,
        "timezone": profile.timezone,
        "unit": profile.unit,
        "mean_high": 0,
        "std_high": 0,
        "member_count": 0,
        "source_url": source_url,
        "raw_response_hash": raw_hash,
        "data_license": "open-meteo-free-api",
        "quality_flags": ["openmeteo_previous_runs", "parse_failed"],
        "parser_version": OPENMETEO_PREVIOUS_PARSER_VERSION,
        "parse_status": "failed",
        "parse_warnings": sorted(set(warnings)),
        "source_unit": "C",
        "training_eligible": False,
        "ineligibility_reason": "openmeteo_previous_runs_parse_failed",
        "meta": {
            "provider": "open-meteo",
            "endpoint": "previous_runs",
            "model": model,
            "previous_day": previous_day,
            "target_local_date": target_date,
            "raw_response_hash": raw_hash,
            "payload_keys": sorted(payload.keys()) if isinstance(payload, dict) else [],
        },
    }


def _previous_run_hourly_points(
    profile: CitySettlementProfile,
    model: str,
    target_date: str,
    previous_day: int,
    times: list[Any],
    series: list[Any],
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for index, raw_time in enumerate(times):
        valid = _parse_openmeteo_time(raw_time)
        temp_c = _as_float(_at(series, index))
        if valid is None or temp_c is None:
            continue
        if _local_date(valid.isoformat(), profile.timezone) != target_date:
            continue
        points.append({
            "valid_at": valid.isoformat(),
            "temperature_2m": _convert_temperature(temp_c, "C", profile.unit),
            "temperature_2m_c": round(float(temp_c), 2),
            "source_unit": "C",
            "model": model,
            "member_id": f"previous_day{previous_day}",
            "previous_day": previous_day,
        })
    return points


def _previous_run_at(target_date: str, timezone_name: str, previous_day: int) -> datetime:
    try:
        local_date = datetime.fromisoformat(target_date).date()
        zone = ZoneInfo(timezone_name)
        local_start = datetime.combine(local_date, datetime_time.min, tzinfo=zone)
        return (local_start - timedelta(days=max(1, int(previous_day)))).astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(days=max(1, int(previous_day or 1)))


def _utc_date_window_for_local_day(target_date: str, timezone_name: str) -> tuple[str, str]:
    try:
        local_date = datetime.fromisoformat(target_date).date()
        zone = ZoneInfo(timezone_name)
        local_start = datetime.combine(local_date, datetime_time.min, tzinfo=zone)
        local_end = local_start + timedelta(days=1, seconds=-1)
        return (
            local_start.astimezone(timezone.utc).date().isoformat(),
            local_end.astimezone(timezone.utc).date().isoformat(),
        )
    except Exception:
        return target_date, target_date


def _normalize_previous_days(previous_days: list[int] | tuple[int, ...] | None) -> list[int]:
    values: list[int] = []
    for raw in previous_days or [1, 2, 3]:
        try:
            value = int(raw)
        except Exception:
            continue
        if 1 <= value <= 7 and value not in values:
            values.append(value)
    return values or [1, 2, 3]


def _deterministic_series(hourly: dict[str, Any], model: str) -> dict[str, list[Any]]:
    return {
        field: _series_for_field(hourly, field, model)
        for field in OPENMETEO_HOURLY_FIELDS
    }


def _ensemble_member_series(hourly: dict[str, Any], model: str) -> dict[str, dict[str, list[Any]]]:
    members: dict[str, dict[str, list[Any]]] = {}
    for key, values in hourly.items():
        if not str(key).startswith("temperature_2m_member"):
            continue
        member_suffix = str(key).replace("temperature_2m_", "")
        member_id = member_suffix if member_suffix.startswith("member") else f"member_{member_suffix}"
        members[member_id] = {"temperature_2m": values}
    return members


def _series_for_field(hourly: dict[str, Any], field: str, model: str) -> list[Any]:
    suffixed = f"{field}_{model}"
    value = hourly.get(suffixed)
    if isinstance(value, list):
        return value
    value = hourly.get(field)
    return value if isinstance(value, list) else []


def _hourly_points_from_series(
    profile: CitySettlementProfile,
    model: str,
    member_id: str,
    series: dict[str, list[Any]],
    times: list[Any],
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    temp_series = series.get("temperature_2m") or []
    for index, raw_time in enumerate(times):
        temp_c = _as_float(_at(temp_series, index))
        valid = _parse_openmeteo_time(raw_time)
        if temp_c is None or valid is None:
            continue
        point = {
            "valid_at": valid.isoformat(),
            "temperature_2m": _convert_temperature(temp_c, "C", profile.unit),
            "temperature_2m_c": round(temp_c, 2),
            "apparent_temperature": _convert_optional_temperature(_at(series.get("apparent_temperature") or [], index), profile.unit),
            "relative_humidity_2m": _as_float(_at(series.get("relative_humidity_2m") or [], index)),
            "dew_point_2m": _convert_optional_temperature(_at(series.get("dew_point_2m") or [], index), profile.unit),
            "cloud_cover": _as_float(_at(series.get("cloud_cover") or [], index)),
            "wind_speed_10m": _as_float(_at(series.get("wind_speed_10m") or [], index)),
            "wind_gusts_10m": _as_float(_at(series.get("wind_gusts_10m") or [], index)),
            "precipitation": _as_float(_at(series.get("precipitation") or [], index)),
            "precipitation_probability": _as_float(_at(series.get("precipitation_probability") or [], index)),
            "source_unit": "C",
            "model": model,
            "member_id": member_id,
        }
        points.append(point)
    return points


def _request_json(client: requests.Session, url: str, params: dict[str, Any]) -> dict[str, Any]:
    headers = {"User-Agent": openmeteo_user_agent()}
    attempts = 3
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            response = client.get(url, params=params, headers=headers, timeout=30)
            status_code = int(getattr(response, "status_code", 0) or 0)
            text = str(getattr(response, "text", "") or "")
            response_url = str(getattr(response, "url", "") or _preview_url(url, params))
            if status_code in {429} or status_code >= 500:
                if attempt < attempts:
                    time.sleep(5 if attempt == 1 else 15)
                    continue
            if not (200 <= status_code < 300):
                return {"ok": False, "status_code": status_code, "text": text, "url": response_url}
            try:
                payload = response.json()
            except Exception:
                payload = json.loads(text)
            return {"ok": True, "status_code": status_code, "json": payload, "url": response_url}
        except Exception as exc:
            last_error = str(exc)
            if attempt < attempts:
                time.sleep(5 if attempt == 1 else 15)
    return {"ok": False, "status_code": 0, "error": last_error, "url": _preview_url(url, params)}


def openmeteo_user_agent() -> str:
    contact = os.getenv("WEATHERBOT_CONTACT_EMAIL", "local-operator@example.com")
    return f"WeatherBot/3.5 (contact: {contact})"


def _select_profiles(cities: list[str] | None, *, limit_cities: int) -> list[CitySettlementProfile]:
    requested = [str(city or "").strip().lower() for city in (cities or []) if str(city or "").strip()]
    if requested:
        profiles = [SETTLEMENT_REGISTRY[city] for city in requested if city in SETTLEMENT_REGISTRY]
        return profiles
    profiles: list[CitySettlementProfile] = []
    seen: set[str] = set()
    for city in OPENMETEO_DEFAULT_CITY_PRIORITY:
        if city in SETTLEMENT_REGISTRY:
            profiles.append(SETTLEMENT_REGISTRY[city])
            seen.add(city)
    for city, profile in SETTLEMENT_REGISTRY.items():
        if city not in seen:
            profiles.append(profile)
            seen.add(city)
        if len(profiles) >= max(1, int(limit_cities or 5)):
            break
    return profiles[: max(1, int(limit_cities or 5))]


def _convert_optional_temperature(value: Any, target_unit: str) -> float | None:
    numeric = _as_float(value)
    if numeric is None:
        return None
    return _convert_temperature(numeric, "C", target_unit)


def _convert_temperature(value: float, source_unit: str, target_unit: str) -> float:
    source = str(source_unit or "").upper()
    target = str(target_unit or "").upper()
    if source == target:
        return round(float(value), 2)
    if source == "C" and target == "F":
        return round(float(value) * 9.0 / 5.0 + 32.0, 2)
    if source == "F" and target == "C":
        return round((float(value) - 32.0) * 5.0 / 9.0, 2)
    return round(float(value), 2)


def _local_date(value: str, timezone_name: str) -> str:
    parsed = _parse_time(value)
    if parsed is None:
        return ""
    try:
        return parsed.astimezone(ZoneInfo(timezone_name)).date().isoformat()
    except ZoneInfoNotFoundError:
        return parsed.date().isoformat()


def _parse_openmeteo_time(value: Any) -> datetime | None:
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


def _parse_time(value: Any) -> datetime | None:
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


def _floor_hour(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()


def _infer_run_at(model: str, retrieved: datetime | None) -> datetime:
    if retrieved is None:
        return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cadence = max(1, int(MODEL_UPDATE_CADENCE_HOURS.get(model, 6)))
    current = retrieved.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    aligned_hour = current.hour - (current.hour % cadence)
    return current.replace(hour=aligned_hour)


def _horizon(profile: CitySettlementProfile, target_date: str, retrieved: datetime | None) -> str:
    if retrieved is None:
        return "unknown"
    try:
        local_date = retrieved.astimezone(ZoneInfo(profile.timezone)).date()
        target = datetime.fromisoformat(target_date).date()
    except Exception:
        return "unknown"
    return f"D+{max(0, (target - local_date).days)}"


def _lead_hours(retrieved: datetime | None, valid_at: str) -> float:
    valid = _parse_time(valid_at)
    if retrieved is None or valid is None:
        return 0.0
    return round((valid - retrieved).total_seconds() / 3600.0, 3)


def _population_std(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def _stable_hash(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _at(values: list[Any], index: int) -> Any:
    return values[index] if index < len(values) else None


def _preview_url(url: str, params: dict[str, Any]) -> str:
    return f"{url}?{urlencode(params, doseq=True)}"
