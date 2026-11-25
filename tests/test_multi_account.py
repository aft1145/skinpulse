"""多账号（多账号 spec §5、§6、§11）：按号同步、错误按号隔离、推送带简称。"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from skinpulse.adapters.youpin.errors import LoginRequired, RateLimited, RiskControl
from skinpulse.db.models import Account, MyBuyOrder, OrderEvent, RankSnapshot, SystemStatus
from skinpulse.db.session import utcnow
from skinpulse.services import account as account_mod
from skinpulse.services.account import AccountRuntime, LoginLostNotice, sync_my_orders
from skinpulse.services.fills import resolve_gone
from skinpulse.services.monitor import Monitor
from tests.test_services_monitor import FakeNtfy, FakeYoupin, _my_order, _setup


def _accounts(*pairs):
    return {i: AccountRuntime(i, client, label=label) for i, client, label in pairs}


def _seed_accounts(Session, *ids):
    with Session() as s:
        for i in ids:
            s.add(Account(id=i, user_id=1000 + i, nickname=f"n{i}", token_valid=True))
        s.commit()


def _monitor(Session, settings, accounts, ntfy=None):
    first = accounts[min(accounts)].client
    return Monitor(Session, first, ntfy or FakeNtfy(), settings, sleep=lambda s: None, accounts=accounts)


# ---- 同步：消失只在本号判 ----

def test_syncing_one_account_never_deactivates_the_other_accounts_orders(tmp_path):
    Session = _setup(tmp_path)
    a1 = AccountRuntime(1, FakeYoupin(my_orders=[_my_order("A1")]))
    a2 = AccountRuntime(2, FakeYoupin(my_orders=[_my_order("B1"), _my_order("B2")]))
    with Session() as s:
        sync_my_orders(s, a1)
        sync_my_orders(s, a2)
        sync_my_orders(s, a1)              # 号 1 再同步一次：号 2 的单一张都不能消失
        rows = {o.purchase_no: o for o in s.scalars(select(MyBuyOrder))}
        assert {k: (o.active, o.account_id) for k, o in rows.items()} == {
            "A1": (True, 1), "B1": (True, 2), "B2": (True, 2)}
        assert not list(s.scalars(select(OrderEvent).where(OrderEvent.kind == "gone")))
        assert {e.purchase_no: e.account_id for e in s.scalars(select(OrderEvent))} == {"A1": 1, "B1": 2, "B2": 2}
        a2.client.my_orders = [_my_order("B1")]
        sync_my_orders(s, a2)
        gone = list(s.scalars(select(OrderEvent).where(OrderEvent.kind == "gone")))
        assert [(e.purchase_no, e.account_id) for e in gone] == [("B2", 2)]
        assert s.get(MyBuyOrder, "A1").active is True


def test_sync_writes_its_own_account_row(tmp_path):
    Session = _setup(tmp_path)
    with Session() as s:
        sync_my_orders(s, AccountRuntime(2, FakeYoupin(my_orders=[], user_id=42)))
        assert s.get(Account, 2).user_id == 42 and s.get(Account, 1) is None


# ---- 每轮：错误按号隔离 ----

def test_one_account_logged_out_the_other_still_alerts_with_its_label(tmp_path, settings, monkeypatch):
    monkeypatch.setattr(account_mod, "_login_notice", LoginLostNotice())
    settings.alerts.confirm_rounds = 1
    Session = _setup(tmp_path)
    _seed_accounts(Session, 1, 2)
    nt = FakeNtfy()
    accs = _accounts((1, FakeYoupin(sync_exc=LoginRequired("84101", "x")), "主号"),
                     (2, FakeYoupin(my_orders=[_my_order("B1", rank="2")]), "小号"))
    rep = _monitor(Session, settings, accs, nt).run_round()
    assert rep.alerts_sent == 1
    titles = [m.title for m in nt.sent]
    assert "悠悠登录已失效：主号" in titles
    alert = next(m for m in nt.sent if m.title.startswith("求购第"))
    assert "[小号] " in alert.title and "[小号] " in alert.body
    with Session() as s:
        assert s.get(Account, 1).token_valid is False
        assert s.get(SystemStatus, "youpin_account:2").last_ok_at is not None


def test_an_account_that_failed_to_sync_gets_no_snapshot_this_round(tmp_path, settings):
    Session = _setup(tmp_path)
    _seed_accounts(Session, 1, 2)
    y1 = FakeYoupin(my_orders=[_my_order("A1")])
    accs = _accounts((1, y1, "主号"), (2, FakeYoupin(my_orders=[_my_order("B1")]), "小号"))
    m = _monitor(Session, settings, accs)
    m.run_round()
    y1.sync_exc = RateLimited("84900", "slow down")
    rep = m.run_round()
    assert rep.checked == 1
    with Session() as s:
        counts = {}
        for sn in s.scalars(select(RankSnapshot)):
            counts[sn.purchase_no] = counts.get(sn.purchase_no, 0) + 1
        assert counts == {"A1": 1, "B1": 2}
        assert s.get(SystemStatus, "youpin_account:1").paused_until is not None
        assert s.get(SystemStatus, "youpin_account:2").paused_until is None


def test_risk_control_on_one_account_pauses_every_account_and_stops_syncing(tmp_path, settings):
    Session = _setup(tmp_path)
    _seed_accounts(Session, 1, 2)
    y2 = FakeYoupin(my_orders=[_my_order("B1")])
    accs = _accounts((1, FakeYoupin(sync_exc=RiskControl("84104", "x")), "主号"), (2, y2, "小号"))
    rep = _monitor(Session, settings, accs).run_round()
    assert y2.sync_calls == 0 and rep.checked == 0
    with Session() as s:
        assert s.get(SystemStatus, "youpin_account:1").paused_until is not None
        assert s.get(SystemStatus, "youpin_account:2").paused_until is not None


def test_accounts_are_synced_in_turn_with_a_gap(tmp_path, settings):
    Session = _setup(tmp_path)
    _seed_accounts(Session, 1, 2, 3)
    settings.polling.account_gap_s = 7
    slept = []
    accs = _accounts(*[(i, FakeYoupin(my_orders=[_my_order(f"O{i}")]), f"号{i}") for i in (1, 2, 3)])
    Monitor(Session, accs[1].client, FakeNtfy(), settings, sleep=slept.append, accounts=accs).run_round()
    assert slept.count(7) == 2


def test_single_account_never_sleeps_for_the_account_gap(tmp_path, settings):
    Session = _setup(tmp_path)
    settings.polling.account_gap_s = 7
    slept = []
    Monitor(Session, FakeYoupin(), FakeNtfy(), settings, sleep=slept.append).run_round()
    assert 7 not in slept


def test_slow_sync_is_flagged(tmp_path, settings):
    Session = _setup(tmp_path)
    _seed_accounts(Session, 1, 2)
    t = [utcnow()]

    def now():
        t[0] += timedelta(seconds=100)
        return t[0]
    accs = _accounts((1, FakeYoupin(), "主号"), (2, FakeYoupin(my_orders=[]), "小号"))
    m = Monitor(Session, accs[1].client, FakeNtfy(), settings, sleep=lambda s: None, accounts=accs, now=now)
    assert m.run_round().sync_slow is True


# ---- 匿名通道按模板共用、深取用所属号 ----

def test_public_queue_is_fetched_once_per_template_when_several_accounts(tmp_path, settings):
    settings.polling.verify_sample_size = 2
    Session = _setup(tmp_path)
    _seed_accounts(Session, 1, 2)
    y1 = FakeYoupin(my_orders=[_my_order("A1", rank="5")])
    y2 = FakeYoupin(my_orders=[_my_order("B1", rank="6")])
    _monitor(Session, settings, _accounts((1, y1, "主号"), (2, y2, "小号"))).run_round()
    assert y1.verify_calls == 1 and y2.verify_calls == 0      # 匿名通道走第一个号的 client，同模板只取一次


def test_deep_fetch_uses_the_owning_accounts_client(tmp_path, settings):
    settings.polling.verify_sample_size = 1
    settings.polling.visible_limit = 1          # 匿名一页只看 1 行：看不见自己，要深取
    Session = _setup(tmp_path)
    _seed_accounts(Session, 1, 2)
    y1 = FakeYoupin(my_orders=[])
    y2 = FakeYoupin(my_orders=[_my_order("B1", price="1", rank="5")], user_id=555)   # 公开队列里没有它
    _monitor(Session, settings, _accounts((1, y1, "主号"), (2, y2, "小号"))).run_round()
    assert y2.deep_calls == 1 and y1.deep_calls == 0


# ---- 成交去向按号翻 ----

def test_resolve_gone_only_looks_at_its_own_accounts_events(tmp_path, settings):
    Session = _setup(tmp_path)
    now = utcnow()
    with Session() as s:
        s.add(OrderEvent(purchase_no="A1", template_id=1, kind="gone", at=now, account_id=1))
        s.add(OrderEvent(purchase_no="B1", template_id=1, kind="gone", at=now, account_id=2))
        s.commit()
        client = FakeYoupin()
        rep = resolve_gone(s, client, settings, now=now, account_id=2)
        assert rep.pending == 1


# ---- 心跳 ----

def test_heartbeat_checks_every_account_and_names_the_one_that_lost_login(settings, monkeypatch, tmp_path):
    import skinpulse.scheduler as sched_mod

    monkeypatch.setattr(sched_mod.time, "sleep", lambda s: None)
    Session = _setup(tmp_path)
    _seed_accounts(Session, 1, 2)

    class Dead(FakeYoupin):
        def get_user_info(self):
            raise LoginRequired("84101", "x")
    nt = FakeNtfy()
    monkeypatch.setattr(account_mod, "_login_notice", LoginLostNotice())
    accs = _accounts((1, FakeYoupin(), "主号"), (2, Dead(), "小号"))
    rt = type("Rt", (), {"accounts": accs, "settings": settings, "session_factory": Session, "ntfy": nt,
                         "client": accs[1].client})()
    sched_mod.job_heartbeat(rt)
    assert [m.title for m in nt.sent] == ["悠悠登录已失效：小号"]


# ---- 运行时：从库里装配启用的号 ----

class _Secrets:
    def __init__(self, data):
        self.data = data

    def get(self, k):
        return self.data.get(k)


def test_account_runtimes_load_enabled_accounts_with_their_own_tokens_and_device_ids(tmp_path, settings):
    from skinpulse.runtime import _account_runtimes
    Session = _setup(tmp_path)
    with Session() as s:
        s.add(Account(id=1, user_id=1, nickname="昵称一"))
        s.add(Account(id=2, user_id=2, nickname="昵称二", label="小号"))
        s.add(Account(id=3, user_id=3, enabled=False))
        s.commit()
    secrets = _Secrets({"youpin_token": "t1", "youpin_token:2": "t2", "youpin_token:3": "t3"})
    accs = _account_runtimes(settings, secrets, Session)
    assert sorted(accs) == [1, 2]
    assert (accs[1].label, accs[1].client.token) == ("昵称一", "t1")
    assert (accs[2].label, accs[2].client.token) == ("小号", "t2")
    with Session() as s:
        dev = {a.id: a.device_id for a in s.scalars(select(Account))}
    assert dev[1] and dev[2] and dev[1] != dev[2] and dev[3] is None
    assert accs[1].client.device_id == dev[1]
    # 再装配一次：在跑的号沿用原来的对象和设备身份
    again = _account_runtimes(settings, secrets, Session, accs)
    assert again[1] is accs[1] and again[2].client.device_id == dev[2]


def test_account_runtimes_on_a_fresh_install_is_account_one(tmp_path, settings):
    from skinpulse.runtime import _account_runtimes
    Session = _setup(tmp_path)
    accs = _account_runtimes(settings, _Secrets({}), Session)
    assert list(accs) == [1] and accs[1].client.token is None


# ---- 简报 ----

def test_brief_names_carry_the_account_label_only_with_two_enabled_accounts(tmp_path):
    from skinpulse.services.brief import _display_name, account_labels
    Session = _setup(tmp_path)
    with Session() as s:
        s.add(Account(id=1, nickname="昵称一"))
        s.commit()
        assert account_labels(s) == {}
        s.add(Account(id=2, label="小号"))
        s.commit()
        labels = account_labels(s)
        assert labels == {1: "昵称一", 2: "小号"}
        o = MyBuyOrder(purchase_no="X", template_id=1, commodity_name="AWP | 树蝰 (崭新出厂)", queue_key="1|ANY|-1|ANY|ANY",
                       account_id=2)
        assert _display_name(o, labels) == "[小号] AWP | 树蝰 (崭新出厂)"
        assert _display_name(o) == "AWP | 树蝰 (崭新出厂)"


def test_trade_and_market_messages_prefix_the_label():
    from datetime import datetime, timezone
    from decimal import Decimal

    from skinpulse.config import NtfySettings
    from skinpulse.core.messages import MarketView, TradeView, build_fill_message, build_market_message
    now = datetime(2026, 11, 24, tzinfo=timezone.utc)
    tv = TradeView("P1", "AWP | 树蝰 (崭新出厂)", 1, "不限", Decimal("150"), 1, now, now, account_label="小号")
    assert build_fill_message([tv], NtfySettings(), tz="Asia/Shanghai").title == "求购成交：[小号] AWP | 树蝰 (崭新)"
    mv = MarketView("P1", "M4A4 | 涡轮 (略有磨损)", 1, Decimal("13"), 2, Decimal("16"), Decimal("17"), 3,
                    frozenset({"near"}), account_label="小号")
    many = build_market_message([mv, mv], NtfySettings(), checked_at=now, tz="Asia/Shanghai")
    assert many.body.startswith("[小号] M4A4 | 涡轮 (略磨)")


def test_a_sync_that_finishes_after_the_account_was_disabled_writes_nothing(tmp_path):
    """评审 2026-11-24：看板停用和正在跑的同步撞车时，同步不能把刚停用的单改回活跃。"""
    Session = _setup(tmp_path)

    class DisablesMidway(FakeYoupin):
        def search_my_purchase_orders(self, status=20):
            with Session() as other:                     # 看板线程在同步进行中停用了这个号
                other.get(Account, 2).enabled = False
                other.commit()
            return super().search_my_purchase_orders(status)
    with Session() as s:
        s.add(Account(id=2, user_id=2, enabled=True))
        s.commit()
        assert sync_my_orders(s, AccountRuntime(2, DisablesMidway(my_orders=[_my_order("B1")]))) == 0
    with Session() as s:
        assert s.get(MyBuyOrder, "B1") is None


def test_meta_token_follows_the_only_enabled_account_even_if_it_is_not_account_one(tmp_path, settings):
    from tests.test_web_accounts import _rt as web_rt
    from skinpulse.web.overview import meta_payload
    rt = web_rt(tmp_path, settings, accounts=((1, "主号"), (2, "小号")))
    with rt.session_factory() as s:
        s.get(Account, 1).enabled = False
        s.get(Account, 1).token_valid = False
        s.commit()
    m = meta_payload(rt)
    assert m["token"]["valid"] is True and "token_invalid" not in m["service"]["issues"]
