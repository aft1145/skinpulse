"""BUFF 只读适配器（BUFF spec §3、§12）：fixture 是 2026-11-24 登录后的真实响应，脱敏过。"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from skinpulse.adapters.buff.client import BuffClient
from skinpulse.adapters.buff.errors import BuffError, BuffLoginRequired, BuffRateLimited
from skinpulse.config import BuffSettings

FX = Path(__file__).parent / "fixtures" / "buff"
BASE = "https://buff.163.com"


def fx(name: str) -> dict:
    return json.loads((FX / f"{name}.json").read_text(encoding="utf-8"))


def client(**kw) -> BuffClient:
    kw.setdefault("sleep", lambda s: None)
    return BuffClient(BuffSettings(), "SESSION-SECRET", "dev-1", **kw)


@respx.mock
def test_my_buy_orders_parses_fixture_and_sends_cookies():
    route = respx.get(f"{BASE}/api/market/buy_order/wait_supply").mock(return_value=httpx.Response(200, json=fx("wait_supply")))
    orders, me = client().my_buy_orders()
    assert len(orders) == 8 and me.uid == "U1000000001"
    ak = next(o for o in orders if o.goods_id == 1134183)
    assert ak.price == Decimal("101") and ak.buy_max_price == Decimal("102") and ak.specific == ()
    assert ak.market_hash_name == "AK-47 | Crane Flight (Minimal Wear)" and ak.remaining == 1
    req = route.calls[0].request
    assert "session=SESSION-SECRET" in req.headers["cookie"] and "Device-Id=dev-1" in req.headers["cookie"]
    assert req.url.params["game"] == "csgo" and req.url.params["page_size"] == "60"


@respx.mock
def test_queue_history_inventory_parse():
    respx.get(f"{BASE}/api/market/goods/buy_order").mock(return_value=httpx.Response(200, json=fx("queue_mixed")))
    respx.get(f"{BASE}/api/market/buy_order/history").mock(return_value=httpx.Response(200, json=fx("buy_history")))
    respx.get(f"{BASE}/api/market/sell_order/history").mock(return_value=httpx.Response(200, json=fx("sell_history")))
    respx.get(f"{BASE}/api/market/steam_inventory").mock(return_value=httpx.Response(200, json=fx("inventory")))
    c = client()
    q = c.buy_queue(1134183)
    assert len(q.rows) == 28 and q.rows[0].specific[0].values == ("0.07", "0.08")
    b = c.buy_history()
    assert [(x.type, x.state) for x in b.bills] == [(2, "SUCCESS"), (2, "SUCCESS"), (6, "SUCCESS"), (2, "FAIL")]
    s = c.sell_history()
    assert s.bills[0].income == Decimal("29.55") and s.bills[0].fee == Decimal("0.45")
    assert s.bills[0].transact_time is not None and s.bills[0].market_hash_name.startswith("P90")
    inv = c.inventory()
    assert inv.assets[0].assetid == "90000000046" and inv.total_page == 165
    assert respx.calls[-1].request.url.params["force"] == "0"


def test_no_counterparty_identity_in_dataclasses():
    """对手的 user_id、昵称不进数据类：repr 里一个都不能有。"""
    from skinpulse.adapters.buff.models import parse_bills, parse_queue
    text = repr(parse_queue(fx("queue_mixed")["data"])) + repr(parse_bills(fx("buy_history")["data"])) \
        + repr(parse_bills(fx("sell_history")["data"]))
    assert "U10000000" not in text and "用户" not in text


@respx.mock
@pytest.mark.parametrize("resp, exc", [
    (httpx.Response(200, json=fx("login_required")), BuffLoginRequired),
    (httpx.Response(200, json={"code": "Captcha Validate Required", "error": "访问异常, 请完成安全验证"}), BuffRateLimited),
    (httpx.Response(429, text="<html>429 Too Many Requests</html>"), BuffRateLimited),
    (httpx.Response(403, text="forbidden"), BuffRateLimited),
    (httpx.Response(200, text="<html>系统繁忙，请稍后再试</html>"), BuffRateLimited),
    (httpx.Response(200, json={"code": "Some Error", "error": "别的错"}), BuffError),
    (httpx.Response(200, text="not json"), BuffError),
])
def test_errors(resp, exc):
    respx.get(f"{BASE}/api/market/buy_order/wait_supply").mock(return_value=resp)
    with pytest.raises(exc) as e:
        client().whoami()
    assert "SESSION-SECRET" not in str(e.value) and "buff.163.com" not in str(e.value)


@respx.mock
def test_requests_are_paced_per_client():
    respx.get(f"{BASE}/api/market/goods/buy_order").mock(return_value=httpx.Response(200, json=fx("queue_mixed")))
    now = [100.0]
    slept = []
    c = BuffClient(BuffSettings(request_gap_s=6), "c", "d", sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)),
                   clock=lambda: now[0], rand=lambda: 0.5)
    c.buy_queue(1)
    now[0] += 1.0
    c.buy_queue(1)
    assert slept == [pytest.approx(6 * 1.15 - 1.0)]
    assert c.requests == 2


def test_client_only_uses_get():
    """只读靠结构保证：BUFF 包里没有别的 HTTP 动词。"""
    src = "".join(p.read_text(encoding="utf-8") for p in (Path(__file__).parents[1] / "skinpulse" / "adapters" / "buff").glob("*.py"))
    assert not re.search(r"\.(post|put|patch|delete|request|stream)\(", src)
    assert "self.http.get(" in src


def test_repr_hides_cookie():
    assert "SESSION-SECRET" not in repr(client())


@respx.mock
def test_session_rotation_logged_once_and_only_when_changed(caplog):
    route = respx.get(f"{BASE}/api/market/goods/buy_order")
    c = client()
    route.mock(return_value=httpx.Response(200, json=fx("queue_mixed"), headers={"set-cookie": "session=SESSION-SECRET; Path=/"}))
    with caplog.at_level("INFO", logger="skinpulse.adapters.buff.client"):
        c.buy_queue(1)
        assert not caplog.records                                   # 同一个值：续期，不记
        route.mock(return_value=httpx.Response(200, json=fx("queue_mixed"), headers={"set-cookie": "session=OTHER; Path=/"}))
        c.buy_queue(1)
        c.buy_queue(1)
    assert len(caplog.records) == 1
    assert "OTHER" not in caplog.text and "SESSION-SECRET" not in caplog.text
    assert c.cookie == "SESSION-SECRET"
