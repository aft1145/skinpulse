"""`skinpulse demo`：假数据建库、只读看板、不联网、不读钥匙串。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from skinpulse.adapters.steamdt.models import KlinePeriod
from skinpulse.demo import build_demo_runtime
from skinpulse.demo.market import DemoMarket
from skinpulse.demo.seed import CATALOG, ORDERED
from skinpulse.web.app import create_app

NOW = datetime(2026, 11, 23, 6, 0, tzinfo=timezone.utc)     # 本地 14:00，不在选品的夜间窗口里
PORT = 8799


@pytest.fixture
def client(tmp_path, monkeypatch):
    def no_keyring(*a, **kw):
        raise AssertionError("演示模式不该碰系统钥匙串")

    def no_network(self, request):
        raise AssertionError(f"演示模式不该联网：{request.url.host}")

    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(f"skinpulse.secrets.keyring.{name}", no_keyring)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    rt = build_demo_runtime(tmp_path / "demo", PORT, now=NOW)
    return TestClient(create_app(rt, None), base_url=f"http://127.0.0.1:{PORT}")


def test_every_page_has_data(client):
    ov = client.get("/api/overview").json()
    assert ov["headline"]["counts"]["1"] >= 4
    assert client.get("/api/meta").json()["service"]["healthy"] is True
    meta = client.get("/api/meta").json()
    assert meta["demo"] is True and all(meta["setup"].values())         # demo 不显示「开始使用」

    item = client.get(f"/api/item/{CATALOG[0][2]}")
    assert item.status_code == 200 and "AK-47" in item.text

    market = client.get("/api/market").json()
    assert market["configured"] is True

    assert len(client.get("/api/fills").json()["fills"]) == 6
    # 简报页会把「到真实的昨天为止」没记录的日子补成 gap，NOW 是固定的，所以只数真发过的
    assert len([r for r in client.get("/api/brief").json()["rows"] if r["kind"] != "gap"]) == 2
    assert client.get("/api/analysis").status_code == 200

    runs = client.get("/api/pick/runs").json()["runs"]
    assert [r["status"] for r in runs] == ["done"]
    detail = client.get(f"/api/pick/runs/{runs[0]['id']}").json()
    assert detail["results"] and {r["verdict"] for r in detail["results"]} - {"none"}

    assert client.get("/api/status").status_code == 200

    paused = client.get("/api/paused").json()
    assert paused["result"]["status"] == "done" and paused["paused_since"] == 2
    tiers = paused["summary"]["tiers"]
    assert all(tiers[t]["count"] for t in ("inverted", "top1", "near", "near_sell", "other"))
    assert paused["summary"]["old"] == 2 and paused["items"][0]["tier"] == "inverted"


@pytest.mark.parametrize("path", ["/api/settings", "/api/youpin/token", "/api/pushplus/token", "/api/youpin/sync",
                                  "/api/pick/runs", "/api/system/restart",
                                  "/api/paused/check"])
def test_writes_are_refused(client, path):
    r = client.post(path, json={})
    assert r.status_code == 403 and "演示模式" in r.json()["detail"]


def test_reuses_existing_demo_dir(tmp_path):
    first = build_demo_runtime(tmp_path / "d", PORT, now=NOW)
    again = build_demo_runtime(tmp_path / "d", PORT, now=NOW + timedelta(hours=1))   # 不会再灌一遍
    from skinpulse.db.models import MyBuyOrder
    with first.session_factory() as a, again.session_factory() as b:
        # 6 张成交过的单，加 1 张小号挂在主号前面的单（多账号 spec §9），加 BUFF 号的 3 张（BUFF spec §13）
        assert a.query(MyBuyOrder).count() == b.query(MyBuyOrder).count() == ORDERED + 10


def test_demo_has_two_accounts_and_an_own_order_ahead(tmp_path):
    from skinpulse.web.overview import meta_payload, overview_payload
    rt = build_demo_runtime(tmp_path / "d", PORT, now=NOW)
    assert [a["label"] for a in meta_payload(rt)["accounts"]] == ["主号", "小号", "BUFF"]
    d = overview_payload(rt)
    assert d["accounts"] == {"1": "主号", "2": "小号", "3": "BUFF"}
    assert any(o["own_ahead"] == {"label": "小号", "price": o["top1_price"]} for o in d["orders"])
    assert {o["account_id"] for o in overview_payload(rt, account_id=2)["orders"]} == {2}


def test_demo_market_ends_yesterday_and_varies_by_item():
    m = DemoMarket({CATALOG[0][0]: Decimal("175"), CATALOG[1][0]: Decimal("620")}, tz="Asia/Shanghai", now=NOW)
    a = m.get_item_kline(CATALOG[0][0], KlinePeriod.DAY)
    b = m.get_item_kline(CATALOG[1][0], KlinePeriod.DAY)
    tz = ZoneInfo("Asia/Shanghai")
    assert a[-1].ts.astimezone(tz).date() == NOW.astimezone(tz).date() - timedelta(days=1)
    assert a[-1].close == Decimal("175.00") and b[-1].close == Decimal("620.00")
    assert all(x.low <= min(x.open, x.close) and x.high >= max(x.open, x.close) for x in a + b)
    ratio = [float(y.close / x.close) for x, y in zip(a, b)]
    assert max(ratio) - min(ratio) > 0.1                       # 不是同一条线整体缩放
    assert m.get_item_kline("没有这个饰品", KlinePeriod.DAY) == []
    assert m.prices(CATALOG[0][0]).value.of("YOUPIN").sell_price == Decimal("175.00")


def test_refuses_a_real_data_dir(tmp_path):
    real = tmp_path / "data"
    real.mkdir()
    (real / "skinpulse.db").write_bytes(b"")
    (real / "settings.toml").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="不是 demo 建的"):
        build_demo_runtime(real, PORT, now=NOW)
    assert (real / "settings.toml").read_text(encoding="utf-8") == "x = 1\n"


def test_demo_has_a_buff_account_with_three_orders(tmp_path):
    from skinpulse.web.overview import overview_payload
    rt = build_demo_runtime(tmp_path / "d", PORT, now=NOW)
    assert set(rt.buff_accounts) == {3} and set(rt.accounts) == {1, 2}
    rows = [o for o in overview_payload(rt)["orders"] if o["platform"] == "buff"]
    assert sorted(o["rank"] for o in rows) == [1, 2, 4]
    assert any(o["cond_ahead"] == 4 for o in rows) and any(o["queue"] == "磨损 0.07-0.08" for o in rows)
    assert any(o["template_id"] < 0 for o in rows) and all(o["value"] is None for o in rows)
