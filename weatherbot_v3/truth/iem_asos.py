from __future__ import annotations

import csv
import io
import json
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests

from ..db import connect, dump_json, init_v3_db, log_data_fetch, utc_now
from ..metar import IEM_ASOS_URL, iem_user_agent


PARSER_VERSION = "truth-iem-asos-daily-v1"
SETTLEMENT_TRUTH_TYPE = "iem_asos_approximation"


def fetch_iem_asos_daily(
    icao: str,
    date_local: str | date,
    tz: str,
    *,
    session: requests.Session | None = None,
    persist: bool = True,
    path: Path | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Fetch one local calendar day of IEM ASOS routine reports and compute max C.

    This is intentionally an approximation to Wunderground airport daily history:
    it uses the same airport ASOS/METAR stream but stores explicit provenance so
    live gates can distinguish it from exact Wunderground settlement truth.
    """
    target = _as_date(date_local)
    return fetch_iem_asos_range(
        icao,
        target,
        target,
        tz,
        session=session,
        persist=persist,
        path=path,
        timeout=timeout,
    )[0]


def fetch_iem_asos_range(
    icao: str,
    start_date_local: str | date,
    end_date_local: str | date,
    tz: str,
    *,
    session: requests.Session | None = None,
    persist: bool = True,
    path: Path | None = None,
    timeout: float = 60.0,
) -> list[dict[str, Any]]:
    """Fetch one station/date range in a single IEM request, then split by local day."""
    station = str(icao or "").strip().upper()
    start_target = _as_date(start_date_local)
    end_target = _as_date(end_date_local)
    if end_target < start_target:
        start_target, end_target = end_target, start_target
    targets: list[date] = []
    cursor = start_target
    while cursor <= end_target:
        targets.append(cursor)
        cursor += timedelta(days=1)
    tz_name = str(tz or "UTC")
    zone = ZoneInfo(tz_name)
    start_local = datetime.combine(start_target, dt_time(0, 0), tzinfo=zone)
    end_local = datetime.combine(end_target, dt_time(23, 59), tzinfo=zone)
    params = _iem_params(station, start_local, end_local, tz_name)
    client = session or requests.Session()
    started_at = utc_now()
    started_perf = time.perf_counter()
    url = f"{IEM_ASOS_URL}?{urlencode(params, doseq=True)}"
    try:
        response = client.get(
            IEM_ASOS_URL,
            params=params,
            headers={"User-Agent": iem_user_agent()},
            timeout=timeout,
        )
        status_code = int(getattr(response, "status_code", 0) or 0)
        text = str(getattr(response, "text", "") or "")
        source_url = str(getattr(response, "url", "") or url)
        if not (200 <= status_code < 300):
            results = [_empty_result(station, target, tz_name, source_url, f"http_{status_code}") for target in targets]
        else:
            results = [
                parse_iem_asos_daily_csv(text, station, target.isoformat(), tz_name, source_url=source_url)
                for target in targets
            ]
    except Exception as exc:
        results = [_empty_result(station, target, tz_name, url, str(exc)) for target in targets]

    duration_ms = round((time.perf_counter() - started_perf) * 1000, 2)
    finished_at = utc_now()
    if persist:
        successful = [result for result in results if result.get("ok")]
        if successful:
            init_v3_db(path)
            with connect(path) as conn:
                for result in successful:
                    _persist_iem_asos_daily(conn, result)

    for result in results:
        result["duration_ms"] = duration_ms
    failed = [result for result in results if not result.get("ok")]
    log_data_fetch(
        source="iem_asos",
        stage="truth_iem_daily",
        status="OK" if not failed else "WARN",
        city=station,
        target_date=f"{start_target.isoformat()}..{end_target.isoformat()}",
        duration_ms=duration_ms,
        message=(
            "IEM ASOS truth approximation range fetched"
            if not failed
            else f"IEM ASOS truth range incomplete: {len(failed)}/{len(results)} failed"
        ),
        details={
            "station": station,
            "timezone": tz_name,
            "start_date": start_target.isoformat(),
            "end_date": end_target.isoformat(),
            "requested_days": len(results),
            "ok_days": len(results) - len(failed),
            "failed_days": [
                {"date_local": result.get("date_local"), "reason": result.get("reason")}
                for result in failed
            ],
            "persisted": bool(persist),
            "parser_version": PARSER_VERSION,
        },
        started_at=started_at,
        finished_at=finished_at,
        log_key=f"{PARSER_VERSION}:{station}:{start_target.isoformat()}:{end_target.isoformat()}",
        path=path,
    )
    return results


def parse_iem_asos_daily_csv(
    text: str,
    icao: str,
    date_local: str,
    tz: str,
    *,
    source_url: str = "",
) -> dict[str, Any]:
    zone = ZoneInfo(str(tz or "UTC"))
    hourly: list[dict[str, Any]] = []
    for row in _csv_rows(text):
        tmpf = _float(row.get("tmpf"))
        valid = str(row.get("valid") or row.get("time") or "").strip()
        if tmpf is None or not valid:
            continue
        local_dt = _parse_iem_local_time(valid, zone)
        if not local_dt or local_dt.date().isoformat() != date_local:
            continue
        temp_c = round((tmpf - 32.0) * 5.0 / 9.0, 1)
        hourly.append({
            "icao": str(icao or "").upper(),
            "date_local": date_local,
            "timezone": str(tz or "UTC"),
            "observed_at_local": local_dt.isoformat(),
            "observed_at_utc": local_dt.astimezone(timezone.utc).isoformat(),
            "temp_c": temp_c,
            "tmpf": tmpf,
            "raw_text": str(row.get("metar") or "").strip(),
            "source_url": source_url,
            "parser_version": PARSER_VERSION,
            "raw": row,
        })
    if not hourly:
        return _empty_result(str(icao or "").upper(), _as_date(date_local), str(tz or "UTC"), source_url, "no_iem_tmpf_rows")
    high = max(hourly, key=lambda item: item["temp_c"])
    low = min(hourly, key=lambda item: item["temp_c"])
    return {
        "ok": True,
        "icao": str(icao or "").upper(),
        "date_local": date_local,
        "timezone": str(tz or "UTC"),
        "high_c": high["temp_c"],
        "low_c": low["temp_c"],
        "high_time_local": high["observed_at_local"],
        "low_time_local": low["observed_at_local"],
        "obs_count": len(hourly),
        "source_url": source_url,
        "all_hourly": hourly,
        "settlement_truth_type": SETTLEMENT_TRUTH_TYPE,
        "parser_version": PARSER_VERSION,
    }


def persist_iem_asos_daily(result: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    init_v3_db(path)
    with connect(path) as conn:
        return _persist_iem_asos_daily(conn, result)


def _persist_iem_asos_daily(conn: Any, result: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    icao = str(result.get("icao") or "").upper()
    date_local = str(result.get("date_local") or "")
    truth_key = f"iem_asos:{icao}:{date_local}"
    conn.execute(
        """
            INSERT INTO truth_iem_daily (
                truth_key, icao, date_local, timezone, high_c, low_c,
                high_time_local, low_time_local, obs_count, source_url,
                settlement_truth_type, parser_version, raw_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(truth_key) DO UPDATE SET
                timezone=excluded.timezone,
                high_c=excluded.high_c,
                low_c=excluded.low_c,
                high_time_local=excluded.high_time_local,
                low_time_local=excluded.low_time_local,
                obs_count=excluded.obs_count,
                source_url=excluded.source_url,
                settlement_truth_type=excluded.settlement_truth_type,
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
            str(result.get("high_time_local") or ""),
            str(result.get("low_time_local") or ""),
            int(result.get("obs_count") or 0),
            str(result.get("source_url") or ""),
            str(result.get("settlement_truth_type") or SETTLEMENT_TRUTH_TYPE),
            str(result.get("parser_version") or PARSER_VERSION),
            dump_json({k: v for k, v in result.items() if k != "all_hourly"}),
            now,
            now,
        ),
    )
    hourly_count = 0
    for item in result.get("all_hourly") or []:
        observed_local = str(item.get("observed_at_local") or "")
        observation_key = f"iem_asos_hourly:{icao}:{observed_local}"
        conn.execute(
            """
                INSERT INTO truth_iem_hourly (
                    observation_key, icao, date_local, timezone, observed_at_local,
                    observed_at_utc, temp_c, tmpf, raw_text, source_url,
                    parser_version, raw_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(observation_key) DO UPDATE SET
                    temp_c=excluded.temp_c,
                    tmpf=excluded.tmpf,
                    raw_text=excluded.raw_text,
                    source_url=excluded.source_url,
                    parser_version=excluded.parser_version,
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
            """,
            (
                observation_key,
                icao,
                date_local,
                str(item.get("timezone") or result.get("timezone") or ""),
                observed_local,
                str(item.get("observed_at_utc") or ""),
                item.get("temp_c"),
                item.get("tmpf"),
                str(item.get("raw_text") or ""),
                str(item.get("source_url") or result.get("source_url") or ""),
                str(item.get("parser_version") or PARSER_VERSION),
                dump_json(item.get("raw") or item),
                now,
                now,
            ),
        )
        hourly_count += 1
    return {"ok": True, "truth_key": truth_key, "hourly_upserted": hourly_count}


def _iem_params(station: str, start_local: datetime, end_local: datetime, tz_name: str) -> list[tuple[str, Any]]:
    return [
        ("station", station),
        ("data", "tmpf"),
        ("data", "metar"),
        ("tz", tz_name),
        ("format", "onlycomma"),
        ("latlon", "no"),
        ("missing", "null"),
        ("trace", "null"),
        ("direct", "yes"),
        ("year1", start_local.year),
        ("month1", start_local.month),
        ("day1", start_local.day),
        ("hour1", start_local.hour),
        ("minute1", start_local.minute),
        ("year2", end_local.year),
        ("month2", end_local.month),
        ("day2", end_local.day),
        ("hour2", end_local.hour),
        ("minute2", end_local.minute),
        ("report_type", 3),
    ]


def _csv_rows(text: str) -> list[dict[str, Any]]:
    lines = [line for line in str(text or "").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        return []
    return [dict(row) for row in csv.DictReader(io.StringIO("\n".join(lines)))]


def _parse_iem_local_time(value: str, zone: ZoneInfo) -> datetime | None:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=zone)
        except Exception:
            pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=zone)
        return parsed.astimezone(zone)
    except Exception:
        return None


def _float(value: Any) -> float | None:
    try:
        if value in (None, "", "null", "M", "NA"):
            return None
        return float(value)
    except Exception:
        return None


def _as_date(value: str | date) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value))


def _empty_result(icao: str, target: date, tz: str, source_url: str, reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "icao": str(icao or "").upper(),
        "date_local": target.isoformat(),
        "timezone": tz,
        "high_c": None,
        "low_c": None,
        "high_time_local": "",
        "low_time_local": "",
        "obs_count": 0,
        "source_url": source_url,
        "all_hourly": [],
        "settlement_truth_type": SETTLEMENT_TRUTH_TYPE,
        "parser_version": PARSER_VERSION,
        "reason": reason,
    }
