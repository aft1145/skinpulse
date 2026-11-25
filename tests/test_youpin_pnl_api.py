"""盈亏统计用的悠悠查询接口（盈亏 spec §2）：请求体、解析、对手信息不外泄。fixture 是 2026-11-24 的真实响应脱敏后截的几行。"""
import json
from datetime import timezone
from decimal import Decimal

import httpx
import pytest
import respx

from skinpulse.adapters.youpin.errors import LoginRequired
from tests.conftest import APP, PC, device_w2_responder, load_fixture


def _route(path: str, fixture: str):
    return respx.post(f"{PC}{path}").mock(return_value=httpx.Response(200, json=load_fixture(f"youpin/{fixture}")))


@pytest.fixture
def client(youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    youpin_client.token = "tok"
    return youpin_client


@respx.mock
def test_sell_list_request_and_parse(client):
    route = _route("/api/youpin/bff/trade/sale/v1/sell/list", "trade_sell_list.json")
    rows = client.search_trade_orders_page("sell", 2)
    sent = json.loads(route.calls.last.request.content)
    assert (sent["orderStatus"], sent["pageIndex"], sent["pageSize"]) == (340, 2, 20)
    assert route.calls.last.request.headers["authorization"] == "Bearer tok"
    first = rows[0]
    assert (first.side, first.order_status, first.sub_type, first.qty) == ("sell", 340, 10, 1)
    assert first.amount == Decimal("142") and first.template_id == 110880
    assert first.hash_name.startswith("Glock-18") and "忍瞳" in first.name
    assert first.created_at.tzinfo is timezone.utc and first.created_at < first.finished_at
    multi = [r for r in rows if r.qty > 1]
    assert multi and multi[0].amount == Decimal("1.3")   # 多件单的 totalAmount 是全部件数的总价（分）


@respx.mock
def test_buy_list_parse_and_no_counterparty_fields(client):
    _route("/api/youpin/bff/trade/sale/v1/buy/list", "trade_buy_list.json")
    rows = client.search_trade_orders_page("buy", 1)
    assert len(rows) == 4 and all(r.side == "buy" and r.sub_type == 5 for r in rows)
    assert rows[0].created_at > rows[-1].created_at          # 按下单时间倒序
    fields = set(vars(rows[0]))
    assert not any(k in f for f in fields for k in ("user", "steam", "offer", "raw"))


def test_trade_side_is_validated(youpin_client):
    with pytest.raises(ValueError):
        youpin_client.search_trade_orders_page("lease", 1)


@respx.mock
def test_order_detail_revenue_and_purchase_no(client):
    route = _route("/api/youpin/bff/trade/v1/order/query/detail", "trade_order_detail_sell.json")
    d = client.get_trade_order_detail("O1", 42)
    assert json.loads(route.calls.last.request.content) == {"orderNo": "O1", "userId": 42}
    assert d.revenue == Decimal("140.58") and d.purchase_no is None
    respx.post(f"{PC}/api/youpin/bff/trade/v1/order/query/detail").mock(
        return_value=httpx.Response(200, json=load_fixture("youpin/trade_order_detail_buy.json")))
    b = client.get_trade_order_detail("O2", 42)
    assert b.revenue is None and b.purchase_no


@respx.mock
def test_inventory_trend(client):
    route = _route("/api/youpin/commodity/user/inventory/price/trend", "inventory_trend.json")
    inv = client.get_inventory_trend()
    assert json.loads(route.calls.last.request.content)["pageSize"] == 1000
    assert inv.pnl_total == Decimal("-2184.23") and len(inv.items) == 5
    with_buy = [i for i in inv.items if i.buy_price is not None]
    assert len(with_buy) == 3 and with_buy[0].market_price == Decimal("309.4")
    assert [i.buy_price for i in inv.items][-2:] == [None, None]   # 空串 = 不知道，不是 0


@respx.mock
def test_lease_out_page(client):
    _route("/api/youpin/bff/trade/v1/order/lease/out/list", "lease_out_list.json")
    page = client.search_lease_out_page(1)
    assert page.has_next is True and len(page.rows) == 2
    assert page.rows[0].hash_name == "StatTrak™ Glock-18 | Shinobu (Minimal Wear)"
    assert page.rows[0].rent_per_day == Decimal("0.07")


@respx.mock
def test_login_lost_maps_to_login_required(client):
    respx.post(f"{PC}/api/youpin/bff/trade/sale/v1/buy/list").mock(
        return_value=httpx.Response(200, json={"code": 84101, "msg": "登录信息已失效"}))
    with pytest.raises(LoginRequired):
        client.search_trade_orders_page("buy", 1)


def test_money_zero_and_bad_timestamps_are_missing():
    from skinpulse.adapters.youpin.models import InventoryItem, TradeOrder

    assert InventoryItem.from_api({"marketPrice": "0", "assetBuyPrice": "0"}).market_price is None
    t = TradeOrder.from_api({"orderNo": "1", "createOrderTime": "bad", "productDetail": {}}, "buy")
    assert t.created_at is None and t.amount is None and t.qty == 1
