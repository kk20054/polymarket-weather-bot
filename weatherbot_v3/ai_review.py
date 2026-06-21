from __future__ import annotations

import json
from typing import Any

import requests

from .config import load_config
from .db import insert_ai_review


def default_review(signal: dict[str, Any], reason: str = "ai_disabled") -> dict[str, Any]:
    return {
        "provider": "none",
        "model": "none",
        "approve": True,
        "confidence": 0.0,
        "summary": "AI审核未启用，按量化规则继续。",
        "reasons": [reason],
        "vetoes": [],
        "raw_text": "",
    }


class AIReviewer:
    def __init__(self) -> None:
        self.cfg = load_config()

    def review(self, signal_id: int, signal: dict[str, Any], market_quote: dict[str, Any] | None = None, calibration: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.cfg.ai_review_enabled:
            review = default_review(signal)
            insert_ai_review(signal_id, review)
            return review
        if self.cfg.ai_provider.lower() != "minimax":
            review = {
                **default_review(signal, "unsupported_ai_provider"),
                "approve": False,
                "provider": self.cfg.ai_provider,
                "summary": f"不支持的 AI provider: {self.cfg.ai_provider}",
                "vetoes": ["unsupported_ai_provider"],
            }
            insert_ai_review(signal_id, review)
            return review
        review = self._review_with_minimax(signal, market_quote, calibration)
        insert_ai_review(signal_id, review)
        return review

    def _review_with_minimax(self, signal: dict[str, Any], market_quote: dict[str, Any] | None, calibration: dict[str, Any] | None) -> dict[str, Any]:
        if not self.cfg.minimax_api_key:
            return {
                **default_review(signal, "missing_minimax_api_key"),
                "provider": "minimax",
                "model": self.cfg.minimax_model,
                "approve": False,
                "summary": "MiniMax API Key 未配置，AI审核拒绝实盘。",
                "vetoes": ["missing_minimax_api_key"],
            }
        payload = {
            "model": self.cfg.minimax_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是一个天气预测市场风控审核器。只输出JSON，不要Markdown。"
                        "字段必须包含 approve(boolean), confidence(0-1), summary(string), "
                        "reasons(array), vetoes(array)。没有足够证据时 approve=false。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "review_weather_prediction_market_signal",
                            "signal": signal,
                            "market_quote": market_quote or {},
                            "calibration": calibration or {},
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0.1,
        }
        try:
            resp = requests.post(
                f"{self.cfg.minimax_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.cfg.minimax_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=(5, 20),
            )
            resp.raise_for_status()
            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            parsed = json.loads(text)
            return {
                "provider": "minimax",
                "model": self.cfg.minimax_model,
                "approve": bool(parsed.get("approve")),
                "confidence": _bounded_float(parsed.get("confidence"), 0.0),
                "summary": str(parsed.get("summary") or ""),
                "reasons": parsed.get("reasons") if isinstance(parsed.get("reasons"), list) else [],
                "vetoes": parsed.get("vetoes") if isinstance(parsed.get("vetoes"), list) else [],
                "raw_text": text,
            }
        except Exception as exc:
            return {
                "provider": "minimax",
                "model": self.cfg.minimax_model,
                "approve": False,
                "confidence": 0.0,
                "summary": f"AI审核失败：{exc}",
                "reasons": [],
                "vetoes": ["ai_error"],
                "raw_text": "",
            }


def _bounded_float(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return default

