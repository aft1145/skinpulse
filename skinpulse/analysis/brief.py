"""每日简报的纯逻辑（M3a，spec §7a）：从排名快照算出「前 N 名表」和每行的变动，以及「发不发」。
不碰数据库、不碰网络——取数在 services/brief.py，文案在 core/messages.py。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from skinpulse.analysis.reach import unreachable


@dataclass(frozen=True)
class Snap:
    ts: datetime
    rank: int | None
    top1_price: Decimal | None


@dataclass(frozen=True)
class OrderInfo:
    purchase_no: str
    name: str
    my_price: Decimal
    template_id: int
    # 离市价判断用（难以成交 spec §3）：订单行当前的值，和推送、看板同一份；不给就是判断不了
    sell_min: Decimal | None = None
    sell_depth: int | None = None
    ref_bid: Decimal | None = None


@dataclass(frozen=True)
class TopRow:
    """收盘时在前 N 的一张单。开盘 = D 之前最后一条有排名的快照（新单没有，为 None）。"""
    purchase_no: str
    name: str
    my_price: Decimal
    rank: int
    open_rank: int | None
    top1_price: Decimal | None
    open_top1: Decimal | None
    sell_price: Decimal | None     # D 当天 23:50 快照里悠悠的在售最低价；没有就是 None，绝不当 0
    is_new: bool
    rank_changed: bool
    top1_changed: bool

    @property
    def changed(self) -> bool:
        return self.is_new or self.rank_changed or self.top1_changed


@dataclass(frozen=True)
class TopTable:
    rows: list[TopRow]
    orders_with_data: int

    @property
    def changed_rows(self) -> list[TopRow]:
        return [r for r in self.rows if r.changed]


def _known(price: Decimal | None) -> bool:
    return price is not None and price > 0


def _sort_key(r: TopRow):
    return (r.rank, -r.my_price, r.name)


def top_table(orders: list[OrderInfo], snaps: dict[str, list[Snap]], day_start: datetime, day_end: datetime,
              *, top_rank: int, max_gap_ratio: float, max_market_ratio: float = 0, min_market_gap: float = 0,
              sell_prices: dict[int, Decimal | None] | None = None) -> TopTable:
    """spec §7a.2：D 收盘（D 内最后一条有排名的快照）排在前 top_rank、且不是「难以成交」（analysis/reach）的单，
    和开盘（D 之前最后一条有排名的快照）比出三种变动。rank 为 None 的快照不参与（未知不等于落后）。"""
    sell_prices = sell_prices or {}
    rows: list[TopRow] = []
    with_data = 0
    for o in orders:
        known = sorted((s for s in snaps.get(o.purchase_no, []) if s.rank is not None), key=lambda s: s.ts)
        before = [s for s in known if s.ts < day_start]
        inday = [s for s in known if day_start <= s.ts < day_end]
        if not inday:
            continue
        with_data += 1
        close = inday[-1]
        if close.rank > top_rank or unreachable(o.my_price, close.top1_price, o.sell_min, o.sell_depth, o.ref_bid,
                                                max_gap_ratio=max_gap_ratio, max_market_ratio=max_market_ratio,
                                                min_market_gap=min_market_gap):
            continue
        opening = before[-1] if before else None
        is_new = opening is None or opening.rank > top_rank
        rank_changed = not is_new and opening.rank != close.rank
        # 收盘第 1：榜首就是我，榜首价变了其实是我的价变了——用户没选这一项，不标。
        # 榜首价 0 = 没有数据（CLAUDE.md 坑 3），和 None 一样算不知道。
        top1_changed = (close.rank != 1 and opening is not None and _known(opening.top1_price)
                        and _known(close.top1_price) and opening.top1_price != close.top1_price)
        rows.append(TopRow(o.purchase_no, o.name, o.my_price, close.rank,
                           opening.rank if opening else None, close.top1_price,
                           opening.top1_price if opening else None, sell_prices.get(o.template_id),
                           is_new, rank_changed, top1_changed))
    rows.sort(key=_sort_key)
    return TopTable(rows, with_data)


NO_DATA = "no_data"
SENT = "sent"
QUIET_SENT = "quiet_sent"
SKIPPED = "skipped"
NO_CHANNEL = "no_channel"
FAILED = "failed"


@dataclass(frozen=True)
class Decision:
    decision: str
    reasons: dict

    @property
    def send(self) -> bool:
        return self.decision in (SENT, QUIET_SENT)


def decide(table: TopTable, *, day: date, last_sent_day: date | None, first_log_day: date | None,
           quiet_days: int, top_rank: int) -> Decision:
    """spec §7a.4：自上而下，命中即停。「离开前 N」不参与（用户 2026-09-23 选择）。"""
    if table.orders_with_data == 0:
        return Decision(NO_DATA, {"summary": "当天没有任何排名快照（监控没在跑）"})
    changed = table.changed_rows
    if changed:
        counts = {"new": sum(r.is_new for r in changed),
                  "rank_changed": sum(r.rank_changed for r in changed),
                  "top1_changed": sum(r.top1_changed for r in changed)}
        summary = (f"前 {top_rank} 共 {len(table.rows)} 张，{len(changed)} 张有变动"
                   f"（新进 {counts['new']}，名次变化 {counts['rank_changed']}，榜首价变化 {counts['top1_changed']}）")
        return Decision(SENT, {"summary": summary, **counts})
    anchor = last_sent_day or first_log_day or day
    days = (day - anchor).days
    if days >= quiet_days:
        return Decision(QUIET_SENT, {"summary": f"距上次发出已 {days} 天，发静默兜底",
                                     "days_since_last_sent": days})
    return Decision(SKIPPED, {"summary": f"前 {top_rank} 没有变动（距上次发出 {days} 天）",
                              "days_since_last_sent": days})
