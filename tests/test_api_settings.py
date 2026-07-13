from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from weatherbot_v3.api_settings import MASKED_VALUE, list_api_settings, test_api_setting, update_api_setting
from weatherbot_v3.env_utils import set_env_value


class _Response:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code
        self.content = b"{}"

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError(response=response)


class _Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.response

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.response


class ApiSettingsTests(unittest.TestCase):
    def test_dotenv_update_preserves_other_lines_and_clear_is_targeted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".env"
            path.write_text("# local settings\nOTHER_KEY=keep\nWEATHER_COM_API_KEY=old\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=False):
                set_env_value("WEATHER_COM_API_KEY", "new-secret", path=path)
                text = path.read_text(encoding="utf-8")
                self.assertIn("# local settings", text)
                self.assertIn("OTHER_KEY=keep", text)
                self.assertEqual(text.count("WEATHER_COM_API_KEY="), 1)
                self.assertIn("WEATHER_COM_API_KEY=new-secret", text)
                set_env_value("WEATHER_COM_API_KEY", "", path=path)
                self.assertNotIn("WEATHER_COM_API_KEY=", path.read_text(encoding="utf-8"))

    def test_settings_list_never_returns_secret(self):
        secret = "never-return-this-value"
        with patch.dict(os.environ, {"WEATHER_COM_API_KEY": secret}, clear=False):
            payload = list_api_settings()
        provider = next(item for item in payload["providers"] if item["key"] == "weather_com")
        self.assertTrue(provider["configured"])
        self.assertEqual(provider["masked_value"], MASKED_VALUE)
        self.assertNotIn(secret, str(payload))
        self.assertNotIn("env_name", str(payload))

    def test_weather_com_connection_test_uses_candidate_without_returning_it(self):
        secret = "candidate-secret"
        session = _Session(_Response({"validTimeUtc": ["2026-07-13T00:00:00Z"]}))
        result = test_api_setting("weather_com", secret, session=session)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "success")
        self.assertNotIn(secret, str(result))
        self.assertEqual(session.calls[0][2]["params"]["apiKey"], secret)

    def test_unauthorized_provider_is_reported_in_plain_language(self):
        session = _Session(_Response({}, status_code=401))
        result = test_api_setting("weather_com", "bad-key", session=session)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "unauthorized")
        self.assertIn("权限", result["message"])

    def test_feishu_requires_explicit_side_effect_confirmation(self):
        result = test_api_setting("feishu", "https://example.invalid/webhook")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "confirmation_required")

    def test_update_rejects_empty_value(self):
        with self.assertRaisesRegex(ValueError, "api_key_value_required"):
            update_api_setting("weather_com", "")


if __name__ == "__main__":
    unittest.main()
