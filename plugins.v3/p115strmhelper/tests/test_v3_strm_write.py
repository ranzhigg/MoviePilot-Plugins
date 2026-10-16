"""V3 STRM 文件时间与目录顺序回归测试。"""

import ast
import os
import stat
import tempfile
from pathlib import Path
from time import time
from types import SimpleNamespace
from typing import Any, Optional
from unittest import TestCase


def _load_helpers():
    source_path = Path(__file__).resolve().parents[1] / "utils/strm.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    names = {"get_source_mtime", "set_path_mtime", "write_strm_file"}
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {
        "Any": Any,
        "Optional": Optional,
        "Path": Path,
        "os": os,
        "stat": stat,
        "tempfile": tempfile,
        "time": time,
        "logger": SimpleNamespace(debug=lambda *args, **kwargs: None),
    }
    exec(
        compile(ast.Module(body=functions, type_ignores=[]), str(source_path), "exec"),
        namespace,
    )
    return (
        namespace["get_source_mtime"],
        namespace["set_path_mtime"],
        namespace["write_strm_file"],
    )


class V3StrmWriteTest(TestCase):
    def test_directory_order_time_overrides_file_time(self) -> None:
        get_source_mtime, _, _ = _load_helpers()
        self.assertEqual(
            get_source_mtime(
                {"_p115_order_mtime": 1700000000, "ctime": 1900000000}
            ),
            1700000000,
        )
        self.assertEqual(
            get_source_mtime(
                {
                    "extra": '{"_p115_order_mtime": 1700000001}',
                    "ctime": 1900000000,
                }
            ),
            1700000001,
        )
        self.assertEqual(
            get_source_mtime(
                {
                    "user_utime": 1700000002,
                    "user_ptime": 1900000001,
                    "ctime": 1900000003,
                }
            ),
            1700000002,
        )

    def test_existing_strm_and_parent_directory_are_normalized(self) -> None:
        _, set_path_mtime, write_strm_file = _load_helpers()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "movie"
            path = root / "movie.strm"
            source_time = 1700000000

            self.assertTrue(write_strm_file(path, "https://example/1", source_time))
            self.assertTrue(set_path_mtime(root, source_time))
            self.assertAlmostEqual(path.stat().st_mtime, source_time, delta=0.01)
            self.assertAlmostEqual(root.stat().st_mtime, source_time, delta=0.01)

            newer_time = 1700000100
            self.assertTrue(write_strm_file(path, "https://example/1", newer_time))
            self.assertAlmostEqual(path.stat().st_mtime, newer_time, delta=0.01)
