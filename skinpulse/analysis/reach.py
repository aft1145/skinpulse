"""「难以成交」：离第 1 或离市价太远的求购单（难以成交 spec §1）。

推送（Monitor._is_eligible_for_alert）、每日简报（analysis/brief.top_table）、看板（web/overview）
共用这一处定义，三处结论一致。纯函数：不碰数据库、不碰网络。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

Reason = Literal["top", "market"]
MarketSrc = Literal["sell", "bid"]


def _pos(v: Decimal | None) -> bool:
    # 价格 0 = 没有数据（CLAUDE.md 坑 3），和 None 一样算不知道
    return v is not None and v > 0


def market_ref(sell_min: Decimal | None, sell_depth: int | None,
               ref_bid: Decimal | None) -> tuple[Decimal | None, MarketSrc | None]:
    """市价：有人在卖用悠悠在售最低；查过、没人在卖（sell_depth == 0）用别处买盘；
    其余（没查过、BUFF 单、缺价）判断不了，返回 (None, None)。"""
    if _pos(sell_min) and sell_depth != 0:
        return sell_min, "sell"
    if sell_depth == 0 and _pos(ref_bid):
        return ref_bid, "bid"
    return None, None


def unreachable(my: Decimal | None, top1: Decimal | None, sell_min: Decimal | None, sell_depth: int | None,
                ref_bid: Decimal | None, *, max_gap_ratio: float, max_market_ratio: float,
                min_market_gap: float) -> Reason | None:
    """满足任一条就是难以成交，返回原因；判断不了的一律 None——宁可多列，不悄悄藏掉。

    - "top"：榜首价 ÷ 我的价 ≥ max_gap_ratio（先判）；
    - "market"：市价 ÷ 我的价 ≥ max_market_ratio **且** 市价 − 我的价 ≥ min_market_gap。

    倍数填 0 关掉对应那条。设置是 float，走 Decimal(str(x)) 和金额列比较（同原来的 is_muted）。"""
    if not _pos(my):
        return None
    if max_gap_ratio > 0 and _pos(top1) and top1 / my >= Decimal(str(max_gap_ratio)):
        return "top"
    if max_market_ratio > 0:
        market, _ = market_ref(sell_min, sell_depth, ref_bid)
        if market is not None and market / my >= Decimal(str(max_market_ratio)) \
                and market - my >= Decimal(str(min_market_gap)):
            return "market"
    return None


def reach_kwargs(alerts) -> dict:
    """从 AlertSettings 取三个阈值，给 unreachable(**…) 用；每次现读，改设置下一轮就生效。"""
    return {"max_gap_ratio": alerts.max_gap_ratio, "max_market_ratio": alerts.max_market_ratio,
            "min_market_gap": alerts.min_market_gap}
