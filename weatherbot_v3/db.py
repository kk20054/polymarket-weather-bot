from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR, load_config


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(path: Path | None = None) -> sqlite3.Connection:
    cfg = load_config()
    db_path = path or cfg.v3_db_path
    db_path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    return conn


def init_v3_db(path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS markets (
                market_id TEXT PRIMARY KEY,
                event_slug TEXT,
                event_url TEXT,
                question TEXT,
                city TEXT,
                city_name TEXT,
                target_date TEXT,
                bucket_label TEXT,
                yes_token_id TEXT,
                no_token_id TEXT,
                order_min_size REAL,
                tick_size REAL,
                enable_order_book INTEGER,
                raw_json TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS forecasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT,
                city TEXT,
                target_date TEXT,
                source TEXT,
                model_probability REAL,
                ensemble_mean REAL,
                ensemble_std REAL,
                ensemble_members INTEGER,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS orderbooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT,
                yes_token_id TEXT,
                best_bid REAL,
                best_ask REAL,
                spread REAL,
                volume REAL,
                order_min_size REAL,
                tick_size REAL,
                enable_order_book INTEGER,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                legacy_signal_id INTEGER,
                signal_key TEXT UNIQUE,
                market_id TEXT,
                city TEXT,
                city_name TEXT,
                target_date TEXT,
                bucket_label TEXT,
                event_url TEXT,
                yes_token_id TEXT,
                model_probability REAL,
                market_probability REAL,
                probability_edge REAL,
                ev REAL,
                kelly REAL,
                suggested_size REAL,
                quality_score REAL,
                status TEXT DEFAULT 'candidate',
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ai_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER,
                provider TEXT,
                model TEXT,
                approve INTEGER,
                confidence REAL,
                summary TEXT,
                reasons TEXT,
                vetoes TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS paper_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER,
                idempotency_key TEXT UNIQUE,
                market_id TEXT,
                yes_token_id TEXT,
                side TEXT,
                limit_price REAL,
                amount REAL,
                shares REAL,
                status TEXT,
                failure_reason TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS live_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER,
                idempotency_key TEXT UNIQUE,
                market_id TEXT,
                yes_token_id TEXT,
                side TEXT,
                limit_price REAL,
                amount REAL,
                shares REAL,
                status TEXT,
                dry_run INTEGER,
                clob_order_id TEXT,
                failure_reason TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER,
                order_type TEXT,
                price REAL,
                shares REAL,
                amount REAL,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settlements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT,
                result TEXT,
                actual_temp REAL,
                pnl REAL,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS risk_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                severity TEXT,
                message TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT,
                event_type TEXT,
                status TEXT,
                message TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );
            """
        )


def dump_json(payload: Any) -> str:
    return json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)


def upsert_signal(signal: dict[str, Any], legacy_signal_id: int | None = None) -> int:
    init_v3_db()
    now = utc_now()
    market_id = str(signal.get("market_id") or "")
    signal_key = str(signal.get("signal_key") or f"{market_id}:{signal.get('created_at') or now}")
    raw = signal.get("raw_json")
    if isinstance(raw, str):
        try:
            raw_payload = json.loads(raw)
        except Exception:
            raw_payload = {"raw_json": raw}
    else:
        raw_payload = raw or signal
    probability = _num(signal.get("probability"), _num(signal.get("p"), 0.0))
    price = _num(signal.get("limit_price"), _num(signal.get("entry_price"), 0.0))
    edge = probability - price if probability and price else _num(signal.get("probability_edge"), 0.0)
    ev = _num(signal.get("ev"), 0.0)
    quality = round(max(0.0, min(1.0, (edge * 1.5) + min(max(ev, 0.0), 2.0) / 4.0)), 4)
    row = {
        "legacy_signal_id": legacy_signal_id,
        "signal_key": signal_key,
        "market_id": market_id,
        "city": signal.get("city") or raw_payload.get("city") or "",
        "city_name": signal.get("city_name") or "",
        "target_date": signal.get("date") or signal.get("target_date") or "",
        "bucket_label": signal.get("bucket_label") or "",
        "event_url": signal.get("event_url") or raw_payload.get("event_url") or "",
        "yes_token_id": signal.get("yes_token_id") or raw_payload.get("yes_token_id") or "",
        "model_probability": probability,
        "market_probability": price,
        "probability_edge": edge,
        "ev": ev,
        "kelly": _num(signal.get("kelly"), 0.0),
        "suggested_size": _num(signal.get("amount"), _num(signal.get("cost"), 0.0)),
        "quality_score": quality,
        "status": signal.get("status") or "candidate",
        "raw_json": dump_json(raw_payload),
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO signals (
                legacy_signal_id, signal_key, market_id, city, city_name, target_date,
                bucket_label, event_url, yes_token_id, model_probability,
                market_probability, probability_edge, ev, kelly, suggested_size,
                quality_score, status, raw_json, created_at, updated_at
            ) VALUES (
                :legacy_signal_id, :signal_key, :market_id, :city, :city_name, :target_date,
                :bucket_label, :event_url, :yes_token_id, :model_probability,
                :market_probability, :probability_edge, :ev, :kelly, :suggested_size,
                :quality_score, :status, :raw_json, :created_at, :updated_at
            )
            ON CONFLICT(signal_key) DO UPDATE SET
                market_probability=excluded.market_probability,
                probability_edge=excluded.probability_edge,
                ev=excluded.ev,
                kelly=excluded.kelly,
                suggested_size=excluded.suggested_size,
                quality_score=excluded.quality_score,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            {**row, "created_at": now, "updated_at": now},
        )
        return int(conn.execute("SELECT id FROM signals WHERE signal_key = ?", (signal_key,)).fetchone()["id"])


def insert_orderbook(market_id: str, payload: dict[str, Any]) -> None:
    init_v3_db()
    best_bid = _num(payload.get("bestBid"), _num(payload.get("best_bid"), 0.0))
    best_ask = _num(payload.get("bestAsk"), _num(payload.get("best_ask"), 0.0))
    spread = _num(payload.get("spread"), best_ask - best_bid if best_ask and best_bid else 0.0)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO orderbooks (
                market_id, yes_token_id, best_bid, best_ask, spread, volume,
                order_min_size, tick_size, enable_order_book, raw_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                market_id,
                str(payload.get("yes_token_id") or ""),
                best_bid,
                best_ask,
                spread,
                _num(payload.get("volume"), 0.0),
                _num(payload.get("orderMinSize"), _num(payload.get("order_min_size"), 0.0)),
                _num(payload.get("orderPriceMinTickSize"), _num(payload.get("tick_size"), 0.0)),
                1 if payload.get("enableOrderBook", payload.get("enable_order_book", True)) else 0,
                dump_json(payload),
                utc_now(),
            ),
        )


def insert_ai_review(signal_id: int, review: dict[str, Any]) -> None:
    init_v3_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO ai_reviews (
                signal_id, provider, model, approve, confidence, summary,
                reasons, vetoes, raw_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                review.get("provider", ""),
                review.get("model", ""),
                1 if review.get("approve") else 0,
                _num(review.get("confidence"), 0.0),
                review.get("summary", ""),
                dump_json(review.get("reasons", [])),
                dump_json(review.get("vetoes", [])),
                dump_json(review),
                utc_now(),
            ),
        )


def insert_order(table: str, order: dict[str, Any]) -> int:
    if table not in {"paper_orders", "live_orders"}:
        raise ValueError("invalid order table")
    init_v3_db()
    now = utc_now()
    live_cols = ", dry_run, clob_order_id" if table == "live_orders" else ""
    live_vals = ", :dry_run, :clob_order_id" if table == "live_orders" else ""
    with connect() as conn:
        conn.execute(
            f"""
            INSERT OR IGNORE INTO {table} (
                signal_id, idempotency_key, market_id, yes_token_id, side,
                limit_price, amount, shares, status, failure_reason, raw_json,
                created_at, updated_at{live_cols}
            ) VALUES (
                :signal_id, :idempotency_key, :market_id, :yes_token_id, :side,
                :limit_price, :amount, :shares, :status, :failure_reason, :raw_json,
                :created_at, :updated_at{live_vals}
            )
            """,
            {
                "signal_id": order.get("signal_id"),
                "idempotency_key": order.get("idempotency_key"),
                "market_id": order.get("market_id"),
                "yes_token_id": order.get("yes_token_id"),
                "side": order.get("side", "BUY"),
                "limit_price": _num(order.get("limit_price"), 0.0),
                "amount": _num(order.get("amount"), 0.0),
                "shares": _num(order.get("shares"), 0.0),
                "status": order.get("status", "created"),
                "failure_reason": order.get("failure_reason"),
                "raw_json": dump_json(order),
                "created_at": now,
                "updated_at": now,
                "dry_run": 1 if order.get("dry_run", True) else 0,
                "clob_order_id": order.get("clob_order_id"),
            },
        )
        row = conn.execute(f"SELECT id FROM {table} WHERE idempotency_key = ?", (order.get("idempotency_key"),)).fetchone()
        return int(row["id"]) if row else 0


def log_risk(event_type: str, message: str, severity: str = "warning", payload: dict[str, Any] | None = None) -> None:
    init_v3_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO risk_events (event_type, severity, message, raw_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (event_type, severity, message, dump_json(payload), utc_now()),
        )


def log_notification(channel: str, event_type: str, status: str, message: str, payload: dict[str, Any] | None = None) -> None:
    init_v3_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO notifications (channel, event_type, status, message, raw_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (channel, event_type, status, message, dump_json(payload), utc_now()),
        )


def dashboard_summary() -> dict[str, Any]:
    init_v3_db()
    with connect() as conn:
        def count(sql: str, args: tuple[Any, ...] = ()) -> int:
            return int(conn.execute(sql, args).fetchone()[0])

        return {
            "signals": count("SELECT COUNT(*) FROM signals"),
            "ai_reviews": count("SELECT COUNT(*) FROM ai_reviews"),
            "paper_orders": count("SELECT COUNT(*) FROM paper_orders"),
            "live_orders": count("SELECT COUNT(*) FROM live_orders"),
            "live_open_orders": count("SELECT COUNT(*) FROM live_orders WHERE status IN ('dry_run', 'submitted', 'open')"),
            "risk_events": count("SELECT COUNT(*) FROM risk_events"),
            "notifications": count("SELECT COUNT(*) FROM notifications"),
            "latest_risk_events": [dict(r) for r in conn.execute("SELECT * FROM risk_events ORDER BY id DESC LIMIT 10").fetchall()],
            "latest_live_orders": [dict(r) for r in conn.execute("SELECT * FROM live_orders ORDER BY id DESC LIMIT 10").fetchall()],
            "latest_paper_orders": [dict(r) for r in conn.execute("SELECT * FROM paper_orders ORDER BY id DESC LIMIT 10").fetchall()],
        }


def _num(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default
