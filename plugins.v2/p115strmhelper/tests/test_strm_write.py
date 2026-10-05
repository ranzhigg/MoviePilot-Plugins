"""STRM 写入顺序与文件时间的回归测试。"""

import ast
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest import TestCase


def _load_strm_write_helpers():
    source_path = Path(__file__).resolve().parents[1] / "utils/strm.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"get_source_mtime", "write_strm_file"}
    ]
    namespace = {
        "Any": Any,
        "Dict": Dict,
        "Optional": Optional,
        "Path": Path,
        "os": os,
        "stat": __import__("stat"),
        "tempfile": __import__("tempfile"),
        "time": __import__("time").time,
        "logger": SimpleNamespace(debug=lambda *args, **kwargs: None),
    }
    exec(
        compile(ast.Module(body=functions, type_ignores=[]), str(source_path), "exec"),
        namespace,
    )
    return namespace["get_source_mtime"], namespace["write_strm_file"]


class StrmWriteTest(TestCase):
    def test_source_time_prefers_creation_and_accepts_milliseconds(self) -> None:
        get_source_mtime, _ = _load_strm_write_helpers()
        self.assertEqual(
            get_source_mtime({"ctime": 1700000000, "mtime": 1800000000}),
            1700000000,
        )
        self.assertEqual(
            get_source_mtime({"ctime": 0, "mtime": 1700000000000}),
            1700000000,
        )
        self.assertEqual(
            get_source_mtime(SimpleNamespace(modify_time=1700000001)),
            1700000001,
        )

    def test_new_file_uses_source_time_and_repeated_write_is_noop(self) -> None:
        _, write_strm_file = _load_strm_write_helpers()
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "movie.strm"
            source_time = 1700000000

            self.assertTrue(write_strm_file(path, "https://example/1", source_time))
            self.assertEqual(path.read_text(encoding="utf-8"), "https://example/1")
            self.assertAlmostEqual(path.stat().st_mtime, source_time, delta=0.01)
            self.assertEqual(
                [entry.name for entry in root.iterdir()],
                ["movie.strm"],
            )

            os.utime(path, (source_time, source_time))
            before = path.stat().st_mtime_ns
            self.assertFalse(write_strm_file(path, "https://example/1", source_time))
            self.assertEqual(path.stat().st_mtime_ns, before)

    def test_legacy_time_is_normalized_and_changed_content_uses_source_time(self) -> None:
        _, write_strm_file = _load_strm_write_helpers()
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "movie.strm"
            write_strm_file(path, "https://example/old", 1700000000)
            old_time = 1700000012
            os.utime(path, (old_time, old_time))

            self.assertTrue(write_strm_file(path, "https://example/old", 1700000000))
            self.assertAlmostEqual(path.stat().st_mtime, 1700000000, delta=0.01)

            self.assertTrue(write_strm_file(path, "https://example/new", 1900000000))
            self.assertEqual(path.read_text(encoding="utf-8"), "https://example/new")
            self.assertAlmostEqual(path.stat().st_mtime, 1900000000, delta=0.01)
