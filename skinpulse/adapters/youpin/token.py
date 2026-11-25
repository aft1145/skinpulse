"""悠悠 token 的两个小工具：粘贴时的规范化，和从 token 里读到期时间（看板改版 spec §5）。

token 是 JWT，payload 里的 `exp` 就是到期时间（勾「10 天免登录」时是签发后 10 天，见
research §2）。这里只解 payload、不验签——只是拿来显示「还剩几天」，信不信由服务端说了算。
读不出来一律返回 None：不抛、不记日志，token 的任何片段都不能出现在日志里。
"""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone


def jwt_expiry(token: str | None) -> datetime | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload))["exp"]
        if isinstance(exp, bool) or not isinstance(exp, (int, float)):
            return None
        return datetime.fromtimestamp(exp, tz=timezone.utc)
    except Exception:  # noqa: BLE001 - 任何解析失败都等价于「不知道」
        return None


# 从 devtools 里复制 token 时很容易把它所在的那个名字一起框进去：Application -> Cookies 那栏
# 复制出来是 `uu_token=eyJ...`，Headers 那栏则是 `authorization: eyJ...` 或 `authorization:
# Bearer eyJ...`。这条正则把"名字 + 分隔符（+ Bearer）"整段切掉，但只在后面紧跟着 `eyJ` 时才动手
# ——`eyJ` 是 JWT 的固定开头（base64url 编码的 `{"`）。有这个前瞻在，一个干净的 token 不可能被从
# 中间劈开：JWT 用 `.` 分段、不含 `:`，`=` 只会作为 base64 填充出现在段尾，后面跟的是 `.` 或另一
# 个 `=`，绝不会是 `eyJ`。
_NAME_PREFIX_RE = re.compile(r'^[^\s=;:]{1,64}\s*[=:]\s*(?:bearer\s+)?(?=eyJ)', re.IGNORECASE)


def normalize_token(raw: str) -> str:
    """去掉粘贴时常见的干扰：首尾空白、包裹的引号、`uu_token=` / `authorization:` 这类名字前缀，
    以及 `YoupinClient` 自己会在请求头里加的 'Bearer ' 前缀（大小写不敏感）。

    token 的正规来源是 devtools 里某个 pc-api 请求的 `authorization` 请求头（见 README），而那
    一带的界面全都是"名字 + 值"成对显示的，连名字一起复制是最常见的手滑。"""
    t = raw.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        t = t[1:-1].strip()
    t = _NAME_PREFIX_RE.sub("", t, count=1).strip()
    if t[:7].casefold() == "bearer ":
        t = t[7:].strip()
    return t
