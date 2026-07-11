from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import requests

from ..db import connect, dump_json, init_v3_db, log_data_fetch, utc_now


PARSER_VERSION = "truth-hko-daily-extract-v1"
SETTLEMENT_TRUTH_TYPE = "hong_kong_observatory_daily_extract"
HKO_DAILY_EXTRACT_URL = "https://www.hko.gov.hk/cis/dailyExtract/dailyExtract_{yyyymm}.xml"


def fetch_hko_daily_extract(
    date_local: str | date,
    *,
    session: requests.Session | None = None,
    persist: bool = True,
    path: Path | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    return fetch_hko_daily_extract_many(
        [date_local],
        session=session,
        persist=persist,
        path=path,
        timeout=timeout,
    )[0]


def fetch_hko_daily_extract_many(
    date_locals: list[str | date],
    *,
    session: requests.Session | None = None,
    persist: bool = True,
    path: Path | None = None,
    timeout: float = 20.0,
) -> list[dict[str, Any]]:
    targets = [_as_date(value) for value in date_locals]
    if not targets:
        return []
    grouped: dict[str, list[date]] = defaultdict(list)
    for target in targets:
        grouped[target.strftime("%Y%m")].append(target)
    client = session or requests.Session()
    results_by_date: dict[str, dict[str, Any]] = {}
    for yyyymm, month_targets in grouped.items():
        url = HKO_DAILY_EXTRACT_URL.format(yyyymm=yyyymm)
        started_at = utc_now()
        started_perf = time.perf_counter()
        response_text = ""
        source_url = url
        request_error = ""
        try:
            response = client.get(
                url,
                headers={"User-Agent": "WeatherBot/HKO-truth (local research)", "Accept": "application/json,text/xml"},
                timeout=timeout,
            )
            status_code = int(getattr(response, "status_code", 0) or 0)
            response_text = str(getattr(response, "text", "") or "")
            source_url = str(getattr(response, "url", "") or url)
            if not (200 <= status_code < 300):
                request_error = f"http_{status_code}"
        except Exception as exc:
            request_error = str(exc)
        duration_ms = round((time.perf_counter() - started_perf) * 1000, 2)
        finished_at = utc_now()
        for target in month_targets:
            result = (
                _empty(target, source_url, request_error)
                if request_error
                else parse_hko_daily_extract(response_text, target.isoformat(), source_url=source_url)
            )
            result["duration_ms"] = duration_ms
            if persist and result.get("ok"):
                persist_hko_daily(result, path=path)
            log_data_fetch(
                source="hko",
                stage="truth_hko_daily",
                status="OK" if result.get("ok") else "WARN",
                city="hong-kong",
                target_date=target.isoformat(),
                duration_ms=duration_ms,
                message="HKO Daily Extract fetched" if result.get("ok") else str(result.get("reason") or "hko_daily_missing"),
                details={k: v for k, v in result.items() if k != "raw"},
                started_at=started_at,
                finished_at=finished_at,
                log_key=f"{PARSER_VERSION}:{target.isoformat()}",
            )
            results_by_date[target.isoformat()] = result
    return [results_by_date[target.isoformat()] for target in targets]


def parse_hko_daily_extract(text: str, date_local: str, *, source_url: str = "") -> dict[str, Any]:
    payload = _parse_payload(text)
    payload_hash = hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()
    month_data = ((payload.get("stn") or {}).get("data") or []) if isinstance(payload, dict) else []
    day = int(date_local[-2:])
    for month in month_data:
        for row in month.get("dayData") or []:
            if not isinstance(row, list) or not row:
                continue
            if str(row[0]).zfill(2) != f"{day:02d}":
                continue
            high = _float(row[2] if len(row) > 2 else None)
            mean = _float(row[3] if len(row) > 3 else None)
            low = _float(row[4] if len(row) > 4 else None)
            if high is None:
                return _empty(_as_date(date_local), source_url, "hko_row_missing_high_temperature", raw=payload)
            return {
                "ok": True,
                "date_local": date_local,
                "high_c": high,
                "low_c": low,
                "mean_c": mean,
                "source_url": source_url,
                "settlement_truth_type": SETTLEMENT_TRUTH_TYPE,
                "parser_version": PARSER_VERSION,
                "raw": {"row": row, "payload_sha256": payload_hash, "month": date_local[:7]},
            }
    return _empty(_as_date(date_local), source_url, "date_not_found_in_hko_daily_extract", raw=payload)


def persist_hko_daily(result: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    init_v3_db(path)
    now = utc_now()
    date_local = str(result.get("date_local") or "")
    truth_key = f"hko:{date_local}"
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO truth_hko_daily (
                truth_key, date_local, high_c, low_c, mean_c, source_url,
                settlement_truth_type, parser_version, raw_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(truth_key) DO UPDATE SET
                high_c=excluded.high_c,
                low_c=excluded.low_c,
                mean_c=excluded.mean_c,
                source_url=excluded.source_url,
                settlement_truth_type=excluded.settlement_truth_type,
                parser_version=excluded.parser_version,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                truth_key,
                date_local,
                result.get("high_c"),
                result.get("low_c"),
                result.get("mean_c"),
                str(result.get("source_url") or ""),
                str(result.get("settlement_truth_type") or SETTLEMENT_TRUTH_TYPE),
                str(result.get("parser_version") or PARSER_VERSION),
                dump_json(result.get("raw") or result),
                now,
                now,
            ),
        )
    return {"ok": True, "truth_key": truth_key}


def _parse_payload(text: str) -> dict[str, Any]:
    clean = str(text or "").strip()
    try:
        return json.loads(clean)
    except Exception:
        pass
    # HKO uses a .xml suffix but currently returns JSON. Keep a small fallback
    # for future XML-ish wrapping rather than failing silently.
    start = clean.find("{")
    end = clean.rfind("}")
    if start >= 0 and end > start:
        return json.loads(clean[start:end + 1])
    return {}


def _empty(target: date, source_url: str, reason: str, *, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "date_local": target.isoformat(),
        "high_c": None,
        "low_c": None,
        "mean_c": None,
        "source_url": source_url,
        "settlement_truth_type": SETTLEMENT_TRUTH_TYPE,
        "parser_version": PARSER_VERSION,
        "reason": reason,
        "raw": raw or {},
    }


def _float(value: Any) -> float | None:
    try:
        if value in (None, "", "Trace", "trace", "null"):
            return None
        return float(str(value).strip())
    except Exception:
        return None


def _as_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
