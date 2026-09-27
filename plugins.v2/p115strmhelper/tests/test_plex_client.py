"""Plex API 短暂连接故障重试回归测试。"""

from __future__ import annotations

import importlib.util
import logging
import sys
import types
import unittest
from pathlib import Path


PLUGIN_DIR = Path(__file__).resolve().parents[1]


class _Response:
    status_code = 200

    @staticmethod
    def json():
        return {"ok": True}


class _FlakyClient:
    attempts = 0
    created = 0

    def __init__(self, **kwargs):
        del kwargs
        type(self).created += 1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def close(self):
        return None

    def get(self, url, headers=None):
        del url, headers
        type(self).attempts += 1
        if type(self).attempts < 3:
            raise OSError("transient Plex disconnect")
        return _Response()


def _load_module():
    """在最小伪环境中加载 PlexClient，避免引入完整 MP 运行时。"""
    names = ("app", "app.sdk", "app.sdk.logging", "httpx")
    previous = {name: sys.modules.get(name) for name in names}
    app = types.ModuleType("app")
    app.__path__ = []
    sdk = types.ModuleType("app.sdk")
    sdk.__path__ = []
    logging_module = types.ModuleType("app.sdk.logging")
    logging_module.logger = logging.getLogger("p115-plex-client-test")
    httpx_module = types.ModuleType("httpx")
    httpx_module.Client = _FlakyClient
    sys.modules.update(
        {
            "app": app,
            "app.sdk": sdk,
            "app.sdk.logging": logging_module,
            "httpx": httpx_module,
        }
    )
    try:
        path = PLUGIN_DIR / "helper" / "plex_app" / "plex_client.py"
        spec = importlib.util.spec_from_file_location("p115_plex_client_test", path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class PlexClientRetryTest(unittest.TestCase):
    def test_get_retries_transient_disconnect(self):
        _FlakyClient.attempts = 0
        _FlakyClient.created = 0
        module = _load_module()
        module.PlexClient._GET_RETRY_DELAYS = (0, 0)
        client = module.PlexClient("http://plex.example", "token")

        self.assertEqual(client._get("/identity"), {"ok": True})
        self.assertEqual(_FlakyClient.attempts, 3)
        self.assertEqual(_FlakyClient.created, 1)


if __name__ == "__main__":
    unittest.main()
