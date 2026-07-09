from __future__ import annotations

import hashlib
import json
import os
import statistics
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import requests

from .db import log_data_fetch, upsert_mesonet_observation, utc_now
from .env_utils import env_value
from .registry import SETTLEMENT_REGISTRY, CitySettlementProfile


PWS_NETWORK = "wunderground_pws"
PWS_PARSER_VERSION = "wunderground-pws-v1"
PWS_DISCOVERY_URL = "https://api.weather.com/v3/location/near"
PWS_CURRENT_URL = "https://api.weather.com/v2/pws/observations/current"
DEFAULT_USER_AGENT = "WeatherBot/2.5 (contact: local-user)"


def fetch_wunderground_pws(
    cities: list[str] | None = None,
    *,
    dry_run: bool = False,
    limit_cities: int = 5,
    station_limit: int = 5,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    selected = _select_us_profiles(cities, limit_cities=limit_cities)
    results = [
        fetch_wunderground_pws_city(
            profile.city,
            dry_run=dry_run,
            station_limit=station_limit,
            session=session,
        )
        for profile in selected
    ]
    hard_failures = [row for row in results if not row.get("ok") and not row.get("skipped")]
    return {
        "ok": not hard_failures,
        "source": PWS_NETWORK,
        "dry_run": dry_run,
        "cities": len(selected),
        "rows_upserted": sum(int(row.get("rows_upserted") or 0) for row in results),
        "skipped": sum(1 for row in results if row.get("skipped")),
        "failed": len(hard_failures),
        "results": results,
    }


def fetch_wunderground_pws_city(
    city: str,
    *,
    dry_run: bool = False,
    station_limit: int = 5,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    profile = SETTLEMENT_REGISTRY.get(str(city or "").strip().lower())
    started = utc_now()
    started_perf = time.perf_counter()
    if not profile:
        return _logged_result(started, started_perf, city, {"ok": False, "city": city, "error": "unknown_city"})
    if profile.region != "us":
        payload = {
            "ok": True,
            "skipped": True,
            "city": profile.city,
            "reason": "pws_us_only",
            "rows_upserted": 0,
        }
        return _logged_result(started, started_perf, profile.city, payload)
    api_key = _api_key()
    if not api_key:
        payload = {
            "ok": True,
            "skipped": True,
            "city": profile.city,
            "station_id": profile.station_id,
            "reason": "missing_wunderground_api_key",
            "required_env": ["WUNDERGROUND_API_KEY", "WEATHER_COM_API_KEY"],
            "rows_upserted": 0,
        }
        return _logged_result(started, started_perf, profile.city, payload)

    client = session or requests.Session()
    try:
        station_ids = _configured_station_ids(profile)
        discovery_url = ""
        if not station_ids:
            station_ids, discovery_url = discover_pws_station_ids(
                profile,
                api_key=api_key,
                station_limit=station_limit,
                session=client,
            )
        if not station_ids:
            payload = {
                "ok": False,
                "city": profile.city,
                "station_id": profile.station_id,
                "error": "no_pws_stations_found",
                "rows_upserted": 0,
                "discovery_url": discovery_url,
            }
            return _logged_result(started, started_perf, profile.city, payload)
        observations: list[dict[str, Any]] = []
        source_urls: list[str] = []
        for station_id in station_ids[: max(1, int(station_limit or 5))]:
            payload, source_url = fetch_pws_current_observation(
                station_id,
                api_key=api_key,
                session=client,
            )
            source_urls.append(source_url)
            observations.extend(parse_pws_current_payload(payload, station_id=station_id))
        aggregate = aggregate_pws_observations(
            observations,
            profile,
            source_url=";".join(_strip_api_key(url) for url in source_urls),
        )
        if not aggregate:
            payload = {
                "ok": False,
                "city": profile.city,
                "station_id": profile.station_id,
                "error": "no_parseable_pws_observations",
                "stations_checked": station_ids,
                "rows_upserted": 0,
            }
            return _logged_result(started, started_perf, profile.city, payload)
        row_id = 0
        if not dry_run:
            row_id = upsert_mesonet_observation(aggregate)
        payload = {
            "ok": True,
            "city": profile.city,
            "station_id": aggregate["station_id"],
            "source_station_ids": station_ids[: max(1, int(station_limit or 5))],
            "observations_seen": len(observations),
            "rows_upserted": 0 if dry_run else 1,
            "row_id": row_id,
            "dry_run": dry_run,
            "source_url": aggregate.get("source_url"),
        }
        return _logged_result(started, started_perf, profile.city, payload)
    except Exception as exc:
        payload = {
            "ok": False,
            "city": profile.city,
            "station_id": profile.station_id,
            "error": str(exc),
            "rows_upserted": 0,
        }
        return _logged_result(started, started_perf, profile.city, payload)


def discover_pws_station_ids(
    profile: CitySettlementProfile,
    *,
    api_key: str,
    station_limit: int = 5,
    session: requests.Session | None = None,
) -> tuple[list[str], str]:
    params = {
        "geocode": f"{profile.latitude},{profile.longitude}",
        "product": "pws",
        "format": "json",
        "apiKey": api_key,
    }
    payload, source_url = _request_json(session or requests.Session(), PWS_DISCOVERY_URL, params)
    location = payload.get("location") if isinstance(payload.get("location"), dict) else payload
    candidates: list[Any] = []
    for key in ("stationId", "stationID", "station_ids", "pwsId", "pws_ids"):
        value = location.get(key) if isinstance(location, dict) else None
        if isinstance(value, list):
            candidates.extend(value)
        elif value:
            candidates.append(value)
    station_ids = []
    for item in candidates:
        text = str(item or "").strip().upper()
        if text and text not in station_ids:
            station_ids.append(text)
    return station_ids[: max(1, int(station_limit or 5))], _strip_api_key(source_url)


def fetch_pws_current_observation(
    station_id: str,
    *,
    api_key: str,
    session: requests.Session | None = None,
) -> tuple[dict[str, Any], str]:
    params = {
        "stationId": station_id,
        "format": "json",
        "units": "m",
        "apiKey": api_key,
    }
    return _request_json(session or requests.Session(), PWS_CURRENT_URL, params)


def parse_pws_current_payload(payload: dict[str, Any], *, station_id: str = "") -> list[dict[str, Any]]:
    observations = payload.get("observations") if isinstance(payload, dict) else None
    if isinstance(observations, dict):
        observations = [observations]
    if not isinstance(observations, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in observations:
        if not isinstance(item, dict):
            continue
        metric = item.get("metric") if isinstance(item.get("metric"), dict) else {}
        rows.append({
            "station_id": str(item.get("stationID") or item.get("stationId") or station_id or "").upper(),
            "station_name": item.get("neighborhood") or item.get("stationName") or item.get("stationID") or station_id,
            "observed_at": _parse_observed_at(item.get("obsTimeUtc") or item.get("obsTimeLocal")),
            "temperature_c": _as_float(metric.get("temp") if metric else item.get("temp")),
            "humidity": _as_float(item.get("humidity")),
            "dew_point_c": _as_float(metric.get("dewpt") if metric else item.get("dewpt")),
            "wind_direction": _as_float(item.get("winddir")),
            "wind_kph": _as_float(metric.get("windSpeed") if metric else item.get("windSpeed")),
            "wind_gust_kph": _as_float(metric.get("windGust") if metric else item.get("windGust")),
            "pressure_hpa": _as_float(metric.get("pressure") if metric else item.get("pressure")),
            "precip_mm": _as_float(metric.get("precipRate") if metric else item.get("precipRate")),
            "payload": item,
        })
    return rows


def aggregate_pws_observations(
    observations: list[dict[str, Any]],
    profile: CitySettlementProfile,
    *,
    source_url: str = "",
) -> dict[str, Any] | None:
    usable = [row for row in observations if _as_float(row.get("temperature_c")) is not None and row.get("observed_at")]
    if not usable:
        return None
    observed_at = max(str(row["observed_at"]) for row in usable)
    station_ids = sorted({str(row.get("station_id") or "").upper() for row in usable if row.get("station_id")})
    temperature_c = _median([row.get("temperature_c") for row in usable])
    raw_unit = "C"
    temperature = _convert_temp(temperature_c, "C", profile.unit)
    raw_payload = {
        "provider": PWS_NETWORK,
        "parser_version": PWS_PARSER_VERSION,
        "profile_station": profile.station_id,
        "source_station_ids": station_ids,
        "observations": observations,
        "aggregation": "median",
    }
    raw_response = json.dumps(raw_payload, ensure_ascii=False, sort_keys=True)
    return {
        "observation_key": _stable_key(PWS_NETWORK, profile.city, observed_at),
        "city": profile.city,
        "city_name": profile.city_name,
        "station_id": f"WU_PWS_{profile.station_id}",
        "station_name": f"Wunderground PWS aggregate near {profile.station_id}",
        "network": PWS_NETWORK,
        "observed_at": observed_at,
        "temperature": temperature,
        "humidity": _median([row.get("humidity") for row in usable]),
        "dew_point": _convert_temp(_median([row.get("dew_point_c") for row in usable]), "C", profile.unit),
        "wind_direction": _median([row.get("wind_direction") for row in usable]),
        "wind_speed": _median([row.get("wind_kph") for row in usable]),
        "wind_gust": _median([row.get("wind_gust_kph") for row in usable]),
        "pressure": _median([row.get("pressure_hpa") for row in usable]),
        "precipitation": _median([row.get("precip_mm") for row in usable]),
        "source_url": source_url,
        "raw_response": raw_response,
        "raw_response_hash": hashlib.sha256(raw_response.encode("utf-8")).hexdigest()[:32],
        "parser_version": PWS_PARSER_VERSION,
        "parse_status": "parsed",
        "parse_warnings": [],
        "raw_unit": profile.unit,
        "quality_flags": [PWS_NETWORK, "mesonet_observation", "display_only", "not_settlement_truth", "nearby_station", "aggregated_pws"],
        "fetched_at": utc_now(),
        "raw_json": raw_payload,
    }


def _logged_result(started: str, started_perf: float, city: str, payload: dict[str, Any]) -> dict[str, Any]:
    finished = utc_now()
    status = "OK" if payload.get("ok") else "WARN"
    log_data_fetch(
        source=PWS_NETWORK,
        stage="pws_fetch",
        status=status,
        duration_ms=round((time.perf_counter() - started_perf) * 1000),
        city=city,
        message=str(payload.get("reason") or payload.get("error") or "pws fetch complete"),
        details=payload,
        started_at=started,
        finished_at=finished,
    )
    return payload


def _request_json(client: requests.Session, url: str, params: dict[str, Any]) -> tuple[dict[str, Any], str]:
    source_url = f"{url}?{urlencode(params)}"
    response = client.get(
        url,
        params=params,
        timeout=20,
        headers={"User-Agent": DEFAULT_USER_AGENT},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("pws_response_not_object")
    return payload, source_url


def _select_us_profiles(cities: list[str] | None, *, limit_cities: int) -> list[CitySettlementProfile]:
    requested = {str(city or "").strip().lower() for city in (cities or []) if str(city or "").strip()}
    profiles = [
        profile for profile in SETTLEMENT_REGISTRY.values()
        if profile.region == "us" and (not requested or profile.city in requested)
    ]
    return profiles[: max(1, int(limit_cities or 5))]


def _configured_station_ids(profile: CitySettlementProfile) -> list[str]:
    env_key = f"WUNDERGROUND_PWS_STATIONS_{profile.city.upper().replace('-', '_')}"
    direct = env_value(env_key)
    if direct:
        return _split_station_ids(direct)
    raw_json = env_value("WUNDERGROUND_PWS_STATIONS_JSON")
    if raw_json:
        try:
            data = json.loads(raw_json)
            value = data.get(profile.city) if isinstance(data, dict) else None
            if isinstance(value, list):
                return _split_station_ids(",".join(str(item) for item in value))
            if value:
                return _split_station_ids(str(value))
        except Exception:
            return []
    return []


def _api_key() -> str:
    return str(env_value("WUNDERGROUND_API_KEY") or env_value("WEATHER_COM_API_KEY") or "").strip()


def _split_station_ids(value: str) -> list[str]:
    result: list[str] = []
    for item in str(value or "").replace(";", ",").split(","):
        station = item.strip().upper()
        if station and station not in result:
            result.append(station)
    return result


def _parse_observed_at(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _strip_api_key(url: str) -> str:
    return str(url or "").replace(_api_key(), "<redacted>")


def _median(values: list[Any]) -> float | None:
    numeric = [_as_float(value) for value in values]
    numeric = [value for value in numeric if value is not None]
    if not numeric:
        return None
    return round(float(statistics.median(numeric)), 2)


def _convert_temp(value: float | None, source_unit: str, target_unit: str) -> float | None:
    if value is None:
        return None
    source = str(source_unit or "").upper()
    target = str(target_unit or "").upper()
    if source == target:
        return round(float(value), 2)
    if source == "C" and target == "F":
        return round((float(value) * 9.0 / 5.0) + 32.0, 2)
    if source == "F" and target == "C":
        return round((float(value) - 32.0) * 5.0 / 9.0, 2)
    return round(float(value), 2)


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _stable_key(*parts: Any) -> str:
    text = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
