from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import ASIAN_CITY_PRIORITY
from .db import connect, init_v3_db, list_signal_decisions, utc_now
from .paper import PAPER_EXECUTION_VERSION, execute_paper_decision_record, refresh_paper_decision_quote
from .sizing import size_for_cohort
from .stations import enabled_station_rows
from .strategy_profiles import (
    ensure_default_strategy_profile,
    get_strategy_profile_revision,
    profile_snapshot,
)


PAPER_VALIDATION_VERSION = "paper-validation-v2"
_PAPER_VALIDATION_EXECUTION_LOCK = threading.RLock()


def start_paper_validation_run(
    *,
    duration_days: int = 14,
    bankroll_usd: float = 40.0,
    max_per_trade_usd: float = 2.0,
    daily_max_usd: float = 10.0,
    max_open_positions: int = 5,
    max_orders_per_day: int = 5,
    decision_max_age_minutes: float = 30.0,
    cities: list[str] | None = None,
    strategies: list[str] | None = None,
    strategy_revision_id: str = "",
    notes: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    init_v3_db(path)
    active = get_active_paper_validation_run(path=path)
    if active:
        return {"ok": False, "status": "blocked", "reason": "paper_validation_run_already_active", "run": active}
    now = datetime.now(timezone.utc)
    clean_cities = _default_cities(path=path) if cities is None else _unique(cities)
    clean_strategies = _unique(strategies or ["single_bucket_ev"])
    profile = (
        get_strategy_profile_revision(strategy_revision_id, path=path)
        if strategy_revision_id
        else ensure_default_strategy_profile("paper_default", path=path)
    )
    if not profile:
        return {"ok": False, "status": "blocked", "reason": "strategy_profile_revision_not_found"}
    profile_parameters = profile["parameters"]
    enabled_strategies = {
        name for name, parameters in profile_parameters["strategies"].items()
        if parameters.get("enabled", True)
    }
    unsupported = [name for name in clean_strategies if name not in enabled_strategies]
    if unsupported:
        return {"ok": False, "status": "blocked", "reason": "strategy_disabled_in_profile", "strategies": unsupported}
    sizing_policy = profile_parameters["sizing"]
    run = {
        "run_id": f"paper-{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}",
        "status": "active",
        "started_at": now.isoformat(),
        "ends_at": (now + timedelta(days=max(1, min(int(duration_days), 30)))).isoformat(),
        "stopped_at": "",
        "bankroll_usd": max(1.0, float(bankroll_usd)),
        "max_per_trade_usd": max(0.1, float(max_per_trade_usd)),
        "daily_max_usd": max(0.1, float(daily_max_usd)),
        "max_open_positions": max(1, int(max_open_positions)),
        "max_orders_per_day": max(1, int(max_orders_per_day)),
        "decision_max_age_minutes": max(1.0, float(decision_max_age_minutes)),
        "cities": clean_cities,
        "strategies": clean_strategies,
        "strategy_revision_id": profile["revision_id"],
        "strategy_profile_snapshot": profile_snapshot(profile),
        "kelly_multiplier": float(sizing_policy["kelly_multiplier"]),
        "bankroll_fraction_cap": float(sizing_policy["max_bankroll_fraction_per_trade"]),
        "execution_version": PAPER_EXECUTION_VERSION,
        "notes": str(notes or ""),
        "version": PAPER_VALIDATION_VERSION,
    }
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO paper_validation_runs (
                run_id, status, started_at, ends_at, stopped_at, bankroll_usd,
                max_per_trade_usd, daily_max_usd, max_open_positions,
                max_orders_per_day, decision_max_age_minutes, cities_json,
                strategies_json, execution_version, notes, raw_json, created_at,
                updated_at, strategy_revision_id, strategy_profile_snapshot_json,
                kelly_multiplier, bankroll_fraction_cap
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run["run_id"], run["status"], run["started_at"], run["ends_at"], "",
                run["bankroll_usd"], run["max_per_trade_usd"], run["daily_max_usd"],
                run["max_open_positions"], run["max_orders_per_day"],
                run["decision_max_age_minutes"], json.dumps(clean_cities),
                json.dumps(clean_strategies), run["execution_version"], run["notes"],
                json.dumps(run, ensure_ascii=False, sort_keys=True), run["started_at"],
                run["started_at"], run["strategy_revision_id"],
                json.dumps(run["strategy_profile_snapshot"], ensure_ascii=False, sort_keys=True),
                run["kelly_multiplier"], run["bankroll_fraction_cap"],
            ),
        )
    return {"ok": True, "status": "active", "run": paper_validation_status(run_id=run["run_id"], path=path)}


def stop_paper_validation_run(*, run_id: str = "", path: Path | None = None) -> dict[str, Any]:
    init_v3_db(path)
    run = _load_run(run_id, path=path) if run_id else get_active_paper_validation_run(path=path)
    if not run:
        return {"ok": True, "status": "stopped", "reason": "no_active_paper_validation_run"}
    now = utc_now()
    with connect(path) as conn:
        conn.execute(
            "UPDATE paper_validation_runs SET status='stopped', stopped_at=?, updated_at=? WHERE run_id=?",
            (now, now, run["run_id"]),
        )
    return {"ok": True, "status": "stopped", "run": paper_validation_status(run_id=run["run_id"], path=path)}


def get_active_paper_validation_run(*, path: Path | None = None) -> dict[str, Any] | None:
    init_v3_db(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM paper_validation_runs WHERE status='active' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    run = _decode_run(dict(row))
    if _parse_time(run["ends_at"]) <= datetime.now(timezone.utc):
        _complete_expired_run(run["run_id"], path=path)
        return None
    return run


def paper_validation_status(*, run_id: str = "", path: Path | None = None) -> dict[str, Any]:
    init_v3_db(path)
    run = _load_run(run_id, path=path) if run_id else get_active_paper_validation_run(path=path)
    if not run:
        return {"ok": True, "status": "inactive", "version": PAPER_VALIDATION_VERSION}
    metrics = _run_metrics(run, path=path)
    return {"ok": True, **run, **metrics}


def run_paper_validation_tick(
    *,
    apply: bool = True,
    run_id: str = "",
    decision_id: str = "",
    city_key: str = "",
    target_date: str = "",
    strategies: list[str] | None = None,
    strategy_revision_id: str = "",
    decision_batch_issued_at: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    with _PAPER_VALIDATION_EXECUTION_LOCK:
        return _run_paper_validation_tick_locked(
            apply=apply,
            run_id=run_id,
            decision_id=decision_id,
            city_key=city_key,
            target_date=target_date,
            strategies=strategies,
            strategy_revision_id=strategy_revision_id,
            decision_batch_issued_at=decision_batch_issued_at,
            path=path,
        )


def _run_paper_validation_tick_locked(
    *,
    apply: bool,
    run_id: str,
    decision_id: str,
    city_key: str,
    target_date: str,
    strategies: list[str] | None,
    strategy_revision_id: str,
    decision_batch_issued_at: str,
    path: Path | None,
) -> dict[str, Any]:
    active = get_active_paper_validation_run(path=path)
    if run_id:
        requested = _load_run(run_id, path=path)
        if not requested or requested.get("status") != "active" or not active or active.get("run_id") != run_id:
            return {
                "ok": False,
                "status": "blocked",
                "reason": "paper_validation_run_not_active",
                "run_id": run_id,
            }
        run = requested
    else:
        run = active
    if not run:
        return {"ok": True, "status": "inactive", "skipped": True, "reason": "no_active_paper_validation_run"}
    if strategy_revision_id and str(run.get("strategy_revision_id") or "") != str(strategy_revision_id):
        return {
            "ok": False,
            "status": "blocked",
            "reason": "strategy_revision_mismatch",
            "run_id": run["run_id"],
        }
    metrics = _run_metrics(run, path=path)
    remaining_open = max(0, int(run["max_open_positions"]) - int(metrics["open_positions"]))
    remaining_orders = max(0, int(run["max_orders_per_day"]) - int(metrics["orders_today"]))
    remaining_daily = max(0.0, float(run["daily_max_usd"]) - float(metrics["spent_today_usd"]))
    cash_available = max(0.0, float(metrics["cash_available_usd"]))
    capacity = min(remaining_open, remaining_orders)
    if capacity <= 0 or remaining_daily < 0.01 or cash_available < 0.01:
        return {
            "ok": True,
            "status": "capacity_reached",
            "run_id": run["run_id"],
            "executed": 0,
            "metrics": metrics,
        }

    candidates = _fresh_candidates(
        run,
        decision_id=decision_id,
        city_key=city_key,
        target_date=target_date,
        strategies=strategies,
        strategy_revision_id=strategy_revision_id,
        decision_batch_issued_at=decision_batch_issued_at,
        path=path,
    )
    results: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    profile_parameters = (run.get("strategy_profile_snapshot") or {}).get("parameters", {})
    decision_policy = profile_parameters.get("decision_policy", {})
    strategy_parameters = profile_parameters.get("strategies", {})
    max_book_age_seconds = _number_or_default(decision_policy.get("stale_book_seconds"), 300.0)
    max_spread_bps = _number_or_default(decision_policy.get("max_spread_bps"), 500.0)
    for decision in candidates:
        group_rows = _candidate_group_rows(decision, path=path)
        leg_count = len(group_rows) if decision.get("ladder_group_id") else 1
        if leg_count <= 0 or leg_count > remaining_open or leg_count > remaining_orders:
            continue
        if remaining_daily < 0.01 or cash_available < 0.01:
            break
        fresh_group_rows = [refresh_paper_decision_quote(row, path=path) for row in (group_rows or [decision])]
        fresh_reasons = _fresh_quote_gate_reasons(
            fresh_group_rows,
            decision_policy=decision_policy,
            strategy_parameters=strategy_parameters,
        )
        if fresh_reasons:
            skipped.append({
                "decision_id": decision.get("decision_id"),
                "ladder_group_id": decision.get("ladder_group_id") or "",
                "reason": fresh_reasons[0],
                "reasons": fresh_reasons,
            })
            continue
        decision = next(
            (row for row in fresh_group_rows if row.get("decision_id") == decision.get("decision_id")),
            fresh_group_rows[0],
        )
        group_rows = fresh_group_rows
        sizing_decision = max(
            group_rows or [decision],
            key=lambda row: float(row.get("model_probability") or 0.0),
        )
        strategy_config = strategy_parameters.get(str(decision.get("strategy_name") or "single_bucket_ev"), {})
        strategy_cap = strategy_config.get("max_order_usd") if decision.get("strategy_name") == "tail_buying" else None
        exposure_multiplier = (
            float(strategy_config.get("group_exposure_multiplier", 0.60))
            if decision.get("ladder_group_id") else 1.0
        )
        sizing = size_for_cohort(
            sizing_decision.get("model_probability"),
            sizing_decision.get("market_ask"),
            bankroll=cash_available,
            max_per_trade_usd=float(run["max_per_trade_usd"]),
            kelly_multiplier=_number_or_default(run.get("kelly_multiplier"), 0.15),
            bankroll_fraction_cap=_number_or_default(run.get("bankroll_fraction_cap"), 0.05),
            strategy_cap_usd=strategy_cap,
            remaining_daily_usd=remaining_daily,
            cash_available_usd=cash_available,
            exposure_multiplier=exposure_multiplier,
        )
        amount = sizing.capped_position_size_usd
        if amount < 0.01:
            continue
        sizing_snapshot = {
            **sizing.snapshot(),
            "cohort_run_id": run["run_id"],
            "strategy_name": decision.get("strategy_name"),
            "strategy_revision_id": run.get("strategy_revision_id"),
            "ladder_leg_count": leg_count,
        }
        preflight = execute_paper_decision_record(
            decision,
            amount=amount,
            dry_run=True,
            path=path,
            cohort_run_id=run["run_id"],
            max_per_trade_usd=float(run["max_per_trade_usd"]),
            sizing_snapshot=sizing_snapshot,
            max_book_age_seconds=max_book_age_seconds,
            max_spread_bps=max_spread_bps,
        )
        if not preflight.get("ok"):
            skipped.append({
                "decision_id": decision.get("decision_id"),
                "ladder_group_id": decision.get("ladder_group_id") or "",
                "reason": preflight.get("reason") or "paper_preflight_failed",
                "reasons": preflight.get("risk_reasons") or [preflight.get("reason") or "paper_preflight_failed"],
            })
            continue
        result = preflight
        if apply:
            result = execute_paper_decision_record(
                decision,
                amount=amount,
                dry_run=False,
                path=path,
                cohort_run_id=run["run_id"],
                max_per_trade_usd=float(run["max_per_trade_usd"]),
                sizing_snapshot=sizing_snapshot,
                max_book_age_seconds=max_book_age_seconds,
                max_spread_bps=max_spread_bps,
            )
        results.append(result)
        if result.get("ok") and result.get("status") != "duplicate":
            filled = _result_filled_amount(result, amount)
            remaining_daily = max(0.0, remaining_daily - filled)
            cash_available = max(0.0, cash_available - filled)
            remaining_open = max(0, remaining_open - leg_count)
            remaining_orders = max(0, remaining_orders - leg_count)
    return {
        "ok": all(row.get("ok") or row.get("status") == "duplicate" for row in results),
        "status": ("executed" if apply else "dry_run") if results else ("no_executable_candidates" if skipped else "no_fresh_candidates"),
        "reason": skipped[0]["reason"] if skipped and not results else None,
        "run_id": run["run_id"],
        "apply": apply,
        "candidate_count": len(candidates),
        "executed": sum(1 for row in results if row.get("ok") and row.get("status") != "duplicate"),
        "duplicates": sum(1 for row in results if row.get("status") == "duplicate"),
        "results": results,
        "skipped_candidates": skipped,
        "metrics": _run_metrics(run, path=path) if apply else metrics,
    }


def _fresh_candidates(
    run: dict[str, Any],
    *,
    decision_id: str = "",
    city_key: str = "",
    target_date: str = "",
    strategies: list[str] | None = None,
    strategy_revision_id: str = "",
    decision_batch_issued_at: str = "",
    path: Path | None,
) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=float(run["decision_max_age_minutes"]))
    allowed_cities = set(run.get("cities") or [])
    allowed_strategies = set(run.get("strategies") or [])
    if strategies:
        allowed_strategies &= set(strategies)
    rows = list_signal_decisions(limit=2000, path=path)
    with connect(path) as conn:
        existing_rows = conn.execute(
            "SELECT decision_id, yes_token_id FROM paper_orders WHERE cohort_run_id=?",
            (run["run_id"],),
        ).fetchall()
    existing_decisions = {str(row["decision_id"] or "") for row in existing_rows}
    existing_tokens = {str(row["yes_token_id"] or "") for row in existing_rows}
    rows.sort(key=lambda row: str(row.get("issued_at") or row.get("updated_at") or ""), reverse=True)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if decision_id and str(row.get("decision_id") or "") != str(decision_id):
            continue
        if city_key and str(row.get("city_key") or "") != str(city_key):
            continue
        if target_date and str(row.get("target_date") or "") != str(target_date):
            continue
        if decision_batch_issued_at and str(row.get("issued_at") or "") != str(decision_batch_issued_at):
            continue
        issued = _parse_time(str(row.get("issued_at") or row.get("updated_at") or ""))
        if issued < cutoff:
            continue
        city = str(row.get("city_key") or "")
        strategy = str(row.get("strategy_name") or "single_bucket_ev")
        if city not in allowed_cities or strategy not in allowed_strategies:
            continue
        expected_revision = strategy_revision_id or str(run.get("strategy_revision_id") or "")
        if str(row.get("strategy_revision_id") or "") != expected_revision:
            continue
        if not bool(row.get("paper_allowed")) or str(row.get("paper_decision") or "") != "buy":
            continue
        if str(row.get("decision_id") or "") in existing_decisions:
            continue
        if str(row.get("yes_token_id") or "") in existing_tokens:
            continue
        key = str(row.get("ladder_group_id") or row.get("yes_token_id") or row.get("decision_id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        selected.append(row)
    selected.sort(key=lambda row: (-float(row.get("edge") or 0.0), str(row.get("issued_at") or "")))
    return selected


def _fresh_quote_gate_reasons(
    rows: list[dict[str, Any]],
    *,
    decision_policy: dict[str, Any],
    strategy_parameters: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    max_spread_bps = _number_or_default(decision_policy.get("max_spread_bps"), 500.0)
    stale_book_seconds = _number_or_default(decision_policy.get("stale_book_seconds"), 300.0)
    for row in rows:
        probability = _optional_number(row.get("model_probability"))
        ask = _optional_number(row.get("market_ask"))
        bid = _optional_number(row.get("market_bid"))
        if probability is None:
            reasons.append("model_probability_missing")
            continue
        if ask is None or ask <= 0 or ask >= 1:
            reasons.append("best_ask_missing_or_invalid")
            continue
        strategy_name = str(row.get("strategy_name") or "single_bucket_ev")
        strategy_config = strategy_parameters.get(strategy_name, {})
        edge = probability - ask
        row["market_implied_probability"] = ask
        row["edge"] = edge
        min_edge = _number_or_default(strategy_config.get("min_edge"), 0.0)
        if edge + 1e-12 < min_edge:
            reasons.append("edge_below_min_after_reprice")
        if strategy_name == "tail_buying" and ask > _number_or_default(strategy_config.get("max_ask"), 0.15) + 1e-12:
            reasons.append("tail_ask_above_max_after_reprice")
        snapshot = row.get("orderbook_snapshot") or {}
        spread = _optional_number(snapshot.get("spread"))
        if spread is None and bid is not None:
            spread = max(0.0, ask - bid)
        spread_bps = spread / ask * 10_000.0 if spread is not None else None
        row["spread_bps"] = spread_bps
        if spread_bps is None:
            reasons.append("spread_missing_after_reprice")
        elif spread_bps > max_spread_bps + 1e-9:
            reasons.append("spread_too_wide_after_reprice")
        age_seconds = _optional_number((row.get("execution_quote") or {}).get("age_seconds"))
        if age_seconds is None:
            reasons.append("orderbook_timestamp_missing_or_invalid")
        elif age_seconds > stale_book_seconds:
            reasons.append("orderbook_stale")
    return _unique_strings(reasons)


def _optional_number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _number_or_default(value: Any, default: float) -> float:
    number = _optional_number(value)
    return default if number is None else number


def _unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value))


def _run_metrics(run: dict[str, Any], *, path: Path | None) -> dict[str, Any]:
    today = datetime.now(timezone.utc).date().isoformat()
    with connect(path) as conn:
        orders = conn.execute(
            "SELECT * FROM paper_orders WHERE cohort_run_id=? ORDER BY id",
            (run["run_id"],),
        ).fetchall()
        settlements = conn.execute(
            """
            SELECT s.* FROM settlements s
            JOIN paper_orders p ON p.id=s.paper_order_id
            WHERE p.cohort_run_id=? AND s.settlement_status='resolved'
            """,
            (run["run_id"],),
        ).fetchall()
    order_rows = [dict(row) for row in orders]
    settlement_rows = [dict(row) for row in settlements]
    open_rows = [row for row in order_rows if str(row.get("lifecycle_status") or "") == "open"]
    exited_rows = [row for row in order_rows if str(row.get("lifecycle_status") or "") == "exited"]
    today_rows = [row for row in order_rows if str(row.get("opened_at") or "")[:10] == today]
    realized = sum(float(row.get("pnl") or 0.0) for row in settlement_rows)
    realized += sum(float(row.get("realized_pnl") or 0.0) for row in exited_rows)
    open_cost = sum(float(row.get("filled_amount") or 0.0) for row in open_rows)
    brier = [float(row["brier_score"]) for row in settlement_rows if row.get("brier_score") is not None]
    wins = sum(1 for row in settlement_rows if row.get("result") == "win")
    return {
        "orders_total": len(order_rows),
        "orders_today": len(today_rows),
        "open_positions": len(open_rows),
        "resolved_orders": len(settlement_rows),
        "exited_orders": len(exited_rows),
        "wins": wins,
        "losses": len(settlement_rows) - wins,
        "win_rate": wins / len(settlement_rows) if settlement_rows else None,
        "realized_pnl": round(realized, 4),
        "brier_score": sum(brier) / len(brier) if brier else None,
        "spent_today_usd": round(sum(float(row.get("filled_amount") or 0.0) for row in today_rows), 4),
        "open_cost_usd": round(open_cost, 4),
        "cash_available_usd": round(float(run["bankroll_usd"]) + realized - open_cost, 4),
    }


def _load_run(run_id: str, *, path: Path | None) -> dict[str, Any] | None:
    if not run_id:
        return None
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM paper_validation_runs WHERE run_id=?", (run_id,)).fetchone()
    return _decode_run(dict(row)) if row else None


def _decode_run(row: dict[str, Any]) -> dict[str, Any]:
    row["cities"] = _json_list(row.pop("cities_json", "[]"))
    row["strategies"] = _json_list(row.pop("strategies_json", "[]"))
    row["raw"] = _json_obj(row.pop("raw_json", "{}"))
    row["strategy_profile_snapshot"] = _json_obj(row.pop("strategy_profile_snapshot_json", "{}"))
    return row


def _candidate_group_rows(decision: dict[str, Any], *, path: Path | None) -> list[dict[str, Any]]:
    group_id = str(decision.get("ladder_group_id") or "")
    if not group_id:
        return [decision]
    return [
        row for row in list_signal_decisions(
            city_key=str(decision.get("city_key") or ""),
            target_date=str(decision.get("target_date") or ""),
            limit=1000,
            path=path,
        )
        if str(row.get("ladder_group_id") or "") == group_id
        and str(row.get("strategy_revision_id") or "") == str(decision.get("strategy_revision_id") or "")
    ]


def _result_filled_amount(result: dict[str, Any], fallback: float) -> float:
    if result.get("results"):
        return sum(
            float((item.get("order") or {}).get("filled_amount") or 0.0)
            for item in result["results"]
        )
    return float((result.get("order") or {}).get("filled_amount") or fallback)


def _default_cities(*, path: Path | None = None) -> list[str]:
    cities = []
    for row in enabled_station_rows(path=path):
        city = str(row.get("city_key") or row.get("city") or "")
        if not city or ASIAN_CITY_PRIORITY.get(city, {}).get("mode") == "monitor_only":
            continue
        cities.append(city)
    return _unique(cities)


def _complete_expired_run(run_id: str, *, path: Path | None) -> None:
    now = utc_now()
    with connect(path) as conn:
        conn.execute(
            "UPDATE paper_validation_runs SET status='completed', stopped_at=?, updated_at=? WHERE run_id=? AND status='active'",
            (now, now, run_id),
        )


def _parse_time(value: str) -> datetime:
    text = str(value or "").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    except Exception:
        return []


def _json_obj(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
