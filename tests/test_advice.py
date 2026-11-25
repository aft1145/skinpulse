"""价位二期的建议规则（价位 spec §9.5）：每条规则的边界和顺序。"""
from __future__ import annotations

from decimal import Decimal as D

from skinpulse.analysis.advice import CANCEL, HOLD, LOWER, NONE, RAISE, advise
from skinpulse.analysis.valuation import margins

BASE = dict(q10=D("-0.10"), exit=D("100"), cap=D("78.26"), conditional=False, in_trade=False,
            target_margin=D("0.15"), min_profit=D("1"))
# exit 100、坏情况 −10% → 坏情况下到手 90；cap = min(90/1.15, 90−1) = 78.26


def run(my, top=None, rank=None, **kw):
    args = {**BASE, **kw}
    m_bad = margins(args["exit"], args["q10"], my, top, rank).m_bad if my is not None and args["exit"] is not None else None
    return advise(my_price=my, top_price=top, rank=rank, m_bad=m_bad, **args)


def test_none_rules_come_first():
    assert run(D("50"), in_trade=True).kind == NONE
    assert run(D("50"), conditional=True).rule.startswith("条件单")
    assert run(D("50"), q10=None).rule.startswith("日线不足")
    assert run(D("50"), exit=None, cap=None).rule.startswith("没有在售价")
    assert run(D("50"), cap=None).kind == NONE
    assert run(None).rule == "没有求购价"
    # 不评价排在撤单前面：条件单哪怕会亏也不评价
    assert run(D("95"), conditional=True).kind == NONE


def test_cancel_when_bad_case_loses():
    a = run(D("95"))                       # 90 / 95 − 1 < 0
    assert a.kind == CANCEL and a.price is None and "会亏" in a.rule and "¥95.00" in a.rule
    assert run(D("90")).kind == LOWER      # m_bad 正好 0：不亏，不撤单


def test_lower_when_above_cap():
    a = run(D("80"))
    assert a.kind == LOWER and a.price == D("78.26") and "降到 ¥78.26 以内" in a.rule
    assert run(D("78.26"), rank=1).kind == HOLD      # 正好等于上限：不用降
    neg = run(D("0.80"), exit=D("1"), cap=D("-0.10"))  # 坏情况下到手 0.9：够不上 ¥1 最低利润
    assert neg.kind == LOWER and neg.price is None and "怎么买都不够" in neg.rule


def test_raise_when_top_plus_tick_fits_the_cap():
    a = run(D("50"), top=D("78.25"), rank=3)
    assert a.kind == RAISE and a.price == D("78.26") and "加到 ¥78.26 可排第 1" in a.rule
    over = run(D("50"), top=D("78.26"), rank=3)       # 78.27 > cap
    assert over.kind == HOLD and over.rule.startswith("抢第 1 要出 ¥78.27，超过上限")
    # 没名次（抽样之外）但知道榜首价，照样可以加价
    assert run(D("50"), top=D("60"), rank=None).kind == RAISE
    assert run(D("50"), top=None, rank=5).kind == HOLD


def test_hold_and_rank_one():
    a = run(D("50"), top=D("49"), rank=1)
    assert a.kind == HOLD and a.rule.startswith("已是第 1") and a.label == "保持"
