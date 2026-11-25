"""求购单消失后查去向（成交推送 spec §4.2）。

单子从「求购中」（status 20）里消失，可能是成交、被悠悠暂停（求购余额不够）或撤单，列表接口分不出来。
这里拿同一个「我的求购」接口换 status，到「已完成」（40）、「暂停中」（30）里按单号找它：

- 两个列表**不按时间排**（2026-10-02 实测），只能一页页翻，每页 40 条；
- 一轮里每个列表最多翻一遍，不管有几张单待查；先翻 40，还有没找到的再翻 30；
- 找到一张就落库一张（逐页提交），中途撞上风控，已查清的也不丢。

只读：只是换个 status 查自己的列表。去向查清后，成交的那条事件 notice 记 pending，由 Monitor 推送。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from skinpulse.db.models import MyBuyOrder, OrderEvent

log = logging.getLogger(__name__)

STATUS_DONE = 40     # 已完成
STATUS_PAUSED = 30   # 暂停中
PAGE_SIZE = 40
_YOUPIN_TIME = "%Y.%m.%d %H:%M:%S"


@dataclass
class ResolveReport:
    pending: int = 0      # 这次要查的消失事件
    done: int = 0
    paused: int = 0
    missing: int = 0
    likely: int = 0       # 推测成交：交易中过，之后哪个列表里都没有
    pages: int = 0        # 这次一共请求了几页
    incomplete: int = 0   # 翻到页数上限还没找到的（过一阵再查）
    incomplete_ids: list[int] = field(default_factory=list)


def fill_qty(ev: OrderEvent) -> int:
    """一次成交的件数：部分成交是剩余数的减少；整单消失的成交是消失前的剩余数（之前部分成交的件数
    已经各有一条 filled 事件了）。推送正文和看板共用这一份。"""
    if ev.kind == "filled":
        return (ev.qty_before or 0) - (ev.qty_after or 0)
    return ev.qty_before or 1


def fill_time(ev: OrderEvent) -> tuple[datetime, bool]:
    """一次成交的时间和它是不是悠悠给的精确成交时间（整单成交才有；部分成交只有我们发现的时间；
    推测成交用被卖的时刻，不算精确）。"""
    exact = ev.kind == "gone" and ev.outcome == "done" and ev.outcome_at is not None
    return (ev.outcome_at if ev.kind == "gone" and ev.outcome_at is not None else ev.at), exact


def parse_youpin_time(value, tz: str) -> datetime | None:
    """悠悠列表里的 `createTime`（"2026.10.02 09:55:14"，本地时间）→ UTC aware。解析不了就是 None。"""
    if not value:
        return None
    try:
        local = datetime.strptime(str(value).strip(), _YOUPIN_TIME)
    except ValueError:
        return None
    return local.replace(tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)


def _apply(ev: OrderEvent, status: int, raw: dict, tz: str, *, backfill: bool) -> None:
    ev.outcome_at = parse_youpin_time(raw.get("createTime"), tz)
    if status == STATUS_PAUSED:
        ev.outcome, ev.notice = "paused", None
        return
    ev.outcome = "done"
    bought, total = raw.get("buyQuantity"), raw.get("quantity")
    ev.outcome_qty = bought if isinstance(bought, int) else None
    if isinstance(bought, int) and isinstance(total, int) and bought < total:
        log.warning("resolve_gone: order %s finished with buyQuantity %d < quantity %d",
                    ev.purchase_no, bought, total)
    # 交易中已经推过的保持 skipped（一次成交只推最早的一条）；回填绝不推送
    if ev.notice != "skipped":
        ev.notice = None if backfill else "pending"


def _sold_at(session: Session, purchase_no: str, tz: str) -> datetime | None:
    """交易中的单最后一次被看到时的 `createTime`：被卖的时刻（live-api-findings §7）。"""
    row = session.get(MyBuyOrder, purchase_no)
    if row is None:
        return None
    try:
        raw = json.loads(row.conditions_json or "")
    except ValueError:
        return None
    return parse_youpin_time(raw.get("createTime"), tz) if isinstance(raw, dict) else None


def resolve_gone(session: Session, client, settings, *, now: datetime, max_pages: int | None = None,
                 backfill: bool = False, exclude_ids: set[int] | frozenset[int] = frozenset(),
                 account_id: int | None = None) -> ResolveReport:
    """给还没查清去向的 `gone` 事件查去向。

    backfill=False（每轮监控）：只查 `fills.resolve_window_h` 小时内消失的；查清的成交 notice=pending。
    backfill=True（CLI `fills backfill`）：不限时间，只写去向，notice 不动——绝不推送。
    超出时间窗还没查清的留着 NULL（不改成 missing），以后回填还能查。
    exclude_ids：这一轮先不查的事件（上次翻到页数上限还没找到的，由 Monitor 退避，免得每轮都翻满两个列表）。
    每一页之前都按 page_sleep_s 等一下，包括第一页：前面紧挨着的就是同一个账号通道上的同步请求。"""
    cfg = settings.fills
    max_pages = max_pages or cfg.max_pages
    rep = ResolveReport()
    q = select(OrderEvent).where(OrderEvent.kind == "gone", OrderEvent.outcome.is_(None))
    if not backfill:
        q = q.where(OrderEvent.at >= now - timedelta(hours=cfg.resolve_window_h))
    if account_id is not None:
        # 多账号：每个号只在自己的「已完成」「暂停中」里找自己的单（多账号 spec §5）
        q = q.where(OrderEvent.account_id == account_id)
    waiting: dict[str, list[OrderEvent]] = {}
    for ev in session.scalars(q.order_by(OrderEvent.id)):
        if ev.id not in exclude_ids:
            waiting.setdefault(ev.purchase_no, []).append(ev)
    rep.pending = sum(len(v) for v in waiting.values())
    if not waiting:
        return rep

    complete: dict[int, bool] = {}
    for status in (STATUS_DONE, STATUS_PAUSED):
        if not waiting:
            break
        complete[status] = False
        for page in range(1, max_pages + 1):
            client.sleep(client.s.page_sleep_s)
            rows = client.search_my_purchase_orders_page(status, page, PAGE_SIZE)
            rep.pages += 1
            hit = False
            for row in rows:
                for ev in waiting.pop(row.purchase_no, []):
                    _apply(ev, status, row.raw, settings.timezone, backfill=backfill)
                    hit = True
                    if status == STATUS_DONE:
                        rep.done += 1
                    else:
                        rep.paused += 1
            if hit:
                session.commit()
            if len(rows) < PAGE_SIZE:
                complete[status] = True
                break
            if not waiting:
                break

    for no, evs in waiting.items():
        if complete.get(STATUS_DONE) and complete.get(STATUS_PAUSED):
            # 两个列表都完整翻完了还没有：多半是撤单。消失前在交易中的，记推测成交（intent 2026-11-23：
            # 真成交过、却不在「已完成」里）；绝不推送，交易中推过的 notice 保持 skipped
            for ev in evs:
                if ev.in_trade_at is not None:
                    ev.outcome, ev.outcome_qty = "likely_done", None
                    ev.outcome_at = _sold_at(session, no, settings.timezone) or ev.in_trade_at
                    ev.notice = "skipped" if ev.notice == "skipped" else None
                    rep.likely += 1
                else:
                    ev.outcome, ev.notice = "missing", None
                    rep.missing += 1
        else:
            rep.incomplete += len(evs)
            rep.incomplete_ids += [ev.id for ev in evs]
    session.commit()
    if rep.incomplete:
        log.warning("resolve_gone: %d order(s) not found within max_pages=%d per list; will retry",
                    rep.incomplete, max_pages)
    return rep
