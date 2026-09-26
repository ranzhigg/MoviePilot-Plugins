"""媒体代理资源级能力令牌回归测试"""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path


PLUGIN_DIR = Path(__file__).resolve().parents[1]


class _ConfigStub:
    """提供令牌模块所需的插件数据存储"""

    def __init__(self) -> None:
        self.data = {}

    def get_plugin_data(self, key: str):
        return self.data.get(key)

    def save_plugin_data(self, key: str, value):
        self.data[key] = value


def _load_module():
    """在隔离伪包中加载媒体代理令牌模块"""
    prefix = "p115_media_proxy_test"
    root = types.ModuleType(prefix)
    root.__path__ = [str(PLUGIN_DIR)]
    core = types.ModuleType(f"{prefix}.core")
    core.__path__ = [str(PLUGIN_DIR / "core")]
    config = types.ModuleType(f"{prefix}.core.config")
    config.configer = _ConfigStub()
    sys.modules[prefix] = root
    sys.modules[f"{prefix}.core"] = core
    sys.modules[f"{prefix}.core.config"] = config
    return importlib.import_module(f"{prefix}.core.media_proxy")


class MediaProxyTokenTest(unittest.TestCase):
    """验证令牌只允许访问签发时绑定的资源"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_module()

    def test_pickcode_token_is_bound_to_resource(self) -> None:
        pickcode = "a1b2c3d4e5f6g7h8i"
        token = self.module.issue_media_token(pickcode=pickcode)
        self.assertTrue(token)
        self.assertEqual(
            self.module.verify_media_token(token, pickcode=pickcode)["pickcode"],
            pickcode,
        )
        self.assertIsNone(
            self.module.verify_media_token(token, pickcode="z1b2c3d4e5f6g7h8i")
        )

    def test_share_token_requires_all_resource_fields(self) -> None:
        self.assertEqual(self.module.issue_media_token(), "")
        token = self.module.issue_media_token(
            share_code="share", receive_code="1234", file_id="99"
        )
        self.assertIsNotNone(
            self.module.verify_media_token(
                token,
                share_code="share",
                receive_code="1234",
                file_id="99",
            )
        )
        self.assertIsNone(
            self.module.verify_media_token(
                token,
                share_code="share",
                receive_code="1234",
                file_id="100",
            )
        )

    def test_tampered_token_is_rejected(self) -> None:
        token = self.module.issue_media_token(pickcode="a1b2c3d4e5f6g7h8i")
        body, signature = token.split(".", 1)
        replacement = "A" if signature[0] != "A" else "B"
        tampered = f"{body}.{replacement}{signature[1:]}"
        self.assertIsNone(
            self.module.verify_media_token(
                tampered, pickcode="a1b2c3d4e5f6g7h8i"
            )
        )


if __name__ == "__main__":
    unittest.main()
