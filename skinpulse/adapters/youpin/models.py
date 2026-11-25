from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


def _dec(v) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        # A field the caller expected to be numeric came back as unparseable garbage.
        # Treat it the same as "missing" rather than raising - one malformed row's
        # price must not crash parsing for the rest of the (possibly hundreds of) rows.
        return None


@dataclass(frozen=True)
class PublicPurchaseOrder:
    purchase_no: str
    user_id: int
    user_name: str
    price: Decimal
    remaining_qty: int
    abrade_text: str | None
    type_id: int
    special_style: str | None
    fade_text: str | None
    is_rank_first: bool | None
    template_id: int

    @classmethod
    def from_api(cls, d: dict) -> "PublicPurchaseOrder":
        irf = d.get("isRankFirst")
        return cls(
            purchase_no=str(d.get("purchaseNo", "")),
            user_id=int(d.get("userId") or 0),
            user_name=str(d.get("userName") or ""),
            price=_dec(d.get("purchasePrice")) or Decimal("0"),
            remaining_qty=int(d.get("surplusQuantity") or 0),
            abrade_text=d.get("abradeText"),
            type_id=int(d.get("typeId") if d.get("typeId") is not None else -1),
            special_style=d.get("specialStyle"),
            fade_text=d.get("fadeText"),
            is_rank_first=None if irf is None else bool(irf),
            template_id=int(d.get("templateId") or 0),
        )


@dataclass(frozen=True)
class OnSaleItem:
    commodity_no: str
    user_id: int
    price: Decimal
    publish_time: str
    have_sticker: bool

    @classmethod
    def from_api(cls, d: dict) -> "OnSaleItem":
        return cls(
            commodity_no=str(d.get("commodityNo") or d.get("id") or ""),
            user_id=int(d.get("userId") or 0),
            price=_dec(d.get("price")) or Decimal("0"),
            publish_time=str(d.get("publishTime") or ""),
            have_sticker=bool(d.get("haveSticker")),
        )


@dataclass(frozen=True)
class TemplateDetail:
    template_id: int
    commodity_name: str
    hash_name: str
    steam_price: Decimal | None

    @classmethod
    def from_api(cls, d: dict) -> "TemplateDetail":
        t = d.get("templateInfo") or d
        return cls(int(t.get("id") or 0), str(t.get("commodityName") or ""),
                   str(t.get("commodityHashName") or ""), _dec(t.get("steamPrice")))


@dataclass(frozen=True)
class UserInfo:
    user_id: int
    nickname: str


@dataclass(frozen=True)
class MyPurchaseOrder:
    """searchPurchaseOrderList (my-orders) 的字段名已于 2026-08-30 用一个有 371 条真实求购单
    的账号核对过真实响应，见 from_api：确认过的键名排在每个候选列表最前面。响应信封确认为
    {"code":0,"msg":...,"timestamp":...,"data":[...]}（data 是裸数组，不是嵌套对象）。
    templateHashName 和 typeId 这两个字段在这个接口的响应里从未出现过（typeId 只在公开求购
    队列 getTemplatePurchaseOrderListPC 里有），分别兜底成 "" 和 -1；候选名仍然保留在列表里
    （而不是直接硬编码默认值），因为接口没有文档，字段随时可能变化。"""
    purchase_no: str
    template_id: int
    template_hash_name: str
    commodity_name: str
    price: Decimal
    qty: int
    remaining_qty: int
    abrade_text: str | None
    type_id: int
    special_style: str | None
    fade_text: str | None
    raw: dict = field(default_factory=dict, compare=False)

    @classmethod
    def from_api(cls, d: dict) -> "MyPurchaseOrder":
        """确认过的键名排在每个候选列表最前面；旧的猜测名仍留作候补容错。unitPrice 是不带
        货币符号的裸数字字符串（如 "24"，区别于 maxPurchasePrice 那种带 "¥" 前缀的展示字符串）。
        remaining_qty 这个接口不直接给，是 quantity（意向总数）减 buyQuantity（已成交数量）
        算出来的，下限钳到 0。"""
        def pick(*keys, default=None):
            for k in keys:
                if k in d and d[k] is not None:
                    return d[k]
            return default
        qty = int(pick("quantity", "purchaseNum", "num", default=1))
        bought = int(pick("buyQuantity", default=0))
        return cls(
            purchase_no=str(pick("orderNo", "purchaseNo", "purchaseOrderNo", default="")),
            template_id=int(pick("templateId", default=0)),
            template_hash_name=str(pick("templateHashName", "commodityHashName", default="")),
            commodity_name=str(pick("commodityName", "templateName", default="")),
            price=_dec(pick("unitPrice", "purchasePrice", "price")) or Decimal("0"),
            qty=qty,
            remaining_qty=max(qty - bought, 0),
            abrade_text=pick("abradeText", "abradeRange"),
            type_id=int(pick("typeId", default=-1)),
            special_style=pick("styleSpecial", "specialStyle", "specialStyleName"),
            fade_text=pick("fadeText", "fadeRange"),
            raw=d,
        )


# ---------- 盈亏统计（盈亏 spec §2，research 2026-11-24）：买卖订单、库存、租出。只读 ----------
#
# 这几个响应里有交易对手的昵称、头像、Steam ID、报价号：这里一律不取，也不保留 raw。

_CENTS = Decimal("100")


def _ms_utc(v) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc) if v not in (None, "") else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _money(v) -> Decimal | None:
    """元的字符串（「142.00」）；空串、0 都算没有（坑 3：价格 0 = 没有数据）。"""
    d = _dec(v)
    return d if d is not None and d > 0 else None


@dataclass(frozen=True)
class TradeOrder:
    """买入 / 卖出订单列表的一行（`trade/sale/v1/{buy,sell}/list`）。金额字段是**分**，这里转成元。"""

    order_no: str
    side: str
    order_status: int | None
    sub_type: int | None
    template_id: int
    hash_name: str
    name: str
    qty: int
    amount: Decimal | None
    asset_id: str | None
    created_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_api(cls, d: dict, side: str) -> "TradeOrder":
        p = d.get("productDetail") or {}
        cents = _dec(d.get("totalAmount"))
        aid = p.get("assertId")
        return cls(
            order_no=str(d.get("orderNo") or ""),
            side=side,
            order_status=_int_or_none(d.get("orderStatus")),
            sub_type=_int_or_none(d.get("orderSubType")),
            template_id=int(p.get("commodityTemplateId") or 0),
            hash_name=str(p.get("commodityHashName") or ""),
            name=str(p.get("commodityName") or ""),
            qty=max(1, int(d.get("commodityNum") or 1)),
            amount=None if cents is None else cents / _CENTS,
            asset_id=None if aid in (None, "") else str(aid),
            created_at=_ms_utc(d.get("createOrderTime")),
            finished_at=_ms_utc(d.get("finishOrderTime")),
        )


def _int_or_none(v) -> int | None:
    try:
        return None if v in (None, "") else int(v)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class TradeOrderDetail:
    """订单详情（`trade/v1/order/query/detail`）里盈亏要的两个字段：卖出的到手价 `revenue.amount`（元，
    已扣手续费），买入的 `leaseNo`（名字很怪，装的是求购单号）。"""

    revenue: Decimal | None
    purchase_no: str | None

    @classmethod
    def from_api(cls, d: dict) -> "TradeOrderDetail":
        rev = d.get("revenue") if isinstance(d, dict) else None
        lease_no = d.get("leaseNo") if isinstance(d, dict) else None
        return cls(revenue=_money(rev.get("amount")) if isinstance(rev, dict) else None,
                   purchase_no=str(lease_no) if lease_no else None)


@dataclass(frozen=True)
class InventoryItem:
    steam_asset_id: str
    template_id: int
    hash_name: str
    market_price: Decimal | None
    buy_price: Decimal | None

    @classmethod
    def from_api(cls, d: dict) -> "InventoryItem":
        return cls(steam_asset_id=str(d.get("steamAssetId") or ""), template_id=int(d.get("templateId") or 0),
                   hash_name=str(d.get("templateHashName") or ""), market_price=_money(d.get("marketPrice")),
                   buy_price=_money(d.get("assetBuyPrice")))


@dataclass(frozen=True)
class Inventory:
    """`commodity/user/inventory/price/trend` 一次全拿（pageSize=1000）。`pnl_total` 是悠悠自己算的浮动盈亏
    （只算有买入价的那些件，按市价），页面上做对照。"""

    items: list[InventoryItem]
    total_count: int | None
    pnl_total: Decimal | None
    buy_total: Decimal | None

    @classmethod
    def from_api(cls, d: dict) -> "Inventory":
        d = d or {}
        rows = d.get("itemsInfos") or []
        return cls(items=[InventoryItem.from_api(x) for x in rows], total_count=_int_or_none(d.get("totalCount")),
                   pnl_total=_dec(d.get("profitAndLossTotal")), buy_total=_dec(d.get("buyPriceTotal")))


_RENT_RE = re.compile(r"(\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class LeaseOutOrder:
    """租出中的一单。列表里没有 templateId，只有英文名（`commodityHashName`），对账靠它。"""

    order_no: str
    hash_name: str
    rent_per_day: Decimal | None

    @classmethod
    def from_api(cls, d: dict) -> "LeaseOutOrder":
        info = d.get("commodityInfo") or {}
        m = _RENT_RE.search(str(d.get("leaseAmountDesc") or ""))
        return cls(order_no=str(d.get("orderNo") or d.get("orderId") or ""),
                   hash_name=str(info.get("commodityHashName") or ""),
                   rent_per_day=_dec(m.group(1)) if m else None)


@dataclass(frozen=True)
class LeaseOutPage:
    rows: list[LeaseOutOrder]
    has_next: bool
    total: int | None

    @classmethod
    def from_api(cls, d: dict) -> "LeaseOutPage":
        d = d or {}
        return cls(rows=[LeaseOutOrder.from_api(x) for x in d.get("orderDataList") or []],
                   has_next=bool(d.get("hasNext")), total=_int_or_none(d.get("totalCount")))
