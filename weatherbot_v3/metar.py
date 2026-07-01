from __future__ import annotations

import os
import hashlib
from typing import Any

import requests

from .db import upsert_metar_report
from .registry import SETTLEMENT_REGISTRY, CitySettlementProfile


AWC_METAR_URL = os.getenv("AVIATION_WEATHER_METAR_URL", "https://aviationweather.gov/api/data/metar")
USER_AGENT = os.getenv("WEATHERBOT_USER_AGENT", "WeatherBot/0.1 local research")


def refresh_metar_reports(
    cities: list[str] | None = None,
    *,
    hours: float = 24.0,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    selected = _select_profiles(cities)
    if not selected:
        return {
            "ok": False,
            "reason": "no_supported_cities",
            "requested_cities": cities or [],
            "stations": [],
            "reports_fetched": 0,
            "reports_upserted": 0,
            "failures": [],
        }

    station_to_profile = {profile.station_id.upper(): profile for profile in selected}
    raw_reports = fetch_awc_metars(sorted(station_to_profile), hours=hours, session=session)
    upserted = 0
    skipped = 0
    failures: list[dict[str, Any]] = []
    for item in raw_reports:
        station_id = str(item.get("stationId") or item.get("icaoId") or item.get("station_id") or "").upper()
        profile = station_to_profile.get(station_id)
        if not profile:
            skipped += 1
            continue
        try:
            upsert_metar_report(metar_report_from_awc(item, profile))
            upserted += 1
        except Exception as exc:
            failures.append({
                "station_id": station_id,
                "error": str(exc),
                "raw_text": item.get("rawOb") or item.get("raw_text") or "",
            })

    return {
        "ok": not failures,
        "source": "aviationweather.gov",
        "endpoint": AWC_METAR_URL,
        "cities": [profile.city for profile in selected],
        "stations": sorted(station_to_profile),
        "hours": max(1.0, min(float(hours or 24.0), 96.0)),
        "reports_fetched": len(raw_reports),
        "reports_upserted": upserted,
        "reports_skipped": skipped,
        "failures": failures,
    }


def fetch_awc_metars(
    station_ids: list[str],
    *,
    hours: float = 24.0,
    session: requests.Session | None = None,
    timeout: float = 20.0,
) -> list[dict[str, Any]]:
    ids = sorted({str(item or "").strip().upper() for item in station_ids if str(item or "").strip()})
    if not ids:
        return []
    bounded_hours = max(1.0, min(float(hours or 24.0), 96.0))
    client = session or requests.Session()
    response = client.get(
        AWC_METAR_URL,
        params={"ids": ",".join(ids), "format": "json", "hours": bounded_hours},
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    if response.status_code == 204:
        return []
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return [item for item in payload["data"] if isinstance(item, dict)]
    return []


def metar_report_from_awc(item: dict[str, Any], profile: CitySettlementProfile) -> dict[str, Any]:
    raw_temp_c = _as_float(item.get("temp"))
    raw_dew_c = _as_float(item.get("dewp"))
    return {
        "report_key": _report_key(item),
        "city": profile.city,
        "city_name": profile.city_name,
        "station_id": profile.station_id,
        "report_type": item.get("reportType") or item.get("report_type") or "METAR",
        "report_time": item.get("obsTime") or item.get("reportTime") or item.get("receiptTime") or "",
        "raw_text": item.get("rawOb") or item.get("raw_text") or "",
        "temperature": _convert_temp(raw_temp_c, profile.unit),
        "dew_point": _convert_temp(raw_dew_c, profile.unit),
        "wind_direction": _as_float(item.get("wdir")),
        "wind_speed": _as_float(item.get("wspd")),
        "wind_gust": _as_float(item.get("wgst")),
        "visibility": _as_float(item.get("visib")),
        "cloud_layers": item.get("clouds") or item.get("cloudLayers") or [],
        "altimeter": _as_float(item.get("altim") or item.get("altimeter")),
        "pressure": _as_float(item.get("presTend") or item.get("pressure")),
        "precipitation": _as_float(item.get("precip") or item.get("pcp")),
        "sea_level_pressure": _as_float(item.get("slp") or item.get("seaLevelPressure")),
        "peak_wind": item.get("peakWind") or item.get("pkWnd") or {},
        "source_url": AWC_METAR_URL,
        "parser_version": "aviationweather-json-v4",
        "parse_status": "parsed",
        "parse_warnings": _parse_warnings(item),
        "raw_json": {
            "provider": "aviationweather.gov",
            "unit": profile.unit,
            "raw_temperature_c": raw_temp_c,
            "raw_dew_point_c": raw_dew_c,
            "payload": item,
        },
    }


def _select_profiles(cities: list[str] | None) -> list[CitySettlementProfile]:
    if not cities:
        return list(SETTLEMENT_REGISTRY.values())
    selected: list[CitySettlementProfile] = []
    for city in cities:
        key = str(city or "").strip().lower()
        if not key:
            continue
        profile = SETTLEMENT_REGISTRY.get(key)
        if profile:
            selected.append(profile)
    return selected


def _report_key(item: dict[str, Any]) -> str:
    station_id = str(item.get("stationId") or item.get("icaoId") or "").upper()
    observed_at = str(item.get("obsTime") or item.get("reportTime") or item.get("receiptTime") or "")
    raw_text = str(item.get("rawOb") or "")
    raw_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]
    return f"awc:{station_id}:{observed_at}:{raw_hash}"


def _convert_temp(value_c: float | None, unit: str) -> float | None:
    if value_c is None:
        return None
    if str(unit).upper() == "F":
        return round((value_c * 9.0 / 5.0) + 32.0, 2)
    return round(value_c, 2)


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _parse_warnings(item: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if not item.get("rawOb"):
        warnings.append("missing_raw_metar")
    if not item.get("obsTime"):
        warnings.append("missing_observation_time")
    if item.get("temp") is None:
        warnings.append("missing_temperature")
    return warnings
