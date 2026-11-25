"""大盘：`GET /api/market`。饰品详情见 test_web_item.py。

`_client` 构造的 Runtime 里 `client`（悠悠客户端）是 None——页面只要碰一下悠悠就会直接崩，
所以这些测试顺带证明了：**打开页面不会向悠悠发任何请求**。
"""
from __future__ import annotations

from skinpulse.adapters.steamdt.errors import SteamDTQuotaExceeded
from tests.market_fakes import FakeMarket
from tests.test_web import _client


def test_overview_carries_the_template_id_for_item_links(tmp_path, settings):
    """新总览（前端）按 template_id 链到 /item/{id}。"""
    c, _ = _client(tmp_path, settings)
    assert {o["template_id"] for o in c.get("/api/overview").json()["orders"]} == {1414}


# ---- 大盘：GET /api/market（看板 spec §4.4、§5）----


def _market(c):
    r = c.get("/api/market")
    assert r.status_code == 200, r.text
    return r.json()


def test_index_is_numbers_with_hourly_history(tmp_path, settings):
    c, rt = _client(tmp_path, settings)
    rt.market = FakeMarket()

    i = _market(c)["index"]
    assert i["value"] == 795.52 and i["diff"] == 3.55
    assert i["updated_at"].endswith("Z") and i["fetched_at"].endswith("Z")
    assert i["history"][0] == ["2026-09-22T05:00:00Z", 793.01]
    assert i["stale"] is False and i["error"] is None


def test_change_is_the_api_percentage_not_scaled_again(tmp_path, settings):
    """diffYesterdayRatio 已经是百分数（0.4482 表示 0.4482%），再乘 100 就会显示成「涨 44.8%」。"""
    c, rt = _client(tmp_path, settings)
    rt.market = FakeMarket()

    assert _market(c)["index"]["diff_pct"] == 0.4482


def test_year_kline_has_bars_ranges_signals_and_year_change(tmp_path, settings):
    c, rt = _client(tmp_path, settings)
    rt.market = FakeMarket()

    k = _market(c)["kline"]
    assert len(k["bars"]) == 40 and len(k["bars"][0]) == 5          # fixture 是 40 根
    assert set(k["ranges"]) == {"1m", "3m", "1y"} and k["ranges"]["1y"]["hi"] >= k["ranges"]["1y"]["lo"]
    first, last = k["bars"][0][2], k["bars"][-1][2]
    assert k["year_from"] == first
    assert k["year_change_pct"] == round((last / first - 1) * 100, 2)
    assert isinstance(k["signals"]["ma7"], float)


def test_one_request_calls_each_broad_source_once(tmp_path, settings):
    """大盘接口配额很紧（4005）：一次页面请求每个数据源只取一次。"""
    c, rt = _client(tmp_path, settings)
    rt.market = FakeMarket()

    _market(c)
    assert sorted(":".join(x) for x in rt.market.calls) == ["broad_index", "broad_kline:DAY", "broad_kline:WEEK"]


def test_without_steamdt_key_nothing_is_requested(tmp_path, settings):
    c, _ = _client(tmp_path, settings)
    assert _market(c) == {"configured": False, "index": None, "kline": None, "weekly": None}


def test_stale_and_failed_sources_are_200_with_the_error(tmp_path, settings):
    c, rt = _client(tmp_path, settings)
    rt.market = FakeMarket(stale=True)
    j = _market(c)
    assert j["index"]["stale"] is True and j["kline"]["stale"] is True

    rt.market = FakeMarket(fail=SteamDTQuotaExceeded("4005", "当前接口请求已达到上限"))
    j = _market(c)
    assert j["index"]["value"] is None and j["index"]["history"] == [] and "4005" in j["index"]["error"]
    assert j["kline"]["bars"] == [] and "4005" in j["kline"]["error"]


def test_market_path_is_served_by_the_frontend(tmp_path, settings):
    c, _ = _client(tmp_path, settings)
    assert "steamdt-key" not in c.get("/market").text


def test_weekly_broad_kline_goes_back_years(tmp_path, settings):
    """大盘周线实测从 2023-07 起（170 根）；和日线一起在打开页面时取，切到「全部」不再请求。"""
    c, rt = _client(tmp_path, settings)
    rt.market = FakeMarket()

    w = _market(c)["weekly"]
    assert len(w["bars"]) == 170 and w["bars"][0][0].startswith("2023-07-0")
    assert w["range"]["lo"] <= w["range"]["hi"]
