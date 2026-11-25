"""`POST /api/youpin/token`：在看板上粘贴悠悠 token（看板 spec §5、§6；三期）。

流程：规范化 → 请求一次账号信息校验 → 通过才保存 → 让下一轮监控马上开始（每轮开头本来就先同步
求购单）。不在接口里同步：429 张要分 11 页、每页歇 2.5 秒，用户会干等 30 多秒（2026-09-27 实测），
和监控线程抢着同步也没必要。跑在服务进程里，监控线程正在用 `rt.client`：
- 校验用一个**临时 client**，通过之前正在用的 token 一个字都不动；
- 通过后才换 `rt.client.token`（每次请求现读，不用重启）、存钥匙串、清掉到期时间的缓存。

token 的任何片段都不进响应、日志和异常信息：请求体不回显（不用 pydantic 模型校验，免得 422
把输入原样带回去），失败只报类别，传输层异常只记类型名。
"""
from __future__ import annotations

import logging
import math

import httpx

from skinpulse.adapters.youpin.client import YoupinClient
from skinpulse.adapters.youpin.errors import LoginRequired, YoupinError
from skinpulse.adapters.youpin.token import jwt_expiry, normalize_token
from sqlalchemy import select

from skinpulse.core.alerts import AlertState
from skinpulse.db.models import Account, AlertStateRow, MyBuyOrder
from skinpulse.db.session import utcnow
from skinpulse.secrets import youpin_token_key
from skinpulse.services import account as account_svc
from skinpulse.web import overview

log = logging.getLogger(__name__)


class TokenError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


LABEL_MAX = 8    # 推送标题里放得下（多账号 spec §2）


def validation_client(rt, account_id: int = 1) -> YoupinClient:
    """校验用的临时 client：和这个号正在用的那个同一个设备身份（deviceId、uk）、同一个连接池，只是 token
    不同。每次都新建一个随机设备的话，每粘一次 token 就要多一次 deviceW2，悠悠那边还会看到一台
    陌生设备。"""
    acc = rt.accounts.get(account_id)
    live = acc.client if acc is not None else rt.client
    c = YoupinClient(rt.settings.youpin, http=live.http, device_id=live.device_id)
    c.uk, c.device_uk, c._uk_at = live.uk, live.device_uk, live._uk_at
    return c


def _validate(body, factory, throttle):
    """规范化、限流、请求一次账号信息。返回 (token, UserInfo)；失败抛 TokenError，原来的 token 不受影响。"""
    raw = body.get("token") if isinstance(body, dict) else None
    token = normalize_token(raw) if isinstance(raw, str) else ""
    if not token or any(ch.isspace() for ch in token):
        # 格式都不对：不去请求悠悠，不占限流名额，用 400 和「无效或已过期」（422）区分开
        raise TokenError(400, "未读取到 token：请粘贴 authorization 请求头的值（可带或不带 Bearer），中间不能有空格或换行")
    wait = throttle.acquire()
    if wait is not None:
        raise TokenError(429, f"操作太频繁，{math.ceil(wait)} 秒后再试")

    try:
        info = account_svc.validate_token(token, factory)
    except LoginRequired:
        throttle.record(ok=False)
        log.info("token update: rejected as invalid")
        raise TokenError(422, "token 无效或已过期，未保存；原有 token 不受影响") from None
    except YoupinError as e:
        throttle.record(ok=False)
        log.info("token update: could not verify (%s)", type(e).__name__)
        raise TokenError(503, "暂时无法验证，未保存；原有 token 不受影响，请稍后再试") from None
    except (httpx.HTTPError, OSError) as e:  # 传输层异常（网络不通等）只记类型名，消息里可能带 URL
        throttle.record(ok=False)
        log.warning("token update: could not verify (%s)", type(e).__name__)
        raise TokenError(503, "暂时无法验证（无法连接悠悠），未保存；原有 token 不受影响") from None
    # 别的异常是代码问题，照常抛成 500，不伪装成网络问题
    throttle.record(ok=True)
    return token, info


def _start_round(sched) -> bool:
    if sched is None:
        return False
    try:
        sched.modify_job("monitor_round", next_run_time=utcnow())
        return True
    except Exception as e:  # noqa: BLE001 - 提前不了就等下一轮，token 已经换好了
        log.warning("token update: could not start a round now (%s)", type(e).__name__)
        return False


def _duplicate(s, user_id: int, account_id: int) -> Account | None:
    return s.scalar(select(Account).where(Account.platform == "youpin", Account.user_id == user_id,
                                          Account.id != account_id))


def update_token(rt, body, *, factory, throttle, sched=None) -> dict:
    """换一个号的 token（body 可带 account_id，默认 1）。"""
    account_id = body.get("account_id", 1) if isinstance(body, dict) else 1
    if not isinstance(account_id, int) or isinstance(account_id, bool) or (account_id != 1 and account_id not in rt.accounts):
        raise TokenError(404, "账号不存在或已停用")
    make = factory if account_id == 1 else (lambda: factory(account_id))
    token, info = _validate(body, make, throttle)
    with rt.session_factory() as s:
        dup = _duplicate(s, info.user_id, account_id)
        if dup is not None:
            raise TokenError(409, f"这个 token 属于另一个账号（{dup.display_name()}），未保存")
        acc = s.get(Account, account_id) or Account(id=account_id)
        acc.user_id, acc.nickname, acc.token_valid = info.user_id, info.nickname, True
        s.add(acc)
        s.commit()
    live = rt.accounts.get(account_id)
    if live is not None:
        live.client.token = token      # 每次请求现读，不用重启
    rt.secrets.set(youpin_token_key(account_id), token)
    overview._token_cache.clear()
    log.info("token update: saved (account=%s, user_id=%s)", account_id, info.user_id)

    round_started = _start_round(sched)
    expires = jwt_expiry(token)
    return {"nickname": info.nickname, "user_id": info.user_id,
            "expires_at": overview.iso_z(expires), "round_started": round_started}


def _label(body) -> str | None:
    raw = body.get("label") if isinstance(body, dict) else None
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise TokenError(400, "简称要是文字")
    label = raw.strip()
    if len(label) > LABEL_MAX:
        raise TokenError(400, f"简称最多 {LABEL_MAX} 个字")
    return label or None


def add_account(rt, body, *, factory, throttle, sched=None, new_client=None) -> dict:
    """添加一个悠悠号（多账号 spec §7）：用**新的设备身份**校验，重号拒绝（409），建行、存钥匙串、
    重新装配启用的号、马上开一轮。号 1 还没登录过（新装）时就填进号 1。"""
    from skinpulse.runtime import load_accounts
    label = _label(body)
    client_box: dict = {}

    def make():
        c = (new_client or (lambda: YoupinClient(rt.settings.youpin)))()
        client_box["c"] = c
        return c
    with rt.session_factory() as s:
        first = s.get(Account, 1)
        target = 1 if first is None or first.user_id is None else None
    token, info = _validate(body, make if target is None else factory, throttle)
    with rt.session_factory() as s:
        dup = _duplicate(s, info.user_id, target or 0)
        if dup is not None:
            raise TokenError(409, f"该账号已存在（{dup.display_name()}），未重复添加")
        acc = s.get(Account, target) if target else None
        if acc is None:
            acc = Account(id=target) if target else Account()
            s.add(acc)
        acc.platform, acc.enabled = "youpin", True
        acc.user_id, acc.nickname, acc.token_valid = info.user_id, info.nickname, True
        acc.label = label
        acc.created_at = acc.created_at or utcnow()
        if "c" in client_box:
            acc.device_id = getattr(client_box["c"], "device_id", None)
        s.commit()
        account_id = acc.id
    rt.secrets.set(youpin_token_key(account_id), token)
    load_accounts(rt)
    overview._token_cache.clear()
    log.info("account added (account=%s, user_id=%s)", account_id, info.user_id)
    return {"id": account_id, "nickname": info.nickname, "user_id": info.user_id,
            "expires_at": overview.iso_z(jwt_expiry(token)), "round_started": _start_round(sched)}


def patch_account(rt, account_id: int, body, *, sched=None) -> dict:
    """改简称、停用 / 启用（多账号 spec §7）。停用：它的单不再算活跃、告警状态回 NORMAL，**不记 gone**
    （不是消失，不查去向、不推送）；钥匙串里的 token 留着。不能停用最后一个启用的号。"""
    from skinpulse.runtime import load_accounts
    if not isinstance(body, dict) or not ({"label", "enabled"} & set(body)):
        raise TokenError(400, "要改 label 或 enabled")
    enabled = body.get("enabled")
    if "enabled" in body and not isinstance(enabled, bool):
        raise TokenError(400, "enabled 要是 true 或 false")
    with rt.session_factory() as s:
        acc = s.get(Account, account_id)
        if acc is None:
            raise TokenError(404, "账号不存在")
        buff = acc.platform == "buff"
        if "label" in body:
            acc.label = _label(body)
        if enabled is False and acc.enabled:
            # 「至少留一个」只管悠悠号；BUFF 号随时能停（BUFF spec §8）
            others = s.scalar(select(Account.id).where(Account.platform == "youpin", Account.enabled.is_(True),
                                                       Account.id != account_id).limit(1))
            if others is None and not buff:
                raise TokenError(409, "至少要留一个启用的号")
            now = utcnow()
            for o in s.scalars(select(MyBuyOrder).where(MyBuyOrder.account_id == account_id,
                                                        MyBuyOrder.active.is_(True))):
                o.active = False
                o.in_trade_since = o.in_trade_notice = None
                o.market_flag = o.market_notice = None
                row = s.get(AlertStateRow, o.purchase_no)
                if row is not None and row.state != AlertState.NORMAL.value:
                    row.state, row.streak, row.updated_at = AlertState.NORMAL.value, 0, now
        if enabled is not None:
            acc.enabled = enabled
        s.commit()
    if buff:
        from skinpulse.runtime import load_buff_accounts
        from skinpulse.scheduler import sync_buff_job
        load_buff_accounts(rt)
        sync_buff_job(sched, rt)
    else:
        load_accounts(rt)
    overview._token_cache.clear()
    log.info("account %s updated (%s)", account_id, ", ".join(sorted(set(body) & {"label", "enabled"})))
    return {"ok": True}
