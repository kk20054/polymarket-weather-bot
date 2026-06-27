from __future__ import annotations

import json
import hashlib
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
                snapshot_key TEXT UNIQUE,
                market_id TEXT,
                yes_token_id TEXT,
                best_bid REAL,
                best_ask REAL,
                spread REAL,
                volume REAL,
                order_min_size REAL,
                tick_size REAL,
                enable_order_book INTEGER,
                snapshot_type TEXT,
                quote_timestamp TEXT,
                book_hash TEXT,
                bids_json TEXT,
                asks_json TEXT,
                bid_depth REAL,
                ask_depth REAL,
                source_url TEXT,
                raw_response_hash TEXT,
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

            CREATE TABLE IF NOT EXISTS truth_observation_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                truth_key TEXT NOT NULL,
                truth_version TEXT NOT NULL,
                supersedes_truth_id INTEGER,
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
                observed_at TEXT,
                retrieved_at TEXT,
                is_preliminary INTEGER,
                is_final INTEGER,
                quality_flags TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(truth_key, truth_version)
            );

            CREATE TABLE IF NOT EXISTS settlement_contracts (
                contract_id TEXT PRIMARY KEY,
                event_slug TEXT UNIQUE,
                city TEXT,
                city_name TEXT,
                target_local_date TEXT,
                station_id TEXT,
                station_name TEXT,
                timezone TEXT,
                unit TEXT,
                metric TEXT,
                rounding_rule TEXT,
                bucket_boundary TEXT,
                resolution_source_text TEXT,
                source_url TEXT,
                truth_provider_priority TEXT,
                rule_version TEXT,
                registry_version TEXT,
                parse_confidence REAL,
                confidence_reason TEXT,
                auto_verified_at TEXT,
                manual_verified_at TEXT,
                manual_verified_by TEXT,
                manual_verification_note TEXT,
                verification_evidence TEXT,
                raw_json TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS forecast_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_key TEXT UNIQUE,
                city TEXT,
                target_date TEXT,
                source TEXT,
                provider TEXT,
                model TEXT,
                model_version TEXT,
                run_type TEXT,
                run_at TEXT,
                retrieved_at TEXT,
                valid_at TEXT,
                horizon TEXT,
                lead_hours REAL,
                latitude REAL,
                longitude REAL,
                station_id TEXT,
                timezone TEXT,
                unit TEXT,
                mean_high REAL,
                std_high REAL,
                member_count INTEGER,
                source_url TEXT,
                raw_response_hash TEXT,
                data_license TEXT,
                quality_flags TEXT,
                training_eligible INTEGER,
                ineligibility_reason TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS forecast_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER,
                member_name TEXT,
                high_temp REAL,
                member_id TEXT,
                hourly_json TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(run_id, member_id)
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

            CREATE TABLE IF NOT EXISTS data_qualification_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_version TEXT NOT NULL,
                status TEXT NOT NULL,
                score REAL NOT NULL,
                live_allowed INTEGER NOT NULL,
                raw_json TEXT NOT NULL,
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
        "market_rules": {
            "exchange_market_id": "TEXT",
            "contract_id": "TEXT",
            "target_local_date": "TEXT",
            "bucket_boundary": "TEXT",
            "rounding_rule": "TEXT",
            "truth_provider_priority": "TEXT",
            "rule_version": "TEXT",
            "registry_version": "TEXT",
            "parsed_at": "TEXT",
            "manual_verified_at": "TEXT",
        },
        "settlement_contracts": {
            "manual_verified_by": "TEXT",
            "manual_verification_note": "TEXT",
        },
        "truth_observations": {
            "truth_version": "TEXT",
            "supersedes_truth_id": "INTEGER",
        },
        "forecast_runs": {
            "run_key": "TEXT",
            "provider": "TEXT",
            "model": "TEXT",
            "model_version": "TEXT",
            "run_type": "TEXT",
            "retrieved_at": "TEXT",
            "valid_at": "TEXT",
            "lead_hours": "REAL",
            "latitude": "REAL",
            "longitude": "REAL",
            "station_id": "TEXT",
            "timezone": "TEXT",
            "unit": "TEXT",
            "member_count": "INTEGER",
            "source_url": "TEXT",
            "raw_response_hash": "TEXT",
            "data_license": "TEXT",
            "quality_flags": "TEXT",
            "training_eligible": "INTEGER",
            "ineligibility_reason": "TEXT",
        },
        "forecast_members": {
            "member_id": "TEXT",
            "hourly_json": "TEXT",
        },
        "orderbooks": {
            "snapshot_key": "TEXT",
            "snapshot_type": "TEXT",
            "quote_timestamp": "TEXT",
            "book_hash": "TEXT",
            "bids_json": "TEXT",
            "asks_json": "TEXT",
            "bid_depth": "REAL",
            "ask_depth": "REAL",
            "source_url": "TEXT",
            "raw_response_hash": "TEXT",
        },
    }
    for table, columns in ensure.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_forecast_runs_run_key ON forecast_runs(run_key)")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_forecast_members_run_member "
        "ON forecast_members(run_id, member_id)"
    )
    conn.execute(
        """
        UPDATE forecast_runs
        SET training_eligible = 0,
            ineligibility_reason = COALESCE(ineligibility_reason, 'legacy_run_before_training_gate')
        WHERE training_eligible IS NULL
        """
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_orderbooks_snapshot_key ON orderbooks(snapshot_key)")


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


def insert_orderbook(market_id: str, payload: dict[str, Any]) -> int:
    init_v3_db()
    bids = _levels(payload.get("bids"))
    asks = _levels(payload.get("asks"))
    best_bid = max((level["price"] for level in bids), default=_num(payload.get("bestBid"), _num(payload.get("best_bid"), 0.0)))
    best_ask = min((level["price"] for level in asks), default=_num(payload.get("bestAsk"), _num(payload.get("best_ask"), 0.0)))
    spread = _num(payload.get("spread"), best_ask - best_bid if best_ask and best_bid else 0.0)
    raw_response_hash = str(payload.get("raw_response_hash") or _json_hash(payload))
    snapshot_key = str(
        payload.get("snapshot_key")
        or f"{payload.get('yes_token_id') or payload.get('asset_id') or market_id}:{payload.get('hash') or raw_response_hash}"
    )
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO orderbooks (
                snapshot_key, market_id, yes_token_id, best_bid, best_ask, spread,
                volume, order_min_size, tick_size, enable_order_book, snapshot_type,
                quote_timestamp, book_hash, bids_json, asks_json, bid_depth,
                ask_depth, source_url, raw_response_hash, raw_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_key) DO UPDATE SET
                best_bid=excluded.best_bid,
                best_ask=excluded.best_ask,
                spread=excluded.spread,
                bids_json=excluded.bids_json,
                asks_json=excluded.asks_json,
                bid_depth=excluded.bid_depth,
                ask_depth=excluded.ask_depth,
                quote_timestamp=excluded.quote_timestamp,
                raw_json=excluded.raw_json,
                created_at=excluded.created_at
            """,
            (
                snapshot_key,
                market_id,
                str(payload.get("yes_token_id") or payload.get("asset_id") or ""),
                best_bid,
                best_ask,
                spread,
                _num(payload.get("volume"), 0.0),
                _num(payload.get("orderMinSize"), _num(payload.get("order_min_size"), _num(payload.get("min_order_size"), 0.0))),
                _num(payload.get("orderPriceMinTickSize"), _num(payload.get("tick_size"), 0.0)),
                1 if payload.get("enableOrderBook", payload.get("enable_order_book", True)) else 0,
                str(payload.get("snapshot_type") or ("clob" if bids or asks else "gamma")),
                str(payload.get("quote_timestamp") or payload.get("timestamp") or ""),
                str(payload.get("hash") or ""),
                dump_json(bids),
                dump_json(asks),
                round(sum(level["size"] for level in bids), 6),
                round(sum(level["size"] for level in asks), 6),
                str(payload.get("source_url") or ""),
                raw_response_hash,
                dump_json(payload),
                utc_now(),
            ),
        )
        return int(conn.execute("SELECT id FROM orderbooks WHERE snapshot_key = ?", (snapshot_key,)).fetchone()["id"])


def _levels(raw: Any) -> list[dict[str, float]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    levels = []
    for item in raw or []:
        try:
            levels.append({"price": float(item.get("price")), "size": float(item.get("size"))})
        except Exception:
            continue
    return levels


def _json_hash(payload: Any) -> str:
    encoded = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
    rule = _normalize_market_rule(rule, now)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO market_rules (
                market_id, exchange_market_id, event_slug, market_slug, question, city, city_name,
                station_id, station_name, timezone, unit, bucket_low, bucket_high,
                metric, resolution_source_text, source_url, truth_confidence,
                confidence_reason, contract_id, target_local_date, bucket_boundary,
                rounding_rule, truth_provider_priority, rule_version, registry_version,
                parsed_at, manual_verified_at, raw_json, updated_at
            ) VALUES (
                :market_id, :exchange_market_id, :event_slug, :market_slug, :question, :city, :city_name,
                :station_id, :station_name, :timezone, :unit, :bucket_low, :bucket_high,
                :metric, :resolution_source_text, :source_url, :truth_confidence,
                :confidence_reason, :contract_id, :target_local_date, :bucket_boundary,
                :rounding_rule, :truth_provider_priority, :rule_version, :registry_version,
                :parsed_at, :manual_verified_at, :raw_json, :updated_at
            )
            ON CONFLICT(market_id) DO UPDATE SET
                event_slug=excluded.event_slug,
                exchange_market_id=excluded.exchange_market_id,
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
                contract_id=excluded.contract_id,
                target_local_date=excluded.target_local_date,
                bucket_boundary=excluded.bucket_boundary,
                rounding_rule=excluded.rounding_rule,
                truth_provider_priority=excluded.truth_provider_priority,
                rule_version=excluded.rule_version,
                registry_version=excluded.registry_version,
                parsed_at=excluded.parsed_at,
                manual_verified_at=COALESCE(excluded.manual_verified_at, market_rules.manual_verified_at),
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            {**rule, "raw_json": dump_json(rule), "updated_at": now},
        )


def upsert_market_rules(rules: list[dict[str, Any]], prune_missing: bool = False) -> None:
    if not rules:
        return
    init_v3_db()
    now = utc_now()
    normalized = [_normalize_market_rule(rule, now, duplicate_market_ids=_duplicate_market_ids(rules)) for rule in rules]
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO market_rules (
                market_id, exchange_market_id, event_slug, market_slug, question, city, city_name,
                station_id, station_name, timezone, unit, bucket_low, bucket_high,
                metric, resolution_source_text, source_url, truth_confidence,
                confidence_reason, contract_id, target_local_date, bucket_boundary,
                rounding_rule, truth_provider_priority, rule_version, registry_version,
                parsed_at, manual_verified_at, raw_json, updated_at
            ) VALUES (
                :market_id, :exchange_market_id, :event_slug, :market_slug, :question, :city, :city_name,
                :station_id, :station_name, :timezone, :unit, :bucket_low, :bucket_high,
                :metric, :resolution_source_text, :source_url, :truth_confidence,
                :confidence_reason, :contract_id, :target_local_date, :bucket_boundary,
                :rounding_rule, :truth_provider_priority, :rule_version, :registry_version,
                :parsed_at, :manual_verified_at, :raw_json, :updated_at
            )
            ON CONFLICT(market_id) DO UPDATE SET
                event_slug=excluded.event_slug,
                exchange_market_id=excluded.exchange_market_id,
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
                contract_id=excluded.contract_id,
                target_local_date=excluded.target_local_date,
                bucket_boundary=excluded.bucket_boundary,
                rounding_rule=excluded.rounding_rule,
                truth_provider_priority=excluded.truth_provider_priority,
                rule_version=excluded.rule_version,
                registry_version=excluded.registry_version,
                parsed_at=excluded.parsed_at,
                manual_verified_at=COALESCE(excluded.manual_verified_at, market_rules.manual_verified_at),
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            [{**rule, "raw_json": dump_json(rule), "updated_at": now} for rule in normalized],
        )
        if prune_missing:
            keep_ids = [str(rule.get("market_id") or "") for rule in normalized if rule.get("market_id")]
            if keep_ids:
                conn.execute("CREATE TEMP TABLE IF NOT EXISTS _market_rule_keep (market_id TEXT PRIMARY KEY)")
                conn.execute("DELETE FROM _market_rule_keep")
                conn.executemany("INSERT OR IGNORE INTO _market_rule_keep (market_id) VALUES (?)", [(item,) for item in keep_ids])
                conn.execute("DELETE FROM market_rules WHERE market_id NOT IN (SELECT market_id FROM _market_rule_keep)")
                conn.execute("DROP TABLE _market_rule_keep")


def _duplicate_market_ids(rules: list[dict[str, Any]]) -> set[str]:
    counts: dict[str, int] = {}
    for rule in rules:
        market_id = str(rule.get("market_id") or "")
        if market_id:
            counts[market_id] = counts.get(market_id, 0) + 1
    return {market_id for market_id, count in counts.items() if count > 1}


def _normalize_market_rule(
    rule: dict[str, Any],
    now: str,
    duplicate_market_ids: set[str] | None = None,
) -> dict[str, Any]:
    exchange_market_id = str(rule.get("exchange_market_id") or rule.get("market_id") or "")
    market_id = str(rule.get("market_id") or "")
    if not market_id or market_id in (duplicate_market_ids or set()):
        basis = "|".join(
            [
                str(rule.get("event_slug") or rule.get("contract_id") or ""),
                str(rule.get("question") or ""),
                str(rule.get("bucket_low") if rule.get("bucket_low") is not None else ""),
                str(rule.get("bucket_high") if rule.get("bucket_high") is not None else ""),
            ]
        )
        market_id = "rule:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
    event_slug = str(rule.get("event_slug") or "")
    priority = rule.get("truth_provider_priority") or [
        "polymarket_resolved",
        "official_station",
        "visual_crossing_station",
        "open_meteo_archive",
    ]
    if not isinstance(priority, str):
        priority = dump_json(priority)
    return {
        **rule,
        "market_id": market_id,
        "exchange_market_id": exchange_market_id,
        "contract_id": str(rule.get("contract_id") or event_slug),
        "target_local_date": str(rule.get("target_local_date") or rule.get("target_date") or ""),
        "bucket_boundary": str(rule.get("bucket_boundary") or "inclusive"),
        "rounding_rule": str(rule.get("rounding_rule") or "source_reported_daily_high"),
        "truth_provider_priority": priority,
        "rule_version": str(rule.get("rule_version") or "settlement-rule-v1"),
        "registry_version": str(rule.get("registry_version") or "airport-settlement-registry-v1"),
        "parsed_at": str(rule.get("parsed_at") or now),
        "manual_verified_at": rule.get("manual_verified_at"),
    }


def upsert_truth_observation(observation: dict[str, Any]) -> None:
    init_v3_db()
    now = utc_now()
    _, truth_version, supersedes_truth_id = append_truth_observation(observation)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO truth_observations (
                city, city_name, target_date, station_id, station_name, unit,
                actual_temp, provider, source_url, observation_count,
                source_confidence, calibration_eligible, reason_if_ineligible,
                truth_version, supersedes_truth_id, raw_json, created_at
            ) VALUES (
                :city, :city_name, :target_date, :station_id, :station_name, :unit,
                :actual_temp, :provider, :source_url, :observation_count,
                :source_confidence, :calibration_eligible, :reason_if_ineligible,
                :truth_version, :supersedes_truth_id, :raw_json, :created_at
            )
            ON CONFLICT(city, target_date, station_id, provider) DO UPDATE SET
                actual_temp=excluded.actual_temp,
                source_url=excluded.source_url,
                observation_count=excluded.observation_count,
                source_confidence=excluded.source_confidence,
                calibration_eligible=excluded.calibration_eligible,
                reason_if_ineligible=excluded.reason_if_ineligible,
                truth_version=excluded.truth_version,
                supersedes_truth_id=excluded.supersedes_truth_id,
                raw_json=excluded.raw_json,
                created_at=excluded.created_at
            """,
            {
                **observation,
                "calibration_eligible": 1 if observation.get("calibration_eligible") else 0,
                "truth_version": truth_version,
                "supersedes_truth_id": supersedes_truth_id,
                "raw_json": dump_json(observation),
                "created_at": now,
            },
        )


def append_truth_observation(observation: dict[str, Any]) -> tuple[int, str, int | None]:
    init_v3_db()
    now = utc_now()
    truth_key = ":".join(
        [
            str(observation.get("city") or ""),
            str(observation.get("target_date") or ""),
            str(observation.get("station_id") or ""),
            str(observation.get("provider") or ""),
        ]
    )
    version_payload = {
        key: observation.get(key)
        for key in (
            "actual_temp",
            "observation_count",
            "source_confidence",
            "calibration_eligible",
            "reason_if_ineligible",
            "source_url",
            "observed_at",
            "is_preliminary",
            "is_final",
            "quality_flags",
        )
    }
    truth_version = hashlib.sha256(dump_json(version_payload).encode("utf-8")).hexdigest()
    with connect() as conn:
        existing = conn.execute(
            "SELECT id, truth_version FROM truth_observation_versions WHERE truth_key = ? ORDER BY id DESC LIMIT 1",
            (truth_key,),
        ).fetchone()
        if existing and existing["truth_version"] == truth_version:
            return int(existing["id"]), truth_version, None
        supersedes_truth_id = int(existing["id"]) if existing else None
        conn.execute(
            """
            INSERT INTO truth_observation_versions (
                truth_key, truth_version, supersedes_truth_id, city, city_name,
                target_date, station_id, station_name, unit, actual_temp, provider,
                source_url, observation_count, source_confidence,
                calibration_eligible, reason_if_ineligible, observed_at,
                retrieved_at, is_preliminary, is_final, quality_flags,
                raw_json, created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                truth_key,
                truth_version,
                supersedes_truth_id,
                observation.get("city"),
                observation.get("city_name"),
                observation.get("target_date"),
                observation.get("station_id"),
                observation.get("station_name"),
                observation.get("unit"),
                observation.get("actual_temp"),
                observation.get("provider"),
                observation.get("source_url"),
                int(observation.get("observation_count") or 0),
                _num(observation.get("source_confidence"), 0.0),
                1 if observation.get("calibration_eligible") else 0,
                observation.get("reason_if_ineligible"),
                observation.get("observed_at"),
                observation.get("retrieved_at") or now,
                1 if observation.get("is_preliminary") else 0,
                1 if observation.get("is_final") else 0,
                dump_json(observation.get("quality_flags", [])),
                dump_json(observation),
                now,
            ),
        )
        row = conn.execute(
            "SELECT id FROM truth_observation_versions WHERE truth_key = ? AND truth_version = ?",
            (truth_key, truth_version),
        ).fetchone()
        return int(row["id"]), truth_version, supersedes_truth_id


def upsert_settlement_contracts(contracts: list[dict[str, Any]]) -> None:
    if not contracts:
        return
    init_v3_db()
    now = utc_now()
    rows = []
    for contract in contracts:
        event_slug = str(contract.get("event_slug") or "")
        rows.append({
            **contract,
            "contract_id": str(contract.get("contract_id") or event_slug),
            "event_slug": event_slug,
            "truth_provider_priority": dump_json(contract.get("truth_provider_priority", [])),
            "verification_evidence": dump_json(contract.get("verification_evidence", [])),
            "manual_verified_by": contract.get("manual_verified_by"),
            "manual_verification_note": contract.get("manual_verification_note"),
            "raw_json": dump_json(contract),
            "updated_at": now,
        })
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO settlement_contracts (
                contract_id, event_slug, city, city_name, target_local_date,
                station_id, station_name, timezone, unit, metric, rounding_rule,
                bucket_boundary, resolution_source_text, source_url,
                truth_provider_priority, rule_version, registry_version,
                parse_confidence, confidence_reason, auto_verified_at,
                manual_verified_at, manual_verified_by, manual_verification_note,
                verification_evidence, raw_json, updated_at
            ) VALUES (
                :contract_id, :event_slug, :city, :city_name, :target_local_date,
                :station_id, :station_name, :timezone, :unit, :metric, :rounding_rule,
                :bucket_boundary, :resolution_source_text, :source_url,
                :truth_provider_priority, :rule_version, :registry_version,
                :parse_confidence, :confidence_reason, :auto_verified_at,
                :manual_verified_at, :manual_verified_by, :manual_verification_note,
                :verification_evidence, :raw_json, :updated_at
            )
            ON CONFLICT(contract_id) DO UPDATE SET
                city=excluded.city,
                city_name=excluded.city_name,
                target_local_date=excluded.target_local_date,
                station_id=excluded.station_id,
                station_name=excluded.station_name,
                timezone=excluded.timezone,
                unit=excluded.unit,
                metric=excluded.metric,
                rounding_rule=excluded.rounding_rule,
                bucket_boundary=excluded.bucket_boundary,
                resolution_source_text=excluded.resolution_source_text,
                source_url=excluded.source_url,
                truth_provider_priority=excluded.truth_provider_priority,
                rule_version=excluded.rule_version,
                registry_version=excluded.registry_version,
                parse_confidence=excluded.parse_confidence,
                confidence_reason=excluded.confidence_reason,
                auto_verified_at=excluded.auto_verified_at,
                manual_verified_at=COALESCE(excluded.manual_verified_at, settlement_contracts.manual_verified_at),
                manual_verified_by=COALESCE(excluded.manual_verified_by, settlement_contracts.manual_verified_by),
                manual_verification_note=COALESCE(excluded.manual_verification_note, settlement_contracts.manual_verification_note),
                verification_evidence=excluded.verification_evidence,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            rows,
        )


def list_settlement_contracts(
    status: str = "all",
    city: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    init_v3_db()
    where = []
    params: list[Any] = []
    if status == "verified":
        where.append("manual_verified_at IS NOT NULL AND manual_verified_at != ''")
    elif status == "unverified":
        where.append("(manual_verified_at IS NULL OR manual_verified_at = '')")
    elif status == "auto":
        where.append("auto_verified_at IS NOT NULL AND auto_verified_at != ''")
    if city:
        where.append("city = ?")
        params.append(city)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    with connect() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) FROM settlement_contracts {clause}", params).fetchone()[0])
        rows = [
            _decode_contract_row(dict(row))
            for row in conn.execute(
                f"""
                SELECT *
                FROM settlement_contracts
                {clause}
                ORDER BY
                    CASE WHEN manual_verified_at IS NULL OR manual_verified_at = '' THEN 0 ELSE 1 END,
                    target_local_date DESC,
                    city,
                    event_slug
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()
        ]
        summary = dict(
            conn.execute(
                """
                SELECT
                    COUNT(*) contracts,
                    SUM(CASE WHEN manual_verified_at IS NOT NULL AND manual_verified_at != '' THEN 1 ELSE 0 END) manual_verified,
                    SUM(CASE WHEN auto_verified_at IS NOT NULL AND auto_verified_at != '' THEN 1 ELSE 0 END) auto_verified
                FROM settlement_contracts
                """
            ).fetchone()
        )
    contracts = int(summary.get("contracts") or 0)
    manual_verified = int(summary.get("manual_verified") or 0)
    auto_verified = int(summary.get("auto_verified") or 0)
    return {
        "status": status,
        "city": city,
        "limit": limit,
        "offset": offset,
        "total": total,
        "summary": {
            "contracts": contracts,
            "manual_verified": manual_verified,
            "unverified": max(0, contracts - manual_verified),
            "auto_verified": auto_verified,
            "manual_progress": round((manual_verified / contracts) if contracts else 0.0, 4),
        },
        "contracts": rows,
    }


def set_settlement_contract_verification(
    contract_id: str,
    verified: bool,
    reviewer: str = "",
    note: str = "",
) -> dict[str, Any]:
    init_v3_db()
    contract_id = str(contract_id or "").strip()
    if not contract_id:
        raise ValueError("contract_id is required")
    now = utc_now()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM settlement_contracts WHERE contract_id = ? OR event_slug = ?",
            (contract_id, contract_id),
        ).fetchone()
        if not row:
            raise KeyError(contract_id)
        actual_id = str(row["contract_id"] or contract_id)
        verified_at = now if verified else None
        conn.execute(
            """
            UPDATE settlement_contracts
            SET manual_verified_at = ?,
                manual_verified_by = ?,
                manual_verification_note = ?,
                updated_at = ?
            WHERE contract_id = ?
            """,
            (verified_at, reviewer, note, now, actual_id),
        )
        conn.execute(
            """
            UPDATE market_rules
            SET manual_verified_at = ?
            WHERE contract_id = ? OR event_slug = ?
            """,
            (verified_at, actual_id, str(row["event_slug"] or "")),
        )
        updated = conn.execute("SELECT * FROM settlement_contracts WHERE contract_id = ?", (actual_id,)).fetchone()
    return _decode_contract_row(dict(updated))


def bulk_settlement_contract_verification(
    contract_ids: list[str] | None = None,
    limit: int = 5,
    reviewer: str = "",
    note: str = "",
    require_auto_verified: bool = True,
    mature_only: bool = False,
    apply: bool = False,
) -> dict[str, Any]:
    init_v3_db()
    limit = max(1, min(int(limit or 5), 50))
    contract_ids = [str(item).strip() for item in (contract_ids or []) if str(item).strip()]
    where = ["(manual_verified_at IS NULL OR manual_verified_at = '')"]
    params: list[Any] = []
    if require_auto_verified:
        where.append("auto_verified_at IS NOT NULL AND auto_verified_at != ''")
    if contract_ids:
        placeholders = ",".join("?" for _ in contract_ids)
        where.append(f"(contract_id IN ({placeholders}) OR event_slug IN ({placeholders}))")
        params.extend(contract_ids)
        params.extend(contract_ids)
    clause = " AND ".join(where)
    now = utc_now()
    query_limit = 500 if mature_only and not contract_ids else limit
    with connect() as conn:
        candidate_rows = [
            _decode_contract_row(dict(row))
            for row in conn.execute(
                f"""
                SELECT *
                FROM settlement_contracts
                WHERE {clause}
                ORDER BY target_local_date DESC, city, event_slug
                LIMIT ?
                """,
                [*params, query_limit],
            ).fetchall()
        ]
        rows = [
            row for row in candidate_rows
            if not mature_only or _contract_is_mature(row)
        ][:limit]
        selected_ids = [str(row["contract_id"]) for row in rows]
        skipped_requested = [
            item for item in contract_ids
            if item not in selected_ids and item not in {str(row.get("event_slug") or "") for row in rows}
        ]
        if apply and selected_ids:
            placeholders = ",".join("?" for _ in selected_ids)
            conn.execute(
                f"""
                UPDATE settlement_contracts
                SET manual_verified_at = ?,
                    manual_verified_by = ?,
                    manual_verification_note = ?,
                    updated_at = ?
                WHERE contract_id IN ({placeholders})
                """,
                [now, reviewer, note, now, *selected_ids],
            )
            conn.execute(
                f"""
                UPDATE market_rules
                SET manual_verified_at = ?
                WHERE contract_id IN ({placeholders}) OR event_slug IN (
                    SELECT event_slug FROM settlement_contracts WHERE contract_id IN ({placeholders})
                )
                """,
                [now, *selected_ids, *selected_ids],
            )
            rows = [
                _decode_contract_row(dict(row))
                for row in conn.execute(
                    f"SELECT * FROM settlement_contracts WHERE contract_id IN ({placeholders}) ORDER BY target_local_date DESC, city, event_slug",
                    selected_ids,
                ).fetchall()
            ]
    return {
        "ok": True,
        "applied": bool(apply),
        "selected": len(rows),
        "verified": len(rows) if apply else 0,
        "skipped_requested": skipped_requested,
        "require_auto_verified": require_auto_verified,
        "mature_only": mature_only,
        "contracts": rows,
    }


def _contract_is_mature(contract: dict[str, Any]) -> bool:
    from .model_dataset import is_settlement_pending

    target_date = str(contract.get("target_local_date") or "")
    timezone_name = str(contract.get("timezone") or "UTC")
    return bool(target_date) and not is_settlement_pending(target_date, timezone_name)


def _decode_contract_row(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("truth_provider_priority", "verification_evidence"):
        value = row.get(key)
        if isinstance(value, str):
            try:
                row[key] = json.loads(value)
            except Exception:
                row[key] = []
    return row


def insert_forecast_run(run: dict[str, Any], members: list[dict[str, Any]] | None = None) -> int:
    init_v3_db()
    now = utc_now()
    run_key = str(
        run.get("run_key")
        or ":".join(
            [
                str(run.get("provider") or run.get("source") or "unknown"),
                str(run.get("model") or "unknown"),
                str(run.get("city") or ""),
                str(run.get("target_date") or ""),
                str(run.get("raw_response_hash") or run.get("retrieved_at") or now),
            ]
        )
    )
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO forecast_runs (
                run_key, city, target_date, source, provider, model, model_version,
                run_type, run_at, retrieved_at, valid_at, horizon, lead_hours,
                latitude, longitude, station_id, timezone, unit, mean_high, std_high,
                member_count, source_url, raw_response_hash, data_license,
                quality_flags, training_eligible, ineligibility_reason,
                raw_json, created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(run_key) DO UPDATE SET
                retrieved_at=excluded.retrieved_at,
                valid_at=excluded.valid_at,
                horizon=excluded.horizon,
                lead_hours=excluded.lead_hours,
                mean_high=excluded.mean_high,
                std_high=excluded.std_high,
                member_count=excluded.member_count,
                quality_flags=excluded.quality_flags,
                training_eligible=excluded.training_eligible,
                ineligibility_reason=excluded.ineligibility_reason,
                raw_json=excluded.raw_json
            """,
            (
                run_key,
                run.get("city"),
                run.get("target_date"),
                run.get("source"),
                run.get("provider"),
                run.get("model"),
                run.get("model_version"),
                run.get("run_type", "forecast"),
                run.get("run_at"),
                run.get("retrieved_at"),
                run.get("valid_at"),
                run.get("horizon"),
                _num(run.get("lead_hours"), 0.0),
                _num(run.get("latitude"), 0.0),
                _num(run.get("longitude"), 0.0),
                run.get("station_id"),
                run.get("timezone"),
                run.get("unit"),
                _num(run.get("mean_high"), 0.0),
                _num(run.get("std_high"), 0.0),
                int(run.get("member_count") or len(members or [])),
                run.get("source_url"),
                run.get("raw_response_hash"),
                run.get("data_license"),
                dump_json(run.get("quality_flags", [])),
                1 if run.get("training_eligible") else 0,
                run.get("ineligibility_reason"),
                dump_json(run),
                now,
            ),
        )
        run_id = int(conn.execute("SELECT id FROM forecast_runs WHERE run_key = ?", (run_key,)).fetchone()["id"])
        for member in members or []:
            conn.execute(
                """
                INSERT INTO forecast_members (
                    run_id, member_name, high_temp, member_id, hourly_json,
                    raw_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, member_id) DO UPDATE SET
                    member_name=excluded.member_name,
                    high_temp=excluded.high_temp,
                    hourly_json=excluded.hourly_json,
                    raw_json=excluded.raw_json
                """,
                (
                    run_id,
                    member.get("member_name") or member.get("member_id"),
                    _num(member.get("high_temp"), 0.0),
                    str(member.get("member_id") or member.get("member_name") or "deterministic"),
                    dump_json(member.get("hourly", [])),
                    dump_json(member),
                    now,
                ),
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
    by_city_date: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("city") or ""), str(row.get("target_date") or ""))
        by_city_date.setdefault(key, []).append(row)
    by_city: dict[str, dict[str, Any]] = {}
    provider_counts: dict[str, int] = {}
    excluded = 0
    for (city, target_date), day_rows in by_city_date.items():
        for row in day_rows:
            provider = str(row.get("provider") or "unknown")
            provider_counts[provider] = provider_counts.get(provider, 0) + 1
            if not row.get("calibration_eligible"):
                excluded += 1
        eligible_rows = [
            row for row in day_rows
            if row.get("calibration_eligible") and row.get("actual_temp") is not None
        ]
        best_eligible = max(
            eligible_rows,
            key=lambda row: _num(row.get("source_confidence"), 0.0),
            default=None,
        )
        display_row = best_eligible or max(
            day_rows,
            key=lambda row: (
                1 if row.get("actual_temp") is not None else 0,
                _num(row.get("source_confidence"), 0.0),
            ),
        )
        item = by_city.setdefault(
            city,
            {
                "city": city,
                "city_name": display_row.get("city_name") or city,
                "station_id": display_row.get("station_id") or "",
                "total_observations": 0,
                "eligible_observations": 0,
                "open_meteo_fallbacks": 0,
                "legacy_unknown": 0,
                "latest_provider": "",
                "latest_date": "",
                "latest_confidence": 0.0,
            },
        )
        item["total_observations"] += 1
        if best_eligible:
            item["eligible_observations"] += 1
        if any(row.get("provider") == "open_meteo_archive" for row in day_rows):
            item["open_meteo_fallbacks"] += 1
        if any(row.get("provider") == "legacy_unknown" for row in day_rows):
            item["legacy_unknown"] += 1
        if not item["latest_date"] or target_date > item["latest_date"]:
            item["latest_date"] = target_date
            item["latest_provider"] = display_row.get("provider") or ""
            item["latest_confidence"] = _num(display_row.get("source_confidence"), 0.0)
            item["station_id"] = display_row.get("station_id") or item["station_id"]
    cities = sorted(by_city.values(), key=lambda row: (row["eligible_observations"], row["total_observations"]), reverse=True)
    total = sum(row["total_observations"] for row in cities)
    eligible = sum(row["eligible_observations"] for row in cities)
    fallbacks = sum(row["open_meteo_fallbacks"] for row in cities)
    legacy = sum(row["legacy_unknown"] for row in cities)
    return {
        "total_observations": total,
        "eligible_observations": eligible,
        "coverage_rate": round((eligible / total) if total else 0.0, 4),
        "open_meteo_fallbacks": fallbacks,
        "open_meteo_fallback_rate": round((fallbacks / total) if total else 0.0, 4),
        "legacy_unknown": legacy,
        "excluded_observations": excluded,
        "provider_counts": provider_counts,
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
