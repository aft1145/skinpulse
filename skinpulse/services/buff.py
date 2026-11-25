"""BUFF 号的同步、名次、交易中和成交配对（BUFF spec §4～§6）。

- 单号存成 `buff:<原单号>`；饰品按 `goods_id` 对到悠悠 templateId，对不上用 `-goods_id`。
- 「消失」只在本号的单里判（同多账号 spec §5）。
- BUFF 的订单不带求购单号：交易中、成交只能按「type=2、同饰品、同价、建于求购单之后、没配过」配对。

对 BUFF 只读：这里只调 `BuffClient` 的 GET 方法。cookie 不进日志。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from skinpulse.adapters.buff.errors import BuffLoginRequired
from skinpulse.adapters.buff.models import BuffBill, BuffBuyOrder
from skinpulse.core.alerts import AlertState
from skinpulse.core.buff_rank import fingerprint, label_of_fingerprint, order_no, queue_key
from skinpulse.db.models import Account, AlertStateRow, Item, MyBuyOrder, OrderEvent
from skinpulse.db.session import utcnow
from skinpulse.services.account import LoginLostNotice, _order_events

log = logging.getLogger(__name__)

PLATFORM = "buff"
BILL_BUY_ORDER = 2          # 求购成交（调研 §8.4）
UNMATCHED_RETRY = timedelta(minutes=20)   # 消失后多久还配不上就下结论（约一轮多一点，新订单可能晚到）


def buff_status_key(account_id: int) -> str:
    return f"buff_account:{account_id}"


@dataclass(eq=False)
class BuffAccountRuntime:
    """服务里的一个 BUFF 号（BUFF spec §4）：自己的 client、登录失效 / 访问受限提醒的状态。"""
    id: int
    client: object
    label: str = "BUFF"
    login_notice: LoginLostNotice | None = None
    limited_notice: LoginLostNotice | None = None

    def __post_init__(self) -> None:
        self.login_notice = self.login_notice or LoginLostNotice()
        self.limited_notice = self.limited_notice or LoginLostNotice()

    @property
    def status_key(self) -> str:
        return buff_status_key(self.id)


def buff_account_ids(session: Session, *, enabled_only: bool = False) -> set[int]:
    q = select(Account.id).where(Account.platform == PLATFORM)
    if enabled_only:
        q = q.where(Account.enabled.is_(True))
    return set(session.scalars(q))


def youpin_account_ids(session: Session) -> set[int]:
    """悠悠号（含停用的）。简报、选品、价位、市价提醒只看这些号的单（BUFF spec §8）。"""
    return set(session.scalars(select(Account.id).where(Account.platform != PLATFORM))) | {1}


def youpin_orders():
    """SQL 条件：只要悠悠号的单。简报、选品、价位、价格快照、对账只看这些（BUFF spec §8）。"""
    return MyBuyOrder.account_id.not_in(select(Account.id).where(Account.platform == PLATFORM))


# ---- 饰品对应 ----

def goods_to_template(session: Session, goods_id: int, hash_name: str, cn_name: str = "") -> int:
    """BUFF goods_id → 悠悠 templateId（BUFF spec §4）：先查 `item.buff_goods_id`，再按 market_hash_name
    找；找到就把 goods_id 记进 item。都对不上给 `-goods_id`（负数 = 只在 BUFF 有，没有详情页）。"""
    row = session.scalar(select(Item).where(Item.buff_goods_id == goods_id).limit(1))
    if row is None and hash_name:
        row = session.scalar(select(Item).where(Item.hash_name == hash_name).limit(1))
        if row is not None and row.youpin_template_id is not None:
            row.buff_goods_id = goods_id
    if row is not None and row.youpin_template_id:
        return int(row.youpin_template_id)
    return -int(goods_id)


def goods_id_of(order: MyBuyOrder) -> int | None:
    """BUFF 单的 goods_id：存在 conditions_json 里（同步时写的原始字段）。"""
    try:
        v = json.loads(order.conditions_json or "{}").get("goods_id")
        return int(v) if v is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def buff_goods_url(goods_id: int | None) -> str | None:
    return f"https://buff.163.com/goods/{goods_id}" if goods_id else None


def created_of(order: MyBuyOrder) -> datetime | None:
    try:
        v = json.loads(order.conditions_json or "{}").get("created_at")
        return datetime.fromisoformat(v) if v else None
    except (ValueError, TypeError, AttributeError):
        return None


# ---- 同步 ----

def _raw(o: BuffBuyOrder) -> str:
    """存进 conditions_json 的原始信息：goods_id、建单时间、条件、最高普通单价。不含任何身份信息。"""
    return json.dumps({
        "goods_id": o.goods_id, "created_at": o.created_at.isoformat() if o.created_at else None,
        "specific": [{"type": s.type, "text": s.text, "values": list(s.values)} for s in o.specific],
        "buy_max_price": str(o.buy_max_price) if o.buy_max_price is not None else None,
    }, ensure_ascii=False)


def sync_buff_orders(session: Session, acc: BuffAccountRuntime, now: datetime | None = None) -> list[MyBuyOrder]:
    """同步一个 BUFF 号「正在求购」的全部单，返回这次看到的单（活跃）。语义同悠悠的 `sync_my_orders`：
    新单 new、改价 repriced、部分成交 filled、消失 gone；「消失」只在本号判。"""
    row_acc = session.get(Account, acc.id)
    try:
        orders, me = acc.client.my_buy_orders()
    except BuffLoginRequired:
        if row_acc is not None:
            row_acc.token_valid = False
            session.commit()
        raise
    acc.login_notice.reset()
    now = now or utcnow()
    if row_acc is not None:
        row_acc.token_valid = True
        if me.nickname:
            row_acc.nickname = me.nickname
        if me.uid and not row_acc.platform_uid:
            row_acc.platform_uid = me.uid
    seen: dict[str, MyBuyOrder] = {}
    events: list[OrderEvent] = []
    for o in orders:
        if not o.order_id or o.price <= 0 or o.state not in ("", "progressing"):
            continue
        no = order_no(o.order_id)
        tid = goods_to_template(session, o.goods_id, o.market_hash_name, o.name)
        row = session.get(MyBuyOrder, no)
        was_active = row is not None and row.active
        before = (row.price, row.remaining_qty, row.qty) if was_active else None
        in_trade_sent = was_active and row.in_trade_notice == "sent"
        if row is None:
            row = MyBuyOrder(purchase_no=no, template_id=tid, account_id=acc.id)
            session.add(row)
        evs = _order_events(no, tid, before, (o.price, o.remaining, o.num), now, in_trade_sent=in_trade_sent)
        for ev in evs:
            ev.account_id = acc.id
        events += evs
        fp = fingerprint(o.specific)
        row.account_id = acc.id
        row.template_id = tid
        row.template_hash_name = o.market_hash_name
        row.commodity_name = o.name or o.market_hash_name
        row.price, row.qty, row.remaining_qty = o.price, o.num, o.remaining
        row.abrade_text = None if fp == "plain" else label_of_fingerprint(fp)
        row.type_id, row.special_style, row.fade_text = -1, None, None
        row.queue_key = queue_key(o.goods_id, o.specific)
        row.conditions_json = _raw(o)
        # 普通单的「榜首」先记最高普通单价（数名次时再按队列更新）；条件单的榜首只能数队列
        if fp == "plain" and o.buy_max_price is not None:
            row.top_bid = max(o.buy_max_price, o.price)
        if before is not None and before[0] != o.price:
            row.reported_rank = None                 # 改价了：旧名次作废，等这一轮数
        row.synced_at, row.active = now, True
        seen[no] = row
    gone = 0
    for row in session.scalars(select(MyBuyOrder).where(MyBuyOrder.active.is_(True), MyBuyOrder.account_id == acc.id)):
        if row.purchase_no in seen:
            continue
        evs = _order_events(row.purchase_no, row.template_id, (row.price, row.remaining_qty, row.qty), None, now,
                            in_trade_sent=row.in_trade_notice == "sent", in_trade_at=row.in_trade_since)
        for ev in evs:
            ev.account_id = acc.id
        events += evs
        row.active = False
        row.in_trade_since = row.in_trade_notice = None
        state = session.get(AlertStateRow, row.purchase_no)
        if state is not None and state.state != AlertState.NORMAL.value:
            state.state, state.streak, state.updated_at = AlertState.NORMAL.value, 0, now
        gone += 1
    if row_acc is not None:
        row_acc.last_sync_at = now
    session.add_all(events)
    session.flush()
    if session.scalar(select(Account.enabled).where(Account.id == acc.id)) is False:
        session.rollback()
        log.info("sync_buff_orders: account %s was disabled during the sync; discarding it", acc.id)
        return []
    session.commit()
    if gone:
        log.info("sync_buff_orders: %d order(s) no longer listed; deactivated", gone)
    log.info("buff: synced %d buy orders for account %s", len(seen), acc.id)
    return list(seen.values())


# ---- 交易中、成交配对（spec §6） ----

def _claimed(session: Session) -> set[str]:
    return set(session.scalars(select(OrderEvent.outcome_ref).where(OrderEvent.outcome_ref.is_not(None))))


def _fits(bill: BuffBill, order: MyBuyOrder) -> bool:
    gid = goods_id_of(order)
    created = created_of(order)
    if bill.type != BILL_BUY_ORDER or gid is None or bill.goods_id != gid or bill.price != order.price:
        return False
    return created is None or bill.created_at is None or bill.created_at >= created


def mark_in_trade(session: Session, acc_id: int, bills: list[BuffBill], now: datetime) -> int:
    """进行中的求购成交订单（不是 SUCCESS 也不是 FAIL）配给活跃的单 → 记交易中。返回新记的张数。"""
    active = sorted(session.scalars(select(MyBuyOrder).where(MyBuyOrder.active.is_(True),
                                                             MyBuyOrder.account_id == acc_id)),
                    key=lambda o: (created_of(o) or now, o.purchase_no))
    claimed: set[str] = set()
    trading: set[str] = set()
    n = 0
    for b in bills:
        if b.success or b.failed or b.type != BILL_BUY_ORDER:
            continue
        for o in active:
            if o.purchase_no in claimed or not _fits(b, o):
                continue
            claimed.add(o.purchase_no)
            trading.add(o.purchase_no)
            if o.in_trade_since is None:
                o.in_trade_since, o.in_trade_notice = now, None
                n += 1
                log.info("buff: order %s in trade (bill state %s)", o.purchase_no, b.state[:16])
            break
    for o in active:
        if o.in_trade_since is not None and o.purchase_no not in trading:
            o.in_trade_since = o.in_trade_notice = None
    return n


def resolve_buff_gone(session: Session, acc_id: int, bills: list[BuffBill], now: datetime) -> dict[str, int]:
    """消失的 BUFF 单（gone、还没去向）去订单里配 SUCCESS 的求购成交 → done；配不到的过一阵下结论：
    交易中出现过 → likely_done（不推），否则 missing（撤单）。"""
    out = {"done": 0, "likely": 0, "missing": 0}
    pending = list(session.scalars(select(OrderEvent).where(
        OrderEvent.account_id == acc_id, OrderEvent.kind == "gone", OrderEvent.outcome.is_(None))
        .order_by(OrderEvent.id)))
    if not pending:
        return out
    claimed = _claimed(session)
    orders = {o.purchase_no: o for o in session.scalars(
        select(MyBuyOrder).where(MyBuyOrder.purchase_no.in_({e.purchase_no for e in pending})))}
    # 同饰品、同价的几张单都消失了：配建单最早的那张
    pending.sort(key=lambda e: (created_of(orders[e.purchase_no]) or e.at) if e.purchase_no in orders else e.at)
    for ev in pending:
        o = orders.get(ev.purchase_no)
        bill = None
        if o is not None:
            for b in sorted(bills, key=lambda b: b.created_at or now):
                if b.success and b.bill_id not in claimed and _fits(b, o):
                    bill = b
                    break
        if bill is None and o is not None:
            # 卖家已经供货、订单还没走完（不是 SUCCESS 也不是 FAIL）：单子可能同时就从「正在求购」里消失了。
            # 这时不能过 20 分钟就判撤单——记下交易中，等订单走完再配（评审 2026-11-24）
            pend = next((b for b in bills if not b.success and not b.failed and b.bill_id not in claimed
                         and _fits(b, o)), None)
            if pend is not None:
                claimed.add(pend.bill_id)
                if ev.in_trade_at is None:
                    ev.in_trade_at = now
                continue
        if bill is not None:
            claimed.add(bill.bill_id)
            ev.outcome, ev.outcome_ref = "done", bill.bill_id
            ev.outcome_at = bill.transact_time or bill.created_at
            ev.outcome_qty = ev.qty_before
            if ev.notice != "skipped":
                ev.notice = "pending"
            out["done"] += 1
        elif now - ev.at >= UNMATCHED_RETRY:
            ev.outcome = "likely_done" if ev.in_trade_at is not None else "missing"
            ev.outcome_at = ev.in_trade_at if ev.in_trade_at is not None else None
            out["likely" if ev.in_trade_at is not None else "missing"] += 1
    session.commit()
    return out
