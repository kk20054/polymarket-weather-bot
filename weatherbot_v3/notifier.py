from __future__ import annotations

from typing import Any

import requests

from .config import load_config
from .db import log_notification


class FeishuNotifier:
    def __init__(self) -> None:
        self.cfg = load_config()

    def send(self, event_type: str, title: str, lines: list[str], payload: dict[str, Any] | None = None) -> bool:
        message = title + "\n" + "\n".join(lines)
        if not self.cfg.feishu_webhook_url:
            log_notification("feishu", event_type, "skipped", message, payload)
            return False
        try:
            resp = requests.post(
                self.cfg.feishu_webhook_url,
                json={"msg_type": "text", "content": {"text": message}},
                timeout=(5, 10),
            )
            resp.raise_for_status()
            log_notification("feishu", event_type, "sent", message, payload)
            return True
        except Exception as exc:
            log_notification("feishu", event_type, "error", f"{message}\nERROR: {exc}", payload)
            return False

    def daily_summary(self, summary: dict[str, Any]) -> bool:
        lines = [
            f"信号数: {summary.get('signals', 0)}",
            f"模拟订单: {summary.get('paper_orders', 0)}",
            f"实盘订单: {summary.get('live_orders', 0)}",
            f"风险事件: {summary.get('risk_events', 0)}",
        ]
        return self.send("daily_summary", "WeatherBot 日度摘要", lines, summary)

