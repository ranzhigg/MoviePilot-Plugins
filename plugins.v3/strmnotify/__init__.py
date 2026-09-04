"""按目录发现新 STRM 并逐条发送媒体通知"""

from pathlib import Path
from threading import RLock
from time import time
from typing import Any, Dict, List, Tuple

from apscheduler.triggers.interval import IntervalTrigger

from app.plugins import _PluginBase
from app.schemas import MessageChannel, NotificationType
from app.sdk.logging import logger

from .engine import metadata, reconcile, scan


class StrmNotify(_PluginBase):
    """STRM逐条通知"""

    plugin_name = "STRM逐条通知"
    plugin_desc = "监控指定目录中的新 STRM，等待对应 NFO 后发送逐条媒体通知"
    plugin_icon = "https://raw.githubusercontent.com/ranzhigg/MoviePilot-Plugins/main/icons/StrmNotify.svg"
    plugin_version = "3.0.2"
    plugin_author = "ranzhigg"
    author_url = "https://github.com/ranzhigg"
    plugin_config_prefix = "strmnotify_"
    plugin_order = 50
    auth_level = 1

    def __init__(self):
        super().__init__()
        self._lock = RLock()
        self._enabled = False
        self._config = {}
        self._roots = []

    def init_plugin(self, config: dict = None):
        """载入配置，新增目录首次扫描时只建立基线"""
        with self._lock:
            self._config = dict(config or {})
            self._enabled = bool(self._config.get("enabled", False))
            roots = []
            for line in str(self._config.get("paths") or "").splitlines():
                path = Path(line.strip())
                if line.strip() and path.is_absolute():
                    roots.append(path.resolve())
            self._roots = [p for p in sorted(set(roots)) if not any(q in p.parents for q in roots)]

    def get_state(self) -> bool:
        """返回插件启用状态"""
        return self._enabled

    def _number(self, key, default, low, high):
        try:
            return max(low, min(high, int(self._config.get(key, default))))
        except (TypeError, ValueError):
            return default

    def get_service(self) -> List[Dict[str, Any]]:
        """注册单实例周期扫描任务"""
        if not self._enabled or not self._roots:
            return []
        return [{"id": "StrmNotify_scan", "name": "STRM逐条通知扫描",
                 "trigger": IntervalTrigger(seconds=self._number("interval", 30, 10, 3600)),
                 "func": self.poll, "kwargs": {}}]

    def poll(self) -> None:
        """按目录保存去重状态，限制每轮通知数量，错误时保留重试队列"""
        if not self._lock.acquire(blocking=False):
            return
        try:
            if not self._enabled:
                return
            state = self.get_data("state") or {}
            budget = self._number("batch", 10, 1, 100)
            wait = self._number("wait", 120, 10, 86400)
            now = time()
            for root in self._roots:
                key = str(root)
                try:
                    files = scan(root)
                except OSError:
                    logger.warning("STRM逐条通知：目录不可用，本轮保留原记录")
                    continue
                updated = reconcile(state.get(key), files, now)
                if updated != state.get(key):
                    state[key] = updated
                    self.save_data("state", state)
                dirty = False
                pending = state[key]["pending"]
                for name, started in list(pending.items()):
                    try:
                        info = metadata(Path(name), now)
                        if info is None:
                            if now - started >= wait:
                                del pending[name]
                                dirty = True
                                logger.info("STRM逐条通知：等待有效 NFO 超时，跳过一条通知")
                            continue
                        if budget <= 0:
                            continue
                        lines = [info["title"]]
                        for field, label in [("year", "年份"), ("actors", "演员"), ("tags", "标签")]:
                            if self._config.get(field, True) and info[field]:
                                value = info[field]
                                lines.append(f"{label}：{', '.join(value) if isinstance(value, list) else value}")
                        if self._config.get("filename", True):
                            lines.append(f"文件：{info['file']}")
                        selected = self._config.get("channel")
                        channel = MessageChannel(selected) if selected else None
                        self.post_message(channel=channel, mtype=NotificationType.Plugin,
                                          title=f"{info['title']} 已入库", text="\n".join(lines),
                                          image=info["image"] if self._config.get("image", True) else None)
                        del pending[name]
                        budget -= 1
                        self.save_data("state", state)
                        dirty = False
                    except Exception as error:
                        logger.warning(f"STRM逐条通知：处理失败，将重试（{type(error).__name__}）")
                if dirty:
                    self.save_data("state", state)
        finally:
            self._lock.release()

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """返回通用媒体通知设置"""
        fields = []
        for key, label in [("enabled", "启用插件"), ("image", "显示封面"),
                           ("year", "显示年份"), ("actors", "显示演员"),
                           ("tags", "显示标签"), ("filename", "显示文件名")]:
            fields.append({"component": "VSwitch", "props": {"model": key, "label": label}})
        fields.append({"component": "VTextarea", "props": {"model": "paths", "label": "监控目录（MP 容器内绝对路径，每行一个）"}})
        for key, label in [("interval", "扫描间隔（秒，10–3600）"), ("wait", "等待 NFO（秒，10–86400）"), ("batch", "每轮最多通知数（1–100）")]:
            fields.append({"component": "VTextField", "props": {"model": key, "label": label, "type": "number"}})
        fields.append({"component": "VSelect", "props": {"model": "channel", "label": "通知渠道",
                       "items": [{"title": "跟随 MP 通知配置", "value": ""}] +
                                [{"title": x.name, "value": x.value} for x in MessageChannel]}})
        fields.append({"component": "VAlert", "props": {"type": "info", "variant": "tonal"},
                       "content": [{"component": "span", "text": "首次扫描只记录存量文件；重写已有路径不重复通知。匹配同名 NFO，单 STRM 目录可使用 movie.nfo。启用前请停用其他插件的同类逐条通知。通知表示 STRM 与 NFO 已就绪，不代表媒体服务器完成扫描。"}]})
        return [{"component": "VForm", "content": fields}], {
            "enabled": False, "paths": "", "interval": 30, "wait": 120, "batch": 10,
            "channel": "", "image": True, "year": True, "actors": True, "tags": True, "filename": True}

    def get_page(self) -> List[dict]:
        """展示当前配置和最近扫描记录，不额外扫描或发送通知"""
        with self._lock:
            enabled = self._enabled
            roots = list(self._roots)
            config = dict(self._config)
            state = self.get_data("state") or {}
            interval = self._number("interval", 30, 10, 3600)
            wait = self._number("wait", 120, 10, 86400)
            batch = self._number("batch", 10, 1, 100)
        active = enabled and bool(roots)
        status = "已启用" if active else ("尚未配置监控目录" if enabled else "未启用")
        known = sum(len((state.get(str(root)) or {}).get("known", [])) for root in roots)
        pending = sum(len((state.get(str(root)) or {}).get("pending", {})) for root in roots)
        def line(text):
            return {"component": "div", "props": {"class": "mb-2", "style": "overflow-wrap: anywhere"}, "text": text}
        content = [
            {"component": "VAlert", "props": {"type": "success" if active else "info", "variant": "tonal", "class": "mb-4"},
             "text": f"{status} · 每 {interval} 秒扫描一次"},
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 6}, "content": [line(f"已记录 STRM：{known}")]},
                {"component": "VCol", "props": {"cols": 6}, "content": [line(f"待处理通知：{pending}")]},
            ]},
            {"component": "h3", "props": {"class": "mb-3"}, "text": "监控目录"},
        ]
        for root in roots:
            record = state.get(str(root))
            detail = "等待首次扫描建立基线" if record is None else f"已记录 {len(record.get('known', []))} 个文件 · 待处理 {len(record.get('pending', {}))} 条"
            content.append({"component": "VCard", "props": {"variant": "outlined", "class": "mb-3"},
                            "content": [{"component": "VCardText", "content": [line(str(root)), line(detail)]}]})
        if not roots:
            content.append(line("请点右下角齿轮，设置监控目录并启用插件"))
        content.extend([
            {"component": "h3", "props": {"class": "my-3"}, "text": "通知设置"},
            line(f"等待 NFO：{wait} 秒 · 每轮最多 {batch} 条"),
            line(f"通知渠道：{config.get('channel') or '跟随 MP 通知配置'}"),
            line("显示内容：" + ("、".join(label for key, label in [("image", "封面"), ("year", "年份"),
                 ("actors", "演员"), ("tags", "标签"), ("filename", "文件名")] if config.get(key, True)) or "仅标题")),
            {"component": "VAlert", "props": {"type": "info", "variant": "tonal", "class": "mt-4"},
             "text": "首次扫描只记录存量文件，新增 STRM 与 NFO 就绪后才通知。以上数量来自最近保存的扫描记录，不代表媒体服务器已入库。修改配置请点右下角齿轮，重新打开此页可刷新数据。"},
        ])
        return [{"component": "VCardText", "content": content}]

    def get_api(self) -> List[dict]:
        """无额外 API"""
        return []

    @staticmethod
    def get_command() -> List[dict]:
        """无聊天命令"""
        return []

    def stop_service(self) -> None:
        """等待本轮扫描结束并停用处理"""
        with self._lock:
            self._enabled = False
