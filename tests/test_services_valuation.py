"""services/valuation：每日估值任务（价位 spec §3、§5）。"""
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import httpx
from sqlalchemy import select

from skinpulse.adapters.steamdt.errors import SteamDTError, SteamDTQuotaExceeded
from skinpulse.adapters.steamdt.models import KlineBar
from skinpulse.config import Settings
from skinpulse.db.models import DailyBar, IndexDaily, Item, MyBuyOrder, PriceDaily, Valuation
from skinpulse.db.session import utcnow
from skinpulse.services.market_data import Fetched
from skinpulse.services.valuation import run_valuation

DAY = date(2026, 9, 26)
NOW = datetime(2026, 9, 26, 16, 30, tzinfo=timezone.utc)   # 上海 09-27 00:30


def bars_until(last: date, n: int, close=lambda i: 100 + i % 5, zero_on=()):
    """n 根日线，最后一根是 last（上海 00:00 打点），再加一根没收盘的 last+1。"""
    out = []
    for k in range(n + 1):
        d = last - timedelta(days=n - 1 - k)
        ts = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) - timedelta(hours=8)
        c = D("0") if d in zero_on else D(str(close(k)))
        out.append(KlineBar(ts=ts, open=c, close=c, high=c, low=c))
    return out


class FakeDT:
    def __init__(self, bars=None, quota_calls=(), transport_calls=(), other_calls=()):
        self.bars = bars if bars is not None else bars_until(DAY, 100)
        self.calls = []
        self.quota_calls, self.transport_calls, self.other_calls = set(quota_calls), set(transport_calls), set(other_calls)

    def get_item_kline(self, mhn, period, platform):
        self.calls.append((mhn, int(period), platform))
        n = len(self.calls)
        if n in self.quota_calls:
            raise SteamDTQuotaExceeded("4005", "当前接口请求已达到上限")
        if n in self.transport_calls:
            raise httpx.ConnectError("Connection refused: https://open.steamdt.com/x?key=SECRETKEY123")
        if n in self.other_calls:
            raise SteamDTError("500", "服务端异常")
        return list(self.bars)


class FakeMarket:
    def __init__(self, bars=None, fail=None):
        self.bars = bars if bars is not None else bars_until(DAY, 100, close=lambda i: 1000)
        self.fail = fail

    def broad_kline(self, period):
        if self.fail:
            raise self.fail
        return Fetched(self.bars, NOW)


def seed(s, n=2):
    now = utcnow()
    for tid in range(1, n + 1):
        s.add(MyBuyOrder(purchase_no=f"P{tid}", template_id=tid, commodity_name=f"item {tid}", price=D("50"),
                         qty=1, remaining_qty=1, type_id=-1, queue_key=f"{tid}|ANY|-1|ANY|ANY",
                         synced_at=now, active=True))
        s.add(Item(hash_name=f"hash {tid}", cn_name=None, youpin_template_id=tid))
    s.add(MyBuyOrder(purchase_no="U", template_id=77, commodity_name="unmapped", price=D("5"), qty=1,
                     remaining_qty=1, type_id=-1, queue_key="77|ANY|-1|ANY|ANY", synced_at=now, active=True))
    s.commit()


def snapshot(s, tid=1, day=DAY):
    for platform, sell, cnt, bid in (("YOUPIN", "100", 60, "96"), ("BUFF", "105", 50, None)):
        s.add(PriceDaily(date=day.isoformat(), template_id=tid, market_hash_name=f"hash {tid}", platform=platform,
                         sell_price=D(sell), sell_count=cnt, bidding_price=None if bid is None else D(bid),
                         bidding_count=0, update_time=None, fetched_at=utcnow()))
    s.commit()


_DEFAULT = object()


def run(s, dt=None, market=_DEFAULT, settings=None, **kw):
    slept = []
    kw.setdefault("clock", lambda: 0.0)       # 时间不走：每次都要歇满间隔，断言才稳定
    rep = run_valuation(s, dt or FakeDT(), FakeMarket() if market is _DEFAULT else market,
                        settings or Settings(), day=DAY, now=NOW, sleep=slept.append, **kw)
    return rep, slept


def test_stores_closed_bars_and_values_each_mapped_template(db_session):
    seed(db_session)
    snapshot(db_session)
    dt = FakeDT(bars=bars_until(DAY, 100, zero_on={DAY - timedelta(days=3)}))
    rep, slept = run(db_session, dt)
    assert [c[1:] for c in dt.calls] == [(2, "YOUPIN")] * 2          # 日线、悠悠
    assert slept == [1.0]                                           # 每分钟 60 次 → 两次请求之间 1 秒
    assert (rep.templates, rep.unmapped, rep.fetched, rep.failed, rep.valued) == (3, 1, 2, 0, 2)
    dates = db_session.scalars(select(DailyBar.date).where(DailyBar.template_id == 1)).all()
    assert max(dates) == "2026-09-26" and "2026-09-27" not in dates   # 没收盘的那根不存
    assert "2026-09-23" not in dates and len(dates) == 99             # 0 价那根整根不存
    assert len(db_session.scalars(select(IndexDaily)).all()) == 100
    v1 = db_session.scalar(select(Valuation).where(Valuation.template_id == 1))
    assert v1.date == "2026-09-26" and v1.bars == 99 and v1.pct is not None and v1.snapshot_date == "2026-09-26"
    # 到手价默认只算悠悠（intent 2026-11-23）：BUFF 105 到手更多也不算；出手难易照旧数 BUFF 的在售件数
    assert (v1.exit_where, v1.exit_sell, v1.liq) == ("YOUPIN", D("100"), "好出手")
    assert v1.b30 == D("0")                                          # 大盘一直 1000
    v2 = db_session.scalar(select(Valuation).where(Valuation.template_id == 2))
    assert v2.exit is None and v2.snapshot_date is None and v2.pct is not None   # 模板 2 没有价格快照


def test_old_bars_are_kept_but_recent_ones_are_refreshed(db_session):
    seed(db_session, n=1)
    now = utcnow()
    for d, c in (("2026-09-01", "1"), ("2026-09-24", "2")):
        db_session.add(DailyBar(template_id=1, platform="YOUPIN", date=d, open=D(c), close=D(c), high=D(c),
                                low=D(c), fetched_at=now))
    db_session.commit()
    run(db_session)
    got = dict(db_session.execute(select(DailyBar.date, DailyBar.close).where(DailyBar.template_id == 1)).all())
    assert got["2026-09-01"] == D("1")        # 老的不覆盖：SteamDT 的历史会变，自己存的为准
    assert got["2026-09-24"] != D("2")        # D−2 以内覆盖：那几根 SteamDT 可能还在修正


def test_quota_retries_once_then_skips_but_still_values_from_own_bars(db_session, caplog):
    seed(db_session, n=2)
    run(db_session)                                                   # 先攒一份自有日线
    dt = FakeDT(quota_calls={1, 2})                                   # 模板 1：第一次、重试都 4005
    with caplog.at_level(logging.WARNING):
        rep, slept = run(db_session, dt)
    assert 65 in slept and len(dt.calls) == 3
    assert (rep.fetched, rep.failed, rep.valued) == (1, 1, 2)         # 取不到也用库里的日线照算
    dt2 = FakeDT(quota_calls={1})                                     # 重试成功
    rep2, _ = run(db_session, dt2)
    assert (rep2.fetched, rep2.failed) == (2, 0)


def test_transport_errors_are_skipped_without_leaking_the_url(db_session, caplog):
    seed(db_session, n=2)
    with caplog.at_level(logging.WARNING):
        rep, _ = run(db_session, FakeDT(transport_calls={1}, other_calls={2}))
    assert (rep.fetched, rep.failed) == (0, 2)
    assert rep.valued == 0            # 一根日线都没有：不算「估值了」（评审 2026-09-28）
    assert "ConnectError" in caplog.text and "SECRETKEY123" not in caplog.text


def test_market_failure_leaves_market_fields_empty(db_session):
    seed(db_session, n=1)
    rep, _ = run(db_session, market=FakeMarket(fail=SteamDTQuotaExceeded("4005", "上限")))
    v = db_session.scalar(select(Valuation))
    assert rep.broad_ok is False and v.b30 is None and v.rel is None and v.pct is not None
    rep2, _ = run(db_session, market=None)            # 没配 key 时没有 MarketData：只用库里已有的大盘
    assert rep2.broad_ok is False


def test_rerun_same_day_replaces_rows(db_session):
    seed(db_session, n=1)
    run(db_session)
    snapshot(db_session)
    run(db_session)
    rows = db_session.scalars(select(Valuation)).all()
    assert len(rows) == 1 and rows[0].exit is not None


def test_no_snapshot_for_the_day_is_reported(db_session):
    seed(db_session, n=1)
    snapshot(db_session, day=DAY - timedelta(days=1))                 # 前一天的不算
    rep, _ = run(db_session)
    assert rep.no_snapshot is True
    assert db_session.scalar(select(Valuation)).exit is None


def test_dry_run_writes_nothing(db_session):
    seed(db_session, n=1)
    rep, _ = run(db_session, dry_run=True)
    assert rep.valued == 1
    assert db_session.scalars(select(Valuation)).all() == []
    assert db_session.scalars(select(DailyBar)).all() == []
    assert db_session.scalars(select(IndexDaily)).all() == []


def test_rate_limit_is_a_ceiling_not_an_added_delay(db_session):
    """每分钟 60 次是上限：请求本身已经花掉的时间要算进间隔里，不能每次都再歇满 1 秒
    （实测每次请求约 1.7 秒，固定再歇 1 秒就只剩每分钟 22 次，430 件要 20 分钟）。"""
    seed(db_session, n=3)
    t = [0.0]

    class SlowDT(FakeDT):
        def get_item_kline(self, *a):
            t[0] += 0.4                       # 这次请求花了 0.4 秒
            return super().get_item_kline(*a)

    slept = []
    def sleep(s):
        slept.append(round(s, 3))
        t[0] += s
    run_valuation(db_session, SlowDT(), FakeMarket(), Settings(), day=DAY, now=NOW, sleep=sleep, clock=lambda: t[0])
    assert slept == [0.6, 0.6]                # 只补足到 1 秒
    slept.clear()
    t[0] = 0.0

    class SlowerDT(FakeDT):
        def get_item_kline(self, *a):
            t[0] += 1.7
            return super().get_item_kline(*a)
    run_valuation(db_session, SlowerDT(), FakeMarket(), Settings(), day=DAY, now=NOW, sleep=sleep, clock=lambda: t[0])
    assert slept == []                        # 请求本身就超过 1 秒：不再歇
