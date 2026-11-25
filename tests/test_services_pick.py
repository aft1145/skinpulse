"""价位三期选品任务（价位 spec §9b.2）：假 SteamDT、假时钟，不发真实请求。"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

from skinpulse.adapters.steamdt.errors import SteamDTQuotaExceeded
from skinpulse.adapters.steamdt.models import ItemPrices, PlatformPrice
from skinpulse.analysis import pick as pk
from skinpulse.config import Settings
from skinpulse.db.models import DailyBar, Item, MyBuyOrder, PickResult, PickRun
from skinpulse.services import pick as sp
from tests.test_services_valuation import bars_until

NOW = datetime(2026, 10, 10, 6, 0, tzinfo=timezone.utc)        # 上海 14:00，不在夜间窗口
DAY = date(2026, 10, 9)


def pp(platform, sell, cnt, bid):
    return PlatformPrice(platform=platform, platform_item_id="1", sell_price=None if sell is None else D(sell),
                         sell_count=cnt, bidding_price=None if bid is None else D(bid), bidding_count=0, update_time=None)


class FakeDT:
    def __init__(self, prices, *, kline_fail=(), batch_fail=()):
        self.prices = prices                  # hash -> (yp_sell, yp_cnt, yp_bid)
        self.batches, self.klines = [], []
        self.kline_fail, self.batch_fail = set(kline_fail), set(batch_fail)

    def get_prices_batch(self, names):
        self.batches.append(list(names))
        if len(self.batches) in self.batch_fail:
            raise httpx.ConnectError("https://open.steamdt.com/x?key=SECRET")
        out = []
        for n in names:
            if n in self.prices:
                sell, cnt, bid = self.prices[n]
                out.append(ItemPrices(market_hash_name=n, platforms=[pp("YOUPIN", sell, cnt, bid), pp("BUFF", sell, cnt, None)]))
        return out

    def get_item_kline(self, mhn, period, platform):
        self.klines.append(mhn)
        if mhn in self.kline_fail:
            raise SteamDTQuotaExceeded("4005", "当前接口请求已达到上限")
        return bars_until(DAY, 120, close=lambda i: 100 + (i % 7))


def seed_catalog(s, n=3, extra=()):
    for i in range(1, n + 1):
        s.add(Item(hash_name=f"AK-47 | Skin{i} (Field-Tested)", cn_name=f"AK {i}", youpin_template_id=i))
    for tid, name in extra:
        s.add(Item(hash_name=name, cn_name=name, youpin_template_id=tid))
    s.commit()


def go(s, f: dict, dt, settings=None, **kw):
    run = sp.create_run(s, pk.PoolFilter.from_dict(f), NOW)
    slept = []
    return sp.run_pick(s, dt, settings or Settings(), run.id, sleep=slept.append, clock=lambda: 0.0,
                       now=lambda: NOW, **kw), slept


def test_prices_klines_verdicts_and_daily_bars(db_session):
    seed_catalog(db_session, extra=[(9, "Sticker | Clown Nose")])
    db_session.add(MyBuyOrder(purchase_no="P", template_id=2, commodity_name="x", price=D("1"), qty=1, remaining_qty=1,
                              type_id=-1, queue_key="2|ANY|-1|ANY|ANY", synced_at=NOW, active=True))
    db_session.commit()
    dt = FakeDT({"AK-47 | Skin1 (Field-Tested)": ("120", 200, "100"),      # 上限够得着 90%：可以挂（价差 17%，不算难出手）
                 "AK-47 | Skin2 (Field-Tested)": ("120", 200, "200"),      # 求购比上限高很多：观望
                 "AK-47 | Skin3 (Field-Tested)": ("120", 200, None)})      # 没有求购价：观望
    run, _ = go(db_session, {"groups": ["rifle"]}, dt)
    assert (run.status, run.phase, run.pool, run.priced, run.done, run.total, run.failed) == ("done", "done", 3, 3, 3, 3, 0)
    assert dt.batches == [sorted(dt.prices)] and len(dt.klines) == 3            # 印花不在池子里
    res = {r.template_id: r for r in db_session.scalars(select(PickResult))}
    assert res[1].verdict == "go" and res[1].first and res[1].bid == D("100.01")   # 合成日线 7 天坏情况 0：上限 101.25
    assert res[2].verdict == "wait" and "90%" in res[2].rule
    assert res[3].verdict == "wait" and "没有悠悠求购价" in res[3].rule
    assert res[2].mine and not res[1].mine
    assert res[1].yp_sell == D("120") and res[1].yp_bid == D("100")
    # 日线照估值的规则写进 daily_bar：只到昨天（已收盘）
    assert db_session.scalar(select(func.max(DailyBar.date)).where(DailyBar.template_id == 1)) == DAY.isoformat()


def test_batches_of_100_with_cooldown_and_price_range(db_session):
    seed_catalog(db_session, n=205)
    prices = {f"AK-47 | Skin{i} (Field-Tested)": (str(i), 50, "1") for i in range(1, 206)}
    dt = FakeDT(prices)
    run, slept = go(db_session, {"groups": ["rifle"], "price_min": 10, "price_max": 19}, dt)
    assert [len(b) for b in dt.batches] == [100, 100, 5]
    assert slept.count(sp.BATCH_COOLDOWN_S) == 2
    assert run.pool == 205 and run.priced == 10 and len(dt.klines) == 10        # 区间外的不取日线


def test_kline_quota_retries_once_then_counts_failure(db_session):
    seed_catalog(db_session, n=2)
    dt = FakeDT({f"AK-47 | Skin{i} (Field-Tested)": ("120", 200, "60") for i in (1, 2)},
                kline_fail={"AK-47 | Skin1 (Field-Tested)"})
    run, slept = go(db_session, {"groups": ["rifle"]}, dt)
    assert run.status == "done" and run.failed == 1
    assert dt.klines.count("AK-47 | Skin1 (Field-Tested)") == 2 and 65 in slept      # 等 65 秒再试一次
    r1 = db_session.scalar(select(PickResult).where(PickResult.template_id == 1))
    assert r1.verdict == "none"                                                       # 没日线：不评价


def test_failed_price_batch_is_skipped_not_fatal(db_session):
    seed_catalog(db_session, n=2)
    dt = FakeDT({f"AK-47 | Skin{i} (Field-Tested)": ("120", 200, "60") for i in (1, 2)}, batch_fail={1})
    run, _ = go(db_session, {"groups": ["rifle"]}, dt)
    assert run.status == "done" and run.priced == 2                                   # 没设区间：没价的照样评
    assert {r.verdict for r in db_session.scalars(select(PickResult))} == {"none"}    # 没在售价：不评价


def test_unexpected_error_marks_failed_with_type_only(db_session):
    seed_catalog(db_session, n=1)

    class Boom(FakeDT):
        def get_item_kline(self, *a):
            raise RuntimeError("https://x?key=SECRET")
    run, _ = go(db_session, {"groups": ["rifle"]}, Boom({}))
    assert run.status == "failed" and run.error == "RuntimeError"


def test_pauses_through_the_night_window(db_session):
    seed_catalog(db_session, n=1)
    t = [datetime(2026, 10, 9, 15, 45, tzinfo=timezone.utc)]          # 上海 23:45：窗口里
    slept = []

    def sleep(s):
        slept.append(s)
        t[0] += timedelta(seconds=s)
    run = sp.create_run(db_session, pk.PoolFilter.from_dict({"groups": ["rifle"]}), t[0])
    sp.run_pick(db_session, FakeDT({"AK-47 | Skin1 (Field-Tested)": ("120", 200, "60")}), Settings(), run.id,
                sleep=sleep, clock=lambda: 0.0, now=lambda: t[0])
    assert run.status == "done"
    assert not sp.in_night_window(Settings(), t[0])
    assert slept.count(sp.NIGHT_POLL_S) >= 60                          # 至少等到 00:50


@pytest.mark.parametrize("hhmm, inside", [("23:39", False), ("23:40", True), ("00:00", True), ("00:49", True), ("00:50", False), ("14:00", False)])
def test_night_window(hhmm, inside):
    h, m = map(int, hhmm.split(":"))
    local = datetime(2026, 10, 9, h, m, tzinfo=timezone(timedelta(hours=8)))
    assert sp.in_night_window(Settings(), local) is inside
    assert sp.night_label(Settings()) == "23:40～00:50"


def test_prune_keeps_last_runs_and_their_results(db_session):
    for i in range(12):
        r = sp.create_run(db_session, pk.PoolFilter(), NOW)
        db_session.add(PickResult(run_id=r.id, template_id=1, bars=0, verdict="none", rule="x", first=False, mine=False))
        db_session.commit()
    sp.prune(db_session)
    ids = list(db_session.scalars(select(PickRun.id).order_by(PickRun.id)))
    assert len(ids) == sp.KEEP_RUNS and ids[0] == 3
    assert set(db_session.scalars(select(PickResult.run_id))) == set(ids)


def test_estimate(db_session):
    seed_catalog(db_session, n=3)
    e = sp.estimate(db_session, pk.PoolFilter.from_dict({"groups": ["rifle"]}), Settings())
    assert e == {"count": 3, "max": 500, "over": False, "minutes": 1}
    assert sp.estimate_minutes(500, 60) == 13             # 4 次批间 65 秒 + 500 根日线
    assert sp.estimate_minutes(0, 60) == 0


# ---- PickRunner ----

def _rt(db_session, steamdt=object()):
    factory = lambda: _Ctx(db_session)   # noqa: E731
    return SimpleNamespace(settings=Settings(), steamdt=steamdt, session_factory=factory)


class _Ctx:
    def __init__(self, s):
        self.s = s

    def __enter__(self):
        return self.s

    def __exit__(self, *a):
        return False


def test_runner_marks_leftover_runs_interrupted(db_session):
    db_session.add(PickRun(created_at=NOW, filters_json="{}", status="running", phase="klines", done=1, total=5,
                           pool=5, priced=5, failed=0))
    db_session.commit()
    sp.PickRunner(_rt(db_session), start_thread=False)
    r = db_session.scalar(select(PickRun))
    assert r.status == "interrupted" and r.error


def test_runner_refusals(db_session, monkeypatch):
    seed_catalog(db_session, n=3)
    monkeypatch.setattr(sp, "utcnow", lambda: NOW)
    with pytest.raises(sp.PickRefused) as e:
        sp.PickRunner(_rt(db_session, steamdt=None), start_thread=False).start({"groups": ["rifle"]})
    assert e.value.status == 503
    runner = sp.PickRunner(_rt(db_session), start_thread=False)
    for body, status in (({"groups": []}, 422), ({"groups": ["knife"]}, 422)):
        with pytest.raises(sp.PickRefused) as e:
            runner.start(body)
        assert e.value.status == status
    run_id = runner.start({"groups": ["rifle"]})
    assert runner.busy and json.loads(db_session.get(PickRun, run_id).filters_json)["groups"] == ["rifle"]
    with pytest.raises(sp.PickRefused) as e:
        runner.start({"groups": ["rifle"]})
    assert e.value.status == 409
    monkeypatch.setattr(sp, "utcnow", lambda: datetime(2026, 10, 9, 16, 0, tzinfo=timezone.utc))   # 上海 00:00
    runner._busy = False
    with pytest.raises(sp.PickRefused) as e:
        runner.start({"groups": ["rifle"]})
    assert e.value.status == 423 and "23:40～00:50" in e.value.message


def test_runner_refuses_over_500(db_session, monkeypatch):
    seed_catalog(db_session, n=501)
    monkeypatch.setattr(sp, "utcnow", lambda: NOW)
    with pytest.raises(sp.PickRefused) as e:
        sp.PickRunner(_rt(db_session), start_thread=False).start({"groups": ["rifle"]})
    assert e.value.status == 422 and "501" in e.value.message


def test_failed_price_batch_counts_templates_and_price_range_drops_them(db_session):
    seed_catalog(db_session, n=2)
    dt = FakeDT({f"AK-47 | Skin{i} (Field-Tested)": ("120", 200, "100") for i in (1, 2)}, batch_fail={1})
    run, _ = go(db_session, {"groups": ["rifle"], "price_min": 1}, dt)
    assert run.price_failed == 2 and run.priced == 0 and dt.klines == []


def test_pool_error_keeps_its_reason(db_session, monkeypatch):
    seed_catalog(db_session, n=3)
    monkeypatch.setattr(pk, "MAX_POOL", 2)
    run, _ = go(db_session, {"groups": ["rifle"]}, FakeDT({}))
    assert run.status == "failed" and run.error == "池子有 3 个，超过 2"


def test_catalog_keeps_the_earliest_item_for_a_repeated_template(db_session):
    seed_catalog(db_session, n=1)
    db_session.add(Item(hash_name="Sticker | Later", cn_name="x", youpin_template_id=1))
    db_session.commit()
    assert sp.catalog(db_session) == [(1, "AK-47 | Skin1 (Field-Tested)")]


def test_runner_frees_itself_after_a_crashed_run(db_session, monkeypatch):
    seed_catalog(db_session, n=1)
    monkeypatch.setattr(sp, "utcnow", lambda: NOW)
    monkeypatch.setattr(sp, "run_pick", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    runner = sp.PickRunner(_rt(db_session), start_thread=False)
    run_id = runner.start({"groups": ["rifle"]})
    with pytest.raises(RuntimeError):
        runner._work(run_id)
    assert runner.busy is False
