"""115 离线添加接口响应的无状态解析工具。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


_DUPLICATE_TASK_ERRCODE = 10008


@dataclass(frozen=True)
class OfflineAddResult:
    """一次离线添加请求的归一化结果。"""

    added_count: int = 0
    duplicate_count: int = 0

    @property
    def success(self) -> bool:
        """请求是否已被 115 成功受理或确认重复。"""
        return self.added_count > 0 or self.duplicate_count > 0


def parse_add_response(response: Any, url_list: List[Any]) -> Optional[OfflineAddResult]:
    """解析 115 添加离线任务响应，区分成功、重复和真实失败。

    115 对重复磁力返回 ``state=false`` 与错误码 ``10008``，但该请求
    并非需要重试的失败。此处不依赖 helper 实例，避免并发请求之间通过
    可变状态传递结果。
    """
    if not isinstance(response, dict):
        return None

    data = response.get("data")
    payload: Dict[str, Any] = data if isinstance(data, dict) else {}
    result = payload.get("result")
    result_count = len(result) if isinstance(result, list) and result else len(url_list)

    if response.get("state"):
        return OfflineAddResult(added_count=result_count)

    codes = (response.get("errcode"), payload.get("errcode"))
    if any(str(code).strip() == str(_DUPLICATE_TASK_ERRCODE) for code in codes):
        return OfflineAddResult(duplicate_count=max(result_count, 1))

    messages = (response.get("error_msg"), payload.get("error_msg"))
    if any("任务已存在" in str(message or "") for message in messages):
        return OfflineAddResult(duplicate_count=max(result_count, 1))
    return None
