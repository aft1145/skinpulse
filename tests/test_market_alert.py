"""市价提醒的判断（市价提醒 spec §1）。"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from skinpulse.core.market_alert import INVERTED, NEAR, flag_label, flags_of, market_flags

BASE = dict(conditional=False, in_trade=False, my_price=D("1.10"), rank=4, top=D("1.30"), sell_min=D("1.17"),
            sell_depth=10, rank_max=10, near_ratio=D("0.9"), rank_always=3, inverted_ratio=D("0.85"))


def f(**kw):
    return market_flags(**{**BASE, **kw})


def test_hideout_case_has_both():
    # 2026-10-15 用户发现的那张：榜首 1.30 > 在售 1.17，我 1.10 第 4（1.10 ≥ 1.17 × 0.9 = 1.053）
    assert f() == {INVERTED, NEAR}
    # 改到 0.90：第 4 名、只有在售的 77%（< 85%），倒挂也不提醒了（用户 2026-10-15：前三名或达到比例才提醒）
    assert f(my_price=D("0.90")) == frozenset()
    assert f(my_price=D("0.90"), rank=3) == {INVERTED}   # 前三名一律提醒


@pytest.mark.parametrize("rank, my, inverted", [
    (1, "0.5", False), (2, "0.5", True), (3, "0.5", True),          # 前三名：不管出价多少
    (4, "0.5", False), (4, "0.9945", True), (4, "0.99", False),     # 第 4～10 名：我的价 ≥ 在售 1.17 × 0.85 = 0.9945
    (10, "1.0", True), (11, "1.0", False), (None, "1.0", False),
])
def test_inverted_rank_or_ratio(rank, my, inverted):
    assert (INVERTED in f(rank=rank, my_price=D(my))) is inverted


def test_boundaries_are_inclusive():
    assert INVERTED in f(top=D("1.17"))                   # 榜首正好等于在售
    assert INVERTED not in f(top=D("1.16"))
    assert NEAR in f(my_price=D("1.053"))                 # 正好 90%
    assert NEAR not in f(my_price=D("1.05"))


@pytest.mark.parametrize("kw", [dict(conditional=True), dict(in_trade=True), dict(sell_depth=0), dict(sell_depth=None),
                                dict(sell_min=None), dict(sell_min=D("0"))])
def test_not_judged(kw):
    assert f(**kw) == frozenset()


def test_missing_prices_skip_only_their_check():
    assert f(top=None) == {NEAR}
    assert f(my_price=None, top=D("1.30"), rank=2) == {INVERTED}
    assert f(my_price=None, top=D("1.30")) == frozenset()          # 第 4 名要靠比例，没我的价判不了


def test_label_round_trip():
    for flags, label in ((frozenset(), None), ({INVERTED}, "inverted"), ({NEAR}, "near"), ({INVERTED, NEAR}, "both")):
        assert flag_label(flags) == label and flags_of(label) == frozenset(flags)
