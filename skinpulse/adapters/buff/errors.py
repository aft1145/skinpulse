"""BUFF 的错误（BUFF spec §3，调研 §4、§5）。

BUFF 和悠悠一样 HTTP 多半是 200、成败看 `code`；例外是限流直接 HTTP 429 + nginx 的 HTML 页。
异常消息里只放业务码和 BUFF 给的中文说明，**绝不放 cookie、URL**（URL 里有参数）。"""
from __future__ import annotations


class BuffError(Exception):
    def __init__(self, code: str, msg: str = "") -> None:
        super().__init__(f"buff {code}: {msg}" if msg else f"buff {code}")
        self.code = str(code)
        self.msg = msg


class BuffLoginRequired(BuffError):
    """`code: "Login Required"`：session 失效或没登录。"""


class BuffRateLimited(BuffError):
    """限流、验证码、「系统繁忙」、403 / 412：这个号暂停 `buff.pause_hours`，不重试。"""


CODE_OK = "OK"
CODE_LOGIN = "Login Required"
CODE_CAPTCHA = "Captcha Validate Required"
# 正文里出现这些字就当风控（Steamauto、AetherSwap 的经验，调研 §4）
RISK_WORDS = ("系统繁忙", "请求过于频繁", "人机验证", "页面已过期", "访问异常")
RISK_STATUS = frozenset({403, 412, 429})


def raise_for_code(code, msg: str = "") -> None:
    code = str(code or "")
    if code == CODE_OK:
        return
    if code == CODE_LOGIN:
        raise BuffLoginRequired(code, msg)
    if code == CODE_CAPTCHA or any(w in (msg or "") for w in RISK_WORDS):
        raise BuffRateLimited(code, msg)
    raise BuffError(code or "unknown", msg)
