"""看板行情接口测试共用的替身：假的 MarketData（记下每次调用）和 Item 映射。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from skinpulse.adapters.steamdt.models import BroadIndex, ItemPrices, KlinePeriod, PlatformPrice, parse_kline
from skinpulse.db.models import Item
from skinpulse.services.market_data import Fetched
from tests.conftest import load_fixture

NOW = datetime(2026, 9, 23, 5, 0, tzinfo=timezone.utc)


def pp(platform, sell=None, bid=None, *, sell_count=5, bid_count=2):
    return PlatformPrice(platform=platform, platform_item_id="1",
                         sell_price=None if sell is None else Decimal(str(sell)), sell_count=sell_count,
                         bidding_price=None if bid is None else Decimal(str(bid)), bidding_count=bid_count,
                         update_time=NOW)


DEFAULT_PRICES = [pp("BUFF", 110, 100), pp("YOUPIN", 108, 95), pp("WAXPEER")]


class FakeMarket:
    def __init__(self, *, stale=False, fail=None, prices=None, kline=None, weekly_fail=None):
        self.stale, self.fail, self.weekly_fail = stale, fail, weekly_fail
        self._prices = DEFAULT_PRICES if prices is None else prices
        self._kline = kline
        self.calls = []

    def _wrap(self, v):
        if self.fail:
            raise self.fail
        return Fetched(v, NOW, stale=self.stale, error="steamdt 4005: 上限" if self.stale else None)

    def prices(self, mhn):
        self.calls.append(("prices", mhn))
        return self._wrap(ItemPrices(mhn, list(self._prices)))

    def item_kline(self, mhn, period, platform):
        self.calls.append(("item_kline", mhn, KlinePeriod(period).name))
        if period == KlinePeriod.WEEK:
            if self.weekly_fail:
                raise self.weekly_fail
            return self._wrap(parse_kline(load_fixture("steamdt/kline_item_weekly.json")["data"]))
        bars = self._kline if self._kline is not None else parse_kline(load_fixture("steamdt/kline_item_daily.json")["data"])
        return self._wrap(bars)

    def broad_index(self):
        self.calls.append(("broad_index",))
        return self._wrap(BroadIndex.from_api(load_fixture("steamdt/broad_index.json")["data"]))

    def broad_kline(self, period):
        self.calls.append(("broad_kline", KlinePeriod(period).name))
        if period == KlinePeriod.WEEK:
            if self.weekly_fail:
                raise self.weekly_fail
            return self._wrap(parse_kline(load_fixture("steamdt/kline_broad_weekly.json")["data"]))
        return self._wrap(parse_kline(load_fixture("steamdt/kline_broad_daily.json")["data"]))


def with_item(rt, template_id=1414, mhn="AK-47 | Redline (Field-Tested)", cn="AK-47 | 红线 (久经沙场)"):
    with rt.session_factory() as s:
        s.add(Item(hash_name=mhn, cn_name=cn, youpin_template_id=template_id))
        s.commit()
