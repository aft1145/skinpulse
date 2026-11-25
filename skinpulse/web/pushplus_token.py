"""`POST /api/pushplus/token`：在看板上录入 PushPlus token（首次使用 spec §2.4）。

和命令行 `pushplus-token` 走同一套检查（services/pushplus_setup.py）：格式不对不发请求；发一条测试消息，
成功才存钥匙串，并把 PushPlus 通道原地加进正在跑的推送器（不用重启）。失败一律不保存，原来的不受影响。

token 的任何片段都不进响应、日志和异常信息：请求体不用 pydantic 模型（免得 422 把输入带回去），
失败只报类别。「发出去了」不等于送到了微信（CLAUDE.md 坑 1），页面上要用户自己去看。"""
from __future__ import annotations

import logging
import math

from skinpulse.adapters.pushplus.client import PushPlusClient
from skinpulse.runtime import attach_channel
from skinpulse.secrets import KEY_PUSHPLUS
from skinpulse.services import pushplus_setup as pp
from skinpulse.web.youpin_token import TokenError

log = logging.getLogger(__name__)

_FAIL = {
    pp.AUTH: (422, "PushPlus 不认可这个 token（903）：请在 PushPlus 网页上点「复制token」，核对位数（收到的是 {shape}）"),
    pp.REALNAME: (422, "PushPlus 要求先完成实名认证才能发消息（905）：在官网认证后再试"),
    pp.RESTRICTED: (422, "PushPlus 账号当前被限制使用（900）：官方建议停止请求，约 2 天后自动恢复"),
    pp.REJECTED: (422, "PushPlus 返回错误：{detail}"),
    pp.NETWORK: (503, "暂时连不上 PushPlus，过一会儿再试"),
}


RESTRICTED_HOLD_S = 6 * 3600     # 900 之后官方让停手；看板这边至少 6 小时不再替用户发请求（重启服务会清掉）


def update_pushplus(rt, body, *, factory=PushPlusClient, throttle) -> dict:
    raw = body.get("token") if isinstance(body, dict) else None
    token, bad = pp.check_token_text(raw) if isinstance(raw, str) else (None, pp.EMPTY)
    if bad == pp.CONTROL:
        raise TokenError(400, "输入中含有不可见的控制字符：请重新复制后粘贴")
    if token is None:
        raise TokenError(400, "未读取到 token：请粘贴 PushPlus 网页上的 token，中间不能有空格或换行")
    held = getattr(throttle, "hold_until", 0.0) - throttle.clock()
    if held > 0:
        raise TokenError(429, f"PushPlus 刚返回账号受限（900），继续请求会延长限制：请在 {math.ceil(held / 3600)} 小时后再试")
    wait = throttle.acquire()
    if wait is not None:
        raise TokenError(429, f"操作太频繁，{math.ceil(wait)} 秒后再试")

    res = pp.send_probe(token, rt.settings.timezone, client_factory=factory)
    if not res.ok:
        throttle.record(ok=False)
        if res.reason == pp.RESTRICTED:
            throttle.hold_until = throttle.clock() + RESTRICTED_HOLD_S   # 挂在这个接口自己的限流器上
        log.info("pushplus token: not saved (%s)", res.reason)
        status, msg = _FAIL[res.reason]
        raise TokenError(status, msg.format(shape=pp.token_shape(token), detail=res.detail) + "。未保存")
    throttle.record(ok=True)
    rt.secrets.set(KEY_PUSHPLUS, token)
    attach_channel(rt, "pushplus", factory(token))
    log.info("pushplus token: saved and attached")
    return {"sent": True}
