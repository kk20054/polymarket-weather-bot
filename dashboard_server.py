#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local dashboard server for WeatherBot."""

import json
import asyncio
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

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


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
MARKETS_DIR = DATA_DIR / "markets"
DASHBOARD_DIR = ROOT / "dashboard"
BOT_LOG_PATH = DATA_DIR / "weatherbet-dashboard.log"
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
    return str(value).replace("Â°F", "°F").replace("Â°C", "°C").replace("Â°", "°")


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


def _bot_running():
    return bool(bot_process and bot_process.poll() is None)


def _event_to_payload(event):
    return {
        "id": event.get("id"),
        "timestamp": event.get("created_at"),
        "type": event.get("event_type"),
        "message": event.get("message"),
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
    elif "[SKIP]" in line:
        event_type = "warning"
    elif "Error:" in line or "error" in line.lower():
        event_type = "error"
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        "message": line,
        "data": {},
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
            weather_forecasts_by_city[city_key] = {
                "city_key": city_key,
                "city_name": market.get("city_name", city_key),
                "target_date": market.get("date", ""),
                "mean_high": high_f or 0,
                "std_high": 2.0 if market.get("unit") == "F" else 2.2,
                "mean_low": 0,
                "std_low": 0,
                "num_members": 1,
                "ensemble_agreement": 0.75,
            }
        pos = market.get("position")
        if pos:
            position_ts = pos.get("opened_at") or market.get("created_at", "")
            position_in_window = _at_or_after(position_ts, simulation_start)
            if position_in_window:
                position_market_ids.add(str(pos.get("market_id") or ""))
            event_url = pos.get("event_url") or market.get("event_url")
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
                    "direction": "yes",
                    "entry_price": pos.get("entry_price") or 0,
                    "size": pos.get("cost") or 0,
                    "timestamp": position_ts,
                    "settled": pos.get("status") == "closed",
                    "result": result,
                    "pnl": pos.get("pnl"),
                })

    today = _today_str()
    all_signals = [s for s in list_signals(300) if not _is_dashboard_position_import(s)]
    signals_by_market = {str(s.get("market_id") or ""): s for s in all_signals}
    expired_signal_count = len([s for s in all_signals if (s.get("date") or "") < today])
    signals = [s for s in all_signals if (s.get("date") or "") >= today]
    weather_signals = []
    simulated_trades = []
    for signal in signals:
        threshold = signal.get("forecast_temp")
        try:
            threshold = float(threshold) if threshold is not None else 0.0
        except Exception:
            threshold = 0.0
        limit_price = signal.get("limit_price") or 0
        amount = signal.get("amount") or 0
        sim_amount = signal.get("sim_amount")
        display_amount = sim_amount if sim_amount is not None else amount
        effective_status = signal.get("status")
        paper_position = str(signal.get("market_id") or "") in position_market_ids
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
            "model_probability": signal.get("probability") or 0,
            "market_probability": signal.get("limit_price") or 0,
            "edge": signal.get("ev") or 0,
            "confidence": min(0.95, max(0.05, signal.get("probability") or 0.5)),
            "suggested_size": amount,
            "reasoning": (
                f"{signal.get('city_name')} {signal.get('date')} {signal.get('bucket_label')} | "
                f"limit ${limit_price:.3f} | amount ${amount:.2f} | "
                f"source {(signal.get('forecast_src') or '').upper()} | token {signal.get('yes_token_id') or 'unknown'}"
            ),
            "ensemble_mean": signal.get("forecast_temp") or 0,
            "ensemble_std": 0,
            "ensemble_members": 1,
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
        })
        if signal.get("status") in ("simulated", "bought") and str(signal.get("market_id") or "") not in position_market_ids:
            simulated_trades.append({
                "id": int(signal.get("id") or 0),
                "market_ticker": signal.get("market_id") or "",
                "platform": "polymarket",
                "event_slug": _event_slug_from_url(signal.get("event_url")) or _clean_text(signal.get("question", "")),
                "direction": "yes",
                "entry_price": signal.get("limit_price") or 0,
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
    equity = round(cash_balance + reserved_capital, 2)
    total_pnl = round(equity - starting, 2)
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
        "total_signals": len(signals),
        "total_with_outcome": total_with_outcome,
        "accuracy": (wins / total_with_outcome) if total_with_outcome else 0,
        "avg_predicted_edge": (sum(predicted_edges) / len(predicted_edges)) if predicted_edges else 0,
        "avg_actual_edge": (sum(actual_edges) / len(actual_edges)) if actual_edges else 0,
        "brier_score": (sum(brier_values) / len(brier_values)) if brier_values else 0,
    }

    stats = {
        "bankroll": equity,
        "cash_balance": cash_balance,
        "reserved_capital": reserved_capital,
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
        "simulation_started_at": simulation_started_at,
        "scanner_status": "running" if _bot_running() else "stopped",
    }

    return {
        "stats": stats,
        "btc_price": None,
        "microstructure": None,
        "windows": [],
        "active_signals": [],
        "recent_trades": sorted(combined_trades, key=lambda t: t.get("timestamp") or "", reverse=True)[:100],
        "equity_curve": equity_curve,
        "calibration": calibration_summary,
        "weather_signals": weather_signals,
        "weather_forecasts": list(weather_forecasts_by_city.values()),
        "events": list_events(100),
    }


@app.on_event("startup")
async def startup():
    init_db()
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


@app.post("/api/bot/start")
async def start_bot():
    global bot_process
    if _bot_running():
        return {"status": "running", "is_running": True}
    DATA_DIR.mkdir(exist_ok=True)
    log_file = BOT_LOG_PATH.open("a", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    bot_process = subprocess.Popen(
        [sys.executable, "-u", "weatherbet.py"],
        cwd=ROOT,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    log_event("success", "扫描器已从看板启动；日志写入 data/weatherbet-dashboard.log")
    return {"status": "running", "is_running": True}


@app.post("/api/bot/stop")
async def stop_bot():
    global bot_process
    if _bot_running():
        bot_process.terminate()
        try:
            bot_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            bot_process.kill()
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
    if update.clear_marks:
        reset_signal_marks()
    log_event("warning", f"模拟账户已重置为 ${balance:.2f}", update.model_dump())
    return {"ok": True, "balance": balance, "simulation_started_at": started_at}


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


app.mount("/static", StaticFiles(directory=DASHBOARD_DIR), name="static")
