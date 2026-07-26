from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import connect, init_v3_db, utc_now


def snapshot_candidate_preclose_quotes(
    *,
    path: Path | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Append a time-stamped quote for every open paper candidate.

    This deliberately captures unchanged books too. CLV needs evidence that a
    quote was observed near close, not merely the timestamp of the last price
    change.
    """

    init_v3_db(path)
    captured_at = _utc_iso(as_of) or utc_now()
    captured_dt = _parse_datetime(captured_at)
    stored = 0
    skipped_closed = 0
    missing_close = 0
    unavailable = 0
    with connect(path) as conn:
        candidates = [
            dict(row)
            for row in conn.execute(
                """
                WITH ranked_candidates AS (
                    SELECT sd.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY sd.yes_token_id
                               ORDER BY sd.issued_at DESC, sd.id DESC
                           ) AS candidate_rank
                    FROM signal_decisions sd
                    WHERE sd.paper_allowed = 1
                      AND COALESCE(sd.yes_token_id, '') != ''
                )
                SELECT sd.decision_id, sd.market_id, sd.yes_token_id AS token_id,
                       sd.city_key, sd.target_date, pe.market_close_at
                FROM ranked_candidates sd
                LEFT JOIN polymarket_markets pm ON pm.market_id = sd.market_id
                LEFT JOIN polymarket_events pe ON pe.event_id = pm.event_id
                WHERE sd.candidate_rank = 1
                ORDER BY sd.issued_at DESC, sd.id DESC
                """
            ).fetchall()
        ]
        for candidate in candidates:
            close_at = _utc_iso(candidate.get("market_close_at"))
            close_dt = _parse_datetime(close_at)
            if close_dt is None:
                missing_close += 1
                continue
            if captured_dt is None or captured_dt > close_dt:
                skipped_closed += 1
                continue
            token_id = str(candidate.get("token_id") or "")
            book = conn.execute(
                """
                SELECT *
                FROM orderbooks
                WHERE yes_token_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (token_id,),
            ).fetchone()
            book_row = dict(book) if book is not None else {}
            best_bid = _number(book_row.get("best_bid"))
            best_ask = _number(book_row.get("best_ask"))
            status = "ok" if best_bid is not None and best_ask is not None else (
                "side_absent" if best_bid is not None or best_ask is not None else "quote_unavailable"
            )
            if status != "ok":
                unavailable += 1
            minute_key = captured_at[:16]
            snapshot_key = hashlib.sha256(
                f"candidate-preclose|{token_id}|{minute_key}".encode("utf-8")
            ).hexdigest()[:32]
            raw = {
                "candidate": candidate,
                "orderbook_snapshot_key": book_row.get("snapshot_key"),
                "book_state": book_row.get("book_state"),
            }
            conn.execute(
                """
                INSERT INTO candidate_preclose_quotes (
                    snapshot_key, decision_id, market_id, token_id, city_key,
                    target_date, market_close_at, captured_at, quote_timestamp,
                    best_bid, best_ask, source, status, raw_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_key) DO NOTHING
                """,
                (
                    snapshot_key,
                    str(candidate.get("decision_id") or ""),
                    str(candidate.get("market_id") or ""),
                    token_id,
                    str(candidate.get("city_key") or ""),
                    str(candidate.get("target_date") or ""),
                    close_at,
                    captured_at,
                    str(book_row.get("quote_timestamp") or book_row.get("created_at") or ""),
                    best_bid,
                    best_ask,
                    str(book_row.get("snapshot_type") or "stored_orderbook"),
                    status,
                    json.dumps(raw, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                ),
            )
            stored += int(conn.execute("SELECT changes()").fetchone()[0] or 0)
    return {
        "ok": True,
        "captured_at": captured_at,
        "candidates": len(candidates),
        "stored": stored,
        "missing_close": missing_close,
        "skipped_closed": skipped_closed,
        "quote_unavailable": unavailable,
    }


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: Any) -> str:
    parsed = _parse_datetime(value)
    return parsed.isoformat() if parsed else ""


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0 < number < 1 else None
