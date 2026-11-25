"""价位三期选品的接口（价位 spec §9b.4）。"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from skinpulse.db.models import PickResult
from skinpulse.services import pick as sp
from skinpulse.web.app import create_app
from tests.test_services_pick import FakeDT, seed_catalog
from tests.test_web_overview import _rt

NOW = datetime(2026, 10, 10, 6, 0, tzinfo=timezone.utc)


class SyncThread:
    """测试里把后台线程换成同步执行：POST 返回时结果已经在库里了。"""

    def __init__(self, target, args=(), **kw):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def client(tmp_path, settings, monkeypatch, *, steamdt=None, n=3):
    rt, _ = _rt(tmp_path, settings)
    with rt.session_factory() as s:
        seed_catalog(s, n=n, extra=[(9, "Sticker | Clown Nose")])
    rt.steamdt = steamdt
    monkeypatch.setattr(sp, "utcnow", lambda: NOW)
    monkeypatch.setattr(sp, "threading", type("T", (), {"Thread": SyncThread, "Lock": threading.Lock}))
    return TestClient(create_app(rt), base_url=f"http://127.0.0.1:{settings.web.port}"), rt


def test_options_and_estimate(tmp_path, settings, monkeypatch):
    c, _ = client(tmp_path, settings, monkeypatch)
    o = c.get("/api/pick/options").json()
    assert {g["key"]: g["default"] for g in o["groups"]}["sticker"] is False
    assert o["subs"]["rifle"] == [{"key": "AK-47", "label": "AK 1", "n": 3}]     # 中文名取目录的 cn_name
    assert o["max"] == 500 and o["night"] == "23:40～00:50"
    e = c.get("/api/pick/estimate", params={"groups": "rifle,sticker", "wears": "ft"}).json()
    assert e["count"] == 3 and e["over"] is False
    assert c.get("/api/pick/estimate", params={"groups": "rifle", "wears": "new"}).status_code == 422


def test_start_run_and_read_results(tmp_path, settings, monkeypatch):
    dt = FakeDT({"AK-47 | Skin1 (Field-Tested)": ("120", 200, "100")})
    c, rt = client(tmp_path, settings, monkeypatch, steamdt=dt, n=1)
    r = c.post("/api/pick/runs", json={"groups": ["rifle"]})
    assert r.status_code == 200
    run_id = r.json()["id"]
    j = c.get(f"/api/pick/runs/{run_id}").json()
    assert j["status"] == "done" and j["busy"] is False and j["counts"]["go"] == 1
    row = j["results"][0]
    assert (row["title"], row["verdict"], row["label"], row["bid"], row["yp_bid"]) == ("AK 1", "go", "可以挂", 100.01, 100.0)
    assert (j["rebound_window"], j["rebound_max"]) == (60, 0.3) and "rebound" in row and "amp" in row
    lst = c.get("/api/pick/runs").json()
    assert [x["id"] for x in lst["runs"]] == [run_id] and lst["runs"][0]["filters"]["groups"] == ["rifle"]
    assert c.get("/api/pick/runs/999").status_code == 404


def test_start_refusals(tmp_path, settings, monkeypatch):
    c, _ = client(tmp_path, settings, monkeypatch)
    r = c.post("/api/pick/runs", json={"groups": ["rifle"]})
    assert r.status_code == 503 and "SteamDT" in r.json()["detail"]
    # 写接口只收同源 JSON
    assert c.post("/api/pick/runs", content="groups=rifle", headers={"content-type": "application/x-www-form-urlencoded"}).status_code in (403, 415)


def test_missing_values_stay_null(tmp_path, settings, monkeypatch):
    c, rt = client(tmp_path, settings, monkeypatch)
    with rt.session_factory() as s:
        run = sp.create_run(s, sp.pk.PoolFilter(), NOW)
        s.add(PickResult(run_id=run.id, template_id=1, bars=0, verdict="none", rule="没有在售价", first=False, mine=False))
        s.commit()
        rid = run.id
    row = c.get(f"/api/pick/runs/{rid}").json()["results"][0]
    assert row["yp_sell"] is None and row["cap"] is None and row["pct"] is None and row["label"] == "不评价"
    assert row["rebound"] is None and row["amp"] is None                 # 老结果、日线不够：不填 0


def test_options_list_the_wears_and_versions_each_group_really_has(tmp_path, settings, monkeypatch):
    """intent 2026-11-23：探员没有磨损和版本，页面要按大类只列有的。零星特例（少于 FACET_MIN 个）不列。"""
    from skinpulse.db.models import Item
    from skinpulse.web import pick as wp

    rt, _ = _rt(tmp_path, settings)
    monkeypatch.setattr(wp, "FACET_MIN", 2)
    rows = [("AK-47 | A (Factory New)", 1), ("AK-47 | B (Factory New)", 2), ("StatTrak™ AK-47 | A (Field-Tested)", 3),
            ("StatTrak™ AK-47 | B (Field-Tested)", 4), ("AK-47 | Vanilla", 5),                     # 一个无磨损：不够 2 个
            ("Agent X | FBI", 6), ("Agent Y | SWAT", 7),
            ("★ Karambit", 8), ("★ Bayonet", 9), ("★ StatTrak™ Karambit | Fade (Factory New)", 10),
            ("★ StatTrak™ Bayonet | Fade (Factory New)", 11)]
    with rt.session_factory() as s:
        for name, tid in rows:
            s.add(Item(hash_name=name, cn_name=name, youpin_template_id=tid))
        s.commit()
    c = TestClient(create_app(rt), base_url=f"http://127.0.0.1:{settings.web.port}")
    f = c.get("/api/pick/options").json()["group_facets"]
    assert f["rifle"] == {"wears": ["fn", "ft"], "versions": ["normal", "stattrak"]}
    assert f["agent"] == {"wears": ["none"], "versions": ["normal"]}
    assert f["knife"] == {"wears": ["fn", "none"], "versions": ["normal", "stattrak"]}
