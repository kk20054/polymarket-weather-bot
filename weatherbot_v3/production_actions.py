from __future__ import annotations

from datetime import timezone, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from .cli import run_forecast_backfill, run_market_buckets_sync, run_orderbook_backfill, run_production_refresh, run_truth_backfill
from .db import bulk_settlement_contract_verification, log_data_fetch
from .forecast_archive import import_forecast_archive
from .hourly import build_hourly_consensus
from .metar import refresh_metar_reports
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
    "refresh_metar_reports": {
        "label": "Refresh METAR reports",
        "description": "Fetch recent AviationWeather METAR/SPECI reports for registry airport stations.",
        "requires_operator": False,
        "mutates": True,
    },
    "build_hourly_consensus": {
        "label": "Build hourly consensus",
        "description": "Join persisted forecasts, METAR, and mesonet observations into PolyWX-style hourly evidence rows.",
        "requires_operator": False,
        "mutates": True,
    },
    "sync_market_buckets": {
        "label": "Sync market buckets",
        "description": "Persist strict Polymarket city/date/bucket/token/tick/orderMinSize mappings from local market payloads.",
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
    "backfill_forecast_members": {
        "label": "Import historical forecast archive",
        "description": "Validate and import no-leak historical forecast/member archive records.",
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
    archive_path: str = "data/forecast_archive/historical_forecasts.jsonl",
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
        "archive_path": archive_path or "data/forecast_archive/historical_forecasts.jsonl",
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

    started_at = datetime.now(timezone.utc).isoformat()
    start = perf_counter()
    try:
        payload = _execute_action(action_key, params)
    except Exception as exc:
        finished_at = datetime.now(timezone.utc).isoformat()
        duration_ms = (perf_counter() - start) * 1000.0
        log_data_fetch(
            source=action_key,
            stage="production_action",
            status="ERR",
            duration_ms=duration_ms,
            city=",".join(clean_cities),
            target_date=start_date or end_date or "",
            message=str(exc),
            details={"params": params, "reason": exc.__class__.__name__},
            started_at=started_at,
            finished_at=finished_at,
        )
        return {
            "ok": False,
            "status": "failed",
            "reason": exc.__class__.__name__,
            "error": str(exc),
            "action_key": action_key,
            "action": action,
            "params": params,
        }
    finished_at = datetime.now(timezone.utc).isoformat()
    duration_ms = (perf_counter() - start) * 1000.0
    log_data_fetch(
        source=action_key,
        stage="production_action",
        status="OK" if bool(payload.get("ok", True)) else "WARN",
        duration_ms=duration_ms,
        city=",".join(clean_cities),
        target_date=start_date or end_date or "",
        message=str(payload.get("message") or payload.get("reason") or f"{action_key} executed"),
        details={"params": params, "payload": payload},
        started_at=started_at,
        finished_at=finished_at,
    )
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
    if action_key == "refresh_metar_reports":
        return refresh_metar_reports(
            cities=params["cities"],
            hours=max(1.0, min(float(params["days"] or 1) * 24.0, 96.0)),
        )
    if action_key == "build_hourly_consensus":
        return build_hourly_consensus(
            cities=params["cities"],
            target_date=params["start_date"] or None,
        )
    if action_key == "sync_market_buckets":
        return run_market_buckets_sync(params["limit"])
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
    if action_key == "backfill_forecast_members":
        archive_path = Path(str(params["archive_path"] or "data/forecast_archive/historical_forecasts.jsonl"))
        if not archive_path.exists():
            return {
                "ok": False,
                "reason": "forecast_archive_missing",
                "archive_path": str(archive_path),
                "hint": "Generate a template from the forecast archive manifest, fill real historical no-leak forecast records, then run this action again.",
            }
        return import_forecast_archive(archive_path, apply=True)
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
