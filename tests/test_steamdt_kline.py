"""SteamDT 的 K 线与大盘接口。fixtures 是 2026-09-23 的实测响应。"""
from __future__ import annotations

from datetime import timezone
from decimal import Decimal

import httpx
import pytest
import respx

from skinpulse.adapters.steamdt.client import (
    BASE_URL, PATH_BROAD_INDEX, PATH_BROAD_KLINE, PATH_ITEM_KLINE, SteamDTClient,
)
from skinpulse.adapters.steamdt.errors import SteamDTError, SteamDTQuotaExceeded, raise_for_envelope
from skinpulse.adapters.steamdt.models import KlineBar, KlinePeriod, parse_kline
from tests.conftest import load_fixture


def _client():
    return SteamDTClient("k", http=httpx.Client(timeout=5))


# ---- K 线字段顺序 ----


def test_kline_row_order_is_open_close_high_low_not_ohlc():
    """接口返回 `[时间, 开, 收, 高, 低]`。实测 365 根日线按这个顺序读全部满足「高≥开收≥低」，
    按常见的 OHLC 顺序读一根都不满足——凭直觉解析会画出一张看着像真的错图。"""
    bar = KlineBar.from_api(["1790438400", 178.29, 173.0, 179.6, 170.0])
    assert (bar.open, bar.close, bar.high, bar.low) == (
        Decimal("178.29"), Decimal("173.0"), Decimal("179.6"), Decimal("170.0"))
    assert bar.consistent
    assert bar.ts.tzinfo is timezone.utc


def test_real_item_kline_is_fully_consistent_under_the_documented_order():
    rows = load_fixture("steamdt/kline_item_daily.json")["data"]
    bars = parse_kline(rows)
    assert len(bars) == len(rows)
    assert all(b.consistent for b in bars)
    assert [b.ts for b in bars] == sorted(b.ts for b in bars)


def test_parser_refuses_a_series_whose_field_order_changed():
    """如果服务端哪天改成了 OHLC，大部分行会不满足「高≥开收≥低」——宁可报错也不画错图。"""
    rows = load_fixture("steamdt/kline_item_daily.json")["data"]
    reordered = [[r[0], r[1], r[3], r[4], r[2]] for r in rows]     # 改成 [时间,开,高,低,收]
    with pytest.raises(SteamDTError, match="数组顺序"):
        parse_kline(reordered)


def test_a_few_dirty_rows_are_dropped_not_fatal():
    rows = load_fixture("steamdt/kline_item_daily.json")["data"]
    dirty = list(rows) + [["1790500000", 100, 100, 50, 200]]      # 高 < 低，一根脏数据
    assert len(parse_kline(dirty)) == len(rows)


def test_rows_with_missing_fields_are_skipped():
    assert parse_kline([["1790438400", 1, 1], None, ["x", 1, 1, 1, 1]]) == []


# ---- 请求形状 ----


def test_item_kline_posts_name_and_period_and_omits_platform_for_all():
    seen = []

    def responder(request):
        import json
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=load_fixture("steamdt/kline_item_daily.json"))

    with respx.mock(base_url=BASE_URL) as m:
        m.post(PATH_ITEM_KLINE).mock(side_effect=responder)
        _client().get_item_kline("AK-47 | Redline (Field-Tested)")
        _client().get_item_kline("AK-47 | Redline (Field-Tested)", KlinePeriod.WEEK, platform="YOUPIN")

    assert seen[0] == {"marketHashName": "AK-47 | Redline (Field-Tested)", "type": 2}
    assert seen[1] == {"marketHashName": "AK-47 | Redline (Field-Tested)", "type": 3, "platform": "YOUPIN"}


def test_item_kline_rejects_a_platform_the_endpoint_does_not_support():
    with pytest.raises(ValueError, match="只支持"):
        _client().get_item_kline("X", platform="DMARKET")


# ---- 大盘 ----


def test_broad_index_change_is_already_a_percentage():
    """diffYesterdayRatio 已经是百分数：实测涨 3.55、指数 795.52，接口给的是 0.4482（即 0.448%），
    再乘 100 就会显示成「涨 44.8%」。"""
    with respx.mock(base_url=BASE_URL) as m:
        m.get(PATH_BROAD_INDEX).mock(return_value=httpx.Response(200, json=load_fixture("steamdt/broad_index.json")))
        bi = _client().get_broad_index()

    assert bi.value > 0
    assert bi.diff is not None and bi.diff_pct is not None
    implied_pct = bi.diff / (bi.value - bi.diff) * 100
    assert abs(implied_pct - bi.diff_pct) < Decimal("0.01")
    assert len(bi.history) > 0


def test_broad_index_without_a_value_is_an_error_not_a_zero():
    """大盘指数是这个结构里唯一不可能为空的字段；守卫不覆盖它，看板就会画出一个 0。"""
    with respx.mock(base_url=BASE_URL) as m:
        m.get(PATH_BROAD_INDEX).mock(return_value=httpx.Response(200, json={
            "success": True, "data": {"broadMarketIndex": 0, "historyMarketIndexList": []},
            "errorCode": 0, "errorMsg": None, "errorData": None, "errorCodeStr": None}))
        with pytest.raises(SteamDTError):
            _client().get_broad_index()


def test_broad_kline_ends_where_the_latest_index_is():
    """大盘 K 线和最新指数是同一条序列：实测最后一根收盘价就是最新指数（都是 795.52）。
    一年前是 1560.73——不是两个数据源对不上，是大盘真的跌了一半。"""
    kline = parse_kline(load_fixture("steamdt/kline_broad_daily.json")["data"])
    latest = load_fixture("steamdt/broad_index.json")["data"]["broadMarketIndex"]
    assert kline[-1].close == Decimal(str(latest))


def test_quota_error_4005_is_recognised():
    """实测文案是「当前接口请求已达到上限」——用的词是「上限」，原来的关键词兜底一个都没覆盖到。"""
    with pytest.raises(SteamDTQuotaExceeded):
        raise_for_envelope({"success": False, "data": None, "errorCode": 4005,
                            "errorMsg": "当前接口请求已达到上限。详细规则请参阅接口文档。",
                            "errorData": None, "errorCodeStr": "STEAM_STOCK_OPEN_APP_ERROR_4005"})


def test_broad_kline_posts_the_period():
    seen = []

    def responder(request):
        import json
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=load_fixture("steamdt/kline_broad_daily.json"))

    with respx.mock(base_url=BASE_URL) as m:
        m.post(PATH_BROAD_KLINE).mock(side_effect=responder)
        bars = _client().get_broad_kline(KlinePeriod.DAY)

    assert seen == [{"type": 2}] and bars
