"""看板用的行情读取层：SteamDT 前面挡一层带过期时间的缓存。

为什么页面不能直接调 SteamDT：大盘那两个接口不在官方配额表里，而且很紧——2026-09-23 实测大盘
K 线几分钟内调三次就回 4005「当前接口请求已达到上限」。刷新几下看板就能把额度打光，而且监控
服务（告警里的「别处买盘」）和看板共用同一个 key、同一份额度。

两条规则：

1. 每类数据有自己的过期时间，按它本身变化的快慢定（见 TTL_S）。
2. **撞上配额或服务端出错时，有旧数据就返回旧数据并标明「已过期」**，而不是让整个页面报错；
   只有从来没取到过的数据才把错误抛给页面。旧数据在页面上必须带着它的获取时间一起显示。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Generic, TypeVar

from skinpulse.adapters.steamdt.errors import SteamDTError, SteamDTQuotaExceeded
from skinpulse.adapters.steamdt.models import KlinePeriod

T = TypeVar("T")

TTL_S = {
    "prices": 5 * 60,          # 各平台在售/求购价，变得快
    "item_kline": 60 * 60,     # 日线一天才动一根，小时级缓存足够
    "broad_index": 10 * 60,    # 大盘最新值按小时更新（实测 history 是整点值）
    "broad_kline": 6 * 60 * 60,  # 大盘 K 线配额最紧，缓存最久
}

# 撞上配额（4005）以后，这个键冷却多久不再请求。大盘接口实测几分钟三次就 4005，冷启动时打开大盘页
# 一下就是指数、日线、周线三次；限流期间再请求只会继续 4005，还可能拖长限流。
QUOTA_COOLDOWN_S = 5 * 60


@dataclass(frozen=True)
class Fetched(Generic[T]):
    value: T
    fetched_at: datetime
    stale: bool = False              # True = 这次刷新失败了，给的是上一次的数据
    error: str | None = None         # stale 时说明为什么没刷新成功（不含任何密钥）


class MarketData:
    def __init__(self, client, *, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.client = client
        self.clock = clock
        self.wall = wall
        self._cache: dict[tuple, tuple[float, datetime, object]] = {}
        self._cooldown: dict[tuple, tuple[float, SteamDTQuotaExceeded]] = {}
        self._lock = threading.Lock()

    def _get(self, key: tuple, ttl: float, fetch: Callable[[], T]) -> Fetched[T]:
        now = self.clock()
        with self._lock:
            hit = self._cache.get(key)
            cool = self._cooldown.get(key)
        if hit is not None and now - hit[0] < ttl:
            return Fetched(hit[2], hit[1])
        if cool is not None and now - cool[0] < QUOTA_COOLDOWN_S:
            if hit is not None:
                return Fetched(hit[2], hit[1], stale=True, error=str(cool[1]))
            raise cool[1]
        try:
            value = fetch()
        except SteamDTError as e:
            if isinstance(e, SteamDTQuotaExceeded):
                with self._lock:
                    self._cooldown[key] = (now, e)
            if hit is not None:
                # 有旧的就给旧的——看板上一个带时间戳的旧数字，比一整页报错有用得多。
                return Fetched(hit[2], hit[1], stale=True, error=str(e))
            raise
        stamp = self.wall()
        with self._lock:
            self._cache[key] = (now, stamp, value)
            self._cooldown.pop(key, None)
        return Fetched(value, stamp)

    def prices(self, market_hash_name: str):
        return self._get(("prices", market_hash_name), TTL_S["prices"],
                         lambda: self.client.get_prices(market_hash_name))

    def item_kline(self, market_hash_name: str, period: KlinePeriod = KlinePeriod.DAY, platform: str = "ALL"):
        return self._get(("item_kline", market_hash_name, int(period), platform), TTL_S["item_kline"],
                         lambda: self.client.get_item_kline(market_hash_name, period, platform))

    def broad_index(self):
        return self._get(("broad_index",), TTL_S["broad_index"], self.client.get_broad_index)

    def broad_kline(self, period: KlinePeriod = KlinePeriod.DAY):
        return self._get(("broad_kline", int(period)), TTL_S["broad_kline"],
                         lambda: self.client.get_broad_kline(period))
