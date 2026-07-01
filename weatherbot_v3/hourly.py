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


def build_hourly_consensus(
    cities: list[str] | None = None,
    target_date: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Build Layer 4 hourly rows by joining Layer 2 observations and Layer 3 forecasts."""
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
    targets = _target_map(profiles, target_date, db_path=db_path)
    forecast_points = forecast_hourly_points(targets, db_path=db_path)
    observation_points = _observation_hourly_points(profiles, target_date, db_path=db_path)
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}

    for city, points in forecast_points.items():
        for point in points:
            key = _consensus_key_from_point(point, city)
            if not key:
                continue
            bucket = buckets.setdefault(key, _empty_bucket(key))
            bucket["forecast_points"].append(point)

    for city, points in observation_points.items():
        for point in points:
            key = _consensus_key_from_point(point, city)
            if not key:
                continue
            bucket = buckets.setdefault(key, _empty_bucket(key))
            bucket["observation_points"].append(point)

    upserted = 0
    for key, bucket in sorted(buckets.items()):
        city, target, hour = key
        profile = SETTLEMENT_REGISTRY.get(city)
        if not profile:
            continue
        forecast = _combined_forecast(bucket["forecast_points"])
        observed = _combined_observation(bucket["observation_points"])
        valid_time = observed.get("timestamp") or forecast.get("timestamp") or _local_hour_iso(profile, target, hour)
        forecast_temp = forecast.get("temperature")
        observed_temp = observed.get("temperature")
        source_mix = {
            "forecast_points": len(bucket["forecast_points"]),
            "observation_points": len(bucket["observation_points"]),
            "forecast_sources": forecast.get("sources", []),
            "observation_sources": observed.get("sources", []),
        }
        warnings = []
        if forecast_temp is None:
            warnings.append("forecast_missing")
        if observed_temp is None:
            warnings.append("observation_missing")
        upsert_hourly_consensus({
            "consensus_key": f"hourly:{city}:{target}:{hour}",
            "city": city,
            "city_name": profile.city_name,
            "target_date": target,
            "local_hour": hour,
            "valid_time": valid_time,
            "station_id": observed.get("station_id") or profile.station_id,
            "forecast_temp": forecast_temp,
            "observed_temp": observed_temp,
            "observation_source": "+".join(observed.get("sources", [])) or ("forecast_only" if forecast_temp is not None else "missing"),
            "humidity": observed.get("humidity") if observed.get("humidity") is not None else forecast.get("humidity"),
            "cloud_cover": observed.get("cloud_cover") if observed.get("cloud_cover") is not None else forecast.get("cloud_cover"),
            "precipitation": forecast.get("precipitation"),
            "wind_speed": observed.get("wind_speed") if observed.get("wind_speed") is not None else forecast.get("wind_speed"),
            "wind_direction": observed.get("wind_direction") if observed.get("wind_direction") is not None else forecast.get("wind_direction"),
            "pressure": observed.get("pressure") if observed.get("pressure") is not None else forecast.get("pressure"),
            "dew_point": observed.get("dew_point") if observed.get("dew_point") is not None else forecast.get("dew_point"),
            "source_count": len(bucket["forecast_points"]) + len(bucket["observation_points"]),
            "source_weights": _source_weights(source_mix),
            "forecast_source": "+".join(forecast.get("sources", [])),
            "forecast_sources": forecast.get("sources", []),
            "observation_sources": observed.get("sources", []),
            "source_mix": source_mix,
            "consensus_version": "hourly-consensus-v2",
            "build_status": "partial" if warnings else "built",
            "build_warnings": warnings,
            "peak_marker": _peak_marker(forecast_temp, observed_temp),
            "raw_json": {
                "builder": "hourly_consensus_v2",
                "unit": profile.unit,
                "forecast": forecast,
                "observation": observed,
                "source_mix": source_mix,
            },
        })
        upserted += 1

    return {
        "ok": True,
        "source": "forecast_members+metar_reports+mesonet_observations",
        "cities": [profile.city for profile in profiles],
        "target_date": target_date or "",
        "forecast_points": sum(len(points) for points in forecast_points.values()),
        "observation_points": sum(len(points) for points in observation_points.values()),
        "rows_built": len(buckets),
        "rows_upserted": upserted,
    }


def hourly_consensus_points(
    targets: dict[str, set[str]] | None = None,
    db_path: Path | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Read persisted hourly consensus rows in the dashboard point shape."""
    init_v3_db(db_path)
    normalized_targets = {
        str(city or "").strip().lower(): {str(date) for date in dates if date}
        for city, dates in (targets or {}).items()
        if city
    }
    with connect(db_path) as conn:
        where: list[str] = []
        params: list[Any] = []
        if normalized_targets:
            cities = sorted(normalized_targets)
            dates = sorted({date for dates_for_city in normalized_targets.values() for date in dates_for_city})
            where.append(f"city IN ({','.join('?' for _ in cities)})")
            where.append(f"target_date IN ({','.join('?' for _ in dates)})")
            params.extend(cities)
            params.extend(dates)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM hourly_consensus
                {clause}
                ORDER BY city, target_date, local_hour, valid_time
                """,
                params,
            ).fetchall()
        ]

    by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        city = str(row.get("city") or "").strip().lower()
        target_date = str(row.get("target_date") or "")
        if not city or not target_date:
            continue
        if normalized_targets and target_date not in normalized_targets.get(city, set()):
            continue
        forecast_temp = _float(row.get("forecast_temp"))
        observed_temp = _float(row.get("observed_temp"))
        residual = _float(row.get("residual"))
        by_city[city].append({
            "timestamp": row.get("valid_time") or row.get("local_hour") or "",
            "target_date": target_date,
            "local_hour": row.get("local_hour"),
            "best": forecast_temp,
            "ensemble_mean": forecast_temp,
            "metar": observed_temp,
            "humidity": _float(row.get("humidity")),
            "cloud_cover": _float(row.get("cloud_cover")),
            "precipitation": _float(row.get("precipitation")),
            "wind_speed": _float(row.get("wind_speed")),
            "wind_direction": _float(row.get("wind_direction")),
            "pressure": _float(row.get("pressure")),
            "dew_point": _float(row.get("dew_point")),
            "diff": residual,
            "source": row.get("observation_source") or "hourly_consensus",
            "forecast_source": row.get("forecast_source") or "",
            "member_count": int(row.get("source_count") or 0),
            "station_id": row.get("station_id"),
            "peak_marker": row.get("peak_marker"),
            "build_status": row.get("build_status") or "",
            "hourly_consensus": True,
        })

    return {
        city: sorted(points, key=lambda point: str(point.get("timestamp") or ""))
        for city, points in by_city.items()
    }


def hourly_consensus_summary(
    city: str | None = None,
    target_date: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    init_v3_db(db_path)
    targets = {str(city).strip().lower(): {str(target_date)}} if city and target_date else None
    if city and not target_date:
        with connect(db_path) as conn:
            dates = {
                str(row["target_date"])
                for row in conn.execute(
                    "SELECT DISTINCT target_date FROM hourly_consensus WHERE city = ?",
                    (str(city).strip().lower(),),
                ).fetchall()
                if row["target_date"]
            }
        targets = {str(city).strip().lower(): dates}
    points = hourly_consensus_points(targets, db_path=db_path)
    selected = points.get(str(city).strip().lower(), []) if city else [point for rows in points.values() for point in rows]
    return {
        "ok": True,
        "city": city or "",
        "target_date": target_date or "",
        "rows": len(selected),
        "points": selected,
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


def _target_map(
    profiles: list[CitySettlementProfile],
    target_date: str | None,
    db_path: Path | None = None,
) -> dict[str, set[str]]:
    if target_date:
        return {profile.city: {str(target_date)} for profile in profiles}
    with connect(db_path) as conn:
        targets: dict[str, set[str]] = {profile.city: set() for profile in profiles}
        profile_cities = sorted(targets)
        if not profile_cities:
            return {}
        placeholders = ",".join("?" for _ in profile_cities)
        for row in conn.execute(
            f"""
            SELECT city, target_date FROM forecast_runs
            WHERE city IN ({placeholders}) AND target_date IS NOT NULL AND target_date != ''
            UNION
            SELECT city, date(report_time) AS target_date FROM metar_reports
            WHERE city IN ({placeholders}) AND report_time IS NOT NULL AND report_time != ''
            UNION
            SELECT city, date(observed_at) AS target_date FROM mesonet_observations
            WHERE city IN ({placeholders}) AND observed_at IS NOT NULL AND observed_at != ''
            """,
            tuple(profile_cities + profile_cities + profile_cities),
        ).fetchall():
            city = str(row["city"] or "").strip().lower()
            date_value = str(row["target_date"] or "").strip()
            if city in targets and date_value:
                targets[city].add(date_value)
    return targets


def _observation_hourly_points(
    profiles: list[CitySettlementProfile],
    target_date: str | None,
    db_path: Path | None = None,
) -> dict[str, list[dict[str, Any]]]:
    station_to_profile = {profile.station_id.upper(): profile for profile in profiles}
    cities = sorted(profile.city for profile in profiles)
    if not cities:
        return {}
    with connect(db_path) as conn:
        city_placeholders = ",".join("?" for _ in cities)
        metar_rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT * FROM metar_reports
                WHERE city IN ({city_placeholders})
                ORDER BY city, report_time
                """,
                tuple(cities),
            ).fetchall()
        ]
        mesonet_rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT * FROM mesonet_observations
                WHERE city IN ({city_placeholders})
                ORDER BY city, observed_at
                """,
                tuple(cities),
            ).fetchall()
        ]

    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in metar_rows:
        profile = station_to_profile.get(str(row.get("station_id") or "").upper()) or SETTLEMENT_REGISTRY.get(str(row.get("city") or ""))
        if not profile:
            continue
        point = _observation_point(
            profile,
            report_time=row.get("report_time"),
            temperature=row.get("temperature"),
            source="metar",
            station_id=row.get("station_id"),
            humidity=None,
            cloud_cover=None,
            wind_speed=row.get("wind_speed"),
            wind_direction=row.get("wind_direction"),
            pressure=row.get("pressure") or row.get("altimeter"),
            dew_point=row.get("dew_point"),
            raw=row,
        )
        _append_observation_bucket(buckets, point, target_date)

    for row in mesonet_rows:
        profile = SETTLEMENT_REGISTRY.get(str(row.get("city") or ""))
        if not profile:
            continue
        point = _observation_point(
            profile,
            report_time=row.get("observed_at"),
            temperature=row.get("temperature"),
            source=str(row.get("network") or "mesonet"),
            station_id=row.get("station_id"),
            humidity=row.get("humidity"),
            cloud_cover=None,
            wind_speed=row.get("wind_speed"),
            wind_direction=row.get("wind_direction"),
            pressure=row.get("pressure"),
            dew_point=row.get("dew_point"),
            raw=row,
        )
        _append_observation_bucket(buckets, point, target_date)

    by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (city, date_value, hour), bucket in buckets.items():
        temps = bucket["temperatures"]
        if not temps:
            continue
        by_city[city].append({
            "timestamp": bucket["valid_time"],
            "target_date": date_value,
            "local_hour": hour,
            "temperature": max(temps),
            "humidity": _mean(bucket["humidity_values"]),
            "cloud_cover": _mean(bucket["cloud_cover_values"]),
            "wind_speed": _mean(bucket["wind_speed_values"]),
            "wind_direction": _circular_mean_degrees(bucket["wind_direction_values"]),
            "pressure": _mean(bucket["pressure_values"]),
            "dew_point": _mean(bucket["dew_point_values"]),
            "sources": sorted(bucket["sources"]),
            "station_id": bucket["station_id"],
            "source_count": len(temps),
        })
    return {
        city: sorted(points, key=lambda point: str(point.get("timestamp") or ""))
        for city, points in by_city.items()
    }


def _observation_point(
    profile: CitySettlementProfile,
    *,
    report_time: Any,
    temperature: Any,
    source: str,
    station_id: Any,
    humidity: Any,
    cloud_cover: Any,
    wind_speed: Any,
    wind_direction: Any,
    pressure: Any,
    dew_point: Any,
    raw: dict[str, Any],
) -> dict[str, Any] | None:
    report_dt = _parse_report_time(report_time)
    temp = _float(temperature)
    if report_dt is None or temp is None:
        return None
    local_dt = report_dt.astimezone(ZoneInfo(profile.timezone))
    return {
        "city": profile.city,
        "target_date": local_dt.date().isoformat(),
        "local_hour": f"{local_dt.hour:02d}:00",
        "timestamp": local_dt.replace(minute=0, second=0, microsecond=0).isoformat(),
        "temperature": temp,
        "source": source,
        "station_id": str(station_id or profile.station_id).upper(),
        "humidity": _float(humidity),
        "cloud_cover": _float(cloud_cover),
        "wind_speed": _float(wind_speed),
        "wind_direction": _float(wind_direction),
        "pressure": _float(pressure),
        "dew_point": _float(dew_point),
        "raw": raw,
    }


def _append_observation_bucket(
    buckets: dict[tuple[str, str, str], dict[str, Any]],
    point: dict[str, Any] | None,
    target_date: str | None,
) -> None:
    if not point:
        return
    if target_date and point["target_date"] != str(target_date):
        return
    key = (point["city"], point["target_date"], point["local_hour"])
    bucket = buckets.setdefault(
        key,
        {
            "valid_time": point["timestamp"],
            "temperatures": [],
            "humidity_values": [],
            "cloud_cover_values": [],
            "wind_speed_values": [],
            "wind_direction_values": [],
            "pressure_values": [],
            "dew_point_values": [],
            "sources": set(),
            "station_id": point["station_id"],
        },
    )
    bucket["temperatures"].append(float(point["temperature"]))
    bucket["sources"].add(str(point["source"]))
    bucket["station_id"] = point["station_id"] or bucket["station_id"]
    for field in ("humidity", "cloud_cover", "wind_speed", "wind_direction", "pressure", "dew_point"):
        value = point.get(field)
        if value is not None:
            bucket[f"{field}_values"].append(float(value))


def _consensus_key_from_point(point: dict[str, Any], city: str) -> tuple[str, str, str] | None:
    target_date = str(point.get("target_date") or "")
    timestamp = str(point.get("timestamp") or "")
    hour = str(point.get("local_hour") or "")
    profile = SETTLEMENT_REGISTRY.get(str(city or "").strip().lower())
    if not target_date or not profile:
        return None
    if not hour and timestamp:
        parsed = _parse_report_time(timestamp)
        if parsed:
            hour = f"{parsed.astimezone(ZoneInfo(profile.timezone)).hour:02d}:00"
    if not hour:
        return None
    return (profile.city, target_date, hour)


def _empty_bucket(key: tuple[str, str, str]) -> dict[str, Any]:
    return {"key": key, "forecast_points": [], "observation_points": []}


def _combined_forecast(points: list[dict[str, Any]]) -> dict[str, Any]:
    values = [_float(point.get("best")) for point in points]
    values = [value for value in values if value is not None]
    sources = sorted({str(point.get("source") or "forecast") for point in points if point.get("source")})
    latest_timestamp = sorted((str(point.get("timestamp") or "") for point in points if point.get("timestamp")), reverse=True)
    return {
        "temperature": _mean(values),
        "timestamp": latest_timestamp[0] if latest_timestamp else "",
        "humidity": _mean([value for value in (_float(point.get("humidity")) for point in points) if value is not None]),
        "cloud_cover": _mean([value for value in (_float(point.get("cloud_cover")) for point in points) if value is not None]),
        "precipitation": _mean([value for value in (_float(point.get("precipitation")) for point in points) if value is not None]),
        "wind_speed": _mean([value for value in (_float(point.get("wind_speed")) for point in points) if value is not None]),
        "wind_direction": _circular_mean_degrees([value for value in (_float(point.get("wind_direction")) for point in points) if value is not None]),
        "pressure": _mean([value for value in (_float(point.get("pressure")) for point in points) if value is not None]),
        "dew_point": _mean([value for value in (_float(point.get("dew_point")) for point in points) if value is not None]),
        "sources": sources,
    }


def _combined_observation(points: list[dict[str, Any]]) -> dict[str, Any]:
    values = [_float(point.get("temperature")) for point in points]
    values = [value for value in values if value is not None]
    source_set: set[str] = set()
    for point in points:
        raw_sources = point.get("sources")
        if isinstance(raw_sources, list):
            source_set.update(str(source) for source in raw_sources if source)
        elif point.get("source"):
            source_set.add(str(point.get("source")))
    sources = sorted(source_set)
    latest_timestamp = sorted((str(point.get("timestamp") or "") for point in points if point.get("timestamp")), reverse=True)
    station_ids = [str(point.get("station_id") or "") for point in points if point.get("station_id")]
    return {
        "temperature": max(values) if values else None,
        "timestamp": latest_timestamp[0] if latest_timestamp else "",
        "humidity": _mean([value for value in (_float(point.get("humidity")) for point in points) if value is not None]),
        "cloud_cover": _mean([value for value in (_float(point.get("cloud_cover")) for point in points) if value is not None]),
        "wind_speed": _mean([value for value in (_float(point.get("wind_speed")) for point in points) if value is not None]),
        "wind_direction": _circular_mean_degrees([value for value in (_float(point.get("wind_direction")) for point in points) if value is not None]),
        "pressure": _mean([value for value in (_float(point.get("pressure")) for point in points) if value is not None]),
        "dew_point": _mean([value for value in (_float(point.get("dew_point")) for point in points) if value is not None]),
        "sources": sources,
        "station_id": station_ids[0] if station_ids else "",
    }


def _local_hour_iso(profile: CitySettlementProfile, target_date: str, local_hour: str) -> str:
    try:
        hour = int(str(local_hour).split(":", 1)[0])
        local_dt = datetime.fromisoformat(target_date).replace(
            hour=hour,
            minute=0,
            second=0,
            microsecond=0,
            tzinfo=ZoneInfo(profile.timezone),
        )
        return local_dt.isoformat()
    except Exception:
        return f"{target_date}T{local_hour}"


def _source_weights(source_mix: dict[str, Any]) -> dict[str, float]:
    forecast_count = int(source_mix.get("forecast_points") or 0)
    observation_count = int(source_mix.get("observation_points") or 0)
    total = max(1, forecast_count + observation_count)
    return {
        "forecast": round(forecast_count / total, 4),
        "observation": round(observation_count / total, 4),
    }


def _peak_marker(forecast_temp: float | None, observed_temp: float | None) -> str:
    if observed_temp is not None:
        return "hourly_observed_max"
    if forecast_temp is not None:
        return "forecast_only"
    return "missing"


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
