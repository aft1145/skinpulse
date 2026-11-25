"""K 线纵轴范围与影线截断（纯逻辑，无 IO）。看板 spec §4.2、§5，ADR-0013。

纵轴按**实体**（开/收）定，影线只在不太离谱时才纳入。为什么不直接用最高/最低价：流动性差的
饰品会有离谱的影线。实测暗影双匕 | 北方森林（崭新）价格在 600~1000 之间，有两根日线的最高价
是 11178——多半是有人挂了个错价，当天的「最高」就被记成了它。按影线定范围，整张图被撑到一万多，
真实走势被压成底部一条直线，图就废了。收盘价没受影响，所以均线信号照常可信。

截断必须如实告诉看图的人：悄悄截掉等于把一个真实存在的数据点藏起来。所以除了范围，还返回
被截的根数和极值，前端写在图上。

范围由后端算、前端只负责画：1 月 / 3 月 / 1 年各算一份，一年前的高价不能把最近一个月的图压扁。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from skinpulse.adapters.steamdt.models import KlineBar

# 影线最多可以伸出实体范围的多少倍，超出就截断。
WICK_ALLOWANCE = Decimal("0.5")

# 看板上的时间范围 → 最后多少根日线。日线接口给 365 根（research §1.6）。
WINDOWS: dict[str, int] = {"1m": 30, "3m": 90, "1y": 365}


@dataclass(frozen=True)
class YRange:
    lo: Decimal
    hi: Decimal
    clipped: int                 # 影线超出范围、被截断的根数
    extreme: Decimal | None      # 被截掉的最高价（有向上截断时）或最低价；没有截断时为 None


def y_range(bars: Sequence[KlineBar]) -> YRange | None:
    if not bars:
        return None
    body_lo = min(min(b.open, b.close) for b in bars)
    body_hi = max(max(b.open, b.close) for b in bars)
    body_span = body_hi - body_lo
    if body_span == 0:
        body_span = max(abs(body_hi) * Decimal("0.02"), Decimal(1))
    allow = body_span * WICK_ALLOWANCE
    lo = max(min(b.low for b in bars), body_lo - allow)
    hi = min(max(b.high for b in bars), body_hi + allow)
    if hi == lo:
        hi, lo = hi + 1, lo - 1
    over = [b for b in bars if b.high > hi]
    under = [b for b in bars if b.low < lo]
    clipped = sum(1 for b in bars if b.high > hi or b.low < lo)
    if over:
        extreme = max(b.high for b in over)
    elif under:
        extreme = min(b.low for b in under)
    else:
        extreme = None
    return YRange(lo=lo, hi=hi, clipped=clipped, extreme=extreme)


def windows(bars: Sequence[KlineBar]) -> dict[str, YRange | None]:
    """每个时间范围各自的纵轴范围。`bars` 按时间升序。"""
    return {name: y_range(bars[-n:]) for name, n in WINDOWS.items()}
