"""STRM 文件发现与 NFO 通知内容解析"""

import os
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse
from xml.etree import ElementTree


def scan(root: Path) -> Dict[str, float]:
    """完整扫描目录，失败时抛错以保留上次记录，不跟随目录符号链接"""
    if not root.is_dir():
        raise OSError("监控目录不可用")
    result = {}

    def fail(error):
        raise error

    for directory, _, files in os.walk(root, followlinks=False, onerror=fail):
        for name in files:
            path = Path(directory) / name
            if path.suffix.lower() == ".strm" and not path.is_symlink():
                result[str(path)] = path.stat().st_mtime
    return result


def metadata(path: Path, now: float) -> Optional[dict]:
    """优先匹配同名 NFO，单文件电影目录才允许使用 movie.nfo"""
    nfo = path.with_suffix(".nfo")
    if not nfo.is_file():
        siblings = [p for p in path.parent.iterdir() if p.suffix.lower() == ".strm"]
        if len(siblings) != 1:
            return None
        nfo = path.parent / "movie.nfo"
    if not nfo.is_file():
        return None
    stat = nfo.stat()
    if now - stat.st_mtime < 2 or stat.st_size > 2 * 1024 * 1024:
        return None
    raw = nfo.read_bytes()
    try:
        raw = raw.decode("utf-8-sig").encode("utf-8")
    except UnicodeDecodeError:
        return None
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        return None
    try:
        tree = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return None
    if tree.tag not in {"movie", "episodedetails", "musicvideo"}:
        return None

    def value(tag):
        return (tree.findtext(tag) or "").strip()

    title = value("title")
    if not title:
        return None
    image = value("cover") or value("thumb") or value("fanart/thumb")
    if urlparse(image).scheme not in {"http", "https"}:
        image = None
    return {
        "title": title,
        "year": value("year"),
        "actors": [n.text.strip() for n in tree.findall("actor/name") if n.text][:5],
        "tags": [n.text.strip() for n in tree.findall("tag") if n.text][:6],
        "image": image,
        "file": path.name,
    }


def reconcile(previous: Optional[dict], files: Dict[str, float], now: float) -> dict:
    """首次仅建立基线，后续只为新增路径加入待通知队列"""
    if previous is None:
        return {"known": sorted(files), "pending": {}}
    known = set(previous.get("known", []))
    pending = {p: t for p, t in previous.get("pending", {}).items() if p in files}
    for path in files.keys() - known:
        pending[path] = now
    return {"known": sorted(files), "pending": pending}
