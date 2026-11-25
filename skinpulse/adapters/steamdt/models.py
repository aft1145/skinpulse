from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import IntEnum

# 字段名全部来自 2026-09-23 对真实接口的实测响应，不是照文档抄的（这个项目在「照推断的字段名
# 写解析器」上吃过一次大亏：价格字段猜成 purchasePrice，371 张单的价格全存成 0.00 而同步报告
# 「成功」）。对应的原始响应见 tests/fixtures/steamdt/。


def _dec(v) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _money(v) -> Decimal | None:
    """价格字段：**0 一律当作「这个平台没有数据」而不是「零元」**。

    实测 `/price/single` 对没覆盖到的平台（WAXPEER / DMARKET / CSMONEY / SKINPORT）返回的是
    `sellPrice: 0, sellCount: 0` 外加一个几个月前的 updateTime——不是真有人零元卖。把 0 原样
    存下来，看板上就会出现「市场价 0」这种看着合理、实际完全错误的数字，而这恰恰是本项目
    已经犯过一次的错误。真实的零元挂单不存在，所以这个取舍没有误伤。
    """
    d = _dec(v)
    return None if d is None or d <= 0 else d


def _ts(v) -> datetime | None:
    """updateTime 是秒级 unix 时间戳。返回 aware datetime（本项目禁止 naive）。"""
    try:
        i = int(v)
    except (TypeError, ValueError):
        return None
    return None if i <= 0 else datetime.fromtimestamp(i, tz=timezone.utc)


@dataclass(frozen=True)
class PlatformPrice:
    """某个饰品在某个平台的行情快照。

    `sell_price` 和 `bidding_price` 都可能是 None（该平台无数据），**两者的语义完全不同**：
    前者是「卖方最低要价」，后者是「买方最高出价」，中间的价差就是这个平台的买卖盘口。
    """

    platform: str
    platform_item_id: str
    sell_price: Decimal | None
    sell_count: int
    bidding_price: Decimal | None
    bidding_count: int
    update_time: datetime | None

    @property
    def has_data(self) -> bool:
        return self.sell_price is not None or self.bidding_price is not None

    @classmethod
    def from_api(cls, d: dict) -> "PlatformPrice":
        return cls(
            platform=str(d.get("platform") or ""),
            platform_item_id=str(d.get("platformItemId") or ""),
            sell_price=_money(d.get("sellPrice")),
            sell_count=int(d.get("sellCount") or 0),
            bidding_price=_money(d.get("biddingPrice")),
            bidding_count=int(d.get("biddingCount") or 0),
            update_time=_ts(d.get("updateTime")),
        )


@dataclass(frozen=True)
class ItemPrices:
    market_hash_name: str
    platforms: list[PlatformPrice]

    def of(self, platform: str) -> PlatformPrice | None:
        return next((p for p in self.platforms if p.platform == platform), None)

    def with_data(self) -> list[PlatformPrice]:
        return [p for p in self.platforms if p.has_data]


@dataclass(frozen=True)
class PlatformAverage:
    platform: str
    avg_price: Decimal | None

    @classmethod
    def from_api(cls, d: dict) -> "PlatformAverage":
        return cls(platform=str(d.get("platform") or ""), avg_price=_money(d.get("avgPrice")))


@dataclass(frozen=True)
class AveragePrice:
    """近 7 日均价：`avg_price` 是全平台综合，`platforms` 是各平台各自的均价。

    实测这个接口只返回**有数据的**平台（AK-47 Case Hardened FT 只回了 6 个），不像
    `/price/single` 会把没数据的平台也列出来占位。所以这里没有 0 值需要过滤，但仍然走
    `_money`，免得以后服务端改了行为时默默把 0 写进库。"""

    market_hash_name: str
    avg_price: Decimal | None
    platforms: list[PlatformAverage]

    def of(self, platform: str) -> Decimal | None:
        return next((p.avg_price for p in self.platforms if p.platform == platform), None)

    @classmethod
    def from_api(cls, d: dict) -> "AveragePrice":
        return cls(
            market_hash_name=str(d.get("marketHashName") or ""),
            avg_price=_money(d.get("avgPrice")),
            platforms=[PlatformAverage.from_api(x) for x in (d.get("dataList") or [])],
        )


@dataclass(frozen=True)
class BaseItem:
    """`/base` 的一条：中文名、marketHashName，以及各平台上这件饰品的 itemId。

    **YOUPIN 的 itemId 就是悠悠的 templateId**——2026-09-23 用我们自己账号里的单核对过
    （1414 = AK-47 红线久经、1022 = 裹手防水布胶带久经，都一字不差）。这条是整个多平台对账
    的地基：有了它就能用 ID 精确映射，不必去匹配中文名。名字匹配有多不可靠，同一次核对里也
    有实例——「人工染色」的英文是 Stained 而不是 Case Hardened（后者是「表面淬火」），凭直觉
    手写英文名当场就错了一个。
    """

    name: str
    market_hash_name: str
    platform_item_ids: dict[str, str]

    def youpin_template_id(self) -> int | None:
        raw = self.platform_item_ids.get("YOUPIN")
        try:
            return int(raw) if raw else None
        except ValueError:
            return None

    @classmethod
    def from_api(cls, d: dict) -> "BaseItem":
        ids = {}
        for p in (d.get("platformList") or []):
            name, item_id = p.get("name"), p.get("itemId")
            if name and item_id:
                ids[str(name)] = str(item_id)
        return cls(
            name=str(d.get("name") or ""),
            market_hash_name=str(d.get("marketHashName") or ""),
            platform_item_ids=ids,
        )


class KlinePeriod(IntEnum):
    """K 线周期。文档只说 type 取 1~3、没说含义，以下是 2026-09-23 实测出来的：

    | type | 周期 | 实测历史深度（AK-47 红线久经） |
    |---|---|---|
    | 1 | 小时 | 约 90 天（2134 根） |
    | 2 | 日 | 365 天 |
    | 3 | 周 | 从 2022-07 开始（222 根）；2026-09-27 再测只剩 171 根，中间缺两段半年左右。大盘周线从 2023-07 开始（170 根） |
    """

    HOUR = 1
    DAY = 2
    WEEK = 3


@dataclass(frozen=True)
class KlineBar:
    """一根 K 线。

    **接口返回的数组顺序是 `[时间, 开, 收, 高, 低]`，不是常见的 OHLC 顺序**——文档这么写，
    实测也是：AK-47 红线久经的 365 根日线，按「开收高低」读全部满足「高 ≥ 开/收、低 ≤ 开/收」，
    按「开高低收」读一根都不满足。凭直觉按 OHLC 解析，画出来的图会是错的，而且看着像真的。
    """

    ts: datetime
    open: Decimal
    close: Decimal
    high: Decimal
    low: Decimal

    @property
    def consistent(self) -> bool:
        return self.high >= max(self.open, self.close) and self.low <= min(self.open, self.close)

    @classmethod
    def from_api(cls, row) -> "KlineBar | None":
        # 字段不足五个的行直接丢：切片不会越界，但解包成四个值时会抛 ValueError。
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            return None
        ts = _ts(row[0])
        o, c, h, lo = (_dec(v) for v in row[1:5])
        if ts is None or None in (o, c, h, lo):
            return None
        return cls(ts=ts, open=o, close=c, high=h, low=lo)


# 字段顺序守卫的容忍度：允许极少数脏行（上游偶发），超过这个比例就认定是顺序变了。
_MAX_INCONSISTENT_SHARE = 0.05


def parse_kline(rows) -> list[KlineBar]:
    """把接口的 K 线数组解析成 KlineBar 列表，并用「高 ≥ 开/收、低 ≤ 开/收」守住字段顺序。

    实测数据里这条关系 100% 成立。如果某天超过 5% 的行不成立，几乎只可能是服务端改了数组顺序
    ——这时宁可报错，也不能把一张顺序错乱的图当真画出来。零星的不自洽行直接丢掉。
    """
    bars = [b for b in (KlineBar.from_api(r) for r in (rows or [])) if b is not None]
    if not bars:
        return []
    bad = [b for b in bars if not b.consistent]
    if len(bad) > len(bars) * _MAX_INCONSISTENT_SHARE:
        from skinpulse.adapters.steamdt.errors import SteamDTError

        raise SteamDTError("kline_shape", f"{len(bad)}/{len(bars)} 根 K 线不满足「高≥开收≥低」，"
                                          f"接口的数组顺序很可能变了（原为 [时间,开,收,高,低]）")
    return [b for b in bars if b.consistent]


@dataclass(frozen=True)
class BroadIndex:
    """CS2 大盘最新指数。

    **`diff_pct` 已经是百分数**，不要再乘 100：实测 `diffYesterday` 3.55、指数 795.52，
    3.55 / (795.52 − 3.55) = 0.448%，而接口给的 `diffYesterdayRatio` 就是 0.4482。
    `history` 是最近约 25 个小时的整点值。
    """

    value: Decimal
    update_time: datetime | None
    diff: Decimal | None
    diff_pct: Decimal | None
    history: list[tuple[datetime, Decimal]]

    @classmethod
    def from_api(cls, d: dict) -> "BroadIndex":
        hist = []
        for row in (d.get("historyMarketIndexList") or []):
            try:
                t, v = _ts(row[0]), _dec(row[1])
            except (IndexError, TypeError):
                continue
            if t is not None and v is not None:
                hist.append((t, v))
        value = _dec(d.get("broadMarketIndex"))
        if value is None or value <= 0:
            from skinpulse.adapters.steamdt.errors import SteamDTError

            # 大盘指数是这个结构里唯一不可能为空的字段；守卫必须覆盖它，否则看板会画出一个 0。
            raise SteamDTError("broad_index_shape", "大盘指数缺失或非正数")
        return cls(value=value, update_time=_ts(d.get("updateTime")), diff=_dec(d.get("diffYesterday")),
                   diff_pct=_dec(d.get("diffYesterdayRatio")), history=hist)
