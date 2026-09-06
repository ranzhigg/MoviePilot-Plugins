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


if __name__ == "__main__":
    unittest.main()
