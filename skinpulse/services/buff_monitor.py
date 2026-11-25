"""BUFF 的一轮监控（BUFF spec §4）：每个启用的 BUFF 号依次同步 → 选候选数名次 → 看订单第 1 页（交易中、
成交）→ 写快照、走告警（和悠悠共用 `Monitor._step_orders`）→ 推送。和悠悠的轮次分开跑、互不等待。

一个号出错只影响它自己：`Login Required` 标失效、推提醒；限流 / 验证码暂停 `buff.pause_hours`、推一次
提醒，不重试。对 BUFF 只读。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from skinpulse.adapters.buff.client import QUEUE_PAGE
from skinpulse.adapters.buff.errors import BuffError, BuffLoginRequired, BuffRateLimited
from skinpulse.core.alerts import AlertState
from skinpulse.core.buff_rank import Candidate, covers_me, pick_candidates, rank_in_queue, raw_order_id, split_queue_key
from skinpulse.core.messages import build_system_message
from skinpulse.core.planner import _is_night
from skinpulse.db.models import AlertStateRow, MyBuyOrder
from skinpulse.services import status as st
from skinpulse.services.buff import (
    BuffAccountRuntime, created_of, goods_id_of, mark_in_trade, resolve_buff_gone, sync_buff_orders,
)

log = logging.getLogger(__name__)

BUFF_ROUND = "buff_round"
HISTORY_PAGE = 20


@dataclass
class BuffRoundReport:
    accounts: int = 0
    synced: int = 0
    queues: int = 0
    checked: int = 0
    alerts_sent: int = 0
    fills_notified: int = 0
    in_trade: int = 0
    resolved: dict = field(default_factory=dict)
    errors: int = 0
    paused: bool = False
    skipped_night: bool = False
    requests: int = 0


def _specifics_of(o: MyBuyOrder):
    from skinpulse.adapters.buff.models import Specific
    try:
        raw = json.loads(o.conditions_json or "{}").get("specific") or []
    except ValueError:
        raw = []
    return tuple(Specific(str(x.get("type") or ""), str(x.get("text") or ""), tuple(x.get("values") or ())) for x in raw)


def _bmp(o: MyBuyOrder) -> Decimal | None:
    try:
        v = json.loads(o.conditions_json or "{}").get("buy_max_price")
        return Decimal(v) if v is not None else None
    except (ValueError, ArithmeticError):
        return None


def rank_orders(session: Session, acc: BuffAccountRuntime, orders: list[MyBuyOrder], cfg, now: datetime) -> int:
    """给这个号的单数名次（spec §5），返回查了几次队列。没轮到的普通单：价比最高普通单低就记名次未知、
    榜首记最高普通单价。同一轮里同一件饰品的队列只取一次。"""
    alerting = {r.purchase_no for r in session.scalars(select(AlertStateRow).where(
        AlertStateRow.purchase_no.in_([o.purchase_no for o in orders]),
        AlertStateRow.state == AlertState.ALERTING.value))}
    cands = [Candidate(o.purchase_no, o.price, _bmp(o), split_queue_key(o.queue_key)[1] == "plain",
                       o.purchase_no in alerting, o.last_verified_at) for o in orders]
    picked = set(pick_candidates(cands, cfg.queue_per_round))
    cache: dict[int, tuple[list, bool]] = {}   # goods_id → (已取的行, 是否翻完)
    queries = 0
    for o in orders:
        plain = split_queue_key(o.queue_key)[1] == "plain"
        if o.purchase_no not in picked:
            # 这一轮没数：名次记未知（不拿上一轮的名次再判一次）；普通单的榜首记最高普通单价
            bmp = _bmp(o)
            o.reported_rank = None
            if plain and bmp is not None:
                o.top_bid = max(bmp, o.price)
            continue
        gid = goods_id_of(o)
        if gid is None:
            continue
        rows, done = cache.get(gid, ([], False))
        my_id = raw_order_id(o.purchase_no)
        page = len(rows) // QUEUE_PAGE + 1 if rows else 1
        while not done and not covers_me(rows, my_id=my_id, price=o.price, page_full=True) \
                and page <= cfg.queue_max_pages:
            qp = acc.client.buy_queue(gid, page)
            queries += 1
            rows = rows + qp.rows
            done = len(qp.rows) < QUEUE_PAGE or page >= max(qp.total_page, 1)
            page += 1
        cache[gid] = (rows, done)
        complete = done or covers_me(rows, my_id=my_id, price=o.price, page_full=True)
        r = rank_in_queue(rows, my_id=my_id, price=o.price, created=created_of(o), specific=_specifics_of(o),
                          complete=complete)
        o.reported_rank, o.top_bid, o.cond_ahead = r.rank, r.top, r.cond_ahead
        o.last_verified_at = now          # BUFF 单借这一列记「上次数名次」的时间（悠悠的抽查不碰 BUFF 单）
        if r.rank is None:
            log.info("buff: order %s is beyond the first %d queue rows", o.purchase_no, r.seen)
    return queries


def _notify_once(m, notice, title: str, body: str) -> None:
    if m.ntfy is None:
        return
    with notice.lock:
        if not notice.due():
            return
        try:
            m.ntfy.publish(build_system_message(title, body, m.s.ntfy))
            notice.mark_sent()
        except Exception as e:  # noqa: BLE001 —— 只记类型名：传输异常带 URL
            log.error("buff: failed to send system notice (%s)", type(e).__name__)


def _night_gate(m, session: Session) -> bool:
    p = m.s.polling
    if not _is_night(m.local_hour(), p):
        return False
    row = st.get_status(session, BUFF_ROUND)
    if row is None or row.last_ok_at is None:
        return False
    return (m.now() - row.last_ok_at).total_seconds() < m.s.buff.round_interval_min * 60 * p.night_multiplier


def run_buff_round(m) -> BuffRoundReport:
    """`m` 是服务里的 Monitor（共用告警引擎、每小时推送上限、推送通道）。"""
    rep = BuffRoundReport()
    accounts = sorted(m.buff_accounts.values(), key=lambda a: a.id)
    rep.accounts = len(accounts)
    if not accounts:
        return rep
    from skinpulse.services.monitor import RoundReport
    rr = RoundReport()
    events: list = []
    seen: dict = {}
    with m.Session() as session:
        if _night_gate(m, session):
            rep.skipped_night = True
            return rep
        st.mark_ok(session, BUFF_ROUND)
        for acc in accounts:
            now = m.now()
            if st.is_paused(session, acc.status_key, now):
                rep.paused = True
                continue
            before = getattr(acc.client, "requests", 0)
            try:
                orders = sync_buff_orders(session, acc, now)
                rep.synced += len(orders)
                rep.queues += rank_orders(session, acc, orders, m.s.buff, now)
                session.commit()
                bills = acc.client.buy_history(1, HISTORY_PAGE).bills
                rep.in_trade += mark_in_trade(session, acc.id, bills, now)
                r = resolve_buff_gone(session, acc.id, bills, now)
                rep.resolved = {k: rep.resolved.get(k, 0) + v for k, v in r.items()}
                st.mark_ok(session, acc.status_key)
            except BuffLoginRequired as e:
                rep.errors += 1
                session.rollback()
                st.mark_error(session, acc.status_key, e.code, None)
                _notify_once(m, acc.login_notice, f"BUFF 登录已失效：{acc.label}",
                             "请在看板「系统」页更新 BUFF 的 cookie；这期间不再请求 BUFF，悠悠照常。")
                continue
            except BuffRateLimited as e:
                rep.errors += 1
                session.rollback()
                hours = m.s.buff.pause_hours
                st.mark_error(session, acc.status_key, e.code, now + timedelta(hours=hours))
                log.warning("buff: account %s rate limited (%s); paused for %sh", acc.id, e.code, hours)
                _notify_once(m, acc.limited_notice, f"BUFF 访问受限，已暂停 {hours:g} 小时",
                             "BUFF 要求验证或限流。这段时间不请求 BUFF、不推 BUFF 的单，悠悠照常；到时间自动恢复。")
                continue
            except BuffError as e:
                rep.errors += 1
                session.rollback()
                st.mark_error(session, acc.status_key, e.code, None)
                log.warning("buff: account %s round failed (%s)", acc.id, e.code)
                continue
            except Exception as e:  # noqa: BLE001 —— 只记类型名：传输异常带 URL
                rep.errors += 1
                session.rollback()
                log.error("buff: account %s round failed (%s)", acc.id, type(e).__name__)
                continue
            finally:
                rep.requests += getattr(acc.client, "requests", 0) - before
            active = list(session.scalars(select(MyBuyOrder).where(MyBuyOrder.active.is_(True),
                                                                   MyBuyOrder.account_id == acc.id)))
            m._step_orders(session, active, {}, now, rr, events, seen)
            session.commit()
        rep.checked = rr.checked
        if events:
            try:
                rep.alerts_sent = m._notify(session, events, rr)
            except Exception as e:  # noqa: BLE001
                rep.errors += 1
                log.error("buff: notification stage failed (%s)", type(e).__name__)
                session.rollback()
        if m.s.fills.enabled:
            try:
                rep.fills_notified = m._notify_fills(session, rr, buff=True)
            except Exception as e:  # noqa: BLE001
                rep.errors += 1
                log.error("buff: fill notification stage failed (%s)", type(e).__name__)
                session.rollback()
    return rep
