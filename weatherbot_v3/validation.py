from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import load_config
from .db import connect, dashboard_summary, init_v3_db
from .model_dataset import build_model_dataset_audit
from .qualification import build_data_readiness


VALIDATION_VERSION = "production-validation-v1"


def build_production_validation_report(
    path: Path | None = None,
    *,
    dashboard_runtime: dict[str, Any] | None = None,
    min_validation_days: int = 14,
    min_signals: int = 50,
    min_settled_paper: int = 30,
    include_action_targets: bool = False,
    action_target_preview_limit: int = 5,
) -> dict[str, Any]:
    """Build a bottom-up production validation report without mutating state."""

    init_v3_db(path)
    cfg = load_config()
    data_readiness = build_data_readiness(path)
    model_audit = build_model_dataset_audit(path)
    summary = dashboard_summary_for_path(path)
    runtime = dict(dashboard_runtime or {})

    data_layer = _layer(
        "data_foundation",
        "数据基座",
        data_readiness.get("status") == "ready" and data_readiness.get("live_allowed"),
        data_readiness.get("production_phase", {}).get("blocked_keys", []),
        _compact_actions(
            data_readiness.get("next_actions", [])[:3],
            include_targets=include_action_targets,
            preview_limit=action_target_preview_limit,
        ),
        {
            "score": data_readiness.get("score"),
            "phase": data_readiness.get("production_phase", {}),
            "summary": data_readiness.get("summary", {}),
        },
    )

    model_summary = model_audit.get("summary", {})
    model_reasons = []
    if model_audit.get("status") != "ready":
        model_reasons.append("model_dataset_not_ready")
    if int(model_summary.get("baseline_ready_samples") or 0) < int(model_audit.get("required_samples") or 0):
        model_reasons.append("baseline_ready_samples_below_required")
    if int(model_summary.get("replay_ready_samples") or 0) < min_settled_paper:
        model_reasons.append("orderbook_replay_samples_below_live_gate")
    model_layer = _layer(
        "leakage_free_model",
        "无泄漏概率模型",
        not model_reasons,
        model_reasons,
        _compact_actions(
            model_audit.get("next_actions", [])[:3],
            include_targets=include_action_targets,
            preview_limit=action_target_preview_limit,
        ),
        {
            "required_samples": model_audit.get("required_samples"),
            "summary": model_summary,
            "leakage_flags": model_audit.get("leakage_flags", {}),
            "reason_counts": model_audit.get("reason_counts", {}),
        },
    )

    paper_counts = _paper_counts(path)
    paper_reasons = []
    if paper_counts["paper_orders"] == 0:
        paper_reasons.append("paper_orders_missing")
    if paper_counts["settled_paper_orders"] < min_settled_paper:
        paper_reasons.append("settled_paper_orders_below_required")
    if paper_counts["signals"] < min_signals:
        paper_reasons.append("signal_sample_below_required")
    paper_layer = _layer(
        "realistic_paper_execution",
        "真实模拟成交",
        not paper_reasons,
        paper_reasons,
        [
            {
                "key": "run_paper_validation",
                "label": "积累模拟成交样本",
                "count": max(0, min_settled_paper - paper_counts["settled_paper_orders"]),
                "command": "Use dashboard one-click simulation after manual fetch, then settle resolved markets.",
                "requires_operator": True,
            }
        ],
        {
            **paper_counts,
            "required_signals": min_signals,
            "required_settled_paper_orders": min_settled_paper,
        },
    )

    dashboard_reasons = []
    if runtime.get("loading"):
        dashboard_reasons.append("dashboard_loading")
    if runtime.get("scanner_status") == "running" or runtime.get("is_running"):
        dashboard_reasons.append("legacy_scanner_running")
    if runtime.get("auto_simulation_enabled"):
        dashboard_reasons.append("auto_simulation_running")
    dashboard_layer = _layer(
        "production_dashboard",
        "生产看板",
        not dashboard_reasons,
        dashboard_reasons,
        [
            {
                "key": "browser_dashboard_check",
                "label": "浏览器验收看板",
                "count": 1,
                "command": "Open http://127.0.0.1:5173 and verify no loading state, no console errors, and no horizontal overflow.",
                "requires_operator": False,
            }
        ],
        {
            "runtime": runtime,
            "v3_summary": summary,
        },
    )

    validation_reasons = []
    if min_validation_days > 0:
        validation_reasons.append("validation_window_not_completed")
    if not data_layer["ready"]:
        validation_reasons.append("data_foundation_not_ready")
    if not model_layer["ready"]:
        validation_reasons.append("model_not_ready")
    if not paper_layer["ready"]:
        validation_reasons.append("paper_execution_not_ready")
    live_layer = _layer(
        "small_live_canary",
        "小额实盘 Canary",
        False,
        list(dict.fromkeys(validation_reasons)),
        [
            {
                "key": "keep_live_locked",
                "label": "继续锁定实盘",
                "count": 1,
                "command": "Keep LIVE_TRADING=false until data/model/paper gates pass and 14-30 day validation is complete.",
                "requires_operator": False,
            }
        ],
        {
            "live_trading_configured": bool(cfg.live_trading),
            "live_dry_run": bool(cfg.live_dry_run),
            "canary_max_order_usd": cfg.canary_max_order_usd,
            "minimum_validation_days": min_validation_days,
        },
    )

    layers = [data_layer, model_layer, paper_layer, dashboard_layer, live_layer]
    ready_layers = sum(1 for layer in layers if layer["ready"])
    hard_blockers = [
        reason
        for layer in layers
        for reason in layer["blockers"]
    ]
    return {
        "validation_version": VALIDATION_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "ready_for_canary" if live_layer["ready"] else "blocked",
        "score": round(ready_layers / len(layers), 3),
        "ready_layers": ready_layers,
        "total_layers": len(layers),
        "live_allowed": False,
        "hard_blockers": list(dict.fromkeys(hard_blockers)),
        "layers": layers,
        "next_actions": _merge_next_actions(layers),
    }


def dashboard_summary_for_path(path: Path | None = None) -> dict[str, Any]:
    if path is None:
        return dashboard_summary()
    init_v3_db(path)
    with connect(path) as conn:
        def count(sql: str) -> int:
            return int(conn.execute(sql).fetchone()[0])

        return {
            "markets": count("SELECT COUNT(*) FROM markets"),
            "signals": count("SELECT COUNT(*) FROM signals"),
            "paper_orders": count("SELECT COUNT(*) FROM paper_orders"),
            "live_orders": count("SELECT COUNT(*) FROM live_orders"),
            "settlements": count("SELECT COUNT(*) FROM settlements"),
        }


def _paper_counts(path: Path | None = None) -> dict[str, int]:
    init_v3_db(path)
    with connect(path) as conn:
        def count(sql: str) -> int:
            return int(conn.execute(sql).fetchone()[0])

        return {
            "signals": count("SELECT COUNT(*) FROM signals"),
            "paper_orders": count("SELECT COUNT(*) FROM paper_orders"),
            "filled_paper_orders": count("SELECT COUNT(*) FROM paper_orders WHERE status IN ('filled', 'simulated', 'open')"),
            "settled_paper_orders": count("SELECT COUNT(DISTINCT po.id) FROM paper_orders po JOIN settlements s ON s.market_id = po.market_id"),
            "live_orders": count("SELECT COUNT(*) FROM live_orders"),
        }


def _layer(
    key: str,
    label: str,
    ready: bool,
    blockers: list[Any],
    next_actions: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    clean_blockers = [str(item) for item in blockers if str(item)]
    return {
        "key": key,
        "label": label,
        "ready": bool(ready),
        "status": "ready" if ready else "blocked",
        "blockers": list(dict.fromkeys(clean_blockers)),
        "next_actions": next_actions,
        "metrics": metrics,
    }


def _merge_next_actions(layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    seen = set()
    for layer in layers:
        for action in layer.get("next_actions") or []:
            key = str(action.get("key") or "")
            if key in seen:
                continue
            seen.add(key)
            actions.append({**action, "layer": layer.get("key")})
    return actions[:8]


def _compact_actions(
    actions: list[dict[str, Any]],
    *,
    include_targets: bool = False,
    preview_limit: int = 5,
) -> list[dict[str, Any]]:
    return [
        _compact_action(action, include_targets=include_targets, preview_limit=preview_limit)
        for action in actions
    ]


def _compact_action(
    action: dict[str, Any],
    *,
    include_targets: bool = False,
    preview_limit: int = 5,
) -> dict[str, Any]:
    """Keep validation actions dashboard-safe while preserving audit breadcrumbs."""

    clean = dict(action)
    targets = clean.get("targets")
    if include_targets or targets is None:
        return clean

    if isinstance(targets, list):
        clean.pop("targets", None)
        clean["targets_count"] = len(targets)
        if preview_limit > 0:
            clean["targets_preview"] = targets[:preview_limit]
    return clean
