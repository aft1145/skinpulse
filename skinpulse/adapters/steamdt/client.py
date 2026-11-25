from __future__ import annotations

import logging
from typing import Any, Iterable

import httpx

from skinpulse.adapters.steamdt.errors import SteamDTError, raise_for_envelope
from skinpulse.adapters.steamdt.models import (
    AveragePrice, BaseItem, BroadIndex, ItemPrices, KlineBar, KlinePeriod, PlatformPrice, parse_kline,
)

log = logging.getLogger(__name__)

BASE_URL = "https://open.steamdt.com"
PATH_PRICE_SINGLE = "/open/cs2/v1/price/single"
PATH_PRICE_BATCH = "/open/cs2/v1/price/batch"
PATH_PRICE_AVG = "/open/cs2/v1/price/avg"
PATH_BASE_ITEMS = "/open/cs2/v1/base"
PATH_ITEM_KLINE = "/open/cs2/item/v1/kline"
PATH_BROAD_INDEX = "/open/cs2/broad/v1/index"
PATH_BROAD_KLINE = "/open/cs2/broad/v1/kline"

# K 线接口认的平台（文档列举）。不传就是 ALL（全平台综合）。
KLINE_PLATFORMS = ("ALL", "BUFF", "YOUPIN", "C5", "STEAM", "HALOSKINS")

# /price/batch 一次最多 100 个名字（文档 maxItems:100）。超了服务端只回 errorCode 100002
# 「参数错误」——不说是哪个参数，也不说上限是多少。2026-09-23 拿 200 个名字实测过一次，
# 白白烧掉一个配额窗口，所以这里在发出去之前就拦住。
MAX_BATCH_NAMES = 100

# 官方公布的每个接口配额（doc.steamdt.com「接口权限列表」，2026-09-23 抄录）。差了五个数量级，
# 调用方必须按接口分别排布节奏，不能拿一个统一的「每秒几次」糊过去。
QUOTAS = {
    PATH_PRICE_SINGLE: "60 次/分钟",
    PATH_PRICE_BATCH: "1 次/分钟",     # 一次能带很多名字，所以这是批量对账的正确原语
    PATH_BASE_ITEMS: "1 次/天",        # 必须落盘缓存，用完这一次当天就没有第二次
    PATH_ITEM_KLINE: "120 次/分钟",
    # 大盘两个接口不在官方配额表里。2026-09-23 实测：大盘 K 线几分钟内调了三次，第三次就回
    # 4005「当前接口请求已达到上限」。额度未知但显然很紧，调用方必须缓存，绝不能每次开页面就打。
    PATH_BROAD_INDEX: "未公布（很紧）",
    PATH_BROAD_KLINE: "未公布（很紧，实测几分钟 3 次即触发 4005）",
}


class SteamDTClient:
    """SteamDT 开放平台客户端（只读）。

    两个和直觉相反、必须记住的行为：

    1. **HTTP 永远是 200**，成败只看响应体里的 `success`/`errorCode`。按状态码判断成败在这里
       一定错——本项目在悠悠那边踩过同款（HTTP 429 其实是版本闸门，不是限流）。
    2. **价格 0 等于「该平台没有数据」**，不是零元，解析时已经转成 None（见 models._money）。
    """

    def __init__(self, api_key: str | None, *, base_url: str = BASE_URL,
                 http: httpx.Client | None = None) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.http = http or httpx.Client(timeout=60, trust_env=False)  # 不读代理变量，见 ADR-0011

    def _headers(self) -> dict[str, str]:
        # key 为空时照样发出去：服务端会回 4002「apiKey不能为空」，映射成 SteamDTAuthError，
        # 比在客户端这边抛一个自造的错误更贴近真实原因。
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _request(self, method: str, path: str, **kw: Any) -> Any:
        r = self.http.request(method, self.base_url + path, headers=self._headers(), **kw)
        try:
            body = r.json()
        except ValueError:
            # 非 JSON 响应（网关错误页之类）。**绝不** echo 整个响应体——它可能把请求头回显出来，
            # 而请求头里带着 API key。只保留状态码和一小段截断摘要。
            raise SteamDTError(f"http_{r.status_code}", f"响应不是 JSON：{r.text[:120]!r}") from None
        raise_for_envelope(body)
        return body.get("data")

    def get_prices(self, market_hash_name: str) -> ItemPrices:
        """单个饰品的各平台在售价/求购价。配额 60 次/分钟。"""
        data = self._request("GET", PATH_PRICE_SINGLE, params={"marketHashName": market_hash_name})
        return ItemPrices(market_hash_name=market_hash_name,
                          platforms=[PlatformPrice.from_api(x) for x in (data or [])])

    def get_prices_batch(self, market_hash_names: Iterable[str]) -> list[ItemPrices]:
        """一次查多个饰品。**配额 1 次/分钟**——这是全库对账唯一划算的接口，一次就要把名字凑齐，
        别一张一张地调。

        一次最多 100 个名字（MAX_BATCH_NAMES），调用方负责分批并在批之间等够一分钟。
        """
        names = list(market_hash_names)
        if not names:
            return []
        if len(names) > MAX_BATCH_NAMES:
            raise ValueError(f"一次最多 {MAX_BATCH_NAMES} 个 marketHashName，给了 {len(names)} 个；"
                             f"请先分批（服务端对超限只回一句「参数错误」，且照样算掉一次配额）")
        data = self._request("POST", PATH_PRICE_BATCH, json={"marketHashNames": names})
        out = []
        for row in (data or []):
            out.append(ItemPrices(market_hash_name=str(row.get("marketHashName") or ""),
                                  platforms=[PlatformPrice.from_api(x) for x in (row.get("dataList") or [])]))
        return out

    def get_average_price_7d(self, market_hash_name: str) -> AveragePrice:
        """近 7 日均价（全平台 + 分平台）。这个接口不在官方那张配额表里，额度未知，保守着用。"""
        data = self._request("GET", PATH_PRICE_AVG, params={"marketHashName": market_hash_name})
        return AveragePrice.from_api(data or {})

    def get_base_items(self) -> list[BaseItem]:
        """全量饰品基础信息：中文名、marketHashName、各平台 itemId。

        **配额 1 次/天**，响应约 11 MB / 4.4 万条（2026-09-23 实测）。调用方必须把结果落盘，
        当天调过第二次就只能等明天——所以这个方法不该出现在任何自动轮询路径里。
        """
        data = self._request("GET", PATH_BASE_ITEMS)
        return [BaseItem.from_api(x) for x in (data or [])]

    def get_item_kline(self, market_hash_name: str, period: KlinePeriod = KlinePeriod.DAY,
                       platform: str = "ALL") -> list[KlineBar]:
        """单品 K 线，按时间升序。配额 120 次/分钟。

        数组顺序是 `[时间, 开, 收, 高, 低]`（不是 OHLC），`parse_kline` 用「高 ≥ 开/收 ≥ 低」守着，
        顺序一旦变了会直接报错而不是画错图。周期含义见 KlinePeriod（文档没写，实测得出）。
        """
        if platform not in KLINE_PLATFORMS:
            raise ValueError(f"K 线只支持这些平台：{', '.join(KLINE_PLATFORMS)}；给的是 {platform!r}")
        body = {"marketHashName": market_hash_name, "type": int(period)}
        if platform != "ALL":
            body["platform"] = platform
        return parse_kline(self._request("POST", PATH_ITEM_KLINE, json=body))

    def get_broad_index(self) -> BroadIndex:
        """CS2 大盘最新指数 + 最近约 25 小时的整点值。配额未公布，调用方必须缓存。"""
        return BroadIndex.from_api(self._request("GET", PATH_BROAD_INDEX) or {})

    def get_broad_kline(self, period: KlinePeriod = KlinePeriod.DAY) -> list[KlineBar]:
        """大盘 K 线。**配额很紧**（实测几分钟 3 次就 4005），调用方必须缓存。"""
        return parse_kline(self._request("POST", PATH_BROAD_KLINE, json={"type": int(period)}))
