"""插件源码编译警告回归测试。"""

import warnings
import unittest
from pathlib import Path


class CompileWarningsTest(unittest.TestCase):
    """防止运行时源码重新引入 Python 编译警告。"""

    def test_directory_upload_queue_has_no_syntax_warning(self):
        """目录上传队列不应触发 SyntaxWarning。"""
        path = (
            Path(__file__).resolve().parents[1]
            / "helper"
            / "monitor"
            / "directory_upload_queue.py"
        )
        source = path.read_text(encoding="utf-8")
        with warnings.catch_warnings():
            warnings.simplefilter("error", SyntaxWarning)
            compile(source, str(path), "exec")


if __name__ == "__main__":
    unittest.main()
