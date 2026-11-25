from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

from skinpulse.analysis.brief import (
    NO_DATA, QUIET_SENT, SENT, SKIPPED, OrderInfo, Snap, TopRow, TopTable, decide, top_table,
)

# 2026-09-22 00:00 Asia/Shanghai == 2026-09-21 16:00 UTC
START = datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)
END = START + timedelta(days=1)


def snap(hours: float, rank, top="110"):
    return Snap(START + timedelta(hours=hours), rank, D(top) if top is not None else None)


def order(no, price="100", tid=1):
    return OrderInfo(no, f"item {no}", D(price), tid)


def table(orders, snaps, **kw):
    args = dict(top_rank=2, max_gap_ratio=3.0)
    args.update(kw)
    return top_table(orders, snaps, START, END, **args)


def only(t: TopTable) -> TopRow:
    assert len(t.rows) == 1
    return t.rows[0]


def test_table_lists_orders_in_top_at_close_only():
    t = table([order("A"), order("B"), order("C")], {
        "A": [snap(1, 1), snap(5, 2)],          # 收盘第 2：进表
        "B": [snap(1, 1), snap(5, 3)],          # 白天第 1、收盘第 3：不进表（看收盘，不看当天最好）
        "C": [snap(1, 5), snap(5, 5)],
    })
    assert [r.purchase_no for r in t.rows] == ["A"]
    assert t.orders_with_data == 3


def test_rows_sort_by_rank_then_my_price_descending_then_name():
    orders = [order("A", "50"), order("B", "80"), order("C", "300"), order("D", "80")]
    snaps = {no: [snap(-1, r), snap(1, r)] for no, r in (("A", 2), ("B", 2), ("C", 1), ("D", 2))}
    snaps["C"] = [snap(-1, 1, top="300"), snap(1, 1, top="300")]
    t = table(orders, snaps)
    assert [r.purchase_no for r in t.rows] == ["C", "B", "D", "A"]


def test_new_order_without_opening_snapshot_is_new():
    r = only(table([order("A")], {"A": [snap(3, 2), snap(4, 2)]}))
    assert r.is_new and r.open_rank is None and r.open_top1 is None
    assert not r.rank_changed and not r.top1_changed and r.changed


def test_order_entering_from_outside_is_new_not_rank_changed():
    r = only(table([order("A")], {"A": [snap(-30, 5), snap(2, 2)]}))   # 开盘 = 前一天（甚至更早）最后一条
    assert r.is_new and r.open_rank == 5 and not r.rank_changed


def test_rank_change_inside_the_top():
    r = only(table([order("A")], {"A": [snap(-1, 1, top="100"), snap(3, 2, top="110")]}))
    assert (r.open_rank, r.rank) == (1, 2)
    assert r.rank_changed and not r.is_new and r.changed


def test_top1_price_change_is_marked_when_i_am_not_first():
    r = only(table([order("A")], {"A": [snap(-1, 2, top="250"), snap(3, 2, top="248")]}))
    assert (r.open_top1, r.top1_price) == (D("250"), D("248"))
    assert r.top1_changed and not r.rank_changed and not r.is_new and r.changed


def test_top1_price_change_is_not_marked_when_i_am_first():
    """收盘第 1：榜首就是我，榜首价变了就是我的价变了——用户没选这一项。"""
    r = only(table([order("A")], {"A": [snap(-1, 1, top="100"), snap(3, 1, top="105")]}))
    assert not r.top1_changed and not r.changed


def test_top1_change_needs_both_prices_known():
    t = table([order("A"), order("B")], {"A": [snap(-1, 2, top=None), snap(3, 2, top="248")],
                                         "B": [snap(-1, 2, top="250"), snap(3, 2, top=None)]})
    assert [r.top1_changed for r in t.rows] == [False, False]


def test_zero_top1_price_is_unknown_not_a_change():
    """价格 0 = 没有数据（CLAUDE.md 坑 3）：不能标出「榜首 0→248」。"""
    t = table([order("A"), order("B")], {"A": [snap(-1, 2, top="0"), snap(3, 2, top="248")],
                                         "B": [snap(-1, 2, top="250"), snap(3, 2, top="0")]})
    assert [r.top1_changed for r in t.rows] == [False, False]


def test_opening_is_the_last_ranked_snapshot_before_the_day():
    """D 之前最后一条 rank 为 None：开盘往前找到最后一条有排名的（第 5），所以算新进。"""
    r = only(table([order("A")], {"A": [snap(-5, 5), snap(-1, None), snap(2, 2)]}))
    assert r.open_rank == 5 and r.is_new


def test_gap_filter_uses_the_close_snapshot():
    """差价过滤看收盘那条：白天被挡、收盘不挡的列；白天不挡、收盘被挡的不列。"""
    t = table([order("A"), order("B")], {"A": [snap(1, 2, top="400"), snap(5, 2, top="150")],
                                         "B": [snap(1, 2, top="150"), snap(5, 2, top="400")]})
    assert [r.purchase_no for r in t.rows] == ["A"]


def test_unchanged_row_is_listed_but_not_changed():
    t = table([order("A")], {"A": [snap(-1, 2), snap(3, 2)]})
    assert len(t.rows) == 1 and t.changed_rows == []


def test_muted_order_is_not_in_the_table():
    """被差价过滤挡住（收盘榜首价 / 我的价 ≥ max_gap_ratio）：不进表，和实时告警一致。"""
    t = table([order("J", price="10"), order("K", price="50")],
              {"J": [snap(1, 2, top="30")], "K": [snap(1, 2, top="149")]})
    assert [r.purchase_no for r in t.rows] == ["K"]
    assert t.orders_with_data == 2


def test_unknown_ranks_are_ignored():
    t = table([order("D")], {"D": [snap(-1, None), snap(1, 2), snap(2, None)]})
    r = only(t)
    assert r.rank == 2 and r.open_rank is None and r.is_new


def test_order_without_snapshots_that_day_is_not_counted():
    t = table([order("E")], {"E": [snap(-5, 1)]})
    assert t.orders_with_data == 0 and t.rows == []


def test_top_rank_is_a_parameter():
    t = table([order("A")], {"A": [snap(-1, 3), snap(1, 3)]}, top_rank=3)
    r = only(t)
    assert not r.is_new and not r.changed


def test_sell_price_is_filled_by_template_and_missing_is_none():
    t = table([order("A", tid=1), order("B", tid=2), order("C", tid=3)],
              {no: [snap(1, 2)] for no in "ABC"}, sell_prices={1: D("52"), 2: None})
    assert {r.purchase_no: r.sell_price for r in t.rows} == {"A": D("52"), "B": None, "C": None}


def test_order_far_from_market_is_left_out_like_the_alert():
    """难以成交 spec §3：离市价太远（市价 ÷ 我的价 ≥ 2 且差 ≥ ¥20）的单不进表，和推送一致；
    差价不到下限的、没查过在售价的照常进表。"""
    far = OrderInfo("A", "item A", D("10"), 1, sell_min=D("4871"), sell_depth=1)
    cheap = OrderInfo("B", "item B", D("7"), 1, sell_min=D("9.46"), sell_depth=3)   # 1.35 倍、差 2.46
    unknown = OrderInfo("C", "item C", D("10"), 1)                                   # 没查过在售价
    snaps = {no: [snap(1, 1, top="10" if no != "B" else "7")] for no in "ABC"}
    t = table([far, cheap, unknown], snaps, max_market_ratio=2.0, min_market_gap=20)
    assert {r.purchase_no for r in t.rows} == {"B", "C"}


DAY = date(2026, 9, 22)


def row(no, rank=2, *, new=False, rank_changed=False, top1_changed=False):
    return TopRow(no, f"item {no}", D("100"), rank, None if new else rank, D("110"), D("110"), None,
                  new, rank_changed, top1_changed)


def dec(t, last=None, first=None, quiet=7):
    return decide(t, day=DAY, last_sent_day=last, first_log_day=first, quiet_days=quiet, top_rank=2)


def test_no_snapshots_means_no_data():
    d = dec(TopTable([row("A", new=True)], orders_with_data=0))
    assert d.decision == NO_DATA and not d.send


def test_any_changed_row_sends_with_counts():
    t = TopTable([row("A", new=True), row("B", rank_changed=True, top1_changed=True), row("C")], orders_with_data=9)
    d = dec(t)
    assert d.decision == SENT and d.send
    assert (d.reasons["new"], d.reasons["rank_changed"], d.reasons["top1_changed"]) == (1, 1, 1)
    assert "前 2 共 3 张" in d.reasons["summary"] and "2 张有变动" in d.reasons["summary"]


def test_no_change_is_skipped():
    d = dec(TopTable([row("A"), row("B")], orders_with_data=9), last=date(2026, 9, 21))
    assert d.decision == SKIPPED and not d.send
    assert "没有变动" in d.reasons["summary"]


def test_quiet_fallback_after_seven_days_since_last_send():
    t = TopTable([row("A")], orders_with_data=9)
    assert dec(t, last=date(2026, 9, 15)).decision == QUIET_SENT
    assert dec(t, last=date(2026, 9, 16)).decision == SKIPPED


def test_quiet_counts_from_first_log_when_never_sent():
    t = TopTable([], orders_with_data=9)
    assert dec(t, first=date(2026, 9, 15)).decision == QUIET_SENT
    assert dec(t).decision == SKIPPED      # 全新安装：第一天不发兜底
