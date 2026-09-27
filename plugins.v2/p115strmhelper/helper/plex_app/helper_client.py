"""helper 写库服务的 HTTP 客户端封装。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from httpx import Client

from app.sdk.logging import logger


class HelperClient:
    """封装对 122 上 plex-mediainfo-helper 写库服务的调用。"""

    def __init__(self, base_url: str, token: str = "", timeout: float = 60.0) -> None:
        """
        初始化 helper 客户端。

        :param base_url: helper 地址，如 http://192.168.0.122:9001
        :param token: 访问 token（对应 helper 的 PTH_TOKEN）
        :param timeout: 请求超时秒数
        """
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def _headers(self) -> Dict[str, str]:
        """构建带 token 的请求头。"""
        h = {"Content-Type": "application/json"}
        if self._token:
            h["X-PTH-Token"] = self._token
        return h

    def health(self) -> bool:
        """
        健康检查。

        :return: 服务可用返回 True
        """
        try:
            with Client(timeout=10.0) as client:
                resp = client.get(f"{self._base}/health")
                return resp.status_code == 200
        except Exception:
            return False

    def dbinfo(self) -> Optional[Dict[str, Any]]:
        """
        查询 helper 的数据库信息。

        :return: dbinfo 响应，失败返回 None
        """
        try:
            with Client(timeout=15.0) as client:
                resp = client.get(f"{self._base}/dbinfo", headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
                logger.warning("helper /dbinfo 返回 %s", resp.status_code)
        except Exception as e:
            logger.warning("helper /dbinfo 失败: %s", e)
        return None

    def write_batch(
        self, items: List[Dict[str, Any]], force: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        批量写入媒体信息。

        :param items: 每项为 helper payload（含 part_id 等）
        :param force: 是否忽略 Plex 繁忙检测强制写入
        :return: 写入结果，失败返回 None
        """
        if not items:
            return {"success": True, "total": 0, "ok": 0, "results": []}
        try:
            with Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._base}/write_batch",
                    headers=self._headers(),
                    json={"items": items, "force": force},
                )
                if resp.status_code in (200, 409):
                    data = resp.json()
                    self._log_batch_result(data, len(items))
                    return data
                logger.warning(
                    "helper /write_batch 返回 %s，本批 %s 条全部未写入",
                    resp.status_code, len(items),
                )
        except Exception as e:
            logger.warning("helper /write_batch 失败: %s（本批 %s 条未写入）", e, len(items))
        return None

    def part_status(
        self, part_ids: List[Any], batch_size: int = 500
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        """读取 Plex 数据库中一批 Part 的真实时长与流数量。

        Plex 的 children 接口不会稳定返回 Stream 数组，补全器不能只依赖该
        接口判断“是否已写入”。该接口由同机 helper 查询 SQLite，返回空流的
        Part 也会明确返回 streams=0；返回 None 表示旧版 helper 尚未支持。
        """
        normalized: List[int] = []
        for value in part_ids or []:
            try:
                item = int(value)
            except (TypeError, ValueError):
                continue
            if item > 0 and item not in normalized:
                normalized.append(item)
        if not normalized:
            return {}
        try:
            size = max(1, min(1000, int(batch_size or 500)))
        except (TypeError, ValueError):
            size = 500

        result: Dict[str, Dict[str, Any]] = {}
        try:
            with Client(timeout=max(15.0, self._timeout)) as client:
                for start in range(0, len(normalized), size):
                    current = normalized[start : start + size]
                    resp = client.post(
                        f"{self._base}/parts/status",
                        headers=self._headers(),
                        json={"part_ids": current},
                    )
                    if resp.status_code == 404:
                        logger.info(
                            "helper 尚未支持 /parts/status，将回退 Plex API 状态判断"
                        )
                        return None
                    if resp.status_code != 200:
                        logger.warning(
                            "helper /parts/status 返回 %s，本批 %s 条状态未读取",
                            resp.status_code,
                            len(current),
                        )
                        return None
                    data = resp.json()
                    if not data.get("success"):
                        logger.warning("helper /parts/status 返回失败: %s", data)
                        return None
                    for item in data.get("parts") or []:
                        try:
                            part_id = str(int(item.get("part_id")))
                        except (TypeError, ValueError):
                            continue
                        result[part_id] = {
                            "duration": item.get("duration"),
                            "streams": item.get("streams", 0),
                        }
        except Exception as e:
            logger.warning("helper /parts/status 失败: %s", e)
            return None
        return result

    @staticmethod
    def _log_batch_result(data: Dict[str, Any], sent: int) -> None:
        """
        按 helper 返回结果打印写入成功/失败明细。

        :param data: helper /write_batch 响应体
        :param sent: 本批发送的条目数
        """
        if data.get("busy"):
            logger.warning(
                "helper 检测到 Plex 繁忙，本批 %s 条全部未写入（可勾选强制写入或错峰重试）",
                sent,
            )
            return
        ok = data.get("ok", 0)
        results = data.get("results") or []
        failed = [
            r for r in results
            if not (r.get("success") or r.get("ok") or r.get("written"))
        ]
        if failed:
            logger.warning("helper 写入完成：成功 %s / 共 %s，失败 %s 条明细：", ok, sent, len(failed))
            for r in failed[:50]:
                pid = r.get("part_id") or r.get("id") or "?"
                reason = r.get("error") or r.get("message") or r.get("reason") or "未知原因"
                logger.warning("  写入失败 part_id=%s：%s", pid, reason)
            if len(failed) > 50:
                logger.warning("  （另有 %s 条失败明细省略）", len(failed) - 50)
        else:
            logger.info("helper 写入完成：成功 %s / 共 %s，全部成功", ok, sent)
