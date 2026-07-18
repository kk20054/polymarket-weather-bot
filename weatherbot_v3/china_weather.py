from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .db import log_data_fetch, upsert_mesonet_observation, utc_now
from .env_utils import env_value
from .registry import SETTLEMENT_REGISTRY, CitySettlementProfile


CHINA_LIVE_NETWORK = "china_live"
WEATHERCOM_CURRENT_NETWORK = "weathercom_current"
CHINA_LIVE_PARSER_VERSION = "china-live-v2"
HKO_RHRREAD_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=rhrread&lang=en"
WEATHERCN_SK2D_URL_TEMPLATE = "https://d1.weather.com.cn/sk_2d/{station_code}.html?_={timestamp_ms}"
WEATHERCOM_CURRENT_URL = "https://api.weather.com/v3/wx/observations/current"
DEFAULT_USER_AGENT = "WeatherBot/2.0 (contact: local-operator)"

CHINA_LIVE_CITY_ALIASES = {
    "hongkong": "hong-kong",
    "hong_kong": "hong-kong",
    "hk": "hong-kong",
    "hko": "hong-kong",
    "hong-kong": "hong-kong",
    "beijing": "beijing",
    "chengdu": "chengdu",
    "chongqing": "chongqing",
    "guangzhou": "guangzhou",
    "qingdao": "qingdao",
    "shanghai": "shanghai",
    "shenzhen": "shenzhen",
    "wuhan": "wuhan",
}

WEATHERCN_STATION_CODES = {
    # Airport-district feeds are intentionally used instead of city-centre
    # feeds. They are display-only evidence near the settlement airport, not
    # settlement truth.
    "beijing": "101010400",   # Shunyi, near ZBAA
    "chengdu": "101270106",   # Shuangliu, ZUUU
    "chongqing": "101040700", # Yubei, near ZUCK
    "guangzhou": "101280110", # Baiyun, near ZGGG
    "qingdao": "101120205",   # Jiaozhou, near ZSQD
    "shanghai": "101020600",
    "shenzhen": "101280605",  # Baoan, ZGSZ
    "wuhan": "101200103",     # Huangpi, near ZHHH
}

WEATHERCN_EXPECTED_NAMEEN = {
    "beijing": "shunyi",
    "chengdu": "shuangliu",
    "chongqing": "yubei",
    "guangzhou": "baiyun",
    "qingdao": "jiaozhou",
    "shanghai": "pudongxinqu",
    "shenzhen": "baoan",
    "wuhan": "huangpi",
}

WIND_DIRECTION_DEGREES = {
    "N": 0,
    "NNE": 22.5,
    "NE": 45,
    "ENE": 67.5,
    "E": 90,
    "ESE": 112.5,
    "SE": 135,
    "SSE": 157.5,
    "S": 180,
    "SSW": 202.5,
    "SW": 225,
    "WSW": 247.5,
    "W": 270,
    "WNW": 292.5,
    "NW": 315,
    "NNW": 337.5,
}


def supported_china_live_cities() -> list[str]:
    return [*sorted(WEATHERCN_STATION_CODES), "hong-kong"]


def normalize_china_city(city: str) -> str:
    key = str(city or "").strip().lower()
    return CHINA_LIVE_CITY_ALIASES.get(key, key)


def fetch_china_weather(cities: list[str] | None = None, *, dry_run: bool = False) -> dict[str, Any]:
    selected = [normalize_china_city(city) for city in (cities or supported_china_live_cities())]
    selected = [city for city in selected if city in supported_china_live_cities()]
    if not selected:
        return {"ok": False, "source": CHINA_LIVE_NETWORK, "reason": "no_supported_cities", "results": []}

    started = utc_now()
    started_perf = time.perf_counter()
    results = []
    for city in dict.fromkeys(selected):
        try:
            results.append(fetch_china_weather_city(city, dry_run=dry_run))
        except Exception as exc:
            results.append({
                "ok": False,
                "city": city,
                "error": "china_live_fetch_failed",
                "message": str(exc)[:240],
                "rows_upserted": 0,
                "dry_run": dry_run,
            })
    failures = [item for item in results if not item.get("ok")]
    finished = utc_now()
    log_data_fetch(
        source=CHINA_LIVE_NETWORK,
        stage="china_weather_fetch",
        status="OK" if not failures else "WARN",
        duration_ms=round((time.perf_counter() - started_perf) * 1000),
        message=f"China Weather Live fetched {len(results) - len(failures)}/{len(results)} cities",
        details={"dry_run": dry_run, "results": results},
        started_at=started,
        finished_at=finished,
    )
    return {
        "ok": not failures,
        "source": CHINA_LIVE_NETWORK,
        "dry_run": dry_run,
        "cities": len(results),
        "ok_cities": len(results) - len(failures),
        "failed": len(failures),
        "rows_upserted": sum(int(item.get("rows_upserted") or 0) for item in results),
        "results": results,
    }


def fetch_china_weather_city(city: str, *, dry_run: bool = False) -> dict[str, Any]:
    city_key = normalize_china_city(city)
    profile = SETTLEMENT_REGISTRY.get(city_key)
    if city_key == "hong-kong":
        raw = _http_get(HKO_RHRREAD_URL)
        payload = json.loads(raw)
        observation = hko_rhrread_observation(payload, profile, raw_response=raw, source_url=HKO_RHRREAD_URL)
    elif city_key in WEATHERCN_STATION_CODES:
        station_code = WEATHERCN_STATION_CODES[city_key]
        source_url = WEATHERCN_SK2D_URL_TEMPLATE.format(
            station_code=station_code,
            timestamp_ms=int(time.time() * 1000),
        )
        try:
            raw = _http_get(
                source_url,
                headers={
                    "User-Agent": f"Mozilla/5.0 {DEFAULT_USER_AGENT}",
                    "Referer": "https://www.weather.com.cn/",
                },
                timeout=8,
            )
            observation = weathercn_sk2d_observation(
                raw,
                profile,
                station_code=station_code,
                source_url=source_url,
            )
            payload = ((observation.get("raw_json") or {}).get("payload") or {})
            expected_name = WEATHERCN_EXPECTED_NAMEEN.get(city_key, "")
            actual_name = str(payload.get("nameen") or "").strip().lower()
            if expected_name and actual_name != expected_name:
                observation["parse_status"] = "failed"
                observation["parse_warnings"] = [
                    *list(observation.get("parse_warnings") or []),
                    f"weathercn_station_identity_mismatch:{actual_name or 'missing'}",
                ]
            if observation.get("parse_status") == "failed":
                raise ValueError("weathercn_parse_failed")
        except Exception as exc:
            observation = _weathercom_current_observation(
                profile,
                primary_failure=_safe_failure_reason(exc),
            )
    else:
        return {"ok": False, "city": city, "error": "unsupported_china_live_city", "rows_upserted": 0}

    status = str(observation.get("parse_status") or "failed")
    provider = str((observation.get("raw_json") or {}).get("provider") or "")
    primary_available = provider != "weathercom_v3_current"
    row_id = 0
    if not dry_run:
        row_id = upsert_mesonet_observation(observation)
    return {
        "ok": status in {"parsed", "partial"} and primary_available,
        "city": city_key,
        "station_id": observation.get("station_id"),
        "observed_at": observation.get("observed_at"),
        "temperature": observation.get("temperature"),
        "humidity": observation.get("humidity"),
        "parse_status": status,
        "parse_warnings": observation.get("parse_warnings", []),
        "provider": provider,
        "fallback": not primary_available,
        "rows_upserted": 0 if dry_run else (1 if row_id else 0),
        "dry_run": dry_run,
        "source_url": observation.get("source_url"),
    }


def _weathercom_current_observation(
    profile: CitySettlementProfile,
    *,
    primary_failure: str,
) -> dict[str, Any]:
    """Use Weather.com current conditions when the China Weather feed is down.

    This remains display-only evidence and records the provider explicitly; it
    must never be treated as weather.com.cn or settlement truth.
    """
    api_key = str(env_value("WEATHER_COM_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(f"weathercn_unavailable:{primary_failure};weathercom_key_missing")
    public_params = {
        "geocode": f"{profile.latitude},{profile.longitude}",
        "format": "json",
        "units": "m",
        "language": "zh-CN",
    }
    request_url = f"{WEATHERCOM_CURRENT_URL}?{urlencode({**public_params, 'apiKey': api_key})}"
    source_url = f"{WEATHERCOM_CURRENT_URL}?{urlencode(public_params)}&apiKey=***"
    raw = _http_get(
        request_url,
        headers={"Accept": "application/json", "User-Agent": DEFAULT_USER_AGENT},
        timeout=12,
    )
    payload = json.loads(raw)
    valid_epoch = _float(payload.get("validTimeUtc"))
    observed_at = (
        datetime.fromtimestamp(valid_epoch, tz=timezone.utc)
        if valid_epoch is not None
        else datetime.now(timezone.utc)
    )
    warnings = [f"weathercn_primary_unavailable:{primary_failure}"]
    temperature = _float(payload.get("temperature"))
    if temperature is None:
        warnings.append("missing_weathercom_current_temperature")
    return _observation(
        profile=profile,
        station_id=profile.station_id,
        station_name=f"Weather.com current near {profile.station_id}",
        observed_at=observed_at,
        temperature=temperature,
        humidity=_float(payload.get("relativeHumidity")),
        wind_speed=_float(payload.get("windSpeed")),
        wind_direction=_float(payload.get("windDirection")),
        pressure=_float(payload.get("pressureMeanSeaLevel")),
        source_url=source_url,
        raw_response=raw,
        fetched_at=datetime.now(timezone.utc),
        parse_warnings=warnings,
        extra_raw={
            "provider": "weathercom_v3_current",
            "fallback_for": "weathercn_sk2d",
            "primary_failure": primary_failure,
            "payload": payload,
        },
        network=WEATHERCOM_CURRENT_NETWORK,
    )


def _safe_failure_reason(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if code is not None:
        return f"http_{code}"
    return type(exc).__name__


def hko_rhrread_observation(
    payload: dict[str, Any],
    profile: CitySettlementProfile | None = None,
    *,
    raw_response: str | None = None,
    source_url: str = HKO_RHRREAD_URL,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    profile = profile or SETTLEMENT_REGISTRY["hong-kong"]
    fetched = _parse_time(fetched_at) or datetime.now(timezone.utc)
    raw = raw_response if raw_response is not None else json.dumps(payload, ensure_ascii=False, sort_keys=True)
    temp_block = payload.get("temperature") if isinstance(payload, dict) else {}
    humidity_block = payload.get("humidity") if isinstance(payload, dict) else {}
    temp_row = _find_place(temp_block.get("data") if isinstance(temp_block, dict) else [], "Hong Kong Observatory")
    humidity_row = _find_place(humidity_block.get("data") if isinstance(humidity_block, dict) else [], "Hong Kong Observatory")
    observed_at = _parse_time((temp_block or {}).get("recordTime")) or _parse_time(payload.get("updateTime")) or fetched
    temperature = _float((temp_row or {}).get("value"))
    humidity = _float((humidity_row or {}).get("value"))
    warnings = []
    if temperature is None:
        warnings.append("missing_hko_temperature")
    if humidity is None:
        warnings.append("missing_hko_humidity")
    return _observation(
        profile=profile,
        station_id="HKO",
        station_name="Hong Kong Observatory",
        observed_at=observed_at,
        temperature=temperature,
        humidity=humidity,
        source_url=source_url,
        raw_response=raw,
        fetched_at=fetched,
        parse_warnings=warnings,
        extra_raw={"provider": "hko_rhrread", "payload": payload},
    )


def weathercn_sk2d_observation(
    raw_text: str,
    profile: CitySettlementProfile | None = None,
    *,
    station_code: str = "101020600",
    source_url: str = "",
    fetched_at: str | None = None,
) -> dict[str, Any]:
    profile = profile or SETTLEMENT_REGISTRY["shanghai"]
    fetched = _parse_time(fetched_at) or datetime.now(timezone.utc)
    payload, parser_warnings = _parse_weathercn_datask(raw_text)
    observed_at = _weathercn_observed_at(payload, profile, fetched)
    temperature = _float(payload.get("temp"))
    humidity = _percent(payload.get("SD") or payload.get("sd"))
    pressure = _float(payload.get("qy"))
    wind_speed = _wind_speed_kph(payload.get("wse") or payload.get("WS"))
    wind_direction = _wind_direction_degrees(payload.get("wde"))
    warnings = list(parser_warnings)
    if temperature is None:
        warnings.append("missing_weathercn_temperature")
    if observed_at is None:
        warnings.append("missing_weathercn_observed_at")
        observed_at = fetched
    return _observation(
        profile=profile,
        station_id=station_code,
        station_name=f"weather.com.cn Shanghai {station_code}",
        observed_at=observed_at,
        temperature=temperature,
        humidity=humidity,
        wind_speed=wind_speed,
        wind_direction=wind_direction,
        pressure=pressure,
        source_url=source_url,
        raw_response=raw_text,
        fetched_at=fetched,
        parse_warnings=warnings,
        extra_raw={"provider": "weathercn_sk2d", "station_code": station_code, "payload": payload},
    )


def _observation(
    *,
    profile: CitySettlementProfile,
    station_id: str,
    station_name: str,
    observed_at: datetime,
    temperature: float | None,
    humidity: float | None,
    source_url: str,
    raw_response: str,
    fetched_at: datetime,
    parse_warnings: list[str],
    extra_raw: dict[str, Any],
    wind_speed: float | None = None,
    wind_direction: float | None = None,
    pressure: float | None = None,
    network: str = CHINA_LIVE_NETWORK,
) -> dict[str, Any]:
    observed_utc = observed_at.astimezone(timezone.utc)
    status = "failed" if temperature is None else ("partial" if parse_warnings else "parsed")
    raw_hash = hashlib.sha256(raw_response.encode("utf-8", errors="replace")).hexdigest()[:32]
    return {
        "observation_key": _stable_key(network, station_id, observed_utc.isoformat()),
        "city": profile.city,
        "city_name": profile.city_name,
        "station_id": station_id,
        "station_name": station_name,
        "network": network,
        "observed_at": observed_utc.isoformat(),
        "temperature": round(temperature, 1) if temperature is not None else None,
        "temperature_c": round(temperature, 1) if temperature is not None else None,
        "humidity": humidity,
        "wind_speed": wind_speed,
        "wind_direction": wind_direction,
        "pressure": pressure,
        "source_url": source_url,
        "raw_response": raw_response,
        "raw_response_hash": raw_hash,
        "parser_version": CHINA_LIVE_PARSER_VERSION,
        "parse_status": status,
        "parse_warnings": sorted(set(parse_warnings)),
        "raw_unit": "C",
        "quality_flags": [network, "display_only", "not_settlement_truth"],
        "fetched_at": fetched_at.astimezone(timezone.utc).isoformat(),
        "raw_json": {
            **extra_raw,
            "network": network,
            "parser_version": CHINA_LIVE_PARSER_VERSION,
            "raw_response_hash": raw_hash,
            "display_only": True,
            "settlement_truth": False,
        },
    }


def _http_get(url: str, *, headers: dict[str, str] | None = None, timeout: int = 20) -> str:
    request_headers = {"User-Agent": DEFAULT_USER_AGENT}
    if headers:
        request_headers.update(headers)
    if "d1.weather.com.cn" in url:
        # weather.com.cn resets ordinary Python TLS clients. curl_cffi uses a
        # maintained browser fingerprint while preserving the same public URL.
        from curl_cffi import requests as browser_requests

        response = browser_requests.get(
            url,
            headers=request_headers,
            impersonate="chrome",
            timeout=timeout,
        )
        response.raise_for_status()
        return response.content.decode("utf-8", errors="replace")
    req = Request(url, headers=request_headers)
    with urlopen(req, timeout=timeout) as response:
        raw = response.read()
    return raw.decode("utf-8", errors="replace")


def _parse_weathercn_datask(raw_text: str) -> tuple[dict[str, Any], list[str]]:
    text = str(raw_text or "").strip()
    warnings: list[str] = []
    match = re.search(r"var\s+dataSK\s*=\s*(\{.*?\})\s*;?\s*$", text, re.DOTALL)
    if not match:
        # Legacy ChinaWeather used HTML scraping; keep a tiny degraded parser so
        # structural drift is explicit rather than silently manufacturing data.
        html_payload = _parse_weathercn_html_fallback(text)
        if html_payload:
            warnings.append("weathercn_html_fallback")
            return html_payload, warnings
        return {}, ["weathercn_datask_not_found"]
    try:
        payload = json.loads(match.group(1))
        if isinstance(payload, dict):
            return payload, warnings
    except Exception as exc:
        warnings.append(f"weathercn_json_decode_failed:{exc}")
    return {}, warnings or ["weathercn_json_decode_failed"]


def _parse_weathercn_html_fallback(text: str) -> dict[str, Any]:
    temp_match = re.search(r"(-?\d+(?:\.\d+)?)\s*℃", text)
    humidity_match = re.search(r"(?:湿度|humidity)[^0-9]*(\d+(?:\.\d+)?)\s*%", text, flags=re.IGNORECASE)
    time_match = re.search(r"(\d{1,2}:\d{2})", text)
    if not temp_match:
        return {}
    return {
        "temp": temp_match.group(1),
        "SD": f"{humidity_match.group(1)}%" if humidity_match else "",
        "time": time_match.group(1) if time_match else "",
    }


def _weathercn_observed_at(payload: dict[str, Any], profile: CitySettlementProfile, fetched: datetime) -> datetime | None:
    local_tz = ZoneInfo(profile.timezone)
    fetched_local = fetched.astimezone(local_tz)
    time_text = str(payload.get("time") or "").strip()
    if not re.match(r"^\d{1,2}:\d{2}$", time_text):
        return None
    hour, minute = [int(part) for part in time_text.split(":", 1)]
    date_text = str(payload.get("date") or "")
    month_day = re.search(r"(\d{1,2})月(\d{1,2})日", date_text)
    year = fetched_local.year
    month = fetched_local.month
    day = fetched_local.day
    if month_day:
        month = int(month_day.group(1))
        day = int(month_day.group(2))
    try:
        return datetime(year, month, day, hour, minute, tzinfo=local_tz)
    except ValueError:
        return None


def _find_place(rows: Any, place: str) -> dict[str, Any] | None:
    if not isinstance(rows, list):
        return None
    normalized = place.strip().lower()
    for row in rows:
        if isinstance(row, dict) and str(row.get("place") or "").strip().lower() == normalized:
            return row
    return None


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


def _percent(value: Any) -> float | None:
    text = str(value or "").strip().replace("%", "")
    return _float(text)


def _wind_speed_kph(value: Any) -> float | None:
    text = str(value or "").strip()
    match = re.search(r"(-?\d+(?:\.\d+)?)", text)
    return _float(match.group(1)) if match else None


def _wind_direction_degrees(value: Any) -> float | None:
    text = str(value or "").strip().upper()
    return WIND_DIRECTION_DEGREES.get(text)


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _stable_key(*parts: Any) -> str:
    text = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
