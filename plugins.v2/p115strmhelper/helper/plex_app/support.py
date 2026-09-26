"""Plex App 播放支持的配置、Webhook、补全与按需标记分析。"""

from __future__ import annotations

import json
from threading import Lock, Thread
from time import monotonic, time
from typing import Any, Dict, List, Optional

from app.log import logger

from ...core.config import configer
from .ffprobe_source import FfprobeSource
from .helper_client import HelperClient
from .mediainfo import MediaInfoCompleter
from .plex_client import PlexClient


class PlexAppSupport:
    """提供 Plex App 的 STRM 补全及播放时原生片头片尾分析。"""

    _PREFIX = "plex_app_"

    def __init__(self) -> None:
        self._task_lock = Lock()
        self._trigger_lock = Lock()
        self._completion_queue_lock = Lock()
        self._recent_triggers: Dict[str, float] = {}
        self._recent_marker_triggers: Dict[str, float] = {}
        self._completion_queue: Dict[str, Dict[str, Any]] = {}
        self._completion_worker_active = False
        self._helper_health_failures = 0

    @staticmethod
    def _value(name: str, default: Any = None) -> Any:
        value = configer.get_config(f"plex_app_{name}")
        return default if value is None else value

    @classmethod
    def configured(cls) -> bool:
        """返回是否已启用且具备调用 Plex/Helper 的最低配置。"""
        return cls.plex_configured() and bool(
            str(cls._value("helper_url", "") or "").strip()
        )

    @classmethod
    def plex_configured(cls) -> bool:
        """返回是否已启用且具备调用 Plex 原生 API 的最低配置。"""
        return bool(
            configer.enabled
            and cls._value("enabled", False)
            and str(cls._value("plex_url", "") or "").strip()
            and str(cls._value("plex_token", "") or "").strip()
        )

    @classmethod
    def _selected_sections(cls) -> List[str]:
        raw = str(cls._value("sections", "") or "")
        return [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]

    @classmethod
    def _build_completer(cls, force_write: bool = False) -> Optional[MediaInfoCompleter]:
        if not cls.configured():
            logger.warning("Plex App 未启用或 Plex/Helper 配置不完整")
            return None
        plex = cls._build_plex_client()
        if not plex:
            return None
        helper_url = str(cls._value("helper_url", "") or "").strip()
        helper_token = str(cls._value("helper_token", "") or "").strip()

        try:
            timeout = max(1, min(300, int(cls._value("ffprobe_timeout", 40) or 40)))
        except (TypeError, ValueError):
            timeout = 40
        try:
            concurrency = max(1, min(16, int(cls._value("concurrency", 3) or 3)))
        except (TypeError, ValueError):
            concurrency = 3

        helper = HelperClient(helper_url, helper_token)
        ffprobe = FfprobeSource(
            path_map=str(cls._value("ffprobe_path_map", "") or ""),
            timeout=timeout,
        )
        return MediaInfoCompleter(
            plex=plex,
            helper=helper,
            overwrite_streams=bool(cls._value("overwrite_streams", True)),
            concurrency=concurrency,
            force_write=force_write,
            ffprobe=ffprobe,
            use_ffprobe=True,
        )

    @classmethod
    def _build_plex_client(cls) -> Optional[PlexClient]:
        """按当前配置创建 Plex 客户端，供补全和播放标记请求共用。"""
        if not cls.plex_configured():
            logger.warning("Plex App 未启用或 Plex 配置不完整")
            return None
        plex_url = str(cls._value("plex_url", "") or "").strip()
        token = str(cls._value("plex_token", "") or "").strip()
        if not plex_url.startswith(("http://", "https://")):
            plex_url = "http://" + plex_url
        return PlexClient(plex_url, token)

    @staticmethod
    def _save_result(key: str, value: Dict[str, Any]) -> None:
        try:
            configer.save_plugin_data(key=key, value=value)
        except Exception as exc:
            logger.debug("保存 Plex App 补全结果失败: %s", exc)

    @staticmethod
    def _get_result(key: str) -> Dict[str, Any]:
        try:
            value = configer.get_plugin_data(key=key) or {}
            return value if isinstance(value, dict) else {}
        except Exception as exc:
            logger.debug("读取 Plex App 补全结果失败: %s", exc)
            return {}

    @classmethod
    def _log_summary(cls, scope: str, summary: Dict[str, Any]) -> None:
        logger.info(
            "Plex App 媒体信息补全[%s]：处理 %s，解析 %s，写入 %s，未命中 %s，失败 %s",
            scope,
            summary.get("strm_parts", 0),
            summary.get("resolved", 0),
            summary.get("written_ok", 0),
            summary.get("unresolved", 0),
            summary.get("write_failed", 0),
        )

    def run_completion(
        self,
        source: str = "manual",
        force_write: bool = False,
        section_keys: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """执行指定 Plex 媒体库的全量 STRM 媒体信息补全。"""
        if not self._task_lock.acquire(blocking=False):
            return {"success": False, "error": "已有 Plex App 补全任务在运行"}
        try:
            completer = self._build_completer(force_write=force_write)
            if not completer:
                return {"success": False, "error": "Plex/Helper 配置不完整"}
            keys = section_keys or self._selected_sections()
            if not keys:
                return {"success": False, "error": "未指定 Plex 媒体库 key"}
            summary = completer.run(
                keys,
                only_missing=bool(self._value("only_missing", True)),
            )
            summary.update({"success": True, "source": source, "ts": int(time())})
            self._save_result("plex_app_last_result", summary)
            self._log_summary("全量", summary)
            return summary
        except Exception as exc:
            logger.error("Plex App 全量补全异常: %s", exc, exc_info=True)
            return {"success": False, "error": str(exc)}
        finally:
            self._task_lock.release()

    def _allowed_rating_key(self, rating_key: str) -> bool:
        """在后台确认条目属于已选媒体库，避免 Webhook 请求被网络调用阻塞。"""
        selected = set(self._selected_sections())
        if not selected:
            logger.info("Plex App 跳过播放补全：未选择媒体库 ratingKey=%s", rating_key)
            return False
        plex = self._build_plex_client()
        if not plex:
            return False
        section_key = plex.item_section_key(rating_key)
        if section_key not in selected:
            logger.info(
                "Plex App 跳过播放补全：条目不在已选媒体库 ratingKey=%s section=%s",
                rating_key,
                section_key,
            )
            return False
        return True

    def _should_trigger(self, rating_key: str, scope: str = "default") -> bool:
        """按事件范围去重，允许播放探测与停止后的后台补齐各执行一次。"""
        now = monotonic()
        try:
            window = max(0, int(self._value("dedup_window", 300) or 300))
        except (TypeError, ValueError):
            window = 300
        with self._trigger_lock:
            for key, stamp in list(self._recent_triggers.items()):
                if now - stamp > window:
                    self._recent_triggers.pop(key, None)
            dedup_key = f"{scope}:{rating_key}"
            last = self._recent_triggers.get(dedup_key)
            if last is not None and now - last <= window:
                return False
            self._recent_triggers[dedup_key] = now
            return True

    @staticmethod
    def _completion_scope(source: str) -> str:
        """将 Webhook 来源归并为稳定的队列和去重范围。"""
        value = str(source or "").lower()
        if "play" in value or "resume" in value:
            return "play"
        if "stop" in value or "scrobble" in value:
            return "stop"
        if "prefetch" in value:
            return "prefetch"
        return "manual"

    @classmethod
    def _bounded_forward(cls, value: Any, default: int) -> int:
        """把配置中的预取集数限制在安全范围内。"""
        try:
            return max(0, min(50, int(value)))
        except (TypeError, ValueError):
            return max(0, min(50, int(default)))

    def _queue_completion(
        self,
        rating_key: str,
        source: str,
        forward: int,
    ) -> bool:
        """将播放补全任务合并入后台队列并启动唯一 worker。"""
        scope = self._completion_scope(source)
        queue_key = f"{scope}:{rating_key}"
        with self._completion_queue_lock:
            existing = self._completion_queue.get(queue_key)
            if existing:
                existing["forward"] = max(int(existing.get("forward", 0)), forward)
            else:
                if len(self._completion_queue) >= 128:
                    logger.warning("Plex App 播放补全队列已满，丢弃 ratingKey=%s", rating_key)
                    return False
                self._completion_queue[queue_key] = {
                    "rating_key": rating_key,
                    "source": source,
                    "forward": forward,
                }
            if self._completion_worker_active:
                return True
            self._completion_worker_active = True

        try:
            Thread(
                target=self._completion_worker,
                daemon=True,
                name="p115-plex-app-completion-queue",
            ).start()
        except Exception:
            with self._completion_queue_lock:
                self._completion_worker_active = False
            logger.exception("Plex App 播放补全队列 worker 启动失败")
            return False
        return True

    def _completion_worker(self) -> None:
        """顺序消费播放补全队列，保证当前条目优先且不并发写 Plex。"""
        while True:
            with self._completion_queue_lock:
                if not self._completion_queue:
                    self._completion_worker_active = False
                    return
                queue_key, task = next(iter(self._completion_queue.items()))
                self._completion_queue.pop(queue_key, None)

            rating_key = str(task.get("rating_key") or "").strip()
            if not rating_key:
                continue
            try:
                allowed = self._allowed_rating_key(rating_key)
            except Exception as exc:
                logger.warning(
                    "Plex App 播放补全条目校验失败 ratingKey=%s: %s",
                    rating_key,
                    exc,
                )
                continue
            if not allowed:
                continue
            self._task_lock.acquire()
            try:
                completer = self._build_completer(force_write=False)
                if not completer:
                    continue
                forward = self._bounded_forward(task.get("forward"), 0)
                summary = completer.run_rating_key(
                    rating_key,
                    only_missing=bool(self._value("only_missing", True)),
                    forward=forward,
                )
                summary.update(
                    {
                        "success": True,
                        "source": task.get("source") or "webhook",
                        "forward": forward,
                        "ts": int(time()),
                    }
                )
                self._save_result("plex_app_last_play_result", summary)
                self._log_summary(summary.get("label") or rating_key, summary)
            except Exception as exc:
                logger.error(
                    "Plex App 播放补全异常 ratingKey=%s: %s",
                    rating_key,
                    exc,
                    exc_info=True,
                )
            finally:
                self._task_lock.release()

    def _should_trigger_marker(self, rating_key: str) -> bool:
        """按播放事件去重，避免 Plex 重复上报造成重复分析。"""
        now = monotonic()
        try:
            window = max(0, int(self._value("dedup_window", 300) or 300))
        except (TypeError, ValueError):
            window = 300
        with self._trigger_lock:
            for key, stamp in list(self._recent_marker_triggers.items()):
                if now - stamp > window:
                    self._recent_marker_triggers.pop(key, None)
            last = self._recent_marker_triggers.get(rating_key)
            if last is not None and now - last <= window:
                return False
            self._recent_marker_triggers[rating_key] = now
            return True

    def detect_markers_on_play(self, rating_key: str, item_type: str = "") -> bool:
        """播放剧集时按需请求 Plex 原生片头/片尾分析，不扫描整个媒体库。"""
        rating_key = str(rating_key or "").strip()
        if not rating_key or not self.plex_configured():
            return False
        if not bool(self._value("marker_detection_enabled", False)):
            return False
        plex = self._build_plex_client()
        if not plex:
            return False
        item_type = str(item_type or "").strip().lower()
        if not self._should_trigger_marker(rating_key):
            return False
        force = bool(self._value("marker_detection_force", False))

        def worker() -> None:
            try:
                resolved_type = item_type or plex.item_type(rating_key)
                if resolved_type != "episode":
                    logger.info(
                        "Plex App 跳过播放标记分析：仅对剧集启用 ratingKey=%s type=%s",
                        rating_key,
                        resolved_type or "unknown",
                    )
                    return
                intro = plex.detect_intro(rating_key, force=force)
                credits = plex.detect_credits(rating_key, force=force)
                summary = {
                    "success": intro or credits,
                    "rating_key": rating_key,
                    "item_type": resolved_type,
                    "intro_queued": bool(intro),
                    "credits_queued": bool(credits),
                    "force": force,
                    "ts": int(time()),
                }
                self._save_result("plex_app_last_marker_result", summary)
                logger.info(
                    "Plex App 播放时请求原生片头片尾分析 ratingKey=%s intro=%s credits=%s",
                    rating_key,
                    intro,
                    credits,
                )
            except Exception as exc:
                logger.warning(
                    "Plex App 播放时请求片头片尾分析失败 ratingKey=%s: %s",
                    rating_key,
                    exc,
                )

        try:
            Thread(target=worker, daemon=True, name="p115-plex-marker-detection").start()
        except Exception:
            logger.exception("Plex App 播放标记分析 worker 启动失败 ratingKey=%s", rating_key)
            return False
        return True

    def complete_rating_key(
        self,
        rating_key: str,
        source: str = "webhook",
        forward: Optional[int] = None,
    ) -> bool:
        """异步排队补全当前播放条目，并按来源选择后续剧集预取量。"""
        rating_key = str(rating_key or "").strip()
        if not rating_key or not self.configured():
            return False
        if not self._selected_sections():
            logger.info("Plex App 跳过播放补全：未选择媒体库 ratingKey=%s", rating_key)
            return False
        scope = self._completion_scope(source)
        if not self._should_trigger(rating_key, scope=scope):
            return False
        if forward is None:
            default = 0 if scope == "play" else 5
            config_key = "play_forward_episodes" if scope == "play" else "forward_episodes"
            forward = self._bounded_forward(self._value(config_key, default), default)
        else:
            forward = self._bounded_forward(forward, 0)
        return self._queue_completion(rating_key, source, forward)

    def helper_check(self) -> Dict[str, Any]:
        """检查 Helper 连通性并返回数据库概览。"""
        url = str(self._value("helper_url", "") or "").strip()
        token = str(self._value("helper_token", "") or "").strip()
        if not url:
            return {"success": False, "error": "未配置 Helper 地址"}
        helper = HelperClient(url, token)
        healthy = helper.health()
        info = helper.dbinfo() if healthy else None
        if healthy:
            self._helper_health_failures = 0
        else:
            self._helper_health_failures += 1
        return {"success": healthy, "healthy": healthy, "dbinfo": info}

    def helper_health_tick(self) -> None:
        result = self.helper_check()
        if result.get("healthy"):
            return
        logger.warning(
            "Plex App Helper 健康检查失败：连续 %s 次",
            self._helper_health_failures,
        )

    def list_sections(self) -> Dict[str, Any]:
        completer = self._build_completer()
        if not completer:
            return {"success": False, "error": "Plex/Helper 配置不完整", "sections": []}
        sections = completer._plex.list_sections()
        return {"success": True, "sections": sections}

    def result(self) -> Dict[str, Any]:
        """返回全量、播放和片头片尾探测结果及当前队列长度。"""
        with self._completion_queue_lock:
            pending = len(self._completion_queue)
        return {
            "success": True,
            "result": self._get_result("plex_app_last_result"),
            "last_play_result": self._get_result("plex_app_last_play_result"),
            "last_marker_result": self._get_result("plex_app_last_marker_result"),
            "pending_play_probes": pending,
        }

    def webhook_payload(self, payload_text: str) -> Dict[str, Any]:
        if not (self.plex_configured() and bool(self._value("webhook_enabled", False))):
            return {"success": False, "error": "Plex App webhook 未启用"}
        try:
            data = json.loads(payload_text or "")
        except (TypeError, ValueError):
            return {"success": False, "error": "payload 非 JSON"}
        event = data.get("event") or ""
        if event not in ("media.play", "media.resume", "media.stop", "media.scrobble"):
            return {"success": True, "skipped": event}
        metadata = data.get("Metadata") or {}
        rating_key = str(metadata.get("ratingKey") or "").strip()
        if not rating_key:
            return {"success": False, "error": "无 ratingKey"}
        if event in ("media.play", "media.resume"):
            metadata_queued = False
            if bool(self._value("play_probe_enabled", True)):
                metadata_queued = self.complete_rating_key(
                    rating_key,
                    source="webhook.play",
                    forward=self._bounded_forward(
                        self._value("play_forward_episodes", 0), 0
                    ),
                )
            marker_queued = self.detect_markers_on_play(
                rating_key, item_type=str(metadata.get("type") or "")
            )
            return {
                "success": True,
                "event": event,
                "ratingKey": rating_key,
                "queued": metadata_queued or marker_queued,
                "metadata_queued": metadata_queued,
                "marker_queued": marker_queued,
            }
        accepted = self.complete_rating_key(
            rating_key, source=f"webhook.{event.rsplit('.', 1)[-1]}"
        )
        return {"success": True, "event": event, "ratingKey": rating_key, "queued": accepted}
