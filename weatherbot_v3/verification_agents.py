from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import V3Config, load_config
from .db import connect_readonly, signal_decision_prediction_cohort_statuses
from .model_dataset import _forecast_no_leak_check
from .executor import LIVE_EXECUTION_PRODUCTION_READY, LIVE_EXECUTION_VERSION
from .source_health import build_source_health_matrix


VERIFICATION_VERSION = "project-verification-v1"
READINESS_MODES = ("observation", "paper", "paper_evidence", "live_canary")
CORE_RUNTIME_SOURCES = (
    "metar",
    "forecast_openmeteo",
    "forecast_weathercom_v3",
    "polymarket_orderbook",
    "hourly_consensus",
    "signal_decisions",
)
ALLOWED_STRATEGIES = {"single_bucket_ev", "ladder_grid", "tail_buying"}
NORMALIZED_DISTRIBUTION_TOLERANCE = 1e-3


@dataclass(frozen=True)
class VerificationContext:
    path: Path
    config: V3Config
    now: datetime
    source_health: dict[str, Any]
    runtime: dict[str, Any]
    deep: bool


class VerificationAgent:
    key = "base"
    label = "Base"

    def run(self, context: VerificationContext) -> dict[str, Any]:
        raise NotImplementedError


def build_project_verification_report(
    path: Path | None = None,
    *,
    source_health: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
    probe_runtime: bool = True,
    now_utc: datetime | None = None,
    base_url: str = "http://127.0.0.1:8765",
    deep: bool = False,
) -> dict[str, Any]:
    """Run independent, read-only verification agents over the current project state."""

    cfg = load_config()
    db_path = Path(path or cfg.v3_db_path)
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not db_path.exists():
        return _missing_database_report(db_path, now)

    health = source_health if source_health is not None else build_source_health_matrix(db_path, now_utc=now, read_only=True)
    runtime_payload = runtime if runtime is not None else (
        probe_local_runtime(base_url=base_url) if probe_runtime else {"probed": False, "available": None}
    )
    context = VerificationContext(
        path=db_path,
        config=cfg,
        now=now,
        source_health=health,
        runtime=runtime_payload,
        deep=deep,
    )
    agents: list[VerificationAgent] = [
        DataFoundationVerificationAgent(),
        ModelIntegrityVerificationAgent(),
        DecisionRiskVerificationAgent(),
        PaperExecutionVerificationAgent(),
        OperatorSurfaceVerificationAgent(),
    ]
    agent_results = []
    for agent in agents:
        started = time.perf_counter()
        result = agent.run(context)
        result["duration_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        agent_results.append(result)
    readiness = _readiness(agent_results)
    failed_checks = [
        check
        for agent in agent_results
        for check in agent["checks"]
        if check["status"] == "fail"
    ]
    warnings = [
        check
        for agent in agent_results
        for check in agent["checks"]
        if check["status"] == "warn"
    ]
    actions = _next_actions(failed_checks)
    return {
        "verification_version": VERIFICATION_VERSION,
        "generated_at": now.isoformat(),
        "database_path": str(db_path),
        "scope": "deep" if deep else "quick",
        "status": "ready_for_live_canary" if readiness["live_canary"]["ready"] else "blocked",
        "highest_ready_stage": _highest_ready_stage(readiness),
        "readiness": readiness,
        "summary": {
            "agents": len(agent_results),
            "passed_agents": sum(1 for result in agent_results if result["status"] == "pass"),
            "warning_agents": sum(1 for result in agent_results if result["status"] == "warn"),
            "failed_agents": sum(1 for result in agent_results if result["status"] == "fail"),
            "failed_checks": len(failed_checks),
            "warnings": len(warnings),
        },
        "agents": agent_results,
        "blockers": [
            {
                "id": check["id"],
                "agent": check["agent"],
                "message": check["message"],
                "blocks": check["blocks"],
            }
            for check in failed_checks
        ],
        "next_actions": actions,
        "safety": {
            "live_trading": bool(cfg.live_trading),
            "live_dry_run": bool(cfg.live_dry_run),
            "paper_validation_active": bool(runtime_payload.get("paper_validation_active")),
            "scheduler_running": bool(runtime_payload.get("scheduler_running")),
        },
    }


class DataFoundationVerificationAgent(VerificationAgent):
    key = "data_foundation"
    label = "数据基座验证代理"

    def run(self, context: VerificationContext) -> dict[str, Any]:
        with connect_readonly(context.path) as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT city_key, station_id, settlement_station_id, timezone,
                           settlement_timezone, settlement_rule_verified_at,
                           verification_status
                    FROM stations
                    WHERE COALESCE(enabled, 0) = 1
                    ORDER BY city_key
                    """
                ).fetchall()
            ]
            integrity_check = str(conn.execute("PRAGMA integrity_check").fetchone()[0]) if context.deep else "skipped_in_quick_mode"
            duplicate_metar = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT station_id, report_time, COUNT(*) AS count
                    FROM metar_reports
                    GROUP BY station_id, report_time
                    HAVING COUNT(*) > 1
                    LIMIT 50
                    """
                ).fetchall()
            ]
            duplicate_consensus = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT city, target_date, local_hour, COUNT(*) AS count
                    FROM hourly_consensus
                    GROUP BY city, target_date, local_hour
                    HAVING COUNT(*) > 1
                    LIMIT 50
                    """
                ).fetchall()
            ]
        enabled = [str(row.get("city_key") or "") for row in rows]
        missing_contract = [
            city
            for city, row in ((str(row.get("city_key") or ""), row) for row in rows)
            if not str(row.get("settlement_rule_verified_at") or "").strip()
            or str(row.get("verification_status") or "") not in {"verified", "settlement_mismatch"}
        ]
        live_eligible = [
            str(row.get("city_key") or "")
            for row in rows
            if str(row.get("verification_status") or "") == "verified"
            and str(row.get("settlement_rule_verified_at") or "").strip()
        ]
        paper_only = [
            str(row.get("city_key") or "")
            for row in rows
            if str(row.get("verification_status") or "") == "settlement_mismatch"
        ]
        source_rows = {
            str(row.get("key") or ""): row
            for row in context.source_health.get("sources") or []
        }
        stale_core = [
            key
            for key in CORE_RUNTIME_SOURCES
            if str((source_rows.get(key) or {}).get("status") or "missing") != "healthy"
        ]
        truth_bad = _truth_coverage_gaps(context.source_health)
        pws = source_rows.get("wunderground_pws") or {}

        checks = [
            _check(
                self.key,
                "enabled_city_registry",
                "pass" if enabled else "fail",
                f"启用城市 {len(enabled)} 个。" if enabled else "没有启用城市。",
                blocks=READINESS_MODES,
                evidence={"enabled_cities": enabled},
                action="先核验并启用至少一个有真实市场与结算规则的城市。",
            ),
            _check(
                self.key,
                "settlement_contracts",
                "pass" if not missing_contract else "fail",
                "所有启用城市均有可审计结算规则。" if not missing_contract else f"{len(missing_contract)} 个启用城市缺少可用结算规则。",
                blocks=("paper", "paper_evidence", "live_canary"),
                evidence={"missing": missing_contract, "paper_only": paper_only, "live_eligible": live_eligible},
                action="运行 polymarket-market-probe 并人工核验缺失城市的结算站、时区和来源。",
            ),
            _check(
                self.key,
                "runtime_source_freshness",
                "pass" if not stale_core else "fail",
                "核心实时数据源均在新鲜度窗口内。" if not stale_core else f"核心链路过期或缺失：{', '.join(stale_core)}。",
                blocks=READINESS_MODES,
                evidence={"bad_sources": stale_core},
                action="受控启动调度器并等待 METAR、预报、盘口、派生决策完成一轮，再重新验证。",
            ),
            _check(
                self.key,
                "settlement_truth_coverage",
                "pass" if not truth_bad else "fail",
                "启用城市的结算历史达到目标覆盖。" if not truth_bad else f"{len(truth_bad)} 个城市的 WU/HKO truth 未达到要求。",
                blocks=("paper_evidence", "live_canary"),
                evidence={"cities": truth_bad},
                action="继续增量回填 WU daily 或 HKO Daily Extract，并保留 IEM 仅作近似对照。",
            ),
            _check(
                self.key,
                "pws_optional_entitlement",
                "pass" if pws.get("status") == "healthy" else "warn",
                "PWS 实时趋势可用。" if pws.get("status") == "healthy" else "PWS 未授权或不新鲜；峰值锁定只能依赖 METAR/China Live。",
                evidence={"status": pws.get("status"), "reasons": pws.get("reasons")},
                action="在开发者设置中配置具备 PWS 产品权限的独立 Wunderground key，或保持明确禁用。",
            ),
            _check(
                self.key,
                "storage_integrity",
                "pass" if integrity_check in {"ok", "skipped_in_quick_mode"} and not duplicate_metar and not duplicate_consensus else "fail",
                "核心观测/派生键没有重复。" if integrity_check in {"ok", "skipped_in_quick_mode"} and not duplicate_metar and not duplicate_consensus else f"存储完整性异常：METAR 重复 {len(duplicate_metar)} 组，consensus 重复 {len(duplicate_consensus)} 组。",
                blocks=("paper_evidence", "live_canary"),
                evidence={"integrity_check": integrity_check, "duplicate_metar": duplicate_metar, "duplicate_consensus": duplicate_consensus},
                action="按 canonical key 去重历史观测/consensus，并补唯一索引与迁移回归测试。",
            ),
        ]
        return _agent_result(self, checks, {"enabled_cities": len(enabled), "live_eligible": len(live_eligible), "paper_only": paper_only})


class ModelIntegrityVerificationAgent(VerificationAgent):
    key = "model_integrity"
    label = "概率模型验证代理"

    def run(self, context: VerificationContext) -> dict[str, Any]:
        with connect_readonly(context.path) as conn:
            enabled = {
                str(row[0])
                for row in conn.execute("SELECT city_key FROM stations WHERE COALESCE(enabled, 0)=1").fetchall()
            }
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    WITH ranked AS (
                        SELECT d.*, ROW_NUMBER() OVER (
                            PARTITION BY city_key ORDER BY issued_at DESC, id DESC
                        ) AS rn
                        FROM daily_max_predictions d
                        WHERE city_key IN (SELECT city_key FROM stations WHERE COALESCE(enabled, 0)=1)
                          AND COALESCE(validity_status, 'valid')='valid'
                    )
                    SELECT * FROM ranked WHERE rn=1 ORDER BY city_key
                    """
                ).fetchall()
            ]
            leaked = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, city, source, target_date
                    FROM forecast_runs
                    WHERE COALESCE(training_eligible, 0)=1
                      AND LOWER(COALESCE(source, '')) LIKE '%polywx%'
                    LIMIT 20
                    """
                ).fetchall()
            ]
            distribution_rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, market_id, sum_probability, normalized
                    FROM event_distributions
                    ORDER BY id DESC
                    LIMIT 5000
                    """
                ).fetchall()
            ]
            forecast_rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT fr.id, fr.city, fr.target_date, fr.source, fr.run_at,
                           fr.retrieved_at, fr.available_at, fr.availability_basis,
                           fr.valid_at, fr.horizon, fr.lead_hours, fr.parse_status,
                           fr.training_eligible, fr.quality_flags, fr.raw_json, s.timezone
                    FROM forecast_runs fr
                    JOIN stations s ON s.city_key=fr.city
                    WHERE COALESCE(fr.training_eligible, 0)=1
                    """
                ).fetchall()
            ]
            prediction_rows = rows
            forecast_by_id = {
                int(row["id"]): dict(row)
                for row in conn.execute(
                    """
                    SELECT id, city, target_date,
                           COALESCE(NULLIF(available_at, ''), NULLIF(retrieved_at, ''), created_at) AS available_at,
                           training_eligible, ineligibility_reason, quarantined_at
                    FROM forecast_runs
                    """
                ).fetchall()
            }

        missing = sorted(enabled - {str(row.get("city_key") or "") for row in rows})
        malformed: list[dict[str, Any]] = []
        missing_v3: list[str] = []
        undercalibrated: list[dict[str, Any]] = []
        for row in rows:
            city = str(row.get("city_key") or "")
            mu = _finite(row.get("mu"))
            sigma = _finite(row.get("sigma"))
            sigma_floor = _finite(row.get("sigma_floor")) or 0.5
            weights = _json_object(row.get("model_weights_json"))
            components = _json_list(row.get("components_json"))
            weight_sum = sum(_finite(value) or 0.0 for value in weights.values())
            if mu is None or sigma is None or sigma < sigma_floor or not weights or abs(weight_sum - 1.0) > 1e-6:
                malformed.append({"city": city, "mu": mu, "sigma": sigma, "sigma_floor": sigma_floor, "weight_sum": weight_sum})
            component_sources = {str(item.get("source") or "") for item in components if isinstance(item, dict)}
            if str(context.config.deb_weight_mode).lower() in {"polywx", "polywx_aligned", "polywx_aligned_deb_v1"} and "weathercom_v3_forecast" not in component_sources:
                missing_v3.append(city)
            for item in components:
                if not isinstance(item, dict):
                    continue
                weight = _finite(item.get("weight_after_mae")) or _finite(item.get("weight")) or 0.0
                samples = int(_finite(item.get("bias_sample_count")) or 0)
                if weight >= 0.10 and samples < 7:
                    undercalibrated.append({"city": city, "source": item.get("source"), "weight": weight, "samples": samples})

        distribution_bad: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in distribution_rows:
            market_id = str(row.get("market_id") or row.get("id") or "")
            if market_id in seen:
                continue
            seen.add(market_id)
            total = _finite(row.get("sum_probability"))
            if total is None or abs(total - 1.0) > NORMALIZED_DISTRIBUTION_TOLERANCE or not bool(row.get("normalized")):
                distribution_bad.append({"market_id": market_id, "sum": total, "normalized": bool(row.get("normalized"))})

        forecast_leaks: list[dict[str, Any]] = []
        for row in forecast_rows:
            result = _forecast_no_leak_check(row, str(row.get("target_date") or ""), str(row.get("timezone") or "UTC"))
            if not result.get("ok"):
                forecast_leaks.append({
                    "run_id": row.get("id"),
                    "city": row.get("city"),
                    "source": row.get("source"),
                    "lead_hours": result.get("lead_hours"),
                    "reason": result.get("reason"),
                })

        prediction_leaks: list[dict[str, Any]] = []
        for prediction in prediction_rows:
            issued_at = _parse_time(prediction.get("issued_at"))
            component_by_run: dict[int, dict[str, Any]] = {}
            for component in _json_list(prediction.get("components_json")):
                if not isinstance(component, dict):
                    continue
                for component_run_id in component.get("source_run_ids") or []:
                    try:
                        component_by_run[int(component_run_id)] = component
                    except (TypeError, ValueError):
                        continue
            for run_id in _json_list(prediction.get("source_run_ids_json")):
                try:
                    numeric_run_id = int(run_id)
                    forecast = forecast_by_id.get(numeric_run_id)
                except (TypeError, ValueError):
                    numeric_run_id = 0
                    forecast = None
                available_at = _parse_time((forecast or {}).get("available_at"))
                component = component_by_run.get(numeric_run_id) or {}
                point_level_d0_contract = (
                    str((forecast or {}).get("ineligibility_reason") or "") == "forecast_lead_negative"
                    and str(component.get("snapshot_selection_mode") or "") == "stitch_local_day"
                    and str(component.get("snapshot_selection_version") or "") == "forecast-snapshot-selection-v2"
                    and str(component.get("daily_high_basis") or "") == "latest_snapshot_per_member_valid_hour_as_of"
                    and str(component.get("point_availability_contract") or "") == "valid_at_gte_snapshot_available_at"
                )
                source_contract_ok = (
                    bool((forecast or {}).get("training_eligible"))
                    and not bool((forecast or {}).get("quarantined_at"))
                ) or point_level_d0_contract
                if (
                    not forecast
                    or not source_contract_ok
                    or str(forecast.get("city") or "") != str(prediction.get("city_key") or "")
                    or str(forecast.get("target_date") or "") != str(prediction.get("target_date") or "")
                    or issued_at is None
                    or available_at is None
                    or available_at > issued_at
                ):
                    prediction_leaks.append({
                        "prediction_id": prediction.get("id"),
                        "run_id": run_id,
                        "city": prediction.get("city_key"),
                    })
                    break

        checks = [
            _check(
                self.key,
                "latest_prediction_coverage",
                "pass" if not missing else "fail",
                "每个启用城市都有最新 DEB。" if not missing else f"{len(missing)} 个启用城市没有 DEB。",
                blocks=("paper", "paper_evidence", "live_canary"),
                evidence={"missing": missing, "covered": len(rows)},
                action="为缺失城市重建 hourly consensus 与 daily-max prediction。",
            ),
            _check(
                self.key,
                "prediction_math",
                "pass" if not malformed and not distribution_bad else "fail",
                "DEB 权重与桶概率均归一且数值有效。" if not malformed and not distribution_bad else "存在无效 μ/σ、权重和或桶概率分布。",
                blocks=("paper", "paper_evidence", "live_canary"),
                evidence={"prediction_issues": malformed, "distribution_issues": distribution_bad},
                action="停止下游决策，修复 DEB/桶积分后重建受影响城市。",
            ),
            _check(
                self.key,
                "weathercom_v3_component",
                "pass" if not missing_v3 else "fail",
                "PolyWX-aligned DEB 均包含 Weather.com v3。" if not missing_v3 else f"{len(missing_v3)} 个城市缺少 v3 组件。",
                blocks=("paper", "paper_evidence", "live_canary"),
                evidence={"missing_v3": sorted(set(missing_v3))},
                action="确认 Weather.com key 权限与 forecast poller，再重建 DEB。",
            ),
            _check(
                self.key,
                "weighted_source_calibration",
                "pass" if not undercalibrated else "fail",
                "高权重模型均达到最小偏差样本数。" if not undercalibrated else f"{len(undercalibrated)} 个高权重组件缺少 7 日偏差样本。",
                blocks=("paper_evidence", "live_canary"),
                evidence={"components": undercalibrated[:30]},
                action="用 WU/HKO truth 与无泄漏历史 forecast run 补足高权重模型校准样本。",
            ),
            _check(
                self.key,
                "polywx_training_leakage",
                "pass" if not leaked else "fail",
                "没有 PolyWX 展示值进入训练样本。" if not leaked else "检测到 PolyWX 来源被标为 training_eligible。",
                blocks=("paper", "paper_evidence", "live_canary"),
                evidence={"rows": leaked},
                action="撤销这些 forecast run 的训练资格并重建模型数据集。",
            ),
            _check(
                self.key,
                "temporal_no_leak",
                "pass" if not forecast_leaks and not prediction_leaks else "fail",
                "训练预报与 DEB 来源均满足时序无泄漏。" if not forecast_leaks and not prediction_leaks else f"发现 {len(forecast_leaks)} 个训练 run 与 {len(prediction_leaks)} 个 DEB 存在时序/来源泄漏。",
                blocks=("paper_evidence", "live_canary"),
                evidence={"forecast_runs": forecast_leaks[:50], "predictions": prediction_leaks[:50]},
                action="清理负 lead 或晚于决策时点的 forecast run，并仅用当时可获得的来源重建 DEB。",
            ),
        ]
        return _agent_result(self, checks, {"latest_predictions": len(rows), "distribution_sets": len(seen)})


class DecisionRiskVerificationAgent(VerificationAgent):
    key = "decision_risk"
    label = "信号与风控验证代理"

    def run(self, context: VerificationContext) -> dict[str, Any]:
        with connect_readonly(context.path) as conn:
            enabled = {
                str(row[0])
                for row in conn.execute("SELECT city_key FROM stations WHERE COALESCE(enabled, 0)=1").fetchall()
            }
            active_row = conn.execute(
                """
                SELECT e.revision_id
                FROM strategy_profile_activation_events e
                WHERE e.scope='signal_generation' AND e.action='activate'
                ORDER BY e.activation_id DESC LIMIT 1
                """
            ).fetchone()
            active_revision = str(active_row[0]) if active_row else ""
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT decision_id, city_key, target_date, issued_at, token_id, yes_token_id,
                           strategy_name, strategy_revision_id, model_probability, market_ask,
                           market_bid, edge, kelly_fraction, position_size_usd, tick_size,
                           order_min_size, book_age_seconds, spread_bps, paper_allowed,
                           paper_decision, live_allowed, live_decision, gate_reasons_json,
                           forecast_algo, deb_version, evidence_links_json
                    FROM signal_decisions
                    WHERE COALESCE(strategy_revision_id, '') = ?
                    ORDER BY issued_at DESC, id DESC
                    LIMIT 5000
                    """,
                    (active_revision,),
                ).fetchall()
            ] if active_revision else []
            matched_markets = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, city AS city_key, target_date, market_id, token_id, yes_token_id,
                           best_bid, best_ask, spread, tick_size,
                           order_min_size, enable_order_book, quote_timestamp AS orderbook_timestamp
                    FROM market_buckets
                    WHERE strict_match_status='matched' AND target_date >= ?
                      AND city IN (SELECT city_key FROM stations WHERE COALESCE(enabled, 0)=1)
                    """,
                    (context.now.date().isoformat(),),
                ).fetchall()
            ]
        raw_decision_count = len(rows)
        cohort_statuses = signal_decision_prediction_cohort_statuses(rows, path=context.path)
        suppressed_reasons: dict[str, int] = {}
        visible_rows: list[dict[str, Any]] = []
        for row, status in zip(rows, cohort_statuses):
            if status.get("ok", True):
                visible_rows.append(row)
                continue
            for reason in status.get("reasons") or ["prediction_source_cohort_invalid"]:
                key = str(reason)
                suppressed_reasons[key] = suppressed_reasons.get(key, 0) + 1
        rows = visible_rows

        newest_by_city: dict[str, datetime] = {}
        for row in rows:
            issued = _parse_time(row.get("issued_at"))
            city = str(row.get("city_key") or "")
            if issued and (city not in newest_by_city or issued > newest_by_city[city]):
                newest_by_city[city] = issued
        covered = {
            city
            for city, issued in newest_by_city.items()
            if (context.now - issued).total_seconds() <= 1800
        }
        missing_fresh = sorted(enabled - covered)
        violations: list[dict[str, Any]] = []
        strategy_counts: dict[str, int] = {}
        paper_candidates = 0
        for row in rows:
            strategy = str(row.get("strategy_name") or "")
            strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1
            if strategy not in ALLOWED_STRATEGIES:
                violations.append({"decision_id": row.get("decision_id"), "reason": "unknown_strategy"})
            for reason in _decision_probability_price_violations(row):
                violations.append({"decision_id": row.get("decision_id"), "reason": reason})
            if bool(row.get("paper_allowed")):
                paper_candidates += 1
                required = (
                    row.get("decision_id"), row.get("yes_token_id") or row.get("token_id"),
                    row.get("tick_size"), row.get("order_min_size"), row.get("strategy_revision_id"),
                )
                if not all(value is not None and value != "" for value in required) or str(row.get("paper_decision") or "") != "buy":
                    violations.append({"decision_id": row.get("decision_id"), "reason": "paper_allowed_identity_incomplete"})
            if bool(row.get("live_allowed")) and not context.config.live_trading:
                violations.append({"decision_id": row.get("decision_id"), "reason": "live_allowed_while_live_disabled"})
            if (_finite(row.get("position_size_usd")) or 0.0) < 0 or (_finite(row.get("kelly_fraction")) or 0.0) < 0:
                violations.append({"decision_id": row.get("decision_id"), "reason": "negative_sizing"})

        market_violations: list[dict[str, Any]] = []
        max_book_age = float(context.config.orderbook_max_age_minutes) * 60.0
        for row in matched_markets:
            bid = _finite(row.get("best_bid"))
            ask = _finite(row.get("best_ask"))
            tick = _finite(row.get("tick_size"))
            minimum = _finite(row.get("order_min_size"))
            quoted_at = _parse_time(row.get("orderbook_timestamp"))
            age = (context.now - quoted_at).total_seconds() if quoted_at else None
            reasons = []
            if bid is None or ask is None or not (0 < bid <= ask < 1):
                reasons.append("invalid_or_crossed_book")
            if tick is None or tick <= 0 or minimum is None or minimum <= 0:
                reasons.append("missing_tick_or_minimum")
            if not bool(row.get("enable_order_book")):
                reasons.append("orderbook_disabled")
            if age is None or age < 0 or age > max_book_age:
                reasons.append("stale_or_future_orderbook")
            if reasons:
                market_violations.append({"market_bucket_id": row.get("id"), "city": row.get("city_key"), "reasons": reasons, "age_seconds": age})

        checks = [
            _check(
                self.key,
                "active_strategy_revision",
                "pass" if active_revision else "fail",
                f"信号生成使用策略版本 {active_revision}。" if active_revision else "没有激活的信号生成策略版本。",
                blocks=("paper", "paper_evidence", "live_canary"),
                evidence={"revision_id": active_revision},
                action="在开发者设置中发布并明确激活一个已验证策略版本。",
            ),
            _check(
                self.key,
                "fresh_decision_coverage",
                "pass" if not missing_fresh else "fail",
                "激活版本覆盖全部启用城市且决策新鲜。" if not missing_fresh else f"{len(missing_fresh)} 个城市没有 30 分钟内的激活版本决策。",
                blocks=("paper", "paper_evidence", "live_canary"),
                evidence={"missing_or_stale": missing_fresh, "covered": sorted(covered)},
                action="核心数据刷新后，按激活 revision 对全部启用城市重建 signal decisions。",
            ),
            _check(
                self.key,
                "decision_invariants",
                "pass" if not violations else "fail",
                "概率、盘口、身份、仓位与 live 锁定不变量通过。" if not violations else f"发现 {len(violations)} 条决策不变量违规。",
                blocks=("paper", "paper_evidence", "live_canary"),
                evidence={"violations": violations[:50]},
                action="修复违规决策生成或迁移逻辑，重建后再开放模拟执行。",
            ),
            _check(
                self.key,
                "paper_candidate_visibility",
                "pass" if paper_candidates > 0 else "warn",
                f"当前激活版本有 {paper_candidates} 条可模拟候选。" if paper_candidates else "当前激活版本没有通过 paper gate 的候选；不得通过放松真实闸门制造信号。",
                evidence={
                    "paper_candidates": paper_candidates,
                    "strategy_counts": strategy_counts,
                    "raw_decisions": raw_decision_count,
                    "suppressed_decisions": raw_decision_count - len(rows),
                    "suppressed_reasons": suppressed_reasons,
                },
            ),
            _check(
                self.key,
                "matched_market_executable",
                "pass" if matched_markets and not market_violations else "fail",
                "最新 matched 市场均有新鲜、未交叉且约束完整的盘口。" if matched_markets and not market_violations else f"matched 市场可执行性失败：{len(market_violations)} 条异常，市场总数 {len(matched_markets)}。",
                blocks=("paper", "paper_evidence", "live_canary"),
                evidence={"matched": len(matched_markets), "violations": market_violations[:50]},
                action="刷新 Gamma/CLOB 盘口并修复 crossed/stale/tick/orderMinSize 异常后再执行模拟。",
            ),
        ]
        return _agent_result(self, checks, {
            "active_revision": active_revision,
            "rows": len(rows),
            "raw_rows": raw_decision_count,
            "suppressed_rows": raw_decision_count - len(rows),
            "suppressed_reasons": suppressed_reasons,
            "paper_candidates": paper_candidates,
            "strategy_counts": strategy_counts,
        })


class PaperExecutionVerificationAgent(VerificationAgent):
    key = "paper_execution"
    label = "模拟执行与结算验证代理"

    def run(self, context: VerificationContext) -> dict[str, Any]:
        with connect_readonly(context.path) as conn:
            orders = [dict(row) for row in conn.execute("SELECT * FROM paper_orders ORDER BY id").fetchall()]
            fills = [dict(row) for row in conn.execute("SELECT order_id FROM fills WHERE order_type='paper'").fetchall()]
            settlements = [dict(row) for row in conn.execute("SELECT * FROM settlements ORDER BY settled_at, id").fetchall()]
            runs = [dict(row) for row in conn.execute("SELECT * FROM paper_validation_runs ORDER BY started_at").fetchall()]
            real_live_orders = int(conn.execute("SELECT COUNT(*) FROM live_orders WHERE dry_run=0").fetchone()[0])
            unknown_live_orders = int(conn.execute("SELECT COUNT(*) FROM live_orders WHERE dry_run IS NULL").fetchone()[0])

        fill_order_ids = {int(row["order_id"]) for row in fills if row.get("order_id") is not None}
        integrity: list[dict[str, Any]] = []
        ladder_counts: dict[str, int] = {}
        current_orders = [row for row in orders if str(row.get("order_version") or "") == "paper-execution-v2"]
        legacy_orders = len(orders) - len(current_orders)
        for row in current_orders:
            required = (
                row.get("decision_id"), row.get("idempotency_key"), row.get("market_id"),
                row.get("yes_token_id"), row.get("strategy_revision_id"), row.get("order_version"),
            )
            if not all(value is not None and value != "" for value in required):
                integrity.append({"order_id": row.get("id"), "reason": "identity_or_revision_missing"})
            if str(row.get("fill_status") or "") in {"filled", "partial"} and int(row.get("id") or 0) not in fill_order_ids:
                integrity.append({"order_id": row.get("id"), "reason": "fill_row_missing"})
            group = str(row.get("ladder_group_id") or "")
            if group:
                ladder_counts[group] = ladder_counts.get(group, 0) + 1
        bad_ladders = {key: count for key, count in ladder_counts.items() if count != 3}
        if bad_ladders:
            integrity.append({"reason": "non_atomic_ladder_leg_count", "groups": bad_ladders})

        resolved = [row for row in settlements if str(row.get("settlement_status") or "") == "resolved"]
        authoritative = [row for row in resolved if str(row.get("settlement_source") or "") == "polymarket_gamma_resolved"]
        pnl = sum(_finite(row.get("pnl")) or 0.0 for row in authoritative)
        model_brier = [_finite(row.get("brier_score")) for row in authoritative]
        market_brier = [_finite(row.get("market_brier_score")) for row in authoritative]
        model_brier = [value for value in model_brier if value is not None]
        market_brier = [value for value in market_brier if value is not None]
        model_brier_mean = sum(model_brier) / len(model_brier) if model_brier else None
        market_brier_mean = sum(market_brier) / len(market_brier) if market_brier else None
        max_validation_days = max((_run_duration_days(row, context.now) for row in runs), default=0.0)
        evidence_ready = (
            len(authoritative) >= 30
            and max_validation_days >= 14.0
            and pnl > 0
            and model_brier_mean is not None
            and market_brier_mean is not None
            and model_brier_mean < market_brier_mean
        )

        checks = [
            _check(
                self.key,
                "paper_order_integrity",
                "pass" if not integrity else "fail",
                "模拟订单身份、版本、fill 与 ladder 原子性通过。" if not integrity else f"发现 {len(integrity)} 项模拟订单完整性问题。",
                blocks=("paper", "paper_evidence", "live_canary"),
                evidence={"issues": integrity[:50], "current_orders": len(current_orders), "legacy_orders": legacy_orders, "fills": len(fills)},
                action="修复 paper order/fill 的身份或原子性问题，并用测试重放受影响订单。",
            ),
            _check(
                self.key,
                "current_paper_order_evidence",
                "pass" if current_orders else "warn",
                f"已有 {len(current_orders)} 笔 revision-bound v2 模拟订单。" if current_orders else f"尚无 revision-bound v2 模拟订单；现有 {legacy_orders} 笔均为旧版记录。",
                evidence={"current_orders": len(current_orders), "legacy_orders": legacy_orders},
                action="数据与盘口门禁恢复后，从右侧交易台启动受控 v2 paper cohort。",
            ),
            _check(
                self.key,
                "paper_validation_evidence",
                "pass" if evidence_ready else "fail",
                "14 日、30 笔权威结算、正 PnL 与优于市场 Brier 均已达标。" if evidence_ready else "模拟证据尚未同时满足 14 日、30 笔权威结算、正 PnL 和优于市场 Brier。",
                blocks=("paper_evidence", "live_canary"),
                evidence={
                    "max_validation_days": round(max_validation_days, 3),
                    "authoritative_settlements": len(authoritative),
                    "realized_pnl": round(pnl, 6),
                    "model_brier": model_brier_mean,
                    "market_brier": market_brier_mean,
                },
                action="完成连续 14-30 天模拟 cohort，并用 Polymarket resolved outcome 结算至少 30 笔后复核 ROI/Brier。",
            ),
            _check(
                self.key,
                "live_order_safety",
                "pass" if real_live_orders == 0 and not context.config.live_trading and context.config.live_dry_run else "fail",
                "实盘关闭、dry-run 开启，且没有真实 live order。" if real_live_orders == 0 and not context.config.live_trading and context.config.live_dry_run else "实盘安全状态不满足预验收要求。",
                blocks=("live_canary",),
                evidence={"live_trading": context.config.live_trading, "live_dry_run": context.config.live_dry_run, "real_live_orders": real_live_orders, "unknown_legacy_live_orders": unknown_live_orders},
                action="保持 LIVE_TRADING=false、LIVE_DRY_RUN=true，并审计任何非 dry-run live order。",
            ),
            _check(
                self.key,
                "live_execution_architecture",
                "pass" if LIVE_EXECUTION_PRODUCTION_READY else "fail",
                f"实盘执行器 {LIVE_EXECUTION_VERSION} 已具备生产风控与预提交幂等。" if LIVE_EXECUTION_PRODUCTION_READY else f"实盘执行器 {LIVE_EXECUTION_VERSION} 尚缺预提交幂等保留、聚合风险预算和 revision-bound 路由。",
                blocks=("live_canary",),
                evidence={"version": LIVE_EXECUTION_VERSION, "production_ready": LIVE_EXECUTION_PRODUCTION_READY},
                action="重构 live executor：先原子保留 idempotency，再检查余额/当日额度/持仓/回撤，并只接受 revision-bound 决策。",
            ),
        ]
        return _agent_result(self, checks, {"orders": len(orders), "settlements": len(settlements), "authoritative_settlements": len(authoritative)})


class OperatorSurfaceVerificationAgent(VerificationAgent):
    key = "operator_surface"
    label = "看板与操作面验证代理"

    def run(self, context: VerificationContext) -> dict[str, Any]:
        runtime = context.runtime
        if runtime.get("available") is None:
            availability_status = "warn"
            availability_message = "本轮跳过本地 API 探测。"
            blocks: tuple[str, ...] = ()
        elif runtime.get("available"):
            availability_status = "pass"
            availability_message = f"本地看板 API 可用，dashboard 延迟 {runtime.get('dashboard_latency_ms')}ms。"
            blocks = ()
        else:
            availability_status = "fail"
            availability_message = "本地看板 API 不可用。"
            blocks = READINESS_MODES
        unsafe_runtime = any(
            (
                runtime.get("legacy_scanner_running"),
                runtime.get("auto_simulation_enabled"),
                runtime.get("live_trading_enabled"),
            )
        )
        settings_safe = runtime.get("api_settings_safe")
        if settings_safe is None:
            settings_status = "warn"
        else:
            settings_status = "pass" if settings_safe else "fail"
        checks = [
            _check(
                self.key,
                "dashboard_runtime",
                availability_status,
                availability_message,
                blocks=blocks,
                evidence={"errors": runtime.get("errors"), "latency_ms": runtime.get("dashboard_latency_ms")},
                action="启动本地 FastAPI/Vite 并确认 /api/dashboard 在 5 秒内返回。",
            ),
            _check(
                self.key,
                "safe_runtime_defaults",
                "pass" if not unsafe_runtime else "fail",
                "legacy scanner、自动模拟和实盘均未误启动。" if not unsafe_runtime else "检测到不应默认运行的扫描、模拟或实盘状态。",
                blocks=READINESS_MODES,
                evidence={
                    "legacy_scanner_running": runtime.get("legacy_scanner_running"),
                    "auto_simulation_enabled": runtime.get("auto_simulation_enabled"),
                    "live_trading_enabled": runtime.get("live_trading_enabled"),
                    "scheduler_running": runtime.get("scheduler_running"),
                },
                action="立即停止意外运行路径并保持 LIVE_TRADING=false。",
            ),
            _check(
                self.key,
                "api_secret_boundary",
                settings_status,
                "API 设置只返回星号与配置状态。" if settings_safe else ("API 设置可能泄露完整凭据。" if settings_safe is False else "未探测 API 设置密钥边界。"),
                blocks=("paper", "paper_evidence", "live_canary") if settings_safe is False else (),
                evidence={"provider_count": runtime.get("api_provider_count")},
                action="修复 API 设置响应，禁止完整密钥离开本地后端。",
            ),
            _check(
                self.key,
                "browser_visual_acceptance",
                "warn",
                "机器验证不替代浏览器截图、console、溢出与 PolyWX 字段级对照。",
                evidence={"required": ["desktop screenshot", "console=0", "no horizontal overflow", "PolyWX field diff"]},
                action="每次 Layer 7 改动后执行浏览器双主题与 Shanghai/Chicago 抽样验收。",
            ),
        ]
        return _agent_result(self, checks, {"runtime_available": runtime.get("available"), "dashboard_latency_ms": runtime.get("dashboard_latency_ms")})


def probe_local_runtime(*, base_url: str = "http://127.0.0.1:8765", timeout_seconds: float = 10.0) -> dict[str, Any]:
    errors: list[str] = []
    dashboard: dict[str, Any] = {}
    settings: dict[str, Any] = {}
    scheduler: dict[str, Any] = {}
    started = time.perf_counter()
    try:
        dashboard = _get_json(f"{base_url.rstrip('/')}/api/dashboard", timeout_seconds)
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        settings = _get_json(f"{base_url.rstrip('/')}/api/developer/api-settings", timeout_seconds)
        scheduler = _get_json(f"{base_url.rstrip('/')}/api/scheduler/status", timeout_seconds)
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
        return {"probed": True, "available": False, "errors": errors}
    stats = dashboard.get("stats") or {}
    auto_sim = dashboard.get("auto_simulation") or {}
    live = dashboard.get("live_trading") or {}
    paper_validation = dashboard.get("paper_validation") or {}
    providers = settings.get("providers") or []
    settings_safe = all(_provider_secret_safe(provider) for provider in providers if isinstance(provider, dict))
    return {
        "probed": True,
        "available": True,
        "dashboard_latency_ms": latency_ms,
        "legacy_scanner_running": bool(stats.get("is_running") or stats.get("scanner_status") == "running"),
        "auto_simulation_enabled": bool(auto_sim.get("enabled")),
        "live_trading_enabled": bool(live.get("enabled")),
        "paper_validation_active": bool(paper_validation.get("active") or paper_validation.get("status") == "running"),
        "scheduler_running": bool(scheduler.get("running")),
        "api_settings_safe": settings_safe,
        "api_provider_count": len(providers),
        "errors": errors,
    }


def project_verification_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# WeatherBot Project Verification",
        "",
        f"- Version: `{report.get('verification_version')}`",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Scope: `{report.get('scope')}`",
        f"- Highest ready stage: `{report.get('highest_ready_stage')}`",
        "",
        "## Readiness",
        "",
        "| Stage | Ready | Blockers |",
        "| --- | --- | ---: |",
    ]
    for mode in READINESS_MODES:
        row = (report.get("readiness") or {}).get(mode) or {}
        lines.append(f"| {mode} | {'yes' if row.get('ready') else 'no'} | {len(row.get('blockers') or [])} |")
    for agent in report.get("agents") or []:
        lines.extend(["", f"## {agent.get('label')}", "", f"Status: `{agent.get('status')}`", ""])
        for check in agent.get("checks") or []:
            lines.append(f"- **{check.get('status')}** `{check.get('id')}`: {check.get('message')}")
    lines.extend(["", "## Next Actions", ""])
    for index, action in enumerate(report.get("next_actions") or [], 1):
        lines.append(f"{index}. {action.get('action')} (`{action.get('check_id')}`)")
    return "\n".join(lines) + "\n"


def _check(
    agent: str,
    check_id: str,
    status: str,
    message: str,
    *,
    blocks: Iterable[str] = (),
    evidence: dict[str, Any] | None = None,
    action: str = "",
) -> dict[str, Any]:
    return {
        "id": check_id,
        "agent": agent,
        "status": status,
        "message": message,
        "blocks": [mode for mode in blocks if mode in READINESS_MODES],
        "evidence": evidence or {},
        "action": action,
    }


def _agent_result(agent: VerificationAgent, checks: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, Any]:
    status = "fail" if any(check["status"] == "fail" for check in checks) else (
        "warn" if any(check["status"] == "warn" for check in checks) else "pass"
    )
    return {"key": agent.key, "label": agent.label, "status": status, "checks": checks, "metrics": metrics}


def _readiness(agents: list[dict[str, Any]]) -> dict[str, Any]:
    checks = [check for agent in agents for check in agent["checks"]]
    result: dict[str, Any] = {}
    inherited: set[str] = set()
    for mode in READINESS_MODES:
        inherited.add(mode)
        blockers = [
            {"id": check["id"], "agent": check["agent"], "message": check["message"]}
            for check in checks
            if check["status"] == "fail" and any(blocked in inherited for blocked in check["blocks"])
        ]
        result[mode] = {"ready": not blockers, "status": "ready" if not blockers else "blocked", "blockers": blockers}
    return result


def _highest_ready_stage(readiness: dict[str, Any]) -> str:
    highest = "code_only"
    for mode in READINESS_MODES:
        if not (readiness.get(mode) or {}).get("ready"):
            break
        highest = mode
    return highest


def _next_actions(failed_checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions = []
    seen = set()
    for check in failed_checks:
        action = str(check.get("action") or "").strip()
        if not action or action in seen:
            continue
        seen.add(action)
        actions.append({"priority": len(actions) + 1, "check_id": check["id"], "agent": check["agent"], "action": action})
    return actions[:10]


def _truth_coverage_gaps(source_health: dict[str, Any]) -> list[str]:
    gaps = []
    for row in source_health.get("city_matrix") or []:
        city = str(row.get("city_key") or "")
        sources = row.get("sources") or {}
        key = "truth_hko_daily" if city == "hong-kong" else "truth_wunderground_daily"
        if str((sources.get(key) or {}).get("status") or "missing") != "healthy":
            gaps.append(city)
    return gaps


def _provider_secret_safe(provider: dict[str, Any]) -> bool:
    allowed = {
        "key",
        "label",
        "description",
        "configured",
        "masked_value",
        "docs_url",
        "test_label",
        "test_has_side_effect",
    }
    if any(key not in allowed for key in provider):
        return False
    masked = str(provider.get("masked_value") or "")
    return not masked or set(masked) == {"*"}


def _get_json(url: str, timeout_seconds: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "WeatherBot-Verification/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} for {url}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"non_object_json:{url}")
    return payload


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        numeric = float(text)
        if numeric >= 1_000_000_000_000:
            numeric /= 1000.0
        if math.isfinite(numeric) and numeric > 0:
            return datetime.fromtimestamp(numeric, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decision_probability_price_violations(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    raw_probability = row.get("model_probability")
    raw_ask = row.get("market_ask")
    raw_edge = row.get("edge")
    probability = _finite(raw_probability)
    ask = _finite(raw_ask)
    edge = _finite(raw_edge)
    if raw_probability not in (None, "") and (probability is None or not 0 <= probability <= 1):
        reasons.append("invalid_model_probability")
    if raw_ask not in (None, "") and (ask is None or not 0 <= ask <= 1):
        reasons.append("invalid_market_price")
    if raw_edge not in (None, "") and edge is None:
        reasons.append("invalid_edge")
    if bool(row.get("paper_allowed")) and (
        probability is None
        or ask is None
        or edge is None
        or not 0 <= probability <= 1
        or not 0 < ask < 1
    ):
        reasons.append("paper_allowed_price_not_executable")
    return reasons


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _run_duration_days(row: dict[str, Any], now: datetime) -> float:
    started = _parse_time(row.get("started_at"))
    if not started:
        return 0.0
    ended = _parse_time(row.get("stopped_at"))
    if ended is None and str(row.get("status") or "") == "running":
        ended = min(_parse_time(row.get("ends_at")) or now, now)
    if ended is None:
        ended = _parse_time(row.get("ends_at"))
    if ended is None or ended <= started:
        return 0.0
    return (ended - started).total_seconds() / 86400.0


def _missing_database_report(path: Path, now: datetime) -> dict[str, Any]:
    blocker = {"id": "database_missing", "agent": "data_foundation", "message": f"数据库不存在：{path}", "blocks": list(READINESS_MODES)}
    readiness = {mode: {"ready": False, "status": "blocked", "blockers": [blocker]} for mode in READINESS_MODES}
    return {
        "verification_version": VERIFICATION_VERSION,
        "generated_at": now.isoformat(),
        "database_path": str(path),
        "status": "blocked",
        "highest_ready_stage": "code_only",
        "readiness": readiness,
        "summary": {"agents": 0, "passed_agents": 0, "warning_agents": 0, "failed_agents": 1, "failed_checks": 1, "warnings": 0},
        "agents": [],
        "blockers": [blocker],
        "next_actions": [{"priority": 1, "check_id": "database_missing", "agent": "data_foundation", "action": "初始化 WeatherBot v3 数据库。"}],
        "safety": {"live_trading": False, "live_dry_run": True, "paper_validation_active": False, "scheduler_running": False},
    }
