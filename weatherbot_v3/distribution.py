from __future__ import annotations

import math
from typing import Any


def build_event_distribution(
    outcomes: list[dict[str, Any]],
    forecast_value: float | None,
    unit: str = "F",
    sigma_f: float = 3.0,
    bias_f: float = 0.0,
    signal_market_id: str | None = None,
) -> dict[str, Any]:
    if forecast_value is None or not outcomes:
        return {"items": [], "sum_probability": 0.0, "normalized": False, "notes": ["missing_forecast_or_outcomes"]}

    forecast_f = _native_to_f(float(forecast_value), unit) - float(bias_f or 0.0)
    rows = []
    raw_sum = 0.0
    for outcome in outcomes:
        bounds = outcome.get("range") or outcome.get("bucket_range")
        if not bounds or len(bounds) != 2:
            continue
        low, high = bounds
        low_f = -999.0 if float(low) <= -900 else _native_to_f(float(low), unit)
        high_f = 999.0 if float(high) >= 900 else _native_to_f(float(high), unit)
        probability = _bucket_probability_f(forecast_f, low_f, high_f, sigma_f)
        raw_sum += probability
        ask = _num(outcome.get("ask"), _num(outcome.get("price"), 0.0))
        bid = _num(outcome.get("bid"), 0.0)
        rows.append({
            "market_id": str(outcome.get("market_id") or ""),
            "question": outcome.get("question") or "",
            "bucket_low": float(low),
            "bucket_high": float(high),
            "probability_raw": round(probability, 6),
            "probability": 0.0,
            "ask": ask,
            "bid": bid,
            "spread": _num(outcome.get("spread"), ask - bid if ask and bid else 0.0),
            "probability_edge": 0.0,
            "ev": 0.0,
            "is_signal": bool(signal_market_id and str(outcome.get("market_id") or "") == str(signal_market_id)),
        })

    if raw_sum <= 0:
        return {"items": rows, "sum_probability": 0.0, "normalized": False, "notes": ["zero_probability_mass"]}

    for row in rows:
        probability = row["probability_raw"] / raw_sum
        ask = row["ask"]
        row["probability"] = round(probability, 4)
        row["probability_edge"] = round(probability - ask, 4) if ask else 0.0
        row["ev"] = round(_calc_ev(probability, ask), 4) if ask else 0.0
        row["spread_cost_ratio"] = round((row["spread"] / ask), 4) if ask else None

    ranked_model = sorted(rows, key=lambda item: item["probability"], reverse=True)
    ranked_market = sorted(rows, key=lambda item: item["ask"], reverse=True)
    return {
        "items": rows,
        "sum_probability": round(sum(row["probability"] for row in rows), 4),
        "normalized": True,
        "forecast_f": round(forecast_f, 2),
        "sigma_f": round(float(sigma_f or 0), 2),
        "bias_f": round(float(bias_f or 0), 2),
        "top_model": ranked_model[:5],
        "top_market": ranked_market[:5],
        "notes": [],
    }


def _bucket_probability_f(forecast_f: float, low_f: float, high_f: float, sigma_f: float) -> float:
    sigma = max(0.5, float(sigma_f or 0))
    if low_f <= -900:
        return max(0.0, min(1.0, _norm_cdf((high_f + 0.5 - forecast_f) / sigma)))
    if high_f >= 900:
        return max(0.0, min(1.0, 1.0 - _norm_cdf((low_f - 0.5 - forecast_f) / sigma)))
    return max(0.0, min(1.0, _norm_cdf((high_f + 0.5 - forecast_f) / sigma) - _norm_cdf((low_f - 0.5 - forecast_f) / sigma)))


def _native_to_f(value: float, unit: str) -> float:
    return value * 9.0 / 5.0 + 32.0 if unit == "C" else value


def _norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(float(value) / math.sqrt(2.0)))


def _calc_ev(probability: float, price: float) -> float:
    if price <= 0 or price >= 1:
        return 0.0
    return probability * (1.0 / price - 1.0) - (1.0 - probability)


def _num(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default
