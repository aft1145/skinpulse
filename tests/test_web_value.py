"""价位一期接口：/api/overview 的 value、/api/item 的 valuation、/api/status 的 valuation（价位 spec §6）。"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal as D

from sqlalchemy import event

from skinpulse.db.models import DailyBar, IndexDaily, Valuation
from skinpulse.db.session import utcnow
from skinpulse.web.overview import overview_payload
from tests.test_web_overview import NOW, TODAY, _order, _rt, _snap


def _val(tid, day="2026-09-26", **kw):
    base = dict(date=day, template_id=tid, computed_at=NOW, bars=365, pct=D("0.1"), lo=D("90"), hi=D("200"),
                cur=D("100"), trend="下降", c30=D("-0.08"), c90=D("-0.2"), b30=D("-0.05"), b90=D("-0.17"),
                ex30=D("-0.03"), rel="持平", vol7=D("0.06"), q10=D("-0.1"), liq="一般", listed=40,
                spread=D("0.04"), exit=D("97.03"), exit_where="YOUPIN", exit_sell=D("100"), cap=D("70"),
                snapshot_date=day)
    base.update(kw)
    return Valuation(**base)


def _rows():
    return [
        _order("P1", 50, tid=1), _snap("P1", TODAY, 3, 60), _val(1),                 # 抢到第 1：97.03/60.01−1 ≈ 62%
        _order("P2", 80, tid=2), _snap("P2", TODAY, 2, 99), _val(2),                 # 抢到第 1 会亏
        _order("P3", 90, tid=3), _snap("P3", TODAY, 1, 90), _val(3, rel="跑输", liq="难出手"),   # 已是第 1
        _order("P4", 5, tid=4), _snap("P4", TODAY, 5, 6),                             # 没估值
        _val(1, day="2026-09-25", pct=D("0.9")),                                      # 旧的一天：不用
    ]


def test_overview_rows_carry_value_and_margins(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings, _rows())
    d = overview_payload(rt, now=NOW)
    by = {o["purchase_no"]: o for o in d["orders"]}
    v1 = by["P1"]["value"]
    assert v1["pct"] == 0.1 and v1["pct_label"] == "一年低位" and v1["trend"] == "下降"
    assert (v1["exit"], v1["exit_where"], v1["exit_sell"]) == (97.03, "YOUPIN", 100.0)
    assert v1["m_my"] == round(97.03 / 50 - 1, 6)
    assert v1["m_top"] == round(97.03 / 60.01 - 1, 6)
    assert v1["m_bad"] == round(97.03 * 0.9 / 50 - 1, 6)
    assert v1["conditional"] is False and v1["vol_label"] == "波动中"
    assert by["P2"]["value"]["m_top"] < 0
    assert by["P3"]["value"]["m_top"] is None                 # 已是第 1
    assert by["P4"]["value"] is None                          # 没估值：null，不是 0


def test_overview_valuation_summary(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings, _rows())
    s = overview_payload(rt, now=NOW)["valuation"]
    assert s["as_of"] == "2026-09-26" and s["snapshot_date"] == "2026-09-26" and s["computed_at"].endswith("Z")
    assert s["counts"] == {"take": 1, "take_loss": 1, "low": 3, "fall": 1, "hard": 1, "evaluated": 3,
                           "insufficient": 0,
                           "advice": {"none": 0, "cancel": 1, "lower": 1, "raise": 1, "hold": 0}}
    assert s["target_margin"] == 0.15
    assert s["fees"] == {"YOUPIN": 0.01, "BUFF": 0.015, "withdraw": 0.01, "undercut": 0.01}


def test_overview_rows_carry_advice(tmp_path, settings):
    """价位 spec §9.5：坏情况下到手 97.03 × 0.9 = 87.33，上限 cap = 70。"""
    rt, _ = _rt(tmp_path, settings, _rows())
    by = {o["purchase_no"]: o for o in overview_payload(rt, now=NOW)["orders"]}
    a1, a2, a3 = (by[k]["value"]["advice"] for k in ("P1", "P2", "P3"))
    assert (a1["kind"], a1["label"], a1["price"]) == ("raise", "可以加价", 60.01)     # 第 3、榜首 60 → 60.01 ≤ 70
    assert (a2["kind"], a2["price"]) == ("lower", 70.0)                                # 80 > 70，坏情况下还赚
    assert (a3["kind"], a3["price"]) == ("cancel", None)                               # 90：坏情况下会亏
    assert "会亏" in a3["rule"] and by["P1"]["value"]["cap"] == 70.0


def test_advice_is_null_when_turned_off(tmp_path, settings):
    settings.valuation.advice = False
    rt, _ = _rt(tmp_path, settings, _rows())
    d = overview_payload(rt, now=NOW)
    assert all(o["value"] is None or o["value"]["advice"] is None for o in d["orders"])
    assert d["valuation"]["counts"]["advice"] is None


def test_in_trade_orders_get_no_advice(tmp_path, settings):
    o = _order("T1", 90, tid=1)
    o.in_trade_since = NOW
    rt, _ = _rt(tmp_path, settings, [o, _snap("T1", TODAY, 3, 60), _val(1)])
    assert overview_payload(rt, now=NOW)["orders"][0]["value"]["advice"]["kind"] == "none"


def test_overview_valuation_is_null_before_the_first_run(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings, [_order("P1", 50, tid=1), _snap("P1", TODAY, 3, 60)])
    d = overview_payload(rt, now=NOW)
    assert d["valuation"] is None and d["orders"][0]["value"] is None


def test_conditional_orders_are_flagged(tmp_path, settings):
    o = _order("C1", 50, tid=1)
    o.queue_key = "1|0.00-0.07|-1|ANY|ANY"
    o.abrade_text = "0.00-0.07"
    rt, _ = _rt(tmp_path, settings, [o, _snap("C1", TODAY, 3, 60), _val(1)])
    assert overview_payload(rt, now=NOW)["orders"][0]["value"]["conditional"] is True


def test_value_query_count_does_not_grow_with_orders(tmp_path, settings):
    def count(n, sub):
        rows = []
        for i in range(n):
            rows += [_order(f"N{i}", 10, tid=i + 1), _snap(f"N{i}", TODAY, 2, 11), _val(i + 1)]
        rt, engine = _rt(sub, settings, rows)
        calls = []
        event.listen(engine, "before_cursor_execute", lambda *a: calls.append(1))
        overview_payload(rt, now=NOW)
        return len(calls)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert count(3, tmp_path / "a") == count(30, tmp_path / "b")


def test_item_valuation_block_with_series90(tmp_path, settings):
    from tests.market_fakes import with_item
    from tests.test_web import _client

    c, rt = _client(tmp_path, settings)          # 模板 1414，单 A（第 3）、B（第 1）、C；榜首 110，我的价 100
    with_item(rt)
    now = utcnow()
    with rt.session_factory() as s:
        s.add(_val(1414, exit=D("150"), q10=D("-0.1")))
        for k in range(90):
            d = (date(2026, 9, 26) - timedelta(days=89 - k)).isoformat()
            if k != 10:
                s.add(DailyBar(template_id=1414, platform="YOUPIN", date=d, open=D("1"), close=D(str(100 + k)),
                               high=D("1"), low=D("1"), fetched_at=now))
            if k != 20:
                s.add(IndexDaily(date=d, open=D("1"), close=D("1000"), high=D("1"), low=D("1"), fetched_at=now))
        s.commit()
    j = c.get("/api/item/1414").json()
    v = j["valuation"]
    assert v["as_of"] == "2026-09-26" and v["c90"] == -0.2 and v["b90"] == -0.17 and v["ex30"] == -0.03
    # 默认按排名最好的那张（B，第 1）算：已是第 1，抢第 1 那项为 null
    assert v["m_my"] == 0.5 and v["m_top"] is None and v["m_bad"] == 0.35
    s90 = v["series90"]
    assert len(s90) == 90 and s90[0][0] == "2026-06-29" and s90[-1] == ["2026-09-26", 189.0, 1000.0]
    assert s90[10][1] is None and s90[20][2] is None           # 缺那天：null，不是 0
    assert v["fees"] == {"YOUPIN": 0.01, "BUFF": 0.015, "withdraw": 0.01, "undercut": 0.01} and v["target_margin"] == 0.15
    assert j["orders"][0]["value"]["m_my"] == 0.5               # 行里也带 value，和总览一致
    # 建议也按那张单算：100 > 上限 70（坏情况下还赚 35%），考虑降价；每行也各自带建议
    assert (v["advice"]["kind"], v["advice"]["price"]) == ("lower", 70.0)
    assert all(o["value"]["advice"] is not None for o in j["orders"])


def test_item_valuation_null_without_rows(tmp_path, settings):
    from tests.market_fakes import with_item
    from tests.test_web import _client

    c, rt = _client(tmp_path, settings)
    with_item(rt)
    assert c.get("/api/item/1414").json()["valuation"] is None


def test_status_valuation_block(tmp_path, settings):
    from tests.test_web import _client

    c, rt = _client(tmp_path, settings)
    st = c.get("/api/status").json()["valuation"]
    assert st == {"enabled": True, "run_at": "00:30", "last": None}
    with rt.session_factory() as s:
        s.add(_val(1414))
        s.commit()
    st = c.get("/api/status").json()["valuation"]
    assert st["last"]["as_of"] == "2026-09-26" and st["last"]["valued"] == 1 and st["last"]["computed_at"].endswith("Z")
