from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import load_config
from .db import (
    connect,
    init_v3_db,
    list_daily_max_predictions,
    list_signal_decisions,
    upsert_signal_decision_record,
)
from .deb import bucket_probabilities
from .stations import get_station


DECISION_VERSION = "signal-decision-v1"
MIN_EDGE = 0.03
MAX_SPREAD_BPS = 500.0
STALE_BOOK_SECONDS = 300.0
MIN_BIAS_SAMPLE_DAYS = 7
LOW_PRICE_TAIL_ASK = 0.05


def build_signal_decisions(
    city_key: str,
    target_date: str,
    *,
    issued_at_hour: str | None = None,
    dry_run: bool = False,
    limit: int = 200,
    path: Path | None = None,
) -> dict[str, Any]:
    init_v3_db(path)
    city = str(city_key or "").strip().lower()
    date = str(target_date or "").strip()
    if not city or not date:
        return {"ok": False, "reasons": ["city_or_target_date_missing"], "decisions": []}

    predictions = list_daily_max_predictions(city_key=city, target_date=date, limit=10, path=path)
    prediction = _select_prediction(predictions, issued_at_hour)
    if not prediction:
        return {"ok": False, "city_key": city, "target_date": date, "reasons": ["missing_daily_max_prediction"], "decisions": []}
    buckets = _list_market_buckets(city, date, limit=limit, path=path)
    if not buckets:
        return {
            "ok": False,
            "city_key": city,
            "target_date": date,
            "prediction": prediction,
            "reasons": ["missing_market_buckets"],
            "decisions": [],
        }

    distribution = bucket_probabilities(
        float(prediction["mu"]),
        float(prediction["sigma"]),
        buckets,
        unit=str(prediction.get("unit") or "C"),
        sigma_floor=_optional_float(prediction.get("sigma_floor")),
        normalize=True,
    )
    probabilities = {
        str(item.get("bucket_key") or ""): item
        for item in distribution.get("items") or []
        if item.get("bucket_key")
    }
    evidence = _evidence_links(city, date, prediction, buckets, path)
    station_live_reasons = _station_live_gate_reasons(city, path)
    decisions = [
        _decision_for_bucket(
            bucket,
            probabilities.get(str(bucket.get("bucket_key") or ""), {}),
            prediction,
            distribution,
            evidence,
            station_live_reasons=station_live_reasons,
        )
        for bucket in buckets
    ]
    stored = 0
    if not dry_run:
        for decision in decisions:
            upsert_signal_decision_record(decision, path=path)
            stored += 1
    status_counts: dict[str, int] = {}
    paper_counts: dict[str, int] = {}
    live_counts: dict[str, int] = {}
    for decision in decisions:
        status_counts[decision["gate_status"]] = status_counts.get(decision["gate_status"], 0) + 1
        paper_counts[decision["paper_decision"]] = paper_counts.get(decision["paper_decision"], 0) + 1
        live_counts[decision["live_decision"]] = live_counts.get(decision["live_decision"], 0) + 1
    return {
        "ok": True,
        "dry_run": dry_run,
        "city_key": city,
        "target_date": date,
        "prediction_id": prediction.get("id"),
        "deb_version": prediction.get("deb_version") or prediction.get("method"),
        "bucket_count": len(buckets),
        "decision_count": len(decisions),
        "stored": stored,
        "status_counts": status_counts,
        "paper_counts": paper_counts,
        "live_counts": live_counts,
        "model_distribution": _distribution_summary(distribution),
        "decisions": decisions,
    }


def build_signal_decisions_for_targets(
    targets: list[tuple[str, str]],
    *,
    dry_run: bool = False,
    limit: int = 200,
    path: Path | None = None,
) -> dict[str, Any]:
    results = [
        build_signal_decisions(city, date, dry_run=dry_run, limit=limit, path=path)
        for city, date in targets
    ]
    return {
        "ok": all(result.get("ok") for result in results),
        "dry_run": dry_run,
        "requested": len(targets),
        "stored": sum(int(result.get("stored") or 0) for result in results),
        "decision_count": sum(int(result.get("decision_count") or 0) for result in results),
        "failed": sum(1 for result in results if not result.get("ok")),
        "results": results,
    }


def signal_decisions_summary(
    city_key: str | None = None,
    target_date: str | None = None,
    *,
    limit: int = 100,
    path: Path | None = None,
) -> dict[str, Any]:
    rows = list_signal_decisions(city_key=city_key, target_date=target_date, limit=limit, path=path)
    status_counts: dict[str, int] = {}
    paper_counts: dict[str, int] = {}
    live_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    for row in rows:
        status_counts[str(row.get("gate_status") or "")] = status_counts.get(str(row.get("gate_status") or ""), 0) + 1
        paper_counts[str(row.get("paper_decision") or "")] = paper_counts.get(str(row.get("paper_decision") or ""), 0) + 1
        live_counts[str(row.get("live_decision") or "")] = live_counts.get(str(row.get("live_decision") or ""), 0) + 1
        for reason in row.get("gate_reasons") or []:
            reason_counts[str(reason)] = reason_counts.get(str(reason), 0) + 1
    return {
        "ok": True,
        "city_key": city_key or "",
        "target_date": target_date or "",
        "count": len(rows),
        "status_counts": status_counts,
        "paper_counts": paper_counts,
        "live_counts": live_counts,
        "reason_counts": [
            {"reason": reason, "count": count}
            for reason, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "decisions": rows,
    }


def _decision_for_bucket(
    bucket: dict[str, Any],
    probability_item: dict[str, Any],
    prediction: dict[str, Any],
    distribution: dict[str, Any],
    evidence: dict[str, Any],
    *,
    station_live_reasons: list[str] | None = None,
) -> dict[str, Any]:
    model_probability = _optional_float(probability_item.get("probability"))
    market_bid = _optional_float(bucket.get("best_bid"))
    market_ask = _optional_float(bucket.get("best_ask"))
    price = _optional_float(bucket.get("price"))
    market_mid = (market_bid + market_ask) / 2.0 if market_bid is not None and market_ask is not None else None
    market_probability = market_ask if market_ask is not None else (price if price is not None else market_mid)
    edge = None if model_probability is None or market_probability is None else model_probability - market_probability
    edge_percent = None
    if edge is not None and market_probability and market_probability > 0:
        edge_percent = edge / market_probability
    spread = _optional_float(bucket.get("spread"))
    spread_bps = _spread_bps(spread, market_ask, market_bid)
    book_age_seconds = _book_age_seconds(bucket.get("quote_timestamp"))

    gate_reasons: list[str] = []
    cautions: list[str] = []
    hard_blocks: list[str] = []
    skip_reasons: list[str] = []
    if bucket.get("strict_match_status") != "matched":
        hard_blocks.append("bucket_not_strict_match")
        gate_reasons.extend(_as_list(bucket.get("strict_match_reasons")) or ["bucket_not_strict_match"])
    if not bucket.get("yes_token_id"):
        hard_blocks.append("yes_token_missing")
    if _optional_float(bucket.get("tick_size")) is None:
        hard_blocks.append("tick_size_missing")
    if _optional_float(bucket.get("order_min_size")) is None:
        hard_blocks.append("order_min_size_missing")
    if not bool(bucket.get("enable_order_book")):
        hard_blocks.append("orderbook_disabled")
    if market_probability is None:
        hard_blocks.append("market_probability_missing")
    if model_probability is None:
        hard_blocks.append("model_probability_missing")
    if _is_low_price_tail(bucket, market_ask):
        hard_blocks.append("low_price_tail_bucket")
    if spread_bps is not None and spread_bps > MAX_SPREAD_BPS:
        hard_blocks.append("spread_too_wide")
    if book_age_seconds is None:
        cautions.append("book_timestamp_missing")
    elif book_age_seconds > STALE_BOOK_SECONDS:
        cautions.append("stale_book")
    if str(prediction.get("deb_version") or prediction.get("method") or "") != "weatherbot-deb-v2":
        hard_blocks.append("deb_version_not_v2")
    if int(prediction.get("bias_sample_count") or 0) < MIN_BIAS_SAMPLE_DAYS:
        gate_reasons.append("insufficient_bias_samples")
    if edge is None or edge < MIN_EDGE:
        skip_reasons.append("edge_below_min")

    gate_reasons.extend(hard_blocks)
    gate_reasons.extend(skip_reasons)
    gate_reasons = _unique(gate_reasons)
    hard_blocks = _unique(hard_blocks)
    skip_reasons = _unique(skip_reasons)
    paper_allowed = not hard_blocks and not skip_reasons
    paper_decision = "buy" if paper_allowed else ("blocked" if hard_blocks else "skip")
    live_allowed = False
    live_decision = "blocked"
    live_reasons = []
    if int(prediction.get("bias_sample_count") or 0) < MIN_BIAS_SAMPLE_DAYS:
        live_reasons.append("insufficient_bias_samples")
    live_reasons.extend(station_live_reasons or [])
    if not paper_allowed:
        live_reasons.append("paper_gate_not_passed")
    if not getattr(load_config(), "live_trading", False):
        live_reasons.append("live_trading_disabled")
    gate_reasons = _unique(gate_reasons + live_reasons)
    gate_status = "paper_allowed" if paper_allowed else ("paper_blocked" if hard_blocks else "skip")
    primary_reason = (hard_blocks or skip_reasons or live_reasons or gate_reasons or [""])[0]
    decision = {
        "decision_id": _decision_id(bucket, prediction),
        "bucket_id": bucket.get("id"),
        "bucket_key": bucket.get("bucket_key") or "",
        "city_key": bucket.get("city") or prediction.get("city_key") or "",
        "target_date": bucket.get("target_date") or prediction.get("target_date") or "",
        "issued_at": prediction.get("issued_at") or "",
        "market_id": bucket.get("market_id") or "",
        "token_id": bucket.get("yes_token_id") or bucket.get("token_id") or "",
        "yes_token_id": bucket.get("yes_token_id") or "",
        "bucket_direction": bucket.get("bucket_direction") or "",
        "bucket_lower": bucket.get("bucket_low"),
        "bucket_upper": bucket.get("bucket_high"),
        "mu": prediction.get("mu"),
        "sigma": prediction.get("sigma"),
        "deb_version": prediction.get("deb_version") or prediction.get("method") or "",
        "model_probability": model_probability,
        "market_ask": market_ask,
        "market_bid": market_bid,
        "market_mid": market_mid,
        "market_implied_probability": market_probability,
        "edge": edge,
        "edge_percent": edge_percent,
        "orderbook_snapshot": {
            "best_bid": market_bid,
            "best_ask": market_ask,
            "spread": spread,
            "bid_depth": bucket.get("bid_depth"),
            "ask_depth": bucket.get("ask_depth"),
            "quote_timestamp": bucket.get("quote_timestamp"),
            "source": bucket.get("orderbook_source"),
            "snapshot_key": bucket.get("orderbook_snapshot_key"),
        },
        "tick_size": bucket.get("tick_size"),
        "order_min_size": bucket.get("order_min_size"),
        "neg_risk": bool(bucket.get("neg_risk")),
        "book_age_seconds": book_age_seconds,
        "spread_bps": spread_bps,
        "gate_status": gate_status,
        "gate_reasons": gate_reasons,
        "paper_allowed": paper_allowed,
        "live_allowed": live_allowed,
        "paper_decision": paper_decision,
        "live_decision": live_decision,
        "blocked_reason_primary": primary_reason,
        "reasons": gate_reasons,
        "cautions": _unique(cautions),
        "action": "buy_yes" if paper_allowed else "observe",
        "decision_version": DECISION_VERSION,
        "model_distribution": _distribution_summary(distribution),
        "model_bucket_probs": probability_item,
        "market_bucket_probs": [{
            "bucket_key": bucket.get("bucket_key"),
            "market_probability": market_probability,
            "best_bid": market_bid,
            "best_ask": market_ask,
            "price": price,
        }],
        "edge_by_bucket": {
            str(bucket.get("bucket_key") or bucket.get("market_id") or ""): {
                "model_probability": model_probability,
                "market_probability": market_probability,
                "edge": edge,
                "edge_percent": edge_percent,
            }
        },
        "evidence_links": {
            **evidence,
            "market_bucket_id": bucket.get("id"),
            "daily_max_prediction_id": prediction.get("id"),
        },
    }
    return decision


def _station_live_gate_reasons(city_key: str, path: Path | None = None) -> list[str]:
    row = get_station(city_key, path)
    if not row:
        return ["station_row_missing"]
    reasons: list[str] = []
    if not str(row.get("settlement_rule_text") or "").strip():
        reasons.append("settlement_rule_text_missing")
    if str(row.get("verification_status") or "") != "verified":
        reasons.append("settlement_rule_unverified")
    settlement_station = str(row.get("settlement_station_id") or row.get("station_id") or "").upper()
    observation_station = str(row.get("station_id") or "").upper()
    if settlement_station and observation_station and settlement_station != observation_station:
        reasons.append("settlement_mismatch")
    return _unique(reasons)


def _list_market_buckets(city: str, target_date: str, *, limit: int, path: Path | None) -> list[dict[str, Any]]:
    with connect(path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM market_buckets
                WHERE city = ? AND target_date = ?
                ORDER BY bucket_low, bucket_high, id
                LIMIT ?
                """,
                (city, target_date, max(1, min(int(limit or 200), 1000))),
            ).fetchall()
        ]
    for row in rows:
        row["neg_risk"] = bool(row.get("neg_risk"))
        row["enable_order_book"] = bool(row.get("enable_order_book"))
        row["strict_match_reasons"] = _loads(row.get("strict_match_reasons"), [])
    return rows


def _select_prediction(predictions: list[dict[str, Any]], issued_at_hour: str | None) -> dict[str, Any] | None:
    if not predictions:
        return None
    if not issued_at_hour:
        return predictions[0]
    target = _hour_key(issued_at_hour)
    for prediction in predictions:
        if _hour_key(prediction.get("issued_at")) == target:
            return prediction
    return predictions[0]


def _evidence_links(
    city: str,
    target_date: str,
    prediction: dict[str, Any],
    buckets: list[dict[str, Any]],
    path: Path | None,
) -> dict[str, Any]:
    with connect(path) as conn:
        hourly_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM hourly_consensus WHERE city = ? AND target_date = ? ORDER BY local_hour LIMIT 24",
                (city, target_date),
            ).fetchall()
        ]
        metar_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM metar_reports WHERE city = ? ORDER BY report_time DESC LIMIT 24",
                (city,),
            ).fetchall()
        ]
    return {
        "daily_max_prediction_id": prediction.get("id"),
        "source_run_ids": prediction.get("source_run_ids") or [],
        "hourly_consensus_ids": hourly_ids,
        "metar_report_ids": metar_ids,
        "market_bucket_ids": [row.get("id") for row in buckets],
    }


def _decision_id(bucket: dict[str, Any], prediction: dict[str, Any]) -> str:
    key = "|".join([
        str(bucket.get("city") or prediction.get("city_key") or ""),
        str(bucket.get("target_date") or prediction.get("target_date") or ""),
        str(bucket.get("yes_token_id") or bucket.get("token_id") or ""),
        _hour_key(prediction.get("issued_at")),
        DECISION_VERSION,
    ])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def _distribution_summary(distribution: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": distribution.get("method"),
        "mu": distribution.get("mu"),
        "sigma": distribution.get("sigma"),
        "unit": distribution.get("unit"),
        "sum_probability": distribution.get("sum_probability"),
        "normalized": distribution.get("normalized"),
        "item_count": len(distribution.get("items") or []),
    }


def _spread_bps(spread: float | None, ask: float | None, bid: float | None) -> float | None:
    if spread is None and ask is not None and bid is not None:
        spread = ask - bid
    if spread is None or ask is None or ask <= 0:
        return None
    return max(0.0, float(spread) / float(ask) * 10_000.0)


def _book_age_seconds(value: Any) -> float | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _hour_key(value: Any) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return str(value or "")
    return parsed.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()


def _is_low_price_tail(bucket: dict[str, Any], ask: float | None) -> bool:
    direction = str(bucket.get("bucket_direction") or "").lower()
    if direction not in {"or_above", "or_below", "above", "below", "under", "over", "at_or_above", "at_or_below"}:
        return False
    return ask is not None and ask < LOW_PRICE_TAIL_ASK


def _optional_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return _loads(value, []) if value else []


def _loads(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default


def _unique(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result
