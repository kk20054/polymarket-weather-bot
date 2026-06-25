from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import load_config
from .db import connect, init_v3_db
from .registry import REGISTRY_VERSION, SETTLEMENT_REGISTRY


AUDIT_VERSION = "data-readiness-v1"
LIVE_TRUTH_PROVIDERS = {
    "polymarket_resolved",
    "nws_station",
    "aviationweather_station",
    "visual_crossing_station",
}


def build_data_readiness(path: Path | None = None) -> dict[str, Any]:
    init_v3_db(path)
    cfg = load_config()
    now = datetime.now(timezone.utc)
    with connect(path) as conn:
        rules = [dict(row) for row in conn.execute("SELECT * FROM market_rules").fetchall()]
        truths = [dict(row) for row in conn.execute("SELECT * FROM truth_observations").fetchall()]
        forecast_runs = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM forecast_runs WHERE COALESCE(run_type, 'forecast') = 'forecast'"
            ).fetchall()
        ]
        observation_runs = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM forecast_runs WHERE run_type = 'observation'"
            ).fetchall()
        ]
        forecast_member_count = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM forecast_members fm
                JOIN forecast_runs fr ON fr.id = fm.run_id
                WHERE COALESCE(fr.run_type, 'forecast') = 'forecast'
                """
            ).fetchone()[0]
        )
        orderbooks = [dict(row) for row in conn.execute("SELECT * FROM orderbooks").fetchall()]

    rules_by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    truths_by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    runs_by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rules:
        rules_by_city[str(row.get("city") or "")].append(row)
    for row in truths:
        truths_by_city[str(row.get("city") or "")].append(row)
    for row in forecast_runs:
        runs_by_city[str(row.get("city") or "")].append(row)

    city_rows = []
    for city, profile in SETTLEMENT_REGISTRY.items():
        city_rules = rules_by_city.get(city, [])
        city_truth = truths_by_city.get(city, [])
        city_runs = runs_by_city.get(city, [])
        fresh_city_runs = [
            row for row in city_runs
            if _age_minutes(row.get("retrieved_at") or row.get("created_at"), now) <= cfg.forecast_max_age_minutes
        ]
        eligible_days = {
            str(row.get("target_date") or "")
            for row in city_truth
            if row.get("calibration_eligible")
            and row.get("provider") in LIVE_TRUTH_PROVIDERS
            and row.get("actual_temp") is not None
        }
        all_truth_days = {str(row.get("target_date") or "") for row in city_truth if row.get("target_date")}
        providers = Counter(str(row.get("provider") or "unknown") for row in city_truth)
        station_mismatch = sum(
            1 for row in city_rules
            if str(row.get("station_id") or "").upper() != profile.station_id
        )
        timezone_mismatch = sum(
            1 for row in city_rules
            if str(row.get("timezone") or "") != profile.timezone
        )
        verified_rules = sum(1 for row in city_rules if row.get("manual_verified_at"))
        reasons = []
        if not city_rules:
            reasons.append("market_rule_missing")
        if station_mismatch:
            reasons.append("station_mapping_mismatch")
        if timezone_mismatch:
            reasons.append("timezone_mismatch")
        if verified_rules == 0:
            reasons.append("settlement_rule_not_manually_verified")
        if len(eligible_days) < cfg.min_independent_settlement_days:
            reasons.append("independent_truth_days_below_min")
        if not city_runs:
            reasons.append("versioned_forecast_runs_missing")
        elif not fresh_city_runs:
            reasons.append("forecast_runs_stale")
        city_rows.append({
            **profile.to_dict(),
            "market_rules": len(city_rules),
            "verified_rules": verified_rules,
            "station_mismatches": station_mismatch,
            "timezone_mismatches": timezone_mismatch,
            "truth_days": len(all_truth_days),
            "eligible_truth_days": len(eligible_days),
            "truth_providers": dict(providers),
            "forecast_runs": len(city_runs),
            "fresh_forecast_runs": len(fresh_city_runs),
            "status": "eligible" if not reasons else "blocked",
            "reasons": reasons,
        })

    total_truth_days = {
        (str(row.get("city") or ""), str(row.get("target_date") or ""))
        for row in truths
        if row.get("target_date")
    }
    eligible_truth_days = {
        (str(row.get("city") or ""), str(row.get("target_date") or ""))
        for row in truths
        if row.get("calibration_eligible")
        and row.get("provider") in LIVE_TRUTH_PROVIDERS
        and row.get("actual_temp") is not None
    }
    provider_counts = Counter(str(row.get("provider") or "unknown") for row in truths)
    timezone_database_available = _timezone_database_available()
    forecast_source_counts = Counter(str(row.get("source") or "unknown") for row in forecast_runs)
    training_eligible_runs = [row for row in forecast_runs if row.get("training_eligible")]
    fresh_forecast_runs = [
        row for row in forecast_runs
        if _age_minutes(row.get("retrieved_at") or row.get("created_at"), now) <= cfg.forecast_max_age_minutes
    ]
    fresh_forecast_cities = {str(row.get("city") or "") for row in fresh_forecast_runs}
    fresh_training_runs = [row for row in fresh_forecast_runs if row.get("training_eligible")]
    fresh_training_cities = {str(row.get("city") or "") for row in fresh_training_runs}
    utc_rules = sum(1 for row in rules if str(row.get("timezone") or "") == "UTC")
    unverified_rules = sum(1 for row in rules if not row.get("manual_verified_at"))
    missing_rule_source = sum(
        1 for row in rules
        if not row.get("resolution_source_text") or not row.get("source_url")
    )
    stale_orderbooks = 0
    latest_orderbook_at = None
    fresh_clob_orderbooks = []
    for row in orderbooks:
        quote_at = _orderbook_time(row.get("quote_timestamp")) or _parse_time(row.get("created_at"))
        if quote_at and (latest_orderbook_at is None or quote_at > latest_orderbook_at):
            latest_orderbook_at = quote_at
        if not quote_at or (now - quote_at).total_seconds() > cfg.orderbook_max_age_minutes * 60:
            stale_orderbooks += 1
        elif row.get("snapshot_type") == "clob" and row.get("bids_json") and row.get("asks_json"):
            fresh_clob_orderbooks.append(row)

    stages = [
        _stage(
            "settlement_contracts",
            "结算合同",
            (
                len(rules) > 0
                and unverified_rules == 0
                and utc_rules == 0
                and missing_rule_source == 0
                and timezone_database_available
            ),
            [
                ("settlement_rule_not_manually_verified", unverified_rules),
                ("timezone_mismatch", utc_rules),
                ("resolution_source_missing", missing_rule_source),
                ("timezone_database_unavailable", 1 if not timezone_database_available else 0),
            ],
            {
                "rules": len(rules),
                "cities": len(rules_by_city),
                "registry_version": REGISTRY_VERSION,
                "timezone_database_available": timezone_database_available,
            },
        ),
        _stage(
            "truth",
            "结算 Truth",
            len(eligible_truth_days) >= cfg.min_independent_settlement_days and provider_counts.get("legacy_unknown", 0) == 0,
            [
                ("independent_truth_days_below_min", max(0, cfg.min_independent_settlement_days - len(eligible_truth_days))),
                ("legacy_truth_unknown", provider_counts.get("legacy_unknown", 0)),
                ("open_meteo_fallback_present", provider_counts.get("open_meteo_archive", 0)),
            ],
            {
                "eligible_days": len(eligible_truth_days),
                "total_days": len(total_truth_days),
                "providers": dict(provider_counts),
                "minimum_days": cfg.min_independent_settlement_days,
            },
        ),
        _stage(
            "forecast_runs",
            "预测运行档案",
            (
                len(forecast_runs) > 0
                and forecast_member_count > 0
                and len(fresh_training_cities) == len(SETTLEMENT_REGISTRY)
                and forecast_source_counts.get("ecmwf", 0) > 0
                and forecast_source_counts.get("gfs_ensemble", 0) > 0
            ),
            [
                ("versioned_forecast_runs_missing", 1 if not forecast_runs else 0),
                ("forecast_members_missing", 1 if forecast_member_count == 0 else 0),
                (
                    "forecast_city_coverage_incomplete",
                    max(0, len(SETTLEMENT_REGISTRY) - len(fresh_training_cities)),
                ),
                ("ecmwf_runs_missing", 1 if forecast_source_counts.get("ecmwf", 0) == 0 else 0),
                ("gfs_ensemble_runs_missing", 1 if forecast_source_counts.get("gfs_ensemble", 0) == 0 else 0),
            ],
            {
                "runs": len(forecast_runs),
                "members": forecast_member_count,
                "cities": len(runs_by_city),
                "fresh_runs": len(fresh_forecast_runs),
                "fresh_cities": len(fresh_forecast_cities),
                "fresh_training_runs": len(fresh_training_runs),
                "fresh_training_cities": len(fresh_training_cities),
                "max_age_minutes": cfg.forecast_max_age_minutes,
                "sources": dict(forecast_source_counts),
                "observation_runs": len(observation_runs),
                "training_eligible_runs": len(training_eligible_runs),
            },
        ),
        _stage(
            "orderbooks",
            "盘口快照",
            len(fresh_clob_orderbooks) > 0,
            [
                ("orderbook_snapshots_missing", 1 if not orderbooks else 0),
                ("all_orderbooks_stale", 1 if orderbooks and stale_orderbooks == len(orderbooks) else 0),
                ("fresh_clob_depth_missing", 1 if orderbooks and not fresh_clob_orderbooks else 0),
            ],
            {
                "snapshots": len(orderbooks),
                "stale_snapshots": stale_orderbooks,
                "fresh_clob_snapshots": len(fresh_clob_orderbooks),
                "latest_at": latest_orderbook_at.isoformat() if latest_orderbook_at else None,
            },
        ),
    ]
    passed = sum(1 for stage in stages if stage["status"] == "ready")
    blockers = [
        reason
        for stage in stages
        for reason in stage["reasons"]
        if reason["count"] > 0
    ]
    return {
        "audit_version": AUDIT_VERSION,
        "generated_at": now.isoformat(),
        "status": "ready" if passed == len(stages) else "blocked",
        "score": round(passed / len(stages), 3),
        "live_allowed": passed == len(stages),
        "stages": stages,
        "blockers": blockers,
        "cities": city_rows,
        "summary": {
            "registered_cities": len(SETTLEMENT_REGISTRY),
            "eligible_cities": sum(1 for row in city_rows if row["status"] == "eligible"),
            "market_rules": len(rules),
            "eligible_truth_days": len(eligible_truth_days),
            "forecast_runs": len(forecast_runs),
            "forecast_members": forecast_member_count,
            "orderbook_snapshots": len(orderbooks),
        },
    }


def persist_data_readiness(payload: dict[str, Any], path: Path | None = None) -> None:
    init_v3_db(path)
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO data_qualification_audits (
                audit_version, status, score, live_allowed, raw_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("audit_version") or AUDIT_VERSION,
                payload.get("status") or "blocked",
                float(payload.get("score") or 0.0),
                1 if payload.get("live_allowed") else 0,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                payload.get("generated_at") or datetime.now(timezone.utc).isoformat(),
            ),
        )


def _stage(
    key: str,
    label: str,
    ready: bool,
    reasons: list[tuple[str, int]],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": "ready" if ready else "blocked",
        "reasons": [{"code": code, "count": int(count)} for code, count in reasons if count],
        "metrics": metrics,
    }


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


def _age_minutes(value: Any, now: datetime) -> float:
    parsed = _parse_time(value)
    if not parsed:
        return float("inf")
    return max(0.0, (now - parsed).total_seconds() / 60.0)


def _timezone_database_available() -> bool:
    try:
        ZoneInfo("America/New_York")
        ZoneInfo("Europe/London")
        return True
    except ZoneInfoNotFoundError:
        return False


def _orderbook_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        raw = str(value)
        if raw.isdigit():
            return datetime.fromtimestamp(int(raw) / 1000.0, tz=timezone.utc)
    except Exception:
        return None
    return _parse_time(value)
