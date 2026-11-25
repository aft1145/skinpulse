"""每日估值（价位 spec §3、§5）：给活跃求购单的饰品存悠悠日线，算价位标签和扣费差价，写进
`valuation` 表，看板只读这张表。

- 只请求 SteamDT：单品日线每个模板一次，按 `valuation.rate_per_min` 限速（配额 120 次/分钟，
  留一半给看板）；大盘日线走 `MarketData.broad_kline` 的缓存（大盘接口很容易 4005）。
  **不向悠悠发任何请求**：在售价、在售数读当天 23:50 的 price_daily。
- 估值用的是**自有**日线（`daily_bar`），不是这次取到的原样数据：SteamDT 的历史会变，自己存的
  才能复现。这次取不到（配额、网络）也照样用库里已有的算。
- 单个模板失败只跳过这个模板；整个任务失败由调度器兜住，绝不影响监控和告警。
- 日志里绝不写 httpx 异常本身：URL 上挂着 key，只记类型名。
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from skinpulse.adapters.steamdt.errors import SteamDTError, SteamDTQuotaExceeded
from skinpulse.adapters.steamdt.models import KlinePeriod
from skinpulse.analysis import valuation as va
from skinpulse.db.models import DailyBar, IndexDaily, MyBuyOrder, PriceDaily, Valuation
from skinpulse.services.market import resolve_names
from skinpulse.services.buff import youpin_orders

log = logging.getLogger(__name__)

PLATFORM = "YOUPIN"          # 视觉稿阶段实测 ALL 和 YOUPIN 几乎一样（价位 spec §2），只取悠悠
QUOTA_RETRY_S = 65           # 4005 后等一分多钟再试一次（配额按分钟算）
REFRESH_RECENT_DAYS = 3      # 已存的日线只覆盖最近 3 天（D、D−1、D−2），更早的以自己存的为准
YEAR_DAYS = 400              # 读自有日线时往回取多少天（够 365 根，留出缺数据的余量）


@dataclass
class ValuationReport:
    day: str
    templates: int = 0       # 活跃求购单涉及的模板数
    unmapped: int = 0        # 没有 SteamDT 映射的
    fetched: int = 0         # 这次取到日线的
    failed: int = 0          # 这次没取到的（仍用库里已有的日线估值）
    bars_written: int = 0
    valued: int = 0
    no_snapshot: bool = False    # D 这天没有价格快照（23:50 没跑）：差价、出手难易都为空
    broad_ok: bool = False


def _upsert_bars(session: Session, model, key: dict, rows, day: date, now: datetime) -> int:
    """按 spec §3.1 写日线：没有的插入；已有的只在 date ≥ D−2 时覆盖。rows 来自 closed_bars。"""
    cutoff = (day - timedelta(days=REFRESH_RECENT_DAYS - 1)).isoformat()
    existing = {r.date: r for r in session.scalars(select(model).filter_by(**key))}
    written = 0
    for d, o, c, h, lo in rows:
        ds = d.isoformat()
        row = existing.get(ds)
        if row is None:
            session.add(model(**key, date=ds, open=o, close=c, high=h, low=lo, fetched_at=now))
        elif ds >= cutoff:
            row.open, row.close, row.high, row.low, row.fetched_at = o, c, h, lo, now
        else:
            continue
        written += 1
    return written


def _fetch_bars(client, mhn: str, sleep: Callable[[float], None]):
    try:
        return client.get_item_kline(mhn, KlinePeriod.DAY, PLATFORM)
    except SteamDTQuotaExceeded:
        sleep(QUOTA_RETRY_S)
        return client.get_item_kline(mhn, KlinePeriod.DAY, PLATFORM)


def _broad_closes(session: Session, day: date) -> dict[date, Decimal]:
    since = (day - timedelta(days=YEAR_DAYS)).isoformat()
    rows = session.execute(select(IndexDaily.date, IndexDaily.close)
                           .where(IndexDaily.date >= since, IndexDaily.date <= day.isoformat()))
    return {date.fromisoformat(d): c for d, c in rows}


def _item_closes(session: Session, tid: int, day: date) -> list[tuple[date, Decimal]]:
    since = (day - timedelta(days=YEAR_DAYS)).isoformat()
    rows = session.execute(select(DailyBar.date, DailyBar.close)
                           .where(DailyBar.template_id == tid, DailyBar.platform == PLATFORM,
                                  DailyBar.date >= since, DailyBar.date <= day.isoformat())
                           .order_by(DailyBar.date))
    return [(date.fromisoformat(d), c) for d, c in rows]


def _quotes(session: Session, tid: int, key: str) -> dict[str, va.Quote]:
    rows = session.scalars(select(PriceDaily).where(PriceDaily.date == key, PriceDaily.template_id == tid))
    return {r.platform: va.Quote(sell=r.sell_price, sell_count=r.sell_count or 0, bid=r.bidding_price) for r in rows}


def run_valuation(session: Session, client, market, settings, *, day: date, now: datetime,
                  sleep: Callable[[float], None] = time.sleep, dry_run: bool = False,
                  clock: Callable[[], float] = time.monotonic) -> ValuationReport:
    """给 D（刚结束的本地日）估值。market 为 None（没配 key 的看板缓存）时只用库里已有的大盘。

    dry_run：照常请求 SteamDT、照常计算，但一行都不留（最后回滚）。"""
    cfg = va.ValuationConfig.from_settings(settings.valuation)
    tz = settings.timezone
    key = day.isoformat()
    rep = ValuationReport(day=key)
    persist = session.flush if dry_run else session.commit

    tids = sorted(set(session.scalars(select(MyBuyOrder.template_id).where(MyBuyOrder.active.is_(True), youpin_orders()))))
    rep.templates = len(tids)
    names = resolve_names(session, tids)
    rep.unmapped = len(tids) - len(names)
    rep.no_snapshot = session.scalar(select(PriceDaily.id).where(PriceDaily.date == key).limit(1)) is None

    if market is not None:
        try:
            broad = va.closed_bars(market.broad_kline(KlinePeriod.DAY).value, tz, day)
            _upsert_bars(session, IndexDaily, {}, broad, day, now)
            persist()
            rep.broad_ok = True
        except SteamDTError as e:
            log.warning("valuation %s: market kline unavailable (%s); using stored index only", key, e)
        except httpx.HTTPError as e:
            log.warning("valuation %s: market kline unavailable (%s); using stored index only", key, type(e).__name__)
    broad_map = _broad_closes(session, day)

    # rate_per_min 是上限：两次请求的**开始**至少隔 interval 秒，请求本身花掉的时间算在里面
    interval = 60 / settings.valuation.rate_per_min
    last_start: float | None = None
    for tid in sorted(names):
        if last_start is not None:
            wait = interval - (clock() - last_start)
            if wait > 0:
                sleep(wait)
        last_start = clock()
        mhn = names[tid]
        try:
            bars = va.closed_bars(_fetch_bars(client, mhn, sleep), tz, day)
            rep.bars_written += _upsert_bars(session, DailyBar, {"template_id": tid, "platform": PLATFORM},
                                             bars, day, now)
            rep.fetched += 1
        except SteamDTError as e:
            # SteamDTError 的消息只有错误码和服务端文案，不含 key
            rep.failed += 1
            log.warning("valuation %s: template %d kline failed (%s); valuing from stored bars", key, tid, e)
        except httpx.HTTPError as e:
            rep.failed += 1
            log.warning("valuation %s: template %d kline failed (%s); valuing from stored bars", key, tid,
                        type(e).__name__)
        quotes = _quotes(session, tid, key)
        v = va.compute(_item_closes(session, tid, day), broad_map, quotes, cfg)
        session.execute(delete(Valuation).where(Valuation.date == key, Valuation.template_id == tid))
        session.add(Valuation(date=key, template_id=tid, computed_at=now,
                              snapshot_date=key if quotes else None,
                              **{k: x for k, x in v.__dict__.items() if k not in va.PICK_ONLY_FIELDS}))
        persist()
        if v.bars:                      # 一根日线都没有的不算「估值了」，报告和系统页才不会虚高
            rep.valued += 1

    if dry_run:
        session.rollback()
    if rep.unmapped:
        log.info("valuation %s: %d template(s) have no SteamDT mapping (run steamdt-items)", key, rep.unmapped)
    return rep
