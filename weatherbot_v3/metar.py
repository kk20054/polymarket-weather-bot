from __future__ import annotations

import os
import csv
import io
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from .db import connect, log_data_fetch, upsert_metar_reports, utc_now
from .registry import SETTLEMENT_REGISTRY, CitySettlementProfile
from .stations import list_stations, sync_station_registry


AWC_METAR_URL = os.getenv("AVIATION_WEATHER_METAR_URL", "https://aviationweather.gov/api/data/metar")
USER_AGENT = os.getenv("WEATHERBOT_USER_AGENT", "WeatherBot/0.1 local research")
IEM_ASOS_URL = os.getenv("IEM_ASOS_URL", "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py")
IEM_ASOS_RAW_DIR = Path(os.getenv("IEM_ASOS_RAW_DIR", "data/iem_asos_raw"))
IEM_PARSER_VERSION = "iem-asos-csv-v1"
IEM_ARCHIVE_LAG_HOURS = float(os.getenv("IEM_ARCHIVE_LAG_HOURS", "6"))
IEM_REQUEST_DELAY_SECONDS = float(os.getenv("IEM_REQUEST_DELAY_SECONDS", "2.0"))
IEM_DATA_FIELDS = (
    "tmpf",
    "dwpf",
    "drct",
    "sknt",
    "gust",
    "vsby",
    "alti",
    "mslp",
    "p01i",
    "skyc1",
    "skyc2",
    "skyc3",
    "skyl1",
    "skyl2",
    "skyl3",
    "wxcodes",
    "metar",
)
DEFAULT_BACKFILL_CITY_PRIORITY = (
    "chicago",
    "tokyo",
    "atlanta",
    "nyc",
    "dallas",
    "miami",
    "seattle",
)


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
    parsed_reports: list[dict[str, Any]] = []
    skipped = 0
    failures: list[dict[str, Any]] = []
    for item in raw_reports:
        station_id = str(item.get("stationId") or item.get("icaoId") or item.get("station_id") or "").upper()
        profile = station_to_profile.get(station_id)
        if not profile:
            skipped += 1
            continue
        try:
            parsed_reports.append(metar_report_from_awc(item, profile))
        except Exception as exc:
            failures.append({
                "station_id": station_id,
                "error": str(exc),
                "raw_text": item.get("rawOb") or item.get("raw_text") or "",
            })
    upserted = 0
    if parsed_reports:
        try:
            upserted = upsert_metar_reports(parsed_reports)
        except Exception as exc:
            failures.append({
                "stage": "metar_batch_upsert",
                "reports": len(parsed_reports),
                "error": str(exc),
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


def fetch_recent_hours(
    city: str | list[str] | None = None,
    *,
    hours: float = 24.0,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    cities: list[str] | None
    if city is None:
        cities = None
    elif isinstance(city, list):
        cities = [str(item).strip() for item in city if str(item).strip()]
    else:
        cities = [str(city).strip()] if str(city).strip() else None
    bounded_hours = max(1.0, min(float(hours or 24.0), 24.0))
    payload = refresh_metar_reports(cities, hours=bounded_hours, session=session)
    payload["mode"] = "recent_hours"
    payload["recent_hours"] = bounded_hours
    payload["idempotency"] = "station_id+report_time"
    return payload


def fetch_awc_metars(
    station_ids: list[str],
    *,
    hours: float = 24.0,
    session: requests.Session | None = None,
    timeout: float = 20.0,
    retries: int = 2,
    retry_backoff_seconds: float = 0.5,
) -> list[dict[str, Any]]:
    ids = sorted({str(item or "").strip().upper() for item in station_ids if str(item or "").strip()})
    if not ids:
        return []
    bounded_hours = max(1.0, min(float(hours or 24.0), 96.0))
    client = session or requests.Session()
    attempts = max(1, int(retries or 1))
    response = None
    last_error: requests.RequestException | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = client.get(
                AWC_METAR_URL,
                params={"ids": ",".join(ids), "format": "json", "hours": bounded_hours},
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
            )
            break
        except requests.RequestException as exc:
            last_error = exc
            if attempt < attempts and retry_backoff_seconds > 0:
                time.sleep(float(retry_backoff_seconds) * attempt)
    if response is None:
        assert last_error is not None
        raise last_error
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
        "report_time": _report_time(item),
        "raw_text": item.get("rawOb") or item.get("raw_text") or "",
        "temperature": _convert_temp(raw_temp_c, profile.unit),
        "dew_point": _convert_temp(raw_dew_c, profile.unit),
        "wind_direction": _as_float(item.get("wdir")),
        "wind_speed": _as_float(item.get("wspd")),
        "wind_gust": _as_float(item.get("wgst")),
        "visibility": _awc_visibility(item.get("visib"), profile.unit),
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


def _awc_visibility(value: Any, display_unit: str) -> float | None:
    """Convert AWC statute-mile visibility into the city's display convention."""
    text = str(value or "").strip().rstrip("+")
    visibility_miles = _as_float(text)
    if visibility_miles is None:
        return None
    if str(display_unit or "").upper() == "C":
        return round(visibility_miles * 1.609344, 1)
    return round(visibility_miles, 1)


def iem_user_agent() -> str:
    contact = os.getenv("WEATHERBOT_CONTACT_EMAIL", "local-operator@example.com")
    return f"WeatherBot/2.5 (contact: {contact})"


def station_rows_for_metar_backfill(
    cities: list[str] | None = None,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    rows = list_stations()
    if not rows:
        sync_station_registry()
        rows = list_stations()
    requested = {str(city or "").strip().lower() for city in (cities or []) if str(city or "").strip()}
    if requested:
        return [
            row
            for row in rows
            if str(row.get("city_key") or row.get("city") or "").lower() in requested
            or str(row.get("station_id") or "").lower() in requested
        ]
    by_city = {str(row.get("city_key") or row.get("city") or "").lower(): row for row in rows}
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for city in DEFAULT_BACKFILL_CITY_PRIORITY:
        row = by_city.get(city)
        if row and city not in seen:
            selected.append(row)
            seen.add(city)
    for row in rows:
        city = str(row.get("city_key") or row.get("city") or "").lower()
        if city and city not in seen:
            selected.append(row)
            seen.add(city)
        if len(selected) >= max(1, int(limit or 5)):
            break
    return selected[: max(1, int(limit or 5))]


def iem_station_candidates(station_row: dict[str, Any]) -> list[str]:
    raw_ids = [
        station_row.get("icao_id"),
        station_row.get("station_id"),
    ]
    provider_ids = station_row.get("provider_station_ids") or {}
    if isinstance(provider_ids, dict):
        raw_ids.extend(provider_ids.values())
    candidates: list[str] = []
    for raw in raw_ids:
        station = str(raw or "").strip().upper()
        if not station or station in candidates:
            continue
        candidates.append(station)
        if len(station) == 4 and station.startswith("K"):
            stripped = station[1:]
            if stripped not in candidates:
                candidates.append(stripped)
    return candidates


def probe_iem_stations(
    cities: list[str] | None = None,
    *,
    limit_cities: int = 5,
    output_path: str | Path | None = None,
    session: requests.Session | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    rows = station_rows_for_metar_backfill(cities, limit=limit_cities)
    end = (now_utc or datetime.now(timezone.utc)) - timedelta(hours=max(0.0, IEM_ARCHIVE_LAG_HOURS))
    start = end - timedelta(hours=1)
    started = utc_now()
    started_perf = time.perf_counter()
    report_rows: list[dict[str, Any]] = []
    for row in rows:
        city = str(row.get("city_key") or row.get("city") or "")
        candidate_results: list[dict[str, Any]] = []
        selected_station = ""
        for candidate in iem_station_candidates(row):
            result = fetch_iem_asos_csv(
                candidate,
                start,
                end,
                session=session,
                nometa=True,
                include_specials=True,
                timeout=20.0,
            )
            data_rows = _count_nometa_data_lines(result.get("text") or "") if result.get("ok") else 0
            candidate_results.append({
                "station": candidate,
                "ok": bool(result.get("ok")),
                "status_code": result.get("status_code"),
                "data_rows": data_rows,
                "reports": data_rows,
                "partial_rows": 0,
                "failed_rows": 0,
                "duration_ms": result.get("duration_ms"),
                "error": result.get("error", ""),
                "url": result.get("url", ""),
            })
            if session is None:
                time.sleep(IEM_REQUEST_DELAY_SECONDS)
            if result.get("ok") and data_rows > 0:
                selected_station = candidate
                break
        report_rows.append({
            "city": city,
            "city_name": row.get("city_name") or "",
            "station_id": row.get("station_id") or "",
            "icao_id": row.get("icao_id") or "",
            "selected_station": selected_station,
            "candidates": candidate_results,
            "status": "ready" if selected_station else "blocked",
            "reason": "" if selected_station else "no_iem_candidate_returned_rows",
        })
    payload = {
        "ok": all(row["selected_station"] for row in report_rows),
        "source": "iem_asos",
        "stage": "refresh_metar_reports",
        "mode": "probe_stations",
        "window_start_utc": start.isoformat(),
        "window_end_utc": end.isoformat(),
        "user_agent": iem_user_agent(),
        "rows": report_rows,
        "selected": {
            row["city"]: row["selected_station"]
            for row in report_rows
            if row.get("selected_station")
        },
        "generated_at": utc_now(),
    }
    target = Path(output_path) if output_path else IEM_ASOS_RAW_DIR / "probe_report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    finished = utc_now()
    log_data_fetch(
        source="iem_asos",
        stage="refresh_metar_reports",
        status="OK" if payload["ok"] else "WARN",
        duration_ms=round((time.perf_counter() - started_perf) * 1000),
        message="IEM ASOS station probe completed",
        details={"probe_report": str(target), "cities": len(rows), "selected": payload["selected"]},
        started_at=started,
        finished_at=finished,
    )
    payload["output_path"] = str(target)
    return payload


def backfill_iem_asos_metars(
    cities: list[str] | None = None,
    *,
    days: int = 30,
    dry_run: bool = False,
    limit_cities: int = 5,
    session: requests.Session | None = None,
    raw_dir: str | Path | None = None,
    probe_report_path: str | Path | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    bounded_days = max(1, min(int(days or 30), 120))
    end = (now_utc or datetime.now(timezone.utc)) - timedelta(hours=max(0.0, IEM_ARCHIVE_LAG_HOURS))
    start = end - timedelta(days=bounded_days)
    rows = station_rows_for_metar_backfill(cities, limit=limit_cities)
    probe_path = Path(probe_report_path) if probe_report_path else IEM_ASOS_RAW_DIR / "probe_report.json"
    selected = _load_probe_selection(probe_path)
    if not selected:
        return {
            "ok": False,
            "reason": "probe_report_missing_or_empty",
            "probe_report": str(probe_path),
            "message": "Run metar-backfill --probe-stations before real IEM ASOS backfill.",
        }
    target_raw_dir = Path(raw_dir) if raw_dir else IEM_ASOS_RAW_DIR
    results: list[dict[str, Any]] = []
    totals = {
        "reports_seen": 0,
        "reports_upserted": 0,
        "partial_rows": 0,
        "failed_rows": 0,
        "skipped_empty_rows": 0,
    }
    for row in rows:
        city = str(row.get("city_key") or row.get("city") or "")
        selected_station = selected.get(city) or selected.get(str(row.get("station_id") or "").upper())
        if not selected_station:
            results.append({
                "city": city,
                "station_id": row.get("station_id") or "",
                "ok": False,
                "reason": "station_not_selected_by_probe",
            })
            continue
        result = _backfill_iem_station(
            row,
            selected_station,
            start,
            end,
            days=bounded_days,
            dry_run=dry_run,
            session=session,
            raw_dir=target_raw_dir,
        )
        results.append(result)
        for key in totals:
            totals[key] += int(result.get(key) or 0)
        if session is None:
            time.sleep(IEM_REQUEST_DELAY_SECONDS)
    return {
        "ok": all(row.get("ok") for row in results),
        "source": "iem_asos",
        "stage": "refresh_metar_reports",
        "days": bounded_days,
        "dry_run": dry_run,
        "probe_report": str(probe_path),
        "results": results,
        **totals,
    }


def fetch_iem_asos_csv(
    station: str,
    start: datetime,
    end: datetime,
    *,
    session: requests.Session | None = None,
    timeout: float = 30.0,
    include_specials: bool = True,
    nometa: bool = False,
    retries: int = 2,
) -> dict[str, Any]:
    client = session or requests.Session()
    params = build_iem_asos_params(station, start, end, include_specials=include_specials, nometa=nometa)
    started = time.perf_counter()
    attempts = max(1, int(retries or 1))
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            response = client.get(
                IEM_ASOS_URL,
                params=params,
                headers={"User-Agent": iem_user_agent()},
                timeout=timeout,
            )
            status_code = int(getattr(response, "status_code", 0) or 0)
            text = str(getattr(response, "text", "") or "")
            if not (200 <= status_code < 300):
                return {
                    "ok": False,
                    "station": str(station or "").upper(),
                    "status_code": status_code,
                    "text": text,
                    "url": str(getattr(response, "url", "") or _iem_url_preview(params)),
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                    "attempts": attempt,
                    "error": text[:200],
                }
            return {
                "ok": 200 <= status_code < 300,
                "station": str(station or "").upper(),
                "status_code": status_code,
                "text": text,
                "url": str(getattr(response, "url", "") or _iem_url_preview(params)),
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "attempts": attempt,
            }
        except Exception as exc:
            last_error = str(exc)
            if attempt < attempts:
                time.sleep(min(0.5 * attempt, 2.0))
    return {
        "ok": False,
        "station": str(station or "").upper(),
        "status_code": 0,
        "text": "",
        "url": _iem_url_preview(params),
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "attempts": attempts,
        "error": last_error,
    }


def build_iem_asos_params(
    station: str,
    start: datetime,
    end: datetime,
    *,
    include_specials: bool = True,
    nometa: bool = False,
) -> list[tuple[str, Any]]:
    start_utc = _ensure_utc(start)
    end_utc = _ensure_utc(end)
    params: list[tuple[str, Any]] = [
        ("station", str(station or "").strip().upper()),
        ("tz", "Etc/UTC"),
        ("format", "onlycomma"),
        ("latlon", "no"),
        ("missing", "null"),
        ("trace", "null"),
        ("direct", "no"),
        ("year1", start_utc.year),
        ("month1", start_utc.month),
        ("day1", start_utc.day),
        ("hour1", start_utc.hour),
        ("minute1", start_utc.minute),
        ("year2", end_utc.year),
        ("month2", end_utc.month),
        ("day2", end_utc.day),
        ("hour2", end_utc.hour),
        ("minute2", end_utc.minute),
    ]
    params.append(("data", "all"))
    params.append(("report_type", 3))
    if include_specials:
        params.append(("report_type", 4))
    if nometa:
        params.append(("nometa", "yes"))
    return params


def parse_iem_asos_csv(text: str, station_row: dict[str, Any], *, source_url: str = "") -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    skipped_empty_rows = 0
    partial_rows = 0
    failed_rows = 0
    for row in _csv_dict_rows(text):
        report_time = _parse_iem_valid(row.get("valid") or row.get("time") or row.get("report_time"))
        if not report_time:
            skipped_empty_rows += 1
            continue
        raw_text = str(row.get("metar") or "").strip()
        has_structured = _row_has_structured_iem_fields(row)
        has_raw = bool(raw_text)
        if not has_raw and not has_structured:
            skipped_empty_rows += 1
            continue
        station_id = str(station_row.get("station_id") or row.get("station") or "").upper()
        city = str(station_row.get("city_key") or station_row.get("city") or "")
        warnings: list[str] = []
        parse_status = "parsed"
        if not has_raw and has_structured:
            parse_status = "partial"
            warnings.append("no_raw_metar_from_iem")
            partial_rows += 1
        elif has_raw and not _looks_like_metar(raw_text):
            parse_status = "failed"
            warnings.append("unrecognized_raw_metar")
            failed_rows += 1
        cloud_layers = _cloud_layers_from_iem(row)
        report = {
            "report_key": f"iem_asos:{station_id}:{report_time}",
            "city": city,
            "city_name": station_row.get("city_name") or "",
            "station_id": station_id,
            "report_type": _report_type_from_iem(raw_text),
            "report_time": report_time,
            "raw_text": raw_text,
            "temperature": _fahrenheit_to_c(row.get("tmpf")),
            "dew_point": _fahrenheit_to_c(row.get("dwpf")),
            "wind_direction": _as_float(row.get("drct")),
            "wind_speed": _as_float(row.get("sknt")),
            "wind_gust": _as_float(row.get("gust")),
            "visibility": _as_float(row.get("vsby")),
            "cloud_layers": cloud_layers,
            "altimeter": _as_float(row.get("alti")),
            "pressure": _as_float(row.get("mslp")),
            "precipitation": _as_float(row.get("p01i")),
            "sea_level_pressure": _as_float(row.get("mslp")),
            "peak_wind": {},
            "source_url": source_url,
            "parser_version": IEM_PARSER_VERSION,
            "parse_status": parse_status,
            "parse_warnings": warnings,
            "raw_json": {
                "provider": "iem_asos",
                "iem_station": str(row.get("station") or "").upper(),
                "source_url": source_url,
                "raw_unit": "F",
                "normalized_temperature_unit": "C",
                "parser_version": IEM_PARSER_VERSION,
                "payload": row,
            },
        }
        reports.append(report)
    return {
        "reports": reports,
        "reports_seen": len(reports),
        "partial_rows": partial_rows,
        "failed_rows": failed_rows,
        "skipped_empty_rows": skipped_empty_rows,
    }


def ingest_iem_asos_csv(
    text: str,
    station_row: dict[str, Any],
    *,
    source_url: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    parsed = parse_iem_asos_csv(text, station_row, source_url=source_url)
    upserted = 0 if dry_run else upsert_metar_reports(parsed["reports"])
    return {
        "ok": True,
        "dry_run": dry_run,
        "reports_seen": parsed["reports_seen"],
        "reports_upserted": upserted,
        "partial_rows": parsed["partial_rows"],
        "failed_rows": parsed["failed_rows"],
        "skipped_empty_rows": parsed["skipped_empty_rows"],
    }


def iem_metar_field_availability(city: str, *, days: int = 30) -> dict[str, Any]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(days or 30)))).isoformat()
    with connect() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM metar_reports
                WHERE city = ?
                  AND parser_version = ?
                  AND report_type = 'METAR'
                  AND report_time >= ?
                """,
                (city, IEM_PARSER_VERSION, cutoff),
            ).fetchall()
        ]
    total = len(rows)
    thresholds = {
        "temperature": 0.95,
        "dew_point": 0.95,
        "wind_speed": 0.90,
        "wind_direction": 0.90,
        "raw_text": 0.98,
        "cloud_coverage": 0.80,
        "visibility": 0.90,
    }
    metrics: dict[str, Any] = {"routine_reports": total, "thresholds": thresholds, "fields": {}}
    raw_field_map = {
        "temperature": "tmpf",
        "dew_point": "dwpf",
        "wind_speed": "sknt",
        "wind_direction": "drct",
        "visibility": "vsby",
    }
    for field, raw_field in raw_field_map.items():
        count = sum(1 for row in rows if _raw_payload_field_present(row, raw_field))
        ratio = (count / total) if total else 0.0
        metrics["fields"][field] = {"count": count, "ratio": round(ratio, 4), "ok": ratio >= thresholds[field]}
    raw_count = sum(1 for row in rows if _field_present(row.get("raw_text")))
    raw_ratio = (raw_count / total) if total else 0.0
    metrics["fields"]["raw_text"] = {
        "count": raw_count,
        "ratio": round(raw_ratio, 4),
        "ok": raw_ratio >= thresholds["raw_text"],
    }
    cloud_count = 0
    for row in rows:
        try:
            layers = json.loads(row.get("cloud_layers_json") or "[]")
        except Exception:
            layers = []
        if layers:
            cloud_count += 1
    cloud_ratio = (cloud_count / total) if total else 0.0
    metrics["fields"]["cloud_coverage"] = {
        "count": cloud_count,
        "ratio": round(cloud_ratio, 4),
        "ok": cloud_ratio >= thresholds["cloud_coverage"],
    }
    metrics["below_threshold"] = [
        {
            "field": field,
            "ratio": value["ratio"],
            "threshold": thresholds[field],
            "reason_category": "iem_missing_or_parser_gap",
        }
        for field, value in metrics["fields"].items()
        if not value["ok"]
    ]
    return metrics


def _backfill_iem_station(
    station_row: dict[str, Any],
    iem_station: str,
    start: datetime,
    end: datetime,
    *,
    days: int,
    dry_run: bool,
    session: requests.Session | None,
    raw_dir: Path,
) -> dict[str, Any]:
    city = str(station_row.get("city_key") or station_row.get("city") or "")
    started = utc_now()
    started_perf = time.perf_counter()
    result = fetch_iem_asos_csv(iem_station, start, end, session=session, include_specials=True, nometa=False)
    if not result.get("ok"):
        finished = utc_now()
        message = f"IEM ASOS fetch failed for {city}/{iem_station}"
        log_data_fetch(
            source="iem_asos",
            stage="refresh_metar_reports",
            status="ERR",
            duration_ms=result.get("duration_ms"),
            city=city,
            message=message,
            details=result,
            started_at=started,
            finished_at=finished,
        )
        return {
            "city": city,
            "station_id": station_row.get("station_id") or "",
            "iem_station": iem_station,
            "ok": False,
            "reason": "iem_fetch_failed",
            "error": result.get("error", ""),
        }
    raw_path = _write_iem_raw_csv(raw_dir, city, iem_station, start, end, result.get("text") or "")
    ingest = ingest_iem_asos_csv(result.get("text") or "", station_row, source_url=result.get("url") or "", dry_run=dry_run)
    coverage = _iem_hour_coverage(city, days=days)
    availability = iem_metar_field_availability(city, days=days) if city == "chicago" else {}
    finished = utc_now()
    status = "OK"
    message = f"IEM ASOS backfill processed {city}/{iem_station}"
    if availability.get("below_threshold"):
        status = "WARN"
        message = f"IEM ASOS backfill processed {city}/{iem_station}; field availability below threshold"
    log_data_fetch(
        source="iem_asos",
        stage="refresh_metar_reports",
        status=status,
        duration_ms=round((time.perf_counter() - started_perf) * 1000),
        city=city,
        message=message,
        details={
            "iem_station": iem_station,
            "raw_path": str(raw_path),
            "coverage": coverage,
            "field_availability": availability,
            **ingest,
        },
        started_at=started,
        finished_at=finished,
    )
    return {
        "city": city,
        "station_id": station_row.get("station_id") or "",
        "iem_station": iem_station,
        "ok": True,
        "raw_path": str(raw_path),
        "coverage": coverage,
        "field_availability": availability,
        **ingest,
    }


def _iem_hour_coverage(city: str, *, days: int) -> dict[str, Any]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(days or 30)))).isoformat()
    with connect() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT report_time, report_type
                FROM metar_reports
                WHERE city = ?
                  AND parser_version = ?
                  AND report_time >= ?
                """,
                (city, IEM_PARSER_VERSION, cutoff),
            ).fetchall()
        ]
    distinct_hours = {
        str(row.get("report_time") or "")[:13]
        for row in rows
        if str(row.get("report_time") or "")
    }
    denominator = max(1, int(days or 30) * 24)
    return {
        "definition": "distinct_utc_hours_with_any_report / (24 * days)",
        "distinct_hours": len(distinct_hours),
        "expected_hours": denominator,
        "hour_coverage_pct": round(len(distinct_hours) / denominator * 100.0, 2),
        "routine_reports": sum(1 for row in rows if str(row.get("report_type") or "").upper() == "METAR"),
        "special_reports": sum(1 for row in rows if str(row.get("report_type") or "").upper() == "SPECI"),
    }


def _load_probe_selection(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    selected = payload.get("selected") if isinstance(payload, dict) else {}
    if isinstance(selected, dict):
        return {str(key): str(value) for key, value in selected.items() if value}
    return {}


def _write_iem_raw_csv(raw_dir: Path, city: str, station: str, start: datetime, end: datetime, text: str) -> Path:
    city_dir = raw_dir / (city or "unknown")
    city_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{station}_{start.strftime('%Y%m%d%H%M')}_{end.strftime('%Y%m%d%H%M')}.csv"
    path = city_dir / filename
    path.write_text(text, encoding="utf-8")
    return path


def _csv_dict_rows(text: str) -> list[dict[str, str]]:
    clean_lines = [
        line
        for line in str(text or "").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not clean_lines:
        return []
    return [dict(row) for row in csv.DictReader(io.StringIO("\n".join(clean_lines)))]


def _count_nometa_data_lines(text: str) -> int:
    return sum(
        1
        for line in str(text or "").splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "," in line
    )


def _row_has_structured_iem_fields(row: dict[str, Any]) -> bool:
    for field in ("tmpf", "dwpf", "drct", "sknt", "gust", "vsby", "alti", "mslp", "p01i", "skyc1", "skyl1"):
        value = row.get(field)
        if value not in (None, "", "null", "M", "NA"):
            return True
    return False


def _parse_iem_valid(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for suffix in ("Z", "+00:00"):
        if text.endswith(suffix):
            return _parse_epoch_or_iso(text).isoformat() if _parse_epoch_or_iso(text) else ""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        except Exception:
            pass
    parsed = _parse_epoch_or_iso(text)
    return parsed.isoformat() if parsed else ""


def _fahrenheit_to_c(value: Any) -> float | None:
    numeric = _as_float(value)
    if numeric is None:
        return None
    return round((numeric - 32.0) * 5.0 / 9.0, 1)


def _cloud_layers_from_iem(row: dict[str, Any]) -> list[dict[str, Any]]:
    layers: list[dict[str, Any]] = []
    for idx in (1, 2, 3, 4):
        cover = str(row.get(f"skyc{idx}") or "").strip()
        base = _as_float(row.get(f"skyl{idx}"))
        if cover and cover.lower() != "null":
            layers.append({"cover": cover, "base_ft": base})
    return layers


def _report_type_from_iem(raw_text: str) -> str:
    text = str(raw_text or "").strip().upper()
    if text.startswith("SPECI "):
        return "SPECI"
    return "METAR"


def _looks_like_metar(raw_text: str) -> bool:
    text = str(raw_text or "").strip().upper()
    return (
        text.startswith("METAR ")
        or text.startswith("SPECI ")
        or re.match(r"^[A-Z0-9]{4}\s+\d{6}Z\b", text) is not None
    )


def _field_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    text = str(value).strip()
    return bool(text) and text.lower() not in {"null", "none", "nan", "m", "na"}


def _raw_payload_field_present(row: dict[str, Any], field: str) -> bool:
    try:
        decoded = json.loads(row.get("raw_json") or "{}")
    except Exception:
        decoded = {}
    payload = decoded.get("payload") or {}
    if not payload and isinstance(decoded.get("raw_json"), dict):
        payload = decoded["raw_json"].get("payload") or {}
    return _field_present(payload.get(field))


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iem_url_preview(params: list[tuple[str, Any]]) -> str:
    from urllib.parse import urlencode

    return f"{IEM_ASOS_URL}?{urlencode(params, doseq=True)}"


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
    observed_at = _report_time(item)
    return f"awc:{station_id}:{observed_at}"


def _report_time(item: dict[str, Any]) -> str:
    raw = item.get("obsTime") or item.get("reportTime") or item.get("receiptTime") or ""
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
