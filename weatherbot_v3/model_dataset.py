from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import load_config
from .db import connect, init_v3_db
from .qualification import LIVE_TRUTH_PROVIDERS


AUDIT_VERSION = "model-dataset-audit-v1"
CORE_FORECAST_SOURCES = {"ecmwf", "gfs_ensemble"}


def build_model_dataset_audit(path: Path | None = None, min_samples: int | None = None) -> dict[str, Any]:
    """Summarize training/replay sample readiness without using future data.

    The unit of analysis is a city/local-date event, not a market snapshot. A
    sample is model-training eligible only when its settlement contract is
    manually verified, final/high-confidence truth exists, and at least one
    training forecast run was available before the relevant local-day boundary.
    """

    init_v3_db(path)
    cfg = load_config()
    required_samples = int(min_samples or max(30, cfg.min_independent_settlement_days))
    with connect(path) as conn:
        contracts = [dict(row) for row in conn.execute("SELECT * FROM settlement_contracts").fetchall()]
        rules = [dict(row) for row in conn.execute("SELECT * FROM market_rules").fetchall()]
        truths = [dict(row) for row in conn.execute("SELECT * FROM truth_observations").fetchall()]
        forecast_runs = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM forecast_runs WHERE COALESCE(run_type, 'forecast') = 'forecast'"
            ).fetchall()
        ]
        forecast_member_counts = {
            int(row["run_id"]): int(row["members"])
            for row in conn.execute(
                "SELECT run_id, COUNT(*) members FROM forecast_members GROUP BY run_id"
            ).fetchall()
        }
        orderbooks = [dict(row) for row in conn.execute("SELECT * FROM orderbooks").fetchall()]

    rules_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    market_ids_by_event: dict[str, set[str]] = defaultdict(set)
    for rule in rules:
        event_slug = str(rule.get("event_slug") or "")
        rules_by_event[event_slug].append(rule)
        market_id = str(rule.get("market_id") or "")
        if market_id:
            market_ids_by_event[event_slug].add(market_id)
        exchange_id = str(rule.get("exchange_market_id") or "")
        if exchange_id:
            market_ids_by_event[event_slug].add(exchange_id)

    truths_by_city_date: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for truth in truths:
        truths_by_city_date[(str(truth.get("city") or ""), str(truth.get("target_date") or ""))].append(truth)

    runs_by_city_date: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for run in forecast_runs:
        runs_by_city_date[(str(run.get("city") or ""), str(run.get("target_date") or ""))].append(run)

    orderbook_counts_by_market = Counter(str(row.get("market_id") or "") for row in orderbooks)
    grouped_contracts: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for contract in contracts:
        key = (str(contract.get("city") or ""), str(contract.get("target_local_date") or ""))
        if key[0] and key[1]:
            grouped_contracts[key].append(contract)

    sample_rows = []
    by_city: dict[str, dict[str, Any]] = {}
    source_counts: Counter[str] = Counter()
    horizon_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    leakage_flags: Counter[str] = Counter()
    missing_truth_targets: set[tuple[str, str]] = set()
    missing_forecast_targets: set[tuple[str, str]] = set()
    missing_member_targets: set[tuple[str, str]] = set()
    missing_orderbook_targets: set[tuple[str, str]] = set()
    auto_verified_pending: set[str] = set()

    for (city, target_date), city_contracts in sorted(grouped_contracts.items(), key=lambda item: (item[0][1], item[0][0]), reverse=True):
        timezone_name = _first_value(city_contracts, "timezone") or "UTC"
        truth_rows = truths_by_city_date.get((city, target_date), [])
        eligible_truth = [
            truth for truth in truth_rows
            if truth.get("calibration_eligible")
            and truth.get("provider") in LIVE_TRUTH_PROVIDERS
            and truth.get("actual_temp") is not None
        ]
        candidate_runs = [
            run for run in runs_by_city_date.get((city, target_date), [])
            if run.get("training_eligible")
        ]
        no_leak_runs = []
        rejected_runs = []
        for run in candidate_runs:
            check = _forecast_no_leak_check(run, target_date, timezone_name)
            if check["ok"]:
                no_leak_runs.append({**run, "_horizon_bucket": check["horizon_bucket"]})
                horizon_counts[check["horizon_bucket"]] += 1
            else:
                rejected_runs.append({**run, "_leak_reason": check["reason"]})
                leakage_flags[check["reason"]] += 1

        member_count = sum(forecast_member_counts.get(int(run.get("id") or 0), 0) for run in no_leak_runs)
        sources = sorted({str(run.get("source") or "unknown") for run in no_leak_runs})
        source_counts.update(sources)
        event_slugs = {str(contract.get("event_slug") or "") for contract in city_contracts}
        market_ids = {
            market_id
            for event_slug in event_slugs
            for market_id in market_ids_by_event.get(event_slug, set())
        }
        orderbook_snapshots = sum(orderbook_counts_by_market.get(market_id, 0) for market_id in market_ids)
        manual_verified = sum(1 for contract in city_contracts if contract.get("manual_verified_at"))
        auto_verified = sum(1 for contract in city_contracts if contract.get("auto_verified_at"))

        reasons = []
        warnings = []
        if manual_verified == 0:
            reasons.append("contract_not_manually_verified")
            auto_verified_pending.update(
                str(contract.get("contract_id") or "")
                for contract in city_contracts
                if contract.get("auto_verified_at") and not contract.get("manual_verified_at")
            )
        if not eligible_truth:
            reasons.append("eligible_truth_missing")
            missing_truth_targets.add((city, target_date))
        if not no_leak_runs:
            reasons.append("no_no_leak_forecast_run")
            missing_forecast_targets.add((city, target_date))
        if member_count == 0:
            reasons.append("forecast_members_missing")
            missing_member_targets.add((city, target_date))
        if not CORE_FORECAST_SOURCES.issubset(set(sources)):
            warnings.append("core_source_coverage_incomplete")
        if rejected_runs:
            warnings.append("future_or_undated_forecast_rejected")
        if orderbook_snapshots == 0:
            warnings.append("orderbook_replay_missing")
            missing_orderbook_targets.add((city, target_date))

        training_eligible = not reasons
        baseline_ready = training_eligible and CORE_FORECAST_SOURCES.issubset(set(sources))
        replay_ready = baseline_ready and orderbook_snapshots > 0
        for reason in reasons + warnings:
            reason_counts[reason] += 1
        row = {
            "city": city,
            "city_name": _first_value(city_contracts, "city_name") or city,
            "target_date": target_date,
            "timezone": timezone_name,
            "contracts": len(city_contracts),
            "manual_verified_contracts": manual_verified,
            "auto_verified_contracts": auto_verified,
            "eligible_truth": len(eligible_truth),
            "forecast_runs": len(candidate_runs),
            "no_leak_forecast_runs": len(no_leak_runs),
            "rejected_forecast_runs": len(rejected_runs),
            "forecast_members": member_count,
            "sources": sources,
            "orderbook_snapshots": orderbook_snapshots,
            "training_eligible": training_eligible,
            "baseline_ready": baseline_ready,
            "replay_ready": replay_ready,
            "reasons": reasons,
            "warnings": warnings,
        }
        sample_rows.append(row)
        city_row = by_city.setdefault(city, {
            "city": city,
            "city_name": row["city_name"],
            "samples": 0,
            "training_eligible": 0,
            "baseline_ready": 0,
            "replay_ready": 0,
            "eligible_truth": 0,
            "no_leak_forecast_runs": 0,
            "warnings": Counter(),
            "reasons": Counter(),
        })
        city_row["samples"] += 1
        city_row["training_eligible"] += 1 if training_eligible else 0
        city_row["baseline_ready"] += 1 if baseline_ready else 0
        city_row["replay_ready"] += 1 if replay_ready else 0
        city_row["eligible_truth"] += len(eligible_truth)
        city_row["no_leak_forecast_runs"] += len(no_leak_runs)
        city_row["warnings"].update(warnings)
        city_row["reasons"].update(reasons)

    city_rows = []
    for row in by_city.values():
        city_rows.append({
            **{key: value for key, value in row.items() if key not in {"warnings", "reasons"}},
            "warnings": dict(row["warnings"]),
            "reasons": dict(row["reasons"]),
        })
    city_rows.sort(key=lambda item: (item["training_eligible"], item["baseline_ready"], item["samples"]), reverse=True)
    training_count = sum(1 for row in sample_rows if row["training_eligible"])
    baseline_count = sum(1 for row in sample_rows if row["baseline_ready"])
    replay_count = sum(1 for row in sample_rows if row["replay_ready"])
    status = "ready" if baseline_count >= required_samples else "blocked"
    next_actions = _build_next_actions(
        auto_verified_pending=auto_verified_pending,
        missing_truth_targets=missing_truth_targets,
        missing_forecast_targets=missing_forecast_targets,
        missing_member_targets=missing_member_targets,
        missing_orderbook_targets=missing_orderbook_targets,
        reason_counts=reason_counts,
        baseline_count=baseline_count,
        required_samples=required_samples,
    )
    return {
        "audit_version": AUDIT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "required_samples": required_samples,
        "summary": {
            "event_days": len(sample_rows),
            "training_eligible_samples": training_count,
            "baseline_ready_samples": baseline_count,
            "replay_ready_samples": replay_count,
            "blocked_samples": len(sample_rows) - training_count,
            "cities": len(city_rows),
            "eligible_cities": sum(1 for row in city_rows if row["training_eligible"] > 0),
            "baseline_ready_cities": sum(1 for row in city_rows if row["baseline_ready"] > 0),
        },
        "reason_counts": dict(reason_counts),
        "leakage_flags": dict(leakage_flags),
        "source_counts": dict(source_counts),
        "horizon_counts": dict(horizon_counts),
        "next_actions": next_actions,
        "cities": city_rows,
        "samples": sample_rows[:200],
    }


def _build_next_actions(
    *,
    auto_verified_pending: set[str],
    missing_truth_targets: set[tuple[str, str]],
    missing_forecast_targets: set[tuple[str, str]],
    missing_member_targets: set[tuple[str, str]],
    missing_orderbook_targets: set[tuple[str, str]],
    reason_counts: Counter[str],
    baseline_count: int,
    required_samples: int,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    sample_gap = max(0, required_samples - baseline_count)
    if auto_verified_pending:
        actions.append({
            "key": "review_auto_verified_contracts",
            "priority": 1,
            "label": "核验自动可信合同",
            "count": len(auto_verified_pending),
            "impact": "解除 Phase 1.5 最大闸门，让已解析可信的事件进入训练候选。",
            "command": ".\\.venv\\Scripts\\python.exe -m weatherbot_v3.cli contracts-bulk-verify --limit 20",
            "apply_command": ".\\.venv\\Scripts\\python.exe -m weatherbot_v3.cli contracts-bulk-verify --limit 20 --apply",
            "requires_operator": True,
            "targets": sorted(auto_verified_pending)[:20],
        })
    if missing_truth_targets:
        cities = sorted({city for city, _ in missing_truth_targets})
        actions.append({
            "key": "backfill_official_truth",
            "priority": 2,
            "label": "补官方结算 truth",
            "count": len(missing_truth_targets),
            "impact": "补齐模型训练标签；Open-Meteo fallback 不解锁生产训练。",
            "command": _city_command("truth-backfill", cities),
            "requires_operator": False,
            "targets": _target_preview(missing_truth_targets),
        })
    if missing_forecast_targets or missing_member_targets:
        cities = sorted({city for city, _ in (missing_forecast_targets | missing_member_targets)})
        actions.append({
            "key": "backfill_forecast_members",
            "priority": 3,
            "label": "补 forecast runs / members",
            "count": max(len(missing_forecast_targets), len(missing_member_targets)),
            "impact": "让 D+1/D+2 样本具备成员级日最高温分布，后续才能做 MOS/EMOS。",
            "command": _city_command("forecast-backfill", cities),
            "requires_operator": False,
            "targets": _target_preview(missing_forecast_targets | missing_member_targets),
        })
    if missing_orderbook_targets:
        actions.append({
            "key": "backfill_orderbooks",
            "priority": 4,
            "label": "补盘口回放快照",
            "count": len(missing_orderbook_targets),
            "impact": "让策略回放使用真实 bid/ask、spread 和深度，而不是只看理论概率。",
            "command": ".\\.venv\\Scripts\\python.exe -m weatherbot_v3.cli orderbook-backfill --limit 200",
            "requires_operator": False,
            "targets": _target_preview(missing_orderbook_targets),
        })
    if sample_gap:
        actions.append({
            "key": "sample_gate",
            "priority": 5,
            "label": "达到 Phase 2 样本门槛",
            "count": sample_gap,
            "impact": f"还差 {sample_gap} 个 baseline-ready 事件日，才能开始稳定比较 baseline/MOS。",
            "command": ".\\.venv\\Scripts\\python.exe -m weatherbot_v3.cli model-dataset-audit --limit 30",
            "requires_operator": False,
            "targets": [],
        })
    if reason_counts.get("future_or_undated_forecast_rejected"):
        actions.append({
            "key": "inspect_forecast_time_rejections",
            "priority": 6,
            "label": "检查被拒绝的预测时间轴",
            "count": int(reason_counts["future_or_undated_forecast_rejected"]),
            "impact": "确认 forecast run_at/retrieved_at 是否真实，避免训练集偷看未来。",
            "command": ".\\.venv\\Scripts\\python.exe -m weatherbot_v3.cli model-dataset-audit --limit 30",
            "requires_operator": False,
            "targets": [],
        })
    return sorted(actions, key=lambda item: int(item["priority"]))


def _city_command(command: str, cities: list[str]) -> str:
    if not cities:
        return f".\\.venv\\Scripts\\python.exe -m weatherbot_v3.cli {command}"
    return f".\\.venv\\Scripts\\python.exe -m weatherbot_v3.cli {command} --cities {','.join(cities[:20])}"


def _target_preview(targets: set[tuple[str, str]], limit: int = 20) -> list[dict[str, str]]:
    return [
        {"city": city, "target_date": target_date}
        for city, target_date in sorted(targets, key=lambda item: (item[1], item[0]), reverse=True)[:limit]
    ]


def _forecast_no_leak_check(run: dict[str, Any], target_date: str, timezone_name: str) -> dict[str, Any]:
    available_at = _parse_time(run.get("run_at")) or _parse_time(run.get("retrieved_at"))
    if not available_at:
        return {"ok": False, "reason": "forecast_time_missing", "horizon_bucket": "unknown"}
    bounds = _local_day_bounds(target_date, timezone_name)
    if not bounds:
        return {"ok": False, "reason": "target_timezone_invalid", "horizon_bucket": "unknown"}
    local_start, local_end = bounds
    lead_hours = _float(run.get("lead_hours"))
    if lead_hours >= 48:
        horizon_bucket = "d2_plus"
        deadline = local_start
    elif lead_hours >= 24:
        horizon_bucket = "d1"
        deadline = local_start
    else:
        horizon_bucket = "d0"
        deadline = local_end
    if available_at > deadline:
        reason = "forecast_after_target_start" if horizon_bucket != "d0" else "forecast_after_target_day"
        return {"ok": False, "reason": reason, "horizon_bucket": horizon_bucket}
    return {"ok": True, "reason": "", "horizon_bucket": horizon_bucket}


def _local_day_bounds(target_date: str, timezone_name: str) -> tuple[datetime, datetime] | None:
    try:
        tz = ZoneInfo(timezone_name)
        local_date = datetime.strptime(target_date, "%Y-%m-%d").date()
    except (ValueError, ZoneInfoNotFoundError):
        return None
    start = datetime.combine(local_date, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(local_date, time.max, tzinfo=tz).astimezone(timezone.utc)
    return start, end


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


def _first_value(rows: list[dict[str, Any]], key: str) -> Any:
    for row in rows:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
