"""充值前检查暂停单（暂停单 spec §2）：纯逻辑，无 IO。

暂停单不在公开求购队列里，名次没法逐个现算（11-24 连查 77 次就被 403，live-api-findings §6），所以只用
「我的求购」列表里悠悠白送的 `maxPurchasePrice`（同条件子队列榜首，不含自己这张）判断恢复后是不是第 1、
差多少；在售最低来自 SteamDT 或悠悠在售接口。价格缺失或 ≤ 0 一律当作没有数据，**绝不当 0 用**。

档位按危险程度，取第一个成立的：
- `inverted` 倒挂：我的价 ≥ 在售最低，恢复后卖家直接卖给我比挂出去赚得多；
- `top1` 直接第 1：子队列没人（`暂无`），或我的价 ≥ 榜首；
- `near` 差 near_gap 以内：离第 1 只差一点，别人一撤单就是我；
- `near_sell` 贴近在售：我的价 ≥ 在售最低 × sell_ratio；
- `other` 其余。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

INVERTED, TOP1, NEAR, NEAR_SELL, OTHER = "inverted", "top1", "near", "near_sell", "other"
TIERS = (INVERTED, TOP1, NEAR, NEAR_SELL, OTHER)
ALONE, AHEAD, TIE = "alone", "ahead", "tie"

_EMPTY_TOP = {"暂无", "无", "-", "—"}


def parse_top_bid(value) -> tuple[Decimal | None, bool]:
    """`maxPurchasePrice`（`"¥49"`、`"¥1,234.5"`、`"暂无"`）→ `(价格, 子队列没人)`。

    `暂无` 是一个结论（没人求购，恢复就是第 1），缺失或解析不了是「不知道」，两者必须分开：
    返回 `(None, True)` 和 `(None, False)`。"""
    if value is None:
        return None, False
    s = str(value).strip()
    if s in _EMPTY_TOP:
        return None, True
    s = s.lstrip("¥￥").replace(",", "").strip()
    if not s:
        return None, False
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None, False
    if not d.is_finite() or d <= 0:
        return None, False
    return d, False


@dataclass(frozen=True)
class Verdict:
    tier: str
    top1_kind: str | None          # alone / ahead / tie；不是第 1 或不知道榜首时为 None
    gap_ratio: Decimal | None      # (榜首 − 我的价) / 榜首，只在榜首比我高时有
    sell_ratio: Decimal | None     # 我的价 / 在售最低，没有在售价时为 None


def _valid(d: Decimal | None) -> bool:
    return d is not None and d > 0


def classify(price: Decimal, top_bid: Decimal | None, top_empty: bool, sell_min: Decimal | None, *,
             near_gap: Decimal, sell_ratio: Decimal) -> Verdict:
    kind: str | None = None
    gap: Decimal | None = None
    if top_empty:
        kind = ALONE
    elif _valid(top_bid):
        if price > top_bid:
            kind = AHEAD
        elif price == top_bid:
            kind = TIE
        else:
            gap = (top_bid - price) / top_bid
    ratio = price / sell_min if _valid(sell_min) and _valid(price) else None

    if ratio is not None and ratio >= 1:
        tier = INVERTED
    elif kind is not None:
        tier = TOP1
    elif gap is not None and gap <= near_gap:
        tier = NEAR
    elif ratio is not None and ratio >= sell_ratio:
        tier = NEAR_SELL
    else:
        tier = OTHER
    return Verdict(tier, kind, gap, ratio)


def paused_days(paused_at: datetime | None, now: datetime) -> int | None:
    """暂停了几整天。暂停单列表里的 `createTime` 就是被暂停的时刻（spec §2）。"""
    if paused_at is None:
        return None
    return max(0, math.floor((now - paused_at).total_seconds() / 86400))


def sort_key(tier: str, sell_ratio: Decimal | None, gap_ratio: Decimal | None) -> tuple:
    """同档内：差 5% 内按差距从小到大；其余按「我的价 / 在售」从大到小，没有在售价的排后面。"""
    if tier == NEAR:
        inner = (gap_ratio is None, gap_ratio if gap_ratio is not None else Decimal(0))
    else:
        inner = (sell_ratio is None, -(sell_ratio if sell_ratio is not None else Decimal(0)))
    return (TIERS.index(tier), *inner)
