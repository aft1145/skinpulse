"""看板上加 BUFF 号、换 cookie、立即跑一轮（BUFF spec §8）。

cookie 的任何片段都不进响应、日志和异常：请求体不回显，失败只报类别，传输层异常只记类型名。
校验 = 用这个 cookie 请求一次只要 1 条的「我的求购」（`BuffClient.whoami`），只读。
"""
from __future__ import annotations

import logging
import math
import uuid

import httpx
from sqlalchemy import select

from skinpulse.adapters.buff.client import BuffClient
from skinpulse.adapters.buff.errors import BuffError, BuffLoginRequired, BuffRateLimited
from skinpulse.db.models import Account
from skinpulse.db.session import utcnow
from skinpulse.secrets import buff_cookie_key
from skinpulse.web.youpin_token import TokenError, _label

log = logging.getLogger(__name__)


def normalize_cookie(raw) -> str:
    """去掉首尾空白和误带的 `session=` 前缀；中间有空白的当格式不对。"""
    if not isinstance(raw, str):
        return ""
    v = raw.strip().strip('"').strip()
    if v.lower().startswith("session="):
        v = v[len("session="):].split(";", 1)[0].strip()
    return "" if any(ch.isspace() for ch in v) else v


def _validate(rt, body, throttle, client_factory, device_id: str):
    cookie = normalize_cookie(body.get("cookie") if isinstance(body, dict) else None)
    if not cookie:
        raise TokenError(400, "未读取到 session：请粘贴开发者工具里 Cookies → buff.163.com → session 那一行的 Value")
    wait = throttle.acquire()
    if wait is not None:
        raise TokenError(429, f"操作太频繁，{math.ceil(wait)} 秒后再试")
    client = (client_factory or (lambda c, d: BuffClient(rt.settings.buff, c, d)))(cookie, device_id)
    try:
        me = client.whoami()
    except BuffLoginRequired:
        throttle.record(ok=False)
        raise TokenError(422, "session 无效或已过期，未保存") from None
    except BuffRateLimited:
        throttle.record(ok=False)
        raise TokenError(503, "BUFF 当前要求验证或限流，未保存；请稍后再试") from None
    except BuffError as e:
        throttle.record(ok=False)
        log.info("buff cookie: could not verify (%s)", e.code[:32])
        raise TokenError(503, "暂时无法验证，未保存；请稍后再试") from None
    except (httpx.HTTPError, OSError) as e:
        throttle.record(ok=False)
        log.warning("buff cookie: could not verify (%s)", type(e).__name__)
        raise TokenError(503, "暂时无法验证（无法连接 BUFF），未保存") from None
    finally:
        http = getattr(client, "http", None)
        if http is not None and client_factory is None:
            http.close()
    throttle.record(ok=True)
    return cookie, me


def _refresh(rt, sched) -> bool:
    """重新装配 BUFF 号，并让这一轮马上开始（只排一次，见 `scheduler.sync_buff_job`）。返回是不是开始了。"""
    from skinpulse.runtime import load_buff_accounts
    from skinpulse.scheduler import sync_buff_job
    load_buff_accounts(rt)
    try:
        return sync_buff_job(sched, rt, start_now=True)
    except Exception as e:  # noqa: BLE001 —— 号已经存好；排不上就等下一轮
        log.warning("buff: could not schedule a round now (%s)", type(e).__name__)
        return False


def add_buff_account(rt, body, *, throttle, sched=None, client_factory=None) -> dict:
    label = _label(body)
    device_id = uuid.uuid4().hex.upper()
    cookie, me = _validate(rt, body, throttle, client_factory, device_id)
    with rt.session_factory() as s:
        if me.uid:
            dup = s.scalar(select(Account).where(Account.platform == "buff", Account.platform_uid == me.uid))
            if dup is not None:
                raise TokenError(409, "该 BUFF 账号已存在，未重复添加")
        acc = Account(platform="buff", enabled=True, label=label, nickname=me.nickname, platform_uid=me.uid,
                      device_id=device_id, token_valid=True, created_at=utcnow())
        s.add(acc)
        s.commit()
        account_id = acc.id
    rt.secrets.set(buff_cookie_key(account_id), cookie)
    started = _refresh(rt, sched)
    log.info("buff account added (account=%s)", account_id)
    return {"id": account_id, "nickname": me.nickname, "platform": "buff", "round_started": started}


def update_buff_cookie(rt, body, *, throttle, sched=None, client_factory=None) -> dict:
    account_id = body.get("account_id") if isinstance(body, dict) else None
    with rt.session_factory() as s:
        acc = s.get(Account, account_id) if isinstance(account_id, int) and not isinstance(account_id, bool) else None
        if acc is None or acc.platform != "buff":
            raise TokenError(404, "未找到该 BUFF 账号")
        device_id, uid = acc.device_id or uuid.uuid4().hex.upper(), acc.platform_uid
    cookie, me = _validate(rt, body, throttle, client_factory, device_id)
    if uid and me.uid and me.uid != uid:
        raise TokenError(409, "这是另一个 BUFF 账号的 session，未保存；添加新账号请使用「添加账号」")
    with rt.session_factory() as s:
        acc = s.get(Account, account_id)
        acc.token_valid, acc.device_id = True, device_id
        acc.platform_uid = acc.platform_uid or me.uid
        acc.nickname = me.nickname or acc.nickname
        s.commit()
    rt.secrets.set(buff_cookie_key(account_id), cookie)
    live = rt.buff_accounts.get(account_id)
    if live is not None:
        live.login_notice.reset()
    started = _refresh(rt, sched)
    log.info("buff cookie updated (account=%s)", account_id)
    return {"id": account_id, "nickname": me.nickname, "round_started": started}


def start_buff_round(sched) -> bool:
    """让 BUFF 的下一轮马上开始（不在接口里同步：一轮要十几次请求、每次隔 6 秒）。"""
    if sched is None or sched.get_job("buff_round") is None:
        return False
    try:
        sched.modify_job("buff_round", next_run_time=utcnow())
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("buff: could not start a round now (%s)", type(e).__name__)
        return False
