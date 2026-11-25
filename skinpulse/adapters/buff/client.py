"""BUFF 只读客户端（BUFF spec §3）。

**只读靠结构保证**：这个类只有一个发请求的方法 `_get`，没有 POST / PUT / DELETE；测试会扫这个包的
源码确认。下单、改价、撤单、上架、付款的接口一个都不能进来（hook `guard_platform_writes.py` 也拦）。

- 凭据只有浏览器里复制的 `session` cookie；另带按号持久化的 `Device-Id` cookie。
- 同一个号两次请求至少隔 `request_gap_s` 秒（再加最多 30% 的随机抖动），由客户端自己守。
- 不读代理环境变量（ADR-0011）。
- cookie、URL 绝不进日志和异常（传输层异常原样抛，调用方只记类型名）。
"""
from __future__ import annotations

import logging
import random
import time
from typing import Callable

import httpx

from skinpulse.adapters.buff.errors import RISK_STATUS, RISK_WORDS, BuffError, BuffRateLimited, raise_for_code
from skinpulse.adapters.buff.models import (
    BillPage, BuffBuyOrder, InventoryPage, Me, QueuePage, parse_bills, parse_buy_orders, parse_inventory, parse_queue,
)
from skinpulse.config import BuffSettings

log = logging.getLogger(__name__)

PATH_MY_BUY_ORDERS = "/api/market/buy_order/wait_supply"
PATH_BUY_QUEUE = "/api/market/goods/buy_order"
PATH_BUY_HISTORY = "/api/market/buy_order/history"
PATH_SELL_HISTORY = "/api/market/sell_order/history"
PATH_INVENTORY = "/api/market/steam_inventory"

MY_ORDERS_PAGE = 60
QUEUE_PAGE = 100
MY_ORDERS_MAX_PAGES = 20
JITTER = 0.3


class BuffClient:
    def __init__(self, settings: BuffSettings, cookie: str | None, device_id: str | None, *,
                 http: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, rand: Callable[[], float] = random.random) -> None:
        self.s = settings
        self.cookie = cookie
        self.device_id = device_id
        self.http = http or httpx.Client(timeout=30, trust_env=False)  # 不读代理变量，见 ADR-0011
        self._sleep, self._clock, self._rand = sleep, clock, rand
        self._last: float | None = None
        self.requests = 0
        self._rotation_logged = False

    @property
    def has_cookie(self) -> bool:
        return bool(self.cookie)

    def __repr__(self) -> str:  # 绝不把 cookie 带进 repr
        return f"BuffClient(device_id={self.device_id!r}, has_cookie={self.has_cookie})"

    # ---- 唯一的请求入口：GET ----

    def _pace(self) -> None:
        if self._last is None:
            return
        gap = self.s.request_gap_s * (1 + JITTER * self._rand())
        wait = gap - (self._clock() - self._last)
        if wait > 0:
            self._sleep(wait)

    def _get(self, path: str, params: dict) -> dict:
        self._pace()
        jar = []
        if self.cookie:
            jar.append(f"session={self.cookie}")
        if self.device_id:
            jar.append(f"Device-Id={self.device_id}")
        headers = {"User-Agent": self.s.user_agent, "Accept-Language": self.s.accept_language,
                   "Accept": "application/json", "Referer": self.s.base + "/"}
        if jar:
            headers["Cookie"] = "; ".join(jar)
        try:
            r = self.http.get(self.s.base + path, params={"game": "csgo", **params}, headers=headers)
        finally:
            self._last = self._clock()
            self.requests += 1
            # 服务端种的 cookie（csrf_token 等）不留：每次只带我们自己的两个，免得混进别的会话
            self.http.cookies.clear()
        # BUFF 每个响应都带 Set-Cookie: session。值没变就是续期，不记；变了只记一次（看 BUFF 会不会换 cookie，
        # 值本身绝不进日志）
        new = r.cookies.get("session")
        if new and self.cookie and new != self.cookie and not self._rotation_logged:
            self._rotation_logged = True
            log.info("buff: server sent a different session cookie (value not logged; the saved one is kept)")
        text = r.text or ""
        if r.status_code in RISK_STATUS:
            raise BuffRateLimited(f"http{r.status_code}", "")
        try:
            body = r.json()
        except ValueError:
            if any(w in text for w in RISK_WORDS):
                raise BuffRateLimited("html", "") from None
            raise BuffError(f"http{r.status_code}" if r.status_code != 200 else "badjson", "") from None
        if not isinstance(body, dict):
            raise BuffError("badjson", "")
        raise_for_code(body.get("code"), str(body.get("error") or body.get("msg") or ""))
        data = body.get("data")
        return data if isinstance(data, dict) else {}

    # ---- 接口 ----

    def my_buy_orders(self) -> tuple[list[BuffBuyOrder], Me]:
        """全部「正在求购」的单，翻到最后一页（每页 60）。"""
        out: list[BuffBuyOrder] = []
        me = Me(None, None)
        for page in range(1, MY_ORDERS_MAX_PAGES + 1):
            data = self._get(PATH_MY_BUY_ORDERS, {"page_num": page, "page_size": MY_ORDERS_PAGE})
            orders, page_me, total_page = parse_buy_orders(data)
            out += orders
            me = Me(me.uid or page_me.uid, me.nickname or page_me.nickname)
            if len(orders) < MY_ORDERS_PAGE or page >= total_page:
                break
        return out, me

    def whoami(self) -> Me:
        """校验 cookie、拿用户号和昵称（添加账号时用）：一页只要 1 条的 `wait_supply`。"""
        _, me, _ = parse_buy_orders(self._get(PATH_MY_BUY_ORDERS, {"page_num": 1, "page_size": 1}))
        return me

    def buy_queue(self, goods_id: int, page: int = 1) -> QueuePage:
        return parse_queue(self._get(PATH_BUY_QUEUE, {"goods_id": goods_id, "page_num": page, "page_size": QUEUE_PAGE}))

    def buy_history(self, page: int = 1, size: int = 20) -> BillPage:
        return parse_bills(self._get(PATH_BUY_HISTORY, {"page_num": page, "page_size": size}))

    def sell_history(self, page: int = 1, size: int = 100) -> BillPage:
        return parse_bills(self._get(PATH_SELL_HISTORY, {"page_num": page, "page_size": size}))

    def inventory(self, page: int = 1, size: int = 500) -> InventoryPage:
        # 只用 force=0（读 BUFF 缓存的库存）；force=1 会让 BUFF 去 Steam 重拉，没必要
        return parse_inventory(self._get(PATH_INVENTORY, {"force": 0, "state": "all", "page_num": page,
                                                          "page_size": size}))
