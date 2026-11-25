from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

log = logging.getLogger(__name__)

# 只对「连接阶段就失败」的请求重试：这类请求一定没送到 ntfy，重试零风险。
#
# 读超时（ReadTimeout）故意不在里面：那时请求可能已经到了服务端、只是响应没回来，重试就会
# 把同一条告警推两遍。服务端 5xx 同理——不知道它存没存下来。
#
# 2026-09-22 夜里实测的三次投递失败全是 ConnectError（从国内连 ntfy.sh 本来就是条细管子，
# 平时单次请求就要 5 秒左右），正好落在这个集合里。
_RETRYABLE = (httpx.ConnectError, httpx.ConnectTimeout)


class NtfyError(Exception):
    pass


@dataclass
class NtfyMessage:
    title: str
    body: str
    priority: int = 3
    tags: list[str] = field(default_factory=list)
    click: str | None = None
    # PushPlus 的 html 模板正文（简报表格用）；ntfy 没有对应模板，publish() 不读这个字段，
    # 永远只发 body。
    html: str | None = None


def _rfc2047(s: str) -> str:
    if s.isascii():
        return s
    return "=?UTF-8?B?" + base64.b64encode(s.encode("utf-8")).decode("ascii") + "?="


class NtfyClient:
    def __init__(self, base_url: str, topic: str, token: str | None = None, http: httpx.Client | None = None,
                 *, retries: int = 2, backoff_s: tuple[float, ...] = (3.0, 10.0),
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.url = base_url.rstrip("/") + "/" + topic
        self.token = token
        self.http = http or httpx.Client(timeout=15, trust_env=False)  # 不读代理变量，见 ADR-0011
        self._topic = topic
        self.retries = retries
        self.backoff_s = backoff_s
        self.sleep = sleep

    def publish(self, msg: NtfyMessage) -> None:
        headers = {"Title": _rfc2047(msg.title), "Priority": str(msg.priority)}
        if msg.tags:
            headers["Tags"] = ",".join(msg.tags)
        if msg.click:
            headers["Click"] = msg.click
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        body = msg.body.encode("utf-8")
        for attempt in range(self.retries + 1):
            try:
                r = self.http.post(self.url, content=body, headers=headers)
                break
            except _RETRYABLE as e:
                if attempt >= self.retries:
                    raise
                wait = self.backoff_s[min(attempt, len(self.backoff_s) - 1)]
                # 只记异常类型：传输层异常的消息里带着完整 URL，而 URL 里就是 topic。
                log.warning("ntfy connect failed (%s); retrying in %ss (%d/%d)",
                            type(e).__name__, wait, attempt + 1, self.retries)
                self.sleep(wait)
        if r.status_code // 100 != 2:
            # The topic is effectively a password on a public ntfy server, and a
            # reverse proxy or non-standard deployment can echo the request path
            # (which contains it) back in an error page. Redact it before it can
            # end up in an exception that a caller might log to disk.
            text = r.text.replace(self._topic, "<topic>") if self._topic else r.text
            raise NtfyError(f"ntfy {r.status_code}: {text[:200]}")
