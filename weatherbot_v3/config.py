from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "config.json"


def _read_config() -> dict[str, Any]:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


@dataclass(frozen=True)
class V3Config:
    live_trading: bool
    live_dry_run: bool
    ai_review_enabled: bool
    ai_required_for_live: bool
    ai_provider: str
    minimax_api_key: str
    minimax_base_url: str
    minimax_model: str
    feishu_webhook_url: str
    max_bet: float
    live_max_order_usd: float
    live_daily_max_usd: float
    live_max_open_positions: int
    live_daily_loss_limit: float
    live_max_drawdown_pct: float
    max_price: float
    max_slippage: float
    orderbook_max_age_minutes: float
    default_order_min_size: float
    default_tick_size: float
    v3_db_path: Path


def load_config() -> V3Config:
    file_cfg = _read_config()

    def get(name: str, default: Any = None) -> Any:
        return os.getenv(name, file_cfg.get(name.lower(), file_cfg.get(name, default)))

    max_bet = _float(get("MAX_BET", file_cfg.get("max_bet", 2.0)), 2.0)
    return V3Config(
        live_trading=_bool(get("LIVE_TRADING", False), False),
        live_dry_run=_bool(get("LIVE_DRY_RUN", True), True),
        ai_review_enabled=_bool(get("AI_REVIEW_ENABLED", False), False),
        ai_required_for_live=_bool(get("AI_REQUIRED_FOR_LIVE", False), False),
        ai_provider=str(get("AI_PROVIDER", "minimax") or "minimax"),
        minimax_api_key=str(get("MINIMAX_API_KEY", "") or ""),
        minimax_base_url=str(get("MINIMAX_BASE_URL", "https://api.minimax.io/v1") or "https://api.minimax.io/v1").rstrip("/"),
        minimax_model=str(get("MINIMAX_MODEL", "MiniMax-M3") or "MiniMax-M3"),
        feishu_webhook_url=str(get("FEISHU_WEBHOOK_URL", "") or ""),
        max_bet=max_bet,
        live_max_order_usd=_float(get("LIVE_MAX_ORDER_USD", max_bet), max_bet),
        live_daily_max_usd=_float(get("LIVE_DAILY_MAX_USD", 10.0), 10.0),
        live_max_open_positions=_int(get("LIVE_MAX_OPEN_POSITIONS", 5), 5),
        live_daily_loss_limit=_float(get("LIVE_DAILY_LOSS_LIMIT", 5.0), 5.0),
        live_max_drawdown_pct=_float(get("LIVE_MAX_DRAWDOWN_PCT", 0.15), 0.15),
        max_price=_float(get("MAX_PRICE", file_cfg.get("max_price", 0.45)), 0.45),
        max_slippage=_float(get("MAX_SLIPPAGE", file_cfg.get("max_slippage", 0.03)), 0.03),
        orderbook_max_age_minutes=_float(get("ORDERBOOK_MAX_AGE_MINUTES", 10.0), 10.0),
        default_order_min_size=_float(get("DEFAULT_ORDER_MIN_SIZE", 5.0), 5.0),
        default_tick_size=_float(get("DEFAULT_TICK_SIZE", 0.01), 0.01),
        v3_db_path=Path(str(get("V3_DB_PATH", DATA_DIR / "weatherbot_v3.db"))),
    )

