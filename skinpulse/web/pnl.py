"""`GET /api/pnl*`：盈亏页（盈亏 spec §6）。只读库，不请求任何外部服务。

持仓、市价、在哪都读最近一天的 `pnl_position` / `pnl_daily`（每天 00:45 写）；已实现和卖出每次用
`trade_order` 现算（几百行，毫秒级），只算到快照那天结束，和持仓同一个时点。`account=None` 是全部号相加。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select

from skinpulse.analysis.category import GROUP_LABELS, categorize
from skinpulse.analysis.pnl import ZERO, build_ledger, cents, item_key, monthly_realized, net_factor
from skinpulse.core.names import split_item_name
from skinpulse.db.models import PnlDaily, PnlPosition, TradeOrder
from skinpulse.services.pnl import load_orders
from skinpulse.web.common import _num, iso_z

ITEMS_MAX = 200
SELLS_DEFAULT = 12


STALE_DAYS = 7   # 一个号的最新快照比全局最新早这么多天以上，就当它不再更新了（停用的号），不再相加


def _snapshots(session, account_id: int | None) -> dict[int, str]:
    """每个号用它**自己**最新的那天（号 → 日期）。全部号相加时，某个号今天的任务失败（暂停、掉登录），
    就用它上一次的快照，而不是把它整个漏掉。"""
    q = select(PnlDaily.account_id, func.max(PnlDaily.date)).group_by(PnlDaily.account_id)
    if account_id is not None:
        q = q.where(PnlDaily.account_id == account_id)
    rows = dict(session.execute(q).all())
    if not rows:
        return {}
    cutoff = (date.fromisoformat(max(rows.values())) - timedelta(days=STALE_DAYS)).isoformat()
    return {a: d for a, d in rows.items() if d >= cutoff}


def _latest(session, account_id: int | None) -> str | None:
    snaps = _snapshots(session, account_id)
    return max(snaps.values()) if snaps else None


def _end_of(day: str, tz: str) -> datetime:
    d = date.fromisoformat(day) + timedelta(days=1)
    return datetime.combine(d, datetime.min.time(), tzinfo=ZoneInfo(tz))


def _ledger(session, rt, account_id: int | None, as_of: str | None):
    """一本全局的账（跨号、跨平台配对，ADR-0025），只看某个号时再按号筛：卖出按卖出的号，持仓按买入的号。"""
    until = _end_of(as_of, rt.settings.timezone) if as_of else None
    ledger = build_ledger(load_orders(session, None, until), rt.settings.valuation.fees)
    if account_id is not None:
        ledger.sells = [x for x in ledger.sells if x.order.account_id == account_id]
        ledger.positions = [x for x in ledger.positions if x.account_id == account_id]
    return ledger


def _factor(rt, platform: str) -> Decimal:
    v = rt.settings.valuation
    return net_factor(v.undercut, v.fees.get(platform, 0.0))


def _of_snapshots(model, snaps: dict[int, str]):
    return or_(*[and_(model.account_id == a, model.date == d) for a, d in snaps.items()])


def _positions(session, account_id: int | None) -> list[PnlPosition]:
    snaps = _snapshots(session, account_id)
    return list(session.scalars(select(PnlPosition).where(_of_snapshots(PnlPosition, snaps)))) if snaps else []


def _name(raw: str) -> tuple[str, str | None]:
    n = split_item_name(raw)
    return n.title, n.tag


def pnl_payload(rt, account_id: int | None = None) -> dict:
    s = rt.settings
    with rt.session_factory() as session:
        as_of = _latest(session, account_id)
        base = {"as_of": as_of, "basis_default": s.pnl.basis}
        if as_of is None:
            return base | {"computed_at": None, "realized": None, "holding": None, "where": None, "youpin": None,
                           "months": [], "categories": [], "notes": None}
        snaps = _snapshots(session, account_id)
        days = list(session.scalars(select(PnlDaily).where(_of_snapshots(PnlDaily, snaps))))
        ledger = _ledger(session, rt, account_id, as_of)
        positions = _positions(session, account_id)

    def total(attr):
        vals = [getattr(d, attr) for d in days if getattr(d, attr) is not None]
        return sum(vals, type(vals[0])(0)) if vals else None

    matched, unmatched = ledger.matched(), ledger.unmatched()
    cats: dict[str, dict] = {}
    for p in positions:
        key = categorize(p.hash_name).group
        c = cats.setdefault(key, {"key": key, "label": GROUP_LABELS.get(key, "其他"), "qty": 0, "cost": ZERO,
                                  "cost_priced": ZERO, "value_mkt": ZERO, "value_net": ZERO})
        c["qty"] += p.qty
        c["cost"] += p.cost
        if p.price is not None:
            c["cost_priced"] += p.cost
            c["value_mkt"] += p.price * p.qty
            c["value_net"] += p.price * p.qty * _factor(rt, p.platform)
    # 一件都没有市价的品类：市值给 null（坑 3：缺失不是 0），前端写「—」排最后
    categories = [{**c, "cost": _num(cents(c["cost"])), "cost_priced": _num(cents(c["cost_priced"])),
                   "value_mkt": _num(cents(c["value_mkt"])) if c["cost_priced"] else None,
                   "value_net": _num(cents(c["value_net"])) if c["cost_priced"] else None}
                  for c in cats.values()]
    youpin = [d.youpin_pnl for d in days if d.youpin_pnl is not None]
    rents = [d.lease_rent_day for d in days if d.lease_rent_day is not None]
    return base | {
        "computed_at": iso_z(max(d.computed_at for d in days)),
        "realized": {"total": _num(ledger.realized_total), "matched": len(matched), "unknown": len(unmatched),
                     "unknown_net": _num(cents(sum((x.net for x in unmatched), ZERO))),
                     "sells": len(ledger.sells)},
        "holding": {"qty": total("qty"), "cost": _num(total("cost")), "cost_priced": _num(total("cost_priced")),
                    "value_mkt": _num(total("value_mkt")), "value_net": _num(total("value_net")),
                    "unpriced_qty": total("unpriced_qty"),
                    "flagged": sum(1 for p in positions if p.price_flag)},
        "where": {"inv": total("inv_qty"), "lease": total("lease_qty"), "unknown": total("unknown_qty"),
                  "unknown_cost": _num(total("unknown_cost"))},
        "youpin": {"pnl": _num(sum(youpin, ZERO)), "n": total("youpin_pnl_n")} if youpin else None,
        "months": [[m, _num(v)] for m, v in monthly_realized(ledger.sells, s.timezone, date.fromisoformat(as_of))],
        "categories": categories,
        "notes": {"lease_qty": total("lease_count"), "lease_rent_day": _num(sum(rents, ZERO)) if rents else None,
                  "unbooked_inv_qty": total("unbooked_inv_qty"),
                  "unbooked_inv_value": _num(total("unbooked_inv_value"))},
    }


def pnl_items(rt, account_id: int | None = None, *, sort: str = "worst", basis: str = "market", q: str = "",
              category: str | None = None, offset: int = 0, limit: int = 50) -> dict:
    """单品表：按英文名合并各号、各平台的持仓（BUFF 独有的饰品模板 ID 是负的，和悠悠的同名饰品合成一行），
    按浮动排序。没有市价的排在最后。`template_id` 只给正的（能链详情页），没有就是 null。"""
    with rt.session_factory() as session:
        as_of = _latest(session, account_id)
        if as_of is None:
            return {"as_of": None, "total": 0, "items": []}
        positions = _positions(session, account_id)
        ledger = _ledger(session, rt, account_id, as_of)
    realized: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for m in ledger.matched():
        realized[item_key(m.order.hash_name, m.order.template_id)] += m.pnl
    rows: dict[str, dict] = {}
    for p in positions:
        k = item_key(p.hash_name, p.template_id)
        r = rows.get(k)
        if r is None:
            title, sub = _name(p.name)
            cat = categorize(p.hash_name).group
            r = rows[k] = {
                "key": k, "template_id": None, "name": title, "sub": sub, "category": cat,
                "category_label": GROUP_LABELS.get(cat, "其他"), "qty": 0, "cost": ZERO, "price": p.price,
                "price_flag": p.price_flag, "where": {"inv": 0, "lease": 0, "unknown": 0}, "platforms": [],
                "factor": _factor(rt, p.platform)}
        if p.template_id > 0 and r["template_id"] is None:
            r["template_id"] = p.template_id
        if p.platform not in r["platforms"]:
            r["platforms"].append(p.platform)
        r["qty"] += p.qty
        r["cost"] += p.cost
        r["where"]["inv"] += p.inv_qty
        r["where"]["lease"] += p.lease_qty
        r["where"]["unknown"] += p.unknown_qty
        if r["price"] is None:
            r["price"] = p.price
    out = []
    needle = q.strip().lower()
    for r in rows.values():
        if needle and needle not in r["name"].lower():
            continue
        if category and r["category"] != category:
            continue
        price = r.pop("price")
        factor = r.pop("factor")
        mkt = None if price is None else price * r["qty"]
        net = None if mkt is None else mkt * factor
        value = net if basis == "net" else mkt
        flt = None if value is None else value - r["cost"]
        out.append({**r, "cost": _num(cents(r["cost"])), "price": _num(price),
                    "value_mkt": _num(cents(mkt)) if mkt is not None else None,
                    "value_net": _num(cents(net)) if net is not None else None,
                    "float": _num(cents(flt)) if flt is not None else None,
                    "realized": _num(cents(realized[r["key"]])) if r["key"] in realized else None,
                    "_f": flt})
    priced = [x for x in out if x["_f"] is not None]
    unpriced = [x for x in out if x["_f"] is None]
    priced.sort(key=lambda x: (x["_f"], x["key"]), reverse=sort == "best")
    ordered = priced + sorted(unpriced, key=lambda x: x["key"])
    for x in ordered:
        x.pop("_f")
    limit = max(1, min(limit, ITEMS_MAX))
    return {"as_of": as_of, "total": len(ordered), "items": ordered[offset:offset + limit]}


def pnl_sells(rt, account_id: int | None = None, *, limit: int | None = SELLS_DEFAULT) -> dict:
    """最近卖出，按时间倒序；每笔带配到的买入批次。"""
    with rt.session_factory() as session:
        # 和摘要同一个时点：只算到最近一次快照那天结束（快照之后才同步进来的卖出，下一次快照再算）
        ledger = _ledger(session, rt, account_id, _latest(session, account_id))
        exact = {r.order_no: r.net_exact for r in session.scalars(
            select(TradeOrder).where(TradeOrder.side == "sell"))}
    sells = list(reversed(ledger.sells))
    rows = []
    for m in sells[:limit] if limit else sells:
        title, sub = _name(m.order.name)
        rows.append({"at": iso_z(m.order.created_at), "order_no": m.order.order_no, "account_id": m.order.account_id,
                     "platform": m.order.platform,
                     "template_id": m.order.template_id if m.order.template_id > 0 else None,
                     "name": title, "sub": sub, "qty": m.order.qty,
                     "net": _num(m.net), "net_exact": exact.get(m.order.order_no, m.net_exact),
                     "cost": _num(m.cost), "pnl": _num(m.pnl),
                     "lots": [{"bought_at": iso_z(u.bought_at), "unit": _num(u.unit), "qty": u.qty,
                               "platform": u.platform, "account_id": u.account_id} for u in m.lots]})
    return {"total": len(sells), "sells": rows}
