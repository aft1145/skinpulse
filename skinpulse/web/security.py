"""看板的跨站防护（看板改版 spec §6）。

看板只监听 127.0.0.1，但这挡不住浏览器：用户打开的任何网页都能向 127.0.0.1:8766 发请求。
普通表单 POST 不需要 CORS 预检；DNS 重绑定还能让一个恶意域名解析到 127.0.0.1，这时请求
的 Host 仍是那个域名。三条规则，任何一条不满足就 403：

1. Host 必须是本机地址（含端口）——所有请求都查，挡 DNS 重绑定。
2. 写请求（非 GET/HEAD/OPTIONS）带 Origin 时，必须同源。浏览器发起的跨站请求一定带
   Origin；curl 之类不带，放行。
3. 写请求的 Content-Type 必须是 application/json。浏览器发不出不经预检的 JSON 请求，
   所以跨站表单、`no-cors` fetch 全部失效。

每个响应再加三个安全头：nosniff、no-referrer，以及只允许同源的 CSP。三期删掉了旧的 Jinja
页面（它们有内联脚本），CSP 从那以后对所有响应生效。
"""
from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
_WILDCARD_BINDS = frozenset({"0.0.0.0", "::", ""})
# Naive UI 运行时注入 <style>，所以 style-src 要放开 'unsafe-inline'；脚本一律同源、不允许内联。
# ECharts 用 canvas 画，不需要 unsafe-eval。
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = ((b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"),
                    (b"content-security-policy", CSP.encode()))


def allowed_hosts(port: int, bind_host: str) -> frozenset[str]:
    """本机的几种写法；看板绑在某个非回环地址上时（有警告，见 app.py），把它也放进来。"""
    hosts = {f"{h}:{port}" for h in _LOOPBACK_HOSTS}
    if bind_host not in _WILDCARD_BINDS and bind_host not in ("127.0.0.1", "localhost", "::1"):
        h = f"[{bind_host}]" if ":" in bind_host else bind_host
        hosts.add(f"{h}:{port}")
    return frozenset(hosts)


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, hosts: frozenset[str]) -> None:
        self.app = app
        self.hosts = hosts
        self.origins = frozenset(f"http://{h}" for h in hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        reason = self._refusal(scope["method"], headers)
        if reason is not None:
            await _deny(send, reason)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                present = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [
                    (k, v) for k, v in SECURITY_HEADERS if k not in present]
            await send(message)

        await self.app(scope, receive, send_with_headers)

    def _refusal(self, method: str, headers: dict[str, str]) -> str | None:
        if headers.get("host", "") not in self.hosts:
            return "host not allowed"
        if method in _SAFE_METHODS:
            return None
        origin = headers.get("origin")
        if origin is not None and origin not in self.origins:
            return "cross-origin request refused"
        content_type = headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type != "application/json":
            return "only application/json is accepted"
        return None


async def _deny(send: Send, reason: str) -> None:
    body = json.dumps({"detail": reason}).encode()
    await send({"type": "http.response.start", "status": 403,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()), *SECURITY_HEADERS]})
    await send({"type": "http.response.body", "body": body})
