"""充值前检查暂停单的服务（暂停单 spec §3）：用假的悠悠 / SteamDT 客户端，不发真请求。"""
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from skinpulse.adapters.steamdt.errors import SteamDTError
from skinpulse.adapters.steamdt.models import ItemPrices, PlatformPrice
from skinpulse.adapters.youpin.errors import LoginRequired, RiskControl
from skinpulse.adapters.youpin.models import MyPurchaseOrder, OnSaleItem
from skinpulse.db.models import Item, PausedCheckItem, PausedCheckRun, PickRun
from skinpulse.db.session import make_engine, make_session_factory, upgrade_db
from skinpulse.services import status as st
from skinpulse.services.account import AccountRuntime
from skinpulse.services.monitor import ANON, Monitor
from skinpulse.services.paused import PausedRefused, mark_interrupted, run_check, start_run

NOON = datetime(2026, 11, 24, 4, 0, tzinfo=timezone.utc)   # 北京时间 12:00，不在夜间窗口


def raw(no, tid, price, top, *, created="2026.07.26 18:49:16", name=None, qty=1):
    return {"orderNo": no, "templateId": tid, "commodityName": name or f"饰品 {tid}", "unitPrice": str(price),
            "quantity": qty, "buyQuantity": 0, "maxPurchasePrice": top, "createTime": created, "status": 30}


class FakeAccountClient:
    def __init__(self, pages, *, token="t", error=None, lock=None):
        self.pages, self.token, self.error, self.lock, self.uk = pages, token, error, lock, "u"
        self.calls = []

    def search_my_purchase_orders_page(self, status, page, size):
        assert status == 30 and size == 40
        if self.lock is not None:
            assert self.lock.locked(), "翻页时必须持锁"
        self.calls.append(page)
        if self.error:
            raise self.error
        return [MyPurchaseOrder.from_api(r) for r in (self.pages[page - 1] if page <= len(self.pages) else [])]


class FakeAnon:
    def __init__(self, on_sale, *, error=None, lock=None):
        self.on_sale, self.error, self.lock, self.token = on_sale, error, lock, None
        self.calls = []

    def get_on_sale(self, tid, page_size=10):
        if self.lock is not None:
            assert self.lock.locked(), "实时核对时必须持锁"
        self.calls.append(tid)
        if self.error:
            raise self.error
        return [OnSaleItem("c", 0, D(p), "", False) for p in self.on_sale.get(tid, [])]


class FakeSteamDT:
    def __init__(self, prices, *, error=None, lock=None):
        self.prices, self.error, self.lock = prices, error, lock
        self.batches = []

    def get_prices_batch(self, names):
        if self.lock is not None:
            assert not self.lock.locked(), "查 SteamDT 时不该持锁"
        self.batches.append(list(names))
        if self.error:
            raise self.error
        return [ItemPrices(n, [PlatformPrice("YOUPIN", "", D(self.prices[n]), 9, None, 0, NOON)])
                for n in names if n in self.prices]


@pytest.fixture
def Session(tmp_path):
    db = tmp_path / "t.db"
    upgrade_db(db)
    S = make_session_factory(make_engine(db))
    with S() as s:
        for tid in range(1, 400):
            s.add(Item(hash_name=f"H{tid}", youpin_template_id=tid))
        s.commit()
    return S


def make_monitor(Session, settings, accounts, anon, steamdt=None):
    accs = {i: AccountRuntime(i, c, label="小号" if i == 2 else "") for i, c in accounts.items()}
    return Monitor(Session, anon, None, settings, steamdt=steamdt, accounts=accs, now=lambda: NOON,
                   sleep=lambda _s: None, rnd=lambda: 0.5)


def items_by_no(Session, run_id):
    with Session() as s:
        return {i.purchase_no: i for i in s.scalars(select(PausedCheckItem).where(PausedCheckItem.run_id == run_id))}


def start(Session, m, source="dashboard"):
    with Session() as s:
        return start_run(s, m, source, NOON)


def test_full_check_two_accounts_tiers_and_sources(Session, settings):
    lock = threading.Lock()
    a1 = FakeAccountClient([[raw("A", 1, 650, "¥190"), raw("B", 2, 16, "¥15.5"), raw("C", 3, 95, "¥100"),
                             raw("D", 4, 10, "¥30")]], lock=lock)
    a2 = FakeAccountClient([[raw("E", 5, 300, "暂无", created="2026.11.24 10:00:00")]], lock=lock)
    anon = FakeAnon({1: ["205"], 5: []}, lock=lock)
    sd = FakeSteamDT({"H1": "210", "H2": "40", "H3": "200", "H4": "50"}, lock=lock)
    m = make_monitor(Session, settings, {1: a1, 2: a2}, anon, sd)
    rid = start(Session, m)
    res = run_check(m, rid, lock=lock)
    assert res.status == "done" and res.rows == 5 and res.skipped == []
    got = items_by_no(Session, rid)
    assert {k: v.tier for k, v in got.items()} == {"A": "inverted", "B": "top1", "C": "near", "D": "other", "E": "top1"}
    # A：SteamDT 说 210（我 650 已倒挂），实时核对改成 205；E：直接第 1 又没有 SteamDT 价 → 实时查到没人在卖
    assert (got["A"].sell_min, got["A"].sell_src, got["A"].sell_depth) == (D("205"), "youpin", 1)
    assert (got["E"].sell_min, got["E"].sell_depth, got["E"].sell_src) == (None, 0, "youpin")
    assert (got["E"].top_empty, got["E"].top1_kind, got["E"].account_id) == (True, "alone", 2)
    assert (got["B"].sell_src, got["B"].top1_kind) == ("steamdt", "ahead")
    assert anon.calls == [1, 5]
    assert got["A"].paused_at == datetime(2026, 7, 26, 10, 49, 16, tzinfo=timezone.utc)
    with Session() as s:
        run = s.get(PausedCheckRun, rid)
        assert (run.status, run.steamdt, run.incomplete, run.skipped, run.finished_at) == ("done", True, False, None, NOON)


def test_paging_stops_on_short_page_and_flags_max_pages(Session, settings):
    full = [raw(f"P{i}", i, 1, "¥2") for i in range(1, 41)]
    a1 = FakeAccountClient([full, [raw("Q", 99, 1, "¥2")]])
    m = make_monitor(Session, settings, {1: a1}, FakeAnon({}))
    rid = start(Session, m)
    assert run_check(m, rid).rows == 41 and a1.calls == [1, 2]
    settings.paused_check.max_pages = 1
    a1.calls.clear()
    rid = start(Session, m)
    run_check(m, rid)
    with Session() as s:
        assert s.get(PausedCheckRun, rid).incomplete is True
    assert a1.calls == [1]


def test_login_lost_skips_only_that_account(Session, settings):
    a1 = FakeAccountClient([[raw("A", 1, 10, "¥20")]])
    a2 = FakeAccountClient([], error=LoginRequired("84101", "x"))
    m = make_monitor(Session, settings, {1: a1, 2: a2}, FakeAnon({}))
    rid = start(Session, m)
    res = run_check(m, rid)
    assert res.status == "done" and res.skipped == [2] and res.rows == 1
    with Session() as s:
        assert s.get(PausedCheckRun, rid).skipped == "2"


def test_all_accounts_failing_fails_the_run(Session, settings):
    a1 = FakeAccountClient([], error=LoginRequired("84101", "x"))
    m = make_monitor(Session, settings, {1: a1}, FakeAnon({}))
    rid = start(Session, m)
    res = run_check(m, rid)
    assert res.status == "failed" and "都没查成" in res.error


def test_risk_control_pauses_every_account_and_fails(Session, settings):
    a1 = FakeAccountClient([], error=RiskControl("1001", "x"))
    a2 = FakeAccountClient([[raw("E", 5, 1, "¥2")]])
    m = make_monitor(Session, settings, {1: a1, 2: a2}, FakeAnon({}))
    rid = start(Session, m)
    res = run_check(m, rid)
    assert res.status == "failed" and "风控" in res.error and a2.calls == []
    with Session() as s:
        assert st.is_paused(s, "youpin_account:1", NOON) and st.is_paused(s, "youpin_account:2", NOON)
        assert s.get(PausedCheckRun, rid).status == "failed"
        assert s.scalars(select(PausedCheckItem)).all() == []


def test_steamdt_failure_still_gives_a_result(Session, settings):
    a1 = FakeAccountClient([[raw("A", 1, 650, "¥190"), raw("B", 2, 10, "¥30")]])
    anon = FakeAnon({1: ["205"]})
    m = make_monitor(Session, settings, {1: a1}, anon, FakeSteamDT({}, error=SteamDTError("4005", "quota")))
    rid = start(Session, m)
    assert run_check(m, rid).status == "done"
    got = items_by_no(Session, rid)
    # 没有 SteamDT 价：直接第 1 的 A 照样实时核对出倒挂；B 没在售价，不填 0
    assert got["A"].tier == "inverted" and got["B"].sell_min is None and anon.calls == [1]
    with Session() as s:
        assert s.get(PausedCheckRun, rid).steamdt is False


def test_verify_cap_and_anon_pause(Session, settings):
    rows = [raw(f"P{i}", i, 100, "¥50") for i in range(1, 6)]   # 都是直接第 1、没有在售价
    anon = FakeAnon({i: ["90"] for i in range(1, 6)})
    m = make_monitor(Session, settings, {1: FakeAccountClient([rows])}, anon)
    settings.paused_check.verify_max = 2
    run_check(m, start(Session, m))
    assert len(anon.calls) == 2
    anon.calls.clear()
    with Session() as s:
        st.mark_error(s, ANON, "429", NOON + timedelta(minutes=5))
    rid = start(Session, m)
    run_check(m, rid)
    assert anon.calls == [] and items_by_no(Session, rid)["P1"].sell_min is None


def test_keeps_only_last_five_runs(Session, settings):
    m = make_monitor(Session, settings, {1: FakeAccountClient([[raw("A", 1, 1, "¥2")]])}, FakeAnon({}))
    ids = [start(Session, m) for _ in range(1)]
    run_check(m, ids[0])
    for _ in range(6):
        run_check(m, start(Session, m))
    with Session() as s:
        runs = s.scalars(select(PausedCheckRun.id)).all()
        assert len(runs) == 5
        assert {i.run_id for i in s.scalars(select(PausedCheckItem))} == set(runs)


def test_start_refusals(Session, settings):
    m = make_monitor(Session, settings, {1: FakeAccountClient([])}, FakeAnon({}))
    start(Session, m)
    with pytest.raises(PausedRefused) as e:
        start(Session, m)
    assert e.value.status == 409
    with Session() as s:
        for r in s.scalars(select(PausedCheckRun)):
            r.status = "done"
        s.add(PickRun(created_at=NOON, filters_json="{}", status="running", phase="pool"))
        s.commit()
    with pytest.raises(PausedRefused) as e:
        start(Session, m)
    assert e.value.status == 409 and "选品" in e.value.message
    with Session() as s:
        s.scalar(select(PickRun)).status = "done"
        s.commit()
        with pytest.raises(PausedRefused) as e:
            start_run(s, m, "cli", datetime(2026, 11, 24, 15, 55, tzinfo=timezone.utc))  # 23:55
        assert e.value.status == 423
    nobody = make_monitor(Session, settings, {1: FakeAccountClient([], token=None)}, FakeAnon({}))
    with pytest.raises(PausedRefused) as e:
        start(Session, nobody)
    assert "没有登录" in e.value.message


def test_mark_interrupted(Session, settings):
    m = make_monitor(Session, settings, {1: FakeAccountClient([])}, FakeAnon({}))
    rid = start(Session, m)
    with Session() as s:
        assert mark_interrupted(s, NOON) == 1
        assert s.get(PausedCheckRun, rid).status == "interrupted"


def test_monitor_round_holds_the_youpin_lock_and_check_job_runs(Session, settings):
    """每轮监控整轮持锁（暂停单 spec §3.4）；job_paused_check 用同一把锁跑完检查。"""
    from types import SimpleNamespace

    from skinpulse.scheduler import job_monitor_round, job_paused_check, schedule_paused_check

    lock = threading.Lock()
    seen = []

    class M:
        def run_round(self):
            seen.append(lock.locked())
            return SimpleNamespace(checked=0, errors=0)

    job_monitor_round(SimpleNamespace(monitor=M(), youpin_lock=lock))
    assert seen == [True] and not lock.locked()

    a1 = FakeAccountClient([[raw("A", 1, 10, "¥20")]], lock=lock)
    m = make_monitor(Session, settings, {1: a1}, FakeAnon({}), FakeSteamDT({}, lock=lock))
    rid = start(Session, m)
    job_paused_check(SimpleNamespace(monitor=m, youpin_lock=lock), rid)
    with Session() as s:
        assert s.get(PausedCheckRun, rid).status == "done"

    added = []
    sched = SimpleNamespace(add_job=lambda *a, **k: added.append((a, k)))
    schedule_paused_check(sched, "rt", 7)
    (args, kw), = added
    assert args[1] == "date" and kw["args"] == ["rt", 7] and kw["id"] == "paused_check_7"
    assert kw["misfire_grace_time"] is None


def _cli_rt(Session, settings, m):
    from types import SimpleNamespace
    return SimpleNamespace(settings=settings, session_factory=Session, monitor=m, accounts=m.accounts, client=None)


def test_cli_check_paused_json_matches_api_and_text(Session, settings, monkeypatch):
    import json

    from typer.testing import CliRunner

    import skinpulse.cli as cli
    import skinpulse.db.session as dbs
    from skinpulse.web.paused import paused_payload

    monkeypatch.setattr(dbs, "utcnow", lambda: NOON)
    a1 = FakeAccountClient([[raw("A", 1, 650, "¥190", name="专业手套（★） | 狩鹿 (久经沙场)"), raw("B", 2, 1, "¥9")]])
    m = make_monitor(Session, settings, {1: a1}, FakeAnon({1: ["205"]}))
    rt = _cli_rt(Session, settings, m)
    monkeypatch.setattr(cli, "_rt", lambda *a, **k: rt)
    r = CliRunner().invoke(cli.app, ["check-paused", "--json", "--no-steamdt"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout) == json.loads(json.dumps(paused_payload(rt, NOON), ensure_ascii=False))
    r = CliRunner().invoke(cli.app, ["check-paused"])
    assert r.exit_code == 0, r.output
    assert "倒挂（我的价 ≥ 在售最低）：1 张" in r.stdout and "★ 专业手套 | 狩鹿（久经沙场）" in r.stdout
    assert "恢复后 第 1 · 高出 ¥460" in r.stdout and "在售 205（实时）" in r.stdout and "其余 1 张" in r.stdout


def test_cli_check_paused_unknown_account_and_refusal(Session, settings, monkeypatch):
    from typer.testing import CliRunner

    import skinpulse.cli as cli
    import skinpulse.db.session as dbs

    monkeypatch.setattr(dbs, "utcnow", lambda: NOON)
    m = make_monitor(Session, settings, {1: FakeAccountClient([], token=None)}, FakeAnon({}))
    monkeypatch.setattr(cli, "_rt", lambda *a, **k: _cli_rt(Session, settings, m))
    assert CliRunner().invoke(cli.app, ["check-paused", "--account", "3"]).exit_code == 1
    r = CliRunner().invoke(cli.app, ["check-paused"])
    assert r.exit_code == 1 and "没有登录" in r.stderr


def test_rows_without_price_mark_incomplete_or_fail(Session, settings):
    """价格是暂停单不可能缺的字段：只缺几张标不完整，整页都缺就整次失败（别出一份「没有暂停单」的假结果）。"""
    some = [raw("A", 1, 10, "¥20"), {**raw("B", 2, 1, "¥2"), "unitPrice": None}]
    m = make_monitor(Session, settings, {1: FakeAccountClient([some])}, FakeAnon({}))
    rid = start(Session, m)
    assert run_check(m, rid).rows == 1
    with Session() as s:
        assert s.get(PausedCheckRun, rid).incomplete is True
    renamed = [{k: v for k, v in raw(f"P{i}", i, 1, "¥2").items() if k != "unitPrice"} for i in range(3)]
    m = make_monitor(Session, settings, {1: FakeAccountClient([renamed])}, FakeAnon({}))
    res = run_check(m, start(Session, m))
    assert res.status == "failed" and "价格读不出来" in res.error


def test_ctrl_c_marks_the_run_interrupted(Session, settings):
    a1 = FakeAccountClient([], error=KeyboardInterrupt())
    m = make_monitor(Session, settings, {1: a1}, FakeAnon({}))
    rid = start(Session, m)
    with pytest.raises(KeyboardInterrupt):
        run_check(m, rid)
    with Session() as s:
        assert s.get(PausedCheckRun, rid).status == "interrupted"
    start(Session, m)          # 不会被「已经有一次检查在跑」挡住


def test_pick_refuses_while_a_paused_check_runs(Session, settings):
    from types import SimpleNamespace

    from skinpulse.services.pick import PickRefused, PickRunner

    m = make_monitor(Session, settings, {1: FakeAccountClient([])}, FakeAnon({}))
    start(Session, m)
    rt = SimpleNamespace(settings=settings, session_factory=Session, steamdt=object())
    runner = PickRunner(rt, start_thread=False)
    import skinpulse.services.pick as sp
    orig = sp.utcnow
    sp.utcnow = lambda: NOON
    try:
        with pytest.raises(PickRefused) as e:
            runner.start({"groups": ["rifle"]})
    finally:
        sp.utcnow = orig
    assert e.value.status == 409 and "暂停单" in e.value.message


def test_first_price_batch_waits_for_a_batch_another_task_just_sent(Session, settings):
    """评审 2026-11-24：盈亏导入（或价格快照）刚发过一批 /price/batch，暂停单检查的第一批要等够 65 秒再发。"""
    from skinpulse.db.session import utcnow
    from skinpulse.services.market import wait_batch_slot

    with Session() as s:
        wait_batch_slot(s, lambda _s: None, now=utcnow)                 # 别的任务刚用过
    slept = []
    a1 = FakeAccountClient([[raw("A", 1, 650, "¥190")]])
    m = make_monitor(Session, settings, {1: a1}, FakeAnon({1: ["205"]}), FakeSteamDT({"H1": "210"}))
    m.sleep = slept.append
    rid = start(Session, m)
    run_check(m, rid, lock=threading.Lock())
    assert any(60 <= x <= 65 for x in slept), slept
