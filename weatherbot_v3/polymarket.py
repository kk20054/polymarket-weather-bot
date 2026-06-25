from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from .config import load_config
from .db import insert_orderbook


@dataclass(frozen=True)
class MarketQuote:
    market_id: str
    yes_token_id: str
    best_bid: float
    best_ask: float
    spread: float
    volume: float
    order_min_size: float
    tick_size: float
    enable_order_book: bool
    raw: dict[str, Any]
    book_source: str = "gamma"
    quote_timestamp: str = ""
    book_hash: str = ""
    bids: tuple[dict[str, float], ...] = ()
    asks: tuple[dict[str, float], ...] = ()
    quote_age_seconds: float | None = None


class PolymarketDataClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.trust_env = False
        self.cfg = load_config()

    def get_market(self, market_id: str) -> dict[str, Any]:
        resp = self.session.get(f"https://gamma-api.polymarket.com/markets/{market_id}", timeout=(5, 10))
        resp.raise_for_status()
        return resp.json()

    def get_orderbook(self, token_id: str) -> dict[str, Any]:
        resp = self.session.get(
            "https://clob.polymarket.com/book",
            params={"token_id": token_id},
            timeout=(5, 10),
        )
        resp.raise_for_status()
        data = resp.json()
        data["snapshot_type"] = "clob"
        data["source_url"] = resp.url
        return data

    def quote(self, market_id: str) -> MarketQuote:
        data = self.get_market(market_id)
        gamma_quote = quote_from_market_payload(data, self.cfg.default_order_min_size, self.cfg.default_tick_size)
        merged = {**data, "yes_token_id": gamma_quote.yes_token_id}
        if gamma_quote.yes_token_id:
            try:
                book = self.get_orderbook(gamma_quote.yes_token_id)
                merged.update(book)
                merged["yes_token_id"] = gamma_quote.yes_token_id
                merged["volume"] = data.get("volume")
                merged["enableOrderBook"] = data.get("enableOrderBook", True)
            except Exception as exc:
                merged["snapshot_type"] = "gamma_fallback"
                merged["orderbook_error"] = str(exc)
        quote = quote_from_market_payload(merged, self.cfg.default_order_min_size, self.cfg.default_tick_size)
        insert_orderbook(market_id, merged)
        return quote


def quote_from_market_payload(data: dict[str, Any], default_order_min_size: float = 5.0, default_tick_size: float = 0.01) -> MarketQuote:
    prices = _parse_list(data.get("outcomePrices"))
    tokens = _parse_list(data.get("clobTokenIds"))
    yes_price = _to_float(prices[0], 0.0) if prices else 0.0
    bids = tuple(_levels(data.get("bids")))
    asks = tuple(_levels(data.get("asks")))
    best_bid = max((level["price"] for level in bids), default=_to_float(data.get("bestBid"), yes_price))
    best_ask = min((level["price"] for level in asks), default=_to_float(data.get("bestAsk"), yes_price))
    spread = _to_float(data.get("spread"), best_ask - best_bid)
    tick_size = _to_float(data.get("tick_size"), _to_float(data.get("orderPriceMinTickSize"), default_tick_size))
    order_min_size = _to_float(data.get("min_order_size"), _to_float(data.get("orderMinSize"), default_order_min_size))
    quote_timestamp = str(data.get("quote_timestamp") or data.get("timestamp") or "")
    quote_age_seconds = _quote_age_seconds(quote_timestamp)
    return MarketQuote(
        market_id=str(data.get("id") or ""),
        yes_token_id=str(tokens[0]) if tokens else str(data.get("yes_token_id") or data.get("asset_id") or ""),
        best_bid=round(best_bid, 4),
        best_ask=round(best_ask, 4),
        spread=round(spread, 4),
        volume=_to_float(data.get("volume"), 0.0),
        order_min_size=order_min_size,
        tick_size=tick_size,
        enable_order_book=bool(data.get("enableOrderBook", True)),
        raw=data,
        book_source=str(data.get("snapshot_type") or ("clob" if bids or asks else "gamma")),
        quote_timestamp=quote_timestamp,
        book_hash=str(data.get("hash") or ""),
        bids=bids,
        asks=asks,
        quote_age_seconds=quote_age_seconds,
    )


def validate_order_constraints(quote: MarketQuote, amount: float, limit_price: float) -> list[str]:
    cfg = load_config()
    errors: list[str] = []
    if not quote.enable_order_book:
        errors.append("orderbook_disabled")
    if quote.best_ask <= 0 or quote.best_ask >= 1:
        errors.append("invalid_best_ask")
    if quote.best_ask > cfg.max_price:
        errors.append("ask_above_max_price")
    if quote.best_ask < cfg.min_price:
        errors.append("ask_below_min_price")
    if quote.spread > cfg.max_slippage:
        errors.append("spread_above_max_slippage")
    if not price_matches_tick(limit_price, quote.tick_size):
        errors.append("price_not_on_tick")
    shares = amount / limit_price if limit_price > 0 else 0
    if amount < quote.order_min_size and shares < quote.order_min_size:
        errors.append("below_order_min_size")
    if amount <= 0:
        errors.append("non_positive_amount")
    if not quote.yes_token_id:
        errors.append("missing_yes_token")
    if quote.quote_age_seconds is not None and quote.quote_age_seconds > cfg.orderbook_max_age_minutes * 60:
        errors.append("orderbook_stale")
    return errors


def estimate_buy_fill(quote: MarketQuote, amount: float, limit_price: float) -> dict[str, Any]:
    remaining = max(0.0, float(amount))
    filled_shares = 0.0
    spent = 0.0
    fills = []
    for level in sorted(quote.asks, key=lambda item: item["price"]):
        price = float(level["price"])
        if price > limit_price + 1e-9 or remaining <= 1e-9:
            break
        available_shares = max(0.0, float(level["size"]))
        shares = min(available_shares, remaining / price)
        if shares <= 0:
            continue
        cost = shares * price
        fills.append({"price": price, "shares": shares, "amount": cost})
        filled_shares += shares
        spent += cost
        remaining -= cost
    average_price = spent / filled_shares if filled_shares > 0 else 0.0
    return {
        "filled_shares": round(filled_shares, 6),
        "filled_amount": round(spent, 6),
        "remaining_amount": round(max(0.0, remaining), 6),
        "average_price": round(average_price, 6),
        "fully_filled": remaining <= 0.01,
        "fills": fills,
    }


def round_price_to_tick(price: float, tick_size: float) -> float:
    if tick_size <= 0:
        return round(price, 4)
    decimals = max(0, min(6, len(str(tick_size).split(".")[-1]) if "." in str(tick_size) else 0))
    return round(math.floor((price + 1e-9) / tick_size) * tick_size, decimals)


def price_matches_tick(price: float, tick_size: float) -> bool:
    if tick_size <= 0:
        return True
    ticks = price / tick_size
    return abs(ticks - round(ticks)) < 1e-6


def _parse_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if not raw:
        return []
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except Exception:
        return []


def _levels(raw: Any) -> list[dict[str, float]]:
    values = _parse_list(raw) if isinstance(raw, str) else (raw or [])
    levels = []
    for item in values:
        try:
            levels.append({"price": float(item.get("price")), "size": float(item.get("size"))})
        except Exception:
            continue
    return levels


def _quote_age_seconds(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        raw = str(value)
        if raw.isdigit():
            timestamp = datetime.fromtimestamp(int(raw) / 1000.0, tz=timezone.utc)
        else:
            timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def _to_float(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default
