"""给盈亏统计导入 BUFF 的买卖订单和库存（BUFF spec §9，盈亏 ADR-0025）。

只读：`buy_order/history`、`sell_order/history`、`steam_inventory?force=0`。只存 SUCCESS 的订单：
- 卖出：`net_amount = income`（平台给的到手价，`net_exact = True`）；
- 买入（type 2 求购成交、6 直接购买）：`net_amount = amount`。
`hash_name` 必填（跨平台配对按它认同一件饰品），`created_at` 是建单时间、`finished_at` 是成交时间。
停翻规则和悠悠一样：不满一页、或（日常同步时）整页都是已入库的单才停；首次导入翻到空页。
配对、持仓怎么算由盈亏那边定；这里不碰 `analysis/pnl.py`。对手的身份不入库。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from skinpulse.db.models import TradeOrder
from skinpulse.services.buff import goods_to_template

log = logging.getLogger(__name__)

PLATFORM = "BUFF"          # trade_order.platform（盈亏那边悠悠是 "YOUPIN"）
PAGE = 100
BUY_TYPES = frozenset({2, 6})


def sync_buff_trades(session: Session, account_id: int, client, *, max_pages: int, now: datetime,
                     import_all: bool = False) -> tuple[int, int, int]:
    """返回 (新买入, 新卖出, 翻了几页)。"""
    new = {"buy": 0, "sell": 0}
    pages = 0
    for side, fetch in (("buy", client.buy_history), ("sell", client.sell_history)):
        for page in range(1, max_pages + 1):
            bills = fetch(page, PAGE).bills
            pages += 1
            nos = [b.bill_id for b in bills if b.bill_id]
            known = set(session.scalars(select(TradeOrder.order_no).where(
                TradeOrder.platform == PLATFORM, TradeOrder.order_no.in_(nos)))) if nos else set()
            all_known = bool(nos) and len(known) == len(set(nos))
            for b in bills:
                if not b.bill_id or b.bill_id in known or not b.success or b.created_at is None:
                    continue
                if side == "buy" and b.type not in BUY_TYPES:
                    continue
                if not b.market_hash_name:
                    log.warning("buff pnl: %s bill without market_hash_name skipped", side)
                    continue
                tid = goods_to_template(session, b.goods_id, b.market_hash_name, b.name)
                exact = side == "buy" or b.income is not None
                net = b.price if side == "buy" else (b.income if b.income is not None else None)
                session.add(TradeOrder(account_id=account_id, platform=PLATFORM, order_no=b.bill_id, side=side,
                                       template_id=tid, hash_name=b.market_hash_name, name=b.name or b.market_hash_name,
                                       qty=1, amount=b.price, net_amount=net, net_exact=exact, detail_checked=True,
                                       sub_type=b.type, asset_id=b.assetid, created_at=b.created_at,
                                       finished_at=b.transact_time, fetched_at=now))
                known.add(b.bill_id)
                new[side] += 1
            session.commit()
            if not bills or len(bills) < PAGE or (all_known and not import_all):
                break
    return new["buy"], new["sell"], pages


@dataclass(frozen=True)
class BuffHolding:
    hash_name: str
    template_id: int
    assetid: str


def fetch_buff_holdings(session: Session, client, *, max_pages: int = 10) -> list[BuffHolding]:
    """BUFF 库存（就是绑定的 Steam 号的库存）。盈亏那边决定怎么并进持仓；悠悠号绑的是同一个 Steam 号时，
    两边是同一批货，按 assetid 去重（BUFF spec §9）。"""
    out: list[BuffHolding] = []
    for page in range(1, max_pages + 1):
        inv = client.inventory(page, 500)
        for a in inv.assets:
            out.append(BuffHolding(a.market_hash_name, goods_to_template(session, a.goods_id, a.market_hash_name),
                                   a.assetid))
        if len(inv.assets) < 500 or page >= inv.total_page:
            break
    return out
