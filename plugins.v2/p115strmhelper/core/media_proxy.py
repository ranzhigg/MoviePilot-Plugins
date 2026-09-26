"""媒体代理能力令牌

令牌只绑定一个 115 资源标识，不复用 MoviePilot 全局 API Token
STRM 文件可以安全地携带该令牌，服务端仍会在代理入口验证签名
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from typing import Any, Dict, Mapping, Optional

from .config import configer


_SECRET_KEY = "p115strmhelper_media_proxy_signing_key"
_TOKEN_VERSION = 1
_SECRET_CACHE: Optional[str] = None


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _secret() -> bytes:
    """读取或创建插件级签名密钥，密钥只存本机插件数据，不进入 STRM"""
    global _SECRET_CACHE
    if _SECRET_CACHE:
        return _SECRET_CACHE.encode("utf-8")
    try:
        value = configer.get_plugin_data(key=_SECRET_KEY)
    except Exception:
        value = None
    if not value:
        value = secrets.token_urlsafe(32)
        try:
            configer.save_plugin_data(key=_SECRET_KEY, value=value)
        except Exception:
            # 配置存储不可用时不返回固定后门密钥，令牌会在本次调用后失效
            pass
    _SECRET_CACHE = str(value)
    return _SECRET_CACHE.encode("utf-8")


def _payload_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def issue_media_token(
    *,
    pickcode: str = "",
    share_code: str = "",
    receive_code: str = "",
    file_id: str = "",
) -> str:
    """签发绑定到单个普通文件或分享文件的能力令牌"""
    if pickcode:
        payload: Dict[str, Any] = {
            "v": _TOKEN_VERSION,
            "kind": "pickcode",
            "pickcode": pickcode.lower(),
        }
    elif share_code and receive_code and str(file_id):
        payload = {
            "v": _TOKEN_VERSION,
            "kind": "share",
            "share_code": share_code,
            "receive_code": receive_code,
            "file_id": str(file_id),
        }
    else:
        return ""
    body = _b64encode(_payload_bytes(payload))
    signature = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64encode(signature)}"


def verify_media_token(
    token: str,
    *,
    pickcode: str = "",
    share_code: str = "",
    receive_code: str = "",
    file_id: str = "",
) -> Optional[Dict[str, Any]]:
    """验证令牌签名及其绑定资源，失败返回 None"""
    try:
        body, signature = str(token or "").split(".", 1)
        expected = hmac.new(
            _secret(), body.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(_b64decode(signature), expected):
            return None
        payload = json.loads(_b64decode(body).decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("v") != _TOKEN_VERSION:
            return None
        if payload.get("kind") == "pickcode":
            if str(payload.get("pickcode") or "").lower() != str(pickcode or "").lower():
                return None
        elif payload.get("kind") == "share":
            if (
                str(payload.get("share_code") or "") != str(share_code or "")
                or str(payload.get("receive_code") or "") != str(receive_code or "")
                or str(payload.get("file_id") or "") != str(file_id or "")
            ):
                return None
        else:
            return None
        return payload
    except (
        TypeError,
        ValueError,
        UnicodeError,
        binascii.Error,
        json.JSONDecodeError,
    ):
        return None
