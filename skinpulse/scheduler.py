from __future__ import annotations

import logging
import threading
import time
from contextlib import nullcontext
from datetime import date, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func, select

from skinpulse.core.localday import last_due_brief, local_today
from skinpulse.db.models import Item, MyBuyOrder, Valuation
from skinpulse.db.session import utcnow
from skinpulse.services.account import AccountRuntime, heartbeat, notify_login_lost
from skinpulse.services.brief import run_daily_brief
from skinpulse.services.monitor import recover_pending, reset_alerting_above_enter_rank
from skinpulse.services.pnl import has_snapshot, holding_template_ids, run_pnl
from skinpulse.services.paused import mark_interrupted as mark_paused_interrupted
from skinpulse.services.paused import run_check as run_paused_check
from skinpulse.services.price_snapshot import take_price_snapshot
from skinpulse.services.valuation import run_valuation
from skinpulse.services.buff import youpin_orders

log = logging.getLogger(__name__)
JOB_IDS = ("monitor_round", "heartbeat")
BRIEF_JOB_IDS = ("price_snapshot", "daily_brief", "daily_brief_catchup")
BRIEF_CATCHUP_WINDOW = timedelta(hours=12)
VALUATION_JOB_IDS = ("valuation", "valuation_catchup")
PNL_JOB_IDS = ("pnl", "pnl_catchup")
VALUATION_CATCHUP_WINDOW = timedelta(hours=12)
# 00:00 的 cron 和启动时的补发是两个不同的 job，max_instances 管不到彼此；这把锁保证同一时刻
# 只有一个在判定，后到的那个会看到 brief_log 里已有当天的记录、不会重发。
_BRIEF_LOCK = threading.Lock()
# 同理：00:30 的 cron 和启动补跑不能同时估值同一天。
_VALUATION_LOCK = threading.Lock()
# 盈亏（盈亏 spec §5.2）：00:45 的 cron、启动补跑、CLI 不能同时算同一天
_PNL_LOCK = threading.Lock()
PNL_CATCHUP_WINDOW = timedelta(hours=12)


def job_heartbeat(rt) -> None:
    """每个启用的号各查一次（多账号 spec §5），号之间隔 account_gap_s。"""
    accounts = getattr(rt, "accounts", None)
    if accounts is None:
        accounts = {1: AccountRuntime(1, rt.client)} if rt.client is not None else {}
    accs = [a for a in accounts.values() if getattr(a.client, "token", None)]
    multi = len(accounts) >= 2
    for i, acc in enumerate(accs):
        if i:
            time.sleep(rt.settings.polling.account_gap_s)
        with rt.session_factory() as s, _youpin_lock(rt):
            try:
                if not heartbeat(s, acc):
                    notify_login_lost(rt.ntfy, rt.settings.ntfy, acc, multi=multi)
            except Exception as e:  # noqa: BLE001
                log.error("heartbeat failed: %s", e)


def _youpin_lock(rt):
    lock = getattr(rt, "youpin_lock", None)
    return lock if lock is not None else nullcontext()


def job_monitor_round(rt) -> None:
    try:
        # 整轮持锁：充值前检查暂停单请求悠悠的那两步和这里错开（暂停单 spec §3.4）
        with _youpin_lock(rt):
            rep = rt.monitor.run_round()
        if rep.checked or rep.errors:
            # 看板顶栏和系统页的「最近一轮」（/api/meta）。夜间闸门跳过的轮什么都没查，不覆盖。
            rt.last_round = {"at": utcnow(), "checked": rep.checked, "alerts_sent": rep.alerts_sent,
                             "errors": rep.errors, "paused": bool(rep.skipped_paused),
                             "gap_gated": rep.gap_gated, "market_gated": rep.market_gated, "notify_failed": rep.notify_failed,
                             "sync_slow": rep.sync_slow}
            # live_confirmed/live_corrected 也打出来：后者是"悠悠上报的排名滞后了"的直接计数
            # （见 AlertSettings.confirm_with_live_queue），是判断这个滞后有多普遍的唯一现场数据。
            log.info("round: checked=%d alerts=%d notify_failed=%d errors=%d paused=%s gap_gated=%d market_gated=%d "
                     "live_confirmed=%d live_corrected=%d fills_resolved=%d fills_notified=%d "
                     "market_checked=%d market_notified=%d",
                     rep.checked, rep.alerts_sent, rep.notify_failed, rep.errors, rep.skipped_paused,
                     rep.gap_gated, rep.market_gated, rep.live_confirmed, rep.live_corrected, rep.fills_resolved,
                     rep.fills_notified, rep.market_checked, rep.market_notified)
            if rep.notify_failed:
                # 单独再打一条 WARNING：只扫 WARNING 以上级别日志的人也不该错过「有告警没送达」。
                log.warning("round: %d alert(s) were raised but NOT delivered; they will re-fire "
                            "after confirm_rounds if the orders are still ranked", rep.notify_failed)
    except Exception:
        log.exception("monitor round crashed")


def job_buff_round(rt) -> None:
    """BUFF 的一轮（BUFF spec §4）。不拿悠悠的锁：BUFF 请求不碰悠悠 client；拿 `buff_lock`，和盈亏任务里的
    BUFF 同步错开（同一个 BUFF client，盈亏 spec §9）。"""
    from skinpulse.services.buff_monitor import run_buff_round
    try:
        lock = getattr(rt, "buff_lock", None)
        with lock if lock is not None else nullcontext():
            rep = run_buff_round(rt.monitor)
        if rep.skipped_night or not rep.accounts:
            return
        rt.last_buff_round = {"at": utcnow(), "checked": rep.checked, "errors": rep.errors, "paused": rep.paused}
        log.info("buff round: accounts=%d synced=%d queues=%d requests=%d checked=%d alerts=%d in_trade=%d "
                 "resolved=%s fills_notified=%d errors=%d paused=%s",
                 rep.accounts, rep.synced, rep.queues, rep.requests, rep.checked, rep.alerts_sent, rep.in_trade,
                 rep.resolved or {}, rep.fills_notified, rep.errors, rep.paused)
    except Exception:
        log.exception("buff round crashed")


def sync_buff_job(sched, rt, *, start_now: bool = False) -> bool:
    """有启用的 BUFF 号才挂 `buff_round`（BUFF spec §4）：只有悠悠号时一个 BUFF 请求都不发。看板加号、停用、
    换 cookie、改 [buff] 设置后调，按现在的状态加上、去掉或改间隔。

    `start_now`：让这一轮马上开始（看板加号、换 cookie 时）。只排**一次**：新挂的任务直接把第一次定在现在，
    已有的任务改下一次的时间。以前是先挂「30 秒后」、再另外改成「现在」，两次撞车，launchd.log 里多一条
    「maximum number of running instances reached」（2026-11-24 上线时看到）。返回这一轮是不是马上开始。"""
    if sched is None:
        return False
    job = sched.get_job("buff_round")
    if not getattr(rt.monitor, "buff_accounts", None):
        if job is not None:
            sched.remove_job("buff_round")
        return False
    minutes = rt.settings.buff.round_interval_min
    if job is None:
        first = utcnow() if start_now else utcnow() + timedelta(seconds=30)
        sched.add_job(job_buff_round, "interval", minutes=minutes, args=[rt], id="buff_round",
                      max_instances=1, coalesce=True, next_run_time=first)
        return start_now
    if getattr(job.trigger, "interval", None) != timedelta(minutes=minutes):
        # 只在间隔真的变了时重排：重排会把下一次推到「现在 + 间隔」
        sched.reschedule_job("buff_round", trigger="interval", minutes=minutes)
    if start_now:
        sched.modify_job("buff_round", next_run_time=utcnow())
    return start_now


def job_price_snapshot(rt) -> None:
    """23:50：给活跃求购单涉及的饰品存一份今天的多平台价格（spec §7a.1）。失败只记日志。"""
    if getattr(rt, "steamdt", None) is None:
        log.info("price snapshot skipped: SteamDT api key not configured")
        return
    try:
        day = local_today(rt.settings.timezone)
        with rt.session_factory() as s:
            extra = holding_template_ids(s) if rt.settings.pnl.enabled else set()
            rep = take_price_snapshot(s, rt.steamdt, day=day, batch_size=rt.settings.steamdt.batch_size,
                                      now=utcnow(), extra_template_ids=extra)
        log.info("price snapshot: %s", rep)
    except Exception:
        log.exception("price snapshot crashed")


def _run_brief(rt, day: date, now: datetime) -> None:
    with _BRIEF_LOCK, rt.session_factory() as s:
        run_daily_brief(s, rt.ntfy, rt.settings, day, now=now)


def job_daily_brief(rt) -> None:
    """00:00：判定并（必要时）推送**昨天**的简报（spec §7a.4）。进程重启后错过的那次由
    `job_daily_brief_catchup` 补，这里的 misfire_grace_time 只管「进程活着但睡着了」。"""
    try:
        now = utcnow()
        _run_brief(rt, local_today(rt.settings.timezone, now) - timedelta(days=1), now)
    except Exception:
        log.exception("daily brief crashed")


def brief_catchup_day(settings, now: datetime) -> date | None:
    """启动时该补哪一天的简报：取最近一个已到点（<= now）的 send_at——今天的还没到就是昨天的，
    send_at 在晚上时刚过零点启动也能补上昨晚那次——没过 12 小时就补它判定的那天；
    过了 12 小时就算了（spec §7a.1）。还没到点的那次交给 cron。"""
    day, due = last_due_brief(settings.timezone, settings.brief.hm("send_at"), now)
    return day if now < due + BRIEF_CATCHUP_WINDOW else None


def job_daily_brief_catchup(rt) -> None:
    """启动后跑一次：00:00 服务没在跑（比如电脑关着）、12 小时内启动的，补判定昨天。
    同一天已有 brief_log 就原样返回，不会重发。"""
    try:
        now = utcnow()
        day = brief_catchup_day(rt.settings, now)
        if day is None:
            log.info("daily brief catch-up: nothing to do (outside the 12h window after send_at)")
            return
        _run_brief(rt, day, now)
    except Exception:
        log.exception("daily brief catch-up crashed")


BRIEF_JOBS = ("price_snapshot", "daily_brief")


def _add_brief_jobs(sched: BackgroundScheduler, rt) -> None:
    """每日简报的两个定时任务：23:50 的价格快照和 00:00 的发送（时间取自 [brief]）。"""
    b = rt.settings.brief
    tz = rt.settings.timezone
    h, m = b.hm("snapshot_at")
    # 最多晚 4 分钟开跑：5 批、每批间隔 65 秒，要约 4.5 分钟，23:54 前开跑才能在 00:00 简报读
    # price_daily 之前写完（晚了简报会读到半份快照）。再晚就算了——跨过零点会记到错的日期上。
    sched.add_job(job_price_snapshot, "cron", hour=h, minute=m, timezone=tz, args=[rt], id="price_snapshot",
                  max_instances=1, coalesce=True, misfire_grace_time=4 * 60)
    h, m = b.hm("send_at")
    # misfire_grace_time 只在进程一直活着、到点时睡着了（休眠）才起作用：内存 jobstore 在
    # add_job 时才算下一次触发，进程 09:00 才启动根本不知道错过了 00:00。那种情况靠 build_scheduler 里的启动补发。
    sched.add_job(job_daily_brief, "cron", hour=h, minute=m, timezone=tz, args=[rt], id="daily_brief",
                  max_instances=1, coalesce=True, misfire_grace_time=12 * 3600)


def _has_valuation(session, day: date) -> bool:
    """这一天是否**算全了**：每个有 SteamDT 映射的活跃模板都有一行。只看「有没有一行」的话，
    00:30 那次算到一半断掉（睡眠、重启），剩下的就永远补不上（评审 2026-09-28）。"""
    want = session.scalar(select(func.count(func.distinct(MyBuyOrder.template_id)))
                          .join(Item, Item.youpin_template_id == MyBuyOrder.template_id)
                          .where(MyBuyOrder.active.is_(True), youpin_orders()))
    have = session.scalar(select(func.count()).select_from(Valuation).where(Valuation.date == day.isoformat()))
    return have >= (want or 0) and (have > 0 or not want)


def valuation_due(settings, now: datetime) -> tuple[date, datetime]:
    """最近一个已到点的 run_at，以及它估值的那天（刚结束的本地日）。和简报同一套算法。"""
    return last_due_brief(settings.timezone, settings.valuation.hm(), now)


def _run_valuation(rt, day: date, now: datetime, *, only_if_missing: bool) -> None:
    with _VALUATION_LOCK, rt.session_factory() as s:
        if only_if_missing and _has_valuation(s, day):
            log.info("valuation catch-up: %s already valued", day)
            return
        rep = run_valuation(s, rt.steamdt, getattr(rt, "market", None), rt.settings, day=day, now=now)
    log.info("valuation: %s", rep)


def job_valuation(rt) -> None:
    """每天 run_at（默认 00:30）：给刚结束的那天估值（价位 spec §5）。失败只记日志，不影响监控。"""
    if getattr(rt, "steamdt", None) is None:
        log.info("valuation skipped: SteamDT api key not configured")
        return
    try:
        now = utcnow()
        _run_valuation(rt, valuation_due(rt.settings, now)[0], now, only_if_missing=False)
    except Exception:
        log.exception("valuation crashed")


def job_valuation_catchup(rt) -> None:
    """启动后跑一次：run_at 时服务没在跑（电脑睡着、关着）、12 小时内启动的，补估那天；已有就不重算。"""
    if getattr(rt, "steamdt", None) is None:
        return
    try:
        now = utcnow()
        day, due = valuation_due(rt.settings, now)
        if now >= due + VALUATION_CATCHUP_WINDOW:
            log.info("valuation catch-up: nothing to do (outside the 12h window after run_at)")
            return
        _run_valuation(rt, day, now, only_if_missing=True)
    except Exception:
        log.exception("valuation crashed (catch-up)")


def _add_valuation_jobs(sched: BackgroundScheduler, rt) -> None:
    h, m = rt.settings.valuation.hm()
    sched.add_job(job_valuation, "cron", hour=h, minute=m, timezone=rt.settings.timezone, args=[rt],
                  id="valuation", max_instances=1, coalesce=True, misfire_grace_time=12 * 3600)
    sched.add_job(job_valuation_catchup, "date", args=[rt], id="valuation_catchup",
                  max_instances=1, misfire_grace_time=None)


def pnl_due(settings, now: datetime) -> tuple[date, datetime]:
    """最近一个已到点的盈亏 run_at，以及它算的那天（刚结束的本地日）。和估值同一套算法。"""
    return last_due_brief(settings.timezone, settings.pnl.hm(), now)


def _run_pnl(rt, day: date, now: datetime, *, only_if_missing: bool) -> None:
    with _PNL_LOCK:
        if only_if_missing:
            with rt.session_factory() as s:
                if has_snapshot(s, day, getattr(rt, "accounts", {}) or {}):
                    log.info("pnl catch-up: %s already done", day)
                    return
        rep = run_pnl(rt, day, now)
    log.info("%s", rep)


def job_pnl(rt) -> None:
    """每天 run_at（默认 00:45）：同步悠悠买卖订单、库存、租出，算刚结束那天的持仓快照。失败只记日志。"""
    try:
        now = utcnow()
        _run_pnl(rt, pnl_due(rt.settings, now)[0], now, only_if_missing=False)
    except Exception as e:
        log.warning("pnl crashed (%s)", type(e).__name__)


def job_pnl_catchup(rt) -> None:
    """启动后跑一次：run_at 时服务没在跑、12 小时内启动的，补算那天；已有就不重算。"""
    try:
        now = utcnow()
        day, due = pnl_due(rt.settings, now)
        if now >= due + PNL_CATCHUP_WINDOW:
            log.info("pnl catch-up: nothing to do (outside the 12h window after run_at)")
            return
        _run_pnl(rt, day, now, only_if_missing=True)
    except Exception as e:
        log.warning("pnl crashed (catch-up, %s)", type(e).__name__)


def _add_pnl_jobs(sched: BackgroundScheduler, rt) -> None:
    h, m = rt.settings.pnl.hm()
    sched.add_job(job_pnl, "cron", hour=h, minute=m, timezone=rt.settings.timezone, args=[rt],
                  id="pnl", max_instances=1, coalesce=True, misfire_grace_time=12 * 3600)
    sched.add_job(job_pnl_catchup, "date", args=[rt], id="pnl_catchup",
                  max_instances=1, misfire_grace_time=None)


def job_paused_check(rt, run_id: int) -> None:
    """看板「检查一次」：后台跑一次充值前检查暂停单（暂停单 spec §3.4）。run_check 自己兜住所有异常。"""
    res = run_paused_check(rt.monitor, run_id, lock=_youpin_lock(rt))
    log.info("paused check %d: %s, %d order(s), skipped accounts %s", run_id, res.status, res.rows, res.skipped or "-")


def schedule_paused_check(sched: BackgroundScheduler, rt, run_id: int) -> None:
    sched.add_job(job_paused_check, "date", args=[rt, run_id], id=f"paused_check_{run_id}",
                  max_instances=1, misfire_grace_time=None)


def build_scheduler(rt) -> BackgroundScheduler:
    """Build the round/heartbeat scheduler, sweeping dangling "pending" notifications
    first.

    That sweep lives here and nowhere else. A crash between a round's per-order commit
    and its end-of-round notification stage leaves a NotificationLog row at "pending"
    with its AlertStateRow still ALERTING, which `AlertEngine.step` would never fire
    again - so it has to be cleared before the first round. But it is destructive to any
    notification currently in flight, so only the process that is about to *own* the
    rounds may run it. It used to sit in `build_runtime`, which every one-shot CLI
    command goes through; see the comment there for the day that bit us.

    Right after it, rows left ALERTING outside the current `enter_rank` (it may have been
    lowered while the service was down) go back to NORMAL - see
    `reset_alerting_above_enter_rank`. Same rule: only here, never in `build_runtime`.
    The sweep runs first, so the reset only ever sees rows with no dead in-flight push.
    """
    with rt.session_factory() as s:
        recover_pending(s)
        reset_alerting_above_enter_rank(s, rt.settings.alerts.enter_rank)
        # 上次进程没跑完的暂停单检查（同选品）
        if n := mark_paused_interrupted(s, utcnow()):
            log.info("paused check: marked %d unfinished run(s) as interrupted", n)
    p = rt.settings.polling
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(job_monitor_round, "interval", minutes=p.round_interval_min, args=[rt], id="monitor_round",
                  max_instances=1, coalesce=True)
    sched.add_job(job_heartbeat, "interval", minutes=p.heartbeat_interval_min, args=[rt], id="heartbeat",
                  max_instances=1, coalesce=True)
    if rt.settings.brief.enabled:
        _add_brief_jobs(sched, rt)
        # 启动后立即跑一次：send_at 之后 12 小时内启动的，补判定昨天；brief_log 按日期唯一，不会重复发。
        # misfire_grace_time=None：start() 晚于 add_job 多久都照跑（默认 1 秒会被当成错过丢掉）。
        sched.add_job(job_daily_brief_catchup, "date", args=[rt], id="daily_brief_catchup",
                      max_instances=1, misfire_grace_time=None)
    if rt.settings.valuation.enabled:
        # 一期没有网页配置，reschedule 不管它：只在启动时注册
        _add_valuation_jobs(sched, rt)
    if rt.settings.pnl.enabled:
        # 和估值一样只在启动时注册（没有网页配置）
        _add_pnl_jobs(sched, rt)
    sync_buff_job(sched, rt)
    return sched


def reschedule(sched: BackgroundScheduler, rt, *, brief: bool = True) -> None:
    """看板改了设置之后按新值重排，不用重启：监控、心跳的间隔，以及（`brief` 为真时）简报的开关和时间。

    简报的两个任务只在 [brief] 真的改了时才重建：重建会按「现在」重新算下一次触发，睡眠醒来后
    还没来得及补跑的那一次会被丢掉。"""
    p = rt.settings.polling
    sched.reschedule_job("monitor_round", trigger="interval", minutes=p.round_interval_min)
    sched.reschedule_job("heartbeat", trigger="interval", minutes=p.heartbeat_interval_min)
    if not brief:
        return
    for job_id in BRIEF_JOBS:
        if sched.get_job(job_id) is not None:
            sched.remove_job(job_id)
    if rt.settings.brief.enabled:
        _add_brief_jobs(sched, rt)
