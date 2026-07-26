from __future__ import annotations

import math
import statistics
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import connect_readonly


FORWARD_PROTOCOL: dict[str, Any] = {
    "protocol_id": "weatherbot-forward-v3-only-clv-v1",
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
    """One-sided mean test approximation used by the preregistered protocol."""

    if standard_deviation <= 0 or effect <= 0:
        return 0
    return int(math.ceil(((z_alpha + z_power) * standard_deviation / effect) ** 2))


def forward_validation_summary(
    *,
    path: Path | None = None,
    protocol: dict[str, Any] | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    global _CACHE, _CACHE_AT

    active_protocol = dict(FORWARD_PROTOCOL if protocol is None else protocol)
    if path is None and protocol is None and use_cache:
        with _CACHE_LOCK:
            if _CACHE is not None and time.monotonic() - _CACHE_AT < _CACHE_TTL_SECONDS:
                return dict(_CACHE)

    with connect_readonly(path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                    decision_id,
                    market_id,
                    token_id,
                    yes_token_id,
                    city_key,
                    target_date,
                    issued_at,
                    edge,
                    decision_time_ask,
                    quote_age_at_decision_seconds
                FROM signal_decisions
                WHERE issued_at >= ?
                  AND strategy_name = ?
                  AND forecast_algo = ?
                  AND paper_allowed = 1
                  AND decision_time_ask >= ?
                  AND decision_time_ask < ?
                  AND edge >= ?
                ORDER BY issued_at ASC, id ASC
                """,
                (
                    active_protocol["started_at"],
                    active_protocol["strategy_name"],
                    active_protocol["forecast_algo"],
                    active_protocol["ask_min"],
                    active_protocol["ask_max"],
                    active_protocol["edge_min"],
                ),
            )
        ]
        candidates = _first_candidate_per_token(rows)
        quotes = _preclose_quotes(conn, candidates, active_protocol)
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

    clv_rows = []
    for candidate in candidates:
        quote = quotes.get(str(candidate.get("decision_id") or ""))
        entry_ask = _float(candidate.get("decision_time_ask"))
        final_ask = _float((quote or {}).get("best_ask"))
        if entry_ask is None or final_ask is None:
            continue
        clv_rows.append({
            **candidate,
            "preclose_ask": final_ask,
            "clv": final_ask - entry_ask,
        })

    result = {
        "ok": True,
        "protocol": active_protocol,
        "progress": {
            "samples": len(candidates),
            "target_samples": int(active_protocol["target_n"]),
            "completion_percent": round(
                min(100.0, len(candidates) / max(1, int(active_protocol["target_n"])) * 100.0),
                1,
            ),
            "expected_evaluation_date": active_protocol["expected_evaluation_date"],
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
                if (_float(row.get("edge")) or 0.0) >= float(active_protocol["hypothesis_a_edge_min"])
            ]),
            "H-B": _metric_summary([
                float(row["clv"])
                for row in clv_rows
                if float(active_protocol["edge_min"])
                <= (_float(row.get("edge")) or 0.0)
                < float(active_protocol["hypothesis_a_edge_min"])
            ]),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if path is None and protocol is None and use_cache:
        with _CACHE_LOCK:
            _CACHE = dict(result)
            _CACHE_AT = time.monotonic()
    return result


def _first_candidate_per_token(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        token = str(row.get("yes_token_id") or row.get("token_id") or row.get("market_id") or "")
        key = f"{token}:{row.get('target_date') or ''}"
        if not token or key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _preclose_quotes(
    conn: Any,
    candidates: list[dict[str, Any]],
    protocol: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    decision_ids = [str(row.get("decision_id") or "") for row in candidates if row.get("decision_id")]
    if not decision_ids:
        return {}
    placeholders = ",".join("?" for _ in decision_ids)
    rows = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT decision_id, market_close_at, captured_at, best_bid, best_ask, status
            FROM candidate_preclose_quotes
            WHERE decision_id IN ({placeholders})
              AND status = 'ok'
              AND best_ask > 0
              AND best_ask < 1
            ORDER BY captured_at ASC
            """,
            decision_ids,
        )
    ]
    latest: dict[str, dict[str, Any]] = {}
    window = int(protocol["preclose_window_seconds"])
    for row in rows:
        captured_at = _datetime(row.get("captured_at"))
        close_at = _datetime(row.get("market_close_at"))
        if captured_at is None or close_at is None:
            continue
        seconds_before_close = (close_at - captured_at).total_seconds()
        if 0 <= seconds_before_close <= window:
            latest[str(row["decision_id"])] = row
    return latest


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
