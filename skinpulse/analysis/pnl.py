"""盈亏的纯逻辑（盈亏 spec §4、ADR-0024），无 IO。

- 先进先出：同账号、同平台、同模板分组，卖出只消耗**卖出之前**买入的批次；消不够的整笔记「成本未知」，
  不做部分配对（部分配对的盈亏没有意义，还会让一笔卖出一半算一半不算）。
- 已实现 = 配上了的卖出的（到手价 − 成本）。到手价优先用平台给的（`net_amount`），没有就按手续费估算、
  向下取整到分。提现费不扣。
- 持仓 = 没消掉的批次。在哪（库存 / 租出 / 去向不明）由 `allocate_locations` 按件数封顶分配。
- 浮动只算有市价的模板：没价的成本也不算，免得把「没价」当成「跌到 0」。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

CENT = Decimal("0.01")
ZERO = Decimal("0")

# 异常价只标不剔（spec §4.4）。2026-11-24 试算 258 个持仓模板：悠悠在售 < 3 件的 1 个（AUG | 变色龙
# 破损不堪，2 件在售）；悠悠 / BUFF 在售价比的第 5、95 百分位是 0.88、1.21，超出 1.5 倍的 3 个（都在 1 元以下）。
THIN_COUNT_DEFAULT = 3
SPREAD_RATIO_DEFAULT = 1.5

FLAG_THIN = "thin"
FLAG_SPREAD = "spread"
SRC_STEAMDT = "steamdt"
SRC_INVENTORY = "inventory"


def cents(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


def estimated_net(amount: Decimal, fee: float) -> Decimal:
    """按手续费估算卖出到手价，向下取整到分（实测悠悠就是这么算的：17.50 → 17.32，research §3）。"""
    return (amount * (Decimal(1) - Decimal(str(fee)))).quantize(CENT, rounding=ROUND_FLOOR)


@dataclass(frozen=True)
class Order:
    account_id: int
    platform: str
    order_no: str
    side: str                     # buy / sell
    template_id: int
    hash_name: str
    name: str
    qty: int
    amount: Decimal               # 成交总价（元）
    net_amount: Decimal | None    # 卖出：平台给的到手价；买入不用
    created_at: datetime

    @property
    def key(self) -> tuple[int, str, int]:
        return (self.account_id, self.platform, self.template_id)


@dataclass(frozen=True)
class LotUse:
    bought_at: datetime
    unit: Decimal
    qty: int
    account_id: int = 0
    platform: str = ""


@dataclass(frozen=True)
class SellMatch:
    order: Order
    net: Decimal
    net_exact: bool
    cost: Decimal | None          # None = 成本未知（不算盈亏）
    lots: tuple[LotUse, ...] = ()

    @property
    def pnl(self) -> Decimal | None:
        return None if self.cost is None else self.net - self.cost


@dataclass
class Position:
    account_id: int
    platform: str
    template_id: int
    hash_name: str
    name: str
    qty: int
    cost: Decimal
    first_bought_at: datetime

    @property
    def key(self) -> tuple[int, str, int]:
        return (self.account_id, self.platform, self.template_id)


@dataclass
class Ledger:
    positions: list[Position] = field(default_factory=list)
    sells: list[SellMatch] = field(default_factory=list)

    @property
    def realized_total(self) -> Decimal:
        return cents(sum((s.pnl for s in self.sells if s.pnl is not None), ZERO))

    def matched(self) -> list[SellMatch]:
        return [s for s in self.sells if s.cost is not None]

    def unmatched(self) -> list[SellMatch]:
        return [s for s in self.sells if s.cost is None]


@dataclass(eq=False)
class _Lot:
    bought_at: datetime
    unit: Decimal
    left: int
    order: Order


def build_ledger(orders: list[Order], fees: dict[str, float]) -> Ledger:
    """先进先出，跨号、跨平台（spec §4.1、ADR-0025）。

    - 按 `hash_name`（英文名）认同一件饰品：BUFF 对不上悠悠模板的订单 `template_id` 是负的 goods_id，按模板
      分组会配不上。`hash_name` 空的退回按 `template_id`，不和别的混。
    - 一笔卖出只能消耗**它之前**买入的批次：先消耗同号同平台的（按买入时间），不够再消耗其余的（按买入时间）。
      总量不够就整笔「成本未知」、一件也不消耗（部分配对的盈亏没有意义）。
    - 持仓按买入的号、平台、模板汇总剩余批次；已实现记在卖出订单的号上。
    `fees` 是各平台的卖出手续费率，只在没有平台给的到手价时用来估算。"""
    groups: dict[str, list[Order]] = defaultdict(list)
    for o in orders:
        groups[o.hash_name or f"#tid:{o.template_id}"].append(o)
    ledger = Ledger()
    for rows in groups.values():
        # 同一时刻先买后卖：同秒的买入算在卖出之前
        rows.sort(key=lambda o: (o.created_at, 0 if o.side == "buy" else 1, o.order_no))
        lots: list[_Lot] = []
        for o in rows:
            if o.side == "buy":
                lots.append(_Lot(o.created_at, o.amount / o.qty, o.qty, o))
                continue
            net = o.net_amount if o.net_amount is not None else estimated_net(o.amount, fees.get(o.platform, 0.0))
            exact = o.net_amount is not None
            if sum(lot.left for lot in lots) < o.qty:
                ledger.sells.append(SellMatch(o, net, exact, None))
                continue
            same = [lot for lot in lots if lot.order.account_id == o.account_id and lot.order.platform == o.platform]
            other = [lot for lot in lots if lot not in same]
            need, cost, used = o.qty, ZERO, []
            for lot in same + other:          # 各自已按买入时间排好（lots 是按时间追加的）
                if not need:
                    break
                if not lot.left:
                    continue
                k = min(need, lot.left)
                lot.left -= k
                need -= k
                cost += lot.unit * k
                used.append(LotUse(lot.bought_at, cents(lot.unit), k, lot.order.account_id, lot.order.platform))
            ledger.sells.append(SellMatch(o, net, exact, cents(cost), tuple(used)))
        held: dict[tuple[int, str, int], list[_Lot]] = defaultdict(list)
        for lot in lots:
            if lot.left:
                held[lot.order.key].append(lot)
        for key, open_lots in held.items():
            last = open_lots[-1].order
            ledger.positions.append(Position(
                account_id=key[0], platform=key[1], template_id=key[2], hash_name=last.hash_name, name=last.name,
                qty=sum(lot.left for lot in open_lots),
                cost=cents(sum((lot.unit * lot.left for lot in open_lots), ZERO)),
                first_bought_at=min(lot.bought_at for lot in open_lots)))
    ledger.sells.sort(key=lambda s: s.order.created_at)
    ledger.positions.sort(key=lambda p: p.key)
    return ledger


def month_key(dt: datetime, tz: str) -> str:
    return dt.astimezone(ZoneInfo(tz)).strftime("%Y-%m")


def monthly_realized(sells: list[SellMatch], tz: str, until: date) -> list[tuple[str, Decimal]]:
    """每月已实现，从第一笔配上的卖出那个月到 `until` 那个月，没有卖出的月份是 0（不是缺数据）。"""
    sums: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for s in sells:
        if s.pnl is not None:
            sums[month_key(s.order.created_at, tz)] += s.pnl
    if not sums:
        return []
    y, m = map(int, min(sums).split("-"))
    end = (until.year, until.month)
    out = []
    while (y, m) <= end:
        k = f"{y:04d}-{m:02d}"
        out.append((k, cents(sums.get(k, ZERO))))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def item_key(hash_name: str | None, template_id: int) -> str:
    """认同一件饰品的键：英文名；没有英文名才退回模板 ID（不和别的混）。"""
    return hash_name or f"#tid:{template_id}"


def allocate_locations(positions: list[Position], inv_by_item: Counter, lease_by_item: Counter
                       ) -> dict[tuple[int, str, int], tuple[int, int, int]]:
    """持仓在哪（spec §4.3）：先分库存，再分租出，都按 `item_key` 计数、封顶到持有件数，剩下的是不在库存。
    库存、租出是全部号合起来的（悠悠、BUFF 看到的是同一个 Steam 库存，调用方已按 assetid 去重）。
    返回持仓键 (号, 平台, 模板) → (库存, 租出, 不在库存)。不改传进来的计数。"""
    inv, lease = Counter(inv_by_item), Counter(lease_by_item)
    out: dict[tuple[int, str, int], tuple[int, int, int]] = {}
    for p in positions:
        k = item_key(p.hash_name, p.template_id)
        a = min(p.qty, inv.get(k, 0))
        inv[k] -= a
        b = min(p.qty - a, lease.get(k, 0))
        lease[k] -= b
        out[p.key] = (a, b, p.qty - a - b)
    return out


def pick_price(youpin_sell: Decimal | None, youpin_count: int | None, buff_sell: Decimal | None,
               inventory_price: Decimal | None, *, thin_count: int = THIN_COUNT_DEFAULT,
               spread_ratio: float = SPREAD_RATIO_DEFAULT) -> tuple[Decimal | None, str | None, str | None]:
    """单件市价（spec §4.4）：23:50 快照里 SteamDT 给的悠悠在售最低；没有就用悠悠库存接口的市价；再没有为 None。
    返回 (价, 来源, 标记)。标记只对 SteamDT 价算（库存价没有在售件数和 BUFF 价可比）。"""
    if youpin_sell is not None and youpin_sell > 0:
        flag = None
        if youpin_count is not None and youpin_count < thin_count:
            flag = FLAG_THIN
        elif buff_sell is not None and buff_sell > 0:
            r = youpin_sell / buff_sell
            if r > Decimal(str(spread_ratio)) or r < 1 / Decimal(str(spread_ratio)):
                flag = FLAG_SPREAD
        return youpin_sell, SRC_STEAMDT, flag
    if inventory_price is not None and inventory_price > 0:
        return inventory_price, SRC_INVENTORY, None
    return None, None, None


def net_factor(undercut: float, fee: float) -> Decimal:
    """到手价系数：市价 ×（1 − 让价）×（1 − 手续费），不扣提现费（ADR-0024）。"""
    return (Decimal(1) - Decimal(str(undercut))) * (Decimal(1) - Decimal(str(fee)))


@dataclass(frozen=True)
class HoldingValue:
    qty: int
    cost: Decimal
    cost_priced: Decimal
    value_mkt: Decimal
    value_net: Decimal
    unpriced_qty: int

    @property
    def float_mkt(self) -> Decimal:
        return self.value_mkt - self.cost_priced

    @property
    def float_net(self) -> Decimal:
        return self.value_net - self.cost_priced


def holding_value(items: list[tuple[int, Decimal, Decimal | None, Decimal]]) -> HoldingValue:
    """items: (件数, 成本, 单价或 None, 到手系数)。没价的件数和成本只进 `qty` / `cost`，不进浮动。"""
    qty = unpriced = 0
    cost = cost_priced = mkt = net = ZERO
    for q, c, price, factor in items:
        qty += q
        cost += c
        if price is None:
            unpriced += q
            continue
        cost_priced += c
        mkt += price * q
        net += price * q * factor
    return HoldingValue(qty, cents(cost), cents(cost_priced), cents(mkt), cents(net), unpriced)
