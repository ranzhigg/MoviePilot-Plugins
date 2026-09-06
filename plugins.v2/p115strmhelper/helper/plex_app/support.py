"""Plex App 播放支持的配置、Webhook 和补全任务编排。"""

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
    """提供 Plex App 的 STRM 媒体信息补全能力。"""

    _PREFIX = "plex_app_"

    def __init__(self) -> None:
        self._task_lock = Lock()
        self._trigger_lock = Lock()
        self._recent_triggers: Dict[str, float] = {}
        self._helper_health_failures = 0

    @staticmethod
    def _value(name: str, default: Any = None) -> Any:
        value = configer.get_config(f"plex_app_{name}")
        return default if value is None else value

    @classmethod
    def configured(cls) -> bool:
        """返回是否已启用且具备调用 Plex/Helper 的最低配置。"""
        return bool(
            configer.enabled
            and cls._value("enabled", False)
            and str(cls._value("plex_url", "") or "").strip()
            and str(cls._value("plex_token", "") or "").strip()
            and str(cls._value("helper_url", "") or "").strip()
        )

    @classmethod
    def _selected_sections(cls) -> List[str]:
        raw = str(cls._value("sections", "") or "")
        return [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]

    @classmethod
    def _build_completer(cls, force_write: bool = False) -> Optional[MediaInfoCompleter]:
        if not cls.configured():
            logger.warning("Plex App 补全未启用或配置不完整")
            return None

        plex_url = str(cls._value("plex_url", "") or "").strip()
        token = str(cls._value("plex_token", "") or "").strip()
        helper_url = str(cls._value("helper_url", "") or "").strip()
        helper_token = str(cls._value("helper_token", "") or "").strip()
        if not plex_url.startswith(("http://", "https://")):
            plex_url = "http://" + plex_url

        try:
            timeout = max(1, min(300, int(cls._value("ffprobe_timeout", 40) or 40)))
        except (TypeError, ValueError):
            timeout = 40
        try:
            concurrency = max(1, min(16, int(cls._value("concurrency", 3) or 3)))
        except (TypeError, ValueError):
            concurrency = 3

        plex = PlexClient(plex_url, token)
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
        selected = set(self._selected_sections())
        if not selected:
            logger.info("Plex App 跳过播放补全：未选择媒体库 ratingKey=%s", rating_key)
            return False
        completer = self._build_completer()
        if not completer:
            return False
        section_key = completer._plex.item_section_key(rating_key)
        if section_key not in selected:
            logger.info(
                "Plex App 跳过播放补全：条目不在已选媒体库 ratingKey=%s section=%s",
                rating_key,
                section_key,
            )
            return False
        return True

    def _should_trigger(self, rating_key: str) -> bool:
        now = monotonic()
        try:
            window = max(0, int(self._value("dedup_window", 300) or 300))
        except (TypeError, ValueError):
            window = 300
        with self._trigger_lock:
            for key, stamp in list(self._recent_triggers.items()):
                if now - stamp > window:
                    self._recent_triggers.pop(key, None)
            last = self._recent_triggers.get(rating_key)
            if last is not None and now - last <= window:
                return False
            self._recent_triggers[rating_key] = now
            return True

    def complete_rating_key(self, rating_key: str, source: str = "webhook") -> bool:
        """异步补全单个播放条目及后续少量剧集。"""
        rating_key = str(rating_key or "").strip()
        if not rating_key or not self.configured():
            return False
        if not self._allowed_rating_key(rating_key) or not self._should_trigger(rating_key):
            return False

        def worker() -> None:
            if not self._task_lock.acquire(blocking=False):
                logger.info("Plex App 跳过并发补全 ratingKey=%s", rating_key)
                return
            try:
                completer = self._build_completer(force_write=False)
                if not completer:
                    return
                try:
                    forward = max(0, min(50, int(self._value("forward_episodes", 5) or 5)))
                except (TypeError, ValueError):
                    forward = 5
                summary = completer.run_rating_key(
                    rating_key,
                    only_missing=bool(self._value("only_missing", True)),
                    forward=forward,
                )
                summary.update({"success": True, "source": source, "ts": int(time())})
                self._save_result("plex_app_last_play_result", summary)
                self._log_summary(summary.get("label") or rating_key, summary)
            except Exception as exc:
                logger.error("Plex App 播放补全异常 ratingKey=%s: %s", rating_key, exc, exc_info=True)
            finally:
                self._task_lock.release()

        Thread(target=worker, daemon=True, name="p115-plex-app-completion").start()
        return True

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
        return {
            "success": True,
            "result": self._get_result("plex_app_last_result"),
            "last_play_result": self._get_result("plex_app_last_play_result"),
        }

    def webhook_payload(self, payload_text: str) -> Dict[str, Any]:
        if not (self.configured() and bool(self._value("webhook_enabled", False))):
            return {"success": False, "error": "Plex App webhook 未启用"}
        try:
            data = json.loads(payload_text or "")
        except (TypeError, ValueError):
            return {"success": False, "error": "payload 非 JSON"}
        event = data.get("event") or ""
        if event not in ("media.stop", "media.scrobble"):
            return {"success": True, "skipped": event}
        rating_key = str((data.get("Metadata") or {}).get("ratingKey") or "").strip()
        if not rating_key:
            return {"success": False, "error": "无 ratingKey"}
        accepted = self.complete_rating_key(rating_key, source="webhook")
        return {"success": True, "event": event, "ratingKey": rating_key, "queued": accepted}
