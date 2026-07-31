from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException, Request

import dashboard_server


def _request(*, host: str, client_host: str = "127.0.0.1") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/developer/api-settings/weathercom",
        "headers": [(b"host", host.encode("ascii"))],
        "client": (client_host, 12345),
        "server": ("127.0.0.1", 8765),
        "scheme": "http",
        "query_string": b"",
    }
    return Request(scope)


class RemoteOriginAuthTests(unittest.TestCase):
    def test_local_hosts_do_not_need_origin_token(self) -> None:
        for host in ("127.0.0.1:8765", "localhost:8765", "[::1]:8765", "testserver"):
            self.assertEqual(dashboard_server._origin_request_allowed(host, None), (True, "local"))

    def test_remote_host_requires_matching_origin_token(self) -> None:
        with patch("dashboard_server.env_value", return_value="expected-secret"):
            self.assertEqual(
                dashboard_server._origin_request_allowed("origin.example.com", None),
                (False, "invalid_origin_token"),
            )
            self.assertEqual(
                dashboard_server._origin_request_allowed("origin.example.com", "wrong"),
                (False, "invalid_origin_token"),
            )
            self.assertEqual(
                dashboard_server._origin_request_allowed("origin.example.com", "expected-secret"),
                (True, "remote_token"),
            )

    def test_remote_developer_write_is_not_treated_as_loopback(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            dashboard_server._require_local_developer_request(
                _request(host="origin.example.com"),
                confirmed=True,
            )
        self.assertEqual(raised.exception.status_code, 403)

    def test_healthz_is_database_independent(self) -> None:
        payload = asyncio.run(dashboard_server.healthz())
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["service"], "weatherbot-dashboard")
        self.assertEqual(payload["live_trading"], False)


if __name__ == "__main__":
    unittest.main()
