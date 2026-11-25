"""`skinpulse demo` 的假行情：一份真实的 K 线（`klines/`，取自 tests/fixtures/steamdt）按饰品缩放、加点起伏，
日期整体平移到「昨天收盘」。同时实现看板用的 MarketData 接口和估值 / 选品用的 SteamDT 客户端接口，
全程不联网。"""
from __future__ import annotations

import json
import math
import zlib
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from skinpulse.adapters.steamdt.models import BroadIndex, ItemPrices, KlineBar, KlinePeriod, PlatformPrice, parse_kline
from skinpulse.services.market_data import Fetched

DATA = Path(__file__).resolve().parent / "klines"
_CENT = Decimal("0.01")


def _load(name: str):
    return json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))["data"]


def _money(x: float) -> Decimal:
    return Decimal(str(max(x, 0.01))).quantize(_CENT, rounding=ROUND_HALF_UP)


def _day_shift(rows: list, tz: ZoneInfo, now: datetime) -> int:
    """把最后一根 K 线挪到本地「昨天」要平移的秒数（整天）。"""
    last = datetime.fromtimestamp(int(rows[-1][0]), timezone.utc).astimezone(tz).date()
    yesterday = now.astimezone(tz).date() - timedelta(days=1)
    return (yesterday - last).days * 86400


def _seed(name: str) -> int:
    return zlib.crc32(name.encode("utf-8"))


class DemoMarket:
    def __init__(self, base_prices: dict[str, Decimal], *, tz: str, now: datetime) -> None:
        self.base = {k: float(v) for k, v in base_prices.items()}
        self.tz = ZoneInfo(tz)
        self.now = now
        self._raw = {
            KlinePeriod.DAY: _load("kline_item_daily_youpin_1y"),
            KlinePeriod.WEEK: _load("kline_item_weekly"),
        }
        self._broad_raw = {
            KlinePeriod.DAY: _load("kline_broad_daily_1y"),
            KlinePeriod.WEEK: _load("kline_broad_weekly"),
        }
        self._shift = _day_shift(self._raw[KlinePeriod.DAY], self.tz, now)
        self._broad_shift = _day_shift(self._broad_raw[KlinePeriod.DAY], self.tz, now)
        self._cache: dict[tuple[str, KlinePeriod], list[KlineBar]] = {}

    # ---------- 生成 ----------

    def _bars(self, mhn: str, period: KlinePeriod) -> list[KlineBar]:
        key = (mhn, KlinePeriod(period))
        if key in self._cache:
            return self._cache[key]
        raw = self._raw[key[1]]
        if mhn not in self.base:
            self._cache[key] = []
            return []
        s = _seed(mhn)
        amp = 0.08 + (s % 9) / 100                       # 起伏 8%～16%
        wave = 40 + s % 120                              # 周期 40～160 根
        phase = (s % 628) / 100
        tilt = ((s >> 8) % 41 - 20) / 100                # 一年里额外 ±20% 的漂移
        n = len(raw)
        factors = [(1 + amp * math.sin(2 * math.pi * i / wave + phase)) * (1 + tilt * (i - n + 1) / n)
                   for i in range(n)]
        scale = self.base[mhn] / (float(raw[-1][2]) * factors[-1])
        rows = [[int(r[0]) + self._shift, *(float(v) * f * scale for v in r[1:5])] for r, f in zip(raw, factors)]
        bars = parse_kline([[t, *(float(_money(v)) for v in vals)] for t, *vals in rows])
        self._cache[key] = bars
        return bars

    def _broad(self, period: KlinePeriod) -> list[KlineBar]:
        raw = self._broad_raw[KlinePeriod(period)]
        return parse_kline([[int(r[0]) + self._broad_shift, *r[1:5]] for r in raw])

    def _prices(self, mhn: str) -> ItemPrices:
        bars = self._bars(mhn, KlinePeriod.DAY)
        if not bars:
            return ItemPrices(mhn, [])
        last = float(bars[-1].close)
        s = _seed(mhn)
        at = self.now - timedelta(minutes=7)

        def pp(platform: str, sell: float, bid: float, sell_count: int, bid_count: int) -> PlatformPrice:
            return PlatformPrice(platform=platform, platform_item_id=str(s % 100000), sell_price=_money(sell),
                                 sell_count=sell_count, bidding_price=_money(bid), bidding_count=bid_count,
                                 update_time=at)

        bid = 0.68 + (s >> 4) % 17 / 100                 # 最高求购是在售价的 68%～84%，选品结论才有高有低
        return ItemPrices(mhn, [
            pp("YOUPIN", last, last * bid, 20 + s % 300, 3 + s % 40),
            pp("BUFF", last * 1.03, last * (bid + 0.03), 30 + s % 500, 5 + s % 60),
        ])

    # ---------- SteamDT 客户端接口（估值、选品用） ----------

    def get_item_kline(self, market_hash_name: str, period: KlinePeriod = KlinePeriod.DAY,
                       platform: str = "ALL") -> list[KlineBar]:
        return list(self._bars(market_hash_name, period))

    def get_prices_batch(self, market_hash_names) -> list[ItemPrices]:
        return [self._prices(n) for n in market_hash_names if n in self.base]

    def get_broad_kline(self, period: KlinePeriod = KlinePeriod.DAY) -> list[KlineBar]:
        return self._broad(period)

    def get_broad_index(self) -> BroadIndex:
        d = dict(_load("broad_index"))
        shift = int(self.now.timestamp()) - int(d["updateTime"]) - 600
        d["updateTime"] = int(d["updateTime"]) + shift
        d["historyMarketIndexList"] = [[int(t) + shift, v] for t, v in d["historyMarketIndexList"]]
        return BroadIndex.from_api(d)

    # ---------- MarketData 接口（看板用） ----------

    def _wrap(self, v):
        return Fetched(v, self.now)

    def prices(self, market_hash_name: str):
        return self._wrap(self._prices(market_hash_name))

    def item_kline(self, market_hash_name: str, period: KlinePeriod = KlinePeriod.DAY, platform: str = "ALL"):
        return self._wrap(self.get_item_kline(market_hash_name, period, platform))

    def broad_index(self):
        return self._wrap(self.get_broad_index())

    def broad_kline(self, period: KlinePeriod = KlinePeriod.DAY):
        return self._wrap(self.get_broad_kline(period))
