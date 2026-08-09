from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .db import connect, init_v3_db, utc_now


STRATEGY_PROFILE_SCHEMA_VERSION = 1
STRATEGY_ENGINE_VERSION = "weatherbot-strategy-v3"
DEFAULT_PROFILE_KEY = "weatherbot_conservative"
ALLOWED_SCOPES = {"signal_generation", "paper_default", "live_default"}
PAPER_STRATEGY_NAMES = (
    "core_modal_v1",
    "single_bucket_ev",
    "ladder_grid",
    "tail_buying",
)

DEFAULT_PARAMETERS: dict[str, Any] = {
    "schema_version": STRATEGY_PROFILE_SCHEMA_VERSION,
    "decision_policy": {
        "min_paper_trade_edge": 0.05,
        "min_live_trade_edge": 0.08,
        "max_spread_bps": 500.0,
        "stale_book_seconds": 300.0,
        "min_bias_sample_days": 7,
        "low_price_tail_ask": 0.05,
    },
    "sizing": {
        "paper_kelly_multiplier": 0.25,
        "live_kelly_multiplier": 0.15,
        "max_paper_bankroll_fraction_per_trade": 0.125,
        "max_live_bankroll_fraction_per_trade": 0.05,
    },
    "strategies": {
        "core_modal_v1": {
            "enabled": False,
            "min_paper_effective_edge": 0.05,
            "min_live_effective_edge": 0.08,
            "min_model_probability": 0.25,
            "max_model_rank": 2,
            "min_paper_market_ask": 0.05,
            "min_live_market_ask": 0.10,
            "min_paper_settlement_days": 0,
            "min_live_settlement_days": 20,
            "require_authoritative_truth": True,
            "min_paper_component_calibration_days": 0,
            "min_live_component_calibration_days": 20,
            "min_calibration_coverage": 0.80,
            "min_model_families": 4,
            "max_paper_model_spread_c": 4.50,
            "max_live_model_spread_c": 1.50,
            "max_model_spread_c": 1.50,
            "provisional_position_multiplier": 1.00,
        },
        "single_bucket_ev": {"enabled": True, "min_edge": 0.05},
        "ladder_grid": {
            "enabled": True,
            "min_edge": 0.03,
            "neighbor_count": 1,
            "group_exposure_multiplier": 0.60,
            "atomic": True,
        },
        "tail_buying": {
            "enabled": True,
            "max_ask": 0.15,
            "min_edge": 0.10,
            "min_live_settlement_days": 20,
            "max_order_usd": 50.0,
            "daily_candidate_cap": 5,
        },
    },
    "exit_policy": {
        "mode": "hold_to_settlement",
        "model_probability_threshold": 0.08,
        "min_bid_over_model_edge": 0.02,
        "confirmations_required": 2,
        "min_hold_minutes": 30,
        "max_quote_age_seconds": 300,
        "take_profit_min_roi": 0.05,
        "take_profit_min_usd": 0.05,
        "take_profit_min_ticks": 1,
        "take_profit_min_hold_minutes": 15,
    },
}


def canonical_parameters(parameters: dict[str, Any]) -> str:
    normalized = validate_parameters(parameters)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def validate_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(parameters, dict):
        raise ValueError("strategy_parameters_must_be_object")
    merged = _deep_merge(DEFAULT_PARAMETERS, _migrate_legacy_parameters(parameters))
    if int(merged.get("schema_version") or 0) != STRATEGY_PROFILE_SCHEMA_VERSION:
        raise ValueError("unsupported_strategy_profile_schema")

    decision = merged["decision_policy"]
    sizing = merged["sizing"]
    strategies = merged["strategies"]
    _bounded(decision, "min_paper_trade_edge", 0.0, 0.5)
    _bounded(decision, "min_live_trade_edge", 0.0, 0.5)
    if decision["min_live_trade_edge"] < decision["min_paper_trade_edge"]:
        raise ValueError("live_trade_edge_below_paper")
    _bounded(decision, "max_spread_bps", 0.0, 5000.0)
    _bounded(decision, "stale_book_seconds", 30.0, 3600.0)
    _bounded(decision, "min_bias_sample_days", 0, 365, integer=True)
    _bounded(decision, "low_price_tail_ask", 0.0, 0.5)
    _bounded(sizing, "paper_kelly_multiplier", 0.0, 1.0)
    _bounded(sizing, "live_kelly_multiplier", 0.0, 1.0)
    _bounded(sizing, "max_paper_bankroll_fraction_per_trade", 0.001, 0.25)
    _bounded(sizing, "max_live_bankroll_fraction_per_trade", 0.001, 0.25)

    core_modal = strategies["core_modal_v1"]
    single = strategies["single_bucket_ev"]
    ladder = strategies["ladder_grid"]
    tail = strategies["tail_buying"]
    _bounded(core_modal, "min_paper_effective_edge", 0.0, 0.5)
    _bounded(core_modal, "min_live_effective_edge", 0.0, 0.5)
    if core_modal["min_live_effective_edge"] < core_modal["min_paper_effective_edge"]:
        raise ValueError("core_modal_live_edge_below_paper")
    _bounded(core_modal, "min_model_probability", 0.0, 1.0)
    _bounded(core_modal, "max_model_rank", 1, 2, integer=True)
    _bounded(core_modal, "min_paper_market_ask", 0.01, 0.5)
    _bounded(core_modal, "min_live_market_ask", 0.01, 0.5)
    if core_modal["min_live_market_ask"] < core_modal["min_paper_market_ask"]:
        raise ValueError("core_modal_live_market_ask_below_paper")
    _bounded(core_modal, "min_paper_settlement_days", 0, 365, integer=True)
    _bounded(core_modal, "min_live_settlement_days", 20, 365, integer=True)
    if core_modal["min_live_settlement_days"] < core_modal["min_paper_settlement_days"]:
        raise ValueError("core_modal_live_settlement_days_below_paper")
    core_modal["require_authoritative_truth"] = bool(core_modal.get("require_authoritative_truth", True))
    _bounded(core_modal, "min_paper_component_calibration_days", 0, 365, integer=True)
    _bounded(core_modal, "min_live_component_calibration_days", 20, 365, integer=True)
    if core_modal["min_live_component_calibration_days"] < core_modal["min_paper_component_calibration_days"]:
        raise ValueError("core_modal_live_component_days_below_paper")
    _bounded(core_modal, "min_calibration_coverage", 0.0, 1.0)
    _bounded(core_modal, "min_model_families", 1, 20, integer=True)
    _bounded(core_modal, "max_paper_model_spread_c", 0.1, 10.0)
    _bounded(core_modal, "max_live_model_spread_c", 0.1, 10.0)
    if core_modal["max_live_model_spread_c"] > core_modal["max_paper_model_spread_c"]:
        raise ValueError("core_modal_live_model_spread_above_paper")
    _bounded(core_modal, "max_model_spread_c", 0.1, 10.0)
    _bounded(core_modal, "provisional_position_multiplier", 0.0, 1.0)
    _bounded(single, "min_edge", 0.0, 0.5)
    _bounded(ladder, "min_edge", 0.0, 0.5)
    _bounded(ladder, "neighbor_count", 1, 1, integer=True)
    _bounded(ladder, "group_exposure_multiplier", 0.0, 1.0)
    _bounded(tail, "max_ask", 0.01, 0.5)
    _bounded(tail, "min_edge", 0.0, 0.5)
    _bounded(tail, "min_live_settlement_days", 0, 365, integer=True)
    _bounded(tail, "max_order_usd", 0.1, 1000.0)
    _bounded(tail, "daily_candidate_cap", 1, 100, integer=True)
    for item in (core_modal, single, ladder, tail):
        item["enabled"] = bool(item.get("enabled", True))
    ladder["atomic"] = True
    exit_policy = merged.get("exit_policy", {})
    if exit_policy.get("mode") not in {
        "hold_to_settlement",
        "model_guarded",
        "model_guarded_take_profit",
    }:
        raise ValueError("unsupported_exit_policy")
    _bounded(exit_policy, "model_probability_threshold", 0.0, 0.5)
    _bounded(exit_policy, "min_bid_over_model_edge", 0.0, 0.25)
    _bounded(exit_policy, "confirmations_required", 1, 10, integer=True)
    _bounded(exit_policy, "min_hold_minutes", 0, 1440, integer=True)
    _bounded(exit_policy, "max_quote_age_seconds", 30, 3600, integer=True)
    _bounded(exit_policy, "take_profit_min_roi", 0.0, 1.0)
    _bounded(exit_policy, "take_profit_min_usd", 0.0, 1000.0)
    _bounded(exit_policy, "take_profit_min_ticks", 1, 100, integer=True)
    _bounded(exit_policy, "take_profit_min_hold_minutes", 0, 1440, integer=True)
    return merged


def validate_paper_strategy_selection(strategies: list[str] | None) -> list[str]:
    """Keep a paper cohort on one entry policy until overlap allocation exists."""
    selected = list(dict.fromkeys(
        str(strategy or "").strip()
        for strategy in (strategies or [])
        if str(strategy or "").strip()
    ))
    unknown = [strategy for strategy in selected if strategy not in PAPER_STRATEGY_NAMES]
    if unknown:
        raise ValueError(f"unsupported_paper_strategy:{','.join(unknown)}")
    if len(selected) != 1:
        raise ValueError("paper_strategy_requires_exactly_one")
    return selected


def core_modal_v1_parameters() -> dict[str, Any]:
    """Return a paper-safe preset without mutating the active revision."""
    parameters = deepcopy(DEFAULT_PARAMETERS)
    for name, strategy in parameters["strategies"].items():
        strategy["enabled"] = name == "core_modal_v1"
    parameters["exit_policy"]["mode"] = "hold_to_settlement"
    return validate_parameters(parameters)


def dynamic_core_modal_paper_parameters() -> dict[str, Any]:
    """Return the controlled paper preset for calibrated dynamic model weights."""
    parameters = core_modal_v1_parameters()
    parameters["exit_policy"].update({
        "mode": "model_guarded",
        "model_probability_threshold": 0.08,
        "min_bid_over_model_edge": 0.02,
        "confirmations_required": 2,
        "min_hold_minutes": 30,
        "max_quote_age_seconds": 300,
    })
    return validate_parameters(parameters)


def create_strategy_profile_revision(
    parameters: dict[str, Any],
    *,
    profile_key: str = DEFAULT_PROFILE_KEY,
    created_by: str = "system",
    change_note: str = "",
    code_commit_sha: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    init_v3_db(path)
    clean_key = str(profile_key or DEFAULT_PROFILE_KEY).strip()
    normalized = validate_parameters(parameters)
    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    content_hash = hashlib.sha256(
        f"{clean_key}|{STRATEGY_PROFILE_SCHEMA_VERSION}|{STRATEGY_ENGINE_VERSION}|{canonical}".encode("utf-8")
    ).hexdigest()
    revision_id = f"spr_{content_hash[:32]}"
    now = utc_now()
    with connect(path) as conn:
        existing = conn.execute(
            "SELECT * FROM strategy_profile_revisions WHERE profile_key=? AND content_sha256=?",
            (clean_key, content_hash),
        ).fetchone()
        if existing:
            return _decode_revision(dict(existing))
        row = conn.execute(
            "SELECT COALESCE(MAX(revision_no), 0) AS max_revision FROM strategy_profile_revisions WHERE profile_key=?",
            (clean_key,),
        ).fetchone()
        revision_no = int(row["max_revision"] or 0) + 1
        conn.execute(
            """
            INSERT INTO strategy_profile_revisions (
                revision_id, profile_key, revision_no, parent_revision_id,
                schema_version, engine_version, content_sha256, parameters_json,
                strategy_names_json, validation_status, validation_report_json,
                code_commit_sha, created_by, change_note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'validated', ?, ?, ?, ?, ?)
            """,
            (
                revision_id,
                clean_key,
                revision_no,
                _latest_revision_id(conn, clean_key),
                STRATEGY_PROFILE_SCHEMA_VERSION,
                STRATEGY_ENGINE_VERSION,
                content_hash,
                canonical,
                json.dumps([key for key, value in normalized["strategies"].items() if value.get("enabled")]),
                json.dumps({"ok": True, "validator": "strategy_profiles.validate_parameters"}),
                str(code_commit_sha or ""),
                str(created_by or "system"),
                str(change_note or ""),
                now,
            ),
        )
        stored = conn.execute("SELECT * FROM strategy_profile_revisions WHERE revision_id=?", (revision_id,)).fetchone()
    return _decode_revision(dict(stored))


def activate_strategy_profile(
    revision_id: str,
    *,
    scope: str,
    actor: str = "system",
    reason: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    init_v3_db(path)
    clean_scope = str(scope or "").strip()
    if clean_scope not in ALLOWED_SCOPES:
        raise ValueError("unsupported_strategy_profile_scope")
    with connect(path) as conn:
        revision = conn.execute("SELECT * FROM strategy_profile_revisions WHERE revision_id=?", (revision_id,)).fetchone()
        if not revision:
            raise ValueError("strategy_profile_revision_not_found")
        current = _active_revision_row(conn, clean_scope)
        if current and current["revision_id"] == revision_id:
            return _decode_revision(dict(revision), active_scopes=[clean_scope])
        conn.execute(
            """
            INSERT INTO strategy_profile_activation_events (scope, revision_id, action, actor, reason, created_at)
            VALUES (?, ?, 'activate', ?, ?, ?)
            """,
            (clean_scope, revision_id, str(actor or "system"), str(reason or ""), utc_now()),
        )
    return get_strategy_profile_revision(revision_id, path=path) or {}


def get_active_strategy_profile(scope: str, *, path: Path | None = None) -> dict[str, Any] | None:
    init_v3_db(path)
    if scope not in ALLOWED_SCOPES:
        raise ValueError("unsupported_strategy_profile_scope")
    with connect(path) as conn:
        row = _active_revision_row(conn, scope)
    return _decode_revision(dict(row), active_scopes=[scope]) if row else None


def ensure_default_strategy_profile(scope: str, *, path: Path | None = None) -> dict[str, Any]:
    active = get_active_strategy_profile(scope, path=path)
    if active:
        normalized = validate_parameters(active.get("parameters") or {})
        if normalized == active.get("parameters"):
            return active
        revision = create_strategy_profile_revision(
            normalized,
            profile_key=str(active.get("profile_key") or DEFAULT_PROFILE_KEY),
            created_by="system",
            change_note="Migrate strategy profile defaults",
            path=path,
        )
        activate_strategy_profile(
            revision["revision_id"],
            scope=scope,
            actor="system",
            reason="migrate strategy profile defaults",
            path=path,
        )
        return get_active_strategy_profile(scope, path=path) or revision
    revision = create_strategy_profile_revision(
        DEFAULT_PARAMETERS,
        created_by="system",
        change_note="WeatherBot conservative default",
        path=path,
    )
    activate_strategy_profile(revision["revision_id"], scope=scope, actor="system", reason="bootstrap default", path=path)
    return get_active_strategy_profile(scope, path=path) or revision


def get_strategy_profile_revision(revision_id: str, *, path: Path | None = None) -> dict[str, Any] | None:
    init_v3_db(path)
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM strategy_profile_revisions WHERE revision_id=?", (revision_id,)).fetchone()
        scopes = [
            scope for scope in ALLOWED_SCOPES
            if (active := _active_revision_row(conn, scope)) and active["revision_id"] == revision_id
        ]
    return _decode_revision(dict(row), active_scopes=scopes) if row else None


def list_strategy_profile_revisions(*, path: Path | None = None) -> list[dict[str, Any]]:
    init_v3_db(path)
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM strategy_profile_revisions ORDER BY profile_key, revision_no DESC"
        ).fetchall()
        active = {
            scope: row["revision_id"]
            for scope in ALLOWED_SCOPES
            if (row := _active_revision_row(conn, scope))
        }
    return [
        _decode_revision(dict(row), active_scopes=[scope for scope, revision_id in active.items() if revision_id == row["revision_id"]])
        for row in rows
    ]


def profile_snapshot(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "revision_id": profile.get("revision_id"),
        "profile_key": profile.get("profile_key"),
        "revision_no": profile.get("revision_no"),
        "schema_version": profile.get("schema_version"),
        "engine_version": profile.get("engine_version"),
        "content_sha256": profile.get("content_sha256"),
        "parameters": deepcopy(profile.get("parameters") or {}),
    }


def _active_revision_row(conn, scope: str):
    event = conn.execute(
        "SELECT * FROM strategy_profile_activation_events WHERE scope=? ORDER BY activation_id DESC LIMIT 1",
        (scope,),
    ).fetchone()
    if not event or event["action"] != "activate":
        return None
    return conn.execute(
        "SELECT * FROM strategy_profile_revisions WHERE revision_id=?",
        (event["revision_id"],),
    ).fetchone()


def _latest_revision_id(conn, profile_key: str) -> str | None:
    row = conn.execute(
        "SELECT revision_id FROM strategy_profile_revisions WHERE profile_key=? ORDER BY revision_no DESC LIMIT 1",
        (profile_key,),
    ).fetchone()
    return str(row["revision_id"]) if row else None


def _decode_revision(row: dict[str, Any], *, active_scopes: list[str] | None = None) -> dict[str, Any]:
    row["parameters"] = json.loads(row.pop("parameters_json", "{}") or "{}")
    row["strategy_names"] = json.loads(row.pop("strategy_names_json", "[]") or "[]")
    row["validation_report"] = json.loads(row.pop("validation_report_json", "{}") or "{}")
    row["active_scopes"] = sorted(active_scopes or [])
    return row


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _migrate_legacy_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(parameters)
    decision = migrated.get("decision_policy")
    if isinstance(decision, dict) and "min_trade_edge" in decision:
        legacy_edge = float(decision.pop("min_trade_edge"))
        decision.setdefault("min_paper_trade_edge", legacy_edge)
        decision.setdefault("min_live_trade_edge", max(0.08, legacy_edge))
    sizing = migrated.get("sizing")
    if isinstance(sizing, dict):
        if "kelly_multiplier" in sizing:
            legacy_kelly = float(sizing.pop("kelly_multiplier"))
            sizing.setdefault("paper_kelly_multiplier", legacy_kelly)
            sizing.setdefault("live_kelly_multiplier", min(0.15, legacy_kelly))
        if "max_bankroll_fraction_per_trade" in sizing:
            legacy_cap = float(sizing.pop("max_bankroll_fraction_per_trade"))
            sizing.setdefault("max_paper_bankroll_fraction_per_trade", legacy_cap)
            sizing.setdefault("max_live_bankroll_fraction_per_trade", min(0.05, legacy_cap))
    strategies = migrated.get("strategies")
    if not isinstance(strategies, dict):
        return migrated
    core_modal = strategies.get("core_modal_v1")
    if isinstance(core_modal, dict):
        _split_mode_value(
            core_modal,
            legacy_key="min_effective_edge",
            paper_key="min_paper_effective_edge",
            live_key="min_live_effective_edge",
            live_floor=0.08,
        )
        _split_mode_value(
            core_modal,
            legacy_key="min_market_ask",
            paper_key="min_paper_market_ask",
            live_key="min_live_market_ask",
            live_floor=0.10,
        )
        _split_legacy_threshold(
            core_modal,
            legacy_key="min_settlement_days",
            paper_key="min_paper_settlement_days",
            live_key="min_live_settlement_days",
        )
        _split_legacy_threshold(
            core_modal,
            legacy_key="min_component_calibration_days",
            paper_key="min_paper_component_calibration_days",
            live_key="min_live_component_calibration_days",
        )
    tail_buying = strategies.get("tail_buying")
    if isinstance(tail_buying, dict) and "min_settlement_days" in tail_buying:
        tail_buying.setdefault("min_live_settlement_days", tail_buying["min_settlement_days"])
        tail_buying.pop("min_settlement_days", None)
    return migrated


def _split_mode_value(
    parameters: dict[str, Any],
    *,
    legacy_key: str,
    paper_key: str,
    live_key: str,
    live_floor: float,
) -> None:
    if legacy_key not in parameters:
        return
    legacy_value = float(parameters.pop(legacy_key))
    parameters.setdefault(paper_key, legacy_value)
    parameters.setdefault(live_key, max(live_floor, legacy_value))


def _split_legacy_threshold(
    parameters: dict[str, Any],
    *,
    legacy_key: str,
    paper_key: str,
    live_key: str,
) -> None:
    if legacy_key not in parameters:
        return
    legacy_value = parameters.pop(legacy_key)
    try:
        legacy_days = int(legacy_value)
    except (TypeError, ValueError):
        legacy_days = legacy_value
    paper_default = 0 if legacy_days == 20 else legacy_days
    live_default = max(20, legacy_days) if isinstance(legacy_days, int) else legacy_days
    parameters.setdefault(paper_key, paper_default)
    parameters.setdefault(live_key, live_default)


def _bounded(container: dict[str, Any], key: str, low: float, high: float, *, integer: bool = False) -> None:
    try:
        value = int(container[key]) if integer else float(container[key])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"invalid_strategy_parameter:{key}") from None
    if value < low or value > high:
        raise ValueError(f"strategy_parameter_out_of_range:{key}")
    container[key] = value
