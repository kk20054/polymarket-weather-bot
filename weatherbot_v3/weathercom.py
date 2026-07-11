from __future__ import annotations

import hashlib
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests

from .db import insert_forecast_runs, log_data_fetch, utc_now
from .env_utils import env_value, redact_secret_text, redact_secrets
from .forecasts.ensemble import convert_temperature
from .registry import SETTLEMENT_REGISTRY, CitySettlementProfile


WEATHERCOM_FORECAST_URL = os.getenv(
    "WEATHER_COM_FORECAST_URL",
    "https://api.weather.com/v3/wx/forecast/hourly/15day",
)
WEATHERCOM_PARSER_VERSION = "weathercom-v3-hourly-forecast-v1"
WEATHERCOM_SOURCE = "weathercom_v3_forecast"
DEFAULT_USER_AGENT = "WeatherBot/2.5 (weather.com v3 forecast probe)"


def fetch_weathercom_forecasts(
    cities: list[str] | None = None,
    *,
    dry_run: bool = False,
    limit_cities: int = 5,
    forecast_days: int = 3,
    session: requests.Session | None = None,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    profiles = _select_profiles(cities, limit_cities=limit_cities)
    results = [
        fetch_weathercom_forecast_city(
            profile.city,
            dry_run=dry_run,
            forecast_days=forecast_days,
            session=session,
            retrieved_at=retrieved_at,
        )
        for profile in profiles
    ]
    hard_failures = [row for row in results if not row.get("ok") and not row.get("skipped")]
    return {
        "ok": not hard_failures,
        "source": WEATHERCOM_SOURCE,
        "dry_run": dry_run,
        "cities": len(profiles),
        "runs_upserted": sum(int(row.get("runs_upserted") or 0) for row in results),
        "members_upserted": sum(int(row.get("members_upserted") or 0) for row in results),
        "skipped": sum(1 for row in results if row.get("skipped")),
        "failed": len(hard_failures),
        "results": results,
    }


def fetch_weathercom_forecast_city(
    city: str,
    *,
    dry_run: bool = False,
    forecast_days: int = 3,
    session: requests.Session | None = None,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    profile = SETTLEMENT_REGISTRY.get(str(city or "").strip().lower())
    started = utc_now()
    started_perf = time.perf_counter()
    if not profile:
        return _logged_result(started, started_perf, city, {"ok": False, "city": city, "error": "unknown_city"})
    if not _forecast_enabled():
        payload = {"ok": True, "skipped": True, "city": profile.city, "reason": "weather_com_forecast_disabled"}
        return _logged_result(started, started_perf, profile.city, payload)
    api_key = _api_key()
    if not api_key:
        payload = {
            "ok": True,
            "skipped": True,
            "city": profile.city,
            "reason": "missing_weather_com_api_key",
            "required_env": ["WEATHER_COM_API_KEY", "WUNDERGROUND_API_KEY"],
        }
        return _logged_result(started, started_perf, profile.city, payload)

    client = session or requests.Session()
    retrieved = _parse_time(retrieved_at) or datetime.now(timezone.utc)
    params = {
        "geocode": f"{profile.latitude},{profile.longitude}",
        "format": "json",
        "units": "m",
        "language": "en-US",
        "apiKey": api_key,
    }
    source_url = f"{WEATHERCOM_FORECAST_URL}?{urlencode(params)}"
    try:
        response = client.get(
            WEATHERCOM_FORECAST_URL,
            params=params,
            timeout=25,
            headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"},
        )
        status_code = int(getattr(response, "status_code", 0) or 0)
        if not (200 <= status_code < 300):
            payload = {
                "ok": False,
                "city": profile.city,
                "reason": "weather_com_forecast_http_error",
                "status_code": status_code,
                "source_url": _strip_api_key(str(getattr(response, "url", source_url))),
                "message": str(getattr(response, "text", "") or "")[:240],
            }
            return _logged_result(started, started_perf, profile.city, payload)
        raw_payload = response.json()
        runs, members = weathercom_runs_from_response(
            profile,
            raw_payload if isinstance(raw_payload, dict) else {},
            source_url=_strip_api_key(str(getattr(response, "url", source_url))),
            retrieved_at=retrieved.isoformat(),
            forecast_days=forecast_days,
        )
        run_ids: list[int] = []
        if not dry_run:
            run_ids = insert_forecast_runs(list(zip(runs, members)))
        payload = {
            "ok": bool(runs),
            "city": profile.city,
            "source": WEATHERCOM_SOURCE,
            "runs_upserted": 0 if dry_run else len(run_ids),
            "members_upserted": 0 if dry_run else sum(len(row) for row in members),
            "planned_runs": len(runs),
            "run_ids": run_ids,
            "dry_run": dry_run,
            "source_url": _strip_api_key(str(getattr(response, "url", source_url))),
            "parse_statuses": sorted({str(run.get("parse_status") or "") for run in runs}),
        }
        if not runs:
            payload["reason"] = "weather_com_forecast_no_hourly_rows"
        return _logged_result(started, started_perf, profile.city, payload)
    except Exception as exc:
        payload = {
            "ok": False,
            "city": profile.city,
            "reason": "weather_com_forecast_exception",
            "error": str(exc),
            "source_url": _strip_api_key(source_url),
        }
        return _logged_result(started, started_perf, profile.city, payload)


def weathercom_runs_from_response(
    profile: CitySettlementProfile,
    payload: dict[str, Any],
    *,
    source_url: str = "",
    retrieved_at: str | None = None,
    forecast_days: int = 3,
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    retrieved = _parse_time(retrieved_at) or datetime.now(timezone.utc)
    raw_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:32]
    hourly = _hourly_rows_from_payload(profile, payload)
    if not hourly:
        failed = _failed_run(profile, retrieved, raw_hash, source_url, ["missing_hourly_forecast_rows"], payload)
        return [failed], [[]]

    target_dates = _target_dates_from_retrieved(profile, retrieved, forecast_days)
    runs: list[dict[str, Any]] = []
    members_by_run: list[list[dict[str, Any]]] = []
    retrieved_hour = retrieved.replace(minute=0, second=0, microsecond=0).isoformat()
    for target_date in target_dates:
        day_rows = [row for row in hourly if row.get("target_date") == target_date]
        if not day_rows:
            continue
        highs = [float(row["temperature_2m"]) for row in day_rows if _number(row.get("temperature_2m")) is not None]
        if not highs:
            continue
        high = max(highs)
        peak = max(day_rows, key=lambda row: (float(row.get("temperature_2m") or -999), str(row.get("local_hour") or "")))
        run = {
            "run_key": f"weathercom:v3:{profile.city}:{target_date}:{retrieved_hour}",
            "city": profile.city,
            "target_date": target_date,
            "source": WEATHERCOM_SOURCE,
            "provider": "weather.com",
            "model": "v3",
            "model_version": "weather.com-v3-hourly",
            "run_type": "forecast",
            # Weather.com does not expose a model-cycle timestamp. The
            # retrieval timestamp is the auditable snapshot time used for
            # ordering and as-of replay.
            "run_at": retrieved.isoformat(),
            "retrieved_at": retrieved.isoformat(),
            "valid_at": str(peak.get("valid_at") or ""),
            "horizon": _horizon(profile, target_date, retrieved),
            "lead_hours": _lead_hours(retrieved, str(peak.get("valid_at") or "")),
            "latitude": profile.latitude,
            "longitude": profile.longitude,
            "station_id": profile.station_id,
            "timezone": profile.timezone,
            "unit": profile.unit,
            "mean_high": round(high, 2),
            "std_high": 0.0,
            "member_count": 1,
            "source_url": source_url,
            "raw_response_hash": raw_hash,
            "data_license": "weather.com-api-key",
            "quality_flags": ["weathercom_forecast", "proprietary_model", "polywx_v3_candidate"],
            "parser_version": WEATHERCOM_PARSER_VERSION,
            "parse_status": "parsed",
            "parse_warnings": [],
            "source_unit": "C",
            "training_eligible": True,
            "ineligibility_reason": "",
            "meta": {
                "role": "weathercom_v3_forecast",
                "retrieved_hour": retrieved_hour,
                "raw_temperature_unit": "C",
                "temperature_storage": "converted_to_city_unit",
            },
            "raw_response_summary": {
                "hourly_rows": len(day_rows),
                "source_keys": sorted(payload.keys())[:30],
            },
        }
        member = {
            "member_id": "deterministic",
            "member_name": "Weather.com v3 hourly forecast",
            "high_temp": round(high, 2),
            "hourly": day_rows,
            "parser_version": WEATHERCOM_PARSER_VERSION,
            "source_unit": "C",
        }
        runs.append(run)
        members_by_run.append([member])
    return runs, members_by_run


def _hourly_rows_from_payload(profile: CitySettlementProfile, payload: dict[str, Any]) -> list[dict[str, Any]]:
    times = payload.get("validTimeUtc") or payload.get("validTimeLocal") or payload.get("fcstValid")
    if not isinstance(times, list):
        return []
    temps = _as_list(payload.get("temperature"))
    cloud = _as_list(payload.get("cloudCover"))
    humidity = _as_list(payload.get("relativeHumidity"))
    dew = _as_list(payload.get("temperatureDewPoint"))
    precip_chance = _as_list(payload.get("precipChance"))
    precip = _as_list(payload.get("qpf"))
    wind_speed = _as_list(payload.get("windSpeed"))
    wind_gust = _as_list(payload.get("windGust"))
    wind_dir = _as_list(payload.get("windDirection"))
    pressure = _as_list(payload.get("pressureMeanSeaLevel") or payload.get("pressureAltimeter"))
    phrase = _as_list(payload.get("wxPhraseLong") or payload.get("narrative"))
    zone = _zone(profile.timezone)
    rows: list[dict[str, Any]] = []
    for index, raw_time in enumerate(times):
        valid = _parse_forecast_time(raw_time)
        temp_c = _number(_at(temps, index))
        if valid is None or temp_c is None:
            continue
        local = valid.astimezone(zone)
        rows.append({
            "valid_at": valid.isoformat(),
            "target_date": local.date().isoformat(),
            "local_hour": local.strftime("%H:00"),
            "temperature_2m": round(convert_temperature(temp_c, "C", profile.unit), 3),
            "temperature_2m_c": round(float(temp_c), 3),
            "relative_humidity_2m": _number(_at(humidity, index)),
            "dew_point_2m": _convert_optional(_at(dew, index), "C", profile.unit),
            "dew_point_2m_c": _number(_at(dew, index)),
            "cloud_cover": _number(_at(cloud, index)),
            "precipitation_probability": _number(_at(precip_chance, index)),
            "precipitation": _number(_at(precip, index)),
            "wind_speed_10m": _number(_at(wind_speed, index)),
            "wind_gusts_10m": _number(_at(wind_gust, index)),
            "wind_direction_10m": _number(_at(wind_dir, index)),
            "pressure_msl": _number(_at(pressure, index)),
            "condition": _at(phrase, index),
            "source": WEATHERCOM_SOURCE,
        })
    return rows


def _failed_run(
    profile: CitySettlementProfile,
    retrieved: datetime,
    raw_hash: str,
    source_url: str,
    warnings: list[str],
    payload: dict[str, Any],
) -> dict[str, Any]:
    target_date = retrieved.astimezone(_zone(profile.timezone)).date().isoformat()
    retrieved_hour = retrieved.replace(minute=0, second=0, microsecond=0).isoformat()
    return {
        "run_key": f"weathercom:v3:{profile.city}:{target_date}:{retrieved_hour}:failed",
        "city": profile.city,
        "target_date": target_date,
        "source": WEATHERCOM_SOURCE,
        "provider": "weather.com",
        "model": "v3",
        "model_version": "weather.com-v3-hourly",
        "run_type": "forecast",
        "retrieved_at": retrieved.isoformat(),
        "valid_at": "",
        "station_id": profile.station_id,
        "timezone": profile.timezone,
        "unit": profile.unit,
        "mean_high": 0.0,
        "std_high": 0.0,
        "member_count": 0,
        "source_url": source_url,
        "raw_response_hash": raw_hash,
        "data_license": "weather.com-api-key",
        "quality_flags": ["weathercom_forecast", "parse_failed"],
        "parser_version": WEATHERCOM_PARSER_VERSION,
        "parse_status": "failed",
        "parse_warnings": warnings,
        "source_unit": "C",
        "training_eligible": False,
        "ineligibility_reason": "weathercom_parse_failed",
        "raw_response_summary": {"source_keys": sorted(payload.keys())[:30]},
    }


def _logged_result(started: str, started_perf: float, city: str, payload: dict[str, Any]) -> dict[str, Any]:
    payload = redact_secrets(payload)
    finished = utc_now()
    status = "OK" if payload.get("ok") else "WARN"
    log_data_fetch(
        source=WEATHERCOM_SOURCE,
        stage="refresh_forecast_runs",
        status=status,
        duration_ms=round((time.perf_counter() - started_perf) * 1000),
        city=city,
        message=str(payload.get("reason") or payload.get("error") or "weather.com forecast fetch complete"),
        details={key: value for key, value in payload.items() if key != "raw"},
        started_at=started,
        finished_at=finished,
    )
    return payload


def _forecast_enabled() -> bool:
    return str(env_value("WEATHER_COM_FORECAST_ENABLED", "true")).strip().lower() not in {"0", "false", "no", "off"}


def _api_key() -> str:
    return str(env_value("WEATHER_COM_API_KEY") or env_value("WUNDERGROUND_API_KEY") or "").strip()


def _select_profiles(cities: list[str] | None, *, limit_cities: int) -> list[CitySettlementProfile]:
    requested = {str(city or "").strip().lower() for city in (cities or []) if str(city or "").strip()}
    profiles = [profile for profile in SETTLEMENT_REGISTRY.values() if not requested or profile.city in requested]
    return profiles[: max(1, int(limit_cities or 5))]


def _target_dates_from_retrieved(profile: CitySettlementProfile, retrieved: datetime, forecast_days: int) -> list[str]:
    start = retrieved.astimezone(_zone(profile.timezone)).date()
    return [(start + timedelta(days=offset)).isoformat() for offset in range(max(1, int(forecast_days or 3)))]


def _horizon(profile: CitySettlementProfile, target_date: str, retrieved: datetime) -> str:
    local_date = retrieved.astimezone(_zone(profile.timezone)).date()
    try:
        delta = (datetime.fromisoformat(target_date).date() - local_date).days
    except Exception:
        delta = 0
    return f"d{max(0, delta)}"


def _lead_hours(retrieved: datetime, valid_at: str) -> float:
    parsed = _parse_time(valid_at)
    if parsed is None:
        return 0.0
    return round((parsed - retrieved).total_seconds() / 3600.0, 2)


def _zone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(str(value or "UTC"))
    except Exception:
        return ZoneInfo("UTC")


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _parse_forecast_time(value: Any) -> datetime | None:
    try:
        if isinstance(value, (int, float)) or str(value).strip().isdigit():
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except Exception:
        pass
    return _parse_time(value)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _at(values: list[Any], index: int) -> Any:
    return values[index] if 0 <= index < len(values) else None


def _number(value: Any) -> float | None:
    try:
        if value in (None, "", "M", "null"):
            return None
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    except Exception:
        return None


def _convert_optional(value: Any, source_unit: str, target_unit: str) -> float | None:
    numeric = _number(value)
    if numeric is None:
        return None
    return round(convert_temperature(numeric, source_unit, target_unit), 3)


def _strip_api_key(url: str) -> str:
    return redact_secret_text(url)
