"""BUFF 的看板接口（BUFF spec §8）：添加 BUFF 号、换 cookie、停用、立即跑一轮、meta / 总览字段。
cookie 绝不出现在任何响应里。"""
from __future__ import annotations

import json
from decimal import Decimal

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi.testclient import TestClient

from skinpulse.adapters.buff.errors import BuffLoginRequired, BuffRateLimited
from skinpulse.adapters.buff.models import Me
from skinpulse.db.models import Account, MyBuyOrder
from skinpulse.db.session import utcnow
from skinpulse.web.app import create_app
from skinpulse.web.buff_account import normalize_cookie
from skinpulse.web.overview import meta_payload, overview_payload
from tests.test_web_accounts import _rt

COOKIE = "1-abcDEF_secret-cookie-value"


class _Buff:
    def __init__(self, me=Me("U1000000001", "BUFF昵称"), exc=None):
        self.me, self.exc = me, exc
        self.seen = []

    def __call__(self, cookie, device_id):
        self.seen.append((cookie, device_id))
        return self

    def whoami(self):
        if self.exc:
            raise self.exc
        return self.me


def _tc(rt, settings, buff, sched=None):
    app = create_app(rt, sched, buff_factory=buff)
    return TestClient(app, base_url=f"http://127.0.0.1:{settings.web.port}")


def _sched():
    return BackgroundScheduler(timezone="UTC")


def test_add_buff_account_saves_cookie_loads_runtime_and_schedules_round(tmp_path, settings):
    rt = _rt(tmp_path, settings)
    sched, buff = _sched(), _Buff()
    r = _tc(rt, settings, buff, sched).post("/api/accounts", json={"platform": "buff", "cookie": f"session={COOKIE}"})
    assert r.status_code == 200, r.text
    assert COOKIE not in r.text
    aid = r.json()["id"]
    assert r.json()["round_started"] is True
    assert rt.secrets.get(f"buff_cookie:{aid}") == COOKIE and buff.seen[0][0] == COOKIE
    assert set(rt.buff_accounts) == {aid} and rt.buff_accounts[aid].client.cookie == COOKIE
    assert set(rt.accounts) == {1}                              # 悠悠那份不动
    assert sched.get_job("buff_round") is not None
    with rt.session_factory() as s:
        acc = s.get(Account, aid)
        assert (acc.platform, acc.platform_uid, acc.nickname, acc.label) == ("buff", "U1000000001", "BUFF昵称", None)


def test_add_buff_account_errors(tmp_path, settings):
    rt = _rt(tmp_path, settings)
    c = _tc(rt, settings, _Buff(exc=BuffLoginRequired("Login Required")))
    assert c.post("/api/accounts", json={"platform": "buff", "cookie": "a b"}).status_code == 400
    r = c.post("/api/accounts", json={"platform": "buff", "cookie": COOKIE})
    assert r.status_code == 422 and COOKIE not in r.text
    c = _tc(rt, settings, _Buff(exc=BuffRateLimited("http429")))
    assert c.post("/api/accounts", json={"platform": "buff", "cookie": COOKIE}).status_code == 503
    assert _tc(rt, settings, _Buff()).post("/api/accounts", json={"platform": "buff", "cookie": COOKIE}).status_code == 200
    r = _tc(rt, settings, _Buff()).post("/api/accounts", json={"platform": "buff", "cookie": COOKIE + "x"})
    assert r.status_code == 409          # 同一个 BUFF 用户号


def test_update_cookie_same_account_only(tmp_path, settings):
    rt = _rt(tmp_path, settings)
    aid = _tc(rt, settings, _Buff()).post("/api/accounts", json={"platform": "buff", "cookie": COOKIE}).json()["id"]
    r = _tc(rt, settings, _Buff()).post("/api/buff/cookie", json={"account_id": aid, "cookie": "new-one"})
    assert r.status_code == 200 and "new-one" not in r.text
    assert rt.secrets.get(f"buff_cookie:{aid}") == "new-one" and rt.buff_accounts[aid].client.cookie == "new-one"
    r = _tc(rt, settings, _Buff(me=Me("U999", "别人"))).post("/api/buff/cookie", json={"account_id": aid, "cookie": "other"})
    assert r.status_code == 409 and rt.secrets.get(f"buff_cookie:{aid}") == "new-one"
    assert _tc(rt, settings, _Buff()).post("/api/buff/cookie", json={"account_id": 1, "cookie": "x"}).status_code == 404


def test_disable_buff_account_removes_job_and_allows_disabling_it_alone(tmp_path, settings):
    rt = _rt(tmp_path, settings)
    sched = _sched()
    c = _tc(rt, settings, _Buff(), sched)
    aid = c.post("/api/accounts", json={"platform": "buff", "cookie": COOKIE}).json()["id"]
    r = c.patch(f"/api/accounts/{aid}", json={"enabled": False, "label": "B号"})
    assert r.status_code == 200, r.text
    assert rt.buff_accounts == {} and sched.get_job("buff_round") is None
    assert c.post("/api/buff/sync", json={}).json() == {"round_started": False}


def test_meta_and_overview_show_buff(tmp_path, settings):
    rt = _rt(tmp_path, settings)
    c = _tc(rt, settings, _Buff())
    aid = c.post("/api/accounts", json={"platform": "buff", "cookie": COOKIE}).json()["id"]
    with rt.session_factory() as s:
        s.add(MyBuyOrder(purchase_no="buff:261123T1", account_id=aid, template_id=-1134183,
                         commodity_name="AK-47 | 翔鹤 (略有磨损)", price=Decimal("101"), qty=1, remaining_qty=1,
                         queue_key="buff:1134183:plain", synced_at=utcnow(), active=True, cond_ahead=7,
                         conditions_json=json.dumps({"goods_id": 1134183})))
        s.commit()
    meta = meta_payload(rt)
    b = next(a for a in meta["accounts"] if a["platform"] == "buff")
    assert b["label"] == "BUFF" and b["token"]["expires_at"] is None and b["orders"] == 1
    assert "token_invalid" not in meta["service"]["issues"] and "not_logged_in" not in meta["service"]["issues"]
    ov = overview_payload(rt)
    row = next(o for o in ov["orders"] if o["purchase_no"] == "buff:261123T1")
    assert row["platform"] == "buff" and row["cond_ahead"] == 7 and row["queue"] == "不限"
    assert row["buff_url"] == "https://buff.163.com/goods/1134183" and row["value"] is None
    assert ov["accounts"] == {"1": "主号", str(aid): "BUFF"}
    assert COOKIE not in json.dumps(meta, ensure_ascii=False) + json.dumps(ov, ensure_ascii=False)


def test_normalize_cookie():
    assert normalize_cookie("  session=abc; Path=/ ") == "abc"
    assert normalize_cookie('"abc"') == "abc"
    assert normalize_cookie("a b") == "" and normalize_cookie(None) == ""


def test_changing_buff_settings_reaches_running_clients(tmp_path, settings):
    rt = _rt(tmp_path, settings)
    c = _tc(rt, settings, _Buff())
    aid = c.post("/api/accounts", json={"platform": "buff", "cookie": COOKIE}).json()["id"]
    r = c.post("/api/settings", json={"buff": {"request_gap_s": 12}})
    assert r.status_code == 200, r.text
    assert rt.buff_accounts[aid].client.s.request_gap_s == 12
    r = c.post("/api/settings", json={"buff": {"user_agent": "x"}})
    assert r.status_code == 422


def test_platform_names_switch_how_accounts_are_written(tmp_path, settings):
    """`[dashboard] platform_names`：顶栏切换、表格标签写平台名；系统页卡片的 label 不变；同一平台几个号时带简称。"""
    rt = _rt(tmp_path, settings)
    c = _tc(rt, settings, _Buff())
    aid = c.post("/api/accounts", json={"platform": "buff", "cookie": COOKIE}).json()["id"]
    r = c.post("/api/settings", json={"dashboard": {"platform_names": True}})
    assert r.status_code == 200 and r.json()["changed"] == ["dashboard.platform_names"]
    meta = meta_payload(rt)
    tags = {a["id"]: a["tag"] for a in meta["accounts"]}
    assert tags == {1: "悠悠", aid: "BUFF"}
    assert next(a for a in meta["accounts"] if a["id"] == 1)["label"] == "主号"      # 系统页卡片照旧
    assert overview_payload(rt)["accounts"] == {"1": "悠悠", str(aid): "BUFF"}
    # 第二个悠悠号：同一平台有两个号，写「悠悠 · 简称」，简称没改过用昵称
    with rt.session_factory() as s:
        s.add(Account(platform="youpin", enabled=True, user_id=2, nickname="小号昵称"))
        s.commit()
    tags = {a["id"]: a["tag"] for a in meta_payload(rt)["accounts"]}
    assert tags[1] == "悠悠 · 主号" and tags[aid] == "BUFF"
    assert sorted(tags.values()) == ["BUFF", "悠悠 · 主号", "悠悠 · 小号昵称"]
    # 关掉：和原来一样
    c.post("/api/settings", json={"dashboard": {"platform_names": False}})
    assert {a["id"]: a["tag"] for a in meta_payload(rt)["accounts"]}[aid] == "BUFF"
    assert overview_payload(rt)["accounts"]["1"] == "主号"
