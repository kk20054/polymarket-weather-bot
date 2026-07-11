from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import apply_paper_settlement_record, connect, list_paper_orders, list_settlements
from .polymarket import PolymarketDataClient
from .registry import get_city_profile


SETTLEMENT_VERSION = "paper-settlement-v1"


def settle_open_paper_orders(
    *,
    city_key: str | None = None,
    target_date: str | None = None,
    limit: int = 200,
    refresh_gamma: bool = False,
    apply: bool = False,
    path: Path | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    orders = list_paper_orders(city_key=city_key, target_date=target_date, limit=limit, path=path)
    candidates = [order for order in orders if str(order.get("lifecycle_status") or "") == "open"]
    legacy_skipped = sum(1 for order in orders if not _order_identity_complete(order))
    results = []
    market_client = client or (PolymarketDataClient() if refresh_gamma and candidates else None)
    for order in candidates:
        if not _order_identity_complete(order):
            results.append({"ok": False, "status": "skipped", "reason": "paper_order_identity_incomplete", "order_id": order.get("id")})
            continue
        results.append(_settle_order(order, refresh_gamma=refresh_gamma, apply=apply, path=path, client=market_client))
    stored = list_settlements(city_key=city_key, target_date=target_date, limit=limit, path=path)
    resolved = [row for row in stored if row.get("settlement_status") == "resolved"]
    wins = [row for row in resolved if row.get("result") == "win"]
    brier = [float(row["brier_score"]) for row in resolved if row.get("brier_score") is not None]
    return {
        "ok": all(row.get("ok") or row.get("status") in {"pending", "skipped"} for row in results),
        "version": SETTLEMENT_VERSION,
        "apply": apply,
        "refresh_gamma": refresh_gamma,
        "orders_scanned": len(orders),
        "candidates": len(candidates),
        "legacy_skipped": legacy_skipped,
        "resolved_now": sum(1 for row in results if row.get("settlement_status") == "resolved"),
        "provisional_now": sum(1 for row in results if str(row.get("settlement_status") or "").startswith("provisional")),
        "pending_now": sum(1 for row in results if row.get("status") == "pending"),
        "resolved_total": len(resolved),
        "wins": len(wins),
        "losses": len(resolved) - len(wins),
        "win_rate": len(wins) / len(resolved) if resolved else None,
        "realized_pnl": round(sum(float(row.get("pnl") or 0.0) for row in resolved), 6),
        "brier_score": sum(brier) / len(brier) if brier else None,
        "results": results,
    }


def market_resolution_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or not payload:
        return {"resolved": False, "reason": "market_payload_missing"}
    if payload.get("closed") is not True:
        return {"resolved": False, "reason": "market_not_closed"}
    outcomes = _json_list(payload.get("outcomes"))
    prices = [_number(value) for value in _json_list(payload.get("outcomePrices"))]
    if not outcomes or len(prices) != len(outcomes) or any(value is None for value in prices):
        return {"resolved": False, "reason": "terminal_outcome_prices_missing"}
    yes_index = next((index for index, value in enumerate(outcomes) if str(value).strip().lower() == "yes"), None)
    no_index = next((index for index, value in enumerate(outcomes) if str(value).strip().lower() == "no"), None)
    if yes_index is None or no_index is None:
        return {"resolved": False, "reason": "yes_no_outcomes_missing"}
    yes_price = float(prices[yes_index])
    no_price = float(prices[no_index])
    if yes_price >= 0.99 and no_price <= 0.01:
        outcome_yes = 1
    elif no_price >= 0.99 and yes_price <= 0.01:
        outcome_yes = 0
    else:
        return {"resolved": False, "reason": "terminal_prices_not_binary", "yes_price": yes_price, "no_price": no_price}
    return {
        "resolved": True,
        "outcome_yes": outcome_yes,
        "yes_price": yes_price,
        "no_price": no_price,
        "source": "polymarket_gamma_resolved",
        "resolved_at": str(payload.get("updatedAt") or payload.get("closedTime") or datetime.now(timezone.utc).isoformat()),
    }


def truth_outcome_for_order(order: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    city = str(order.get("city_key") or "")
    target_date = str(order.get("target_date") or "")
    profile = get_city_profile(city)
    if profile is None or not target_date:
        return {"available": False, "reason": "city_or_target_date_missing"}
    with connect(path) as conn:
        if city == "hong-kong":
            row = conn.execute(
                "SELECT high_c, source_url FROM truth_hko_daily WHERE date_local = ? AND high_c IS NOT NULL",
                (target_date,),
            ).fetchone()
            if row:
                truth = {"actual_c": float(row["high_c"]), "provider": "hong_kong_observatory_daily_extract", "station": "HKO", "exact": True, "source_url": row["source_url"]}
            else:
                truth = None
        else:
            row = conn.execute(
                "SELECT high_c, source_url FROM truth_wunderground_daily WHERE UPPER(icao) = ? AND date_local = ? AND high_c IS NOT NULL",
                (str(profile.station_id or "").upper(), target_date),
            ).fetchone()
            truth = ({"actual_c": float(row["high_c"]), "provider": "wunderground_daily", "station": profile.station_id, "exact": True, "source_url": row["source_url"]} if row else None)
        if truth is None:
            row = conn.execute(
                "SELECT high_c, source_url FROM truth_iem_daily WHERE UPPER(icao) = ? AND date_local = ? AND high_c IS NOT NULL",
                (str(profile.station_id or "").upper(), target_date),
            ).fetchone()
            truth = ({"actual_c": float(row["high_c"]), "provider": "iem_asos_approximation", "station": profile.station_id, "exact": False, "source_url": row["source_url"]} if row else None)
        market = conn.execute(
            "SELECT bucket_lower_c, bucket_upper_c, is_tail, bucket_label FROM polymarket_markets WHERE market_id = ?",
            (str(order.get("market_id") or ""),),
        ).fetchone()
        bucket = dict(market) if market else _market_bucket_for_order(conn, order)
    if truth is None:
        return {"available": False, "reason": "settlement_truth_missing"}
    if not bucket:
        return {"available": False, "reason": "market_bucket_missing", **truth}
    outcome_yes = bucket_contains_celsius(float(truth["actual_c"]), bucket)
    return {"available": True, "outcome_yes": 1 if outcome_yes else 0, "bucket": bucket, **truth}


def bucket_contains_celsius(actual_c: float, bucket: dict[str, Any]) -> bool:
    lower = _number(bucket.get("bucket_lower_c"))
    upper = _number(bucket.get("bucket_upper_c"))
    if lower is None and upper is None:
        unit = str(bucket.get("unit") or "C").upper()
        actual = actual_c if unit == "C" else actual_c * 9.0 / 5.0 + 32.0
        direction = str(bucket.get("bucket_direction") or "exact").lower()
        low = _number(bucket.get("bucket_low"))
        high = _number(bucket.get("bucket_high"))
        resolved_value = math.floor(actual) if unit == "C" else round(actual)
        if direction in {"or_below", "below", "at_or_below"}:
            bound = high if high is not None else low
            return bound is not None and resolved_value <= bound
        if direction in {"or_above", "above", "at_or_above"}:
            bound = low if low is not None else high
            return bound is not None and resolved_value >= bound
        if direction == "between":
            return low is not None and high is not None and low <= resolved_value <= high
        bound = low if low is not None else high
        return bound is not None and resolved_value == bound
    return (lower is None or actual_c >= lower) and (upper is None or actual_c < upper)


def _settle_order(order: dict[str, Any], *, refresh_gamma: bool, apply: bool, path: Path | None, client: Any | None) -> dict[str, Any]:
    market_payload = _stored_market_payload(str(order.get("market_id") or ""), path)
    refresh_error = ""
    if refresh_gamma and client is not None:
        try:
            market_payload = client.get_market(str(order.get("market_id") or ""))
            if apply:
                _store_market_payload(str(order.get("market_id") or ""), market_payload, path)
        except Exception as exc:
            refresh_error = str(exc)
    resolution = market_resolution_from_payload(market_payload)
    truth = truth_outcome_for_order(order, path=path)
    if resolution.get("resolved"):
        outcome_yes = int(resolution["outcome_yes"])
        settlement_status = "resolved"
        settlement_source = "polymarket_gamma_resolved"
    elif truth.get("available"):
        outcome_yes = int(truth["outcome_yes"])
        settlement_status = "provisional_truth" if truth.get("exact") else "provisional_approximation"
        settlement_source = str(truth.get("provider") or "")
    else:
        return {
            "ok": True,
            "status": "pending",
            "order_id": order.get("id"),
            "market_id": order.get("market_id"),
            "reason": resolution.get("reason") or truth.get("reason") or "unresolved",
            "refresh_error": refresh_error,
        }
    shares = float(order.get("filled_shares") or order.get("shares") or 0.0)
    cost = float(order.get("filled_amount") or order.get("amount") or 0.0)
    payout = shares * outcome_yes
    pnl = payout - cost
    model_probability = _number(order.get("model_probability"))
    market_probability = _number(order.get("market_probability"))
    settlement = {
        "settlement_key": f"paper_settlement:{int(order['id'])}",
        "settlement_status": settlement_status,
        "settlement_source": settlement_source,
        "outcome_yes": outcome_yes,
        "result": "win" if outcome_yes else "loss",
        "actual_temp": truth.get("actual_c") if truth.get("available") else None,
        "actual_provider": truth.get("provider") if truth.get("available") else "",
        "actual_station": truth.get("station") if truth.get("available") else "",
        "actual_confidence": 1.0 if truth.get("exact") else (0.5 if truth.get("available") else None),
        "calibration_eligible": bool(truth.get("exact")),
        "payout": payout,
        "pnl": pnl,
        "brier_score": (model_probability - outcome_yes) ** 2 if model_probability is not None else None,
        "market_brier_score": (market_probability - outcome_yes) ** 2 if market_probability is not None else None,
        "settled_at": resolution.get("resolved_at") or "",
        "resolution": resolution,
        "truth": truth,
        "version": SETTLEMENT_VERSION,
    }
    if apply:
        stored = apply_paper_settlement_record(int(order["id"]), settlement, path=path)
        settlement = stored.get("settlement") or settlement
    return {
        "ok": True,
        "status": "settled" if settlement_status == "resolved" else "provisional",
        "order_id": order.get("id"),
        "market_id": order.get("market_id"),
        "settlement_status": settlement_status,
        "outcome_yes": outcome_yes,
        "pnl": pnl if settlement_status == "resolved" else None,
        "brier_score": settlement.get("brier_score"),
        "refresh_error": refresh_error,
        "applied": apply,
    }


def _order_identity_complete(order: dict[str, Any]) -> bool:
    return bool(order.get("id") and order.get("decision_id") and order.get("market_id") and order.get("yes_token_id") and order.get("city_key") and order.get("target_date"))


def _stored_market_payload(market_id: str, path: Path | None) -> dict[str, Any]:
    with connect(path) as conn:
        row = conn.execute("SELECT raw_json FROM polymarket_markets WHERE market_id = ?", (market_id,)).fetchone()
    return _loads(row["raw_json"] if row else None, {})


def _store_market_payload(market_id: str, payload: dict[str, Any], path: Path | None) -> None:
    with connect(path) as conn:
        conn.execute(
            "UPDATE polymarket_markets SET raw_json = ?, updated_at = ? WHERE market_id = ?",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True), datetime.now(timezone.utc).isoformat(), market_id),
        )


def _market_bucket_for_order(conn, order: dict[str, Any]) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT unit, bucket_direction, bucket_low, bucket_high, bucket_label
        FROM market_buckets
        WHERE market_id = ? OR bucket_key = ?
        ORDER BY id DESC LIMIT 1
        """,
        (str(order.get("market_id") or ""), str(order.get("bucket_key") or "")),
    ).fetchone()
    return dict(row) if row else {}


def _json_list(value: Any) -> list[Any]:
    parsed = _loads(value, [])
    return parsed if isinstance(parsed, list) else []


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return default
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None
