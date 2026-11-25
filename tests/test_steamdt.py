"""SteamDT 适配器：解析、错误映射、请求形状。

fixtures 全部是 2026-09-23 对真实接口的实测响应（`tests/fixtures/steamdt/`），不是照文档
造出来的——本项目在「照推断的字段名写解析器」上吃过一次大亏。
"""
from __future__ import annotations

from datetime import timezone
from decimal import Decimal

import httpx
import pytest
import respx

from skinpulse.adapters.steamdt.client import (
    BASE_URL,
    PATH_BASE_ITEMS,
    PATH_PRICE_AVG,
    PATH_PRICE_BATCH,
    PATH_PRICE_SINGLE,
    SteamDTClient,
)
from skinpulse.adapters.steamdt.errors import SteamDTAuthError, SteamDTError, SteamDTQuotaExceeded
from skinpulse.adapters.steamdt.models import AveragePrice, BaseItem, PlatformPrice
from tests.conftest import load_fixture


def _client() -> SteamDTClient:
    return SteamDTClient("test-key", http=httpx.Client(timeout=5))


def _envelope(data, *, success=True, code=0, msg=None, code_str=None):
    return {"success": success, "data": data, "errorCode": code, "errorMsg": msg,
            "errorData": None, "errorCodeStr": code_str}


# ---- 解析 ----


def test_prices_parse_real_response():
    fx = load_fixture("steamdt/price_single.json")
    with respx.mock(base_url=BASE_URL) as m:
        m.get(PATH_PRICE_SINGLE).mock(return_value=httpx.Response(200, json=fx))
        prices = _client().get_prices("AK-47 | Redline (Field-Tested)")

    yp = prices.of("YOUPIN")
    assert yp is not None and yp.platform_item_id == "1414"
    assert yp.sell_price is not None and yp.sell_price > 0
    assert yp.update_time is not None and yp.update_time.tzinfo is timezone.utc


def test_zero_price_means_no_data_not_free():
    """实测 /price/single 会把没覆盖到的平台也列出来，值是 `sellPrice: 0, sellCount: 0`。
    那不是「零元在售」，是「这个平台没有数据」。原样存 0 会让看板显示「市场价 0」——一个
    看着合理、实际完全错误的数字，正是本项目已经犯过一次的那类错误。"""
    p = PlatformPrice.from_api({"platform": "WAXPEER", "platformItemId": "", "sellPrice": 0,
                                "sellCount": 0, "biddingPrice": 0, "biddingCount": 0,
                                "updateTime": 1756742400})
    assert p.sell_price is None and p.bidding_price is None
    assert p.has_data is False
    # 数量字段照原样保留 0：0 件在售是个有意义的事实，和价格缺失不是一回事。
    assert p.sell_count == 0


def test_with_data_filters_out_the_empty_platforms():
    fx = load_fixture("steamdt/price_single.json")
    with respx.mock(base_url=BASE_URL) as m:
        m.get(PATH_PRICE_SINGLE).mock(return_value=httpx.Response(200, json=fx))
        prices = _client().get_prices("AK-47 | Redline (Field-Tested)")

    assert len(prices.with_data()) < len(prices.platforms)
    assert all(p.has_data for p in prices.with_data())


def test_sell_and_bidding_are_kept_apart():
    """卖方最低要价和买方最高出价是两个方向的数字，混用会让「该不该加价」判反。"""
    p = PlatformPrice.from_api({"platform": "BUFF", "platformItemId": "1", "sellPrice": 1439.99,
                                "sellCount": 747, "biddingPrice": 1320.0, "biddingCount": 15,
                                "updateTime": 1790094630})
    assert p.sell_price == Decimal("1439.99") and p.bidding_price == Decimal("1320.0")


def test_average_price_parses_real_response():
    fx = load_fixture("steamdt/price_avg.json")
    with respx.mock(base_url=BASE_URL) as m:
        m.get(PATH_PRICE_AVG).mock(return_value=httpx.Response(200, json=fx))
        avg = _client().get_average_price_7d("AK-47 | Redline (Field-Tested)")

    assert isinstance(avg, AveragePrice)
    assert avg.avg_price is not None and avg.avg_price > 0
    assert avg.of("YOUPIN") is not None


def test_base_items_map_youpin_item_id_to_our_template_id():
    """YOUPIN 的 itemId 就是悠悠的 templateId——整个多平台对账的地基。实测核对过
    1414（AK-47 红线久经）和 1022（裹手防水布胶带久经）。"""
    fx = load_fixture("steamdt/base_items.json")
    with respx.mock(base_url=BASE_URL) as m:
        m.get(PATH_BASE_ITEMS).mock(return_value=httpx.Response(200, json=fx))
        items = _client().get_base_items()

    by_tid = {it.youpin_template_id(): it for it in items if it.youpin_template_id()}
    assert by_tid[1414].market_hash_name == "AK-47 | Redline (Field-Tested)"
    assert by_tid[1022].market_hash_name == "★ Hand Wraps | Duct Tape (Field-Tested)"
    # 人工染色 = Stained，不是 Case Hardened（那是表面淬火）。凭中文名猜英文名当场就会出错，
    # 所以映射必须走 ID。
    assert by_tid[108753].market_hash_name == "★ StatTrak™ Kukri Knife | Stained (Field-Tested)"


def test_base_item_without_a_youpin_id_is_not_mappable():
    """有些饰品没在悠悠上架（fixture 里那把猎杀者匕首就是），不能硬凑一个 templateId 出来。"""
    item = BaseItem.from_api({"name": "x", "marketHashName": "X", "platformList": [{"name": "BUFF", "itemId": "1"}]})
    assert item.youpin_template_id() is None


# ---- 错误映射 ----


def test_http_200_with_success_false_is_still_an_error():
    """SteamDT **永远返回 HTTP 200**，成败只看信封里的 success/errorCode。按状态码判断成败
    在这里一定错——本项目在悠悠那边踩过同款（HTTP 429 其实是版本闸门，不是限流）。"""
    with respx.mock(base_url=BASE_URL) as m:
        m.get(PATH_PRICE_SINGLE).mock(return_value=httpx.Response(
            200, json=_envelope(None, success=False, code=9999, msg="boom")))
        with pytest.raises(SteamDTError) as e:
            _client().get_prices("X")
    assert e.value.code == "9999"


def test_invalid_key_maps_to_an_auth_error_despite_the_misleading_message():
    """2007 的文案是「请先申请ApiKey」，听起来像「你没申请过」，实际含义是「这个 key 我不
    认识」——实测把一个刚被重新生成掉的旧 key 发过去拿到的就是这条。照字面读会让人跑去重新
    申请，而正确动作是把新 key 填进配置。"""
    with respx.mock(base_url=BASE_URL) as m:
        m.get(PATH_PRICE_SINGLE).mock(return_value=httpx.Response(
            200, json=_envelope(None, success=False, code=2007, msg="请先申请ApiKey",
                                code_str="STEAM_STOCK_OPEN_USER_ERROR_2007")))
        with pytest.raises(SteamDTAuthError):
            _client().get_prices("X")


def test_missing_key_maps_to_an_auth_error():
    with respx.mock(base_url=BASE_URL) as m:
        m.get(PATH_PRICE_SINGLE).mock(return_value=httpx.Response(
            200, json=_envelope(None, success=False, code=4002, msg="apiKey不能为空")))
        with pytest.raises(SteamDTAuthError):
            SteamDTClient(None, http=httpx.Client(timeout=5)).get_prices("X")


def test_quota_wording_is_recognised_even_without_a_known_code():
    """配额错误码还没实测到。在拿到之前按文案兜底识别，免得把「配额用完」当普通错误去重试
    ——那只会把下一个配额窗口也烧掉。"""
    with respx.mock(base_url=BASE_URL) as m:
        m.get(PATH_PRICE_SINGLE).mock(return_value=httpx.Response(
            200, json=_envelope(None, success=False, code=1234, msg="该接口已达到配额上限")))
        with pytest.raises(SteamDTQuotaExceeded):
            _client().get_prices("X")


def test_non_json_response_never_echoes_the_body_wholesale():
    """网关错误页可能把请求头回显出来，而请求头里带着 API key。只保留状态码和截断摘要。"""
    long_body = "x" * 5000
    with respx.mock(base_url=BASE_URL) as m:
        m.get(PATH_PRICE_SINGLE).mock(return_value=httpx.Response(502, text=long_body))
        with pytest.raises(SteamDTError) as e:
            _client().get_prices("X")
    assert len(str(e.value)) < 300


# ---- 请求形状 ----


def test_batch_sends_all_names_in_one_request():
    """/price/batch 的配额是 1 次/分钟，所以一次必须把名字凑齐，绝不能一张一张地调。"""
    seen = {}

    def responder(request):
        import json as _json
        seen.update(_json.loads(request.content))
        return httpx.Response(200, json=_envelope([{"marketHashName": "A", "dataList": []},
                                                   {"marketHashName": "B", "dataList": []}]))

    with respx.mock(base_url=BASE_URL) as m:
        route = m.post(PATH_PRICE_BATCH).mock(side_effect=responder)
        out = _client().get_prices_batch(["A", "B"])

    assert route.call_count == 1
    assert seen == {"marketHashNames": ["A", "B"]}
    assert [o.market_hash_name for o in out] == ["A", "B"]


def test_batch_with_no_names_makes_no_request():
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as m:
        route = m.post(PATH_PRICE_BATCH)
        assert _client().get_prices_batch([]) == []
        assert route.call_count == 0


def test_api_key_travels_as_a_bearer_header():
    with respx.mock(base_url=BASE_URL) as m:
        route = m.get(PATH_PRICE_SINGLE).mock(return_value=httpx.Response(200, json=_envelope([])))
        _client().get_prices("X")
    assert route.calls[0].request.headers["authorization"] == "Bearer test-key"


def test_batch_refuses_more_than_the_server_maximum_before_spending_a_quota_window():
    """服务端对超限只回一句 errorCode 100002「参数错误」——不说是哪个参数、也不说上限多少，
    而这一次照样算掉那个 1 次/分钟的配额。实测用 200 个名字撞过一次，所以现在发出去之前就拦。"""
    from skinpulse.adapters.steamdt.client import MAX_BATCH_NAMES

    with respx.mock(base_url=BASE_URL, assert_all_called=False) as m:
        route = m.post(PATH_PRICE_BATCH)
        with pytest.raises(ValueError, match=str(MAX_BATCH_NAMES)):
            _client().get_prices_batch([f"item-{i}" for i in range(MAX_BATCH_NAMES + 1)])
        assert route.call_count == 0
