from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from skinpulse.core.paused import (
    AHEAD, ALONE, INVERTED, NEAR, NEAR_SELL, OTHER, TIE, TOP1, classify, parse_top_bid, paused_days, sort_key,
)

KW = dict(near_gap=D("0.05"), sell_ratio=D("0.9"))


@pytest.mark.parametrize("raw,expected", [
    ("¥49", (D("49"), False)),
    ("¥1,234.5", (D("1234.5"), False)),
    ("￥3.3", (D("3.3"), False)),
    ("暂无", (None, True)),
    ("", (None, False)),
    (None, (None, False)),
    ("¥0", (None, False)),
    ("¥abc", (None, False)),
    ("¥NaN", (None, False)),
])
def test_parse_top_bid(raw, expected):
    assert parse_top_bid(raw) == expected


def test_inverted_when_price_equals_sell_min():
    v = classify(D("205"), D("190"), False, D("205"), **KW)
    assert v.tier == INVERTED and v.top1_kind == AHEAD and v.sell_ratio == 1


def test_inverted_wins_over_top1_and_keeps_kind():
    v = classify(D("650"), D("190"), False, D("205"), **KW)
    assert (v.tier, v.top1_kind) == (INVERTED, AHEAD)


def test_inverted_even_when_behind_top():
    v = classify(D("100"), D("120"), False, D("99"), **KW)
    assert v.tier == INVERTED and v.top1_kind is None and v.gap_ratio == D("20") / D("120")


@pytest.mark.parametrize("top,empty,kind", [(D("15.5"), False, AHEAD), (D("16"), False, TIE), (None, True, ALONE)])
def test_top1_kinds(top, empty, kind):
    v = classify(D("16"), top, empty, D("20"), **KW)
    assert (v.tier, v.top1_kind) == (TOP1, kind)


def test_unknown_top_is_not_top1():
    v = classify(D("16"), None, False, D("100"), **KW)
    assert v.tier == OTHER and v.top1_kind is None and v.gap_ratio is None


def test_near_boundary_exactly_five_percent():
    v = classify(D("95"), D("100"), False, None, **KW)
    assert v.tier == NEAR and v.gap_ratio == D("0.05")
    assert classify(D("94.99"), D("100"), False, None, **KW).tier == OTHER


def test_near_sell_boundary():
    assert classify(D("90"), D("120"), False, D("100"), **KW).tier == NEAR_SELL
    assert classify(D("89.99"), D("120"), False, D("100"), **KW).tier == OTHER


@pytest.mark.parametrize("sell", [None, D("0")])
def test_missing_sell_is_not_zero(sell):
    v = classify(D("10"), D("20"), False, sell, **KW)
    assert v.sell_ratio is None and v.tier == OTHER


def test_paused_days():
    now = datetime(2026, 11, 24, 6, 0, tzinfo=timezone.utc)
    assert paused_days(now - timedelta(days=121, hours=1), now) == 121
    assert paused_days(now - timedelta(hours=23), now) == 0
    assert paused_days(now + timedelta(hours=1), now) == 0
    assert paused_days(None, now) is None


def test_sort_key_orders_tiers_then_inside():
    rows = [
        (NEAR, None, D("0.04")), (NEAR, None, D("0.01")),
        (INVERTED, D("1.2"), None), (INVERTED, D("3.1"), None),
        (TOP1, None, None), (TOP1, D("0.98"), None),
        (OTHER, D("0.5"), None),
    ]
    got = sorted(rows, key=lambda r: sort_key(*r))
    assert got == [
        (INVERTED, D("3.1"), None), (INVERTED, D("1.2"), None),
        (TOP1, D("0.98"), None), (TOP1, None, None),
        (NEAR, None, D("0.01")), (NEAR, None, D("0.04")),
        (OTHER, D("0.5"), None),
    ]
