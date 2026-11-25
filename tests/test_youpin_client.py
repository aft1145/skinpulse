import json
from decimal import Decimal

import httpx
import pytest
import respx

from skinpulse.adapters.youpin.errors import (
    AccountUnderRecovery,
    LoginProhibited,
    RateLimited,
    RiskControl,
    SliderCaptchaRequired,
    SmsCodeAbsent,
    SmsCodeExpired,
    SteamNotBound,
    UkExpired,
    UpwardSmsRequired,
    VersionOrUkRejected,
    YoupinError,
    raise_for_code,
)
from tests.conftest import APP, PC, device_w2_responder, load_fixture


@respx.mock
def test_ensure_uk_caches_and_refreshes(youpin_client):
    route = respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    assert youpin_client.ensure_uk() == "U" * 65
    assert youpin_client.ensure_uk() == "U" * 65
    assert route.call_count == 1
    youpin_client.tick(31)
    youpin_client.ensure_uk()
    assert route.call_count == 2


@respx.mock
def test_get_template_purchase_orders_parses_and_sends_headers(youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    route = respx.post(f"{PC}/api/youpin/bff/trade/purchase/order/getTemplatePurchaseOrderListPC").mock(
        return_value=httpx.Response(200, json=load_fixture("youpin/template_purchase_orders_ak_redline.json")))
    orders = youpin_client.get_template_purchase_orders(1414)
    assert len(orders) == 9
    first = orders[0]
    assert first.price == Decimal("372.00") and first.abrade_text == "0.15-0.16" and first.type_id == -1
    mine = [o for o in orders if o.user_id == 99999999][0]
    assert mine.purchase_no == "TEST-MINE-1"
    req = route.calls[0].request
    assert req.headers["uk"] == "U" * 65
    assert req.headers["platform"] == "pc" and req.headers["AppVersion"] == "5.26.0"
    assert json.loads(req.content) == {"templateId": "1414", "pageIndex": 1, "pageSize": 20}


@respx.mock
def test_85100_triggers_one_uk_refresh_then_raises(youpin_client):
    w2 = respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    respx.post(f"{PC}/api/youpin/bff/trade/purchase/order/getTemplatePurchaseOrderListPC").mock(
        return_value=httpx.Response(200, json={"code": 85100, "msg": "当前app版本过低，请前往更新"}))
    with pytest.raises(VersionOrUkRejected):
        youpin_client.get_template_purchase_orders(1414)
    assert w2.call_count == 2  # 初始 + 强制刷新一次


@respx.mock
def test_84104_raises_risk_control(youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    respx.post(f"{PC}/api/youpin/bff/trade/purchase/order/getTemplatePurchaseOrderListPC").mock(
        return_value=httpx.Response(200, json={"code": 84104, "msg": "风控"}))
    with pytest.raises(RiskControl):
        youpin_client.get_template_purchase_orders(1414)


@respx.mock
def test_1110205_raises_slider_captcha_required(youpin_client):
    """The code that blocks SMS login (see errors.py); registered here too since
    raise_for_code's dispatch is shared by every endpoint, not just the login ones."""
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    respx.post(f"{PC}/api/youpin/bff/trade/purchase/order/getTemplatePurchaseOrderListPC").mock(
        return_value=httpx.Response(200, json={"code": 1110205, "msg": ""}))
    with pytest.raises(SliderCaptchaRequired) as exc_info:
        youpin_client.get_template_purchase_orders(1414)
    assert "set-token" in str(exc_info.value)


# ---- newly-registered SMS-login error codes: each must be identifiable from its exception
# type *and* produce a message that names what it means, even when the server sends no msg
# of its own (a bare numeric code is what this whole registration exists to avoid). ----


@pytest.mark.parametrize("code,exc_type,keyword", [
    ("1110201", SmsCodeAbsent, "ABSENT"),
    ("1110202", SmsCodeExpired, "EXPIRED"),
    ("1110205", SliderCaptchaRequired, "SLIDER"),
    ("1110206", UpwardSmsRequired, "UPWARDSMS"),
    ("1110207", AccountUnderRecovery, "RECOVERY"),
    ("1110208", LoginProhibited, "PROHIBITLOGIN"),
    ("2005", SteamNotBound, "unBoundSteam"),
])
def test_new_sms_login_codes_map_to_named_exceptions_with_fallback_message(code, exc_type, keyword):
    with pytest.raises(exc_type) as exc_info:
        raise_for_code(code, "")  # empty msg: must still say what it means, not just the bare code
    assert keyword in str(exc_info.value)


def test_new_code_uses_server_msg_when_present_instead_of_the_fallback():
    with pytest.raises(SliderCaptchaRequired) as exc_info:
        raise_for_code("1110205", "服务器给的具体提示")
    assert "服务器给的具体提示" in str(exc_info.value)


@respx.mock
def test_http_405_raises_uk_expired_and_429_rate_limited(youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    r = respx.post(f"{PC}/api/homepage/pc/goods/market/queryOnSaleCommodityList")
    r.mock(return_value=httpx.Response(405))
    with pytest.raises(UkExpired):
        youpin_client.get_on_sale(1414)
    r.mock(return_value=httpx.Response(429))
    with pytest.raises(RateLimited):
        youpin_client.get_on_sale(1414)
    r.mock(return_value=httpx.Response(200, content=b""))
    with pytest.raises(RateLimited):
        youpin_client.get_on_sale(1414)


@respx.mock
def test_429_with_body_code_85100_raises_version_or_uk_rejected_not_rate_limited(youpin_client):
    """诊断性 bug fix：版本网关拒绝有时候用 HTTP 429 返回（body code 85100），旧代码看到
    429 就直接判 RateLimited，从不看 body，把"版本太旧"误诊成"限流"去查，白费时间。
    现在 429 也要先看 body 的业务 code，有明确 code 时交给统一的 code -> 异常映射决定。"""
    w2 = respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    respx.post(f"{PC}/api/homepage/pc/goods/market/queryOnSaleCommodityList").mock(
        return_value=httpx.Response(429, json={"code": 85100, "msg": "当前app版本过低，请前往更新"}))
    with pytest.raises(VersionOrUkRejected):
        youpin_client.get_on_sale(1414)
    assert w2.call_count == 2  # 429/85100 和 200/85100 走同一套强制刷新重试


@respx.mock
def test_429_with_unparsable_body_still_raises_rate_limited(youpin_client):
    """429 body 有内容但不是 JSON（比如网关自己吐的纯文本）时，不能崩，也不能被当成
    带 code 的业务响应处理——退回到 RateLimited，和真正的空 body 429 一样。"""
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    respx.post(f"{PC}/api/homepage/pc/goods/market/queryOnSaleCommodityList").mock(
        return_value=httpx.Response(429, content=b"Too Many Requests"))
    with pytest.raises(RateLimited):
        youpin_client.get_on_sale(1414)


@respx.mock
def test_get_on_sale_parses_homepage_shape(youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    respx.post(f"{PC}/api/homepage/pc/goods/market/queryOnSaleCommodityList").mock(
        return_value=httpx.Response(200, json=load_fixture("youpin/on_sale_list.json")))
    items = youpin_client.get_on_sale(1414)
    assert [i.price for i in items] == [Decimal("355.5"), Decimal("356")]
    assert items[1].have_sticker is True


@respx.mock
def test_http_500_raises_youpin_error_not_rate_limited(youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    respx.post(f"{PC}/api/homepage/pc/goods/market/queryOnSaleCommodityList").mock(
        return_value=httpx.Response(500, content=b"internal error"))
    with pytest.raises(YoupinError) as exc_info:
        youpin_client.get_on_sale(1414)
    assert not isinstance(exc_info.value, RateLimited)
    assert exc_info.value.code == "500"


@respx.mock
def test_http_200_empty_body_raises_rate_limited(youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    respx.post(f"{PC}/api/homepage/pc/goods/market/queryOnSaleCommodityList").mock(
        return_value=httpx.Response(200, content=b""))
    with pytest.raises(RateLimited):
        youpin_client.get_on_sale(1414)


@respx.mock
def test_ensure_uk_http_500_raises_youpin_error_not_rate_limited(youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(return_value=httpx.Response(500, content=b"internal error"))
    with pytest.raises(YoupinError) as exc_info:
        youpin_client.ensure_uk()
    assert not isinstance(exc_info.value, RateLimited)
    assert exc_info.value.code == "500"


@respx.mock
def test_ensure_uk_empty_body_raises_rate_limited(youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(return_value=httpx.Response(200, content=b""))
    with pytest.raises(RateLimited):
        youpin_client.ensure_uk()


@respx.mock
def test_get_template_detail_parses_homepage_shape(youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    respx.post(f"{PC}/api/homepage/pc/goods/market/queryTemplateDetail").mock(
        return_value=httpx.Response(200, json={
            "Code": 0,
            "Msg": "success",
            "Data": {
                "templateInfo": {
                    "id": 1414,
                    "commodityName": "AK-47 | 红线 (久经沙场)",
                    "commodityHashName": "AK-47 | Redline (Field-Tested)",
                    "steamPrice": "242.78",
                }
            },
        }))
    detail = youpin_client.get_template_detail(1414)
    assert detail.template_id == 1414
    assert detail.commodity_name == "AK-47 | 红线 (久经沙场)"
    assert detail.hash_name == "AK-47 | Redline (Field-Tested)"
    assert detail.steam_price == Decimal("242.78")


# ---- badjson diagnostics: an unparseable response must describe itself (status, content-type,
# sk header presence, body length, a capped excerpt) instead of just re-raising str(ValueError) -
# and it must never leak anything from the *request* side (headers, token, uk). ----


@respx.mock
def test_badjson_message_reports_status_content_type_length_and_utf8_excerpt(youpin_client):
    html = b"<html><body>Just a moment... (cloudflare challenge)</body></html>"
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    respx.post(f"{PC}/api/homepage/pc/goods/market/queryOnSaleCommodityList").mock(
        return_value=httpx.Response(200, content=html, headers={"content-type": "text/html; charset=utf-8"}))
    with pytest.raises(YoupinError) as exc_info:
        youpin_client.get_on_sale(1414)
    assert exc_info.value.code == "badjson"
    msg = str(exc_info.value)
    assert "200" in msg
    assert "text/html" in msg
    assert str(len(html)) in msg
    assert "Just a moment" in msg  # readable UTF-8 excerpt made it through
    assert "truncat" in msg.lower()  # explicitly flagged as a bounded/short excerpt
    assert "sk-response-header=absent" in msg
    # must never leak anything from the *request* side (this call used a real uk under the hood)
    assert "U" * 65 not in msg
    assert "bearer" not in msg.lower()
    assert "authorization" not in msg.lower()


@respx.mock
def test_badjson_message_reports_hex_excerpt_for_binary_body(youpin_client):
    binary = bytes(range(256)) * 2  # not valid utf-8; long enough to exercise the cap
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    respx.post(f"{PC}/api/homepage/pc/goods/market/queryOnSaleCommodityList").mock(
        return_value=httpx.Response(200, content=binary,
                                    headers={"content-type": "application/octet-stream"}))
    with pytest.raises(YoupinError) as exc_info:
        youpin_client.get_on_sale(1414)
    assert exc_info.value.code == "badjson"
    msg = str(exc_info.value)
    assert "200" in msg
    assert "application/octet-stream" in msg
    assert str(len(binary)) in msg
    assert "truncat" in msg.lower()
    assert "0001020304" in msg  # hex prefix of the binary body's first bytes
    assert "U" * 65 not in msg
    assert "bearer" not in msg.lower()
    assert "authorization" not in msg.lower()


@respx.mock
def test_badjson_message_reports_sk_response_header_present(youpin_client):
    """The `sk` response header is YouPin's own signal that the payload takes an encrypted/
    signed shape (see the web client's response interceptor) - this is the whole reason
    getUserInfoForApp's response might not be plain JSON. Its *presence* must be reported;
    its *value* must not leak."""
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    respx.post(f"{PC}/api/homepage/pc/goods/market/queryOnSaleCommodityList").mock(
        return_value=httpx.Response(200, content=b"\xff\xfe\x00\x01binary-ish",
                                     headers={"content-type": "application/octet-stream",
                                             "sk": "should-not-appear-in-message"}))
    with pytest.raises(YoupinError) as exc_info:
        youpin_client.get_on_sale(1414)
    msg = str(exc_info.value)
    assert "sk-response-header=present" in msg
    assert "should-not-appear-in-message" not in msg


@respx.mock
def test_badjson_message_names_other_nonstandard_response_headers(youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    respx.post(f"{PC}/api/homepage/pc/goods/market/queryOnSaleCommodityList").mock(
        return_value=httpx.Response(200, content=b"not json",
                                     headers={"content-type": "text/plain", "x-weird-signature": "zzz"}))
    with pytest.raises(YoupinError) as exc_info:
        youpin_client.get_on_sale(1414)
    msg = str(exc_info.value)
    assert "x-weird-signature" in msg
    assert "zzz" not in msg  # header *name* only, never its value
