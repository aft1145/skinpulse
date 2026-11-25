"""PushPlus（推送加）：把告警以微信公众号消息的形式送到手机。

为什么要有这条通道：2026-09-23 查实，ntfy 的推送一直没有实时到过手机——国内 FCM 不通，
ntfy App 要实时收就得常驻后台，而用户不打算给后台/自启动权限；消息在 ntfy.sh 上只存 12 小时，
App 没在 12 小时内被打开就永久丢了（库里标成 sent 的 20 次推送，手机上只看到 2 次）。微信本来
就在手机上按时收消息、厂商对它放行，借它的通道不需要任何新权限。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://www.pushplus.plus"
PATH_SEND = "/send"

# 只对「连接阶段就失败」的请求重试——理由和 ntfy 客户端完全一样：这类请求一定没到服务端，
# 重试不会造成重复推送；读超时/5xx 时消息可能已经落地，重试就可能推两遍。
_RETRYABLE = (httpx.ConnectError, httpx.ConnectTimeout)


class PushPlusError(Exception):
    def __init__(self, code: str, msg: str = "", detail: str = "") -> None:
        super().__init__(f"pushplus {code}: {msg}{'（' + detail + '）' if detail else ''}")
        self.code = str(code)
        self.msg = msg
        self.detail = detail


class PushPlusAuthError(PushPlusError):
    """903：token 不对。"""


class PushPlusNeedsRealName(PushPlusError):
    """905：账号还没实名认证，PushPlus 不给发。只能去官网完成认证。"""


class PushPlusRestricted(PushPlusError):
    """900：账号被限制使用（通常是一天请求超过 1000 次，或被封 7 天）。

    官方说明：收到 900 就该**停止请求**，继续发只会延长限制；停手约 2 天自动恢复。
    所以这个错误绝不能重试。"""


_CODE_MAP: dict[str, type[PushPlusError]] = {
    "903": PushPlusAuthError,
    "905": PushPlusNeedsRealName,
    "900": PushPlusRestricted,
}


def _raise_for_envelope(body: dict) -> str:
    """按 PushPlus 的信封判定成败，成功时返回 shortCode（消息流水号）。

    **HTTP 永远是 200**（2026-09-23 实测：不带 token、乱填 token 都回 200），成败只看 `code`。
    这是本项目第三次遇到这种服务（悠悠、SteamDT 之后），按状态码判断成败在这里一定错。

    出错时真正的细节在 `data` 里（比如「无效的用户token」），`msg` 只是类别（「用户令牌不正确」）。
    """
    code = str(body.get("code"))
    if code == "200":
        return str(body.get("data") or "")
    msg = str(body.get("msg") or "")
    detail = str(body.get("data") or "")
    raise _CODE_MAP.get(code, PushPlusError)(code, msg, detail)


class PushPlusClient:
    """发一条消息到用户的微信。

    **返回 code 200 只代表 PushPlus 收下了，不代表已经送到微信**——官方文档原话是同步返回只确认
    接收，实际发送状态要拿返回的 shortCode 另外查。这正是 ntfy 那边吃过的亏（HTTP 200 只说明
    ntfy.sh 收下了，手机上一次没收到过）。发送状态的查询接口属于 PushPlus 的「开放接口」，要另外
    申请 secretKey 并绑定 IP 白名单，暂时没接；目前靠 `pushplus-token` 设置时发一条测试消息、
    用户在微信里亲眼确认来验证整条链路。
    """

    def __init__(self, token: str, *, base_url: str = BASE_URL, http: httpx.Client | None = None,
                 retries: int = 2, backoff_s: tuple[float, ...] = (3.0, 10.0),
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.token = token
        self.url = base_url.rstrip("/") + PATH_SEND
        self.http = http or httpx.Client(timeout=20, trust_env=False)  # 不读代理变量，见 ADR-0011
        self.retries = retries
        self.backoff_s = backoff_s
        self.sleep = sleep

    def publish(self, msg) -> str:
        """发送 `msg`（title / body / html 三个属性，即 NtfyMessage），返回 shortCode。

        默认用 txt 模板：饰品名里有 `|`、`★`、`™`，html 模板会把 `<` `&` 当标签吃掉或渲染错。
        `msg.html` 非空时改用 html 模板发 `msg.html`——调用方必须已经对内容做过 `html.escape`，
        这里不做任何转义或校验。
        """
        if msg.html:
            payload: dict[str, Any] = {"token": self.token, "title": msg.title, "content": msg.html,
                                       "template": "html"}
        else:
            payload = {"token": self.token, "title": msg.title, "content": msg.body,
                       "template": "txt"}
        for attempt in range(self.retries + 1):
            try:
                r = self.http.post(self.url, json=payload)
                break
            except _RETRYABLE as e:
                if attempt >= self.retries:
                    raise
                wait = self.backoff_s[min(attempt, len(self.backoff_s) - 1)]
                log.warning("pushplus connect failed (%s); retrying in %ss (%d/%d)",
                            type(e).__name__, wait, attempt + 1, self.retries)
                self.sleep(wait)
        try:
            body = r.json()
        except ValueError:
            # 不回显响应体：token 在请求体里，网关错误页理论上可能把请求回显出来。
            raise PushPlusError(f"http_{r.status_code}", "响应不是 JSON") from None
        return _raise_for_envelope(body)
