"""K 线纵轴范围与影线截断（看板 spec §4.2、§5；ADR-0013）：范围由后端算好返回，前端只负责画。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from skinpulse.adapters.steamdt.models import KlineBar
from skinpulse.analysis.kline import WINDOWS, YRange, windows, y_range

T0 = datetime(2026, 1, 1, 16, 0, tzinfo=timezone.utc)


def _bar(i, o, c, h, l):  # noqa: E741 - 与 KlineBar 的字段顺序（开、收、高、低）一致
    return KlineBar(ts=T0 + timedelta(days=i), open=Decimal(o), close=Decimal(c), high=Decimal(h), low=Decimal(l))


def _flat(closes):
    return [_bar(i, c, c, c, c) for i, c in enumerate(closes)]


def test_a_wild_wick_is_clipped_and_disclosed():
    """实测暗影双匕 | 北方森林（崭新）价格在 600~1000，却有两根日线的最高价是 11178（多半是错价挂单）。
    按影线定纵轴，图被撑到一万多、真实走势压成一条直线；按实体定纵轴并注明截断，图才能看。"""
    bars = _flat([900, 910, 905, 920, 915]) + [_bar(5, 918, 921, 11178, 915)]
    r = y_range(bars)
    assert r.hi < 1000 and r.lo >= 900 - 11
    assert r.clipped == 1
    assert r.extreme == Decimal(11178)


def test_normal_wicks_are_kept_whole():
    bars = [_bar(i, 100, 102, 103, 99) for i in range(5)]
    assert y_range(bars) == YRange(lo=Decimal(99), hi=Decimal(103), clipped=0, extreme=None)


def test_a_wild_low_wick_reports_the_low_extreme():
    bars = _flat([900, 910, 905, 920]) + [_bar(4, 912, 915, 916, 12)]
    r = y_range(bars)
    assert r.lo > 800 and r.clipped == 1 and r.extreme == Decimal(12)


def test_flat_series_does_not_divide_by_zero():
    r = y_range(_flat([5, 5, 5]))
    assert r.lo < 5 < r.hi and r.clipped == 0


def test_empty_series_has_no_range():
    assert y_range([]) is None


def test_each_window_gets_its_own_range():
    """1 月、3 月、1 年分别算：一年前的高价不能把最近一个月的图压扁。"""
    bars = _flat([200] * 300 + [100] * 65)
    w = windows(bars)
    assert set(w) == set(WINDOWS) == {"1m", "3m", "1y"}
    assert w["1m"].hi == Decimal(101) and w["1m"].lo == Decimal(99)    # 全平：上下各留 1
    assert w["1y"].hi == Decimal(200)
    assert w["3m"].hi == Decimal(200)                                  # 最近 90 根里还有 25 根 200


def test_windows_on_a_short_series_use_what_there_is():
    w = windows(_flat([10, 11, 12]))
    assert w["1m"] == w["3m"] == w["1y"]
