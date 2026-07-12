from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


_DOTENV_CACHE: dict[str, str] | None = None
_SECRET_ENV_NAMES = (
    "WEATHER_COM_API_KEY",
    "WUNDERGROUND_API_KEY",
    "MINIMAX_API_KEY",
    "VISUAL_CROSSING_KEY",
    "POLY_PRIVATE_KEY",
    "FEISHU_WEBHOOK_URL",
    "DEVELOPER_OPERATOR_TOKEN",
)


def env_value(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value:
        return str(value).strip()
    return _dotenv_values().get(name, default).strip()


def _dotenv_values() -> dict[str, str]:
    global _DOTENV_CACHE
    if _DOTENV_CACHE is not None:
        return _DOTENV_CACHE
    path = Path(__file__).resolve().parents[1] / ".env"
    values: dict[str, str] = {}
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    _DOTENV_CACHE = values
    return values


def redact_secret_text(value: Any) -> str:
    text = str(value or "")
    for name in _SECRET_ENV_NAMES:
        secret = env_value(name)
        if secret:
            text = text.replace(secret, "***")
    text = re.sub(r"(?i)(apiKey=)[^&\s\"']+", r"\1***", text)
    text = re.sub(r"(?i)(authorization:\s*bearer\s+)[^\s\"']+", r"\1***", text)
    return text


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: redact_secrets(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    if isinstance(value, str):
        return redact_secret_text(value)
    return value
