from __future__ import annotations

import uuid
from typing import Any

from skinpulse.adapters.youpin.client import YoupinClient
from skinpulse.adapters.youpin.errors import YoupinError

# PC 网页端的短信登录接口。旧的 /api/user/Auth/SendSignInSmsCode + /api/user/Auth/SmsSignIn
# 走 App 渠道，已被服务端版本网关卡死（HTTP 429 + code 85100），改走 PC 站自己用的这两个接口。
PATH_SEND_SMS = "/api/youpin/userAuth/smsLogin/sendSmsCode"
PATH_SMS_SIGN_IN = "/api/youpin/userAuth/smsLogin/doLogin"

_TOKEN_KEYS = ("Token", "token", "accessToken")
_DATA_KEYS = ("data", "Data")


def _extract_token(payload: Any) -> str | None:
    """从 doLogin 的（已经过 _unwrap 剥掉外层 code/msg 的）响应里容错取 token。

    真实的 doLogin 响应形状还没有人实测过，所以按 PC 站 JS 包里能看到的候选字段名
    宽松匹配：优先看顶层的 Token/token/accessToken，找不到再看嵌套一层的 data/Data
    里面是否有同名字段。
    """
    if not isinstance(payload, dict):
        return None
    for key in _TOKEN_KEYS:
        value = payload.get(key)
        if value:
            return value
    for data_key in _DATA_KEYS:
        nested = payload.get(data_key)
        if isinstance(nested, dict):
            for key in _TOKEN_KEYS:
                value = nested.get(key)
                if value:
                    return value
    return None


def send_sms_code(client: YoupinClient, phone: str, area: int = 86) -> str:
    session_id = str(uuid.uuid4())
    client._post(PATH_SEND_SMS, {"sessionId": session_id, "area": area, "mobile": phone})
    return session_id


def sms_sign_in(client: YoupinClient, phone: str, code: str, session_id: str, area: int = 86) -> str:
    data = client._post(PATH_SMS_SIGN_IN, {
        "sessionId": session_id,
        "area": area,
        "mobile": phone,
        "code": code,
        "tenDayFreeSignIn": True,
        "type": 1,
    })
    token = _extract_token(data)
    if not token:
        # 不回显原始 payload（可能带个人数据），只报告看到了哪些顶层字段名，方便诊断。
        shape = sorted(data.keys()) if isinstance(data, dict) else type(data).__name__
        raise YoupinError("nologin", f"no recognizable token field in doLogin response (shape: {shape})")
    client.token = token
    return token
