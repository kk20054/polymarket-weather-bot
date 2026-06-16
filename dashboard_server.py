#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local dashboard server for WeatherBot."""

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
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


class StatusUpdate(BaseModel):
    status: str
    note: str | None = None


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


def build_dashboard_payload():
    init_db()
    markets = load_markets()
    for market in markets:
        upsert_signal_from_market(market)

    state = _read_json(DATA_DIR / "state.json", {})
    open_positions = []
    resolved = []
    forecast_points = 0
    market_points = 0

    for market in markets:
        forecast_points += len(market.get("forecast_snapshots", []))
        market_points += len(market.get("market_snapshots", []))
        pos = market.get("position")
        if pos:
            item = {
                "city": market.get("city"),
                "city_name": market.get("city_name"),
                "date": market.get("date"),
                "unit": market.get("unit"),
                "event_url": pos.get("event_url") or market.get("event_url"),
                "question": pos.get("question"),
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
            else:
                resolved.append(item)

    signals = list_signals(300)
    stats = {
        "balance": state.get("balance", 0),
        "starting_balance": state.get("starting_balance", 0),
        "total_trades": state.get("total_trades", 0),
        "wins": state.get("wins", 0),
        "losses": state.get("losses", 0),
        "markets": len(markets),
        "open_positions": len(open_positions),
        "signals": len(signals),
        "forecast_points": forecast_points,
        "market_points": market_points,
        "db_path": str(DB_PATH),
    }

    return {
        "stats": stats,
        "state": state,
        "signals": signals,
        "open_positions": open_positions,
        "closed_positions": resolved,
        "markets": markets[-100:],
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


@app.post("/api/signals/{signal_id}/status")
async def signal_status(signal_id: int, update: StatusUpdate):
    update_signal_status(signal_id, update.status, update.note)
    log_event("info", f"Signal {signal_id} marked {update.status}", update.model_dump())
    return {"ok": True}


app.mount("/static", StaticFiles(directory=DASHBOARD_DIR), name="static")
