"""P115StrmHelper 运行时依赖兼容补丁。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from typing import Any


def _threadpool_map(
    func: Callable[..., Any],
    it: Iterable[Any],
    /,
    *its: Iterable[Any],
    max_workers: int | None = None,
) -> Iterator[Any]:
    """兼容 p115client 旧版 threadpool_map 的同步映射语义。"""
    from concurrenttools import thread_conmap

    yield from thread_conmap(func, it, *its, max_workers=max_workers)


async def _taskgroup_map(
    func: Callable[..., Any],
    it: Iterable[Any],
    /,
    *its: Iterable[Any],
    max_workers: int | None = None,
) -> AsyncIterator[Any]:
    """兼容 p115client 旧版 taskgroup_map 的异步映射语义。"""
    from concurrenttools import async_conmap

    if max_workers != 0 and (max_workers is None or max_workers <= 0):
        max_workers = 32
    async for result in async_conmap(
        func,
        it,
        *its,
        max_workers=max_workers,
    ):
        yield result


def patch_concurrenttools() -> None:
    """为仍使用旧函数名的 p115client 暴露 concurrenttools 兼容别名。"""
    try:
        import concurrenttools
    except ImportError:
        return

    if not hasattr(concurrenttools, "threadpool_map"):
        replacement = getattr(concurrenttools, "thread_conmap", None)
        if replacement is not None:
            concurrenttools.threadpool_map = _threadpool_map

    if not hasattr(concurrenttools, "taskgroup_map"):
        replacement = getattr(concurrenttools, "async_conmap", None)
        if replacement is not None:
            concurrenttools.taskgroup_map = _taskgroup_map
