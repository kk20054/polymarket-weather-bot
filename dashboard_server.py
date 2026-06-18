#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local dashboard server for WeatherBot."""

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from dashboard_db import (
    DB_PATH,
    init_db,
    list_events,
    list_signals,
    log_event,
    update_signal_status,
    upsert_signal_from_market,
)


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
MARKETS_DIR = DATA_DIR / "markets"
DASHBOARD_DIR = ROOT / "dashboard"

app = FastAPI(title="WeatherBot Dashboard", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class StatusUpdate(BaseModel):
    status: str
    note: str | None = None
    amount: float | None = None


def _read_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


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


def build_dashboard_payload():
    init_db()
    markets = load_markets()
    for market in markets:
        upsert_signal_from_market(market)

    state = _read_json(DATA_DIR / "state.json", {})
    open_positions = []
    recent_trades = []
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
            if pos.get("status") == "open":
                open_positions.append(item)
            recent_trades.append({
                "id": int(pos.get("market_id") or 0) if str(pos.get("market_id", "")).isdigit() else len(recent_trades) + 1,
                "market_ticker": pos.get("market_id", ""),
                "platform": "polymarket",
                "event_slug": _event_slug_from_url(event_url) or pos.get("question", ""),
                "direction": "yes",
                "entry_price": pos.get("entry_price") or 0,
                "size": pos.get("cost") or 0,
                "timestamp": pos.get("opened_at") or market.get("created_at", ""),
                "settled": pos.get("status") == "closed",
                "result": result,
                "pnl": pos.get("pnl"),
            })

    signals = list_signals(300)
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
            "actionable": signal.get("status") not in ("skipped",),
            "platform": signal.get("platform") or "polymarket",
            "status": signal.get("status"),
            "limit_price": signal.get("limit_price"),
            "bid_price": signal.get("bid_price"),
            "spread": signal.get("spread"),
            "shares": signal.get("shares"),
            "sim_amount": sim_amount,
            "manual_note": signal.get("manual_note"),
        })
        if signal.get("status") in ("simulated", "bought"):
            simulated_trades.append({
                "id": int(signal.get("id") or 0),
                "market_ticker": signal.get("market_id") or "",
                "platform": "polymarket",
                "event_slug": _event_slug_from_url(signal.get("event_url")) or _clean_text(signal.get("question", "")),
                "direction": "yes",
                "entry_price": signal.get("limit_price") or 0,
                "size": display_amount or 0,
                "timestamp": signal.get("created_at") or "",
                "settled": False,
                "result": "pending",
                "pnl": None,
            })

    starting = state.get("starting_balance", 0) or 0
    balance = state.get("balance", starting) or 0
    total_pnl = round(balance - starting, 2)
    equity_curve = []
    running_pnl = 0.0
    combined_trades = recent_trades + simulated_trades
    for trade in sorted(combined_trades, key=lambda t: t.get("timestamp") or ""):
        if trade.get("pnl") is not None:
            running_pnl += float(trade["pnl"])
            equity_curve.append({
                "timestamp": trade.get("timestamp"),
                "pnl": round(running_pnl, 2),
                "bankroll": round(starting + running_pnl, 2),
            })

    stats = {
        "bankroll": balance,
        "total_trades": state.get("total_trades", 0) + len(simulated_trades),
        "winning_trades": state.get("wins", 0),
        "win_rate": (state.get("wins", 0) / state.get("total_trades", 1)) if state.get("total_trades") else 0,
        "total_pnl": total_pnl,
        "is_running": False,
        "last_run": last_run,
    }

    return {
        "stats": stats,
        "btc_price": None,
        "microstructure": None,
        "windows": [],
        "active_signals": [],
        "recent_trades": sorted(combined_trades, key=lambda t: t.get("timestamp") or "", reverse=True)[:100],
        "equity_curve": equity_curve,
        "calibration": {
            "total_signals": len(signals),
            "total_with_outcome": state.get("wins", 0) + state.get("losses", 0),
            "accuracy": 0,
            "avg_predicted_edge": 0,
            "avg_actual_edge": 0,
            "brier_score": 0,
        },
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
    return [
        {
            "timestamp": event.get("created_at"),
            "type": event.get("event_type"),
            "message": event.get("message"),
            "data": json.loads(event.get("raw_json") or "{}"),
        }
        for event in list_events(limit)
    ]


@app.post("/api/run-scan")
async def run_scan():
    log_event("info", "Manual scan requested from dashboard; run weatherbet.py for live scanning.")
    return {"total_signals": len(list_signals(500)), "actionable_signals": len(list_signals(500))}


@app.post("/api/bot/start")
async def start_bot():
    log_event("info", "Dashboard start pressed; start weatherbet.py in PowerShell to run scanner.")
    return {"status": "manual", "is_running": False}


@app.post("/api/bot/stop")
async def stop_bot():
    log_event("info", "Dashboard pause pressed; stop the weatherbot PowerShell process with Ctrl+C.")
    return {"status": "manual", "is_running": False}


@app.post("/api/signals/{signal_id}/status")
async def signal_status(signal_id: int, update: StatusUpdate):
    note = update.note
    if update.amount is not None:
        note = note or f"Paper amount ${update.amount:.2f}"
    update_signal_status(signal_id, update.status, note, update.amount)
    log_event("info", f"Signal {signal_id} marked {update.status}", update.model_dump())
    return {"ok": True}


app.mount("/static", StaticFiles(directory=DASHBOARD_DIR), name="static")
