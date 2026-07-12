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
    kelly_multiplier: float
    bankroll_usd: float
    bankroll_fraction_cap: float
    caps: dict[str, float]
    cap_reasons: tuple[str, ...]

    def snapshot(self) -> dict[str, Any]:
        return {
            "kelly_fraction": self.kelly_fraction,
            "kelly_multiplier": self.kelly_multiplier,
            "bankroll_usd": self.bankroll_usd,
            "bankroll_fraction_cap": self.bankroll_fraction_cap,
            "raw_position_size_usd": self.position_size_usd,
            "final_position_size_usd": self.capped_position_size_usd,
            "hard_cap_usd": self.hard_cap_usd,
            "caps": dict(self.caps),
            "cap_reasons": list(self.cap_reasons),
        }


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
    bankroll_fraction_cap: float = 0.05,
) -> SizingResult:
    return size_for_cohort(
        probability,
        ask,
        bankroll=bankroll,
        max_per_trade_usd=max_per_trade_usd,
        kelly_multiplier=kelly_multiplier,
        bankroll_fraction_cap=bankroll_fraction_cap,
    )


def size_for_cohort(
    probability: float | None,
    ask: float | None,
    *,
    bankroll: float,
    max_per_trade_usd: float,
    kelly_multiplier: float = DEFAULT_KELLY_MULTIPLIER,
    bankroll_fraction_cap: float = 0.05,
    strategy_cap_usd: float | None = None,
    remaining_daily_usd: float | None = None,
    cash_available_usd: float | None = None,
    exposure_multiplier: float = 1.0,
) -> SizingResult:
    fraction = calculate_kelly_fraction(probability, ask)
    clean_bankroll = max(0.0, float(bankroll))
    clean_multiplier = max(0.0, float(kelly_multiplier))
    clean_fraction_cap = max(0.0, float(bankroll_fraction_cap))
    raw_size = fraction * clean_multiplier * clean_bankroll * max(0.0, float(exposure_multiplier))
    caps = {
        "bankroll_fraction_cap_usd": clean_bankroll * clean_fraction_cap,
        "cohort_max_per_trade_usd": max(0.0, float(max_per_trade_usd)),
    }
    optional_caps = {
        "strategy_cap_usd": strategy_cap_usd,
        "remaining_daily_usd": remaining_daily_usd,
        "cash_available_usd": cash_available_usd,
    }
    for key, value in optional_caps.items():
        if value is not None:
            caps[key] = max(0.0, float(value))
    hard_cap = min(caps.values(), default=0.0)
    capped = min(raw_size, hard_cap)
    cap_reasons = tuple(
        key for key, value in caps.items()
        if raw_size > value + 1e-9 and abs(value - hard_cap) <= 1e-9
    )
    return SizingResult(
        kelly_fraction=round(fraction, 8),
        position_size_usd=round(raw_size, 4),
        capped_position_size_usd=round(capped, 4),
        hard_cap_usd=round(hard_cap, 4),
        kelly_multiplier=round(clean_multiplier, 8),
        bankroll_usd=round(clean_bankroll, 4),
        bankroll_fraction_cap=round(clean_fraction_cap, 8),
        caps={key: round(value, 4) for key, value in caps.items()},
        cap_reasons=cap_reasons,
    )


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None
