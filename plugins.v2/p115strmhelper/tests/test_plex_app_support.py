"""Plex App 播放即时探测与 Webhook 队列回归测试。"""

from __future__ import annotations

import importlib
import json
import logging
import sys
import types
import unittest
from pathlib import Path


PLUGIN_DIR = Path(__file__).resolve().parents[1]


class _ConfigStub:
    """提供 PlexAppSupport 测试所需的最小配置存储。"""

    enabled = True

    def __init__(self) -> None:
        self.values = {
            "plex_app_enabled": True,
            "plex_app_plex_url": "http://plex.example",
            "plex_app_plex_token": "token",
            "plex_app_helper_url": "http://helper.example",
            "plex_app_sections": "1",
            "plex_app_webhook_enabled": True,
            "plex_app_play_probe_enabled": True,
            "plex_app_play_forward_episodes": 0,
            "plex_app_only_missing": True,
            "plex_app_dedup_window": 300,
            "plex_app_marker_detection_enabled": False,
        }
        self.saved = {}

    def get_config(self, key: str):
        return self.values.get(key)

    def save_plugin_data(self, key: str, value):
        self.saved[key] = value

    def get_plugin_data(self, key: str):
        return self.saved.get(key)


class _PlexStub:
    """提供条目归属和标签查询。"""

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    def item_section_key(self, rating_key: str) -> str:
        return "1"

    def item_label(self, rating_key: str) -> str:
        return f"label:{rating_key}"


class _CompleterStub:
    """记录补全调用，模拟当前条目写入成功。"""

    calls = []

    def __init__(self, plex, **kwargs) -> None:
        self._plex = plex

    def run_rating_key(self, rating_key: str, only_missing: bool = True, forward: int = 0):
        self.__class__.calls.append((rating_key, forward))
        return {
            "rating_key": rating_key,
            "label": f"label:{rating_key}",
            "strm_parts": 1,
            "resolved": 1,
            "written_ok": 1,
            "unresolved": 0,
            "write_failed": 0,
        }


def _load_support_module():
    """在隔离的伪包中加载 support，避免导入完整 MoviePilot 运行时。"""
    prefix = "p115_support_test"
    app = types.ModuleType("app")
    app.__path__ = []
    app_log = types.ModuleType("app.log")
    app_log.logger = logging.getLogger("p115-plex-support-test")
    sys.modules["app"] = app
    sys.modules["app.log"] = app_log

    root = types.ModuleType(prefix)
    root.__path__ = [str(PLUGIN_DIR)]
    helper = types.ModuleType(f"{prefix}.helper")
    helper.__path__ = [str(PLUGIN_DIR / "helper")]
    plex_app = types.ModuleType(f"{prefix}.helper.plex_app")
    plex_app.__path__ = [str(PLUGIN_DIR / "helper" / "plex_app")]
    core = types.ModuleType(f"{prefix}.core")
    core.__path__ = [str(PLUGIN_DIR / "core")]
    sys.modules[prefix] = root
    sys.modules[f"{prefix}.helper"] = helper
    sys.modules[f"{prefix}.helper.plex_app"] = plex_app
    sys.modules[f"{prefix}.core"] = core

    config_module = types.ModuleType(f"{prefix}.core.config")
    config_module.configer = _ConfigStub()
    sys.modules[f"{prefix}.core.config"] = config_module

    ffprobe_module = types.ModuleType(f"{prefix}.helper.plex_app.ffprobe_source")
    ffprobe_module.FfprobeSource = type("FfprobeSourceStub", (), {"__init__": lambda self, **kwargs: None})
    sys.modules[f"{prefix}.helper.plex_app.ffprobe_source"] = ffprobe_module
    helper_module = types.ModuleType(f"{prefix}.helper.plex_app.helper_client")
    helper_module.HelperClient = type("HelperClientStub", (), {"__init__": lambda self, *args: None})
    sys.modules[f"{prefix}.helper.plex_app.helper_client"] = helper_module
    mediainfo_module = types.ModuleType(f"{prefix}.helper.plex_app.mediainfo")
    mediainfo_module.MediaInfoCompleter = _CompleterStub
    sys.modules[f"{prefix}.helper.plex_app.mediainfo"] = mediainfo_module
    plex_module = types.ModuleType(f"{prefix}.helper.plex_app.plex_client")
    plex_module.PlexClient = _PlexStub
    sys.modules[f"{prefix}.helper.plex_app.plex_client"] = plex_module

    return importlib.import_module(f"{prefix}.helper.plex_app.support"), config_module.configer


class PlexAppSupportTest(unittest.TestCase):
    """验证播放事件的即时探测和停止事件的独立预取范围。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.module, cls.configer = _load_support_module()

    def setUp(self) -> None:
        _CompleterStub.calls.clear()
        self.support = self.module.PlexAppSupport()
        self.inline_thread = self.module.Thread

        class InlineThread:
            """让测试同步执行后台函数。"""

            def __init__(self, target, **kwargs) -> None:
                self.target = target

            def start(self) -> None:
                self.target()

        self.module.Thread = InlineThread

    def tearDown(self) -> None:
        self.module.Thread = self.inline_thread

    def test_play_event_probes_current_item_without_waiting_for_stop(self) -> None:
        """播放事件应立即排队当前条目，并使用零后续预取。"""
        result = self.support.webhook_payload(
            json.dumps(
                {
                    "event": "media.play",
                    "Metadata": {"ratingKey": "episode-7", "type": "episode"},
                }
            )
        )
        self.assertTrue(result["metadata_queued"])
        self.assertFalse(result["marker_queued"])
        self.assertEqual(_CompleterStub.calls, [("episode-7", 0)])

    def test_stop_event_has_independent_prefetch_scope(self) -> None:
        """停止事件不能被播放去重吞掉，仍可补齐配置的后续剧集。"""
        payload = {"Metadata": {"ratingKey": "episode-8", "type": "episode"}}
        self.support.webhook_payload(json.dumps({"event": "media.play", **payload}))
        result = self.support.webhook_payload(json.dumps({"event": "media.stop", **payload}))
        self.assertTrue(result["queued"])
        self.assertEqual(_CompleterStub.calls, [("episode-8", 0), ("episode-8", 5)])


if __name__ == "__main__":
    unittest.main()
