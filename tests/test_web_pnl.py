"""`GET /api/pnl*`（盈亏 spec §6）：只读库。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal as D

from fastapi.testclient import TestClient
from sqlalchemy import event

from skinpulse.db.models import PnlDaily, PnlPosition, TradeOrder
from skinpulse.web.app import create_app
from skinpulse.web.pnl import pnl_items, pnl_payload, pnl_sells
from tests.test_web_overview import _rt

T = lambda m, d, h=4: datetime(2026, m, d, h, tzinfo=timezone.utc)  # noqa: E731
NOW = T(11, 24, 17)
HASH = {1: "AK-47 | Redline (Field-Tested)", 2: "★ Karambit | Fade (Factory New)"}


def _order(no, side, tid, amount, at, *, acc=1, qty=1, net=None, exact=False, name=None, h=None):
    return TradeOrder(account_id=acc, platform="YOUPIN", order_no=no, side=side, template_id=tid,
                      hash_name=h or HASH.get(tid, f"Sticker | X{tid}"),
                      name=name or {1: "AK-47 | 红线 (久经沙场)", 2: "爪子刀（★） | 渐变之色 (崭新出厂)"}.get(tid, f"印花 | X{tid}"),
                      qty=qty, amount=D(str(amount)),
                      net_amount=D(str(net if net is not None else amount)), net_exact=exact or side == "buy",
                      sub_type=5, asset_id=None, created_at=at, finished_at=at, fetched_at=NOW)


def _pos(tid, qty, cost, price, *, acc=1, inv=0, lease=0, unknown=0, flag=None, h="h", name="n", day="2026-11-24"):
    return PnlPosition(date=day, account_id=acc, platform="YOUPIN", template_id=tid, hash_name=h, name=name, qty=qty,
                       cost=D(str(cost)), first_bought_at=T(10, 1), inv_qty=inv, lease_qty=lease, unknown_qty=unknown,
                       price=None if price is None else D(str(price)), price_src="steamdt" if price else None,
                       price_flag=flag)


def _daily(acc=1, day="2026-11-24", **kw):
    base = dict(date=day, account_id=acc, realized_total=D("0"), realized_day=D("0"), qty=0, cost=D("0"),
                value_mkt=D("0"), value_net=D("0"), cost_priced=D("0"), unpriced_qty=0, inv_qty=0, lease_qty=0,
                unknown_qty=0, unknown_cost=D("0"), youpin_pnl=None, youpin_pnl_n=None, lease_count=0,
                lease_rent_day=None, unbooked_inv_qty=0, unbooked_inv_value=None, computed_at=NOW)
    return PnlDaily(**{**base, **kw})


def _rows():
    return [
        _order("B1", "buy", 1, 100, T(5, 2)), _order("B2", "buy", 1, 120, T(6, 1)),
        _order("S1", "sell", 1, 150, T(7, 3), net=148.5),
        _order("B3", "buy", 2, 2000, T(8, 1)),
        _order("S9", "sell", 9, 10, T(9, 1), net=9.9),                     # 成本未知
        _order("BX", "buy", 3, 5, T(10, 1), acc=2),
        _pos(1, 1, 120, 100, inv=1, h="AK-47 | Redline (Field-Tested)", name="AK-47 | 红线 (久经沙场)"),
        _pos(2, 1, 2000, 2500, lease=1, h="★ Karambit | Fade (Factory New)", name="爪子刀（★） | 渐变之色 (崭新出厂)"),
        _pos(3, 1, 5, None, acc=2, unknown=1, h="Sticker | X3", name="印花 | X3"),
        _pos(3, 1, 5, 4, acc=1, day="2026-11-20", h="Sticker | X3", name="印花 | X3"),   # 旧快照不读
        _daily(1, qty=2, cost=D("2120"), value_mkt=D("2600"), value_net=D("2548.26"), cost_priced=D("2120"),
               inv_qty=1, lease_qty=1, youpin_pnl=D("-30"), youpin_pnl_n=3, lease_count=1, lease_rent_day=D("1.04"),
               unbooked_inv_qty=7, unbooked_inv_value=D("12.5")),
        _daily(2, qty=1, cost=D("5"), cost_priced=D("0"), unpriced_qty=1, unknown_qty=1, unknown_cost=D("5")),
    ]


def test_payload_sums_accounts_and_reads_latest_snapshot(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings, _rows())
    p = pnl_payload(rt)
    assert p["as_of"] == "2026-11-24" and p["basis_default"] == "market"
    assert p["realized"] == {"total": 48.5, "matched": 1, "unknown": 1, "unknown_net": 9.9, "sells": 2}
    assert p["holding"]["qty"] == 3 and p["holding"]["value_mkt"] == 2600.0 and p["holding"]["unpriced_qty"] == 1
    assert p["where"] == {"inv": 1, "lease": 1, "unknown": 1, "unknown_cost": 5.0}
    assert p["youpin"] == {"pnl": -30.0, "n": 3}
    assert p["months"] == [["2026-07", 48.5], ["2026-08", 0.0], ["2026-09", 0.0], ["2026-10", 0.0], ["2026-11", 0.0]]
    cats = {c["key"]: c for c in p["categories"]}
    assert cats["knife"]["label"] == "刀" and cats["knife"]["value_mkt"] == 2500.0
    assert cats["sticker"]["value_mkt"] is None and cats["sticker"]["cost"] == 5.0   # 一件都没价：null，不是 0
    assert p["notes"] == {"lease_qty": 1, "lease_rent_day": 1.04, "unbooked_inv_qty": 7, "unbooked_inv_value": 12.5}
    one = pnl_payload(rt, 2)
    assert one["holding"]["qty"] == 1 and one["realized"]["sells"] == 0


def test_payload_empty_db(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings, [])
    p = pnl_payload(rt)
    assert p["as_of"] is None and p["holding"] is None and p["months"] == []
    assert pnl_items(rt) == {"as_of": None, "total": 0, "items": []}
    assert pnl_sells(rt) == {"total": 0, "sells": []}


def test_items_sort_basis_search_and_unpriced_last(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings, _rows())
    worst = pnl_items(rt)
    assert [i["template_id"] for i in worst["items"]] == [1, 2, 3] and worst["total"] == 3
    ak = worst["items"][0]
    assert (ak["name"], ak["sub"], ak["float"], ak["realized"], ak["where"]) == \
        ("AK-47 | 红线", "久经沙场", -20.0, 48.5, {"inv": 1, "lease": 0, "unknown": 0})
    assert ak["category"] == "rifle" and ak["category_label"] == "步枪"
    assert worst["items"][2]["float"] is None and worst["items"][2]["value_mkt"] is None
    best = pnl_items(rt, sort="best")
    assert [i["template_id"] for i in best["items"]] == [2, 1, 3]
    net = pnl_items(rt, basis="net")
    assert net["items"][0]["float"] == -21.99 and net["items"][0]["value_net"] == 98.01
    assert [i["template_id"] for i in pnl_items(rt, q="红线")["items"]] == [1]
    assert [i["template_id"] for i in pnl_items(rt, category="knife")["items"]] == [2]
    assert len(pnl_items(rt, offset=1, limit=1)["items"]) == 1


def test_sells_with_lots_and_unknown_cost(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings, _rows())
    out = pnl_sells(rt)
    assert out["total"] == 2
    s9, s1 = out["sells"]
    assert s9["cost"] is None and s9["pnl"] is None and s9["lots"] == []
    assert (s1["net"], s1["cost"], s1["pnl"], s1["name"], s1["sub"]) == (148.5, 100.0, 48.5, "AK-47 | 红线", "久经沙场")
    assert s1["lots"] == [{"bought_at": "2026-05-02T04:00:00Z", "unit": 100.0, "qty": 1, "platform": "YOUPIN",
                           "account_id": 1}]
    assert s1["platform"] == "YOUPIN"
    assert len(pnl_sells(rt, limit=1)["sells"]) == 1


def test_query_count_is_flat(tmp_path, settings):
    rt, engine = _rt(tmp_path, settings, _rows())
    n = {"q": 0}
    event.listen(engine, "before_cursor_execute", lambda *a: n.__setitem__("q", n["q"] + 1))
    pnl_payload(rt)
    pnl_items(rt)
    assert n["q"] <= 10


def test_routes_and_param_validation(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings, _rows())
    c = TestClient(create_app(rt), base_url=f"http://127.0.0.1:{settings.web.port}")
    assert c.get("/api/pnl").json()["as_of"] == "2026-11-24"
    assert c.get("/api/pnl?account=2").json()["holding"]["qty"] == 1
    assert c.get("/api/pnl/items?sort=best&basis=net").status_code == 200
    assert c.get("/api/pnl/items?sort=up").status_code == 422
    assert c.get("/api/pnl/items?limit=500").status_code == 422
    assert c.get("/api/pnl/sells?limit=0").json()["total"] == 2


def test_all_accounts_use_each_accounts_latest_snapshot(tmp_path, settings):
    """评审 2026-11-24：号 2 今天的任务失败时，全部号相加要用它昨天的快照，不能把它整个漏掉；
    太久没更新的号（停用了）不再相加。"""
    rows = [_pos(1, 1, 100, 90, h="AK-47 | Redline (Field-Tested)"),
            _pos(2, 1, 50, 60, acc=2, day="2026-11-23", h="Sticker | X2"),
            _pos(3, 1, 9, 9, acc=3, day="2026-11-01", h="Sticker | X3"),
            _daily(1, qty=1, cost=D("100"), value_mkt=D("90"), cost_priced=D("100")),
            _daily(2, day="2026-11-23", qty=1, cost=D("50"), value_mkt=D("60"), cost_priced=D("50")),
            _daily(3, day="2026-11-01", qty=1, cost=D("9"), value_mkt=D("9"), cost_priced=D("9"))]
    rt, _ = _rt(tmp_path, settings, rows)
    p = pnl_payload(rt)
    assert p["as_of"] == "2026-11-24" and p["holding"]["qty"] == 2 and p["holding"]["value_mkt"] == 150.0
    assert {i["template_id"] for i in pnl_items(rt)["items"]} == {1, 2}


def test_sells_are_cut_at_the_snapshot_day(tmp_path, settings):
    rows = _rows() + [_order("S10", "sell", 1, 130, T(11, 24, 17), net=128.7)]   # 本地 11-25 01:00，快照之后
    rt, _ = _rt(tmp_path, settings, rows)
    assert pnl_sells(rt)["total"] == 2 and pnl_payload(rt)["realized"]["sells"] == 2



def test_buff_sell_matches_youpin_buy_and_negative_template_merges(tmp_path, settings):
    """盈亏接 BUFF（ADR-0025）：BUFF 卖掉悠悠买的那件；BUFF 独有模板（负 ID）按英文名和悠悠的同名饰品合成一行。"""
    rows = [_order("B1", "buy", 1, 100, T(5, 2)),
            TradeOrder(account_id=2, platform="BUFF", order_no="BS1", side="sell", template_id=-77,
                       hash_name=HASH[1], name="AK-47 | 红线 (久经沙场)", qty=1, amount=D("150"), net_amount=D("147"),
                       net_exact=True, sub_type=1, asset_id=None, created_at=T(7, 3), finished_at=T(7, 3), fetched_at=NOW),
            _pos(1, 1, 120, 100, h=HASH[1], name="AK-47 | 红线 (久经沙场)"),
            _pos(-77, 1, 90, 100, acc=2, h=HASH[1], name="AK-47 | 红线 (久经沙场)"),
            _daily(1, qty=1, cost=D("120")), _daily(2, qty=1, cost=D("90"))]
    rows[3].platform = "BUFF"                    # 那件 -77 的持仓是在 BUFF 买的
    rt, _ = _rt(tmp_path, settings, rows)
    sells = pnl_sells(rt)["sells"]
    assert len(sells) == 1 and sells[0]["platform"] == "BUFF" and sells[0]["template_id"] is None
    assert sells[0]["pnl"] == 47.0 and sells[0]["lots"][0]["platform"] == "YOUPIN"
    [item] = pnl_items(rt)["items"]
    assert (item["template_id"], item["qty"], sorted(item["platforms"]), item["realized"]) == (1, 2, ["BUFF", "YOUPIN"], 47.0)
    # 只看 BUFF 号：卖出算它的，悠悠买的那件持仓不算它的
    assert pnl_sells(rt, 2)["total"] == 1 and pnl_sells(rt, 1)["total"] == 0
    assert pnl_items(rt, 2)["items"][0]["qty"] == 1
