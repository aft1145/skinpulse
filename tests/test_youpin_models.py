from decimal import Decimal

from skinpulse.adapters.youpin.models import MyPurchaseOrder

# ---------------------------------------------------------------------------
# 2026-08-30: MyPurchaseOrder.from_api's field mapping was corrected against a real
# searchPurchaseOrderList response (a live 371-order account) - most of the previously
# guessed key names never matched, and the two that happened to (orderNo via a
# fallback, templateId directly) were the only reason a real sync looked "successful"
# while storing price=0.00 on every row. These tests pin the confirmed mapping:
# purchase_no <- orderNo, price <- unitPrice, qty <- quantity,
# remaining_qty <- quantity - buyQuantity (floored at 0), special_style <- styleSpecial.
# ---------------------------------------------------------------------------


def _row(**overrides):
    row = {
        "isNew": 1,
        "orderNo": "TEST-ORDER-1",
        "templateId": 1414,
        "commodityName": "AK-47 | 红线 (久经沙场)",
        "iconUrl": "https://example.com/icon.png",
        "unitPrice": "24",
        "styleSpecial": None,
        "abradeText": None,
        "fadeText": None,
        "buyQuantity": 0,
        "quantity": 1,
        "maxPurchasePrice": "¥28.1",
        "autoReceived": 1,
        "rank": "22",
        "status": 20,
        "statusText": "求购中",
        "statusTextColor": "#FAA93F",
        "createTime": "2026.08.22 13:31:08",
        "countDownTime": None,
        "lastPriceUpdateTime": None,
        "checkPriceMessage": None,
    }
    row.update(overrides)
    return row


def test_from_api_maps_the_confirmed_real_shape():
    o = MyPurchaseOrder.from_api(_row())
    assert o.purchase_no == "TEST-ORDER-1"  # from orderNo, not the old purchaseNo guess
    assert o.template_id == 1414
    assert o.commodity_name == "AK-47 | 红线 (久经沙场)"
    assert o.price == Decimal("24")  # from unitPrice, not the old purchasePrice guess
    assert o.qty == 1  # from quantity, not the old purchaseNum guess
    assert o.remaining_qty == 1  # quantity(1) - buyQuantity(0)
    assert o.raw == _row()  # raw preserved verbatim


def test_from_api_remaining_qty_is_quantity_minus_buy_quantity():
    o = MyPurchaseOrder.from_api(_row(quantity=3, buyQuantity=1))
    assert o.qty == 3
    assert o.remaining_qty == 2  # 3 - 1, not the whole quantity


def test_from_api_remaining_qty_floors_at_zero():
    # buyQuantity should never exceed quantity in practice, but a fully-filled or
    # inconsistent row must not go negative.
    o = MyPurchaseOrder.from_api(_row(quantity=1, buyQuantity=5))
    assert o.remaining_qty == 0


def test_from_api_special_style_from_style_special_not_the_reversed_guess():
    o = MyPurchaseOrder.from_api(_row(styleSpecial="asiimov"))
    assert o.special_style == "asiimov"


def test_from_api_abrade_and_fade_text_unchanged():
    o = MyPurchaseOrder.from_api(_row(abradeText="0.15-0.18", fadeText="80-90"))
    assert o.abrade_text == "0.15-0.18"
    assert o.fade_text == "80-90"


def test_from_api_template_hash_name_absent_from_real_response_defaults_empty():
    o = MyPurchaseOrder.from_api(_row())
    assert o.template_hash_name == ""


def test_from_api_type_id_absent_from_my_orders_defaults_to_minus_one():
    o = MyPurchaseOrder.from_api(_row())
    assert o.type_id == -1


def test_from_api_missing_unit_price_defaults_to_zero():
    row = _row()
    del row["unitPrice"]
    o = MyPurchaseOrder.from_api(row)
    assert o.price == Decimal("0")


def test_from_api_zero_string_unit_price_parses_to_zero():
    o = MyPurchaseOrder.from_api(_row(unitPrice="0"))
    assert o.price == Decimal("0")


def test_from_api_unparseable_unit_price_defaults_to_zero_without_raising():
    o = MyPurchaseOrder.from_api(_row(unitPrice="not-a-number"))
    assert o.price == Decimal("0")


def test_from_api_orderno_still_wins_over_legacy_purchaseno_guess():
    o = MyPurchaseOrder.from_api(_row(orderNo="REAL", purchaseNo="STALE-GUESS"))
    assert o.purchase_no == "REAL"


def test_from_api_falls_back_to_legacy_purchaseno_if_orderno_absent():
    """Tolerance for a future/undocumented shape change - not the current real shape."""
    row = _row()
    del row["orderNo"]
    row["purchaseNo"] = "FALLBACK"
    o = MyPurchaseOrder.from_api(row)
    assert o.purchase_no == "FALLBACK"
