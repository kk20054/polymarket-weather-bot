from __future__ import annotations

import hashlib
import json
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ONLINE_AVAILABILITY_BASIS = "retrieved_at"
ARCHIVE_AVAILABILITY_BASIS = "archive_run_at"


def parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def forecast_available_at(run: dict[str, Any]) -> tuple[datetime | None, str]:
    basis = str(run.get("availability_basis") or "").strip()
    explicit = parse_utc(run.get("available_at"))
    if basis == ARCHIVE_AVAILABILITY_BASIS:
        return parse_utc(run.get("run_at")) or explicit, ARCHIVE_AVAILABILITY_BASIS
    if basis == ONLINE_AVAILABILITY_BASIS:
        return parse_utc(run.get("retrieved_at")) or explicit, ONLINE_AVAILABILITY_BASIS
    if _trusted_archive(run):
        return parse_utc(run.get("run_at")), ARCHIVE_AVAILABILITY_BASIS
    return parse_utc(run.get("retrieved_at")), ONLINE_AVAILABILITY_BASIS


def assess_forecast_run(
    run: dict[str, Any],
    *,
    as_of: str | datetime | None = None,
    target_date: str | None = None,
    timezone_name: str | None = None,
    require_training: bool = False,
    respect_training_flag: bool = True,
) -> dict[str, Any]:
    parse_status = str(run.get("parse_status") or "parsed").strip().lower()
    if parse_status != "parsed":
        return _assessment(False, "forecast_parse_not_ready")

    available_at, basis = forecast_available_at(run)
    if available_at is None:
        return _assessment(False, "forecast_available_at_missing", basis=basis)

    cutoff = as_of if isinstance(as_of, datetime) else parse_utc(as_of)
    if as_of is not None and cutoff is None:
        return _assessment(False, "forecast_as_of_invalid", available_at, basis)
    if cutoff is not None:
        cutoff = cutoff.astimezone(timezone.utc)
        if available_at > cutoff:
            return _assessment(False, "forecast_not_available_as_of", available_at, basis)

    valid_at = parse_utc(run.get("valid_at"))
    effective_valid_lead = (
        (valid_at - available_at).total_seconds() / 3600.0
        if valid_at is not None
        else None
    )
    lead_hours = _number(run.get("lead_hours"))
    if lead_hours is None:
        lead_hours = effective_valid_lead
    if require_training:
        if respect_training_flag and not _truthy(run.get("training_eligible")):
            return _assessment(False, "forecast_training_ineligible", available_at, basis, lead_hours)
        if lead_hours is None:
            return _assessment(False, "forecast_lead_missing", available_at, basis)
        if lead_hours < 0 or (effective_valid_lead is not None and effective_valid_lead < 0):
            return _assessment(False, "forecast_lead_negative", available_at, basis, lead_hours)

        target = str(target_date or run.get("target_date") or "").strip()
        zone_name = str(timezone_name or run.get("timezone") or "").strip()
        bounds = _local_day_bounds(target, zone_name)
        if bounds is None:
            return _assessment(False, "forecast_target_time_invalid", available_at, basis, lead_hours)
        local_start, local_end = bounds
        horizon = _horizon_bucket(run, lead_hours)
        deadline = local_end if horizon == "d0" else local_start
        if available_at > deadline:
            reason = "forecast_after_target_day" if horizon == "d0" else "forecast_after_target_start"
            return _assessment(False, reason, available_at, basis, lead_hours, horizon)
    else:
        horizon = _horizon_bucket(run, lead_hours)

    return _assessment(True, "", available_at, basis, lead_hours, horizon)


def prepare_forecast_snapshot(
    run: dict[str, Any],
    members: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    prepared = dict(run)
    available_at, basis = forecast_available_at(prepared)
    if available_at is not None:
        prepared["available_at"] = available_at.isoformat()
    else:
        prepared["available_at"] = ""
    prepared["availability_basis"] = basis
    valid_at = parse_utc(prepared.get("valid_at"))
    if prepared.get("lead_hours") in (None, "") and available_at is not None and valid_at is not None:
        prepared["lead_hours"] = (valid_at - available_at).total_seconds() / 3600.0

    requested_training = _truthy(prepared.get("training_eligible"))
    assessment = assess_forecast_run(
        prepared,
        require_training=True,
        respect_training_flag=False,
    )
    if requested_training and assessment["ok"]:
        prepared["training_eligible"] = True
        prepared["ineligibility_reason"] = ""
    else:
        prepared["training_eligible"] = False
        prepared["ineligibility_reason"] = str(
            prepared.get("ineligibility_reason")
            or assessment.get("reason")
            or "forecast_training_not_requested"
        )

    snapshot_hash = _snapshot_content_hash(prepared, members or [])
    snapshot_key = _snapshot_key(prepared, snapshot_hash)
    legacy_run_key = str(prepared.get("run_key") or "")
    prepared["snapshot_key"] = snapshot_key
    prepared["snapshot_hash"] = snapshot_hash
    prepared["run_key"] = f"forecast:{snapshot_key}"
    if legacy_run_key and legacy_run_key != prepared["run_key"]:
        prepared["source_run_key"] = legacy_run_key
    return prepared


def _trusted_archive(run: dict[str, Any]) -> bool:
    flags = _list_value(run.get("quality_flags"))
    if "trusted_forecast_archive" in flags:
        return True
    raw = _dict_value(run.get("raw_json"))
    return bool(run.get("archive_imported") or raw.get("archive_imported"))


def _snapshot_content_hash(run: dict[str, Any], members: list[dict[str, Any]]) -> str:
    raw_hash = str(run.get("raw_response_hash") or "").strip()
    if raw_hash:
        return raw_hash.lower()
    excluded = {
        "run_key",
        "snapshot_key",
        "snapshot_hash",
        "source_run_key",
        "created_at",
        "updated_at",
    }
    payload = {
        "run": {key: value for key, value in run.items() if key not in excluded},
        "members": members,
    }
    text = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _snapshot_key(run: dict[str, Any], snapshot_hash: str) -> str:
    available_at, basis = forecast_available_at(run)
    parts = [
        str(run.get("provider") or run.get("source") or "unknown"),
        str(run.get("model") or "unknown"),
        str(run.get("city") or ""),
        str(run.get("target_date") or ""),
        available_at.isoformat() if available_at else "unavailable",
        basis,
        snapshot_hash,
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:40]


def _assessment(
    ok: bool,
    reason: str,
    available_at: datetime | None = None,
    basis: str = "",
    lead_hours: float | None = None,
    horizon: str = "unknown",
) -> dict[str, Any]:
    return {
        "ok": bool(ok),
        "reason": reason,
        "available_at": available_at.isoformat() if available_at else "",
        "availability_basis": basis,
        "lead_hours": lead_hours,
        "horizon_bucket": horizon,
    }


def _horizon_bucket(run: dict[str, Any], lead_hours: float | None) -> str:
    horizon = str(run.get("horizon") or "").strip().lower()
    if horizon.startswith("d+"):
        try:
            return "d0" if int(horizon[2:]) <= 0 else ("d1" if int(horizon[2:]) == 1 else "d2_plus")
        except ValueError:
            pass
    if lead_hours is None:
        return "unknown"
    if lead_hours >= 48:
        return "d2_plus"
    if lead_hours >= 24:
        return "d1"
    return "d0"


def _local_day_bounds(target_date: str, timezone_name: str) -> tuple[datetime, datetime] | None:
    if not target_date or not timezone_name:
        return None
    try:
        local_date = datetime.strptime(target_date, "%Y-%m-%d").date()
        zone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError):
        return None
    return (
        datetime.combine(local_date, time.min, tzinfo=zone).astimezone(timezone.utc),
        datetime.combine(local_date, time.max, tzinfo=zone).astimezone(timezone.utc),
    )


def _dict_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except (TypeError, ValueError):
            return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off", "none"}
    return bool(value)
