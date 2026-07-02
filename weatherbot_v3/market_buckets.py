from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from .db import upsert_market_bucket
from .polymarket import quote_from_market_payload


PARSER_VERSION = "market-buckets-v1"
MONTHS = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
}


def ingest_market_buckets(
    payloads: list[dict[str, Any]] | dict[str, Any],
    *,
    city: str = "",
    city_name: str = "",
    target_date: str = "",
    station_id: str = "",
) -> dict[str, Any]:
    markets = _market_payloads(payloads)
    bucket_rows = []
    for market in markets:
        row = market_bucket_from_payload(
            market,
            city=city,
            city_name=city_name,
            target_date=target_date,
            station_id=station_id,
        )
        upsert_market_bucket(row)
        bucket_rows.append(row)
    return {
        "ok": True,
        "parser_version": PARSER_VERSION,
        "requested": len(markets),
        "stored": len(bucket_rows),
        "matched": sum(1 for row in bucket_rows if row.get("strict_match_status") == "matched"),
        "blocked": sum(1 for row in bucket_rows if row.get("strict_match_status") != "matched"),
        "buckets": bucket_rows,
    }


def market_bucket_from_payload(
    payload: dict[str, Any],
    *,
    city: str = "",
    city_name: str = "",
    target_date: str = "",
    station_id: str = "",
) -> dict[str, Any]:
    question = str(payload.get("question") or payload.get("title") or "")
    parsed = parse_temperature_bucket(question)
    inferred_city = city or str(payload.get("city") or "")
    if not inferred_city:
        inferred_city = parse_city_from_question(question)
    inferred_date = target_date or str(payload.get("target_date") or payload.get("targetDate") or "")
    if not inferred_date:
        inferred_date = parse_date_from_question(question)

    quote = quote_from_market_payload(payload, default_order_min_size=0.0, default_tick_size=0.0)
    prices = _parse_list(payload.get("outcomePrices"))
    tokens = _parse_list(payload.get("clobTokenIds"))
    outcomes = _parse_list(payload.get("outcomes"))
    yes_token_id = str(payload.get("yes_token_id") or quote.yes_token_id or (tokens[0] if tokens else ""))
    no_token_id = str(payload.get("no_token_id") or (tokens[1] if len(tokens) > 1 else ""))
    outcome_name = _outcome_name(outcomes)
    best_bid = quote.best_bid if quote.best_bid > 0 else _num(payload.get("bestBid"))
    best_ask = quote.best_ask if quote.best_ask > 0 else _num(payload.get("bestAsk"))
    price = best_ask or (_num(prices[0]) if prices else None)
    spread = quote.spread if quote.spread > 0 else (
        round(best_ask - best_bid, 6) if best_ask is not None and best_bid is not None else None
    )

    row = {
        "event_slug": str(payload.get("eventSlug") or payload.get("event_slug") or payload.get("event") or ""),
        "event_url": str(payload.get("event_url") or payload.get("eventUrl") or _event_url(payload)),
        "market_id": str(payload.get("id") or payload.get("market_id") or ""),
        "condition_id": str(payload.get("conditionId") or payload.get("condition_id") or ""),
        "question": question,
        "city": inferred_city,
        "city_name": city_name or str(payload.get("city_name") or inferred_city),
        "target_date": inferred_date,
        "station_id": station_id or str(payload.get("station_id") or ""),
        "unit": parsed.get("unit") or str(payload.get("unit") or ""),
        "bucket_label": parsed.get("label") or str(payload.get("bucket_label") or ""),
        "bucket_direction": parsed.get("direction") or "",
        "bucket_low": parsed.get("low"),
        "bucket_high": parsed.get("high"),
        "outcome_name": outcome_name,
        "yes_token_id": yes_token_id,
        "no_token_id": no_token_id,
        "token_id": yes_token_id,
        "token_side": "YES",
        "outcome_index": 0,
        "price": price,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "volume": _num(payload.get("volume")),
        "liquidity": _num(payload.get("liquidity")),
        "order_min_size": quote.order_min_size or _num(payload.get("orderMinSize")),
        "tick_size": quote.tick_size or _num(payload.get("orderPriceMinTickSize")),
        "neg_risk": _bool(payload.get("negRisk", payload.get("neg_risk", False))),
        "enable_order_book": _bool(payload.get("enableOrderBook", payload.get("enable_order_book", True))),
        "quote_timestamp": quote.quote_timestamp or str(payload.get("quote_timestamp") or payload.get("timestamp") or ""),
        "orderbook_snapshot_key": str(payload.get("snapshot_key") or ""),
        "orderbook_source": quote.book_source,
        "bid_depth": round(sum(level["size"] for level in quote.bids), 6) if quote.bids else _num(payload.get("bid_depth")),
        "ask_depth": round(sum(level["size"] for level in quote.asks), 6) if quote.asks else _num(payload.get("ask_depth")),
        "source_url": str(payload.get("source_url") or ""),
        "raw_response_hash": str(payload.get("raw_response_hash") or ""),
        "parser_version": PARSER_VERSION,
        "raw_json": payload,
    }
    reasons = strict_match_reasons(row)
    row["strict_match_status"] = "matched" if not reasons else "blocked"
    row["strict_match_reasons"] = reasons
    return row


def parse_temperature_bucket(question: str) -> dict[str, Any]:
    text = _normalize_question(question)
    unit = parse_unit(text)
    between = re.search(
        r"between\s+(-?\d+(?:\.\d+)?)\s*(?:-|to|and)\s*(-?\d+(?:\.\d+)?)\s*(?:°?\s*)?([cf])",
        text,
    )
    if between:
        low = float(between.group(1))
        high = float(between.group(2))
        unit = between.group(3).upper()
        return {
            "direction": "range",
            "low": min(low, high),
            "high": max(low, high),
            "unit": unit,
            "label": f"{min(low, high):g}-{max(low, high):g}{unit}",
        }
    lower = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:°?\s*)?([cf])?\s*(?:or\s+)?(?:below|lower|under|or below)", text)
    if lower:
        value = float(lower.group(1))
        unit = (lower.group(2) or unit or "").upper()
        return {"direction": "or_below", "low": -999.0, "high": value, "unit": unit, "label": f"{value:g}{unit} or below"}
    upper = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:°?\s*)?([cf])?\s*(?:or\s+)?(?:above|higher|over|or above)", text)
    if upper:
        value = float(upper.group(1))
        unit = (upper.group(2) or unit or "").upper()
        return {"direction": "or_above", "low": value, "high": 999.0, "unit": unit, "label": f"{value:g}{unit} or above"}
    exact = re.search(r"\bbe\s+(-?\d+(?:\.\d+)?)\s*(?:°?\s*)?([cf])\b", text)
    if exact:
        value = float(exact.group(1))
        unit = exact.group(2).upper()
        return {"direction": "exact", "low": value, "high": value, "unit": unit, "label": f"{value:g}{unit}"}
    return {"direction": "", "low": None, "high": None, "unit": unit or "", "label": ""}


def parse_city_from_question(question: str) -> str:
    match = re.search(r"highest temperature in\s+(.+?)\s+be\b", question, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def parse_date_from_question(question: str) -> str:
    match = re.search(
        r"\bon\s+("
        + "|".join(MONTHS)
        + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,\s*(\d{4}))?",
        question,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    year = match.group(3) or str(datetime.utcnow().year)
    return f"{year}-{MONTHS[match.group(1).lower()]}-{int(match.group(2)):02d}"


def parse_unit(question: str) -> str:
    match = re.search(r"°?\s*([cf])\b", question, flags=re.IGNORECASE)
    return match.group(1).upper() if match else ""


def strict_match_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    required = {
        "city": "city_missing",
        "target_date": "target_date_missing",
        "unit": "unit_missing",
        "market_id": "market_id_missing",
        "yes_token_id": "yes_token_missing",
    }
    for field, reason in required.items():
        if not row.get(field):
            reasons.append(reason)
    if row.get("bucket_low") is None and row.get("bucket_high") is None:
        reasons.append("temperature_bucket_unparsed")
    if row.get("tick_size") in (None, 0):
        reasons.append("tick_size_missing")
    if row.get("order_min_size") in (None, 0):
        reasons.append("order_min_size_missing")
    if row.get("enable_order_book") is False:
        reasons.append("orderbook_disabled")
    if row.get("price") is None and row.get("best_ask") is None:
        reasons.append("quote_price_missing")
    return reasons


def _market_payloads(payloads: list[dict[str, Any]] | dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payloads, list):
        return [item for item in payloads if isinstance(item, dict)]
    if not isinstance(payloads, dict):
        return []
    for key in ("markets", "data", "items"):
        value = payloads.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return [payloads]


def _normalize_question(question: str) -> str:
    return (
        str(question or "")
        .replace("–", "-")
        .replace("—", "-")
        .replace("℉", "F")
        .replace("℃", "C")
        .lower()
    )


def _parse_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if raw in (None, ""):
        return []
    try:
        value = json.loads(str(raw))
        return value if isinstance(value, list) else []
    except Exception:
        return []


def _outcome_name(outcomes: list[Any]) -> str:
    if not outcomes:
        return "Yes"
    first = outcomes[0]
    if isinstance(first, dict):
        return str(first.get("name") or first.get("outcome") or "Yes")
    return str(first)


def _event_url(payload: dict[str, Any]) -> str:
    slug = payload.get("eventSlug") or payload.get("event_slug")
    return f"https://polymarket.com/event/{slug}" if slug else ""


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}
