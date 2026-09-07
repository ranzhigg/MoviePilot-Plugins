#!/usr/bin/env python3
"""Pin and restore the forked P115StrmHelper release in MoviePilot.

This deployment helper pins both the repository and release version. It is
safe to keep outside the plugin package and restore after a container rebuild;
it never prints the MoviePilot API key.
"""

import argparse
import json
import os
import re
import urllib.request
from pathlib import Path

from dotenv import dotenv_values


REPO = "https://github.com/ranzhigg/MoviePilot-Plugins"
VERSION = "2.8.86"
ROOT = Path(os.environ.get("P115_PLUGIN_ROOT", "/app/app/plugins/p115strmhelper"))
REQUIRED_MARKERS = {
    "helper/offline/__init__.py": (
        "offline_direct_path_queue",
        "track_direct",
        "直存任务已生成 STRM",
    ),
}


def _api(path, body=None):
    config = dotenv_values("/config/app.env")
    key = os.environ.get("API_TOKEN") or config.get("API_TOKEN")
    if not key:
        raise RuntimeError("未找到现有 MP API 凭据")
    request = urllib.request.Request(
        "http://127.0.0.1:3001/api/v1/" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=240) as response:
        result = json.load(response)
    if isinstance(result, dict) and result.get("success") is False:
        raise RuntimeError(result.get("message") or "MP 操作失败")
    return result


def _version_from_source() -> str:
    version_file = ROOT / "version.py"
    match = re.search(
        r'\bVERSION\s*=\s*["\']([^"\']+)["\']', version_file.read_text()
    )
    if not match:
        raise RuntimeError(f"未找到插件版本：{version_file}")
    return match.group(1)


def verify() -> bool:
    actual = _version_from_source()
    missing = []
    if actual != VERSION:
        missing.append(f"version.py={actual}")
    for relative, markers in REQUIRED_MARKERS.items():
        path = ROOT / relative
        text = path.read_text() if path.is_file() else ""
        for marker in markers:
            if marker not in text:
                missing.append(f"{relative}:{marker}")
    result = {
        "repo": REPO,
        "expected_version": VERSION,
        "actual_version": actual,
        "missing": missing,
    }
    print(json.dumps(result, ensure_ascii=False))
    return not missing


def _compact_result(result):
    if not isinstance(result, dict):
        return {"result_type": type(result).__name__}
    return {
        key: result.get(key)
        for key in ("code", "msg", "success")
        if key in result
    }


def install() -> bool:
    options = _api("plugin/source/P115StrmHelper/options").get("data", {})
    candidates = options.get("candidates", [])
    if not any(
        str(candidate.get("repo_url", "")).rstrip("/").lower() == REPO.lower()
        for candidate in candidates
    ):
        raise RuntimeError("当前市场未找到指定 fork，未修改插件来源")

    identity = options.get("identity") or {}
    body = {"repo_url": REPO, "release_version": VERSION}
    trusted_key = str(identity.get("trusted_source_key") or "").lower()
    if (
        identity.get("trusted_source_type") not in (None, "unknown")
        and trusted_key != "github:ranzhigg/moviepilot-plugins"
    ):
        if identity.get("revision"):
            body["expected_revision"] = identity["revision"]
        result = _api("plugin/source/P115StrmHelper", body)
    else:
        body["force"] = bool(
            identity.get("trusted_source_type") not in (None, "unknown")
        )
        result = _api("plugin/source/P115StrmHelper/install", body)
    print(json.dumps(_compact_result(result), ensure_ascii=False))
    return verify()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("verify", "install"))
    args = parser.parse_args()
    ok = install() if args.action == "install" else verify()
    raise SystemExit(0 if ok else 1)
