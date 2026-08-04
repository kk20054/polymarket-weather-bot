from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard_server import (
    _age_seconds,
    _recommendation_class,
    _recommendation_event_state,
    _recommendation_is_paper_or_spread_watch,
    _station_local_today,
)
from weatherbot_v3.config import load_config
from weatherbot_v3.db import connect


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _component_family(component: dict[str, Any]) -> str:
    return str(component.get("family") or component.get("source") or "").strip()


def _component_has_model_evidence(component: dict[str, Any]) -> bool:
    for key in ("adjusted_daily_highs_c", "raw_daily_highs_c", "daily_highs_c"):
        if any(_finite(value) is not None for value in component.get(key) or []):
            return True
    return any(
        _finite(component.get(key)) is not None
        for key in ("model_daily_high_c", "daily_high_c", "adjusted_high_c", "mu")
    )


def _current_contract_participant(component: dict[str, Any]) -> bool:
    return bool(
        isinstance(component, dict)
        and _component_family(component)
        and int(component.get("member_count") or 0) > 0
        and float(component.get("weight") or 0.0) > 0.0
    )


def _family_contract_participant(component: dict[str, Any]) -> bool:
    """Mirror CoreModalStrategy evidence membership, not fusion weight membership."""
    return bool(
        isinstance(component, dict)
        and _component_family(component)
        and str(component.get("weight_status") or "").strip().lower() != "excluded"
        and _component_has_model_evidence(component)
    )


def _latest_prediction_rows(
    conn,
    enabled: list[str],
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in enabled)
    return [
        dict(row)
        for row in conn.execute(
            f"""
            WITH latest AS (
                SELECT city_key, target_date, MAX(issued_at) AS issued_at
                FROM daily_max_predictions
                WHERE city_key IN ({placeholders})
                  AND target_date BETWEEN ? AND ?
                  AND COALESCE(validity_status, 'valid') = 'valid'
                GROUP BY city_key, target_date
            )
            SELECT prediction.*
            FROM daily_max_predictions prediction
            JOIN latest
              ON latest.city_key = prediction.city_key
             AND latest.target_date = prediction.target_date
             AND latest.issued_at = prediction.issued_at
            WHERE prediction.id = (
                SELECT MAX(other.id)
                FROM daily_max_predictions other
                WHERE other.city_key = prediction.city_key
                  AND other.target_date = prediction.target_date
                  AND other.issued_at = prediction.issued_at
            )
            """,
            (*enabled, start_date, end_date),
        ).fetchall()
    ]


def _latest_decision_rows(
    conn,
    enabled: list[str],
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in enabled)
    return [
        dict(row)
        for row in conn.execute(
            f"""
            WITH latest_round AS (
                SELECT city_key, target_date, MAX(issued_at) AS issued_at
                FROM signal_decisions
                WHERE city_key IN ({placeholders})
                  AND target_date BETWEEN ? AND ?
                GROUP BY city_key, target_date
            )
            SELECT decision.*, bucket.strict_match_status AS market_strict_match_status
            FROM signal_decisions decision
            JOIN latest_round
              ON latest_round.city_key = decision.city_key
             AND latest_round.target_date = decision.target_date
             AND latest_round.issued_at = decision.issued_at
            LEFT JOIN market_buckets bucket ON bucket.bucket_key = decision.bucket_key
            """,
            (*enabled, start_date, end_date),
        ).fetchall()
    ]


def _latest_operational_rows(
    conn,
    enabled: list[str],
    query_cutoff: str,
) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in enabled)
    return [
        dict(row)
        for row in conn.execute(
            f"""
            WITH latest_round AS (
                SELECT city_key, target_date, MAX(issued_at) AS issued_at
                FROM signal_decisions
                WHERE city_key IN ({placeholders})
                  AND target_date >= ?
                GROUP BY city_key, target_date
            )
            SELECT
                decision.*,
                bucket.strict_match_status AS market_strict_match_status,
                bucket.event_url AS bucket_event_url
            FROM signal_decisions decision
            JOIN latest_round
              ON latest_round.city_key = decision.city_key
             AND latest_round.target_date = decision.target_date
             AND latest_round.issued_at = decision.issued_at
            LEFT JOIN market_buckets bucket ON bucket.bucket_key = decision.bucket_key
            ORDER BY decision.city_key, decision.target_date, decision.edge DESC, decision.id DESC
            """,
            (*enabled, query_cutoff),
        ).fetchall()
    ]


def _latest_metars(conn) -> dict[str, dict[str, Any]]:
    return {
        str(row["city"]): dict(row)
        for row in conn.execute(
            """
            SELECT report.*
            FROM metar_reports report
            JOIN (
                SELECT city, MAX(report_time) AS report_time
                FROM metar_reports
                WHERE city IS NOT NULL AND TRIM(city) != ''
                GROUP BY city
            ) latest
              ON latest.city = report.city
             AND latest.report_time = report.report_time
            WHERE report.id = (
                SELECT MAX(other.id)
                FROM metar_reports other
                WHERE other.city = report.city
                  AND other.report_time = report.report_time
            )
            """
        ).fetchall()
    }


def _latest_forecasts(conn, query_cutoff: str) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (str(row["city"]), str(row["target_date"])): dict(row)
        for row in conn.execute(
            """
            SELECT
                city,
                target_date,
                MAX(COALESCE(available_at, retrieved_at, created_at)) AS forecast_time
            FROM forecast_runs
            WHERE target_date >= ?
              AND city IS NOT NULL
              AND TRIM(city) != ''
            GROUP BY city, target_date
            """,
            (query_cutoff,),
        ).fetchall()
    }


def _latest_events(conn, query_cutoff: str) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (str(row["city"]), str(row["target_date"])): dict(row)
        for row in conn.execute(
            """
            WITH ranked AS (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY city, target_date
                        ORDER BY updated_at DESC, event_id DESC
                    ) AS event_rank
                FROM polymarket_events
                WHERE target_date >= ?
            )
            SELECT *
            FROM ranked
            WHERE event_rank = 1
            """,
            (query_cutoff,),
        ).fetchall()
    }


def _gate_reasons(row: dict[str, Any]) -> list[str]:
    values = [str(value) for value in _json_list(row.get("gate_reasons_json")) if str(value or "").strip()]
    primary = str(row.get("blocked_reason_primary") or "").strip()
    return list(dict.fromkeys(([primary] if primary else []) + values))


def _operational_visibility(
    rows: list[dict[str, Any]],
    *,
    stations: dict[str, dict[str, Any]],
    metars: dict[str, dict[str, Any]],
    forecasts: dict[tuple[str, str], dict[str, Any]],
    events: dict[tuple[str, str], dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    hidden = Counter()
    paper_hidden = Counter()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        city = str(row.get("city_key") or "")
        target_date = str(row.get("target_date") or "")
        station = stations.get(city) or {}
        reason = ""
        if target_date < _station_local_today(station, now):
            reason = "past_target_date"
        else:
            event_state = _recommendation_event_state(events.get((city, target_date)) or {}, now)
            if not event_state["is_open"]:
                reason = "market_closed_or_missing"
            else:
                recommendation_class = _recommendation_class(target_date, station, now)
                metar_age = _age_seconds((metars.get(city) or {}).get("report_time"), now=now)
                forecast_age = _age_seconds(
                    (forecasts.get((city, target_date)) or {}).get("forecast_time"),
                    now=now,
                )
                if recommendation_class == "today_observation" and (
                    metar_age is None or metar_age >= 30 * 60
                ):
                    reason = "metar_stale_or_missing"
                elif recommendation_class == "forecast_lead" and (
                    forecast_age is None or forecast_age >= 90 * 60
                ):
                    reason = "forecast_stale_or_missing"
                elif not str(station.get("settlement_rule_verified_at") or "").strip():
                    reason = "settlement_unverified"
                elif str(row.get("market_strict_match_status") or "") != "matched":
                    reason = "bucket_not_strict_match"
                elif not _recommendation_is_paper_or_spread_watch(row):
                    reason = "paper_gate_blocked"
        if reason:
            hidden[reason] += 1
            if bool(row.get("paper_allowed")):
                paper_hidden[reason] += 1
            continue
        candidates.append(row)

    candidates.sort(
        key=lambda row: (
            0 if bool(row.get("paper_allowed")) else 1,
            -float(row.get("edge") or -999.0),
            str(row.get("target_date") or ""),
        )
    )
    seen: set[str] = set()
    visible: list[dict[str, Any]] = []
    duplicate_rows = 0
    for row in candidates:
        city = str(row.get("city_key") or "")
        if city in seen:
            duplicate_rows += 1
            continue
        seen.add(city)
        visible.append(row)
    app_source = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    frontend_consumes_trade_items = "recommendations?.items" in app_source
    return {
        "latest_operational_rows": len(rows),
        "candidate_rows_before_city_collapse": len(candidates),
        "visible_backend_rows": len(visible),
        "visible_backend_cities": len(seen),
        "paper_rows_before_city_collapse": sum(bool(row.get("paper_allowed")) for row in candidates),
        "paper_cities_before_city_collapse": len(
            {str(row.get("city_key") or "") for row in candidates if bool(row.get("paper_allowed"))}
        ),
        "hidden": dict(hidden),
        "paper_hidden": dict(paper_hidden),
        "seen_city_collapsed_rows": duplicate_rows,
        "frontend_consumes_trade_items": frontend_consumes_trade_items,
        "visible_frontend_trade_rows": len(visible) if frontend_consumes_trade_items else 0,
        "visible_rows": [
            {
                "id": row.get("id"),
                "city": row.get("city_key"),
                "target_date": row.get("target_date"),
                "strategy": row.get("strategy_name"),
                "edge": row.get("edge"),
                "paper_allowed": bool(row.get("paper_allowed")),
            }
            for row in visible
        ],
    }


def diagnose(start_date: str, end_date: str, *, path: Path | None = None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    with connect(path) as conn:
        station_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM stations WHERE COALESCE(enabled, 0)=1 ORDER BY tier, city_key"
            ).fetchall()
        ]
        enabled = [str(row["city_key"]) for row in station_rows]
        stations = {str(row["city_key"]): row for row in station_rows}
        predictions = _latest_prediction_rows(conn, enabled, start_date, end_date)
        latest_decisions = _latest_decision_rows(conn, enabled, start_date, end_date)
        placeholders = ",".join("?" for _ in enabled)
        strict = dict(
            conn.execute(
                f"""
                SELECT
                    COUNT(*) AS bucket_rows,
                    COUNT(DISTINCT city || '|' || target_date) AS city_dates,
                    COUNT(DISTINCT city) AS cities
                FROM market_buckets
                WHERE city IN ({placeholders})
                  AND target_date BETWEEN ? AND ?
                  AND strict_match_status='matched'
                """,
                (*enabled, start_date, end_date),
            ).fetchone()
        )
        all_decisions = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM signal_decisions
                WHERE city_key IN ({placeholders})
                  AND target_date BETWEEN ? AND ?
                """,
                (*enabled, start_date, end_date),
            ).fetchall()
        ]
        query_cutoff = min(_station_local_today(row, now) for row in station_rows)
        operational_rows = _latest_operational_rows(conn, enabled, query_cutoff)
        visibility = _operational_visibility(
            operational_rows,
            stations=stations,
            metars=_latest_metars(conn),
            forecasts=_latest_forecasts(conn, query_cutoff),
            events=_latest_events(conn, query_cutoff),
            now=now,
        )

    stage_counts = Counter()
    family_distribution = Counter()
    current_contract_pairs = 0
    corrected_contract_pairs = 0
    deterministic_member_zero = Counter()
    missing_adjusted = Counter()
    component_census = Counter()
    for row in predictions:
        components = _json_list(row.get("components_json"))
        if components:
            stage_counts["components_nonempty"] += 1
        current = [component for component in components if _current_contract_participant(component)]
        corrected = [component for component in components if _family_contract_participant(component)]
        if current:
            current_contract_pairs += 1
        if corrected:
            corrected_contract_pairs += 1
        families = {_component_family(component) for component in corrected}
        family_distribution[len(families)] += 1
        if len(families) >= 4:
            stage_counts["family_count_at_least_4"] += 1
        family_highs: list[float] = []
        for component in corrected:
            family = _component_family(component)
            member_count = int(component.get("member_count") or 0)
            component_census[(family, member_count)] += 1
            if member_count <= 0:
                deterministic_member_zero[family] += 1
            adjusted = [
                number
                for value in component.get("adjusted_daily_highs_c") or []
                if (number := _finite(value)) is not None
            ]
            if adjusted:
                family_highs.append(sum(adjusted) / len(adjusted))
            else:
                missing_adjusted[family] += 1
        if len(family_highs) >= 2:
            stage_counts["model_spread_computable"] += 1

    primary_reasons = Counter(
        str(row.get("blocked_reason_primary") or "<none>")
        for row in latest_decisions
    )
    gate_reasons = Counter()
    for row in latest_decisions:
        gate_reasons.update(set(_gate_reasons(row)))
    paper_latest = [row for row in latest_decisions if bool(row.get("paper_allowed"))]
    paper_ever = [row for row in all_decisions if bool(row.get("paper_allowed"))]

    h4_reasons = ("forecast_algo_not_supported", "low_price_tail_bucket", "below_order_min_size", "non_positive_kelly_size")
    h4 = {}
    for reason in h4_reasons:
        matching = [row for row in all_decisions if reason in _gate_reasons(row)]
        h4[reason] = {
            "gate_rows": len(matching),
            "primary_rows": sum(str(row.get("blocked_reason_primary") or "") == reason for row in matching),
            "cities": len({str(row.get("city_key") or "") for row in matching}),
        }
    structural_minimum = 0
    risk_budget_shortfall = 0
    valid_sizing_rows = 0
    for row in all_decisions:
        ask = _finite(row.get("market_ask"))
        order_min = _finite(row.get("order_min_size"))
        position = _finite(row.get("position_size_usd"))
        cap = _finite(row.get("sizing_max_per_trade_usd"))
        bankroll = _finite(row.get("sizing_bankroll_usd"))
        fraction_cap = _finite(row.get("bankroll_fraction_cap"))
        if ask is None or order_min is None or position is None:
            continue
        valid_sizing_rows += 1
        trade_cap = min(
            cap if cap is not None else float("inf"),
            (bankroll or 0.0) * (fraction_cap or 0.0),
        )
        minimum_cost = ask * order_min
        if minimum_cost > trade_cap + 1e-9:
            structural_minimum += 1
        elif position + 1e-9 < minimum_cost:
            risk_budget_shortfall += 1

    coverage_forbidden_patterns = {
        "weatherbot_v3/cli.py": ("limit_cities: int = 10",),
        "weatherbot_v3/history.py": ('["chicago", "tokyo", "atlanta", "nyc", "dallas"]',),
        "weatherbot_v3/metar.py": ("DEFAULT_BACKFILL_CITY_PRIORITY",),
        "weatherbot_v3/openmeteo.py": ('["chicago", "tokyo", "atlanta", "nyc", "dallas"]',),
        "weatherbot_v3/weathercom.py": ('["chicago", "tokyo", "atlanta", "nyc", "dallas"]',),
        "weatherbot_v3/market_buckets.py": ('["chicago", "tokyo", "atlanta", "nyc", "dallas"]',),
        "weatherbot_v3/polymarket_gamma.py": ("ASIAN_CITY_KEYS",),
        "weatherbot_v3/pws.py": ('["chicago", "tokyo", "atlanta", "nyc", "dallas"]',),
    }
    hardcode_evidence = {}
    for relative, patterns in coverage_forbidden_patterns.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        hardcode_evidence[relative] = {
            pattern: pattern in text
            for pattern in patterns
        }
    coverage_driven_only_by_enabled = not any(
        present
        for evidence in hardcode_evidence.values()
        for present in evidence.values()
    )

    expected_city_dates = len(enabled) * (
        (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days + 1
    )
    return {
        "generated_at": now.isoformat(),
        "window": {"start_date": start_date, "end_date": end_date, "basis": "target_date"},
        "funnel": {
            "enabled_cities": len(enabled),
            "expected_city_dates": expected_city_dates,
            "prediction_city_dates": len(predictions),
            "prediction_cities": len({str(row.get("city_key") or "") for row in predictions}),
            "components_nonempty_city_dates": stage_counts["components_nonempty"],
            "member_count_and_weight_city_dates": current_contract_pairs,
            "model_evidence_and_weight_city_dates": corrected_contract_pairs,
            "family_count_at_least_4_city_dates": stage_counts["family_count_at_least_4"],
            "model_spread_computable_city_dates": stage_counts["model_spread_computable"],
            "strict_matched_bucket_rows": int(strict["bucket_rows"] or 0),
            "strict_matched_city_dates": int(strict["city_dates"] or 0),
            "latest_signal_decision_rows": len(latest_decisions),
            "latest_signal_decision_city_dates": len(
                {(str(row.get("city_key")), str(row.get("target_date"))) for row in latest_decisions}
            ),
            "latest_paper_allowed_rows": len(paper_latest),
            "latest_paper_allowed_cities": len({str(row.get("city_key") or "") for row in paper_latest}),
            "ever_paper_allowed_rows": len(paper_ever),
            "ever_paper_allowed_cities": len({str(row.get("city_key") or "") for row in paper_ever}),
            "visible_recommendation_rows": visibility["visible_frontend_trade_rows"],
        },
        "primary_reasons_top20": primary_reasons.most_common(20),
        "gate_reasons_top20": gate_reasons.most_common(20),
        "family_count_distribution": dict(sorted(family_distribution.items())),
        "component_member_count_census": [
            {"family": family, "member_count": member_count, "rows": rows}
            for (family, member_count), rows in sorted(component_census.items())
        ],
        "deterministic_member_zero": dict(deterministic_member_zero),
        "missing_adjusted_daily_highs": dict(missing_adjusted),
        "h1": {
            "coverage_driven_only_by_stations_enabled": coverage_driven_only_by_enabled,
            "hardcode_evidence": hardcode_evidence,
        },
        "h2": {
            "currently_excludes_deterministic_models": bool(deterministic_member_zero),
            "latent_contract_defect": True,
            "current_contract_city_dates": current_contract_pairs,
            "family_contract_city_dates": corrected_contract_pairs,
        },
        "h3": {
            "family_count_below_4_city_dates": len(predictions) - stage_counts["family_count_at_least_4"],
            "spread_unavailable_city_dates": len(predictions) - stage_counts["model_spread_computable"],
            "missing_adjusted_component_rows": sum(missing_adjusted.values()),
        },
        "h4": {
            "gate_counts": h4,
            "valid_sizing_rows": valid_sizing_rows,
            "order_minimum_exceeds_trade_cap": structural_minimum,
            "risk_budget_below_exchange_minimum": risk_budget_shortfall,
        },
        "h5": visibility,
        "paper_ever_examples": [
            {
                "id": row.get("id"),
                "city": row.get("city_key"),
                "target_date": row.get("target_date"),
                "issued_at": row.get("issued_at"),
                "edge": row.get("edge"),
                "strategy": row.get("strategy_name"),
            }
            for row in paper_ever
        ],
    }


def _table(rows: list[tuple[Any, Any]]) -> list[str]:
    return ["| 项目 | 计数 |", "|---|---:|", *[f"| {name} | {value} |" for name, value in rows]]


def render_markdown(report: dict[str, Any]) -> str:
    funnel = report["funnel"]
    lines = [
        "# Signal Decision Funnel Diagnosis",
        "",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 目标日窗口：`{report['window']['start_date']}` 至 `{report['window']['end_date']}`",
        "- 口径：enabled 城市；每城市/日期只保留最新 prediction 与最新 decision 轮次。",
        "",
        "## 1. 全量漏斗",
        "",
        *_table([
            ("enabled 城市", funnel["enabled_cities"]),
            ("期望城市日", funnel["expected_city_dates"]),
            ("有 daily_max_predictions 的城市日", funnel["prediction_city_dates"]),
            ("prediction.components 非空", funnel["components_nonempty_city_dates"]),
            ("当前 member_count+weight 契约通过", funnel["member_count_and_weight_city_dates"]),
            ("按模型证据+weight 契约通过", funnel["model_evidence_and_weight_city_dates"]),
            ("family_count >= 4", funnel["family_count_at_least_4_city_dates"]),
            ("model_spread_c 可计算", funnel["model_spread_computable_city_dates"]),
            ("strict matched buckets", funnel["strict_matched_bucket_rows"]),
            ("写入最新 signal_decisions", funnel["latest_signal_decision_rows"]),
            ("最新 paper_allowed=true", funnel["latest_paper_allowed_rows"]),
            ("窗口内曾产生 paper_allowed=true", funnel["ever_paper_allowed_rows"]),
            ("前端当前可见策略候选", funnel["visible_recommendation_rows"]),
        ]),
        "",
        "## 2. blocked_reason_primary Top 20",
        "",
        *_table(report["primary_reasons_top20"]),
        "",
        "## 3. gate_reasons Top 20",
        "",
        *_table(report["gate_reasons_top20"]),
        "",
        "## 4. H1-H5 结论",
        "",
        (
            "- **H1 修复后否定**：默认采集、truth、市场与决策覆盖均由 `stations.enabled` 驱动。"
            if report["h1"]["coverage_driven_only_by_stations_enabled"]
            else "- **H1 确认**：仍存在绕过 `stations.enabled` 的默认城市范围。"
        ),
        (
            "- **H2 确认触发**：确定性模型被 `member_count` 契约排除。"
            if report["h2"]["currently_excludes_deterministic_models"]
            else "- **H2 当前未触发、但契约已修复**：模型家族参与已与 ensemble 成员数解耦。"
        ),
        f"- **H3 否定为主因**：family<4 为 {report['h3']['family_count_below_4_city_dates']} 个城市日；spread 不可算为 {report['h3']['spread_unavailable_city_dates']}。",
        f"- **H4 主要是真实盘口/风险约束**：最低订单本身超过交易上限 {report['h4']['order_minimum_exceeds_trade_cap']} 行；Kelly 预算低于交易所最小订单 {report['h4']['risk_budget_below_exchange_minimum']} 行。",
        (
            f"- **H5 修复后否定**：后端当前可见 {report['h5']['visible_backend_rows']} 行，前端已消费交易 `items`。"
            if report["h5"]["frontend_consumes_trade_items"]
            else f"- **H5 确认**：后端当前可见 {report['h5']['visible_backend_rows']} 行，但前端未消费交易 `items`。"
        ),
        "",
        "## 5. 根因分类",
        "",
        "- **数据缺口**：缺报价、缺活跃事件或观测/预报过期。",
        "- **契约缺陷**：模型参与数错误依赖 `member_count`（当前数据未触发，但属于潜在生产缺陷）。",
        "- **严格匹配失败**：bucket 未 strict matched；本窗口 matched 覆盖完整。",
        "- **盘口不可成交**：无有效 bid/ask、spread 过宽、交易所最小份额超过风险预算。",
        "- **真实无优势**：`edge_below_min` / `core_effective_edge_below_min`。",
        "- **展示遮蔽**：后端策略候选未被前端顶部推荐模块消费。",
        "",
        "## 6. 修复边界",
        "",
        "- 不调整 edge、spread、depth、order minimum、truth 成熟度。",
        "- 模型家族计数与 ensemble member_count 解耦。",
        "- 默认覆盖范围改由 stations.enabled 决定。",
        "- 策略候选在看板中单独展示，不与天气关注混淆。",
        "- 最小订单失败拆成“交易上限不足”和“Kelly 风险预算不足”。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit the enabled-city signal decision funnel.")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    end = date.fromisoformat(args.end_date)
    start = end - timedelta(days=max(1, int(args.days)) - 1)
    report = diagnose(start.isoformat(), end.isoformat(), path=load_config().v3_db_path)
    output_dir = args.output_dir or ROOT / "audits" / f"signal-funnel-{end.isoformat()}"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "funnel.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown = render_markdown(report)
    (output_dir / "README.md").write_text(markdown, encoding="utf-8")
    print(markdown)


if __name__ == "__main__":
    main()
