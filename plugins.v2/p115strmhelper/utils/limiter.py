__all__ = ["RateLimiter", "ApiEndpointCooldown"]

from threading import Lock
from time import monotonic, sleep
from typing import Callable


class RateLimiter:
    """
    速率控制器，精确控制 QPS
    """

    def __init__(self, qps: float):
        """
        初始化速率控制器

        :param qps (float): 每秒允许的请求数
        """
        if qps <= 0:
            qps = float("inf")
        self.interval = 1.0 / qps
        self.lock = Lock()
        self.next_call_time = monotonic()

    def acquire(self):
        """
        获取调用许可，阻塞直到满足速率限制
        """
        sleep_duration = 0
        with self.lock:
            now = monotonic()
            sleep_duration = self.next_call_time - now
            self.next_call_time = max(now, self.next_call_time) + self.interval
        if sleep_duration > 0:
            sleep(sleep_duration)


class ApiEndpointCooldown:
    """
    独立冷却时间和线程锁的 API 端点
    """

    def __init__(self, api_callable: Callable, cooldown: float | int):
        """
        初始化 API 端点冷却控制器

        :param api_callable (Callable): 要调用的 API 函数
        :param cooldown (float | int): 冷却时间（秒）
        """
        self.api_callable = api_callable
        self.cooldown = cooldown
        self.lock = Lock()
        self.last_call_time = monotonic() - cooldown

    def __call__(self, payload: dict) -> dict:
        """
        执行 API 调用，处理冷却逻辑

        :param payload (dict): API 调用参数

        :return dict: API 返回结果
        """
        if self.cooldown > 0:
            with self.lock:
                now = monotonic()
                # 先在锁内预约下一次调用时间。若只读取后再睡眠，并发线程
                # 会同时看到同一个 last_call_time，醒来后仍可能挤在一起请求。
                scheduled_time = max(now, self.last_call_time + self.cooldown)
                self.last_call_time = scheduled_time

            sleep_duration = scheduled_time - now
            if sleep_duration > 0:
                sleep(sleep_duration)
        return self.api_callable(payload)
