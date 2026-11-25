"""录入 PushPlus token 的共用部分：命令行 `pushplus-token` 和看板「录入 PushPlus token」（首次使用 spec §2.4）。

检查格式 → 发一条测试消息 → 按失败原因分类。保存和接入推送器由调用方做（命令行存钥匙串；看板还要把
通道原地加进正在跑的推送器）。token 本身绝不进日志和错误消息。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from skinpulse.adapters.ntfy.client import NtfyMessage
from skinpulse.adapters.pushplus.client import (
    PushPlusAuthError, PushPlusClient, PushPlusError, PushPlusNeedsRealName, PushPlusRestricted,
)

# 格式问题（不发请求）
EMPTY = "empty"
CONTROL = "control"
# 发了测试消息之后
AUTH = "auth"              # 903：token 不对
REALNAME = "realname"      # 905：要先实名
RESTRICTED = "restricted"  # 900：账号被限制
REJECTED = "rejected"      # 其它业务错误码
NETWORK = "network"        # 连不上


def check_token_text(raw: str) -> tuple[str | None, str | None]:
    """返回 (token, None) 或 (None, 原因)。首尾空白去掉；中间有空白、有看不见的控制字符都不收。

    控制字符：Windows 上在不回显的输入框里按 Ctrl+V，有时进来的是 SYN（Ctrl+V 本身），发出去只会得到 903，
    而 903 的字面意思「token 不对」会把人引去反复核对一个其实没问题的 token。"""
    token = raw.strip()
    if not token or any(ch.isspace() for ch in token):
        return None, EMPTY
    if not all(ch.isprintable() for ch in token):
        return None, CONTROL
    return token, None


def probe_message(tz: str, now: datetime | None = None) -> NtfyMessage:
    at = (now or datetime.now(ZoneInfo(tz))).astimezone(ZoneInfo(tz)).strftime("%m-%d %H:%M")
    # 故意写成多行、中间空一行：顺便确认 txt 模板下换行在微信里能正常显示——告警正文就靠它分段。
    return NtfyMessage(title="SkinPulse 通道测试",
                       body="看到这条，说明 PushPlus 通道通了。\n\n"
                            "这一行和上一行之间应该空着一行，用来确认换行能正常显示。\n\n"
                            f"触发于 {at}")


@dataclass
class ProbeResult:
    ok: bool
    reason: str | None = None
    detail: str | None = None      # REJECTED 时是 PushPlus 的错误码和说明（不含 token）
    short_code: str | None = None


def classify(e: Exception) -> tuple[str, str | None]:
    if isinstance(e, PushPlusAuthError):
        return AUTH, None
    if isinstance(e, PushPlusNeedsRealName):
        return REALNAME, None
    if isinstance(e, PushPlusRestricted):
        return RESTRICTED, None
    if isinstance(e, PushPlusError):
        return REJECTED, str(e)
    return NETWORK, type(e).__name__


def send_probe(token: str, tz: str, *, client_factory=PushPlusClient, now: datetime | None = None) -> ProbeResult:
    try:
        code = client_factory(token).publish(probe_message(tz, now))
    except Exception as e:  # noqa: BLE001 - 连不上、超时都算「暂时验证不了」，分类见 classify
        reason, detail = classify(e)
        return ProbeResult(False, reason, detail)
    return ProbeResult(True, short_code=code)


def token_shape(token: str) -> str:
    """只说位数和字符类型，不说 token 本身：够用户拿去和网页上的对比，又不会泄露它。"""
    kinds = []
    if any(c.isdigit() for c in token):
        kinds.append("数字")
    if any(c.isascii() and c.isalpha() for c in token):
        kinds.append("英文字母")
    if any(not (c.isascii() and c.isalnum()) for c in token):
        kinds.append("其它符号")
    return f"{len(token)} 位，包含{'、'.join(kinds)}"
