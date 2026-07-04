from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


DEFAULT_KELLY_MULTIPLIER = 0.15


@dataclass(frozen=True)
class SizingResult:
    kelly_fraction: float
    position_size_usd: float
    capped_position_size_usd: float
    hard_cap_usd: float


def calculate_kelly_fraction(probability: float | None, ask: float | None) -> float:
    """Return binary YES Kelly fraction for a $1 payout contract."""
    p = _num(probability)
    price = _num(ask)
    if p is None or price is None or price <= 0.0 or price >= 1.0:
        return 0.0
    odds = (1.0 / price) - 1.0
    if odds <= 0:
        return 0.0
    fraction = (p * odds - (1.0 - p)) / odds
    return max(0.0, fraction) if math.isfinite(fraction) else 0.0


def size_position(
    probability: float | None,
    ask: float | None,
    *,
    bankroll: float,
    max_per_trade_usd: float,
    kelly_multiplier: float = DEFAULT_KELLY_MULTIPLIER,
) -> SizingResult:
    fraction = calculate_kelly_fraction(probability, ask)
    raw_size = max(0.0, fraction) * max(0.0, float(kelly_multiplier)) * max(0.0, float(bankroll))
    hard_cap = min(max(0.0, float(bankroll)) * 0.05, max(0.0, float(max_per_trade_usd)))
    capped = min(raw_size, hard_cap)
    return SizingResult(
        kelly_fraction=round(fraction, 8),
        position_size_usd=round(raw_size, 4),
        capped_position_size_usd=round(capped, 4),
        hard_cap_usd=round(hard_cap, 4),
    )


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None
