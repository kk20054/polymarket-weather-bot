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
                actual_provider TEXT,
                actual_station TEXT,
                actual_confidence REAL,
                calibration_eligible INTEGER,
                pnl REAL,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS market_rules (
                market_id TEXT PRIMARY KEY,
                event_slug TEXT,
                market_slug TEXT,
                question TEXT,
                city TEXT,
                city_name TEXT,
                station_id TEXT,
                station_name TEXT,
                timezone TEXT,
                unit TEXT,
                bucket_low REAL,
                bucket_high REAL,
                metric TEXT,
                resolution_source_text TEXT,
                source_url TEXT,
                truth_confidence REAL,
                confidence_reason TEXT,
                raw_json TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS truth_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                city TEXT,
                city_name TEXT,
                target_date TEXT,
                station_id TEXT,
                station_name TEXT,
                unit TEXT,
                actual_temp REAL,
                provider TEXT,
                source_url TEXT,
                observation_count INTEGER,
                source_confidence REAL,
                calibration_eligible INTEGER,
                reason_if_ineligible TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(city, target_date, station_id, provider)
            );

            CREATE TABLE IF NOT EXISTS forecast_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                city TEXT,
                target_date TEXT,
                source TEXT,
                run_at TEXT,
                horizon TEXT,
                mean_high REAL,
                std_high REAL,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS forecast_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER,
                member_name TEXT,
                high_temp REAL,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS event_distributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT,
                event_slug TEXT,
                signal_id INTEGER,
                sum_probability REAL,
                normalized INTEGER,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS signal_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER UNIQUE,
                market_id TEXT,
                action TEXT,
                live_allowed INTEGER,
                paper_allowed INTEGER,
                reasons TEXT,
                cautions TEXT,
                raw_json TEXT,
                updated_at TEXT NOT NULL
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
        _ensure_columns(conn)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    ensure = {
        "settlements": {
            "actual_provider": "TEXT",
            "actual_station": "TEXT",
            "actual_confidence": "REAL",
            "calibration_eligible": "INTEGER",
        },
        "signals": {
            "decision_json": "TEXT",
        },
        "markets": {
            "station_id": "TEXT",
            "truth_confidence": "REAL",
        },
    }
    for table, columns in ensure.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


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


def upsert_market_rule(rule: dict[str, Any]) -> None:
    init_v3_db()
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO market_rules (
                market_id, event_slug, market_slug, question, city, city_name,
                station_id, station_name, timezone, unit, bucket_low, bucket_high,
                metric, resolution_source_text, source_url, truth_confidence,
                confidence_reason, raw_json, updated_at
            ) VALUES (
                :market_id, :event_slug, :market_slug, :question, :city, :city_name,
                :station_id, :station_name, :timezone, :unit, :bucket_low, :bucket_high,
                :metric, :resolution_source_text, :source_url, :truth_confidence,
                :confidence_reason, :raw_json, :updated_at
            )
            ON CONFLICT(market_id) DO UPDATE SET
                event_slug=excluded.event_slug,
                market_slug=excluded.market_slug,
                question=excluded.question,
                city=excluded.city,
                city_name=excluded.city_name,
                station_id=excluded.station_id,
                station_name=excluded.station_name,
                timezone=excluded.timezone,
                unit=excluded.unit,
                bucket_low=excluded.bucket_low,
                bucket_high=excluded.bucket_high,
                metric=excluded.metric,
                resolution_source_text=excluded.resolution_source_text,
                source_url=excluded.source_url,
                truth_confidence=excluded.truth_confidence,
                confidence_reason=excluded.confidence_reason,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            {**rule, "raw_json": dump_json(rule), "updated_at": now},
        )


def upsert_market_rules(rules: list[dict[str, Any]]) -> None:
    if not rules:
        return
    init_v3_db()
    now = utc_now()
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO market_rules (
                market_id, event_slug, market_slug, question, city, city_name,
                station_id, station_name, timezone, unit, bucket_low, bucket_high,
                metric, resolution_source_text, source_url, truth_confidence,
                confidence_reason, raw_json, updated_at
            ) VALUES (
                :market_id, :event_slug, :market_slug, :question, :city, :city_name,
                :station_id, :station_name, :timezone, :unit, :bucket_low, :bucket_high,
                :metric, :resolution_source_text, :source_url, :truth_confidence,
                :confidence_reason, :raw_json, :updated_at
            )
            ON CONFLICT(market_id) DO UPDATE SET
                event_slug=excluded.event_slug,
                market_slug=excluded.market_slug,
                question=excluded.question,
                city=excluded.city,
                city_name=excluded.city_name,
                station_id=excluded.station_id,
                station_name=excluded.station_name,
                timezone=excluded.timezone,
                unit=excluded.unit,
                bucket_low=excluded.bucket_low,
                bucket_high=excluded.bucket_high,
                metric=excluded.metric,
                resolution_source_text=excluded.resolution_source_text,
                source_url=excluded.source_url,
                truth_confidence=excluded.truth_confidence,
                confidence_reason=excluded.confidence_reason,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            [{**rule, "raw_json": dump_json(rule), "updated_at": now} for rule in rules],
        )


def upsert_truth_observation(observation: dict[str, Any]) -> None:
    init_v3_db()
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO truth_observations (
                city, city_name, target_date, station_id, station_name, unit,
                actual_temp, provider, source_url, observation_count,
                source_confidence, calibration_eligible, reason_if_ineligible,
                raw_json, created_at
            ) VALUES (
                :city, :city_name, :target_date, :station_id, :station_name, :unit,
                :actual_temp, :provider, :source_url, :observation_count,
                :source_confidence, :calibration_eligible, :reason_if_ineligible,
                :raw_json, :created_at
            )
            ON CONFLICT(city, target_date, station_id, provider) DO UPDATE SET
                actual_temp=excluded.actual_temp,
                source_url=excluded.source_url,
                observation_count=excluded.observation_count,
                source_confidence=excluded.source_confidence,
                calibration_eligible=excluded.calibration_eligible,
                reason_if_ineligible=excluded.reason_if_ineligible,
                raw_json=excluded.raw_json,
                created_at=excluded.created_at
            """,
            {
                **observation,
                "calibration_eligible": 1 if observation.get("calibration_eligible") else 0,
                "raw_json": dump_json(observation),
                "created_at": now,
            },
        )


def insert_forecast_run(run: dict[str, Any], members: list[dict[str, Any]] | None = None) -> int:
    init_v3_db()
    now = utc_now()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO forecast_runs (
                city, target_date, source, run_at, horizon, mean_high,
                std_high, raw_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.get("city"),
                run.get("target_date"),
                run.get("source"),
                run.get("run_at"),
                run.get("horizon"),
                _num(run.get("mean_high"), 0.0),
                _num(run.get("std_high"), 0.0),
                dump_json(run),
                now,
            ),
        )
        run_id = int(cur.lastrowid)
        for member in members or []:
            conn.execute(
                "INSERT INTO forecast_members (run_id, member_name, high_temp, raw_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, member.get("member_name"), _num(member.get("high_temp"), 0.0), dump_json(member), now),
            )
        return run_id


def insert_event_distribution(market_id: str, event_slug: str, distribution: dict[str, Any], signal_id: int | None = None) -> None:
    init_v3_db()
    with connect() as conn:
        if signal_id is None:
            conn.execute("DELETE FROM event_distributions WHERE market_id = ? AND signal_id IS NULL", (market_id,))
        else:
            conn.execute("DELETE FROM event_distributions WHERE market_id = ? AND signal_id = ?", (market_id, signal_id))
        conn.execute(
            """
            INSERT INTO event_distributions (
                market_id, event_slug, signal_id, sum_probability,
                normalized, raw_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                market_id,
                event_slug,
                signal_id,
                _num(distribution.get("sum_probability"), 0.0),
                1 if distribution.get("normalized") else 0,
                dump_json(distribution),
                utc_now(),
            ),
        )


def upsert_signal_decision(signal_id: int, decision: dict[str, Any]) -> None:
    init_v3_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO signal_decisions (
                signal_id, market_id, action, live_allowed, paper_allowed,
                reasons, cautions, raw_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_id) DO UPDATE SET
                market_id=excluded.market_id,
                action=excluded.action,
                live_allowed=excluded.live_allowed,
                paper_allowed=excluded.paper_allowed,
                reasons=excluded.reasons,
                cautions=excluded.cautions,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                signal_id,
                decision.get("market_id"),
                decision.get("action"),
                1 if decision.get("live_allowed") else 0,
                1 if decision.get("paper_allowed", True) else 0,
                dump_json(decision.get("reasons", [])),
                dump_json(decision.get("cautions", [])),
                dump_json(decision),
                utc_now(),
            ),
        )
        conn.execute("UPDATE signals SET decision_json = ? WHERE id = ?", (dump_json(decision), signal_id))


def latest_event_distribution(market_id: str) -> dict[str, Any] | None:
    init_v3_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT raw_json FROM event_distributions WHERE market_id = ? ORDER BY id DESC LIMIT 1",
            (market_id,),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["raw_json"])
    except Exception:
        return None


def latest_signal_decision(signal_id: int) -> dict[str, Any] | None:
    init_v3_db()
    with connect() as conn:
        row = conn.execute("SELECT raw_json FROM signal_decisions WHERE signal_id = ?", (signal_id,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["raw_json"])
    except Exception:
        return None


def truth_coverage_summary() -> dict[str, Any]:
    init_v3_db()
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM truth_observations ORDER BY target_date DESC").fetchall()]
    by_city: dict[str, dict[str, Any]] = {}
    for row in rows:
        city = row.get("city") or ""
        item = by_city.setdefault(
            city,
            {
                "city": city,
                "city_name": row.get("city_name") or city,
                "station_id": row.get("station_id") or "",
                "total_observations": 0,
                "eligible_observations": 0,
                "open_meteo_fallbacks": 0,
                "latest_provider": "",
                "latest_date": "",
                "latest_confidence": 0.0,
            },
        )
        item["total_observations"] += 1
        if row.get("calibration_eligible"):
            item["eligible_observations"] += 1
        if row.get("provider") == "open_meteo_archive":
            item["open_meteo_fallbacks"] += 1
        if not item["latest_date"] or str(row.get("target_date") or "") > item["latest_date"]:
            item["latest_date"] = row.get("target_date") or ""
            item["latest_provider"] = row.get("provider") or ""
            item["latest_confidence"] = _num(row.get("source_confidence"), 0.0)
    cities = sorted(by_city.values(), key=lambda row: (row["eligible_observations"], row["total_observations"]), reverse=True)
    total = sum(row["total_observations"] for row in cities)
    eligible = sum(row["eligible_observations"] for row in cities)
    fallbacks = sum(row["open_meteo_fallbacks"] for row in cities)
    return {
        "total_observations": total,
        "eligible_observations": eligible,
        "coverage_rate": round((eligible / total) if total else 0.0, 4),
        "open_meteo_fallbacks": fallbacks,
        "open_meteo_fallback_rate": round((fallbacks / total) if total else 0.0, 4),
        "cities": cities,
    }


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
