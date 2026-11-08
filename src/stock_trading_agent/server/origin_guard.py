"""Origin 守卫中间件（CSRF 防御，R2 收严向覆盖全部非 GET）。

做法：非 GET 且非 /api/stream（SSE 长连无写语义）→ Origin（缺失回落 Referer 的 origin 部分）
皆缺或跨源 → 403；host 白名单 {127.0.0.1, localhost, [::1], testserver}（任意端口——本机
多端口面板互认）。坑位：htmx 同源 XHR 按 fetch 规范必带 Origin（面板自用零影响）；
curl 脚本须显式 `-H 'Origin: http://127.0.0.1:8730'`（CHANGELOG+规约登记）。
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

ALLOWED_ORIGIN_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]", "testserver"})
EXEMPT_PATHS = frozenset({"/api/stream"})  # SSE 长连（GET 且无写语义，豁免开销）


def origin_allowed(headers: Mapping[str, str]) -> bool:
    """纯函数：headers 里 Origin（缺失回落 Referer）的 host 是否在白名单。"""
    raw = headers.get("origin") or headers.get("Origin") or ""
    if not raw:
        referer = headers.get("referer") or headers.get("Referer") or ""
        if not referer:
            return False  # 两者皆缺：按跨源拒（curl POST 须显式带 Origin）
        raw = referer
    try:
        parts = urlsplit(raw)
        host = parts.hostname or ""
        port = parts.port
        host_token = f"[{host}]" if ":" in host else host  # IPv6 形态归一
        return (host_token in ALLOWED_ORIGIN_HOSTS) or (
            host in ALLOWED_ORIGIN_HOSTS and port is not None
        )
    except ValueError:
        return False


class OriginGuardMiddleware:
    """纯 ASGI 中间件：非 GET 写动词 Origin 校验，403 JSON。"""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            path = scope.get("path", "")
            if path not in EXEMPT_PATHS:
                headers = {
                    k.decode("latin-1").lower(): v.decode("latin-1")
                    for k, v in scope.get("headers", [])
                }
                if not origin_allowed(headers):
                    await self._forbid(send)
                    return
        await self.app(scope, receive, send)

    @staticmethod
    async def _forbid(send) -> None:
        import json as _json

        detail = "Origin 校验失败（本地面板写动词须带同源 Origin 头）"
        body = _json.dumps({"detail": detail}, ensure_ascii=True).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
