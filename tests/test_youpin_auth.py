import json
from decimal import Decimal

import httpx
import pytest
import respx

from skinpulse.adapters.youpin import auth
from skinpulse.adapters.youpin.errors import LoginRequired, RateLimited, YoupinError
from tests.conftest import APP, PC, device_w2_responder, load_fixture


@respx.mock
def test_send_sms_code_posts_pc_shape_and_no_auth_header(youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    send = respx.post(f"{PC}/api/youpin/userAuth/smsLogin/sendSmsCode").mock(
        return_value=httpx.Response(200, json={"code": 0, "msg": "ok", "data": None}))

    sid = auth.send_sms_code(youpin_client, "13800000000")

    assert send.call_count == 1
    req = send.calls[0].request
    assert json.loads(req.content) == {"sessionId": sid, "area": 86, "mobile": "13800000000"}
    assert "authorization" not in req.headers


@respx.mock
def test_sms_sign_in_posts_pc_shape_and_sets_token_from_top_level_Token(youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    sign = respx.post(f"{PC}/api/youpin/userAuth/smsLogin/doLogin").mock(
        return_value=httpx.Response(200, json={"code": 0, "msg": "ok", "data": {"Token": "tok123"}}))

    token = auth.sms_sign_in(youpin_client, "13800000000", "123456", "sid-1")

    assert token == "tok123" and youpin_client.token == "tok123"
    body = json.loads(sign.calls[0].request.content)
    assert body == {
        "sessionId": "sid-1", "area": 86, "mobile": "13800000000",
        "code": "123456", "tenDayFreeSignIn": True, "type": 1,
    }


@respx.mock
def test_sms_sign_in_accepts_lowercase_token_top_level(youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    respx.post(f"{PC}/api/youpin/userAuth/smsLogin/doLogin").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"token": "tok456"}}))

    token = auth.sms_sign_in(youpin_client, "13800000000", "123456", "sid-1")

    assert token == "tok456" and youpin_client.token == "tok456"


@respx.mock
def test_sms_sign_in_accepts_token_nested_under_data(youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    respx.post(f"{PC}/api/youpin/userAuth/smsLogin/doLogin").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"data": {"Token": "tok789"}}}))

    token = auth.sms_sign_in(youpin_client, "13800000000", "123456", "sid-1")

    assert token == "tok789" and youpin_client.token == "tok789"


@respx.mock
def test_sms_sign_in_no_token_raises_clear_error_without_raw_payload(youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    respx.post(f"{PC}/api/youpin/userAuth/smsLogin/doLogin").mock(
        return_value=httpx.Response(200, json={
            "code": 0, "data": {"someOtherField": "MARKER-personal-13800000000-secret"},
        }))

    with pytest.raises(YoupinError) as exc_info:
        auth.sms_sign_in(youpin_client, "13800000000", "123456", "sid-1")

    assert "MARKER-personal-13800000000-secret" not in str(exc_info.value)
    assert youpin_client.token is None


@respx.mock
def test_get_user_info_requires_token_and_parses(youpin_client):
    """getUserInfoForApp (PC host) sits behind YouPin's sk-encrypted channel and returns a
    hex body, never JSON - confirmed by probing it with a real token. get_user_info must hit
    the plain-JSON legacy endpoint on the *App* host instead (still with PC-style headers,
    the combination the real probe found working). Not mocking the PC-host getUserInfoForApp
    route at all doubles as a regression check: if get_user_info ever calls it again, respx's
    strict mocking makes the request itself fail instead of silently passing."""
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    with pytest.raises(LoginRequired):
        youpin_client.get_user_info()
    youpin_client.token = "tok"
    route = respx.get(f"{APP}/api/user/Account/getUserInfo").mock(
        return_value=httpx.Response(200, json={"Code": 0, "Data": {"UserId": 99999999, "NickName": "me"}}))
    info = youpin_client.get_user_info()
    assert info.user_id == 99999999 and info.nickname == "me"
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer tok"
    # 走 App host，但请求头仍然是 PC 渠道的那套（不是 App 渠道的 AppType: 4 / platform: android）。
    assert req.headers["platform"] == "pc"
    assert req.headers["appType"] == "1"


@respx.mock
def test_get_user_info_parses_lowercase_shape(youpin_client):
    """The legacy endpoint's exact field names were never inspected (probe-token only ever
    confirmed code=0, not the payload shape), so both a capitalized and an all-lowercase
    shape must parse - this covers the lowercase half of that tolerance."""
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    youpin_client.token = "tok"
    respx.get(f"{APP}/api/user/Account/getUserInfo").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"userId": 88888888, "nickName": "she"}}))
    info = youpin_client.get_user_info()
    assert info.user_id == 88888888 and info.nickname == "she"


@respx.mock
def test_get_user_info_unrecognized_shape_raises_named_error_without_body(youpin_client):
    """user_id is load-bearing (the rank engine uses it to find the account's own orders in
    a public queue), so a response with no recognizable user-id field must fail loudly and
    name the endpoint - not silently fall back to UserInfo(0, ""). The error must not echo
    the response body, which is the account's own data (phone/nickname/avatar/etc.)."""
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    youpin_client.token = "tok"
    respx.get(f"{APP}/api/user/Account/getUserInfo").mock(
        return_value=httpx.Response(200, json={
            "Code": 0, "Data": {"phoneNumber": "MARKER-personal-13800000000-secret"},
        }))
    with pytest.raises(YoupinError) as exc_info:
        youpin_client.get_user_info()
    msg = str(exc_info.value)
    assert "/api/user/Account/getUserInfo" in msg  # names the endpoint
    assert "MARKER-personal-13800000000-secret" not in msg  # never echoes the response body


@respx.mock
def test_search_my_purchase_orders_paginates(youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    youpin_client.token = "tok"
    page1 = load_fixture("youpin/search_purchase_order_list.json")
    page1["data"] = page1["data"] * 20  # 3 条固定数据 * 20 = 60 条 → 触发翻页（默认 page_size=40）
    page2 = load_fixture("youpin/search_purchase_order_list.json")
    route = respx.post(f"{PC}/api/youpin/bff/trade/purchase/order/searchPurchaseOrderList").mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)])
    orders = youpin_client.search_my_purchase_orders()
    assert len(orders) == 63 and route.call_count == 2  # 60 + 3
    assert orders[0].price == Decimal("358.00") and orders[0].abrade_text == "0.15-0.18"
    assert json.loads(route.calls[1].request.content)["pageIndex"] == 2
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer tok"
    assert req.headers["platform"] == "pc"
    assert req.headers["appType"] == "1"


@respx.mock
def test_search_my_purchase_orders_84101_raises(youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    youpin_client.token = "tok"
    respx.post(f"{PC}/api/youpin/bff/trade/purchase/order/searchPurchaseOrderList").mock(
        return_value=httpx.Response(200, json={"code": 84101, "msg": "登录信息已失效"}))
    with pytest.raises(LoginRequired):
        youpin_client.search_my_purchase_orders()


@respx.mock
def test_search_my_purchase_orders_stops_at_max_pages(youpin_client):
    """服务器每页都满量返回时，翻页必须在 max_pages 处停止，而不是无限循环。"""
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    youpin_client.token = "tok"
    page = load_fixture("youpin/search_purchase_order_list.json")  # 固定 3 条，恒 >= page_size=2
    route = respx.post(f"{PC}/api/youpin/bff/trade/purchase/order/searchPurchaseOrderList").mock(
        return_value=httpx.Response(200, json=page))
    orders = youpin_client.search_my_purchase_orders(page_size=2, max_pages=3)
    assert len(orders) == 9  # 3 条 * 3 页
    assert route.call_count == 3


@respx.mock
def test_get_user_info_http_500_raises_youpin_error_not_rate_limited(youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    youpin_client.token = "tok"
    respx.get(f"{APP}/api/user/Account/getUserInfo").mock(
        return_value=httpx.Response(500, content=b"internal error"))
    with pytest.raises(YoupinError) as exc_info:
        youpin_client.get_user_info()
    assert not isinstance(exc_info.value, RateLimited)


@respx.mock
def test_get_user_info_empty_body_raises_rate_limited(youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    youpin_client.token = "tok"
    respx.get(f"{APP}/api/user/Account/getUserInfo").mock(
        return_value=httpx.Response(200, content=b""))
    with pytest.raises(RateLimited):
        youpin_client.get_user_info()


@respx.mock
def test_get_user_info_badjson_raises_youpin_error(youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    youpin_client.token = "tok"
    respx.get(f"{APP}/api/user/Account/getUserInfo").mock(
        return_value=httpx.Response(200, content=b"not json"))
    with pytest.raises(YoupinError) as exc_info:
        youpin_client.get_user_info()
    assert exc_info.value.code == "badjson"


@respx.mock
def test_get_user_info_85100_triggers_one_uk_refresh_then_succeeds(youpin_client):
    w2 = respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    youpin_client.token = "tok"
    respx.get(f"{APP}/api/user/Account/getUserInfo").mock(
        side_effect=[
            httpx.Response(200, json={"code": 85100, "msg": "当前app版本过低，请前往更新"}),
            httpx.Response(200, json={"Code": 0, "Data": {"UserId": 99999999, "NickName": "me"}}),
        ])
    info = youpin_client.get_user_info()
    assert info.user_id == 99999999 and info.nickname == "me"
    assert w2.call_count == 2  # 初始 + 强制刷新一次


@respx.mock
def test_search_my_purchase_orders_page_fetches_one_page_of_a_status(youpin_client):
    """成交推送 spec §2：「已完成」(40)、「暂停中」(30) 用同一个接口换 status；只取一页，翻页由调用方定。"""
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    youpin_client.token = "tok"
    route = respx.post(f"{PC}/api/youpin/bff/trade/purchase/order/searchPurchaseOrderList").mock(
        return_value=httpx.Response(200, json=load_fixture("youpin/my_orders_status40.json")))
    rows = youpin_client.search_my_purchase_orders_page(40, 3)
    assert route.call_count == 1
    assert json.loads(route.calls[0].request.content) == {"pageIndex": 3, "pageSize": 40, "status": 40}
    assert route.calls[0].request.headers["authorization"] == "Bearer tok"
    assert len(rows) == 5
    first = rows[0]
    assert first.purchase_no == "TEST-DONE-1" and first.commodity_name == "SSG 08 | 渐变强酸 (崭新出厂)"
    assert (first.raw["status"], first.raw["buyQuantity"], first.raw["createTime"]) == (40, 1, "2026.09.29 07:38:14")
    assert first.remaining_qty == 0


@respx.mock
def test_search_my_purchase_orders_page_parses_paused_rows(youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    youpin_client.token = "tok"
    respx.post(f"{PC}/api/youpin/bff/trade/purchase/order/searchPurchaseOrderList").mock(
        return_value=httpx.Response(200, json=load_fixture("youpin/my_orders_status30.json")))
    rows = youpin_client.search_my_purchase_orders_page(30, 1)
    assert {r.raw["statusText"] for r in rows} == {"暂停中"}
    assert all(r.raw["rank"] is None and r.raw["buyQuantity"] == 0 for r in rows)
