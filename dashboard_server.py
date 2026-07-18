#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local dashboard server for WeatherBot."""

import json
import asyncio
import math
import os
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from dashboard_db import (
    DB_PATH,
    init_db,
    list_events,
    list_events_after,
    list_signals,
    log_event,
    reset_signal_marks,
    update_signal_status,
    upsert_signal_from_market,
)
from weatherbot_v3.config import load_config as load_v3_config
from weatherbot_v3.api_settings import list_api_settings, test_api_setting, update_api_setting
from weatherbot_v3.db import connect
from weatherbot_v3.db import dashboard_summary as v3_dashboard_summary
from weatherbot_v3.db import forecast_summary
from weatherbot_v3.db import init_v3_db
from weatherbot_v3.db import insert_event_distribution, latest_event_distribution, latest_signal_decision
from weatherbot_v3.db import list_signal_decisions
from weatherbot_v3.db import market_bucket_summary
from weatherbot_v3.db import model_reprice_event_summary, truth_delta_audit_summary
from weatherbot_v3.db import daily_max_prediction_summary
from weatherbot_v3.db import list_data_fetch_logs
from weatherbot_v3.db import weather_evidence_summary
from weatherbot_v3.db import bulk_settlement_contract_verification, list_settlement_contracts, set_settlement_contract_verification, truth_coverage_summary, upsert_market_rules, upsert_settlement_contracts, upsert_signal_decision, upsert_truth_observation
from weatherbot_v3.deb import build_daily_max_predictions, latest_bucket_probabilities
from weatherbot_v3.distribution import build_event_distribution
from weatherbot_v3.executor import LIVE_EXECUTION_PRODUCTION_READY, LiveExecutor
from weatherbot_v3.forecast_archive import build_forecast_archive_manifest
from weatherbot_v3.history import fetch_open_meteo_history, load_history_cache, market_history_points, merge_history_points
from weatherbot_v3.hourly import forecast_hourly_points, forecast_revision_history, hourly_consensus_points, hourly_consensus_summary
from weatherbot_v3.migration import migrate_legacy_signals
from weatherbot_v3.model_dataset import build_model_dataset_audit
from weatherbot_v3.notifier import FeishuNotifier
from weatherbot_v3.paper import execute_paper_decision, execute_paper_decisions
from weatherbot_v3.paper_validation import (
    paper_validation_status,
    run_paper_validation_tick,
    start_paper_validation_run,
    stop_paper_validation_run,
)
from weatherbot_v3.polymarket import PolymarketDataClient
from weatherbot_v3.production_actions import list_production_actions, run_production_action
from weatherbot_v3.qualification import build_data_readiness, persist_data_readiness
from weatherbot_v3.registry import SETTLEMENT_REGISTRY
from weatherbot_v3.scheduler import get_scheduler
from weatherbot_v3.source_health import build_source_health_matrix
from weatherbot_v3.stations import list_stations, set_station_enabled, sync_station_registry
from weatherbot_v3.signals import build_signal_decisions, signal_decisions_summary
from weatherbot_v3.db import paper_execution_summary
from weatherbot_v3.truth import infer_settlement_rule, settlement_contract_from_rule
from weatherbot_v3.cli import run_market_buckets_sync, run_production_refresh
from weatherbot_v3.config import asian_city_priority_config
from weatherbot_v3.validation import build_production_validation_report
from weatherbot_v3.strategy_profiles import (
    ALLOWED_SCOPES,
    activate_strategy_profile,
    create_strategy_profile_revision,
    ensure_default_strategy_profile,
    list_strategy_profile_revisions,
)


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
MARKETS_DIR = DATA_DIR / "markets"
DASHBOARD_DIR = ROOT / "dashboard"
BOT_LOG_PATH = DATA_DIR / "weatherbet-dashboard.log"
BOT_PID_PATH = DATA_DIR / "weatherbet-dashboard.pid"
AUTO_SIMULATION_PATH = DATA_DIR / "auto-simulation.json"
PRODUCTION_REFRESH_PATH = DATA_DIR / "production-refresh.json"
bot_process: subprocess.Popen | None = None
dashboard_refresh_task: asyncio.Task | None = None
auto_refresh_task: asyncio.Task | None = None
bulk_simulation_lock = asyncio.Lock()
production_refresh_lock = asyncio.Lock()
_market_rule_sync_signature: tuple[tuple[str, float], ...] | None = None
# The legacy weatherbet.py scanner bypasses the v3 prediction-cohort,
# strategy, and execution audit chain. The production dashboard must never
# launch it, even when an obsolete environment variable is still present.
AUTO_START_SCANNER = False
AUTO_REFRESH_ENABLED = os.getenv("WEATHERBOT_AUTO_REFRESH", "false").strip().lower() not in {"0", "false", "no", "off"}
AUTO_REFRESH_INTERVAL_SECONDS = max(900, int(os.getenv("WEATHERBOT_AUTO_REFRESH_INTERVAL", "1800") or "1800"))
AUTO_REFRESH_INITIAL_DELAY_SECONDS = max(0, int(os.getenv("WEATHERBOT_AUTO_REFRESH_INITIAL_DELAY", "30") or "30"))
AUTO_REFRESH_DAYS = max(1, min(int(os.getenv("WEATHERBOT_AUTO_REFRESH_DAYS", "1") or "1"), 3))
AUTO_REFRESH_LIMIT = max(1, min(int(os.getenv("WEATHERBOT_AUTO_REFRESH_LIMIT", "20") or "20"), 100))
AUTO_REFRESH_CITIES = os.getenv("WEATHERBOT_AUTO_REFRESH_CITIES", "").strip()
AUTO_REFRESH_SIGNAL_SCAN = os.getenv("WEATHERBOT_AUTO_REFRESH_SIGNAL_SCAN", "false").strip().lower() not in {"0", "false", "no", "off"}
SCHEDULER_AUTO_START = os.getenv("WEATHERBOT_SCHEDULER", "false").strip().lower() not in {"0", "false", "no", "off"}
dashboard_payload_cache: dict | None = None
dashboard_payload_cache_at: datetime | None = None
DASHBOARD_CACHE_TTL_SECONDS = int(os.getenv("WEATHERBOT_DASHBOARD_CACHE_TTL", "20") or "20")
production_validation_cache: dict[tuple[bool], tuple[float, dict]] = {}
PRODUCTION_VALIDATION_CACHE_TTL_SECONDS = int(os.getenv("WEATHERBOT_PRODUCTION_VALIDATION_CACHE_TTL", "60") or "60")
DASHBOARD_AUTO_BUILD = os.getenv("WEATHERBOT_DASHBOARD_AUTO_BUILD", "false").strip().lower() not in {"0", "false", "no", "off"}
STARTUP_MAINTENANCE = os.getenv("WEATHERBOT_STARTUP_MAINTENANCE", "false").strip().lower() not in {"0", "false", "no", "off"}

app = FastAPI(title="WeatherBot Dashboard", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_origin_regex=r"http://(127\.0\.0\.1|localhost):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class StatusUpdate(BaseModel):
    status: str
    note: str | None = None
    amount: float | None = None


class WeatherHistoryBackfillRequest(BaseModel):
    days: int = 30
    cities: list[str] | None = None


class StationEnabledUpdate(BaseModel):
    enabled: bool
    tier: int | None = None


class SimulationReset(BaseModel):
    balance: float
    clear_marks: bool = False


class AutoSimulationUpdate(BaseModel):
    enabled: bool
    interval_seconds: int = 300


class ContractVerificationRequest(BaseModel):
    verified: bool = True
    reviewer: str = "local-operator"
    note: str | None = None


class BulkContractVerificationRequest(BaseModel):
    contract_ids: list[str] | None = None
    limit: int = 5
    reviewer: str = "dashboard"
    note: str | None = None
    mature_only: bool = False
    apply: bool = False


class ProductionRefreshRequest(BaseModel):
    cities: list[str] | None = None
    days: int = 1
    limit: int = 20
    start_date: str = ""
    end_date: str = ""
    skip_signal_scan: bool = True


class ProductionActionRequest(BaseModel):
    action_key: str
    apply: bool = False
    operator_confirmed: bool = False
    cities: list[str] | None = None
    days: int = 1
    limit: int = 20
    start_date: str = ""
    end_date: str = ""
    skip_signal_scan: bool = True
    note: str = ""
    archive_path: str = ""


class MarketBucketsSyncRequest(BaseModel):
    cities: list[str] | None = None
    days: int = 3
    target_date: str = ""
    limit: int = 200
    limit_cities: int = 5
    dry_run: bool = False
    fetch_orderbooks: bool = True


class PaperExecutionRequest(BaseModel):
    decision_id: str = ""
    city: str = ""
    target_date: str = ""
    amount: float | None = None
    strategies: list[str] | None = None
    strategy_revision_id: str = ""
    decision_batch_issued_at: str = ""
    cohort_run_id: str = ""
    limit: int = 20
    dry_run: bool = True


class PaperValidationStartRequest(BaseModel):
    bankroll_usd: float = Field(default=40.0, ge=1.0, le=1_000_000.0)
    duration_days: int = Field(default=14, ge=1, le=30)
    max_per_trade_usd: float = Field(default=2.0, ge=0.1, le=10_000.0)
    daily_max_usd: float = Field(default=10.0, ge=0.1, le=100_000.0)
    max_open_positions: int = Field(default=5, ge=1, le=1000)
    max_orders_per_day: int = Field(default=5, ge=1, le=1000)
    decision_max_age_minutes: float = Field(default=30.0, ge=1.0, le=1440.0)
    cities: list[str] | None = None
    strategies: list[str] | None = None
    strategy_revision_id: str = ""


class PaperValidationTickRequest(BaseModel):
    run_id: str = ""
    decision_id: str = ""
    city: str = ""
    target_date: str = ""
    strategies: list[str] | None = None
    strategy_revision_id: str = ""
    decision_batch_issued_at: str = ""
    apply: bool = True


class StrategyProfileCreateRequest(BaseModel):
    profile_key: str = "weatherbot_conservative"
    parameters: dict
    change_note: str = ""
    activate_scopes: list[str] = Field(default_factory=list)
    confirm: bool = False


class StrategyProfileActivateRequest(BaseModel):
    scope: str = "paper_default"
    reason: str = ""
    confirm: bool = False


class ApiSettingUpdateRequest(BaseModel):
    value: str = Field(default="", max_length=4096)
    clear: bool = False
    confirm: bool = False


class ApiSettingTestRequest(BaseModel):
    value: str = Field(default="", max_length=4096)
    confirm: bool = False
    allow_side_effect: bool = False


def _require_local_developer_request(request: Request, confirmed: bool) -> None:
    client_host = str(request.client.host if request.client else "")
    if client_host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail={"reason": "developer_write_requires_loopback"})
    if not confirmed:
        raise HTTPException(status_code=409, detail={"reason": "developer_write_requires_confirmation"})


class LiveOrderUpdate(BaseModel):
    signal_id: int
    amount: float | None = None


class CanaryDryRunUpdate(BaseModel):
    signal_id: int
    amount: float | None = Field(default=None, gt=0.0)


def _read_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _write_json(path, payload):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def _json_safe(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _production_refresh_summary(payload):
    stages = payload.get("stages") or []
    return {
        "requested_at": payload.get("requested_at"),
        "ok": bool(payload.get("ok")),
        "failed_stages": list(payload.get("failed_stages") or []),
        "stage_count": len(stages),
        "ok_stage_count": sum(1 for stage in stages if stage.get("ok")),
        "blocked_keys": list(((payload.get("readiness") or {}).get("blocked_keys") or [])),
        "scan_signals": bool(payload.get("scan_signals")),
    }


def _save_production_refresh_result(payload):
    previous = _read_json(PRODUCTION_REFRESH_PATH, None) or {}
    history = list(previous.get("history") or [])
    current_summary = _production_refresh_summary(payload)
    if current_summary.get("requested_at"):
        history = [current_summary, *history]
    payload["history"] = history[:7]
    payload["running"] = False
    _write_json(PRODUCTION_REFRESH_PATH, payload)
    return _json_safe(payload)


def _save_production_refresh_progress(payload):
    previous = _read_json(PRODUCTION_REFRESH_PATH, None) or {}
    payload["history"] = list(previous.get("history") or [])[:7]
    payload["running"] = True
    _write_json(PRODUCTION_REFRESH_PATH, payload)
    return _json_safe(payload)


def _production_refresh_runtime_state(payload):
    if not payload:
        return None
    state = dict(payload)
    state["last_refresh_was_auto"] = bool(state.pop("auto_refresh", False))
    state["auto_refresh_enabled"] = AUTO_REFRESH_ENABLED
    state["auto_refresh_running"] = bool(auto_refresh_task and not auto_refresh_task.done())
    state["production_refresh_running"] = production_refresh_lock.locked()
    state["running"] = production_refresh_lock.locked()
    return _json_safe(state)


def _auto_simulation_state():
    state = _read_json(AUTO_SIMULATION_PATH, {})
    return {
        "enabled": bool(state.get("enabled", False)),
        "interval_seconds": max(60, min(int(state.get("interval_seconds") or 300), 3600)),
        "last_run": state.get("last_run"),
        "last_result": state.get("last_result"),
        "last_error": state.get("last_error"),
    }


def _save_auto_simulation_state(**updates):
    state = _auto_simulation_state()
    state.update(updates)
    _write_json(AUTO_SIMULATION_PATH, state)
    return state


def load_markets():
    markets = []
    if not MARKETS_DIR.exists():
        return markets
    for path in MARKETS_DIR.glob("*.json"):
        data = _read_json(path, None)
        if isinstance(data, dict):
            data["_file"] = path.name
            markets.append(data)
    markets.sort(key=lambda m: (m.get("date", ""), m.get("city_name", "")))
    return markets


def _c_to_f(value):
    try:
        return float(value) * 9.0 / 5.0 + 32.0
    except Exception:
        return None


def _native_to_f(value, unit):
    if value is None:
        return None
    return _c_to_f(value) if unit == "C" else float(value)


def _delta_to_f(value, unit):
    if value is None:
        return None
    return float(value) * 9.0 / 5.0 if unit == "C" else float(value)


def _norm_cdf(value):
    return 0.5 * (1.0 + math.erf(float(value) / math.sqrt(2.0)))


def _bucket_probability_f(forecast_f, low_f, high_f, sigma_f):
    if forecast_f is None or low_f is None or high_f is None:
        return None
    try:
        forecast_f = float(forecast_f)
        low_f = float(low_f)
        high_f = float(high_f)
        sigma_f = max(0.5, float(sigma_f or 0))
    except Exception:
        return None
    if low_f <= -900:
        return max(0.0, min(1.0, _norm_cdf((high_f + 0.5 - forecast_f) / sigma_f)))
    if high_f >= 900:
        return max(0.0, min(1.0, 1.0 - _norm_cdf((low_f - 0.5 - forecast_f) / sigma_f)))
    lower = low_f - 0.5
    upper = high_f + 0.5
    return max(0.0, min(1.0, _norm_cdf((upper - forecast_f) / sigma_f) - _norm_cdf((lower - forecast_f) / sigma_f)))


def _calc_ev(probability, price):
    try:
        probability = float(probability)
        price = float(price)
    except Exception:
        return None
    if price <= 0 or price >= 1:
        return 0.0
    return probability * (1.0 / price - 1.0) - (1.0 - probability)


def _latest_snapshot(market, key):
    items = market.get(key, [])
    return items[-1] if items else {}


def _event_slug_from_url(url):
    if not url:
        return None
    return url.rstrip("/").split("/")[-1]


def _clean_text(value):
    if value is None:
        return value
    return (
        str(value)
        .replace("\u00c2\u00b0F", "\u00b0F")
        .replace("\u00c2\u00b0C", "\u00b0C")
        .replace("\u00c2\u00b0", "\u00b0")
    )


def _repair_display_text(value):
    """Repair old mojibake strings at the API boundary without rewriting history."""
    if value is None:
        return value
    text = str(value)
    try:
        repaired = text.encode("latin1").decode("utf-8")
    except UnicodeError:
        return _clean_text(text)
    if any(ch in text for ch in ("Ã", "Â", "æ", "ç", "å", "è", "é")):
        return _clean_text(repaired)
    return _clean_text(text)


def _today_str():
    return datetime.now().date().isoformat()


def _bulk_simulation_skip_reason(signal, dashboard_signal, today):
    status = str(signal.get("status") or dashboard_signal.get("status") or "signal")
    if (signal.get("date") or dashboard_signal.get("target_date") or "") < today:
        return "expired_signal"
    if status in ("skipped", "simulated", "bought"):
        return f"already_{status}"
    if bool(dashboard_signal.get("paper_position")):
        return "already_paper_position"
    if not bool(dashboard_signal.get("actionable", True)):
        return "not_actionable"
    try:
        display_edge = float(dashboard_signal.get("edge") or 0)
    except Exception:
        display_edge = 0.0
    if display_edge <= 0:
        return "calibrated_ev_nonpositive"
    decision = dashboard_signal.get("decision") or {}
    if decision and decision.get("paper_allowed") is False:
        reasons = decision.get("reasons") or []
        return f"paper_gate:{reasons[0]}" if reasons else "paper_gate_blocked"
    pre_strategy_allowed = dashboard_signal.get("live_pre_strategy_allowed")
    if pre_strategy_allowed is False:
        reasons = dashboard_signal.get("live_block_reasons") or []
        paper_hard_reasons = {
            "price_below_min",
            "price_above_max",
            "spread_above_limit",
            "spread_cost_too_high",
            "low_price_tail_unverified",
            "near_lock_missing_metar",
            "distribution_missing",
            "distribution_edge_negative",
        }
        hard_reasons = [r for r in reasons if r in paper_hard_reasons]
        if hard_reasons:
            return f"risk_gate:{hard_reasons[0]}"
    return None


def _market_truth_observation(market):
    actual = market.get("actual_temp")
    if actual is None:
        return None
    provider = market.get("actual_provider") or "legacy_unknown"
    confidence = float(market.get("actual_confidence") or 0.0)
    eligible = bool(market.get("actual_calibration_eligible"))
    if provider == "legacy_unknown":
        eligible = False
        confidence = min(confidence, 0.2)
    return {
        "city": market.get("city") or "",
        "city_name": market.get("city_name") or market.get("city") or "",
        "target_date": market.get("date") or "",
        "station_id": market.get("actual_station") or market.get("station") or "",
        "station_name": market.get("city_name") or market.get("city") or "",
        "unit": market.get("unit") or "F",
        "actual_temp": actual,
        "provider": provider,
        "source_url": market.get("actual_source_url") or "",
        "observation_count": int(market.get("actual_observation_count") or 0),
        "source_confidence": confidence,
        "calibration_eligible": eligible,
        "reason_if_ineligible": market.get("actual_reason_if_ineligible") or ("" if eligible else "legacy_or_low_confidence_truth"),
        "raw": {
            "market_file": market.get("_file"),
            "resolved_outcome": market.get("resolved_outcome"),
        },
    }


def _market_calibration_eligible(market):
    obs = _market_truth_observation(market)
    if not obs:
        return False
    return bool(obs.get("calibration_eligible"))


def _build_truth_health(markets):
    for market in markets:
        obs = _market_truth_observation(market)
        if not obs:
            continue
        try:
            upsert_truth_observation(obs)
        except Exception:
            pass

    summary = truth_coverage_summary()
    cfg = load_v3_config()
    for item in summary.get("cities", []):
        reasons = []
        cautions = []
        if item["eligible_observations"] < cfg.min_independent_settlement_days:
            reasons.append("truth_independent_days_low")
        if item["open_meteo_fallbacks"]:
            cautions.append("open_meteo_truth_fallback_present")
        if item["legacy_unknown"]:
            cautions.append("legacy_truth_unknown_excluded")
        item["status"] = "eligible" if not reasons else "blocked"
        item["reasons"] = reasons
        item["cautions"] = cautions
    return summary


def _truth_city_lookup(truth_health):
    return {row.get("city"): row for row in truth_health.get("cities", [])}


def _forecast_archive_manifest_payload(limit=200, sources=None, include_jsonl=False):
    try:
        limit = max(1, min(int(limit or 200), 1000))
    except Exception:
        limit = 200
    source_list = [
        str(source).strip()
        for source in (sources or ["ecmwf", "gfs_ensemble"])
        if str(source).strip()
    ]
    audit = build_model_dataset_audit()
    manifest = build_forecast_archive_manifest(audit, sources=source_list, limit=limit)
    payload = {
        "manifest_version": manifest["manifest_version"],
        "generated_at": manifest["generated_at"],
        "record_count": manifest["record_count"],
        "by_city": manifest["by_city"],
        "by_source": manifest["by_source"],
        "records": manifest["records"],
        "sources": source_list,
        "schema_doc": "FORECAST_ARCHIVE_IMPORT_CN.md",
        "template_command": ".\\.venv\\Scripts\\python.exe -m weatherbot_v3.cli forecast-archive-manifest --output-path data\\forecast_archive\\historical_forecasts.template.jsonl",
        "import_dry_run_command": ".\\.venv\\Scripts\\python.exe -m weatherbot_v3.cli forecast-archive-import --archive-path data\\forecast_archive\\historical_forecasts.jsonl",
        "import_apply_command": ".\\.venv\\Scripts\\python.exe -m weatherbot_v3.cli forecast-archive-import --archive-path data\\forecast_archive\\historical_forecasts.jsonl --apply",
        "audit_summary": audit.get("summary", {}),
        "reason_counts": audit.get("reason_counts", {}),
    }
    if include_jsonl:
        payload["jsonl"] = manifest["jsonl"]
    return payload


def _sync_v4_market_rules(markets):
    global _market_rule_sync_signature
    signature = tuple(
        sorted(
            (str(market.get("_file") or f"{market.get('city')}:{market.get('date')}"), float(Path(MARKETS_DIR / str(market.get("_file"))).stat().st_mtime if market.get("_file") and (MARKETS_DIR / str(market.get("_file"))).exists() else 0.0))
            for market in markets
        )
    )
    if signature == _market_rule_sync_signature:
        return
    rules = []
    contracts = {}
    for market in markets:
        outcomes = market.get("all_outcomes") or []
        for outcome in outcomes:
            payload = {
                **market,
                "market_id": outcome.get("market_id"),
                "question": outcome.get("question") or market.get("question") or "",
                "event_url": outcome.get("event_url") or market.get("event_url") or "",
            }
            try:
                rule = infer_settlement_rule(payload).to_dict()
                rules.append(rule)
                contract = settlement_contract_from_rule(rule)
                if contract.get("event_slug"):
                    contracts[str(contract["event_slug"])] = contract
            except Exception:
                continue
    upsert_market_rules(rules)
    upsert_settlement_contracts(list(contracts.values()))
    _market_rule_sync_signature = signature


def _latest_market_update():
    if not MARKETS_DIR.exists():
        return None
    latest = None
    for path in MARKETS_DIR.glob("*.json"):
        ts = path.stat().st_mtime
        latest = ts if latest is None else max(latest, ts)
    if latest is None:
        return None
    return datetime.fromtimestamp(latest, timezone.utc).isoformat()


def _data_age_minutes(latest_iso):
    if not latest_iso:
        return None
    try:
        latest = datetime.fromisoformat(latest_iso)
        return round((datetime.now(timezone.utc) - latest).total_seconds() / 60.0, 1)
    except Exception:
        return None


def _age_seconds(value, *, now: datetime | None = None):
    parsed = _parse_iso(value)
    if not parsed:
        return None
    current = now or datetime.now(timezone.utc)
    return max(0.0, (current - parsed).total_seconds())


def _temperature_in_unit(value, unit: str):
    if value is None:
        return None
    try:
        number = float(value)
    except Exception:
        return None
    display_unit = str(unit or "").upper()
    if display_unit == "F" and number < 60:
        converted = _c_to_f(number)
        return round(converted, 1) if converted is not None else None
    if display_unit == "C" and number > 60:
        return round((number - 32.0) * 5.0 / 9.0, 1)
    return round(number, 1)


def _latest_rows_by_city(rows: list[dict], time_key: str) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in rows:
        city = str(row.get("city") or row.get("city_key") or "").strip().lower()
        if not city:
            continue
        current = latest.get(city)
        if current is None or str(row.get(time_key) or "") > str(current.get(time_key) or ""):
            latest[city] = row
    return latest


def _recommendation_reasons(row: dict) -> list[str]:
    reasons = []
    for value in row.get("gate_reasons") or _read_json_from_text(row.get("gate_reasons_json"), []):
        text = str(value or "").strip()
        if text and text not in reasons:
            reasons.append(text)
    primary = str(row.get("blocked_reason_primary") or "").strip()
    if primary and primary not in reasons:
        reasons.insert(0, primary)
    return reasons


def _recommendation_is_paper_or_spread_watch(row: dict) -> bool:
    if bool(row.get("paper_allowed")):
        return True
    ignored = {"live_trading_disabled", "paper_gate_not_passed", "insufficient_bias_samples"}
    blocking = {reason for reason in _recommendation_reasons(row) if reason not in ignored}
    return bool(blocking) and blocking <= {"spread_too_wide"}


def _recommendation_class(target_date: str, station: dict, now: datetime) -> str:
    tz_name = str(station.get("settlement_timezone") or station.get("timezone") or "UTC")
    try:
        local_today = now.astimezone(ZoneInfo(tz_name)).date().isoformat()
    except Exception:
        local_today = now.date().isoformat()
    return "today_observation" if str(target_date or "") == local_today else "forecast_lead"


def _station_local_today(station: dict, now: datetime) -> str:
    tz_name = str(station.get("settlement_timezone") or station.get("timezone") or "UTC")
    try:
        return now.astimezone(ZoneInfo(tz_name)).date().isoformat()
    except Exception:
        return now.date().isoformat()


def _recommendation_query_cutoff(stations: dict[str, dict], now: datetime) -> str:
    local_dates = [_station_local_today(station, now) for station in stations.values()]
    return min(local_dates) if local_dates else _today_str()


def _recommendation_event_state(event: dict, now: datetime) -> dict:
    raw = _read_json_from_text(event.get("raw_json"), {}) if event else {}
    end_at = _parse_iso(raw.get("endDate") or raw.get("end_date"))
    is_open = bool(
        event
        and raw.get("active") is not False
        and raw.get("closed") is not True
        and end_at is not None
        and now < end_at
    )
    if not event:
        reason = "market_missing"
    elif end_at is None:
        reason = "market_end_time_missing"
    elif now >= end_at:
        reason = "market_ended"
    elif raw.get("closed") is True or raw.get("active") is False:
        reason = "market_inactive"
    else:
        reason = ""
    return {
        "is_open": is_open,
        "end_at": end_at.isoformat() if end_at else None,
        "reason": reason,
        "slug": event.get("slug") if event else None,
    }


def _recommendation_display_high(prediction: dict, current_temp, unit: str) -> dict:
    raw = _read_json_from_text(prediction.get("raw_json"), {})
    model_mu = _temperature_in_unit(raw.get("model_mu"), unit)
    effective_mu = _temperature_in_unit(
        prediction.get("mu") if prediction.get("mu") is not None else raw.get("effective_mu"),
        unit,
    )
    observed_high = _temperature_in_unit(raw.get("observed_floor"), unit)
    has_prediction = model_mu is not None or effective_mu is not None
    values = [value for value in (model_mu, effective_mu, observed_high, current_temp) if value is not None] if has_prediction else []
    return {
        "display_high": max(values) if values else None,
        "has_prediction": has_prediction,
        "model_mu": model_mu,
        "effective_mu": effective_mu,
        "observed_high": observed_high,
    }


def _freshest_focus_observation(city: str, metars: dict[str, dict], china_live: dict[str, dict]) -> tuple[dict, str]:
    candidates = [
        (china_live.get(city) or {}, "china_live", "observed_at"),
        (metars.get(city) or {}, "metar", "report_time"),
    ]
    available = [item for item in candidates if item[0].get(item[2])]
    if not available:
        return {}, ""
    row, source, _ = max(
        available,
        key=lambda item: _parse_iso(item[0].get(item[2])) or datetime.min.replace(tzinfo=timezone.utc),
    )
    return row, source


def _recommendations_payload(limit: int = 8, *, scheduler_status: dict | None = None, path: Path | None = None) -> dict:
    """Return separate weather-focus cards and Layer 6 trade candidates."""
    init_v3_db(path)
    now = datetime.now(timezone.utc)
    items: list[dict] = []
    skipped = Counter()
    with connect(path) as conn:
        stations = {
            str(row["city_key"]): dict(row)
            for row in conn.execute("SELECT * FROM stations").fetchall()
        }
        query_cutoff = _recommendation_query_cutoff(stations, now)
        metars = _latest_rows_by_city(
            [dict(row) for row in conn.execute(
                """
                SELECT *
                FROM metar_reports
                WHERE city IS NOT NULL AND TRIM(city) != ''
                ORDER BY report_time DESC, id DESC
                LIMIT 500
                """
            ).fetchall()],
            "report_time",
        )
        china_live = _latest_rows_by_city(
            [dict(row) for row in conn.execute(
                """
                SELECT *
                FROM mesonet_observations
                WHERE network = 'china_live'
                  AND city IS NOT NULL
                  AND TRIM(city) != ''
                ORDER BY observed_at DESC, id DESC
                LIMIT 100
                """
            ).fetchall()],
            "observed_at",
        )
        forecast_runs = [
            dict(row) for row in conn.execute(
                """
                SELECT
                    city,
                    target_date,
                    COALESCE(available_at, retrieved_at, created_at) AS forecast_time,
                    source
                FROM forecast_runs
                WHERE target_date >= ?
                  AND city IS NOT NULL
                  AND TRIM(city) != ''
                ORDER BY city, target_date, COALESCE(available_at, retrieved_at, created_at) DESC, id DESC
                LIMIT 1500
                """,
                (query_cutoff,),
            ).fetchall()
        ]
        predictions = [
            dict(row) for row in conn.execute(
                """
                SELECT *
                FROM daily_max_predictions
                WHERE target_date >= ?
                ORDER BY target_date ASC, issued_at DESC, id DESC
                LIMIT 500
                """,
                (query_cutoff,),
            ).fetchall()
        ]
        events = [
            dict(row) for row in conn.execute(
                """
                SELECT *
                FROM polymarket_events
                WHERE target_date >= ?
                  AND city IS NOT NULL
                  AND TRIM(city) != ''
                ORDER BY city, target_date, updated_at DESC
                LIMIT 1000
                """,
                (query_cutoff,),
            ).fetchall()
        ]
        decisions = [
            dict(row) for row in conn.execute(
                """
                SELECT
                    sd.*,
                    mb.event_url AS bucket_event_url,
                    mb.question AS bucket_question,
                    mb.bucket_label AS market_bucket_label,
                    mb.strict_match_status AS market_strict_match_status,
                    mb.strict_match_reasons AS market_strict_match_reasons,
                    mb.unit AS market_unit,
                    mb.station_id AS market_station_id,
                    mb.price AS bucket_price
                FROM signal_decisions sd
                LEFT JOIN market_buckets mb ON mb.bucket_key = sd.bucket_key
                WHERE sd.target_date >= ?
                ORDER BY sd.city_key, sd.target_date, sd.issued_at DESC, sd.edge DESC, sd.id DESC
                LIMIT 1500
                """,
                (query_cutoff,),
            ).fetchall()
        ]

    latest_prediction_by_city: dict[str, dict] = {}
    latest_prediction_by_city_date: dict[tuple[str, str], dict] = {}
    for row in predictions:
        city = str(row.get("city_key") or "").strip().lower()
        target_date = str(row.get("target_date") or "")
        if city and target_date:
            latest_prediction_by_city_date.setdefault((city, target_date), row)
        if city and city not in latest_prediction_by_city:
            latest_prediction_by_city[city] = row

    latest_event_by_city_date: dict[tuple[str, str], dict] = {}
    for row in events:
        city = str(row.get("city") or "").strip().lower()
        target_date = str(row.get("target_date") or "")
        if city and target_date:
            latest_event_by_city_date.setdefault((city, target_date), row)

    focus_items: list[dict] = []
    for city, station in stations.items():
        if not bool(station.get("enabled")):
            continue
        target_date = _station_local_today(station, now)
        event_state = _recommendation_event_state(latest_event_by_city_date.get((city, target_date)) or {}, now)
        if not event_state["is_open"]:
            skipped["focus_market_closed_or_missing"] += 1
            continue
        prediction = latest_prediction_by_city_date.get((city, target_date)) or {}
        live_row, observation_source = _freshest_focus_observation(city, metars, china_live)
        observed_time = live_row.get("observed_at") or live_row.get("report_time")
        observation_age = _age_seconds(observed_time, now=now)
        if observation_age is None or observation_age >= 30 * 60:
            skipped["focus_observation_stale_or_missing"] += 1
            continue
        unit = str(station.get("settlement_unit") or station.get("unit") or "C").upper()
        current_temp = _temperature_in_unit(live_row.get("temperature"), unit)
        high_summary = _recommendation_display_high(prediction, current_temp, unit)
        predicted_max = high_summary["display_high"]
        if current_temp is None or not high_summary["has_prediction"] or predicted_max is None:
            skipped["focus_prediction_or_temperature_missing"] += 1
            continue
        remaining = float(predicted_max) - float(current_temp)
        near_peak_threshold = 3.6 if unit == "F" else 2.0
        if abs(remaining) > near_peak_threshold:
            skipped["focus_not_near_predicted_max"] += 1
            continue
        focus_items.append({
            "type": "weather_focus",
            "city_key": city,
            "city_name": station.get("city_name") or city,
            "station_id": station.get("station_id") or "",
            "target_date": target_date,
            "current_temp": current_temp,
            "current_temp_unit": unit,
            "deb_mu": predicted_max,
            "deb_model_mu": high_summary["model_mu"],
            "deb_effective_mu": high_summary["effective_mu"],
            "observed_high": high_summary["observed_high"],
            "deb_sigma": prediction.get("sigma"),
            "deb_unit": unit,
            "metar_age_seconds": observation_age,
            "metar_report_time": observed_time,
            "observation_source": observation_source,
            "prediction_issued_at": prediction.get("issued_at"),
            "remaining_to_max": round(remaining, 1),
            "focus_reason": "near_predicted_daily_max",
            "market_open": True,
            "market_end_at": event_state["end_at"],
            "polymarket_url": f"https://polymarket.com/event/{event_state['slug']}" if event_state["slug"] else None,
            "badge": "天气关注",
        })
    focus_items.sort(key=lambda item: (
        abs(float(item.get("remaining_to_max") or 0.0)),
        float(item.get("metar_age_seconds") or 0.0),
        str(item.get("city_name") or ""),
    ))
    focus_items = focus_items[: min(max(1, int(limit)), 4)]

    latest_forecast_by_city_date: dict[tuple[str, str], dict] = {}
    for row in forecast_runs:
        city = str(row.get("city") or "").strip().lower()
        target_date = str(row.get("target_date") or "")
        if city and target_date:
            latest_forecast_by_city_date.setdefault((city, target_date), row)

    latest_issued_by_city_date: dict[tuple[str, str], str] = {}
    for row in decisions:
        key = (str(row.get("city_key") or "").strip().lower(), str(row.get("target_date") or ""))
        if not key[0] or not key[1]:
            continue
        latest_issued_by_city_date.setdefault(key, str(row.get("issued_at") or ""))

    candidates: list[dict] = []
    for row in decisions:
        city = str(row.get("city_key") or "").strip().lower()
        target_date = str(row.get("target_date") or "")
        if not city or not target_date:
            continue
        if str(row.get("issued_at") or "") != latest_issued_by_city_date.get((city, target_date)):
            skipped["older_decision_round"] += 1
            continue
        station = stations.get(city) or {}
        if target_date < _station_local_today(station, now):
            skipped["past_target_date"] += 1
            continue
        event_state = _recommendation_event_state(latest_event_by_city_date.get((city, target_date)) or {}, now)
        if not event_state["is_open"]:
            skipped["market_closed_or_missing"] += 1
            continue
        metar = metars.get(city) or {}
        forecast = latest_forecast_by_city_date.get((city, target_date)) or {}
        recommendation_class = _recommendation_class(target_date, station, now)
        metar_age = _age_seconds(metar.get("report_time"), now=now)
        forecast_age = _age_seconds(forecast.get("forecast_time"), now=now)
        if recommendation_class == "today_observation" and (metar_age is None or metar_age >= 30 * 60):
            skipped["metar_stale_or_missing"] += 1
            continue
        if recommendation_class == "forecast_lead" and (forecast_age is None or forecast_age >= 90 * 60):
            skipped["forecast_stale_or_missing"] += 1
            continue
        if not str(station.get("settlement_rule_verified_at") or "").strip():
            skipped["settlement_unverified"] += 1
            continue
        if str(row.get("market_strict_match_status") or "") != "matched":
            skipped["bucket_not_strict_match"] += 1
            continue
        if not _recommendation_is_paper_or_spread_watch(row):
            skipped["paper_gate_blocked"] += 1
            continue
        candidates.append(row)

    candidates.sort(key=lambda row: (
        0 if bool(row.get("paper_allowed")) else 1,
        -float(row.get("edge") or -999),
        str(row.get("target_date") or ""),
    ))
    ladder_rows_by_group: dict[str, list[dict]] = {}
    for row in candidates:
        group_id = str(row.get("ladder_group_id") or "")
        if group_id:
            ladder_rows_by_group.setdefault(group_id, []).append(row)
    for group_rows in ladder_rows_by_group.values():
        group_rows.sort(key=lambda item: (float(item.get("bucket_lower") or -9999), float(item.get("bucket_upper") or 9999)))
    seen_cities: set[str] = set()
    for row in candidates:
        city = str(row.get("city_key") or "").strip().lower()
        if city in seen_cities:
            continue
        seen_cities.add(city)
        station = stations.get(city) or {}
        metar = metars.get(city) or {}
        forecast = latest_forecast_by_city_date.get((city, str(row.get("target_date") or ""))) or {}
        prediction = latest_prediction_by_city.get(city) or row
        unit = str(station.get("settlement_unit") or row.get("market_unit") or "F").upper()
        reasons = [
            reason for reason in _recommendation_reasons(row)
            if reason not in {"live_trading_disabled", "insufficient_bias_samples"}
        ]
        strategy_name = str(row.get("strategy_name") or "single_bucket_ev")
        ladder_group_id = str(row.get("ladder_group_id") or "")
        sub_rows = ladder_rows_by_group.get(ladder_group_id, []) if strategy_name == "ladder_grid" and ladder_group_id else []
        items.append({
            "type": "trade_candidate",
            "city_key": city,
            "city_name": station.get("city_name") or city,
            "station_id": station.get("station_id") or row.get("market_station_id") or "",
            "settlement_station_id": station.get("settlement_station_id") or station.get("station_id") or "",
            "verification_status": station.get("verification_status") or "",
            "settlement_rule_verified_at": station.get("settlement_rule_verified_at") or "",
            "recommendation_class": _recommendation_class(str(row.get("target_date") or ""), station, now),
            "metar_age_seconds": _age_seconds(metar.get("report_time"), now=now) if metar else None,
            "metar_report_time": metar.get("report_time") if metar else None,
            "forecast_age_seconds": _age_seconds(forecast.get("forecast_time"), now=now) if forecast else None,
            "forecast_time": forecast.get("forecast_time") if forecast else None,
            "current_temp": _temperature_in_unit(metar.get("temperature"), unit),
            "current_temp_unit": unit,
            "target_date": row.get("target_date"),
            "deb_mu": _temperature_in_unit(row.get("mu"), unit),
            "deb_sigma": row.get("sigma"),
            "deb_unit": unit,
            "bucket_label": row.get("market_bucket_label") or (row.get("model_bucket_probs") or {}).get("bucket_label") or row.get("bucket_key"),
            "bucket_key": row.get("bucket_key"),
            "strategy_name": strategy_name,
            "strategy_label": {
                "single_bucket_ev": "Single Bucket EV",
                "ladder_grid": "Ladder Grid",
                "tail_buying": "Tail Buying",
            }.get(strategy_name, strategy_name),
            "kelly_fraction": row.get("kelly_fraction"),
            "position_size_usd": row.get("position_size_usd"),
            "ladder_group_id": ladder_group_id,
            "sub_buckets": [
                {
                    "bucket_key": sub.get("bucket_key"),
                    "bucket_label": sub.get("market_bucket_label") or sub.get("bucket_key"),
                    "model_probability": sub.get("model_probability"),
                    "market_ask": sub.get("market_ask"),
                    "edge": sub.get("edge"),
                    "kelly_fraction": sub.get("kelly_fraction"),
                    "position_size_usd": sub.get("position_size_usd"),
                    "paper_allowed": bool(sub.get("paper_allowed")),
                    "blocked_reasons": [
                        reason for reason in _recommendation_reasons(sub)
                        if reason not in {"live_trading_disabled", "insufficient_bias_samples"}
                    ],
                }
                for sub in sub_rows
            ],
            "edge": row.get("edge"),
            "edge_percent": row.get("edge_percent"),
            "model_probability": row.get("model_probability"),
            "market_probability": row.get("market_implied_probability"),
            "market_ask": row.get("market_ask"),
            "market_bid": row.get("market_bid"),
            "paper_allowed": bool(row.get("paper_allowed")),
            "blocked_reasons": reasons,
            "polymarket_url": row.get("bucket_event_url"),
            "market_id": row.get("market_id"),
            "token_id": row.get("yes_token_id") or row.get("token_id"),
            "market_open": True,
            "market_end_at": event_state["end_at"],
        })
        if len(items) >= limit:
            break

    for city in ("shanghai", "hong-kong"):
        if len(items) >= limit:
            break
        if any(item.get("city_key") == city for item in items):
            continue
        station = stations.get(city) or {}
        if str(station.get("verification_status") or "") != "no_active_market":
            continue
        prediction = latest_prediction_by_city.get(city) or {}
        live_row = china_live.get(city) or metars.get(city) or {}
        observed_time = live_row.get("observed_at") or live_row.get("report_time")
        unit = str(station.get("settlement_unit") or station.get("unit") or "C").upper()
        items.append({
            "type": "observation_only",
            "city_key": city,
            "city_name": station.get("city_name") or city,
            "station_id": station.get("station_id") or "",
            "settlement_station_id": station.get("settlement_station_id") or station.get("station_id") or "",
            "verification_status": "no_active_market",
            "settlement_rule_verified_at": station.get("settlement_rule_verified_at") or "",
            "metar_age_seconds": _age_seconds(observed_time, now=now),
            "metar_report_time": observed_time,
            "current_temp": _temperature_in_unit(live_row.get("temperature"), unit),
            "current_temp_unit": unit,
            "target_date": prediction.get("target_date") or today,
            "deb_mu": _temperature_in_unit(prediction.get("mu"), unit),
            "deb_sigma": prediction.get("sigma"),
            "deb_unit": unit,
            "china_live_temp": _temperature_in_unit(live_row.get("temperature"), unit),
            "china_live_observed_at": observed_time,
            "badge": "仅观测分析（无市场）",
            "blocked_reasons": ["no_active_market"],
        })

    scheduler_running = bool((scheduler_status or {}).get("running"))
    empty_reason = ""
    if not items:
        empty_reason = "scheduler_stopped" if not scheduler_running else "no_recommendations_after_gates"
    return {
        "ok": True,
        "recommendation_version": "recommendations-v2-separated-contracts",
        "generated_at": now.isoformat(),
        "scheduler_running": scheduler_running,
        "filters": {
            "weather_focus": {
                "observation_max_age_seconds": 30 * 60,
                "near_predicted_max_c": 2.0,
                "near_predicted_max_f": 3.6,
                "trade_claim": False,
                "open_market_required": True,
            },
            "today_observation_metar_max_age_seconds": 30 * 60,
            "forecast_lead_forecast_max_age_seconds": 90 * 60,
            "settlement_rule_verified_required": True,
            "strict_match_status": "matched",
            "paper_allowed_or_spread_watch": True,
            "open_market_required": True,
        },
        "skipped": dict(skipped),
        "count": len(items),
        "trade_candidate_count": sum(1 for item in items if item.get("type") == "trade_candidate"),
        "observation_only_count": sum(1 for item in items if item.get("type") == "observation_only"),
        "weather_focus_count": len(focus_items),
        "focus_items": focus_items,
        "empty_reason": empty_reason,
        "items": items,
    }


def _parse_iso(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _at_or_after(value, lower_bound):
    if not lower_bound:
        return True
    parsed = _parse_iso(value)
    return bool(parsed and parsed >= lower_bound)


def _bucket_bounds(signal):
    raw = _read_json_from_text(signal.get("raw_json"), {})
    low = raw.get("bucket_low")
    high = raw.get("bucket_high")
    if low is not None and high is not None:
        return low, high
    label = str(signal.get("bucket_label") or "")
    label = label.replace("F", "").replace("C", "")
    try:
        left, right = label.split("-", 1)
        return float(left), float(right)
    except Exception:
        return signal.get("forecast_temp"), signal.get("forecast_temp")


def _read_json_from_text(value, fallback):
    try:
        return json.loads(value or "{}")
    except Exception:
        return fallback


def _is_dashboard_position_import(signal):
    return _read_json_from_text(signal.get("raw_json"), {}).get("source") == "dashboard_simulation"


def _market_path_for_signal(signal):
    city = signal.get("city")
    date = signal.get("date")
    if not city or not date:
        return None
    return MARKETS_DIR / f"{city}_{date}.json"


def _position_from_signal(signal, amount, opened_at, execution=None):
    raw = _read_json_from_text(signal.get("raw_json"), {})
    execution = execution or {}
    low, high = _bucket_bounds(signal)
    entry_price = float(
        execution.get("average_fill_price")
        or signal.get("limit_price")
        or raw.get("entry_price")
        or 0
    )
    bid_price = float(signal.get("bid_price") or raw.get("bid_at_entry") or entry_price)
    shares = float(execution.get("shares") or (round(amount / entry_price, 2) if entry_price > 0 else 0))
    return {
        "market_id": signal.get("market_id") or raw.get("market_id"),
        "event_url": signal.get("event_url") or raw.get("event_url"),
        "yes_token_id": signal.get("yes_token_id") or raw.get("yes_token_id"),
        "no_token_id": raw.get("no_token_id"),
        "question": _clean_text(signal.get("question") or raw.get("question")),
        "bucket_low": low,
        "bucket_high": high,
        "entry_price": entry_price,
        "bid_at_entry": bid_price,
        "spread": signal.get("spread") if signal.get("spread") is not None else raw.get("spread"),
        "shares": shares,
        "cost": round(amount, 2),
        "requested_cost": execution.get("fill", {}).get("filled_amount", amount)
        + execution.get("fill", {}).get("remaining_amount", 0),
        "unfilled_amount": execution.get("fill", {}).get("remaining_amount", 0),
        "fill_status": execution.get("status") or "legacy_fill",
        "fill_levels": execution.get("fill", {}).get("fills", []),
        "p": signal.get("probability") if signal.get("probability") is not None else raw.get("p"),
        "ev": signal.get("ev") if signal.get("ev") is not None else raw.get("ev"),
        "kelly": signal.get("kelly") if signal.get("kelly") is not None else raw.get("kelly"),
        "forecast_temp": signal.get("forecast_temp") if signal.get("forecast_temp") is not None else raw.get("forecast_temp"),
        "forecast_src": signal.get("forecast_src") or raw.get("forecast_src"),
        "sigma": raw.get("sigma"),
        "opened_at": opened_at,
        "status": "open",
        "pnl": None,
        "exit_price": None,
        "close_reason": None,
        "closed_at": None,
        "city": signal.get("city") or raw.get("city"),
        "source": "dashboard_simulation",
    }


def _open_paper_position(signal, amount, simulation_start, opened_at, execution=None):
    path = _market_path_for_signal(signal)
    if not path or amount <= 0:
        return False
    market = _read_json(path, None)
    if not isinstance(market, dict):
        return False

    current = market.get("position")
    if current:
        current_ts = current.get("opened_at") or market.get("created_at")
        if _at_or_after(current_ts, simulation_start):
            return False
        history = market.get("position_history")
        if not isinstance(history, list):
            history = []
        archived = dict(current)
        archived["archived_at"] = opened_at
        history.append(archived)
        market["position_history"] = history

    market["position"] = _position_from_signal(signal, amount, opened_at, execution)
    market["status"] = "open"
    market["pnl"] = None
    market["resolved_outcome"] = None
    _write_json(path, market)
    return True


def _find_outcome(market, market_id=None, yes_token_id=None):
    for outcome in market.get("all_outcomes") or []:
        if market_id and str(outcome.get("market_id") or "") == str(market_id):
            return outcome
        if yes_token_id and str(outcome.get("yes_token_id") or "") == str(yes_token_id):
            return outcome
    return {}


def _position_mark_price(market, pos):
    outcome = _find_outcome(
        market,
        market_id=pos.get("market_id"),
        yes_token_id=pos.get("yes_token_id"),
    )
    bid = outcome.get("bid")
    if bid is None:
        bid = pos.get("bid_at_entry")
    if bid is None:
        bid = pos.get("entry_price")
    try:
        return max(0.0, min(1.0, float(bid)))
    except Exception:
        return None


def _unrealized_pnl(pos, mark_price):
    if mark_price is None:
        return None
    try:
        shares = float(pos.get("shares") or 0)
        cost = float(pos.get("cost") or 0)
        return round((shares * float(mark_price)) - cost, 2)
    except Exception:
        return None


def _clear_dashboard_positions(reset_at):
    cleared = 0
    if not MARKETS_DIR.exists():
        return cleared
    for path in MARKETS_DIR.glob("*.json"):
        market = _read_json(path, None)
        if not isinstance(market, dict):
            continue
        pos = market.get("position")
        if not isinstance(pos, dict) or pos.get("source") != "dashboard_simulation":
            continue
        history = market.get("position_history")
        if not isinstance(history, list):
            history = []
        archived = dict(pos)
        archived["archived_at"] = reset_at
        archived["archive_reason"] = "simulation_reset"
        history.append(archived)
        market["position_history"] = history
        market["position"] = None
        market["pnl"] = None
        market["resolved_outcome"] = None
        if market.get("status") != "resolved":
            market["status"] = "open"
        _write_json(path, market)
        cleared += 1
    return cleared


def _bot_running():
    return bool(bot_process and bot_process.poll() is None)


def _terminate_pid_tree(pid):
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.kill(int(pid), 15)
        return True
    except Exception:
        return False


def _cleanup_stale_bot_process():
    if _bot_running():
        return False
    try:
        pid = int(BOT_PID_PATH.read_text(encoding="utf-8").strip())
    except Exception:
        return False
    cleaned = _terminate_pid_tree(pid)
    try:
        BOT_PID_PATH.unlink(missing_ok=True)
    except Exception:
        pass
    if cleaned:
        log_event("warning", f"已清理残留扫描器进程 PID {pid}")
    return cleaned


def _start_bot_process(source: str = "dashboard") -> dict:
    return {
        "status": "retired",
        "is_running": False,
        "reason": "legacy_scanner_retired_use_v3_scheduler",
        "source": source,
    }


def _event_to_payload(event):
    return {
        "id": event.get("id"),
        "timestamp": event.get("created_at"),
        "type": event.get("event_type"),
        "message": _repair_display_text(event.get("message")),
        "data": json.loads(event.get("raw_json") or "{}"),
    }


def _json_compact(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return str(value)


def _event_fetch_stage(event_payload: dict) -> str:
    text = " ".join([
        str(event_payload.get("type") or ""),
        str(event_payload.get("message") or ""),
        _json_compact(event_payload.get("data") or {}),
    ]).lower()
    if any(key in text for key in ("orderbook", "clob", "market", "盘口")):
        return "orderbook"
    if any(key in text for key in ("signal", "buy", "trade", "order")):
        return "signal"
    if any(key in text for key in ("truth", "history", "settle", "actual", "observ")):
        return "observation"
    if any(key in text for key in ("forecast", "weather", "metar", "refresh", "scan")):
        return "weather"
    return "system"


def _event_fetch_status(event_payload: dict) -> str:
    text = " ".join([
        str(event_payload.get("type") or ""),
        str(event_payload.get("message") or ""),
        _json_compact(event_payload.get("data") or {}),
    ]).lower()
    if any(key in text for key in ("error", "fail", "forbidden", "timeout", "exception", "err")):
        return "ERR"
    if any(key in text for key in ("warn", "skip", "blocked", "fallback")):
        return "WARN"
    if any(key in text for key in ("success", "ok", "done", "buy", "signal", "refresh")):
        return "OK"
    return "INFO"


def _fetch_log_payload(events: list[dict], limit: int = 100) -> list[dict]:
    rows: list[dict] = []
    for index, event in enumerate(events[:limit], start=1):
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        raw_source = (
            data.get("source")
            or data.get("provider")
            or data.get("stage")
            or data.get("action")
            or event.get("type")
            or _event_fetch_stage(event)
        )
        raw_duration = data.get("elapsed_ms") or data.get("duration_ms") or data.get("duration")
        message = _repair_display_text(event.get("message") or "")
        compact = _json_compact(data) if data else ""
        rows.append({
            "index": index,
            "time": event.get("timestamp"),
            "source": str(raw_source or "--"),
            "stage": _event_fetch_stage(event),
            "status": _event_fetch_status(event),
            "duration": raw_duration,
            "message": message,
            "details": compact,
            "event_id": event.get("id"),
            "event_type": event.get("type"),
        })
    return rows


def _data_fetch_log_payload(limit: int = 100) -> list[dict]:
    rows: list[dict] = []
    for index, row in enumerate(list_data_fetch_logs(limit), start=1):
        rows.append({
            "index": index,
            "time": row.get("finished_at") or row.get("created_at") or row.get("started_at"),
            "source": row.get("source") or "--",
            "stage": row.get("stage") or row.get("source") or "weather",
            "status": row.get("status") or "INFO",
            "duration": row.get("duration_ms"),
            "message": _repair_display_text(row.get("message") or ""),
            "details": row.get("details_json") or "",
            "event_id": row.get("id"),
            "event_type": "data_fetch_log",
            "city": row.get("city") or "",
            "target_date": row.get("target_date") or "",
        })
    return rows


def _combined_fetch_log_payload(events: list[dict], limit: int = 100) -> list[dict]:
    persisted = _data_fetch_log_payload(limit)
    transient = _fetch_log_payload(events, limit)
    rows = (persisted + transient)[:limit]
    for index, row in enumerate(rows, start=1):
        row["index"] = index
    return rows


def _tail_log_lines(path, start_pos):
    if not path.exists():
        return start_pos, []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(start_pos)
        lines = [line.rstrip() for line in handle.readlines()]
        return handle.tell(), [line for line in lines if line]


def _line_to_event(line):
    event_type = "info"
    if "[BUY]" in line:
        event_type = "trade"
    elif "[WIN]" in line:
        event_type = "success"
    elif "[LOSS]" in line:
        event_type = "error"
    elif "[RESOLVE]" in line:
        event_type = "warning"
    elif "[SKIP]" in line:
        event_type = "warning"
    elif "timed out" in line.lower() or "proxyerror" in line.lower():
        event_type = "warning"
    elif "Error:" in line or "error" in line.lower():
        event_type = "error"
    message = line
    if "[RESOLVE]" in line and ("timed out" in line.lower() or "proxyerror" in line.lower()):
        message = "结算接口网络超时，稍后重试即可：" + line
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        "message": message,
        "data": {},
    }


def _fetch_polymarket_yes_resolution(market_id):
    try:
        session = requests.Session()
        session.trust_env = False
        response = session.get(f"https://gamma-api.polymarket.com/markets/{market_id}", timeout=(5, 10))
        response.raise_for_status()
        data = response.json()
        if not data.get("closed", False):
            return None
        prices = data.get("outcomePrices", "[0.5,0.5]")
        if isinstance(prices, str):
            prices = json.loads(prices)
        yes_price = float(prices[0])
        if yes_price >= 0.95:
            return True
        if yes_price <= 0.05:
            return False
    except Exception as exc:
        return {"error": str(exc)}
    return None


def _settle_dashboard_positions():
    state = _read_json(DATA_DIR / "state.json", {})
    simulation_start = _parse_iso(state.get("simulation_started_at"))
    balance = float(state.get("balance", state.get("starting_balance", 0)) or 0)
    checked = 0
    settled = 0
    pending = 0
    errors = []
    now_iso = datetime.now(timezone.utc).isoformat()

    if not MARKETS_DIR.exists():
        return {"checked": 0, "settled": 0, "pending": 0, "errors": []}

    candidates = []
    for path in MARKETS_DIR.glob("*.json"):
        market = _read_json(path, None)
        if not isinstance(market, dict):
            continue
        pos = market.get("position")
        if not isinstance(pos, dict):
            continue
        if pos.get("source") != "dashboard_simulation" or pos.get("status") != "open":
            continue
        if not _at_or_after(pos.get("opened_at") or market.get("created_at"), simulation_start):
            continue
        market_id = pos.get("market_id")
        if not market_id:
            continue
        candidates.append((path, market_id))

    results = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(_fetch_polymarket_yes_resolution, market_id): (path, market_id)
            for path, market_id in candidates
        }
        for future in as_completed(futures):
            path, market_id = futures[future]
            try:
                results[(str(path), market_id)] = future.result()
            except Exception as exc:
                results[(str(path), market_id)] = {"error": str(exc)}

    for path, market_id in candidates:
        market = _read_json(path, None)
        if not isinstance(market, dict):
            continue
        pos = market.get("position")
        if not isinstance(pos, dict) or pos.get("status") != "open":
            continue
        checked += 1
        result = results.get((str(path), market_id))
        if isinstance(result, dict) and result.get("error"):
            errors.append({"market_id": market_id, "error": result["error"]})
            pending += 1
            continue
        if result is None:
            pending += 1
            continue

        entry_price = float(pos.get("entry_price") or 0)
        cost = float(pos.get("cost") or 0)
        shares = float(pos.get("shares") or 0)
        pnl = round(shares * (1 - entry_price), 2) if result else round(-cost, 2)
        balance += cost + pnl

        pos["exit_price"] = 1.0 if result else 0.0
        pos["pnl"] = pnl
        pos["close_reason"] = "resolved"
        pos["closed_at"] = now_iso
        pos["status"] = "closed"
        market["pnl"] = pnl
        market["status"] = "resolved"
        market["resolved_outcome"] = "win" if result else "loss"
        _write_json(path, market)

        state["wins" if result else "losses"] = int(state.get("wins" if result else "losses", 0) or 0) + 1
        settled += 1

    if settled:
        state["balance"] = round(balance, 2)
        state["peak_balance"] = max(float(state.get("peak_balance", balance) or balance), balance)
        _write_json(DATA_DIR / "state.json", state)

    return {"checked": checked, "settled": settled, "pending": pending, "errors": errors}


def _ev_bucket(ev):
    pct = abs(float(ev or 0)) * 100
    if pct < 25:
        return "0-25%"
    if pct < 50:
        return "25-50%"
    if pct < 100:
        return "50-100%"
    return "100%+"


def _price_bucket(price):
    try:
        value = float(price)
    except Exception:
        return "unknown"
    if value < 0.03:
        return "<3c"
    if value < 0.1:
        return "3-10c"
    if value < 0.25:
        return "10-25c"
    if value <= 0.45:
        return "25-45c"
    return ">45c"


def _fit_band(fit):
    if not fit:
        return "fit_missing"
    samples = int(fit.get("samples") or 0)
    mae = float(fit.get("mae_f") or 0)
    bias = abs(float(fit.get("bias_f") or 0))
    if samples < 10:
        return "sample_low"
    if mae > 3.0:
        return "mae_high"
    if bias > 2.0:
        return "bias_high"
    return "fit_ok"


def _snapshot_at_or_before(snapshots, timestamp):
    if not snapshots:
        return {}
    target = _parse_iso(timestamp)
    if not target:
        return snapshots[-1]
    best = None
    for snap in snapshots:
        snap_ts = _parse_iso(snap.get("ts"))
        if not snap_ts:
            continue
        if snap_ts <= target:
            best = snap
        elif best is not None:
            break
    return best or snapshots[0]


def _bucket_value_in_range(value, low, high):
    if value is None or low is None or high is None:
        return False
    try:
        value = float(value)
        low = float(low)
        high = float(high)
    except Exception:
        return False
    if low == high:
        return (low - 0.5) <= value < (high + 0.5)
    return low <= value <= high


def _entry_snapshot_features(market, item):
    unit = market.get("unit") or "F"
    opened_at = item.get("opened_at") or market.get("created_at")
    snap = _snapshot_at_or_before(market.get("forecast_snapshots") or [], opened_at)
    horizon = snap.get("horizon") or item.get("horizon") or ""
    try:
        hours_left = float(snap.get("hours_left")) if snap.get("hours_left") is not None else None
    except Exception:
        hours_left = None

    best_native = snap.get("best")
    metar_native = snap.get("metar")
    try:
        best_f = _native_to_f(float(best_native), unit) if best_native is not None else None
    except Exception:
        best_f = None
    try:
        metar_f = _native_to_f(float(metar_native), unit) if metar_native is not None else None
    except Exception:
        metar_f = None

    gap_f = abs(best_f - metar_f) if best_f is not None and metar_f is not None else None
    near_lock = horizon == "D+0" or (hours_left is not None and hours_left <= 18)
    near_lock_8h = bool(near_lock and hours_left is not None and hours_left <= 8)

    low = item.get("bucket_low")
    high = item.get("bucket_high")
    try:
        low_f = _native_to_f(float(low), unit) if low is not None else None
        high_f = _native_to_f(float(high), unit) if high is not None else None
    except Exception:
        low_f = high_f = None

    forecast_native = item.get("forecast_temp")
    try:
        forecast_f = _native_to_f(float(forecast_native), unit) if forecast_native is not None else None
    except Exception:
        forecast_f = None

    ensemble_std = snap.get("ensemble_std")
    try:
        ensemble_std_f = _delta_to_f(float(ensemble_std), unit) if ensemble_std is not None else None
    except Exception:
        ensemble_std_f = None

    return {
        "opened_at": opened_at,
        "unit": unit,
        "horizon_at_entry": horizon,
        "hours_left_at_entry": hours_left,
        "entry_best_f": best_f,
        "entry_metar_f": metar_f,
        "entry_model_metar_gap_f": gap_f,
        "entry_ensemble_std_f": ensemble_std_f,
        "near_lock_window": bool(near_lock),
        "near_lock_8h": near_lock_8h,
        "near_lock_metar_available": metar_f is not None,
        "near_lock_gap_risk": bool(near_lock_8h and gap_f is not None and gap_f > 3.0),
        "near_lock_metar_aligned": bool(near_lock_8h and gap_f is not None and gap_f <= 2.0),
        "bucket_low_f": low_f,
        "bucket_high_f": high_f,
        "forecast_temp_f": forecast_f,
        "raw_forecast_in_bucket": _bucket_value_in_range(forecast_f, low_f, high_f),
    }


def _augment_strategy_replay_record(record, fit, source_fit=None):
    bias = _fit_bias_for_calibration(fit)
    forecast_f = record.get("forecast_temp_f")
    adjusted = forecast_f - bias if forecast_f is not None else None
    record["bias_adjusted_forecast_f"] = round(adjusted, 2) if adjusted is not None else None
    record["bias_adjusted_in_bucket"] = _bucket_value_in_range(
        adjusted,
        record.get("bucket_low_f"),
        record.get("bucket_high_f"),
    )
    mos_adjusted = None
    if forecast_f is not None and fit and fit.get("mos_slope") is not None and fit.get("mos_intercept_f") is not None:
        try:
            mos_adjusted = float(fit.get("mos_slope")) * float(forecast_f) + float(fit.get("mos_intercept_f"))
        except Exception:
            mos_adjusted = None
    record["mos_adjusted_forecast_f"] = round(mos_adjusted, 2) if mos_adjusted is not None else None
    record["mos_adjusted_in_bucket"] = _bucket_value_in_range(
        mos_adjusted,
        record.get("bucket_low_f"),
        record.get("bucket_high_f"),
    )

    ensemble_std_f = record.get("entry_ensemble_std_f")
    dispersion_ratio = None
    if ensemble_std_f is not None and fit:
        historical_spread_f = max(3.0, float(fit.get("rmse_f") or 0) * 2.0, float(fit.get("mae_f") or 0) * 3.0)
        ensemble_spread_f = max(0.2, float(ensemble_std_f) * 2.0)
        dispersion_ratio = historical_spread_f / ensemble_spread_f
    record["historical_dispersion_ratio"] = round(dispersion_ratio, 2) if dispersion_ratio is not None else None
    record["cheap_underdispersed_tail"] = bool(
        record.get("horizon_at_entry") in ("D+1", "D+2")
        and dispersion_ratio is not None
        and dispersion_ratio > 1.6
        and float(record.get("entry_price") or 0) <= 0.14
    )

    unit = record.get("unit") or "F"
    default_sigma_f = 2.16 if unit == "C" else 2.0
    sigma_candidates = [1.5, default_sigma_f]
    if record.get("entry_ensemble_std_f") is not None:
        sigma_candidates.append(float(record.get("entry_ensemble_std_f") or 0))
    if fit:
        sigma_candidates.append(float(fit.get("mae_f") or 0))
        sigma_candidates.append(float(fit.get("rmse_f") or 0) * 0.75)
    if source_fit:
        sigma_candidates.append(float(source_fit.get("mae_f") or 0))
    sigma_f = max(sigma_candidates)
    calibrated_probability = _bucket_probability_f(
        adjusted,
        record.get("bucket_low_f"),
        record.get("bucket_high_f"),
        sigma_f,
    )
    entry_price = float(record.get("entry_price") or 0)
    calibrated_ev = _calc_ev(calibrated_probability, entry_price) if calibrated_probability is not None else None
    mos_probability = _bucket_probability_f(
        mos_adjusted,
        record.get("bucket_low_f"),
        record.get("bucket_high_f"),
        sigma_f,
    ) if mos_adjusted is not None else None
    mos_ev = _calc_ev(mos_probability, entry_price) if mos_probability is not None else None
    record["calibrated_sigma_f"] = round(sigma_f, 2)
    record["calibration_bias_f"] = round(bias, 2)
    record["calibrated_probability"] = round(calibrated_probability, 4) if calibrated_probability is not None else None
    record["calibrated_prob_edge"] = round(calibrated_probability - entry_price, 4) if calibrated_probability is not None else None
    record["calibrated_ev"] = round(calibrated_ev, 4) if calibrated_ev is not None else None
    record["mos_probability"] = round(mos_probability, 4) if mos_probability is not None else None
    record["mos_prob_edge"] = round(mos_probability - entry_price, 4) if mos_probability is not None else None
    record["mos_ev"] = round(mos_ev, 4) if mos_ev is not None else None
    record["calibrated_positive_edge"] = bool(
        calibrated_probability is not None
        and calibrated_ev is not None
        and calibrated_probability - entry_price >= 0.08
        and calibrated_ev >= 0.10
    )
    record["mos_positive_edge"] = bool(
        mos_probability is not None
        and mos_ev is not None
        and mos_probability - entry_price >= 0.08
        and mos_ev >= 0.10
    )
    record["city_fit_samples"] = int(fit.get("samples") or 0) if fit else 0
    record["source_fit_samples"] = int(source_fit.get("samples") or 0) if source_fit else 0
    return record


def _slice_row(name, records, kind):
    resolved = [r for r in records if r["resolved"]]
    wins = [r for r in resolved if r["result"] == "win"]
    pnl = round(sum(float(r.get("pnl") or 0) for r in records if r.get("pnl") is not None), 2)
    cost = sum(float(r.get("cost") or 0) for r in records)
    return {
        "kind": kind,
        "name": name,
        "count": len(records),
        "resolved": len(resolved),
        "wins": len(wins),
        "win_rate": (len(wins) / len(resolved)) if resolved else 0,
        "pnl": pnl,
        "roi": (pnl / cost) if cost else 0,
    }


def _policy_candidate_row(name, description, records):
    row = _slice_row(name, records, "policy_candidate")
    resolved = int(row.get("resolved") or 0)
    roi = float(row.get("roi") or 0)
    sample_factor = min(1.0, resolved / 20) if resolved else 0.0
    warnings = []
    if resolved == 0:
        warnings.append("settled_sample_missing")
    if resolved < 20:
        warnings.append("sample_low")
    if row["pnl"] <= 0:
        warnings.append("pnl_negative")
    if roi <= 0:
        warnings.append("roi_negative")
    if row["win_rate"] < 0.52:
        warnings.append("win_rate_low")
    row.update({
        "description": description,
        "score": round((roi * sample_factor) if resolved else -999.0, 4),
        "warnings": warnings,
    })
    return row


def _build_policy_candidates(completed):
    def price_between(low=None, high=None):
        def _inner(record):
            price = float(record.get("entry_price") or 0)
            if low is not None and price < low:
                return False
            if high is not None and price > high:
                return False
            return True
        return _inner

    def edge_threshold(ev_field, edge_field, ev_min=0.10, edge_min=0.08, sample_min=0, price_low=0.10, price_high=0.45):
        def _inner(record):
            if not bool(record.get("live_allowed_replay")):
                return False
            if not price_between(price_low, price_high)(record):
                return False
            if int(record.get("city_fit_samples") or 0) < sample_min:
                return False
            ev = record.get(ev_field)
            edge = record.get(edge_field)
            if ev is None or edge is None:
                return False
            return float(ev) >= ev_min and float(edge) >= edge_min
        return _inner

    def calibrated_threshold(ev_min=0.10, edge_min=0.08, sample_min=0, price_low=0.10, price_high=0.45):
        return edge_threshold("calibrated_ev", "calibrated_prob_edge", ev_min, edge_min, sample_min, price_low, price_high)

    def mos_threshold(ev_min=0.10, edge_min=0.08, sample_min=0, price_low=0.10, price_high=0.45):
        return edge_threshold("mos_ev", "mos_prob_edge", ev_min, edge_min, sample_min, price_low, price_high)

    candidates = [
        ("all_completed", "全部已完成仓位基线", lambda r: True),
        ("current_gate_allowed", "当前风控允许组", lambda r: bool(r.get("live_allowed_replay"))),
        ("price_3_45c", "只做 3-45c，排除极低价和高价", price_between(0.03, 0.45)),
        ("price_10_45c", "只做 10-45c，压制低价虚高 EV", price_between(0.10, 0.45)),
        ("price_10_25c", "只做 10-25c 中低价区间", price_between(0.10, 0.25)),
        ("fit_ok", "只做城市拟合可用样本", lambda r: r.get("fit_band") == "fit_ok"),
        ("not_fit_missing", "排除缺少城市拟合样本", lambda r: r.get("fit_band") != "fit_missing"),
        (
            "gate_allowed_10_45c",
            "当前允许组 + 只做 10-45c",
            lambda r: bool(r.get("live_allowed_replay")) and price_between(0.10, 0.45)(r),
        ),
        (
            "gate_allowed_fit_ok",
            "当前允许组 + 城市拟合可用",
            lambda r: bool(r.get("live_allowed_replay")) and r.get("fit_band") == "fit_ok",
        ),
        (
            "gate_allowed_fit_ok_10_45c",
            "当前允许组 + 拟合可用 + 10-45c",
            lambda r: bool(r.get("live_allowed_replay")) and r.get("fit_band") == "fit_ok" and price_between(0.10, 0.45)(r),
        ),
        (
            "avoid_d0_metar_gap",
            "排除 D+0 临近结算时模型与 METAR 背离 >3F",
            lambda r: not bool(r.get("near_lock_gap_risk")),
        ),
        (
            "gate_avoid_d0_metar_gap_10_45c",
            "当前允许组 + 10-45c + 排除临近 METAR 背离",
            lambda r: bool(r.get("live_allowed_replay")) and price_between(0.10, 0.45)(r) and not bool(r.get("near_lock_gap_risk")),
        ),
        (
            "near_lock_metar_aligned",
            "只做 D+0 <=8h 且模型与 METAR 差距 <=2F",
            lambda r: bool(r.get("near_lock_metar_aligned")),
        ),
        (
            "bias_adjusted_in_bucket",
            "偏差校正后预测仍落在目标温度桶",
            lambda r: bool(r.get("bias_adjusted_in_bucket")),
        ),
        (
            "gate_bias_adjusted_10_45c",
            "当前允许组 + 10-45c + 偏差校正仍落桶",
            lambda r: bool(r.get("live_allowed_replay")) and price_between(0.10, 0.45)(r) and bool(r.get("bias_adjusted_in_bucket")),
        ),
        (
            "cheap_underdispersed_tail",
            "D+1/D+2 低价尾部 + 历史波动大于 ensemble",
            lambda r: bool(r.get("cheap_underdispersed_tail")),
        ),
        (
            "calibrated_positive_edge",
            "校准概率后仍满足 EV/概率差",
            lambda r: bool(r.get("calibrated_positive_edge")),
        ),
        (
            "gate_calibrated_positive_10_45c",
            "当前允许组 + 10-45c + 校准后仍有优势",
            lambda r: bool(r.get("live_allowed_replay")) and price_between(0.10, 0.45)(r) and bool(r.get("calibrated_positive_edge")),
        ),
        (
            "mos_positive_edge",
            "MOS线性校正后仍满足 EV/概率差",
            lambda r: bool(r.get("mos_positive_edge")),
        ),
        (
            "gate_mos_positive_10_45c",
            "当前允许组 + 10-45c + MOS校正后仍有优势",
            lambda r: bool(r.get("live_allowed_replay")) and price_between(0.10, 0.45)(r) and bool(r.get("mos_positive_edge")),
        ),
    ]

    for sample_min in (0, 5, 10, 20):
        for ev_min, edge_min in ((0.10, 0.08), (0.25, 0.12), (0.50, 0.18)):
            name = f"cal_ev{int(ev_min * 100)}_edge{int(edge_min * 100)}_s{sample_min}"
            description = f"允许组 + 10-45c + 校准EV>={ev_min:.0%} + 概率差>={edge_min:.0%} + 城市样本>={sample_min}"
            candidates.append((name, description, calibrated_threshold(ev_min, edge_min, sample_min)))

    for sample_min in (0, 5, 10):
        for ev_min, edge_min in ((0.10, 0.08), (0.25, 0.12)):
            name = f"mos_ev{int(ev_min * 100)}_edge{int(edge_min * 100)}_s{sample_min}"
            description = f"允许组 + 10-45c + MOS EV>={ev_min:.0%} + 概率差>={edge_min:.0%} + 城市样本>={sample_min}"
            candidates.append((name, description, mos_threshold(ev_min, edge_min, sample_min)))

    for source in sorted({r.get("source") for r in completed if r.get("source")}):
        source_records = [r for r in completed if r.get("source") == source]
        if len(source_records) >= 3:
            candidates.append((f"source_{source}", f"只做 {source} 来源", lambda r, source=source: r.get("source") == source))

    rows = []
    for name, description, predicate in candidates:
        group = [r for r in completed if predicate(r)]
        if not group:
            continue
        rows.append(_policy_candidate_row(name, description, group))
    return sorted(rows, key=lambda row: (row["resolved"] > 0, row["score"], row["pnl"], row["resolved"]), reverse=True)


def _strategy_readiness(backtest):
    risk_slices = backtest.get("risk_slices") or []
    allowed = next((row for row in risk_slices if row.get("kind") == "gate" and row.get("name") == "gate_allowed"), {})
    blocked = next((row for row in risk_slices if row.get("kind") == "gate" and row.get("name") == "gate_blocked"), {})
    resolved = int(backtest.get("resolved_positions") or 0)
    allowed_resolved = int(allowed.get("resolved") or 0)
    allowed_pnl = float(allowed.get("pnl") or 0)
    allowed_roi = float(allowed.get("roi") or 0)
    allowed_win_rate = float(allowed.get("win_rate") or 0)
    blocked_roi = float(blocked.get("roi") or 0)
    brier = float(backtest.get("brier_score") or 0)
    reasons = []

    if resolved < 30:
        reasons.append("resolved_sample_below_30")
    if allowed_resolved < 20:
        reasons.append("allowed_sample_below_20")
    if allowed_pnl <= 0:
        reasons.append("allowed_group_pnl_negative")
    if allowed_roi <= 0:
        reasons.append("allowed_group_roi_negative")
    if allowed_win_rate < 0.52:
        reasons.append("allowed_win_rate_low")
    if allowed_resolved and blocked and allowed_roi <= blocked_roi:
        reasons.append("allowed_not_outperforming_blocked")
    if brier and brier > 0.25:
        reasons.append("brier_too_high")

    live_ready = not reasons
    if live_ready:
        status = "ready"
    elif "allowed_group_pnl_negative" in reasons or "allowed_group_roi_negative" in reasons:
        status = "blocked"
    else:
        status = "watch"

    return {
        "live_ready": live_ready,
        "status": status,
        "reasons": reasons,
        "resolved_positions": resolved,
        "allowed_resolved": allowed_resolved,
        "allowed_pnl": round(allowed_pnl, 2),
        "allowed_roi": round(allowed_roi, 4),
        "allowed_win_rate": round(allowed_win_rate, 4),
        "blocked_roi": round(blocked_roi, 4),
        "brier_score": round(brier, 4),
    }


def _historical_gate_replay(record, fit):
    reasons = []
    cautions = []
    if not fit:
        reasons.append("fit_missing")
    else:
        markets = int(fit.get("markets") or 0)
        if markets < 10:
            reasons.append("fit_independent_days_too_low")
        elif markets < 20:
            reasons.append("fit_independent_days_low")
        if int(fit.get("samples") or 0) < 10:
            cautions.append("fit_sample_low")
        if float(fit.get("mae_f") or 0) > 3.0:
            reasons.append("city_mae_high")
        if abs(float(fit.get("bias_f") or 0)) > 2.0:
            reasons.append("city_bias_high")

    price = float(record.get("entry_price") or 0)
    if price < 0.03:
        reasons.append("price_below_min")
    if price > 0.45:
        reasons.append("price_above_max")
    if 0 < price < 0.10 and not bool(record.get("cheap_underdispersed_tail")):
        reasons.append("low_price_tail_unverified")

    spread = record.get("spread")
    try:
        spread = float(spread) if spread is not None else None
    except Exception:
        spread = None
    if spread is None:
        cautions.append("spread_missing")
    elif spread > 0.03:
        reasons.append("spread_above_limit")
    elif price > 0 and price <= 0.10 and (spread >= 0.02 or (spread / price) >= 0.25):
        reasons.append("spread_cost_too_high")

    return {
        "live_allowed": not reasons,
        "risk_level": "blocked" if reasons else ("caution" if cautions else "eligible"),
        "block_reasons": reasons,
        "cautions": cautions,
    }


def _iter_position_records(markets):
    for market in markets:
        positions = []
        pos = market.get("position")
        if isinstance(pos, dict):
            positions.append((pos, False))
        history = market.get("position_history")
        if isinstance(history, list):
            positions.extend((item, True) for item in history if isinstance(item, dict))

        for item, archived in positions:
            if not item.get("market_id"):
                continue
            entry_features = _entry_snapshot_features(market, item)
            pnl = item.get("pnl")
            cost = float(item.get("cost") or 0)
            result = market.get("resolved_outcome")
            is_resolved = market.get("status") == "resolved" and result in ("win", "loss") and not archived
            if not is_resolved and pnl is not None:
                result = "win" if float(pnl or 0) > 0 else "loss"
            yield {
                "market_id": str(item.get("market_id") or ""),
                "city": market.get("city") or item.get("city") or "",
                "city_name": market.get("city_name") or "",
                "date": market.get("date") or "",
                "source": (item.get("forecast_src") or "unknown").upper(),
                "status": item.get("status") or market.get("status") or "",
                "entry_price": float(item.get("entry_price") or 0),
                "bid_at_entry": item.get("bid_at_entry"),
                "spread": item.get("spread"),
                "cost": cost,
                "p": item.get("p"),
                "ev": item.get("ev"),
                "forecast_temp": item.get("forecast_temp"),
                "bucket_low": item.get("bucket_low"),
                "bucket_high": item.get("bucket_high"),
                "pnl": float(pnl) if pnl is not None else None,
                "result": result,
                "resolved": is_resolved,
                "archived": archived,
                **entry_features,
            }


def _build_backtest_summary(markets):
    all_records = list(_iter_position_records(markets))
    records = [
        r for r in all_records
        if r["pnl"] is not None or (not r["archived"] and r["status"] == "open")
    ]
    temperature_fit = _build_temperature_fit(markets)
    fit_by_city = {row.get("city_key"): row for row in temperature_fit.get("cities", [])}
    fit_by_source = {str(row.get("source") or "").upper(): row for row in temperature_fit.get("sources", [])}
    for record in records:
        fit = fit_by_city.get(record.get("city")) or {}
        source_key = str(record.get("source") or "").upper()
        source_fit = fit_by_source.get(source_key) or fit_by_source.get("MODEL_BEST")
        _augment_strategy_replay_record(record, fit, source_fit)
        gate = _historical_gate_replay(record, fit)
        record["fit_band"] = _fit_band(fit)
        record["price_bucket"] = _price_bucket(record.get("entry_price"))
        record["live_allowed_replay"] = gate["live_allowed"]
        record["risk_level"] = gate["risk_level"]
        record["block_reasons"] = gate["block_reasons"]
        record["cautions"] = gate["cautions"]

    completed = [r for r in records if r["pnl"] is not None and r["cost"] > 0]
    resolved = [r for r in completed if r["resolved"]]
    wins = [r for r in resolved if r["result"] == "win"]
    total_pnl = round(sum(r["pnl"] for r in completed), 2)
    actual_returns = [r["pnl"] / r["cost"] for r in completed if r["cost"]]
    predicted_edges = [float(r["ev"]) for r in completed if r.get("ev") is not None]
    calibrated_edges = [float(r["calibrated_ev"]) for r in completed if r.get("calibrated_ev") is not None]
    mos_edges = [float(r["mos_ev"]) for r in completed if r.get("mos_ev") is not None]
    brier_values = []
    calibrated_brier_values = []
    mos_brier_values = []
    for r in resolved:
        y = 1.0 if r["result"] == "win" else 0.0
        if r.get("p") is not None:
            brier_values.append((float(r["p"]) - y) ** 2)
        if r.get("calibrated_probability") is not None:
            calibrated_brier_values.append((float(r["calibrated_probability"]) - y) ** 2)
        if r.get("mos_probability") is not None:
            mos_brier_values.append((float(r["mos_probability"]) - y) ** 2)

    buckets = {}
    for r in completed:
        name = _ev_bucket(r.get("ev"))
        bucket = buckets.setdefault(name, {"bucket": name, "count": 0, "resolved": 0, "wins": 0, "pnl": 0.0})
        bucket["count"] += 1
        bucket["pnl"] += r["pnl"]
        if r["resolved"]:
            bucket["resolved"] += 1
            if r["result"] == "win":
                bucket["wins"] += 1

    sources = {}
    for r in completed:
        source = r["source"]
        item = sources.setdefault(source, {"source": source, "count": 0, "resolved": 0, "wins": 0, "pnl": 0.0})
        item["count"] += 1
        item["pnl"] += r["pnl"]
        if r["resolved"]:
            item["resolved"] += 1
            if r["result"] == "win":
                item["wins"] += 1

    bucket_rows = []
    for bucket in buckets.values():
        resolved_count = bucket["resolved"]
        bucket_rows.append({
            **bucket,
            "pnl": round(bucket["pnl"], 2),
            "win_rate": (bucket["wins"] / resolved_count) if resolved_count else 0,
        })
    source_rows = []
    for item in sources.values():
        resolved_count = item["resolved"]
        source_rows.append({
            **item,
            "pnl": round(item["pnl"], 2),
            "win_rate": (item["wins"] / resolved_count) if resolved_count else 0,
        })

    slice_rows = []
    for allowed in (True, False):
        group = [r for r in completed if bool(r.get("live_allowed_replay")) is allowed]
        slice_rows.append(_slice_row("gate_allowed" if allowed else "gate_blocked", group, "gate"))

    for bucket_name in sorted({r.get("price_bucket") for r in completed if r.get("price_bucket")}):
        group = [r for r in completed if r.get("price_bucket") == bucket_name]
        slice_rows.append(_slice_row(bucket_name, group, "price_bucket"))

    for band in sorted({r.get("fit_band") for r in completed if r.get("fit_band")}):
        group = [r for r in completed if r.get("fit_band") == band]
        slice_rows.append(_slice_row(band, group, "fit_band"))

    for name, predicate in (
        ("calibrated_positive_edge", lambda r: bool(r.get("calibrated_positive_edge"))),
        ("mos_positive_edge", lambda r: bool(r.get("mos_positive_edge"))),
        ("bias_adjusted_in_bucket", lambda r: bool(r.get("bias_adjusted_in_bucket"))),
    ):
        group = [r for r in completed if predicate(r)]
        if group:
            slice_rows.append(_slice_row(name, group, "strategy_feature"))

    block_reasons = {}
    for r in completed:
        for reason in r.get("block_reasons") or []:
            block_reasons.setdefault(reason, []).append(r)
    block_reason_rows = [
        _slice_row(reason, group, "block_reason")
        for reason, group in block_reasons.items()
    ]

    summary = {
        "total_positions": len(records),
        "completed_positions": len(completed),
        "resolved_positions": len(resolved),
        "open_positions": len([r for r in records if not r["archived"] and r["status"] == "open"]),
        "wins": len(wins),
        "losses": len(resolved) - len(wins),
        "win_rate": (len(wins) / len(resolved)) if resolved else 0,
        "settlement_rate": (len(resolved) / len(records)) if records else 0,
        "total_pnl": total_pnl,
        "avg_actual_return": (sum(actual_returns) / len(actual_returns)) if actual_returns else 0,
        "avg_predicted_ev": (sum(predicted_edges) / len(predicted_edges)) if predicted_edges else 0,
        "avg_calibrated_ev": (sum(calibrated_edges) / len(calibrated_edges)) if calibrated_edges else 0,
        "avg_mos_ev": (sum(mos_edges) / len(mos_edges)) if mos_edges else 0,
        "brier_score": (sum(brier_values) / len(brier_values)) if brier_values else 0,
        "calibrated_brier_score": (sum(calibrated_brier_values) / len(calibrated_brier_values)) if calibrated_brier_values else 0,
        "mos_brier_score": (sum(mos_brier_values) / len(mos_brier_values)) if mos_brier_values else 0,
        "buckets": sorted(bucket_rows, key=lambda b: b["bucket"]),
        "sources": sorted(source_rows, key=lambda s: s["source"]),
        "risk_slices": sorted(slice_rows, key=lambda row: (row["kind"], row["name"])),
        "block_reasons": sorted(block_reason_rows, key=lambda row: row["pnl"]),
        "policy_candidates": _build_policy_candidates(completed),
        "notes": [
            "本复盘基于本地保存的模拟/纸面仓位，不是逐分钟盘口回放。",
            "风控回放使用当前城市拟合与盘口门槛反推历史仓位会被允许还是拦截，用来验证规则方向。",
            "样本少于 30 个已结算仓位时，胜率、Brier 和风控切片只能作观察，不能作为实盘放大依据。",
        ],
    }
    summary["strategy_readiness"] = _strategy_readiness(summary)
    return summary


def _metric_summary(records):
    if not records:
        return {
            "samples": 0,
            "mae_f": 0,
            "bias_f": 0,
            "decayed_bias_f": 0,
            "rmse_f": 0,
            "mos_slope": None,
            "mos_intercept_f": None,
            "mos_mae_f": None,
            "mos_rmse_f": None,
            "mos_improvement_f": None,
        }
    ordered = sorted(records, key=lambda r: (r.get("target_date") or "", r.get("timestamp") or ""))
    errors = [float(r["error_f"]) for r in ordered]
    abs_errors = [abs(e) for e in errors]
    decayed_bias = errors[0]
    alpha = 0.97
    for error in errors[1:]:
        decayed_bias = alpha * decayed_bias + (1.0 - alpha) * error
    summary = {
        "samples": len(records),
        "mae_f": round(sum(abs_errors) / len(abs_errors), 2),
        "bias_f": round(sum(errors) / len(errors), 2),
        "decayed_bias_f": round(decayed_bias, 2),
        "rmse_f": round((sum(e * e for e in errors) / len(errors)) ** 0.5, 2),
    }
    summary.update(_mos_summary(ordered, summary["mae_f"]))
    return summary


def _mos_summary(records, baseline_mae_f):
    pairs = [
        (float(r["forecast_f"]), float(r["actual_f"]))
        for r in records
        if r.get("forecast_f") is not None and r.get("actual_f") is not None
    ]
    if len(pairs) < 20:
        return {
            "mos_slope": None,
            "mos_intercept_f": None,
            "mos_mae_f": None,
            "mos_rmse_f": None,
            "mos_improvement_f": None,
        }
    xs = [x for x, _ in pairs]
    ys = [y for _, y in pairs]
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    variance = sum((x - x_mean) ** 2 for x in xs)
    if variance <= 1e-9:
        return {
            "mos_slope": None,
            "mos_intercept_f": None,
            "mos_mae_f": None,
            "mos_rmse_f": None,
            "mos_improvement_f": None,
        }
    slope = sum((x - x_mean) * (y - y_mean) for x, y in pairs) / variance
    intercept = y_mean - slope * x_mean
    mos_errors = [(slope * x + intercept) - y for x, y in pairs]
    mos_abs = [abs(e) for e in mos_errors]
    mos_mae = sum(mos_abs) / len(mos_abs)
    mos_rmse = (sum(e * e for e in mos_errors) / len(mos_errors)) ** 0.5
    return {
        "mos_slope": round(slope, 4),
        "mos_intercept_f": round(intercept, 2),
        "mos_mae_f": round(mos_mae, 2),
        "mos_rmse_f": round(mos_rmse, 2),
        "mos_improvement_f": round(float(baseline_mae_f or 0) - mos_mae, 2),
    }


def _fit_bias_for_calibration(fit):
    if not fit:
        return 0.0
    if fit.get("decayed_bias_f") is not None:
        return float(fit.get("decayed_bias_f") or 0.0)
    return float(fit.get("bias_f") or 0.0)


def _fit_trade_readiness(summary, market_count=0):
    samples = int(summary.get("samples") or 0)
    mae = float(summary.get("mae_f") or 0)
    bias_abs = abs(float(summary.get("decayed_bias_f", summary.get("bias_f") or 0) or 0))
    reasons = []
    hard_block = False

    if market_count < 10:
        reasons.append("fit_independent_days_too_low")
        hard_block = True
    elif market_count < 20:
        reasons.append("fit_independent_days_low")
    if samples < 6:
        reasons.append("fit_samples_too_low")
        hard_block = True
    elif samples < 15:
        reasons.append("fit_samples_low")
    if market_count < 3:
        reasons.append("fit_markets_low")
    if mae > 4.5:
        reasons.append("fit_mae_block")
        hard_block = True
    elif mae > 3.0:
        reasons.append("fit_mae_watch")
    if bias_abs > 3.5:
        reasons.append("fit_bias_block")
        hard_block = True
    elif bias_abs > 2.0:
        reasons.append("fit_bias_watch")

    score = max(0.0, min(1.0, 1.0 - (mae / 6.0)))
    score *= max(0.3, min(1.0, samples / 20.0))
    score *= max(0.2, min(1.0, market_count / 20.0))
    score *= max(0.4, min(1.0, 1.0 - (bias_abs / 6.0)))
    status = "blocked" if hard_block else ("watch" if reasons else "eligible")
    return {
        "fit_status": status,
        "fit_reasons": reasons,
        "trade_score": round(score, 3),
    }


def _build_temperature_fit(markets):
    records = []
    market_keys = set()
    eligible_market_keys = set()
    history_cache = merge_history_points(market_history_points(markets))
    for market in markets:
        snapshots = market.get("forecast_snapshots") or []
        if not snapshots:
            continue
        city = market.get("city") or ""
        date = market.get("date") or ""
        cached_history = next(
            (
                row for row in history_cache.get(city, [])
                if str(row.get("target_date") or "") == str(date)
                and row.get("actual_high") is not None
            ),
            None,
        )
        actual = market.get("actual_temp")
        actual_provider = market.get("actual_provider") or "unknown"
        actual_station = market.get("actual_station") or market.get("station") or ""
        truth_confidence = float(market.get("actual_confidence") or 0.0)
        calibration_tier = "live_truth" if bool(market.get("actual_calibration_eligible")) else "research_truth"
        reason_if_ineligible = market.get("actual_reason_if_ineligible") or "truth_not_high_confidence"
        if actual is None and cached_history:
            actual = cached_history.get("actual_high")
            actual_provider = cached_history.get("provider") or "history_cache"
            actual_station = cached_history.get("station_id") or actual_station
            truth_confidence = float(cached_history.get("source_confidence") or 0.0)
            calibration_tier = str(cached_history.get("calibration_tier") or "research_truth")
            reason_if_ineligible = "research_history_not_live_settlement_truth"
        if actual is None:
            continue
        try:
            actual_native = float(actual)
        except Exception:
            continue
        unit = market.get("unit") or "F"
        actual_f = _native_to_f(actual_native, unit) or actual_native
        market_keys.add(f"{city}:{date}")
        calibration_eligible = bool(_market_calibration_eligible(market)) or calibration_tier == "live_truth"
        if calibration_eligible:
            eligible_market_keys.add(f"{city}:{date}")
        for snap in snapshots:
            for source in ("best", "ecmwf", "hrrr", "metar"):
                forecast = snap.get(source)
                if forecast is None:
                    continue
                try:
                    forecast_native = float(forecast)
                except Exception:
                    continue
                forecast_f = _native_to_f(forecast_native, unit) or forecast_native
                error_native = forecast_native - actual_native
                error_f = forecast_f - actual_f
                ensemble_std = snap.get("ensemble_std")
                ensemble_std_f = _delta_to_f(ensemble_std, unit) if ensemble_std is not None else None
                records.append({
                    "city_key": city,
                    "city_name": market.get("city_name") or city,
                    "target_date": date,
                    "unit": unit,
                    "actual_provider": actual_provider,
                    "actual_station": actual_station,
                    "truth_confidence": truth_confidence,
                    "calibration_eligible": calibration_eligible,
                    "calibration_tier": "live_truth" if calibration_eligible else calibration_tier,
                    "reason_if_ineligible": "" if calibration_eligible else (
                        reason_if_ineligible
                    ),
                    "source": "model_best" if source == "best" else source.upper(),
                    "best_source": (snap.get("best_source") or "").upper(),
                    "timestamp": snap.get("ts"),
                    "horizon": snap.get("horizon"),
                    "hours_left": float(snap.get("hours_left") or 0),
                    "forecast": round(forecast_native, 2),
                    "actual": round(actual_native, 2),
                    "forecast_f": round(forecast_f, 2),
                    "actual_f": round(actual_f, 2),
                    "error": round(error_native, 2),
                    "error_f": round(error_f, 2),
                    "abs_error_f": round(abs(error_f), 2),
                    "ensemble_std": round(float(ensemble_std), 2) if ensemble_std is not None else None,
                    "ensemble_std_f": round(float(ensemble_std_f), 2) if ensemble_std_f is not None else None,
                })

    best_snapshot_records = [r for r in records if r["source"] == "model_best"]

    # Calibration must count independent weather days, not repeated scanner snapshots
    # or duplicate bucket markets for the same city/date. Use the observation closest
    # to a consistent D+1 reference point so the headline MAE is comparable over time.
    canonical_by_day = {}
    for record in best_snapshot_records:
        key = f"{record['city_key']}:{record['target_date']}"
        distance_to_reference = abs(float(record.get("hours_left") or 0) - 24.0)
        current = canonical_by_day.get(key)
        if current is None:
            canonical_by_day[key] = record
            continue
        current_distance = abs(float(current.get("hours_left") or 0) - 24.0)
        if distance_to_reference < current_distance:
            canonical_by_day[key] = record
        elif distance_to_reference == current_distance and (record.get("timestamp") or "") > (current.get("timestamp") or ""):
            canonical_by_day[key] = record

    best_records = list(canonical_by_day.values())
    eligible_best_records = [r for r in best_records if r.get("calibration_eligible")]
    by_city = {}
    for record in best_records:
        key = record["city_key"] or record["city_name"]
        by_city.setdefault(key, []).append(record)

    city_rows = []
    for key, items in by_city.items():
        summary = _metric_summary(items)
        latest = sorted(items, key=lambda r: r.get("timestamp") or "")[-1]
        market_count = len({f"{r['city_key']}:{r['target_date']}" for r in items})
        city_rows.append({
            "city_key": key,
            "city_name": latest["city_name"],
            "unit": latest["unit"],
            "markets": market_count,
            "latest_date": latest["target_date"],
            "latest_forecast": latest["forecast"],
            "latest_actual": latest["actual"],
            **summary,
            **_fit_trade_readiness(summary, market_count),
        })

    by_source = {}
    for record in records:
        by_source.setdefault(record["source"], []).append(record)
    source_rows = []
    for source, items in by_source.items():
        source_market_count = len({f"{r['city_key']}:{r['target_date']}" for r in items})
        source_summary = _metric_summary(items)
        source_rows.append({
            "source": source,
            "markets": source_market_count,
            **source_summary,
            **_fit_trade_readiness(source_summary, source_market_count),
        })

    near_lock_records = [
        r for r in records
        if r["source"] == "METAR" and r["hours_left"] <= 18
    ]
    dispersion_records = [
        r for r in best_records
        if r.get("horizon") in ("D+1", "D+2") and r.get("ensemble_std_f") is not None
    ]
    underdispersed = [
        r for r in dispersion_records
        if r["abs_error_f"] > max(1.0, float(r.get("ensemble_std_f") or 0) * 1.5)
    ]

    readiness_counts = dict(Counter(row["fit_status"] for row in city_rows))
    provider_counts = dict(Counter((r.get("actual_provider") or "unknown") for r in best_records))
    tier_counts = dict(Counter((r.get("calibration_tier") or "unknown") for r in best_records))
    ineligible_counts = dict(Counter((r.get("reason_if_ineligible") or "eligible") for r in best_records if not r.get("calibration_eligible")))
    return {
        "summary": {
            "markets": len(market_keys),
            "eligible_markets": len(eligible_market_keys),
            "eligible_samples": len(eligible_best_records),
            "observed_samples": len(best_records),
            "snapshot_samples": len(best_snapshot_records),
            "provider_counts": provider_counts,
            "tier_counts": tier_counts,
            "ineligible_counts": ineligible_counts,
            **_metric_summary(best_records),
        },
        "readiness_counts": {
            "eligible": int(readiness_counts.get("eligible", 0)),
            "watch": int(readiness_counts.get("watch", 0)),
            "blocked": int(readiness_counts.get("blocked", 0)),
        },
        "cities": sorted(city_rows, key=lambda row: row["mae_f"], reverse=True),
        "sources": sorted(source_rows, key=lambda row: row["mae_f"]),
        "records": sorted(best_records, key=lambda r: (r["target_date"], r["city_name"], r["hours_left"])),
        "strategy_summary": {
            "near_lock": {
                **_metric_summary(near_lock_records),
                "description": "D+0 <=18h METAR versus final actual temperature.",
            },
            "dispersion": {
                "samples": len(dispersion_records),
                "underdispersed_cases": len(underdispersed),
                "underdispersed_rate": round((len(underdispersed) / len(dispersion_records)) if dispersion_records else 0, 3),
                "description": "D+1/D+2 cases where actual error exceeded 1.5x ensemble std.",
            },
        },
        "notes": [
            "拟合页按城市和日期合并重复市场及扫描快照，选取最接近赛前 24 小时的预测作为独立天气日样本。",
            "原始 forecast_snapshots 仍保留用于审计和分时研究，但不会重复放大首页 MAE、Bias 和样本量。",
            "页面展示按城市/日期去重后的独立天气日；只有 calibration_eligible=true 的样本才应进入自动实盘校准。",
            "MAE/RMSE 统一折算为华氏度，便于跨 °C/°F 城市比较；表格仍展示原市场单位。",
            "下一步交易过滤应要求：样本数足够、城市 MAE 可控、数据源分歧低、盘口 spread/orderMinSize/tick size 合格。",
        ],
    }


def _hourly_merge_key(point: dict) -> str:
    target_date = str(point.get("target_date") or "")
    local_hour = point.get("local_hour")
    if not local_hour:
        timestamp = str(point.get("timestamp") or "")
        local_hour = timestamp[11:16] if len(timestamp) >= 16 and "T" in timestamp else timestamp
    return f"{target_date}:{local_hour}"


def _merge_hourly_points(forecast_points: list[dict], consensus_points: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for point in forecast_points:
        merged[_hourly_merge_key(point)] = dict(point)
    for point in consensus_points:
        key = _hourly_merge_key(point)
        base = merged.setdefault(key, {})
        base.update({k: v for k, v in point.items() if v is not None or k not in base})
        for field in ("best", "ensemble_mean", "ecmwf", "hrrr"):
            if base.get(field) is None and key in merged:
                base[field] = merged[key].get(field)
    return sorted(
        [point for point in merged.values() if point.get("target_date")],
        key=lambda point: (str(point.get("target_date") or ""), str(point.get("timestamp") or point.get("local_hour") or "")),
    )


def _recent_hourly_targets(limit_per_city: int = 10) -> dict[str, set[str]]:
    """Return recent city/date targets that already exist in the v3 database."""
    try:
        init_v3_db()
        with connect() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT LOWER(city) AS city, target_date, MAX(sort_key) AS sort_key
                    FROM (
                        SELECT
                            city,
                            target_date,
                            COALESCE(available_at, retrieved_at, created_at, '') AS sort_key
                        FROM forecast_runs
                        WHERE COALESCE(run_type, 'forecast') = 'forecast'
                        UNION ALL
                        SELECT
                            city,
                            target_date,
                            COALESCE(updated_at, created_at, valid_time, '') AS sort_key
                        FROM hourly_consensus
                    )
                    WHERE city IS NOT NULL
                      AND TRIM(city) != ''
                      AND target_date IS NOT NULL
                      AND TRIM(target_date) != ''
                    GROUP BY LOWER(city), target_date
                    ORDER BY target_date DESC, sort_key DESC
                    """
                ).fetchall()
            ]
    except Exception:
        return {}

    valid_cities = set(SETTLEMENT_REGISTRY.keys())
    targets: dict[str, set[str]] = {}
    for row in rows:
        city = str(row.get("city") or "").strip().lower()
        target_date = str(row.get("target_date") or "").strip()
        if not city or not target_date or city not in valid_cities:
            continue
        city_targets = targets.setdefault(city, set())
        if len(city_targets) >= limit_per_city:
            continue
        city_targets.add(target_date)
    return targets


def _hourly_cache_for_targets(targets: dict[str, set[str]], *, allow_forecast_fallback: bool = False) -> dict[str, list[dict]]:
    if not targets:
        return {}
    consensus_cache: dict[str, list[dict]]
    try:
        consensus_cache = hourly_consensus_points(targets)
    except Exception:
        consensus_cache = {}

    hourly_cache: dict[str, list[dict]] = {}
    if allow_forecast_fallback:
        forecast_targets: dict[str, set[str]] = {}
        for city, dates in targets.items():
            city_points = list(consensus_cache.get(city) or [])
            for target_date in dates:
                date_points = [point for point in city_points if str(point.get("target_date") or "") == str(target_date)]
                has_forecast = any(_point_forecast_value(point) is not None for point in date_points)
                if not date_points or not has_forecast:
                    forecast_targets.setdefault(city, set()).add(target_date)

        try:
            hourly_cache = forecast_hourly_points(forecast_targets) if forecast_targets else {}
        except Exception:
            hourly_cache = {}

    cities = set(hourly_cache.keys()) | set(consensus_cache.keys())
    return {
        city: _merge_hourly_points(
            list(hourly_cache.get(city) or []),
            list(consensus_cache.get(city) or []),
        )
        for city in cities
    }


def _build_weather_city_series(markets):
    """Compact chart-friendly forecast history by city for the dashboard."""
    history_cache = merge_history_points(market_history_points(markets))
    hourly_targets = {
        city: set(dates)
        for city, dates in _recent_hourly_targets().items()
    }
    for market in markets:
        city_key = market.get("city") or ""
        target_date = market.get("date") or ""
        if city_key and target_date:
            hourly_targets.setdefault(city_key, set()).add(target_date)
    by_city = {row["city_key"]: row for row in _registry_city_series(hourly_targets)}
    for market in markets:
        city_key = market.get("city") or ""
        if not city_key:
            continue
        unit = market.get("unit") or "F"
        city = by_city.setdefault(
            city_key,
            {
                "city_key": city_key,
                "city_name": market.get("city_name") or city_key,
                "station_id": market.get("station") or market.get("actual_station") or "",
                "unit": unit,
                "forecast_points": [],
                "history_points": [],
                "hourly_points": [],
                "humidity_status": "not_collected",
            },
        )
        city["unit"] = unit
        city["history_points"] = list(history_cache.get(city_key) or city.get("history_points") or [])
        for snap in market.get("forecast_snapshots") or []:
            ts = snap.get("ts")
            if not ts:
                continue
            point = {
                "timestamp": ts,
                "target_date": market.get("date") or "",
                "horizon": snap.get("horizon") or "",
                "best": snap.get("best"),
                "ecmwf": snap.get("ecmwf"),
                "hrrr": snap.get("hrrr"),
                "metar": snap.get("metar"),
                "ensemble_mean": snap.get("ensemble_mean"),
                "ensemble_std": snap.get("ensemble_std"),
                "humidity": snap.get("humidity") or snap.get("relative_humidity"),
                "cloud_cover": snap.get("cloud_cover"),
                "precipitation": snap.get("precipitation"),
                "precipitation_probability": snap.get("precipitation_probability"),
                "wind_speed": snap.get("wind_speed") or snap.get("wind_speed_10m"),
                "wind_direction": snap.get("wind_direction") or snap.get("wind_direction_10m"),
                "pressure": snap.get("pressure") or snap.get("pressure_msl") or snap.get("surface_pressure"),
                "dew_point": snap.get("dew_point") or snap.get("dew_point_2m"),
                "condition": snap.get("condition") or snap.get("weather") or snap.get("weather_description"),
                "source": snap.get("best_source") or "",
            }
            city["forecast_points"].append(point)

    rows = []
    for city in by_city.values():
        points = sorted(city["forecast_points"], key=lambda p: p.get("timestamp") or "")
        deduped = {}
        for point in points:
            deduped[f"{point.get('timestamp')}:{point.get('target_date')}"] = point
        points = list(deduped.values())[-80:]
        history_points = sorted(city.get("history_points") or [], key=lambda p: p.get("target_date") or "")[-120:]
        hourly_points = sorted(city.get("hourly_points") or [], key=lambda p: p.get("timestamp") or "")[-240:]
        if not points and not history_points and not hourly_points:
            continue
        latest = points[-1] if points else (hourly_points[-1] if hourly_points else {})
        latest_metar_point = next((point for point in reversed(hourly_points) if point.get("metar") is not None), {})
        humidity_values = [p.get("humidity") for p in points if p.get("humidity") is not None]
        humidity_values += [p.get("humidity") for p in hourly_points if p.get("humidity") is not None]
        humidity_values += [p.get("humidity_mean") for p in history_points if p.get("humidity_mean") is not None]
        city["forecast_points"] = points
        city["history_points"] = history_points
        city["hourly_points"] = hourly_points
        city["points"] = points
        city["latest_best"] = latest.get("best")
        city["latest_metar"] = latest_metar_point.get("metar")
        city["latest_source"] = latest.get("source")
        city["latest_timestamp"] = latest.get("timestamp")
        city["humidity_status"] = "available" if humidity_values else "not_collected"
        city["history_count"] = max(
            len(history_points),
            sum(1 for point in hourly_points if point.get("historical") is not None),
        )
        city["forecast_count"] = len(points) + sum(
            1 for point in hourly_points if _point_forecast_value(point) is not None
        )
        city["hourly_count"] = len(city["hourly_points"])
        rows.append(city)
    return sorted(rows, key=lambda row: row.get("city_name") or "")


def _registry_city_series(targets: dict[str, set[str]] | None = None):
    """Lightweight city index used before the first manual data refresh."""
    hourly_by_city = _hourly_cache_for_targets(targets if targets is not None else _recent_hourly_targets())
    station_rows = {str(row.get("city_key") or row.get("city") or ""): row for row in list_stations()}
    latest_fetch = _latest_fetch_by_city()
    rows = []
    for profile in SETTLEMENT_REGISTRY.values():
        station_row = station_rows.get(profile.city) or {}
        hourly_points = sorted(
            list(hourly_by_city.get(profile.city) or []),
            key=lambda point: str(point.get("timestamp") or point.get("local_hour") or ""),
        )[-240:]
        latest = hourly_points[-1] if hourly_points else {}
        latest_metar_point = next((point for point in reversed(hourly_points) if point.get("metar") is not None), {})
        humidity_values = [p.get("humidity") for p in hourly_points if p.get("humidity") is not None]
        forecast_count = sum(1 for point in hourly_points if _point_forecast_value(point) is not None)
        rows.append({
            "city_key": profile.city,
            "city_name": profile.city_name,
            "station_id": profile.station_id,
            "station_name": profile.station_name,
            "unit": profile.unit,
            "region": station_row.get("region") or profile.region,
            "display_enabled": bool(station_row.get("display_enabled", True)),
            "city_scope": station_row.get("city_scope") or profile.city_scope,
            "enabled": bool(station_row.get("enabled")),
            "tier": int(station_row.get("tier") or 9),
            "settlement_station_id": station_row.get("settlement_station_id") or profile.station_id,
            "settlement_station_name": station_row.get("settlement_station_name") or profile.station_name,
            "settlement_rule_verified_at": station_row.get("settlement_rule_verified_at") or "",
            "settlement_timezone": station_row.get("settlement_timezone") or station_row.get("timezone") or profile.timezone,
            "settlement_unit": station_row.get("settlement_unit") or station_row.get("unit") or profile.unit,
            "settlement_time_basis": station_row.get("settlement_time_basis") or "local_day",
            "primary_settlement_source": station_row.get("primary_settlement_source") or "",
            "verification_status": station_row.get("verification_status") or "provisional",
            "last_refreshed_at": latest_fetch.get(profile.city),
            "latest_best": _point_forecast_value(latest) if latest else None,
            "latest_metar": latest_metar_point.get("metar") if latest_metar_point else None,
            "latest_source": latest.get("source") or latest.get("forecast_source") if latest else None,
            "latest_timestamp": latest.get("timestamp") if latest else None,
            "humidity_status": "available" if humidity_values else "not_collected",
            "history_count": sum(1 for point in hourly_points if point.get("historical") is not None),
            "forecast_count": forecast_count,
            "hourly_count": len(hourly_points),
            "history_points": [],
            "forecast_points": [],
            "hourly_points": hourly_points,
            "points": [],
        })
    return sorted(rows, key=lambda row: (row.get("city_name") or "").lower())


def _latest_fetch_by_city() -> dict[str, str]:
    try:
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT city, MAX(COALESCE(finished_at, created_at)) AS last_refreshed_at
                FROM data_fetch_logs
                WHERE city IS NOT NULL
                  AND city != ''
                  AND UPPER(COALESCE(status, '')) IN ('OK', 'WARN')
                GROUP BY city
                """
            ).fetchall()
        return {str(row["city"]): str(row["last_refreshed_at"]) for row in rows if row["last_refreshed_at"]}
    except Exception:
        return {}


def _distribution_item_count(distribution) -> int:
    if not isinstance(distribution, dict):
        return 0
    for key in ("items", "buckets", "distribution"):
        value = distribution.get(key)
        if isinstance(value, list):
            return len(value)
    return 0


def _distribution_items(distribution) -> list[dict]:
    if not isinstance(distribution, dict):
        return []
    for key in ("items", "buckets", "distribution"):
        value = distribution.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _distribution_bucket_label(item: dict) -> str:
    for key in ("label", "bucket", "bucket_label", "question"):
        value = item.get(key)
        if value:
            return str(value)
    low = _float_or_none(item.get("bucket_low"))
    high = _float_or_none(item.get("bucket_high"))
    if low is None and high is None:
        return "--"
    if low is not None and low <= -900 and high is not None:
        return f"{high:g} or below"
    if high is not None and high >= 900 and low is not None:
        return f"{low:g} or above"
    if low is not None and high is not None:
        return f"{low:g}-{high:g}"
    return str(low if low is not None else high)


def _probability_bucket_summary(city_signals: list[dict]) -> dict:
    top_buckets: list[dict] = []
    total_items = 0
    normalized_count = 0
    actionable_count = 0
    signal_count = len(city_signals)

    for signal in city_signals:
        distribution = signal.get("distribution")
        items = _distribution_items(distribution)
        if items and isinstance(distribution, dict) and distribution.get("normalized"):
            normalized_count += 1
        if signal.get("actionable"):
            actionable_count += 1
        total_items += len(items)
        for item in items:
            probability = _float_or_none(item.get("probability"))
            if probability is None:
                probability = _float_or_none(item.get("probability_raw"))
            top_buckets.append({
                "bucket": _distribution_bucket_label(item),
                "probability": probability,
                "ask": _float_or_none(item.get("ask")),
                "bid": _float_or_none(item.get("bid")),
                "edge": _float_or_none(item.get("probability_edge") if item.get("probability_edge") is not None else item.get("ev")),
                "market_id": item.get("market_id") or signal.get("market_id"),
                "signal_id": signal.get("id"),
                "is_signal": bool(item.get("is_signal")),
                "actionable": bool(signal.get("actionable")),
            })

    top_buckets.sort(key=lambda row: (row.get("probability") is not None, row.get("probability") or -1), reverse=True)
    best = top_buckets[0] if top_buckets else {}
    return {
        "signal_count": signal_count,
        "bucket_count": total_items,
        "normalized_count": normalized_count,
        "actionable_signal_count": actionable_count,
        "highest_bucket": best.get("bucket"),
        "highest_probability": best.get("probability"),
        "strict_matching_required": True,
        "source": "signal.distribution",
        "top_buckets": top_buckets[:10],
    }


def _bucket_is_open_tail(low, high, label: str = "") -> bool:
    text = str(label or "").lower()
    if any(term in text for term in ("or below", "or above", "below", "above", "999", "-999")):
        return True
    return (low is not None and low <= -900) or (high is not None and high >= 900)


def _market_bucket_summary(city_signals: list[dict]) -> dict:
    reason_counter: Counter[str] = Counter()
    top_executable: list[dict] = []
    top_blocked: list[dict] = []
    signal_count = len(city_signals)
    bucket_count = 0
    matched_bucket_count = 0
    open_tail_count = 0
    low_price_tail_count = 0
    missing_price_count = 0
    high_spread_count = 0
    stale_book_count = 0
    actionable_signal_count = 0
    paper_allowed_count = 0
    live_allowed_count = 0

    for signal in city_signals:
        distribution = signal.get("distribution")
        items = _distribution_items(distribution)
        bucket_count += len(items)
        if signal.get("actionable"):
            actionable_signal_count += 1

        price = _float_or_none(signal.get("limit_price"))
        if price is None:
            price = _float_or_none(signal.get("market_probability"))
        bid = _float_or_none(signal.get("bid_price"))
        spread = _float_or_none(signal.get("spread"))
        if spread is None and price is not None and bid is not None:
            spread = max(0.0, price - bid)
        edge = _float_or_none(signal.get("probability_edge"))
        if edge is None:
            edge = _float_or_none(signal.get("edge"))

        decision = signal.get("decision") or {}
        decision_reasons = list(decision.get("reasons") or [])
        live_reasons = list(signal.get("live_block_reasons") or [])
        cautions = list(decision.get("cautions") or []) + list(signal.get("live_cautions") or [])
        reasons = []
        for reason in decision_reasons + live_reasons:
            if reason and reason not in reasons:
                reasons.append(str(reason))
        if not signal.get("actionable"):
            reasons.append(f"status_{signal.get('status') or 'inactive'}")
        if price is None:
            reasons.append("price_missing")
        if spread is not None and (spread > 0.03 or (price is not None and price <= 0.10 and (spread >= 0.02 or spread / max(price, 0.001) >= 0.25))):
            reasons.append("spread_cost_too_high")

        paper_allowed = bool(decision.get("paper_allowed")) and signal.get("actionable") and price is not None
        live_allowed = bool(signal.get("live_allowed") or decision.get("live_allowed"))
        if paper_allowed:
            paper_allowed_count += 1
        if live_allowed:
            live_allowed_count += 1

        if price is None:
            missing_price_count += 1
        if spread is not None and (spread > 0.03 or "spread_above_limit" in reasons or "spread_cost_too_high" in reasons):
            high_spread_count += 1
        if any("stale" in reason or "orderbook" in reason for reason in reasons + cautions):
            stale_book_count += 1
        for reason in reasons:
            reason_counter[reason] += 1

        signal_bucket_label = str(signal.get("bucket_label") or "")
        matched_item = None
        for item in items:
            label = _distribution_bucket_label(item)
            low = _float_or_none(item.get("bucket_low"))
            high = _float_or_none(item.get("bucket_high"))
            item_price = _float_or_none(item.get("ask"))
            if _bucket_is_open_tail(low, high, label):
                open_tail_count += 1
            if item_price is not None and item_price < 0.10:
                low_price_tail_count += 1
            if item.get("is_signal"):
                matched_bucket_count += 1
                matched_item = item
        if not items and _bucket_is_open_tail(None, None, signal_bucket_label):
            open_tail_count += 1
        if price is not None and price < 0.10:
            low_price_tail_count += 1

        row = {
            "signal_id": signal.get("id"),
            "market_id": signal.get("market_id"),
            "event_url": signal.get("event_url"),
            "bucket": _distribution_bucket_label(matched_item) if matched_item else signal_bucket_label,
            "price": price,
            "bid": bid,
            "spread": spread,
            "edge": edge,
            "paper_allowed": paper_allowed,
            "live_allowed": live_allowed,
            "reasons": reasons[:5],
        }
        if paper_allowed and not reasons:
            top_executable.append(row)
        elif reasons:
            top_blocked.append(row)

    top_executable.sort(key=lambda row: (row.get("edge") is not None, row.get("edge") or -999), reverse=True)
    return {
        "signal_count": signal_count,
        "bucket_count": bucket_count,
        "matched_bucket_count": matched_bucket_count,
        "actionable_signal_count": actionable_signal_count,
        "paper_allowed_count": paper_allowed_count,
        "live_allowed_count": live_allowed_count,
        "blocked_signal_count": max(0, signal_count - paper_allowed_count),
        "open_tail_count": open_tail_count,
        "low_price_tail_count": low_price_tail_count,
        "missing_price_count": missing_price_count,
        "high_spread_count": high_spread_count,
        "stale_book_count": stale_book_count,
        "strict_matching_required": True,
        "ready": bucket_count > 0 or signal_count > 0,
        "reason_counts": [
            {"reason": reason, "count": count}
            for reason, count in reason_counter.most_common(10)
        ],
        "top_executable": top_executable[:5],
        "top_blocked": top_blocked[:5],
    }


def _row_matches_text(row: dict, terms: list[str]) -> bool:
    text = _json_compact(row).lower()
    return any(term and term.lower() in text for term in terms)


def _float_or_none(value):
    try:
        if value is None or value == "":
            return None
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return None
        return number
    except Exception:
        return None


def _point_forecast_value(point: dict) -> float | None:
    for key in ("best", "ensemble_mean", "ecmwf", "hrrr"):
        number = _float_or_none(point.get(key))
        if number is not None:
            return number
    return None


def _pearson_r(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 2:
        return None
    observed = [pair[0] for pair in pairs]
    forecast = [pair[1] for pair in pairs]
    mean_observed = sum(observed) / len(observed)
    mean_forecast = sum(forecast) / len(forecast)
    numerator = sum((o - mean_observed) * (f - mean_forecast) for o, f in pairs)
    observed_var = sum((o - mean_observed) ** 2 for o in observed)
    forecast_var = sum((f - mean_forecast) ** 2 for f in forecast)
    denominator = math.sqrt(observed_var * forecast_var)
    if denominator <= 0:
        return None
    return max(-1.0, min(1.0, numerator / denominator))


def _diff_stats_summary(chart_points: list[dict], history_points: list[dict]) -> dict:
    def hour_bucket(point: dict, *timestamp_keys: str) -> str:
        hour = str(point.get("local_hour") or "")
        if hour:
            return hour[:2] + ":00" if len(hour) >= 2 and hour[:2].isdigit() else hour
        for key in timestamp_keys:
            timestamp = str(point.get(key) or "")
            if len(timestamp) >= 13 and "T" in timestamp:
                return f"{timestamp[11:13]}:00"
            if timestamp:
                return timestamp
        return ""

    pairs: list[tuple[float, float]] = []
    deltas: list[float] = []
    rows: list[dict] = []
    metar_hours = set()
    forecast_hours = set()
    historical_hours = set()
    for point in chart_points:
        hour = hour_bucket(point, "timestamp")
        observed = _float_or_none(point.get("metar"))
        forecast = _point_forecast_value(point)
        if observed is not None:
            metar_hours.add(hour)
        if forecast is not None:
            forecast_hours.add(hour)
        if observed is None or forecast is None:
            continue
        delta = observed - forecast
        pairs.append((observed, forecast))
        deltas.append(delta)
        rows.append({
            "timestamp": point.get("timestamp"),
            "local_hour": hour,
            "observed": observed,
            "forecast": forecast,
            "delta": round(delta, 3),
            "source": point.get("source") or "hourly",
        })
    for point in history_points:
        hour = hour_bucket(point, "timestamp", "observed_at")
        if hour:
            historical_hours.add(hour)
    count = len(deltas)
    mae = (sum(abs(delta) for delta in deltas) / count) if count else None
    bias = (sum(deltas) / count) if count else None
    pearson = _pearson_r(pairs)
    overlap_count = len(metar_hours & forecast_hours)
    possible_overlap = max(len(metar_hours), len(forecast_hours), 1)
    hist_metar_overlap = len(historical_hours & metar_hours) if historical_hours and metar_hours else 0
    hist_metar_possible = max(len(historical_hours), len(metar_hours), 1) if (historical_hours or metar_hours) else 0
    return {
        "count": count,
        "avg_delta": round(bias, 3) if bias is not None else None,
        "mae": round(mae, 3) if mae is not None else None,
        "pearson_r": round(pearson, 4) if pearson is not None else None,
        "metar_hours": len(metar_hours),
        "forecast_hours": len(forecast_hours),
        "overlap_count": overlap_count,
        "overlap_ratio": round(overlap_count / possible_overlap, 4) if possible_overlap else None,
        "historical_metar_overlap_count": hist_metar_overlap,
        "historical_metar_overlap_ratio": (
            round(hist_metar_overlap / hist_metar_possible, 4)
            if hist_metar_possible
            else None
        ),
        "rows": rows[:100],
    }


def _city_date_evidence_modules(city: dict, target_date: str, signals: list[dict], fetch_log: list[dict]) -> dict:
    hourly_points = [p for p in city.get("hourly_points") or [] if str(p.get("target_date") or "") == target_date]
    forecast_points = [p for p in city.get("forecast_points") or [] if str(p.get("target_date") or "") == target_date]
    history_points = [p for p in city.get("history_points") or [] if str(p.get("target_date") or "") == target_date]
    chart_points = hourly_points or forecast_points
    metar_rows = [p for p in chart_points if p.get("metar") is not None]
    forecast_rows = [
        p for p in chart_points
        if p.get("best") is not None
        or p.get("ecmwf") is not None
        or p.get("hrrr") is not None
        or p.get("ensemble_mean") is not None
    ]
    diff_rows = [
        p for p in chart_points
        if p.get("metar") is not None
        and (
            p.get("best") is not None
            or p.get("ecmwf") is not None
            or p.get("hrrr") is not None
            or p.get("ensemble_mean") is not None
        )
    ]
    city_signals = [
        signal for signal in signals
        if str(signal.get("city_key") or "") == str(city.get("city_key") or "")
        and str(signal.get("target_date") or "") == target_date
    ]
    terms = [str(city.get("city_key") or ""), str(city.get("city_name") or ""), target_date]
    log_rows = [row for row in fetch_log if _row_matches_text(row, terms)]
    bucket_count = sum(_distribution_item_count(signal.get("distribution")) for signal in city_signals)
    diff_summary = _diff_stats_summary(chart_points, history_points)
    probability_summary = _probability_bucket_summary(city_signals)
    market_summary = _market_bucket_summary(city_signals)

    return {
        "hourly_temperature": {
            "chart": "LineChart",
            "rows": len(chart_points),
            "series": ["Real METAR", "Model Forecast", "Diff", "Cloud Cover %"],
            "empty_state": "No hourly forecast rows for this city/date.",
            "ready": bool(chart_points),
        },
        "daily_max_prediction": {
            "engine": "DEB/Gaussian-compatible",
            "signals": len(city_signals),
            "probability_summary": probability_summary,
            "empty_state": "No prediction yet.",
            "ready": bool(city_signals),
        },
        "probability_buckets": {
            "rows": bucket_count,
            "source": "signal.distribution",
            "probability_summary": probability_summary,
            "empty_state": "No probability buckets yet.",
            "ready": bucket_count > 0,
        },
        "forecast": {
            "rows": len(forecast_rows),
            "table": "Forecast Data",
            "empty_state": "No forecast data for this date.",
            "ready": bool(forecast_rows),
        },
        "metar": {
            "rows": len(metar_rows),
            "table": "METAR Observations",
            "empty_state": "No METAR observations for this date.",
            "ready": bool(metar_rows),
        },
        "historical": {
            "rows": len(history_points),
            "table": "Historical Observations",
            "empty_state": "No historical observations for this date.",
            "ready": bool(history_points),
        },
        "diff_stats": {
            "rows": len(diff_rows),
            "formula": "Observed - Forecast",
            "summary": diff_summary,
            "empty_state": "No diff stats for this date.",
            "ready": bool(diff_rows),
        },
        "fetch_log": {
            "rows": len(log_rows),
            "table": "Fetch Log (last 100)",
            "empty_state": "No log entries.",
            "ready": bool(log_rows),
        },
        "market_buckets": {
            "signals": len(city_signals),
            "buckets": bucket_count,
            "strict_matching_required": True,
            "probability_summary": probability_summary,
            "market_summary": market_summary,
            "ready": bucket_count > 0,
        },
    }


def _build_city_evidence_payload(city_series: list[dict], weather_signals: list[dict], fetch_log: list[dict]) -> list[dict]:
    """PolyWX-style city/date evidence contract shared by dashboard and signals."""
    signals_by_city: dict[str, list[dict]] = {}
    for signal in weather_signals:
        signals_by_city.setdefault(str(signal.get("city_key") or ""), []).append(signal)

    payload = []
    for city in city_series:
        city_key = str(city.get("city_key") or "")
        if not city_key:
            continue
        date_keys = set()
        for collection, field in (
            ("hourly_points", "target_date"),
            ("forecast_points", "target_date"),
            ("history_points", "target_date"),
        ):
            for row in city.get(collection) or []:
                value = row.get(field)
                if value:
                    date_keys.add(str(value))
        for signal in signals_by_city.get(city_key, []):
            if signal.get("target_date"):
                date_keys.add(str(signal.get("target_date")))

        date_payloads = []
        for target_date in sorted(date_keys, reverse=True)[:14]:
            modules = _city_date_evidence_modules(
                city,
                target_date,
                signals_by_city.get(city_key, []),
                fetch_log,
            )
            ready_count = sum(1 for module in modules.values() if module.get("ready"))
            date_payloads.append({
                "target_date": target_date,
                "ready_modules": ready_count,
                "module_count": len(modules),
                "tabs": ["Forecast", "METAR", "Historical", "Diff Stats", "Fetch Log"],
                "modules": modules,
            })

        payload.append({
            "city_key": city_key,
            "city_name": city.get("city_name") or city_key,
            "station_id": city.get("station_id") or "",
            "unit": city.get("unit") or "F",
            "generated_from": "weather_city_series + weather_signals + fetch_log",
            "data_sources": ["Forecast", "METAR", "Historical", "Market", "Fetch Log"],
            "dates": date_payloads,
            "latest_date": date_payloads[0]["target_date"] if date_payloads else None,
            "latest_ready_modules": date_payloads[0]["ready_modules"] if date_payloads else 0,
        })
    return sorted(payload, key=lambda row: row.get("city_name") or "")


def _city_evidence_matches(row: dict, city: str) -> bool:
    city_lower = city.lower().strip()
    city_key = str(row.get("city_key") or "").lower()
    city_name = str(row.get("city_name") or "").lower().replace(" ", "-")
    station_id = str(row.get("station_id") or "").lower()
    candidates = {
        city_key,
        city_name,
        station_id,
        f"{city_key}-{station_id}" if station_id else city_key,
        f"{city_name}-{station_id}" if station_id else city_name,
    }
    return city_lower in candidates


def _strategy_diagnostics(signal, raw_signal, market, fit):
    tags = []
    notes = []
    score_parts = []
    near_lock = None
    unit = (market or {}).get("unit") or ("F" if (signal.get("bucket_label") or "").endswith("F") else "C")
    latest = _latest_snapshot(market or {}, "forecast_snapshots")
    horizon = signal.get("horizon") or latest.get("horizon") or raw_signal.get("horizon") or ""
    limit_price = float(signal.get("limit_price") or 0)
    hours_left = latest.get("hours_left")
    try:
        hours_left = float(hours_left) if hours_left is not None else None
    except Exception:
        hours_left = None

    if horizon == "D+0" or (hours_left is not None and hours_left <= 18):
        metar = latest.get("metar")
        best = latest.get("best")
        if metar is not None and hours_left is not None:
            try:
                metar_value = float(metar)
                best_value = float(best) if best is not None else metar_value
                remaining_native = max(0.0, best_value - metar_value)
                near_lock = {
                    "hours_left": round(hours_left, 2),
                    "observed_temp": round(metar_value, 2),
                    "model_best": round(best_value, 2),
                    "remaining_potential": round(remaining_native, 2),
                }
                if hours_left <= 18:
                    tags.append("near_lock_watch")
                    score_parts.append(0.2)
                    notes.append(f"NEAR-LOCK watch: {hours_left:.1f}h left with METAR {metar_value:.1f}{unit}.")
                if hours_left <= 8 and remaining_native <= (1.0 if unit == "F" else 0.6):
                    tags.append("near_lock_strong")
                    score_parts.append(0.35)
                    notes.append("METAR suggests limited remaining upside; check exact bucket before buying.")
            except Exception:
                notes.append("NEAR-LOCK data present but could not be parsed.")
        elif horizon == "D+0":
            tags.append("near_lock_missing_metar")
            notes.append("D+0 market without usable METAR; do not treat as near-lock.")

    ensemble_std = raw_signal.get("ensemble_std")
    dispersion_ratio = None
    fit_rmse = fit.get("rmse_f")
    fit_mae = fit.get("mae_f")
    if ensemble_std is not None and fit:
        try:
            ensemble_std_f = max(0.1, _delta_to_f(float(ensemble_std), unit) or 0.1)
            historical_spread_f = max(3.0, float(fit_rmse or 0) * 2.0, float(fit_mae or 0) * 3.0)
            ensemble_spread_f = max(0.2, ensemble_std_f * 2.0)
            dispersion_ratio = historical_spread_f / ensemble_spread_f
            if horizon in ("D+1", "D+2") and dispersion_ratio > 1.6:
                tags.append("dispersion_underpricing_watch")
                score_parts.append(min(0.3, (dispersion_ratio - 1.6) * 0.12))
                notes.append(f"Ensemble may be under-dispersed: ratio {dispersion_ratio:.2f}.")
                if limit_price <= 0.14:
                    tags.append("cheap_tail_candidate")
                    score_parts.append(0.2)
                    notes.append("Low-priced tail bucket matches dispersion-underpricing playbook.")
        except Exception:
            notes.append("Dispersion diagnostics could not be parsed.")

    samples = int(fit.get("samples") or 0) if fit else 0
    market_count = int(fit.get("markets") or 0) if fit else 0
    mae = float(fit.get("mae_f") or 0) if fit else None
    bias = _fit_bias_for_calibration(fit) if fit else None
    if market_count < 20:
        score_parts.append(-0.25)
        notes.append(f"Independent settled city-days only {market_count}; keep in paper until >=20.")
        if market_count < 10:
            tags.append("independent_history_thin")
    if samples < 10:
        score_parts.append(-0.15)
        notes.append("City fit sample is still thin; keep in paper unless other evidence is strong.")
    if mae is not None and mae > 3.0:
        tags.append("fit_risk")
        score_parts.append(-0.2)
        notes.append(f"City MAE is high at {mae:.1f}F.")
    if bias is not None and abs(bias) > 2.0:
        tags.append("bias_risk")
        score_parts.append(-0.1)
        notes.append(f"City decayed bias is elevated at {bias:+.1f}F.")

    if not tags:
        tags.append("standard_ev")
    if limit_price < 0.10:
        tags.append("low_price_tail_watch")
        notes.append("Low-price bucket: edge must survive spread cost, depth, and independent-day checks.")
    score = max(0.0, min(1.0, 0.45 + sum(score_parts)))
    return {
        "strategy_tags": tags,
        "strategy_score": round(score, 2),
        "strategy_notes": notes[:6],
        "near_lock": near_lock,
        "dispersion_ratio": round(dispersion_ratio, 2) if dispersion_ratio is not None else None,
    }


def _fit_quality_flags(fit, truth=None):
    flags = []
    if fit:
        markets = int(fit.get("markets") or 0)
        if markets < 10:
            flags.append("fit_independent_days_too_low")
        elif markets < 20:
            flags.append("fit_independent_days_low")
        if int(fit.get("samples") or 0) < 10:
            flags.append("fit_sample_low")
        if float(fit.get("mae_f") or 0) > 3.0:
            flags.append("city_mae_high")
        if abs(_fit_bias_for_calibration(fit)) > 2.0:
            flags.append("city_bias_high")
    else:
        flags.append("fit_missing")
    if truth:
        if int(truth.get("eligible_observations") or 0) < load_v3_config().min_independent_settlement_days:
            flags.append("truth_independent_days_low")
        if truth.get("open_meteo_fallbacks"):
            flags.append("open_meteo_truth_fallback_present")
        if truth.get("legacy_unknown"):
            flags.append("legacy_truth_unknown")
    else:
        flags.append("truth_missing")
    return flags


def _live_gate(signal, quality_flags, strategy):
    reasons = []
    cautions = []
    tags = set(strategy.get("strategy_tags") or [])
    low, high = _bucket_bounds(signal)
    if (low is not None and low <= -900) or (high is not None and high >= 900):
        cautions.append("open_tail_bucket")

    if "fit_missing" in quality_flags:
        reasons.append("fit_missing")
    if "fit_independent_days_too_low" in quality_flags:
        reasons.append("fit_independent_days_too_low")
    if "fit_independent_days_low" in quality_flags:
        reasons.append("fit_independent_days_low")
    if "city_mae_high" in quality_flags:
        reasons.append("city_mae_high")
    if "city_bias_high" in quality_flags:
        reasons.append("city_bias_high")
    if "fit_sample_low" in quality_flags:
        cautions.append("fit_sample_low")
    if "truth_missing" in quality_flags:
        reasons.append("truth_missing")
    if "truth_independent_days_low" in quality_flags:
        reasons.append("truth_independent_days_low")
    if "open_meteo_truth_fallback_present" in quality_flags:
        reasons.append("open_meteo_truth_fallback_present")
    if "legacy_truth_unknown" in quality_flags:
        reasons.append("legacy_truth_unknown")
    if "near_lock_missing_metar" in tags:
        reasons.append("near_lock_missing_metar")

    try:
        score = float(strategy.get("strategy_score") or 0)
    except Exception:
        score = 0.0
    if score < 0.35:
        reasons.append("strategy_score_low")

    try:
        limit_price = float(signal.get("limit_price") or 0)
    except Exception:
        limit_price = 0.0
    if limit_price < 0.03:
        reasons.append("price_below_min")
    if limit_price > 0.45:
        reasons.append("price_above_max")
    if 0 < limit_price < 0.10 and "cheap_tail_candidate" not in tags:
        reasons.append("low_price_tail_unverified")

    spread = signal.get("spread")
    try:
        spread = float(spread) if spread is not None else None
    except Exception:
        spread = None
    if spread is None:
        cautions.append("spread_missing")
    elif spread > 0.03:
        reasons.append("spread_above_limit")
    elif limit_price > 0 and limit_price <= 0.10 and (spread >= 0.02 or (spread / limit_price) >= 0.25):
        reasons.append("spread_cost_too_high")

    if (signal.get("date") or "") < _today_str():
        reasons.append("expired_signal")
    if signal.get("status") in ("simulated", "bought", "skipped"):
        cautions.append(f"already_{signal.get('status')}")

    return {
        "live_allowed": not reasons,
        "live_risk_level": "blocked" if reasons else ("caution" if cautions else "eligible"),
        "live_block_reasons": reasons,
        "live_cautions": cautions,
    }


def _signal_diagnostics_payload(signal, raw_signal, fit_by_city, market_by_city_date, truth_by_city=None):
    fit = fit_by_city.get(signal.get("city")) or {}
    truth = (truth_by_city or {}).get(signal.get("city"))
    quality_flags = _fit_quality_flags(fit, truth)
    market_for_signal = market_by_city_date.get((signal.get("city"), signal.get("date")), {})
    strategy = _strategy_diagnostics(signal, raw_signal, market_for_signal, fit)
    gate = _live_gate(signal, quality_flags, strategy)
    return fit, quality_flags, strategy, gate


def _signal_calibration_payload(signal, raw_signal, fit, source_fit, market):
    unit = (market or {}).get("unit") or ("F" if str(signal.get("bucket_label") or "").endswith("F") else "C")
    low, high = _bucket_bounds(signal)
    forecast = signal.get("forecast_temp") if signal.get("forecast_temp") is not None else raw_signal.get("forecast_temp")
    price = float(signal.get("limit_price") or raw_signal.get("entry_price") or 0)
    raw_p = raw_signal.get("raw_p")
    if raw_p is None:
        raw_p = signal.get("probability") if signal.get("probability") is not None else raw_signal.get("p")
    raw_ev = raw_signal.get("raw_ev")
    if raw_ev is None and raw_p is not None:
        raw_ev = _calc_ev(raw_p, price)

    try:
        forecast_f = _native_to_f(float(forecast), unit)
        low_f = -999 if low == -999 else _native_to_f(float(low), unit)
        high_f = 999 if high == 999 else _native_to_f(float(high), unit)
    except Exception:
        return {
            "raw_model_probability": raw_p,
            "raw_edge": raw_ev,
            "calibrated_probability": raw_signal.get("calibrated_probability") or signal.get("probability"),
            "calibrated_edge": raw_signal.get("calibrated_ev") or signal.get("ev"),
            "calibrated_sigma_f": raw_signal.get("calibrated_sigma_f"),
            "calibration_bias_f": raw_signal.get("calibration_bias_f"),
        }

    bias_f = _fit_bias_for_calibration(fit) if fit else float(raw_signal.get("calibration_bias_f") or 0.0)
    adjusted_f = forecast_f - bias_f
    latest = _latest_snapshot(market or {}, "forecast_snapshots")
    ensemble_std = raw_signal.get("ensemble_std")
    if ensemble_std is None:
        ensemble_std = latest.get("ensemble_std")
    default_sigma_f = 2.16 if unit == "C" else 2.0
    sigma_candidates = [1.5, default_sigma_f]
    if ensemble_std is not None:
        try:
            sigma_candidates.append(float(_delta_to_f(float(ensemble_std), unit) or 0))
        except Exception:
            pass
    if fit:
        sigma_candidates.append(float(fit.get("mae_f") or 0))
        sigma_candidates.append(float(fit.get("rmse_f") or 0) * 0.75)
    if source_fit:
        sigma_candidates.append(float(source_fit.get("mae_f") or 0))
    sigma_f = max(sigma_candidates)
    calibrated_p = _bucket_probability_f(adjusted_f, low_f, high_f, sigma_f)
    calibrated_ev = _calc_ev(calibrated_p, price) if calibrated_p is not None else None
    return {
        "raw_model_probability": round(float(raw_p), 4) if raw_p is not None else None,
        "raw_edge": round(float(raw_ev), 4) if raw_ev is not None else None,
        "calibrated_probability": round(float(raw_signal.get("calibrated_probability") or calibrated_p or 0), 4) if calibrated_p is not None or raw_signal.get("calibrated_probability") is not None else None,
        "calibrated_edge": round(float(raw_signal.get("calibrated_ev") or calibrated_ev or 0), 4) if calibrated_ev is not None or raw_signal.get("calibrated_ev") is not None else None,
        "calibrated_sigma_f": round(float(raw_signal.get("calibrated_sigma_f") or sigma_f), 2),
        "calibration_bias_f": round(float(raw_signal.get("calibration_bias_f") if raw_signal.get("calibration_bias_f") is not None else bias_f), 2),
    }


def _distribution_for_signal(signal, raw_signal, market, calibration_payload):
    outcomes = (market or {}).get("all_outcomes") or []
    latest = _latest_snapshot(market or {}, "forecast_snapshots")
    unit = (market or {}).get("unit") or ("F" if str(signal.get("bucket_label") or "").endswith("F") else "C")
    forecast = signal.get("forecast_temp")
    if forecast is None:
        forecast = raw_signal.get("forecast_temp")
    if forecast is None:
        forecast = latest.get("best")
    sigma = calibration_payload.get("calibrated_sigma_f")
    if sigma is None:
        sigma = raw_signal.get("calibrated_sigma_f") or raw_signal.get("sigma") or (2.16 if unit == "C" else 2.0)
    bias = calibration_payload.get("calibration_bias_f") or raw_signal.get("calibration_bias_f") or 0.0
    distribution = build_event_distribution(
        outcomes,
        forecast,
        unit=unit,
        sigma_f=float(sigma or 0),
        bias_f=float(bias or 0),
        signal_market_id=str(signal.get("market_id") or ""),
    )
    signal_row = next((item for item in distribution.get("items", []) if item.get("is_signal")), None)
    if signal_row:
        distribution["signal_probability"] = signal_row.get("probability")
        distribution["signal_probability_edge"] = signal_row.get("probability_edge")
        distribution["signal_ev"] = signal_row.get("ev")
        distribution["signal_spread_cost_ratio"] = signal_row.get("spread_cost_ratio")
    return distribution


def _decision_for_signal(signal, display_edge, quality_flags, strategy, live_gate, distribution, truth):
    reasons = list(live_gate.get("live_block_reasons") or [])
    cautions = list(live_gate.get("live_cautions") or [])
    if not distribution.get("normalized"):
        reasons.append("distribution_missing")
    signal_prob = distribution.get("signal_probability")
    if signal_prob is not None and float(signal_prob) < float(signal.get("limit_price") or 0):
        reasons.append("distribution_edge_negative")
    if truth and truth.get("status") != "eligible":
        for reason in truth.get("reasons") or []:
            if reason not in reasons:
                reasons.append(reason)
    try:
        display_edge = float(display_edge or 0.0)
    except Exception:
        display_edge = 0.0
    action = "buy_paper_candidate" if display_edge > 0 and not reasons else "skip_or_watch"
    return {
        "signal_id": signal.get("id"),
        "market_id": signal.get("market_id"),
        "action": action,
        "paper_allowed": display_edge > 0 and not any(reason in reasons for reason in ("distribution_missing", "distribution_edge_negative")),
        "live_allowed": bool(live_gate.get("live_allowed")) and not reasons,
        "reasons": reasons,
        "cautions": cautions,
        "quality_flags": quality_flags,
        "strategy_tags": strategy.get("strategy_tags") or [],
        "strategy_score": strategy.get("strategy_score"),
        "distribution_signal_probability": signal_prob,
        "truth_status": truth.get("status") if truth else "missing",
    }


def build_dashboard_payload():
    init_db()
    markets = load_markets()
    _sync_v4_market_rules(markets)
    for market in markets:
        upsert_signal_from_market(market)

    state = _read_json(DATA_DIR / "state.json", {})
    simulation_started_at = state.get("simulation_started_at")
    simulation_start = _parse_iso(simulation_started_at)
    open_positions = []
    recent_trades = []
    position_market_ids = set()
    weather_forecasts_by_city = {}
    weather_city_series = _build_weather_city_series(markets)
    truth_health = _build_truth_health(markets)
    data_readiness = build_data_readiness()
    truth_by_city = _truth_city_lookup(truth_health)
    temperature_fit = _build_temperature_fit(markets)
    fit_by_city = {row.get("city_key"): row for row in temperature_fit.get("cities", [])}
    fit_by_source = {str(row.get("source") or "").upper(): row for row in temperature_fit.get("sources", [])}
    market_by_city_date = {
        (market.get("city"), market.get("date")): market
        for market in markets
        if market.get("city") and market.get("date")
    }
    forecast_points = 0
    market_points = 0
    last_run = None

    for market in markets:
        forecast_points += len(market.get("forecast_snapshots", []))
        market_points += len(market.get("market_snapshots", []))
        latest_forecast = _latest_snapshot(market, "forecast_snapshots")
        if latest_forecast.get("ts"):
            last_run = max(last_run or latest_forecast["ts"], latest_forecast["ts"])
        best = latest_forecast.get("best")
        city_key = market.get("city", "")
        if best is not None and city_key:
            high_f = _native_to_f(best, market.get("unit"))
            ensemble_members = int(latest_forecast.get("ensemble_members") or 0)
            ensemble_std = latest_forecast.get("ensemble_std")
            if ensemble_std is None:
                ensemble_std = 2.0 if market.get("unit") == "F" else 2.2
            weather_forecasts_by_city[city_key] = {
                "city_key": city_key,
                "city_name": market.get("city_name", city_key),
                "target_date": market.get("date", ""),
                "mean_high": high_f or 0,
                "std_high": float(ensemble_std or 0),
                "mean_low": 0,
                "std_low": 0,
                "num_members": ensemble_members or 1,
                "ensemble_agreement": None,
            }
        pos = market.get("position")
        if pos:
            position_ts = pos.get("opened_at") or market.get("created_at", "")
            position_in_window = _at_or_after(position_ts, simulation_start)
            if position_in_window:
                position_market_ids.add(str(pos.get("market_id") or ""))
            event_url = pos.get("event_url") or market.get("event_url")
            mark_price = _position_mark_price(market, pos)
            unrealized = _unrealized_pnl(pos, mark_price)
            result = "pending"
            if pos.get("status") == "closed":
                result = "win" if (pos.get("pnl") or 0) > 0 else "loss"
            item = {
                "city": market.get("city"),
                "city_name": market.get("city_name"),
                "date": market.get("date"),
                "unit": market.get("unit"),
                "event_url": event_url,
                "question": _clean_text(pos.get("question")),
                "market_id": pos.get("market_id"),
                "yes_token_id": pos.get("yes_token_id"),
                "bucket_low": pos.get("bucket_low"),
                "bucket_high": pos.get("bucket_high"),
                "entry_price": pos.get("entry_price"),
                "bid_at_entry": pos.get("bid_at_entry"),
                "spread": pos.get("spread"),
                "cost": pos.get("cost"),
                "shares": pos.get("shares"),
                "forecast_temp": pos.get("forecast_temp"),
                "forecast_src": pos.get("forecast_src"),
                "ev": pos.get("ev"),
                "status": pos.get("status"),
                "pnl": pos.get("pnl"),
                "mark_price": mark_price,
                "unrealized_pnl": unrealized,
                "close_reason": pos.get("close_reason"),
            }
            if pos.get("status") == "open" and position_in_window:
                open_positions.append(item)
            if position_in_window:
                recent_trades.append({
                    "id": int(pos.get("market_id") or 0) if str(pos.get("market_id", "")).isdigit() else len(recent_trades) + 1,
                    "market_ticker": pos.get("market_id", ""),
                    "platform": "polymarket",
                    "event_slug": _event_slug_from_url(event_url) or pos.get("question", ""),
                    "event_url": event_url,
                    "market_title": _clean_text(pos.get("question")),
                    "shares": pos.get("shares"),
                    "close_reason": pos.get("close_reason"),
                    "exit_price": pos.get("exit_price"),
                    "source": pos.get("forecast_src"),
                    "direction": "yes",
                    "entry_price": pos.get("entry_price") or 0,
                    "bid_at_entry": pos.get("bid_at_entry"),
                    "spread": pos.get("spread"),
                    "mark_price": mark_price,
                    "unrealized_pnl": unrealized,
                    "size": pos.get("cost") or 0,
                    "timestamp": position_ts,
                    "settled": pos.get("status") == "closed",
                    "result": result,
                    "pnl": pos.get("pnl"),
                })

    today = _today_str()
    all_signals = [s for s in list_signals(300) if not _is_dashboard_position_import(s)]
    signals_by_market = {}
    for signal in all_signals:
        signals_by_market.setdefault(str(signal.get("market_id") or ""), signal)
    expired_signal_count = len([s for s in all_signals if (s.get("date") or "") < today])
    signals = [s for s in all_signals if (s.get("date") or "") >= today]
    weather_signals = []
    simulated_trades = []
    for signal in signals:
        raw_signal = _read_json_from_text(signal.get("raw_json"), {})
        threshold = signal.get("forecast_temp")
        try:
            threshold = float(threshold) if threshold is not None else 0.0
        except Exception:
            threshold = 0.0
        limit_price = signal.get("limit_price") or 0
        model_probability = signal.get("probability") or 0
        probability_edge = raw_signal.get("prob_edge")
        if probability_edge is None:
            probability_edge = model_probability - limit_price
        amount = signal.get("amount") or 0
        sim_amount = signal.get("sim_amount")
        display_amount = sim_amount if sim_amount is not None else amount
        effective_status = signal.get("status")
        paper_position = str(signal.get("market_id") or "") in position_market_ids
        fit, quality_flags, strategy, live_gate = _signal_diagnostics_payload(
            signal,
            raw_signal,
            fit_by_city,
            market_by_city_date,
            truth_by_city,
        )
        market_for_signal = market_by_city_date.get((signal.get("city"), signal.get("date")), {})
        source_key = str(signal.get("forecast_src") or raw_signal.get("forecast_src") or "").upper()
        calibration_payload = _signal_calibration_payload(
            signal,
            raw_signal,
            fit,
            fit_by_source.get(source_key) or fit_by_source.get("MODEL_BEST"),
            market_for_signal,
        )
        display_probability = calibration_payload.get("calibrated_probability")
        if display_probability is None:
            display_probability = model_probability
        display_edge = calibration_payload.get("calibrated_edge")
        if display_edge is None:
            display_edge = signal.get("ev") or 0
        display_probability_edge = (display_probability - limit_price) if display_probability is not None else probability_edge
        signal_distribution = _distribution_for_signal(
            signal,
            raw_signal,
            market_for_signal,
            calibration_payload,
        )
        decision_payload = _decision_for_signal(
            signal,
            display_edge,
            quality_flags,
            strategy,
            live_gate,
            signal_distribution,
            truth_by_city.get(signal.get("city")),
        )
        weather_signals.append({
            "id": signal.get("id"),
            "market_id": signal.get("market_id"),
            "city_key": signal.get("city"),
            "city_name": _clean_text(signal.get("city_name")),
            "target_date": signal.get("date"),
            "question": _clean_text(signal.get("question")),
            "event_url": signal.get("event_url"),
            "yes_token_id": signal.get("yes_token_id"),
            "bucket_label": signal.get("bucket_label"),
            "threshold_f": threshold,
            "metric": "high",
            "direction": "yes",
            "model_probability": display_probability,
            "market_probability": signal.get("limit_price") or 0,
            "probability_edge": display_probability_edge,
            "edge": display_edge,
            **calibration_payload,
            "confidence": raw_signal.get("confidence") or min(0.95, max(0.05, model_probability or 0.5)),
            "suggested_size": amount,
            "reasoning": (
                f"{signal.get('city_name')} {signal.get('date')} {signal.get('bucket_label')} | "
                f"limit ${limit_price:.3f} | amount ${amount:.2f} | "
                f"source {(signal.get('forecast_src') or '').upper()} | "
                f"method {raw_signal.get('prob_method', 'unknown')} | "
                f"token {signal.get('yes_token_id') or 'unknown'}"
            ),
            "ensemble_mean": signal.get("forecast_temp") or 0,
            "ensemble_std": raw_signal.get("ensemble_std") or 0,
            "ensemble_members": raw_signal.get("ensemble_members") or 1,
            "actionable": effective_status not in ("skipped", "simulated", "bought"),
            "platform": signal.get("platform") or "polymarket",
            "status": effective_status,
            "paper_position": paper_position,
            "limit_price": signal.get("limit_price"),
            "bid_price": signal.get("bid_price"),
            "spread": signal.get("spread"),
            "shares": signal.get("shares"),
            "sim_amount": sim_amount,
            "manual_note": signal.get("manual_note"),
            "fit_markets": fit.get("markets"),
            "fit_samples": fit.get("samples"),
            "fit_mae_f": fit.get("mae_f"),
            "fit_bias_f": fit.get("bias_f"),
            "fit_decayed_bias_f": fit.get("decayed_bias_f"),
            "quality_flags": quality_flags,
            "truth": truth_by_city.get(signal.get("city")),
            "distribution": signal_distribution,
            "decision": decision_payload,
            **strategy,
            **live_gate,
        })
        if signal.get("status") in ("simulated", "bought") and str(signal.get("market_id") or "") not in position_market_ids:
            market_for_signal = market_by_city_date.get((signal.get("city"), signal.get("date")), {})
            outcome = _find_outcome(
                market_for_signal,
                market_id=signal.get("market_id"),
                yes_token_id=signal.get("yes_token_id"),
            )
            mark_price = outcome.get("bid")
            if mark_price is None:
                mark_price = signal.get("bid_price")
            if mark_price is None:
                mark_price = signal.get("limit_price")
            try:
                mark_price = float(mark_price)
            except Exception:
                mark_price = None
            try:
                unrealized = round((float(signal.get("shares") or 0) * float(mark_price)) - float(display_amount or 0), 2) if mark_price is not None else None
            except Exception:
                unrealized = None
            simulated_trades.append({
                "id": int(signal.get("id") or 0),
                "market_ticker": signal.get("market_id") or "",
                "platform": "polymarket",
                "event_slug": _event_slug_from_url(signal.get("event_url")) or _clean_text(signal.get("question", "")),
                "event_url": signal.get("event_url"),
                "market_title": _clean_text(signal.get("question", "")),
                "shares": signal.get("shares"),
                "close_reason": None,
                "exit_price": None,
                "source": signal.get("forecast_src"),
                "direction": "yes",
                "entry_price": signal.get("limit_price") or 0,
                "bid_at_entry": signal.get("bid_price"),
                "spread": signal.get("spread"),
                "mark_price": mark_price,
                "unrealized_pnl": unrealized,
                "size": display_amount or 0,
                "timestamp": signal.get("status_updated_at") or signal.get("created_at") or "",
                "settled": False,
                "result": "pending",
                "pnl": None,
            })

    starting = state.get("starting_balance", 0) or 0
    cash_balance = state.get("balance", starting) or 0
    latest_update = _latest_market_update()
    data_age = _data_age_minutes(latest_update)
    equity_curve = []
    running_pnl = 0.0
    combined_trades = [
        trade for trade in (recent_trades + simulated_trades)
        if _at_or_after(trade.get("timestamp"), simulation_start)
    ]
    for trade in sorted(combined_trades, key=lambda t: t.get("timestamp") or ""):
        if trade.get("pnl") is not None:
            running_pnl += float(trade["pnl"])
            equity_curve.append({
                "timestamp": trade.get("timestamp"),
                "pnl": round(running_pnl, 2),
                "bankroll": round(starting + running_pnl, 2),
            })

    settled_trades = [t for t in combined_trades if t.get("result") in ("win", "loss")]
    open_trade_count = len([t for t in combined_trades if t.get("result") == "pending"])
    reserved_capital = round(sum(float(t.get("size") or 0) for t in combined_trades if t.get("result") == "pending"), 2)
    unrealized_pnl = round(
        sum(float(t.get("unrealized_pnl") or 0) for t in combined_trades if t.get("result") == "pending"),
        2,
    )
    # Pending stake is marked conservatively at the current bid, so a fresh
    # paper fill can show the bid/ask spread as immediate unrealized loss.
    realized_pnl = round(sum(float(t.get("pnl") or 0) for t in combined_trades if t.get("pnl") is not None), 2)
    total_pnl = round(realized_pnl + unrealized_pnl, 2)
    equity = round(starting + total_pnl, 2)
    cash_balance = round(max(0.0, starting + realized_pnl - reserved_capital), 2)
    wins = len([t for t in settled_trades if t.get("result") == "win"])
    total_with_outcome = len(settled_trades)
    brier_values = []
    predicted_edges = []
    actual_edges = []
    for trade in settled_trades:
        signal = signals_by_market.get(str(trade.get("market_ticker") or ""))
        if signal:
            p = signal.get("probability")
            if p is not None:
                y = 1.0 if trade.get("result") == "win" else 0.0
                brier_values.append((float(p) - y) ** 2)
            if signal.get("ev") is not None:
                predicted_edges.append(float(signal.get("ev")))
        if trade.get("size"):
            actual_edges.append(float(trade.get("pnl") or 0) / float(trade.get("size")))

    calibration_summary = {
        "total_signals": len(combined_trades),
        "total_with_outcome": total_with_outcome,
        "settlement_rate": (total_with_outcome / len(combined_trades)) if combined_trades else 0,
        "accuracy": (wins / total_with_outcome) if total_with_outcome else 0,
        "avg_predicted_edge": (sum(predicted_edges) / len(predicted_edges)) if predicted_edges else 0,
        "avg_actual_edge": (sum(actual_edges) / len(actual_edges)) if actual_edges else 0,
        "brier_score": (sum(brier_values) / len(brier_values)) if brier_values else 0,
    }
    backtest_summary = _build_backtest_summary(markets)
    model_dataset_audit = None
    strategy_readiness = backtest_summary.get("strategy_readiness") or {}
    cfg = load_v3_config()
    readiness_reasons = list(strategy_readiness.get("reasons") or [])
    if truth_health.get("eligible_observations", 0) < cfg.min_independent_settlement_days:
        readiness_reasons.append("truth_observations_below_min")
    if not data_readiness.get("live_allowed"):
        readiness_reasons.extend(
            str(reason.get("code") or "")
            for reason in data_readiness.get("blockers", [])
            if reason.get("code")
        )
    if readiness_reasons:
        strategy_readiness = {
            **strategy_readiness,
            "live_ready": False,
            "status": "blocked",
            "reasons": list(dict.fromkeys(readiness_reasons)),
        }
        backtest_summary["strategy_readiness"] = strategy_readiness
    if not strategy_readiness.get("live_ready"):
        for signal in weather_signals:
            reasons = list(signal.get("live_block_reasons") or [])
            if "strategy_not_ready" not in reasons:
                reasons.append("strategy_not_ready")
            signal["live_pre_strategy_allowed"] = bool(signal.get("live_allowed"))
            signal["live_allowed"] = False
            signal["live_risk_level"] = "blocked"
            signal["live_block_reasons"] = reasons

    stats = {
        "bankroll": equity,
        "cash_balance": cash_balance,
        "reserved_capital": reserved_capital,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "total_trades": len(combined_trades),
        "open_trades": open_trade_count,
        "settled_trades": total_with_outcome,
        "winning_trades": wins,
        "win_rate": (wins / total_with_outcome) if total_with_outcome else 0,
        "total_pnl": total_pnl,
        "is_running": _bot_running(),
        "last_run": last_run,
        "latest_market_update": latest_update,
        "data_age_minutes": data_age,
        "expired_signal_count": expired_signal_count,
        "signal_count": len(signals),
        "actionable_count": len([s for s in weather_signals if s.get("actionable")]),
        "live_candidate_count": len([s for s in weather_signals if s.get("actionable") and s.get("live_allowed")]),
        "live_blocked_count": len([s for s in weather_signals if s.get("actionable") and not s.get("live_allowed")]),
        "strategy_live_ready": bool(strategy_readiness.get("live_ready")),
        "strategy_readiness_status": strategy_readiness.get("status") or "watch",
        "strategy_readiness_reasons": strategy_readiness.get("reasons") or [],
        "strategy_allowed_pnl": strategy_readiness.get("allowed_pnl"),
        "strategy_allowed_roi": strategy_readiness.get("allowed_roi"),
        "strategy_allowed_resolved": strategy_readiness.get("allowed_resolved"),
        "simulation_started_at": simulation_started_at,
        "scanner_status": "running" if _bot_running() else "stopped",
        "auto_simulation": _auto_simulation_state(),
    }

    events = list_events(100)
    fetch_log = _combined_fetch_log_payload(events)
    city_evidence = _build_city_evidence_payload(weather_city_series, weather_signals, fetch_log)
    return {
        "stats": stats,
        "v3": v3_dashboard_summary(),
        "data_readiness": data_readiness,
        "production_refresh": _production_refresh_runtime_state(_read_json(PRODUCTION_REFRESH_PATH, None)),
        "model_dataset_audit": model_dataset_audit,
        "truth_health": truth_health,
        "btc_price": None,
        "microstructure": None,
        "windows": [],
        "active_signals": [],
        "recent_trades": sorted(combined_trades, key=lambda t: t.get("timestamp") or "", reverse=True)[:100],
        "equity_curve": equity_curve,
        "calibration": calibration_summary,
        "backtest": backtest_summary,
        "weather_signals": weather_signals,
        "weather_forecasts": list(weather_forecasts_by_city.values()),
        "weather_city_series": weather_city_series,
        "city_statuses": asian_city_priority_config(),
        "city_evidence": city_evidence,
        "recommendations": {
            "items": [],
            "trade_candidate_count": 0,
            "observation_only_count": 0,
            "message": "recommendation_cache_warming",
        },
        "events": events,
        "fetch_log": fetch_log,
    }


def _minimal_dashboard_payload(reason: str = "cache_warming"):
    now = datetime.now(timezone.utc).isoformat()
    events = list_events(50)
    fetch_log = _combined_fetch_log_payload(events, 50)
    city_series = _registry_city_series()
    stats = {
        "bankroll": 0,
        "cash_balance": 0,
        "reserved_capital": 0,
        "realized_pnl": 0,
        "unrealized_pnl": 0,
        "total_trades": 0,
        "open_trades": 0,
        "settled_trades": 0,
        "winning_trades": 0,
        "win_rate": 0,
        "total_pnl": 0,
        "is_running": _bot_running(),
        "last_run": None,
        "latest_market_update": None,
        "data_age_minutes": None,
        "expired_signal_count": 0,
        "signal_count": 0,
        "actionable_count": 0,
        "live_candidate_count": 0,
        "live_blocked_count": 0,
        "strategy_live_ready": False,
        "strategy_readiness_status": "warming",
        "strategy_readiness_reasons": [reason],
        "simulation_started_at": None,
        "scanner_status": "running" if _bot_running() else "stopped",
        "auto_simulation": _auto_simulation_state(),
    }
    try:
        v3_summary = v3_dashboard_summary()
    except Exception as exc:
        v3_summary = {"error": str(exc)}
    return {
        "stats": stats,
        "v3": v3_summary,
        "data_readiness": None,
        "production_refresh": _production_refresh_runtime_state(_read_json(PRODUCTION_REFRESH_PATH, None)),
        "model_dataset_audit": None,
        "truth_health": None,
        "btc_price": None,
        "microstructure": None,
        "windows": [],
        "active_signals": [],
        "recent_trades": [],
        "equity_curve": [],
        "calibration": None,
        "backtest": None,
        "weather_signals": [],
        "weather_forecasts": [],
        "weather_city_series": city_series,
        "city_statuses": asian_city_priority_config(),
        "city_evidence": _build_city_evidence_payload(city_series, [], fetch_log),
        "recommendations": _recommendations_payload(),
        "events": events,
        "fetch_log": fetch_log,
        "_meta": {"cache": "warming", "reason": reason, "generated_at": now},
    }


async def _refresh_dashboard_cache_once():
    global dashboard_payload_cache, dashboard_payload_cache_at
    try:
        payload = await asyncio.to_thread(build_dashboard_payload)
        dashboard_payload_cache_at = datetime.now(timezone.utc)
        payload["_meta"] = {
            "cache": "fresh",
            "generated_at": dashboard_payload_cache_at.isoformat(),
        }
        dashboard_payload_cache = payload
    except Exception as exc:
        log_event("error", f"Dashboard cache refresh failed: {exc}")


def _ensure_dashboard_refresh(force: bool = False):
    global dashboard_refresh_task
    stale = True
    if dashboard_payload_cache_at:
        stale = (datetime.now(timezone.utc) - dashboard_payload_cache_at).total_seconds() > DASHBOARD_CACHE_TTL_SECONDS
    if not force and not stale:
        return
    if dashboard_refresh_task is None or dashboard_refresh_task.done():
        dashboard_refresh_task = asyncio.create_task(_refresh_dashboard_cache_once())


def _clear_production_validation_cache():
    production_validation_cache.clear()


def _production_validation_runtime() -> dict:
    return {
        "scanner_status": "running" if _bot_running() else "stopped",
        "is_running": _bot_running(),
        "auto_simulation_enabled": _auto_simulation_state()["enabled"],
        "production_refresh_running": production_refresh_lock.locked(),
    }


async def _auto_refresh_loop():
    global auto_refresh_task
    try:
        if AUTO_REFRESH_INITIAL_DELAY_SECONDS:
            await asyncio.sleep(AUTO_REFRESH_INITIAL_DELAY_SECONDS)
        while True:
            try:
                async with production_refresh_lock:
                    result = await asyncio.to_thread(
                        run_production_refresh,
                        cities=AUTO_REFRESH_CITIES,
                        days=AUTO_REFRESH_DAYS,
                        limit=AUTO_REFRESH_LIMIT,
                        start_date="",
                        end_date="",
                        scan_signals=AUTO_REFRESH_SIGNAL_SCAN,
                    )
                    result = {
                        **result,
                        "requested_at": datetime.now(timezone.utc).isoformat(),
                        "auto_refresh": True,
                    }
                    _save_production_refresh_result(result)
                    log_event(
                        "success" if result.get("ok") else "warning",
                        "Auto data refresh completed" if result.get("ok") else "Auto data refresh finished with failed stages",
                        _production_refresh_summary(result),
                    )
                await _refresh_dashboard_cache_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log_event("error", f"Auto refresh failed: {exc}")
            await asyncio.sleep(AUTO_REFRESH_INTERVAL_SECONDS)
    finally:
        auto_refresh_task = None


def _ensure_auto_refresh_task():
    global auto_refresh_task
    if not AUTO_REFRESH_ENABLED:
        return
    if auto_refresh_task is None or auto_refresh_task.done():
        auto_refresh_task = asyncio.create_task(_auto_refresh_loop())


async def _stop_auto_refresh_task():
    global auto_refresh_task
    task = auto_refresh_task
    auto_refresh_task = None
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def _ensure_auto_simulation_task():
    return _disable_persisted_auto_simulation_on_startup()


def _disable_persisted_auto_simulation_on_startup():
    state = _auto_simulation_state()
    if not state["enabled"]:
        return state
    return _save_auto_simulation_state(enabled=False, last_error=None)


@app.on_event("startup")
async def startup():
    init_db()
    init_v3_db()
    if STARTUP_MAINTENANCE:
        migrate_legacy_signals(500)
        persist_data_readiness(build_data_readiness())
    _cleanup_stale_bot_process()
    if AUTO_START_SCANNER:
        try:
            _start_bot_process("dashboard startup")
        except Exception as exc:
            log_event("error", f"Scanner auto-start failed: {exc}")
    _ensure_auto_refresh_task()
    _ensure_auto_simulation_task()
    if SCHEDULER_AUTO_START:
        await get_scheduler().start()
    global dashboard_payload_cache
    dashboard_payload_cache = _minimal_dashboard_payload("manual_refresh_required")
    if DASHBOARD_AUTO_BUILD:
        _ensure_dashboard_refresh(force=True)
    log_event("info", "Dashboard started", {
        "auto_refresh_enabled": AUTO_REFRESH_ENABLED,
        "auto_refresh_initial_delay_seconds": AUTO_REFRESH_INITIAL_DELAY_SECONDS,
        "auto_refresh_interval_seconds": AUTO_REFRESH_INTERVAL_SECONDS,
        "auto_refresh_signal_scan": AUTO_REFRESH_SIGNAL_SCAN,
        "auto_simulation_resume": False,
        "scheduler_auto_start": SCHEDULER_AUTO_START,
        "legacy_scanner_auto_start": AUTO_START_SCANNER,
        "dashboard_auto_build": DASHBOARD_AUTO_BUILD,
        "startup_maintenance": STARTUP_MAINTENANCE,
    })


@app.on_event("shutdown")
async def shutdown():
    await get_scheduler().stop()
    await _stop_auto_refresh_task()
    if _bot_running():
        _terminate_pid_tree(bot_process.pid)


@app.get("/")
async def index():
    return FileResponse(DASHBOARD_DIR / "index.html")


@app.get("/api/dashboard")
async def dashboard(city: str = ""):
    # HTTP reads may refresh the local presentation cache even when collector
    # auto-start is disabled. This never starts a scanner or scheduler.
    _ensure_dashboard_refresh()
    if dashboard_payload_cache is not None:
        payload = dict(dashboard_payload_cache)
    else:
        payload = _minimal_dashboard_payload()
    payload["weather_city_series"] = [
        row if city and _city_evidence_matches(row, city) else {**row, "hourly_points": []}
        for row in (payload.get("weather_city_series") or [])
    ]
    if city:
        payload["city_evidence"] = [
            row for row in (payload.get("city_evidence") or [])
            if _city_evidence_matches(row, city)
        ]
    else:
        payload["city_evidence"] = []
    latest_refresh = await asyncio.to_thread(
        lambda: _production_refresh_runtime_state(_read_json(PRODUCTION_REFRESH_PATH, None))
    )
    if latest_refresh is not None:
        payload["production_refresh"] = latest_refresh
    scheduler_payload = get_scheduler().status()
    payload["scheduler_status"] = scheduler_payload
    try:
        payload["recommendations"] = await asyncio.to_thread(
            _recommendations_payload,
            scheduler_status=scheduler_payload,
        )
    except Exception as exc:
        payload["recommendations"] = {
            "items": [],
            "focus_items": [],
            "trade_candidate_count": 0,
            "observation_only_count": 0,
            "weather_focus_count": 0,
            "empty_reason": "recommendation_read_failed",
            "message": str(exc)[:160],
        }
    return await asyncio.to_thread(_json_safe, payload)


@app.get("/api/production-refresh/status")
async def production_refresh_status():
    state = await asyncio.to_thread(
        lambda: _production_refresh_runtime_state(_read_json(PRODUCTION_REFRESH_PATH, None))
    )
    return state or {
        "refresh_version": "production-refresh-v2",
        "ok": None,
        "running": production_refresh_lock.locked(),
        "failed_stages": [],
        "stages": [],
        "history": [],
    }


@app.get("/api/scheduler/status")
async def scheduler_status():
    return get_scheduler().status()


@app.get("/api/paper-validation/status")
async def paper_validation_status_api():
    return await asyncio.to_thread(paper_validation_status)


@app.post("/api/paper-validation/start")
async def paper_validation_start_api(request: PaperValidationStartRequest):
    allowed_strategies = {"single_bucket_ev", "ladder_grid", "tail_buying"}
    strategies = list(dict.fromkeys(request.strategies or ["single_bucket_ev"]))
    unknown = [strategy for strategy in strategies if strategy not in allowed_strategies]
    if unknown:
        raise HTTPException(status_code=400, detail={"reason": "unsupported_paper_strategy", "strategies": unknown})
    result = await asyncio.to_thread(
        start_paper_validation_run,
        duration_days=request.duration_days,
        bankroll_usd=request.bankroll_usd,
        max_per_trade_usd=request.max_per_trade_usd,
        daily_max_usd=request.daily_max_usd,
        max_open_positions=request.max_open_positions,
        max_orders_per_day=request.max_orders_per_day,
        decision_max_age_minutes=request.decision_max_age_minutes,
        cities=request.cities,
        strategies=strategies,
        strategy_revision_id=request.strategy_revision_id,
        notes="dashboard controlled paper validation",
    )
    return result.get("run") if result.get("ok") and result.get("run") else result


@app.get("/api/developer/strategy-profiles")
async def strategy_profiles_api():
    await asyncio.to_thread(ensure_default_strategy_profile, "signal_generation")
    await asyncio.to_thread(ensure_default_strategy_profile, "paper_default")
    profiles = await asyncio.to_thread(list_strategy_profile_revisions)
    return {
        "ok": True,
        "profiles": profiles,
        "allowed_scopes": sorted(ALLOWED_SCOPES),
        "live_trading": bool(load_v3_config().live_trading),
        "live_execution_production_ready": bool(LIVE_EXECUTION_PRODUCTION_READY),
    }


@app.post("/api/developer/strategy-profiles")
async def strategy_profile_create_api(payload: StrategyProfileCreateRequest, request: Request):
    _require_local_developer_request(request, payload.confirm)
    unknown_scopes = [scope for scope in payload.activate_scopes if scope not in ALLOWED_SCOPES]
    if unknown_scopes:
        raise HTTPException(status_code=400, detail={"reason": "unsupported_strategy_profile_scope", "scopes": unknown_scopes})
    try:
        revision = await asyncio.to_thread(
            create_strategy_profile_revision,
            payload.parameters,
            profile_key=payload.profile_key,
            created_by="local_dashboard",
            change_note=payload.change_note,
        )
        for scope in payload.activate_scopes:
            await asyncio.to_thread(
                activate_strategy_profile,
                revision["revision_id"],
                scope=scope,
                actor="local_dashboard",
                reason=payload.change_note or "developer profile publish",
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"reason": str(exc)}) from exc
    return revision


@app.post("/api/developer/strategy-profiles/{revision_id}/activate")
async def strategy_profile_activate_api(
    revision_id: str,
    payload: StrategyProfileActivateRequest,
    request: Request,
):
    _require_local_developer_request(request, payload.confirm)
    try:
        return await asyncio.to_thread(
            activate_strategy_profile,
            revision_id,
            scope=payload.scope,
            actor="local_dashboard",
            reason=payload.reason or "developer profile activation",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"reason": str(exc)}) from exc


@app.get("/api/developer/api-settings")
async def developer_api_settings_api():
    return await asyncio.to_thread(list_api_settings)


@app.put("/api/developer/api-settings/{provider_key}")
async def developer_api_setting_update_api(
    provider_key: str,
    payload: ApiSettingUpdateRequest,
    request: Request,
):
    _require_local_developer_request(request, payload.confirm)
    try:
        provider = await asyncio.to_thread(
            update_api_setting,
            provider_key,
            payload.value,
            clear=payload.clear,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"reason": str(exc)}) from exc
    return {"ok": True, "provider": provider}


@app.post("/api/developer/api-settings/{provider_key}/test")
async def developer_api_setting_test_api(
    provider_key: str,
    payload: ApiSettingTestRequest,
    request: Request,
):
    _require_local_developer_request(request, payload.confirm)
    try:
        return await asyncio.to_thread(
            test_api_setting,
            provider_key,
            payload.value,
            allow_side_effect=payload.allow_side_effect,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"reason": str(exc)}) from exc


@app.post("/api/paper-validation/stop")
async def paper_validation_stop_api():
    return await asyncio.to_thread(stop_paper_validation_run)


@app.post("/api/paper-validation/tick")
async def paper_validation_tick_api(request: PaperValidationTickRequest | None = None):
    payload = request or PaperValidationTickRequest()
    return await asyncio.to_thread(
        run_paper_validation_tick,
        apply=payload.apply,
        run_id=payload.run_id,
        decision_id=payload.decision_id,
        city_key=payload.city,
        target_date=payload.target_date,
        strategies=payload.strategies,
        strategy_revision_id=payload.strategy_revision_id,
        decision_batch_issued_at=payload.decision_batch_issued_at,
    )


@app.get("/api/source-health")
async def source_health():
    matrix = await asyncio.to_thread(build_source_health_matrix)
    get_scheduler().update_source_health_cache(matrix)
    return matrix


@app.post("/api/scheduler/start")
async def scheduler_start():
    payload = await get_scheduler().start()
    return payload


@app.post("/api/scheduler/stop")
async def scheduler_stop():
    payload = await get_scheduler().stop()
    return payload


@app.get("/api/city-evidence")
async def city_evidence(city: str | None = None, date: str | None = None):
    payload = dashboard_payload_cache or _minimal_dashboard_payload()
    rows = list(payload.get("city_evidence") or [])
    if city:
        rows = [row for row in rows if _city_evidence_matches(row, city)]
    if date:
        filtered = []
        for row in rows:
            dates = [item for item in row.get("dates") or [] if str(item.get("target_date") or "") == date]
            if dates:
                filtered.append({**row, "dates": dates, "latest_date": dates[0].get("target_date"), "latest_ready_modules": dates[0].get("ready_modules", 0)})
        rows = filtered
    return {
        "cities": rows,
        "count": len(rows),
        "source": "dashboard_payload_cache" if dashboard_payload_cache is not None else "minimal_dashboard_payload",
    }


@app.get("/api/signals")
async def signals():
    return {"signals": list_signals(500)}


@app.get("/api/events")
async def events(limit: int = 50):
    return [_event_to_payload(event) for event in list_events(limit)]


@app.get("/api/backtest")
async def backtest():
    return _build_backtest_summary(load_markets())


@app.get("/api/backtest/policies")
async def backtest_policies():
    summary = _build_backtest_summary(load_markets())
    return {
        "policy_candidates": summary.get("policy_candidates", []),
        "strategy_readiness": summary.get("strategy_readiness", {}),
        "notes": summary.get("notes", []),
    }


@app.get("/api/model-dataset/audit")
async def model_dataset_audit():
    return build_model_dataset_audit()


@app.get("/api/forecast-archive/manifest")
async def forecast_archive_manifest(limit: int = 200, sources: str = "ecmwf,gfs_ensemble", include_jsonl: bool = False):
    source_list = [source.strip() for source in sources.split(",") if source.strip()]
    return await asyncio.to_thread(
        _forecast_archive_manifest_payload,
        limit=limit,
        sources=source_list,
        include_jsonl=include_jsonl,
    )


@app.get("/api/forecasts")
async def forecasts(city: str = "", target_date: str = ""):
    return await asyncio.to_thread(forecast_summary, city or None, target_date or None)


@app.get("/api/hourly-consensus")
async def hourly_consensus(city: str = "", target_date: str = ""):
    return await asyncio.to_thread(
        hourly_consensus_summary,
        city or None,
        target_date or None,
        ensure_schema=False,
    )


@app.get("/api/forecast-history")
async def forecast_history(city: str = "", target_date: str = "", hour: str = ""):
    return await asyncio.to_thread(
        forecast_revision_history,
        city,
        target_date,
        hour,
    )


@app.get("/api/market-buckets")
async def market_buckets(city: str = "", target_date: str = "", limit: int = 200):
    payload = await asyncio.to_thread(market_bucket_summary, city or None, target_date or None)
    payload["limit"] = max(1, min(int(limit or 200), 1000))
    payload["latest"] = payload.get("latest", [])[:payload["limit"]]
    return payload


@app.post("/api/market-buckets/sync-active")
async def market_buckets_sync_active(request: MarketBucketsSyncRequest):
    return run_market_buckets_sync(
        request.limit,
        cities_arg=",".join(request.cities or []),
        days_arg=request.days,
        target_date=request.target_date,
        active_weather=True,
        dry_run=request.dry_run,
        limit_cities=request.limit_cities,
        fetch_orderbooks=request.fetch_orderbooks,
    )


@app.get("/api/daily-max-predictions")
async def daily_max_predictions(city: str = "", target_date: str = ""):
    return await asyncio.to_thread(daily_max_prediction_summary, city or None, target_date or None)


@app.post("/api/daily-max-predictions/build")
async def daily_max_predictions_build(city: str = "", target_date: str = "", limit: int = 50):
    return build_daily_max_predictions(
        city=city or None,
        target_date=target_date or None,
        limit=limit,
    )


@app.get("/api/bucket-probabilities")
async def bucket_probabilities_api(city: str = "", target_date: str = ""):
    if not city or not target_date:
        raise HTTPException(status_code=400, detail="city and target_date are required")
    return await asyncio.to_thread(latest_bucket_probabilities, city, target_date)


@app.get("/api/signal-decisions")
async def signal_decisions(city: str = "", target_date: str = "", limit: int = 100):
    return await asyncio.to_thread(
        signal_decisions_summary,
        city or None,
        target_date or None,
        limit=limit,
    )


@app.get("/api/truth-delta-audit")
async def truth_delta_audit(city: str = "", limit: int = 500):
    return await asyncio.to_thread(truth_delta_audit_summary, city or None, limit=limit)


@app.get("/api/model-reprice-events")
async def model_reprice_events(city: str = "", target_date: str = "", alpha_only: bool = False, limit: int = 200):
    return await asyncio.to_thread(
        model_reprice_event_summary,
        city or None,
        target_date or None,
        alpha_only=alpha_only,
        limit=limit,
    )


@app.get("/api/signal-decisions/{decision_id}")
async def signal_decision_detail(decision_id: str):
    rows = list_signal_decisions(decision_id=decision_id, limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail="signal decision not found")
    return {"ok": True, "decision": rows[0]}


@app.post("/api/signal-decisions/build")
async def signal_decisions_build(city: str = "", target_date: str = "", limit: int = 200, dry_run: bool = False):
    if not city or not target_date:
        raise HTTPException(status_code=400, detail="city and target_date are required")
    return build_signal_decisions(city, target_date, dry_run=dry_run, limit=limit)


@app.get("/api/paper-orders")
async def paper_orders(city: str = "", target_date: str = "", limit: int = 100):
    return paper_execution_summary(city or None, target_date or None, limit=limit)


@app.post("/api/paper-orders/execute")
async def paper_orders_execute(request: PaperExecutionRequest):
    if not request.dry_run and not request.cohort_run_id:
        raise HTTPException(
            status_code=409,
            detail="start a paper validation account before writing simulated orders",
        )
    if request.cohort_run_id:
        if not request.strategy_revision_id or not request.decision_batch_issued_at:
            raise HTTPException(
                status_code=400,
                detail="cohort paper execution requires strategy_revision_id and decision_batch_issued_at",
            )
        return await asyncio.to_thread(
            run_paper_validation_tick,
            apply=not request.dry_run,
            run_id=request.cohort_run_id,
            decision_id=request.decision_id,
            city_key=request.city,
            target_date=request.target_date,
            strategies=request.strategies,
            strategy_revision_id=request.strategy_revision_id,
            decision_batch_issued_at=request.decision_batch_issued_at,
        )
    if request.decision_id:
        return execute_paper_decision(
            request.decision_id,
            amount=request.amount,
            dry_run=request.dry_run,
        )
    if not request.city or not request.target_date:
        raise HTTPException(status_code=400, detail="decision_id or city+target_date is required")
    if not request.strategy_revision_id or not request.decision_batch_issued_at:
        raise HTTPException(
            status_code=400,
            detail="bulk paper execution requires strategy_revision_id and decision_batch_issued_at",
        )
    return execute_paper_decisions(
        city_key=request.city,
        target_date=request.target_date,
        limit=request.limit,
        amount=request.amount,
        strategies=request.strategies,
        strategy_revision_id=request.strategy_revision_id,
        decision_batch_issued_at=request.decision_batch_issued_at,
        dry_run=request.dry_run,
    )


@app.get("/api/temperature-fit")
async def temperature_fit():
    return _build_temperature_fit(load_markets())


@app.get("/api/truth/coverage")
async def truth_coverage():
    markets = load_markets()
    return {
        "local": _build_truth_health(markets),
        "db": truth_coverage_summary(),
    }


@app.get("/api/data-readiness")
async def data_readiness():
    payload = build_data_readiness()
    persist_data_readiness(payload)
    _clear_production_validation_cache()
    return payload


@app.get("/api/stations")
async def stations(region: str = "", city: str = "", sync_registry: bool = True):
    sync_result = sync_station_registry() if sync_registry else None
    rows = list_stations(region=region, city=city)
    return {
        "ok": True,
        "sync": sync_result,
        "count": len(rows),
        "stations": rows,
    }


@app.post("/api/stations/{city_key}/enabled")
async def station_enabled(city_key: str, request: StationEnabledUpdate):
    payload = set_station_enabled(city_key, request.enabled, tier=request.tier)
    if not payload.get("ok"):
        raise HTTPException(status_code=404, detail=payload)
    await _refresh_dashboard_cache_once()
    return payload


@app.get("/api/observations")
async def observations(city: str = "", target_date: str = ""):
    return {
        "ok": True,
        "city": city,
        "target_date": target_date,
        "evidence": weather_evidence_summary(city or None, target_date or None),
    }


@app.get("/api/production-validation")
async def production_validation(include_targets: bool = False, refresh: bool = False):
    cache_key = (bool(include_targets),)
    now = time.monotonic()
    cached = production_validation_cache.get(cache_key)
    if (
        not refresh
        and cached is not None
        and now - cached[0] <= PRODUCTION_VALIDATION_CACHE_TTL_SECONDS
    ):
        return cached[1]

    payload = await asyncio.to_thread(
        build_production_validation_report,
        dashboard_runtime=_production_validation_runtime(),
        include_action_targets=include_targets,
    )
    production_validation_cache[cache_key] = (now, payload)
    return payload


@app.get("/api/production-actions")
async def production_actions():
    return {"actions": list_production_actions()}


@app.post("/api/production-actions/run")
async def production_actions_run(request: ProductionActionRequest):
    if request.action_key == "run_paper_validation":
        result = await _run_paper_validation_action(request)
    else:
        result = await asyncio.to_thread(
            run_production_action,
            request.action_key,
            apply=request.apply,
            operator_confirmed=request.operator_confirmed,
            cities=request.cities or [],
            days=request.days,
            limit=request.limit,
            start_date=request.start_date,
            end_date=request.end_date,
            skip_signal_scan=request.skip_signal_scan,
            note=request.note,
            archive_path=request.archive_path,
        )
    if request.apply and result.get("status") == "executed":
        _clear_production_validation_cache()
        await _refresh_dashboard_cache_once()
    return result


async def _run_paper_validation_action(request: ProductionActionRequest):
    action = {
        "label": "Run paper validation",
        "description": "Use the dashboard paper executor to simulate eligible current signals.",
        "requires_operator": True,
        "mutates": True,
    }
    bounded_limit = max(1, min(int(request.limit or 20), 500))
    params = {
        "limit": bounded_limit,
        "note": request.note or "",
    }
    if not request.apply:
        return {
            "ok": True,
            "status": "dry_run",
            "action_key": request.action_key,
            "action": action,
            "params": params,
            "message": "Set apply=true and confirm operator approval to run one controlled paper validation pass.",
        }
    if not request.operator_confirmed:
        return {
            "ok": False,
            "status": "blocked",
            "reason": "operator_confirmation_required",
            "action_key": request.action_key,
            "action": action,
            "params": params,
        }
    if bulk_simulation_lock.locked():
        return {
            "ok": False,
            "status": "blocked",
            "reason": "paper_validation_already_running",
            "action_key": request.action_key,
            "action": action,
            "params": params,
        }
    async with bulk_simulation_lock:
        payload = await asyncio.to_thread(run_paper_validation_tick, apply=True)
    return {
        "ok": bool(payload.get("ok", False)),
        "status": str(payload.get("status") or "blocked"),
        "action_key": request.action_key,
        "action": action,
        "params": params,
        "payload": payload,
    }


@app.post("/api/production-refresh")
async def production_refresh(request: ProductionRefreshRequest):
    if production_refresh_lock.locked():
        current = _read_json(PRODUCTION_REFRESH_PATH, {}) or {}
        return {
            **current,
            "ok": False,
            "running": True,
            "failed_stages": list(dict.fromkeys([*(current.get("failed_stages") or []), "already_running"])),
            "message": "production-refresh is already running",
        }
    cities = ",".join(item.strip() for item in (request.cities or []) if item.strip())
    request_payload = {
        "cities": request.cities or [],
        "days": request.days,
        "limit": request.limit,
        "start_date": request.start_date,
        "end_date": request.end_date,
        "skip_signal_scan": request.skip_signal_scan,
    }
    requested_at = datetime.now(timezone.utc).isoformat()

    def save_progress(progress: dict):
        _save_production_refresh_progress({
            **progress,
            "requested_at": requested_at,
            "request": request_payload,
        })

    async with production_refresh_lock:
        payload = await asyncio.to_thread(
            run_production_refresh,
            cities=cities,
            days=max(1, min(int(request.days or 1), 7)),
            limit=max(1, min(int(request.limit or 20), 500)),
            start_date=request.start_date or "",
            end_date=request.end_date or "",
            scan_signals=not request.skip_signal_scan,
            progress_callback=save_progress,
        )
        saved = {
            **payload,
            "running": False,
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "request": request_payload,
        }
        saved = _save_production_refresh_result(saved)
        log_event(
            "success" if saved.get("ok") else "warning",
            "production-refresh completed" if saved.get("ok") else "production-refresh finished with failed stages",
            {
                "failed_stages": saved.get("failed_stages", []),
                "readiness": saved.get("readiness", {}),
            },
        )
        await _refresh_dashboard_cache_once()
        _clear_production_validation_cache()
    return saved


@app.get("/api/contracts")
async def contracts(status: str = "unverified", city: str = "", limit: int = 25, offset: int = 0):
    return await asyncio.to_thread(
        list_settlement_contracts,
        status=status,
        city=city,
        limit=limit,
        offset=offset,
    )


@app.post("/api/contracts/{contract_id}/verification")
async def verify_contract(contract_id: str, request: ContractVerificationRequest):
    try:
        contract = set_settlement_contract_verification(
            contract_id,
            verified=request.verified,
            reviewer=request.reviewer,
            note=request.note or "",
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="contract_not_found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    readiness = build_data_readiness()
    persist_data_readiness(readiness)
    _clear_production_validation_cache()
    return {"ok": True, "contract": contract, "data_readiness": readiness}


@app.post("/api/contracts/bulk-verification")
async def verify_contracts_bulk(request: BulkContractVerificationRequest):
    result = bulk_settlement_contract_verification(
        contract_ids=request.contract_ids,
        limit=request.limit,
        reviewer=request.reviewer,
        note=request.note or "dashboard bulk review",
        require_auto_verified=True,
        mature_only=request.mature_only,
        apply=request.apply,
    )
    readiness = build_data_readiness()
    persist_data_readiness(readiness)
    _clear_production_validation_cache()
    return {"ok": True, **result, "data_readiness": readiness}


@app.post("/api/weather/backfill-history")
async def weather_backfill_history(request: WeatherHistoryBackfillRequest):
    # Import lazily so dashboard startup stays light and does not couple to the
    # scanner unless the user explicitly requests historical backfill.
    from bot_v2 import LOCATIONS, TIMEZONES

    days = max(1, min(int(request.days or 30), 365))
    requested = set(request.cities or [])
    city_keys = [city for city in LOCATIONS.keys() if not requested or city in requested]
    fetched: list[dict] = []
    errors: list[dict] = []
    for city in city_keys:
        try:
            fetched.extend(fetch_open_meteo_history(
                city,
                LOCATIONS[city],
                TIMEZONES.get(city, "UTC"),
                days=days,
            ))
        except Exception as exc:
            errors.append({"city": city, "error": str(exc)})
    cache = merge_history_points(fetched)
    log_event(
        "info" if not errors else "warning",
        f"历史天气补全：城市 {len(city_keys)}，写入 {len(fetched)} 条，错误 {len(errors)} 个",
        {"errors": errors[:10]},
    )
    return {
        "ok": len(errors) == 0,
        "days": days,
        "cities": len(city_keys),
        "fetched": len(fetched),
        "cached_cities": len(cache),
        "errors": errors,
    }


@app.get("/api/weather/history-cache")
async def weather_history_cache():
    cache = load_history_cache()
    return {
        "cities": len(cache),
        "points": sum(len(points) for points in cache.values()),
        "cache": cache,
    }


@app.get("/api/markets/{market_id}/distribution")
async def market_distribution(market_id: str):
    cached = latest_event_distribution(market_id)
    if cached:
        return cached
    dashboard_payload = build_dashboard_payload()
    for signal in dashboard_payload.get("weather_signals", []):
        if str(signal.get("market_id") or "") == str(market_id):
            return signal.get("distribution") or {"items": []}
    return {"items": [], "normalized": False, "notes": ["distribution_not_found"]}


@app.get("/api/signals/{signal_id}/decision")
async def signal_decision(signal_id: int):
    cached = latest_signal_decision(signal_id)
    if cached:
        return cached
    dashboard_payload = build_dashboard_payload()
    for signal in dashboard_payload.get("weather_signals", []):
        if int(signal.get("id") or 0) == signal_id:
            return signal.get("decision") or {}
    return {"signal_id": signal_id, "action": "unknown", "reasons": ["signal_not_found"]}


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    await websocket.accept()
    last_event_id = 0
    log_pos = BOT_LOG_PATH.stat().st_size if BOT_LOG_PATH.exists() else 0
    try:
        await websocket.send_json({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "success",
            "message": "实时日志已连接",
        })
        while True:
            for event in list_events_after(last_event_id, 50):
                last_event_id = max(last_event_id, int(event.get("id") or 0))
                await websocket.send_json(_event_to_payload(event))
            log_pos, lines = _tail_log_lines(BOT_LOG_PATH, log_pos)
            for line in lines[-30:]:
                await websocket.send_json(_line_to_event(line))
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return


@app.post("/api/run-scan")
async def run_scan():
    log_event("info", "Manual scan requested from dashboard; run weatherbet.py for live scanning.")
    return {"total_signals": len(list_signals(500)), "actionable_signals": len(list_signals(500))}


@app.post("/api/settle-trades")
async def settle_trades():
    result = _settle_dashboard_positions()
    message = (
        f"结算检查：已检查 {result['checked']} 个模拟仓位，"
        f"结算 {result['settled']} 个，待结算 {result['pending']} 个"
    )
    if result["errors"]:
        message += f"，错误 {len(result['errors'])} 个"
    log_event("info" if not result["errors"] else "warning", message, result)
    _clear_production_validation_cache()
    return {
        "ok": True,
        "checked": result["checked"],
        "settled_count": result["settled"],
        "pending_count": result["pending"],
        "errors": result["errors"],
    }


@app.post("/api/bot/start")
async def start_bot():
    raise HTTPException(
        status_code=410,
        detail={
            "code": "legacy_scanner_retired",
            "message": "Legacy weatherbet.py scanner is retired; use the controlled v3 scheduler.",
        },
    )


@app.post("/api/bot/stop")
async def stop_bot():
    global bot_process
    if _bot_running():
        _terminate_pid_tree(bot_process.pid)
        try:
            bot_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        try:
            BOT_PID_PATH.unlink(missing_ok=True)
        except Exception:
            pass
        log_event("warning", "扫描器已从看板停止")
    return {"status": "stopped", "is_running": False}


@app.post("/api/simulation/reset")
async def reset_simulation(update: SimulationReset):
    balance = max(0.0, float(update.balance))
    started_at = datetime.now(timezone.utc).isoformat()
    _write_json(DATA_DIR / "state.json", {
        "balance": balance,
        "starting_balance": balance,
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "peak_balance": balance,
        "simulation_started_at": started_at,
    })
    cleared_positions = 0
    if update.clear_marks:
        reset_signal_marks()
        cleared_positions = _clear_dashboard_positions(started_at)
    payload = update.model_dump()
    payload["cleared_positions"] = cleared_positions
    _clear_production_validation_cache()
    log_event("warning", f"模拟账户已重置为 ${balance:.2f}", payload)
    return {
        "ok": True,
        "balance": balance,
        "simulation_started_at": started_at,
        "cleared_positions": cleared_positions,
    }


def _bulk_simulate_signals_once(log_when_idle=True, limit=None):
    return {
        "ok": False,
        "status": "retired",
        "reason": "legacy_bulk_simulation_retired_use_paper_validation",
        "count": 0,
        "spent": 0.0,
        "skipped": 0,
    }

def _refresh_signal_orderbooks(signals, limit=50):
    market_ids = []
    seen = set()
    for signal in signals:
        market_id = str(signal.get("market_id") or "")
        if not market_id or market_id in seen:
            continue
        market_ids.append(market_id)
        seen.add(market_id)
        if len(market_ids) >= limit:
            break
    if not market_ids:
        return {"requested": 0, "refreshed": 0, "failed": 0}
    client = PolymarketDataClient()
    refreshed = 0
    failed = 0
    for market_id in market_ids:
        try:
            quote = client.quote(market_id)
            if quote.book_source == "clob":
                refreshed += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    return {"requested": len(market_ids), "refreshed": refreshed, "failed": failed}


@app.post("/api/signals/bulk-simulate")
async def bulk_simulate_signals():
    raise HTTPException(
        status_code=410,
        detail="legacy bulk simulation retired; start a paper validation run",
    )


@app.get("/api/simulation/auto")
async def auto_simulation_status():
    return _auto_simulation_state()


@app.post("/api/simulation/auto")
async def update_auto_simulation(update: AutoSimulationUpdate):
    global dashboard_payload_cache
    interval = max(60, min(int(update.interval_seconds or 300), 3600))
    if update.enabled:
        state = _save_auto_simulation_state(
            enabled=False,
            interval_seconds=interval,
            last_error="legacy_auto_simulation_retired_use_paper_validation",
        )
        return {
            "ok": False,
            **state,
            "reason": "legacy_auto_simulation_retired_use_paper_validation",
        }
    state = _save_auto_simulation_state(
        enabled=False,
        interval_seconds=interval,
        last_error=None,
    )
    log_event("warning", "旧版自动模拟保持关闭")
    _clear_production_validation_cache()
    if dashboard_payload_cache is not None:
        payload = dict(dashboard_payload_cache)
        payload["stats"] = {**(payload.get("stats") or {}), "auto_simulation": dict(state)}
        dashboard_payload_cache = payload
    return {"ok": True, **state}


@app.post("/api/signals/{signal_id}/status")
async def signal_status(signal_id: int, update: StatusUpdate):
    note = update.note
    amount = update.amount
    if update.status == "simulated":
        return {
            "ok": False,
            "error": "legacy_signal_simulation_retired_use_paper_validation",
        }
    if amount is not None:
        note = note or f"Paper amount ${amount:.2f}"
    update_signal_status(signal_id, update.status, note, amount)
    log_event("info", f"Signal {signal_id} marked {update.status}", update.model_dump())
    _clear_production_validation_cache()
    return {"ok": True}


@app.get("/api/v3/status")
async def v3_status():
    cfg = load_v3_config()
    summary = v3_dashboard_summary()
    summary["config"] = {
        "live_trading": cfg.live_trading,
        "live_dry_run": cfg.live_dry_run,
        "ai_review_enabled": cfg.ai_review_enabled,
        "ai_required_for_live": cfg.ai_required_for_live,
        "max_order_usd": cfg.live_max_order_usd,
        "canary_max_order_usd": cfg.canary_max_order_usd,
        "daily_max_usd": cfg.live_daily_max_usd,
        "max_open_positions": cfg.live_max_open_positions,
        "truth_provider_mode": cfg.truth_provider_mode,
        "min_independent_settlement_days": cfg.min_independent_settlement_days,
        "visual_crossing_configured": bool(cfg.visual_crossing_key),
        "feishu_configured": bool(cfg.feishu_webhook_url),
        "minimax_configured": bool(cfg.minimax_api_key),
    }
    return summary


@app.post("/api/executor/canary-dry-run")
async def canary_dry_run(update: CanaryDryRunUpdate):
    signal = next(
        (
            s for s in list_signals(500)
            if int(s.get("id") or 0) == update.signal_id and not _is_dashboard_position_import(s)
        ),
        None,
    )
    if not signal:
        return {"ok": False, "status": "blocked", "reason": "signal_not_found"}
    cfg = load_v3_config()
    amount = min(
        float(update.amount) if update.amount is not None else 1.0,
        float(cfg.canary_max_order_usd),
        float(cfg.live_max_order_usd),
    )
    result = LiveExecutor().place_order(signal, amount, force_dry_run=True)
    log_event(
        "info" if result.status == "dry_run" else "warning",
        f"canary dry-run {result.status}: {result.reason or 'ok'}",
        result.payload,
    )
    _clear_production_validation_cache()
    return {
        "ok": result.status == "dry_run",
        "mode": result.mode,
        "status": result.status,
        "order_id": result.order_id,
        "reason": result.reason,
        "payload": result.payload,
    }


@app.post("/api/v3/live-order")
async def v3_live_order(update: LiveOrderUpdate):
    signal = next(
        (
            s for s in list_signals(500)
            if int(s.get("id") or 0) == update.signal_id and not _is_dashboard_position_import(s)
        ),
        None,
    )
    if not signal:
        return {"ok": False, "error": "signal_not_found"}
    markets = load_markets()
    backtest_summary = _build_backtest_summary(markets)
    strategy_readiness = backtest_summary.get("strategy_readiness") or {}
    if not strategy_readiness.get("live_ready"):
        payload = {
            "signal_id": update.signal_id,
            "market_id": signal.get("market_id"),
            "question": signal.get("question"),
            "strategy_readiness": strategy_readiness,
        }
        log_event("warning", "v3 live order blocked: strategy not ready", payload)
        return {
            "ok": False,
            "status": "blocked",
            "reason": "strategy_not_ready",
            "strategy_readiness": strategy_readiness,
            "payload": payload,
        }
    temperature_fit = _build_temperature_fit(markets)
    fit_by_city = {row.get("city_key"): row for row in temperature_fit.get("cities", [])}
    truth_by_city = _truth_city_lookup(_build_truth_health(markets))
    market_by_city_date = {
        (market.get("city"), market.get("date")): market
        for market in markets
        if market.get("city") and market.get("date")
    }
    raw_signal = _read_json_from_text(signal.get("raw_json"), {})
    _fit, _quality_flags, _strategy, gate = _signal_diagnostics_payload(
        signal,
        raw_signal,
        fit_by_city,
        market_by_city_date,
        truth_by_city,
    )
    if not gate.get("live_allowed"):
        payload = {
            "signal_id": update.signal_id,
            "market_id": signal.get("market_id"),
            "question": signal.get("question"),
            "gate": gate,
        }
        log_event("warning", "v3 live order blocked by dashboard gate", payload)
        return {
            "ok": False,
            "status": "blocked",
            "reason": "dashboard_live_gate",
            "gate": gate,
            "payload": payload,
        }
    result = LiveExecutor().place_order(signal, update.amount)
    log_event("info" if result.ok else "warning", f"v3 live order {result.status}: {result.reason or 'ok'}", result.payload)
    _clear_production_validation_cache()
    return {
        "ok": result.ok,
        "mode": result.mode,
        "status": result.status,
        "order_id": result.order_id,
        "reason": result.reason,
        "payload": result.payload,
    }


@app.post("/api/v3/notify-daily")
async def v3_notify_daily():
    summary = v3_dashboard_summary()
    sent = FeishuNotifier().daily_summary(summary)
    log_event("success" if sent else "warning", "v3 daily summary notification requested", {"sent": sent})
    return {"ok": True, "sent": sent, "summary": summary}


app.mount("/static", StaticFiles(directory=DASHBOARD_DIR), name="static")
