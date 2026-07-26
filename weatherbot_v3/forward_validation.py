from __future__ import annotations

import hashlib
import json
import math
import statistics
import threading
import time
from datetime import date, datetime, time as day_time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .db import connect, connect_readonly, init_v3_db, utc_now


FORWARD_PROTOCOL_V1: dict[str, Any] = {
    "protocol_id": "weatherbot-forward-v3-only-clv-v1",
    "status": "void",
    "started_at": "2026-07-26T15:30:00+00:00",
    "ask_min": 0.20,
    "ask_max": 0.40,
    "edge_min": 0.08,
    "strategy_name": "core_modal_v1",
    "forecast_algo": "polywx_aligned_deb_v1",
    "target_n": 73,
    "power_effect_clv": 0.03,
    "expected_evaluation_date": "2026-08-03",
    "preclose_window_seconds": 15 * 60,
    "hypothesis_a_edge_min": 0.15,
}

FORWARD_PROTOCOL: dict[str, Any] = {
    "protocol_id": "weatherbot-forward-v3-only-clv-v2",
    "status": "frozen",
    "started_at": "2026-07-26T17:30:00+00:00",
    "ask_min": 0.20,
    "ask_max": 0.40,
    "edge_min": 0.08,
    "strategy_name": "core_modal_v1",
    "forecast_algo": "polywx_aligned_deb_v1",
    "target_n": 338,
    "power_effect_clv": 0.03,
    "expected_evaluation_date": "2026-08-12",
    "anchor_kind": "decision_plus_6h_capped_peak_minus_1h",
    "anchor_quote_max_age_seconds": 15 * 60,
    "hypothesis_a_edge_min": 0.15,
    "main_cohort": "model_side_candidate",
}

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, Any] | None = None
_CACHE_AT = 0.0
_CACHE_TTL_SECONDS = 30.0


def required_sample_size(
    standard_deviation: float,
    effect: float,
    *,
    z_alpha: float = 1.6448536269514722,
    z_power: float = 0.8416212335729143,
) -> int:
    """One-sided mean test approximation used by the frozen protocol."""

    if standard_deviation <= 0 or effect <= 0:
        return 0
    return int(math.ceil(((z_alpha + z_power) * standard_deviation / effect) ** 2))


def enroll_forward_validation_candidates(
    *,
    path: Path | None = None,
    protocol: dict[str, Any] | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Freeze each token/date's first v2 model-side candidate.

    Execution eligibility is stored as a stratum. It is intentionally not an
    enrollment gate, so statistical observation cannot relax trading safety.
    """

    active = dict(FORWARD_PROTOCOL if protocol is None else protocol)
    init_v3_db(path)
    observed_at = _utc_iso(as_of) or utc_now()
    protocol_start = _datetime(active.get("started_at"))
    observed_dt = _datetime(observed_at)
    if protocol_start is None or observed_dt is None:
        return {"ok": False, "reason": "invalid_protocol_time"}

    stored = 0
    already_enrolled = 0
    before_protocol = 0
    after_as_of = 0
    missing_decision_time = 0
    missing_peak = 0
    anchor_not_after_decision = 0
    selected = 0

    with connect(path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT sd.*, s.timezone, s.settlement_timezone
                FROM signal_decisions sd
                LEFT JOIN stations s ON s.city_key = sd.city_key
                WHERE sd.strategy_name = ?
                  AND sd.forecast_algo = ?
                  AND sd.decision_time_ask >= ?
                  AND sd.decision_time_ask < ?
                  AND sd.edge >= ?
                  AND COALESCE(sd.yes_token_id, sd.token_id, '') != ''
                ORDER BY sd.updated_at ASC, sd.id ASC
                """,
                (
                    active["strategy_name"],
                    active["forecast_algo"],
                    active["ask_min"],
                    active["ask_max"],
                    active["edge_min"],
                ),
            )
        ]
        for row in rows:
            raw = _json_object(row.get("raw_json"))
            orderbook = _json_object(row.get("orderbook_snapshot_json"))
            decision_at = _datetime(
                raw.get("decision_time")
                or orderbook.get("checked_at")
                or row.get("updated_at")
            )
            if decision_at is None:
                missing_decision_time += 1
                continue
            if decision_at < protocol_start:
                before_protocol += 1
                continue
            if decision_at > observed_dt:
                after_as_of += 1
                continue

            token_id = str(row.get("yes_token_id") or row.get("token_id") or "").strip()
            target_date = str(row.get("target_date") or "").strip()
            if not token_id or not target_date:
                continue
            enrollment_key = _hash_key(
                active["protocol_id"],
                token_id,
                target_date,
            )
            exists = conn.execute(
                "SELECT 1 FROM forward_validation_candidates WHERE enrollment_key=?",
                (enrollment_key,),
            ).fetchone()
            if exists is not None:
                already_enrolled += 1
                continue

            peak_hour = str(raw.get("peak_hour") or "").strip()
            if not peak_hour:
                prediction_row = conn.execute(
                    """
                    SELECT peak_hour
                    FROM daily_max_predictions
                    WHERE city_key=? AND target_date=? AND issued_at<=?
                    ORDER BY issued_at DESC, id DESC
                    LIMIT 1
                    """,
                    (
                        str(row.get("city_key") or ""),
                        target_date,
                        decision_at.isoformat(),
                    ),
                ).fetchone()
                peak_hour = str(prediction_row["peak_hour"] or "").strip() if prediction_row else ""
            anchor_at = _anchor_at(
                decision_at=decision_at,
                target_date=target_date,
                peak_hour=peak_hour,
                timezone_name=str(
                    row.get("settlement_timezone")
                    or row.get("timezone")
                    or "UTC"
                ),
            )
            if anchor_at is None:
                missing_peak += 1
                continue
            if anchor_at <= decision_at:
                anchor_not_after_decision += 1
                continue

            selected += 1
            local_decision_date = _local_date(
                decision_at,
                str(row.get("settlement_timezone") or row.get("timezone") or "UTC"),
            )
            lead_bucket = _lead_bucket(local_decision_date, target_date)
            payload = {
                "source_signal_decision_id": row.get("id"),
                "source_updated_at": row.get("updated_at"),
                "decision_time_source": (
                    "raw_json.decision_time"
                    if raw.get("decision_time")
                    else "orderbook_snapshot.checked_at"
                    if orderbook.get("checked_at")
                    else "signal_decisions.updated_at"
                ),
                "paper_allowed_at_entry": bool(row.get("paper_allowed")),
                "gate_reasons": _json_value(row.get("gate_reasons_json"), default=[]),
                "anchor_inputs": {
                    "decision_at": decision_at.isoformat(),
                    "peak_hour": peak_hour,
                    "timezone": str(
                        row.get("settlement_timezone")
                        or row.get("timezone")
                        or "UTC"
                    ),
                },
            }
            conn.execute(
                """
                INSERT INTO forward_validation_candidates (
                    enrollment_key, protocol_id, decision_id, market_id, token_id,
                    bucket_key, city_key, target_date, lead_bucket, decision_at,
                    enrolled_at, entry_ask, model_probability, market_probability,
                    edge, paper_allowed_at_entry, blocked_reason_primary, peak_hour,
                    anchor_kind, anchor_at, quote_age_at_entry_seconds, raw_json,
                    created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?
                )
                ON CONFLICT(enrollment_key) DO NOTHING
                """,
                (
                    enrollment_key,
                    active["protocol_id"],
                    str(row.get("decision_id") or ""),
                    str(row.get("market_id") or ""),
                    token_id,
                    str(row.get("bucket_key") or ""),
                    str(row.get("city_key") or ""),
                    target_date,
                    lead_bucket,
                    decision_at.isoformat(),
                    observed_at,
                    float(row["decision_time_ask"]),
                    _float(row.get("model_probability")),
                    _float(row.get("market_implied_probability"))
                    or _float(row.get("market_ask")),
                    float(row["edge"]),
                    int(bool(row.get("paper_allowed"))),
                    str(row.get("blocked_reason_primary") or ""),
                    peak_hour,
                    active["anchor_kind"],
                    anchor_at.isoformat(),
                    _float(row.get("quote_age_at_decision_seconds")),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                ),
            )
            stored += int(conn.execute("SELECT changes()").fetchone()[0] or 0)

    _clear_cache()
    return {
        "ok": True,
        "protocol_id": active["protocol_id"],
        "observed_at": observed_at,
        "rows_scanned": len(rows),
        "selected": selected,
        "stored": stored,
        "already_enrolled": already_enrolled,
        "before_protocol": before_protocol,
        "after_as_of": after_as_of,
        "missing_decision_time": missing_decision_time,
        "missing_peak": missing_peak,
        "anchor_not_after_decision": anchor_not_after_decision,
    }


def snapshot_forward_validation_anchor_quotes(
    *,
    path: Path | None = None,
    protocol: dict[str, Any] | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Freeze the last valid ask at or before each candidate's v2 anchor."""

    active = dict(FORWARD_PROTOCOL if protocol is None else protocol)
    init_v3_db(path)
    captured_at = _utc_iso(as_of) or utc_now()
    captured_dt = _datetime(captured_at)
    if captured_dt is None:
        return {"ok": False, "reason": "invalid_capture_time"}
    max_age = int(active.get("anchor_quote_max_age_seconds") or 900)
    stored = 0
    pending = 0
    unavailable = 0
    already_captured = 0
    due = 0

    with connect(path) as conn:
        candidates = [
            dict(row)
            for row in conn.execute(
                """
                SELECT c.*
                FROM forward_validation_candidates c
                LEFT JOIN forward_validation_anchor_quotes q
                  ON q.protocol_id=c.protocol_id
                 AND q.enrollment_key=c.enrollment_key
                WHERE c.protocol_id=?
                  AND q.id IS NULL
                ORDER BY c.anchor_at ASC, c.id ASC
                """,
                (active["protocol_id"],),
            )
        ]
        for candidate in candidates:
            anchor_dt = _datetime(candidate.get("anchor_at"))
            if anchor_dt is None or captured_dt < anchor_dt:
                pending += 1
                continue
            due += 1
            window_start = anchor_dt - timedelta(seconds=max_age)
            book = conn.execute(
                """
                SELECT *
                FROM orderbooks
                WHERE yes_token_id=?
                  AND created_at>=?
                  AND created_at<=?
                  AND best_ask>0
                  AND best_ask<1
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (
                    str(candidate.get("token_id") or ""),
                    window_start.isoformat(),
                    anchor_dt.isoformat(),
                ),
            ).fetchone()
            book_row = dict(book) if book is not None else {}
            best_ask = _probability(book_row.get("best_ask"))
            best_bid = _probability(book_row.get("best_bid"))
            if best_ask is None and captured_dt < anchor_dt + timedelta(seconds=max_age):
                pending += 1
                continue
            status = "ok" if best_ask is not None else "quote_unavailable"
            if status != "ok":
                unavailable += 1
            snapshot_key = _hash_key(
                active["protocol_id"],
                str(candidate.get("enrollment_key") or ""),
                active["anchor_kind"],
            )
            raw = {
                "orderbook_snapshot_key": book_row.get("snapshot_key"),
                "book_state": book_row.get("book_state"),
                "quote_max_age_seconds": max_age,
                "captured_after_anchor_seconds": round(
                    (captured_dt - anchor_dt).total_seconds(),
                    3,
                ),
            }
            conn.execute(
                """
                INSERT INTO forward_validation_anchor_quotes (
                    snapshot_key, protocol_id, enrollment_key, decision_id,
                    token_id, anchor_kind, anchor_at, captured_at, quote_timestamp,
                    best_bid, best_ask, status, source, raw_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_key) DO NOTHING
                """,
                (
                    snapshot_key,
                    active["protocol_id"],
                    str(candidate.get("enrollment_key") or ""),
                    str(candidate.get("decision_id") or ""),
                    str(candidate.get("token_id") or ""),
                    active["anchor_kind"],
                    anchor_dt.isoformat(),
                    captured_at,
                    str(book_row.get("quote_timestamp") or book_row.get("created_at") or ""),
                    best_bid,
                    best_ask,
                    status,
                    str(book_row.get("snapshot_type") or "stored_orderbook"),
                    json.dumps(raw, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                ),
            )
            changes = int(conn.execute("SELECT changes()").fetchone()[0] or 0)
            stored += changes
            already_captured += int(changes == 0)

    _clear_cache()
    return {
        "ok": True,
        "protocol_id": active["protocol_id"],
        "captured_at": captured_at,
        "candidates": len(candidates),
        "due": due,
        "stored": stored,
        "pending": pending,
        "quote_unavailable": unavailable,
        "already_captured": already_captured,
    }


def forward_validation_summary(
    *,
    path: Path | None = None,
    protocol: dict[str, Any] | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    global _CACHE, _CACHE_AT

    active = dict(FORWARD_PROTOCOL if protocol is None else protocol)
    if path is None and protocol is None and use_cache:
        with _CACHE_LOCK:
            if _CACHE is not None and time.monotonic() - _CACHE_AT < _CACHE_TTL_SECONDS:
                return dict(_CACHE)

    init_v3_db(path)
    with connect_readonly(path) as conn:
        candidates = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM forward_validation_candidates
                WHERE protocol_id=?
                ORDER BY decision_at ASC, id ASC
                """,
                (active["protocol_id"],),
            )
        ]
        anchor_quotes = {
            str(row["enrollment_key"]): dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM forward_validation_anchor_quotes
                WHERE protocol_id=? AND status='ok' AND best_ask>0 AND best_ask<1
                ORDER BY anchor_at ASC, id ASC
                """,
                (active["protocol_id"],),
            )
        }
        cohort_orders = _cohort_orders(conn, candidates)
        settled_orders = [
            row
            for row in cohort_orders
            if row.get("lifecycle_status") == "settled"
            and row.get("status") in {"paper_won", "paper_lost"}
        ]
        open_orders = [
            row for row in cohort_orders
            if row.get("lifecycle_status") != "settled"
        ]

    clv_rows: list[dict[str, Any]] = []
    for candidate in candidates:
        quote = anchor_quotes.get(str(candidate.get("enrollment_key") or ""))
        entry_ask = _float(candidate.get("entry_ask"))
        anchor_ask = _float((quote or {}).get("best_ask"))
        if entry_ask is None or anchor_ask is None:
            continue
        clv_rows.append({
            **candidate,
            "anchor_ask": anchor_ask,
            "clv": anchor_ask - entry_ask,
        })

    paper_true = [row for row in candidates if bool(row.get("paper_allowed_at_entry"))]
    paper_false = [row for row in candidates if not bool(row.get("paper_allowed_at_entry"))]
    blocker_counts: dict[str, int] = {}
    for row in paper_false:
        reason = str(row.get("blocked_reason_primary") or "unclassified")
        blocker_counts[reason] = blocker_counts.get(reason, 0) + 1
    result = {
        "ok": True,
        "protocol": active,
        "progress": {
            "samples": len(clv_rows),
            "enrolled_candidates": len(candidates),
            "target_samples": int(active["target_n"]),
            "completion_percent": round(
                min(100.0, len(clv_rows) / max(1, int(active["target_n"])) * 100.0),
                1,
            ),
            "expected_evaluation_date": active["expected_evaluation_date"],
        },
        "clv": _metric_summary([float(row["clv"]) for row in clv_rows]),
        "probability_score": _binary_probability_score(settled_orders),
        "paper_pnl": {
            "settled_orders": len(settled_orders),
            "open_orders": len(open_orders),
            "realized_usd": round(sum(_float(row.get("realized_pnl")) or 0.0 for row in settled_orders), 4),
            "unrealized_usd": round(sum(_float(row.get("unrealized_pnl")) or 0.0 for row in open_orders), 4),
        },
        "hypotheses": {
            "H-A": _metric_summary([
                float(row["clv"])
                for row in clv_rows
                if (_float(row.get("edge")) or 0.0) >= float(active["hypothesis_a_edge_min"])
            ]),
            "H-B": _metric_summary([
                float(row["clv"])
                for row in clv_rows
                if float(active["edge_min"])
                <= (_float(row.get("edge")) or 0.0)
                < float(active["hypothesis_a_edge_min"])
            ]),
        },
        "strata": {
            "paper_allowed": {
                "true": {
                    "enrolled": len(paper_true),
                    "clv": _metric_summary([
                        float(row["clv"])
                        for row in clv_rows
                        if bool(row.get("paper_allowed_at_entry"))
                    ]),
                },
                "false": {
                    "enrolled": len(paper_false),
                    "clv": _metric_summary([
                        float(row["clv"])
                        for row in clv_rows
                        if not bool(row.get("paper_allowed_at_entry"))
                    ]),
                },
            },
            "lead": _stratum_metrics(clv_rows, "lead_bucket"),
            "ask": _ask_strata(clv_rows),
        },
        "blocked_reason_counts": dict(
            sorted(blocker_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if path is None and protocol is None and use_cache:
        with _CACHE_LOCK:
            _CACHE = dict(result)
            _CACHE_AT = time.monotonic()
    return result


def _cohort_orders(conn: Any, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decision_ids = [str(row.get("decision_id") or "") for row in candidates if row.get("decision_id")]
    if not decision_ids:
        return []
    placeholders = ",".join("?" for _ in decision_ids)
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT
                decision_id,
                status,
                lifecycle_status,
                model_probability,
                market_probability,
                realized_pnl,
                unrealized_pnl
            FROM paper_orders
            WHERE decision_id IN ({placeholders})
            ORDER BY opened_at ASC, id ASC
            """,
            decision_ids,
        )
    ]


def _anchor_at(
    *,
    decision_at: datetime,
    target_date: str,
    peak_hour: str,
    timezone_name: str,
) -> datetime | None:
    try:
        local_day = date.fromisoformat(target_date)
        parsed_peak = day_time.fromisoformat(peak_hour)
        zone = ZoneInfo(timezone_name)
    except (ValueError, KeyError):
        return None
    predicted_peak = datetime.combine(local_day, parsed_peak, tzinfo=zone)
    return min(
        decision_at + timedelta(hours=6),
        predicted_peak.astimezone(timezone.utc) - timedelta(hours=1),
    )


def _local_date(value: datetime, timezone_name: str) -> date:
    try:
        zone = ZoneInfo(timezone_name)
    except (KeyError, ValueError):
        zone = timezone.utc
    return value.astimezone(zone).date()


def _lead_bucket(decision_date: date, target_date: str) -> str:
    try:
        delta = (date.fromisoformat(target_date) - decision_date).days
    except ValueError:
        return "unknown"
    return f"D+{delta}" if delta >= 0 else f"D{delta}"


def _metric_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "std": None, "ci95_low": None, "ci95_high": None}
    mean = statistics.fmean(values)
    std = statistics.stdev(values) if len(values) >= 2 else 0.0
    half_width = 1.96 * std / math.sqrt(len(values)) if len(values) >= 2 else 0.0
    return {
        "n": len(values),
        "mean": round(mean, 8),
        "std": round(std, 8),
        "ci95_low": round(mean - half_width, 8),
        "ci95_high": round(mean + half_width, 8),
    }


def _binary_probability_score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = []
    for row in rows:
        model_probability = _float(row.get("model_probability"))
        market_probability = _float(row.get("market_probability"))
        if model_probability is None or market_probability is None:
            continue
        outcome = 1.0 if row.get("status") == "paper_won" else 0.0
        scored.append((model_probability, market_probability, outcome))
    if not scored:
        return {"n": 0, "model_brier": None, "market_brier": None}
    return {
        "n": len(scored),
        "model_brier": round(statistics.fmean((model - outcome) ** 2 for model, _, outcome in scored), 8),
        "market_brier": round(statistics.fmean((market - outcome) ** 2 for _, market, outcome in scored), 8),
    }


def _stratum_metrics(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    groups: dict[str, list[float]] = {}
    for row in rows:
        key = str(row.get(field) or "unknown")
        groups.setdefault(key, []).append(float(row["clv"]))
    return {key: _metric_summary(values) for key, values in sorted(groups.items())}


def _ask_strata(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[float]] = {
        "0.20-0.30": [],
        "0.30-0.40": [],
    }
    for row in rows:
        ask = _float(row.get("entry_ask"))
        if ask is None:
            continue
        key = "0.20-0.30" if ask < 0.30 else "0.30-0.40"
        groups[key].append(float(row["clv"]))
    return {key: _metric_summary(values) for key, values in groups.items()}


def _json_object(value: Any) -> dict[str, Any]:
    parsed = _json_value(value, default={})
    return parsed if isinstance(parsed, dict) else {}


def _json_value(value: Any, *, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def _hash_key(*parts: Any) -> str:
    joined = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:40]


def _probability(value: Any) -> float | None:
    number = _float(value)
    return number if number is not None and 0 < number < 1 else None


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: Any) -> str:
    parsed = _datetime(value)
    return parsed.isoformat() if parsed is not None else ""


def _clear_cache() -> None:
    global _CACHE, _CACHE_AT
    with _CACHE_LOCK:
        _CACHE = None
        _CACHE_AT = 0.0
