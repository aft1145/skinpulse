from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from skinpulse.adapters.youpin import crypto
from skinpulse.adapters.youpin.errors import (
    LoginRequired, RateLimited, UkExpired, VersionOrUkRejected, YoupinError, raise_for_code,
)
from skinpulse.adapters.youpin.models import (
    Inventory, LeaseOutPage, MyPurchaseOrder, OnSaleItem, PublicPurchaseOrder, TemplateDetail, TradeOrder,
    TradeOrderDetail, UserInfo,
)
from skinpulse.config import YoupinSettings

log = logging.getLogger(__name__)

PATH_DEVICE_W2 = "/api/deviceW2"
PATH_TEMPLATE_PURCHASE_ORDERS_PC = "/api/youpin/bff/trade/purchase/order/getTemplatePurchaseOrderListPC"
PATH_ON_SALE_LIST = "/api/homepage/pc/goods/market/queryOnSaleCommodityList"
PATH_TEMPLATE_DETAIL = "/api/homepage/pc/goods/market/queryTemplateDetail"
# 曾经以为这是"PC 站自己的"用户信息接口。2026-08 用真实 token 跑 `probe-token`（见 cli.py）后
# 已经证实不是：它走的是 sk 头那套加密通道——浏览器自己发的请求本身就带着一个 344 字符的 sk 请求头
# （不是服务端单方面加的，是客户端主动发起加密），响应也回一个等长的 sk 响应头，body 是十六进制、
# 解不出 JSON。想正常用这个接口就得反向实现那一整套 sk 加密方案，不划算——PATH_USER_INFO_LEGACY
# 用明文 JSON 就能拿到同样的账号信息。常量留着给 `probe-token` 做对比诊断用，但 get_user_info
# 不再调用它。
PATH_USER_INFO = "/api/youpin/bff/user/Account/getUserInfoForApp"
# 曾经写着"App 渠道专属，已随 App 渠道一起停用"——那只是从代码线索推断的，从没真正抓包验证过。
# 2026-08 用真实 token 跑 `probe-token` 后证实这个推断是错的：App host 上明文 JSON 返回
# code=0、账号信息齐全，是三个探测接口里唯一能直接解析出结构的一个。get_user_info 现在就打
# 这个接口（app_api_base host，仍然带 PC 请求头——那是探测成功时用的请求头）。
PATH_USER_INFO_LEGACY = "/api/user/Account/getUserInfo"
PATH_SEARCH_MY_PURCHASE_ORDERS = "/api/youpin/bff/trade/purchase/order/searchPurchaseOrderList"
# 盈亏统计（盈亏 spec §2，research 2026-11-24）。都是查询接口，只读。
PATH_TRADE_LIST = "/api/youpin/bff/trade/sale/v1/{side}/list"
PATH_ORDER_DETAIL = "/api/youpin/bff/trade/v1/order/query/detail"
PATH_INVENTORY_TREND = "/api/youpin/commodity/user/inventory/price/trend"
PATH_LEASE_OUT_LIST = "/api/youpin/bff/trade/v1/order/lease/out/list"
PATH_MY_ON_SALE = "/api/youpin/bff/new/commodity/v1/commodity/list/sell"
TRADE_DONE = 340          # 买卖订单「已完成」
TRADE_PAGE_SIZE = 20      # 买卖订单列表每页上限（开源项目实测，超过返回空列表）


# ---- badjson 诊断 与 probe-token 共用的响应摘要工具 ----
#
# 两处都需要回答同一个问题——"这个没解析成 JSON/形状奇怪的响应长什么样"——而不是"该把它映射成
# 哪个业务异常"，所以单独抽出来。这些函数只碰 httpx.Response 本身，从不碰 r.request：请求头、
# token、uk 都在 request 那边，绝不能出现在诊断文本里，这是诊断文本能安全地整个贴进 issue 的前提。

_RESPONSE_EXCERPT_LIMIT = 200  # 约 200 个字符；探测/诊断的响应体可能带账号自己的数据，只给一小段

# 用来跟响应头做"眼熟吗"比对的常见 HTTP 响应头名单（小写）。不在这份名单里、也不是 sk 的头，会被
# 当成"非标准"头单独点名——这类头往往才是"响应形状不对劲"的线索（比如 YouPin 网页端拦截器专门
# 认的那个 sk 头，见模块顶部说明）。
_COMMON_RESPONSE_HEADERS = frozenset({
    "content-type", "content-length", "connection", "date", "server",
    "cache-control", "expires", "pragma", "vary", "etag", "last-modified",
    "transfer-encoding", "content-encoding", "keep-alive",
    "access-control-allow-origin", "access-control-allow-credentials",
    "access-control-allow-headers", "access-control-allow-methods",
    "access-control-expose-headers", "access-control-max-age",
    "x-content-type-options", "x-frame-options", "x-xss-protection",
    "strict-transport-security", "referrer-policy", "content-disposition",
    "location", "retry-after", "content-language", "content-security-policy",
    "set-cookie", "via", "age", "alt-svc", "x-powered-by", "x-request-id",
    "traceparent", "cf-ray", "cf-cache-status",
})


def _has_sk_header(headers: httpx.Headers) -> bool:
    return "sk" in {k.lower() for k in headers.keys()}


def _other_response_header_names(headers: httpx.Headers) -> list[str]:
    """sk 之外、不在常见响应头名单里的头名——只报名字，不报值：值可能是签名/凭证性质的内容，
    名字本身足够用来诊断"这响应有点怪"，没必要多带出风险。"""
    names = {k.lower() for k in headers.keys()}
    return sorted(names - _COMMON_RESPONSE_HEADERS - {"sk"})


def _body_excerpt(content: bytes, limit: int = _RESPONSE_EXCERPT_LIMIT) -> str:
    """人类可读的响应体摘要：UTF-8 解得出来就截断到 limit 个字符；解不出来就退回十六进制前缀
    （limit // 2 字节——两个十六进制字符对应一个字节，长度仍大致落在 limit 附近）。"""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        n = max(limit // 2, 1)
        more = "..." if len(content) > n else ""
        return f"<{len(content)}-byte binary, hex prefix: {content[:n].hex()}{more}>"
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def _describe_unparseable_response(r: httpx.Response) -> str:
    """r.json() 抛错时用来拼诊断信息：HTTP 状态、content-type、sk 响应头是否存在、还有没有别的
    非标准响应头、body 长度，以及一段截断的摘要。_handle_response 的 badjson 分支和 probe-token
    共用这份诊断内容——"一个没解析成 JSON 的响应长什么样"，两处的答案必须一致。"""
    content_type = r.headers.get("content-type") or "<none>"
    other_headers = _other_response_header_names(r.headers)
    return (
        f"HTTP {r.status_code}, content-type={content_type!r}, "
        f"sk-response-header={'present' if _has_sk_header(r.headers) else 'absent'}, "
        f"other-response-headers={other_headers or '[]'}, "
        f"body-length={len(r.content)} bytes, "
        f"body-excerpt(<={_RESPONSE_EXCERPT_LIMIT} chars, truncated)={_body_excerpt(r.content)!r}"
    )


@dataclass(frozen=True)
class ProbeResult:
    """`YoupinClient._probe()` 的返回值：只把响应摊开成看得懂的字段，不做业务 code -> 异常的
    映射——那套映射是给正常调用用的，会把诊断最需要看到的原始形状吞掉。"""

    status_code: int
    content_type: str
    has_sk_header: bool
    body_length: int
    is_json: bool
    code: int | str | None = None
    msg: str = ""
    excerpt: str | None = None


class YoupinClient:
    def __init__(self, settings: YoupinSettings, *, http: httpx.Client | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 device_id: str | None = None) -> None:
        self.s = settings
        self.http = http or httpx.Client(timeout=20, trust_env=False)  # 不读代理变量，见 ADR-0011
        self.clock = clock
        self.sleep = sleep
        self.device_id = device_id or str(uuid.uuid4())
        self.token: str | None = None
        self.uk: str | None = None
        self.device_uk: str = ""
        self._uk_at: float = -1e9

    # ---------- uk ----------
    def ensure_uk(self, force: bool = False) -> str:
        if not force and self.uk and (self.clock() - self._uk_at) < self.s.uk_ttl_s:
            return self.uk
        cipher = crypto.UkCipher(crypto.random_aes_key())
        payload = crypto.build_device_w2_payload(cipher, str(uuid.uuid4()))
        r = self.http.post(self.s.app_api_base + PATH_DEVICE_W2, json=payload,
                           headers={"user-agent": self.s.app_headers.user_agent})
        if not r.content.strip():
            raise RateLimited(str(r.status_code), "deviceW2 failed")
        if r.status_code != 200:
            raise YoupinError(str(r.status_code), f"deviceW2 failed: HTTP {r.status_code}")
        self.uk, self.device_uk = crypto.parse_device_w2_response(cipher, r.text)
        self._uk_at = self.clock()
        log.debug("uk refreshed")
        return self.uk

    # ---------- headers ----------
    def _pc_headers(self) -> dict[str, str]:
        h = self.s.pc_headers
        return {
            "content-type": "application/json",
            "uk": self.ensure_uk(),
            "deviceUk": self.device_uk,
            "deviceId": self.device_id,
            "platform": h.platform,
            "appType": h.app_type,
            "AppVersion": h.app_version,
            "App-Version": h.app_version,
            "secret-v": h.secret_v,
            "user-agent": h.user_agent,
            "Origin": "https://www.youpin898.com",
            "Referer": "https://www.youpin898.com/",
        }

    def _app_headers(self) -> dict[str, str]:
        """Android App 渠道请求头。当前没有任何调用点使用它——该渠道被服务端版本网关
        卡死（见 _handle_response 里 429/85100 的处理），账号相关调用都走 _pc_headers()。
        保留是给 PC 渠道将来也被卡版本时的应急后备，见 config.YoupinAppHeaders 的说明。"""
        h = self.s.app_headers
        return {
            "content-type": "application/json; charset=utf-8",
            "uk": self.ensure_uk(),
            "App-Version": h.app_version,
            "AppType": h.app_type,
            "deviceType": h.device_type,
            "package-type": h.package_type,
            "DeviceId": self.device_id,
            "DeviceToken": self.device_id,
            "platform": h.platform,
            "Gameid": h.game_id,
            "user-agent": h.user_agent,
        }

    # ---------- transport ----------
    @staticmethod
    def _unwrap(body: dict) -> Any:
        code = body.get("code", body.get("Code"))
        msg = body.get("msg", body.get("Msg", ""))
        raise_for_code(code, msg)
        return body.get("data", body.get("Data"))

    def _handle_response(self, r: httpx.Response) -> Any:
        """单一的 HTTP 状态码 -> 异常映射入口，_get/_post 共用。"""
        if r.status_code == 405:
            self.uk = None
            raise UkExpired("405", "uk expired")
        if r.status_code == 429:
            # 429 既用于真限流，也用于版本网关拒绝（body code 85100），两者必须靠 body
            # 里的业务 code 区分，否则版本拒绝会被误判成"限流"而无法诊断。body 为空或
            # 解析不出 code 时，才退回到 RateLimited。
            if r.content.strip():
                try:
                    body = r.json()
                except ValueError:
                    body = None
                if isinstance(body, dict) and body.get("code", body.get("Code")) is not None:
                    self._unwrap(body)  # 有明确业务 code：交给统一的 code -> 异常映射决定
            raise RateLimited("429", "rate limited")
        if not r.content.strip():
            raise RateLimited(str(r.status_code), "empty response")
        if r.status_code != 200:
            raise YoupinError(str(r.status_code), f"HTTP {r.status_code}")
        try:
            body = r.json()
        except ValueError as e:
            raise YoupinError("badjson", _describe_unparseable_response(r)) from e
        return self._unwrap(body)

    def _post(self, path: str, json: dict, *, base: str | None = None,
              headers_factory: Callable[[], dict[str, str]] | None = None,
              authed: bool = False, _retry: bool = True) -> Any:
        """host 和请求头是两个独立的选择：base 默认 PC host，headers_factory 默认
        _pc_headers（每次调用都重新生成，好让重试时能带上刷新后的 uk）。当前没有调用点
        传别的值——账号相关接口全部改走 PC 渠道了；App 渠道的 base/headers 仍然可以在
        需要时显式传入（见 _app_headers 的说明）。"""
        base = self.s.pc_api_base if base is None else base
        make_headers = headers_factory or self._pc_headers
        headers = make_headers()
        if authed:
            if not self.token:
                raise LoginRequired("84101", "no token")
            headers["authorization"] = "Bearer " + self.token
        r = self.http.post(base + path, json=json, headers=headers)
        try:
            return self._handle_response(r)
        except VersionOrUkRejected:
            if _retry:
                self.ensure_uk(force=True)
                return self._post(path, json, base=base, headers_factory=headers_factory,
                                  authed=authed, _retry=False)
            raise

    # ---------- anonymous channel ----------
    def get_template_purchase_orders(self, template_id: int, page_size: int = 20, *,
                                      authed: bool = False) -> list[PublicPurchaseOrder]:
        """公开求购队列。**匿名调用硬上限 20 条**：`pageIndex`/`pageSize` 都被忽略，`hasNext`
        还会在后面明明还有的时候谎报 `false`。

        带上 token（`authed=True`）之后两个参数才真的生效——2026-09-21 实测模板 101422
        （total=347）：匿名传 pageSize=100 仍返 20 条；登录后 pageSize=100 返 100 条、
        pageSize=400 一次返回全部 347 条，pageIndex=2 与 pageIndex=1 单号零重叠、且正好接在
        第 20 条之后。这是把"排在前 20 名之外的自己的单"算出排名的唯一办法。

        默认仍走匿名：认证请求是拿账号在换深度，风控盯的正是账号，所以只在匿名那一页装不下
        又没找到自己时才值得升级（见 Monitor._public_queue_for）。"""
        data = self._post(PATH_TEMPLATE_PURCHASE_ORDERS_PC,
                          {"templateId": str(template_id), "pageIndex": 1, "pageSize": page_size},
                          authed=authed)
        rows = (data or {}).get("purchaseOrderResponseList") or []
        return [PublicPurchaseOrder.from_api(x) for x in rows]

    def get_on_sale(self, template_id: int, page_size: int = 10) -> list[OnSaleItem]:
        data = self._post(PATH_ON_SALE_LIST, {"gameId": "730", "listType": "10", "templateId": str(template_id),
                                              "listSortType": 1, "sortType": 0, "pageIndex": 1, "pageSize": page_size})
        return [OnSaleItem.from_api(x) for x in (data or [])]

    def get_template_detail(self, template_id: int) -> TemplateDetail:
        data = self._post(PATH_TEMPLATE_DETAIL, {"gameId": "730", "listType": "10", "templateId": str(template_id)})
        return TemplateDetail.from_api(data or {})

    # ---------- account channel ----------
    def _get(self, path: str, params: dict | None = None, *, base: str | None = None,
             headers_factory: Callable[[], dict[str, str]] | None = None,
             authed: bool = True, _retry: bool = True) -> Any:
        base = self.s.pc_api_base if base is None else base
        make_headers = headers_factory or self._pc_headers
        headers = make_headers()
        if authed:
            if not self.token:
                raise LoginRequired("84101", "no token")
            headers["authorization"] = "Bearer " + self.token
        r = self.http.get(base + path, params=params, headers=headers)
        try:
            return self._handle_response(r)
        except VersionOrUkRejected:
            if _retry:
                self.ensure_uk(force=True)
                return self._get(path, params=params, base=base, headers_factory=headers_factory,
                                 authed=authed, _retry=False)
            raise

    def get_user_info(self) -> UserInfo:
        """账号信息，见模块顶部 PATH_USER_INFO_LEGACY 的说明：走 app_api_base host + PC 请求头，
        不是 PATH_USER_INFO（那个在 sk 加密通道后面）。这个老接口的确切字段名从没被实测过——只
        知道响应是一段 633 字节的 JSON，从没解码看过——所以字段名仍然按候选名容错解析。

        user_id 是排名引擎（core.rank.compute_rank）在公共求购队列里认自己单子的兜底字段：
        purchase_no 直接匹配不到时，靠 user_id == my_user_id 兜底。user_id==0 本身还是
        "字段缺失"的哨兵值，compute_rank 已经用 `if ... and my_user_id` 防住了它——但防住
        的代价是这条兜底路径被整个跳过，本该能靠 user_id 找到的自己的订单会变成"排名不可解/
        超出可见范围"，且不报任何错，看起来只是运气不好。一个字段名都对不上时，绝不能像旧代码
        那样静默退回 UserInfo(0, "")，把这种静默失效引到排名引擎里——宁可在这里就响亮地失败。"""
        d = self._get(PATH_USER_INFO_LEGACY, base=self.s.app_api_base)
        uid = d.get("UserId", d.get("userId")) if isinstance(d, dict) else None
        if uid is None:
            # 不回显原始响应体——它是账号自己的数据（手机号/昵称/头像等）。只报告顶层字段名
            # 做诊断，风格与 auth._extract_token 处理 doLogin 响应的方式一致。
            shape = sorted(d.keys()) if isinstance(d, dict) else type(d).__name__
            raise YoupinError(
                "nouserid",
                f"no recognizable user id field in {PATH_USER_INFO_LEGACY} response (shape: {shape})",
            )
        nick = d.get("NickName", d.get("nickName", d.get("nickname", "")))
        return UserInfo(int(uid), str(nick or ""))

    def search_my_purchase_orders_page(self, status: int, page_index: int,
                                        page_size: int = 40) -> list[MyPurchaseOrder]:
        """「我的求购」的一页。status 20 = 求购中、30 = 暂停中、40 = 已完成（live-api-findings §7）；
        30、40 不按时间排，翻页和停止条件由调用方决定（services.fills.resolve_gone）。只读。"""
        data = self._post(PATH_SEARCH_MY_PURCHASE_ORDERS,
                          {"pageIndex": page_index, "pageSize": page_size, "status": status},
                          authed=True)
        if isinstance(data, dict):
            rows = data.get("list") or data.get("purchaseOrderList") or data.get("purchaseOrderResponseList") or []
        else:
            rows = data or []
        return [MyPurchaseOrder.from_api(x) for x in rows]

    def search_my_purchase_orders(self, status: int = 20, page_size: int = 40, *,
                                   max_pages: int = 50) -> list[MyPurchaseOrder]:
        out: list[MyPurchaseOrder] = []
        page = 1
        while True:
            if page > 1:
                self.sleep(self.s.page_sleep_s)
            rows = self.search_my_purchase_orders_page(status, page, page_size)
            out.extend(rows)
            if len(rows) < page_size:
                return out
            if page >= max_pages:
                log.warning("search_my_purchase_orders: hit max_pages=%d, returning %d orders collected so far",
                            max_pages, len(out))
                return out
            page += 1

    # ---------- 盈亏统计：买卖订单、库存、租出（盈亏 spec §2）。每个方法只取一页 / 一次 ----------
    def search_trade_orders_page(self, side: str, page_index: int,
                                 page_size: int = TRADE_PAGE_SIZE) -> list[TradeOrder]:
        """买入（side="buy"）/ 卖出（"sell"）订单的一页，只要「已完成」。按下单时间倒序（research §2）。"""
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be buy or sell, got {side!r}")
        data = self._post(PATH_TRADE_LIST.format(side=side),
                          {"keys": "", "orderStatus": TRADE_DONE, "pageIndex": page_index, "pageSize": page_size,
                           "presenterId": 0, "sceneType": 0}, authed=True)
        rows = (data or {}).get("orderList") or [] if isinstance(data, dict) else []
        return [TradeOrder.from_api(x, side) for x in rows]

    def get_trade_order_detail(self, order_no: str, user_id: int) -> TradeOrderDetail:
        data = self._post(PATH_ORDER_DETAIL, {"orderNo": order_no, "userId": user_id}, authed=True)
        return TradeOrderDetail.from_api(data if isinstance(data, dict) else {})

    def get_inventory_trend(self, page_index: int = 1) -> Inventory:
        """库存一页（pageSize=1000，近千件也一次拿完；更多时调用方按 totalCount 翻页），带悠悠记的买入价和市价。
        不带刷新参数，不触发去 Steam 拉库存。"""
        data = self._post(PATH_INVENTORY_TREND, {"pageIndex": page_index, "pageSize": 1000, "IsMerge": 0},
                          authed=True)
        return Inventory.from_api(data if isinstance(data, dict) else {})

    def search_lease_out_page(self, page_index: int, page_size: int = 20) -> LeaseOutPage:
        """租出中的订单一页（只有「租赁中」的）。这个接口的 hasNext 可信（research §5）。"""
        data = self._post(PATH_LEASE_OUT_LIST, {"gameId": 730, "pageIndex": page_index, "pageSize": page_size,
                                                "sortType": 0, "keywords": ""}, authed=True)
        return LeaseOutPage.from_api(data if isinstance(data, dict) else {})

    # ---------- diagnostics ----------
    def _probe(self, method: str, path: str, *, base: str, headers_factory: Callable[[], dict[str, str]],
               json: dict | None = None, token: str | None = None) -> ProbeResult:
        """只读诊断探测，专给 `probe-token`（cli.py）用：发一个请求，把响应摊开成 ProbeResult
        直接返回给调用方，不走 _handle_response 的业务码 -> 异常映射、不重试，也不改动
        self.token/self.uk 之外的任何状态——尤其绝不写 secrets，这是 probe-token "只探测、绝不
        保存"这条承诺的落地点。token 给了就现场拼 Authorization 头；不给就照 headers_factory()
        原样发，用来跑"不带 token"的基线对照。"""
        headers = headers_factory()
        if token:
            headers["authorization"] = "Bearer " + token
        if method == "GET":
            r = self.http.get(base + path, headers=headers)
        else:
            r = self.http.post(base + path, json=json, headers=headers)
        try:
            parsed = r.json()
        except ValueError:
            parsed = None
        content_type = r.headers.get("content-type") or ""
        has_sk = _has_sk_header(r.headers)
        if isinstance(parsed, dict):
            code = parsed.get("code", parsed.get("Code"))
            msg = str(parsed.get("msg", parsed.get("Msg", "")))
            return ProbeResult(r.status_code, content_type, has_sk, len(r.content), True, code=code, msg=msg)
        return ProbeResult(r.status_code, content_type, has_sk, len(r.content), False,
                           excerpt=_body_excerpt(r.content))
