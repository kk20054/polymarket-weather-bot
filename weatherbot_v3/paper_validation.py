from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import ASIAN_CITY_PRIORITY
from .db import connect, init_v3_db, list_signal_decisions, utc_now
from .paper import PAPER_EXECUTION_VERSION, execute_paper_decision_record
from .stations import enabled_station_rows


PAPER_VALIDATION_VERSION = "paper-validation-v1"


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
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run["run_id"], run["status"], run["started_at"], run["ends_at"], "",
                run["bankroll_usd"], run["max_per_trade_usd"], run["daily_max_usd"],
                run["max_open_positions"], run["max_orders_per_day"],
                run["decision_max_age_minutes"], json.dumps(clean_cities),
                json.dumps(clean_strategies), run["execution_version"], run["notes"],
                json.dumps(run, ensure_ascii=False, sort_keys=True), run["started_at"],
                run["started_at"],
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


def run_paper_validation_tick(*, apply: bool = True, path: Path | None = None) -> dict[str, Any]:
    run = get_active_paper_validation_run(path=path)
    if not run:
        return {"ok": True, "status": "inactive", "skipped": True, "reason": "no_active_paper_validation_run"}
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

    candidates = _fresh_candidates(run, path=path)
    results: list[dict[str, Any]] = []
    for decision in candidates:
        if len(results) >= capacity or remaining_daily < 0.01 or cash_available < 0.01:
            break
        amount = min(
            float(run["max_per_trade_usd"]),
            float(decision.get("position_size_usd") or run["max_per_trade_usd"]),
            remaining_daily,
            cash_available,
        )
        preflight = execute_paper_decision_record(
            decision,
            amount=amount,
            dry_run=True,
            path=path,
            cohort_run_id=run["run_id"],
        )
        if not preflight.get("ok"):
            continue
        result = preflight
        if apply:
            result = execute_paper_decision_record(
                decision,
                amount=amount,
                dry_run=False,
                path=path,
                cohort_run_id=run["run_id"],
            )
        results.append(result)
        if result.get("ok") and result.get("status") != "duplicate":
            filled = float((result.get("order") or {}).get("filled_amount") or amount)
            remaining_daily = max(0.0, remaining_daily - filled)
            cash_available = max(0.0, cash_available - filled)
    return {
        "ok": all(row.get("ok") or row.get("status") == "duplicate" for row in results),
        "status": "executed" if results else "no_fresh_candidates",
        "run_id": run["run_id"],
        "apply": apply,
        "candidate_count": len(candidates),
        "executed": sum(1 for row in results if row.get("ok") and row.get("status") != "duplicate"),
        "duplicates": sum(1 for row in results if row.get("status") == "duplicate"),
        "results": results,
        "metrics": _run_metrics(run, path=path) if apply else metrics,
    }


def _fresh_candidates(run: dict[str, Any], *, path: Path | None) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=float(run["decision_max_age_minutes"]))
    allowed_cities = set(run.get("cities") or [])
    allowed_strategies = set(run.get("strategies") or [])
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
        issued = _parse_time(str(row.get("issued_at") or row.get("updated_at") or ""))
        if issued < cutoff or issued < _parse_time(run["started_at"]):
            continue
        city = str(row.get("city_key") or "")
        strategy = str(row.get("strategy_name") or "single_bucket_ev")
        if city not in allowed_cities or strategy not in allowed_strategies:
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
    today_rows = [row for row in order_rows if str(row.get("opened_at") or "")[:10] == today]
    realized = sum(float(row.get("pnl") or 0.0) for row in settlement_rows)
    open_cost = sum(float(row.get("filled_amount") or 0.0) for row in open_rows)
    brier = [float(row["brier_score"]) for row in settlement_rows if row.get("brier_score") is not None]
    wins = sum(1 for row in settlement_rows if row.get("result") == "win")
    return {
        "orders_total": len(order_rows),
        "orders_today": len(today_rows),
        "open_positions": len(open_rows),
        "resolved_orders": len(settlement_rows),
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
    return row


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
