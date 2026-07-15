from __future__ import annotations

import hashlib
import json
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .env_utils import env_value


ONLINE_AVAILABILITY_BASIS = "retrieved_at"
ARCHIVE_AVAILABILITY_BASIS = "archive_run_at"
DEFAULT_COMPONENT_MAX_AGE_HOURS = 18.0
DEFAULT_COMPONENT_MAX_SKEW_HOURS = 12.0
FORECAST_COMPONENT_COHORT_VERSION = "forecast-component-cohort-v1"


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


def apply_forecast_component_cohort(
    components: list[dict[str, Any]],
    *,
    as_of: str | datetime,
    max_age_hours: float | None = None,
    max_skew_hours: float | None = None,
) -> dict[str, Any]:
    """Fail closed when a DEB mixes stale or asynchronous source evidence."""

    cutoff = as_of if isinstance(as_of, datetime) else parse_utc(as_of)
    if cutoff is None:
        return {
            "components": [],
            "excluded": [dict(component) for component in components],
            "warnings": ["forecast_component_as_of_invalid"],
            "cohort_as_of": "",
        }
    cutoff = cutoff.astimezone(timezone.utc)
    age_limit = _positive_float(
        max_age_hours,
        env_value("DEB_COMPONENT_MAX_AGE_HOURS", str(DEFAULT_COMPONENT_MAX_AGE_HOURS)),
        DEFAULT_COMPONENT_MAX_AGE_HOURS,
    )
    skew_limit = _positive_float(
        max_skew_hours,
        env_value("DEB_COMPONENT_MAX_SKEW_HOURS", str(DEFAULT_COMPONENT_MAX_SKEW_HOURS)),
        DEFAULT_COMPONENT_MAX_SKEW_HOURS,
    )

    prepared: list[tuple[dict[str, Any], datetime]] = []
    excluded: list[dict[str, Any]] = []
    warnings: list[str] = []
    for source_component in components:
        component = dict(source_component)
        source = str(component.get("family") or component.get("source") or "unknown")
        available_at = parse_utc(
            component.get("effective_available_at")
            or component.get("peak_available_at")
            or component.get("available_at")
            or component.get("retrieved_at")
        )
        if available_at is None:
            component["cohort_exclusion_reason"] = "forecast_component_available_at_missing"
            excluded.append(component)
            warnings.append(f"forecast_component_available_at_missing:{source}")
            continue
        age_hours = (cutoff - available_at).total_seconds() / 3600.0
        component.update({
            "cohort_as_of": cutoff.isoformat(),
            "effective_available_at": available_at.isoformat(),
            "source_age_hours": round(age_hours, 4),
            "max_source_age_hours": age_limit,
        })
        if age_hours < -1e-6:
            component["source_age_ok"] = False
            component["cohort_exclusion_reason"] = "forecast_component_after_as_of"
            excluded.append(component)
            warnings.append(f"forecast_component_after_as_of:{source}")
            continue
        if age_hours > age_limit:
            component["source_age_ok"] = False
            component["cohort_exclusion_reason"] = "forecast_component_stale"
            excluded.append(component)
            warnings.append(f"forecast_component_stale:{source}:{age_hours:.1f}h")
            continue
        component["source_age_ok"] = True
        prepared.append((component, available_at))

    newest = max((available_at for _component, available_at in prepared), default=None)
    kept: list[dict[str, Any]] = []
    for component, available_at in prepared:
        source = str(component.get("family") or component.get("source") or "unknown")
        skew_hours = (newest - available_at).total_seconds() / 3600.0 if newest else 0.0
        component.update({
            "source_skew_hours": round(skew_hours, 4),
            "max_source_skew_hours": skew_limit,
            "source_skew_ok": skew_hours <= skew_limit,
        })
        if skew_hours > skew_limit:
            component["cohort_exclusion_reason"] = "forecast_component_skew_exceeded"
            excluded.append(component)
            warnings.append(f"forecast_component_skew_exceeded:{source}:{skew_hours:.1f}h")
            continue
        kept.append(component)

    return {
        "components": kept,
        "excluded": excluded,
        "warnings": sorted(set(warnings)),
        "cohort_as_of": cutoff.isoformat(),
        "max_age_hours": age_limit,
        "max_skew_hours": skew_limit,
    }


def forecast_component_cohort_as_of(
    components: list[dict[str, Any]],
    *,
    requested_as_of: str | datetime,
    target_date: str,
    timezone_name: str,
) -> tuple[str | datetime, bool]:
    """Preserve the caller's cutoff; historical replay must never rebase itself."""

    del components, target_date, timezone_name
    cutoff = requested_as_of if isinstance(requested_as_of, datetime) else parse_utc(requested_as_of)
    return (cutoff if cutoff is not None else requested_as_of), False


def historical_build_requires_explicit_as_of(
    target_date: str,
    timezone_name: str,
    *,
    reference: str | datetime | None = None,
) -> bool:
    """Return true when rebuilding a completed local day needs an explicit cutoff."""

    current = reference if isinstance(reference, datetime) else parse_utc(reference)
    current = current or datetime.now(timezone.utc)
    try:
        target = datetime.strptime(str(target_date), "%Y-%m-%d").date()
        local_today = current.astimezone(ZoneInfo(timezone_name)).date()
    except (ValueError, ZoneInfoNotFoundError):
        return False
    return target < local_today


def persisted_prediction_cohort_status(prediction: dict[str, Any]) -> dict[str, Any]:
    """Validate the source-cohort contract on a persisted DEB prediction."""
    raw = prediction.get("raw") if isinstance(prediction.get("raw"), dict) else {}
    algorithm = str(
        prediction.get("forecast_algo")
        or prediction.get("method")
        or prediction.get("deb_version")
        or raw.get("forecast_algo")
        or raw.get("algo")
        or ""
    ).strip().lower()
    if algorithm not in {"polywx_aligned_deb_v1", "polywx", "polywx_aligned"}:
        return {"ok": True, "applicable": False, "version": "", "reasons": []}

    version = str(
        prediction.get("cohort_contract_version")
        or raw.get("cohort_contract_version")
        or ""
    ).strip()
    cohort_as_of = str(
        prediction.get("cohort_as_of")
        or raw.get("cohort_as_of")
        or ""
    ).strip()
    components = prediction.get("components")
    if not isinstance(components, list):
        components = raw.get("components") if isinstance(raw.get("components"), list) else []

    reasons: list[str] = []
    if version != FORECAST_COMPONENT_COHORT_VERSION:
        reasons.append("prediction_missing_source_cohort_contract")
    if not parse_utc(cohort_as_of):
        reasons.append("prediction_cohort_as_of_missing")
    if not components:
        reasons.append("prediction_components_missing")
    for component in components:
        if not isinstance(component, dict):
            reasons.append("prediction_component_invalid")
            continue
        source = str(component.get("source") or "unknown")
        if component.get("source_age_ok") is not True:
            reasons.append(f"prediction_component_age_unverified:{source}")
        if component.get("source_skew_ok") is not True:
            reasons.append(f"prediction_component_skew_unverified:{source}")

    return {
        "ok": not reasons,
        "applicable": True,
        "version": version,
        "cohort_as_of": cohort_as_of,
        "reasons": list(dict.fromkeys(reasons)),
    }


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


def _positive_float(value: Any, fallback: Any, default: float) -> float:
    for candidate in (value, fallback, default):
        try:
            parsed = float(candidate)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return float(default)


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off", "none"}
    return bool(value)
