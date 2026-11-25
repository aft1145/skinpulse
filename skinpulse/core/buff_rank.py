"""BUFF 的名次（BUFF spec §5）：纯逻辑、无 IO。

BUFF 不给名次，要拿公开队列自己数。口径（ADR-0022）：**普通单只和普通单比，条件单只和条件完全相同
的单比**；同价先挂的在前。价比我高（同价比我早）的**其它**条件的单另外数成 `cond_ahead`，只显示、
不进告警。认「我这张单」只能靠单号：公开队列里的 user_id 是打过码的（调研 §8、fixture）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable, Sequence

from skinpulse.adapters.buff.models import BuffQueueRow, Specific

PREFIX = "buff:"
PLAIN = "plain"
# specific.type → 队列标签里的中文（其它类型原样显示）
TYPE_LABELS = {"paintwear": "磨损"}


def order_no(order_id: str) -> str:
    """BUFF 单在库里的单号：加 `buff:` 前缀（ADR-0022），前缀只在适配器边界加、去。"""
    return PREFIX + order_id


def raw_order_id(purchase_no: str) -> str:
    return purchase_no[len(PREFIX):] if purchase_no.startswith(PREFIX) else purchase_no


def is_buff(purchase_no: str) -> bool:
    return purchase_no.startswith(PREFIX)


def fingerprint(specific: Iterable[Specific]) -> str:
    """条件指纹：没有条件是 `plain`；有条件按 type 排序后 `type=v1-v2` 用 `;` 连起来。"""
    parts = sorted(f"{s.type}={'-'.join(s.values)}" for s in specific)
    return ";".join(parts) if parts else PLAIN


def queue_key(goods_id: int, specific: Iterable[Specific]) -> str:
    return f"{PREFIX}{goods_id}:{fingerprint(specific)}"


def label_of_fingerprint(fp: str) -> str:
    """`queue_key` 的条件部分 → 队列标签（「不限」或「磨损 0.07-0.08」）。"""
    if not fp or fp == PLAIN:
        return "不限"
    out = []
    for part in fp.split(";"):
        t, _, v = part.partition("=")
        out.append(f"{TYPE_LABELS.get(t, t)} {v}".strip())
    return " / ".join(out)


def split_queue_key(key: str) -> tuple[int, str]:
    """`buff:<goods_id>:<指纹>` → (goods_id, 指纹)。"""
    body = key[len(PREFIX):]
    gid, _, fp = body.partition(":")
    return int(gid), fp or PLAIN


@dataclass(frozen=True)
class RankOutcome:
    rank: int | None          # None：翻到页数上限还没数完（「300 名以后」）
    top: Decimal | None       # 同条件里的最高价（含我自己）
    cond_ahead: int           # 价比我高（同价比我早）的其它条件的单
    seen: int                 # 看过的行数
    found: bool               # 队列里看到我这张单了


def _ahead(row: BuffQueueRow, price: Decimal, created: datetime | None) -> bool:
    if row.price != price:
        return row.price > price
    return created is not None and row.created_at is not None and row.created_at < created


def rank_in_queue(rows: Sequence[BuffQueueRow], *, my_id: str, price: Decimal, created: datetime | None,
                  specific: Iterable[Specific], complete: bool) -> RankOutcome:
    """拿看过的队列行数我这张单的名次。`complete`：我前面的单是不是都看到了（看到了我、或者翻到了比我
    便宜的单、或者翻完了）。不完整时名次给 None。"""
    fp = fingerprint(specific)
    same_ahead = 0
    other_ahead = 0
    top = price
    found = False
    for r in rows:
        if r.order_id == my_id:
            found = True
            continue
        same = fingerprint(r.specific) == fp
        if same and r.price > top:
            top = r.price
        if _ahead(r, price, created):
            if same:
                same_ahead += 1
            else:
                other_ahead += 1
    return RankOutcome(rank=same_ahead + 1 if complete else None, top=top, cond_ahead=other_ahead,
                       seen=len(rows), found=found)


def covers_me(rows: Sequence[BuffQueueRow], *, my_id: str, price: Decimal, page_full: bool) -> bool:
    """这些行够不够数清我前面的单：看到我了、最后一行已经比我便宜，或者没有下一页了。"""
    if any(r.order_id == my_id for r in rows):
        return True
    if not page_full:
        return True
    return bool(rows) and rows[-1].price < price


@dataclass(frozen=True)
class Candidate:
    purchase_no: str
    price: Decimal
    buy_max_price: Decimal | None
    plain: bool
    alerting: bool
    last_ranked: datetime | None


def pick_candidates(orders: Sequence[Candidate], budget: int) -> list[str]:
    """这一轮要查队列的单（spec §5）：先「普通单、价 ≥ 最高普通单价」（可能是第 1），再告警中的，
    其余按上次数名次的时间从早到晚轮换（从没数过的最先），取满 `budget` 为止。"""
    if budget <= 0:
        return []

    def may_lead(c: Candidate) -> bool:
        return c.plain and (c.buy_max_price is None or c.price >= c.buy_max_price)

    first = [c for c in orders if may_lead(c)]
    second = [c for c in orders if c.alerting and not may_lead(c)]
    picked = {c.purchase_no for c in first + second}
    rest = sorted((c for c in orders if c.purchase_no not in picked),
                  key=lambda c: (c.last_ranked is not None, c.last_ranked or datetime.min))
    out = [c.purchase_no for c in first + second + rest]
    return out[:budget]
