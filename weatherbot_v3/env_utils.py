from __future__ import annotations

import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any


_DOTENV_CACHE: dict[str, str] | None = None
_DOTENV_LOCK = threading.RLock()
_SECRET_ENV_NAMES = (
    "WEATHER_COM_API_KEY",
    "WUNDERGROUND_API_KEY",
    "MINIMAX_API_KEY",
    "VISUAL_CROSSING_KEY",
    "POLY_PRIVATE_KEY",
    "FEISHU_WEBHOOK_URL",
    "DEVELOPER_OPERATOR_TOKEN",
    "WEATHERBOT_ORIGIN_TOKEN",
)


def env_value(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value:
        return str(value).strip()
    return _dotenv_values().get(name, default).strip()


def dotenv_path() -> Path:
    return Path(__file__).resolve().parents[1] / ".env"


def set_env_value(name: str, value: str, *, path: Path | None = None) -> None:
    """Persist one allow-listed local setting without exposing other secrets."""
    global _DOTENV_CACHE
    key = str(name or "").strip()
    secret = str(value or "").strip()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
        raise ValueError("invalid_env_name")
    if "\n" in secret or "\r" in secret or len(secret) > 4096:
        raise ValueError("invalid_env_value")

    target = Path(path) if path is not None else dotenv_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with _DOTENV_LOCK:
        try:
            lines = target.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            lines = []
        assignment = re.compile(rf"^\s*{re.escape(key)}\s*=")
        replacement = f"{key}={secret}"
        output: list[str] = []
        replaced = False
        for line in lines:
            if assignment.match(line):
                if not replaced and secret:
                    output.append(replacement)
                    replaced = True
                continue
            output.append(line)
        if secret and not replaced:
            if output and output[-1].strip():
                output.append("")
            output.append(replacement)

        content = "\n".join(output)
        if output:
            content += "\n"
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            delete=False,
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        try:
            with handle:
                handle.write(content)
            os.replace(handle.name, target)
        finally:
            try:
                Path(handle.name).unlink(missing_ok=True)
            except OSError:
                pass
        if secret:
            os.environ[key] = secret
        else:
            os.environ.pop(key, None)
        _DOTENV_CACHE = None


def _dotenv_values() -> dict[str, str]:
    global _DOTENV_CACHE
    if _DOTENV_CACHE is not None:
        return _DOTENV_CACHE
    path = dotenv_path()
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
