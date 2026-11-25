"""难以成交（难以成交 spec §1）：离第 1 或离市价太远。"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from skinpulse.analysis.reach import market_ref, reach_kwargs, unreachable
from skinpulse.config import AlertSettings

LIMITS = {"max_gap_ratio": 3.0, "max_market_ratio": 2.0, "min_market_gap": 20.0}


def u(my, top=None, sell=None, depth=None, bid=None, **kw):
    lim = {**LIMITS, **kw}
    return unreachable(None if my is None else D(my), None if top is None else D(top),
                       None if sell is None else D(sell), depth, None if bid is None else D(bid), **lim)


def test_market_ref_sources():
    assert market_ref(D("49"), 10, D("60")) == (D("49"), "sell")       # 有人在卖：在售最低
    assert market_ref(None, 0, D("1500")) == (D("1500"), "bid")       # 没人在卖：别处买盘
    assert market_ref(None, None, D("1500")) == (None, None)          # 没查过在售：不拿别处买盘凑
    assert market_ref(D("0"), 0, D("0")) == (None, None)              # 0 = 没有数据（坑 3）
    assert market_ref(None, 0, None) == (None, None)


@pytest.mark.parametrize("args, want", [
    # intent 2026-11-25 里的真实样本
    (dict(my="10", top="10", sell="4871", depth=1), "market"),         # 2013 DreamHack 纪念包
    (dict(my="25", top="25", sell="49", depth=10), None),              # CZ75 猛虎：1.96 倍
    (dict(my="7", top="7", sell="9.46", depth=3), None),               # 音乐盒：差 ¥2.46
    (dict(my="17", top="18", sell="67.35", depth=10), "market"),       # R8 头骨粉碎者：3.96 倍、差 ¥50
    (dict(my="10", top="10", depth=0, bid="1500"), "market"),          # EMS One：没人在卖、别处买盘 1500
])
def test_real_samples(args, want):
    assert u(**args) == want


def test_boundaries_are_inclusive_and_both_conditions_needed():
    assert u("20", sell="40", depth=1) == "market"                     # 正好 2 倍、正好差 ¥20
    assert u("20", sell="39.99", depth=1) is None                      # 不到 2 倍
    assert u("10", sell="29.99", depth=1) is None                      # 3 倍但差价不到 ¥20
    assert u("500", sell="990", depth=1) is None                       # 差 ¥490 但不到 2 倍


def test_top_wins_and_zero_disables_each_rule():
    assert u("10", top="30", sell="4871", depth=1) == "top"            # 两条都成立：报离第 1
    assert u("10", top="30", sell="4871", depth=1, max_gap_ratio=0) == "market"
    assert u("10", top="10", sell="4871", depth=1, max_market_ratio=0) is None


def test_unknown_prices_never_hide():
    assert u(None, top="30", sell="100", depth=1) is None
    assert u("0", top="30", sell="100", depth=1) is None
    assert u("10", top=None, sell=None, depth=None) is None


def test_reach_kwargs_reads_alert_settings():
    a = AlertSettings()
    assert reach_kwargs(a) == {"max_gap_ratio": 3.0, "max_market_ratio": 2.0, "min_market_gap": 20.0}
    with pytest.raises(ValueError):
        AlertSettings(max_market_ratio=-1)
