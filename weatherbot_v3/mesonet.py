from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from .db import upsert_mesonet_observation
from .registry import SETTLEMENT_REGISTRY, CitySettlementProfile


MESONET_PARSER_VERSION = "pws-observation-row-v1"


def ingest_mesonet_observations(
    rows_by_city: dict[str, list[dict[str, Any]]],
    *,
    network: str = "pws",
    source_url: str = "",
) -> dict[str, Any]:
    upserted = 0
    skipped = 0
    failures: list[dict[str, Any]] = []
    cities_seen: list[str] = []
    for city, rows in rows_by_city.items():
        profile = SETTLEMENT_REGISTRY.get(str(city or "").strip().lower())
        if not profile:
            skipped += len(rows or [])
            failures.append({"city": city, "error": "unknown_city"})
            continue
        cities_seen.append(profile.city)
        for item in rows or []:
            try:
                upsert_mesonet_observation(
                    mesonet_observation_from_pws_row(
                        item,
                        profile,
                        network=network,
                        source_url=source_url,
                    )
                )
                upserted += 1
            except Exception as exc:
                failures.append({
                    "city": profile.city,
                    "station_id": item.get("station_id") or item.get("stationId") or "",
                    "error": str(exc),
                })
    return {
        "ok": not failures,
        "source": network,
        "source_url": source_url,
        "cities": sorted(set(cities_seen)),
        "rows_seen": sum(len(rows or []) for rows in rows_by_city.values()),
        "rows_upserted": upserted,
        "rows_skipped": skipped,
        "failures": failures,
    }


def mesonet_observation_from_pws_row(
    item: dict[str, Any],
    profile: CitySettlementProfile,
    *,
    network: str = "pws",
    source_url: str = "",
) -> dict[str, Any]:
    observed_at = _observation_time(item)
    station_id = str(
        item.get("station_id")
        or item.get("stationId")
        or item.get("id")
        or profile.station_id
        or ""
    ).upper()
    raw_temp, raw_unit = _temperature_with_unit(item)
    temperature = _convert_temperature(raw_temp, raw_unit, profile.unit)
    warnings = _parse_warnings(item, observed_at, station_id, raw_temp)
    parse_status = "parsed" if not warnings else "partial"
    observation_key = str(
        item.get("observation_key")
        or _stable_key("mesonet", network, profile.city, station_id, observed_at, raw_temp)
    )
    raw_json = {
        "provider": network,
        "parser_version": MESONET_PARSER_VERSION,
        "target_unit": profile.unit,
        "raw_unit": raw_unit,
        "payload": item,
    }
    return {
        "observation_key": observation_key,
        "city": profile.city,
        "city_name": profile.city_name,
        "station_id": station_id,
        "station_name": item.get("station_name") or item.get("stationName") or station_id,
        "network": network,
        "observed_at": observed_at,
        "temperature": temperature,
        "humidity": _as_float(item.get("humidity") or item.get("humidity_pct")),
        "dew_point": _convert_temperature(_as_float(item.get("dew_point_c") or item.get("dewpoint_c")), "C", profile.unit)
        if (item.get("dew_point_c") is not None or item.get("dewpoint_c") is not None)
        else _as_float(item.get("dew_point") or item.get("dewpoint")),
        "wind_direction": _as_float(item.get("wind_dir_deg") or item.get("wind_direction")),
        "wind_speed": _as_float(item.get("wind_kph") or item.get("wind_speed")),
        "wind_gust": _as_float(item.get("wind_gust") or item.get("wind_gust_kph")),
        "pressure": _as_float(item.get("pressure_hpa") or item.get("pressure")),
        "precipitation": _as_float(item.get("precip_mm") or item.get("precipitation")),
        "source_url": source_url,
        "parser_version": MESONET_PARSER_VERSION,
        "parse_status": parse_status,
        "parse_warnings": warnings,
        "raw_unit": raw_unit,
        "quality_flags": _quality_flags(network, station_id, profile),
        "raw_json": raw_json,
    }


def _temperature_with_unit(item: dict[str, Any]) -> tuple[float | None, str]:
    for key, unit in (
        ("temperature_c", "C"),
        ("temp_c", "C"),
        ("temperature_f", "F"),
        ("temp_f", "F"),
        ("temperature", str(item.get("unit") or item.get("raw_unit") or "")),
    ):
        value = _as_float(item.get(key))
        if value is not None:
            return value, unit.upper() or "unknown"
    return None, str(item.get("unit") or item.get("raw_unit") or "unknown")


def _convert_temperature(value: float | None, source_unit: str, target_unit: str) -> float | None:
    if value is None:
        return None
    source = str(source_unit or "").upper()
    target = str(target_unit or "").upper()
    if source == target:
        return round(value, 2)
    if source == "C" and target == "F":
        return round((value * 9.0 / 5.0) + 32.0, 2)
    if source == "F" and target == "C":
        return round((value - 32.0) * 5.0 / 9.0, 2)
    return round(value, 2)


def _observation_time(item: dict[str, Any]) -> str:
    raw = (
        item.get("observation_time")
        or item.get("observed_at")
        or item.get("obsTime")
        or item.get("time")
        or item.get("valid_time")
        or item.get("fetched_at")
        or ""
    )
    parsed = _parse_epoch_or_iso(raw)
    return parsed.isoformat() if parsed else str(raw or "")


def _parse_epoch_or_iso(value: Any) -> datetime | None:
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


def _parse_warnings(
    item: dict[str, Any],
    observed_at: str,
    station_id: str,
    temperature: float | None,
) -> list[str]:
    warnings: list[str] = []
    if not observed_at:
        warnings.append("missing_observation_time")
    if not station_id:
        warnings.append("missing_station_id")
    if temperature is None:
        warnings.append("missing_temperature")
    if item.get("quality") in {"bad", "suspect"}:
        warnings.append("provider_quality_flag")
    return warnings


def _quality_flags(network: str, station_id: str, profile: CitySettlementProfile) -> list[str]:
    flags = ["mesonet_observation", str(network or "unknown")]
    if station_id != profile.station_id.upper():
        flags.append("nearby_station")
    else:
        flags.append("primary_station")
    return flags


def _stable_key(*parts: Any) -> str:
    text = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None
