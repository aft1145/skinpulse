"""每日简报：取数、判定、推送、记账（M3a，spec §7a）。

纯逻辑在 analysis/brief.py，文案在 core/messages.py。这里只读数据库——**不为简报请求悠悠**。
每天一条 brief_log，发了什么、为什么没发都记下；同一天已有记录就不重复发（force 除外）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from skinpulse.adapters.ntfy.client import NtfyMessage
from skinpulse.analysis.brief import (
    FAILED, NO_CHANNEL, QUIET_SENT, SENT, Decision, OrderInfo, Snap, TopTable, decide, top_table,
)
from skinpulse.analysis.platform_gap import YOUPIN
from skinpulse.analysis.reach import reach_kwargs
from skinpulse.config import Settings
from skinpulse.core.localday import local_day_bounds
from skinpulse.core.messages import build_brief_message, build_quiet_brief_message, queue_label_of
from skinpulse.core.rank import queue_key_from_str
from skinpulse.db.models import Account, BriefLog, MyBuyOrder, PriceDaily, RankSnapshot
from skinpulse.services import status as st
from skinpulse.services.notifier import AllChannelsFailed
from skinpulse.services.buff import youpin_orders

log = logging.getLogger(__name__)
UNLIMITED = 10**9


def _display_name(o: MyBuyOrder, accounts: dict[int, str] | None = None) -> str:
    k = queue_key_from_str(o.queue_key)
    label = queue_label_of(k.abrade, k.type_id, k.special, k.fade)
    name = o.commodity_name if label == "不限" else f"{o.commodity_name} [{label}]"
    acct = (accounts or {}).get(o.account_id)
    return f"[{acct}] {name}" if acct else name


def account_labels(session: Session) -> dict[int, str]:
    """启用的悠悠号 ≥ 2 时每个号的简称（多账号 spec §6）；一个号时空 dict，简报一字不改。"""
    rows = list(session.scalars(select(Account).where(Account.platform == "youpin", Account.enabled.is_(True))))
    return {a.id: a.display_name() for a in rows} if len(rows) >= 2 else {}


def _active_orders(session: Session) -> list[MyBuyOrder]:
    return list(session.scalars(select(MyBuyOrder).where(MyBuyOrder.active.is_(True), youpin_orders())))


def _youpin_sell_prices(session: Session, day: date) -> dict[int, Decimal | None]:
    """D 当天 23:50 价格快照里悠悠的在售最低价，按模板。那天没拍到就是空 dict——在售列写「—」。"""
    rows = session.execute(select(PriceDaily.template_id, PriceDaily.sell_price)
                           .where(PriceDaily.date == day.isoformat(), PriceDaily.platform == YOUPIN))
    return {tid: price for tid, price in rows}


def sell_note(session: Session, settings: Settings, day: date) -> str:
    """表下那行在售脚注：D 当天悠悠快照里最晚的 `fetched_at`（本地时间）。快照只补当天缺的模板，
    白天手动跑过一次 `price-snapshot`，23:50 那次就全跳过了——在售列其实是白天的价格，要写出来；
    没有快照（没拍、没配 SteamDT key）也要明说，不能只剩一列「—」。"""
    latest = session.scalar(select(func.max(PriceDaily.fetched_at))
                            .where(PriceDaily.date == day.isoformat(), PriceDaily.platform == YOUPIN))
    if latest is None:
        return f"在售：{day:%m-%d} 没有价格快照"
    return f"在售：{latest.astimezone(ZoneInfo(settings.timezone)):%m-%d %H:%M} 悠悠价格快照"


def load_top_table(session: Session, settings: Settings, day: date) -> TopTable:
    start, end = local_day_bounds(day, settings.timezone)
    orders = _active_orders(session)
    snaps: dict[str, list[Snap]] = {}

    def add(r: RankSnapshot) -> None:
        snaps.setdefault(r.purchase_no, []).append(Snap(r.ts, r.rank, r.top1_price))

    for r in session.scalars(select(RankSnapshot).where(RankSnapshot.ts >= start, RankSnapshot.ts < end)):
        add(r)
    # 开盘值：当天之前最后一条「有排名」的快照（监控可能停过好几天，所以不限定是前一天）。
    last_before = (select(RankSnapshot.purchase_no, func.max(RankSnapshot.ts).label("mts"))
                   .where(RankSnapshot.ts < start, RankSnapshot.rank.is_not(None))
                   .group_by(RankSnapshot.purchase_no).subquery())
    for r in session.scalars(select(RankSnapshot).join(last_before, and_(
            RankSnapshot.purchase_no == last_before.c.purchase_no, RankSnapshot.ts == last_before.c.mts))):
        add(r)
    labels = account_labels(session)
    return top_table([OrderInfo(o.purchase_no, _display_name(o, labels), o.price, o.template_id,
                                o.sell_min, o.sell_depth, o.ref_bid) for o in orders],
                     snaps, start, end, top_rank=settings.brief.top_rank, **reach_kwargs(settings.alerts),
                     sell_prices=_youpin_sell_prices(session, day))


@dataclass
class BriefPlan:
    day: date
    decision: Decision
    table: TopTable
    push: NtfyMessage | None       # 推送用：纯文本截到 4000 字节、html 截到 16000 字节以内
    full_body: str | None          # 存档用：不截断的纯文本
    sell_note: str | None = None   # 表下面那行「在售：……」；只有发表格的那天有


def _sent_days(session: Session, day: date) -> tuple[date | None, date | None]:
    key = day.isoformat()
    last = session.scalar(select(func.max(BriefLog.date))
                          .where(BriefLog.date < key, BriefLog.decision.in_((SENT, QUIET_SENT))))
    first = session.scalar(select(func.min(BriefLog.date)).where(BriefLog.date < key))
    return (date.fromisoformat(last) if last else None, date.fromisoformat(first) if first else None)


def plan_brief(session: Session, settings: Settings, day: date) -> BriefPlan:
    """只读：算出这一天要不要发、发什么。CLI 预览直接用它。"""
    table = load_top_table(session, settings, day)
    last_sent, first_log = _sent_days(session, day)
    b = settings.brief
    d = decide(table, day=day, last_sent_day=last_sent, first_log_day=first_log,
               quiet_days=b.quiet_days, top_rank=b.top_rank)
    push = full = note = None
    if d.decision == SENT:
        note = sell_note(session, settings, day)
        push = build_brief_message(day, table, top_rank=b.top_rank, sell_note=note)
        full = build_brief_message(day, table, top_rank=b.top_rank, sell_note=note, budget=UNLIMITED).body
    elif d.decision == QUIET_SENT:
        push = build_quiet_brief_message(day, b.quiet_days, b.top_rank)
        full = push.body
    return BriefPlan(day, d, table, push, full, note)


def _num(v) -> float | None:
    return None if v is None else float(v)


def table_json(plan: BriefPlan) -> str | None:
    """发出去的那张表，存进 brief_log.rows_json（看板简报页按表格展示）。只有发表格的那天有。"""
    if plan.decision.decision != SENT:
        return None
    rows = [{"rank": r.rank, "name": r.name, "my_price": _num(r.my_price), "top1_price": _num(r.top1_price),
             # 在售价 0 = 没有数据，存 null（和推送里的「—」一致）
             "sell_price": _num(r.sell_price) if r.sell_price is not None and r.sell_price > 0 else None,
             "is_new": r.is_new, "open_rank": r.open_rank, "open_top1": _num(r.open_top1),
             "rank_changed": r.rank_changed, "top1_changed": r.top1_changed} for r in plan.table.rows]
    return json.dumps({"rows": rows, "sell_note": plan.sell_note}, ensure_ascii=False)


def _mark_channels(session: Session, row: BriefLog, results: list) -> None:
    try:
        for r in results:
            if r.ok:
                st.mark_ok(session, r.name)
            else:
                st.mark_error(session, r.name, "publish_failed", None)
    except Exception as e:  # noqa: BLE001 - 只记类型名；brief_log 已经提交，这里失败不往外抛
        log.warning("daily brief %s: recording channel status failed: %s", row.date, type(e).__name__)
        try:
            session.expunge(row)  # 已提交、属性都在；免得 rollback 把它标成过期、离开 session 后读不了
            session.rollback()
        except Exception as e2:  # noqa: BLE001 - 收尾本身出错也不能越过已经提交的 brief_log
            log.warning("daily brief %s: rollback after status failure failed: %s", row.date, type(e2).__name__)


def run_daily_brief(session: Session, notifier, settings: Settings, day: date, *, now: datetime,
                    force: bool = False) -> BriefLog:
    key = day.isoformat()
    existing = session.get(BriefLog, key)
    if existing is not None and not force:
        return existing
    plan = plan_brief(session, settings, day)
    decision = plan.decision.decision
    reasons = dict(plan.decision.reasons)
    sent_at = None
    results: list = []
    if plan.push is not None:
        if notifier is None:
            decision = NO_CHANNEL
            reasons["delivery"] = "没有配置推送通道"
        else:
            try:
                results = notifier.publish(plan.push) or []
                sent_at = now
            except AllChannelsFailed as e:
                decision = FAILED
                reasons["delivery"] = str(e)      # 各通道错误已脱敏（见 services/notifier._describe）
                results = e.results
            except Exception as e:  # noqa: BLE001 - 传输层异常只记类型名，消息里可能带 URL
                decision = FAILED
                reasons["delivery"] = type(e).__name__
    if existing is not None:
        session.delete(existing)
        session.flush()
    row = BriefLog(date=key, decision=decision, reasons_json=json.dumps(reasons, ensure_ascii=False),
                   title=plan.push.title if plan.push else None, body=plan.full_body,
                   rows_json=table_json(plan), created_at=now, sent_at=sent_at)
    session.add(row)
    session.commit()
    # 先把「这天发过了」落库，再记各通道状态：状态表写失败（database is locked）不能带走 brief_log，
    # 否则启动补发会再发一次（spec §7a.5）。
    _mark_channels(session, row, results)
    if decision in (FAILED, NO_CHANNEL):
        # 推送没送出去不能悄悄过去——spec 要求这条按 WARNING 记，reasons["delivery"] 已经脱敏
        # （AllChannelsFailed 自己的 str()，或者传输层异常的类型名）。
        log.warning("daily brief %s: %s (%s)", key, decision, reasons.get("delivery", ""))
    else:
        log.info("daily brief %s: %s (%s)", key, decision, reasons.get("summary", ""))
    return row
