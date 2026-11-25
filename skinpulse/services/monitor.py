from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable
from zoneinfo import ZoneInfo

from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session

from skinpulse.adapters.ntfy.client import NtfyError
from skinpulse.adapters.youpin.errors import (
    LoginRequired,
    MachineRoomBlocked,
    RateLimited,
    RiskControl,
    UkExpired,
    YoupinError,
)
from skinpulse.analysis.reach import Reason, reach_kwargs, unreachable
from skinpulse.config import Settings
from skinpulse.core.alerts import AlertEngine, AlertEvent, AlertState, HourlyCap, OrderAlert
from skinpulse.core.market_alert import flag_label, market_flags
from skinpulse.core.messages import (
    MarketView, build_market_message,
    OrderView, TradeView, build_enter_top2_message, build_fill_message, build_in_trade_message, queue_label_of,
)
from skinpulse.core.planner import _is_night
from skinpulse.core.rank import (
    AGREED, DISAGREED, RankResult, compute_rank, queue_key_from_str, verification_outcome,
)
from skinpulse.core.ratelimit import BackoffGuard, TokenBucket, jittered
from skinpulse.db.models import (
    Account, AlertStateRow, MyBuyOrder, NotificationLog, OrderEvent, RankCheck, RankSnapshot,
)
from skinpulse.db.session import utcnow
from skinpulse.services import status as st
from skinpulse.services.account import AccountRuntime, account_status_key, notify_login_lost, sync_my_orders
from skinpulse.services.buff import buff_account_ids, buff_goods_url, goods_id_of
from skinpulse.core.buff_rank import is_buff
from skinpulse.services.fills import fill_qty, fill_time, resolve_gone
from skinpulse.services.notifier import AllChannelsFailed

log = logging.getLogger(__name__)
MARKET_PROBE_AFTER = timedelta(minutes=30)   # 市价提醒：在售价旧过这么久的第 2～5 名单，实时探一下
ANON = "youpin_anon"  # public/anonymous queue channel - pass B (sampled verification)
# authenticated my-orders channel - pass A (sync_my_orders). 多账号后按号分 key（多账号 spec §5），这是号 1 的
ACCOUNT = account_status_key(1)
# Pseudo-adapter: not a real request channel, never paused. Reuses SystemStatus purely as
# durable storage for the timestamp of the last round that actually ran (in last_ok_at),
# so the night gate (see Monitor._should_skip_for_night) survives a process restart
# instead of resetting to "never gated" every time. Deliberately absent from web/app.py's
# ADAPTERS tuple, so it never shows up on the /status page.
ROUND = "monitor_round"
# Sentinel for "never verified" in the pass-B rotation sort key (see
# Monitor._select_verify_targets): sorts before every real timestamp, so an order that
# has never been sampled is always at least as overdue as one that has.
_NEVER_VERIFIED = datetime.min.replace(tzinfo=timezone.utc)
# 查去向翻到页数上限还没找到的消失单，过这么久再查（Monitor._resolve_fills）
RESOLVE_RETRY_AFTER = timedelta(hours=1)


@dataclass
class RoundReport:
    checked: int = 0
    alerts_sent: int = 0
    skipped_paused: bool = False
    # Night gate (see Monitor._should_skip_for_night): the round was skipped entirely
    # because it's within the configured quiet hours and not enough time has elapsed
    # since the last one that ran. Mutually exclusive with skipped_paused in practice -
    # the night gate is checked first and returns before either channel is touched.
    skipped_night: bool = False
    errors: int = 0
    # Pass B (sampled verification against the public queue - see run_round below).
    verified: int = 0
    verify_agreed: int = 0
    verify_disagreed: int = 0
    # 抽样核对没能给出结论的次数：公开队列那一页被 visible_limit 截断了，自己的单排在页外，
    # 于是算不出本地排名。这既不是一致也不是不一致（见 core.rank.verification_outcome），
    # 单独计数，免得把我们的视野盲区算成悠悠的错。
    verify_inconclusive: int = 0
    # 本轮给几张即将告警的单取到了「别处买盘」（SteamDT，见 _refresh_reference_bids）。
    ref_bids_fetched: int = 0
    # 本轮生成了、但**没能送到用户手上**的告警事件数（ntfy 投递失败）。
    #
    # 这个字段存在的原因：2026-09-22 夜里一条「MP7 笑一个 第 2 名」连续三次投递失败，那三轮的
    # 摘要写的都是 `alerts=0 errors=0`——只看摘要完全看不出有告警没送达，得去翻单独的 ERROR
    # 行。这个项目最该防的就是这种静默失败。被 hourly cap 或「没配 ntfy」挡下的不算在这里：
    # 那是有意不发，不是没发出去。
    notify_failed: int = 0
    # 推送前用公开队列实时核对（_confirm_with_live_queue）成功复核了几张单，以及其中有几张
    # 的排名和悠悠上报的不一致——后者是"悠悠的值滞后了"的直接计数。
    live_confirmed: int = 0
    live_corrected: int = 0
    # 市场价刷新（_refresh_sell_prices）本轮成功刷新了几张单的在售最低价。
    sell_refreshed: int = 0
    # Gap filter (AlertSettings.max_gap_ratio, see Monitor._is_eligible_for_alert and
    # AlertEngine.step's `eligible` parameter): how many active orders this round had a
    # leading bid at least max_gap_ratio times their own price, and so were held back
    # from alerting even though their reported rank sits inside the alerting band.
    gap_gated: int = 0
    # 离市价太远（难以成交 spec §3，AlertSettings.max_market_ratio / min_market_gap）而没进告警的单数；
    # 两条都成立的单只算在 gap_gated 里（离第 1 先判）。
    market_gated: int = 0
    # 成交推送（成交推送 spec §4）：这一轮查清去向的消失单数，和推出去的交易中/成交单数
    fills_resolved: int = 0
    fills_notified: int = 0
    # 市价提醒（市价提醒 spec §2）：推送前实时核对了几张单的在售价，推出去几张
    market_checked: int = 0
    market_notified: int = 0
    # 一轮里同步所有号（含号间间隔）花了多久；超过轮间隔的一半就是「一轮快跑不完了」（多账号 spec §5）
    sync_seconds: float = 0.0
    sync_slow: bool = False


def latest_snapshot(session: Session, purchase_no: str) -> RankSnapshot | None:
    return session.scalar(select(RankSnapshot).where(RankSnapshot.purchase_no == purchase_no)
                          .order_by(desc(RankSnapshot.ts)).limit(1))


def order_view(order: MyBuyOrder, snap: RankSnapshot | None) -> OrderView:
    k = queue_key_from_str(order.queue_key)
    return OrderView(purchase_no=order.purchase_no, commodity_name=order.commodity_name,
                     template_id=order.template_id, queue_label=queue_label_of(k.abrade, k.type_id, k.special, k.fade),
                     my_price=order.price, rank=snap.rank if snap else None,
                     top1_price=snap.top1_price if snap else None, top2_price=snap.top2_price if snap else None,
                     top1_qty=snap.top1_qty if snap else None,
                     # 市场价读的是订单行而不是快照：它是抽样轮换刷新的，本轮没轮到这张单
                     # 不代表我们不知道它的市场价，读快照会让看板上的值每轮闪烁。
                     sell_min=order.sell_min, sell_depth=order.sell_depth,
                     ref_bid=order.ref_bid, ref_bid_platforms=order.ref_bid_platforms,
                     remaining_qty=order.remaining_qty,
                     # BUFF 单：点开跳 BUFF 商品页、带上挡路的条件单（BUFF spec §7）
                     url=buff_goods_url(goods_id_of(order)) if is_buff(order.purchase_no) else None,
                     cond_ahead=order.cond_ahead if is_buff(order.purchase_no) else None)


def _queue_label(order: MyBuyOrder) -> str:
    k = queue_key_from_str(order.queue_key)
    return queue_label_of(k.abrade, k.type_id, k.special, k.fade)


def _reset_alert_state(session: Session, purchase_no: str, now: datetime) -> None:
    """Shared by `recover_pending` and `Monitor._notify`'s failed/suppressed paths
    (finding 2, and its "still stuck ALERTING" follow-on): a notification that never
    actually reached the user must not leave `AlertStateRow` durably ALERTING, or
    `AlertEngine.step` returns None on every later round until the order falls below
    `exit_rank` and climbs back into the top 2 - silently swallowing the alert. Reset
    to NORMAL/streak=0 so `confirm_rounds` re-applies and the alert can fire again."""
    row = session.get(AlertStateRow, purchase_no)
    if row is not None:
        row.state = AlertState.NORMAL.value
        row.streak = 0
        row.updated_at = now


def recover_pending(session: Session) -> int:
    """Finding 2: `_notify` finalizes each round's `NotificationLog` rows from
    "pending" to "sent"/"failed"/"suppressed", but if the process crashes between the
    per-order commit that writes a "pending" row (together with the ALERTING state
    transition it accompanies) and that end-of-round `_notify` call, the row dangles
    at "pending" forever - `AlertStateRow` stays ALERTING, `AlertEngine.step` returns
    None on every later round (it only fires on a NORMAL->ALERTING transition), and the
    user never receives that alert.

    Sweep every row still stuck at "pending", mark it "failed" (it was demonstrably
    never published - nothing still running today could finish publishing a row from
    a previous, dead process), and reset its matching AlertStateRow to NORMAL with
    streak=0 so the alert can fire again on the next round if the order is still
    ranked within `alerts.enter_rank`. Returns the number of rows recovered.

    Called once per service start by `scheduler.build_scheduler`, before the first
    round - and only there (CLAUDE.md pitfall 5: a one-shot CLI command running next
    to the service must not sweep the service's in-flight rows).
    """
    rows = session.scalars(select(NotificationLog).where(NotificationLog.status == "pending")).all()
    count = 0
    for row in rows:
        row.status = "failed"
        _reset_alert_state(session, row.fingerprint, utcnow())
        count += 1
    if count:
        session.commit()
        log.warning("recovered %d dangling pending notification(s)", count)
    return count


def reset_alerting_above_enter_rank(session: Session, enter_rank: int) -> int:
    """Put every AlertStateRow that is ALERTING with `last_rank > enter_rank` back to a
    fresh NORMAL (streak 0). Returns how many rows changed.

    Why: `enter_rank` can shrink (ADR-0008 took it from 2 to 1, exit_rank stays 4). A row
    that went ALERTING at rank 2 under the old threshold stays ALERTING for ranks 1-3
    (`AlertEngine.step` only leaves ALERTING at exit_rank or worse), so the order reaching
    rank 1 - exactly the alert the new threshold exists for - would push nothing until it
    first dropped to rank 4. Rows at or inside the new band are left alone: they already
    alerted for a rank the new threshold would alert on too.

    Callers: `scheduler.build_scheduler` at service start (after `recover_pending`; the
    old value is unknown there, so it always runs and is a no-op when nothing sits
    outside the band), and `Monitor.apply_settings` when the new value is smaller. Never
    `build_runtime`: a one-shot CLI command must not mutate alert state while the service
    runs (CLAUDE.md pitfall 5).

    It cannot strand a notification: it only moves ALERTING -> NORMAL and never touches
    `notification_log`. A still-pending row is finalized as before - by the in-flight
    round's `_notify`, or at startup by `recover_pending`, which runs first.

    Known, accepted side effect: with enter_rank=1, an order that alerted at rank 1 and now
    sits at rank 2-3 is reset on every service restart, so it can alert once more if it
    climbs back to 1 afterwards. Restarts are rare; a duplicate beats a lost alert.
    """
    rows = session.scalars(select(AlertStateRow).where(AlertStateRow.state == AlertState.ALERTING.value,
                                                       AlertStateRow.last_rank > enter_rank)).all()
    now = utcnow()
    for row in rows:
        row.state = AlertState.NORMAL.value
        row.streak = 0
        row.updated_at = now
    if rows:
        session.commit()
        log.info("reset %d alerting order(s) ranked below enter_rank=%d", len(rows), enter_rank)
    return len(rows)


class Monitor:
    def __init__(self, session_factory, client, ntfy, settings: Settings, *,
                 steamdt=None, accounts: dict[int, AccountRuntime] | None = None,
                 buff_accounts: dict | None = None,
                 now: Callable[[], datetime] = utcnow, local_hour: Callable[[], int] | None = None,
                 sleep: Callable[[float], None] = time.sleep, rnd: Callable[[], float] = random.random) -> None:
        self.Session = session_factory
        # 匿名通道（公开队列、在售价）用的 client：第一个启用的号的（多账号 spec §3）
        self.client = client
        # 启用的号（多账号 spec §4）。服务里和 rt.accounts 是同一个 dict，看板加号、停用时原地改；
        # 不传（测试、老调用方）就是只有号 1，client 就是上面那个。
        self._accounts = accounts
        # 启用的 BUFF 号（BUFF spec §4）：不放进上面那份（那份全是悠悠 client），单独一份，看板加号、停用时整个换掉
        self.buff_accounts: dict = buff_accounts if buff_accounts is not None else {}
        self._uids: dict[int, int] = {}
        self._queue_cache: dict[tuple[int, int], list] = {}
        self.ntfy = ntfy
        # SteamDT 客户端是可选的：没配 API key 时一切照旧，只是告警里少「别处买盘」那一行。
        # 它绝不能成为告警的前置条件——行情是锦上添花，排名才是这个项目的本职。
        self.steamdt = steamdt
        self.s = settings
        self.now = now
        self.local_hour = local_hour or (lambda: datetime.now(ZoneInfo(self.s.timezone)).hour)
        self.sleep = sleep
        self.rnd = rnd
        cap = settings.polling.hourly_request_cap
        self.bucket = TokenBucket(capacity=cap, refill_per_s=cap / 3600.0)
        self.guard = BackoffGuard()
        self.engine = AlertEngine(settings.alerts.enter_rank, settings.alerts.exit_rank, settings.alerts.confirm_rounds)
        self.hourly_cap = HourlyCap(settings.alerts.hourly_cap)
        self._resolve_backoff: dict[int, datetime] = {}

    def apply_settings(self, new_settings: Settings) -> None:
        """Bring a running Monitor into line with a settings edit made through the web
        UI. Several values are read live off `self.s` on every use, so reassigning it
        is enough:
        - `_pace` reads polling interval and jitter directly
        - `run_round`'s pass B reads visible_limit for its sampled fetches and
          verify_sample_size/verify_enabled/fast_lane_rank_threshold for what it selects
        - `run_round`'s night gate (`_should_skip_for_night`) reads
          night_start_hour/night_end_hour/night_multiplier/round_interval_min live, every
          call - it is not captured anywhere at construction
        - `_notify` reads ntfy config for message formatting
        - The default `local_hour` callable reads timezone at call time

        But `engine`, `bucket`, and `hourly_cap` are built once in `__init__` from
        that same settings object and then carry their own state independently of
        `self.s` - a plain reassignment leaves them silently stale until the process
        restarts (the finding this method fixes).

        AlertEngine is stateless (per-order state lives in AlertStateRow), so it is
        cheap and correct to rebuild unconditionally on every settings edit. When
        `enter_rank` shrinks, rows left ALERTING outside the new band are reset too
        (`reset_alerting_above_enter_rank`), or they would swallow the next alert.

        TokenBucket and HourlyCap are not stateless: they carry, respectively, the
        accumulated request budget and the sliding window of notifications already
        sent this hour. Rebuilding either on every edit - even one that never touched
        its cap - would hand back a full budget or clear the window, letting a round
        exceed the pacing/notification caps those objects exist to enforce. So each is
        rebuilt only when the specific setting it derives from actually changed.

        An explicitly-supplied `local_hour` override (passed to `__init__`) is never
        touched - it survives settings edits unchanged.
        """
        old = self.s
        self.s = new_settings
        self.engine = AlertEngine(new_settings.alerts.enter_rank, new_settings.alerts.exit_rank,
                                  new_settings.alerts.confirm_rounds)
        if new_settings.polling.hourly_request_cap != old.polling.hourly_request_cap:
            cap = new_settings.polling.hourly_request_cap
            self.bucket = TokenBucket(capacity=cap, refill_per_s=cap / 3600.0)
        if new_settings.alerts.hourly_cap != old.alerts.hourly_cap:
            self.hourly_cap = HourlyCap(new_settings.alerts.hourly_cap)
        if new_settings.alerts.enter_rank < old.alerts.enter_rank:
            # Settings are already applied in memory; a DB hiccup here must not fail the web
            # request (the file would then not be saved). The next service start redoes this.
            try:
                with self.Session() as session:
                    reset_alerting_above_enter_rank(session, new_settings.alerts.enter_rank)
            except Exception as e:  # noqa: BLE001
                log.warning("resetting alert state after enter_rank shrank failed: %s", type(e).__name__)

    @property
    def accounts(self) -> dict[int, AccountRuntime]:
        if self._accounts is None:
            return {1: AccountRuntime(1, self.client)}
        return self._accounts

    def _label_of(self, account_id: int) -> str | None:
        """推送里的简称：悠悠号在启用的悠悠号 ≥ 2 时才有（多账号 spec §6），一个号时 None，文案一字不改；
        BUFF 号的单一律带简称（BUFF spec §7）。"""
        b = self.buff_accounts.get(account_id)
        if b is not None:
            return b.label or "BUFF"
        accs = self.accounts
        if len(accs) < 2:
            return None
        a = accs.get(account_id)
        return a.label if a is not None and a.label else None

    def _pause_accounts(self, session: Session, code: str, kind: str) -> None:
        """风控、机房拦截多半按 IP / 设备来：一个号撞上就暂停所有号（多账号 spec §5）。退避只算一次。"""
        secs = self.guard.on_error(kind)
        until = self.now() + timedelta(seconds=secs) if secs else None
        for a in self.accounts.values():
            try:
                st.mark_error(session, a.status_key, code, until)
            except Exception:
                log.exception("failed to persist channel pause (adapter=%s, code=%s, kind=%s); "
                              "continuing with in-memory backoff only", a.status_key, code, kind)
                session.rollback()

    def _sync_one(self, session: Session, acc: AccountRuntime, rep: RoundReport) -> str:
        """同步一个号：返回 ok / skip（只影响这个号）/ halt（所有号都停，这一轮不再同步别的号）。"""
        try:
            sync_my_orders(session, acc)
        except LoginRequired as e:
            # 这个号的 token 失效：只标它、只推它的提醒，别的号照常（多账号 spec §5）。和 job_heartbeat
            # 共用一份提醒状态，谁先发现都只推一条。
            rep.errors += 1
            self._safe_mark_error(session, acc.status_key, e.code)
            notify_login_lost(self.ntfy, self.s.ntfy, acc, multi=len(self.accounts) >= 2)
            return "skip"
        except (RiskControl, MachineRoomBlocked) as e:
            rep.errors += 1
            self._pause_accounts(session, e.code, "risk_control" if isinstance(e, RiskControl) else "other")
            return "halt"
        except RateLimited as e:
            rep.errors += 1; self._pause(session, acc.status_key, e.code, "rate_limited"); return "skip"
        except UkExpired as e:
            rep.errors += 1
            acc.client.uk = None
            self._safe_mark_error(session, acc.status_key, e.code)
            return "skip"
        except YoupinError as e:
            rep.errors += 1
            self._safe_mark_error(session, acc.status_key, e.code)
            return "skip"
        except Exception:
            # 同步是这个号这一轮唯一的新数据来源：意外失败就跳过这个号，不拿旧数据往下算
            rep.errors += 1
            log.exception("unexpected error during account sync (account %s); skipping it this round", acc.id)
            session.rollback()
            return "skip"
        st.mark_ok(session, acc.status_key)
        return "ok"

    # ---- helpers ----
    def _alert_row(self, session: Session, purchase_no: str) -> AlertStateRow:
        row = session.get(AlertStateRow, purchase_no)
        if row is None:
            row = AlertStateRow(purchase_no=purchase_no, state="NORMAL", streak=0, updated_at=self.now())
            session.add(row)
        return row

    def _pause(self, session: Session, adapter: str, code: str, kind: str) -> None:
        secs = self.guard.on_error(kind)
        try:
            st.mark_error(session, adapter, code, self.now() + timedelta(seconds=secs) if secs else None)
        except Exception:
            # A DB error while persisting a channel-wide pause (e.g. the dashboard
            # holding a concurrent SQLite write lock) must not escape run_round: the
            # caller still aborts right after this returns, using the in-memory
            # BackoffGuard state already updated above - only the durable SystemStatus
            # audit trail (last_error_code/paused_until) is stale until the next
            # successful write.
            log.exception("failed to persist channel pause (adapter=%s, code=%s, kind=%s); "
                          "continuing with in-memory backoff only", adapter, code, kind)
            session.rollback()

    def _safe_mark_error(self, session: Session, adapter: str, code: str) -> None:
        """Like `_pause`, but for a failure that doesn't call for a fixed backoff window:
        UkExpired resolves itself on the very next request (a fresh `uk` is fetched
        unconditionally once the cached one is cleared), and a generic YoupinError or a
        lost login isn't something a timer fixes. Same DB-safety wrapper as `_pause` -
        a failure here must not escape and discard the round's report."""
        try:
            st.mark_error(session, adapter, code, None)
        except Exception:
            log.exception("failed to record %s error (code=%s); continuing", adapter, code)
            session.rollback()

    def _pace(self) -> None:
        self.sleep(jittered(self.s.polling.request_interval_s, self.s.polling.jitter_pct, self.rnd))

    def _unreachable(self, o: MyBuyOrder, top_bid: Decimal | None = None) -> Reason | None:
        """难以成交的原因（analysis/reach.unreachable）：离第 1 太远（AlertSettings.max_gap_ratio），或离市价
        太远（max_market_ratio 且 min_market_gap）。是的话这张单不进告警（AlertEngine.step 的 `eligible`：
        必须在状态机之前挡，不能事后丢事件，否则单子会卡在 ALERTING）。

        阈值每次从 `self.s` 现读（见 apply_settings），改设置下一轮就生效。有实时核对拿到的榜首价就用它：
        悠悠上报的 top_bid 可能落后几分钟（AlertSettings.confirm_with_live_queue）。市价读订单行上的
        sell_min / sell_depth / ref_bid（抽样轮换刷新，和看板、简报读的是同一份）。判断不了的不挡。"""
        top = o.top_bid if top_bid is None else top_bid
        return unreachable(o.price, top, o.sell_min, o.sell_depth, o.ref_bid, **reach_kwargs(self.s.alerts))

    def _is_eligible_for_alert(self, o: MyBuyOrder, top_bid: Decimal | None = None) -> bool:
        return self._unreachable(o, top_bid) is None

    def _skip_order(self, session: Session, o: MyBuyOrder, code: str | None, *, wait: Callable[[], None]) -> None:
        """Common tail for a per-order 'skip and continue' error during pass B (sampled
        verification - see run_round): record the error against SystemStatus unless
        `code` is None (the non-YouPin catch-all, which already logged via
        log.exception and has no YouPin error code to attribute), then touch
        last_checked_at (harmless - pass A already stamped it this round) and pace (or
        apply its own backoff) before returning to the loop for the next sample.
        Deliberately does NOT touch last_verified_at: a failed verification attempt
        should stay maximally overdue so `_select_verify_targets` retries it before
        anything already successfully verified. Callers that already rolled back a
        partial write must do so before calling this, so the timestamp survives it.

        No circuit breaker here: pacing (`_pace` after every attempt) already bounds
        the cost of an error storm across the (small, capped by verify_sample_size)
        sample; the genuinely channel-wide conditions (RiskControl, RateLimited,
        MachineRoomBlocked) already abort the sampling loop immediately.
        """
        try:
            if code is not None:
                st.mark_error(session, ANON, code, None)
            o.last_checked_at = self.now()
            session.commit()
        except Exception:
            # A DB error while recording this order's skip (e.g. a concurrent
            # dashboard write locking SQLite) must not escape run_round: an event
            # already committed for a *different* order earlier in this same round
            # (finding 2's ALERTING + pending NotificationLog write, see
            # _reset_alert_state's docstring) must still reach `_notify` at the end of
            # the loop, and every other sample behind this one must still get its turn.
            # Roll back so the session is clean for the next iteration; this order
            # simply keeps its previous last_checked_at and is eligible again next
            # round - not ideal, but far better than silently discarding the whole
            # round's report (and every alert already queued in it).
            log.exception("failed to record skip for order %s; continuing round", o.purchase_no)
            session.rollback()
        wait()

    def _should_skip_for_night(self, session: Session) -> bool:
        """Night gate (revived polling.night_start_hour/night_end_hour/night_multiplier -
        see core.planner._is_night): during the configured quiet-hours window, only let
        the round actually proceed once night_multiplier x round_interval_min minutes
        have elapsed since the last round that ran, instead of every round_interval_min
        minutes like the rest of the day. Outside the window this always returns False -
        the normal APScheduler-driven interval (see scheduler.py) applies unmodified.

        Reads settings live off `self.s` (see apply_settings), and reads the comparison
        timestamp from SystemStatus (adapter=ROUND) rather than anything kept in memory,
        so neither a settings edit nor a process restart can defeat the gate.
        """
        p = self.s.polling
        if not _is_night(self.local_hour(), p):
            return False
        row = st.get_status(session, ROUND)
        if row is None or row.last_ok_at is None:
            return False  # never run before - let it run now rather than gate forever
        elapsed_s = (self.now() - row.last_ok_at).total_seconds()
        return elapsed_s < p.round_interval_min * 60 * p.night_multiplier

    # ---- main ----
    def pause_after_error(self, session: Session, acc: AccountRuntime, e: Exception) -> str:
        """别的任务（盈亏）在账号通道上撞到错误时，按同步的规矩处理（多账号 spec §5）：风控、机房拦截暂停
        所有号，返回 "halt"；限流只暂停这个号，返回 "skip"；其他错误只记一笔，返回 "skip"。"""
        if isinstance(e, (RiskControl, MachineRoomBlocked)):
            self._pause_accounts(session, e.code, "risk_control" if isinstance(e, RiskControl) else "other")
            return "halt"
        if isinstance(e, RateLimited):
            self._pause(session, acc.status_key, e.code, "rate_limited")
            return "skip"
        if isinstance(e, YoupinError):
            self._safe_mark_error(session, acc.status_key, e.code)
        return "skip"

    def run_round(self) -> RoundReport:
        """Pass A (authoritative, one authenticated request): sync_my_orders refreshes
        every order's YouPin-reported rank in one call, then every active order gets a
        RankSnapshot and an alert-engine step from that reported rank directly - no more
        one public-queue request per order.

        Pass B (sampled verification, unauthenticated/anonymous): a handful of orders
        are re-derived the old way (public queue + compute_rank) and checked against
        what pass A just reported. This started out as the open question "does YouPin's
        `rank` count position in the whole template list or within the same-filter
        sub-queue?" - answered on 2026-09-07: it is the sub-queue, confirmed on three
        orders whose two readings differ (docs/research/2026-09-06-live-api-findings.md
        §5). Pass B stays on as a sentinel, so a change in that semantics shows up as
        disagreements instead of silently wrong alerts. A pass-B failure (of any kind,
        including a channel-wide pause) never undoes pass A: alerts computed from the
        authoritative sync are notified regardless.

        Before any of that: the night gate (_should_skip_for_night) can skip the round
        entirely during configured quiet hours - checked first, so a gated round touches
        neither channel.
        """
        rep = RoundReport()
        events: list[tuple[AlertEvent, OrderView]] = []
        with self.Session() as session:
            if self._should_skip_for_night(session):
                rep.skipped_night = True
                return rep
            # Stamp durably as soon as the gate lets the round through - regardless of
            # what happens next (even a channel pause below) - so the gate always
            # measures "time since the round last got this far", not "time since it
            # last fully succeeded".
            st.mark_ok(session, ROUND)

            # ---- 按号依次同步（多账号 spec §5）：一个号的错误只影响它自己 ----
            ok_ids: list[int] = []
            started = self.now()
            for i, acc in enumerate(sorted(self.accounts.values(), key=lambda a: a.id)):
                if st.is_paused(session, acc.status_key, self.now()):
                    rep.skipped_paused = True
                    continue
                if ok_ids or i:
                    self.sleep(self.s.polling.account_gap_s)
                result = self._sync_one(session, acc, rep)
                if result == "halt":
                    break
                if result == "ok":
                    ok_ids.append(acc.id)
            rep.sync_seconds = (self.now() - started).total_seconds()
            if rep.sync_seconds > self.s.polling.round_interval_min * 60 / 2:
                rep.sync_slow = True
                log.warning("syncing %d account(s) took %.0fs, over half the %d-minute round interval",
                            len(self.accounts), rep.sync_seconds, self.s.polling.round_interval_min)
            if not ok_ids:
                return rep
            rep.skipped_paused = False

            # ---- pass A: write a RankSnapshot + step the alert engine from the sync we just ran ----
            # 只处理这一轮同步成功的号的单：同步失败的号不拿上一轮的名次再判一次（多账号 spec §5）
            self._uids = {a.id: a.user_id or 0 for a in session.scalars(select(Account))}
            self._queue_cache = {}
            now = self.now()
            orders = list(session.scalars(select(MyBuyOrder).where(MyBuyOrder.active.is_(True),
                                                                   MyBuyOrder.account_id.in_(ok_ids))))
            seen: dict[str, tuple[int | None, Decimal | None]] = {}   # 这一轮写进快照的名次、榜首（市价提醒用）
            # 推送之前先把"即将告警"的那几张单拿公开队列重算一遍（见 AlertSettings
            # .confirm_with_live_queue）：悠悠上报的 rank/榜首价滞后，而告警整个建在它上面。
            live = self._confirm_with_live_queue(session, orders, rep)
            # 紧接着给同一批候选单补上「别处买盘」，好让告警正文能当场说出真实买盘在不在悠悠。
            self._refresh_reference_bids(session, orders, rep)
            self._step_orders(session, orders, live, now, rep, events, seen)
            session.commit()
            if rep.gap_gated or rep.market_gated:
                # Once per round, not once per order (see RoundReport.gap_gated's
                # docstring) - this filter must never mute an order silently, but a line
                # per gated order across hundreds of them would just be noise.
                a = self.s.alerts
                log.info("gap filter: %d order(s) far from #1 (max_gap_ratio=%s), %d far from market "
                         "(max_market_ratio=%s, min_market_gap=%s) held back from alerting",
                         rep.gap_gated, a.max_gap_ratio, rep.market_gated, a.max_market_ratio, a.min_market_gap)

            # ---- 成交推送：消失的单去「已完成」「暂停中」里查去向（成交推送 spec §4.2） ----
            # 排在 pass A 落库之后：这里出什么错都不能撤回本轮的快照和第 1 名告警。
            if self.s.fills.enabled:
                self._resolve_fills(session, rep, ok_ids)

            # ---- pass B: sample a few orders and cross-check against the old method ----
            if not self.s.polling.verify_enabled or self.s.polling.verify_sample_size <= 0:
                pass  # disabled by config - not a pause, so skipped_paused stays False
            elif st.is_paused(session, ANON, self.now()):
                rep.skipped_paused = True
            else:
                self._run_verification(session, orders, rep)

            # ---- 市场价：抽样刷新几张单的在售最低价 ----
            # 独立于 pass B 的开关和轮换：pass B 是"验证排名语义"，这里是"补一个决策要用的
            # 数字"，两件事没有理由绑在一起开关。排在最后，和 pass B 一样绝不影响已经落库的
            # 快照与告警。
            if self.s.polling.sell_sample_size <= 0:
                pass  # disabled by config
            elif st.is_paused(session, ANON, self.now()):
                rep.skipped_paused = True
            else:
                self._refresh_sell_prices(session, orders, rep)

            if events:
                try:
                    rep.alerts_sent = self._notify(session, events, rep)
                except Exception as e:  # noqa: BLE001
                    # Unguarded, this could discard the whole RoundReport for a SQLite
                    # "database is locked" failure - the same class of problem the
                    # per-order catch-all above exists to contain.
                    #
                    # Never log with exc_info here (log.exception does): _mark_failed's
                    # session.commit() can itself raise this same class of DB error while
                    # handling an already-caught ntfy publish failure, chaining the DB
                    # error's __context__ back to the original transport exception -
                    # log.exception formats that whole chain, including the transport
                    # error's message, which carries the full request URL and therefore
                    # the ntfy topic. Log only the exception's type, which cannot carry a URL.
                    rep.errors += 1
                    log.error("notification stage failed for %d event(s); not confirmed sent (%s)",
                             len(events), type(e).__name__)
                    session.rollback()

            # ---- 市价提醒（市价提醒 spec §2）：排在「升到第 1 名」之后、成交推送之前 ----
            if self.s.market_alerts.enabled:
                try:
                    rep.market_notified = self._check_market(session, orders, seen, rep)
                except Exception as e:  # noqa: BLE001
                    # 只记类型名：推送通道、匿名通道的传输异常都可能带着 URL
                    rep.errors += 1
                    log.error("market alert stage failed (%s)", type(e).__name__)
                    session.rollback()

            # ---- 成交推送：交易中、求购成交（成交推送 spec §4.3），排在「升到第 1 名」之后 ----
            if self.s.fills.enabled:
                try:
                    rep.fills_notified = self._notify_fills(session, rep)
                except Exception as e:  # noqa: BLE001
                    # 同上：只记类型名（推送通道的传输异常可能带着 URL）
                    rep.errors += 1
                    log.error("fill notification stage failed (%s)", type(e).__name__)
                    session.rollback()
        return rep

    def _step_orders(self, session: Session, orders: list[MyBuyOrder], live: dict, now: datetime,
                     rep: RoundReport, events: list, seen: dict) -> None:
        """pass A 的后半段：每张单写一条快照、走一步告警引擎、需要推送时记一行 pending 日志。悠悠和 BUFF
        的轮次共用（BUFF spec §4）。`live` 是这一轮实时核对过的名次、榜首；没有就用单上记的。"""
        for o in orders:
            # 有实时值就用实时值——它更新鲜，而这一轮的判断和推给用户的数字都该用它。
            rank, top = live.get(o.purchase_no, (o.reported_rank, o.top_bid))
            seen[o.purchase_no] = (rank, top)
            snap = RankSnapshot(purchase_no=o.purchase_no, ts=now, rank=rank, beyond=None,
                                top1_price=top, top2_price=None, top1_qty=None, sample_size=0,
                                # 抄当前已知的市场价（可能来自前几轮——市场价是抽样轮换
                                # 刷新的），两列一起抄，否则历史里分不清"没人在卖"和"没查过"。
                                sell_min=o.sell_min, sell_depth=o.sell_depth)
            session.add(snap)
            o.last_checked_at = now
            row = self._alert_row(session, o.purchase_no)
            alert = OrderAlert(o.purchase_no, AlertState(row.state), row.streak, row.last_rank)
            reason = self._unreachable(o, top)
            eligible = reason is None
            if reason == "top":
                rep.gap_gated += 1
            elif reason == "market":
                rep.market_gated += 1
            ev = self.engine.step(alert, rank, eligible=eligible)  # None-safe: leaves state untouched
            row.state, row.streak, row.last_rank, row.updated_at = alert.state.value, alert.streak, alert.last_rank, now
            if ev:
                # Same commit as the state transition (finding 2): a crash between here
                # and the end-of-round _notify must not leave ALERTING durably persisted
                # with no notification row to recover from. _notify finalizes this row
                # to sent/failed/suppressed rather than creating a new one.
                session.add(NotificationLog(fingerprint=ev.purchase_no, event=ev.kind, rank=ev.rank,
                                            payload_json="{}", created_at=now, status="pending"))
            rep.checked += 1
            if ev:
                view = order_view(o, snap)
                view.account_label = self._label_of(o.account_id)
                events.append((ev, view))

    def _resolve_fills(self, session: Session, rep: RoundReport, account_ids: list[int] | None = None) -> None:
        # 翻到页数上限还没找到的单，一小时后再查：否则每轮都要把两个列表翻满（最坏 2 × max_pages 个
        # 账号通道请求），持续 resolve_window_h 小时，正是风控盯的那种量（评审 2026-10-02）。
        # 多账号：每个号用自己的 client 翻自己的「已完成」「暂停中」（多账号 spec §5）。
        now = self.now()
        self._resolve_backoff = {k: t for k, t in self._resolve_backoff.items() if t > now}
        accs = self.accounts
        ids = account_ids if account_ids is not None else sorted(accs)
        for aid in ids:
            acc = accs.get(aid)
            if acc is None:
                continue
            try:
                r = resolve_gone(session, acc.client, self.s, now=now, exclude_ids=set(self._resolve_backoff),
                                 account_id=aid)
            except (RiskControl, MachineRoomBlocked) as e:
                rep.errors += 1
                self._pause_accounts(session, e.code, "risk_control" if isinstance(e, RiskControl) else "other")
                return
            except RateLimited as e:
                rep.errors += 1; self._pause(session, acc.status_key, e.code, "rate_limited"); continue
            except YoupinError as e:
                rep.errors += 1
                self._safe_mark_error(session, acc.status_key, e.code)
                continue
            except Exception as e:  # noqa: BLE001
                rep.errors += 1
                log.error("resolving gone orders failed (%s)", type(e).__name__)
                session.rollback()
                continue
            for ev_id in r.incomplete_ids:
                self._resolve_backoff[ev_id] = now + RESOLVE_RETRY_AFTER
            rep.fills_resolved += r.done + r.paused + r.missing + r.likely
            if r.pending:
                log.info("resolve_gone: %d pending -> done=%d paused=%d missing=%d likely=%d (pages=%d)",
                         r.pending, r.done, r.paused, r.missing, r.likely, r.pages)

    def _notify_fills(self, session: Session, rep: RoundReport, *, buff: bool = False) -> int:
        """推「交易中」和「求购成交」，各合成一条消息。成功 → sent；全部通道失败 → failed，下一轮重试；
        撞上每小时上限或没配通道 → suppressed，不再重试（和「升到第 1 名」一致）。

        推送状态写在 MyBuyOrder.in_trade_notice / OrderEvent.notice 上，推完才写：进程在中间崩了，
        状态还是待推，下一轮会再推一次（宁可重复，不能漏）。悬着的 pending 日志行由 recover_pending
        在启动时改成 failed。"""
        now, tz, sent = self.now(), self.s.timezone, 0
        # 悠悠轮次只推悠悠号的、BUFF 轮次只推 BUFF 号的（BUFF spec §7）：两个轮次并行跑，不分开会推两遍
        bids = buff_account_ids(session)
        mine_o = MyBuyOrder.account_id.in_(bids) if buff else MyBuyOrder.account_id.not_in(bids)
        mine_e = OrderEvent.account_id.in_(bids) if buff else OrderEvent.account_id.not_in(bids)

        trading = list(session.scalars(
            select(MyBuyOrder).where(mine_o, MyBuyOrder.active.is_(True), MyBuyOrder.in_trade_since.is_not(None),
                                     or_(MyBuyOrder.in_trade_notice.is_(None),
                                         MyBuyOrder.in_trade_notice == "failed"))
            .order_by(MyBuyOrder.in_trade_since)))
        if trading:
            views = [TradeView(o.purchase_no, o.commodity_name, o.template_id, _queue_label(o), o.price,
                               o.remaining_qty, o.in_trade_since, now, account_label=self._label_of(o.account_id),
                               url=buff_goods_url(goods_id_of(o)) if is_buff(o.purchase_no) else None)
                     for o in trading]
            result = self._publish_trades(session, build_in_trade_message(views, self.s.ntfy, tz=tz),
                                          [o.purchase_no for o in trading], "IN_TRADE", rep)
            for o in trading:
                o.in_trade_notice = result
            session.commit()
            sent += len(trading) if result == "sent" else 0

        cutoff = now - timedelta(hours=self.s.fills.notify_window_h)
        due: list[OrderEvent] = []
        for ev in session.scalars(select(OrderEvent).where(mine_e, OrderEvent.notice.in_(("pending", "failed")))
                                  .order_by(OrderEvent.id)):
            if (ev.outcome_at or ev.at) < cutoff:
                ev.notice = "suppressed"
                log.warning("fill of order %s is older than notify_window_h=%d; not pushed",
                            ev.purchase_no, self.s.fills.notify_window_h)
            else:
                due.append(ev)
        session.commit()
        if due:
            orders = {o.purchase_no: o for o in session.scalars(
                select(MyBuyOrder).where(MyBuyOrder.purchase_no.in_({ev.purchase_no for ev in due})))}
            views = []
            for ev in due:
                o = orders.get(ev.purchase_no)
                when, exact = fill_time(ev)
                # 「发现于 / 晚了多久」量到推送这一刻，而不是发现消失的那一刻：两者平时在同一轮，
                # 但旧代码记下的消失、或者推送失败重试几轮后，晚的是推送（2026-10-05 晚了 11 小时却没写）
                views.append(TradeView(ev.purchase_no, o.commodity_name if o else ev.purchase_no, ev.template_id,
                                       _queue_label(o) if o else "不限", ev.price, fill_qty(ev), when, now, exact,
                                       account_label=self._label_of(ev.account_id),
                                       url=buff_goods_url(goods_id_of(o)) if o is not None and is_buff(o.purchase_no)
                                       else None))
            result = self._publish_trades(session, build_fill_message(views, self.s.ntfy, tz=tz),
                                          [ev.purchase_no for ev in due], "FILLED", rep)
            for ev in due:
                ev.notice = result
            session.commit()
            sent += len(due) if result == "sent" else 0
        return sent

    def _market_flags(self, o: MyBuyOrder, seen: dict, sell_min, sell_depth) -> frozenset[str]:
        cfg = self.s.market_alerts
        rank, top = seen.get(o.purchase_no, (o.reported_rank, o.top_bid))
        return market_flags(conditional=_queue_label(o) != "不限", in_trade=o.in_trade_since is not None,
                            my_price=o.price, rank=rank, top=top, sell_min=sell_min, sell_depth=sell_depth,
                            rank_max=cfg.rank_max, near_ratio=Decimal(str(cfg.near_ratio)),
                            rank_always=cfg.rank_always, inverted_ratio=Decimal(str(cfg.inverted_ratio)))

    def _stale_in_band(self, o: MyBuyOrder, seen: dict, now: datetime) -> bool:
        """第 2～rank_max 名、不限条件、不在交易中、知道榜首价，而在售价超过 MARKET_PROBE_AFTER 没查过。"""
        rank, top = seen.get(o.purchase_no, (o.reported_rank, o.top_bid))
        if rank is None or not 2 <= rank <= self.s.market_alerts.rank_max or top is None or top <= 0:
            return False
        if _queue_label(o) != "不限" or o.in_trade_since is not None:
            return False
        return o.sell_checked_at is None or now - o.sell_checked_at > MARKET_PROBE_AFTER

    def _check_market(self, session: Session, orders: list[MyBuyOrder], seen: dict, rep: RoundReport) -> int:
        """市价提醒（市价提醒 spec §2）。先用库里已知的在售价（可能是几小时前的）挑候选，推送前用公开接口
        核对实时在售价、按实时值重判，成立的合成一条推出去。同一段只推一次：market_notice 为 sent 就不再推，
        情况都不成立（或改价、单子消失）时清空，下次成立是新的一段。推完才写状态（宁可重复，不能漏）。"""
        cfg = self.s.market_alerts
        now = self.now()
        candidates: list[MyBuyOrder] = []
        probes: list[MyBuyOrder] = []
        for o in orders:
            f = self._market_flags(o, seen, o.sell_min, o.sell_depth)
            o.market_flag = flag_label(f)                        # 看板标记按已知值；推送前再用实时值核对
            if not f:
                if o.market_notice:
                    o.market_notice = None                       # 这一段结束，重新布防
                if self._stale_in_band(o, seen, now):
                    probes.append(o)
            elif o.market_notice in (None, "failed", "suppressed"):
                # suppressed（撞上每小时上限）也重试：这一段还没推到用户手上（宁可重复，不能漏）
                candidates.append(o)
        session.commit()
        if not candidates and not probes:
            return 0

        def order_key(o: MyBuyOrder):
            rank = seen.get(o.purchase_no, (o.reported_rank, None))[0]
            return (rank is None, rank or 0)
        # 先核对已知值就成立的候选（按名次），剩下的名额给第 2～rank_max 名里在售价太旧的单「探一下」：
        # 在售价跌到榜首之下这件事，旧值看不出来（市场价刷新大约 4 小时才轮到一张单）
        targets = sorted(candidates, key=order_key)
        targets += sorted(probes, key=lambda o: o.sell_checked_at or _NEVER_VERIFIED)
        due: list[tuple[MyBuyOrder, frozenset[str]]] = []
        checked_ok = False
        for o in targets[: cfg.verify_max]:
            if st.is_paused(session, ANON, self.now()) or not self.bucket.try_acquire():
                break                                            # 没核到的下一轮再核
            try:
                items = self.client.get_on_sale(o.template_id)
                self.guard.on_success()
            except RiskControl as e:
                rep.errors += 1; self._pause(session, ANON, e.code, "risk_control"); break
            except RateLimited as e:
                rep.errors += 1; self._pause(session, ANON, e.code, "rate_limited"); break
            except MachineRoomBlocked as e:
                rep.errors += 1; self._pause(session, ANON, e.code, "other"); break
            except YoupinError as e:
                rep.errors += 1
                self._safe_mark_error(session, ANON, e.code)
                continue
            except Exception as e:  # noqa: BLE001  一张单的意外错误只跳过这一张；只记类型名
                rep.errors += 1
                log.error("market check for order %s failed (%s); skipping it", o.purchase_no, type(e).__name__)
                session.rollback()
                continue
            checked_ok = True
            # 空列表是结论（没人在卖），不是失败；和市场价刷新写同样的三列
            o.sell_depth = len(items)
            o.sell_min = min((i.price for i in items), default=None)
            o.sell_checked_at = self.now()
            rep.market_checked += 1
            f = self._market_flags(o, seen, o.sell_min, o.sell_depth)
            o.market_flag = flag_label(f)
            if f:
                due.append((o, f))
            else:
                o.market_notice = None
            session.commit()
            self._pace()
        if checked_ok:
            st.mark_ok(session, ANON)
        if not due:
            return 0

        v = self.s.valuation
        keep = (1 - Decimal(str(v.undercut))) * (1 - Decimal(str(v.fees.get("YOUPIN", 0)))) * (1 - Decimal(str(v.withdraw_fee)))
        views = []
        for o, f in due:
            rank, top = seen.get(o.purchase_no, (o.reported_rank, o.top_bid))
            margin = (o.sell_min * keep / o.price - 1) if "near" in f and o.price else None
            views.append(MarketView(o.purchase_no, o.commodity_name, o.template_id, o.price, rank, top,
                                    o.sell_min, o.sell_depth, f, margin, account_label=self._label_of(o.account_id)))
        msg = build_market_message(views, self.s.ntfy, checked_at=self.now(), tz=self.s.timezone)
        result = self._publish_trades(session, msg, [o.purchase_no for o, _ in due], "MARKET", rep)
        for o, _ in due:
            o.market_notice = result
        session.commit()
        return len(due) if result == "sent" else 0

    def _publish_trades(self, session: Session, msg, purchase_nos: list[str], event: str,
                        rep: RoundReport) -> str:
        now = self.now()
        logs = [NotificationLog(fingerprint=no, event=event, rank=None, payload_json="{}", created_at=now,
                                status="pending") for no in purchase_nos]
        session.add_all(logs)
        return self._publish(session, msg, logs, rep)

    def _select_verify_targets(self, orders: list[MyBuyOrder]) -> list[MyBuyOrder]:
        """Pass B's sampling rule: see _select_rotating_sample, keyed on last_verified_at."""
        return self._select_rotating_sample(orders, self.s.polling.verify_sample_size,
                                            lambda o: o.last_verified_at)

    def _select_sell_targets(self, orders: list[MyBuyOrder]) -> list[MyBuyOrder]:
        """市场价刷新的抽样规则：和 pass B 同一套优先级，但用自己的轮换时间戳
        `sell_checked_at` 和自己的配额 `sell_sample_size`——两个步骤的开关和节奏互相独立，
        关掉 pass B 不该让市场价跟着停摆。"""
        return self._select_rotating_sample(orders, self.s.polling.sell_sample_size,
                                            lambda o: o.sell_checked_at)

    def _select_rotating_sample(self, orders: list[MyBuyOrder], limit: int,
                                 last_at: Callable[[MyBuyOrder], datetime | None]) -> list[MyBuyOrder]:
        """Shared sampling rule, in priority order, capped at `limit`:
        1. orders whose reported_rank is at or below polling.fast_lane_rank_threshold -
           the band worth prioritizing for a cross-check, tunable independently of
           alerts.exit_rank (see PollingSettings.fast_lane_rank_threshold's docstring);
        2. then the least-recently-verified active orders (never-verified first), so
           every order eventually rotates through the check, not just the ones that
           happen to sit near the threshold.
        Ties within each group also break by least-recently-verified, so a group larger
        than the remaining budget still rotates fairly instead of favoring the same
        few orders every round.
        """
        if limit <= 0:
            return []

        def key(o: MyBuyOrder):
            return last_at(o) or _NEVER_VERIFIED

        threshold = self.s.polling.fast_lane_rank_threshold
        near = sorted((o for o in orders if o.reported_rank is not None and o.reported_rank <= threshold), key=key)
        chosen = near[:limit]
        if len(chosen) < limit:
            picked = {o.purchase_no for o in chosen}
            rest = sorted((o for o in orders if o.purchase_no not in picked), key=key)
            chosen = chosen + rest[: limit - len(chosen)]
        return chosen

    def _ranked_from_public_queue(self, o: MyBuyOrder) -> tuple[RankResult, int]:
        """取公开队列并算出这张单的排名；匿名那一页装满了又没找到自己时，用认证请求深取一次。

        匿名调用硬上限 20 条（`pageIndex`/`pageSize` 都被忽略），所以"子队列里排第 4、但在
        全表里排到 20 名开外"的单——磨损"不限"的单最容易这样——永远算不出排名。带 token 请求
        时 pageSize 才生效，能一次取回整个队列（2026-09-21 实测：模板 101422 的 347 条一次
        取完），见 YoupinClient.get_template_purchase_orders。

        兜底只在真正需要时才掏账号出来：`beyond` 有值就代表"这一页被截断了、我们看不见自己"。
        队列整个取回来还找不到自己是另一回事（那是真的对不上），不该用一次认证请求去追。

        返回 `(结果, 这次实际的页大小)`。页大小要一起返回，否则调用方无法判定"没找到自己"
        到底是被截断了还是真的不在队列里——深取之后那个阈值不再是 visible_limit。
        """
        limit = self.s.polling.visible_limit
        key = queue_key_from_str(o.queue_key)
        # 排名按这张单所属号的 user_id 找自己（多账号 spec §5）
        uid = self._uids.get(o.account_id, 0)
        # 多号时，一轮之内同一个模板的匿名队列只取一次，几个号挂同一个模板时共用（多账号 spec §5）。
        # 一个号时照旧每次现取：单号的请求行为一个都不变。
        shared = len(self.accounts) >= 2
        public = self._queue_cache.get((o.template_id, limit)) if shared else None
        if public is None:
            public = self.client.get_template_purchase_orders(o.template_id, limit)
            if shared:
                self._queue_cache[(o.template_id, limit)] = public
        res = compute_rank(uid, o.purchase_no, key, public, limit)
        deep = self.s.polling.deep_page_size
        owner = self.accounts.get(o.account_id)
        authed = owner.client if owner is not None else self.client
        if res.rank is None and res.beyond is not None and deep > 0 and getattr(authed, "token", None):
            self._pace()
            deep_rows = authed.get_template_purchase_orders(o.template_id, deep, authed=True)
            # 深取回来的这一页就是全部了，所以判"排不进"的阈值也跟着用它的长度：再用 20 去判，
            # 一张排在第 30 名、却已经实实在在被我们看见的单，会被错记成"看不见"。
            res = compute_rank(uid, o.purchase_no, key, deep_rows, deep)
            log.info("deep queue fetch for order %s: %d row(s), rank=%s", o.purchase_no, len(deep_rows), res.rank)
            return res, deep
        return res, limit

    def _alert_band_candidates(self, session: Session, orders: list[MyBuyOrder]) -> list[MyBuyOrder]:
        """本轮**有可能真的产生告警事件**的单，按排名从好到差。

        两个筛选条件缺一不可：排名进了 `enter_rank` 带，并且还没处于 ALERTING——已经在告警中
        的单这轮不会再发事件（`AlertEngine.step` 只在 NORMAL→ALERTING 的跳变上产生事件），
        为它花配额是纯粹的浪费。

        实时排名核对和「别处买盘」查询共用这个名单：两件事都只在要推送的那一刻才有意义。

        离市价太远（难以成交 spec §3）的单也不进名单：它们被 `eligible=False` 挡着、永远到不了 ALERTING，
        不排除的话每一轮都会被白查一遍（第 1 名里常有七八张）。这条规则只看订单行上的在售价、别处买盘，
        不吃滞后的 top_bid，所以和下面 `_refresh_reference_bids` 说的「不按差价过滤预先排除」不冲突。
        """
        band = self.s.alerts.enter_rank
        limits = reach_kwargs(self.s.alerts)
        candidates = []
        for o in orders:
            if o.reported_rank is None or o.reported_rank > band:
                continue
            if unreachable(o.price, None, o.sell_min, o.sell_depth, o.ref_bid, **limits) == "market":
                continue
            row = session.get(AlertStateRow, o.purchase_no)
            if row is not None and row.state == AlertState.ALERTING.value:
                continue
            candidates.append(o)
        candidates.sort(key=lambda o: o.reported_rank)
        return candidates

    def _confirm_with_live_queue(self, session: Session, orders: list[MyBuyOrder],
                                  rep: RoundReport) -> dict[str, tuple[int | None, Decimal | None]]:
        """把"马上要告警"的那几张单的排名和榜首价，用公开队列现算一遍，返回
        {purchase_no: (rank, top1_price)}；算不出来的单不进这个表，调用方照旧用悠悠上报的值。

        为什么非做不可：悠悠在「我的求购单」里给的 `rank`/`maxPurchasePrice` 不是实时的。
        2026-09-21 实测，模板 108753 悠悠说第 2 名、榜首 ¥351，而同一秒取回的公开队列是
        352 / 351 / 我的 158——实际第 3 名，且要 352 才顶得上去。背靠背连采三轮完全一致，
        不是采样间隔造成的；逐字段比对原始 JSON 也排除了"那一行带我们看不见的条件"。告警
        整个建在这个值上，而滞后最严重的恰好是机器人抢价的队列，也就是最值得抢的那些。

        只核对**有可能本轮产生事件**的单：排名进了 enter_rank 带、且还没处于 ALERTING。
        已经 ALERTING 的单这轮不会再发事件，核对它纯属浪费配额。稳态下这个集合只有新进来
        的那几张，所以每轮多打的请求是个位数。

        任何失败都不改变告警行为——拿不到实时值就退回悠悠的值，绝不因为核对不上而不告警。
        """
        if not self.s.alerts.confirm_with_live_queue or self.s.alerts.live_confirm_max <= 0:
            return {}
        if st.is_paused(session, ANON, self.now()):
            return {}
        candidates = self._alert_band_candidates(session, orders)
        out: dict[str, tuple[int | None, Decimal | None]] = {}
        for o in candidates[: self.s.alerts.live_confirm_max]:
            if not self.bucket.try_acquire():
                log.warning("hourly request cap reached; stopping live alert confirmation")
                break
            try:
                res, _ = self._ranked_from_public_queue(o)
                self.guard.on_success()
                if res.rank is not None:
                    out[o.purchase_no] = (res.rank, res.top1_price)
                    rep.live_confirmed += 1
                    if res.rank != o.reported_rank:
                        rep.live_corrected += 1
                        log.info("live confirm corrected order %s: reported rank %s -> %s (top bid %s -> %s)",
                                 o.purchase_no, o.reported_rank, res.rank, o.top_bid, res.top1_price)
                self._pace()
            except (RiskControl, RateLimited, MachineRoomBlocked) as e:
                # 通道级错误：暂停匿名通道并停止核对。已经拿到的实时值仍然有效。
                rep.errors += 1
                kind = "risk_control" if isinstance(e, RiskControl) else (
                    "rate_limited" if isinstance(e, RateLimited) else "other")
                self._pause(session, ANON, e.code, kind)
                break
            except Exception:
                # 核对失败绝不能影响告警：这张单退回悠悠上报的值，继续下一张。
                rep.errors += 1
                log.exception("live confirm failed for order %s; falling back to the reported rank",
                              o.purchase_no)
                session.rollback()
                self._pace()
                continue
        return out

    def _refresh_reference_bids(self, session: Session, orders: list[MyBuyOrder], rep: RoundReport) -> None:
        """给「即将告警」的那几张单取一次别的平台的最高求购价（BUFF/C5 中位数），写进
        `ref_bid` / `ref_bid_platforms` / `ref_bid_at`。

        为什么只在告警前后做，而不是全量刷：2026-09-23 的全量对账给出的结论是**悠悠求购价
        比 BUFF/C5 中位数低 9.1%**（79% 的饰品都低），这意味着「在悠悠排进前 2」并不等于
        「这件东西抢手」。这句判断只在要推送的那一刻有用，所以只给排名进带的单花配额。

        名单和实时排名核对共用（`_alert_band_candidates`），**没有再按差价过滤筛一遍**——被
        `max_gap_ratio` 挡住的单这一轮确实不会推送，理论上这几次查询是白花的。不筛是有意的：
        差价过滤吃的是 `top_bid`，而那个值可能滞后几分钟（见 confirm_with_live_queue），照它
        预先排除，等于把实时核对本来要纠正的偏差又搬回到前面来。/price/single 的配额是 60 次/
        分钟，而进带的单每轮只有个位数，省这几次不值得冒那个风险。

        失败一律吞掉：拿不到行情就少一行，**绝不能因此不告警**。排名是本职，行情是加分。
        """
        if self.steamdt is None:
            return
        from skinpulse.adapters.steamdt.errors import SteamDTError
        from skinpulse.analysis.platform_gap import reference_bid
        from skinpulse.services.market import resolve_names

        candidates = self._alert_band_candidates(session, orders)
        if not candidates:
            return
        names = resolve_names(session, [o.template_id for o in candidates])
        now = self.now()
        for o in candidates:
            mhn = names.get(o.template_id)
            if not mhn:
                continue  # 这件饰品没在 SteamDT 的悠悠映射里，没法对账
            try:
                ref = reference_bid(self.steamdt.get_prices(mhn))
            except SteamDTError as e:
                # 配额、鉴权、服务端异常都走这里。记一条 INFO 就够——它不影响告警。
                log.info("reference bid lookup failed for order %s (%s); alert goes out without it",
                         o.purchase_no, e)
                continue
            except Exception:
                log.exception("unexpected error fetching reference bid for order %s", o.purchase_no)
                continue
            if ref is None:
                continue  # 别的平台没有求购数据，没有参照可言
            o.ref_bid, platforms = ref
            o.ref_bid_platforms = "/".join(platforms)
            o.ref_bid_at = now
            rep.ref_bids_fetched += 1
        session.commit()

    def _refresh_sell_prices(self, session: Session, orders: list[MyBuyOrder], rep: RoundReport) -> None:
        """给抽到的几张单刷新市场价（悠悠在售最低价），写回 MyBuyOrder 的
        sell_min/sell_depth/sell_checked_at 三列。

        **空列表是结论，不是失败**：`queryOnSaleCommodityList` 返回 0 条意味着这个模板当前
        一件都没有人在卖，这时 `sell_depth=0`、`sell_min=None`，看板会显示"无人在售"。
        绝不要在这里把 sell_min 兜底成 0——"0 元在售"是个看起来合理、实际完全错误的值，而
        这个项目已经因为价格字段静悄悄变成 0 吃过一次大亏（live-api-findings §4）。

        错误路由和 pass B 一致：通道级错误（风控/限流/机房封禁）暂停匿名通道并中止本轮刷新，
        单张单的错误跳过继续。失败的单不写 sell_checked_at，于是下一轮它仍然排在轮换最前面。
        """
        had_success = False
        aborted = False
        for o in self._select_sell_targets(orders):
            if not self.bucket.try_acquire():
                log.warning("hourly request cap reached; stopping market-price refresh")
                break
            try:
                # 不传 page_size：服务端忽略 pageSize（2026-09-21 实测 5/10/20/50 一律返 20
                # 条），给它配个旋钮等于骗人。我们只取最低价，返多少条都够用。
                items = self.client.get_on_sale(o.template_id)
                self.guard.on_success()
                had_success = True
                o.sell_depth = len(items)
                o.sell_min = min((i.price for i in items), default=None)
                o.sell_checked_at = self.now()
                session.commit()
                rep.sell_refreshed += 1
                self._pace()
            except RiskControl as e:
                rep.errors += 1; aborted = True; self._pause(session, ANON, e.code, "risk_control"); break
            except RateLimited as e:
                rep.errors += 1; aborted = True; self._pause(session, ANON, e.code, "rate_limited"); break
            except MachineRoomBlocked as e:
                rep.errors += 1; aborted = True; self._pause(session, ANON, e.code, "other"); break
            except UkExpired as e:
                # 不在这里付 60 秒退避：市场价是锦上添花的数字，为它把整轮拖住不值得。
                # 丢掉 uk，下一轮自然会重新取。
                rep.errors += 1
                self.client.uk = None
                self._skip_order(session, o, e.code, wait=self._pace)
                continue
            except YoupinError as e:
                rep.errors += 1
                self._skip_order(session, o, e.code, wait=self._pace)
                continue
            except Exception:
                rep.errors += 1
                log.exception("unexpected error refreshing market price for order %s; skipping it", o.purchase_no)
                session.rollback()  # must run before _skip_order, so its write survives the rollback
                self._skip_order(session, o, None, wait=self._pace)
                continue
        if had_success and not aborted:
            st.mark_ok(session, ANON)

    def _run_verification(self, session: Session, orders: list[MyBuyOrder], rep: RoundReport) -> None:
        """Pass B's body: fetch the public queue and compute_rank the old way for each
        sampled order, exactly like the pre-redesign per-order loop did - same pacing,
        same token-bucket spend per fetch, same error routing (channel-wide errors abort
        sampling and pause the anonymous channel; per-order errors skip and continue).
        The one difference from that old loop: a failure here only ever affects pass B's
        own counters/rows, never pass A's already-committed snapshots, alert state, or
        notifications.
        """
        had_success = False
        aborted = False
        uk_backoff_waited = False  # UkExpired's 60s backoff (below) pays out at most once per round
        for o in self._select_verify_targets(orders):
            if not self.bucket.try_acquire():
                log.warning("hourly request cap reached; stopping verification sampling")
                break
            try:
                res, page_limit = self._ranked_from_public_queue(o)
                self.guard.on_success()
                had_success = True  # SystemStatus.mark_ok is recorded once, after the loop (below)
                # 阈值用这次实际请求的页大小，不是固定的 visible_limit：深取兜底之后整个队列
                # 都在手里了，这时还找不到自己就是真的对不上（DISAGREED），而不是"看不见"。
                outcome = verification_outcome(o.reported_rank, res.rank, res.sample_size, page_limit)
                session.add(RankCheck(purchase_no=o.purchase_no, ts=self.now(), reported_rank=o.reported_rank,
                                      computed_rank=res.rank, queue_size=res.queue_size, sample_size=res.sample_size,
                                      agreed=outcome == AGREED))
                o.last_verified_at = self.now()
                session.commit()
                rep.verified += 1
                if outcome == AGREED:
                    rep.verify_agreed += 1
                elif outcome == DISAGREED:
                    rep.verify_disagreed += 1
                    # The signal that YouPin's `rank` might not mean what we assume (see
                    # run_round's docstring) - name both values so it's actionable from
                    # the log alone.
                    log.warning("rank check disagreement for order %s: reported=%s computed=%s",
                               o.purchase_no, o.reported_rank, res.rank)
                else:
                    rep.verify_inconclusive += 1
                    # 不是警告：这只说明这张单排在公开队列可见的那一页之外，我们无从核对。
                    log.info("rank check inconclusive for order %s: reported=%s, order sits past the "
                             "%d-row public page", o.purchase_no, o.reported_rank, res.sample_size)
                self._pace()
            except RiskControl as e:
                rep.errors += 1; aborted = True; self._pause(session, ANON, e.code, "risk_control"); break
            except RateLimited as e:
                rep.errors += 1; aborted = True; self._pause(session, ANON, e.code, "rate_limited"); break
            except MachineRoomBlocked as e:
                # Channel-wide like RiskControl/RateLimited, not per-order: a block that
                # rejects one request will reject every remaining sample this round too.
                rep.errors += 1; aborted = True; self._pause(session, ANON, e.code, "other"); break
            except UkExpired as e:
                rep.errors += 1
                self.client.uk = None
                if uk_backoff_waited:
                    # Already paid the channel-wide 60s wait once this round (below):
                    # every request shares one `uk`, so a second, third, ... 60s wait for
                    # the same still-rejecting channel would stall the round for nothing -
                    # pace normally instead, same as any other skip-type error.
                    self._skip_order(session, o, e.code, wait=self._pace)
                else:
                    uk_backoff_waited = True
                    self._skip_order(session, o, e.code,
                                      wait=lambda: self.sleep(self.guard.on_error("uk_expired")))  # own backoff; don't also _pace()
                continue
            except LoginRequired as e:
                rep.errors += 1
                self._skip_order(session, o, e.code, wait=self._pace)
                continue
            except YoupinError as e:
                rep.errors += 1
                self._skip_order(session, o, e.code, wait=self._pace)
                continue
            except Exception:
                # Backstop, not a replacement for the YoupinError handlers above: a malformed
                # row or a locked SQLite file (the dashboard reads concurrently) must not
                # discard the whole round's report for the other samples.
                rep.errors += 1
                log.exception("unexpected error verifying order %s; skipping it", o.purchase_no)
                session.rollback()  # must run before _skip_order, so its write survives the rollback
                self._skip_order(session, o, None, wait=self._pace)
                continue
        # Recorded once per round, not once per successful sample (each call is its own
        # UPDATE+COMMIT round-trip on the same SystemStatus row). Skipped when sampling
        # ended in a channel-wide pause - that pause must not be immediately clobbered by
        # a success that happened earlier in the very same round.
        if had_success and not aborted:
            st.mark_ok(session, ANON)

    def _pending_log_for(self, session: Session, ev: AlertEvent) -> NotificationLog:
        """Reuse the row `run_round` already wrote in the same commit as the state
        transition (finding 2), keeping exactly one row per event. Falls back to
        creating one only for callers (tests, mainly) that invoke `_notify` directly
        without going through the round loop that pre-creates it."""
        row = session.scalar(
            select(NotificationLog)
            .where(NotificationLog.fingerprint == ev.purchase_no, NotificationLog.event == ev.kind,
                   NotificationLog.status == "pending")
            .order_by(desc(NotificationLog.id)).limit(1)
        )
        if row is None:
            row = NotificationLog(fingerprint=ev.purchase_no, event=ev.kind, rank=ev.rank,
                                  payload_json="{}", created_at=self.now(), status="pending")
            session.add(row)
        return row

    @staticmethod
    def _count_failed(rep: RoundReport | None, n: int) -> None:
        # 投递失败同时计入 errors：它确实是一次出错，摘要里的 errors=0 不该对此撒谎。
        if rep is not None:
            rep.notify_failed += n
            rep.errors += 1

    def _mark_failed(self, session: Session, logs: list[NotificationLog], channels: list[str] | None = None,
                     undelivered: Callable[[NotificationLog], None] | None = None) -> None:
        for lg in logs:
            lg.status = "failed"
            if undelivered:
                undelivered(lg)
        session.commit()
        for name in channels or ["ntfy"]:
            st.mark_error(session, name, "publish_failed", None)

    def _notify(self, session: Session, events: list[tuple[AlertEvent, OrderView]],
                rep: RoundReport | None = None) -> int:
        # Grouping already collapses a whole round into exactly one ntfy push,
        # so the round consumes exactly one unit of the hourly budget
        # regardless of how many orders fired.
        views = [v for _, v in events]
        logs = [self._pending_log_for(session, ev) for ev, _ in events]
        # 触发时间取这批事件被观察到的那一刻（pending 行是在本轮检测到跳变时写下的），
        # 不是此刻——此刻已经晚了实时核对、行情查询那几十秒。
        triggered_at = min(lg.created_at for lg in logs)
        msg = build_enter_top2_message(views, self.s.ntfy, triggered_at=triggered_at, tz=self.s.timezone,
                                       enter_rank=self.s.alerts.enter_rank)
        # Nothing delivered (suppressed or failed): reset each order's AlertStateRow in the
        # same commit that finalizes its log row. It must not stay stuck ALERTING -
        # AlertEngine.step would return None on every later round until the order dropped
        # out of and back into the top 2, silently swallowing the alert.
        result = self._publish(session, msg, logs, rep,
                               undelivered=lambda lg: _reset_alert_state(session, lg.fingerprint, self.now()))
        return len(logs) if result == "sent" else 0

    def _publish(self, session: Session, msg, logs: list[NotificationLog], rep: RoundReport | None = None, *,
                 undelivered: Callable[[NotificationLog], None] | None = None) -> str:
        """Publish one message for `logs` and finalize them: returns "sent", "failed" or
        "suppressed". Shared by the rank-1 alert (`_notify`) and the fill pushes
        (`_notify_fills`); `undelivered` runs on each log row that was not delivered, in the
        same commit that finalizes it."""
        payload_json = json.dumps({"title": msg.title, "body": msg.body}, ensure_ascii=False)
        for lg in logs:
            lg.payload_json = payload_json
        if not self.hourly_cap.allow(1):
            log.warning("hourly notification cap reached; %d events dropped", len(logs))
            # Leave a trail: the alerts fired even though delivery was suppressed,
            # so the dashboard/audit can see they happened and why.
            for lg in logs:
                lg.status = "suppressed"
                if undelivered:
                    undelivered(lg)
            session.commit()
            return "suppressed"
        session.commit()
        if self.ntfy is None:
            # No adapter configured: nothing was, or could have been, delivered. This is
            # not a delivery failure (mark_error/"failed" would misreport it as ntfy being
            # sick) and it is definitely not a success (mark_ok/"sent" would claim a push
            # that never existed) - suppressed is the honest audit trail for "never attempted".
            log.warning("no ntfy client configured; %d event(s) suppressed", len(logs))
            for lg in logs:
                lg.status = "suppressed"
                if undelivered:
                    undelivered(lg)
            session.commit()
            return "suppressed"
        try:
            # self.ntfy 历史上只是 ntfy 客户端，现在通常是 FanoutNotifier（PushPlus + ntfy，见
            # services/notifier.py），publish 返回每条通道的结果；为了不牵动一大片调用点和测试，
            # 属性名没改。单通道客户端返回的不是列表，按老规矩当作 "ntfy" 一条通道处理。
            results = self.ntfy.publish(msg)
        except AllChannelsFailed as e:
            # 各通道的错误在 FanoutNotifier 里已经按通道逐条记过（且已脱敏），这里只记汇总。
            log.error("notification publish failed on every channel: %s", e)
            self._mark_failed(session, logs, [r.name for r in e.results], undelivered)
            self._count_failed(rep, len(logs))
            return "failed"
        except NtfyError as e:
            # NtfyClient already redacts the topic from HTTP-status failures before
            # raising this, so the message is safe to log in full.
            log.error("ntfy publish failed: %s", e)
            self._mark_failed(session, logs, undelivered=undelivered)
            self._count_failed(rep, len(logs))
            return "failed"
        except Exception as e:  # noqa: BLE001
            # Transport-level failure (httpx). Never log its message: the topic is effectively
            # a password on a public ntfy server, and a transport error carries the full
            # request URL, which embeds it.
            log.error("ntfy publish failed: %s", type(e).__name__)
            self._mark_failed(session, logs, undelivered=undelivered)
            self._count_failed(rep, len(logs))
            return "failed"
        # Everything below runs only once the publish call itself has actually succeeded,
        # so a DB error here (e.g. this commit, or mark_ok) can no longer be mistaken for a
        # publish failure and mark a delivered push "failed" - it propagates to run_round's
        # notification-stage guard instead, leaving these rows correctly "sent".
        for lg in logs:
            lg.status, lg.sent_at = "sent", self.now()
        session.commit()
        # 逐条通道记健康状态：一条通道挂了、另一条成功时，失败的那条必须在 /status 上看得见，
        # 不能被整体的「已发出」掩盖——ntfy「发出去了但手机从没收到」就是这样藏了两个多星期。
        if isinstance(results, list):
            for r in results:
                if r.ok:
                    st.mark_ok(session, r.name)
                else:
                    st.mark_error(session, r.name, "publish_failed", None)
        else:
            st.mark_ok(session, "ntfy")
        return "sent"
