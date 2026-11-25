"""`/api/paused` 和「检查一次」（暂停单 spec §5.3、§3.4）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from types import SimpleNamespace

from fastapi.testclient import TestClient

from skinpulse.db.models import Account, MyBuyOrder, OrderEvent, PausedCheckItem, PausedCheckRun
from skinpulse.db.session import utcnow
from skinpulse.services.account import AccountRuntime
from skinpulse.web.app import create_app
from tests.test_web_overview import _rt


def client(rt, settings, sched=None):
    return TestClient(create_app(rt, sched), base_url=f"http://127.0.0.1:{settings.web.port}")


def seed_run(rt, *, status="done", started=None, items=()):
    started = started or utcnow() - timedelta(hours=1)
    with rt.session_factory() as s:
        r = PausedCheckRun(started_at=started, finished_at=started + timedelta(minutes=8), status=status,
                           source="dashboard", phase=None, done=0, total=0, incomplete=False, steamdt=True)
        s.add(r)
        s.flush()
        for it in items:
            s.add(PausedCheckItem(run_id=r.id, **it))
        s.commit()
        return r.id


def item(no, price, tier, *, top=None, empty=False, sell=None, depth=None, kind=None, days=3, name=None, account=1):
    return dict(purchase_no=no, account_id=account, template_id=1, commodity_name=name or "AK-47 | 红线 (略有磨损)",
                price=D(price), remaining_qty=1, paused_at=utcnow() - timedelta(days=days, hours=1),
                top_bid=None if top is None else D(top), top_empty=empty, sell_min=None if sell is None else D(sell),
                sell_depth=depth, sell_src="youpin" if sell is not None else None, sell_at=None, tier=tier,
                top1_kind=kind)


def test_never_checked(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings)
    with rt.session_factory() as s:
        s.add(OrderEvent(purchase_no="X", template_id=1, kind="gone", at=utcnow(), outcome="paused"))
        s.add(OrderEvent(purchase_no="Y", template_id=1, kind="gone", at=utcnow(), outcome="paused"))
        # 暂停过、后来恢复了的不算
        s.add(OrderEvent(purchase_no="Z", template_id=1, kind="gone", at=utcnow(), outcome="paused"))
        s.add(MyBuyOrder(purchase_no="Z", template_id=1, price=D("1"), queue_key="1|ANY", synced_at=utcnow(),
                         active=True))
        s.commit()
    body = client(rt, settings).get("/api/paused").json()
    assert body["run"] is None and body["result"] is None and body["summary"] is None
    assert body["known_paused"] == 2 and body["items"] == [] and body["accounts"] == {}


def test_done_result_shape_order_and_summary(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings)
    seed_run(rt, items=[
        item("A", "650", "inverted", top="190", sell="205", depth=1, kind="ahead", days=121,
             name="专业手套（★） | 狩鹿 (久经沙场)"),
        item("A2", "260", "inverted", top="200", sell="202", depth=10, kind="ahead"),
        item("B", "16", "top1", top="15.5", sell="16.38", depth=10, kind="ahead"),
        item("C", "95", "near", top="100", sell="120"),
        item("D", "1", "other", top="5"),
    ])
    body = client(rt, settings).get("/api/paused").json()
    assert body["result"]["status"] == "done" and body["run"]["id"] == body["result"]["id"]
    sm = body["summary"]
    assert sm["total"] == 5 and sm["old"] == 1 and sm["amount"] == 1022.0
    assert sm["tiers"]["inverted"] == {"count": 2, "amount": 910.0}
    assert sm["tiers"]["other"]["count"] == 1 and sm["tiers"]["near_sell"]["count"] == 0
    nos = [i["purchase_no"] for i in body["items"]]
    assert nos == ["A", "A2", "B", "C"]                      # other 不列出；倒挂按我的价 / 在售降序
    a = body["items"][0]
    assert (a["title"], a["star"], a["tag"], a["paused_days"], a["old"]) == ("专业手套 | 狩鹿", True, "久经沙场", 121, True)
    assert (a["over"], a["sell_depth"], a["sell_src"], a["top1_kind"]) == (460.0, 1, "youpin", "ahead")
    assert round(a["sell_ratio"], 2) == 3.17
    c = body["items"][3]
    assert c["over"] is None and c["gap_ratio"] == 0.05


def test_running_check_keeps_showing_the_last_result(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings)
    done = seed_run(rt, items=[item("A", "10", "top1", empty=True, kind="alone")])
    with rt.session_factory() as s:
        s.add(OrderEvent(purchase_no="N", template_id=1, kind="gone", at=utcnow(), outcome="paused"))
        s.commit()
    running = seed_run(rt, status="running", started=utcnow())
    body = client(rt, settings).get("/api/paused").json()
    assert body["run"]["id"] == running and body["run"]["status"] == "running"
    assert body["result"]["id"] == done and [i["purchase_no"] for i in body["items"]] == ["A"]
    assert body["paused_since"] == 1


def test_missing_prices_stay_null(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings)
    seed_run(rt, items=[item("A", "10", "top1", empty=True, kind="alone")])
    a = client(rt, settings).get("/api/paused").json()["items"][0]
    assert (a["top_bid"], a["sell_min"], a["sell_depth"], a["sell_ratio"], a["over"]) == (None, None, None, None, None)


def test_multi_account_labels(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings)
    with rt.session_factory() as s:
        s.add(Account(id=2, user_id=2, nickname="alt", token_valid=True, label="小号"))
        s.commit()
    assert client(rt, settings).get("/api/paused").json()["accounts"] == {"1": "me", "2": "小号"}


def test_check_without_scheduler_is_refused(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings)
    r = client(rt, settings).post("/api/paused/check", json={})
    assert r.status_code == 409 and "check-paused" in r.json()["detail"]


NOON = datetime(2026, 11, 24, 4, 0, tzinfo=timezone.utc)   # 北京时间 12:00，不在夜间窗口


def test_check_starts_a_job_and_refuses_a_second(tmp_path, settings, monkeypatch):
    import skinpulse.db.session as dbs

    monkeypatch.setattr(dbs, "utcnow", lambda: NOON)
    rt, _ = _rt(tmp_path, settings)
    rt.monitor._accounts = {1: AccountRuntime(1, SimpleNamespace(token="t"))}
    added = []
    c = client(rt, settings, SimpleNamespace(add_job=lambda *a, **k: added.append(k["id"])))
    r = c.post("/api/paused/check", json={})
    assert r.status_code == 200 and added == [f"paused_check_{r.json()['run_id']}"]
    r2 = c.post("/api/paused/check", json={})
    assert r2.status_code == 409 and "在跑" in r2.json()["detail"]
    assert c.get("/api/paused").json()["run"]["status"] == "running"


def test_check_without_logged_in_account(tmp_path, settings, monkeypatch):
    import skinpulse.db.session as dbs

    monkeypatch.setattr(dbs, "utcnow", lambda: NOON)
    rt, _ = _rt(tmp_path, settings)
    r = client(rt, settings, SimpleNamespace(add_job=lambda *a, **k: None)).post("/api/paused/check", json={})
    assert r.status_code == 409 and "没有登录" in r.json()["detail"]


def test_check_in_night_window(tmp_path, settings, monkeypatch):
    import skinpulse.db.session as dbs

    monkeypatch.setattr(dbs, "utcnow", lambda: datetime(2026, 11, 24, 15, 55, tzinfo=timezone.utc))  # 23:55
    rt, _ = _rt(tmp_path, settings)
    rt.monitor._accounts = {1: AccountRuntime(1, SimpleNamespace(token="t"))}
    r = client(rt, settings, SimpleNamespace(add_job=lambda *a, **k: None)).post("/api/paused/check", json={})
    assert r.status_code == 423


def test_demo_refuses_check(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings)
    rt.demo = True
    r = client(rt, settings, SimpleNamespace(add_job=lambda *a, **k: None)).post("/api/paused/check", json={})
    assert r.status_code == 403


def test_paused_since_skips_orders_the_check_already_saw(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings)
    started = utcnow() - timedelta(minutes=10)
    seed_run(rt, started=started, items=[item("SEEN", "10", "top1", empty=True, kind="alone")])
    with rt.session_factory() as s:
        # 翻页那半分钟里被暂停的：检查里已经有了，不算「检查之后又被暂停」
        s.add(OrderEvent(purchase_no="SEEN", template_id=1, kind="gone", at=started + timedelta(seconds=20), outcome="paused"))
        s.add(OrderEvent(purchase_no="NEW", template_id=1, kind="gone", at=started + timedelta(minutes=5), outcome="paused"))
        s.add(OrderEvent(purchase_no="OLD", template_id=1, kind="gone", at=started - timedelta(hours=1), outcome="paused"))
        s.commit()
    body = client(rt, settings).get("/api/paused").json()
    assert body["paused_since"] == 1 and body["known_paused"] == 3


def test_paused_tags_follow_platform_names(tmp_path, settings):
    """两个悠悠号、打开「账号写成平台名」：暂停单卡片的标签和账号切换写法一致（悠悠 · 简称）。"""
    from skinpulse.db.models import Account
    from skinpulse.web.overview import youpin_labels
    from tests.test_web_accounts import _rt
    rt = _rt(tmp_path, settings)
    with rt.session_factory() as s:
        s.add(Account(platform="youpin", enabled=True, user_id=2, nickname="小号昵称"))
        s.commit()
        assert sorted(youpin_labels(s).values()) == ["主号", "小号昵称"]
        assert sorted(youpin_labels(s, True).values()) == ["悠悠 · 主号", "悠悠 · 小号昵称"]
