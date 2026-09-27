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
import re
import secrets
from typing import Any, Dict, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import configer


_SECRET_KEY = "p115strmhelper_media_proxy_signing_key"
_TOKEN_VERSION = 1
_SECRET_CACHE: Optional[str] = None
_MEDIA_PROXY_ROUTE = re.compile(r"/(redirect_url|media_proxy)(?=/|$)")


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


def build_media_proxy_url(source_url: str) -> str:
    """把插件生成的 redirect_url/旧 media_proxy 地址升级为媒体网关地址。

    这个转换只处理 P115StrmHelper 自己的 URL，并根据 URL 中的资源参数重新签发
    能力令牌。普通 115 CDN 地址、缺少资源参数的自定义 URL 会原样返回空串，
    由调用方决定是否回退到原始地址。
    """
    value = str(source_url or "").strip()
    if not value.startswith(("http://", "https://")):
        return ""
    try:
        parts = urlsplit(value)
        route = _MEDIA_PROXY_ROUTE.search(parts.path)
        if route is None or "/P115StrmHelper/" not in parts.path:
            return ""

        query = parse_qsl(parts.query, keep_blank_values=True)
        values = dict(query)
        pickcode = str(values.get("pickcode") or "").strip()
        share_code = str(values.get("share_code") or "").strip()
        receive_code = str(values.get("receive_code") or "").strip()
        file_id = str(values.get("id") or values.get("file_id") or "").strip()
        token = issue_media_token(
            pickcode=pickcode,
            share_code=share_code,
            receive_code=receive_code,
            file_id=file_id,
        )
        if not token:
            return ""

        path = (
            parts.path[: route.start(0)]
            + "/media_proxy"
            + parts.path[route.end(0) :]
        )
        query = [(key, item) for key, item in query if key != "media_token"]
        query.append(("media_token", token))
        return urlunsplit(
            (parts.scheme, parts.netloc, path, urlencode(query), parts.fragment)
        )
    except (TypeError, ValueError):
        return ""
