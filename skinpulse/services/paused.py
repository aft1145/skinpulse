"""充值前检查暂停单（暂停单 spec §3）：翻「暂停中」→ SteamDT 在售价 → 悠悠在售接口实时核对 → 落库。

只读、不推送。三个约束决定了它的形状：

- **不逐个查公开求购队列**：2026-11-24 一次性检查连查 77 次就被 HTTP 403，正式服务每轮的抽样跟着失败了
  至少 50 分钟（live-api-findings §6）。榜首用「我的求购」列表里悠悠白送的 `maxPurchasePrice`。
- **悠悠请求和每轮监控错开**：两边用同一个 client，所以第 1 步（翻页，账号通道）和第 3 步（在售，匿名通道）
  持 `lock`（服务里是 `rt.youpin_lock`，`job_monitor_round` 整轮持有）；第 2 步只请求 SteamDT（约 6 分钟），
  不持锁，监控照常跑。
- **错误按号处理**，和每轮同步一样（多账号 spec §5）：登录失效、限流只跳过这个号；风控、机房拦截暂停所有号、
  整次失败。通道暂停写进 `system_status`，监控也会照着停。

依赖都从 Monitor 拿（启用的号、匿名 client、SteamDT、设置、令牌桶、退避、暂停通道的写法），服务和
`check-paused` 命令共用。
"""
from __future__ import annotations

import logging
import threading
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Callable

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from skinpulse.adapters.steamdt.client import MAX_BATCH_NAMES
from skinpulse.adapters.steamdt.errors import SteamDTError
from skinpulse.adapters.youpin.errors import (
    LoginRequired, MachineRoomBlocked, RateLimited, RiskControl, UkExpired, YoupinError,
)
from skinpulse.core.paused import INVERTED, NEAR_SELL, TOP1, classify, parse_top_bid, sort_key
from skinpulse.db.models import PausedCheckItem, PausedCheckRun, PickRun
from skinpulse.services import status as st
from skinpulse.services.fills import STATUS_PAUSED, parse_youpin_time
from skinpulse.services.market import resolve_names, wait_batch_slot
from skinpulse.services.monitor import ANON
from skinpulse.services.pick import in_night_window, night_label

log = logging.getLogger(__name__)

PAGE_SIZE = 40
KEEP_RUNS = 5
PAGES, PRICES, VERIFY = "pages", "prices", "verify"


class PausedRefused(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class _Abort(Exception):
    """整次检查失败、写进 `error` 的中文原因。"""


@dataclass
class _Row:
    account_id: int
    purchase_no: str
    template_id: int
    name: str
    price: Decimal
    remaining_qty: int
    paused_at: datetime | None
    top_bid: Decimal | None
    top_empty: bool
    sell_min: Decimal | None = None
    sell_depth: int | None = None
    sell_src: str | None = None
    sell_at: datetime | None = None


@dataclass
class CheckResult:
    run_id: int
    status: str
    rows: int = 0
    skipped: list[int] = field(default_factory=list)
    error: str | None = None


def _logged_in(monitor) -> list:
    return [a for a in sorted(monitor.accounts.values(), key=lambda a: a.id) if getattr(a.client, "token", None)]


def start_run(session: Session, monitor, source: str, now: datetime) -> int:
    """建一条 running 的检查记录；不能开始时抛 PausedRefused（中文原因）。"""
    if session.scalar(select(PausedCheckRun.id).where(PausedCheckRun.status == "running")) is not None:
        raise PausedRefused(409, "已经有一次检查在跑")
    if session.scalar(select(PickRun.id).where(PickRun.status == "running")) is not None:
        raise PausedRefused(409, "选品正在跑，两边抢 SteamDT 的配额，等它跑完再查")
    if in_night_window(monitor.s, now):
        raise PausedRefused(423, f"{night_label(monitor.s)} 是价格快照和估值的时间，过了再来")
    if not _logged_in(monitor):
        raise PausedRefused(409, "还没有登录的悠悠号")
    run = PausedCheckRun(started_at=now, status="running", source=source, phase=PAGES, done=0, total=0,
                         incomplete=False, steamdt=False)
    session.add(run)
    session.commit()
    return run.id


def mark_interrupted(session: Session, now: datetime) -> int:
    """服务启动时把上次进程没跑完的标成 interrupted（同选品）。"""
    runs = session.scalars(select(PausedCheckRun).where(PausedCheckRun.status == "running")).all()
    for r in runs:
        r.status, r.finished_at = "interrupted", now
    session.commit()
    return len(runs)


def run_check(monitor, run_id: int, *, lock: threading.Lock | None = None, account_ids: list[int] | None = None,
              use_steamdt: bool = True, progress: Callable[[str], None] | None = None) -> CheckResult:
    """跑完一次检查。任何异常都落成 failed，不往外抛（服务里是后台 job，命令行里照样有结果可看）。"""
    say = progress or (lambda _m: None)
    guard = lock if lock is not None else nullcontext()
    res = CheckResult(run_id=run_id, status="failed")
    with monitor.Session() as s:
        run = s.get(PausedCheckRun, run_id)
        try:
            rows = _pages(monitor, s, run, guard, account_ids, res, say)
            if use_steamdt and monitor.steamdt is not None:
                _prices(monitor, s, run, rows, say)
            _verify(monitor, s, run, rows, guard, say)
            _save(monitor, s, run, rows)
            res.status, res.rows = "done", len(rows)
            say(f"完成：{len(rows)} 张暂停单")
        except _Abort as e:
            s.rollback()
            run = s.get(PausedCheckRun, run_id)
            run.status, run.finished_at, run.error = "failed", monitor.now(), str(e)
            res.error = str(e)
            s.commit()
        except Exception as e:  # noqa: BLE001  传输层异常各步已经就地处理；走到这里的是代码问题，要 traceback
            log.exception("paused check %s failed", run_id)
            s.rollback()
            run = s.get(PausedCheckRun, run_id)
            run.status, run.finished_at, run.error = "failed", monitor.now(), type(e).__name__
            res.error = type(e).__name__
            s.commit()
        except BaseException:
            # 命令行里按了 Ctrl-C：不标掉的话这条一直是 running，看板一直「正在检查…」、再也开不了新的检查
            s.rollback()
            run = s.get(PausedCheckRun, run_id)
            run.status, run.finished_at, run.error = "interrupted", monitor.now(), "中途停止"
            s.commit()
            raise
    return res


def _progress(s: Session, run: PausedCheckRun, phase: str, done: int, total: int) -> None:
    run.phase, run.done, run.total = phase, done, total
    s.commit()


def _pages(monitor, s: Session, run: PausedCheckRun, guard, account_ids, res: CheckResult, say) -> list[_Row]:
    cfg, tz = monitor.s.paused_check, monitor.s.timezone
    accs = [a for a in _logged_in(monitor) if account_ids is None or a.id in account_ids]
    if not accs:
        raise _Abort("没有可查的悠悠号")
    rows: dict[str, _Row] = {}
    pages = 0
    _progress(s, run, PAGES, 0, 0)
    for i, acc in enumerate(accs):
        if i:
            monitor.sleep(monitor.s.polling.account_gap_s)
        if st.is_paused(s, acc.status_key, monitor.now()):
            res.skipped.append(acc.id)
            continue
        with guard:
            for page in range(1, cfg.max_pages + 1):
                if page > 1:
                    monitor.sleep(monitor.s.youpin.page_sleep_s)
                try:
                    got = acc.client.search_my_purchase_orders_page(STATUS_PAUSED, page, PAGE_SIZE)
                except LoginRequired as e:
                    monitor._safe_mark_error(s, acc.status_key, e.code)
                    res.skipped.append(acc.id)
                    break
                except (RiskControl, MachineRoomBlocked) as e:
                    monitor._pause_accounts(s, e.code, "risk_control" if isinstance(e, RiskControl) else "other")
                    raise _Abort("悠悠风控，所有号的请求都暂停了，过一阵再查") from None
                except RateLimited as e:
                    monitor._pause(s, acc.status_key, e.code, "rate_limited")
                    res.skipped.append(acc.id)
                    break
                except UkExpired as e:
                    acc.client.uk = None
                    monitor._safe_mark_error(s, acc.status_key, e.code)
                    res.skipped.append(acc.id)
                    break
                except YoupinError as e:
                    monitor._safe_mark_error(s, acc.status_key, e.code)
                    res.skipped.append(acc.id)
                    break
                monitor.guard.on_success()
                pages += 1
                bad = 0
                for o in got:
                    top, empty = parse_top_bid(o.raw.get("maxPurchasePrice"))
                    if o.price <= 0 or not o.purchase_no:
                        bad += 1
                        continue
                    rows[o.purchase_no] = _Row(acc.id, o.purchase_no, o.template_id, o.commodity_name, o.price,
                                               o.remaining_qty, parse_youpin_time(o.raw.get("createTime"), tz),
                                               top, empty)
                if bad:
                    # 价格、单号是暂停单不可能缺的字段：缺了多半是悠悠改了字段名。整页都读不出来就别出一份
                    # 「没有暂停单」的假结果；只缺几张就标不完整（REVIEW 第 2 遍）
                    log.warning("paused check: %d of %d row(s) on page %d had no price or order number", bad, len(got), page)
                    if bad == len(got):
                        raise _Abort("暂停单列表里的价格读不出来（悠悠可能改了接口），这次结果不可信")
                    run.incomplete = True
                _progress(s, run, PAGES, pages, 0)
                say(f"翻暂停单：号 {acc.id} 第 {page} 页，{len(got)} 张")
                if len(got) < PAGE_SIZE:
                    break
            else:
                run.incomplete = True
                log.warning("paused check: account %s hit max_pages=%d", acc.id, cfg.max_pages)
    run.skipped = ",".join(str(a) for a in res.skipped) or None
    s.commit()
    if len(res.skipped) == len(accs):
        raise _Abort("所有号都没查成（登录失效或被限流）")
    return list(rows.values())


def _prices(monitor, s: Session, run: PausedCheckRun, rows: list[_Row], say) -> None:
    names = resolve_names(s, {r.template_id for r in rows})
    by_name: dict[str, int] = {n: tid for tid, n in names.items()}
    ordered = sorted(by_name)
    batches = (len(ordered) + MAX_BATCH_NAMES - 1) // MAX_BATCH_NAMES
    prices: dict[int, tuple[Decimal | None, int, datetime | None]] = {}
    _progress(s, run, PRICES, 0, batches)
    for b, i in enumerate(range(0, len(ordered), MAX_BATCH_NAMES)):
        wait_batch_slot(s, monitor.sleep)   # 和别的任务共用 1 次/分钟（盈亏导入、价格快照……）
        try:
            items = monitor.steamdt.get_prices_batch(ordered[i:i + MAX_BATCH_NAMES])
        except SteamDTError as e:   # 消息只有错误码和服务端文案，不含 key
            log.warning("paused check: SteamDT batch %d failed (%s); continuing", b + 1, e)
            items = []
        except httpx.HTTPError as e:  # 传输层异常可能带 URL：只记类型名
            log.warning("paused check: SteamDT batch %d failed (%s); continuing", b + 1, type(e).__name__)
            items = []
        for it in items:
            tid = by_name.get(it.market_hash_name)
            yp = it.of("YOUPIN") if tid is not None else None
            if yp is not None and yp.sell_price is not None and yp.sell_price > 0:
                prices[tid] = (yp.sell_price, yp.sell_count, yp.update_time)
                run.steamdt = True
        _progress(s, run, PRICES, b + 1, batches)
        say(f"查在售价：{b + 1}/{batches} 批")
    for r in rows:
        if r.template_id in prices:
            r.sell_min, r.sell_depth, r.sell_at = prices[r.template_id]
            r.sell_src = "steamdt"


def _verdict(monitor, r: _Row):
    cfg = monitor.s.paused_check
    return classify(r.price, r.top_bid, r.top_empty, r.sell_min,
                    near_gap=Decimal(str(cfg.near_gap)), sell_ratio=Decimal(str(cfg.sell_ratio)))


def _verify(monitor, s: Session, run: PausedCheckRun, rows: list[_Row], guard, say) -> None:
    """按 SteamDT 价已经倒挂 / 贴近在售的，和直接第 1 却没有在售价的，用悠悠在售接口实时查一次。"""
    cand = []
    for r in rows:
        v = _verdict(monitor, r)
        if v.tier in (INVERTED, NEAR_SELL) or (v.tier == TOP1 and r.sell_min is None):
            cand.append((sort_key(v.tier, v.sell_ratio, v.gap_ratio), r))
    cand.sort(key=lambda x: x[0])
    targets = [r for _, r in cand[: monitor.s.paused_check.verify_max]]
    _progress(s, run, VERIFY, 0, len(targets))
    if not targets:
        return
    with guard:
        for n, r in enumerate(targets):
            if st.is_paused(s, ANON, monitor.now()) or not monitor.bucket.try_acquire():
                break                                    # 没核到的照用 SteamDT 价
            if n:
                monitor._pace()
            try:
                items = monitor.client.get_on_sale(r.template_id)
            except (RiskControl, RateLimited, MachineRoomBlocked) as e:
                kind = ("risk_control" if isinstance(e, RiskControl)
                        else "rate_limited" if isinstance(e, RateLimited) else "other")
                monitor._pause(s, ANON, e.code, kind)
                break
            except YoupinError as e:
                monitor._safe_mark_error(s, ANON, e.code)
                continue
            monitor.guard.on_success()
            # 空列表是结论（没人在卖），不是失败；和市场价刷新同一口径
            r.sell_depth = len(items)
            r.sell_min = min((i.price for i in items), default=None)
            r.sell_src, r.sell_at = "youpin", monitor.now()
            _progress(s, run, VERIFY, n + 1, len(targets))
            say(f"核对在售：{n + 1}/{len(targets)}")


def _save(monitor, s: Session, run: PausedCheckRun, rows: list[_Row]) -> None:
    for r in rows:
        v = _verdict(monitor, r)
        s.add(PausedCheckItem(run_id=run.id, account_id=r.account_id, purchase_no=r.purchase_no,
                              template_id=r.template_id, commodity_name=r.name, price=r.price,
                              remaining_qty=r.remaining_qty, paused_at=r.paused_at, top_bid=r.top_bid,
                              top_empty=r.top_empty, sell_min=r.sell_min, sell_depth=r.sell_depth,
                              sell_src=r.sell_src, sell_at=r.sell_at, tier=v.tier, top1_kind=v.top1_kind))
    run.status, run.finished_at, run.phase = "done", monitor.now(), None
    s.flush()
    old = s.scalars(select(PausedCheckRun.id).where(PausedCheckRun.status != "running")
                    .order_by(PausedCheckRun.id.desc()).offset(KEEP_RUNS)).all()
    if old:
        s.execute(delete(PausedCheckItem).where(PausedCheckItem.run_id.in_(old)))
        s.execute(delete(PausedCheckRun).where(PausedCheckRun.id.in_(old)))
    s.commit()

