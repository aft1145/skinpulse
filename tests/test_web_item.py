"""看板二期：`GET /api/item/{template_id}`（看板 spec §4.2、§5）。

`_client` 构造的 Runtime 里悠悠客户端是 None——接口只要碰一下悠悠就会直接崩，所以这些测试
顺带证明了：**打开详情页不会向悠悠发任何请求**。
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from skinpulse.adapters.steamdt.errors import SteamDTQuotaExceeded
from skinpulse.adapters.steamdt.models import KlineBar
from tests.market_fakes import NOW, FakeMarket, pp, with_item
from tests.test_web import _client


def _get(c, tid=1414):
    r = c.get(f"/api/item/{tid}")
    assert r.status_code == 200, r.text
    return r.json()


def test_item_identity_and_our_orders_come_from_the_overview_rows(tmp_path, settings):
    c, rt = _client(tmp_path, settings)
    with_item(rt)
    rt.market = FakeMarket()

    j = _get(c)
    assert j["template_id"] == 1414
    # 有单时名字跟总览一致（悠悠的 commodity_name，排名最好那张）；SteamDT 的中文名和悠悠的有 59 个
    # 写法不同（空格、全角逗号），只在没单时兜底
    assert (j["title"], j["star"], j["stattrak"], j["tag"]) == ("item B", False, False, None)
    assert j["market_hash_name"] == "AK-47 | Redline (Field-Tested)"
    assert "1414" in j["youpin_url"]
    # 和总览同一套行：同一个 rank 来源，按排名排好
    overview = {o["purchase_no"]: o for o in c.get("/api/overview").json()["orders"]}
    assert [o["purchase_no"] for o in j["orders"]] == ["B", "A", "C"]
    assert j["orders"] == [overview[o["purchase_no"]] for o in j["orders"]]


def test_prices_are_numbers_youpin_first_and_empty_platforms_dropped(tmp_path, settings):
    c, rt = _client(tmp_path, settings)
    with_item(rt)
    rt.market = FakeMarket()

    p = _get(c)["prices"]
    assert [r["platform"] for r in p["rows"]] == ["YOUPIN", "BUFF"]       # WAXPEER 没数据，不列
    youpin = p["rows"][0]
    assert youpin == {"platform": "YOUPIN", "name": "悠悠有品", "sell": 108.0, "sell_count": 5,
                      "bid": 95.0, "bid_count": 2, "updated_at": "2026-09-23T05:00:00Z"}
    assert p["fetched_at"] == "2026-09-23T05:00:00Z" and p["stale"] is False and p["error"] is None


def test_missing_side_is_null_not_zero(tmp_path, settings):
    """价格 0 = 没有数据（CLAUDE.md 坑 3）：没有求购就是 null，件数也是 null，不是 0。
    在售 0 件但有价格的情况也要原样给出，前端才分得清。"""
    c, rt = _client(tmp_path, settings)
    with_item(rt)
    rt.market = FakeMarket(prices=[pp("YOUPIN", 108, None, sell_count=0), pp("STEAM", None, 90)])

    rows = {r["platform"]: r for r in _get(c)["prices"]["rows"]}
    assert rows["YOUPIN"]["bid"] is None and rows["YOUPIN"]["bid_count"] is None
    assert rows["YOUPIN"]["sell"] == 108.0 and rows["YOUPIN"]["sell_count"] == 0
    assert rows["STEAM"]["sell"] is None and rows["STEAM"]["sell_count"] is None


def test_kline_returns_raw_bars_in_steamdt_order_and_a_range_per_window(tmp_path, settings):
    """SteamDT K 线是 [时间, 开, 收, 高, 低]（CLAUDE.md 坑 7）。接口原样按这个顺序给，
    转成 ECharts 的 [开, 收, 低, 高] 是前端的事（charts/kline.ts，有 vitest 守着）。"""
    c, rt = _client(tmp_path, settings)
    with_item(rt)
    t0 = NOW - timedelta(days=3)
    bars = [KlineBar(ts=t0 + timedelta(days=i), open=Decimal("10"), close=Decimal("12"),
                     high=Decimal("13"), low=Decimal("9")) for i in range(3)]
    rt.market = FakeMarket(kline=bars)

    k = _get(c)["kline"]
    assert k["bars"][0] == ["2026-09-20T05:00:00Z", 10.0, 12.0, 13.0, 9.0]
    assert set(k["ranges"]) == {"1m", "3m", "1y"}
    assert k["ranges"]["1m"] == {"lo": 9.0, "hi": 13.0, "clipped": 0, "extreme": None}
    assert k["fetched_at"] == "2026-09-23T05:00:00Z" and k["stale"] is False


def test_kline_signals_are_numbers(tmp_path, settings):
    c, rt = _client(tmp_path, settings)
    with_item(rt)
    rt.market = FakeMarket()

    s = _get(c)["kline"]["signals"]
    assert isinstance(s["last_close"], float) and isinstance(s["ma7"], float)
    assert set(s) >= {"last_close", "ma7", "ma30", "ma_gap_pct", "change_7d_pct", "change_30d_pct",
                      "trend", "trend_threshold_pct"}


def test_one_request_calls_each_market_source_once(tmp_path, settings):
    c, rt = _client(tmp_path, settings)
    with_item(rt)
    rt.market = FakeMarket()

    _get(c)
    assert sorted(x[0] if x[0] == "prices" else f"{x[0]}:{x[2]}" for x in rt.market.calls) == \
        ["item_kline:DAY", "item_kline:WEEK", "prices"]


def test_without_steamdt_key_we_still_show_our_orders_and_request_nothing(tmp_path, settings):
    c, rt = _client(tmp_path, settings)
    with_item(rt)                      # rt.market 默认是 None：没配 key

    j = _get(c)
    assert j["market"] == {"configured": False, "mapped": True}
    assert j["prices"] is None and j["kline"] is None
    assert len(j["orders"]) == 3


def test_unmapped_template_does_not_hit_steamdt(tmp_path, settings):
    c, rt = _client(tmp_path, settings)
    rt.market = FakeMarket()           # 没建 Item 映射

    j = _get(c)
    assert j["market"] == {"configured": True, "mapped": False}
    assert j["prices"] is None and j["kline"] is None
    assert rt.market.calls == []


def test_stale_data_is_returned_with_its_error(tmp_path, settings):
    c, rt = _client(tmp_path, settings)
    with_item(rt)
    rt.market = FakeMarket(stale=True)

    j = _get(c)
    assert j["prices"]["stale"] is True and "4005" in j["prices"]["error"]
    assert j["kline"]["stale"] is True and j["kline"]["bars"]


def test_source_down_with_nothing_cached_is_200_with_the_error(tmp_path, settings):
    c, rt = _client(tmp_path, settings)
    with_item(rt)
    rt.market = FakeMarket(fail=SteamDTQuotaExceeded("4005", "当前接口请求已达到上限"))

    j = _get(c)
    assert j["prices"]["rows"] == [] and "4005" in j["prices"]["error"]
    assert j["kline"]["bars"] == [] and j["kline"]["signals"] is None and "4005" in j["kline"]["error"]
    assert j["kline"]["ranges"] == {"1m": None, "3m": None, "1y": None}


def test_unknown_template_is_a_json_404(tmp_path, settings):
    c, _ = _client(tmp_path, settings)
    r = c.get("/api/item/999999")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


def test_mapped_template_without_orders_still_has_a_page(tmp_path, settings):
    """⌘K 以后可能搜到没挂单的饰品：有映射就给行情，orders 为空。"""
    c, rt = _client(tmp_path, settings)
    with_item(rt, template_id=2020, mhn="AWP | Asiimov (Field-Tested)", cn="AWP | 二西莫夫 (久经沙场)")
    rt.market = FakeMarket()

    j = _get(c, 2020)
    assert j["orders"] == [] and j["title"] == "AWP | 二西莫夫"
    assert j["prices"]["rows"]


def test_item_path_is_served_by_the_frontend_not_jinja(tmp_path, settings):
    """二期起 /item/{id} 由前端路由接管；旧的 Jinja 页面已删。"""
    c, _ = _client(tmp_path, settings)
    r = c.get("/item/1414")
    assert "我在这个饰品上的求购" not in r.text


def test_item_carries_the_seven_local_days_of_rank_7d(tmp_path, settings):
    """前端画 7 天排名时用后端给的日期，不自己按浏览器时间推算（零点前后会差一天）。"""
    c, rt = _client(tmp_path, settings)
    days = _get(c)["days"]
    assert len(days) == 7 and days == sorted(days)


# ---- 周线（「全部」范围，2026-09-27 用户要求）----

def test_weekly_kline_covers_years_with_one_range(tmp_path, settings):
    """日线只有 365 天；周线从 2022-07 起（实测 AK-47 红线久经 171 根）。纵轴范围整段算一份。"""
    c, rt = _client(tmp_path, settings)
    with_item(rt)
    rt.market = FakeMarket()

    w = _get(c)["weekly"]
    assert len(w["bars"]) == 171 and w["bars"][0][0].startswith("2022-07-0")
    assert set(w["range"]) == {"lo", "hi", "clipped", "extreme"}
    assert w["fetched_at"].endswith("Z") and w["stale"] is False and w["error"] is None


def test_weekly_failure_does_not_break_the_daily_chart(tmp_path, settings):
    c, rt = _client(tmp_path, settings)
    with_item(rt)
    rt.market = FakeMarket(weekly_fail=SteamDTQuotaExceeded("4005", "当前接口请求已达到上限"))

    j = _get(c)
    assert j["kline"]["bars"] and j["weekly"]["bars"] == [] and "4005" in j["weekly"]["error"]
    assert j["weekly"]["range"] is None


def test_no_weekly_without_steamdt(tmp_path, settings):
    c, rt = _client(tmp_path, settings)
    with_item(rt)
    assert _get(c)["weekly"] is None
