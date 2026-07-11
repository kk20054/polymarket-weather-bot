from __future__ import annotations

import hashlib
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from ..db import connect, dump_json, init_v3_db, log_data_fetch, utc_now
from ..env_utils import env_value


PARSER_VERSION = "truth-wunderground-daily-v1"
HOURLY_PARSER_VERSION = "truth-wunderground-hourly-v1"
SETTLEMENT_TRUTH_TYPE = "wunderground_daily"
HOURLY_SETTLEMENT_TRUTH_TYPE = "wunderground_hourly_history"


def fetch_wunderground_daily(
    icao: str,
    date_local: str | date,
    *,
    country_code: str = "",
    timezone_name: str = "UTC",
    session: requests.Session | None = None,
    persist: bool = True,
    path: Path | None = None,
) -> dict[str, Any] | None:
    result = fetch_wunderground_daily_result(
        icao,
        date_local,
        country_code=country_code,
        timezone_name=timezone_name,
        session=session,
    )
    if result.get("ok") and persist:
        persist_wunderground_daily(result, path=path)
        hourly_result = result.get("hourly_result")
        if isinstance(hourly_result, dict) and hourly_result.get("ok"):
            persist_wunderground_hourly(hourly_result, path=path)
    return result if result.get("ok") else None


def fetch_wunderground_daily_result(
    icao: str,
    date_local: str | date,
    *,
    country_code: str = "",
    timezone_name: str = "UTC",
    hourly_fallback: bool = True,
    session: requests.Session | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    station = str(icao or "").strip().upper()
    target = _as_date(date_local)
    client = session or requests.Session()
    started_at = utc_now()
    started_perf = time.perf_counter()
    attempts: list[dict[str, Any]] = []
    for attempt in _candidate_requests(station, target, country_code):
        try:
            response = client.get(
                attempt["url"],
                params=attempt.get("params") or None,
                headers={"User-Agent": "WeatherBot/WU-truth (local research)", "Accept": "application/json,text/html"},
                timeout=timeout,
            )
            status_code = int(getattr(response, "status_code", 0) or 0)
            text = str(getattr(response, "text", "") or "")
            source_url = str(getattr(response, "url", "") or attempt["url"])
            if not (200 <= status_code < 300):
                attempts.append(_safe_attempt(attempt, f"http_{status_code}", source_url=source_url))
                continue
            parsed = _parse_weather_daily_payload(text, station, target, source_url, attempt["method"])
            hourly_parsed = None
            if attempt["method"] == "weather_com_location_historical_json":
                hourly_parsed = _parse_weather_hourly_payload(text, station, target, timezone_name, source_url, attempt["method"])
                if hourly_parsed and hourly_parsed.get("rows"):
                    parsed = _daily_from_hourly_result(
                        hourly_parsed,
                        attempts=attempts,
                        skip_reasons=["derived_from_wunderground_hourly_history"],
                    )
            if parsed:
                parsed["duration_ms"] = round((time.perf_counter() - started_perf) * 1000, 2)
                log_data_fetch(
                    source="wunderground",
                    stage="truth_wunderground_daily",
                    status="OK",
                    city=station,
                    target_date=target.isoformat(),
                    duration_ms=parsed["duration_ms"],
                    message=f"Wunderground daily truth fetched via {attempt['method']}",
                    details={k: v for k, v in parsed.items() if k != "raw"},
                    started_at=started_at,
                    finished_at=utc_now(),
                    log_key=f"{PARSER_VERSION}:{station}:{target.isoformat()}:{attempt['method']}",
                )
                return parsed
            attempts.append(_safe_attempt(attempt, "no_daily_high_in_payload", source_url=source_url))
        except Exception as exc:
            attempts.append(_safe_attempt(attempt, "exception", error=str(exc)))
    if hourly_fallback:
        hourly = fetch_wunderground_hourly_result(
            station,
            target,
            country_code=country_code,
            timezone_name=timezone_name,
            session=client,
            timeout=timeout,
        )
        if hourly.get("ok") and hourly.get("rows"):
            parsed = _daily_from_hourly_result(
                hourly,
                attempts=attempts,
                skip_reasons=["daily_endpoint_failed", "derived_from_wunderground_hourly_history"],
            )
            parsed["duration_ms"] = round((time.perf_counter() - started_perf) * 1000, 2)
            log_data_fetch(
                source="wunderground",
                stage="truth_wunderground_daily",
                status="OK",
                city=station,
                target_date=target.isoformat(),
                duration_ms=parsed["duration_ms"],
                message="Wunderground daily truth derived from hourly history",
                details={k: v for k, v in parsed.items() if k not in {"raw", "hourly_result"}},
                started_at=started_at,
                finished_at=utc_now(),
                log_key=f"{PARSER_VERSION}:{station}:{target.isoformat()}:hourly-derived",
            )
            return parsed
        attempts.append({
            "method": "weather_com_location_historical_json_daily_from_hourly",
            "status": "hourly_fallback_failed",
            "skip_reasons": hourly.get("skip_reasons") or [],
        })
    result = {
        "ok": False,
        "icao": station,
        "date_local": target.isoformat(),
        "high_c": None,
        "low_c": None,
        "source_url": "",
        "method": "",
        "settlement_truth_type": SETTLEMENT_TRUTH_TYPE,
        "skip_reasons": [str(item.get("status") or "failed") for item in attempts],
        "attempts": attempts,
        "parser_version": PARSER_VERSION,
        "duration_ms": round((time.perf_counter() - started_perf) * 1000, 2),
    }
    log_data_fetch(
        source="wunderground",
        stage="truth_wunderground_daily",
        status="WARN",
        city=station,
        target_date=target.isoformat(),
        duration_ms=result["duration_ms"],
        message="Wunderground daily truth skipped; no endpoint returned a daily high",
        details=result,
        started_at=started_at,
        finished_at=utc_now(),
        log_key=f"{PARSER_VERSION}:{station}:{target.isoformat()}:skip",
    )
    return result


def fetch_wunderground_hourly_history(
    icao: str,
    date_local: str | date,
    *,
    country_code: str = "",
    timezone_name: str = "UTC",
    session: requests.Session | None = None,
    persist: bool = True,
    path: Path | None = None,
) -> dict[str, Any]:
    result = fetch_wunderground_hourly_result(
        icao,
        date_local,
        country_code=country_code,
        timezone_name=timezone_name,
        session=session,
    )
    if result.get("ok") and persist:
        persist_wunderground_hourly(result, path=path)
    return result


def fetch_wunderground_hourly_result(
    icao: str,
    date_local: str | date,
    *,
    country_code: str = "",
    timezone_name: str = "UTC",
    session: requests.Session | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    station = str(icao or "").strip().upper()
    target = _as_date(date_local)
    zone_name = str(timezone_name or "UTC")
    client = session or requests.Session()
    started_at = utc_now()
    started_perf = time.perf_counter()
    attempts: list[dict[str, Any]] = []
    for attempt in _hourly_candidate_requests(station, target, country_code):
        try:
            response = client.get(
                attempt["url"],
                params=attempt.get("params") or None,
                headers={"User-Agent": "WeatherBot/WU-hourly (local research)", "Accept": "application/json,text/html"},
                timeout=timeout,
            )
            status_code = int(getattr(response, "status_code", 0) or 0)
            text = str(getattr(response, "text", "") or "")
            source_url = str(getattr(response, "url", "") or attempt["url"])
            if not (200 <= status_code < 300):
                attempts.append(_safe_attempt(attempt, f"http_{status_code}", source_url=source_url))
                continue
            parsed = _parse_weather_hourly_payload(text, station, target, zone_name, source_url, attempt["method"])
            if parsed and parsed.get("rows"):
                parsed["duration_ms"] = round((time.perf_counter() - started_perf) * 1000, 2)
                log_data_fetch(
                    source="wunderground",
                    stage="truth_wunderground_hourly",
                    status="OK",
                    city=station,
                    target_date=target.isoformat(),
                    duration_ms=parsed["duration_ms"],
                    message=f"Wunderground hourly history fetched via {attempt['method']}",
                    details={k: v for k, v in parsed.items() if k not in {"raw", "rows"}},
                    started_at=started_at,
                    finished_at=utc_now(),
                    log_key=f"{HOURLY_PARSER_VERSION}:{station}:{target.isoformat()}:{attempt['method']}",
                )
                return parsed
            attempts.append(_safe_attempt(attempt, "no_hourly_observations_in_payload", source_url=source_url))
        except Exception as exc:
            attempts.append(_safe_attempt(attempt, "exception", error=str(exc)))
    result = {
        "ok": False,
        "icao": station,
        "date_local": target.isoformat(),
        "timezone": zone_name,
        "rows": [],
        "source_url": "",
        "method": "",
        "settlement_truth_type": HOURLY_SETTLEMENT_TRUTH_TYPE,
        "skip_reasons": [str(item.get("status") or "failed") for item in attempts],
        "attempts": attempts,
        "parser_version": HOURLY_PARSER_VERSION,
        "duration_ms": round((time.perf_counter() - started_perf) * 1000, 2),
    }
    log_data_fetch(
        source="wunderground",
        stage="truth_wunderground_hourly",
        status="WARN",
        city=station,
        target_date=target.isoformat(),
        duration_ms=result["duration_ms"],
        message="Wunderground hourly history skipped; no endpoint returned observations",
        details=result,
        started_at=started_at,
        finished_at=utc_now(),
        log_key=f"{HOURLY_PARSER_VERSION}:{station}:{target.isoformat()}:skip",
    )
    return result


def persist_wunderground_daily(result: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    init_v3_db(path)
    now = utc_now()
    icao = str(result.get("icao") or "").upper()
    date_local = str(result.get("date_local") or "")
    truth_key = f"wunderground:{icao}:{date_local}"
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO truth_wunderground_daily (
                truth_key, icao, date_local, timezone, high_c, low_c, source_url,
                method, settlement_truth_type, skip_reasons_json, parser_version,
                raw_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(truth_key) DO UPDATE SET
                high_c=excluded.high_c,
                low_c=excluded.low_c,
                source_url=excluded.source_url,
                method=excluded.method,
                settlement_truth_type=excluded.settlement_truth_type,
                skip_reasons_json=excluded.skip_reasons_json,
                parser_version=excluded.parser_version,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                truth_key,
                icao,
                date_local,
                str(result.get("timezone") or ""),
                result.get("high_c"),
                result.get("low_c"),
                str(result.get("source_url") or ""),
                str(result.get("method") or ""),
                str(result.get("settlement_truth_type") or SETTLEMENT_TRUTH_TYPE),
                dump_json(result.get("skip_reasons") or []),
                str(result.get("parser_version") or PARSER_VERSION),
                dump_json(result.get("raw") or result),
                now,
                now,
            ),
        )
    return {"ok": True, "truth_key": truth_key}


def _daily_from_hourly_result(
    hourly: dict[str, Any],
    *,
    attempts: list[dict[str, Any]],
    skip_reasons: list[str] | None = None,
) -> dict[str, Any]:
    rows = [row for row in hourly.get("rows") or [] if isinstance(row, dict)]
    temps = [float(row["temp_c"]) for row in rows if row.get("temp_c") is not None]
    if not temps:
        raise ValueError("hourly_result_without_temperatures")
    return {
        "ok": True,
        "icao": str(hourly.get("icao") or "").upper(),
        "date_local": str(hourly.get("date_local") or ""),
        "timezone": str(hourly.get("timezone") or ""),
        "high_c": round(max(temps), 1),
        "low_c": round(min(temps), 1),
        "source_url": str(hourly.get("source_url") or ""),
        "method": f"{hourly.get('method') or 'wunderground_hourly'}_daily_from_hourly",
        "settlement_truth_type": SETTLEMENT_TRUTH_TYPE,
        "skip_reasons": skip_reasons or ["derived_from_wunderground_hourly_history"],
        "daily_attempts": attempts,
        "hourly_row_count": len(rows),
        "parser_version": PARSER_VERSION,
        "raw": {
            "daily_attempts": attempts,
            "hourly_source_url": hourly.get("source_url"),
            "hourly_method": hourly.get("method"),
            "hourly_row_count": len(rows),
            "hourly_high_c": hourly.get("high_c"),
            "hourly_low_c": hourly.get("low_c"),
        },
        "hourly_result": hourly,
    }


def persist_wunderground_hourly(result: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    init_v3_db(path)
    now = utc_now()
    rows = [row for row in result.get("rows") or [] if isinstance(row, dict)]
    upserted = 0
    with connect(path) as conn:
        for row in rows:
            observation_key = str(row.get("observation_key") or "")
            if not observation_key:
                continue
            conn.execute(
                """
                INSERT INTO truth_wunderground_hourly (
                    observation_key, icao, date_local, timezone, observed_at_local,
                    observed_at_utc, temp_c, dew_point_c, heat_index_c, humidity,
                    pressure_hpa, visibility_km, wind_direction, wind_speed_kph,
                    wind_gust_kph, cloud_cover_pct, condition, source_url, method,
                    settlement_truth_type, parser_version, raw_json, created_at,
                    updated_at
                ) VALUES (
                    :observation_key, :icao, :date_local, :timezone, :observed_at_local,
                    :observed_at_utc, :temp_c, :dew_point_c, :heat_index_c, :humidity,
                    :pressure_hpa, :visibility_km, :wind_direction, :wind_speed_kph,
                    :wind_gust_kph, :cloud_cover_pct, :condition, :source_url, :method,
                    :settlement_truth_type, :parser_version, :raw_json, :created_at,
                    :updated_at
                )
                ON CONFLICT(observation_key) DO UPDATE SET
                    temp_c=excluded.temp_c,
                    dew_point_c=excluded.dew_point_c,
                    heat_index_c=excluded.heat_index_c,
                    humidity=excluded.humidity,
                    pressure_hpa=excluded.pressure_hpa,
                    visibility_km=excluded.visibility_km,
                    wind_direction=excluded.wind_direction,
                    wind_speed_kph=excluded.wind_speed_kph,
                    wind_gust_kph=excluded.wind_gust_kph,
                    cloud_cover_pct=excluded.cloud_cover_pct,
                    condition=excluded.condition,
                    source_url=excluded.source_url,
                    method=excluded.method,
                    settlement_truth_type=excluded.settlement_truth_type,
                    parser_version=excluded.parser_version,
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
                """,
                {
                    "observation_key": observation_key,
                    "icao": str(row.get("icao") or result.get("icao") or "").upper(),
                    "date_local": str(row.get("date_local") or result.get("date_local") or ""),
                    "timezone": str(row.get("timezone") or result.get("timezone") or ""),
                    "observed_at_local": str(row.get("observed_at_local") or ""),
                    "observed_at_utc": str(row.get("observed_at_utc") or ""),
                    "temp_c": row.get("temp_c"),
                    "dew_point_c": row.get("dew_point_c"),
                    "heat_index_c": row.get("heat_index_c"),
                    "humidity": row.get("humidity"),
                    "pressure_hpa": row.get("pressure_hpa"),
                    "visibility_km": row.get("visibility_km"),
                    "wind_direction": row.get("wind_direction"),
                    "wind_speed_kph": row.get("wind_speed_kph"),
                    "wind_gust_kph": row.get("wind_gust_kph"),
                    "cloud_cover_pct": row.get("cloud_cover_pct"),
                    "condition": str(row.get("condition") or ""),
                    "source_url": str(row.get("source_url") or result.get("source_url") or ""),
                    "method": str(row.get("method") or result.get("method") or ""),
                    "settlement_truth_type": str(row.get("settlement_truth_type") or result.get("settlement_truth_type") or HOURLY_SETTLEMENT_TRUTH_TYPE),
                    "parser_version": str(row.get("parser_version") or result.get("parser_version") or HOURLY_PARSER_VERSION),
                    "raw_json": dump_json(row.get("raw") or row),
                    "created_at": now,
                    "updated_at": now,
                },
            )
            upserted += 1
    return {"ok": True, "rows_upserted": upserted}


def _candidate_requests(icao: str, target: date, country_code: str) -> list[dict[str, Any]]:
    ymd = target.strftime("%Y%m%d")
    country = (country_code or _country_from_icao(icao)).upper()
    rows: list[dict[str, Any]] = []
    weather_key = env_value("WEATHER_COM_API_KEY")
    wu_key = env_value("WUNDERGROUND_API_KEY")
    if weather_key:
        rows.append({
            "method": "weather_com_v3_historical_daily",
            "url": "https://api.weather.com/v3/wx/observations/historical/daily",
            "params": {"stationId": icao, "format": "json", "units": "m", "date": ymd, "apiKey": weather_key},
        })
    if wu_key:
        rows.append({
            "method": "wunderground_pws_history_daily",
            "url": "https://api.weather.com/v2/pws/history/daily",
            "params": {"stationId": icao, "format": "json", "units": "m", "date": ymd, "apiKey": wu_key},
        })
    location_params = {"units": "m", "startDate": ymd, "endDate": ymd}
    location_key = weather_key or wu_key
    if location_key:
        location_params["apiKey"] = location_key
    rows.append({
        "method": "weather_com_location_historical_json",
        "url": f"https://api.weather.com/v1/location/{icao}:9:{country}/observations/historical.json",
        "params": location_params,
    })
    return rows


def _hourly_candidate_requests(icao: str, target: date, country_code: str) -> list[dict[str, Any]]:
    ymd = target.strftime("%Y%m%d")
    country = (country_code or _country_from_icao(icao)).upper()
    location_key = env_value("WEATHER_COM_API_KEY") or env_value("WUNDERGROUND_API_KEY")
    if not location_key:
        return []
    return [{
        "method": "weather_com_location_historical_json",
        "url": f"https://api.weather.com/v1/location/{icao}:9:{country}/observations/historical.json",
        "params": {"units": "m", "startDate": ymd, "endDate": ymd, "apiKey": location_key},
    }]


def _parse_weather_daily_payload(text: str, icao: str, target: date, source_url: str, method: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except Exception:
        return None
    candidates: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("observations"), list):
            candidates.extend(item for item in payload["observations"] if isinstance(item, dict))
        if isinstance(payload.get("daily"), list):
            candidates.extend(item for item in payload["daily"] if isinstance(item, dict))
        candidates.append(payload)
    high = None
    low = None
    for item in candidates:
        high = _first_float(item, ("imperial.tempHigh", "metric.tempHigh", "temperatureMax", "tempHigh", "maxt", "max_temp", "temperatureMaxC"))
        low = _first_float(item, ("imperial.tempLow", "metric.tempLow", "temperatureMin", "tempLow", "mint", "min_temp", "temperatureMinC"))
        if high is not None:
            break
    if high is None:
        temps = [
            value
            for item in candidates
            for value in [_first_float(item, ("temp", "metric.temp", "temperature", "temperatureC"))]
            if value is not None
        ]
        if temps:
            high = max(temps)
            low = min(temps)
    if high is None:
        return None
    return {
        "ok": True,
        "icao": icao,
        "date_local": target.isoformat(),
        "high_c": round(float(high), 1),
        "low_c": round(float(low), 1) if low is not None else None,
        "source_url": _redact_url(source_url),
        "method": method,
        "settlement_truth_type": SETTLEMENT_TRUTH_TYPE,
        "skip_reasons": [],
        "parser_version": PARSER_VERSION,
        "raw": payload,
    }


def _parse_weather_hourly_payload(
    text: str,
    icao: str,
    target: date,
    timezone_name: str,
    source_url: str,
    method: str,
) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except Exception:
        return None
    observations = payload.get("observations") if isinstance(payload, dict) else None
    if not isinstance(observations, list):
        return None
    zone = _zone(timezone_name)
    rows: list[dict[str, Any]] = []
    for item in observations:
        if not isinstance(item, dict):
            continue
        observed_utc = _observation_time(item)
        temp_c = _first_float(item, ("metric.temp", "temp", "temperature", "temperatureC"))
        if observed_utc is None or temp_c is None:
            continue
        observed_local = observed_utc.astimezone(zone)
        if observed_local.date() != target:
            continue
        raw = dict(item)
        row = {
            "observation_key": f"wunderground_hourly:{icao}:{observed_utc.isoformat()}",
            "icao": icao,
            "date_local": target.isoformat(),
            "timezone": timezone_name,
            "observed_at_local": observed_local.isoformat(),
            "observed_at_utc": observed_utc.isoformat(),
            "temp_c": round(float(temp_c), 2),
            "dew_point_c": _round_or_none(_first_float(item, ("metric.dewpt", "metric.dewPt", "metric.dewPoint", "dewpt", "dewPt", "dewPoint", "dew_point"))),
            "heat_index_c": _round_or_none(_first_float(item, ("metric.heatIndex", "heatIndex", "heat_index"))),
            "humidity": _round_or_none(_first_float(item, ("rh", "humidity", "relativeHumidity"))),
            "pressure_hpa": _round_or_none(_first_float(item, ("metric.pressure", "pressure", "pressureMeanSeaLevel"))),
            "visibility_km": _round_or_none(_first_float(item, ("metric.vis", "vis", "visibility"))),
            "wind_direction": _round_or_none(_first_float(item, ("wdir", "winddir", "windDirection"))),
            "wind_speed_kph": _round_or_none(_first_float(item, ("metric.wspd", "windSpeed", "wspd"))),
            "wind_gust_kph": _round_or_none(_first_float(item, ("metric.gust", "windGust", "gust"))),
            "cloud_cover_pct": _cloud_cover_pct(item),
            "condition": _condition(item),
            "source_url": _redact_url(source_url),
            "method": method,
            "settlement_truth_type": HOURLY_SETTLEMENT_TRUTH_TYPE,
            "parser_version": HOURLY_PARSER_VERSION,
            "raw": raw,
        }
        rows.append(row)
    if not rows:
        return None
    temps = [float(row["temp_c"]) for row in rows if row.get("temp_c") is not None]
    return {
        "ok": True,
        "icao": icao,
        "date_local": target.isoformat(),
        "timezone": timezone_name,
        "rows": rows,
        "row_count": len(rows),
        "high_c": round(max(temps), 1) if temps else None,
        "low_c": round(min(temps), 1) if temps else None,
        "source_url": _redact_url(source_url),
        "method": method,
        "settlement_truth_type": HOURLY_SETTLEMENT_TRUTH_TYPE,
        "skip_reasons": [],
        "parser_version": HOURLY_PARSER_VERSION,
        "raw": payload,
    }


def _first_float(payload: dict[str, Any], paths: tuple[str, ...]) -> float | None:
    for path in paths:
        value: Any = payload
        for part in path.split("."):
            if isinstance(value, dict):
                value = value.get(part)
            else:
                value = None
                break
        try:
            if value not in (None, "", "null", "M"):
                return float(value)
        except Exception:
            pass
    return None


def _observation_time(payload: dict[str, Any]) -> datetime | None:
    for key in ("valid_time_gmt", "validTimeUtc", "expire_time_gmt", "obsTimeUtc", "observationTimeUtc"):
        value = payload.get(key)
        if value in (None, "", "M"):
            continue
        parsed = _parse_datetime(value)
        if parsed is not None:
            return parsed
    for key in ("obsTimeLocal", "observationTimeLocal", "validTimeLocal"):
        value = payload.get(key)
        parsed = _parse_datetime(value)
        if parsed is not None:
            return parsed
    return None


def _parse_datetime(value: Any) -> datetime | None:
    try:
        if isinstance(value, (int, float)) or str(value).strip().isdigit():
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except Exception:
        pass
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


def _zone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(str(value or "UTC"))
    except Exception:
        return ZoneInfo("UTC")


def _round_or_none(value: float | None, places: int = 2) -> float | None:
    return round(float(value), places) if value is not None else None


def _condition(payload: dict[str, Any]) -> str:
    for key in ("wx_phrase", "wxPhraseLong", "phrase", "condition", "clds"):
        value = payload.get(key)
        if value not in (None, "", "M"):
            return str(value)
    return ""


def _cloud_cover_pct(payload: dict[str, Any]) -> float | None:
    numeric = _first_float(payload, ("cloudCover", "cloud_cover", "cloudCoverPct", "metric.cloudCover"))
    if numeric is not None:
        return max(0.0, min(100.0, round(float(numeric), 2)))
    raw = " ".join(
        str(payload.get(key) or "")
        for key in ("clds", "wx_phrase", "wxPhraseLong", "phrase", "condition")
    ).strip().upper()
    if not raw:
        return None
    if "MOSTLY CLOUDY" in raw:
        return 75.0
    mapping = {
        "CAVOK": 0.0,
        "CLR": 0.0,
        "CLEAR": 0.0,
        "FAIR": 0.0,
        "FEW": 25.0,
        "SCT": 50.0,
        "PARTLY CLOUDY": 50.0,
        "BKN": 100.0,
        "BROKEN": 100.0,
        "OVC": 100.0,
        "OVERCAST": 100.0,
        "CLOUDY": 100.0,
    }
    for key, value in mapping.items():
        if key in raw:
            return value
    return None


def _country_from_icao(icao: str) -> str:
    station = str(icao or "").upper()
    if station.startswith("Z"):
        return "CN"
    if station.startswith("RJ"):
        return "JP"
    if station.startswith("RK"):
        return "KR"
    if station.startswith("RC"):
        return "TW"
    if station.startswith("WS"):
        return "SG"
    if station.startswith("VH"):
        return "HK"
    if station.startswith("K"):
        return "US"
    return "US"


def _safe_attempt(
    attempt: dict[str, Any],
    status: str,
    *,
    source_url: str = "",
    error: str = "",
) -> dict[str, Any]:
    row = {
        "method": str(attempt.get("method") or ""),
        "url": str(attempt.get("url") or ""),
        "params": _redact_params(attempt.get("params") or {}),
        "status": status,
    }
    if source_url:
        row["source_url"] = _redact_url(source_url)
    if error:
        row["error"] = error
    return row


def _redact_params(params: dict[str, Any]) -> dict[str, Any]:
    safe = dict(params)
    for key in list(safe.keys()):
        if str(key).lower() in {"apikey", "api_key", "key", "token"}:
            safe[key] = "***"
    return safe


def _redact_url(url: str) -> str:
    text = str(url or "")
    for secret in (env_value("WEATHER_COM_API_KEY"), env_value("WUNDERGROUND_API_KEY")):
        if secret:
            text = text.replace(secret, "***")
    return text


def _as_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def raw_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
