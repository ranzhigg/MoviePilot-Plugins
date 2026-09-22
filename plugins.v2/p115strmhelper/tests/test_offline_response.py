"""离线添加响应归一化测试。"""

import importlib.util
import sys
import unittest
from pathlib import Path


def _load_response_module():
    path = Path(__file__).resolve().parents[1] / "helper" / "offline" / "response.py"
    spec = importlib.util.spec_from_file_location("offline_response", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_response = _load_response_module()
parse_add_response = _response.parse_add_response


class OfflineResponseTest(unittest.TestCase):
    """覆盖成功、重复与真实失败的 115 响应。"""

    def test_success_uses_result_count(self):
        result = parse_add_response(
            {"state": True, "data": {"result": [{"info_hash": "a"}]}},
            ["magnet:a", "magnet:b"],
        )
        self.assertTrue(result.success)
        self.assertEqual(result.added_count, 1)
        self.assertEqual(result.duplicate_count, 0)

    def test_duplicate_errcode_is_idempotent_success(self):
        result = parse_add_response(
            {
                "state": False,
                "data": {"errcode": 10008, "result": [{"info_hash": "a"}]},
            },
            ["magnet:a"],
        )
        self.assertTrue(result.success)
        self.assertEqual(result.added_count, 0)
        self.assertEqual(result.duplicate_count, 1)

    def test_unrelated_failure_is_not_masked(self):
        self.assertIsNone(
            parse_add_response(
                {"state": False, "data": {"errcode": 401, "error_msg": "登录失效"}},
                ["magnet:a"],
            )
        )


if __name__ == "__main__":
    unittest.main()
