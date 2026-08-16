from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Iterable


def parse_levels(value: Any, *, side: str) -> list[dict[str, float]]:
    raw = value
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    if not isinstance(raw, list):
        return []
    levels: list[dict[str, float]] = []
    for item in raw:
        if isinstance(item, dict):
            price = _number(item.get("price"))
            size = _number(item.get("size", item.get("quantity")))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            price = _number(item[0])
            size = _number(item[1])
        else:
            continue
        if price is None or size is None or not (0 < price < 1) or size <= 0:
            continue
        levels.append({"price": price, "size": size})
    reverse = str(side).lower() == "bids"
    return sorted(levels, key=lambda row: row["price"], reverse=reverse)


def best_level_size(levels: Iterable[dict[str, Any]], *, side: str) -> float:
    normalized = parse_levels(list(levels), side=side)
    return float(normalized[0]["size"]) if normalized else 0.0


def executable_ask_shares(levels: Any, limit_price: float) -> float:
    asks = parse_levels(levels, side="asks")
    return sum(level["size"] for level in asks if level["price"] <= float(limit_price) + 1e-12)


def walk_buy_limit(levels: Any, *, limit_price: float, requested_shares: float) -> dict[str, Any]:
    remaining = max(0.0, float(requested_shares))
    fills: list[dict[str, float]] = []
    for level in parse_levels(levels, side="asks"):
        if remaining <= 1e-12 or level["price"] > float(limit_price) + 1e-12:
            break
        shares = min(remaining, level["size"])
        if shares <= 0:
            continue
        fills.append({
            "price": level["price"],
            "shares": shares,
            "amount": level["price"] * shares,
        })
        remaining -= shares
    filled_shares = sum(row["shares"] for row in fills)
    filled_amount = sum(row["amount"] for row in fills)
    return {
        "fills": fills,
        "filled_shares": filled_shares,
        "filled_amount": filled_amount,
        "average_fill_price": filled_amount / filled_shares if filled_shares > 0 else 0.0,
        "unfilled_shares": max(0.0, float(requested_shares) - filled_shares),
    }


def select_orderbook_as_of(
    conn: Any,
    *,
    decision_time: str,
    yes_token_id: str = "",
    market_id: str = "",
) -> dict[str, Any] | None:
    cutoff = _utc_iso(decision_time)
    token = str(yes_token_id or "").strip()
    market = str(market_id or "").strip()
    if not cutoff or (not token and not market):
        return None
    timestamp_expression = """
        CASE
          WHEN length(trim(COALESCE(quote_timestamp, ''))) >= 13
               AND trim(quote_timestamp) NOT GLOB '*[^0-9]*'
            THEN datetime(CAST(quote_timestamp AS REAL) / 1000.0, 'unixepoch')
          ELSE COALESCE(NULLIF(quote_timestamp, ''), created_at)
        END
    """

    def select_by(column: str, value: str) -> Any:
        return conn.execute(
            f"""
            SELECT *
            FROM orderbooks
            WHERE {column} = ?
              AND julianday({timestamp_expression}) <= julianday(?)
            ORDER BY julianday({timestamp_expression}) DESC, id DESC
            LIMIT 1
            """,
            (value, cutoff),
        ).fetchone()

    # Keep the token lookup isolated so SQLite can use
    # idx_orderbooks_token_id. Combining token and market with OR forces a
    # multi-million-row scan on production databases.
    row = select_by("yes_token_id", token) if token else None
    if row is None and market:
        row = select_by("market_id", market)
    return dict(row) if row is not None else None


def snapshot_from_row(row: dict[str, Any]) -> dict[str, Any]:
    bids = parse_levels(row.get("bids_json", row.get("bids")), side="bids")
    asks = parse_levels(row.get("asks_json", row.get("asks")), side="asks")
    return {
        "snapshot_key": row.get("snapshot_key"),
        "best_bid": row.get("best_bid"),
        "best_ask": row.get("best_ask"),
        "spread": row.get("spread"),
        "bid_depth": row.get("bid_depth"),
        "ask_depth": row.get("ask_depth"),
        "best_bid_size": best_level_size(bids, side="bids"),
        "best_ask_size": best_level_size(asks, side="asks"),
        "bids": bids,
        "asks": asks,
        "quote_timestamp": row.get("quote_timestamp") or row.get("created_at"),
        "source": row.get("snapshot_type") or "stored_orderbook",
        "depth_basis": "price_levels" if bids or asks else "legacy_total_depth",
    }


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _utc_iso(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()
