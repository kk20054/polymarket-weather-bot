from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .db import insert_forecast_run, utc_now
from .registry import SETTLEMENT_REGISTRY, CitySettlementProfile


FORECAST_PARSER_VERSION = "polywx-hourly-forecast-v1"


def ingest_polywx_forecasts(
    rows_by_city_date: dict[str, dict[str, list[dict[str, Any]]]],
    *,
    source_url: str = "",
) -> dict[str, Any]:
    run_ids: list[int] = []
    failures: list[dict[str, Any]] = []
    rows_seen = 0
    for city, by_date in rows_by_city_date.items():
        profile = SETTLEMENT_REGISTRY.get(str(city or "").strip().lower())
        if not profile:
            failures.append({"city": city, "error": "unknown_city"})
            continue
        for target_date, rows in (by_date or {}).items():
            rows_seen += len(rows or [])
            try:
                run, members = forecast_run_from_polywx_rows(
                    profile.city,
                    str(target_date),
                    rows or [],
                    source_url=source_url,
                )
                run_ids.append(insert_forecast_run(run, members))
            except Exception as exc:
                failures.append({"city": profile.city, "target_date": target_date, "error": str(exc)})
    return {
        "ok": not failures,
        "source": "polywx_forecast",
        "source_url": source_url,
        "rows_seen": rows_seen,
        "runs_upserted": len(run_ids),
        "run_ids": run_ids,
        "failures": failures,
    }


def forecast_run_from_polywx_rows(
    city: str,
    target_date: str,
    rows: list[dict[str, Any]],
    *,
    source_url: str = "",
    retrieved_at: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    profile = SETTLEMENT_REGISTRY.get(str(city or "").strip().lower())
    if not profile:
        raise ValueError("unknown_city")
    if not rows:
        raise ValueError("forecast_rows_missing")

    retrieved = _latest_time([row.get("fetched_at") for row in rows]) or _parse_time(retrieved_at) or _parse_time(utc_now())
    hourly: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_units: set[str] = set()
    for index, row in enumerate(rows, start=1):
        parsed = _hourly_point(row, profile, target_date)
        raw_units.add(parsed["raw_unit"])
        warnings.extend(f"row_{index}_{warning}" for warning in parsed["warnings"])
        if parsed["point"]:
            hourly.append(parsed["point"])

    if not hourly:
        raise ValueError("forecast_hourly_points_missing")

    temps = [float(point["temperature_2m"]) for point in hourly if point.get("temperature_2m") is not None]
    if not temps:
        raise ValueError("forecast_temperatures_missing")
    high_temp = max(temps)
    std_high = _population_std(temps)
    valid_at = _peak_valid_at(hourly) or hourly[-1]["valid_at"]
    raw_hash = _stable_hash({"city": profile.city, "target_date": target_date, "rows": rows})
    source_unit = ",".join(sorted(unit for unit in raw_units if unit)) or "unknown"
    parse_status = "parsed" if not warnings else "partial"
    quality_flags = ["polywx_forecast_xhr", "model_source_not_disclosed"]
    if retrieved is None:
        warnings.append("retrieved_at_missing")
    run = {
        "run_key": f"polywx_forecast:{profile.city}:{target_date}:{raw_hash[:16]}",
        "city": profile.city,
        "target_date": target_date,
        "source": "polywx_forecast",
        "provider": "polywx",
        "model": "polywx_hourly_forecast",
        "model_version": "undisclosed",
        "run_type": "forecast",
        "run_at": retrieved.isoformat() if retrieved else "",
        "retrieved_at": retrieved.isoformat() if retrieved else "",
        "valid_at": valid_at,
        "horizon": _horizon(profile, target_date, retrieved),
        "lead_hours": _lead_hours(retrieved, valid_at),
        "latitude": profile.latitude,
        "longitude": profile.longitude,
        "station_id": profile.station_id,
        "timezone": profile.timezone,
        "unit": profile.unit,
        "mean_high": round(high_temp, 2),
        "std_high": round(std_high, 3),
        "member_count": 1,
        "source_url": source_url,
        "raw_response_hash": raw_hash,
        "data_license": "polywx_reference_only",
        "quality_flags": quality_flags,
        "parser_version": FORECAST_PARSER_VERSION,
        "parse_status": parse_status,
        "parse_warnings": sorted(set(warnings)),
        "source_unit": source_unit,
        "training_eligible": False,
        "ineligibility_reason": "polywx_model_source_and_run_time_not_disclosed",
        "raw_rows": rows,
    }
    member = {
        "member_id": "polywx_deterministic",
        "member_name": "PolyWX hourly forecast",
        "high_temp": round(high_temp, 2),
        "hourly": hourly,
        "parser_version": FORECAST_PARSER_VERSION,
        "source_unit": source_unit,
    }
    return run, [member]


def _hourly_point(row: dict[str, Any], profile: CitySettlementProfile, target_date: str) -> dict[str, Any]:
    warnings: list[str] = []
    valid_at = _valid_time(row, profile, target_date)
    if not valid_at:
        warnings.append("missing_valid_time")
    raw_temp, raw_unit = _temperature_with_unit(row)
    temp = _convert_temperature(raw_temp, raw_unit, profile.unit)
    if temp is None:
        warnings.append("missing_temperature")
    dew_point = _convert_temperature(_as_float(row.get("dew_point_c") or row.get("dewpoint_c")), "C", profile.unit) if (
        row.get("dew_point_c") is not None or row.get("dewpoint_c") is not None
    ) else _as_float(row.get("dew_point") or row.get("dewpoint"))
    if not valid_at or temp is None:
        return {"point": None, "warnings": warnings, "raw_unit": raw_unit}
    return {
        "point": {
            "valid_at": valid_at,
            "temperature_2m": round(temp, 2),
            "relative_humidity_2m": _as_float(row.get("relative_humidity_2m") or row.get("humidity") or row.get("humidity_pct")),
            "cloud_cover": _as_float(row.get("cloud_cover") or row.get("cloud_cover_pct")),
            "precipitation": _as_float(row.get("precipitation") or row.get("precip_mm")),
            "precipitation_probability": _as_float(row.get("precipitation_probability") or row.get("precip_chance_pct")),
            "wind_speed_10m": _as_float(row.get("wind_speed_10m") or row.get("wind_kph") or row.get("wind_speed")),
            "wind_direction_10m": _as_float(row.get("wind_direction_10m") or row.get("wind_dir_deg") or row.get("wind_direction")),
            "pressure_msl": _as_float(row.get("pressure_msl") or row.get("pressure_hpa") or row.get("pressure")),
            "dew_point_2m": dew_point,
            "weather_code": row.get("weather_code") or row.get("icon_code"),
            "condition_phrase": row.get("condition_phrase") or row.get("condition"),
            "raw_unit": raw_unit,
        },
        "warnings": warnings,
        "raw_unit": raw_unit,
    }


def _valid_time(row: dict[str, Any], profile: CitySettlementProfile, target_date: str) -> str:
    raw = row.get("valid_at") or row.get("time") or row.get("timestamp") or row.get("datetime")
    parsed = _parse_time(raw)
    if parsed:
        return parsed.isoformat()
    hour = str(row.get("hour") or row.get("local_hour") or "").strip()
    if not hour:
        return ""
    try:
        hour_part, minute_part = (hour.split(":", 1) + ["0"])[:2]
        local = datetime.combine(
            datetime.fromisoformat(target_date).date(),
            time(hour=int(hour_part), minute=int(minute_part[:2] or 0)),
            tzinfo=ZoneInfo(profile.timezone),
        )
    except (ValueError, ZoneInfoNotFoundError):
        return ""
    return local.astimezone(timezone.utc).isoformat()


def _temperature_with_unit(row: dict[str, Any]) -> tuple[float | None, str]:
    for key, unit in (
        ("temperature_c", "C"),
        ("temp_c", "C"),
        ("temperature_2m_c", "C"),
        ("temperature_f", "F"),
        ("temp_f", "F"),
        ("temperature_2m_f", "F"),
        ("temperature_2m", str(row.get("unit") or row.get("raw_unit") or "")),
        ("temperature", str(row.get("unit") or row.get("raw_unit") or "")),
    ):
        value = _as_float(row.get(key))
        if value is not None:
            return value, unit.upper() or "unknown"
    return None, str(row.get("unit") or row.get("raw_unit") or "unknown")


def _convert_temperature(value: float | None, source_unit: str, target_unit: str) -> float | None:
    if value is None:
        return None
    source = str(source_unit or "").upper()
    target = str(target_unit or "").upper()
    if source == target:
        return round(value, 2)
    if source == "C" and target == "F":
        return round(value * 9.0 / 5.0 + 32.0, 2)
    if source == "F" and target == "C":
        return round((value - 32.0) * 5.0 / 9.0, 2)
    return round(value, 2)


def _latest_time(values: list[Any]) -> datetime | None:
    parsed = [_parse_time(value) for value in values]
    parsed = [value for value in parsed if value is not None]
    return max(parsed) if parsed else None


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        numeric = float(text)
        if numeric > 10_000_000_000:
            numeric /= 1000.0
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    except Exception:
        pass
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _peak_valid_at(hourly: list[dict[str, Any]]) -> str:
    if not hourly:
        return ""
    return max(hourly, key=lambda item: float(item.get("temperature_2m") or -999))["valid_at"]


def _lead_hours(retrieved: datetime | None, valid_at: str) -> float:
    valid = _parse_time(valid_at)
    if retrieved is None or valid is None:
        return 0.0
    return round((valid - retrieved).total_seconds() / 3600.0, 3)


def _horizon(profile: CitySettlementProfile, target_date: str, retrieved: datetime | None) -> str:
    if retrieved is None:
        return "unknown"
    try:
        local_date = retrieved.astimezone(ZoneInfo(profile.timezone)).date()
        target = datetime.fromisoformat(target_date).date()
    except (ValueError, ZoneInfoNotFoundError):
        return "unknown"
    return f"D+{max(0, (target - local_date).days)}"


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
