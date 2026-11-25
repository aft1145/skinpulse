"""价位与扣费差价（纯逻辑，无 IO）。价位 spec §4，ADR-0014。

要回答的问题：**按这个价买进来，扣完手续费、提现费、让价，再扛过 7 天交易保护期，还能剩多少**。
一期只描述、不给建议——建议要等二期回测（在整体下跌的市场里，「在跌且跑输大盘」这类规则
一跑就是 150 多张「考虑撤单」，没回测过的阈值不能拿来指挥操作）。

标签的阈值全部写成有名有姓的常量，和 `signals.py` 一个做法：标签是结论，结论必须能被人追溯
到它是怎么得出来的。页面上标签总是和原始数字一起显示。

约定：
- 收盘价只用**已收盘**的日子（`closed_bars` 负责剔掉当天那根和 0 价的那根）。
- 算不出来就是 None，**绝不拿 0 或更短的区间凑**。
- 比率都是小数：0.15 = 15%；`ex30` 是百分点 / 100（0.08 = 8 个百分点）。
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from statistics import pvariance
from zoneinfo import ZoneInfo

from skinpulse.adapters.steamdt.models import KlineBar
from skinpulse.analysis import signals

# ---- 窗口 ----
YEAR_BARS = 365          # 「一年」按最近 365 根日线算（SteamDT 日线接口本来就给 365 根）
CHANGE_30, CHANGE_90 = 30, 90
BAD_WINDOW = 7           # 「坏情况」看 7 天：CS2 交易保护期 7 天，买进来至少要扛过这么久
ALIGN_BACK_DAYS = 3      # 大盘和单品按日期对齐，缺那天就往前找，最多 3 天；再远就不算

# ---- 位置 ----
# 一年收盘里排在最低 20% / 最高 20%。2026-09-27 实测 432 件里 295 件在低位——大盘一年跌了 49%，
# 所以这个标签单独看几乎不说明问题，页面上总是和「相对大盘」「趋势」放在一起。
LOW_PCT = Decimal("0.20")
HIGH_PCT = Decimal("0.80")

# ---- 相对大盘 ----
# 30 日涨跌和大盘差超过 3 个百分点才叫跑赢 / 跑输。饰品日线噪声大，1~2 个点的差每天都有。
REL_PP = Decimal("0.03")

# ---- 波动 ----
# 日对数收益的 EWMA 标准差 × √7。λ=0.94 是 RiskMetrics 的日频取值；外部实测里 GARCH 族在
# 样本外都没赢过 EWMA（intent「调研结论」）。前 20 个收益的总体方差做初值。
EWMA_LAMBDA = 0.94
EWMA_SEED = 20
VOL_LOW = Decimal("0.05")      # 7 天典型波动 < 5% 叫「波动小」
VOL_HIGH = Decimal("0.12")     # > 12% 叫「波动大」；2026-09-27 实测中位数约 6%

# ---- 出手难易 ----
# 悠悠 + BUFF 在售件数、悠悠买卖价差（(在售最低 − 最高求购) / 在售最低）。
LIQ_GOOD_LISTED = 100
LIQ_GOOD_SPREAD = Decimal("0.05")
LIQ_HARD_LISTED = 20
LIQ_HARD_SPREAD = Decimal("0.20")

# ---- 其他 ----
TICK = Decimal("0.01")         # 悠悠出价的最小单位：抢第 1 就是榜首价 + 0.01
MONEY = Decimal("0.01")
RATIO = Decimal("0.000001")


@dataclass(frozen=True)
class ValuationConfig:
    """和 `config.ValuationSettings` 对应的纯数据，全部 Decimal，省得纯逻辑依赖 pydantic。"""

    min_bars: int
    undercut: Decimal
    withdraw_fee: Decimal
    fees: Mapping[str, Decimal]
    target_margin: Decimal
    min_profit: Decimal
    bad_quantile: Decimal
    # 到手价只在这些平台里取；None = `fees` 里的全部（intent 2026-11-23：用户只在悠悠出货）
    exit_platforms: tuple[str, ...] | None = None
    # 「刚从低点涨上来」：现价 ÷ 近 rebound_window 根最低收盘 − 1 ≥ rebound_max 时选品判观望（R3）
    rebound_window: int = 60
    rebound_max: Decimal = Decimal("0.3")

    @classmethod
    def from_settings(cls, s) -> ValuationConfig:
        d = lambda x: Decimal(str(x))  # noqa: E731  float 先转字符串，0.015 不会变成 0.01499…
        return cls(min_bars=s.min_bars, undercut=d(s.undercut), withdraw_fee=d(s.withdraw_fee),
                   fees={k: d(v) for k, v in s.fees.items()}, target_margin=d(s.target_margin),
                   min_profit=d(s.min_profit), bad_quantile=d(s.bad_quantile),
                   exit_platforms=tuple(s.exit_platforms), rebound_window=s.rebound_window,
                   rebound_max=d(s.rebound_max))


@dataclass(frozen=True)
class Quote:
    """某平台某天的价格快照（来自 price_daily）。价格 None 或 ≤ 0 = 没有数据。"""

    sell: Decimal | None
    sell_count: int
    bid: Decimal | None


@dataclass(frozen=True)
class Valuation:
    bars: int
    pct: Decimal | None = None
    lo: Decimal | None = None
    hi: Decimal | None = None
    cur: Decimal | None = None
    trend: str | None = None
    c30: Decimal | None = None
    c90: Decimal | None = None
    b30: Decimal | None = None
    b90: Decimal | None = None
    ex30: Decimal | None = None
    rel: str | None = None
    vol7: Decimal | None = None
    q10: Decimal | None = None
    liq: str | None = None
    listed: int | None = None
    spread: Decimal | None = None
    exit: Decimal | None = None
    exit_where: str | None = None
    exit_sell: Decimal | None = None
    cap: Decimal | None = None
    rebound: Decimal | None = None   # 现价 ÷ 近 rebound_window 根最低收盘 − 1
    amp: Decimal | None = None       # 一年最高 ÷ 一年最低（「炒过」，只作描述）


# 只有选品用、`valuation` 表里没有列的字段（每晚估值写库时跳过）
PICK_ONLY_FIELDS = frozenset({"rebound", "amp"})


@dataclass(frozen=True)
class Margins:
    m_my: Decimal | None
    m_top: Decimal | None
    m_bad: Decimal | None


# ---------------------------------------------------------------- 日线

def closed_bars(bars: Iterable[KlineBar], tz: str,
                day: date) -> list[tuple[date, Decimal, Decimal, Decimal, Decimal]]:
    """K 线 → [(本地日期, 开, 收, 高, 低)]，按日期升序，只留 ≤ day 的（已收盘的）日子。

    - 日期按 `tz` 的本地日期取。单品日线打在当天 00:00（上海），大盘日线打在当天 23:55，
      两种都能落到同一天。
    - 同一天出现两根时留后来的那根。
    - 任一价格 ≤ 0 的那根整根不要：0 = 没有数据（解析器用的是 `_dec`，0 会原样进来）。
    """
    zone = ZoneInfo(tz)
    by_day: dict[date, tuple] = {}
    for b in sorted(bars, key=lambda b: b.ts):
        d = b.ts.astimezone(zone).date()
        if d > day or min(b.open, b.close, b.high, b.low) <= 0:
            continue
        by_day[d] = (d, b.open, b.close, b.high, b.low)
    return [by_day[d] for d in sorted(by_day)]


# ---------------------------------------------------------------- 标签

def pct_label(pct: Decimal | None) -> str | None:
    if pct is None:
        return None
    if pct <= LOW_PCT:
        return "一年低位"
    if pct >= HIGH_PCT:
        return "一年高位"
    return "一年中位"


def rel_label(ex30: Decimal | None) -> str | None:
    if ex30 is None:
        return None
    if ex30 > REL_PP:
        return "跑赢"
    if ex30 < -REL_PP:
        return "跑输"
    return "持平"


def vol_label(vol7: Decimal | None) -> str | None:
    if vol7 is None:
        return None
    if vol7 < VOL_LOW:
        return "波动小"
    if vol7 > VOL_HIGH:
        return "波动大"
    return "波动中"


def liq_label(listed: int, spread: Decimal | None) -> str:
    if listed >= LIQ_GOOD_LISTED and spread is not None and spread <= LIQ_GOOD_SPREAD:
        return "好出手"
    if listed < LIQ_HARD_LISTED or (spread is not None and spread > LIQ_HARD_SPREAD):
        return "难出手"
    return "一般"


# ---------------------------------------------------------------- 计算

def _r(x: Decimal | None) -> Decimal | None:
    return None if x is None else x.quantize(RATIO)


def _change(closes: list[Decimal], n: int) -> Decimal | None:
    if len(closes) <= n or closes[-1 - n] <= 0:
        return None
    return closes[-1] / closes[-1 - n] - 1


def _on_or_before(series: Mapping[date, Decimal], d: date) -> Decimal | None:
    for k in range(ALIGN_BACK_DAYS + 1):
        v = series.get(d - timedelta(days=k))
        if v is not None and v > 0:
            return v
    return None


def _broad_change(broad: Mapping[date, Decimal], days: list[date], n: int) -> Decimal | None:
    """大盘在「单品这 n 根」对应的同一段日期里的涨跌：按日期对齐，不按根数。"""
    if len(days) <= n:
        return None
    end, start = _on_or_before(broad, days[-1]), _on_or_before(broad, days[-1 - n])
    if end is None or start is None:
        return None
    return end / start - 1


def _ewma_vol(closes: list[Decimal]) -> float | None:
    r = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    if len(r) <= EWMA_SEED:
        return None
    v = pvariance(r[:EWMA_SEED])
    for x in r[EWMA_SEED:]:
        v = EWMA_LAMBDA * v + (1 - EWMA_LAMBDA) * x * x
    return math.sqrt(v)


def _quantile(xs: list[Decimal], q: Decimal) -> Decimal | None:
    """最近秩次，不插值（和 platform_gap.summarize 同一个口径）。"""
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    i = min(n - 1, max(0, round(float(q) * (n - 1))))
    return xs[i]


def _exit(quotes: Mapping[str, Quote], cfg: ValuationConfig) -> tuple[Decimal, str, Decimal] | None:
    """卖出去到手多少：每个有在售价的平台各算一遍，取多的那家。"""
    best = None
    for platform, fee in cfg.fees.items():
        if cfg.exit_platforms is not None and platform not in cfg.exit_platforms:
            continue
        q = quotes.get(platform)
        if q is None or q.sell is None or q.sell <= 0:
            continue
        net = (q.sell * (1 - cfg.undercut) * (1 - fee) * (1 - cfg.withdraw_fee)).quantize(MONEY)
        if best is None or net > best[0]:
            best = (net, platform, q.sell)
    return best


def compute(closes: list[tuple[date, Decimal]], broad: Mapping[date, Decimal],
            quotes: Mapping[str, Quote], cfg: ValuationConfig) -> Valuation:
    """一个模板一天的估值。`closes` 是已收盘的 (本地日期, 收盘价)，按日期升序；`broad` 是大盘
    收盘价按日期索引；`quotes` 是当天 price_daily 里悠悠、BUFF 两行。"""
    year = [(d, c) for d, c in closes if c > 0][-YEAR_BARS:]
    days = [d for d, _ in year]
    cs = [c for _, c in year]
    out: dict = {"bars": len(cs)}

    ex = _exit(quotes, cfg)
    if ex is not None:
        out["exit"], out["exit_where"], out["exit_sell"] = ex

    yp = quotes.get("YOUPIN")
    if yp is not None and yp.sell is not None and yp.sell > 0:
        listed = yp.sell_count + sum(q.sell_count for p, q in quotes.items() if p != "YOUPIN" and p in cfg.fees)
        spread = (yp.sell - yp.bid) / yp.sell if yp.bid is not None and yp.bid > 0 else None
        out.update(listed=listed, spread=_r(spread), liq=liq_label(listed, spread))

    if len(cs) >= cfg.min_bars:
        cur = cs[-1]
        out["pct"] = _r(Decimal(sum(1 for c in cs if c <= cur)) / len(cs))
        out["lo"], out["hi"], out["cur"] = min(cs), max(cs), cur
        out["amp"] = _r(max(cs) / min(cs))
        if len(cs) >= cfg.rebound_window:
            out["rebound"] = _r(cur / min(cs[-cfg.rebound_window:]) - 1)
        sig = signals.from_closes(cs)
        out["trend"] = sig.trend if sig else None
        c30, c90 = _change(cs, CHANGE_30), _change(cs, CHANGE_90)
        b30, b90 = _broad_change(broad, days, CHANGE_30), _broad_change(broad, days, CHANGE_90)
        ex30 = c30 - b30 if c30 is not None and b30 is not None else None
        out.update(c30=_r(c30), c90=_r(c90), b30=_r(b30), b90=_r(b90), ex30=_r(ex30), rel=rel_label(ex30))
        vol = _ewma_vol(cs)
        out["vol7"] = None if vol is None else _r(Decimal(str(vol * math.sqrt(BAD_WINDOW))))
        r7 = [cs[i + BAD_WINDOW] / cs[i] - 1 for i in range(len(cs) - BAD_WINDOW)]
        q10 = _r(_quantile(r7, cfg.bad_quantile))
        out["q10"] = q10
        if ex is not None and q10 is not None:
            # 二期「建议」用的求购价上限，一期只存不显示：碰上坏情况、扣完目标差价和最低利润还不亏。
            # 可能是负数——那表示按规则怎么买都不够，照实记，不改成 0（0 = 没有数据）。
            bad_exit = ex[0] * (1 + q10)
            out["cap"] = min(bad_exit / (1 + cfg.target_margin), bad_exit - cfg.min_profit).quantize(MONEY)
    return Valuation(**out)


def margins(exit: Decimal | None, q10: Decimal | None, my_price: Decimal | None,
            top_price: Decimal | None, rank: int | None) -> Margins:
    """按单算的三项差价（接口里现算，改价后不用等第二天）。

    - m_my：按我的价成交后的扣费差价
    - m_top：抢到第 1（榜首价 + 0.01）后的扣费差价；已是第 1 或没有榜首价时 None
    - m_bad：按我的价成交、7 天后碰上坏情况的扣费差价
    """
    if exit is None or my_price is None or my_price <= 0:
        m_my = m_bad = None
    else:
        m_my = _r(exit / my_price - 1)
        m_bad = None if q10 is None else _r(exit * (1 + q10) / my_price - 1)
    if exit is None or rank == 1 or top_price is None or top_price <= 0:
        m_top = None
    else:
        m_top = _r(exit / (top_price + TICK) - 1)
    return Margins(m_my=m_my, m_top=m_top, m_bad=m_bad)
