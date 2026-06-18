#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite storage for WeatherBot signals and dashboard snapshots."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "weatherbot.db"


def _connect():
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_key TEXT UNIQUE,
                created_at TEXT NOT NULL,
                city TEXT,
                city_name TEXT,
                date TEXT,
                horizon TEXT,
                bucket_label TEXT,
                question TEXT,
                event_url TEXT,
                market_id TEXT,
                yes_token_id TEXT,
                action TEXT,
                limit_price REAL,
                bid_price REAL,
                spread REAL,
                amount REAL,
                sim_amount REAL,
                shares REAL,
                forecast_temp REAL,
                forecast_src TEXT,
                probability REAL,
                ev REAL,
                kelly REAL,
                status TEXT DEFAULT 'signal',
                manual_note TEXT,
                raw_json TEXT
            )
            """
        )
        _ensure_signal_columns(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                raw_json TEXT
            )
            """
        )


def _ensure_signal_columns(conn):
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
    if "sim_amount" not in columns:
        conn.execute("ALTER TABLE signals ADD COLUMN sim_amount REAL")


def log_event(event_type, message, payload=None):
    init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO events (created_at, event_type, message, raw_json) VALUES (?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                event_type,
                message,
                json.dumps(payload or {}, ensure_ascii=False),
            ),
        )


def log_signal(signal, loc, date, horizon, bucket_label, event_url):
    """Store a generated BUY signal. Duplicate keys are ignored."""
    init_db()
    created_at = signal.get("opened_at") or datetime.now(timezone.utc).isoformat()
    signal_key = f"{signal.get('market_id')}:{created_at}"
    row = {
        "signal_key": signal_key,
        "created_at": created_at,
        "city": signal.get("city") or "",
        "city_name": loc.get("name", ""),
        "date": date,
        "horizon": horizon,
        "bucket_label": bucket_label,
        "question": signal.get("question", ""),
        "event_url": event_url or signal.get("event_url", ""),
        "market_id": signal.get("market_id", ""),
        "yes_token_id": signal.get("yes_token_id", ""),
        "action": "BUY YES",
        "limit_price": signal.get("entry_price"),
        "bid_price": signal.get("bid_at_entry"),
        "spread": signal.get("spread"),
        "amount": signal.get("cost"),
        "shares": signal.get("shares"),
        "forecast_temp": signal.get("forecast_temp"),
        "forecast_src": signal.get("forecast_src"),
        "probability": signal.get("p"),
        "ev": signal.get("ev"),
        "kelly": signal.get("kelly"),
        "status": "signal",
        "manual_note": "Open URL, choose exact market, Buy Yes, set limit and amount, review, submit.",
        "raw_json": json.dumps(signal, ensure_ascii=False),
    }
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    with _connect() as conn:
        conn.execute(
            f"INSERT OR IGNORE INTO signals ({columns}) VALUES ({placeholders})",
            tuple(row.values()),
        )


def list_signals(limit=200):
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM signals ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_events(limit=100):
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM events ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def update_signal_status(signal_id, status, manual_note=None, sim_amount=None):
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE signals
            SET status = ?,
                manual_note = COALESCE(?, manual_note),
                sim_amount = COALESCE(?, sim_amount)
            WHERE id = ?
            """,
            (status, manual_note, sim_amount, signal_id),
        )


def upsert_signal_from_market(market):
    pos = market.get("position") or {}
    if not pos:
        return
    loc = {"name": market.get("city_name", market.get("city", ""))}
    unit = "F" if market.get("unit") == "F" else "C"
    bucket_label = f"{pos.get('bucket_low')}-{pos.get('bucket_high')}{unit}"
    signal = dict(pos)
    signal["city"] = market.get("city")
    event_url = pos.get("event_url") or market.get("event_url", "")
    log_signal(signal, loc, market.get("date", ""), "", bucket_label, event_url)
