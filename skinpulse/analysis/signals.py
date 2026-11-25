"""单品走势信号（纯逻辑，无 IO）：均线、区间涨跌、趋势标签。

只基于日线收盘价。SteamDT 的 K 线**不含成交量**（文档原话「k线数据(不包含成交数据)」），所以
spec §7 里的「量比」这里做不了——不是漏了，是数据源没有。

标签的阈值全部写死在这里、有名有姓，而不是藏在代码里的魔法数：标签是结论，结论必须能被人
追溯到它是怎么得出来的。页面上标签和原始数字总是一起显示。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from skinpulse.adapters.steamdt.models import KlineBar

# MA7 相对 MA30 偏离超过这个百分比，才叫「上升 / 下降」；以内都算「横盘」。
# 2% 是刻意取得宽一点：饰品日线噪声大，1% 的来回每天都有，标成趋势只会误导。
TREND_THRESHOLD_PCT = Decimal("2")


@dataclass(frozen=True)
class Signals:
    last_close: Decimal
    ma7: Decimal | None
    ma30: Decimal | None
    change_7d_pct: Decimal | None
    change_30d_pct: Decimal | None
    bars: int

    @property
    def ma_gap_pct(self) -> Decimal | None:
        """MA7 相对 MA30 高/低多少（%）。正数 = 短期均价在长期均价之上。"""
        if self.ma7 is None or self.ma30 is None or self.ma30 == 0:
            return None
        return (self.ma7 / self.ma30 - 1) * 100

    @property
    def trend(self) -> str | None:
        gap = self.ma_gap_pct
        if gap is None:
            return None          # 数据不够 30 天就不下结论
        if gap > TREND_THRESHOLD_PCT:
            return "上升"
        if gap < -TREND_THRESHOLD_PCT:
            return "下降"
        return "横盘"


def _ma(closes: list[Decimal], n: int) -> Decimal | None:
    if len(closes) < n:
        return None
    window = closes[-n:]
    return sum(window, Decimal(0)) / n


def _change(closes: list[Decimal], n: int) -> Decimal | None:
    """最新收盘相对 n 根之前的收盘涨跌了百分之多少。数据不够 n+1 根就返回 None，不拿更短的区间凑。"""
    if len(closes) <= n or closes[-1 - n] == 0:
        return None
    return (closes[-1] / closes[-1 - n] - 1) * 100


def compute(daily: list[KlineBar]) -> Signals | None:
    """由日线（时间升序）计算信号。日线为空返回 None。

    注意 SteamDT 日线的最后一根是**当天尚未收盘**的那一根（实测末根时间就是请求当时），所以
    「7 日涨跌」严格说是「当前价相对 7 天前收盘」，页面上按这个意思写。
    """
    if not daily:
        return None
    return from_closes([b.close for b in daily])


def from_closes(closes: list[Decimal]) -> Signals | None:
    """同 compute，只要收盘价（按时间升序）。价位估值从自有日线表读收盘价时用它。"""
    if not closes:
        return None
    return Signals(last_close=closes[-1], ma7=_ma(closes, 7), ma30=_ma(closes, 30),
                   change_7d_pct=_change(closes, 7), change_30d_pct=_change(closes, 30), bars=len(closes))
