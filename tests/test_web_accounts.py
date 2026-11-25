"""多账号的看板接口（多账号 spec §7）：添加、改简称、停用、立即同步、总览按号筛选、own_ahead、meta。"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from skinpulse.adapters.youpin.models import UserInfo
from skinpulse.db.models import Account, AlertStateRow, MyBuyOrder, OrderEvent
from skinpulse.db.session import make_engine, make_session_factory, upgrade_db, utcnow
from skinpulse.runtime import Runtime
from skinpulse.services.monitor import Monitor
from skinpulse.web.app import create_app
from skinpulse.web.overview import meta_payload, overview_payload, own_ahead_map
from tests.test_web_token import NEW, _Sched


class _Secrets:
    def __init__(self):
        self.data = {}

    def get(self, k):
        return self.data.get(k)

    def set(self, k, v):
        self.data[k] = v


class _Yp:
    """校验用的 client 替身：get_user_info 给出预设的号。"""
    def __init__(self, user_id=2, nickname="小号昵称"):
        self.user_id, self.nickname = user_id, nickname
        self.token = None
        self.device_id = f"dev-{user_id}"

    def get_user_info(self):
        return UserInfo(self.user_id, self.nickname)

    def search_my_purchase_orders(self, *a, **k):
        return []


def _order(no, price, account_id, *, key="1414|ANY|-1|ANY|ANY", created=None):
    raw = {"createTime": created} if created else {}
    import json
    return MyBuyOrder(purchase_no=no, template_id=1414, commodity_name=f"AK-47 | 红线 {no} (久经沙场)",
                      price=Decimal(str(price)), qty=1, remaining_qty=1, type_id=-1, queue_key=key,
                      synced_at=utcnow(), active=True, account_id=account_id, conditions_json=json.dumps(raw))


def _rt(tmp_path, settings, *rows, accounts=((1, "主号"),)):
    db = tmp_path / "a.db"
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))
    with Session() as s:
        for i, label in accounts:
            s.add(Account(id=i, user_id=100 + i, nickname=f"n{i}", label=label, token_valid=True))
        for r in rows:
            s.add(r)
        s.commit()
    from skinpulse.config import save_settings
    sp = tmp_path / "settings.toml"
    save_settings(settings, sp)
    mon = Monitor(Session, None, None, settings, sleep=lambda s: None)
    rt = Runtime(settings, sp, _Secrets(), Session, client=None, ntfy=None, monitor=mon)
    from skinpulse.runtime import load_accounts
    load_accounts(rt)
    return rt


def _tc(rt, settings, *, new=None, sched=None):
    app = create_app(rt, sched, youpin_factory=lambda *a: _Yp(user_id=101), youpin_new_factory=lambda: new or _Yp())
    return TestClient(app, base_url=f"http://127.0.0.1:{settings.web.port}")


# ---- 添加 ----

def test_add_account_validates_saves_and_loads_it(tmp_path, settings):
    rt = _rt(tmp_path, settings)
    sched = _Sched()
    r = _tc(rt, settings, sched=sched).post("/api/accounts", json={"token": NEW, "label": " 小号 "})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == 2 and r.json()["round_started"] is True
    assert rt.secrets.get("youpin_token:2") == NEW
    assert sorted(rt.accounts) == [1, 2] and rt.accounts[2].label == "小号"
    assert rt.accounts[2].client.token == NEW
    with rt.session_factory() as s:
        acc = s.get(Account, 2)
        assert (acc.user_id, acc.label, acc.enabled, acc.device_id) == (2, "小号", True, "dev-2")


def test_add_account_rejects_the_same_youpin_account_twice(tmp_path, settings):
    rt = _rt(tmp_path, settings)
    r = _tc(rt, settings, new=_Yp(user_id=101)).post("/api/accounts", json={"token": NEW})
    assert r.status_code == 409 and "已存在（主号）" in r.json()["detail"]
    assert rt.secrets.get("youpin_token:2") is None


def test_add_account_label_is_at_most_eight_chars(tmp_path, settings):
    rt = _rt(tmp_path, settings)
    r = _tc(rt, settings).post("/api/accounts", json={"token": NEW, "label": "一二三四五六七八九"})
    assert r.status_code == 400


def test_add_account_on_a_fresh_install_fills_account_one(tmp_path, settings):
    rt = _rt(tmp_path, settings, accounts=())
    r = _tc(rt, settings).post("/api/accounts", json={"token": NEW, "label": "主号"})
    assert r.status_code == 200 and r.json()["id"] == 1
    assert rt.secrets.get("youpin_token") == NEW


def test_update_token_of_another_account(tmp_path, settings):
    rt = _rt(tmp_path, settings, accounts=((1, "主号"), (2, "小号")))
    app = create_app(rt, youpin_factory=lambda *a: _Yp(user_id=102))
    tc = TestClient(app, base_url=f"http://127.0.0.1:{settings.web.port}")
    r = tc.post("/api/youpin/token", json={"token": NEW, "account_id": 2})
    assert r.status_code == 200, r.text
    assert rt.secrets.get("youpin_token:2") == NEW and rt.accounts[2].client.token == NEW
    assert rt.secrets.get("youpin_token") is None
    assert tc.post("/api/youpin/token", json={"token": NEW, "account_id": 9}).status_code == 404


# ---- 改简称、停用 ----

def test_disable_account_deactivates_its_orders_without_gone_events(tmp_path, settings):
    rt = _rt(tmp_path, settings, _order("A1", 10, 1), _order("B1", 10, 2), accounts=((1, "主号"), (2, "小号")))
    with rt.session_factory() as s:
        s.add(AlertStateRow(purchase_no="B1", state="ALERTING", streak=1, updated_at=utcnow()))
        s.commit()
    tc = _tc(rt, settings)
    assert tc.patch("/api/accounts/2", json={"enabled": False}).status_code == 200
    assert sorted(rt.accounts) == [1]
    with rt.session_factory() as s:
        assert s.get(MyBuyOrder, "B1").active is False and s.get(MyBuyOrder, "A1").active is True
        assert s.get(AlertStateRow, "B1").state == "NORMAL"
        assert not list(s.scalars(select(OrderEvent)))
    # 最后一个启用的号停不掉
    r = tc.patch("/api/accounts/1", json={"enabled": False})
    assert r.status_code == 409
    assert tc.patch("/api/accounts/2", json={"enabled": True}).status_code == 200
    assert sorted(rt.accounts) == [1, 2]


def test_rename_account(tmp_path, settings):
    rt = _rt(tmp_path, settings, accounts=((1, "主号"), (2, "小号")))
    assert _tc(rt, settings).patch("/api/accounts/2", json={"label": "二号"}).status_code == 200
    assert rt.accounts[2].label == "二号"
    assert _tc(rt, settings).patch("/api/accounts/7", json={"label": "x"}).status_code == 404


# ---- 立即同步 ----

def test_sync_one_account_or_all(tmp_path, settings):
    rt = _rt(tmp_path, settings, accounts=((1, "主号"), (2, "小号")))
    calls = []
    for a in rt.accounts.values():
        a.client = SimpleNamespace(get_user_info=lambda a=a: UserInfo(100 + a.id, "n"),
                                   search_my_purchase_orders=lambda a=a: calls.append(a.id) or [])
    tc = _tc(rt, settings)
    assert tc.post("/api/youpin/sync", json={"account_id": 2}).status_code == 200
    assert calls == [2]
    assert tc.post("/api/youpin/sync", json={}).status_code == 200
    assert calls == [2, 1, 2]
    assert tc.post("/api/youpin/sync", json={"account_id": 5}).status_code == 404


# ---- 总览、meta ----

def test_overview_filters_by_account_and_marks_own_orders_ahead(tmp_path, settings):
    rt = _rt(tmp_path, settings, _order("A1", 19.5, 1), _order("B1", 19.6, 2), _order("A2", 5, 1, key="9|ANY|-1|ANY|ANY"),
             accounts=((1, "主号"), (2, "小号")))
    d = overview_payload(rt)
    assert d["accounts"] == {"1": "主号", "2": "小号"}
    by = {o["purchase_no"]: o for o in d["orders"]}
    assert by["A1"]["own_ahead"] == {"label": "小号", "price": 19.6}
    assert by["B1"]["own_ahead"] is None and by["A2"]["own_ahead"] is None
    assert {o["purchase_no"] for o in overview_payload(rt, account_id=2)["orders"]} == {"B1"}


def test_own_ahead_rules():
    labels = {1: "主号", 2: "小号"}
    same_price_earlier = _order("B", 10, 2, created="2026-11-01 10:00:00")
    me = _order("A", 10, 1, created="2026-11-02 10:00:00")
    later = _order("C", 10, 2, created="2026-11-03 10:00:00")
    mine_too = _order("D", 20, 1)
    other_queue = _order("E", 30, 2, key="1414|0.15-0.18|-1|ANY|ANY")
    out = own_ahead_map([same_price_earlier, me, later, mine_too, other_queue], labels)
    assert out["A"] == {"label": "小号", "price": 10.0}     # 同价早挂的算在前面
    assert "D" not in out                                    # 自己号的单不算
    assert own_ahead_map([me, same_price_earlier], {}) == {}  # 一个号时恒为空


def test_meta_lists_accounts_and_watches_the_worst_token(tmp_path, settings):
    import base64
    import json

    def jwt(days):
        exp = int((utcnow() + timedelta(days=days, hours=1)).timestamp())
        seg = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()  # noqa: E731
        return f"{seg({'alg': 'HS256'})}.{seg({'exp': exp})}.sig"
    rt = _rt(tmp_path, settings, accounts=((1, "主号"), (2, "小号")))
    rt.secrets.data = {"youpin_token": jwt(6), "youpin_token:2": jwt(2)}
    from skinpulse.web import overview as ov
    ov._token_cache.clear()
    m = meta_payload(rt)
    assert [a["label"] for a in m["accounts"]] == ["主号", "小号"]
    assert m["token"]["days_left"] == 2
    with rt.session_factory() as s:
        s.get(Account, 2).token_valid = False
        s.commit()
    m = meta_payload(rt)
    assert "token_invalid" in m["service"]["issues"] and m["token"]["valid"] is False
    rt.last_round = {"at": utcnow(), "checked": 1, "errors": 0, "sync_slow": True}
    assert "sync_slow" in meta_payload(rt)["service"]["issues"]
