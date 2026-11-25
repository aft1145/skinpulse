"""市价提醒（市价提醒 spec §1）：纯逻辑，无 IO。

两种情况，满足任一就提醒：
- `inverted`「榜首高于在售」：榜首价 ≥ 悠悠在售最低，而我排第 2～rank_always（默认前 3），或者排第
  rank_always+1～rank_max（默认 4～10）且我的价 ≥ 在售最低 × inverted_ratio（默认 0.85）。卖家直接卖给高价
  求购比挂出去赚得多，排在前面的单会被很快吃掉；名次再靠后的，只有出价接近市价才可能轮到（16 笔真实成交
  的求购价有四分之三在收盘的 0.85 以上）。
- `near`「贴近市价」：我的价 ≥ 在售最低 × near_ratio，一成交差价就很薄。

只看不限条件、不在交易中、有人在卖的单：条件单的在售最低是整个模板的，不代表那个磨损区间；交易中已经有
自己的推送。价格缺失或 ≤ 0 一律当作没有数据，不判。
"""
from __future__ import annotations

from decimal import Decimal

INVERTED, NEAR = "inverted", "near"


def market_flags(*, conditional: bool, in_trade: bool, my_price: Decimal | None, rank: int | None,
                 top: Decimal | None, sell_min: Decimal | None, sell_depth: int | None,
                 rank_max: int, near_ratio: Decimal, rank_always: int = 3,
                 inverted_ratio: Decimal = Decimal("0.85")) -> frozenset[str]:
    if conditional or in_trade or not sell_depth or sell_depth <= 0 or sell_min is None or sell_min <= 0:
        return frozenset()
    out = set()
    if top is not None and top > 0 and top >= sell_min and rank is not None and 2 <= rank <= rank_max:
        close = my_price is not None and my_price > 0 and my_price >= sell_min * inverted_ratio
        if rank <= rank_always or close:
            out.add(INVERTED)
    if my_price is not None and my_price > 0 and my_price >= sell_min * near_ratio:
        out.add(NEAR)
    return frozenset(out)


def flag_label(flags: frozenset[str] | set[str]) -> str | None:
    """存库、给看板用的一个词：inverted / near / both；都不成立为 None。"""
    if INVERTED in flags and NEAR in flags:
        return "both"
    return next(iter(flags), None)


def flags_of(label: str | None) -> frozenset[str]:
    return frozenset({INVERTED, NEAR}) if label == "both" else frozenset({label}) if label else frozenset()
