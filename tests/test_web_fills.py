"""`GET /api/fills`：最近成交（成交推送 spec §6），和 overview 行上的 `in_trade_since`。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import event

from skinpulse.db.models import NotificationLog, OrderEvent
from skinpulse.web.app import create_app
from skinpulse.web.fills import fills_payload
from skinpulse.web.overview import overview_payload
from tests.test_web_overview import _order, _rt

NOW = datetime(2026, 10, 2, 3, 0, tzinfo=timezone.utc)        # 10-02 11:00 上海


def _gone_done(no, filled_at, *, detected=None, price="5", notice="sent", tid=1):
    return OrderEvent(purchase_no=no, template_id=tid, kind="gone", qty_before=1, qty_after=None,
                      price=Decimal(price), at=detected or filled_at + timedelta(minutes=5),
                      outcome="done", outcome_at=filled_at, outcome_qty=1, notice=notice)


def _log(no, event, at):
    return NotificationLog(fingerprint=no, event=event, rank=None, payload_json="{}", created_at=at,
                           sent_at=at + timedelta(seconds=2), status="sent")


def _rows(tmp_path, settings):
    t_ssg = datetime(2026, 10, 2, 1, 55, 14, tzinfo=timezone.utc)
    t_awp = datetime(2026, 10, 1, 10, 16, 6, tzinfo=timezone.utc)
    return [
        _order("SSG", 3.9, name="SSG 08 | 渐变强酸 (崭新出厂)", tid=4579),
        _order("AWP", 150, name="AWP | 树蝰 (崭新出厂)", tid=2230),
        _order("ST", 7, name="CZ75（StatTrak™） | 短趾雕 (崭新出厂)", tid=9),
        _order("OLD", 24, name="格洛克 18 型 | 格更鸟 (崭新出厂)", tid=8),
        # 成交后补推：Mac 睡着，10:20 才发现
        _gone_done("SSG", t_ssg, detected=t_ssg + timedelta(minutes=25), price="3.9", tid=4579),
        _log("SSG", "FILLED", t_ssg + timedelta(minutes=25)),
        # 交易中时推送过，成交那条 skipped
        _gone_done("AWP", t_awp, price="150", notice="skipped", tid=2230),
        _log("AWP", "IN_TRADE", t_awp - timedelta(minutes=26)),
        # 部分成交：只有发现时间
        OrderEvent(purchase_no="ST", template_id=9, kind="filled", qty_before=3, qty_after=1, price=Decimal("7"),
                   at=datetime(2026, 9, 29, 6, 43, tzinfo=timezone.utc), notice="pending"),
        # 回填的老成交：notice 为空
        _gone_done("OLD", datetime(2026, 9, 6, 16, 48, tzinfo=timezone.utc), price="24", notice=None, tid=8),
        # 超出 30 天的、被暂停的、撤单的：都不算
        _gone_done("OLD", datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc), price="24", notice=None, tid=8),
        OrderEvent(purchase_no="ST", template_id=9, kind="gone", qty_before=1, price=Decimal("7"),
                   at=NOW - timedelta(hours=3), outcome="paused"),
        OrderEvent(purchase_no="ST", template_id=9, kind="gone", qty_before=1, price=Decimal("7"),
                   at=NOW - timedelta(hours=2), outcome="missing"),
    ]


def test_fills_payload_lists_recent_fills_newest_first(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings, _rows(tmp_path, settings))
    d = fills_payload(rt, now=NOW)
    got = [(f["purchase_no"], f["qty"], f["exact"], f["notice_kind"]) for f in d["fills"]]
    assert got == [("SSG", 1, True, "FILLED"), ("AWP", 1, True, "IN_TRADE"), ("ST", 2, False, None),
                   ("OLD", 1, True, None)]
    ssg = d["fills"][0]
    assert ssg["title"] == "SSG 08 | 渐变强酸" and ssg["tag"] == "崭新出厂" and ssg["template_id"] == 4579
    assert ssg["price"] == 3.9 and ssg["filled_at"] == "2026-10-02T01:55:14Z"
    assert ssg["detected_at"] == "2026-10-02T02:20:14Z" and ssg["notice_at"] == "2026-10-02T02:20:16Z"
    assert d["fills"][1]["notice_at"] == "2026-10-01T09:50:08Z"            # 交易中那条的发送时间
    assert d["fills"][2]["stattrak"] is True and d["fills"][2]["title"] == "CZ75 | 短趾雕"
    assert d["total_30d"] == {"count": 5, "amount": 191.9, "largest": {"title": "AWP | 树蝰", "price": 150.0}}
    daily = d["daily"]
    assert len(daily) == 30 and daily[0] == ["2026-09-03", 0] and daily[-1] == ["2026-10-02", 1]
    assert dict(daily)["2026-09-29"] == 2 and dict(daily)["2026-09-07"] == 1 and dict(daily)["2026-10-01"] == 1


def test_fills_payload_empty(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings)
    d = fills_payload(rt, now=NOW)
    assert d["fills"] == [] and d["total_30d"] == {"count": 0, "amount": 0.0, "largest": None}
    assert len(d["daily"]) == 30 and all(n == 0 for _, n in d["daily"])


def test_fills_query_count_does_not_grow(tmp_path, settings):
    rows = []
    for i in range(30):
        rows += [_order(f"P{i}", 5), _gone_done(f"P{i}", NOW - timedelta(days=1, minutes=i))]
    rt, engine = _rt(tmp_path, settings, rows)
    n = {"q": 0}
    event.listen(engine, "before_cursor_execute", lambda *a: n.__setitem__("q", n["q"] + 1))
    fills_payload(rt, now=NOW)
    assert n["q"] <= 4


def test_api_fills_route(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings, _rows(tmp_path, settings))
    c = TestClient(create_app(rt), base_url=f"http://127.0.0.1:{settings.web.port}")
    r = c.get("/api/fills?days=7")
    assert r.status_code == 200 and len(r.json()["daily"]) == 7


def test_overview_rows_carry_in_trade_since(tmp_path, settings):
    o = _order("A", 150)
    o.in_trade_since = NOW - timedelta(minutes=10)
    rt, _ = _rt(tmp_path, settings, [o, _order("B", 5)])
    rows = {r["purchase_no"]: r for r in overview_payload(rt, now=NOW)["orders"]}
    assert rows["A"]["in_trade_since"] == "2026-10-02T02:50:00Z" and rows["A"]["rank"] is None
    assert rows["B"]["in_trade_since"] is None


def test_missing_price_is_left_out_of_the_total_not_counted_as_zero(tmp_path, settings):
    ev = _gone_done("X", NOW - timedelta(hours=2), price="5")
    ev.price = None
    rt, _ = _rt(tmp_path, settings, [_order("X", 5), ev, _order("Y", 7), _gone_done("Y", NOW - timedelta(hours=3), price="7")])
    d = fills_payload(rt, now=NOW)
    assert d["total_30d"]["amount"] == 7.0 and d["total_30d"]["largest"]["price"] == 7.0
    assert [f["price"] for f in d["fills"]] == [None, 7.0]


def test_likely_fill_is_listed_as_an_estimate(tmp_path, settings):
    """推测成交（intent 2026-11-23）：列出来、计入合计，但标 likely、时间不算精确。"""
    sold = datetime(2026, 10, 2, 2, 29, 37, tzinfo=timezone.utc)
    rows = [
        _order("PKG", 13, name="2023年巴黎锦标赛荒漠迷城纪念包", tid=104207),
        OrderEvent(purchase_no="PKG", template_id=104207, kind="gone", qty_before=1, price=Decimal("13"),
                   at=sold + timedelta(minutes=7), outcome="likely_done", outcome_at=sold, notice="skipped",
                   in_trade_at=sold + timedelta(minutes=2)),
        _log("PKG", "IN_TRADE", sold + timedelta(minutes=4)),
    ]
    rt, _ = _rt(tmp_path, settings, rows)
    d = fills_payload(rt, now=NOW)
    f = d["fills"][0]
    assert (f["purchase_no"], f["likely"], f["exact"], f["notice_kind"]) == ("PKG", True, False, "IN_TRADE")
    assert f["filled_at"] == "2026-10-02T02:29:37Z" and f["qty"] == 1
    assert d["total_30d"]["count"] == 1
