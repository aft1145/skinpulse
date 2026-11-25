"""`GET /api/fills`：最近成交（成交推送 spec §6），给总览的「最近成交」卡片。

只读库，不请求任何外部服务。「一次成交」= 一条 `filled` 事件（剩余数变少），或一条查清是成交的
`gone` 事件（`outcome = done`），或推测成交（`likely_done`：交易中过、之后哪个列表里都没有，标 likely）。成交时间优先用悠悠「已完成」列表给的（exact），部分成交只有我们
发现的时间。每次成交是靠哪条推送知道的，从 notification_log 里找：notice=sent 是成交那条，
notice=skipped 是之前推过的「交易中」那条。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, or_, select

from skinpulse.core.localday import local_day_bounds, local_today
from skinpulse.core.names import split_item_name
from skinpulse.db.models import MyBuyOrder, NotificationLog, OrderEvent
from skinpulse.db.session import utcnow
from skinpulse.services.fills import fill_qty, fill_time
from skinpulse.web.common import _num, iso_z

FILL_LIMIT = 50
MAX_DAYS = 90


def _notice(ev: OrderEvent, logs: list[NotificationLog]) -> NotificationLog | None:
    if ev.notice == "sent":
        hits = [lg for lg in logs if lg.event == "FILLED" and lg.created_at >= ev.at]
        return min(hits, key=lambda lg: lg.created_at, default=None)
    if ev.notice == "skipped":
        hits = [lg for lg in logs if lg.event == "IN_TRADE" and lg.created_at <= ev.at]
        return max(hits, key=lambda lg: lg.created_at, default=None)
    return None


def fills_payload(rt, *, days: int = 30, now: datetime | None = None, account_id: int | None = None) -> dict:
    now = now or utcnow()
    tz = rt.settings.timezone
    days = max(1, min(days, MAX_DAYS))
    today = local_today(tz, now)
    first = today - timedelta(days=days - 1)
    start = local_day_bounds(first, tz)[0]
    with rt.session_factory() as s:
        evs = list(s.scalars(select(OrderEvent).where(
            or_(OrderEvent.kind == "filled",
                and_(OrderEvent.kind == "gone", OrderEvent.outcome.in_(("done", "likely_done")))),
            or_(OrderEvent.outcome_at >= start, and_(OrderEvent.outcome_at.is_(None), OrderEvent.at >= start)))))
        if account_id is not None:      # 总览顶栏选了某个号（多账号 spec §8）
            evs = [ev for ev in evs if ev.account_id == account_id]
        nos = {ev.purchase_no for ev in evs}
        orders = {o.purchase_no: o for o in s.scalars(select(MyBuyOrder).where(MyBuyOrder.purchase_no.in_(nos)))}
        logs: dict[str, list[NotificationLog]] = {}
        for lg in s.scalars(select(NotificationLog).where(
                NotificationLog.fingerprint.in_(nos), NotificationLog.status == "sent",
                NotificationLog.event.in_(("IN_TRADE", "FILLED")))):
            logs.setdefault(lg.fingerprint, []).append(lg)

    items = []
    daily = {first + timedelta(days=i): 0 for i in range(days)}
    for ev in evs:
        when, exact = fill_time(ev)
        qty = fill_qty(ev)
        o = orders.get(ev.purchase_no)
        name = split_item_name(o.commodity_name if o else "")
        lg = _notice(ev, logs.get(ev.purchase_no, []))
        items.append({
            "purchase_no": ev.purchase_no, "template_id": ev.template_id, "account_id": ev.account_id,
            "title": name.title or ev.purchase_no, "tag": name.tag, "star": name.star, "stattrak": name.stattrak,
            "price": _num(ev.price), "qty": qty,
            "filled_at": iso_z(when), "exact": exact, "likely": ev.outcome == "likely_done",
            "detected_at": iso_z(ev.at),
            "notice": ev.notice, "notice_kind": lg.event if lg else None,
            "notice_at": iso_z(lg.sent_at or lg.created_at) if lg else None,
            "_when": when,
        })
        day = local_today(tz, when)
        if day in daily:
            daily[day] += qty
    items.sort(key=lambda f: f["_when"], reverse=True)
    for f in items:
        del f["_when"]
    # 价格缺失绝不当 0（CLAUDE.md 坑 3）：不计入合计，也不参与「最大一笔」
    priced = [f for f in items if f["price"] is not None]
    amount = sum((Decimal(str(f["price"])) * f["qty"] for f in priced), Decimal("0"))
    top = max(priced, key=lambda f: f["price"], default=None)
    return {
        "fills": items[:FILL_LIMIT],
        "total_30d": {"count": sum(f["qty"] for f in items), "amount": float(amount),
                      "largest": None if top is None else {"title": top["title"], "price": top["price"]}},
        "daily": [[d.isoformat(), n] for d, n in daily.items()],
    }
