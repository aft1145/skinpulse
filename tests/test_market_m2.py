"""M2 的分析与缓存：信号计算、MarketData 缓存与过期兜底。

图表在二期改由前端 ECharts 画（ADR-0013）：纵轴范围与截断见 test_kline_range.py，
涨红跌绿、K 线字段顺序见 frontend/src/charts/kline.test.ts。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from skinpulse.adapters.steamdt.errors import SteamDTError, SteamDTQuotaExceeded
from skinpulse.adapters.steamdt.models import KlineBar, parse_kline
from skinpulse.analysis import signals as sig
from skinpulse.services.market_data import MarketData, TTL_S
from tests.conftest import load_fixture

T0 = datetime(2026, 1, 1, 16, 0, tzinfo=timezone.utc)   # = 北京时间 01-02 00:00


def _bars(closes):
    return [KlineBar(ts=T0 + timedelta(days=i), open=Decimal(c), close=Decimal(c),
                     high=Decimal(c), low=Decimal(c)) for i, c in enumerate(closes)]


# ---- 信号 ----


def test_changes_compare_against_n_bars_ago():
    s = sig.compute(_bars([100] * 23 + [110] * 7 + [120]))     # 31 根
    assert s.change_7d_pct == Decimal("120") / Decimal("110") * 100 - 100
    assert s.change_30d_pct == Decimal(20)


def test_not_enough_history_gives_no_number_rather_than_a_shorter_window():
    """数据不足 30 天就不给「30 日涨跌」，不拿更短的区间凑一个出来冒充。"""
    s = sig.compute(_bars([100] * 10))
    assert s.change_30d_pct is None and s.ma30 is None and s.trend is None
    assert s.change_7d_pct == 0


def test_trend_needs_ma7_to_clear_ma30_by_the_threshold():
    """阈值 2%：饰品日线噪声大，1% 的来回天天都有，标成趋势只会误导。"""
    flat = sig.compute(_bars([100] * 23 + [101] * 7))           # MA7 比 MA30 高不到 2%
    up = sig.compute(_bars([100] * 23 + [110] * 7))
    down = sig.compute(_bars([100] * 23 + [90] * 7))
    assert flat.trend == "横盘" and up.trend == "上升" and down.trend == "下降"


def test_real_series_computes():
    s = sig.compute(parse_kline(load_fixture("steamdt/kline_item_daily.json")["data"]))
    assert s is not None and s.ma7 is not None and s.ma30 is not None


def test_empty_series_is_none():
    assert sig.compute([]) is None


# ---- MarketData 缓存 ----


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class _FakeClient:
    def __init__(self):
        self.calls = 0
        self.fail = None

    def get_broad_index(self):
        self.calls += 1
        if self.fail:
            raise self.fail
        return f"v{self.calls}"


def test_cached_value_is_reused_within_its_ttl():
    clock, client = _Clock(), _FakeClient()
    md = MarketData(client, clock=clock)
    assert md.broad_index().value == "v1"
    clock.t += TTL_S["broad_index"] - 1
    assert md.broad_index().value == "v1" and client.calls == 1
    clock.t += 2
    assert md.broad_index().value == "v2" and client.calls == 2


def test_quota_error_serves_the_old_value_marked_stale():
    """大盘接口实测几分钟三次就 4005。有旧数据就给旧数据并标明过期——带时间戳的旧数字比整页报错有用。"""
    clock, client = _Clock(), _FakeClient()
    md = MarketData(client, clock=clock)
    first = md.broad_index()
    clock.t += TTL_S["broad_index"] + 1
    client.fail = SteamDTQuotaExceeded("4005", "当前接口请求已达到上限")

    got = md.broad_index()

    assert got.value == "v1" and got.stale is True
    assert got.fetched_at == first.fetched_at          # 显示的是真实的获取时间，不是此刻
    assert "4005" in got.error


def test_error_with_nothing_cached_is_raised():
    client = _FakeClient()
    client.fail = SteamDTError("500", "boom")
    with pytest.raises(SteamDTError):
        MarketData(client, clock=_Clock()).broad_index()


def test_quota_error_pauses_retries_for_that_key():
    """撞上 4005 以后几分钟内再去请求只会继续 4005、还可能拖长限流。所以这个键冷却 5 分钟：
    有旧数据给旧数据（标过期），没有就直接报上次的错，都不再打 SteamDT。"""
    from skinpulse.services.market_data import QUOTA_COOLDOWN_S

    clock, client = _Clock(), _FakeClient()
    md = MarketData(client, clock=clock)
    client.fail = SteamDTQuotaExceeded("4005", "当前接口请求已达到上限")
    with pytest.raises(SteamDTQuotaExceeded):
        md.broad_index()
    clock.t += QUOTA_COOLDOWN_S - 1
    with pytest.raises(SteamDTQuotaExceeded):
        md.broad_index()
    assert client.calls == 1                          # 冷却期里没有再请求

    client.fail = None
    clock.t += 2
    assert md.broad_index().value == "v2" and client.calls == 2


def test_quota_cooldown_with_an_old_value_keeps_serving_it_stale():
    clock, client = _Clock(), _FakeClient()
    md = MarketData(client, clock=clock)
    md.broad_index()
    clock.t += TTL_S["broad_index"] + 1
    client.fail = SteamDTQuotaExceeded("4005", "当前接口请求已达到上限")
    md.broad_index()
    clock.t += 60
    got = md.broad_index()
    assert got.value == "v1" and got.stale is True and "4005" in got.error
    assert client.calls == 2


def test_other_errors_do_not_start_a_cooldown():
    clock, client = _Clock(), _FakeClient()
    md = MarketData(client, clock=clock)
    client.fail = SteamDTError("500", "boom")
    for _ in range(2):
        with pytest.raises(SteamDTError):
            md.broad_index()
    assert client.calls == 2
