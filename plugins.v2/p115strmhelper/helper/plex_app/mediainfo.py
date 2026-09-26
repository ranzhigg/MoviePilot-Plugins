"""媒体信息补全编排：枚举 Plex STRM 条目，取数据源，写入 helper。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from time import sleep
from typing import Any, Callable, Dict, List, Optional

from app.sdk.logging import logger

from .emby_client import EmbyClient
from .ffprobe_source import FfprobeSource
from .helper_client import HelperClient
from .plex_client import PlexClient


class MediaInfoCompleter:
    """编排 Plex STRM 媒体流信息补全的完整流程。"""

    def __init__(
        self,
        plex: PlexClient,
        helper: HelperClient,
        emby: Optional[EmbyClient] = None,
        use_emby: bool = True,
        overwrite_streams: bool = True,
        concurrency: int = 3,
        force_write: bool = False,
        ffprobe: Optional[FfprobeSource] = None,
        use_ffprobe: bool = True,
        write_batch_size: int = 20,
        write_busy_retries: int = 2,
        write_retry_delay: float = 2.0,
    ) -> None:
        """
        初始化补全器。

        :param plex: Plex 客户端
        :param helper: helper 写库客户端
        :param emby: Emby 客户端（数据源）
        :param use_emby: 是否启用 Emby 数据源
        :param overwrite_streams: 写入前是否清空该 part 旧流
        :param concurrency: 数据源探测并发数
        :param force_write: 是否忽略 Plex 繁忙强制写入
        :param ffprobe: ffprobe 数据源
        :param use_ffprobe: 是否启用 ffprobe 数据源
        :param write_batch_size: 每次提交给 Helper 的最大条数
        :param write_busy_retries: Helper 报告 Plex 繁忙时的重试次数
        :param write_retry_delay: 繁忙重试的基础等待秒数
        """
        self._plex = plex
        self._helper = helper
        self._emby = emby
        self._use_emby = use_emby and emby is not None
        self._ffprobe = ffprobe
        self._use_ffprobe = use_ffprobe and ffprobe is not None
        self._overwrite = overwrite_streams
        self._concurrency = max(1, concurrency)
        self._force = force_write
        self._write_batch_size = max(1, min(100, int(write_batch_size or 20)))
        self._write_busy_retries = max(0, min(5, int(write_busy_retries or 0)))
        self._write_retry_delay = max(0.0, min(30.0, float(write_retry_delay or 0)))

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        """把 Helper 的可选统计字段安全转换为整数。"""
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return default

    def _write_payloads(
        self,
        payloads: List[Dict[str, Any]],
        summary: Dict[str, Any],
        scope: str,
        item_index: Optional[Dict[Any, Dict[str, Any]]] = None,
        progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        """
        以小批次写入 Helper。

        Plex 播放或扫描期间 Helper 会拒绝写库。以前全量补全只发一个大批次，
        导致一次忙碌判断就丢掉全部解析结果；现在每批独立统计，并只重试当前批次。
        """
        if not payloads:
            return

        total = len(payloads)
        batches = 0
        for start in range(0, total, self._write_batch_size):
            batch = payloads[start : start + self._write_batch_size]
            batches += 1
            result: Optional[Dict[str, Any]] = None
            busy_attempt = 0

            while True:
                result = self._helper.write_batch(batch, force=self._force)
                if not result or not result.get("busy") or self._force:
                    break
                if busy_attempt >= self._write_busy_retries:
                    break
                busy_attempt += 1
                summary["write_retries"] = summary.get("write_retries", 0) + 1
                if self._write_retry_delay:
                    sleep(self._write_retry_delay * busy_attempt)

            batch_summary = {
                "written_ok": 0,
                "write_failed": 0,
                "helper_busy": False,
            }
            if result is None:
                batch_summary["write_failed"] = len(batch)
                summary["write_failed"] += len(batch)
                for item in batch:
                    if item_index is not None:
                        current = item_index.get(item.get("part_id"))
                        if current:
                            current["status"] = "write_failed"
            elif result.get("busy"):
                batch_summary["helper_busy"] = True
                batch_summary["write_failed"] = len(batch)
                summary["helper_busy"] = True
                summary["write_failed"] += len(batch)
                for item in batch:
                    if item_index is not None:
                        current = item_index.get(item.get("part_id"))
                        if current:
                            current["status"] = "busy"
            else:
                ok = max(0, min(len(batch), self._safe_int(result.get("ok"))))
                failed = len(batch) - ok
                batch_summary["written_ok"] = ok
                batch_summary["write_failed"] = failed
                summary["written_ok"] += ok
                summary["write_failed"] += failed

                results = result.get("results") or []
                for item_result in results:
                    part_id = item_result.get("part_id")
                    if item_index is not None:
                        current = item_index.get(part_id)
                        if current:
                            success = bool(
                                item_result.get("success")
                                or item_result.get("ok")
                                or item_result.get("written")
                            )
                            current["status"] = "written" if success else "write_failed"
                            if not success and item_result.get("error"):
                                current["error"] = str(item_result["error"])[:120]
                if item_index is not None and not results and ok == len(batch):
                    for item in batch:
                        current = item_index.get(item.get("part_id"))
                        if current:
                            current["status"] = "written"

            self._log_write_outcome(
                f"{scope} 批次 {batches}",
                len(batch),
                result,
                batch_summary,
            )
            if progress_cb:
                progress_cb(
                    {
                        "phase": "writing",
                        "done": min(start + len(batch), total),
                        "total": total,
                        "batches": batches,
                        "written_ok": summary.get("written_ok", 0),
                        "write_failed": summary.get("write_failed", 0),
                    }
                )

    def _resolve_one(self, part: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        为单个 STRM part 从 Emby 或 ffprobe 解析媒体信息。

        :param part: {part_id, file, title, ...}
        :return: helper payload（含 part_id 与流信息），失败返回 None
        """
        file_path = part.get("file") or ""
        info: Optional[Dict[str, Any]] = None

        if self._use_emby and self._emby:
            try:
                info = self._emby.find_streams_by_name(file_path)
            except Exception as e:
                logger.debug("Emby 数据源失败 %s: %s", file_path, e)

        if not info and self._use_ffprobe and self._ffprobe:
            try:
                info = self._ffprobe.find_streams_by_name(file_path)
            except Exception as e:
                logger.debug("ffprobe 数据源失败 part_id=%s: %s", part.get("part_id"), e)

        if not info:
            return None
        info["part_id"] = part["part_id"]
        info["overwrite_streams"] = self._overwrite
        return info

    def _log_write_outcome(
        self,
        scope: str,
        sent: int,
        res: Optional[Dict[str, Any]],
        summary: Dict[str, Any],
    ) -> None:
        """
        统一打印写入结果日志：区分 helper 无响应 / Plex 繁忙 / 部分失败 / 全部成功。

        :param scope: 日志范围描述，如 "全量补全" 或 "ratingKey=xxx"
        :param sent: 本次发送写入的条目数
        :param res: helper.write_batch 返回值
        :param summary: 当前汇总（用于读取 written_ok/write_failed）
        """
        if sent == 0:
            return
        if res is None:
            logger.warning(
                "Plex App 写入失败[%s]：helper 无响应/请求异常，%s 条全部未写入",
                scope, sent,
            )
            return
        if res.get("busy"):
            logger.warning(
                "Plex App 写入未完成[%s]：Plex 繁忙，%s 条未写入（可勾选强制写入或错峰重试）",
                scope, sent,
            )
            return
        failed = summary.get("write_failed", 0)
        ok = summary.get("written_ok", 0)
        if failed > 0:
            logger.warning(
                "Plex App 写入部分失败[%s]：成功 %s / 共 %s，失败 %s 条",
                scope, ok, sent, failed,
            )
        else:
            logger.info("Plex App 写入成功[%s]：%s 条全部写入", scope, ok)

    def _log_unresolved(self, scope: str, unresolved_files: List[str]) -> None:
        """
        打印未能从任何数据源取到媒体信息的文件明细。

        :param scope: 日志范围描述
        :param unresolved_files: 未解析成功的文件路径列表
        """
        if not unresolved_files:
            return
        logger.warning(
            "Plex App 未取到媒体信息[%s]：%s 个文件（ffprobe 未命中）",
            scope, len(unresolved_files),
        )
        for f in unresolved_files[:50]:
            logger.warning("  未解析: %s", f)
        if len(unresolved_files) > 50:
            logger.warning("  （另有 %s 个未解析文件省略）", len(unresolved_files) - 50)

    def run_rating_key(
        self, rating_key: str, only_missing: bool = True, forward: int = 5
    ) -> Dict[str, Any]:
        """
        针对单个播放条目 ratingKey 执行增量补全（用于播放停止后的针对性补全）。

        采用播放驱动的窗口策略：电影仅补当前这部；单集则补「当前集 + 后 forward 集」
        窗口，only_missing=True 时窗口内已补全的集自动跳过，实现增量。

        :param rating_key: 当前播放条目的 ratingKey（电影或单集）
        :param only_missing: 是否仅处理缺失媒体信息的 part
        :param forward: 单集场景下向后预取的集数
        :return: 汇总结果
        """
        summary: Dict[str, Any] = {
            "rating_key": rating_key,
            "label": self._plex.item_label(rating_key),
            "strm_parts": 0,
            "resolved": 0,
            "emby_hits": 0,
            "ffprobe_hits": 0,
            "unresolved": 0,
            "written_ok": 0,
            "write_failed": 0,
            "helper_busy": False,
            "write_retries": 0,
            "items": [],
        }
        parts = self._plex.collect_window_parts_by_rating_key(
            rating_key, forward=forward, only_missing=only_missing
        )
        summary["strm_parts"] = len(parts)
        # 日志范围优先用可读剧名/片名，取不到再退回 ratingKey
        scope = summary.get("label") or f"ratingKey={rating_key}"
        if not parts:
            return summary

        payloads: List[Dict[str, Any]] = []
        unresolved_files: List[str] = []
        # part_id -> 明细项引用，写入阶段回填状态
        item_index: Dict[Any, Dict[str, Any]] = {}
        for p in parts:
            item = {
                "label": p.get("label") or p.get("title") or "",
                "part_id": p.get("part_id"),
                "status": "unresolved",
            }
            summary["items"].append(item)
            item_index[p.get("part_id")] = item
            info = self._resolve_one(p)
            if info:
                payloads.append(info)
                summary["resolved"] += 1
                item["status"] = "resolved"
                if info.get("source") == "emby":
                    summary["emby_hits"] += 1
                elif info.get("source") == "ffprobe":
                    summary["ffprobe_hits"] += 1
            else:
                summary["unresolved"] += 1
                unresolved_files.append(p.get("file") or str(p.get("part_id")))

        scope = summary.get("label") or f"ratingKey={rating_key}"
        self._log_unresolved(scope, unresolved_files)

        for p in payloads:
            p.pop("source", None)
        if payloads:
            self._write_payloads(payloads, summary, scope, item_index=item_index)
        return summary

    def run(
        self,
        section_keys: List[str],
        only_missing: bool = True,
        progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        执行补全：枚举分区 STRM part，解析数据源并写入 helper。

        :param section_keys: 要处理的 Plex 分区 key 列表
        :param only_missing: 是否仅处理缺失媒体信息的 part
        :param progress_cb: 进度回调（收到阶段性统计）
        :return: 汇总结果
        """
        summary: Dict[str, Any] = {
            "sections": len(section_keys),
            "strm_parts": 0,
            "resolved": 0,
            "emby_hits": 0,
            "ffprobe_hits": 0,
            "unresolved": 0,
            "written_ok": 0,
            "write_failed": 0,
            "helper_busy": False,
            "write_retries": 0,
            "details": [],
        }

        # 1. 枚举 STRM part
        all_parts: List[Dict[str, Any]] = []
        for skey in section_keys:
            all_parts.extend(self._plex.collect_strm_parts(skey, only_missing))
        summary["strm_parts"] = len(all_parts)
        if not all_parts:
            return summary
        if progress_cb:
            progress_cb({"phase": "enumerated", "count": len(all_parts)})

        # 2. 并发解析数据源
        payloads: List[Dict[str, Any]] = []
        unresolved_files: List[str] = []
        with ThreadPoolExecutor(max_workers=self._concurrency) as pool:
            futures = {pool.submit(self._resolve_one, p): p for p in all_parts}
            done = 0
            for fut in as_completed(futures):
                done += 1
                src_part = futures[fut]
                info = fut.result()
                if info:
                    payloads.append(info)
                    summary["resolved"] += 1
                    if info.get("source") == "emby":
                        summary["emby_hits"] += 1
                    elif info.get("source") == "ffprobe":
                        summary["ffprobe_hits"] += 1
                else:
                    summary["unresolved"] += 1
                    unresolved_files.append(
                        src_part.get("file") or str(src_part.get("part_id"))
                    )
                if progress_cb and done % 10 == 0:
                    progress_cb(
                        {"phase": "resolving", "done": done, "total": len(all_parts)}
                    )

        self._log_unresolved("全量补全", unresolved_files)

        # 3. 写入 helper（去掉 source 字段再发）
        for p in payloads:
            p.pop("source", None)
        if payloads:
            self._write_payloads(
                payloads,
                summary,
                "全量补全",
                progress_cb=progress_cb,
            )
        if progress_cb:
            progress_cb({"phase": "done", **summary})
        return summary
