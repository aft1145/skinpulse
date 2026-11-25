"""盈亏纯逻辑（盈亏 spec §4）。"""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

from skinpulse.analysis.pnl import (
    FLAG_SPREAD, FLAG_THIN, SRC_INVENTORY, SRC_STEAMDT, Order, allocate_locations, build_ledger, estimated_net,
    holding_value, monthly_realized, net_factor, pick_price,
)

T0 = datetime(2026, 5, 1, 4, 0, tzinfo=timezone.utc)
FEES = {"YOUPIN": 0.01, "BUFF": 0.015}


def o(side, day, amount, qty=1, tid=1, acc=1, plat="YOUPIN", net=None, no=None, h="H1"):
    return Order(acc, plat, no or f"{side}{day}{tid}{acc}{plat}", side, tid, h, f"N{tid}", qty, D(str(amount)),
                 None if net is None else D(str(net)), T0 + timedelta(days=day))


def test_fifo_single_and_multiple_lots():
    led = build_ledger([o("buy", 0, 10), o("buy", 1, 20), o("sell", 2, 30, net=29.7)], FEES)
    [s] = led.sells
    assert s.cost == D("10.00") and s.pnl == D("19.70") and s.net_exact
    assert [(u.unit, u.qty) for u in s.lots] == [(D("10.00"), 1)]
    [p] = led.positions
    assert (p.qty, p.cost, p.first_bought_at) == (1, D("20.00"), T0 + timedelta(days=1))
    assert led.realized_total == D("19.70")


def test_multi_qty_sell_spans_lots():
    led = build_ledger([o("buy", 0, 10), o("buy", 1, 40, qty=2), o("sell", 3, 90, qty=3, net=89.1)], FEES)
    [s] = led.sells
    assert s.cost == D("50.00") and [(u.unit, u.qty) for u in s.lots] == [(D("10.00"), 1), (D("20.00"), 2)]
    assert led.positions == []


def test_sell_before_buy_is_not_matched_and_does_not_consume():
    led = build_ledger([o("sell", 0, 30), o("buy", 1, 10)], FEES)
    [s] = led.sells
    assert s.cost is None and s.pnl is None and not s.net_exact
    assert s.net == D("29.70")                      # 估算的到手价
    assert led.positions[0].qty == 1 and led.realized_total == 0


def test_short_lots_make_whole_sell_unknown_and_lots_stay():
    led = build_ledger([o("buy", 0, 10), o("sell", 1, 60, qty=2)], FEES)
    assert led.sells[0].cost is None
    assert led.positions[0].qty == 1 and led.positions[0].cost == D("10.00")


def test_youpin_buy_matches_buff_sell():
    """ADR-0025：悠悠买、BUFF 卖配成一对；已实现记在卖出的号上，配到的批次带买入的号和平台。"""
    led = build_ledger([o("buy", 0, 301, acc=1), o("sell", 5, 360, acc=2, plat="BUFF", net=355.87)], FEES)
    [s] = led.sells
    assert s.cost == D("301.00") and s.pnl == D("54.87") and s.order.account_id == 2
    assert [(u.account_id, u.platform) for u in s.lots] == [(1, "YOUPIN")]
    assert led.positions == []


def test_same_account_and_platform_lots_go_first():
    rows = [o("buy", 0, 10, acc=1, no="old-yp"), o("buy", 1, 20, acc=2, plat="BUFF", no="buff"),
            o("sell", 3, 30, acc=2, plat="BUFF", net=29)]
    led = build_ledger(rows, FEES)
    assert led.sells[0].cost == D("20.00")                     # 同号同平台那批，虽然更晚
    [p] = led.positions
    assert (p.account_id, p.platform, p.cost) == (1, "YOUPIN", D("10.00"))


def test_other_platform_lots_only_if_bought_before_the_sell():
    led = build_ledger([o("sell", 1, 30, acc=2, plat="BUFF", net=29), o("buy", 2, 10, acc=1)], FEES)
    assert led.sells[0].cost is None and led.positions[0].qty == 1


def test_matches_by_hash_name_across_template_ids():
    """BUFF 对不上悠悠模板的订单 template_id 是负的 goods_id，按英文名照样配上；英文名不同的不配。"""
    rows = [o("buy", 0, 10, acc=1, tid=7, h="AK-47 | Redline (Field-Tested)"),
            o("sell", 1, 30, acc=2, plat="BUFF", tid=-123, h="AK-47 | Redline (Field-Tested)", net=29),
            o("sell", 1, 30, acc=2, plat="BUFF", tid=7, h="Other", net=29)]
    led = build_ledger(rows, FEES)
    costs = {s.order.hash_name: s.cost for s in led.sells}
    assert costs == {"AK-47 | Redline (Field-Tested)": D("10.00"), "Other": None}


def test_positions_split_by_buying_account_and_platform():
    rows = [o("buy", 0, 10, acc=1), o("buy", 1, 20, acc=2, plat="BUFF", tid=-5)]
    led = build_ledger(rows, FEES)
    assert [(p.key, p.cost) for p in led.positions] == [((1, "YOUPIN", 1), D("10.00")), ((2, "BUFF", -5), D("20.00"))]


def test_same_second_buy_counts_before_sell():
    t = 0
    led = build_ledger([o("sell", t, 30, net=29.7), o("buy", t, 10)], FEES)
    assert led.sells[0].cost == D("10.00")


def test_estimated_net_floors_to_cent():
    assert estimated_net(D("17.50"), 0.01) == D("17.32")
    assert estimated_net(D("142"), 0.01) == D("140.58")
    assert estimated_net(D("10"), 0.015) == D("9.85")


def test_monthly_realized_fills_gaps_in_local_months():
    # 05-31 17:00 UTC 是上海 06-01 01:00：算在 6 月
    rows = [o("buy", 0, 10), o("buy", 0, 10, no="b2"),
            Order(1, "YOUPIN", "s1", "sell", 1, "H1", "N1", 1, D("15"), D("15"), datetime(2026, 5, 31, 17, tzinfo=timezone.utc)),
            o("sell", 70, 13, net=13)]
    led = build_ledger(rows, FEES)
    assert monthly_realized(led.sells, "Asia/Shanghai", date(2026, 8, 3)) == [
        ("2026-06", D("5.00")), ("2026-07", D("3.00")), ("2026-08", D("0.00"))]
    assert monthly_realized([], "Asia/Shanghai", date(2026, 8, 3)) == []


def test_allocate_locations_caps_and_spills():
    led = build_ledger([o("buy", 0, 10, qty=3, tid=1, h="A"), o("buy", 0, 5, tid=2, h="B"), o("buy", 0, 5, tid=3, h="C")],
                       FEES)
    inv = Counter({"A": 1, "B": 5})
    loc = allocate_locations(led.positions, inv, Counter({"A": 1, "C": 0, "Z": 4}))
    assert loc == {(1, "YOUPIN", 1): (1, 1, 1), (1, "YOUPIN", 2): (1, 0, 0), (1, "YOUPIN", 3): (0, 0, 1)}
    assert inv == Counter({"A": 1, "B": 5})     # 不改调用方的计数


def test_pick_price_order_and_flags():
    assert pick_price(D("10"), 50, D("10.5"), D("9")) == (D("10"), SRC_STEAMDT, None)
    assert pick_price(D("520"), 2, D("520"), None) == (D("520"), SRC_STEAMDT, FLAG_THIN)
    assert pick_price(D("0.48"), 100, D("0.23"), None)[2] == FLAG_SPREAD
    assert pick_price(D("0.8"), 2000, D("1.28"), None)[2] == FLAG_SPREAD   # 0.625 < 1/1.5
    assert pick_price(D("1.2"), 2000, D("1.0"), None)[2] is None
    assert pick_price(None, None, D("3"), D("9")) == (D("9"), SRC_INVENTORY, None)
    assert pick_price(None, None, None, None) == (None, None, None)
    assert pick_price(D("0"), 5, None, D("2"))[1] == SRC_INVENTORY        # 价格 0 = 没有数据


def test_holding_value_two_bases_and_unpriced():
    f = net_factor(0.01, 0.01)
    assert f == D("0.9801")
    hv = holding_value([(2, D("100"), D("40"), f), (1, D("30"), None, f)])
    assert (hv.qty, hv.cost, hv.cost_priced, hv.unpriced_qty) == (3, D("130.00"), D("100.00"), 1)
    assert hv.value_mkt == D("80.00") and hv.float_mkt == D("-20.00")
    assert hv.value_net == D("78.41") and hv.float_net == D("-21.59")
