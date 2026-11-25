"""价位二期的建议（纯逻辑，无 IO）。价位 spec §9.5，回测见 docs/research/2026-10-09-valuation-backtest.md。

按顺序判断，命中一条就停。每条建议带一句「触发它的规则」，里面写出用到的数字，页面原样显示：
结论必须能追溯到它是怎么来的。

只用通过了回测的东西：
- 「7 天坏情况」（R1 通过）→ `m_bad` 和求购价上限 `cap`，撤单、降价、加价都靠它；
- 「在跌且跑输大盘」（R2 **没通过**）不进规则，只是描述标签。

程序只给建议；改价、撤单由用户自己在悠悠里操作，对悠悠只读。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from skinpulse.analysis.valuation import TICK

NONE, CANCEL, LOWER, RAISE, HOLD = "none", "cancel", "lower", "raise", "hold"
LABELS = {NONE: "不评价", CANCEL: "考虑撤单", LOWER: "考虑降价", RAISE: "可以加价", HOLD: "保持"}


@dataclass(frozen=True)
class Advice:
    kind: str
    rule: str                    # 触发它的规则，一句话，带数字
    price: Decimal | None = None  # 降到 / 加到的价；其余为 None

    @property
    def label(self) -> str:
        return LABELS[self.kind]


def _pct(x: Decimal) -> str:
    return f"{x * 100:+.1f}%"


def _money(x: Decimal) -> str:
    return f"¥{x:.2f}"


def advise(*, q10: Decimal | None, exit: Decimal | None, cap: Decimal | None, m_bad: Decimal | None,
           my_price: Decimal | None, top_price: Decimal | None, rank: int | None,
           conditional: bool, in_trade: bool, target_margin: Decimal, min_profit: Decimal) -> Advice:
    # 1. 不评价
    if in_trade:
        return Advice(NONE, "交易中：有卖家正在卖给这张单")
    if conditional:
        return Advice(NONE, "条件单：K 线按整个磨损档算，代表不了这个区间")
    if q10 is None:
        return Advice(NONE, "日线不足，算不出 7 天坏情况")
    if exit is None or cap is None:
        return Advice(NONE, "没有在售价（或当天没有价格快照），算不出能卖多少")
    if my_price is None or my_price <= 0 or m_bad is None:
        return Advice(NONE, "没有求购价")

    bad = f"7 天坏情况 {_pct(q10)}"
    # 2. 考虑撤单：按我的价成交，碰上坏情况会亏
    if m_bad < 0:
        return Advice(CANCEL, f"按我的价 {_money(my_price)} 成交，{bad} 后差价 {_pct(m_bad)}，会亏")
    # 3. 考虑降价：碰上坏情况还赚，但不够目标差价或最低利润
    if my_price > cap:
        need = f"目标差价 {target_margin * 100:.0f}%、每件至少 {_money(min_profit)}"
        if cap <= 0:
            return Advice(LOWER, f"{bad} 后差价 {_pct(m_bad)}，按规则怎么买都不够{need}")
        return Advice(LOWER, f"{bad} 后差价 {_pct(m_bad)}，不够{need}；降到 {_money(cap)} 以内", price=cap)
    # 4. 可以加价：不是第 1，加到「榜首 + 0.01」也没超过上限
    lead = "已是第 1，" if rank == 1 else ""
    if rank != 1 and top_price is not None and top_price > 0:
        to = top_price + TICK
        if to <= cap:
            m_to = exit * (1 + q10) / to - 1
            return Advice(RAISE, f"加到 {_money(to)} 可排第 1，{bad} 后差价还有 {_pct(m_to)}（上限 {_money(cap)}）", price=to)
        lead = f"抢第 1 要出 {_money(to)}，超过上限；"
    # 5. 保持
    return Advice(HOLD, f"{lead}{bad} 后差价 {_pct(m_bad)}，在上限 {_money(cap)} 以内")
