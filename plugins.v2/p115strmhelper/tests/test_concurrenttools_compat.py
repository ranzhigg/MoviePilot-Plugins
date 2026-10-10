"""P115StrmHelper 并发依赖兼容测试。"""

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from compat import patch_concurrenttools  # noqa: E402


class ConcurrentToolsCompatTest(unittest.TestCase):
    """验证新版 concurrenttools 能兼容 p115client 旧导入名。"""

    def test_adds_legacy_names_when_only_new_names_exist(self):
        """缺少旧名称时应提供同步和异步兼容入口。"""
        concurrenttools = types.ModuleType("concurrenttools")

        def thread_conmap(*args, **kwargs):
            return iter(())

        async def async_conmap(*args, **kwargs):
            if False:
                yield None

        concurrenttools.thread_conmap = thread_conmap
        concurrenttools.async_conmap = async_conmap

        with patch.dict(sys.modules, {"concurrenttools": concurrenttools}):
            patch_concurrenttools()

        self.assertTrue(callable(concurrenttools.threadpool_map))
        self.assertTrue(callable(concurrenttools.taskgroup_map))


if __name__ == "__main__":
    unittest.main()
