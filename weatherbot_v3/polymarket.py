from __future__ import annotations

import json
import math
from dataclasses import dataclass
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


class PolymarketDataClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.trust_env = False
        self.cfg = load_config()

    def get_market(self, market_id: str) -> dict[str, Any]:
        resp = self.session.get(f"https://gamma-api.polymarket.com/markets/{market_id}", timeout=(5, 10))
        resp.raise_for_status()
        return resp.json()

    def quote(self, market_id: str) -> MarketQuote:
        data = self.get_market(market_id)
        quote = quote_from_market_payload(data, self.cfg.default_order_min_size, self.cfg.default_tick_size)
        insert_orderbook(market_id, {**data, "yes_token_id": quote.yes_token_id})
        return quote


def quote_from_market_payload(data: dict[str, Any], default_order_min_size: float = 5.0, default_tick_size: float = 0.01) -> MarketQuote:
    prices = _parse_list(data.get("outcomePrices"))
    tokens = _parse_list(data.get("clobTokenIds"))
    yes_price = _to_float(prices[0], 0.0) if prices else 0.0
    best_bid = _to_float(data.get("bestBid"), yes_price)
    best_ask = _to_float(data.get("bestAsk"), yes_price)
    spread = _to_float(data.get("spread"), best_ask - best_bid)
    tick_size = _to_float(data.get("orderPriceMinTickSize"), default_tick_size)
    order_min_size = _to_float(data.get("orderMinSize"), default_order_min_size)
    return MarketQuote(
        market_id=str(data.get("id") or ""),
        yes_token_id=str(tokens[0]) if tokens else "",
        best_bid=round(best_bid, 4),
        best_ask=round(best_ask, 4),
        spread=round(spread, 4),
        volume=_to_float(data.get("volume"), 0.0),
        order_min_size=order_min_size,
        tick_size=tick_size,
        enable_order_book=bool(data.get("enableOrderBook", True)),
        raw=data,
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
    return errors


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


def _to_float(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default

