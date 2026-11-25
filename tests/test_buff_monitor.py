"""BUFF 的一轮监控（BUFF spec §4～§7、§12）：同步、数名次、交易中、成交配对、错误按号隔离、推送。"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from skinpulse.adapters.buff.errors import BuffLoginRequired, BuffRateLimited
from skinpulse.adapters.buff.models import BillPage, BuffBill, BuffQueueRow, Me, QueuePage, parse_buy_orders, parse_queue
from skinpulse.db.models import Account, Item, MyBuyOrder, OrderEvent, RankSnapshot, SystemStatus
from skinpulse.services.buff import BuffAccountRuntime, goods_to_template, sync_buff_orders
from skinpulse.services.buff_monitor import run_buff_round
from skinpulse.services.monitor import Monitor
from tests.test_services_monitor import FakeNtfy, _setup

FX = Path(__file__).parent / "fixtures" / "buff"
ORDERS, ME, _ = parse_buy_orders(json.loads((FX / "wait_supply.json").read_text("utf-8"))["data"])
AK_QUEUE = parse_queue(json.loads((FX / "queue_mixed.json").read_text("utf-8"))["data"]).rows
AK = 1134183
AK_ID = "261123T1000000003"
EARLY = datetime(2026, 1, 1, tzinfo=timezone.utc)


class FakeBuff:
    def __init__(self, orders=ORDERS, queues=None, bills=(), exc=None):
        self.orders = list(orders)
        self.queues = queues if queues is not None else default_queues(self.orders)
        self.bills = list(bills)
        self.exc = exc
        self.requests = 0
        self.queue_calls: list[tuple[int, int]] = []

    def my_buy_orders(self):
        self.requests += 1
        if self.exc:
            raise self.exc
        return list(self.orders), ME

    def buy_queue(self, goods_id, page=1):
        self.requests += 1
        self.queue_calls.append((goods_id, page))
        rows = self.queues.get(goods_id, [])
        return QueuePage(rows=rows, page_num=page, page_size=100, total_page=1)

    def buy_history(self, page=1, size=20):
        self.requests += 1
        return BillPage(bills=list(self.bills), page_num=page, page_size=size)


def default_queues(orders):
    """AK 用实测队列；别的饰品：一张比我早挂、价等于最高普通单价的单在前，我排第 2。"""
    q = {AK: AK_QUEUE}
    for o in orders:
        if o.goods_id != AK:
            q[o.goods_id] = [BuffQueueRow("X" + o.order_id, o.buy_max_price, 1, (), EARLY),
                             BuffQueueRow(o.order_id, o.price, 1, (), o.created_at)]
    return q


def _seed_buff_account(Session, aid=2, label="BUFF"):
    with Session() as s:
        s.add(Account(id=aid, platform="buff", enabled=True, label=label, token_valid=True))
        s.commit()


def _monitor(Session, settings, client, ntfy=None, aid=2):
    settings.alerts.enter_rank, settings.alerts.exit_rank, settings.alerts.confirm_rounds = 1, 4, 1
    acc = BuffAccountRuntime(aid, client, label="BUFF")
    return Monitor(Session, None, ntfy or FakeNtfy(), settings, sleep=lambda s: None, accounts={},
                   buff_accounts={aid: acc}, local_hour=lambda: 12)


def test_round_syncs_ranks_and_writes_snapshots(tmp_path, settings):
    Session = _setup(tmp_path)
    _seed_buff_account(Session)
    client = FakeBuff()
    nt = FakeNtfy()
    rep = run_buff_round(_monitor(Session, settings, client, nt))
    assert rep.synced == 8 and rep.checked == 8 and rep.errors == 0 and rep.alerts_sent == 0
    assert rep.queues == 8                                  # 8 张单都在预算（10）里
    with Session() as s:
        ak = s.get(MyBuyOrder, "buff:" + AK_ID)
        assert ak.account_id == 2 and ak.reported_rank == 2 and ak.top_bid == Decimal("102") and ak.cond_ahead == 7
        assert ak.queue_key == "buff:1134183:plain" and ak.template_id == -AK
        assert len(list(s.scalars(select(RankSnapshot)))) == 8
        assert s.get(SystemStatus, "buff_account:2").last_ok_at is not None
    assert not nt.sent


def test_rank_one_pushes_with_buff_label_link_and_conditional_note(tmp_path, settings):
    Session = _setup(tmp_path)
    _seed_buff_account(Session)
    queues = default_queues(ORDERS)
    queues[AK] = [r for r in AK_QUEUE if r.price != Decimal("102")]
    ak = next(o for o in ORDERS if o.goods_id == AK)
    orders = [replace(ak, buy_max_price=Decimal("101"))]
    nt = FakeNtfy()
    rep = run_buff_round(_monitor(Session, settings, FakeBuff(orders, queues), nt))
    assert rep.alerts_sent == 1
    msg = nt.sent[0]
    assert msg.title.startswith("求购第1名：[BUFF] AK-47")
    assert msg.click == "https://buff.163.com/goods/1134183"
    assert "前面还有 7 张条件单" in msg.body


def test_buff_sync_never_touches_youpin_orders(tmp_path, settings):
    Session = _setup(tmp_path)
    _seed_buff_account(Session)
    with Session() as s:
        s.add(MyBuyOrder(purchase_no="Y1", account_id=1, template_id=1, price=Decimal("5"), queue_key="1|ANY|-1|ANY|ANY",
                         synced_at=EARLY, active=True))
        s.commit()
    run_buff_round(_monitor(Session, settings, FakeBuff()))
    with Session() as s:
        assert s.get(MyBuyOrder, "Y1").active is True
        assert not list(s.scalars(select(OrderEvent).where(OrderEvent.purchase_no == "Y1")))


def test_login_required_marks_account_and_notifies_once(tmp_path, settings):
    Session = _setup(tmp_path)
    _seed_buff_account(Session)
    nt = FakeNtfy()
    m = _monitor(Session, settings, FakeBuff(exc=BuffLoginRequired("Login Required", "请先登录")), nt)
    rep = run_buff_round(m)
    run_buff_round(m)
    assert rep.errors == 1
    assert [x.title for x in nt.sent] == ["BUFF 登录已失效：BUFF"]
    with Session() as s:
        assert s.get(Account, 2).token_valid is False
        assert s.get(SystemStatus, "buff_account:2").last_error_code == "Login Required"


def test_rate_limit_pauses_and_skips_next_round(tmp_path, settings):
    Session = _setup(tmp_path)
    _seed_buff_account(Session)
    nt = FakeNtfy()
    client = FakeBuff(exc=BuffRateLimited("http429", ""))
    m = _monitor(Session, settings, client, nt)
    run_buff_round(m)
    rep = run_buff_round(m)
    assert rep.paused and client.requests == 1               # 第二轮一个请求都没发
    assert [x.title for x in nt.sent] == ["BUFF 访问受限，已暂停 2 小时"]
    with Session() as s:
        row = s.get(SystemStatus, "buff_account:2")
        assert row.paused_until is not None


def _bill(bid, *, state="SUCCESS", price="101", goods=AK, created=None, done=None, type_=2):
    created = created or datetime(2026, 11, 24, 10, tzinfo=timezone.utc)
    return BuffBill(bid, goods, type_, state, "", Decimal(price), Decimal(0), Decimal(0), created, done, "a1")


def test_gone_order_matched_to_bill_is_pushed_as_fill(tmp_path, settings):
    Session = _setup(tmp_path)
    _seed_buff_account(Session)
    nt = FakeNtfy()
    ak = next(o for o in ORDERS if o.goods_id == AK)
    client = FakeBuff([ak])
    m = _monitor(Session, settings, client, nt)
    run_buff_round(m)
    # 成交时间要落在 24 小时推送窗口里：写死日期的话过一天就挂（2026-11-25 挂过，同 10-05 那两条）
    done_at = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=10)
    client.orders = []
    client.bills = [_bill("B1", price="100"), _bill("B2", type_=6), _bill("B3", done=done_at)]
    run_buff_round(m)
    with Session() as s:
        ev = s.scalar(select(OrderEvent).where(OrderEvent.kind == "gone"))
        assert ev.account_id == 2 and ev.outcome == "done" and ev.outcome_ref == "B3" and ev.outcome_at == done_at
        assert ev.notice == "sent"
    fill = [x for x in nt.sent if "成交" in x.title]
    assert len(fill) == 1 and "[BUFF]" in fill[0].title and fill[0].click == "https://buff.163.com/goods/1134183"


def test_two_same_price_orders_one_bill_goes_to_the_earlier_order(tmp_path, settings):
    Session = _setup(tmp_path)
    _seed_buff_account(Session)
    ak = next(o for o in ORDERS if o.goods_id == AK)
    a = replace(ak, order_id="A", created_at=datetime(2026, 11, 1, tzinfo=timezone.utc))
    b = replace(ak, order_id="B", created_at=datetime(2026, 11, 2, tzinfo=timezone.utc))
    clock = [datetime(2026, 11, 24, 12, tzinfo=timezone.utc)]
    client = FakeBuff([a, b])
    m = _monitor(Session, settings, client)
    m.now = lambda: clock[0]
    run_buff_round(m)
    client.orders, client.bills = [], [_bill("B1")]
    run_buff_round(m)
    with Session() as s:
        evs = {e.purchase_no: e for e in s.scalars(select(OrderEvent).where(OrderEvent.kind == "gone"))}
        assert evs["buff:A"].outcome == "done" and evs["buff:B"].outcome is None
    clock[0] += timedelta(minutes=30)
    run_buff_round(m)
    with Session() as s:
        assert s.scalar(select(OrderEvent.outcome).where(OrderEvent.purchase_no == "buff:B",
                                                         OrderEvent.kind == "gone")) == "missing"


def test_in_trade_bill_marks_order_and_pushes(tmp_path, settings):
    Session = _setup(tmp_path)
    _seed_buff_account(Session)
    nt = FakeNtfy()
    ak = next(o for o in ORDERS if o.goods_id == AK)
    client = FakeBuff([ak], bills=[_bill("T1", state="WAIT_DELIVER")])
    run_buff_round(_monitor(Session, settings, client, nt))
    with Session() as s:
        o = s.get(MyBuyOrder, "buff:" + AK_ID)
        assert o.in_trade_since is not None and o.in_trade_notice == "sent"
    assert any(x.title.startswith("交易中") and "[BUFF]" in x.title for x in nt.sent)


def test_goods_mapping_via_hash_name_records_buff_goods_id(tmp_path):
    Session = _setup(tmp_path)
    with Session() as s:
        s.add(Item(hash_name="AK-47 | Crane Flight (Minimal Wear)", youpin_template_id=121646))
        s.commit()
        assert goods_to_template(s, AK, "AK-47 | Crane Flight (Minimal Wear)") == 121646
        s.commit()
        assert s.scalar(select(Item.buff_goods_id)) == AK
        assert goods_to_template(s, AK, "") == 121646            # 第二次直接按 goods_id
        assert goods_to_template(s, 5, "nope") == -5


def test_youpin_round_does_not_push_buff_fills(tmp_path, settings):
    Session = _setup(tmp_path)
    _seed_buff_account(Session)
    with Session() as s:
        s.add(OrderEvent(account_id=2, purchase_no="buff:X", template_id=-1, kind="gone", qty_before=1,
                         price=Decimal(1), at=datetime.now(timezone.utc), outcome="done", notice="pending"))
        s.commit()
    nt = FakeNtfy()
    m = _monitor(Session, settings, FakeBuff(), nt)
    from skinpulse.services.monitor import RoundReport
    with Session() as s:
        assert m._notify_fills(s, RoundReport()) == 0
        assert m._notify_fills(s, RoundReport(), buff=True) == 1


def test_disabled_during_sync_discards(tmp_path):
    Session = _setup(tmp_path)
    _seed_buff_account(Session)
    with Session() as s:
        s.get(Account, 2).enabled = False
        s.commit()
        assert sync_buff_orders(s, BuffAccountRuntime(2, FakeBuff())) == []
        assert not list(s.scalars(select(MyBuyOrder)))


def test_no_buff_accounts_means_no_requests(tmp_path, settings):
    Session = _setup(tmp_path)
    m = Monitor(Session, None, FakeNtfy(), settings, accounts={}, buff_accounts={})
    rep = run_buff_round(m)
    assert rep.accounts == 0


def test_fixture_me():
    assert ME == Me("U1000000001", "用户1")


def test_buff_job_only_with_buff_accounts(settings):
    from types import SimpleNamespace

    from apscheduler.schedulers.background import BackgroundScheduler

    from skinpulse.scheduler import sync_buff_job
    sched = BackgroundScheduler(timezone="UTC")
    rt = SimpleNamespace(settings=settings, monitor=SimpleNamespace(buff_accounts={}))
    sync_buff_job(sched, rt)
    assert sched.get_job("buff_round") is None
    rt.monitor.buff_accounts = {2: object()}
    sync_buff_job(sched, rt)
    assert sched.get_job("buff_round") is not None
    rt.monitor.buff_accounts = {}
    sync_buff_job(sched, rt)
    assert sched.get_job("buff_round") is None


def test_youpin_only_views_exclude_buff_orders(tmp_path):
    from skinpulse.services.brief import _active_orders
    from skinpulse.services.buff import youpin_orders
    Session = _setup(tmp_path)
    _seed_buff_account(Session)
    with Session() as s:
        for no, aid, tid in (("Y1", 1, 11), ("buff:B1", 2, 11), ("buff:B2", 2, -5)):
            s.add(MyBuyOrder(purchase_no=no, account_id=aid, template_id=tid, price=Decimal(1), queue_key="k",
                             synced_at=EARLY, active=True))
        s.commit()
        assert [o.purchase_no for o in _active_orders(s)] == ["Y1"]
        assert set(s.scalars(select(MyBuyOrder.purchase_no).where(youpin_orders()))) == {"Y1"}


def test_gone_with_in_progress_bill_waits_instead_of_calling_it_cancelled(tmp_path, settings):
    """评审 2026-11-24：单子在供货时就消失了，订单还没走完——不能过 20 分钟就判撤单。"""
    Session = _setup(tmp_path)
    _seed_buff_account(Session)
    nt = FakeNtfy()
    ak = next(o for o in ORDERS if o.goods_id == AK)
    clock = [datetime(2026, 11, 24, 12, tzinfo=timezone.utc)]
    client = FakeBuff([ak])
    m = _monitor(Session, settings, client, nt)
    m.now = lambda: clock[0]
    run_buff_round(m)
    client.orders, client.bills = [], [_bill("P1", state="WAIT_DELIVER")]
    run_buff_round(m)
    clock[0] += timedelta(hours=2)
    run_buff_round(m)
    with Session() as s:
        ev = s.scalar(select(OrderEvent).where(OrderEvent.kind == "gone"))
        assert ev.outcome is None and ev.in_trade_at is not None
    client.bills = [_bill("P1", done=clock[0])]
    run_buff_round(m)
    with Session() as s:
        ev = s.scalar(select(OrderEvent).where(OrderEvent.kind == "gone"))
        assert ev.outcome == "done" and ev.outcome_ref == "P1"


def test_unqueried_orders_lose_their_old_rank(tmp_path, settings):
    Session = _setup(tmp_path)
    _seed_buff_account(Session)
    client = FakeBuff()
    m = _monitor(Session, settings, client)
    run_buff_round(m)
    settings.buff.queue_per_round = 0
    run_buff_round(m)
    with Session() as s:
        assert all(o.reported_rank is None for o in s.scalars(select(MyBuyOrder)))


def test_start_now_schedules_a_single_run(settings):
    """加号、换 cookie 时只排一次（2026-11-24 上线时两次撞车，多了一条 max instances 警告）。"""
    from types import SimpleNamespace

    from apscheduler.schedulers.background import BackgroundScheduler

    from skinpulse.db.session import utcnow
    from skinpulse.scheduler import sync_buff_job
    sched = BackgroundScheduler(timezone="UTC")
    rt = SimpleNamespace(settings=settings, monitor=SimpleNamespace(buff_accounts={2: object()}))
    assert sync_buff_job(sched, rt, start_now=True) is True
    job = sched.get_job("buff_round")
    assert abs((job.next_run_time - utcnow()).total_seconds()) < 5
    trigger = job.trigger
    assert sync_buff_job(sched, rt, start_now=True) is True      # 已有任务：只改下一次时间，不重排
    job = sched.get_job("buff_round")
    assert job.trigger is trigger and abs((job.next_run_time - utcnow()).total_seconds()) < 5
    assert len(sched.get_jobs()) == 1
