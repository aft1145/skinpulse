"""analysis/valuation：价位、走势、出手难易、扣费差价（价位 spec §4）。"""
import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from skinpulse.adapters.steamdt.models import KlineBar, parse_kline
from skinpulse.analysis import valuation as va
from tests.conftest import load_fixture

TZ = "Asia/Shanghai"
CFG = va.ValuationConfig(min_bars=60, undercut=D("0.01"), withdraw_fee=D("0.01"),
                         fees={"YOUPIN": D("0.01"), "BUFF": D("0.015")},
                         target_margin=D("0.15"), min_profit=D("1"), bad_quantile=D("0.10"))


def _days(n, start=date(2025, 9, 1)):
    return [start + timedelta(days=i) for i in range(n)]


def _series(closes, start=date(2025, 9, 1)):
    return list(zip(_days(len(closes), start), [D(str(c)) for c in closes]))


# ---------- closed_bars ----------

def _bar(ts, o="10", c="10", h="10", lo="10"):
    return KlineBar(ts=ts, open=D(o), close=D(c), high=D(h), low=D(lo))


def test_closed_bars_uses_local_dates_and_drops_unclosed_and_zero():
    utc = timezone.utc
    bars = [
        _bar(datetime(2026, 9, 24, 16, 0, tzinfo=utc)),                 # 上海 09-25 00:00
        _bar(datetime(2026, 9, 25, 15, 55, 10, tzinfo=utc)),            # 上海 09-25 23:55（大盘的打法）→ 同一天，后来的那根留下
        _bar(datetime(2026, 9, 25, 16, 0, tzinfo=utc), c="0"),          # 上海 09-26，收盘 0 → 没有数据，整根丢
        _bar(datetime(2026, 9, 26, 16, 0, tzinfo=utc)),                 # 上海 09-27：D=09-26 时还没收盘
    ]
    out = va.closed_bars(bars, TZ, date(2026, 9, 26))
    assert [d for d, *_ in out] == [date(2026, 9, 25)]
    assert out[0][1:] == (D("10"), D("10"), D("10"), D("10"))


# ---------- 价位 ----------

def test_insufficient_bars_leaves_position_null_but_still_prices_exit():
    quotes = {"YOUPIN": va.Quote(sell=D("100"), sell_count=50, bid=D("98"))}
    v = va.compute(_series([10] * 59), {}, quotes, CFG)
    assert v.bars == 59
    assert (v.pct, v.lo, v.hi, v.cur, v.trend, v.q10, v.vol7, v.c30) == (None,) * 8
    assert v.exit == D("97.03") and v.exit_where == "YOUPIN"     # 100 × 0.99 × 0.99 × 0.99
    assert va.compute(_series([10] * 60), {}, quotes, CFG).pct == D("1")


def test_percentile_and_labels():
    # 100 根：1..100 去掉 20，最后一根收 20 → ≤20 的有 20 根（包括它自己），分位正好 0.2
    closes = [x for x in range(1, 101) if x != 20] + [20]
    v = va.compute(_series(closes), {}, {}, CFG)
    assert v.pct == D("0.2") and (v.lo, v.hi, v.cur) == (D("1"), D("100"), D("20"))
    assert va.pct_label(v.pct) == "一年低位"
    assert va.pct_label(D("0.200001")) == "一年中位"
    assert va.pct_label(D("0.8")) == "一年高位"
    assert va.pct_label(None) is None


def test_only_last_365_bars_count():
    closes = [1000] * 100 + [10] * 365   # 一年以前的高价不算
    v = va.compute(_series(closes), {}, {}, CFG)
    assert v.bars == 365 and v.hi == D("10")


def test_trend_reuses_signals():
    up = [100] * 40 + [110] * 7        # MA7 110，MA30 约 102 → 偏离 > 2%
    assert va.compute(_series([100] * 40 + up), {}, {}, CFG).trend == "上升"
    assert va.compute(_series([100] * 80), {}, {}, CFG).trend == "横盘"


# ---------- 相对大盘 ----------

def test_change_vs_market_aligned_by_date():
    days = _days(100)
    closes = [D("100")] * 70 + [D("110")] * 30        # 最新 110，30 根之前 100 → +10%
    series = list(zip(days, closes))
    broad = {d: D("1000") for d in days}
    broad[days[-1]] = D("1020")                        # 大盘 +2%
    v = va.compute(series, broad, {}, CFG)
    assert v.c30 == D("0.1") and v.b30 == D("0.02") and v.ex30 == D("0.08") and v.rel == "跑赢"
    # 大盘缺了那天：往前找最近的一天（3 天内）
    del broad[days[-1 - 30]]
    broad[days[-1 - 30] - timedelta(days=2)] = D("1000")
    assert va.compute(series, broad, {}, CFG).b30 == D("0.02")
    # 往前 3 天都没有：算不了，不拿别的日子凑
    for k in range(1, 4):
        broad.pop(days[-1 - 30] - timedelta(days=k), None)
    v2 = va.compute(series, broad, {}, CFG)
    assert v2.b30 is None and v2.ex30 is None and v2.rel is None and v2.c30 == D("0.1")


def test_rel_labels():
    assert va.rel_label(D("0.031")) == "跑赢"
    assert va.rel_label(D("0.03")) == "持平"
    assert va.rel_label(D("-0.031")) == "跑输"
    assert va.rel_label(None) is None


# ---------- 波动、坏情况 ----------

def test_ewma_vol_with_alternating_returns():
    # 100、110 交替：对数收益 ±ln(1.1)，前 20 个做初值的方差就是 ln(1.1)²，之后 EWMA 保持不变
    closes = [100 if i % 2 == 0 else 110 for i in range(81)]
    v = va.compute(_series(closes), {}, {}, CFG)
    assert float(v.vol7) == pytest.approx(math.log(1.1) * math.sqrt(7), rel=1e-4)
    assert va.vol_label(D("0.049")) == "波动小"
    assert va.vol_label(D("0.05")) == "波动中"
    assert va.vol_label(D("0.121")) == "波动大"


def test_q10_nearest_rank():
    # 20 天 100、20 天 80：7 天涨跌里有 7 个 −20%，其余是 0（共 33 个）
    closes = [100] * 20 + [80] * 20
    cfg = va.ValuationConfig(**{**CFG.__dict__, "min_bars": 30})
    assert va.compute(_series(closes), {}, {}, cfg).q10 == D("-0.2")        # 第 round(0.1×32)=3 个
    cfg3 = va.ValuationConfig(**{**CFG.__dict__, "min_bars": 30, "bad_quantile": D("0.3")})
    assert va.compute(_series(closes), {}, {}, cfg3).q10 == D("0")         # 第 round(0.3×32)=10 个


# ---------- 出手难易、到手价 ----------

def test_liquidity_labels():
    good = {"YOUPIN": va.Quote(D("100"), 80, D("96")), "BUFF": va.Quote(D("101"), 30, None)}
    assert va.compute(_series([10] * 60), {}, good, CFG).liq == "好出手"        # 110 件、价差 4%
    thin = {"YOUPIN": va.Quote(D("100"), 10, D("99")), "BUFF": va.Quote(D("101"), 5, None)}
    assert va.compute(_series([10] * 60), {}, thin, CFG).liq == "难出手"        # 只有 15 件
    wide = {"YOUPIN": va.Quote(D("100"), 200, D("70"))}
    v = va.compute(_series([10] * 60), {}, wide, CFG)
    assert (v.liq, v.spread, v.listed) == ("难出手", D("0.3"), 200)             # 价差 30%
    mid = {"YOUPIN": va.Quote(D("100"), 50, None)}
    assert va.compute(_series([10] * 60), {}, mid, CFG).liq == "一般"           # 没有求购价，价差不知道
    none = va.compute(_series([10] * 60), {}, {"BUFF": va.Quote(D("101"), 50, None)}, CFG)
    assert (none.liq, none.listed, none.spread) == (None, None, None)          # 没有悠悠在售价


def test_exit_takes_the_better_platform_and_ignores_zero():
    quotes = {"YOUPIN": va.Quote(D("100"), 10, None), "BUFF": va.Quote(D("105"), 10, None)}
    v = va.compute(_series([10] * 60), {}, quotes, CFG)
    # 悠悠 100×0.99×0.99×0.99 = 97.03；BUFF 105×0.99×0.985×0.99 = 101.37
    assert (v.exit, v.exit_where, v.exit_sell) == (D("101.37"), "BUFF", D("105"))
    zero = {"YOUPIN": va.Quote(D("0"), 0, None)}
    v0 = va.compute(_series([10] * 60), {}, zero, CFG)
    assert (v0.exit, v0.exit_where, v0.exit_sell) == (None, None, None)


def test_cap():
    closes = [100] * 20 + [80] * 20 + [80] * 30          # q10 = −20%
    quotes = {"YOUPIN": va.Quote(D("100"), 10, None)}
    v = va.compute(_series(closes), {}, quotes, CFG)
    assert v.q10 == D("-0.2")
    bad_exit = D("97.03") * D("0.8")                      # 77.624
    assert v.cap == min(bad_exit / D("1.15"), bad_exit - 1).quantize(D("0.01"))


# ---------- 按单算的差价 ----------

def test_margins():
    m = va.margins(exit=D("97.03"), q10=D("-0.1"), my_price=D("50"), top_price=D("80"), rank=3)
    assert m.m_my == D("0.9406")                      # 97.03 / 50 − 1
    assert m.m_top == (D("97.03") / D("80.01") - 1).quantize(D("0.000001"))
    assert m.m_bad == (D("97.03") * D("0.9") / 50 - 1).quantize(D("0.000001"))
    assert va.margins(D("97.03"), None, D("50"), D("80"), 1).m_top is None        # 已是第 1
    assert va.margins(D("97.03"), None, D("50"), None, 3).m_top is None           # 没有榜首价
    assert va.margins(D("97.03"), None, D("50"), D("80"), 3).m_bad is None        # 没有坏情况
    none = va.margins(None, D("-0.1"), D("50"), D("80"), 3)
    assert (none.m_my, none.m_top, none.m_bad) == (None, None, None)
    assert va.margins(D("97.03"), None, D("0"), D("80"), 3).m_my is None          # 价格 0 = 没有数据
    assert va.margins(D("50"), None, D("60"), D("80"), 3).m_top < 0               # 会亏


# ---------- 真实数据 ----------

def test_real_youpin_year_characterization():
    item = parse_kline(load_fixture("steamdt/kline_item_daily_youpin_1y.json")["data"])
    broad = parse_kline(load_fixture("steamdt/kline_broad_daily_1y.json")["data"])
    last_day = item[-1].ts.astimezone(timezone(timedelta(hours=8))).date()
    day = last_day - timedelta(days=1)                   # 最后一根还没收盘
    bars = va.closed_bars(item, TZ, day)
    bmap = {d: c for d, _o, c, _h, _l in va.closed_bars(broad, TZ, day)}
    v = va.compute([(d, c) for d, _o, c, _h, _l in bars], bmap, {}, CFG)
    assert bars[-1][0] == day and v.bars == len(bars) >= 300
    assert D("0") < v.pct <= D("1") and v.lo <= v.cur <= v.hi
    assert v.trend in ("上升", "横盘", "下降") and v.rel in ("跑赢", "持平", "跑输")
    assert v.q10 < 0 < v.vol7
    assert v.b30 is not None and v.b90 is not None
    # 快照（AK-47 红线久经，2026-09-27 取）：算法有意改动时一起更新这几个数
    assert (day, v.bars, v.pct, v.cur, v.trend, v.rel) == (date(2026, 9, 26), 364, D("0.016484"), D("175.5"), "下降", "持平")
    assert (v.c30, v.b30, v.ex30, v.q10, v.vol7) == (D("-0.0729"), D("-0.043138"), D("-0.029762"), D("-0.043253"), D("0.036115"))


# ---------- 从近期低点反弹、振幅、只按指定平台算到手价（intent 2026-11-23-pick-hype-and-filters）----------

def test_rebound_from_the_recent_low_and_yearly_amplitude():
    closes = [100] * 30 + [40] * 10 + [50] * 50 + [52]       # 近 60 根最低 40（还在窗口里）
    v = va.compute(_series(closes), {}, {}, CFG)
    assert v.rebound == D("0.3") and v.amp == D("2.5")       # 52/40 − 1；100/40
    later = [100] * 30 + [40] * 10 + [50] * 70 + [52]        # 40 已经掉出 60 根窗口：最低是 50
    assert va.compute(_series(later), {}, {}, CFG).rebound == D("0.04")


def test_rebound_and_amp_need_enough_bars_and_are_never_zero_filled():
    v = va.compute(_series([10] * 59), {}, {}, CFG)
    assert (v.rebound, v.amp) == (None, None)


def test_exit_only_counts_the_configured_platforms():
    quotes = {"YOUPIN": va.Quote(D("100"), 10, None), "BUFF": va.Quote(D("105"), 10, None)}
    cfg = va.ValuationConfig(**{**CFG.__dict__, "exit_platforms": ("YOUPIN",)})
    v = va.compute(_series([10] * 60), {}, quotes, cfg)
    assert (v.exit, v.exit_where) == (D("97.03"), "YOUPIN")            # BUFF 到手更多也不算
    assert va.compute(_series([10] * 60), {}, {"BUFF": quotes["BUFF"]}, cfg).exit is None


def test_settings_default_exit_platform_is_youpin_only():
    from skinpulse.config import ValuationSettings

    cfg = va.ValuationConfig.from_settings(ValuationSettings())
    assert cfg.exit_platforms == ("YOUPIN",) and cfg.rebound_window == 60 and cfg.rebound_max == D("0.3")
