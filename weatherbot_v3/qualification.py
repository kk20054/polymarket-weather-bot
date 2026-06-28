from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, time, timezone
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
        contracts = [dict(row) for row in conn.execute("SELECT * FROM settlement_contracts").fetchall()]
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
    contracts_by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    truths_by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    runs_by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rules:
        rules_by_city[str(row.get("city") or "")].append(row)
    for row in contracts:
        contracts_by_city[str(row.get("city") or "")].append(row)
    for row in truths:
        truths_by_city[str(row.get("city") or "")].append(row)
    for row in forecast_runs:
        runs_by_city[str(row.get("city") or "")].append(row)

    city_rows = []
    for city, profile in SETTLEMENT_REGISTRY.items():
        city_rules = rules_by_city.get(city, [])
        city_contracts = contracts_by_city.get(city, [])
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
            1 for row in city_contracts
            if str(row.get("station_id") or "").upper() != profile.station_id
        )
        timezone_mismatch = sum(
            1 for row in city_contracts
            if str(row.get("timezone") or "") != profile.timezone
        )
        verified_rules = sum(1 for row in city_contracts if row.get("manual_verified_at"))
        auto_verified_rules = sum(1 for row in city_contracts if row.get("auto_verified_at"))
        reasons = []
        if not city_contracts:
            reasons.append("settlement_contract_missing")
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
            "settlement_contracts": len(city_contracts),
            "verified_rules": verified_rules,
            "auto_verified_rules": auto_verified_rules,
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
    eligible_provider_counts = Counter(
        str(row.get("provider") or "unknown")
        for row in truths
        if row.get("calibration_eligible") and row.get("actual_temp") is not None
    )
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
    utc_rules = sum(1 for row in contracts if str(row.get("timezone") or "") == "UTC")
    unverified_rules = sum(1 for row in contracts if not row.get("manual_verified_at"))
    auto_verified_rules = sum(1 for row in contracts if row.get("auto_verified_at"))
    contract_review_queue = _contract_review_queue(contracts, now)
    mature_auto_verified_unreviewed = int(
        contract_review_queue["counts"]["mature_auto_verified_unreviewed"]
    )
    missing_rule_source = sum(
        1 for row in contracts
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
                len(contracts) > 0
                and unverified_rules == 0
                and utc_rules == 0
                and missing_rule_source == 0
                and timezone_database_available
            ),
            [
                ("settlement_rule_not_manually_verified", unverified_rules),
                ("settlement_contracts_missing", 1 if not contracts else 0),
                ("timezone_mismatch", utc_rules),
                ("resolution_source_missing", missing_rule_source),
                ("timezone_database_unavailable", 1 if not timezone_database_available else 0),
            ],
            {
                "contracts": len(contracts),
                "market_rules": len(rules),
                "cities": len(contracts_by_city),
                "auto_verified_contracts": auto_verified_rules,
                "mature_auto_verified_unreviewed_contracts": mature_auto_verified_unreviewed,
                "manual_verified_contracts": len(contracts) - unverified_rules,
                "contract_review_queue": contract_review_queue["counts"],
                "contract_review_targets": contract_review_queue["targets"],
                "registry_version": REGISTRY_VERSION,
                "timezone_database_available": timezone_database_available,
            },
        ),
        _stage(
            "truth",
            "结算 Truth",
            len(eligible_truth_days) >= cfg.min_independent_settlement_days
            and eligible_provider_counts.get("legacy_unknown", 0) == 0,
            [
                ("independent_truth_days_below_min", max(0, cfg.min_independent_settlement_days - len(eligible_truth_days))),
                ("legacy_truth_unknown", eligible_provider_counts.get("legacy_unknown", 0)),
                ("open_meteo_fallback_present", provider_counts.get("open_meteo_archive", 0)),
            ],
            {
                "eligible_days": len(eligible_truth_days),
                "total_days": len(total_truth_days),
                "providers": dict(provider_counts),
                "eligible_providers": dict(eligible_provider_counts),
                "excluded_legacy_unknown": provider_counts.get("legacy_unknown", 0)
                - eligible_provider_counts.get("legacy_unknown", 0),
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
    phase = _production_phase(stages, blockers, len(contracts), len(rules))
    next_actions = _build_next_actions(stages, city_rows, contracts, now)
    return {
        "audit_version": AUDIT_VERSION,
        "generated_at": now.isoformat(),
        "status": "ready" if passed == len(stages) else "blocked",
        "score": round(passed / len(stages), 3),
        "live_allowed": passed == len(stages),
        "production_phase": phase,
        "stages": stages,
        "blockers": blockers,
        "next_actions": next_actions,
        "cities": city_rows,
        "summary": {
            "registered_cities": len(SETTLEMENT_REGISTRY),
            "eligible_cities": sum(1 for row in city_rows if row["status"] == "eligible"),
            "market_rules": len(rules),
            "settlement_contracts": len(contracts),
            "eligible_truth_days": len(eligible_truth_days),
            "forecast_runs": len(forecast_runs),
            "forecast_members": forecast_member_count,
            "orderbook_snapshots": len(orderbooks),
        },
    }


def _production_phase(
    stages: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    contract_count: int,
    rule_count: int,
) -> dict[str, Any]:
    stage_status = {str(stage.get("key")): str(stage.get("status")) for stage in stages}
    blocker_codes = {str(reason.get("code") or "") for reason in blockers}
    blocked_keys = [str(stage.get("key")) for stage in stages if stage.get("status") != "ready"]

    if not rule_count or not contract_count or "settlement_contracts_missing" in blocker_codes:
        return {
            "id": "phase0",
            "label": "Phase 0",
            "name": "审计与数据基座启动",
            "status": "active",
            "next": "Phase 1：合同、Truth、预测和盘口入库",
            "operator_action": "先同步市场规则和结算合同，再生成数据资格审计。",
            "blocked_keys": blocked_keys,
        }

    if "settlement_rule_not_manually_verified" in blocker_codes:
        contract_stage = next((stage for stage in stages if stage.get("key") == "settlement_contracts"), {})
        mature_auto_pending = int(
            ((contract_stage.get("metrics") or {}).get("mature_auto_verified_unreviewed_contracts") or 0)
        )
        operator_action = (
            "优先核验已成熟、自动解析可信的结算合同，清掉实盘数据闸门。"
            if mature_auto_pending
            else "成熟自动可信合同已处理完；继续补 Truth/预测/盘口，剩余合同需逐条人工核验。"
        )
        return {
            "id": "phase1_5",
            "label": "Phase 1.5",
            "name": "合同核验与数据闸门收尾",
            "status": "active",
            "next": "Phase 2：无泄漏概率模型与策略稳定化",
            "operator_action": operator_action,
            "blocked_keys": blocked_keys,
        }

    if any(status != "ready" for status in stage_status.values()):
        return {
            "id": "phase1",
            "label": "Phase 1",
            "name": "真实数据基座补齐",
            "status": "active",
            "next": "Phase 2：无泄漏概率模型与策略稳定化",
            "operator_action": "补齐 blocked 阶段所需的 Truth、预测运行或 CLOB 盘口快照。",
            "blocked_keys": blocked_keys,
        }

    return {
        "id": "phase2_ready",
        "label": "Phase 2",
        "name": "可进入概率模型稳定化",
        "status": "ready_for_next",
        "next": "Phase 2：训练、回放、校准和策略组验证",
        "operator_action": "数据闸门已过，下一步验证概率模型和 paper trading edge。",
        "blocked_keys": blocked_keys,
    }


def _contract_review_queue(
    contracts: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    counts = {
        "manual_verified": 0,
        "mature_auto_verified_unreviewed": 0,
        "future_auto_verified_unreviewed": 0,
        "manual_review_required_unverified": 0,
        "source_missing_unverified": 0,
        "low_confidence_unverified": 0,
    }
    targets: dict[str, list[dict[str, str]]] = {key: [] for key in counts}
    for contract in contracts:
        if contract.get("manual_verified_at"):
            counts["manual_verified"] += 1
            continue
        if not contract.get("resolution_source_text") or not contract.get("source_url"):
            counts["source_missing_unverified"] += 1
            _append_target(targets["source_missing_unverified"], contract)
        confidence = float(contract.get("parse_confidence") or 0.0)
        if confidence < 0.8:
            counts["low_confidence_unverified"] += 1
            _append_target(targets["low_confidence_unverified"], contract)
        if contract.get("auto_verified_at"):
            if _contract_settlement_mature(contract, now):
                counts["mature_auto_verified_unreviewed"] += 1
                _append_target(targets["mature_auto_verified_unreviewed"], contract)
            else:
                counts["future_auto_verified_unreviewed"] += 1
                _append_target(targets["future_auto_verified_unreviewed"], contract)
        else:
            counts["manual_review_required_unverified"] += 1
            _append_target(targets["manual_review_required_unverified"], contract)
    return {
        "counts": counts,
        "targets": {key: value for key, value in targets.items() if value},
    }


def _append_target(targets: list[dict[str, str]], contract: dict[str, Any], limit: int = 20) -> None:
    if len(targets) >= limit:
        return
    targets.append(_contract_target(contract))


def _build_next_actions(
    stages: list[dict[str, Any]],
    city_rows: list[dict[str, Any]],
    contracts: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    stage_by_key = {str(stage.get("key")): stage for stage in stages}
    reason_counts = {
        str(reason.get("code")): int(reason.get("count") or 0)
        for stage in stages
        for reason in stage.get("reasons", [])
    }
    actions: list[dict[str, Any]] = []

    contract_metrics = stage_by_key.get("settlement_contracts", {}).get("metrics") or {}
    mature_auto_pending = int(contract_metrics.get("mature_auto_verified_unreviewed_contracts") or 0)
    unverified_contracts = int(reason_counts.get("settlement_rule_not_manually_verified") or 0)
    mature_auto_targets = [
        _contract_target(contract)
        for contract in contracts
        if contract.get("auto_verified_at")
        and not contract.get("manual_verified_at")
        and _contract_settlement_mature(contract, now)
    ][:20]
    if mature_auto_pending:
        actions.append({
            "key": "review_mature_auto_contracts",
            "priority": 1,
            "label": "核验成熟自动合同",
            "count": mature_auto_pending,
            "impact": "先处理已经过结算日、且自动解析可信的合同，最快解除 Phase 1.5 的人工核验闸门。",
            "command": ".\\.venv\\Scripts\\python.exe -m weatherbot_v3.cli contracts-bulk-verify --limit 20 --mature-only",
            "apply_command": ".\\.venv\\Scripts\\python.exe -m weatherbot_v3.cli contracts-bulk-verify --limit 20 --mature-only --apply --note \"auto-verified mature contract reviewed from readiness queue\"",
            "requires_operator": True,
            "targets": mature_auto_targets,
        })
    elif unverified_contracts:
        actions.append({
            "key": "inspect_unverified_contracts",
            "priority": 2,
            "label": "逐条核验剩余合同",
            "count": unverified_contracts,
            "impact": "自动成熟合同已处理完，剩余合同需要人工确认站点、日期、单位和来源 URL。",
            "command": ".\\.venv\\Scripts\\python.exe -m weatherbot_v3.cli contracts-list --status unverified --limit 20",
            "requires_operator": True,
            "targets": _city_targets(city_rows, {"settlement_rule_not_manually_verified"}),
        })

    forecast_gap = int(reason_counts.get("forecast_city_coverage_incomplete") or 0)
    if forecast_gap:
        forecast_targets = _city_targets(
            city_rows,
            {"versioned_forecast_runs_missing", "forecast_runs_stale"},
        )
        cities = ",".join(target["city"] for target in forecast_targets[:20])
        command = ".\\.venv\\Scripts\\python.exe -m weatherbot_v3.cli forecast-backfill --days 4"
        if cities:
            command = f"{command} --cities {cities}"
        actions.append({
            "key": "refresh_forecast_runs",
            "priority": 3,
            "label": "刷新预测运行档案",
            "count": forecast_gap,
            "impact": "补齐每个城市的新鲜 ECMWF/GFS 预测运行；历史训练样本仍需 forecast archive 单独导入。",
            "command": command,
            "requires_operator": False,
            "targets": forecast_targets,
        })

    orderbook_gap = max(
        int(reason_counts.get("orderbook_snapshots_missing") or 0),
        int(reason_counts.get("all_orderbooks_stale") or 0),
        int(reason_counts.get("fresh_clob_depth_missing") or 0),
    )
    if orderbook_gap:
        actions.append({
            "key": "refresh_clob_orderbooks",
            "priority": 4,
            "label": "刷新 CLOB 盘口",
            "count": orderbook_gap,
            "impact": "拉取真实 bid/ask、spread、tick、orderMinSize 和深度，避免模拟成交继续基于过期盘口。",
            "command": ".\\.venv\\Scripts\\python.exe -m weatherbot_v3.cli orderbook-backfill --limit 200",
            "requires_operator": False,
            "targets": [],
        })

    truth_gap = int(reason_counts.get("independent_truth_days_below_min") or 0)
    if truth_gap:
        truth_targets = _city_targets(city_rows, {"independent_truth_days_below_min"})
        cities = ",".join(target["city"] for target in truth_targets[:20])
        command = ".\\.venv\\Scripts\\python.exe -m weatherbot_v3.cli truth-backfill --limit 200"
        if cities:
            command = f"{command} --cities {cities}"
        actions.append({
            "key": "backfill_official_truth",
            "priority": 5,
            "label": "补官方 Truth",
            "count": truth_gap,
            "impact": "补机场/官方站点结算 truth；Open-Meteo fallback 不解锁生产训练或实盘。",
            "command": command,
            "requires_operator": False,
            "targets": truth_targets,
        })

    if actions:
        actions.append({
            "key": "rerun_data_readiness",
            "priority": 99,
            "label": "复查数据资格",
            "count": 1,
            "impact": "每轮补数或核验后重新生成审计，确认 blocker 是否真的减少。",
            "command": ".\\.venv\\Scripts\\python.exe -m weatherbot_v3.cli data-readiness",
            "requires_operator": False,
            "targets": [],
        })
    return sorted(actions, key=lambda item: int(item["priority"]))


def _contract_target(contract: dict[str, Any]) -> dict[str, str]:
    return {
        "contract_id": str(contract.get("contract_id") or ""),
        "event_slug": str(contract.get("event_slug") or ""),
        "city": str(contract.get("city") or ""),
        "city_name": str(contract.get("city_name") or contract.get("city") or ""),
        "target_date": str(contract.get("target_local_date") or ""),
        "station_id": str(contract.get("station_id") or ""),
        "source_url": str(contract.get("source_url") or ""),
    }


def _city_targets(
    city_rows: list[dict[str, Any]],
    reason_codes: set[str],
    limit: int = 20,
) -> list[dict[str, str]]:
    targets = []
    for row in city_rows:
        reasons = {str(reason) for reason in row.get("reasons", [])}
        if reasons & reason_codes:
            targets.append({
                "city": str(row.get("city") or ""),
                "city_name": str(row.get("city_name") or row.get("city") or ""),
                "station_id": str(row.get("station_id") or ""),
            })
    return targets[:limit]


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


def _contract_settlement_mature(contract: dict[str, Any], now: datetime) -> bool:
    target_date = str(contract.get("target_local_date") or "")
    timezone_name = str(contract.get("timezone") or "")
    try:
        tz = ZoneInfo(timezone_name)
        local_date = datetime.strptime(target_date, "%Y-%m-%d").date()
    except (ValueError, ZoneInfoNotFoundError):
        return False
    local_end = datetime.combine(local_date, time.max, tzinfo=tz).astimezone(timezone.utc)
    return local_end < now.astimezone(timezone.utc)


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
