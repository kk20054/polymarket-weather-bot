from __future__ import annotations

import os
from pathlib import Path


_DOTENV_CACHE: dict[str, str] | None = None


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
