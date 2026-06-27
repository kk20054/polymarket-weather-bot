from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .db import init_v3_db, insert_forecast_run
from .registry import get_city_profile


TEMPERATURE_KEYS = ("high_temp", "temperature_2m", "temperature", "temp", "value")
DEFAULT_ARCHIVE_SOURCES = ("ecmwf", "gfs_ensemble")


def build_forecast_archive_manifest(
    audit: dict[str, Any],
    sources: list[str] | tuple[str, ...] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Build a JSONL-ready template for missing historical forecast archives."""
    requested_sources = tuple(source.strip().lower() for source in (sources or DEFAULT_ARCHIVE_SOURCES) if source)
    records: list[dict[str, Any]] = []
    for sample in audit.get("samples", []):
        if sample.get("settlement_pending"):
            continue
        reasons = set(sample.get("reasons") or [])
        warnings = set(sample.get("warnings") or [])
        relevant = {
            "no_no_leak_forecast_run",
            "forecast_members_missing",
            "core_source_coverage_incomplete",
        }
        if not ((reasons | warnings) & relevant):
            continue

        existing_sources = {str(source).strip().lower() for source in sample.get("sources") or [] if source}
        if not existing_sources or "no_no_leak_forecast_run" in reasons:
            missing_sources = set(requested_sources)
        elif "core_source_coverage_incomplete" in warnings:
            missing_sources = set(requested_sources) - existing_sources
        elif "forecast_members_missing" in reasons:
            missing_sources = existing_sources & set(requested_sources)
        else:
            missing_sources = set(requested_sources)

        for source in sorted(missing_sources):
            if limit is not None and len(records) >= limit:
                break
            records.append(_manifest_record(sample, source, reasons, warnings))
        if limit is not None and len(records) >= limit:
            break

    by_city = Counter(record["city"] for record in records)
    by_source = Counter(record["source"] for record in records)
    return {
        "manifest_version": "forecast-archive-manifest-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "record_count": len(records),
        "by_city": dict(sorted(by_city.items())),
        "by_source": dict(sorted(by_source.items())),
        "records": records,
        "jsonl": "\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records),
    }


def write_forecast_archive_manifest(manifest: dict[str, Any], path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(str(manifest.get("jsonl") or "") + ("\n" if manifest.get("records") else ""), encoding="utf-8")


def import_forecast_archive(path: str | Path, apply: bool = False, strict: bool = True) -> dict[str, Any]:
    """Validate and optionally persist historical forecast/member archive records."""
    records = _load_archive_records(Path(path))
    summary: dict[str, Any] = {
        "archive_path": str(path),
        "apply": bool(apply),
        "strict": bool(strict),
        "requested": len(records),
        "valid": 0,
        "imported": 0,
        "skipped": 0,
        "errors": [],
        "by_city": {},
        "by_source": {},
        "run_ids": [],
    }
    city_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()

    if apply:
        init_v3_db()

    for index, record in enumerate(records, start=1):
        normalized = normalize_archive_record(record, strict=strict)
        if not normalized["ok"]:
            summary["skipped"] += 1
            summary["errors"].append({
                "index": index,
                "city": record.get("city") if isinstance(record, dict) else None,
                "target_date": record.get("target_date") if isinstance(record, dict) else None,
                "reason": normalized["reason"],
            })
            continue

        run = normalized["run"]
        members = normalized["members"]
        summary["valid"] += 1
        city_counts[str(run["city"])] += 1
        source_counts[str(run["source"])] += 1
        if apply:
            run_id = insert_forecast_run(run, members)
            summary["run_ids"].append(run_id)
            summary["imported"] += 1

    summary["by_city"] = dict(sorted(city_counts.items()))
    summary["by_source"] = dict(sorted(source_counts.items()))
    return summary


def _manifest_record(sample: dict[str, Any], source: str, reasons: set[str], warnings: set[str]) -> dict[str, Any]:
    city = str(sample.get("city") or "").strip().lower()
    profile = get_city_profile(city)
    timezone_name = str(sample.get("timezone") or (profile.timezone if profile else "") or "UTC")
    return {
        "city": city,
        "city_name": sample.get("city_name") or (profile.city_name if profile else city),
        "target_date": sample.get("target_date"),
        "timezone": timezone_name,
        "station_id": profile.station_id if profile else "",
        "station_name": profile.station_name if profile else "",
        "unit": profile.unit if profile else "",
        "source": source,
        "provider": f"{source}_archive",
        "model": _default_model_for_source(source),
        "model_version": "<fill real archive model version>",
        "run_at": "<fill ISO time visible before target local day boundary>",
        "retrieved_at": "<fill ISO retrieval time>",
        "valid_at": "<fill ISO forecast time inside target local day>",
        "lead_hours": "<fill numeric lead hours>",
        "members": [
            {
                "member_id": "<fill member id>",
                "high_temp": "<fill member daily high in settlement unit>",
                "hourly": [],
            }
        ],
        "archive_gap_reasons": sorted(reasons | warnings),
        "no_leak_rule": "D+1/D+2 run_at < target local day start; D+0 run_at < target local day end",
    }


def _default_model_for_source(source: str) -> str:
    return {
        "ecmwf": "ecmwf_ifs",
        "gfs_ensemble": "gefs",
        "hrrr": "hrrr",
    }.get(source, source)


def normalize_archive_record(record: dict[str, Any], strict: bool = True) -> dict[str, Any]:
    if not isinstance(record, dict):
        return _invalid("record_not_object")

    city = str(record.get("city") or "").strip().lower()
    target_date = str(record.get("target_date") or record.get("date") or "").strip()
    if not city:
        return _invalid("city_missing")
    if not target_date:
        return _invalid("target_date_missing")

    profile = get_city_profile(city)
    timezone_name = str(record.get("timezone") or (profile.timezone if profile else "") or "UTC")
    bounds = _local_day_bounds(target_date, timezone_name)
    if bounds is None:
        return _invalid("target_timezone_invalid")
    local_start, local_end = bounds

    run_at = _parse_time(record.get("run_at"))
    retrieved_at = _parse_time(record.get("retrieved_at")) or run_at
    valid_at = _parse_time(record.get("valid_at"))
    if run_at is None:
        return _invalid("run_at_missing")
    if valid_at is None:
        return _invalid("valid_at_missing")
    if not (local_start <= valid_at <= local_end):
        return _invalid("valid_at_outside_target_day")

    source = str(record.get("source") or record.get("provider") or "").strip().lower()
    provider = str(record.get("provider") or source).strip().lower()
    model = str(record.get("model") or "").strip()
    model_version = str(record.get("model_version") or "").strip()
    if strict and not source:
        return _invalid("source_missing")
    if strict and not model:
        return _invalid("model_missing")
    if strict and not model_version:
        return _invalid("model_version_missing")

    lead_hours = _float(record.get("lead_hours"))
    if lead_hours is None:
        lead_hours = (valid_at - run_at).total_seconds() / 3600.0
    if lead_hours < 0:
        return _invalid("lead_hours_negative")

    horizon = _horizon_from_lead(lead_hours)
    deadline = local_end if horizon == "d0" else local_start
    available_at = run_at
    if available_at > deadline:
        reason = "run_at_after_target_day" if horizon == "d0" else "run_at_after_target_start"
        return _invalid(reason)

    members_payload = record.get("members")
    if not isinstance(members_payload, list) or not members_payload:
        return _invalid("members_missing")
    members: list[dict[str, Any]] = []
    member_highs: list[float] = []
    for member_index, member in enumerate(members_payload, start=1):
        normalized_member = _normalize_member(member, member_index, local_start, local_end, strict)
        if not normalized_member["ok"]:
            return _invalid(f"member_{member_index}_{normalized_member['reason']}")
        members.append(normalized_member["member"])
        member_highs.append(float(normalized_member["member"]["high_temp"]))

    mean_high = _float(record.get("mean_high"))
    if mean_high is None:
        mean_high = sum(member_highs) / len(member_highs)
    std_high = _float(record.get("std_high"))
    if std_high is None:
        std_high = _population_std(member_highs)

    station_id = str(record.get("station_id") or (profile.station_id if profile else "") or "")
    station_name = str(record.get("station_name") or (profile.station_name if profile else "") or "")
    unit = str(record.get("unit") or (profile.unit if profile else "") or "").upper()
    raw_hash = str(record.get("raw_response_hash") or _stable_hash(record))
    run_key = str(
        record.get("run_key")
        or ":".join([
            "archive",
            source or "unknown",
            model or "unknown",
            city,
            target_date,
            run_at.isoformat(),
            valid_at.isoformat(),
            raw_hash[:16],
        ])
    )
    quality_flags = list(record.get("quality_flags") or [])
    if source in {"open_meteo_historical_forecast", "open_meteo_archive"}:
        quality_flags.append("historical_continuous_product_review_required")

    run = {
        "run_key": run_key,
        "city": city,
        "target_date": target_date,
        "source": source or "unknown",
        "provider": provider or source or "unknown",
        "model": model or source or "unknown",
        "model_version": model_version or "unknown",
        "run_type": str(record.get("run_type") or "forecast"),
        "run_at": run_at.isoformat(),
        "retrieved_at": (retrieved_at or run_at).isoformat(),
        "valid_at": valid_at.isoformat(),
        "horizon": horizon,
        "lead_hours": lead_hours,
        "latitude": _float(record.get("latitude")) if record.get("latitude") is not None else (profile.latitude if profile else None),
        "longitude": _float(record.get("longitude")) if record.get("longitude") is not None else (profile.longitude if profile else None),
        "station_id": station_id,
        "station_name": station_name,
        "timezone": timezone_name,
        "unit": unit,
        "mean_high": mean_high,
        "std_high": std_high,
        "member_count": len(members),
        "source_url": record.get("source_url"),
        "raw_response_hash": raw_hash,
        "data_license": record.get("data_license"),
        "quality_flags": sorted(set(str(flag) for flag in quality_flags if flag)),
        "training_eligible": True,
        "ineligibility_reason": "",
        "archive_imported": True,
    }
    return {"ok": True, "run": run, "members": members}


def _load_archive_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".jsonl":
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
        return records
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("runs"), list):
        return payload["runs"]
    if isinstance(payload, dict):
        return [payload]
    raise ValueError("archive must be a JSON object, a JSON array, or JSONL records")


def _normalize_member(
    member: dict[str, Any],
    member_index: int,
    local_start: datetime,
    local_end: datetime,
    strict: bool,
) -> dict[str, Any]:
    if not isinstance(member, dict):
        return _invalid("not_object")
    member_id = str(member.get("member_id") or member.get("member_name") or "").strip()
    if strict and not member_id:
        return _invalid("id_missing")
    member_id = member_id or f"member{member_index:03d}"
    high_temp = _float(member.get("high_temp"))
    hourly = member.get("hourly") or member.get("hourly_json") or []
    if high_temp is None and isinstance(hourly, list):
        high_temp = _daily_max_from_hourly(hourly, local_start, local_end)
    if high_temp is None:
        return _invalid("high_temp_missing")
    return {
        "ok": True,
        "member": {
            **member,
            "member_id": member_id,
            "member_name": member.get("member_name") or member_id,
            "high_temp": high_temp,
            "hourly": hourly if isinstance(hourly, list) else [],
        },
    }


def _daily_max_from_hourly(hourly: list[Any], local_start: datetime, local_end: datetime) -> float | None:
    values: list[float] = []
    for item in hourly:
        if not isinstance(item, dict):
            continue
        valid_at = _parse_time(item.get("valid_at") or item.get("time") or item.get("timestamp"))
        if valid_at is None or not (local_start <= valid_at <= local_end):
            continue
        for key in TEMPERATURE_KEYS:
            value = _float(item.get(key))
            if value is not None:
                values.append(value)
                break
    return max(values) if values else None


def _local_day_bounds(target_date: str, timezone_name: str) -> tuple[datetime, datetime] | None:
    try:
        tz = ZoneInfo(timezone_name)
        local_date = datetime.strptime(target_date, "%Y-%m-%d").date()
    except (ValueError, ZoneInfoNotFoundError):
        return None
    return (
        datetime.combine(local_date, time.min, tzinfo=tz).astimezone(timezone.utc),
        datetime.combine(local_date, time.max, tzinfo=tz).astimezone(timezone.utc),
    )


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _horizon_from_lead(lead_hours: float) -> str:
    if lead_hours >= 48:
        return "d2_plus"
    if lead_hours >= 24:
        return "d1"
    return "d0"


def _population_std(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def _stable_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _invalid(reason: str) -> dict[str, Any]:
    return {"ok": False, "reason": reason}
