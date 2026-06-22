#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local dashboard server for WeatherBot."""

import json
import asyncio
import math
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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
from weatherbot_v3.db import dashboard_summary as v3_dashboard_summary
from weatherbot_v3.db import init_v3_db
from weatherbot_v3.executor import LiveExecutor, PaperExecutor
from weatherbot_v3.migration import migrate_legacy_signals
from weatherbot_v3.notifier import FeishuNotifier


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
MARKETS_DIR = DATA_DIR / "markets"
DASHBOARD_DIR = ROOT / "dashboard"
BOT_LOG_PATH = DATA_DIR / "weatherbet-dashboard.log"
BOT_PID_PATH = DATA_DIR / "weatherbet-dashboard.pid"
bot_process: subprocess.Popen | None = None

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


class SimulationReset(BaseModel):
    balance: float
    clear_marks: bool = False


class LiveOrderUpdate(BaseModel):
    signal_id: int
    amount: float | None = None


def _read_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _write_json(path, payload):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


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


def _position_from_signal(signal, amount, opened_at):
    raw = _read_json_from_text(signal.get("raw_json"), {})
    low, high = _bucket_bounds(signal)
    entry_price = float(signal.get("limit_price") or raw.get("entry_price") or 0)
    bid_price = float(signal.get("bid_price") or raw.get("bid_at_entry") or entry_price)
    shares = round(amount / entry_price, 2) if entry_price > 0 else 0
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


def _open_paper_position(signal, amount, simulation_start, opened_at):
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

    market["position"] = _position_from_signal(signal, amount, opened_at)
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


def _event_to_payload(event):
    return {
        "id": event.get("id"),
        "timestamp": event.get("created_at"),
        "type": event.get("event_type"),
        "message": _repair_display_text(event.get("message")),
        "data": json.loads(event.get("raw_json") or "{}"),
    }


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
    bias = float(fit.get("bias_f") or 0) if fit else 0.0
    forecast_f = record.get("forecast_temp_f")
    adjusted = forecast_f - bias if forecast_f is not None else None
    record["bias_adjusted_forecast_f"] = round(adjusted, 2) if adjusted is not None else None
    record["bias_adjusted_in_bucket"] = _bucket_value_in_range(
        adjusted,
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

    sigma_candidates = [1.5]
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
    record["calibrated_sigma_f"] = round(sigma_f, 2)
    record["calibrated_probability"] = round(calibrated_probability, 4) if calibrated_probability is not None else None
    record["calibrated_prob_edge"] = round(calibrated_probability - entry_price, 4) if calibrated_probability is not None else None
    record["calibrated_ev"] = round(calibrated_ev, 4) if calibrated_ev is not None else None
    record["calibrated_positive_edge"] = bool(
        calibrated_probability is not None
        and calibrated_ev is not None
        and calibrated_probability - entry_price >= 0.08
        and calibrated_ev >= 0.10
    )
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
    ]

    for source in sorted({r.get("source") for r in completed if r.get("source")}):
        source_records = [r for r in completed if r.get("source") == source]
        if len(source_records) >= 3:
            candidates.append((f"source_{source}", f"只做 {source} 来源", lambda r, source=source: r.get("source") == source))

    rows = []
    seen = set()
    for name, description, predicate in candidates:
        group = [r for r in completed if predicate(r)]
        if not group:
            continue
        key = tuple(sorted(r.get("market_id") or "" for r in group))
        if key in seen:
            continue
        seen.add(key)
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

    spread = record.get("spread")
    try:
        spread = float(spread) if spread is not None else None
    except Exception:
        spread = None
    if spread is None:
        cautions.append("spread_missing")
    elif spread > 0.03:
        reasons.append("spread_above_limit")

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
        gate = _historical_gate_replay(record, fit)
        source_key = str(record.get("source") or "").upper()
        source_fit = fit_by_source.get(source_key) or fit_by_source.get("MODEL_BEST")
        _augment_strategy_replay_record(record, fit, source_fit)
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
    brier_values = []
    calibrated_brier_values = []
    for r in resolved:
        y = 1.0 if r["result"] == "win" else 0.0
        if r.get("p") is not None:
            brier_values.append((float(r["p"]) - y) ** 2)
        if r.get("calibrated_probability") is not None:
            calibrated_brier_values.append((float(r["calibrated_probability"]) - y) ** 2)

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
        "brier_score": (sum(brier_values) / len(brier_values)) if brier_values else 0,
        "calibrated_brier_score": (sum(calibrated_brier_values) / len(calibrated_brier_values)) if calibrated_brier_values else 0,
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
        return {"samples": 0, "mae_f": 0, "bias_f": 0, "rmse_f": 0}
    errors = [float(r["error_f"]) for r in records]
    abs_errors = [abs(e) for e in errors]
    return {
        "samples": len(records),
        "mae_f": round(sum(abs_errors) / len(abs_errors), 2),
        "bias_f": round(sum(errors) / len(errors), 2),
        "rmse_f": round((sum(e * e for e in errors) / len(errors)) ** 0.5, 2),
    }


def _build_temperature_fit(markets):
    records = []
    market_keys = set()
    for market in markets:
        actual = market.get("actual_temp")
        snapshots = market.get("forecast_snapshots") or []
        if actual is None or not snapshots:
            continue
        try:
            actual_native = float(actual)
        except Exception:
            continue
        unit = market.get("unit") or "F"
        actual_f = _native_to_f(actual_native, unit) or actual_native
        city = market.get("city") or ""
        date = market.get("date") or ""
        market_keys.add(f"{city}:{date}")
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

    best_records = [r for r in records if r["source"] == "model_best"]
    by_city = {}
    for record in best_records:
        key = record["city_key"] or record["city_name"]
        by_city.setdefault(key, []).append(record)

    city_rows = []
    for key, items in by_city.items():
        summary = _metric_summary(items)
        latest = sorted(items, key=lambda r: r.get("timestamp") or "")[-1]
        city_rows.append({
            "city_key": key,
            "city_name": latest["city_name"],
            "unit": latest["unit"],
            "markets": len({f"{r['city_key']}:{r['target_date']}" for r in items}),
            "latest_date": latest["target_date"],
            "latest_forecast": latest["forecast"],
            "latest_actual": latest["actual"],
            **summary,
        })

    by_source = {}
    for record in records:
        by_source.setdefault(record["source"], []).append(record)
    source_rows = []
    for source, items in by_source.items():
        source_rows.append({
            "source": source,
            "markets": len({f"{r['city_key']}:{r['target_date']}" for r in items}),
            **_metric_summary(items),
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

    return {
        "summary": {
            "markets": len(market_keys),
            **_metric_summary(best_records),
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
            "拟合页基于本地 forecast_snapshots 与 actual_temp，适合发现模型偏差，不等同于完整盘口回放。",
            "MAE/RMSE 统一折算为华氏度，便于跨 °C/°F 城市比较；表格仍展示原市场单位。",
            "下一步交易过滤应要求：样本数足够、城市 MAE 可控、数据源分歧低、盘口 spread/orderMinSize/tick size 合格。",
        ],
    }


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
    mae = float(fit.get("mae_f") or 0) if fit else None
    bias = float(fit.get("bias_f") or 0) if fit else None
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
        notes.append(f"City bias is elevated at {bias:+.1f}F.")

    if not tags:
        tags.append("standard_ev")
    score = max(0.0, min(1.0, 0.45 + sum(score_parts)))
    return {
        "strategy_tags": tags,
        "strategy_score": round(score, 2),
        "strategy_notes": notes[:6],
        "near_lock": near_lock,
        "dispersion_ratio": round(dispersion_ratio, 2) if dispersion_ratio is not None else None,
    }


def _fit_quality_flags(fit):
    flags = []
    if fit:
        if int(fit.get("samples") or 0) < 10:
            flags.append("fit_sample_low")
        if float(fit.get("mae_f") or 0) > 3.0:
            flags.append("city_mae_high")
        if abs(float(fit.get("bias_f") or 0)) > 2.0:
            flags.append("city_bias_high")
    else:
        flags.append("fit_missing")
    return flags


def _live_gate(signal, quality_flags, strategy):
    reasons = []
    cautions = []
    tags = set(strategy.get("strategy_tags") or [])

    if "fit_missing" in quality_flags:
        reasons.append("fit_missing")
    if "city_mae_high" in quality_flags:
        reasons.append("city_mae_high")
    if "city_bias_high" in quality_flags:
        reasons.append("city_bias_high")
    if "fit_sample_low" in quality_flags:
        cautions.append("fit_sample_low")
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

    spread = signal.get("spread")
    try:
        spread = float(spread) if spread is not None else None
    except Exception:
        spread = None
    if spread is None:
        cautions.append("spread_missing")
    elif spread > 0.03:
        reasons.append("spread_above_limit")

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


def _signal_diagnostics_payload(signal, raw_signal, fit_by_city, market_by_city_date):
    fit = fit_by_city.get(signal.get("city")) or {}
    quality_flags = _fit_quality_flags(fit)
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

    bias_f = float((fit or {}).get("bias_f") or raw_signal.get("calibration_bias_f") or 0.0)
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


def build_dashboard_payload():
    init_db()
    markets = load_markets()
    for market in markets:
        upsert_signal_from_market(market)

    state = _read_json(DATA_DIR / "state.json", {})
    simulation_started_at = state.get("simulation_started_at")
    simulation_start = _parse_iso(simulation_started_at)
    open_positions = []
    recent_trades = []
    position_market_ids = set()
    weather_forecasts_by_city = {}
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
            "fit_samples": fit.get("samples"),
            "fit_mae_f": fit.get("mae_f"),
            "fit_bias_f": fit.get("bias_f"),
            "quality_flags": quality_flags,
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
    strategy_readiness = backtest_summary.get("strategy_readiness") or {}
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
    }

    return {
        "stats": stats,
        "v3": v3_dashboard_summary(),
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
        "events": list_events(100),
    }


@app.on_event("startup")
async def startup():
    init_db()
    init_v3_db()
    migrate_legacy_signals(500)
    _cleanup_stale_bot_process()
    log_event("info", "Dashboard started")


@app.get("/")
async def index():
    return FileResponse(DASHBOARD_DIR / "index.html")


@app.get("/api/dashboard")
async def dashboard():
    return build_dashboard_payload()


@app.get("/api/signals")
async def signals():
    return {"signals": list_signals(500)}


@app.get("/api/events")
async def events(limit: int = 50):
    return [_event_to_payload(event) for event in list_events(limit)]


@app.get("/api/backtest")
async def backtest():
    return _build_backtest_summary(load_markets())


@app.get("/api/temperature-fit")
async def temperature_fit():
    return _build_temperature_fit(load_markets())


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
    return {
        "ok": True,
        "checked": result["checked"],
        "settled_count": result["settled"],
        "pending_count": result["pending"],
        "errors": result["errors"],
    }


@app.post("/api/bot/start")
async def start_bot():
    global bot_process
    if _bot_running():
        return {"status": "running", "is_running": True}
    _cleanup_stale_bot_process()
    DATA_DIR.mkdir(exist_ok=True)
    log_file = BOT_LOG_PATH.open("a", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    for proxy_key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        env.pop(proxy_key, None)
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    bot_process = subprocess.Popen(
        [sys.executable, "-u", "weatherbet.py"],
        cwd=ROOT,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    BOT_PID_PATH.write_text(str(bot_process.pid), encoding="utf-8")
    log_event("success", "扫描器已从看板启动；日志写入 data/weatherbet-dashboard.log")
    return {"status": "running", "is_running": True}


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
    log_event("warning", f"模拟账户已重置为 ${balance:.2f}", payload)
    return {
        "ok": True,
        "balance": balance,
        "simulation_started_at": started_at,
        "cleared_positions": cleared_positions,
    }


@app.post("/api/signals/bulk-simulate")
async def bulk_simulate_signals():
    today = _today_str()
    count = 0
    spent = 0.0
    state = _read_json(DATA_DIR / "state.json", {})
    remaining = max(0.0, float(state.get("balance", state.get("starting_balance", 0)) or 0))
    simulation_start = _parse_iso(state.get("simulation_started_at"))
    opened_at = datetime.now(timezone.utc).isoformat()
    current_signals = [
        s for s in list_signals(500)
        if (s.get("date") or "") >= today and not _is_dashboard_position_import(s)
    ]
    candidates = sorted(
        [s for s in current_signals if s.get("status") not in ("skipped", "simulated", "bought")],
        key=lambda s: float(s.get("ev") or 0),
        reverse=True,
    )
    for signal in candidates:
        if (signal.get("date") or "") < today:
            continue
        requested = float(signal.get("sim_amount") or signal.get("amount") or 0)
        if requested <= 0 or remaining <= 0:
            break
        amount = min(requested, remaining)
        result = PaperExecutor().place_order(signal, amount)
        if not result.ok:
            update_signal_status(signal["id"], "skipped", f"v3 paper rejected: {result.reason}", amount)
            continue
        if not _open_paper_position(signal, amount, simulation_start, opened_at):
            continue
        update_signal_status(signal["id"], "simulated", f"Bulk paper amount ${amount:.2f}", amount)
        count += 1
        spent += amount
        remaining -= amount
    if spent > 0:
        state["balance"] = round(max(0.0, float(state.get("balance", 0) or 0) - spent), 2)
        state["total_trades"] = int(state.get("total_trades", 0) or 0) + count
        _write_json(DATA_DIR / "state.json", state)
    log_event("success", f"一键模拟买入 {count} 条当前信号，用额 ${spent:.2f}，剩余额度 ${remaining:.2f}")
    return {"ok": True, "count": count, "spent": round(spent, 2), "remaining": round(remaining, 2)}


@app.post("/api/signals/{signal_id}/status")
async def signal_status(signal_id: int, update: StatusUpdate):
    note = update.note
    amount = update.amount
    if update.status == "simulated":
        signal = next((s for s in list_signals(500) if int(s.get("id") or 0) == signal_id and not _is_dashboard_position_import(s)), None)
        if not signal:
            return {"ok": False, "error": "signal_not_found"}
        state = _read_json(DATA_DIR / "state.json", {})
        cash = max(0.0, float(state.get("balance", state.get("starting_balance", 0)) or 0))
        requested = float(amount if amount is not None else signal.get("amount") or 0)
        amount = min(requested, cash)
        if amount <= 0:
            log_event("warning", f"Signal {signal_id} skipped: no simulation cash available")
            return {"ok": False, "error": "no_cash"}
        result = PaperExecutor().place_order(signal, amount)
        if not result.ok:
            update_signal_status(signal_id, "skipped", f"v3 paper rejected: {result.reason}", amount)
            log_event("warning", f"Signal {signal_id} v3 paper rejected: {result.reason}", result.payload)
            return {"ok": False, "error": result.reason or "paper_rejected"}
        opened_at = datetime.now(timezone.utc).isoformat()
        if _open_paper_position(signal, amount, _parse_iso(state.get("simulation_started_at")), opened_at):
            state["balance"] = round(cash - amount, 2)
            state["total_trades"] = int(state.get("total_trades", 0) or 0) + 1
            _write_json(DATA_DIR / "state.json", state)
    if amount is not None:
        note = note or f"Paper amount ${amount:.2f}"
    update_signal_status(signal_id, update.status, note, amount)
    log_event("info", f"Signal {signal_id} marked {update.status}", update.model_dump())
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
        "daily_max_usd": cfg.live_daily_max_usd,
        "max_open_positions": cfg.live_max_open_positions,
        "feishu_configured": bool(cfg.feishu_webhook_url),
        "minimax_configured": bool(cfg.minimax_api_key),
    }
    return summary


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
