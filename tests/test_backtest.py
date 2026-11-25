"""价位二期回测（价位 spec §9.2、§9.3）：合成序列，不碰库。"""
from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal

from skinpulse.analysis import backtest as bt
from skinpulse.analysis import valuation as va
from skinpulse.analysis.valuation import ValuationConfig

CFG = ValuationConfig(min_bars=60, undercut=Decimal("0.01"), withdraw_fee=Decimal("0.01"),
                      fees={"YOUPIN": Decimal("0.01"), "BUFF": Decimal("0.015")},
                      target_margin=Decimal("0.15"), min_profit=Decimal("1"), bad_quantile=Decimal("0.10"))
START = date(2025, 10, 1)


def series(n=200, start=START, f=lambda i: 100 + 10 * math.sin(i / 5)):
    return [(start + timedelta(days=i), Decimal(str(round(f(i), 2)))) for i in range(n)]


def broad_for(closes):
    return {d: Decimal("1000") for d, _ in closes}


def _s(day, *, r7=0.0, r14=0.0, q10=-0.05, group="rifle", trend="横盘", rel="持平", pct=0.5, rebound=0.0, amp=1.5):
    return bt.Sample(day=day, template_id=1, group=group, q={0.10: q10, 0.075: q10 - 0.02, 0.05: q10 - 0.04, 0.025: q10 - 0.06},
                     r7=r7, r14=r14, trend=trend, rel=rel, pct=pct, rebound=rebound, amp=amp)


def test_samples_start_at_min_bars_step_and_need_14_bars_ahead():
    cl = series(100)
    ss = bt.samples(7, "rifle", cl, broad_for(cl), CFG, step=5)
    assert [s.day for s in ss] == [cl[t][0] for t in range(59, 100 - 14, 5)]
    assert all(s.template_id == 7 and s.group == "rifle" for s in ss)


def test_forward_returns_count_bars():
    cl = series(100)
    s = bt.samples(1, "x", cl, broad_for(cl), CFG, step=100)[0]   # 只有 t = 59 一个
    assert s.r7 == float(cl[66][1] / cl[59][1] - 1)
    assert s.r14 == float(cl[73][1] / cl[59][1] - 1)


def test_no_lookahead_labels_only_use_data_up_to_t():
    cl = series(120)
    crashed = cl[:60] + [(d, c / 3) for d, c in cl[60:]]        # t = 59 之后全部腰斩
    a = bt.samples(1, "x", cl, broad_for(cl), CFG, step=100)[0]
    b = bt.samples(1, "x", crashed, broad_for(cl), CFG, step=100)[0]
    assert (a.q, a.trend, a.rel, a.pct) == (b.q, b.trend, b.rel, b.pct)
    assert b.r7 < a.r7


def test_quantiles_are_ordered_and_main_matches_compute():
    cl = series(150)
    s = bt.samples(1, "x", cl, broad_for(cl), CFG, step=100)[0]
    assert s.q[0.025] <= s.q[0.05] <= s.q[0.075] <= s.q[0.10]


def test_too_short_series_gives_no_samples():
    cl = series(70)
    assert bt.samples(1, "x", cl, broad_for(cl), CFG) == []
    assert bt.stats([]) is None


def test_stats():
    d = date(2026, 1, 1)
    ss = [_s(d, r7=-0.10, r14=-0.10), _s(d, r7=0.0, r14=0.0), _s(d, r7=0.02, r14=0.04), _s(d, r7=-0.01, r14=-0.06)]
    st = bt.stats(ss, 0.10)
    assert st.n == 4 and st.cover == 0.25 and st.p_loss14 == 0.5
    assert st.r14_median == -0.03 and abs(st.r7_mean - (-0.0225)) < 1e-12
    assert bt.stats(ss).cover is None


def _cover_set(day, n, misses, **kw):
    """n 个样本，其中 misses 个跌破坏情况。"""
    return [_s(day, r7=-0.2 if i < misses else 0.0, **kw) for i in range(n)]


def test_r1_passes_and_fails_by_half_and_group():
    a, b = date(2026, 1, 1), date(2026, 6, 1)
    ok = _cover_set(a, 100, 10) + _cover_set(b, 100, 13)
    v = bt.check_r1(ok, 0.10)
    assert v.passed and len(v.reasons) == 2           # 大类样本不到 1000，不单独判
    bad = _cover_set(a, 100, 10) + _cover_set(b, 100, 14)
    v = bt.check_r1(bad, 0.10)
    assert not v.passed and v.reasons[1].startswith("✗ H2 覆盖率 14.0%")
    # 样本够多的大类单独判：步枪 16% 不过，哪怕两段合起来不超
    big = _cover_set(a, 600, 96, group="rifle") + _cover_set(b, 600, 96, group="rifle") + _cover_set(b, 3000, 0, group="pistol")
    v = bt.check_r1(big, 0.10)
    assert not v.passed and any(r.startswith("✗ 大类 rifle 覆盖率 16.0%") for r in v.reasons)


def test_best_quantile_picks_the_widest_passing_on_h1_then_validates():
    a, b = date(2026, 1, 1), date(2026, 6, 1)
    # r7 = −0.08：比 0.10 档（−0.05）、0.075 档（−0.07）低，比 0.05 档（−0.09）高
    ss = [_s(a, r7=-0.08) for _ in range(20)] + [_s(a) for _ in range(80)] + [_s(b) for _ in range(100)]
    q, v = bt.best_quantile(ss)
    assert q == 0.05 and v.passed
    assert bt.best_quantile([_s(a, r7=-1.0) for _ in range(10)]) == (None, None)


def test_r2_needs_both_halves():
    a, b = date(2026, 1, 1), date(2026, 6, 1)

    def half(day, flagged_r14):
        rest = [_s(day, r14=0.0) for _ in range(80)]
        flagged = [_s(day, r14=flagged_r14, trend="下降", rel="跑输") for _ in range(20)]
        return rest + flagged

    assert bt.check_r2(half(a, -0.10) + half(b, -0.10)).passed
    v = bt.check_r2(half(a, 0.05) + half(b, -0.10))
    assert not v.passed and v.reasons[0].startswith("✗ H1 r14 中位数")
    assert not bt.check_r2([_s(a)]).passed       # 没有被标出的样本


def test_extra_quantiles_match_a_full_compute():
    from dataclasses import replace

    from skinpulse.analysis.valuation import compute

    cl = series(150)
    s = bt.samples(1, "x", cl, broad_for(cl), CFG, step=100)[0]
    for q in (0.075, 0.05, 0.025):
        full = compute(cl[:60], broad_for(cl), {}, replace(CFG, bad_quantile=Decimal(str(q))))
        assert s.q[q] == float(full.q10)


def test_split_date_moves_the_halves():
    early, late = date(2026, 1, 1), date(2026, 6, 1)
    ss = _cover_set(early, 100, 30) + _cover_set(late, 100, 0)
    assert not bt.check_r1(ss, 0.10).passed                         # 默认分界：H1 30%
    v = bt.check_r1(ss, 0.10, split=date(2025, 6, 1))                # 全部算 H2：15% 也不过，但 H1 没样本
    assert v.reasons[0] == "✗ H1 没有样本"
    assert [len(bt.h1(ss, date(2026, 7, 1))), len(bt.h2(ss, date(2026, 7, 1)))] == [200, 0]


def test_r3_rebounded_samples_must_be_worse_in_both_halves():
    """R3（intent 2026-11-23）：从近 60 天最低涨了 ≥ 30% 的样本，14 天后更差，标准同 R2。"""
    a, b = date(2026, 1, 1), date(2026, 6, 1)

    def half(day, flagged_r14):
        rest = [_s(day, r14=0.0, rebound=0.1) for _ in range(80)]
        flagged = [_s(day, r14=flagged_r14, rebound=0.3) for _ in range(20)]     # 正好 30% 算标出
        return rest + flagged

    assert bt.check_r3(half(a, -0.10) + half(b, -0.10), 0.30).passed
    v = bt.check_r3(half(a, -0.10) + half(b, 0.05), 0.30)
    assert not v.passed and any(r.startswith("✗ H2") for r in v.reasons)
    assert not bt.check_r3([_s(a, rebound=None)], 0.30).passed         # 没有被标出的样本


def test_samples_carry_rebound_and_amp_from_compute():
    cl = series(120)
    s = bt.samples(1, "x", cl, broad_for(cl), CFG, step=100)[0]
    v = va.compute(cl[:60], broad_for(cl), {}, CFG)
    assert s.rebound == float(v.rebound) and s.amp == float(v.amp)
