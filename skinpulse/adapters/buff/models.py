"""BUFF 响应 → dataclass（BUFF spec §3）。字段以 2026-11-24 登录后实测为准（调研 §8、§9，fixture 在
tests/fixtures/buff/）。

**对手的身份不进数据类**：队列里别人的 user_id、订单里的买家 / 卖家、昵称、Steam ID，解析时就丢掉。
（公开队列里的 user_id 和我自己 `wait_supply` 里的 user_id 不是同一个号——BUFF 对外打了码——所以
认「我这张单」只能靠单号。）金额一律 Decimal。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


def _dec(v) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _ts(v) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(v), tz=timezone.utc) if v else None
    except (TypeError, ValueError, OverflowError):
        return None


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Specific:
    """求购条件（`specific` 的一项），如 `paintwear` / `磨损: 0.07-0.08` / `["0.07", "0.08"]`。"""
    type: str
    text: str
    values: tuple[str, ...]

    @classmethod
    def from_api(cls, d: dict) -> "Specific":
        return cls(str(d.get("type") or ""), str(d.get("text") or ""), tuple(str(x) for x in (d.get("values") or [])))


def _specifics(raw) -> tuple[Specific, ...]:
    return tuple(Specific.from_api(x) for x in (raw or []) if isinstance(x, dict))


@dataclass(frozen=True)
class GoodsInfo:
    goods_id: int
    market_hash_name: str
    name: str
    buy_max_price: Decimal | None = None
    sell_min_price: Decimal | None = None

    @classmethod
    def from_api(cls, d: dict) -> "GoodsInfo":
        return cls(goods_id=_int(d.get("goods_id")), market_hash_name=str(d.get("market_hash_name") or ""),
                   name=str(d.get("name") or ""), buy_max_price=_dec(d.get("buy_max_price")),
                   sell_min_price=_dec(d.get("sell_min_price")))


def _goods(data: dict) -> dict[int, GoodsInfo]:
    out = {}
    for k, v in (data.get("goods_infos") or {}).items():
        if isinstance(v, dict):
            g = GoodsInfo.from_api({"goods_id": k, **v})
            out[g.goods_id] = g
    return out


@dataclass(frozen=True)
class BuffBuyOrder:
    """我的一张求购单（`wait_supply`）。`buy_max_price` 是这件饰品此刻最高的**普通单**价（调研 §8.2）。"""
    order_id: str
    goods_id: int
    price: Decimal
    num: int
    real_num: int
    specific: tuple[Specific, ...]
    buy_max_price: Decimal | None
    created_at: datetime | None
    state: str
    market_hash_name: str = ""
    name: str = ""

    @property
    def remaining(self) -> int:
        return max(self.num - self.real_num, 0)


@dataclass(frozen=True)
class BuffQueueRow:
    """公开求购队列的一行。不带 user_id（见模块说明）。"""
    order_id: str
    price: Decimal
    num: int
    specific: tuple[Specific, ...]
    created_at: datetime | None

    @classmethod
    def from_api(cls, d: dict) -> "BuffQueueRow":
        return cls(order_id=str(d.get("id") or ""), price=_dec(d.get("price")) or Decimal(0), num=_int(d.get("num"), 1),
                   specific=_specifics(d.get("specific")), created_at=_ts(d.get("created_at")))


@dataclass(frozen=True)
class QueuePage:
    rows: list[BuffQueueRow]
    page_num: int
    page_size: int
    total_page: int


@dataclass(frozen=True)
class BuffBill:
    """一笔订单（购买记录或卖出记录）。`type`：2 求购成交、6 直接购买、1 出售（调研 §8.4、§9.1）。
    买家、卖家不进来。"""
    bill_id: str
    goods_id: int
    type: int
    state: str
    state_text: str
    price: Decimal
    fee: Decimal | None
    income: Decimal | None
    created_at: datetime | None
    transact_time: datetime | None
    assetid: str | None
    market_hash_name: str = ""
    name: str = ""

    @property
    def success(self) -> bool:
        return self.state == "SUCCESS"

    @property
    def failed(self) -> bool:
        return self.state == "FAIL"


@dataclass(frozen=True)
class BillPage:
    bills: list[BuffBill]
    page_num: int
    page_size: int


@dataclass(frozen=True)
class BuffAsset:
    assetid: str
    goods_id: int
    market_hash_name: str
    name: str
    is_renting: bool


@dataclass(frozen=True)
class InventoryPage:
    assets: list[BuffAsset]
    page_num: int
    page_size: int
    total_page: int


@dataclass(frozen=True)
class Me:
    """`wait_supply` 里能看到的我自己：用户号、昵称（只在添加账号时用来查重、显示）。"""
    uid: str | None
    nickname: str | None


def parse_buy_orders(data: dict) -> tuple[list[BuffBuyOrder], Me, int]:
    """一页 `wait_supply` → (单, 我, total_page)。"""
    goods = _goods(data)
    orders = []
    uid = None
    for d in data.get("items") or []:
        if not isinstance(d, dict):
            continue
        uid = uid or (str(d["user_id"]) if d.get("user_id") else None)
        g = goods.get(_int(d.get("goods_id")))
        price = _dec(d.get("price"))
        if price is None:
            continue
        orders.append(BuffBuyOrder(
            order_id=str(d.get("id") or ""), goods_id=_int(d.get("goods_id")), price=price, num=_int(d.get("num"), 1),
            real_num=_int(d.get("real_num")), specific=_specifics(d.get("specific")),
            buy_max_price=_dec(d.get("buy_max_price")) or (g.buy_max_price if g else None),
            created_at=_ts(d.get("created_at")), state=str(d.get("state") or ""),
            market_hash_name=g.market_hash_name if g else "", name=g.name if g else ""))
    infos = data.get("user_infos") or {}
    nick = None
    if uid and isinstance(infos.get(uid), dict):
        nick = infos[uid].get("nickname")
    elif len(infos) == 1:
        only = next(iter(infos.values()))
        if isinstance(only, dict):
            uid = uid or only.get("user_id")
            nick = only.get("nickname")
    return orders, Me(uid, nick), _int(data.get("total_page"), 1)


def parse_queue(data: dict) -> QueuePage:
    return QueuePage(rows=[BuffQueueRow.from_api(d) for d in data.get("items") or [] if isinstance(d, dict)],
                     page_num=_int(data.get("page_num"), 1), page_size=_int(data.get("page_size")),
                     total_page=_int(data.get("total_page"), 1))


def parse_bills(data: dict) -> BillPage:
    goods = _goods(data)
    bills = []
    for d in data.get("items") or []:
        if not isinstance(d, dict):
            continue
        gid = _int(d.get("goods_id"))
        g = goods.get(gid)
        asset = d.get("asset_info") if isinstance(d.get("asset_info"), dict) else {}
        bills.append(BuffBill(
            bill_id=str(d.get("id") or ""), goods_id=gid, type=_int(d.get("type")), state=str(d.get("state") or ""),
            state_text=str(d.get("state_text") or ""), price=_dec(d.get("price")) or Decimal(0),
            fee=_dec(d.get("fee")), income=_dec(d.get("income")), created_at=_ts(d.get("created_at")),
            transact_time=_ts(d.get("transact_time")), assetid=str(asset["assetid"]) if asset.get("assetid") else None,
            market_hash_name=g.market_hash_name if g else "", name=g.name if g else ""))
    return BillPage(bills=bills, page_num=_int(data.get("page_num"), 1), page_size=_int(data.get("page_size")))


def parse_inventory(data: dict) -> InventoryPage:
    assets = []
    for d in data.get("items") or []:
        if not isinstance(d, dict):
            continue
        aid = d.get("assetid") or (d.get("asset_info") or {}).get("assetid")
        if not aid:
            continue
        assets.append(BuffAsset(assetid=str(aid), goods_id=_int(d.get("goods_id")),
                                market_hash_name=str(d.get("market_hash_name") or ""), name=str(d.get("name") or ""),
                                is_renting=bool(d.get("is_renting"))))
    return InventoryPage(assets=assets, page_num=_int(data.get("page_num"), 1), page_size=_int(data.get("page_size")),
                         total_page=_int(data.get("total_page"), 1))
