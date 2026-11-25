"""价位三期选品（价位 spec §9b）：圈池子和结论。"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from skinpulse.analysis import pick as pk
from skinpulse.analysis.valuation import Valuation

CATALOG = [
    (1, "AK-47 | Slate (Field-Tested)"),
    (2, "StatTrak™ AK-47 | Slate (Field-Tested)"),
    (3, "Souvenir AWP | Pit Viper (Minimal Wear)"),
    (4, "★ Karambit | Doppler (Factory New)"),
    (5, "★ Sport Gloves | Vice (Field-Tested)"),
    (6, "Sticker | Clown Nose"),
    (7, "Spectrum Case"),
    (8, "'Two Times' McCoy | TACP Cavalry"),
    (9, "AK-47 | Redline (Minimal Wear)"),
]


@pytest.mark.parametrize("name, expected", [
    ("AK-47 | Slate (Field-Tested)", pk.ParsedName("rifle", "AK-47", "ft", "normal")),
    ("StatTrak™ AK-47 | Slate (Field-Tested)", pk.ParsedName("rifle", "AK-47", "ft", "stattrak")),
    ("Souvenir AWP | Pit Viper (Minimal Wear)", pk.ParsedName("sniper", "AWP", "mw", "souvenir")),
    ("★ StatTrak™ Karambit | Doppler (Factory New)", pk.ParsedName("knife", "Karambit", "fn", "stattrak")),
    ("Sticker | Clown Nose", pk.ParsedName("sticker", None, "none", "normal")),
    ("", pk.ParsedName("other", None, "none", "normal")),
])
def test_parse_name(name, expected):
    assert pk.parse_name(name) == expected


def test_default_pool_excludes_stickers_cases_and_other():
    assert pk.select_pool(CATALOG, pk.PoolFilter()) == [1, 2, 3, 4, 5, 8, 9]
    assert {"sticker", "charm", "container", "patch", "graffiti", "other"} == pk.DEFAULT_EXCLUDED


def test_pool_filters_by_weapon_wear_and_version():
    f = pk.PoolFilter.from_dict({"groups": ["rifle"], "subs": ["AK-47"], "wears": ["ft"], "versions": ["normal"]})
    assert pk.select_pool(CATALOG, f) == [1]
    f = pk.PoolFilter.from_dict({"groups": ["rifle", "sniper", "knife"], "versions": ["stattrak", "souvenir"]})
    assert pk.select_pool(CATALOG, f) == [2, 3]


def test_filter_round_trip_and_validation():
    d = {"groups": ["rifle"], "subs": [], "wears": ["ft", "mw"], "versions": [], "price_min": 1, "price_max": 300}
    f = pk.PoolFilter.from_dict(d)
    assert f.price_min == D("1") and pk.PoolFilter.from_dict(f.to_dict()) == f
    for bad, msg in (({"groups": []}, "至少选一个大类"), ({"groups": ["guns"]}, "不认识的值"),
                     ({"groups": ["rifle"], "wears": ["new"]}, "不认识的值"), ({"groups": ["rifle"], "x": 1}, "不认识的条件"),
                     ({"groups": ["rifle"], "price_min": -1}, "负数"), ({"groups": ["rifle"], "price_min": "a"}, "数字"),
                     ({"groups": ["rifle"], "price_min": 5, "price_max": 1}, "下限"), (None, "JSON 对象"),
                     ({"groups": "rifle"}, "字符串列表")):
        with pytest.raises(pk.PoolError, match=msg):
            pk.PoolFilter.from_dict(bad)


def test_price_range():
    assert pk.PoolFilter().price_ok(None)                     # 没设区间：没价的也留着
    f = pk.PoolFilter(price_min=D("1"), price_max=D("10"))
    assert f.price_ok(D("1")) and f.price_ok(D("10")) and not f.price_ok(D("10.01")) and not f.price_ok(D("0.99"))
    assert not f.price_ok(None) and not f.price_ok(D("0"))   # 设了区间：没价的看不出在不在里面


def V(**kw):
    base = dict(bars=365, pct=D("0.3"), q10=D("-0.1"), exit=D("100"), cap=D("78.26"), liq="一般")
    base.update(kw)
    return Valuation(**base)


def test_verdict_order_and_boundaries():
    assert pk.verdict(V(pct=None), D("50")).kind == pk.NONE
    assert pk.verdict(V(exit=None, cap=None), D("50")).kind == pk.NONE
    assert pk.verdict(V(cap=D("-0.5")), D("50")).rule.startswith("上限 -0.50")
    assert pk.verdict(V(cap=D("0")), D("50")).kind == pk.NO
    assert pk.verdict(V(liq="难出手"), D("50")).kind == pk.NO
    w = pk.verdict(V(), None)
    assert w.kind == pk.WAIT and "没有悠悠求购价" in w.rule
    assert pk.verdict(V(), D("0")).kind == pk.WAIT               # 0 = 没有数据
    # cap 78.26：最高求购 86.95 时 90% = 78.255，够得着
    assert pk.verdict(V(), D("86.95")).kind == pk.GO
    assert pk.verdict(V(), D("86.96")).kind == pk.WAIT          # 90% = 78.264 > 78.26


def test_go_bid_is_top_plus_tick_or_cap():
    first = pk.verdict(V(), D("50"))
    assert first.kind == pk.GO and first.first and first.bid == D("50.01") and "排第 1" in first.rule
    assert first.margin == (D("90") / D("50.01") - 1).quantize(D("0.000001"))
    below = pk.verdict(V(), D("80"))                             # 80 × 0.9 = 72 ≤ 78.26 < 80.01
    assert below.kind == pk.GO and not below.first and below.bid == D("78.26") and "之下" in below.rule
    assert below.label == "可以挂"


def test_just_rebounded_from_the_recent_low_waits():
    """R3（intent 2026-11-23，回测通过）：从近 60 天低点涨了 ≥ 30% 的，先观望；排在「不建议」之后。"""
    r = (60, D("0.3"))
    w = pk.verdict(V(rebound=D("0.712")), D("50"), rebound=r)
    assert w.kind == pk.WAIT and w.rule == "从近 60 天低点涨了 71%，刚涨上来的先不追"
    assert pk.verdict(V(rebound=D("0.3")), D("50"), rebound=r).kind == pk.WAIT        # 正好 30% 算
    assert pk.verdict(V(rebound=D("0.299")), D("50"), rebound=r).kind == pk.GO
    assert pk.verdict(V(rebound=None), D("50"), rebound=r).kind == pk.GO             # 日线不够：不拦
    assert pk.verdict(V(rebound=D("0.9"), liq="难出手"), D("50"), rebound=r).kind == pk.NO
    assert pk.verdict(V(rebound=D("0.9")), D("50")).kind == pk.GO                    # 没给门槛：不判
