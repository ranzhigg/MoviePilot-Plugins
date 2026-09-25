import time
from pathlib import Path, PurePosixPath
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, List, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

from p115center import P115Center, OfflineInfo
from p115client import P115Client
from p115client.tool.clouddownload import clouddownload_iter
from p115client.tool.attr import get_attr

from app.log import logger

from ...core.config import configer
from ...core.p115 import get_pid_by_path
from ...helper.life import MonitorLife
from ...schemas.offline import OfflineTaskItem
from ...utils.path import PathUtils
from ...utils.string import StringUtils
from ...utils.sentry import sentry_manager
from .response import OfflineAddResult, parse_add_response


@sentry_manager.capture_all_class_exceptions
class OfflineDownloadHelper:
    """
    离线下载
    """

    _DIRECT_PATH_QUEUE_KEY = "offline_direct_path_queue"
    _DIRECT_PATH_QUEUE_VERSION = 1
    _DIRECT_RETRY_BASE_SECONDS = 30
    _DIRECT_RETRY_MAX_SECONDS = 30 * 60

    def __init__(self, client: P115Client, monitorlife: MonitorLife):
        self.client = client
        self.monitorlife = monitorlife
        self.transfer_list: List = []
        self._direct_path_queue_lock = Lock()
        self.direct_path_queue: List[Dict[str, Any]] = (
            self.__load_direct_path_queue()
        )

        self.offline_list_cache = {"data": None, "timestamp": 0}

    @staticmethod
    def build_offline_urls_payload(urls, savepath=None, wp_path_id=None):
        """
        构建添加下载列表参数
        """
        payload = {}
        for i, url in enumerate(urls):
            payload[f"url[{i}]"] = url.strip()

        if savepath:
            payload["savepath"] = savepath
        if wp_path_id:
            payload["wp_path_id"] = wp_path_id

        return payload

    @staticmethod
    def __normalize_hash(value: Any) -> str:
        """
        规范化离线任务 hash，115 返回值可能存在大小写差异。
        """
        return str(value or "").strip().lower()

    @staticmethod
    def __normalize_pan_path(value: Any) -> str:
        """
        将网盘路径统一为带根斜杠的 POSIX 路径。

        115 的添加接口可以接受不带根斜杠的路径，而路径映射和 STRM
        生成使用绝对网盘路径；统一格式可以避免直存任务找不到媒体映射。
        """
        path = str(value or "").strip().replace("\\", "/")
        if not path or path == "/":
            return "/"
        return "/" + path.strip("/")

    @classmethod
    def __join_pan_path(cls, parent: Any, name: Any) -> str:
        """
        拼接离线任务所在目录和任务根目录名。
        """
        parent_path = cls.__normalize_pan_path(parent)
        child_name = str(name or "").strip().replace("\\", "/").strip("/")
        if not child_name:
            return parent_path
        if parent_path == "/":
            return f"/{child_name}"
        return f"{parent_path.rstrip('/')}/{child_name}"

    def __load_direct_path_queue(self) -> List[Dict[str, Any]]:
        """
        读取直存任务队列。

        队列写入 MoviePilot 插件数据，而不是只保存在当前进程，故插件
        重载或容器重建后仍可继续轮询。兼容早期直接保存为 list 的格式。
        """
        raw = configer.get_plugin_data(self._DIRECT_PATH_QUEUE_KEY)
        if isinstance(raw, dict):
            raw_items = raw.get("items", [])
        elif isinstance(raw, list):
            raw_items = raw
        else:
            raw_items = []

        if not isinstance(raw_items, list):
            return []

        queue: List[Dict[str, Any]] = []
        seen = set()
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            item_hash = self.__normalize_hash(
                raw_item.get("hash") or raw_item.get("info_hash")
            )
            item_path = self.__normalize_pan_path(raw_item.get("path"))
            if not item_hash or item_path == "/":
                continue
            if item_hash in seen:
                continue
            seen.add(item_hash)

            try:
                created_at = float(raw_item.get("created_at", time.time()))
            except (TypeError, ValueError):
                created_at = time.time()
            try:
                retry_count = max(int(raw_item.get("retry_count", 0)), 0)
            except (TypeError, ValueError):
                retry_count = 0
            try:
                next_retry_at = float(raw_item.get("next_retry_at", 0))
            except (TypeError, ValueError):
                next_retry_at = 0

            item: Dict[str, Any] = {
                "hash": item_hash,
                "path": item_path,
                "created_at": created_at,
                "retry_count": retry_count,
                "next_retry_at": next_retry_at,
            }
            if raw_item.get("last_error"):
                item["last_error"] = str(raw_item["last_error"])[-500:]
            queue.append(item)
        return queue

    def __save_direct_path_queue(self) -> None:
        """
        持久化直存队列的完整快照。
        """
        with self._direct_path_queue_lock:
            items = [dict(item) for item in self.direct_path_queue]
            configer.save_plugin_data(
                self._DIRECT_PATH_QUEUE_KEY,
                {
                    "version": self._DIRECT_PATH_QUEUE_VERSION,
                    "items": items,
                },
            )

    def __enqueue_direct_path(self, info_hash: Any, path: Any) -> bool:
        """
        将显式指定路径的离线任务加入持久化队列。
        """
        normalized_hash = self.__normalize_hash(info_hash)
        normalized_path = self.__normalize_pan_path(path)
        if not normalized_hash or normalized_path == "/":
            return False

        with self._direct_path_queue_lock:
            for item in self.direct_path_queue:
                if self.__normalize_hash(item.get("hash")) != normalized_hash:
                    continue
                changed = item.get("path") != normalized_path
                item["path"] = normalized_path
                if changed:
                    item["next_retry_at"] = 0
                    item["retry_count"] = 0
                    item.pop("last_error", None)
                    configer.save_plugin_data(
                        self._DIRECT_PATH_QUEUE_KEY,
                        {
                            "version": self._DIRECT_PATH_QUEUE_VERSION,
                            "items": [dict(entry) for entry in self.direct_path_queue],
                        },
                    )
                return changed

            self.direct_path_queue.append(
                {
                    "hash": normalized_hash,
                    "path": normalized_path,
                    "created_at": time.time(),
                    "retry_count": 0,
                    "next_retry_at": 0,
                }
            )
            configer.save_plugin_data(
                self._DIRECT_PATH_QUEUE_KEY,
                {
                    "version": self._DIRECT_PATH_QUEUE_VERSION,
                    "items": [dict(entry) for entry in self.direct_path_queue],
                },
            )
            return True

    def __schedule_direct_retry(self, item: Dict[str, Any], error: Any) -> None:
        """
        为暂时无法生成 STRM 的任务设置退避，避免完成任务高频重试。
        """
        try:
            retry_count = max(int(item.get("retry_count", 0)), 0) + 1
        except (TypeError, ValueError):
            retry_count = 1
        delay = min(
            self._DIRECT_RETRY_BASE_SECONDS * (2 ** min(retry_count - 1, 6)),
            self._DIRECT_RETRY_MAX_SECONDS,
        )
        item["retry_count"] = retry_count
        item["next_retry_at"] = time.time() + delay
        item["last_error"] = str(error)[-500:]

    @staticmethod
    def __extract_magnet_hash(url: Any) -> str:
        """
        从磁力链接提取 BTIH，作为添加接口未返回 hash 时的兜底。
        """
        raw_url = str(url or "").strip()
        if not raw_url.lower().startswith("magnet:"):
            return ""
        try:
            query = parse_qs(urlsplit(raw_url).query)
            for value in query.get("xt", []):
                decoded = unquote(str(value))
                prefix = "urn:btih:"
                if decoded.lower().startswith(prefix):
                    return decoded[len(prefix) :].strip()
        except (TypeError, ValueError):
            pass
        return ""

    @classmethod
    def __extract_result_hashes(cls, result: Any, url_list: List) -> List[str]:
        """
        提取添加接口返回的任务 hash，并兼容磁力链接兜底。
        """
        if isinstance(result, dict):
            result_items = [result]
        elif isinstance(result, list):
            result_items = result
        else:
            result_items = []

        hashes: List[str] = []
        seen = set()
        for item in result_items:
            if isinstance(item, dict):
                info_hash = item.get("info_hash") or item.get("hash")
            elif isinstance(item, str):
                info_hash = item
            else:
                info_hash = None
            normalized_hash = cls.__normalize_hash(info_hash)
            if normalized_hash and normalized_hash not in seen:
                seen.add(normalized_hash)
                hashes.append(normalized_hash)

        for url in url_list:
            normalized_hash = cls.__normalize_hash(cls.__extract_magnet_hash(url))
            if normalized_hash and normalized_hash not in seen:
                seen.add(normalized_hash)
                hashes.append(normalized_hash)
        return hashes

    @staticmethod
    def __get_task_data(task: Any) -> Dict[str, Any]:
        """
        获取已完成任务的有效数据。

        clouddownload_iter 返回扁平任务字典；部分接口包装后会再套一层
        data，故这里兼容两种格式。
        """
        if not isinstance(task, dict):
            return {}
        nested = task.get("data")
        if isinstance(nested, dict) and (
            nested.get("delete_file_id") is not None or nested.get("name")
        ):
            return nested
        return task

    def __get_direct_task_path(
        self,
        queue_item: Dict[str, Any],
        task_data: Dict[str, Any],
        root_attr: Dict[str, Any],
    ) -> str:
        """
        解析离线任务实际生成的网盘目录路径。
        """
        task_name = root_attr.get("name") or task_data.get("name")
        if not task_name:
            raise RuntimeError("完成任务缺少目录名称")

        for key in ("path", "full_path", "file_path"):
            candidate = root_attr.get(key)
            if not candidate:
                continue
            candidate = self.__normalize_pan_path(candidate)
            if PurePosixPath(candidate).name == str(task_name):
                return candidate
        return self.__join_pan_path(queue_item.get("path"), task_name)

    def __resolve_direct_local_path(
        self, pan_path: str
    ) -> Tuple[str, Path, str]:
        """
        按媒体库映射计算直存任务的本地目录。

        返回 (网盘媒体根、本地任务目录、网盘任务目录)。注意本地目录
        通过映射的相对路径计算，不能把网盘的“整理”目录重复拼到本地。
        """
        mapping = configer.get_config("monitor_life_paths") or configer.get_config(
            "increment_sync_strm_paths"
        )
        status, local_root, pan_root = PathUtils.get_media_path(mapping, pan_path)
        if not status or not local_root or not pan_root:
            raise RuntimeError(f"未匹配到媒体库路径映射: {pan_path}")

        try:
            relative_path = PurePosixPath(pan_path).relative_to(
                PurePosixPath(self.__normalize_pan_path(pan_root))
            )
        except ValueError as exc:
            raise RuntimeError(f"媒体路径映射不包含任务目录: {pan_path}") from exc

        local_path = Path(local_root) / PathUtils.sanitize_path_parts(
            Path(relative_path.as_posix())
        )
        return pan_root, local_path, pan_path

    def __generate_direct_strm(self, queue_item: Dict[str, Any], task: Dict) -> int:
        """
        为直存完成任务只扫描任务目录并生成 STRM，不触发全库增量扫描。
        """
        task_data = self.__get_task_data(task)
        delete_file_id = task_data.get("delete_file_id")
        if delete_file_id is None:
            raise RuntimeError("完成任务缺少 delete_file_id")
        try:
            delete_file_id = int(delete_file_id)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("完成任务 delete_file_id 无效") from exc

        root_attr = get_attr(
            self.client,
            id=delete_file_id,
            **configer.get_ios_ua_app(app=False),
        )
        if not hasattr(root_attr, "get"):
            raise RuntimeError("无法获取完成任务目录信息")

        pan_task_path = self.__get_direct_task_path(
            queue_item=queue_item,
            task_data=task_data,
            root_attr=root_attr,
        )
        pan_root, local_task_path, _ = self.__resolve_direct_local_path(
            pan_task_path
        )

        # 延迟导入，避免离线 helper 在 service 初始化阶段与 STRM helper 形成
        # 循环导入。
        from ...helper.strm.api import ApiSyncStrmHelper
        from ...schemas.strm_api import (
            StrmApiPayloadByPathData,
            StrmApiPayloadByPathItem,
            StrmApiStatusCode,
        )

        is_dir = root_attr.get("is_dir", root_attr.get("is_directory", True))
        if isinstance(is_dir, str):
            is_dir = is_dir.strip().lower() not in {"", "0", "false", "no"}
        if is_dir:
            strm_helper = ApiSyncStrmHelper(
                client=self.client,
                mediainfo_downloader=self.monitorlife.mediainfodownloader,
            )
            code, msg, result = strm_helper.generate_strm_paths(
                StrmApiPayloadByPathData(
                    data=[
                        StrmApiPayloadByPathItem(
                            local_path=local_task_path.as_posix(),
                            pan_media_path=pan_task_path,
                        )
                    ]
                )
            )
        else:
            raise RuntimeError("完成任务根节点不是目录，等待下一次状态确认")

        if code != StrmApiStatusCode.Success:
            raise RuntimeError(f"STRM 生成接口失败: {msg}")

        processed_count = sum(
            int(getattr(result, field, 0) or 0)
            for field in (
                "success_count",
                "fail_count",
                "download_success_count",
                "download_fail_count",
            )
        )
        if processed_count == 0:
            raise RuntimeError("完成任务目录暂时没有可见文件，等待下一次确认")

        success_count = int(getattr(result, "success_count", 0) or 0)
        logger.info(
            "【离线下载】直存任务已生成 STRM：%s（成功 %s 个）",
            local_task_path,
            success_count,
        )
        return success_count

    def __process_direct_task(
        self, queue_item: Dict[str, Any], task: Dict
    ) -> Tuple[bool, str]:
        """
        处理一个直存完成任务，并返回是否可以从队列移除。
        """
        try:
            self.__generate_direct_strm(queue_item, task)
            return True, ""
        except Exception as e:
            logger.warning(
                "【离线下载】直存任务暂不能生成 STRM，将按退避策略重试：%s",
                e,
            )
            return False, str(e)

    def __remove_transfer_list_by_hash(self, target_hash):
        """
        通过 hash 删除指定字典
        """
        i = 0
        while i < len(self.transfer_list):
            if self.__normalize_hash(self.transfer_list[i].get("hash")) == (
                self.__normalize_hash(target_hash)
            ):
                target_path = self.transfer_list[i]["path"]
                del self.transfer_list[i]
                return target_path
            i += 1
        return None

    def __exists_in_transfer_list(self, target_hash):
        """
        判断 hash 是否在 transfer_list 中
        """
        normalized_hash = self.__normalize_hash(target_hash)
        if any(
            self.__normalize_hash(d.get("hash")) == normalized_hash
            for d in self.transfer_list
        ):
            return True
        return False

    def __add_transfer_task(self, item):
        """
        添加整理任务
        """
        try:
            if not item[1].get("data", None):
                if item[1].get("count", None):
                    logger.error(
                        f"【离线下载】{item[0]} 下载失败，无法添加到网盘整理队列"
                    )
                    self.__remove_transfer_list_by_hash(item[0])
                else:
                    item[1]["count"] = 1
                    logger.warn(f"【离线下载】{item[0]} 下载任务二次检测")
                return
            parent_path = self.__remove_transfer_list_by_hash(item[0])
            logger.info(f"【离线下载】{item[0]} 下载完成，添加到网盘整理队列")
            data = get_attr(
                self.client,
                id=int(item[1].get("data").get("delete_file_id")),
                **configer.get_ios_ua_app(app=False),
            )
            event = {
                "file_id": int(data["id"]),
                "file_category": 0 if data["is_dir"] else 1,
                "parent_id": int(data["parent_id"]),
                "file_size": int(data["size"]),
                "pick_code": data["pickcode"],
                "update_time": data["user_utime"],
                "sha1": data["sha1"],
            }
            file_path = Path(parent_path) / str(item[1].get("data").get("name"))
            rmt_mediaext = [
                f".{ext.strip()}"
                for ext in configer.get_config("user_rmt_mediaext")
                .replace("，", ",")
                .split(",")
            ]
            # 直接传入网盘整理
            self.monitorlife.media_transfer(
                event=event,
                file_path=file_path,
                rmt_mediaext=rmt_mediaext,
            )
        except Exception as e:
            logger.error(f"【离线下载】{item[0]} 无法添加到网盘整理队列: {e}")

    def add_urls(self, url_list: List, cid: int):
        """
        添加一组任务
        """
        payload = self.build_offline_urls_payload(urls=url_list, wp_path_id=cid)
        return self.client.clouddownload_task_add_urls(
            payload, **configer.get_ios_ua_app(app=False)
        )

    def get_tasks(self):
        """
        获取当前所有任务
        """
        return clouddownload_iter(
            self.client, cooldown=2, type="web", **configer.get_ios_ua_app(app=False)
        )

    def get_tasks_status(self, info_hash: List):
        """
        获取一组任务的状态和信息
        """
        info_hash_set = {
            self.__normalize_hash(item_hash) for item_hash in info_hash
        }
        for item in self.get_tasks():
            item_hash = item.get("info_hash", None)
            if self.__normalize_hash(item_hash) in info_hash_set:
                # 0 进行中、1 下载失败、2 下载成功、3 重试中
                if int(item.get("status", -1)) == 2:
                    yield [item_hash, {"status": True, "data": item}]
                elif int(item.get("status", -1)) == 1:
                    yield [item_hash, {"status": True, "data": ""}]
                else:
                    yield [item_hash, {"status": False, "data": item}]

    def add_urls_to_transfer_result(self, url_list: List) -> OfflineAddResult:
        """添加整理任务，并保留新增与重复任务的区分结果。"""
        try:
            # 寻找离线下载目录中可运行网盘整理的目录，如果无则调用 add_urls_to_path 函数
            parent_path = None
            for path in configer.pan_transfer_paths.split("\n"):
                if not path:
                    continue
                if path in configer.offline_download_paths:
                    parent_path = path
                    break
            if not parent_path:
                return self.add_urls_to_path_result(
                    url_list, configer.offline_download_paths[0], track_direct=False
                )

            parent_id = get_pid_by_path(
                client=self.client,
                path=parent_path,
                mkdir=True,
                update_cache=True,
                by_cache=True,
            )
            logger.debug(f"【离线下载】获取到下载目录 {parent_path} ID：{parent_id}")

            resp = self.add_urls(url_list=url_list, cid=parent_id)
            outcome = parse_add_response(resp, url_list)
            if not outcome:
                logger.error(f"【离线下载】下载任务添加失败: {url_list} {resp}")
                return OfflineAddResult()
            if outcome.duplicate_count:
                logger.info("【离线下载】任务已存在，跳过重复提交: %s", url_list)
                return outcome

            result = (resp.get("data") or {}).get("result")

            # 获取所有任务的 hash，添加到待整理列表中
            for info_hash in self.__extract_result_hashes(result, url_list):
                self.transfer_list.append(
                    {"hash": info_hash, "path": str(parent_path)}
                )

            for url in url_list:
                self.post_offline_info(url)
            logger.debug(f"【离线下载】下载任务添加完成: {url_list}")
            return outcome
        except Exception as e:
            logger.error(f"【离线下载】未知错误：{e}")
            return OfflineAddResult()

    def add_urls_to_transfer(self, url_list: List) -> Tuple[bool, int]:
        """
        添加一组任务并进行网盘整理

        :return: (是否成功, 接口返回的任务条数；失败时为 0)
        """
        outcome = self.add_urls_to_transfer_result(url_list)
        return outcome.success, outcome.added_count

    def add_urls_to_path_result(
        self, url_list: List, path: str, track_direct: bool = True
    ) -> OfflineAddResult:
        """
        添加一组任务下载到指定路径

        :param track_direct: 是否在完成后只扫描该目录并生成 STRM。显式
            指定路径的下载默认开启；整理流程的兼容回退会关闭。
        :return: 区分新增、重复与失败的离线添加结果。
        """
        try:
            parent_id = get_pid_by_path(
                client=self.client,
                path=path,
                mkdir=True,
                update_cache=True,
                by_cache=True,
            )
            logger.debug(f"【离线下载】获取到下载目录 {path} ID：{parent_id}")

            resp = self.add_urls(url_list=url_list, cid=parent_id)
            outcome = parse_add_response(resp, url_list)
            if not outcome:
                logger.error(f"【离线下载】下载任务添加失败: {url_list} {resp}")
                return OfflineAddResult()
            if outcome.duplicate_count:
                logger.info("【离线下载】任务已存在，跳过重复提交: %s", url_list)
                return outcome

            result = (resp.get("data") or {}).get("result")

            if track_direct:
                direct_hashes = self.__extract_result_hashes(result, url_list)
                queued_count = 0
                for info_hash in direct_hashes:
                    try:
                        if self.__enqueue_direct_path(info_hash, path):
                            queued_count += 1
                    except Exception as e:
                        logger.error(
                            "【离线下载】直存任务队列持久化失败（路径: %s）: %s",
                            path,
                            e,
                        )
                if queued_count:
                    logger.info(
                        "【离线下载】已将 %s 个直存任务加入持久化加速队列（路径: %s）",
                        queued_count,
                        path,
                    )
                elif not direct_hashes:
                    logger.warning(
                        "【离线下载】添加成功但未取得任务 hash，无法加入直存加速队列（路径: %s）",
                        path,
                    )

            for url in url_list:
                self.post_offline_info(url)
            logger.debug(f"【离线下载】下载任务添加完成: {url_list}")
            return outcome
        except Exception as e:
            logger.error(f"【离线下载】未知错误：{e}")
            return OfflineAddResult()

    def add_urls_to_path(
        self, url_list: List, path: str, track_direct: bool = True
    ) -> Tuple[bool, int]:
        """兼容旧调用方的指定路径离线下载入口。"""
        outcome = self.add_urls_to_path_result(url_list, path, track_direct)
        return outcome.success, outcome.added_count

    def pull_status_to_task(self):
        """
        等待下载完成运行指定任务。

        普通整理任务继续走原有 transfer_list；显式指定网盘路径的任务
        走持久化直存队列，只查询和扫描对应的离线任务及任务目录。
        """
        now = time.time()
        ready_direct_queue = []
        for item in self.direct_path_queue:
            try:
                next_retry_at = float(item.get("next_retry_at", 0) or 0)
            except (TypeError, ValueError):
                next_retry_at = 0
            if next_retry_at <= now:
                ready_direct_queue.append(item)

        transfer_hashes = {
            self.__normalize_hash(item.get("hash")) for item in self.transfer_list
        }
        direct_hashes = {
            self.__normalize_hash(item.get("hash")) for item in ready_direct_queue
        }
        tracked_hashes = {
            item_hash for item_hash in transfer_hashes | direct_hashes if item_hash
        }
        if not tracked_hashes:
            return

        # 一次轮询同时覆盖普通整理和直存队列，避免每类任务各请求一次 115。
        task_status_by_hash: Dict[str, Tuple[str, int, Dict]] = {}
        for task in self.get_tasks():
            if not isinstance(task, dict):
                continue
            item_hash = task.get("info_hash")
            normalized_hash = self.__normalize_hash(item_hash)
            if normalized_hash not in tracked_hashes:
                continue
            try:
                status = int(task.get("status", -1))
            except (TypeError, ValueError):
                status = -1
            task_status_by_hash[normalized_hash] = (
                str(item_hash or ""),
                status,
                task,
            )

        # 普通 / 转存任务保持原有整理流程。
        for item_hash, status, task in task_status_by_hash.values():
            if not self.__exists_in_transfer_list(item_hash):
                continue
            if status == 2:
                self.__add_transfer_task(
                    [item_hash, {"status": True, "data": task}]
                )
            elif status == 1:
                self.__add_transfer_task([item_hash, {"status": True, "data": ""}])

        # 直存任务完成后只处理自己的目录；失败任务不留在队列中，避免
        # 同一个不可恢复任务永久占用轮询。生成异常则持久化退避状态。
        direct_queue_changed = False
        for queue_item in list(ready_direct_queue):
            normalized_hash = self.__normalize_hash(queue_item.get("hash"))
            task_info = task_status_by_hash.get(normalized_hash)
            if not task_info:
                continue
            item_hash, status, task = task_info
            if status == 2:
                completed, error = self.__process_direct_task(queue_item, task)
                if completed:
                    try:
                        self.direct_path_queue.remove(queue_item)
                        direct_queue_changed = True
                    except ValueError:
                        pass
                else:
                    self.__schedule_direct_retry(queue_item, error)
                    direct_queue_changed = True
            elif status == 1:
                try:
                    self.direct_path_queue.remove(queue_item)
                    direct_queue_changed = True
                except ValueError:
                    pass
                logger.error(
                    "【离线下载】直存任务下载失败，已移出加速队列（路径: %s）",
                    queue_item.get("path"),
                )

        if direct_queue_changed:
            self.__save_direct_path_queue()

        if self.transfer_list:
            logger.info(
                "【离线下载】等待普通整理任务下载完成：%s 个",
                len(self.transfer_list),
            )
        if self.direct_path_queue:
            logger.info(
                "【离线下载】等待直存任务下载完成：%s 个",
                len(self.direct_path_queue),
            )

    def get_cached_data(self):
        """
        获取缓存离线下载列表
        """
        status_mapping = {0: "下载中", 1: "下载失败", 2: "已完成", 3: "重试中"}

        now = time.time()
        if (
            self.offline_list_cache["data"] is None
            or (now - self.offline_list_cache["timestamp"]) > 120
        ):
            raw_tasks = self.get_tasks()
            formatted_tasks = []

            for task in raw_tasks:
                task_model = OfflineTaskItem(
                    info_hash=task.get("info_hash", ""),
                    name=task.get("name", ""),
                    size=task.get("size", 0),
                    size_text=StringUtils.format_size(task.get("size", 0)),
                    status=task.get("status", 0),
                    status_text=status_mapping.get(task.get("status", 4), "未知状态"),
                    percent=task.get("percentDone", 0.0),
                    add_time=task.get("add_time", 0),
                )
                formatted_tasks.append(task_model)

            self.offline_list_cache = {"data": formatted_tasks, "timestamp": now}

        return self.offline_list_cache["data"]

    @staticmethod
    def post_offline_info(url: str):
        """
        上传离线下载信息
        """
        if not configer.get_config("upload_offline_info"):
            return

        try:
            client = P115Center(configer.get_config("machine_id"))
            resp = client.upload_offline_info(
                OfflineInfo(
                    url=url,
                    postime=datetime.now(timezone.utc),
                )
            )
            logger.info(f"【离线下载】离线下载信息报告服务器成功: {resp.model_dump()}")
        except Exception as e:
            logger.debug(f"【离线下载】离线下载报告服务器失败: {e}")
