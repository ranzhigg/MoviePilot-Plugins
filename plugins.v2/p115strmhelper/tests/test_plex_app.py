"""Plex App 媒体信息补全的纯 Python 回归测试。"""

from __future__ import annotations

import importlib
import logging
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


PLEX_APP_DIR = Path(__file__).resolve().parents[1] / "helper" / "plex_app"
if str(PLEX_APP_DIR) not in sys.path:
    sys.path.insert(0, str(PLEX_APP_DIR))

from ffprobe_source import (  # noqa: E402
    FfprobeSource,
    _normalize_ffprobe,
    map_path,
    parse_path_map,
    read_strm_url,
)
import ffprobe_source as ffprobe_module  # noqa: E402


class FfprobeSourceTest(unittest.TestCase):
    def test_path_mapping_prefers_longest_prefix(self) -> None:
        mappings = parse_path_map("/Volumes/data=/media\n/Volumes/data/mp=/special")
        self.assertEqual(
            map_path("/Volumes/data/mp/movie.strm", mappings),
            "/special/movie.strm",
        )

    def test_read_strm_url_skips_comments_and_blank_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "movie.strm"
            path.write_text("# generated\n\nhttps://example.invalid/movie.mkv\n", encoding="utf-8")
            self.assertEqual(read_strm_url(str(path)), "https://example.invalid/movie.mkv")

    def test_normalize_ffprobe_output(self) -> None:
        result = _normalize_ffprobe(
            {
                "format": {"format_name": "matroska,webm", "duration": "1420.125"},
                "streams": [
                    {
                        "index": 0,
                        "codec_type": "video",
                        "codec_name": "hevc",
                        "width": 1920,
                        "height": 1080,
                        "avg_frame_rate": "24000/1001",
                        "pix_fmt": "yuv420p10le",
                    },
                    {
                        "index": 1,
                        "codec_type": "audio",
                        "codec_name": "eac3",
                        "channels": 6,
                        "sample_rate": "48000",
                    },
                    {"index": 2, "codec_type": "subtitle", "codec_name": "subrip"},
                ],
            }
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["container"], "mkv")
        self.assertEqual(result["duration"], 1420125)
        self.assertEqual(result["video_codec"], "hevc")
        self.assertEqual(result["audio_codec"], "eac3")
        self.assertEqual(result["streams"][0]["bit_depth"], 10)
        self.assertEqual(result["streams"][0]["frame_rate"], 23.976)
        self.assertEqual(result["streams"][2]["codec"], "srt")

    def test_media_gateway_is_probed_before_direct_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "movie.strm"
            source = (
                "https://mp.example/api/v1/plugin/P115StrmHelper/redirect_url"
                "?pickcode=a1b2c3d4e5f6g7h8i"
            )
            path.write_text(source + "\n", encoding="utf-8")
            calls = []

            def fake_gateway(url: str) -> str:
                return url.replace("/redirect_url", "/media_proxy") + "&media_token=test"

            def fake_probe(url: str, timeout: float = 40.0):
                calls.append(url)
                return {"source": "ffprobe", "streams": [{"stream_type": 1}]}

            with patch.object(
                ffprobe_module, "build_media_proxy_url", side_effect=fake_gateway
            ), patch.object(ffprobe_module, "ffprobe_url", side_effect=fake_probe):
                result = FfprobeSource(cache_ttl=0).find_streams_by_name(str(path))

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result["probe_route"], "media_proxy")
            self.assertIn("/media_proxy", calls[0])
            self.assertEqual(parse_qs(urlsplit(calls[0]).query)["probe"], ["1"])

    def test_ffprobe_uses_bounded_network_options(self) -> None:
        completed = types.SimpleNamespace(
            returncode=0,
            stdout=(
                '{"format":{"format_name":"matroska"},'
                '"streams":[{"codec_type":"video","codec_name":"h264"}]}'
            ),
        )
        with patch.object(ffprobe_module.subprocess, "run", return_value=completed) as run:
            result = ffprobe_module.ffprobe_url(
                "https://example.invalid/movie.mkv", timeout=5
            )

        self.assertIsNotNone(result)
        command = run.call_args.args[0]
        self.assertNotIn("-nostdin", command)
        self.assertEqual(command[command.index("-rw_timeout") + 1], "5000000")


class MediaInfoCompleterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        app = types.ModuleType("app")
        app.__path__ = []
        sdk = types.ModuleType("app.sdk")
        sdk.__path__ = []
        logging_module = types.ModuleType("app.sdk.logging")
        logging_module.logger = logging.getLogger("p115-plex-app-test")
        sys.modules.setdefault("app", app)
        sys.modules.setdefault("app.sdk", sdk)
        sys.modules.setdefault("app.sdk.logging", logging_module)
        if "httpx" not in sys.modules:
            httpx = types.ModuleType("httpx")
            httpx.Client = type("ClientStub", (), {})
            sys.modules["httpx"] = httpx
        package = types.ModuleType("p115_plex_app")
        package.__path__ = [str(PLEX_APP_DIR)]
        sys.modules.setdefault("p115_plex_app", package)
        cls.module = importlib.import_module("p115_plex_app.mediainfo")
        cls.plex_module = importlib.import_module("p115_plex_app.plex_client")

    def test_native_marker_requests_are_scoped_to_one_rating_key(self) -> None:
        """原生标记接口必须只接收当前条目，并分别请求 intro/credits。"""
        client = self.plex_module.PlexClient("http://plex.example", "token")
        calls = []

        def fake_put(path, params=None):
            calls.append((path, params))
            return True

        client._put = fake_put
        self.assertTrue(client.detect_intro("episode/7", force=True))
        self.assertTrue(client.detect_credits("episode/7", force=False))
        self.assertEqual(
            calls,
            [
                ("/library/metadata/episode%2F7/intro", {"force": 1}),
                (
                    "/library/metadata/episode%2F7/credits",
                    {"force": 0, "manual": 1},
                ),
            ],
        )

    def test_resource_locator_matches_share_strm_url(self) -> None:
        """分享 STRM 应按分享码、提取码和文件 ID 定位 Part。"""
        client = self.plex_module.PlexClient("http://plex.example", "token")
        client.list_sections = lambda: [{"key": "1"}]
        client.collect_strm_parts = lambda section_key, only_missing=False: [
            {
                "part_id": 99,
                "file": (
                    "https://mp.example/api/v1/plugin/P115StrmHelper/"
                    "media_proxy?share_code=share123&receive_code=1234&id=99"
                ),
            }
        ]
        part = client.find_strm_part_by_resource(
            share_code="share123", receive_code="1234", file_id="99"
        )
        self.assertEqual(part["part_id"], 99)

    def test_ffprobe_fallback_writes_payload(self) -> None:
        module = self.module

        class PlexStub:
            def item_label(self, rating_key: str) -> str:
                return "测试电影"

            def collect_window_parts_by_rating_key(self, rating_key: str, **kwargs):
                return [{"part_id": 12, "file": "/media/test.strm", "label": "测试电影"}]

        class HelperStub:
            def write_batch(self, items, force=False):
                return {"ok": len(items), "results": [{"part_id": 12, "success": True}]}

        class ProbeStub:
            def find_streams_by_name(self, file_path: str):
                return {"source": "ffprobe", "streams": [{"stream_type": 1, "codec": "hevc"}]}

        completer = module.MediaInfoCompleter(
            plex=PlexStub(),
            helper=HelperStub(),
            emby=None,
            use_emby=False,
            ffprobe=ProbeStub(),
            use_ffprobe=True,
        )
        summary = completer.run_rating_key("138375")
        self.assertEqual(summary["resolved"], 1)
        self.assertEqual(summary["ffprobe_hits"], 1)
        self.assertEqual(summary["written_ok"], 1)

    def test_media_info_is_written_in_small_batches(self) -> None:
        """全量/窗口补全不能因单次 Helper 忙碌判断而整批失败。"""
        module = self.module

        class PlexStub:
            def item_label(self, rating_key: str) -> str:
                return "测试剧集"

            def collect_window_parts_by_rating_key(self, rating_key: str, **kwargs):
                return [
                    {"part_id": 1, "file": "/media/1.strm", "label": "一"},
                    {"part_id": 2, "file": "/media/2.strm", "label": "二"},
                    {"part_id": 3, "file": "/media/3.strm", "label": "三"},
                ]

        class HelperStub:
            calls = []

            def write_batch(self, items, force=False):
                self.calls.append([item["part_id"] for item in items])
                return {
                    "ok": len(items),
                    "results": [
                        {"part_id": item["part_id"], "success": True}
                        for item in items
                    ],
                }

        class ProbeStub:
            def find_streams_by_name(self, file_path: str):
                return {"source": "ffprobe", "streams": [{"stream_type": 1}]}

        helper = HelperStub()
        completer = module.MediaInfoCompleter(
            plex=PlexStub(),
            helper=helper,
            emby=None,
            use_emby=False,
            ffprobe=ProbeStub(),
            use_ffprobe=True,
            write_batch_size=2,
        )
        summary = completer.run_rating_key("episode-1")
        self.assertEqual(helper.calls, [[1, 2], [3]])
        self.assertEqual(summary["resolved"], 3)
        self.assertEqual(summary["written_ok"], 3)
        self.assertEqual(summary["write_failed"], 0)

    def test_full_scan_reports_library_total_and_remaining_count(self) -> None:
        """全库扫描应区分总量、扫描前完整量和本次实际待补量。"""
        module = self.module

        class PlexStub:
            def collect_strm_parts(self, section_key: str, only_missing: bool = True):
                del section_key
                parts = [
                    {"part_id": 1, "file": "/media/1.strm"},
                    {"part_id": 2, "file": "/media/2.strm"},
                    {"part_id": 3, "file": "/media/3.strm"},
                ]
                return parts[:2] if only_missing else parts

        class HelperStub:
            def part_status(self, part_ids, batch_size=500):
                del batch_size
                return {
                    str(part_id): {
                        "duration": 0 if part_id < 3 else 120,
                        "streams": 0 if part_id < 3 else 2,
                    }
                    for part_id in part_ids
                }

            def write_batch(self, items, force=False):
                del force
                return {
                    "ok": len(items),
                    "results": [
                        {"part_id": item["part_id"], "success": True}
                        for item in items
                    ],
                }

        class ProbeStub:
            def find_streams_by_name(self, file_path: str):
                del file_path
                return {"source": "ffprobe", "streams": [{"stream_type": 1}]}

        completer = module.MediaInfoCompleter(
            plex=PlexStub(),
            helper=HelperStub(),
            emby=None,
            use_emby=False,
            ffprobe=ProbeStub(),
            use_ffprobe=True,
        )
        summary = completer.run(["1"], full_scan=True)
        self.assertEqual(summary["total_strm_parts"], 3)
        self.assertEqual(summary["missing_before"], 2)
        self.assertEqual(summary["completed_before"], 1)
        self.assertEqual(summary["status_source"], "helper_db")
        self.assertEqual(summary["written_ok"], 2)
        self.assertEqual(summary["pending_after"], 0)
        self.assertEqual(summary["completed_after"], 3)

if __name__ == "__main__":
    unittest.main()
