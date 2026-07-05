from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Any

import requests

from ..db import connect, dump_json, init_v3_db, log_data_fetch, utc_now


PARSER_VERSION = "truth-wunderground-daily-v1"
SETTLEMENT_TRUTH_TYPE = "wunderground_daily"


def fetch_wunderground_daily(
    icao: str,
    date_local: str | date,
    *,
    country_code: str = "",
    session: requests.Session | None = None,
    persist: bool = True,
    path: Path | None = None,
) -> dict[str, Any] | None:
    result = fetch_wunderground_daily_result(
        icao,
        date_local,
        country_code=country_code,
        session=session,
    )
    if result.get("ok") and persist:
        persist_wunderground_daily(result, path=path)
    return result if result.get("ok") else None


def fetch_wunderground_daily_result(
    icao: str,
    date_local: str | date,
    *,
    country_code: str = "",
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
                attempts.append({**attempt, "status": f"http_{status_code}", "source_url": source_url})
                continue
            parsed = _parse_weather_daily_payload(text, station, target, source_url, attempt["method"])
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
            attempts.append({**attempt, "status": "no_daily_high_in_payload", "source_url": source_url})
        except Exception as exc:
            attempts.append({**attempt, "status": "exception", "error": str(exc)})
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


def _candidate_requests(icao: str, target: date, country_code: str) -> list[dict[str, Any]]:
    ymd = target.strftime("%Y%m%d")
    country = (country_code or _country_from_icao(icao)).upper()
    rows: list[dict[str, Any]] = []
    weather_key = os.getenv("WEATHER_COM_API_KEY", "").strip()
    wu_key = os.getenv("WUNDERGROUND_API_KEY", "").strip()
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
    rows.append({
        "method": "weather_com_location_historical_json",
        "url": f"https://api.weather.com/v1/location/{icao}:9:{country}/observations/historical.json",
        "params": {"units": "m", "startDate": ymd, "endDate": ymd},
    })
    return rows


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
        return None
    return {
        "ok": True,
        "icao": icao,
        "date_local": target.isoformat(),
        "high_c": round(float(high), 1),
        "low_c": round(float(low), 1) if low is not None else None,
        "source_url": source_url,
        "method": method,
        "settlement_truth_type": SETTLEMENT_TRUTH_TYPE,
        "skip_reasons": [],
        "parser_version": PARSER_VERSION,
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
    if station.startswith("K"):
        return "US"
    return "US"


def _as_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def raw_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
