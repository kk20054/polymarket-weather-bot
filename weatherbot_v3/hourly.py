from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .db import connect, init_v3_db, upsert_hourly_consensus, upsert_hourly_consensus_rows
from .forecast_archive import TEMPERATURE_KEYS
from .forecast_time import assess_forecast_run, forecast_available_at, parse_utc
from .registry import forecast_source_matches_profile_location
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

METAR_WEATHER_RE = re.compile(
    r"^(?:[-+])?(?:VC)?(?:(?:MI|PR|BC|DR|BL|SH|TS|FZ))*"
    r"(?:DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS|TS)+$"
)

METAR_CLOUD_COVER_PCT = {
    "SKC": 0.0,
    "CLR": 0.0,
    "NSC": 0.0,
    "NCD": 0.0,
    "CAVOK": 0.0,
    "FEW": 25.0,
    "SCT": 50.0,
    "BKN": 100.0,
    "OVC": 100.0,
    "VV": 100.0,
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

HOURLY_CONSENSUS_VERSION = "hourly-consensus-v2"
HOURLY_CONSENSUS_METHOD = "median_primary_v1"
WEATHERCOM_SOURCE = "weathercom_v3_forecast"

CONUS_PRIMARY_SOURCES = (
    "openmeteo_ncep_hrrr_conus",
    "openmeteo_ncep_nbm_conus",
    "openmeteo_ecmwf_ifs025",
    "openmeteo_gfs_seamless",
    "openmeteo_icon_seamless",
)
TOKYO_PRIMARY_SOURCES = (
    "openmeteo_jma_seamless",
    "openmeteo_ecmwf_ifs025",
    "openmeteo_gfs_seamless",
    "openmeteo_icon_seamless",
)
GLOBAL_PRIMARY_SOURCES = (
    "openmeteo_ecmwf_ifs025",
    "openmeteo_gfs_seamless",
    "openmeteo_icon_seamless",
)


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
        temperature = _convert_temp(temperature, _metar_temperature_unit(row, profile), profile.unit)
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
            bucket["dew_points"].append(_convert_temp(dew_point, _metar_temperature_unit(row, profile), profile.unit))
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
    target_dates_by_city: dict[str, set[str]] | None = None,
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
    targets = _normalize_target_dates(target_dates_by_city) if target_dates_by_city else _target_map(profiles, target_date, db_path=db_path)
    forecast_points = forecast_hourly_points(targets, db_path=db_path, require_training=True)
    observation_points = _observation_hourly_points(
        profiles,
        target_date,
        db_path=db_path,
        target_dates_by_city=targets if target_dates_by_city else None,
    )
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

    rows_to_upsert: list[dict[str, Any]] = []
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
        warnings.extend(forecast.get("warnings", []))
        build_status = "fallback_only" if forecast.get("fallback_only") else ("partial" if warnings else "built")
        rows_to_upsert.append({
            "consensus_key": f"hourly:{city}:{target}:{hour}",
            "city": city,
            "city_name": profile.city_name,
            "target_date": target,
            "local_hour": hour,
            "valid_time": valid_time,
            "station_id": observed.get("station_id") or profile.station_id,
            "forecast_temp": forecast_temp,
            "observed_temp": observed_temp,
            "observation_source": observed.get("primary_source") or "+".join(observed.get("sources", [])) or ("forecast_only" if forecast_temp is not None else "missing"),
            "humidity": observed.get("humidity") if observed.get("humidity") is not None else forecast.get("humidity"),
            "cloud_cover": observed.get("cloud_cover") if observed.get("cloud_cover") is not None else forecast.get("cloud_cover"),
            "precipitation": forecast.get("precipitation"),
            "wind_speed": observed.get("wind_speed") if observed.get("wind_speed") is not None else forecast.get("wind_speed"),
            "wind_direction": observed.get("wind_direction") if observed.get("wind_direction") is not None else forecast.get("wind_direction"),
            "pressure": observed.get("pressure") if observed.get("pressure") is not None else forecast.get("pressure"),
            "dew_point": observed.get("dew_point") if observed.get("dew_point") is not None else forecast.get("dew_point"),
            "forecast_spread": forecast.get("spread"),
            "forecast_member_count": forecast.get("member_count"),
            "consensus_method": forecast.get("method") or HOURLY_CONSENSUS_METHOD,
            "source_count": len(bucket["forecast_points"]) + len(bucket["observation_points"]),
            "source_weights": _source_weights(source_mix),
            "forecast_source": forecast.get("forecast_source") or "+".join(forecast.get("sources", [])),
            "forecast_sources": forecast.get("sources", []),
            "observation_sources": observed.get("sources", []),
            "source_mix": source_mix,
            "consensus_version": HOURLY_CONSENSUS_VERSION,
            "build_status": build_status,
            "build_warnings": warnings,
            "peak_marker": _peak_marker(forecast_temp, observed_temp),
            "raw_json": {
                "builder": HOURLY_CONSENSUS_VERSION,
                "unit": profile.unit,
                "forecast": forecast,
                "observation": observed,
                "source_mix": source_mix,
            },
        })
    upserted = upsert_hourly_consensus_rows(rows_to_upsert, path=db_path)

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
        raw_payload = _loads(row.get("raw_json"), {})
        forecast_payload = raw_payload.get("forecast") if isinstance(raw_payload, dict) else {}
        observation_payload = raw_payload.get("observation") if isinstance(raw_payload, dict) else {}
        if not forecast_payload and isinstance(raw_payload, dict) and isinstance(raw_payload.get("raw_json"), dict):
            forecast_payload = raw_payload["raw_json"].get("forecast")
        if not observation_payload and isinstance(raw_payload, dict) and isinstance(raw_payload.get("raw_json"), dict):
            observation_payload = raw_payload["raw_json"].get("observation")
        source_temperatures = (
            observation_payload.get("source_temperatures")
            if isinstance(observation_payload, dict) and isinstance(observation_payload.get("source_temperatures"), dict)
            else {}
        )
        china_live_temp = _float(source_temperatures.get("china_live")) if source_temperatures else None
        historical_temp = None
        if source_temperatures:
            historical_temp = _float(source_temperatures.get("wunderground_history"))
            if historical_temp is None:
                historical_temp = _float(source_temperatures.get("open_meteo_historical"))
        pws_temp = None
        if source_temperatures:
            pws_temp = _float(
                source_temperatures.get("wunderground_pws")
                if "wunderground_pws" in source_temperatures
                else source_temperatures.get("pws")
            )
        if source_temperatures and "metar" in source_temperatures:
            metar_temp = _float(source_temperatures.get("metar"))
        elif source_temperatures:
            metar_temp = None
        else:
            metar_temp = observed_temp
        by_city[city].append({
            "timestamp": row.get("valid_time") or row.get("local_hour") or "",
            "target_date": target_date,
            "local_hour": row.get("local_hour"),
            "best": forecast_temp,
            "ensemble_mean": forecast_temp,
            "metar": metar_temp,
            "historical": historical_temp,
            "china_live": china_live_temp,
            "pws": pws_temp,
            "humidity": _float(row.get("humidity")),
            "cloud_cover": _float(row.get("cloud_cover")),
            "forecast_cloud_cover": _float(forecast_payload.get("cloud_cover")) if isinstance(forecast_payload, dict) else None,
            "visibility": _float(observation_payload.get("visibility")) if isinstance(observation_payload, dict) else None,
            "precipitation": _float(row.get("precipitation")),
            "precipitation_probability": _float(forecast_payload.get("precipitation_probability")) if isinstance(forecast_payload, dict) else None,
            "wind_speed": _float(row.get("wind_speed")),
            "wind_direction": _float(row.get("wind_direction")),
            "pressure": _float(row.get("pressure")),
            "dew_point": _float(row.get("dew_point")),
            "condition": (
                observation_payload.get("condition")
                if isinstance(observation_payload, dict) and observation_payload.get("condition")
                else (forecast_payload.get("condition") if isinstance(forecast_payload, dict) else None)
            ),
            "forecast_spread": _float(row.get("forecast_spread")),
            "consensus_method": row.get("consensus_method") or "",
            "diff": residual,
            "source": row.get("observation_source") or "hourly_consensus",
            "forecast_source": row.get("forecast_source") or "",
            "member_count": int(row.get("forecast_member_count") or row.get("source_count") or 0),
            "revision_count": int(forecast_payload.get("revision_count") or 0) if isinstance(forecast_payload, dict) else 0,
            "retrieved_at": forecast_payload.get("retrieved_at") if isinstance(forecast_payload, dict) else None,
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
    source = "hourly_consensus"
    if city and target_date and not selected:
        city_key = str(city).strip().lower()
        forecast_points = forecast_hourly_points({city_key: {str(target_date)}}, db_path=db_path)
        selected = [
            {
                **point,
                "hourly_consensus": False,
                "transient": True,
                "build_status": point.get("build_status") or "transient_forecast_members",
            }
            for point in forecast_points.get(city_key, [])
        ]
        if selected:
            source = "forecast_members_transient"
    series = source_series_summary(str(city), str(target_date), db_path=db_path) if city and target_date else {}
    peak_marker = (
        forecast_revision_peak_marker(str(city), str(target_date), db_path=db_path)
        if city and target_date
        else None
    ) or _forecast_peak_marker(series.get("forecast") or [], str(target_date or ""))
    return {
        "ok": True,
        "city": city or "",
        "target_date": target_date or "",
        "rows": len(selected),
        "source": source,
        "points": selected,
        "series": series,
        "forecast_peak_marker": peak_marker,
    }


def _forecast_peak_marker(forecast_rows: list[dict[str, Any]], target_date: str) -> dict[str, Any] | None:
    revisions = [
        {
            "local_hour": row.get("local_time") or row.get("local_hour"),
            "temperature": next(
                (row.get(key) for key in ("temperature", "ensemble_mean", "best") if row.get(key) is not None),
                None,
            ),
            "retrieved_at": row.get("retrieved_at"),
            "source": row.get("forecast_source") or row.get("source") or "forecast",
        }
        for row in forecast_rows
    ]
    return _peak_marker_from_forecast_revisions(
        revisions,
        target_date,
        method="current_forecast_peak_v1",
        lookback_hours=0,
    )


def _peak_marker_from_forecast_revisions(
    revisions: list[dict[str, Any]],
    target_date: str,
    *,
    method: str = "forecast_revision_peak_v1",
    lookback_hours: int = 72,
) -> dict[str, Any] | None:
    """Return the latest hour that reached the archive-wide maximum."""
    candidates: list[tuple[int, float, dict[str, Any]]] = []
    for row in revisions:
        value = _float(row.get("temperature"))
        local_time = str(row.get("local_time") or row.get("local_hour") or "")
        match = re.match(r"^(\d{1,2}):(\d{2})", local_time)
        if value is None or not match:
            continue
        minute = int(match.group(1)) * 60 + int(match.group(2))
        if minute < 0 or minute >= 24 * 60:
            continue
        candidates.append((minute, value, row))
    if not candidates:
        return None
    max_temp = max(value for _, value, _ in candidates)
    peak_minute, _, source_row = max(
        (row for row in candidates if abs(row[1] - max_temp) <= 1e-9),
        key=lambda row: row[0],
    )
    hour, minute = divmod(peak_minute, 60)
    retrieved_values = sorted(str(row.get("retrieved_at") or "") for _, _, row in candidates if row.get("retrieved_at"))
    return {
        "hour_float": peak_minute / 60,
        "date": str(target_date or ""),
        "local_time": f"{hour:02d}:{minute:02d}:00",
        "temperature": max_temp,
        "source_hour": f"{hour:02d}:{minute:02d}",
        "method": method,
        "tie_policy": "latest_hour_across_maximum_revisions",
        "lookback_hours": int(lookback_hours),
        "snapshot_count": len(candidates),
        "latest_retrieved_at": retrieved_values[-1] if retrieved_values else None,
        "source": str(source_row.get("source") or "forecast"),
    }


def forecast_revision_peak_marker(
    city: str,
    target_date: str,
    *,
    db_path: Path | None = None,
    lookback_hours: int = 72,
) -> dict[str, Any] | None:
    """Build the PolyWX-style 3-day forecast peak from persisted revisions."""
    city_key = str(city or "").strip().lower()
    profile = SETTLEMENT_REGISTRY.get(city_key)
    if not profile or not target_date:
        return None
    with connect(db_path) as conn:
        runs = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, retrieved_at, run_at, available_at, availability_basis,
                       created_at, source_url, training_eligible
                FROM forecast_runs
                WHERE city = ? AND target_date = ? AND source = ?
                  AND COALESCE(parse_status, 'parsed') = 'parsed'
                ORDER BY COALESCE(available_at, retrieved_at, created_at) DESC, id DESC
                LIMIT 240
                """,
                (city_key, str(target_date), WEATHERCOM_SOURCE),
            ).fetchall()
        ]
        runs = [
            row for row in runs
            if _is_training_eligible(row)
            and forecast_source_matches_profile_location(row.get("source_url"), profile)
        ]
        retrieved_times = [
            parsed for row in runs
            if (parsed := _parse_report_time(row.get("available_at") or row.get("retrieved_at") or row.get("created_at")))
        ]
        if not retrieved_times:
            return None
        latest_retrieved = max(retrieved_times)
        cutoff = latest_retrieved - timedelta(hours=max(1, int(lookback_hours or 72)))
        selected_runs = [
            row for row in runs
            if (parsed := _parse_report_time(row.get("available_at") or row.get("retrieved_at") or row.get("created_at")))
            and parsed >= cutoff
        ]
        if not selected_runs:
            return None
        run_ids = [int(row["id"]) for row in selected_runs]
        placeholders = ",".join("?" for _ in run_ids)
        members = [
            dict(row)
            for row in conn.execute(
                f"SELECT run_id, hourly_json FROM forecast_members WHERE run_id IN ({placeholders})",
                run_ids,
            ).fetchall()
        ]
    run_by_id = {int(row["id"]): row for row in selected_runs}
    revisions: list[dict[str, Any]] = []
    for member in members:
        run = run_by_id.get(int(member.get("run_id") or 0))
        if not run:
            continue
        hourly = _loads(member.get("hourly_json"), [])
        for item in hourly if isinstance(hourly, list) else []:
            if not isinstance(item, dict):
                continue
            valid_at = str(item.get("valid_at") or item.get("time") or item.get("timestamp") or "")
            parts = _forecast_local_parts(profile, str(target_date), valid_at)
            if not parts or parts[0] != str(target_date):
                continue
            temperature = _temperature_value(item)
            if temperature is None:
                continue
            revisions.append({
                "local_hour": parts[1],
                "temperature": temperature,
                "retrieved_at": run.get("available_at") or run.get("retrieved_at") or run.get("created_at"),
                "source": WEATHERCOM_SOURCE,
            })
    return _peak_marker_from_forecast_revisions(
        revisions,
        str(target_date),
        method="forecast_revision_peak_v1",
        lookback_hours=lookback_hours,
    )


def source_series_summary(
    city: str,
    target_date: str,
    *,
    db_path: Path | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return independent, native-frequency evidence series for the chart."""
    city_key = str(city or "").strip().lower()
    profile = SETTLEMENT_REGISTRY.get(city_key)
    if not profile or not target_date:
        return {}
    zone = ZoneInfo(profile.timezone)
    start_local = datetime.fromisoformat(str(target_date)).replace(tzinfo=zone)
    end_local = start_local.replace(hour=23, minute=59, second=59, microsecond=999999)
    start_utc = start_local.astimezone(timezone.utc).isoformat()
    end_utc = end_local.astimezone(timezone.utc).isoformat()
    with connect(db_path) as conn:
        metar_rows = [
            dict(row) for row in conn.execute(
                """
                SELECT * FROM metar_reports
                WHERE city = ? AND report_time >= ? AND report_time <= ?
                ORDER BY report_time
                """,
                (city_key, start_utc, end_utc),
            ).fetchall()
        ]
        mesonet_rows = [
            dict(row) for row in conn.execute(
                """
                SELECT * FROM mesonet_observations
                WHERE city = ? AND observed_at >= ? AND observed_at <= ?
                  AND network IN ('china_live', 'wunderground_pws', 'open_meteo_historical')
                ORDER BY observed_at
                """,
                (city_key, start_utc, end_utc),
            ).fetchall()
        ]
        historical_rows = [
            dict(row) for row in conn.execute(
                """
                SELECT * FROM truth_wunderground_hourly
                WHERE UPPER(icao) = ? AND date_local = ?
                ORDER BY observed_at_utc
                """,
                (str(profile.station_id or "").upper(), str(target_date)),
            ).fetchall()
        ]

    series: dict[str, list[dict[str, Any]]] = {
        "forecast": list(forecast_hourly_points({city_key: {str(target_date)}}, db_path=db_path).get(city_key) or []),
        "metar": [],
        "historical": [],
        "china_live": [],
        "pws": [],
        "historical_fallback": [],
    }
    for row in metar_rows:
        point = _observation_point(
            profile,
            report_time=row.get("report_time"),
            temperature=row.get("temperature"),
            source_unit=_metar_temperature_unit(row, profile),
            source="metar",
            station_id=row.get("station_id"),
            humidity=None,
            cloud_cover=_metar_cloud_cover_percent(row),
            wind_speed=row.get("wind_speed"),
            wind_direction=row.get("wind_direction"),
            pressure=row.get("pressure") or row.get("altimeter"),
            dew_point=row.get("dew_point"),
            visibility=row.get("visibility"),
            condition=_metar_weather_tokens(row),
            raw=row,
        )
        if point:
            series["metar"].append(_native_series_point(point, row.get("report_time"), profile))
    for row in historical_rows:
        historical_wind_speed = _float(row.get("wind_speed_kph"))
        historical_pressure = _float(row.get("pressure_hpa"))
        historical_visibility = _float(row.get("visibility_km"))
        if str(profile.unit or "").upper() == "F":
            historical_wind_speed = historical_wind_speed / 1.609344 if historical_wind_speed is not None else None
            historical_pressure = historical_pressure / 33.8638866667 if historical_pressure is not None else None
            historical_visibility = historical_visibility / 1.609344 if historical_visibility is not None else None
        point = _observation_point(
            profile,
            report_time=row.get("observed_at_utc"),
            temperature=row.get("temp_c"),
            source_unit="C",
            source="wunderground_history",
            station_id=row.get("icao"),
            humidity=row.get("humidity"),
            cloud_cover=row.get("cloud_cover_pct"),
            wind_speed=historical_wind_speed,
            wind_direction=row.get("wind_direction"),
            pressure=historical_pressure,
            dew_point=row.get("dew_point_c"),
            visibility=historical_visibility,
            condition=row.get("condition"),
            raw=row,
        )
        if point:
            series["historical"].append(_native_series_point(point, row.get("observed_at_utc"), profile))
    for row in mesonet_rows:
        network = str(row.get("network") or "")
        if network == "china_live" and city_key == "shanghai" and str(row.get("station_id") or "") != "101020600":
            # Keep legacy downtown snapshots in the audit trail, but the
            # production chart is aligned to the Pudong/ZSPD reference feed.
            continue
        point = _observation_point(
            profile,
            report_time=row.get("observed_at"),
            temperature=row.get("temperature"),
            source_unit=str(row.get("raw_unit") or "C"),
            source=network,
            station_id=row.get("station_id"),
            humidity=row.get("humidity"),
            cloud_cover=None,
            wind_speed=row.get("wind_speed"),
            wind_direction=row.get("wind_direction"),
            pressure=row.get("pressure"),
            dew_point=row.get("dew_point"),
            visibility=None,
            condition=_condition_label(row),
            raw=row,
        )
        if not point:
            continue
        key = "pws" if network == "wunderground_pws" else ("historical_fallback" if network == "open_meteo_historical" else network)
        series.setdefault(key, []).append(_native_series_point(point, row.get("observed_at"), profile))
    return series


def _native_series_point(
    point: dict[str, Any],
    report_time: Any,
    profile: CitySettlementProfile,
) -> dict[str, Any]:
    result = dict(point)
    parsed = _parse_report_time(report_time)
    if parsed is not None:
        local = parsed.astimezone(ZoneInfo(profile.timezone))
        result["timestamp"] = local.isoformat()
        result["local_time"] = local.strftime("%H:%M")
    raw = result.get("raw")
    if isinstance(raw, dict):
        result["raw_text"] = raw.get("raw_text") or raw.get("rawOb") or raw.get("raw_metar")
        result["retrieved_at"] = raw.get("fetched_at") or raw.get("updated_at") or raw.get("created_at")
    return result


def forecast_hourly_points(
    targets: dict[str, set[str]] | None = None,
    db_path: Path | None = None,
    max_sources_per_target: int = 4,
    *,
    require_training: bool = False,
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
                ORDER BY city, target_date, COALESCE(available_at, retrieved_at, created_at) DESC, id DESC
                """,
                params,
            ).fetchall()
        ]
        latest_runs: list[dict[str, Any]] = []
        seen_source_keys: set[tuple[str, str, str, str, str]] = set()
        for run in run_rows:
            city = str(run.get("city") or "").strip().lower()
            target_date = str(run.get("target_date") or "")
            if not city or not target_date:
                continue
            if normalized_targets and target_date not in normalized_targets.get(city, set()):
                continue
            source_key = (
                city,
                target_date,
                str(run.get("source") or ""),
                str(run.get("provider") or ""),
                str(run.get("model") or ""),
            )
            # Keep Weather.com snapshots so past local hours can be filled from
            # the most recent snapshot that still contained that hour. Other
            # model sources keep only their latest run.
            if source_key in seen_source_keys and str(run.get("source") or "") != WEATHERCOM_SOURCE:
                continue
            seen_source_keys.add(source_key)
            latest_runs.append(run)

        runs_by_target: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for run in latest_runs:
            runs_by_target[(str(run.get("city") or "").strip().lower(), str(run.get("target_date") or ""))].append(run)
        selected_runs: list[dict[str, Any]] = []
        for (city, _target_date), runs in runs_by_target.items():
            selected_runs.extend(
                _select_forecast_runs_for_target(
                    city,
                    runs,
                    require_training=require_training,
                )
            )

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
                ORDER BY run_id DESC, member_id
                """,
                run_ids,
            ).fetchall()
        ]

    runs_by_id = {int(run["id"]): run for run in selected_runs}
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    seen_weathercom_hours: set[tuple[str, str, str]] = set()
    weathercom_revision_counts: dict[tuple[str, str, str], int] = defaultdict(int)
    for member in member_rows:
        run = runs_by_id.get(int(member.get("run_id") or 0))
        if not run:
            continue
        hourly = _loads(member.get("hourly_json"), [])
        if not isinstance(hourly, list):
            continue
        source = _source_label(run)
        source_role = str(run.get("_forecast_role") or "primary")
        available_at, _availability_basis = forecast_available_at(run)
        for item in hourly:
            if not isinstance(item, dict):
                continue
            valid_at = str(item.get("valid_at") or item.get("time") or item.get("timestamp") or "").strip()
            if not valid_at:
                continue
            if require_training:
                point_valid_at = parse_utc(valid_at)
                if available_at is None or point_valid_at is None or available_at > point_valid_at:
                    continue
            temp = _temperature_value(item)
            if temp is None:
                continue
            unit = str(run.get("unit") or "")
            city = str(run.get("city") or "").strip().lower()
            target_date = str(run.get("target_date") or "")
            profile = SETTLEMENT_REGISTRY.get(city)
            local_parts = _forecast_local_parts(profile, target_date, valid_at)
            if not local_parts:
                continue
            local_date, local_hour, local_timestamp = local_parts
            if local_date != target_date:
                continue
            key = (city, target_date, local_hour)
            if source == WEATHERCOM_SOURCE:
                weathercom_revision_counts[key] += 1
                if key in seen_weathercom_hours:
                    continue
                seen_weathercom_hours.add(key)
            bucket = buckets.setdefault(
                key,
                {
                    "timestamp": local_timestamp,
                    "target_date": target_date,
                    "local_hour": local_hour,
                    "city": city,
                    "values": [],
                    **{f"{field}_values": [] for field in FIELD_KEYS},
                    "condition_values": [],
                    "sources": set(),
                    "primary_sources": set(),
                    "fallback_sources": set(),
                    "source_values": defaultdict(list),
                    "weathercom_values": [],
                    **{f"weathercom_{field}_values": [] for field in FIELD_KEYS},
                    "unit": unit,
                    "horizon": run.get("horizon") or "",
                    "retrieved_at": run.get("available_at") or run.get("retrieved_at") or run.get("run_at") or run.get("created_at"),
                    "roles": set(),
                },
            )
            bucket["values"].append(float(temp))
            bucket["sources"].add(source)
            bucket["roles"].add(source_role)
            if source_role == "fallback":
                bucket["fallback_sources"].add(source)
            else:
                bucket["primary_sources"].add(source)
            bucket["source_values"][source].append(float(temp))
            if source == WEATHERCOM_SOURCE:
                bucket["weathercom_values"].append(float(temp))
            for field, keys in FIELD_KEYS.items():
                value = _first_float(item, keys)
                if value is not None:
                    bucket[f"{field}_values"].append(value)
                    if source == WEATHERCOM_SOURCE:
                        bucket[f"weathercom_{field}_values"].append(value)
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
        primary_sources = sorted(str(source) for source in bucket["primary_sources"] if source)
        fallback_sources = sorted(str(source) for source in bucket["fallback_sources"] if source)
        values_sorted = sorted(float(value) for value in values if math.isfinite(float(value)))
        display_values = sorted(float(value) for value in bucket["weathercom_values"] if math.isfinite(float(value))) or values_sorted
        forecast_temp = _median(display_values)
        forecast_spread = _percentile(values_sorted, 75) - _percentile(values_sorted, 25) if values_sorted else None
        fallback_only = bool(values_sorted) and not primary_sources and bool(fallback_sources)
        point = {
            "timestamp": bucket["timestamp"],
            "target_date": bucket["target_date"],
            "local_hour": bucket["local_hour"],
            "horizon": bucket["horizon"],
            "best": forecast_temp,
            "ensemble_mean": forecast_temp,
            "ensemble_std": _std(values),
            "forecast_values": display_values,
            "forecast_spread": forecast_spread,
            "forecast_member_count": len(values_sorted),
            "forecast_source": WEATHERCOM_SOURCE if bucket["weathercom_values"] else ("polywx_fallback" if fallback_only else ("openmeteo_multi_model" if any(source.startswith("openmeteo_") for source in primary_sources) else "forecast_archive")),
            "forecast_sources": source_parts,
            "fallback_only": fallback_only,
            "warnings": ["fallback_polywx_only"] if fallback_only else [],
            "consensus_method": "weathercom_v3_display_v2" if bucket["weathercom_values"] else HOURLY_CONSENSUS_METHOD,
            "humidity": _mean(bucket["weathercom_humidity_values"]) if bucket["weathercom_humidity_values"] else _mean(bucket["humidity_values"]),
            "cloud_cover": _mean(bucket["weathercom_cloud_cover_values"]) if bucket["weathercom_cloud_cover_values"] else _mean(bucket["cloud_cover_values"]),
            "precipitation": _mean(bucket["weathercom_precipitation_values"]) if bucket["weathercom_precipitation_values"] else _mean(bucket["precipitation_values"]),
            "precipitation_probability": _mean(bucket["weathercom_precipitation_probability_values"]) if bucket["weathercom_precipitation_probability_values"] else _mean(bucket["precipitation_probability_values"]),
            "wind_speed": _mean(bucket["weathercom_wind_speed_values"]) if bucket["weathercom_wind_speed_values"] else _mean(bucket["wind_speed_values"]),
            "wind_direction": _circular_mean_degrees(bucket["weathercom_wind_direction_values"]) if bucket["weathercom_wind_direction_values"] else _circular_mean_degrees(bucket["wind_direction_values"]),
            "pressure": _mean(bucket["weathercom_pressure_values"]) if bucket["weathercom_pressure_values"] else _mean(bucket["pressure_values"]),
            "dew_point": _mean(bucket["weathercom_dew_point_values"]) if bucket["weathercom_dew_point_values"] else _mean(bucket["dew_point_values"]),
            "shortwave_radiation": _mean(bucket["shortwave_radiation_values"]),
            "condition": _mode(bucket["condition_values"]),
            "source": " + ".join(source_parts) if source_parts else "forecast_archive",
            "member_count": len(values),
            "ecmwf": _mean(_matching_source_values(source_values, "ecmwf")),
            "hrrr": _mean(_matching_source_values(source_values, "hrrr", "gfs")),
            "archive": True,
            "revision_count": weathercom_revision_counts.get((city, bucket["target_date"], bucket["local_hour"]), 0),
            "retrieved_at": bucket.get("retrieved_at"),
        }
        by_city[city].append(point)

    return {
        city: sorted(points, key=lambda point: str(point.get("timestamp") or ""))
        for city, points in by_city.items()
    }


def _select_forecast_runs_for_target(
    city: str,
    runs: list[dict[str, Any]],
    *,
    require_training: bool,
) -> list[dict[str, Any]]:
    profile = SETTLEMENT_REGISTRY.get(str(city or "").strip().lower())
    primary_sources = set(_primary_sources_for_profile(profile))
    eligible = []
    for run in runs:
        if not forecast_source_matches_profile_location(run.get("source_url"), profile):
            continue
        if require_training:
            assessment = assess_forecast_run(
                run,
                target_date=str(run.get("target_date") or ""),
                timezone_name=profile.timezone if profile else str(run.get("timezone") or "UTC"),
                require_training=True,
            )
            if not assessment["ok"]:
                continue
        elif str(run.get("parse_status") or "parsed").lower() != "parsed" or forecast_available_at(run)[0] is None:
            continue
        eligible.append(run)
    weathercom = [
        run
        for run in eligible
        if _source_label(run) == WEATHERCOM_SOURCE
    ]
    if weathercom:
        supplemental = [
            run for run in eligible
            if _source_label(run) != WEATHERCOM_SOURCE
            and any(token in _source_label(run).lower() for token in ("ecmwf", "gfs", "hrrr", "nbm", "icon", "gem", "jma", "cma"))
        ]
        return [
            *(_with_forecast_role(run, "display") for run in weathercom[:48]),
            *(_with_forecast_role(run, "supplemental") for run in supplemental[:6]),
        ]
    exact_primary = [run for run in eligible if _source_label(run) in primary_sources]
    if exact_primary:
        exact_labels = {_source_label(run) for run in exact_primary}
        supplemental = [
            run
            for run in eligible
            if _source_label(run) not in exact_labels
            and any(token in _source_label(run).lower() for token in ("ecmwf", "gfs", "hrrr", "nbm", "icon"))
        ]
        # Recent Open-Meteo refreshes can contain only the remaining UTC hours
        # for a local target day. Keep older complete model snapshots as
        # supplemental evidence so a METAR rebuild never erases populated
        # forecast hours from the dashboard.
        return [
            *(_with_forecast_role(run, "primary") for run in exact_primary),
            *(_with_forecast_role(run, "supplemental") for run in supplemental[:3]),
        ]

    openmeteo_candidates = [
        run
        for run in eligible
        if _source_label(run).startswith("openmeteo_") and not _source_label(run).startswith("openmeteo_ensemble_")
    ]
    if openmeteo_candidates:
        return [_with_forecast_role(run, "primary") for run in openmeteo_candidates]

    legacy_primary = [
        run
        for run in eligible
        if any(token in _source_label(run).lower() for token in ("ecmwf", "gfs", "hrrr", "nbm", "icon", "jma"))
    ]
    if legacy_primary:
        return [_with_forecast_role(run, "primary") for run in legacy_primary]

    polywx = [run for run in runs if _source_label(run) == "polywx_forecast"]
    if polywx:
        return [_with_forecast_role(polywx[0], "fallback")]
    return []


def _primary_sources_for_profile(profile: CitySettlementProfile | None) -> tuple[str, ...]:
    if profile and profile.region == "us":
        return CONUS_PRIMARY_SOURCES
    if profile and profile.city == "tokyo":
        return TOKYO_PRIMARY_SOURCES
    return GLOBAL_PRIMARY_SOURCES


def _is_training_eligible(run: dict[str, Any]) -> bool:
    value = run.get("training_eligible")
    if value is None:
        return _source_label(run) != "polywx_forecast"
    try:
        return bool(int(value))
    except Exception:
        return bool(value)


def _with_forecast_role(run: dict[str, Any], role: str) -> dict[str, Any]:
    copied = dict(run)
    copied["_forecast_role"] = role
    return copied


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
            UNION
            SELECT LOWER(s.city_key) AS city, w.date_local AS target_date
            FROM truth_wunderground_hourly w
            JOIN stations s ON UPPER(s.station_id) = UPPER(w.icao)
            WHERE LOWER(s.city_key) IN ({placeholders}) AND w.date_local IS NOT NULL AND w.date_local != ''
            """,
            tuple(profile_cities + profile_cities + profile_cities + profile_cities),
        ).fetchall():
            city = str(row["city"] or "").strip().lower()
            date_value = str(row["target_date"] or "").strip()
            if city in targets and date_value:
                targets[city].add(date_value)
    return targets


def _normalize_target_dates(target_dates_by_city: dict[str, set[str]] | None) -> dict[str, set[str]]:
    return {
        str(city or "").strip().lower(): {str(date) for date in dates if date}
        for city, dates in (target_dates_by_city or {}).items()
        if city
    }


def _observation_hourly_points(
    profiles: list[CitySettlementProfile],
    target_date: str | None,
    db_path: Path | None = None,
    target_dates_by_city: dict[str, set[str]] | None = None,
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
        station_placeholders = ",".join("?" for _ in station_to_profile)
        wunderground_rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM truth_wunderground_hourly
                WHERE icao IN ({station_placeholders})
                ORDER BY icao, observed_at_utc
                """,
                tuple(station_to_profile),
            ).fetchall()
        ] if station_to_profile else []

    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in metar_rows:
        profile = station_to_profile.get(str(row.get("station_id") or "").upper()) or SETTLEMENT_REGISTRY.get(str(row.get("city") or ""))
        if not profile:
            continue
        point = _observation_point(
            profile,
            report_time=row.get("report_time"),
            temperature=row.get("temperature"),
            source_unit=_metar_temperature_unit(row, profile),
            source="metar",
            station_id=row.get("station_id"),
            humidity=None,
            cloud_cover=_metar_cloud_cover_percent(row),
            wind_speed=row.get("wind_speed"),
            wind_direction=row.get("wind_direction"),
            pressure=row.get("pressure") or row.get("altimeter"),
            dew_point=row.get("dew_point"),
            visibility=row.get("visibility"),
            condition=_metar_weather_tokens(row),
            raw=row,
        )
        _append_observation_bucket(buckets, point, target_date, target_dates_by_city)

    for row in mesonet_rows:
        profile = SETTLEMENT_REGISTRY.get(str(row.get("city") or ""))
        if not profile:
            continue
        point = _observation_point(
            profile,
            report_time=row.get("observed_at"),
            temperature=row.get("temperature"),
            source_unit=profile.unit,
            source=str(row.get("network") or "mesonet"),
            station_id=row.get("station_id"),
            humidity=row.get("humidity"),
            cloud_cover=None,
            wind_speed=row.get("wind_speed"),
            wind_direction=row.get("wind_direction"),
            pressure=row.get("pressure"),
            dew_point=row.get("dew_point"),
            visibility=None,
            condition=_condition_label(row),
            raw=row,
        )
        _append_observation_bucket(buckets, point, target_date, target_dates_by_city)

    for row in wunderground_rows:
        profile = station_to_profile.get(str(row.get("icao") or "").upper())
        if not profile:
            continue
        point = _observation_point(
            profile,
            report_time=row.get("observed_at_utc"),
            temperature=row.get("temp_c"),
            source_unit="C",
            source="wunderground_history",
            station_id=row.get("icao"),
            humidity=row.get("humidity"),
            cloud_cover=row.get("cloud_cover_pct"),
            wind_speed=row.get("wind_speed_kph"),
            wind_direction=row.get("wind_direction"),
            pressure=row.get("pressure_hpa"),
            dew_point=row.get("dew_point_c"),
            visibility=row.get("visibility_km"),
            condition=row.get("condition"),
            raw=row,
        )
        _append_observation_bucket(buckets, point, target_date, target_dates_by_city)

    by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (city, date_value, hour), bucket in buckets.items():
        latest_point = bucket.get("latest_point") or {}
        latest_by_source = bucket.get("latest_by_source") or {}
        if not latest_point:
            continue
        source_temperatures = {
            source: float(point["temperature"])
            for source, point in sorted(latest_by_source.items())
            if _float(point.get("temperature")) is not None
        }
        primary_point = latest_by_source.get("metar") or latest_point
        primary_source = "metar" if "metar" in latest_by_source else ("mesonet_other" if latest_by_source else "")
        by_city[city].append({
            "timestamp": bucket["valid_time"],
            "target_date": date_value,
            "local_hour": hour,
            "temperature": _float(primary_point.get("temperature")),
            "humidity": _float(primary_point.get("humidity")),
            "cloud_cover": _float(primary_point.get("cloud_cover")),
            "wind_speed": _float(primary_point.get("wind_speed")),
            "wind_direction": _float(primary_point.get("wind_direction")),
            "pressure": _float(primary_point.get("pressure")),
            "dew_point": _float(primary_point.get("dew_point")),
            "visibility": _float(primary_point.get("visibility")),
            "condition": primary_point.get("condition"),
            "sources": sorted(bucket["sources"]),
            "source": primary_source,
            "source_temperatures": source_temperatures,
            "station_id": primary_point.get("station_id") or bucket["station_id"],
            "source_count": bucket["source_count"],
            "report_time_utc": primary_point.get("report_time_utc"),
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
    source_unit: Any,
    source: str,
    station_id: Any,
    humidity: Any,
    cloud_cover: Any,
    wind_speed: Any,
    wind_direction: Any,
    pressure: Any,
    dew_point: Any,
    visibility: Any,
    condition: Any,
    raw: dict[str, Any],
) -> dict[str, Any] | None:
    report_dt = _parse_report_time(report_time)
    temp = _float(temperature)
    if report_dt is None or temp is None:
        return None
    temp = _convert_temp(temp, str(source_unit or profile.unit), profile.unit)
    dew_point_value = _float(dew_point)
    if dew_point_value is not None:
        dew_point_value = _convert_temp(dew_point_value, str(source_unit or profile.unit), profile.unit)
    local_dt = report_dt.astimezone(ZoneInfo(profile.timezone))
    return {
        "city": profile.city,
        "target_date": local_dt.date().isoformat(),
        "local_hour": f"{local_dt.hour:02d}:00",
        "timestamp": local_dt.replace(minute=0, second=0, microsecond=0).isoformat(),
        "report_time_utc": report_dt.isoformat(),
        "temperature": temp,
        "source": source,
        "station_id": str(station_id or profile.station_id).upper(),
        "humidity": _float(humidity),
        "cloud_cover": _float(cloud_cover),
        "wind_speed": _float(wind_speed),
        "wind_direction": _float(wind_direction),
        "pressure": _float(pressure),
        "dew_point": dew_point_value,
        "visibility": _float(visibility),
        "condition": str(condition) if condition else None,
        "raw": raw,
    }


def _append_observation_bucket(
    buckets: dict[tuple[str, str, str], dict[str, Any]],
    point: dict[str, Any] | None,
    target_date: str | None,
    target_dates_by_city: dict[str, set[str]] | None = None,
) -> None:
    if not point:
        return
    if target_date and point["target_date"] != str(target_date):
        return
    if target_dates_by_city and point["target_date"] not in target_dates_by_city.get(str(point["city"]), set()):
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
            "visibility_values": [],
            "condition_values": [],
            "sources": set(),
            "latest_by_source": {},
            "latest_point": None,
            "source_count": 0,
            "station_id": point["station_id"],
        },
    )
    bucket["sources"].add(str(point["source"]))
    bucket["source_count"] += 1
    bucket["station_id"] = point["station_id"] or bucket["station_id"]
    source = str(point["source"])
    current_source_point = bucket["latest_by_source"].get(source)
    if current_source_point is None or _observation_sort_key(point) >= _observation_sort_key(current_source_point):
        bucket["latest_by_source"][source] = point
    if bucket["latest_point"] is None or _observation_sort_key(point) >= _observation_sort_key(bucket["latest_point"]):
        bucket["latest_point"] = point
    for field in ("humidity", "cloud_cover", "wind_speed", "wind_direction", "pressure", "dew_point", "visibility"):
        value = point.get(field)
        if value is not None:
            bucket[f"{field}_values"].append(float(value))
    condition = point.get("condition")
    if condition:
        bucket["condition_values"].append(str(condition))


def _observation_sort_key(point: dict[str, Any]) -> datetime:
    parsed = _parse_report_time(point.get("report_time_utc") or point.get("timestamp"))
    if parsed is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed


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
    values: list[float] = []
    sources: set[str] = set()
    fallback_only = False
    warnings: list[str] = []
    forecast_source = ""
    for point in points:
        point_values = point.get("forecast_values")
        if isinstance(point_values, list):
            values.extend(float(value) for value in point_values if _float(value) is not None)
        else:
            value = _float(point.get("best"))
            if value is not None:
                values.append(value)
        point_sources = point.get("forecast_sources")
        if isinstance(point_sources, list):
            sources.update(str(source) for source in point_sources if source)
        elif point.get("source"):
            sources.add(str(point.get("source")))
        fallback_only = fallback_only or bool(point.get("fallback_only"))
        warnings.extend(str(item) for item in point.get("warnings", []) if item)
        if point.get("forecast_source"):
            forecast_source = str(point.get("forecast_source"))
    values = [value for value in values if math.isfinite(value)]
    sources_sorted = sorted(sources)
    latest_timestamp = sorted((str(point.get("timestamp") or "") for point in points if point.get("timestamp")), reverse=True)
    spread_values = [_float(point.get("forecast_spread")) for point in points]
    spread_values = [value for value in spread_values if value is not None]
    spread = _mean(spread_values)
    return {
        "temperature": _median(values),
        "spread": spread if spread is not None else (_percentile(values, 75) - _percentile(values, 25) if values else None),
        "member_count": len(values),
        "method": "weathercom_v3_display_v2" if WEATHERCOM_SOURCE in sources_sorted else HOURLY_CONSENSUS_METHOD,
        "forecast_source": forecast_source or ("polywx_fallback" if fallback_only else ("openmeteo_multi_model" if any(source.startswith("openmeteo_") for source in sources_sorted) else "forecast_archive")),
        "fallback_only": fallback_only,
        "warnings": sorted(set(warnings)),
        "timestamp": latest_timestamp[0] if latest_timestamp else "",
        "humidity": _mean([value for value in (_float(point.get("humidity")) for point in points) if value is not None]),
        "cloud_cover": _mean([value for value in (_float(point.get("cloud_cover")) for point in points) if value is not None]),
        "precipitation": _mean([value for value in (_float(point.get("precipitation")) for point in points) if value is not None]),
        "precipitation_probability": _mean([value for value in (_float(point.get("precipitation_probability")) for point in points) if value is not None]),
        "wind_speed": _mean([value for value in (_float(point.get("wind_speed")) for point in points) if value is not None]),
        "wind_direction": _circular_mean_degrees([value for value in (_float(point.get("wind_direction")) for point in points) if value is not None]),
        "pressure": _mean([value for value in (_float(point.get("pressure")) for point in points) if value is not None]),
        "dew_point": _mean([value for value in (_float(point.get("dew_point")) for point in points) if value is not None]),
        "condition": _mode([str(point.get("condition")) for point in points if point.get("condition")]),
        "revision_count": max((int(point.get("revision_count") or 0) for point in points), default=0),
        "retrieved_at": max((str(point.get("retrieved_at") or "") for point in points), default=""),
        "sources": sources_sorted,
    }


def _combined_observation(points: list[dict[str, Any]]) -> dict[str, Any]:
    values = [_float(point.get("temperature")) for point in points]
    values = [value for value in values if value is not None]
    source_set: set[str] = set()
    source_temperature_values: dict[str, tuple[datetime, float]] = {}
    latest_point: dict[str, Any] | None = None
    for point in points:
        if latest_point is None or _observation_sort_key(point) >= _observation_sort_key(latest_point):
            latest_point = point
        raw_sources = point.get("sources")
        if isinstance(raw_sources, list):
            source_set.update(str(source) for source in raw_sources if source)
        elif point.get("source"):
            source_set.add(str(point.get("source")))
        raw_source_temperatures = point.get("source_temperatures")
        if isinstance(raw_source_temperatures, dict):
            for source, value in raw_source_temperatures.items():
                numeric = _float(value)
                if numeric is not None:
                    sort_key = _observation_sort_key(point)
                    existing = source_temperature_values.get(str(source))
                    if existing is None or sort_key >= existing[0]:
                        source_temperature_values[str(source)] = (sort_key, numeric)
        elif point.get("source"):
            numeric = _float(point.get("temperature"))
            if numeric is not None:
                sort_key = _observation_sort_key(point)
                existing = source_temperature_values.get(str(point.get("source")))
                if existing is None or sort_key >= existing[0]:
                    source_temperature_values[str(point.get("source"))] = (sort_key, numeric)
    sources = sorted(source_set)
    latest_timestamp = sorted((str(point.get("timestamp") or "") for point in points if point.get("timestamp")), reverse=True)
    station_ids = [str(point.get("station_id") or "") for point in points if point.get("station_id")]
    source_temperatures = {
        source: value
        for source, (_timestamp, value) in sorted(source_temperature_values.items())
    }
    if "metar" in source_temperatures:
        primary_temperature = source_temperatures["metar"]
    elif source_temperatures:
        primary_temperature = _float(latest_point.get("temperature")) if latest_point else None
    else:
        primary_temperature = _float(latest_point.get("temperature")) if latest_point else (values[-1] if values else None)
    if "metar" in source_temperatures:
        primary_source = "metar"
    elif source_temperatures:
        primary_source = "mesonet_other"
    else:
        primary_source = ""
    return {
        "temperature": primary_temperature,
        "timestamp": latest_timestamp[0] if latest_timestamp else "",
        "humidity": _mean([value for value in (_float(point.get("humidity")) for point in points) if value is not None]),
        "cloud_cover": _mean([value for value in (_float(point.get("cloud_cover")) for point in points) if value is not None]),
        "wind_speed": _mean([value for value in (_float(point.get("wind_speed")) for point in points) if value is not None]),
        "wind_direction": _circular_mean_degrees([value for value in (_float(point.get("wind_direction")) for point in points) if value is not None]),
        "pressure": _mean([value for value in (_float(point.get("pressure")) for point in points) if value is not None]),
        "dew_point": _mean([value for value in (_float(point.get("dew_point")) for point in points) if value is not None]),
        "visibility": _mean([value for value in (_float(point.get("visibility")) for point in points) if value is not None]),
        "condition": _mode([str(point.get("condition")) for point in points if point.get("condition")]),
        "sources": sources,
        "primary_source": primary_source,
        "source_temperatures": source_temperatures,
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


def _forecast_local_parts(
    profile: CitySettlementProfile | None,
    target_date: str,
    valid_at: str,
) -> tuple[str, str, str] | None:
    """Return station-local date/hour for a forecast timestamp.

    Open-Meteo rows fetched with an explicit timezone may persist naive local
    timestamps, while newer collector rows can persist UTC offsets. Treat naive
    forecast timestamps as station-local so historical 24h forecast snapshots
    do not slide into the previous local day.
    """
    if not profile:
        return None
    text = str(valid_at or "").strip()
    if not text:
        return None
    zone = ZoneInfo(profile.timezone)
    tail = text[10:]
    has_timezone = text.endswith("Z") or "+" in tail or "-" in tail
    try:
        if has_timezone:
            parsed = _parse_report_time(text)
            if parsed is None:
                return None
            local_dt = parsed.astimezone(zone)
        else:
            local_dt = datetime.fromisoformat(text).replace(tzinfo=zone)
    except Exception:
        return None
    return (
        local_dt.date().isoformat(),
        f"{local_dt.hour:02d}:00",
        local_dt.replace(minute=0, second=0, microsecond=0).isoformat(),
    )


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


def _median(values: list[float]) -> float | None:
    valid = sorted(value for value in values if math.isfinite(value))
    if not valid:
        return None
    mid = len(valid) // 2
    if len(valid) % 2:
        return valid[mid]
    return (valid[mid - 1] + valid[mid]) / 2.0


def _percentile(values: list[float], percentile: float) -> float:
    valid = sorted(value for value in values if math.isfinite(value))
    if not valid:
        return 0.0
    if len(valid) == 1:
        return valid[0]
    rank = (len(valid) - 1) * (float(percentile) / 100.0)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return valid[int(rank)]
    fraction = rank - low
    return valid[low] * (1.0 - fraction) + valid[high] * fraction


def _mode(values: list[str]) -> str | None:
    counts: dict[str, int] = {}
    for value in values:
        if value:
            counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _metar_weather_tokens(row: dict[str, Any]) -> str | None:
    raw = str(row.get("raw_text") or "").strip()
    if not raw:
        return None
    tokens = []
    for token in raw.split():
        cleaned = token.strip().upper()
        if METAR_WEATHER_RE.match(cleaned):
            tokens.append(cleaned)
    return " ".join(tokens) if tokens else None


def _metar_cloud_cover_percent(row: dict[str, Any]) -> float | None:
    layers = _loads(row.get("cloud_layers_json"), [])
    cover_codes: list[str] = []
    if isinstance(layers, list):
        for layer in layers:
            if isinstance(layer, dict):
                cover = layer.get("cover") or layer.get("coverage") or layer.get("skyc") or layer.get("sky_cover")
                if cover:
                    cover_codes.append(str(cover).upper())
            elif layer:
                cover_codes.append(str(layer).upper())
    raw = str(row.get("raw_text") or "").upper()
    for code in METAR_CLOUD_COVER_PCT:
        if code in {"CAVOK", "SKC", "CLR", "NSC", "NCD"} and re.search(rf"\b{code}\b", raw):
            cover_codes.append(code)
        elif re.search(rf"\b{code}\d{{3}}\b", raw):
            cover_codes.append(code)
    values = [METAR_CLOUD_COVER_PCT[code] for code in cover_codes if code in METAR_CLOUD_COVER_PCT]
    return max(values) if values else None


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


def _metar_temperature_unit(row: dict[str, Any], profile: CitySettlementProfile) -> str:
    parser_version = str(row.get("parser_version") or "").lower()
    if parser_version.startswith("iem-asos-csv"):
        return "C"
    raw = _loads(row.get("raw_json"), {})
    if isinstance(raw, dict):
        unit = raw.get("normalized_temperature_unit") or raw.get("temperature_unit")
        if unit:
            return str(unit)
        payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
        unit = payload.get("normalized_temperature_unit") if isinstance(payload, dict) else None
        if unit:
            return str(unit)
    return profile.unit


def _convert_temp(value: float, source_unit: str, target_unit: str) -> float:
    source = str(source_unit or "").strip().upper()
    target = str(target_unit or "").strip().upper()
    if source.startswith(target[:1]):
        return float(value)
    if source.startswith("C") and target.startswith("F"):
        return float(value) * 9.0 / 5.0 + 32.0
    if source.startswith("F") and target.startswith("C"):
        return (float(value) - 32.0) * 5.0 / 9.0
    return float(value)


def _matching_source_values(source_values: dict[str, list[float]], *needles: str) -> list[float]:
    values: list[float] = []
    lowered_needles = tuple(needle.lower() for needle in needles)
    for source, source_items in source_values.items():
        lower = str(source).lower()
        if any(needle in lower for needle in lowered_needles):
            values.extend(source_items)
    return values
