#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""遍历指定 115 目录并清理广告文本附件，默认只列出，--delete 时删除"""
import json
import sqlite3
import sys
import time
import unicodedata
import fcntl
import argparse
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, "/app")

DB_PATH = "/config/user.db"

class SqliteSystemConfigReaderWriter:
    """读取 MP 已保存的存储配置，不在脚本中保存凭证"""
    def __init__(self, db_path: str = DB_PATH) -> None:
        self._db_path = db_path

    def _conn(self):
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _key(key):
        return key.value if hasattr(key, "value") else str(key)

    def get(self, key: Any) -> Any:
        """读取系统配置项"""
        k = self._key(key)
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM systemconfig WHERE key = ?", (k,)).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return row["value"]


def bootstrap() -> None:
    """装配当前 MP V3 的系统配置读取服务"""
    from app.application.configuration import SystemConfigService, configure_system_config
    repo = SqliteSystemConfigReaderWriter()
    service = SystemConfigService(repository=repo)
    configure_system_config(service)


def ad_reason(item: Any) -> Optional[str]:
    """仅匹配广告文本附件，空白和常见繁简字统一后比较"""
    if item.type != "file" or Path(item.name or "").suffix.lower() not in {".txt", ".html", ".htm", ".url"}:
        return None
    name = unicodedata.normalize("NFKC", item.name or "").casefold()
    name = "".join(c for c in name if not c.isspace() and unicodedata.category(c) != "Cf")
    name = name.translate(str.maketrans({"網":"网", "區":"区", "獲":"获"}))
    return next((w for w in ["直播", "收藏不迷路", "最新位址", "最新地址", "聚合全网", "社区最新", "广告"] if w in name), None)


def main() -> int:
    """解析目录并执行限速扫描，存在失败时返回非零状态"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="115 内的目标绝对目录，不允许根目录")
    parser.add_argument("--delete", action="store_true")
    parser.add_argument("--cooldown", type=int, default=0)
    args = parser.parse_args()
    root = Path(args.root)
    if not root.is_absolute() or root == Path("/") or ".." in root.parts:
        parser.error("--root 必须是非根目录的绝对路径，且不能包含 ..")
    if args.cooldown < 0:
        parser.error("--cooldown 不能为负数")
    work = Path("/config/temp/blacklist-cleanup")
    work.mkdir(parents=True, exist_ok=True)
    lock = (work / "scan.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("已有扫描运行，退出", flush=True)
        return 1
    if args.cooldown:
        print("等待 115 冷却", args.cooldown, "秒后低速继续", flush=True)
        time.sleep(args.cooldown)
    bootstrap()
    from app.modules.filemanager.storages.u115 import U115Pan
    original_request = U115Pan._request_api
    last_request = [0.0]
    def paced_request(self, *args, **kwargs):
        time.sleep(max(0.0, 3.0 - (time.monotonic() - last_request[0])))
        try:
            return original_request(self, *args, **kwargs)
        finally:
            last_request[0] = time.monotonic()
    U115Pan._request_api = paced_request
    pan = U115Pan()
    if not pan.check():
        raise RuntimeError("115 登录状态不可用")
    folder = pan.get_item(root)
    if not folder or folder.type != "dir":
        raise RuntimeError("目标目录不存在，停止")
    report = work / (time.strftime("%Y%m%d-%H%M%S") + ".jsonl")
    counts = dict(directories=0, files=0, matched=0, deleted=0, failed=0)
    queue = [folder]
    visited = set()
    print("START", root, "delete=", args.delete, "report=", report, flush=True)
    with report.open("x") as out:
        report.chmod(0o600)
        def record(data):
            out.write(json.dumps(data, ensure_ascii=False) + "\n")
            out.flush()
        while queue:
            parent = queue.pop()
            if parent.fileid in visited:
                continue
            visited.add(parent.fileid)
            if not parent.fileid or not Path(parent.path).is_relative_to(root):
                raise RuntimeError("目录边界校验失败")
            items = None
            for attempt in range(3):
                try:
                    items = pan.list(parent)
                    break
                except Exception:
                    if attempt < 2:
                        time.sleep(2 * (attempt + 1))
            if items is None:
                counts["failed"] += 1
                record(dict(event="list_failed", path=parent.path))
                continue
            counts["directories"] += 1
            for item in items:
                if not Path(item.path).is_relative_to(root):
                    raise RuntimeError("文件边界校验失败")
                if item.type == "dir":
                    queue.append(item)
                    continue
                counts["files"] += 1
                reason = ad_reason(item)
                if not reason:
                    continue
                counts["matched"] += 1
                record(dict(event="candidate", id=item.fileid, parent=parent.fileid, path=item.path, size=item.size, reason=reason))
                if args.delete:
                    try:
                        current = pan.get_item(Path(item.path))
                        if not current or current.fileid != item.fileid or current.size != item.size or not ad_reason(current):
                            raise RuntimeError("删除前文件信息已变化")
                        if not pan.delete(current):
                            raise RuntimeError("删除接口返回失败")
                        remaining = pan.get_item(Path(item.path))
                        if remaining and remaining.fileid == item.fileid:
                            raise RuntimeError("删除后仍存在")
                        counts["deleted"] += 1
                        record(dict(event="deleted", id=item.fileid, path=item.path))
                    except Exception:
                        counts["failed"] += 1
                        record(dict(event="delete_failed", id=item.fileid, path=item.path))
            if counts["directories"] % 50 == 0:
                print("PROGRESS", counts, "pending_dirs=", len(queue), flush=True)
        record(dict(event="summary", **counts))
    print("DONE", counts, "report=", report, flush=True)
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
