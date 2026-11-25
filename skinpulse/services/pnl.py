"""盈亏的每日任务（盈亏 spec §5）：同步悠悠买卖订单、补卖出到手价、拉库存和租出，算一份持仓快照。

- 对悠悠只读，只用 adapters/youpin/client.py 里的几个查询方法。
- 悠悠请求那一段持有 `rt.youpin_lock`（监控整轮、心跳、看板同步、暂停单检查都持有它）；计算、写库不持有。
- 任何错误只影响这个任务：风控类按同步的规矩暂停账号通道（`Monitor.pause_after_error`），日志只记类型名。
- 对手的昵称、Steam ID、报价号从不经过这里（适配器解析时就丢了）。
"""
from __future__ import annotations

import contextlib
import logging
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from skinpulse.adapters.youpin.client import TRADE_DONE, TRADE_PAGE_SIZE
from skinpulse.adapters.youpin.errors import LoginRequired, MachineRoomBlocked, RateLimited, RiskControl, YoupinError
from skinpulse.adapters.youpin.models import Inventory, LeaseOutOrder, TradeOrder as ApiTrade
from skinpulse.analysis.pnl import (
    ZERO, Order, allocate_locations, build_ledger, cents, estimated_net, holding_value, item_key, net_factor,
    pick_price,
)
from skinpulse.db.models import Item, PausedCheckRun, PickRun, PnlDaily, PnlPosition, PriceDaily, TradeOrder
from skinpulse.services import status as st
from skinpulse.services.market import wait_batch_slot

log = logging.getLogger(__name__)

PLATFORM = "YOUPIN"
IMPORT_MAX_PAGES = 200
LEASE_MAX_PAGES = 30        # 每页 20：600 件租出
INVENTORY_MAX_PAGES = 10    # 每页 1000
PRICE_LOOKBACK_DAYS = 7     # D 那天的 23:50 快照缺了（睡着），往前找最近一天的


@dataclass
class HoldingsState:
    inventory: Inventory
    leases: list[LeaseOutOrder]


@dataclass
class PnlReport:
    day: str
    accounts: int = 0
    new_buys: int = 0
    new_sells: int = 0
    details: int = 0
    pages: int = 0
    snapshots: int = 0
    skipped: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (f"pnl {self.day}: accounts={self.accounts} snapshots={self.snapshots} new_buys={self.new_buys} "
                f"new_sells={self.new_sells} details={self.details} pages={self.pages}"
                + (f" skipped={','.join(self.skipped)}" if self.skipped else ""))


# ---------- 悠悠请求 ----------

def _upsert_item(session: Session, t: ApiTrade) -> None:
    """持仓模板也要进 23:50 价格快照，而快照靠 `Item` 把 templateId 映射成英文名（`services.market.resolve_names`）。
    没有就插一行；有但没填 templateId 就补上。不覆盖已有的值。"""
    if not t.hash_name or not t.template_id:
        return
    row = session.scalar(select(Item).where(Item.hash_name == t.hash_name))
    if row is None:
        session.add(Item(hash_name=t.hash_name, cn_name=t.name or None, youpin_template_id=t.template_id))
    elif row.youpin_template_id is None:
        row.youpin_template_id = t.template_id


def sync_trades(session: Session, account_id: int, client, *, fees: dict[str, float], max_pages: int,
                now: datetime, import_all: bool = False) -> tuple[int, int, int]:
    """买入、卖出两个列表各从第 1 页往后翻（按下单时间倒序），只存「已完成」。返回 (新买入, 新卖出, 翻了几页)。

    停的条件：不满一页、到 `max_pages`，或者（每日同步时）**整页都是已入库的单**。不能「碰到一个已知单就停」：
    有的单完成得晚（卖出有过 2～3 天的），会排在已知单后面；首次导入中途断了，下次也得接着翻到底。
    首次导入（`import_all`）不看已知单，一直翻到不满一页。"""
    new = {"buy": 0, "sell": 0}
    pages = 0
    for side in ("buy", "sell"):
        for page in range(1, max_pages + 1):
            if pages:
                client.sleep(client.s.page_sleep_s)
            rows = client.search_trade_orders_page(side, page)
            pages += 1
            nos = [r.order_no for r in rows if r.order_no]
            known = set(session.scalars(select(TradeOrder.order_no).where(
                TradeOrder.platform == PLATFORM, TradeOrder.order_no.in_(nos)))) if nos else set()
            all_known = bool(nos) and len(known) == len(set(nos))
            for r in rows:
                if r.order_no in known or not r.order_no:
                    continue
                if r.order_status != TRADE_DONE or r.amount is None or r.created_at is None or not r.template_id:
                    log.debug("pnl: skipping %s order (status=%s)", side, r.order_status)
                    continue
                net = r.amount if side == "buy" else estimated_net(r.amount, fees.get(PLATFORM, 0.0))
                session.add(TradeOrder(account_id=account_id, platform=PLATFORM, order_no=r.order_no, side=side,
                                       template_id=r.template_id, hash_name=r.hash_name, name=r.name, qty=r.qty,
                                       amount=r.amount, net_amount=net, net_exact=side == "buy",
                                       sub_type=r.sub_type, asset_id=r.asset_id, created_at=r.created_at,
                                       finished_at=r.finished_at, fetched_at=now))
                known.add(r.order_no)
                _upsert_item(session, r)
                new[side] += 1
            session.commit()
            if len(rows) < TRADE_PAGE_SIZE or (all_known and not import_all):
                break
    return new["buy"], new["sell"], pages


def fill_sell_details(session: Session, account_id: int, client, *, limit: int) -> int:
    """卖出到手价：`net_exact = false` 的卖出按时间倒序补 `limit` 张（订单详情 `revenue.amount`）。拿不到的
    保留估算值，下次再补。返回补上了几张。"""
    rows = list(session.scalars(select(TradeOrder).where(
        TradeOrder.account_id == account_id, TradeOrder.platform == PLATFORM, TradeOrder.side == "sell",
        TradeOrder.net_exact.is_(False), TradeOrder.detail_checked.is_(False))
        .order_by(TradeOrder.created_at.desc()).limit(limit))) if limit else []
    if not rows:
        return 0
    try:
        user_id = client.get_user_info().user_id
    except (LoginRequired, RiskControl, RateLimited, MachineRoomBlocked):
        raise
    except YoupinError as e:      # 取不到自己的悠悠 ID 只是这一步做不了：保留估算值，快照照写
        log.warning("pnl: user info for sell details failed (%s); keeping estimates", type(e).__name__)
        return 0
    done = 0
    for r in rows:
        client.sleep(client.s.page_sleep_s)
        d = client.get_trade_order_detail(r.order_no, user_id)
        r.detail_checked = True   # 查过一次就不再查：详情里没有到手价的单不会每天占着名额
        if d.revenue is not None:
            r.net_amount, r.net_exact = d.revenue, True
            done += 1
        session.commit()
    return done


def fetch_holdings_state(client) -> HoldingsState:
    """库存（每页 1000，按 totalCount 翻页，平时 1 次），租出翻到 hasNext=false。翻到上限还没完就记 WARNING：
    少拿的那些会被算成「去向不明」。"""
    client.sleep(client.s.page_sleep_s)
    inv = client.get_inventory_trend()
    items = list(inv.items)
    page = 1
    while inv.total_count and len(items) < inv.total_count and page < INVENTORY_MAX_PAGES:
        page += 1
        client.sleep(client.s.page_sleep_s)
        more = client.get_inventory_trend(page)
        if not more.items:
            break
        items.extend(more.items)
    if inv.total_count and len(items) < inv.total_count:
        log.warning("pnl: inventory has %d items, got %d", inv.total_count, len(items))
    inv = Inventory(items, inv.total_count, inv.pnl_total, inv.buy_total)
    leases: list[LeaseOutOrder] = []
    for page in range(1, LEASE_MAX_PAGES + 1):
        client.sleep(client.s.page_sleep_s)
        p = client.search_lease_out_page(page)
        leases.extend(p.rows)
        if not p.has_next or not p.rows:
            break
    else:
        log.warning("pnl: lease list still has more after %d pages (%d rows read)", LEASE_MAX_PAGES, len(leases))
    return HoldingsState(inv, leases)


# ---------- 计算、写库 ----------

def _local_end(day: date, tz: str) -> datetime:
    return datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=ZoneInfo(tz))


def load_orders(session: Session, account_id: int | None = None, until: datetime | None = None,
                platform: str | None = None) -> list[Order]:
    """`trade_order` → 配对用的 `Order`。默认全部号、全部平台（ADR-0025 跨平台配对）。"""
    q = select(TradeOrder)
    if platform is not None:
        q = q.where(TradeOrder.platform == platform)
    if account_id is not None:
        q = q.where(TradeOrder.account_id == account_id)
    if until is not None:
        q = q.where(TradeOrder.created_at < until)
    return [Order(r.account_id, r.platform, r.order_no, r.side, r.template_id, r.hash_name, r.name, r.qty, r.amount,
                  r.net_amount if r.side == "sell" else None, r.created_at) for r in session.scalars(q)]


def _price_rows(session: Session, tids: set[int], day: date) -> dict[int, dict[str, PriceDaily]]:
    """每个模板 D 当天（缺了就往前最多 7 天里最近一天）的 price_daily，按平台。只认正的模板 ID。"""
    tids = {t for t in tids if t > 0}
    lo = (day - timedelta(days=PRICE_LOOKBACK_DAYS)).isoformat()
    out: dict[int, dict[str, PriceDaily]] = {}
    best: dict[int, str] = {}
    if not tids:
        return out
    for r in session.scalars(select(PriceDaily).where(PriceDaily.template_id.in_(tids), PriceDaily.date >= lo,
                                                       PriceDaily.date <= day.isoformat())):
        if r.date > best.get(r.template_id, ""):
            best[r.template_id] = r.date
            out[r.template_id] = {}
        if r.date == best[r.template_id]:
            out[r.template_id][r.platform] = r
    return out


@dataclass(frozen=True)
class LivePrice:
    """首次导入时现查的 SteamDT 价（不写 price_daily），字段同 price_daily 的悠悠、BUFF 两行。"""
    youpin_sell: Decimal | None
    youpin_count: int | None
    buff_sell: Decimal | None


def live_prices_for_unpriced(session: Session, steamdt, settings, day: date,
                             sleep=time.sleep) -> dict[str, LivePrice]:
    """首次导入（spec §5.3）：持仓饰品还没进过 23:50 的价格快照，直接用 SteamDT 批量价补上，**不写 price_daily**：
    写了的话 23:50 那次会把这些模板当成「今天已有」跳过，存下的就是早上的价。批量 1 次/分钟，和别的任务共用
    （`wait_batch_slot`）。按英文名返回（BUFF 独有的饰品没有悠悠模板 ID）。"""
    ledger = build_ledger(load_orders(session, None, _local_end(day, settings.timezone)), settings.valuation.fees)
    have = _price_rows(session, {p.template_id for p in ledger.positions}, day)
    names = sorted({p.hash_name for p in ledger.positions if p.hash_name and p.template_id not in have})
    out: dict[str, LivePrice] = {}
    for i in range(0, len(names), 100):
        wait_batch_slot(session, sleep)     # 和价格快照、选品、暂停单检查共用 1 次/分钟
        try:
            items = steamdt.get_prices_batch(names[i:i + 100])
        except Exception as e:      # SteamDT 出错只少几个价，不影响导入；异常消息里可能有 URL，只记类型名
            log.warning("pnl: live price batch failed (%s)", type(e).__name__)
            continue
        for it in items:
            yp, bf = it.of("YOUPIN"), it.of("BUFF")
            out[it.market_hash_name] = LivePrice(yp.sell_price if yp else None, yp.sell_count if yp else None,
                                                 bf.sell_price if bf else None)
    return out


def write_snapshots(session: Session, settings, day: date, youpin: dict[int, HoldingsState],
                    buff: dict[int, list], account_ids: list[int], now: datetime,
                    live: dict[str, LivePrice] | None = None) -> dict[int, PnlDaily]:
    """D 这一天、每个号的 pnl_position + pnl_daily（同一天重跑覆盖）。一本账算全部号、全部平台（ADR-0025）：
    持仓记在买入的号上，已实现记在卖出的号上。订单只算到 D 结束（本地）为止。

    - 库存：悠悠库存和 BUFF 库存看到的是同一个 Steam 库存，按 assetid 合并去重；租出：悠悠租出列表。
    - 悠悠对照数、租金、没有买入记录的库存只记在悠悠号上（没有买入记录的那部分记在 id 最小的悠悠号上）。"""
    tz = settings.timezone
    pnl = settings.pnl
    fees = settings.valuation.fees
    end = _local_end(day, tz)
    start = end - timedelta(days=1)
    ledger = build_ledger(load_orders(session, None, end), fees)

    assets: dict[str, str] = {}                       # assetid → item_key
    no_asset: list[str] = []                          # 没有 assetid 的（不去重）
    market: dict[str, list[Decimal]] = {}
    # 悠悠库存偶尔不带英文名：按模板 ID 去 Item 里补，免得同一件在 BUFF 那边按英文名、这边按 #tid 记成两样
    missing = {i.template_id for st_ in youpin.values() for i in st_.inventory.items if not i.hash_name and i.template_id}
    names = dict(session.execute(select(Item.youpin_template_id, Item.hash_name)
                                 .where(Item.youpin_template_id.in_(missing))).all()) if missing else {}
    for st_ in youpin.values():
        for i in st_.inventory.items:
            k = item_key(i.hash_name or names.get(i.template_id), i.template_id)
            if i.steam_asset_id:
                assets[i.steam_asset_id] = k
            else:
                no_asset.append(k)
            if i.market_price is not None:
                market.setdefault(k, []).append(i.market_price)
    for holdings in buff.values():
        for h in holdings:
            k = item_key(h.hash_name, h.template_id)
            if h.assetid:
                assets.setdefault(str(h.assetid), k)
            else:
                no_asset.append(k)
    inv_by_item = Counter(assets.values()) + Counter(no_asset)
    lease_by_item = Counter(x.hash_name for st_ in youpin.values() for x in st_.leases)
    # 只给要写的号分配库存（停用的号的持仓不写，也不该占掉别的号的库存）；它们的订单照样参与配对
    positions = [p for p in ledger.positions if p.account_id in account_ids]
    loc = allocate_locations(positions, inv_by_item, lease_by_item)
    inv_price = {k: Decimal(str(statistics.median(ps))) for k, ps in market.items()}
    prices = _price_rows(session, {p.template_id for p in ledger.positions}, day)

    key = day.isoformat()
    session.execute(delete(PnlPosition).where(PnlPosition.date == key, PnlPosition.account_id.in_(account_ids)))
    session.execute(delete(PnlDaily).where(PnlDaily.date == key, PnlDaily.account_id.in_(account_ids)))
    per_acc: dict[int, dict] = {a: {"items": [], "totals": Counter(), "unknown_cost": ZERO} for a in account_ids}
    for p in positions:
        acc = per_acc[p.account_id]
        k = item_key(p.hash_name, p.template_id)
        rows = prices.get(p.template_id, {})
        yp, bf = rows.get("YOUPIN"), rows.get("BUFF")
        lv = (live or {}).get(p.hash_name) if not rows else None
        if lv is not None:
            ys, yc, bs = lv.youpin_sell, lv.youpin_count, lv.buff_sell
        else:
            ys, yc, bs = (yp.sell_price if yp else None), (yp.sell_count if yp else None), (bf.sell_price if bf else None)
        price, src, flag = pick_price(ys, yc, bs, inv_price.get(k),
                                      thin_count=pnl.thin_count, spread_ratio=pnl.spread_ratio)
        a, b, c = loc[p.key]
        acc["totals"].update(inv=a, lease=b, unknown=c)
        if c:
            acc["unknown_cost"] += p.cost * c / p.qty
        acc["items"].append((p.qty, p.cost, price, net_factor(settings.valuation.undercut, fees.get(p.platform, 0.0))))
        session.add(PnlPosition(date=key, account_id=p.account_id, platform=p.platform, template_id=p.template_id,
                                hash_name=p.hash_name, name=p.name, qty=p.qty, cost=p.cost,
                                first_bought_at=p.first_bought_at, inv_qty=a, lease_qty=b, unknown_qty=c,
                                price=price, price_src=src, price_flag=flag))
    # 库存里有、账上没有的（别处来的）：件数和市价一行带过，记在 id 最小的悠悠号上
    alloc = Counter()
    for p in positions:
        alloc[item_key(p.hash_name, p.template_id)] += loc[p.key][0]
    unbooked_qty, unbooked_val = 0, ZERO
    for k, n in inv_by_item.items():
        extra = n - alloc.get(k, 0)
        if extra > 0:
            unbooked_qty += extra
            if k in inv_price:
                unbooked_val += inv_price[k] * extra
    unbooked_owner = min(youpin) if youpin else None

    out: dict[int, PnlDaily] = {}
    for aid in account_ids:
        acc = per_acc[aid]
        hv = holding_value(acc["items"])
        mine = [x for x in ledger.sells if x.order.account_id == aid and x.pnl is not None]
        realized = cents(sum((x.pnl for x in mine), ZERO))
        realized_day = cents(sum((x.pnl for x in mine if x.order.created_at >= start), ZERO))
        st_ = youpin.get(aid)
        rents = [x.rent_per_day for x in st_.leases if x.rent_per_day is not None] if st_ else []
        daily = PnlDaily(
            date=key, account_id=aid, realized_total=realized, realized_day=realized_day,
            qty=hv.qty, cost=hv.cost, value_mkt=hv.value_mkt, value_net=hv.value_net, cost_priced=hv.cost_priced,
            unpriced_qty=hv.unpriced_qty, inv_qty=acc["totals"]["inv"], lease_qty=acc["totals"]["lease"],
            unknown_qty=acc["totals"]["unknown"], unknown_cost=cents(acc["unknown_cost"]),
            youpin_pnl=st_.inventory.pnl_total if st_ else None,
            youpin_pnl_n=sum(1 for i in st_.inventory.items if i.buy_price is not None) if st_ else None,
            lease_count=len(st_.leases) if st_ else 0,
            lease_rent_day=cents(sum(rents, ZERO)) if rents else None,
            unbooked_inv_qty=unbooked_qty if aid == unbooked_owner else 0,
            unbooked_inv_value=cents(unbooked_val) if aid == unbooked_owner else None, computed_at=now)
        session.add(daily)
        out[aid] = daily
    session.commit()
    return out


# ---------- 编排 ----------

def _fetch_youpin(rt, acc, now, rep: PnlReport, *, import_all: bool, echo) -> HoldingsState | None | str:
    """一个悠悠号：同步订单、补卖出详情、拉库存和租出（持 youpin_lock）。返回 state；失败返回 None，
    风控类要所有号都停时返回 "halt"。"""
    s = rt.settings
    monitor = getattr(rt, "monitor", None)
    lock = getattr(rt, "youpin_lock", None) or contextlib.nullcontext()
    with rt.session_factory() as session:
        if st.is_paused(session, acc.status_key, now):
            rep.skipped.append(f"{acc.id}:paused")
            return None
        try:
            with lock:
                b, sl, pages = sync_trades(session, acc.id, acc.client, fees=s.valuation.fees,
                                           max_pages=IMPORT_MAX_PAGES if import_all else s.pnl.max_pages, now=now,
                                           import_all=import_all)
                rep.new_buys += b
                rep.new_sells += sl
                rep.pages += pages
                if echo:
                    echo(f"号 {acc.id}：新买入 {b} 笔、新卖出 {sl} 笔（翻了 {pages} 页）")
                rep.details += fill_sell_details(session, acc.id, acc.client,
                                                 limit=10_000 if import_all else s.pnl.detail_per_run)
                return fetch_holdings_state(acc.client)
        except LoginRequired:
            rep.skipped.append(f"{acc.id}:login")
            return None
        except YoupinError as e:
            session.rollback()
            rep.skipped.append(f"{acc.id}:{type(e).__name__}")
            if monitor is not None and monitor.pause_after_error(session, acc, e) == "halt":
                return "halt"
            return None
        except Exception as e:
            session.rollback()
            log.warning("pnl: account %s fetch failed (%s)", acc.id, type(e).__name__)
            rep.skipped.append(f"{acc.id}:{type(e).__name__}")
            return None


def _fetch_buff(rt, acc, now, rep: PnlReport, *, import_all: bool, echo) -> list | None:
    """一个 BUFF 号：导入买卖订单（BUFF spec §9）、拉库存（持 buff_lock）。失败返回 None，不挡悠悠。"""
    from skinpulse.adapters.buff.errors import BuffError, BuffLoginRequired, BuffRateLimited
    from skinpulse.services.buff_pnl import fetch_buff_holdings, sync_buff_trades
    lock = getattr(rt, "buff_lock", None) or contextlib.nullcontext()
    with rt.session_factory() as session:
        if st.is_paused(session, acc.status_key, now):
            rep.skipped.append(f"buff{acc.id}:paused")
            return None
        try:
            with lock:
                b, sl, pages = sync_buff_trades(session, acc.id, acc.client, now=now, import_all=import_all,
                                                max_pages=IMPORT_MAX_PAGES if import_all else rt.settings.pnl.max_pages)
                holdings = fetch_buff_holdings(session, acc.client)
                session.commit()      # goods_to_template 可能补了 item.buff_goods_id
        except BuffLoginRequired:
            session.rollback()
            rep.skipped.append(f"buff{acc.id}:login")
            return None
        except BuffRateLimited as e:
            session.rollback()
            st.mark_error(session, acc.status_key, e.code, now + timedelta(hours=rt.settings.buff.pause_hours))
            rep.skipped.append(f"buff{acc.id}:limited")
            return None
        except (BuffError, Exception) as e:  # noqa: BLE001 —— 只记类型名：传输异常带 URL
            session.rollback()
            log.warning("pnl: buff account %s fetch failed (%s)", acc.id, type(e).__name__)
            rep.skipped.append(f"buff{acc.id}:{type(e).__name__}")
            return None
    rep.new_buys += b
    rep.new_sells += sl
    rep.pages += pages
    if echo:
        echo(f"BUFF 号 {acc.id}：新买入 {b} 笔、新卖出 {sl} 笔（翻了 {pages} 页），库存 {len(holdings)} 件")
    return holdings


def run_pnl(rt, day: date, now: datetime, *, import_all: bool = False, echo=None) -> PnlReport:
    """先把每个悠悠号、每个 BUFF 号的订单和库存都拉完，再一次算账写全部号的快照（ADR-0025：一个号的卖出会配
    另一个号的买入，所以不能按号各算各的）。任何一个悠悠号没拉成，这一天就不写快照（账缺一块会算错），页面
    继续显示上一天的；BUFF 号失败不挡（悠悠库存通常已是同一个 Steam 库存）。"""
    s = rt.settings
    rep = PnlReport(day=day.isoformat())
    accounts = sorted(rt.accounts.values(), key=lambda a: a.id)
    buff_accounts = sorted(getattr(rt, "buff_accounts", {}).values(), key=lambda a: a.id)
    rep.accounts = len(accounts) + len(buff_accounts)
    youpin: dict[int, HoldingsState] = {}
    complete = True
    for acc in accounts:
        state = _fetch_youpin(rt, acc, now, rep, import_all=import_all, echo=echo)
        if state == "halt":
            complete = False
            break
        if state is None:
            complete = False
            continue
        youpin[acc.id] = state
    buff: dict[int, list] = {}
    for acc in buff_accounts:
        holdings = _fetch_buff(rt, acc, now, rep, import_all=import_all, echo=echo)
        if holdings is not None:
            buff[acc.id] = holdings
    if not complete or not youpin:
        log.warning("pnl %s: not writing snapshots (an account was not fetched: %s)", rep.day, ",".join(rep.skipped))
        return rep
    with rt.session_factory() as session:
        live = None
        steamdt = getattr(rt, "steamdt", None)
        if import_all and steamdt is not None:
            busy = steamdt_batch_busy(session)
            if busy:
                # 正在跑的那个任务每分钟都要发一批：这边再插进去，两边都会撞 4005。缺的价等今晚 23:50 的快照
                log.info("pnl: skipping live prices, %s is using SteamDT", busy)
                if echo:
                    echo(f"{busy}正在用 SteamDT 批量价，这次不补市价（今晚 23:50 的价格快照会补上）")
            else:
                live = live_prices_for_unpriced(session, steamdt, s, day)
                if echo:
                    echo(f"从 SteamDT 补了 {len(live)} 个饰品的市价")
        ids = [a.id for a in accounts] + [a.id for a in buff_accounts]
        try:
            write_snapshots(session, s, day, youpin, buff, ids, now, live)
            rep.snapshots = len(ids)
        except Exception as e:
            session.rollback()
            log.warning("pnl %s: snapshot failed (%s)", rep.day, type(e).__name__)
            rep.skipped.append("snapshot")
    return rep


def steamdt_batch_busy(session: Session) -> str | None:
    """暂停单检查或选品正在跑（它们一分钟发一批 /price/batch）时返回中文名字，否则 None。"""
    if session.scalar(select(PausedCheckRun.id).where(PausedCheckRun.status == "running").limit(1)) is not None:
        return "暂停单检查"
    if session.scalar(select(PickRun.id).where(PickRun.status == "running").limit(1)) is not None:
        return "选品"
    return None


def has_snapshot(session: Session, day: date, account_ids) -> bool:
    """这一天每个号都有快照了吗（补跑只补缺的；一个号成功、另一个失败时不能算「已经有了」）。"""
    want = set(account_ids)
    have = set(session.scalars(select(PnlDaily.account_id).where(PnlDaily.date == day.isoformat())))
    return bool(want) and want <= have


def holding_template_ids(session: Session) -> set[int]:
    """最近一天持仓快照里还持有的模板（23:50 价格快照要覆盖它们，spec §5.1）。"""
    last = session.scalar(select(PnlPosition.date).order_by(PnlPosition.date.desc()).limit(1))
    if last is None:
        return set()
    return set(session.scalars(select(PnlPosition.template_id).where(PnlPosition.date == last,
                                                                    PnlPosition.qty > 0, PnlPosition.template_id > 0)))
