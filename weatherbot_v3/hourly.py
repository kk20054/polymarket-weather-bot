from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .db import connect, init_v3_db, upsert_hourly_consensus
from .forecast_archive import TEMPERATURE_KEYS
from .registry import SETTLEMENT_REGISTRY, CitySettlementProfile

FIELD_KEYS = {
    "humidity": ("relative_humidity_2m", "humidity", "rh"),
    "cloud_cover": ("cloud_cover", "cloudcover", "cloud_cover_total"),
    "precipitation": ("precipitation", "rain", "showers"),
    "precipitation_probability": ("precipitation_probability", "precip_probability", "pop"),
    "wind_speed": ("wind_speed_10m", "windspeed_10m", "wind_speed", "wind"),
    "wind_direction": ("wind_direction_10m", "winddirection_10m", "wind_direction"),
    "pressure": ("pressure_msl", "surface_pressure", "pressure"),
    "dew_point": ("dew_point_2m", "dewpoint_2m", "dew_point"),
    "shortwave_radiation": ("shortwave_radiation", "solar_radiation"),
}

WEATHER_CODE_LABELS = {
    0: "Clear",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    80: "Rain showers",
    81: "Rain showers",
    82: "Heavy showers",
    95: "Thunderstorm",
}


def build_metar_hourly_consensus(
    cities: list[str] | None = None,
    target_date: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Aggregate persisted METAR reports into station-local hourly evidence rows."""
    init_v3_db(db_path)
    profiles = _select_profiles(cities)
    if not profiles:
        return {
            "ok": False,
            "reason": "no_supported_cities",
            "requested_cities": cities or [],
            "rows_built": 0,
            "rows_upserted": 0,
        }
    station_to_profile = {profile.station_id.upper(): profile for profile in profiles}
    with connect(db_path) as conn:
        placeholders = ",".join("?" for _ in station_to_profile)
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM metar_reports
                WHERE station_id IN ({placeholders})
                ORDER BY station_id, report_time
                """,
                tuple(station_to_profile),
            ).fetchall()
        ]

    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    skipped = 0
    for row in rows:
        profile = station_to_profile.get(str(row.get("station_id") or "").upper())
        if not profile:
            skipped += 1
            continue
        report_dt = _parse_report_time(row.get("report_time"))
        if not report_dt:
            skipped += 1
            continue
        local_dt = report_dt.astimezone(ZoneInfo(profile.timezone))
        local_date = local_dt.date().isoformat()
        if target_date and local_date != str(target_date):
            continue
        temperature = _float(row.get("temperature"))
        if temperature is None:
            skipped += 1
            continue
        local_hour = f"{local_dt.hour:02d}:00"
        key = (profile.city, local_date, local_hour)
        bucket = buckets.setdefault(
            key,
            {
                "profile": profile,
                "target_date": local_date,
                "local_hour": local_hour,
                "valid_time": local_dt.replace(minute=0, second=0, microsecond=0).isoformat(),
                "temperatures": [],
                "dew_points": [],
                "source_reports": [],
                "latest_report_time": "",
            },
        )
        bucket["temperatures"].append(temperature)
        dew_point = _float(row.get("dew_point"))
        if dew_point is not None:
            bucket["dew_points"].append(dew_point)
        bucket["source_reports"].append({
            "id": row.get("id"),
            "report_time": row.get("report_time"),
            "temperature": temperature,
            "raw_text": row.get("raw_text"),
        })
        if str(row.get("report_time") or "") > str(bucket.get("latest_report_time") or ""):
            bucket["latest_report_time"] = str(row.get("report_time") or "")

    upserted = 0
    for (city, target, hour), bucket in sorted(buckets.items()):
        profile = bucket["profile"]
        temperatures = bucket["temperatures"]
        observed_temp = max(temperatures) if temperatures else None
        upsert_hourly_consensus({
            "consensus_key": f"metar:{profile.station_id}:{target}:{hour}",
            "city": city,
            "city_name": profile.city_name,
            "target_date": target,
            "local_hour": hour,
            "valid_time": bucket["valid_time"],
            "station_id": profile.station_id,
            "observed_temp": observed_temp,
            "observation_source": "metar",
            "source_count": len(temperatures),
            "source_weights": {"metar": 1.0},
            "peak_marker": "hourly_metar_max",
            "raw_json": {
                "builder": "metar_hourly_consensus_v1",
                "unit": profile.unit,
                "latest_report_time": bucket["latest_report_time"],
                "sample_count": len(temperatures),
                "dew_point_mean": _mean(bucket["dew_points"]),
                "source_reports": bucket["source_reports"],
            },
        })
        upserted += 1

    return {
        "ok": True,
        "source": "metar_reports",
        "cities": [profile.city for profile in profiles],
        "stations": sorted(station_to_profile),
        "target_date": target_date or "",
        "reports_seen": len(rows),
        "rows_built": len(buckets),
        "rows_upserted": upserted,
        "reports_skipped": skipped,
    }


def forecast_hourly_points(
    targets: dict[str, set[str]] | None = None,
    db_path: Path | None = None,
    max_sources_per_target: int = 4,
) -> dict[str, list[dict[str, Any]]]:
    """Aggregate archived forecast member hourly data for dashboard use.

    The archive can contain repeated runs for the same city/date. For the
    dashboard we keep only the latest run for each source/provider/model so
    old snapshots do not masquerade as independent hourly evidence.
    """
    init_v3_db(db_path)
    normalized_targets = {
        str(city or "").strip().lower(): {str(date) for date in dates if date}
        for city, dates in (targets or {}).items()
        if city
    }
    with connect(db_path) as conn:
        where = ["COALESCE(run_type, 'forecast') = 'forecast'"]
        params: list[Any] = []
        if normalized_targets:
            cities = sorted(normalized_targets)
            dates = sorted({date for dates_for_city in normalized_targets.values() for date in dates_for_city})
            where.append(f"city IN ({','.join('?' for _ in cities)})")
            where.append(f"target_date IN ({','.join('?' for _ in dates)})")
            params.extend(cities)
            params.extend(dates)
        run_rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM forecast_runs
                WHERE {' AND '.join(where)}
                ORDER BY city, target_date, COALESCE(retrieved_at, run_at, created_at) DESC, id DESC
                """,
                params,
            ).fetchall()
        ]
        selected_runs: list[dict[str, Any]] = []
        source_counts: dict[tuple[str, str], int] = defaultdict(int)
        seen_source_keys: set[tuple[str, str, str, str, str]] = set()
        for run in run_rows:
            city = str(run.get("city") or "").strip().lower()
            target_date = str(run.get("target_date") or "")
            if not city or not target_date:
                continue
            if normalized_targets and target_date not in normalized_targets.get(city, set()):
                continue
            target_key = (city, target_date)
            if source_counts[target_key] >= max_sources_per_target:
                continue
            source_key = (
                city,
                target_date,
                str(run.get("source") or ""),
                str(run.get("provider") or ""),
                str(run.get("model") or ""),
            )
            if source_key in seen_source_keys:
                continue
            seen_source_keys.add(source_key)
            source_counts[target_key] += 1
            selected_runs.append(run)

        if not selected_runs:
            return {}

        run_ids = [int(run["id"]) for run in selected_runs]
        placeholders = ",".join("?" for _ in run_ids)
        member_rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM forecast_members
                WHERE run_id IN ({placeholders})
                ORDER BY run_id, member_id
                """,
                run_ids,
            ).fetchall()
        ]

    runs_by_id = {int(run["id"]): run for run in selected_runs}
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    for member in member_rows:
        run = runs_by_id.get(int(member.get("run_id") or 0))
        if not run:
            continue
        hourly = _loads(member.get("hourly_json"), [])
        if not isinstance(hourly, list):
            continue
        source = _source_label(run)
        for item in hourly:
            if not isinstance(item, dict):
                continue
            valid_at = str(item.get("valid_at") or item.get("time") or item.get("timestamp") or "").strip()
            if not valid_at:
                continue
            temp = _temperature_value(item)
            if temp is None:
                continue
            unit = str(run.get("unit") or "")
            city = str(run.get("city") or "").strip().lower()
            target_date = str(run.get("target_date") or "")
            key = (city, target_date, valid_at)
            bucket = buckets.setdefault(
                key,
                {
                    "timestamp": valid_at,
                    "target_date": target_date,
                    "city": city,
                    "values": [],
                    **{f"{field}_values": [] for field in FIELD_KEYS},
                    "condition_values": [],
                    "sources": set(),
                    "source_values": defaultdict(list),
                    "unit": unit,
                    "horizon": run.get("horizon") or "",
                },
            )
            bucket["values"].append(float(temp))
            bucket["sources"].add(source)
            bucket["source_values"][source].append(float(temp))
            for field, keys in FIELD_KEYS.items():
                value = _first_float(item, keys)
                if value is not None:
                    bucket[f"{field}_values"].append(value)
            condition = _condition_label(item)
            if condition:
                bucket["condition_values"].append(condition)

    by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (city, _target_date, _valid_at), bucket in buckets.items():
        values = bucket["values"]
        if not values:
            continue
        source_values = bucket["source_values"]
        source_parts = sorted(str(source) for source in bucket["sources"] if source)
        point = {
            "timestamp": bucket["timestamp"],
            "target_date": bucket["target_date"],
            "horizon": bucket["horizon"],
            "best": _mean(values),
            "ensemble_mean": _mean(values),
            "ensemble_std": _std(values),
            "humidity": _mean(bucket["humidity_values"]),
            "cloud_cover": _mean(bucket["cloud_cover_values"]),
            "precipitation": _mean(bucket["precipitation_values"]),
            "precipitation_probability": _mean(bucket["precipitation_probability_values"]),
            "wind_speed": _mean(bucket["wind_speed_values"]),
            "wind_direction": _circular_mean_degrees(bucket["wind_direction_values"]),
            "pressure": _mean(bucket["pressure_values"]),
            "dew_point": _mean(bucket["dew_point_values"]),
            "shortwave_radiation": _mean(bucket["shortwave_radiation_values"]),
            "condition": _mode(bucket["condition_values"]),
            "source": " + ".join(source_parts) if source_parts else "forecast_archive",
            "member_count": len(values),
            "ecmwf": _mean(_matching_source_values(source_values, "ecmwf")),
            "hrrr": _mean(_matching_source_values(source_values, "hrrr", "gfs")),
            "archive": True,
        }
        by_city[city].append(point)

    return {
        city: sorted(points, key=lambda point: str(point.get("timestamp") or ""))
        for city, points in by_city.items()
    }


def _loads(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default


def _temperature_value(item: dict[str, Any]) -> float | None:
    for key in TEMPERATURE_KEYS:
        value = _float(item.get(key))
        if value is not None:
            return value
    return None


def _first_float(item: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _float(item.get(key))
        if value is not None:
            return value
    return None


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _select_profiles(cities: list[str] | None) -> list[CitySettlementProfile]:
    if not cities:
        return list(SETTLEMENT_REGISTRY.values())
    selected: list[CitySettlementProfile] = []
    for city in cities:
        key = str(city or "").strip().lower()
        profile = SETTLEMENT_REGISTRY.get(key)
        if profile:
            selected.append(profile)
    return selected


def _parse_report_time(value: Any) -> datetime | None:
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


def _condition_label(item: dict[str, Any]) -> str | None:
    raw = item.get("condition") or item.get("weather") or item.get("weather_description")
    if raw:
        return str(raw)
    code = _first_float(item, ("weather_code", "weathercode"))
    if code is None:
        return None
    return WEATHER_CODE_LABELS.get(int(code), f"Code {int(code)}")


def _mean(values: list[float]) -> float | None:
    valid = [value for value in values if math.isfinite(value)]
    if not valid:
        return None
    return sum(valid) / len(valid)


def _mode(values: list[str]) -> str | None:
    counts: dict[str, int] = {}
    for value in values:
        if value:
            counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _circular_mean_degrees(values: list[float]) -> float | None:
    valid = [value % 360 for value in values if math.isfinite(value)]
    if not valid:
        return None
    sin_sum = sum(math.sin(math.radians(value)) for value in valid)
    cos_sum = sum(math.cos(math.radians(value)) for value in valid)
    if sin_sum == 0 and cos_sum == 0:
        return None
    return (math.degrees(math.atan2(sin_sum, cos_sum)) + 360) % 360


def _std(values: list[float]) -> float | None:
    valid = [value for value in values if math.isfinite(value)]
    if len(valid) <= 1:
        return 0.0 if valid else None
    avg = sum(valid) / len(valid)
    return math.sqrt(sum((value - avg) ** 2 for value in valid) / len(valid))


def _source_label(run: dict[str, Any]) -> str:
    return str(run.get("source") or run.get("provider") or run.get("model") or "forecast_archive")


def _matching_source_values(source_values: dict[str, list[float]], *needles: str) -> list[float]:
    values: list[float] = []
    lowered_needles = tuple(needle.lower() for needle in needles)
    for source, source_items in source_values.items():
        lower = str(source).lower()
        if any(needle in lower for needle in lowered_needles):
            values.extend(source_items)
    return values
