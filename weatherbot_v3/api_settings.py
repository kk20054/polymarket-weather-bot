from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import requests

from .config import load_config
from .env_utils import dotenv_path, env_value, redact_secret_text, set_env_value
from .pws import discover_pws_station_ids
from .registry import SETTLEMENT_REGISTRY


MASKED_VALUE = "************"
_USER_AGENT = "WeatherBot/2.5 (local API settings test)"

API_PROVIDER_SPECS: dict[str, dict[str, Any]] = {
    "weather_com": {
        "env_name": "WEATHER_COM_API_KEY",
        "label": "Weather.com 预报",
        "description": "提供逐小时预报、云量，并作为最高温融合模型的主要输入。",
        "docs_url": "https://developer.weather.com/",
        "test_label": "测试预报接口",
    },
    "wunderground_pws": {
        "env_name": "WUNDERGROUND_API_KEY",
        "label": "Wunderground 个人气象站",
        "description": "读取机场附近个人站的实时温度趋势，辅助判断当天最高温拐点。",
        "docs_url": "https://developer.weather.com/docs/openapi/pws-observations-current-conditions-2-0",
        "test_label": "测试 PWS 权限",
    },
    "minimax": {
        "env_name": "MINIMAX_API_KEY",
        "label": "MiniMax AI 审核",
        "description": "可选。用于候选信号复核和日度摘要，不替代量化概率。",
        "docs_url": "https://platform.minimax.io/",
        "test_label": "测试 AI 接口",
    },
    "visual_crossing": {
        "env_name": "VISUAL_CROSSING_KEY",
        "label": "Visual Crossing 历史天气",
        "description": "可选历史站点数据补充；只作为回填来源，不替代市场结算依据。",
        "docs_url": "https://www.visualcrossing.com/weather-api/",
        "test_label": "测试历史天气接口",
    },
    "feishu": {
        "env_name": "FEISHU_WEBHOOK_URL",
        "label": "飞书通知",
        "description": "向你的飞书群发送信号、异常和日度结算摘要。测试会发送一条消息。",
        "docs_url": "https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot",
        "test_label": "发送测试消息",
        "test_has_side_effect": True,
    },
}


def list_api_settings() -> dict[str, Any]:
    try:
        updated_at = datetime.fromtimestamp(dotenv_path().stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        updated_at = None
    providers = []
    for key, spec in API_PROVIDER_SPECS.items():
        configured = bool(env_value(spec["env_name"]))
        providers.append({
            "key": key,
            "label": spec["label"],
            "description": spec["description"],
            "configured": configured,
            "masked_value": MASKED_VALUE if configured else "",
            "docs_url": spec["docs_url"],
            "test_label": spec["test_label"],
            "test_has_side_effect": bool(spec.get("test_has_side_effect")),
        })
    return {
        "ok": True,
        "storage": ".env（仅保存在本机）",
        "updated_at": updated_at,
        "providers": providers,
    }


def update_api_setting(provider_key: str, value: str = "", *, clear: bool = False) -> dict[str, Any]:
    spec = _provider_spec(provider_key)
    next_value = "" if clear else str(value or "").strip()
    if not clear and not next_value:
        raise ValueError("api_key_value_required")
    set_env_value(spec["env_name"], next_value)
    return next(item for item in list_api_settings()["providers"] if item["key"] == provider_key)


def test_api_setting(
    provider_key: str,
    value: str = "",
    *,
    allow_side_effect: bool = False,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    spec = _provider_spec(provider_key)
    secret = str(value or "").strip() or env_value(spec["env_name"])
    if not secret:
        return _test_result(provider_key, False, "missing", "请先填写并保存密钥。", 0)
    if spec.get("test_has_side_effect") and not allow_side_effect:
        return _test_result(provider_key, False, "confirmation_required", "测试飞书会发送一条真实消息，请确认后再试。", 0)

    client = session or requests.Session()
    started = time.perf_counter()
    try:
        if provider_key == "weather_com":
            response = client.get(
                "https://api.weather.com/v3/wx/forecast/hourly/15day",
                params={"geocode": "31.1443,121.8083", "format": "json", "units": "m", "language": "zh-CN", "apiKey": secret},
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
                timeout=(5, 20),
            )
            _raise_for_provider(response)
            payload = response.json()
            if not isinstance(payload, dict) or not payload.get("validTimeUtc"):
                raise ValueError("weather_com_response_missing_hourly_data")
            message = "连接成功，已读取 Weather.com 逐小时预报。"
        elif provider_key == "wunderground_pws":
            stations, _ = discover_pws_station_ids(
                SETTLEMENT_REGISTRY["shanghai"], api_key=secret, station_limit=1, session=client,
            )
            if not stations:
                raise ValueError("pws_connection_ok_but_no_station_found")
            message = f"连接成功，PWS 权限可用并找到附近站点（{stations[0]}）。"
        elif provider_key == "minimax":
            cfg = load_config()
            response = client.post(
                f"{cfg.minimax_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
                json={
                    "model": cfg.minimax_model,
                    "messages": [{"role": "user", "content": "Reply OK"}],
                    "temperature": 0,
                    "max_tokens": 1,
                },
                timeout=(5, 20),
            )
            _raise_for_provider(response)
            payload = response.json()
            if not isinstance(payload, dict) or not payload.get("choices"):
                raise ValueError("minimax_response_missing_choices")
            message = "连接成功，MiniMax 对话接口可用。"
        elif provider_key == "visual_crossing":
            response = client.get(
                "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/KORD/today/today",
                params={"unitGroup": "metric", "key": secret, "include": "days", "elements": "tempmax"},
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
                timeout=(5, 20),
            )
            _raise_for_provider(response)
            payload = response.json()
            days = payload.get("days") if isinstance(payload, dict) else None
            if (
                not isinstance(days, list)
                or not days
                or not isinstance(days[0], dict)
                or days[0].get("tempmax") is None
            ):
                raise ValueError("visual_crossing_response_missing_tempmax")
            message = "连接成功，已读取 Visual Crossing 历史天气接口。"
        elif provider_key == "feishu":
            response = client.post(
                secret,
                json={"msg_type": "text", "content": {"text": "WeatherBot API 配置测试成功。"}},
                headers={"User-Agent": _USER_AGENT},
                timeout=(5, 15),
            )
            _raise_for_provider(response)
            payload = response.json() if response.content else {}
            if isinstance(payload, dict) and payload.get("code") not in (None, 0):
                raise ValueError(f"feishu_error_{payload.get('code')}")
            message = "测试消息已发送，请在飞书群中确认。"
        else:
            raise ValueError("unsupported_api_provider")
        return _test_result(provider_key, True, "success", message, _duration_ms(started))
    except requests.HTTPError as exc:
        status_code = int(exc.response.status_code if exc.response is not None else 0)
        if status_code in {401, 403}:
            message = "密钥无效，或当前账号没有开通这个接口的权限。"
            status = "unauthorized"
        elif status_code == 429:
            message = "接口配额已用完或请求过于频繁，请稍后再试。"
            status = "rate_limited"
        else:
            message = f"服务返回 HTTP {status_code or '错误'}，请稍后重试。"
            status = "failed"
        return _test_result(provider_key, False, status, message, _duration_ms(started))
    except Exception as exc:
        reason = redact_secret_text(str(exc)).replace(secret, "***")
        friendly = {
            "pws_connection_ok_but_no_station_found": "密钥已通过请求，但没有找到可用的附近个人站。",
            "weather_com_response_missing_hourly_data": "接口已响应，但返回内容不含逐小时预报。",
            "visual_crossing_response_missing_tempmax": "接口已响应，但返回内容不含日最高温 tempmax。",
        }.get(reason, "连接失败，请检查密钥、网络或服务权限。")
        return _test_result(provider_key, False, "failed", friendly, _duration_ms(started), reason=reason)


def _provider_spec(provider_key: str) -> dict[str, Any]:
    try:
        return API_PROVIDER_SPECS[str(provider_key or "").strip()]
    except KeyError as exc:
        raise ValueError("unsupported_api_provider") from exc


def _raise_for_provider(response: requests.Response) -> None:
    response.raise_for_status()


def _duration_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _test_result(provider_key: str, ok: bool, status: str, message: str, duration_ms: int, *, reason: str = "") -> dict[str, Any]:
    return {
        "provider_key": provider_key,
        "ok": ok,
        "status": status,
        "message": message,
        "duration_ms": duration_ms,
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
    }
