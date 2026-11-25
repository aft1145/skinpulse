"""`/api/paused`：充值前检查暂停单的结果（暂停单 spec §5.3）。只读库，不请求悠悠（坑 6）。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select

from skinpulse.core.names import split_item_name
from skinpulse.core.paused import OTHER, TIERS, classify, paused_days, sort_key
from skinpulse.db.models import MyBuyOrder, OrderEvent, PausedCheckItem, PausedCheckRun
from skinpulse.web.common import _num, iso_z
from skinpulse.web.overview import youpin_labels


def _paused_events(s, after: PausedCheckRun | None):
    """被暂停、而且之后没回到「求购中」的单（库里知道的；服务上线前暂停的看不到）。

    给了 `after`：只数这次检查开始之后才被暂停、而且这次检查里没有的（翻页那半分钟里被暂停的可能已经翻到了）。"""
    active = select(MyBuyOrder.purchase_no).where(MyBuyOrder.active.is_(True))
    q = (select(func.count(func.distinct(OrderEvent.purchase_no)))
         .where(OrderEvent.kind == "gone", OrderEvent.outcome == "paused", OrderEvent.purchase_no.not_in(active)))
    if after is not None:
        seen = select(PausedCheckItem.purchase_no).where(PausedCheckItem.run_id == after.id)
        q = q.where(OrderEvent.at > after.started_at, OrderEvent.purchase_no.not_in(seen))
    return s.scalar(q) or 0


def _run(r: PausedCheckRun | None) -> dict | None:
    if r is None:
        return None
    return {"id": r.id, "status": r.status, "source": r.source, "started_at": iso_z(r.started_at),
            "finished_at": iso_z(r.finished_at), "phase": r.phase, "done": r.done, "total": r.total,
            "incomplete": r.incomplete, "steamdt": r.steamdt,
            "skipped": [int(x) for x in r.skipped.split(",")] if r.skipped else [], "error": r.error}


def paused_payload(rt, now: datetime) -> dict:
    cfg = rt.settings.paused_check
    with rt.session_factory() as s:
        labels = youpin_labels(s, rt.settings.dashboard.platform_names)
        latest = s.scalar(select(PausedCheckRun).order_by(PausedCheckRun.id.desc()).limit(1))
        # 卡片显示的是最近一次查完的结果；正在跑的那次只给进度
        shown = latest if latest is None or latest.status == "done" else s.scalar(
            select(PausedCheckRun).where(PausedCheckRun.status == "done").order_by(PausedCheckRun.id.desc()).limit(1))
        rows = list(s.scalars(select(PausedCheckItem).where(PausedCheckItem.run_id == shown.id))) if shown else []
        out = {
            "run": _run(latest),
            "result": _run(shown),
            "summary": None,
            "items": [],
            "paused_since": _paused_events(s, shown) if shown else 0,
            "known_paused": _paused_events(s, None),
            "old_days": cfg.old_days,
            "stale_h": cfg.stale_h,
            "accounts": {str(k): v for k, v in labels.items()},
        }
    if shown is None:
        return out

    tiers = {t: {"count": 0, "amount": Decimal(0)} for t in TIERS}
    items = []
    old = 0
    for r in rows:
        v = classify(r.price, r.top_bid, r.top_empty, r.sell_min, near_gap=Decimal(str(cfg.near_gap)),
                     sell_ratio=Decimal(str(cfg.sell_ratio)))
        amount = r.price * max(r.remaining_qty, 1)
        tiers[r.tier]["count"] += 1
        tiers[r.tier]["amount"] += amount
        days = paused_days(r.paused_at, now)
        is_old = days is not None and days > cfg.old_days
        old += is_old
        if r.tier == OTHER:
            continue
        name = split_item_name(r.commodity_name)
        items.append((sort_key(r.tier, v.sell_ratio, v.gap_ratio), {
            "purchase_no": r.purchase_no, "account_id": r.account_id, "template_id": r.template_id,
            "title": name.title, "star": name.star, "stattrak": name.stattrak, "tag": name.tag,
            "price": _num(r.price), "remaining_qty": r.remaining_qty,
            "paused_at": iso_z(r.paused_at), "paused_days": days, "old": is_old,
            "top_bid": _num(r.top_bid), "top_empty": r.top_empty, "top1_kind": r.top1_kind,
            "gap_ratio": _num(v.gap_ratio), "over": _num(r.price - r.top_bid) if r.top_bid is not None
            and r.price >= r.top_bid else None,
            "sell_min": _num(r.sell_min), "sell_depth": r.sell_depth, "sell_src": r.sell_src,
            "sell_at": iso_z(r.sell_at), "sell_ratio": _num(v.sell_ratio), "tier": r.tier,
        }))
    items.sort(key=lambda x: (x[0], x[1]["title"]))
    out["summary"] = {
        "total": len(rows), "amount": _num(sum((r.price * max(r.remaining_qty, 1) for r in rows), Decimal(0))),
        "old": old, "tiers": {t: {"count": d["count"], "amount": _num(d["amount"])} for t, d in tiers.items()},
    }
    out["items"] = [d for _, d in items]
    return out
