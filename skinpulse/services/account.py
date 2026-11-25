from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from skinpulse.adapters.ntfy.client import NtfyError
from skinpulse.adapters.youpin.errors import LoginRequired, RateLimited
from skinpulse.adapters.youpin.models import UserInfo
from skinpulse.core.alerts import AlertState
from skinpulse.core.messages import build_system_message
from skinpulse.core.rank import queue_key_of, queue_key_str
from skinpulse.db.models import Account, AlertStateRow, MyBuyOrder, OrderEvent
from skinpulse.db.session import utcnow

log = logging.getLogger(__name__)

# 「悠悠登录已失效」：发现失效的那一刻推一条，之后仍失效就每 24 小时再推一条；token 恢复
# （心跳或同步成功）后清零，下次失效又立刻推。写死，不进设置。
LOGIN_LOST_REPEAT_S = 24 * 3600


class LoginLostNotice:
    """job_heartbeat 和 Monitor.run_round 的 pass A 都可能先发现 token 失效，两边共用这一份
    状态，所以不会一边推一条。只有真的发出去才计时：全部通道都失败时，下次发现再试。
    状态只在内存里：失效期间重启服务会再推一条（宁可重复，不能漏）。"""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        # 两个调用方在不同线程里：判断、发送、计时整段串行，免得同时判成「该推」各推一条
        self.lock = threading.Lock()
        self._sent_at: float | None = None

    def due(self) -> bool:
        return self._sent_at is None or self.clock() - self._sent_at >= LOGIN_LOST_REPEAT_S

    def mark_sent(self) -> None:
        self._sent_at = self.clock()

    def reset(self) -> None:
        self._sent_at = None


_login_notice = LoginLostNotice()


def account_status_key(account_id: int) -> str:
    """账号通道在 system_status 里的 key（多账号 spec §2；迁移 0014 把老的 youpin_account 改成了号 1 的）。"""
    return f"youpin_account:{account_id}"


@dataclass(eq=False)
class AccountRuntime:
    """服务里的一个悠悠号（多账号 spec §4）：自己的 client（token、设备身份、uk）和登录失效提醒的状态。

    号 1 的提醒状态沿用模块级的 `_login_notice`（每次现读，测试可以替换它）；别的号各有一份。"""
    id: int
    client: object
    label: str = ""
    login_notice: LoginLostNotice | None = None

    def __post_init__(self) -> None:
        if self.login_notice is None and self.id != 1:
            self.login_notice = LoginLostNotice()

    @property
    def status_key(self) -> str:
        return account_status_key(self.id)

    def notice(self) -> LoginLostNotice:
        return self.login_notice if self.login_notice is not None else _login_notice


def as_account(client_or_acc) -> AccountRuntime:
    """老调用方（CLI、测试）传的是 client 本身：当作号 1。"""
    return client_or_acc if isinstance(client_or_acc, AccountRuntime) else AccountRuntime(1, client_or_acc)


def login_restored(acc: AccountRuntime | None = None) -> None:
    (acc.notice() if acc is not None else _login_notice).reset()

_LEADING_NON_NUMERIC_RE = re.compile(r"^[^\d.]+")

# 校验 token 时最多请求几次、每次间隔多久。悠悠在轻度限流时会回空响应（client 里映射成
# RateLimited），这是一时的，跟 token 对不对无关，所以只重试它；LoginRequired 等明确的拒绝不重试。
VALIDATE_ATTEMPTS = 3
VALIDATE_RETRY_DELAY_S = 2.0


def validate_token(token: str, client_factory: Callable[[], object], *,
                   attempts: int = VALIDATE_ATTEMPTS, delay_s: float = VALIDATE_RETRY_DELAY_S,
                   sleep: Callable[[float], None] = time.sleep,
                   on_retry: Callable[[int, RateLimited], None] | None = None) -> UserInfo:
    """用 `client_factory()` 给的 client 带着候选 token 请求一次账号信息。

    网页换 token 时传一个临时 client：服务进程里监控线程正在用 `rt.client`，校验通过之前不能
    把没验证过的 token 塞给它（看板三期 plan）。CLI 的 set-token 传的是 `rt.client` 本身，
    它是一次性进程，没有这个顾虑。

    失败原样抛出：LoginRequired = token 无效或过期；其他 YoupinError（包括重试用完的
    RateLimited）= 暂时没法验证。异常里不含 token。"""
    client = client_factory()
    client.token = token
    for attempt in range(1, attempts + 1):
        try:
            return client.get_user_info()
        except RateLimited as e:
            if attempt >= attempts:
                raise
            if on_retry is not None:
                on_retry(attempt + 1, e)
            sleep(delay_s)
    raise AssertionError("unreachable")  # attempts >= 1


def _account(session: Session, account_id: int = 1) -> Account:
    acc = session.get(Account, account_id)
    if acc is None:
        acc = Account(id=account_id)
        session.add(acc)
    return acc


def _parse_reported_rank(value) -> int | None:
    """YouPin's own `rank` field on a my-orders row (observed range 1-213 on a live
    371-order account; three hand-verified against the public queue matched exactly).
    It's a string that may be absent or non-numeric - store None rather than raising
    or guessing when it doesn't parse as a positive integer."""
    if value is None:
        return None
    try:
        n = int(str(value).strip())
    except ValueError:
        return None
    return n if n > 0 else None


def _parse_top_bid(value) -> Decimal | None:
    """YouPin's own `maxPurchasePrice` field - a display string with a leading currency
    symbol (e.g. "¥28.1"), unlike `unitPrice` which is bare. Strip any leading
    non-numeric prefix and surrounding whitespace before converting; store None rather
    than raising when it doesn't parse."""
    if value is None:
        return None
    s = _LEADING_NON_NUMERIC_RE.sub("", str(value).strip()).strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


IN_TRADE_RANK = "*"      # 悠悠的 rank：有卖家卖给了这张单、交易还没完成（live-api-findings §7）
# 已经报过的「既不是数字也不是 *」的 rank 值：同一个值只在进程里报一次，免得每轮一条刷满 launchd.log
_reported_odd_ranks: set[str] = set()


def is_in_trade(raw_rank) -> bool:
    return raw_rank is not None and str(raw_rank).strip() == IN_TRADE_RANK


def _create_time(conditions_json: str | None) -> str | None:
    """上一次同步存下的原始记录里的 `createTime`；解析不了（老数据、截断）就是 None。"""
    try:
        v = json.loads(conditions_json or "").get("createTime")
    except (ValueError, AttributeError):
        return None
    return (str(v).strip() or None) if v else None


def _order_events(purchase_no: str, template_id: int, before: tuple | None, after: tuple | None,
                  now: datetime, *, in_trade_sent: bool = False, in_trade_at: datetime | None = None) -> list[OrderEvent]:
    """一张单这次同步前后的变化 → 事件（价位 spec §3.1）。before / after 是 (价格, 剩余数, 总数)，
    None 表示「之前没有（或不活跃）」/「这次列表里没有了」。

    in_trade_at：消失时这张单还在交易中，记下第一次看到 * 的时刻；查不到去向时据此记「推测成交」
    （intent 2026-11-23）。
    in_trade_sent：这张单上一次被看到时在交易中、而且「交易中」已经推出去了。一次成交只推最早的
    一条（ADR-0015），所以这时成交事件的 notice 记 skipped；否则 filled 记 pending 等着推，gone 先
    留空，等 services.fills.resolve_gone 查清是成交再定（成交推送 spec §4.1）。"""
    def ev(kind, qty_before, qty_after, price, notice=None, in_trade_at=None):
        return OrderEvent(purchase_no=purchase_no, template_id=template_id, kind=kind, qty_before=qty_before,
                          qty_after=qty_after, price=price, at=now, notice=notice, in_trade_at=in_trade_at)
    if before is None:
        return [ev("new", None, after[1], after[0])]
    if after is None:
        # 成交、被暂停、撤单都会让单从列表里消失，列表接口分不出来——先记「消失了」，去向另查
        return [ev("gone", before[1], None, before[0], "skipped" if in_trade_sent else None, in_trade_at)]
    out = []
    (p0, r0, n0), (p1, r1, n1) = before, after
    # 用户在悠悠里改了件数：总数和剩余数一起变，那部分不是成交（评审 2026-09-28）
    resized = n0 is not None and n1 is not None and n1 != n0
    if resized:
        out.append(ev("resized", n0, n1, p0))
    if r0 is not None and r1 is not None:
        sold = (r0 - r1) - ((n0 - n1) if resized else 0)
        if sold > 0:
            out.append(ev("filled", r0, r0 - sold, p0, "skipped" if in_trade_sent else "pending"))
    if p0 is not None and p1 is not None and p1 != p0:
        out.append(ev("repriced", r0, r1, p1))
    return out


def _safe_events(events: list, *args, **kwargs) -> None:
    # 事件给二期回测攒数据、也驱动成交推送；记不下来也绝不能拖垮同步（监控每轮开头就靠它）
    try:
        events.extend(_order_events(*args, **kwargs))
    except Exception as e:  # noqa: BLE001
        log.warning("sync_my_orders: order event skipped (%s)", type(e).__name__)


def sync_my_orders(session: Session, client_or_acc) -> int:
    """同步一个号「求购中」的全部单。传 AccountRuntime（服务里）或 client（老调用方，当号 1）。

    「消失」只在**这个号**的单里判（多账号 spec §5）：不加这个条件，同步主号会把小号的单全判成消失。"""
    ar = as_account(client_or_acc)
    client = ar.client
    acc = _account(session, ar.id)
    try:
        info = client.get_user_info()
        acc.user_id, acc.nickname, acc.token_valid = info.user_id, info.nickname, True
        orders = client.search_my_purchase_orders()
    except LoginRequired:
        acc.token_valid = False
        session.commit()
        raise
    login_restored(ar)
    now = utcnow()
    seen: set[str] = set()
    events: list[OrderEvent] = []
    stored = 0
    odd_ranks: list[str] = []
    missing_purchase_no = 0
    missing_template_id = 0
    missing_or_zero_price = 0
    for o in orders:
        # searchPurchaseOrderList's real field names were verified 2026-08-30 against a
        # live 371-order account (see MyPurchaseOrder's docstring), but the endpoint has
        # no documentation and could still change - from_api falls back to
        # purchase_no="" / template_id=0 / price=0 when its candidate key names aren't
        # present. purchase_no="" is the primary key, so session.get(MyBuyOrder, "")
        # would return whichever "" row a *previous* iteration just created, silently
        # collapsing every unparseable order onto that one row. template_id=0 would
        # instead store fine but burn a request every round on
        # get_template_purchase_orders(0) and never rank. price<=0 (missing,
        # unparseable, or a genuine "0") can never be ranked either - the rank engine
        # ranks on price, and a real sync of this exact fixture-guessing bug once stored
        # 371 rows this way, all silently unusable. Skip all three rather than proceed
        # on data this app can't use.
        if not o.purchase_no:
            missing_purchase_no += 1
            continue
        if not o.template_id:
            missing_template_id += 1
            continue
        if o.price is None or o.price <= 0:
            missing_or_zero_price += 1
            continue
        seen.add(o.purchase_no)
        key = queue_key_str(queue_key_of(o.template_id, o.abrade_text, o.type_id, o.special_style, o.fade_text))
        row = session.get(MyBuyOrder, o.purchase_no)
        # 消失过又出现的单：旧行还在（只是 active=False），也按新单记
        was_active = row is not None and row.active
        before = (row.price, row.remaining_qty, row.qty) if was_active else None
        in_trade_sent = was_active and row.in_trade_notice == "sent"
        prev_created = _create_time(row.conditions_json) if was_active else None
        if row is None:
            row = MyBuyOrder(purchase_no=o.purchase_no, template_id=o.template_id, account_id=ar.id)
            session.add(row)
        elif row.account_id != ar.id:
            # 悠悠单号全站唯一，理论上不会发生；真发生了就跟着最新看到它的号走
            log.warning("sync_my_orders: order %s moved from account %s to %s", o.purchase_no, row.account_id, ar.id)
            row.account_id = ar.id
        n_events = len(events)
        _safe_events(events, o.purchase_no, o.template_id, before, (o.price, o.remaining_qty, o.qty), now,
                     in_trade_sent=in_trade_sent)
        for ev in events[n_events:]:
            ev.account_id = ar.id
        row.template_hash_name = o.template_hash_name
        row.commodity_name = o.commodity_name
        row.price, row.qty, row.remaining_qty = o.price, o.qty, o.remaining_qty
        row.abrade_text, row.type_id, row.special_style, row.fade_text = o.abrade_text, o.type_id, o.special_style, o.fade_text
        row.queue_key = key
        row.conditions_json = json.dumps(o.raw, ensure_ascii=False)[:4000]
        # Bonus fields YouPin hands us for free on this same row - see MyBuyOrder's
        # comment. Not derived from anything else in `o` (MyPurchaseOrder's own fields
        # stay as they were - see module docstring), so read straight off the raw dict.
        raw_rank = o.raw.get("rank")
        row.reported_rank = _parse_reported_rank(raw_rank)
        row.top_bid = _parse_top_bid(o.raw.get("maxPurchasePrice"))
        # 交易中（成交推送 spec §4.1）：一段「交易中」只记第一次看到的时刻；回到数字名次就清空。
        # 消失后又出现的单是新的一段。reported_rank 照旧是 None，「升到第 1 名」不受影响。
        if is_in_trade(raw_rank):
            created = str(o.raw.get("createTime") or "").strip() or None
            if was_active and row.in_trade_since is not None:
                pass                                    # 还在同一段交易中
            elif prev_created and created == prev_created:
                # 改价（撤单重挂）的过渡：旧单也短暂显示 *，但 createTime 不变；真被卖时它会变成
                # 被卖的时刻（intent 2026-11-23）。不算交易中，名次当作暂时未知。
                log.info("sync_my_orders: order %s shows * but createTime unchanged; treating as reprice, not in trade",
                         o.purchase_no)
            else:
                row.in_trade_since, row.in_trade_notice = now, None
        else:
            row.in_trade_since = row.in_trade_notice = None
            if raw_rank is not None and row.reported_rank is None:
                odd_ranks.append(str(raw_rank)[:8])
        row.synced_at, row.active = now, True
        stored += 1
    gone = 0
    for row in session.scalars(select(MyBuyOrder).where(MyBuyOrder.active.is_(True),
                                                        MyBuyOrder.account_id == ar.id)):
        if row.purchase_no not in seen:
            n_events = len(events)
            _safe_events(events, row.purchase_no, row.template_id, (row.price, row.remaining_qty, row.qty), None, now,
                         in_trade_sent=row.in_trade_notice == "sent", in_trade_at=row.in_trade_since)
            for ev in events[n_events:]:
                ev.account_id = ar.id
            row.active = False
            row.in_trade_since = row.in_trade_notice = None
            row.market_flag = row.market_notice = None
            # 这张单从悠悠的列表里消失了（成交或到期）。它的告警状态必须跟着退回 NORMAL，
            # 否则那一行会永远停在 ALERTING：`alert_state` 的 ALERTING 计数会随着成交一路
            # 虚高，而这个计数正是平时用来判断告警引擎有没有在正常工作的那个数。
            # （2026-09-21 库里就有一条这样的陈旧记录：一张 09-07 成交的单还挂在 ALERTING。）
            state = session.get(AlertStateRow, row.purchase_no)
            if state is not None and state.state != AlertState.NORMAL.value:
                state.state = AlertState.NORMAL.value
                state.streak = 0
                state.updated_at = now
            gone += 1
    if gone:
        log.info("sync_my_orders: %d order(s) no longer listed; deactivated", gone)
    new_odd = [r for r in odd_ranks if r not in _reported_odd_ranks]
    if new_odd:
        # 既不是数字也不是 "*"：悠悠可能又加了一种状态。只记前 8 个字符；同一个值每个进程只报一次
        _reported_odd_ranks.update(new_odd)
        log.warning("sync_my_orders: %d order(s) with an unrecognized rank value, e.g. %r",
                    len(odd_ranks), new_odd[0])
    acc.last_sync_at = now
    session.add_all(events)
    # 看板上在这次同步进行中停用了这个号（patch_account 已经把它的单改成不活跃）：这次写的全都不要，
    # 否则会把刚停用的单又改回活跃，而停用的号以后不再同步，它们就一直挂着。先 flush 再读：写事务
    # 开始之后，停用要么已经提交（这里读得到），要么得等这次提交之后才能写（那时它会再改一遍）。
    session.flush()
    if session.scalar(select(Account.enabled).where(Account.id == ar.id)) is False:
        session.rollback()
        log.info("sync_my_orders: account %s was disabled during the sync; discarding it", ar.id)
        return 0
    session.commit()
    skipped = missing_purchase_no + missing_template_id + missing_or_zero_price
    if skipped:
        # Never log the raw row - it may carry personal data (see also: token/uk/phone
        # redaction rules elsewhere in this codebase). Counts and reasons only.
        if stored == 0:
            log.error(
                "sync_my_orders: all %d row(s) unusable and 0 stored (missing purchaseNo: %d, "
                "missing/zero templateId: %d, missing/unparseable/non-positive price: %d) - "
                "check searchPurchaseOrderList field mapping",
                len(orders), missing_purchase_no, missing_template_id, missing_or_zero_price,
            )
        else:
            log.warning(
                "sync_my_orders: skipped %d of %d row(s) as unusable (missing purchaseNo: %d, "
                "missing/zero templateId: %d, missing/unparseable/non-positive price: %d)",
                skipped, len(orders), missing_purchase_no, missing_template_id, missing_or_zero_price,
            )
    log.info("synced %d purchase orders", stored)
    return stored


def heartbeat(session: Session, client_or_acc) -> bool:
    ar = as_account(client_or_acc)
    acc = _account(session, ar.id)
    try:
        ar.client.get_user_info()
        acc.token_valid = True
        session.commit()
        login_restored(ar)
        return True
    except LoginRequired:
        acc.token_valid = False
        session.commit()
        return False


def notify_login_lost(ntfy, ntfy_settings, acc: AccountRuntime | None = None, *, multi: bool = False) -> None:
    """The one notice that tells the user the YouPin token expired and monitoring has
    stopped. Called from job_heartbeat and from Monitor.run_round's pass A (see
    monitor.py); `_login_notice` (shared, module-level) sends it once when the failure is
    first seen, then once every 24 hours while it lasts, whichever caller triggers it."""
    if ntfy is None:
        return
    notice = acc.notice() if acc is not None else _login_notice
    # 多号时标题带上是哪个号（多账号 spec §6）；一个号时一字不改
    title = f"悠悠登录已失效：{acc.label}" if multi and acc is not None and acc.label else "悠悠登录已失效"
    with notice.lock:
        if not notice.due():
            return
        try:
            ntfy.publish(build_system_message(title, "请在看板「系统」页更新 token 后继续同步求购。", ntfy_settings))
            notice.mark_sent()
        except NtfyError as e:
            # NtfyClient already redacts the topic from HTTP-status failures before
            # raising this, so the message is safe to log in full.
            log.error("failed to send login-lost notice: %s", e)
        except Exception as e:  # noqa: BLE001
            # Transport-level failure (httpx). Never log its message: the topic is
            # effectively a password on a public ntfy server, and a transport error
            # carries the full request URL, which embeds it.
            log.error("failed to send login-lost notice: %s", type(e).__name__)
