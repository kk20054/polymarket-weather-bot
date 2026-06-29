from __future__ import annotations

from typing import Any

from .cli import run_forecast_backfill, run_orderbook_backfill, run_production_refresh, run_truth_backfill
from .db import bulk_settlement_contract_verification
from .qualification import build_data_readiness, persist_data_readiness


ACTION_CATALOG: dict[str, dict[str, Any]] = {
    "refresh_forecast_runs": {
        "label": "Refresh forecast runs",
        "description": "Fetch current forecast snapshots for configured cities.",
        "requires_operator": False,
        "mutates": True,
    },
    "refresh_clob_orderbooks": {
        "label": "Refresh CLOB orderbooks",
        "description": "Fetch current bid/ask depth for candidate signal markets.",
        "requires_operator": False,
        "mutates": True,
    },
    "production_refresh": {
        "label": "Run production refresh",
        "description": "Run contracts sync, forecast refresh, optional signal migration, and orderbook refresh.",
        "requires_operator": False,
        "mutates": True,
    },
    "review_mature_auto_contracts": {
        "label": "Review mature auto contracts",
        "description": "Bulk-verify mature contracts that the parser marked as auto-verifiable.",
        "requires_operator": True,
        "mutates": True,
    },
    "backfill_official_truth": {
        "label": "Backfill official settlement truth",
        "description": "Fetch station-level actual temperatures for matured settlement contracts.",
        "requires_operator": False,
        "mutates": True,
    },
    "review_auto_verified_contracts": {
        "label": "Review auto verified contracts",
        "description": "Bulk-verify auto-verifiable contracts from the model dataset audit queue.",
        "requires_operator": True,
        "mutates": True,
    },
}


def list_production_actions() -> list[dict[str, Any]]:
    return [
        {"key": key, **value}
        for key, value in ACTION_CATALOG.items()
    ]


def run_production_action(
    action_key: str,
    *,
    apply: bool = False,
    operator_confirmed: bool = False,
    cities: list[str] | None = None,
    days: int = 1,
    limit: int = 20,
    start_date: str = "",
    end_date: str = "",
    skip_signal_scan: bool = True,
    note: str = "",
) -> dict[str, Any]:
    action = ACTION_CATALOG.get(action_key)
    if not action:
        return {
            "ok": False,
            "status": "blocked",
            "reason": "unsupported_production_action",
            "action_key": action_key,
            "supported_actions": sorted(ACTION_CATALOG),
        }

    bounded_days = max(1, min(int(days or 1), 7))
    bounded_limit = max(1, min(int(limit or 20), 500))
    clean_cities = [item.strip() for item in (cities or []) if item and item.strip()]
    params = {
        "cities": clean_cities,
        "days": bounded_days,
        "limit": bounded_limit,
        "start_date": start_date or "",
        "end_date": end_date or "",
        "skip_signal_scan": bool(skip_signal_scan),
        "note": note or "",
    }

    if not apply:
        return {
            "ok": True,
            "status": "dry_run",
            "action_key": action_key,
            "action": action,
            "params": params,
            "message": "Set apply=true to execute this whitelisted production action.",
        }

    if action.get("requires_operator") and not operator_confirmed:
        return {
            "ok": False,
            "status": "blocked",
            "reason": "operator_confirmation_required",
            "action_key": action_key,
            "action": action,
            "params": params,
        }

    payload = _execute_action(action_key, params)
    readiness = build_data_readiness()
    persist_data_readiness(readiness)
    return {
        "ok": bool(payload.get("ok", True)),
        "status": "executed",
        "action_key": action_key,
        "action": action,
        "params": params,
        "payload": payload,
        "readiness": {
            "status": readiness.get("status"),
            "score": readiness.get("score"),
            "live_allowed": readiness.get("live_allowed"),
            "blocked_keys": (readiness.get("production_phase") or {}).get("blocked_keys", []),
        },
    }


def _execute_action(action_key: str, params: dict[str, Any]) -> dict[str, Any]:
    cities_arg = ",".join(params["cities"])
    if action_key == "refresh_forecast_runs":
        return run_forecast_backfill(cities_arg, params["days"])
    if action_key == "refresh_clob_orderbooks":
        return run_orderbook_backfill(params["limit"], params["start_date"], params["end_date"])
    if action_key == "production_refresh":
        return run_production_refresh(
            cities=cities_arg,
            days=params["days"],
            limit=params["limit"],
            start_date=params["start_date"],
            end_date=params["end_date"],
            scan_signals=not params["skip_signal_scan"],
        )
    if action_key == "backfill_official_truth":
        return run_truth_backfill(cities_arg, params["limit"], params["start_date"], params["end_date"])
    if action_key in {"review_mature_auto_contracts", "review_auto_verified_contracts"}:
        return bulk_settlement_contract_verification(
            contract_ids=None,
            limit=params["limit"],
            reviewer="dashboard",
            note=params["note"] or f"{action_key} from production action",
            require_auto_verified=True,
            mature_only=action_key == "review_mature_auto_contracts",
            apply=True,
        )
    return {
        "ok": False,
        "reason": "unsupported_production_action",
        "action_key": action_key,
    }
