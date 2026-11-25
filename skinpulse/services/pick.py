"""价位三期：选品任务（价位 spec §9b.2）。

- 只请求 SteamDT：`/price/batch`（每批 ≤ 100 个，批间等 BATCH_COOLDOWN_S）和单品日线（按
  `valuation.rate_per_min` 限速）。**对悠悠不发任何请求**。
- 取到的日线照估值任务的规则写进 `daily_bar`（价位 spec §3.1），以后估值、回测都能用。
- 夜里价格快照（23:50）和估值（00:30）也在用同一份配额：窗口内不开始，跑到窗口里就暂停。
- 同一时间只跑一个（`PickRunner` 的锁）；服务重启时还在跑的记为 interrupted，不续跑。
- 日志、`error` 字段只记异常类型名：传输层异常的消息里可能带着挂了 key 的 URL。
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from skinpulse.adapters.steamdt.errors import SteamDTError
from skinpulse.analysis import pick as pk
from skinpulse.analysis import valuation as va
from skinpulse.core.localday import local_today
from skinpulse.db.models import DailyBar, Item, MyBuyOrder, PausedCheckRun, PickResult, PickRun
from skinpulse.db.session import utcnow
from skinpulse.services.market import BATCH_COOLDOWN_S, wait_batch_slot
from skinpulse.services.valuation import (
    PLATFORM, _broad_closes, _fetch_bars, _item_closes, _upsert_bars,
)
from skinpulse.services.buff import youpin_orders

log = logging.getLogger(__name__)

KEEP_RUNS = 10
BATCH = 100                     # /price/batch 一次最多 100 个名字
NIGHT_BEFORE = timedelta(minutes=10)   # 价格快照前 10 分钟起让出配额
NIGHT_AFTER = timedelta(minutes=20)    # 估值开始后 20 分钟内也不跑（430 个模板约 12 分钟）
NIGHT_POLL_S = 60
QUOTE_PLATFORMS = ("YOUPIN", "BUFF")


class PickRefused(Exception):
    """不能开始：status 是给接口用的 HTTP 码，消息是中文。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------------------------------------------------------------- 时间窗口

def _hm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def in_night_window(settings, now: datetime) -> bool:
    """`now` 落在「价格快照前 10 分钟」到「估值开始后 20 分钟」之间（按 settings.timezone，跨午夜）。"""
    local = now.astimezone(ZoneInfo(settings.timezone))
    minutes = local.hour * 60 + local.minute
    sh, sm = _hm(settings.brief.snapshot_at)
    eh, em = _hm(settings.valuation.run_at)
    start = (sh * 60 + sm - NIGHT_BEFORE.seconds // 60) % 1440
    end = (eh * 60 + em + NIGHT_AFTER.seconds // 60) % 1440
    return start <= minutes < end if start <= end else (minutes >= start or minutes < end)


def night_label(settings) -> str:
    sh, sm = _hm(settings.brief.snapshot_at)
    eh, em = _hm(settings.valuation.run_at)
    s = (sh * 60 + sm - NIGHT_BEFORE.seconds // 60) % 1440
    e = (eh * 60 + em + NIGHT_AFTER.seconds // 60) % 1440
    return f"{s // 60:02d}:{s % 60:02d}～{e // 60:02d}:{e % 60:02d}"


# ---------------------------------------------------------------- 圈池子、预估

_catalog_cache: dict = {}


def catalog(session: Session) -> list[tuple[int, str]]:
    """(模板号, 英文名)，模板号重复时取 `item.id` 最小的那条（和总览 `_hash_names` 一致）。

    目录 3.5 万条、很少变（`steamdt-items` 一天最多同步一次）：按「条数 + 最大 id」缓存，选品页每改一次条件
    就要预估一次，不能每次都重读重解析。"""
    key = (str(session.get_bind().url), *session.execute(select(func.count(Item.id), func.max(Item.id))
                                                         .where(Item.youpin_template_id.is_not(None))).one())
    if _catalog_cache.get("key") != key:
        rows = session.execute(select(Item.youpin_template_id, Item.hash_name)
                               .where(Item.youpin_template_id.is_not(None)).order_by(Item.id.desc())).tuples().all()
        by_tid = dict(rows)          # 倒序插入：最后留下的是 id 最小的
        _catalog_cache.update(key=key, rows=sorted(by_tid.items()),
                              parsed={t: pk.parse_name(n) for t, n in by_tid.items()})
    return _catalog_cache["rows"]


def pool_of(session: Session, f: pk.PoolFilter) -> list[int]:
    catalog(session)
    return sorted(t for t, p in _catalog_cache["parsed"].items() if f.matches(p))


def estimate_minutes(n: int, rate_per_min: int) -> int:
    """取价每批约 1 分钟（批间等 65 秒），日线按限速；价格区间筛掉的那部分不取日线，这里按全取估（偏多）。"""
    if n <= 0:
        return 0
    return math.ceil((math.ceil(n / BATCH) - 1) * BATCH_COOLDOWN_S / 60 + n / rate_per_min) or 1


def estimate(session: Session, f: pk.PoolFilter, settings) -> dict:
    n = len(pool_of(session, f))
    return {"count": n, "max": pk.MAX_POOL, "over": n > pk.MAX_POOL,
            "minutes": estimate_minutes(n, settings.valuation.rate_per_min)}


# ---------------------------------------------------------------- 运行记录

def create_run(session: Session, f: pk.PoolFilter, now: datetime) -> PickRun:
    run = PickRun(created_at=now, filters_json=json.dumps(f.to_dict(), ensure_ascii=False),
                  status="running", phase="pool", done=0, total=0, pool=0, priced=0, failed=0, price_failed=0)
    session.add(run)
    session.commit()
    prune(session)
    return run


def prune(session: Session, keep: int = KEEP_RUNS) -> None:
    old = list(session.scalars(select(PickRun.id).order_by(PickRun.id.desc()).offset(keep)))
    if old:
        session.execute(delete(PickResult).where(PickResult.run_id.in_(old)))
        session.execute(delete(PickRun).where(PickRun.id.in_(old)))
        session.commit()


def mark_interrupted(session: Session, now: datetime) -> int:
    runs = list(session.scalars(select(PickRun).where(PickRun.status == "running")))
    for r in runs:
        r.status, r.finished_at, r.error = "interrupted", now, "服务重启时还没跑完"
    session.commit()
    return len(runs)


# ---------------------------------------------------------------- 任务本体

def _wait_out_night(settings, now: Callable[[], datetime], sleep: Callable[[float], None]) -> None:
    while in_night_window(settings, now()):
        sleep(NIGHT_POLL_S)


def _quotes(prices: dict[str, object]) -> dict[str, va.Quote]:
    out = {}
    for p in QUOTE_PLATFORMS:
        pp = prices.get(p)
        if pp is not None:
            out[p] = va.Quote(sell=pp.sell_price, sell_count=pp.sell_count or 0, bid=pp.bidding_price)
    return out


def run_pick(session: Session, client, settings, run_id: int, *,
             sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
             now: Callable[[], datetime] | None = None) -> PickRun:
    # 不写成默认参数 `now=utcnow`：那样导入时就绑死了真实时钟，测试换掉模块里的 utcnow 也换不到这里，
    # 23:40～00:50 跑测试会卡在 _wait_out_night（2026-11-25 撞上）
    now = now or (lambda: utcnow())
    run = session.get(PickRun, run_id)
    try:
        _run(session, client, settings, run, sleep=sleep, clock=clock, now=now)
        run.status, run.phase, run.finished_at = "done", "done", now()
    except Exception as e:  # noqa: BLE001  任务失败不能拖垮服务；只记类型名（池子条件的错误是中文原因，照记）
        session.rollback()
        run = session.get(PickRun, run_id)
        run.status, run.finished_at = "failed", now()
        run.error = str(e)[:255] if isinstance(e, pk.PoolError) else type(e).__name__
        log.warning("pick run %d failed (%s)", run_id, type(e).__name__)
    session.commit()
    return run


def _run(session: Session, client, settings, run: PickRun, *, sleep, clock, now) -> None:
    f = pk.PoolFilter.from_dict(json.loads(run.filters_json))
    cfg = va.ValuationConfig.from_settings(settings.valuation)
    tz = settings.timezone
    day = local_today(tz, now()) - timedelta(days=1)       # 只用已收盘的日线

    names = dict(catalog(session))
    pool = pool_of(session, f)
    if len(pool) > pk.MAX_POOL:
        raise pk.PoolError(f"池子有 {len(pool)} 个，超过 {pk.MAX_POOL}")
    run.pool, run.phase, run.done, run.total = len(pool), "prices", 0, math.ceil(len(pool) / BATCH)
    session.commit()

    # 1. 分批取价
    by_name = {names[t]: t for t in pool}
    ordered = sorted(by_name)
    prices: dict[int, dict] = {}
    for i in range(0, len(ordered), BATCH):
        _wait_out_night(settings, now, sleep)
        wait_batch_slot(session, sleep)
        try:
            items = client.get_prices_batch(ordered[i:i + BATCH])
        except SteamDTError as e:
            log.warning("pick run %d: price batch %d failed (%s)", run.id, i // BATCH + 1, e)
            items = []
        except httpx.HTTPError as e:
            log.warning("pick run %d: price batch %d failed (%s)", run.id, i // BATCH + 1, type(e).__name__)
            items = []
        got = 0
        for it in items:
            tid = by_name.get(it.market_hash_name)
            if tid is not None:
                prices[tid] = {p.platform: p for p in it.platforms}
                got += 1
        run.price_failed += len(ordered[i:i + BATCH]) - got
        run.done += 1
        session.commit()

    # 2. 按价格区间筛（悠悠在售最低）
    kept = [t for t in pool if f.price_ok(getattr(prices.get(t, {}).get("YOUPIN"), "sell_price", None))]
    run.priced, run.phase, run.done, run.total = len(kept), "klines", 0, len(kept)
    session.commit()

    # 3. 逐个取日线、估值、给结论
    mine = set(session.scalars(select(MyBuyOrder.template_id).where(MyBuyOrder.active.is_(True), youpin_orders())))
    broad = _broad_closes(session, day)
    interval = 60 / settings.valuation.rate_per_min
    last_start: float | None = None
    for tid in kept:
        _wait_out_night(settings, now, sleep)
        if last_start is not None and (wait := interval - (clock() - last_start)) > 0:
            sleep(wait)
        last_start = clock()
        try:
            bars = va.closed_bars(_fetch_bars(client, names[tid], sleep), tz, day)
            _upsert_bars(session, DailyBar, {"template_id": tid, "platform": PLATFORM}, bars, day, now())
        except SteamDTError as e:
            run.failed += 1
            log.warning("pick run %d: template %d kline failed (%s)", run.id, tid, e)
        except httpx.HTTPError as e:
            run.failed += 1
            log.warning("pick run %d: template %d kline failed (%s)", run.id, tid, type(e).__name__)
        q = _quotes(prices.get(tid, {}))
        v = va.compute(_item_closes(session, tid, day), broad, q, cfg)
        yp = prices.get(tid, {}).get("YOUPIN")
        verdict = pk.verdict(v, yp.bidding_price if yp else None, rebound=(cfg.rebound_window, cfg.rebound_max))
        session.add(PickResult(
            run_id=run.id, template_id=tid,
            yp_sell=yp.sell_price if yp else None, yp_count=yp.sell_count if yp else None,
            yp_bid=yp.bidding_price if yp else None,
            bars=v.bars, pct=v.pct, trend=v.trend, rel=v.rel, q10=v.q10, liq=v.liq,
            exit=v.exit, exit_where=v.exit_where, cap=v.cap, rebound=v.rebound, amp=v.amp,
            verdict=verdict.kind, rule=verdict.rule, bid=verdict.bid, margin=verdict.margin,
            first=verdict.first, mine=tid in mine))
        run.done += 1
        session.commit()


class PickRunner:
    """看板进程里唯一的选品执行者：一把锁、一个后台线程。建的时候把上次进程留下的 running 标成中断。"""

    def __init__(self, rt, *, sleep: Callable[[float], None] = time.sleep, start_thread: bool = True):
        self.rt = rt
        self.sleep = sleep
        self.start_thread = start_thread
        self._lock = threading.Lock()
        self._busy = False
        with rt.session_factory() as s:
            if n := mark_interrupted(s, utcnow()):
                log.info("pick: marked %d unfinished run(s) as interrupted", n)

    @property
    def busy(self) -> bool:
        return self._busy

    def start(self, body) -> int:
        settings = self.rt.settings
        if getattr(self.rt, "steamdt", None) is None:
            raise PickRefused(503, "没配 SteamDT key，取不了价格和日线")
        try:
            f = pk.PoolFilter.from_dict(body)
        except pk.PoolError as e:
            raise PickRefused(422, str(e)) from None
        if in_night_window(settings, utcnow()):
            raise PickRefused(423, f"{night_label(settings)} 是价格快照和估值的时间，过了再来")
        with self._lock:
            if self._busy:
                raise PickRefused(409, "已经有一次选品在跑")
            with self.rt.session_factory() as s:
                # 充值前检查暂停单也在用 SteamDT 的批量价（1 次/分钟），两边一起跑都会撞 4005（暂停单 spec §3.4）
                if s.scalar(select(PausedCheckRun.id).where(PausedCheckRun.status == "running")) is not None:
                    raise PickRefused(409, "暂停单检查正在查 SteamDT 价格，等它跑完再选品")
            with self.rt.session_factory() as s:
                n = len(pool_of(s, f))
                if n == 0:
                    raise PickRefused(422, "按这些条件一个模板也圈不到")
                if n > pk.MAX_POOL:
                    raise PickRefused(422, f"池子有 {n} 个，超过 {pk.MAX_POOL}：再收窄一点（加武器、磨损或版本）")
                run_id = create_run(s, f, utcnow()).id
            self._busy = True
        if self.start_thread:
            threading.Thread(target=self._work, args=(run_id,), name=f"pick-{run_id}", daemon=True).start()
        return run_id

    def _work(self, run_id: int) -> None:
        try:
            with self.rt.session_factory() as s:
                run_pick(s, self.rt.steamdt, self.rt.settings, run_id, sleep=self.sleep)
        finally:
            self._busy = False
