from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import DATA_DIR
from .db import connect, init_v3_db
from .forecasts.ensemble import BIAS_MIN_SAMPLE_COUNT, model_family
from .forecast_time import assess_forecast_run, parse_utc
from .registry import SETTLEMENT_REGISTRY, forecast_source_matches_profile_location


DEFAULT_BIAS_TABLE = DATA_DIR / "bias_table.json"
COMMON_MODELS = ("weathercom_v3", "ecmwf", "gfs", "icon", "gem", "jma")
DEFAULT_CITY_MODELS = {
    "atlanta": (*COMMON_MODELS, "hrrr", "nbm"),
    "beijing": (*COMMON_MODELS, "cma"),
    "chicago": (*COMMON_MODELS, "hrrr", "nbm"),
    "dallas": (*COMMON_MODELS, "hrrr", "nbm"),
    "hong-kong": (*COMMON_MODELS, "cma"),
    "nyc": (*COMMON_MODELS, "hrrr", "nbm"),
    "qingdao": (*COMMON_MODELS, "cma"),
    "seoul": COMMON_MODELS,
    "shanghai": (*COMMON_MODELS, "cma"),
    "shenzhen": (*COMMON_MODELS, "cma"),
    "singapore": COMMON_MODELS,
    "taipei": COMMON_MODELS,
    "tokyo": COMMON_MODELS,
    "wuhan": (*COMMON_MODELS, "cma"),
}


def train_bias_table(
    *,
    cities: list[str] | None = None,
    days: int = 90,
    path: Path | None = None,
    output_path: Path | None = None,
    as_of_date_exclusive: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Train auditable additive model corrections without target-day leakage.

    Exact settlement truth is preferred in this order: HKO for Hong Kong,
    Wunderground for all other supported markets, then IEM ASOS only as an
    explicitly labelled approximation. For each city/model/date, only the
    latest forecast snapshot whose real or archived run time is before the
    target local day is eligible.
    """
    init_v3_db(path)
    selected = _selected_cities(cities, path)
    rows: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()
    lookback_days = max(1, int(days or 90))
    for city in selected:
        profile = SETTLEMENT_REGISTRY.get(city)
        if not profile:
            continue
        truth_by_date = _truth_by_date(
            profile.station_id,
            city,
            path,
            before_date=as_of_date_exclusive,
        )
        for family in DEFAULT_CITY_MODELS.get(city, COMMON_MODELS):
            records, audit = _residual_records_for_family(
                city,
                family,
                truth_by_date,
                lookback_days,
                profile.timezone,
                path,
            )
            residuals = [float(record["residual_c"]) for record in records]
            additive_bias = statistics.median(residuals) if residuals else 0.0
            corrected = [value - additive_bias for value in residuals]
            recent_corrected = corrected[-7:]
            truth_counts = Counter(str(record["truth_basis"]) for record in records)
            rows.append({
                "city": city,
                "icao": profile.station_id,
                "location_version": profile.location_version,
                "latitude": profile.latitude,
                "longitude": profile.longitude,
                "model": family,
                "additive_bias_c": round(float(additive_bias), 4),
                "raw_mae_c": _round_metric(_mae(residuals)),
                "mae_c": _round_metric(_mae(corrected)),
                "rmse_c": _round_metric(_rmse(corrected)),
                "mae_7d_c": _round_metric(_mae(recent_corrected)),
                "sample_count": len(records),
                "independent_dates": len(records),
                "sample_dates": [record["target_date"] for record in records],
                "truth_basis_counts": dict(sorted(truth_counts.items())),
                "lookback_days": lookback_days,
                "last_trained_at": now,
                "bias_definition": "forecast_high_c_minus_truth_high_c",
                "correction_application": "forecast_high_c_minus_additive_bias_c",
                "lead_time_policy": "prefer_openmeteo_previous_day1_then_latest_pre_local_day",
                "archived_previous_day1_samples": sum(
                    1 for record in records if record.get("forecast_snapshot_type") == "openmeteo_previous_day1"
                ),
                "runtime_eligible": len(records) >= BIAS_MIN_SAMPLE_COUNT,
                "minimum_runtime_samples": BIAS_MIN_SAMPLE_COUNT,
                **audit,
            })
    payload = {
        "generated_at": now,
        "rows": rows,
        "row_count": len(rows),
        "city_count": len(selected),
        "runtime_eligible_rows": sum(1 for row in rows if row["runtime_eligible"]),
        "source": "weatherbot_v3.bias.train_bias_table",
        "training_policy": {
            "truth_priority": ["hong_kong_observatory_daily_extract", "wunderground_daily", "iem_asos_approximation"],
            "forecast_cutoff": "prefer fixed T+24 previous_day1; otherwise latest forecast as_of strictly before target local-day start",
            "independent_sample": "one forecast snapshot per city/model/target_date",
            "minimum_runtime_samples": BIAS_MIN_SAMPLE_COUNT,
            "as_of_date_exclusive": as_of_date_exclusive or "",
        },
    }
    if persist:
        destination = output_path or DEFAULT_BIAS_TABLE
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(f"{destination.suffix}.tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(destination)
    return payload


def _selected_cities(cities: list[str] | None, path: Path | None) -> list[str]:
    requested = [str(city).strip().lower() for city in (cities or []) if str(city).strip()]
    if requested:
        return list(dict.fromkeys(requested))
    with connect(path) as conn:
        enabled = [
            str(row["city_key"])
            for row in conn.execute("SELECT city_key FROM stations WHERE enabled = 1 ORDER BY tier, city_key").fetchall()
            if str(row["city_key"] or "") in DEFAULT_CITY_MODELS
        ]
    return enabled or list(DEFAULT_CITY_MODELS)


def _truth_by_date(
    station_id: str,
    city: str,
    path: Path | None,
    *,
    before_date: str | None = None,
) -> dict[str, dict[str, Any]]:
    station = str(station_id or "").upper()
    truth: dict[str, dict[str, Any]] = {}
    with connect(path) as conn:
        for row in conn.execute(
            """
            SELECT date_local, high_c
            FROM truth_iem_daily
            WHERE UPPER(icao) = ? AND high_c IS NOT NULL
            ORDER BY date_local
            """,
            (station,),
        ).fetchall():
            date_local = str(row["date_local"])
            if before_date and date_local >= before_date:
                continue
            truth[date_local] = {
                "high_c": float(row["high_c"]),
                "basis": "iem_asos_approximation",
                "exact": False,
            }
        for row in conn.execute(
            """
            SELECT date_local, high_c
            FROM truth_wunderground_daily
            WHERE UPPER(icao) = ? AND high_c IS NOT NULL
            ORDER BY date_local
            """,
            (station,),
        ).fetchall():
            date_local = str(row["date_local"])
            if before_date and date_local >= before_date:
                continue
            truth[date_local] = {
                "high_c": float(row["high_c"]),
                "basis": "wunderground_daily",
                "exact": True,
            }
        if city == "hong-kong":
            for row in conn.execute(
                """
                SELECT date_local, high_c
                FROM truth_hko_daily
                WHERE high_c IS NOT NULL
                ORDER BY date_local
                """
            ).fetchall():
                date_local = str(row["date_local"])
                if before_date and date_local >= before_date:
                    continue
                truth[date_local] = {
                    "high_c": float(row["high_c"]),
                    "basis": "hong_kong_observatory_daily_extract",
                    "exact": True,
                }
    return truth


def _residual_records_for_family(
    city: str,
    family: str,
    truth_by_date: dict[str, dict[str, Any]],
    days: int,
    timezone_name: str,
    path: Path | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not truth_by_date:
        return [], {"candidate_rows": 0, "leakage_excluded_rows": 0, "duplicate_rows": 0}
    target_dates = list(sorted(truth_by_date.keys(), reverse=True))[: max(1, int(days or 90))]
    with connect(path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, target_date, source, model, unit, mean_high, source_url,
                       run_at, retrieved_at, available_at, availability_basis,
                       valid_at, lead_hours, horizon, timezone,
                       training_eligible, parse_status
                FROM forecast_runs
                WHERE city = ?
                  AND target_date IN ({})
                  AND mean_high IS NOT NULL
                  AND COALESCE(training_eligible, 0) = 1
                  AND COALESCE(parse_status, 'parsed') = 'parsed'
                ORDER BY target_date, id
                """.format(",".join("?" for _ in target_dates)),
                (city, *target_dates),
            ).fetchall()
        ]
    candidates: dict[str, list[dict[str, Any]]] = {}
    family_rows = 0
    leakage_excluded = 0
    invalid_as_of = 0
    location_mismatch_excluded = 0
    for row in rows:
        if model_family(row.get("source") or row.get("model") or "") != family:
            continue
        family_rows += 1
        profile = SETTLEMENT_REGISTRY.get(city)
        if not forecast_source_matches_profile_location(row.get("source_url"), profile):
            location_mismatch_excluded += 1
            continue
        target_date = str(row.get("target_date") or "")
        assessment = assess_forecast_run(
            row,
            target_date=target_date,
            timezone_name=timezone_name,
            require_training=True,
        )
        if not assessment["ok"]:
            invalid_as_of += 1
            continue
        as_of = parse_utc(assessment.get("available_at"))
        cutoff = _target_local_start_utc(target_date, timezone_name)
        if as_of is None or cutoff is None:
            invalid_as_of += 1
            continue
        if as_of >= cutoff:
            leakage_excluded += 1
            continue
        row["as_of"] = as_of
        candidates.setdefault(target_date, []).append(row)
    records: list[dict[str, Any]] = []
    for target_date in sorted(candidates):
        preferred = [row for row in candidates[target_date] if _is_previous_day1(row)]
        selected = max(preferred or candidates[target_date], key=lambda row: (row["as_of"], int(row.get("id") or 0)))
        truth = truth_by_date[target_date]
        forecast_c = _to_c(float(selected["mean_high"]), str(selected.get("unit") or "C"))
        records.append({
            "target_date": target_date,
            "forecast_c": forecast_c,
            "truth_c": float(truth["high_c"]),
            "truth_basis": truth["basis"],
            "truth_exact": bool(truth["exact"]),
            "residual_c": forecast_c - float(truth["high_c"]),
            "source": str(selected.get("source") or ""),
            "forecast_snapshot_type": "openmeteo_previous_day1" if _is_previous_day1(selected) else "latest_pre_local_day",
            "forecast_run_id": int(selected.get("id") or 0),
            "forecast_as_of": selected["as_of"].isoformat(),
            "lead_hours": selected.get("lead_hours"),
            "horizon": selected.get("horizon"),
        })
    return records, {
        "candidate_rows": family_rows,
        "location_mismatch_excluded_rows": location_mismatch_excluded,
        "leakage_excluded_rows": leakage_excluded,
        "invalid_as_of_rows": invalid_as_of,
        "duplicate_rows": max(0, family_rows - leakage_excluded - invalid_as_of - len(records)),
    }


def _is_previous_day1(row: dict[str, Any]) -> bool:
    source = str(row.get("source") or "").lower()
    horizon = str(row.get("horizon") or "").upper()
    return "openmeteo_previous_" in source and source.endswith("_day1") and horizon == "D+1"


def _target_local_start_utc(target_date: str, timezone_name: str) -> datetime | None:
    try:
        local_date = datetime.fromisoformat(target_date).date()
        return datetime.combine(local_date, datetime.min.time(), tzinfo=ZoneInfo(timezone_name)).astimezone(timezone.utc)
    except Exception:
        return None


def _parse_datetime(value: Any) -> datetime | None:
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


def _mae(values: list[float]) -> float | None:
    return sum(abs(value) for value in values) / len(values) if values else None


def _rmse(values: list[float]) -> float | None:
    return math.sqrt(sum(value * value for value in values) / len(values)) if values else None


def _round_metric(value: float | None) -> float | None:
    return None if value is None else round(float(value), 4)


def _to_c(value: float, unit: str) -> float:
    return (value - 32.0) * 5.0 / 9.0 if str(unit or "C").upper() == "F" else value
