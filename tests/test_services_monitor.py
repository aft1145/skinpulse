import logging
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import freezegun
import pytest
from sqlalchemy import select

from skinpulse.adapters.youpin.errors import (
    LoginRequired, MachineRoomBlocked, RateLimited, RiskControl, UkExpired, YoupinError,
)
from skinpulse.adapters.youpin.models import MyPurchaseOrder, PublicPurchaseOrder, UserInfo
from skinpulse.core.alerts import AlertEvent
from skinpulse.core.messages import OrderView
from skinpulse.db.models import (
    Account, AlertStateRow, MyBuyOrder, NotificationLog, RankCheck, RankSnapshot, SystemStatus,
)
from skinpulse.db.session import make_engine, make_session_factory, upgrade_db, utcnow
from skinpulse.services.monitor import ACCOUNT, ANON, ROUND, Monitor, RoundReport, recover_pending
from tests.conftest import load_fixture


def _public():
    rows = load_fixture("youpin/template_purchase_orders_ak_redline.json")["data"]["purchaseOrderResponseList"]
    return [PublicPurchaseOrder.from_api(r) for r in rows]


def _my_order(purchase_no, template_id=1414, price="358", abrade="0.15-0.18", rank="2", max_price="¥360"):
    """A MyPurchaseOrder as search_my_purchase_orders would return it: `.raw` carries
    YouPin's own rank/maxPurchasePrice, the fields sync_my_orders stores as
    reported_rank/top_bid (see services/account.py) and pass A now alerts from
    directly. Against the AK redline fixture (_public above), price=358 in the
    "0.15-0.18" sub-queue computes to rank 2 (359, 358, 357) - matching rank="2" here,
    so the default agrees with pass B unless a test overrides one side."""
    return MyPurchaseOrder(purchase_no, template_id, "h", "AK-47 | 红线 (久经沙场)", Decimal(price), 1, 1,
                           abrade, -1, None, None, {"rank": rank, "maxPurchasePrice": max_price})


class FakeYoupin:
    """Serves both channels run_round now uses: the authenticated account channel
    (pass A - get_user_info/search_my_purchase_orders, driving sync_my_orders) and the
    anonymous public-queue channel (pass B - get_template_purchase_orders). Each
    channel's exception and call count are tracked independently, so a test can fail
    one without the other."""

    def __init__(self, my_orders=None, public=None, sync_exc=None, verify_exc=None, user_id=99999999,
                 on_sale=None, sell_exc=None, deep=None):
        self.my_orders = [_my_order("TEST-MINE-1")] if my_orders is None else my_orders
        self.public = _public() if public is None else public
        self.sync_exc = sync_exc
        self.verify_exc = verify_exc
        self.on_sale = on_sale or {}
        self.sell_exc = sell_exc
        self.sell_calls = 0
        self.deep = deep
        self.deep_calls = 0
        self.user_id = user_id
        self.sync_calls = 0
        self.verify_calls = 0
        self.uk = "u"
        self.token = "tok"
        # 成交推送：查去向用的「我的求购」单页查询（status 30/40），默认两个列表都是空的
        self.fill_pages: dict[int, list[list]] = {}
        self.fill_exc = None
        self.fill_calls: list[tuple[int, int]] = []
        self.s = SimpleNamespace(page_sleep_s=0)

    def sleep(self, s):
        pass

    def get_user_info(self):
        return UserInfo(self.user_id, "me")

    def search_my_purchase_orders_page(self, status, page_index, page_size=40):
        self.fill_calls.append((status, page_index))
        if self.fill_exc:
            raise self.fill_exc
        pages = self.fill_pages.get(status, [])
        return pages[page_index - 1] if page_index <= len(pages) else []

    def search_my_purchase_orders(self, status=20):
        self.sync_calls += 1
        if self.sync_exc:
            raise self.sync_exc
        return self.my_orders

    def get_template_purchase_orders(self, template_id, page_size=20, *, authed=False):
        """authed=True 是"深取"：带 token 请求时 pageSize 才真的生效，能越过匿名那 20 条的
        硬上限（见 YoupinClient.get_template_purchase_orders）。`deep` 没设时深取返回的和
        匿名一样，也就是"深取也没找到"。"""
        if authed:
            self.deep_calls += 1
            return self.public if self.deep is None else self.deep
        self.verify_calls += 1
        if self.verify_exc:
            raise self.verify_exc
        return self.public

    def get_on_sale(self, template_id, page_size=10):
        """市场价刷新（Monitor._refresh_sell_prices）用的在售接口。默认返回空列表，也就是
        "这个模板没人在卖"——一个真实存在且必须能表达的状态，不是错误。按 template_id 分路，
        让一个测试能同时安排"有人卖"和"没人卖"两种模板。"""
        self.sell_calls += 1
        if self.sell_exc:
            raise self.sell_exc
        return self.on_sale.get(template_id, [])


class RoutedVerify(FakeYoupin):
    """Routes pass B's get_template_purchase_orders by template_id, so a test can make
    one specific sampled order fail without affecting another."""

    def __init__(self, routes: dict[int, Exception], **kw):
        super().__init__(**kw)
        self.routes = routes

    def get_template_purchase_orders(self, template_id, page_size=20):
        self.verify_calls += 1
        exc = self.routes.get(template_id)
        if exc is not None:
            raise exc
        return self.public


class FakeNtfy:
    def __init__(self):
        self.sent = []

    def publish(self, msg):
        self.sent.append(msg)


def _setup(tmp_path):
    db = tmp_path / "m.db"
    upgrade_db(db)
    return make_session_factory(make_engine(db))


def _seed_order(Session, purchase_no: str, **overrides) -> None:
    """Directly write a MyBuyOrder row for tests that need pre-existing state a sync
    wouldn't set (last_verified_at, active=False, ...). sync_my_orders upserts by
    purchase_no without touching fields it doesn't own (see services/account.py), so a
    later round's sync leaves these overrides alone unless the fake client's matching
    my_order also changes them."""
    fields = dict(template_id=1414, commodity_name="x", price=Decimal("358"), qty=1, remaining_qty=1,
                  type_id=-1, queue_key="1414|0.15-0.18|-1|ANY|ANY", synced_at=utcnow(), active=True)
    fields.update(overrides)
    with Session() as s:
        s.add(MyBuyOrder(purchase_no=purchase_no, **fields))
        s.commit()


# ---- new round shape: pass A (authoritative) + pass B (sampled verification) ----

def test_round_drives_alerts_from_reported_rank_with_one_authenticated_call(tmp_path, settings):
    settings.alerts.confirm_rounds = 1
    settings.polling.verify_sample_size = 2
    Session = _setup(tmp_path)
    my_orders = [_my_order("A", rank="2"), _my_order("B", rank="15"), _my_order("C", rank="9")]
    yp, nt = FakeYoupin(my_orders=my_orders), FakeNtfy()
    m = Monitor(Session, yp, nt, settings, sleep=lambda s: None)

    r = m.run_round()

    assert yp.sync_calls == 1  # one authenticated call refreshes every order's rank
    assert yp.verify_calls == 2  # pass B sampled only verify_sample_size orders, not 3
    assert r.checked == 3  # every active order got a RankSnapshot from pass A
    assert r.alerts_sent == 1  # only A (rank 2 <= enter_rank) crosses the threshold
    assert len(nt.sent) == 1
    with Session() as s:
        snaps = {sn.purchase_no: sn for sn in s.scalars(select(RankSnapshot))}
        assert len(snaps) == 3  # exactly one RankSnapshot per active order
        assert snaps["A"].rank == 2 and snaps["A"].top1_price == Decimal("360")
        assert snaps["B"].rank == 15
        assert snaps["C"].rank == 9
        assert s.get(AlertStateRow, "A").state == "ALERTING"
        assert s.get(AlertStateRow, "B").state == "NORMAL"
        assert s.get(AlertStateRow, "C").state == "NORMAL"


def test_none_reported_rank_leaves_alert_state_untouched(tmp_path, settings):
    settings.alerts.confirm_rounds = 1
    Session = _setup(tmp_path)
    unparseable = _my_order("A", rank="not-a-number")
    m = Monitor(Session, FakeYoupin(my_orders=[unparseable]), FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()

    assert r.checked == 1
    assert r.alerts_sent == 0
    with Session() as s:
        row = s.get(MyBuyOrder, "A")
        assert row.reported_rank is None
        snap = s.scalar(select(RankSnapshot))
        assert snap.rank is None  # written from the (unknown) reported_rank anyway
        state = s.get(AlertStateRow, "A")
        assert state.state == "NORMAL" and state.streak == 0  # untouched, not e.g. reset or errored


def test_pass_a_uses_same_now_for_every_order_and_updates_last_checked_at(tmp_path, settings):
    settings.polling.verify_sample_size = 0  # isolate pass A
    Session = _setup(tmp_path)
    my_orders = [_my_order("A", rank="2"), _my_order("B", rank="9")]
    m = Monitor(Session, FakeYoupin(my_orders=my_orders), FakeNtfy(), settings, sleep=lambda s: None)

    m.run_round()

    with Session() as s:
        a, b = s.get(MyBuyOrder, "A"), s.get(MyBuyOrder, "B")
        assert a.last_checked_at is not None and b.last_checked_at is not None
        assert a.last_checked_at == b.last_checked_at  # same round timestamp for every order


# ---- gap filter (alerts.max_gap_ratio, see Monitor._is_eligible_for_alert and
# AlertEngine.step's `eligible` parameter) ----

def test_gap_ratio_at_or_above_threshold_gates_alert(tmp_path, settings):
    """Boundary: a ratio exactly equal to max_gap_ratio is gated - the rule is 'at or
    above', not 'strictly above'."""
    settings.alerts.confirm_rounds = 1
    settings.polling.verify_sample_size = 0
    settings.alerts.max_gap_ratio = 3.0
    Session = _setup(tmp_path)
    # price 10, leading bid 30 -> ratio exactly 3.0
    my_orders = [_my_order("A", rank="2", price="10", max_price="¥30")]
    m = Monitor(Session, FakeYoupin(my_orders=my_orders), FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()

    assert r.alerts_sent == 0
    assert r.gap_gated == 1
    with Session() as s:
        state = s.get(AlertStateRow, "A")
        assert state.state == "NORMAL" and state.streak == 0
        assert state.last_rank == 2  # still recorded truthfully despite being gated
        assert s.get(MyBuyOrder, "A").top_bid == Decimal("30")


def test_gap_ratio_just_below_threshold_is_not_gated(tmp_path, settings):
    settings.alerts.confirm_rounds = 1
    settings.polling.verify_sample_size = 0
    settings.alerts.max_gap_ratio = 3.0
    Session = _setup(tmp_path)
    my_orders = [_my_order("A", rank="2", price="10", max_price="¥29.99")]  # ratio 2.999 < 3.0
    m = Monitor(Session, FakeYoupin(my_orders=my_orders), FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()

    assert r.alerts_sent == 1
    assert r.gap_gated == 0


def test_gated_order_alerts_normally_once_gap_narrows(tmp_path, settings):
    """The important property: gating must not strand the order in a way that prevents
    it from ever alerting again - once the leading bid comes back down (or the owner
    raises their own price), the very next round alerts normally."""
    settings.alerts.confirm_rounds = 1
    settings.polling.verify_sample_size = 0
    settings.alerts.max_gap_ratio = 3.0
    Session = _setup(tmp_path)
    yp = FakeYoupin(my_orders=[_my_order("A", rank="2", price="10", max_price="¥30")])  # ratio 3.0: gated
    m = Monitor(Session, yp, FakeNtfy(), settings, sleep=lambda s: None)

    r1 = m.run_round()
    assert r1.alerts_sent == 0 and r1.gap_gated == 1
    with Session() as s:
        assert s.get(AlertStateRow, "A").state == "NORMAL"

    yp.my_orders = [_my_order("A", rank="2", price="10", max_price="¥20")]  # ratio 2.0: eligible again
    r2 = m.run_round()

    assert r2.alerts_sent == 1
    assert r2.gap_gated == 0
    with Session() as s:
        assert s.get(AlertStateRow, "A").state == "ALERTING"


def test_alerting_order_becoming_ineligible_returns_to_normal_and_realerts_later(tmp_path, settings):
    settings.alerts.confirm_rounds = 1
    settings.polling.verify_sample_size = 0
    settings.alerts.max_gap_ratio = 3.0
    Session = _setup(tmp_path)
    yp = FakeYoupin(my_orders=[_my_order("A", rank="1", price="10", max_price="¥15")])  # ratio 1.5: eligible
    m = Monitor(Session, yp, FakeNtfy(), settings, sleep=lambda s: None)

    r1 = m.run_round()
    assert r1.alerts_sent == 1
    with Session() as s:
        assert s.get(AlertStateRow, "A").state == "ALERTING"

    # The leader jumps far ahead while still technically rank 1 - becomes ineligible.
    yp.my_orders = [_my_order("A", rank="1", price="10", max_price="¥1500")]  # ratio 150
    r2 = m.run_round()
    assert r2.alerts_sent == 0
    assert r2.gap_gated == 1
    with Session() as s:
        state = s.get(AlertStateRow, "A")
        assert state.state == "NORMAL" and state.streak == 0

    # Gap narrows back - must be able to alert again, not stay stuck.
    yp.my_orders = [_my_order("A", rank="1", price="10", max_price="¥15")]
    r3 = m.run_round()
    assert r3.alerts_sent == 1
    with Session() as s:
        assert s.get(AlertStateRow, "A").state == "ALERTING"


def test_max_gap_ratio_zero_disables_filter(tmp_path, settings):
    settings.alerts.confirm_rounds = 1
    settings.polling.verify_sample_size = 0
    settings.alerts.max_gap_ratio = 0
    Session = _setup(tmp_path)
    my_orders = [_my_order("A", rank="2", price="10", max_price="¥1500")]  # ratio 150, would gate at 3.0
    m = Monitor(Session, FakeYoupin(my_orders=my_orders), FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()

    assert r.alerts_sent == 1
    assert r.gap_gated == 0


def test_no_top_bid_is_not_gated(tmp_path, settings):
    """An order with no known top_bid (e.g. YouPin didn't report maxPurchasePrice) can't
    be judged, so it must stay eligible rather than being silently muted."""
    settings.alerts.confirm_rounds = 1
    settings.polling.verify_sample_size = 0
    settings.alerts.max_gap_ratio = 3.0
    Session = _setup(tmp_path)
    my_orders = [_my_order("A", rank="2", price="10", max_price="not-a-price")]  # top_bid parses to None
    m = Monitor(Session, FakeYoupin(my_orders=my_orders), FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()

    assert r.alerts_sent == 1
    assert r.gap_gated == 0
    with Session() as s:
        assert s.get(MyBuyOrder, "A").top_bid is None


def test_zero_price_is_not_gated(tmp_path, settings):
    """A zero (non-positive) own price can't produce a meaningful ratio - stay eligible
    rather than dividing by zero or muting blindly. sync_my_orders itself already
    refuses to store a non-positive-price row (see services/account.py), so a zero-price
    MyBuyOrder can never reach run_round in practice - test _is_eligible_for_alert
    directly instead, as the defensive guard it is."""
    settings.alerts.max_gap_ratio = 3.0
    Session = _setup(tmp_path)
    m = Monitor(Session, FakeYoupin(), FakeNtfy(), settings, sleep=lambda s: None)
    o = SimpleNamespace(price=Decimal("0"), top_bid=Decimal("30"), sell_min=Decimal("90"), sell_depth=3, ref_bid=None)

    assert m._is_eligible_for_alert(o) is True


def test_apply_settings_changes_gap_threshold_live(tmp_path, settings):
    """Monitor._is_eligible_for_alert must read max_gap_ratio off self.s on every call
    (see apply_settings), never a value captured at construction - the same class of bug
    this project has already fixed for enter_rank/exit_rank/confirm_rounds and the
    hourly caps."""
    settings.alerts.confirm_rounds = 1
    settings.polling.verify_sample_size = 0
    settings.alerts.max_gap_ratio = 3.0
    Session = _setup(tmp_path)
    yp = FakeYoupin(my_orders=[_my_order("A", rank="2", price="10", max_price="¥100")])  # ratio 10: gated at 3.0
    m = Monitor(Session, yp, FakeNtfy(), settings, sleep=lambda s: None)

    r1 = m.run_round()
    assert r1.alerts_sent == 0 and r1.gap_gated == 1

    new = settings.model_copy(deep=True)
    new.alerts.max_gap_ratio = 20.0  # raised above the 10x gap - now eligible
    m.apply_settings(new)

    with Session() as s:
        s.get(AlertStateRow, "A").state = "NORMAL"  # fresh ENTER_TOP2 possible next round
        s.commit()
    r2 = m.run_round()
    assert r2.alerts_sent == 1
    assert r2.gap_gated == 0


def test_gated_count_logged_once_per_round_not_per_order(tmp_path, settings, caplog):
    settings.alerts.confirm_rounds = 1
    settings.polling.verify_sample_size = 0
    settings.alerts.max_gap_ratio = 3.0
    Session = _setup(tmp_path)
    my_orders = [_my_order("A", rank="2", price="10", max_price="¥1500", template_id=1414),
                 _my_order("B", rank="2", price="10", max_price="¥1500", template_id=2020)]
    m = Monitor(Session, FakeYoupin(my_orders=my_orders), FakeNtfy(), settings, sleep=lambda s: None)

    with caplog.at_level(logging.INFO, logger="skinpulse.services.monitor"):
        r = m.run_round()

    assert r.gap_gated == 2
    gap_lines = [rec.getMessage() for rec in caplog.records if "gap filter" in rec.getMessage()]
    assert len(gap_lines) == 1
    assert "2" in gap_lines[0]



def test_rank1_far_below_market_is_not_alerted(tmp_path, settings):
    """难以成交 spec §3：第 1 名但离市价太远（市价 ÷ 我的价 ≥ max_market_ratio 且差价 ≥ min_market_gap）
    不推「升到第 1 名」，计进 market_gated；阈值改了下一轮生效。"""
    settings.alerts.confirm_rounds = 1
    settings.polling.verify_sample_size = 0
    settings.alerts.max_market_ratio = 2.0
    settings.alerts.min_market_gap = 20
    Session = _setup(tmp_path)
    yp = FakeYoupin(my_orders=[_my_order("A", rank="1", price="10", max_price="¥10")])
    m = Monitor(Session, yp, FakeNtfy(), settings, sleep=lambda s: None)
    m.run_round()                              # 第一轮：建单（在售价还没查过 → 判断不了，不挡）
    with Session() as s:
        o = s.get(MyBuyOrder, "A")
        o.sell_min, o.sell_depth = Decimal("4871"), 1
        s.get(AlertStateRow, "A").state = "NORMAL"
        s.commit()

    r = m.run_round()
    assert r.alerts_sent == 0 and r.market_gated == 1 and r.gap_gated == 0

    new = settings.model_copy(deep=True)
    new.alerts.max_market_ratio = 0            # 关掉离市价这条
    m.apply_settings(new)
    r2 = m.run_round()
    assert r2.alerts_sent == 1 and r2.market_gated == 0


# ---- pass B selection (_select_verify_targets) ----

def test_verify_selection_prioritizes_near_threshold_then_least_recently_verified(tmp_path, settings):
    settings.polling.verify_sample_size = 2
    settings.polling.fast_lane_rank_threshold = 4
    Session = _setup(tmp_path)
    now = utcnow()
    _seed_order(Session, "NEAR-OLD", reported_rank=3, last_verified_at=now - timedelta(hours=2))
    _seed_order(Session, "NEAR-NEW", reported_rank=4, last_verified_at=now - timedelta(minutes=1))
    _seed_order(Session, "FAR-NEVER", reported_rank=50, last_verified_at=None)
    _seed_order(Session, "FAR-OLD", reported_rank=50, last_verified_at=now - timedelta(days=1))
    m = Monitor(Session, FakeYoupin(), FakeNtfy(), settings, sleep=lambda s: None)

    with Session() as s:
        orders = list(s.scalars(select(MyBuyOrder)))
        chosen = m._select_verify_targets(orders)

    assert len(chosen) == 2  # capped at verify_sample_size, never exceeds it
    # both near-threshold orders (<=4) outrank every far one, regardless of recency
    assert [o.purchase_no for o in chosen] == ["NEAR-OLD", "NEAR-NEW"]


def test_verify_selection_fills_remaining_budget_from_least_recently_verified(tmp_path, settings):
    settings.polling.verify_sample_size = 3
    settings.polling.fast_lane_rank_threshold = 4
    Session = _setup(tmp_path)
    now = utcnow()
    _seed_order(Session, "NEAR", reported_rank=2, last_verified_at=None)
    _seed_order(Session, "FAR-NEVER", reported_rank=50, last_verified_at=None)
    _seed_order(Session, "FAR-OLD", reported_rank=50, last_verified_at=now - timedelta(days=1))
    _seed_order(Session, "FAR-RECENT", reported_rank=50, last_verified_at=now - timedelta(minutes=1))
    m = Monitor(Session, FakeYoupin(), FakeNtfy(), settings, sleep=lambda s: None)

    with Session() as s:
        orders = list(s.scalars(select(MyBuyOrder)))
        chosen = {o.purchase_no for o in m._select_verify_targets(orders)}

    # NEAR always wins its slot; the other 2 of 3 slots go to the 2 most-overdue of the
    # remaining far orders (never-verified before merely-old, old before recent).
    assert chosen == {"NEAR", "FAR-NEVER", "FAR-OLD"}


def test_verify_selection_priority_uses_fast_lane_rank_threshold_not_exit_rank(tmp_path, settings):
    """Pass B's near-threshold priority (see Monitor._select_verify_targets) must key off
    polling.fast_lane_rank_threshold, not alerts.exit_rank - deliberately independent
    knobs (see PollingSettings.fast_lane_rank_threshold's docstring), so sampling focus
    can be tuned without touching alert hysteresis. Set them so the two picks disagree:
    only the correct implementation selects NEAR."""
    settings.polling.verify_sample_size = 1
    settings.polling.fast_lane_rank_threshold = 10  # NEAR (rank 8) qualifies
    settings.alerts.enter_rank = 1
    settings.alerts.exit_rank = 2  # NEAR (rank 8) would NOT qualify if this were used instead
    Session = _setup(tmp_path)
    now = utcnow()
    # If fast_lane_rank_threshold is (correctly) used, NEAR wins outright as "near". If
    # alerts.exit_rank were used instead, neither order is "near" and the single slot
    # falls to whichever is more overdue for verification - FAR (never verified) beats
    # NEAR (verified a moment ago) - so the two implementations disagree on the answer.
    _seed_order(Session, "NEAR", reported_rank=8, last_verified_at=now - timedelta(seconds=1))
    _seed_order(Session, "FAR", reported_rank=50, last_verified_at=None)
    m = Monitor(Session, FakeYoupin(), FakeNtfy(), settings, sleep=lambda s: None)

    with Session() as s:
        orders = list(s.scalars(select(MyBuyOrder)))
        chosen = m._select_verify_targets(orders)

    assert [o.purchase_no for o in chosen] == ["NEAR"]


def test_verify_selection_never_exceeds_sample_size_even_with_many_eligible(tmp_path, settings):
    settings.polling.verify_sample_size = 3
    Session = _setup(tmp_path)
    for i in range(10):
        _seed_order(Session, f"O{i}", reported_rank=1, last_verified_at=None)
    m = Monitor(Session, FakeYoupin(), FakeNtfy(), settings, sleep=lambda s: None)

    with Session() as s:
        orders = list(s.scalars(select(MyBuyOrder)))
        chosen = m._select_verify_targets(orders)

    assert len(chosen) == 3


# ---- night gate (revived polling.night_start_hour/night_end_hour/night_multiplier -
# see core.planner._is_night): the round is throttled during quiet hours ----

def _night_monitor(Session, settings, *, local_hour, my_orders=None):
    yp = FakeYoupin(my_orders=[_my_order("A")] if my_orders is None else my_orders)
    return yp, Monitor(Session, yp, FakeNtfy(), settings, sleep=lambda s: None, local_hour=lambda: local_hour)


def test_first_ever_round_runs_even_during_night_window(tmp_path, settings):
    """No prior round timestamp exists yet, so there is nothing to gate against - the
    very first round must run rather than wait out a full night_multiplier interval."""
    settings.polling.night_start_hour, settings.polling.night_end_hour = 0, 8
    Session = _setup(tmp_path)
    yp, m = _night_monitor(Session, settings, local_hour=3)

    r = m.run_round()

    assert r.skipped_night is False
    assert yp.sync_calls == 1
    with Session() as s:
        assert s.get(SystemStatus, ROUND).last_ok_at is not None  # stamped for next time


def test_round_gated_at_night_until_multiplier_times_interval_elapsed(tmp_path, settings):
    """During the configured (wrapping-past-midnight) night window, the round must be
    skipped until night_multiplier x round_interval_min minutes have elapsed since the
    last round that actually ran - not every round_interval_min minutes like daytime."""
    settings.polling.round_interval_min = 5
    settings.polling.night_multiplier = 3.0
    settings.polling.night_start_hour, settings.polling.night_end_hour = 22, 6  # wraps midnight
    Session = _setup(tmp_path)

    with freezegun.freeze_time("2026-08-30 23:00:00") as frozen:
        yp, m = _night_monitor(Session, settings, local_hour=23)

        r1 = m.run_round()
        assert r1.skipped_night is False
        assert yp.sync_calls == 1  # first round of the "night" always runs

        frozen.tick(timedelta(minutes=10))  # 10 min < 3 x 5 = 15 min threshold
        r2 = m.run_round()
        assert r2.skipped_night is True
        assert yp.sync_calls == 1  # gated: pass A did not run again

        frozen.tick(timedelta(minutes=6))  # cumulative 16 min >= 15 min threshold
        r3 = m.run_round()
        assert r3.skipped_night is False
        assert yp.sync_calls == 2  # gate opened: pass A ran again


def test_round_not_gated_outside_night_window(tmp_path, settings):
    """Outside the configured night window, run_round must apply no extra throttling at
    all, regardless of how little time has passed since the last round - the normal
    APScheduler-driven round_interval_min cadence (see scheduler.py) applies unmodified."""
    settings.polling.round_interval_min = 5
    settings.polling.night_multiplier = 3.0
    settings.polling.night_start_hour, settings.polling.night_end_hour = 22, 6
    Session = _setup(tmp_path)

    with freezegun.freeze_time("2026-08-30 12:00:00") as frozen:
        yp, m = _night_monitor(Session, settings, local_hour=12)

        m.run_round()
        frozen.tick(timedelta(minutes=1))  # far less than round_interval_min itself
        r2 = m.run_round()

        assert r2.skipped_night is False
        assert yp.sync_calls == 2


def test_night_gate_survives_restart_via_durable_timestamp(tmp_path, settings):
    """The last-round timestamp must live in durable storage (SystemStatus), not on the
    Monitor instance: a brand-new Monitor (simulating a process restart) built right
    after one round already ran tonight must still honor the gate."""
    settings.polling.round_interval_min = 5
    settings.polling.night_multiplier = 3.0
    settings.polling.night_start_hour, settings.polling.night_end_hour = 22, 6
    Session = _setup(tmp_path)

    with freezegun.freeze_time("2026-08-30 23:00:00") as frozen:
        yp1, m1 = _night_monitor(Session, settings, local_hour=23)
        m1.run_round()
        assert yp1.sync_calls == 1

        frozen.tick(timedelta(minutes=2))  # still well inside the 15-minute gated window
        yp2, m2 = _night_monitor(Session, settings, local_hour=23)  # a fresh instance - no shared memory with m1
        r2 = m2.run_round()

        assert r2.skipped_night is True
        assert yp2.sync_calls == 0  # gate still enforced against m1's durable timestamp


def test_verify_sample_size_zero_skips_pass_b_entirely(tmp_path, settings):
    settings.alerts.confirm_rounds = 1
    settings.polling.verify_sample_size = 0
    Session = _setup(tmp_path)
    my_orders = [_my_order("A", rank="1")]  # rank 1 -> would also fire an alert
    yp = FakeYoupin(my_orders=my_orders)
    m = Monitor(Session, yp, FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()

    assert yp.verify_calls == 0
    assert r.verified == 0 and r.skipped_paused is False
    assert r.checked == 1  # pass A still ran normally


def test_verify_disabled_skips_pass_b_entirely(tmp_path, settings):
    settings.alerts.confirm_rounds = 1
    settings.polling.verify_enabled = False
    Session = _setup(tmp_path)
    my_orders = [_my_order("A", rank="1")]
    yp = FakeYoupin(my_orders=my_orders)
    m = Monitor(Session, yp, FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()

    assert yp.verify_calls == 0
    assert r.verified == 0 and r.skipped_paused is False
    assert r.checked == 1


# ---- disagreement recording ----

def test_disagreement_writes_rank_check_row_and_logs_warning(tmp_path, settings, caplog):
    settings.polling.verify_sample_size = 1
    Session = _setup(tmp_path)
    # purchase_no "TEST-MINE-1" matches the AK redline fixture's own row of that name
    # directly (compute_rank's primary match path, not the userId fallback) - that row
    # sits at position 2 (price 358, sorted below 359 and above 357) in the "0.15-0.18"
    # sub-queue. reported_rank=5 is a deliberate mismatch against that computed 2.
    my_orders = [_my_order("TEST-MINE-1", rank="5")]
    m = Monitor(Session, FakeYoupin(my_orders=my_orders), FakeNtfy(), settings, sleep=lambda s: None)

    with caplog.at_level(logging.WARNING, logger="skinpulse.services.monitor"):
        r = m.run_round()

    assert r.verified == 1 and r.verify_disagreed == 1 and r.verify_agreed == 0
    with Session() as s:
        check = s.scalar(select(RankCheck))
        assert check.purchase_no == "TEST-MINE-1"
        assert check.reported_rank == 5 and check.computed_rank == 2
        assert check.agreed is False
        assert check.queue_size == 3 and check.sample_size == 9  # AK redline fixture: 9 rows, 3 in this sub-queue
    assert "TEST-MINE-1" in caplog.text and "reported=5" in caplog.text and "computed=2" in caplog.text


def test_agreement_writes_rank_check_row_with_agreed_true(tmp_path, settings):
    settings.polling.verify_sample_size = 1
    Session = _setup(tmp_path)
    my_orders = [_my_order("TEST-MINE-1", rank="2")]  # matches the fixture's own computed rank (2)
    m = Monitor(Session, FakeYoupin(my_orders=my_orders), FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()

    assert r.verify_agreed == 1 and r.verify_disagreed == 0
    with Session() as s:
        check = s.scalar(select(RankCheck))
        assert check.agreed is True
        order = s.get(MyBuyOrder, "TEST-MINE-1")
        assert order.last_verified_at is not None  # rotation bookkeeping advanced on success


def test_order_past_the_visible_page_counts_as_inconclusive_not_disagreement(tmp_path, settings, caplog):
    """自己的单排在公开队列可见的那一页之外时，`compute_rank` 只能返回 None——这既不是一致也不是
    不一致，单独计成 verify_inconclusive，而且不该打 WARNING（见 core.rank.verification_outcome）。"""
    settings.polling.verify_sample_size = 1
    settings.polling.visible_limit = 9  # == AK redline fixture 的行数，等于"这一页被取满了"
    settings.polling.deep_page_size = 0  # 关掉深取兜底，专测截断的判定
    Session = _setup(tmp_path)
    # purchase_no 不在 fixture 里，账号的 user_id 也对不上任何一行（fixture 里那行属于
    # FakeYoupin 的默认 user_id 99999999，这里换掉，免得走 compute_rank 的 user_id 兜底匹配）
    my_orders = [_my_order("TEST-NOT-IN-QUEUE", rank="30")]
    client = FakeYoupin(my_orders=my_orders, user_id=12345)
    m = Monitor(Session, client, FakeNtfy(), settings, sleep=lambda s: None)

    with caplog.at_level(logging.WARNING, logger="skinpulse.services.monitor"):
        r = m.run_round()

    assert r.verified == 1
    assert r.verify_inconclusive == 1 and r.verify_disagreed == 0 and r.verify_agreed == 0
    assert "TEST-NOT-IN-QUEUE" not in caplog.text  # 视野盲区不是警告
    with Session() as s:
        check = s.scalar(select(RankCheck))
        assert check.computed_rank is None and check.agreed is False
        assert check.sample_size == 9  # 行数据照旧写进去，判定完全从存下来的列推导


def test_verified_order_not_found_in_public_queue_counts_as_disagreement(tmp_path, settings):
    """Both `reported_rank` known and `computed_rank` unknown (order not found in its
    template's public queue) must count as a disagreement, not silently pass: agreed is
    only true when *both* ranks are known and equal."""
    settings.polling.verify_sample_size = 1
    Session = _setup(tmp_path)
    my_orders = [_my_order("A", rank="2", template_id=9999)]  # no rows for this template in the fixture
    m = Monitor(Session, FakeYoupin(my_orders=my_orders), FakeNtfy(), settings, sleep=lambda s: None)

    m.run_round()

    with Session() as s:
        check = s.scalar(select(RankCheck))
        assert check.reported_rank == 2 and check.computed_rank is None
        assert check.agreed is False


# ---- 告警前的实时核对（Monitor._confirm_with_live_queue）----


def test_stale_reported_rank_is_corrected_and_the_alert_does_not_fire(tmp_path, settings):
    """悠悠上报的 rank 滞后时，实时队列说了算——告警不该按一个已经不成立的名次发出去。

    实测依据：2026-09-21 模板 108753，悠悠说第 2 名、榜首 ¥351，同一秒的公开队列是
    352 / 351 / 我的 158，实际第 3 名。背靠背连采三轮完全一致。"""
    settings.alerts.confirm_with_live_queue = True
    settings.alerts.enter_rank = 1          # 上报的 rank=1 进带，实时算出来是 2，出带
    settings.alerts.confirm_rounds = 1
    Session = _setup(tmp_path)
    client = FakeYoupin(my_orders=[_my_order("TEST-MINE-1", rank="1")])  # fixture 里它实际排第 2
    ntfy = FakeNtfy()
    m = Monitor(Session, client, ntfy, settings, sleep=lambda s: None)

    r = m.run_round()

    assert r.live_confirmed == 1 and r.live_corrected == 1
    assert r.alerts_sent == 0 and ntfy.sent == []
    with Session() as s:
        assert s.get(AlertStateRow, "TEST-MINE-1").state == "NORMAL"
        snap = s.scalar(select(RankSnapshot).where(RankSnapshot.purchase_no == "TEST-MINE-1"))
        assert snap.rank == 2  # 存的是实时算出来的名次，不是上报的那个


def test_confirmed_rank_alerts_with_the_live_numbers(tmp_path, settings):
    """核对通过时照常告警，但推送里的榜首价用实时值——上报的榜首价同样会滞后，而用户
    正是照着这个数字去加价的。"""
    settings.alerts.confirm_with_live_queue = True
    settings.alerts.confirm_rounds = 1
    settings.alerts.max_gap_ratio = 0       # 关掉差价过滤，专测数字来源
    Session = _setup(tmp_path)
    # 上报 rank=2（与 fixture 实际位次一致），但上报榜首价 ¥360，实时队列榜首是 359
    client = FakeYoupin(my_orders=[_my_order("TEST-MINE-1", rank="2", max_price="¥360")])
    ntfy = FakeNtfy()
    m = Monitor(Session, client, ntfy, settings, sleep=lambda s: None)

    r = m.run_round()

    assert r.live_confirmed == 1 and r.live_corrected == 0
    assert r.alerts_sent == 1
    assert "359" in ntfy.sent[0].body and "360" not in ntfy.sent[0].body
    with Session() as s:
        snap = s.scalar(select(RankSnapshot).where(RankSnapshot.purchase_no == "TEST-MINE-1"))
        assert snap.top1_price == Decimal("359")


def test_live_confirm_skips_orders_already_alerting(tmp_path, settings):
    """已经在 ALERTING 的单这一轮不会再产生事件，核对它纯属浪费配额。"""
    settings.alerts.confirm_with_live_queue = True
    settings.polling.verify_sample_size = 0   # 关掉 pass B，让 verify_calls 只反映实时核对
    Session = _setup(tmp_path)
    client = FakeYoupin(my_orders=[_my_order("TEST-MINE-1", rank="2")])
    m = Monitor(Session, client, FakeNtfy(), settings, sleep=lambda s: None)
    _seed_order(Session, "TEST-MINE-1")
    with Session() as s:
        s.add(AlertStateRow(purchase_no="TEST-MINE-1", state="ALERTING", streak=2, last_rank=2,
                            updated_at=utcnow()))
        s.commit()

    r = m.run_round()

    assert r.live_confirmed == 0 and client.verify_calls == 0


def test_live_confirm_failure_falls_back_to_the_reported_rank(tmp_path, settings):
    """核对失败绝不能变成"不告警"：拿不到实时值就退回悠悠上报的值，照常判断。"""
    settings.alerts.confirm_with_live_queue = True
    settings.alerts.confirm_rounds = 1
    settings.alerts.max_gap_ratio = 0
    Session = _setup(tmp_path)
    client = FakeYoupin(my_orders=[_my_order("TEST-MINE-1", rank="2")],
                        verify_exc=YoupinError("9999", "boom"))
    ntfy = FakeNtfy()
    m = Monitor(Session, client, ntfy, settings, sleep=lambda s: None)

    r = m.run_round()

    assert r.live_confirmed == 0 and r.errors >= 1
    assert r.alerts_sent == 1 and len(ntfy.sent) == 1  # 照上报值发了


def test_live_confirm_disabled_makes_no_extra_requests(tmp_path, settings):
    settings.alerts.confirm_with_live_queue = False
    settings.polling.verify_sample_size = 0
    Session = _setup(tmp_path)
    client = FakeYoupin(my_orders=[_my_order("TEST-MINE-1", rank="2")])
    m = Monitor(Session, client, FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()

    assert client.verify_calls == 0 and r.live_confirmed == 0


# ---- 市场价刷新（Monitor._refresh_sell_prices）----


def _on_sale(*prices):
    from skinpulse.adapters.youpin.models import OnSaleItem

    return [OnSaleItem(commodity_no=f"C{i}", user_id=1, price=Decimal(str(p)), publish_time="", have_sticker=False)
            for i, p in enumerate(prices)]


def test_sell_refresh_records_lowest_listing_and_depth(tmp_path, settings):
    settings.polling.sell_sample_size = 1
    Session = _setup(tmp_path)
    client = FakeYoupin(my_orders=[_my_order("TEST-MINE-1")], on_sale={1414: _on_sale("73", "64.49", "99")})
    m = Monitor(Session, client, FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()

    assert r.sell_refreshed == 1 and r.errors == 0
    with Session() as s:
        o = s.get(MyBuyOrder, "TEST-MINE-1")
        assert o.sell_min == Decimal("64.49")  # 最低价，不是接口返回的第一条
        assert o.sell_depth == 3
        assert o.sell_checked_at is not None


def test_nobody_selling_is_recorded_as_depth_zero_not_as_a_zero_price(tmp_path, settings):
    """在售接口返回空列表是一个确定的结论——"这个模板现在没人在卖"——不是失败，更不是
    0 元。实测遇到过：SSG 08（StatTrak™）| 巨铁 (破损不堪) 连查三次都是 0 件在售。
    把 sell_min 兜底成 0 会让看板显示"市场价 0"，比不显示还糟。"""
    settings.polling.sell_sample_size = 1
    Session = _setup(tmp_path)
    client = FakeYoupin(my_orders=[_my_order("TEST-MINE-1")], on_sale={})  # 该模板没有任何在售
    m = Monitor(Session, client, FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()

    assert r.sell_refreshed == 1 and r.errors == 0
    with Session() as s:
        o = s.get(MyBuyOrder, "TEST-MINE-1")
        assert o.sell_depth == 0
        assert o.sell_min is None          # 不是 Decimal("0")
        assert o.sell_checked_at is not None  # 查过了，是个结论


def test_failed_sell_refresh_leaves_the_order_maximally_overdue(tmp_path, settings):
    """查失败的单不写 sell_checked_at，于是下一轮它仍然排在轮换最前面——和 pass B 里
    _skip_order 不碰 last_verified_at 是同一个理由。"""
    settings.polling.sell_sample_size = 1
    Session = _setup(tmp_path)
    client = FakeYoupin(my_orders=[_my_order("TEST-MINE-1")], sell_exc=YoupinError("9999", "boom"))
    m = Monitor(Session, client, FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()

    assert r.sell_refreshed == 0 and r.errors == 1
    with Session() as s:
        o = s.get(MyBuyOrder, "TEST-MINE-1")
        assert o.sell_checked_at is None and o.sell_min is None and o.sell_depth is None


def test_sell_refresh_is_disabled_by_zero_sample_size(tmp_path, settings):
    settings.polling.sell_sample_size = 0
    Session = _setup(tmp_path)
    client = FakeYoupin(my_orders=[_my_order("TEST-MINE-1")], on_sale={1414: _on_sale("73")})
    m = Monitor(Session, client, FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()

    assert client.sell_calls == 0 and r.sell_refreshed == 0


def test_sell_refresh_runs_even_with_pass_b_switched_off(tmp_path, settings):
    """两个步骤各有各的开关：pass B 验证的是排名语义，市场价补的是决策用的数字，
    关掉前者不该让后者跟着停摆。"""
    settings.polling.verify_enabled = False
    settings.polling.sell_sample_size = 1
    Session = _setup(tmp_path)
    client = FakeYoupin(my_orders=[_my_order("TEST-MINE-1")], on_sale={1414: _on_sale("73")})
    m = Monitor(Session, client, FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()

    assert client.verify_calls == 0
    assert client.sell_calls == 1 and r.sell_refreshed == 1


def test_snapshot_carries_the_last_known_market_price(tmp_path, settings):
    """市场价是抽样轮换刷新的，所以快照抄的是"当前已知值"，可能来自前几轮。两列一起抄，
    否则历史里分不清"没人在卖"和"没查过"。"""
    settings.polling.sell_sample_size = 1
    Session = _setup(tmp_path)
    client = FakeYoupin(my_orders=[_my_order("TEST-MINE-1")], on_sale={1414: _on_sale("73")})
    m = Monitor(Session, client, FakeNtfy(), settings, sleep=lambda s: None)

    m.run_round()   # 第一轮：写快照时还不知道市场价，轮末才查到
    m.run_round()   # 第二轮：快照应当带上第一轮查到的值

    with Session() as s:
        snaps = list(s.scalars(select(RankSnapshot).where(RankSnapshot.purchase_no == "TEST-MINE-1")
                               .order_by(RankSnapshot.ts)))
        assert snaps[0].sell_min is None and snaps[0].sell_depth is None
        assert snaps[-1].sell_min == Decimal("73") and snaps[-1].sell_depth == 1


# ---- 投递失败必须在每轮摘要里看得见 ----


class BrokenNtfy:
    def __init__(self):
        self.calls = 0

    def publish(self, msg):
        import httpx

        self.calls += 1
        raise httpx.ConnectError("down")


def test_undelivered_alert_shows_up_in_the_round_report(tmp_path, settings):
    """2026-09-22 夜里「MP7 笑一个 第 2 名」连续三次投递失败，那三轮的摘要写的都是
    `alerts=0 errors=0`——只看摘要完全看不出有告警没送达。"""
    settings.alerts.confirm_rounds = 1
    settings.alerts.max_gap_ratio = 0
    Session = _setup(tmp_path)
    m = Monitor(Session, FakeYoupin(my_orders=[_my_order("TEST-MINE-1", rank="2")]), BrokenNtfy(),
                settings, sleep=lambda s: None)

    r = m.run_round()

    assert r.alerts_sent == 0
    assert r.notify_failed == 1
    assert r.errors >= 1          # 不能再是 errors=0


def test_suppressed_alerts_are_not_counted_as_delivery_failures(tmp_path, settings):
    """被 hourly cap 挡下是有意不发，不是没发出去——混进 notify_failed 会让这个数字失去意义。"""
    settings.alerts.confirm_rounds = 1
    settings.alerts.max_gap_ratio = 0
    settings.alerts.hourly_cap = 1
    Session = _setup(tmp_path)
    m = Monitor(Session, FakeYoupin(my_orders=[_my_order("TEST-MINE-1", rank="2")]), FakeNtfy(),
                settings, sleep=lambda s: None)
    m.hourly_cap.allow(1)        # 先把这一小时的额度用掉

    r = m.run_round()

    assert r.alerts_sent == 0 and r.notify_failed == 0


def test_round_log_line_carries_notify_failed(caplog, settings):
    """只扫 WARNING 以上级别日志的人也不该错过「有告警没送达」。"""
    import logging

    from skinpulse.scheduler import job_monitor_round

    class _Rt:
        def __init__(self):
            self.monitor = type("M", (), {"run_round": lambda self: RoundReport(checked=5, notify_failed=2, errors=1)})()

    with caplog.at_level(logging.INFO, logger="skinpulse.scheduler"):
        job_monitor_round(_Rt())

    assert "notify_failed=2" in caplog.text
    assert any(r.levelno == logging.WARNING and "NOT delivered" in r.getMessage() for r in caplog.records)


# ---- 多通道推送：每条通道单独记健康状态 ----


def test_one_dead_channel_is_visible_even_when_the_other_delivers(tmp_path, settings):
    """PushPlus 收下、ntfy 挂了：告警算发出去了，但 ntfy 那条通道的失败必须在 /status 上看得见，
    不能被整体的「已发出」掩盖——ntfy「返回成功却从没到过手机」就是这样藏了两个多星期。"""
    from skinpulse.services import status as st
    from skinpulse.services.notifier import FanoutNotifier

    settings.alerts.confirm_rounds = 1
    settings.alerts.max_gap_ratio = 0
    Session = _setup(tmp_path)
    ok = FakeNtfy()
    notifier = FanoutNotifier([("pushplus", ok), ("ntfy", BrokenNtfy())])
    m = Monitor(Session, FakeYoupin(my_orders=[_my_order("TEST-MINE-1", rank="2")]), notifier, settings,
                sleep=lambda s: None)

    r = m.run_round()

    assert r.alerts_sent == 1 and r.notify_failed == 0
    with Session() as s:
        assert st.get_status(s, "pushplus").last_ok_at is not None
        assert st.get_status(s, "ntfy").last_error_code == "publish_failed"


def test_every_channel_failing_marks_each_one_and_counts_as_undelivered(tmp_path, settings):
    from skinpulse.services import status as st
    from skinpulse.services.notifier import FanoutNotifier

    settings.alerts.confirm_rounds = 1
    settings.alerts.max_gap_ratio = 0
    Session = _setup(tmp_path)
    notifier = FanoutNotifier([("pushplus", BrokenNtfy()), ("ntfy", BrokenNtfy())])
    m = Monitor(Session, FakeYoupin(my_orders=[_my_order("TEST-MINE-1", rank="2")]), notifier, settings,
                sleep=lambda s: None)

    r = m.run_round()

    assert r.alerts_sent == 0 and r.notify_failed == 1
    with Session() as s:
        assert st.get_status(s, "pushplus").last_error_code == "publish_failed"
        assert st.get_status(s, "ntfy").last_error_code == "publish_failed"


def test_pushed_alert_carries_the_moment_it_was_detected(tmp_path, settings):
    """正文最后一行是排名被观察到的那一刻，不是推送真正发出的那一刻——中间隔着实时核对、
    行情查询那几十秒，而通道在路上还可能再耽搁。"""
    from datetime import datetime, timezone

    settings.alerts.confirm_rounds = 1
    settings.alerts.max_gap_ratio = 0
    Session = _setup(tmp_path)
    ntfy = FakeNtfy()
    detected = datetime(2026, 9, 22, 16, 51, tzinfo=timezone.utc)
    m = Monitor(Session, FakeYoupin(my_orders=[_my_order("TEST-MINE-1", rank="2")]), ntfy, settings,
                now=lambda: detected, sleep=lambda s: None)

    m.run_round()

    assert ntfy.sent[0].body.splitlines()[-1] == "触发于 09-23 00:51"


# ---- 别处买盘（Monitor._refresh_reference_bids）----


class FakeSteamDT:
    """只实现 get_prices。`raises` 用来演练「行情挂了但告警必须照发」。"""

    def __init__(self, by_name=None, raises=None):
        self.by_name = by_name or {}
        self.raises = raises
        self.calls = []

    def get_prices(self, market_hash_name):
        from skinpulse.adapters.steamdt.models import ItemPrices

        self.calls.append(market_hash_name)
        if self.raises:
            raise self.raises
        return ItemPrices(market_hash_name=market_hash_name, platforms=self.by_name.get(market_hash_name, []))


def _sdt_row(platform, bid):
    from skinpulse.adapters.steamdt.models import PlatformPrice

    return PlatformPrice(platform=platform, platform_item_id="1", sell_price=None, sell_count=0,
                         bidding_price=Decimal(str(bid)), bidding_count=1, update_time=utcnow())


def _map_template(Session, template_id, market_hash_name):
    from skinpulse.db.models import Item

    with Session() as s:
        s.add(Item(hash_name=market_hash_name, cn_name="x", youpin_template_id=template_id))
        s.commit()


def test_alert_carries_the_bid_from_other_platforms(tmp_path, settings):
    """全量对账的结论是悠悠求购价比 BUFF/C5 中位数低 9.1%，所以「在悠悠排进前 2」不等于
    「这件东西抢手」。把别处买盘写进告警正文，就是为了让人当场看出这一点。"""
    settings.alerts.confirm_rounds = 1
    settings.alerts.max_gap_ratio = 0
    Session = _setup(tmp_path)
    _map_template(Session, 1414, "AK-47 | Redline (Field-Tested)")
    sdt = FakeSteamDT({"AK-47 | Redline (Field-Tested)": [_sdt_row("BUFF", 500), _sdt_row("C5", 500)]})
    ntfy = FakeNtfy()
    m = Monitor(Session, FakeYoupin(my_orders=[_my_order("TEST-MINE-1", rank="2", max_price="¥400")]),
                ntfy, settings, steamdt=sdt, sleep=lambda s: None)

    r = m.run_round()

    assert r.ref_bids_fetched == 1 and r.alerts_sent == 1
    assert "别处买盘 500" in ntfy.sent[0].body
    assert "BUFF/C5" in ntfy.sent[0].body
    with Session() as s:
        o = s.get(MyBuyOrder, "TEST-MINE-1")
        assert o.ref_bid == Decimal("500") and o.ref_bid_platforms == "BUFF/C5"


def test_alert_still_fires_when_the_market_source_is_down(tmp_path, settings):
    """行情是加分，排名才是本职。SteamDT 挂了只该让告警少一行，绝不能让告警不发。"""
    from skinpulse.adapters.steamdt.errors import SteamDTQuotaExceeded

    settings.alerts.confirm_rounds = 1
    settings.alerts.max_gap_ratio = 0
    Session = _setup(tmp_path)
    _map_template(Session, 1414, "AK-47 | Redline (Field-Tested)")
    ntfy = FakeNtfy()
    m = Monitor(Session, FakeYoupin(my_orders=[_my_order("TEST-MINE-1", rank="2")]), ntfy, settings,
                steamdt=FakeSteamDT(raises=SteamDTQuotaExceeded("1", "配额上限")), sleep=lambda s: None)

    r = m.run_round()

    assert r.ref_bids_fetched == 0
    assert r.alerts_sent == 1 and "别处买盘" not in ntfy.sent[0].body


def test_no_steamdt_configured_changes_nothing(tmp_path, settings):
    settings.alerts.confirm_rounds = 1
    settings.alerts.max_gap_ratio = 0
    Session = _setup(tmp_path)
    ntfy = FakeNtfy()
    m = Monitor(Session, FakeYoupin(my_orders=[_my_order("TEST-MINE-1", rank="2")]), ntfy, settings,
                sleep=lambda s: None)

    r = m.run_round()

    assert r.ref_bids_fetched == 0 and r.alerts_sent == 1


def test_reference_bid_is_only_fetched_for_orders_that_could_alert(tmp_path, settings):
    """已经在 ALERTING 的单这轮不会再发事件，给它查行情是纯浪费。"""
    Session = _setup(tmp_path)
    _map_template(Session, 1414, "AK-47 | Redline (Field-Tested)")
    _seed_order(Session, "TEST-MINE-1")
    with Session() as s:
        s.add(AlertStateRow(purchase_no="TEST-MINE-1", state="ALERTING", streak=2, last_rank=2,
                            updated_at=utcnow()))
        s.commit()
    sdt = FakeSteamDT()
    m = Monitor(Session, FakeYoupin(my_orders=[_my_order("TEST-MINE-1", rank="2")]), FakeNtfy(),
                settings, steamdt=sdt, sleep=lambda s: None)

    m.run_round()

    assert sdt.calls == []


def test_unmapped_template_is_skipped_without_a_request(tmp_path, settings):
    """没在 SteamDT 的悠悠映射里的饰品，不该拿中文名去硬凑一次查询。"""
    Session = _setup(tmp_path)   # 故意不建 Item 映射
    sdt = FakeSteamDT()
    m = Monitor(Session, FakeYoupin(my_orders=[_my_order("TEST-MINE-1", rank="2")]), FakeNtfy(),
                settings, steamdt=sdt, sleep=lambda s: None)

    r = m.run_round()

    assert sdt.calls == [] and r.ref_bids_fetched == 0


# ---- 深取兜底：登录后公开队列才真的能翻页 ----


def _pub_row(no, price, uid=777, abrade="0.15-0.18", template_id=1414):
    from skinpulse.adapters.youpin.models import PublicPurchaseOrder

    return PublicPurchaseOrder(purchase_no=no, user_id=uid, user_name="u", price=Decimal(price),
                               remaining_qty=1, abrade_text=abrade, type_id=-1, special_style=None,
                               fade_text=None, is_rank_first=None, template_id=template_id)


def test_deep_fetch_finds_an_order_the_anonymous_page_could_not_reach(tmp_path, settings):
    """匿名请求硬上限 20 条，`pageIndex`/`pageSize` 都被忽略；带 token 才真的生效
    （2026-09-21 实测模板 101422：匿名传 pageSize=100 仍返 20 条，登录后 pageSize=400
    一次返回全部 347 条）。所以"子队列里排得靠前、但在全表里排到 20 名开外"的单，只有深取
    才算得出排名——以前它永远是"无法判定"。"""
    settings.polling.verify_sample_size = 1
    settings.polling.visible_limit = 3
    settings.polling.deep_page_size = 50
    Session = _setup(tmp_path)
    shallow = [_pub_row(f"OTHER-{i}", p) for i, p in enumerate(("500", "400", "300"))]  # 取满 3 行，没有我们
    deep = shallow + [_pub_row("TEST-MINE-1", "250", uid=99999999)]   # 价最低，排第 4
    client = FakeYoupin(my_orders=[_my_order("TEST-MINE-1", rank="4")], public=shallow, deep=deep)
    m = Monitor(Session, client, FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()

    assert client.deep_calls == 1
    assert r.verify_agreed == 1 and r.verify_inconclusive == 0
    with Session() as s:
        check = s.scalar(select(RankCheck))
        assert check.computed_rank == 4 and check.agreed is True


def test_deep_fetch_not_attempted_when_the_page_was_not_full(tmp_path, settings):
    """队列整个取回来还找不到自己，那是真的对不上，不该再掏一次账号去追。"""
    settings.polling.verify_sample_size = 1
    settings.polling.visible_limit = 20
    settings.polling.deep_page_size = 50
    Session = _setup(tmp_path)
    client = FakeYoupin(my_orders=[_my_order("A", rank="2", template_id=9999)])  # fixture 里没有这个模板
    m = Monitor(Session, client, FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()

    assert client.deep_calls == 0
    assert r.verify_disagreed == 1 and r.verify_inconclusive == 0


def test_deep_fetch_that_still_cannot_find_us_is_a_real_disagreement(tmp_path, settings):
    """深取把整个队列都拿回来了还是找不到自己——这不再是"看不见"，是真的对不上。
    判定阈值必须跟着用深取的页大小，不能还拿 visible_limit 去判。"""
    settings.polling.verify_sample_size = 1
    settings.polling.visible_limit = 3
    settings.polling.deep_page_size = 50
    Session = _setup(tmp_path)
    shallow = [_pub_row(f"OTHER-{i}", p) for i, p in enumerate(("500", "400", "300"))]
    client = FakeYoupin(my_orders=[_my_order("TEST-MINE-1", rank="4")], public=shallow, deep=shallow)
    m = Monitor(Session, client, FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()

    assert client.deep_calls == 1
    assert r.verify_disagreed == 1 and r.verify_inconclusive == 0


# ---- pass A: the account channel is now load-bearing for the whole round ----

def test_login_required_from_sync_returns_report_records_status_and_does_not_propagate(tmp_path, settings, monkeypatch):
    import skinpulse.services.account as account_mod
    monkeypatch.setattr(account_mod, "_login_notice", account_mod.LoginLostNotice())
    Session = _setup(tmp_path)
    nt = FakeNtfy()
    yp = FakeYoupin(sync_exc=LoginRequired("84101", "expired"))
    m = Monitor(Session, yp, nt, settings, sleep=lambda s: None)

    r = m.run_round()  # must not raise

    assert isinstance(r, RoundReport)
    assert r.errors == 1
    assert r.checked == 0
    with Session() as s:
        st_row = s.get(SystemStatus, ACCOUNT)
        assert st_row is not None and st_row.last_error_code == "84101"
        assert s.get(Account, 1).token_valid is False
    assert len(nt.sent) == 1  # the "login lost" notice fired from this path too
    assert "登录" in nt.sent[0].title


def test_login_lost_notice_shared_with_heartbeat(tmp_path, settings, monkeypatch):
    """job_heartbeat and run_round's pass A can each independently discover the token
    is dead; the user must get one notice (then one per 24 hours), not one per caller."""
    import skinpulse.scheduler as sched_mod
    import skinpulse.services.account as account_mod
    from skinpulse.scheduler import job_heartbeat

    monkeypatch.setattr(account_mod, "_login_notice", account_mod.LoginLostNotice())
    Session = _setup(tmp_path)
    nt = FakeNtfy()
    yp = FakeYoupin(sync_exc=LoginRequired("84101", "expired"))
    m = Monitor(Session, yp, nt, settings, sleep=lambda s: None)
    rt = SimpleNamespace(client=yp, ntfy=nt, settings=settings, session_factory=Session)

    m.run_round()  # consumes the shared budget

    # heartbeat() itself never raises LoginRequired - it catches it internally and
    # returns False (see services/account.py). Simulate that discovery directly, patched
    # on the scheduler module's own binding (scheduler.py imported the name at module
    # load, so patching services.account.heartbeat would not reach job_heartbeat's call).
    monkeypatch.setattr(sched_mod, "heartbeat", lambda session, client: False)
    job_heartbeat(rt)  # must not send a second notice

    assert len(nt.sent) == 1


@pytest.mark.parametrize("exc,kind", [
    (RiskControl("84104", "风控"), "risk_control"),
    (RateLimited("429", "请求过快"), "rate_limited"),
    (MachineRoomBlocked("5050", "机房拦截"), "other"),
])
def test_pass_a_channel_wide_error_pauses_account_channel_and_aborts(tmp_path, settings, exc, kind):
    Session = _setup(tmp_path)
    _seed_order(Session, "PRE-EXISTING", last_checked_at=None)
    m = Monitor(Session, FakeYoupin(sync_exc=exc), FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()

    assert r.errors == 1 and r.checked == 0
    with Session() as s:
        st_row = s.get(SystemStatus, ACCOUNT)
        assert st_row.last_error_code == exc.code and st_row.paused_until is not None
        assert s.get(MyBuyOrder, "PRE-EXISTING").last_checked_at is None  # pass A never ran
    assert m.run_round().skipped_paused is True


def test_pass_a_uk_expired_records_to_system_status_without_pausing(tmp_path, settings):
    Session = _setup(tmp_path)
    yp = FakeYoupin(sync_exc=UkExpired("405", "uk 过期"))
    m = Monitor(Session, yp, FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()

    assert r.errors == 1
    with Session() as s:
        st_row = s.get(SystemStatus, ACCOUNT)
        assert st_row is not None and st_row.last_error_code == "405"
        assert st_row.paused_until is None  # UkExpired doesn't pause the channel
    assert yp.uk is None  # cached uk was still cleared


def test_pass_a_unexpected_error_is_recorded_and_does_not_propagate(tmp_path, settings):
    Session = _setup(tmp_path)
    m = Monitor(Session, FakeYoupin(sync_exc=RuntimeError("simulated: database is locked")),
                FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()  # must not raise

    assert r.errors == 1 and r.checked == 0


# ---- pass B: channel-wide vs skip-type errors, and pass A surviving pass B's failure ----

def test_pass_b_risk_control_pauses_anon_channel_but_pass_a_alerts_still_notified(tmp_path, settings):
    settings.alerts.confirm_rounds = 1
    settings.polling.verify_sample_size = 1
    Session = _setup(tmp_path)
    my_orders = [_my_order("A", rank="1")]  # fires an alert in pass A
    nt = FakeNtfy()
    yp = FakeYoupin(my_orders=my_orders, verify_exc=RiskControl("84104", "风控"))
    m = Monitor(Session, yp, nt, settings, sleep=lambda s: None)

    r = m.run_round()

    assert r.alerts_sent == 1 and len(nt.sent) == 1  # pass A's alert was not discarded
    assert r.errors == 1
    with Session() as s:
        assert s.get(SystemStatus, ANON).paused_until is not None
        assert s.scalar(select(RankCheck)) is None  # the sample never completed
    assert m.run_round().skipped_paused is True  # pass B skipped next round; pass A still runs
    with Session() as s:
        assert s.get(MyBuyOrder, "A").last_checked_at is not None  # pass A ran again


def test_pass_b_generic_youpin_error_skips_sample_without_pausing(tmp_path, settings):
    settings.polling.verify_sample_size = 2
    Session = _setup(tmp_path)
    my_orders = [_my_order("A", template_id=1414), _my_order("B", template_id=2020, rank="9")]
    yp = RoutedVerify({1414: YoupinError("500", "server error")}, my_orders=my_orders)
    m = Monitor(Session, yp, FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()

    assert yp.verify_calls == 2  # both samples attempted; a generic error must not abort sampling
    assert r.verified == 1 and r.errors == 1
    with Session() as s:
        assert s.get(SystemStatus, ANON).paused_until is None
        assert s.scalar(select(RankCheck)) is not None  # the surviving sample was recorded


def test_pass_b_no_breaker_all_samples_attempted_and_paced(tmp_path, settings):
    settings.polling.verify_sample_size = 4
    Session = _setup(tmp_path)
    my_orders = [_my_order(f"A{i}") for i in range(4)]
    sleeps = []
    yp = FakeYoupin(my_orders=my_orders, verify_exc=YoupinError("500", "server error"))
    m = Monitor(Session, yp, FakeNtfy(), settings, sleep=lambda s: sleeps.append(s))

    r = m.run_round()

    assert yp.verify_calls == 4  # every selected sample attempted, not aborted early
    assert r.errors == 4 and r.verified == 0
    assert len(sleeps) == 4  # paced after every attempt


def test_pass_b_unexpected_error_is_contained_per_sample(tmp_path, settings, monkeypatch):
    settings.polling.verify_sample_size = 2
    Session = _setup(tmp_path)
    my_orders = [_my_order("A", template_id=1414), _my_order("B", template_id=1414, rank="9")]

    import skinpulse.services.monitor as monitor_mod
    original_compute_rank = monitor_mod.compute_rank
    state = {"n": 0}

    def flaky_compute_rank(*args, **kwargs):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("simulated: database is locked")
        return original_compute_rank(*args, **kwargs)

    monkeypatch.setattr(monitor_mod, "compute_rank", flaky_compute_rank)
    yp = FakeYoupin(my_orders=my_orders)
    m = Monitor(Session, yp, FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()

    assert yp.verify_calls == 2  # both samples fetched; the crash on the first didn't abort the loop
    assert r.verified == 1
    assert r.errors == 1


def test_pass_b_uk_expired_backoff_waited_once_per_round_then_paced(tmp_path, settings):
    settings.polling.jitter_pct = 0  # deterministic pace value, distinguishable from the 60s backoff
    settings.polling.verify_sample_size = 3
    Session = _setup(tmp_path)
    my_orders = [_my_order(f"A{i}") for i in range(3)]
    sleeps = []
    yp = FakeYoupin(my_orders=my_orders, verify_exc=UkExpired("405", "uk 过期"))
    m = Monitor(Session, yp, FakeNtfy(), settings, sleep=lambda s: sleeps.append(s))

    r = m.run_round()

    assert r.errors == 3
    assert yp.verify_calls == 3
    assert sleeps.count(60) == 1  # the 60s backoff was waited exactly once this round
    paced = [s for s in sleeps if s != 60]
    assert len(paced) == 2
    assert all(s == settings.polling.request_interval_s for s in paced)

    # The once-per-round flag must reset on the next round, not stay latched forever.
    sleeps.clear()
    m.run_round()
    assert sleeps.count(60) == 1


def test_error_handler_commit_failure_does_not_strand_other_orders_pending(tmp_path, settings, monkeypatch):
    # Order A fires ENTER_TOP2 in pass A and commits its ALERTING state + pending
    # NotificationLog row. Order B is pass B's one sample and raises YoupinError, and
    # B's error-handler commit (st.mark_error inside _skip_order) itself raises -
    # simulating "database is locked" from a concurrent dashboard write. This must not
    # escape run_round and discard A's already-fired alert.
    settings.alerts.confirm_rounds = 1
    # Both orders are eligible for pass B (A is near-threshold; B fills the remaining
    # slot via least-recently-verified rotation) so B - the one whose fetch fails -
    # actually gets sampled alongside A.
    settings.polling.verify_sample_size = 2
    Session = _setup(tmp_path)
    my_orders = [_my_order("A", rank="1"), _my_order("B", template_id=2020, rank="50")]

    import skinpulse.services.monitor as monitor_mod
    original_mark_error = monitor_mod.st.mark_error

    def flaky_mark_error(session, adapter, code, paused_until):
        if adapter == monitor_mod.ANON and code == "500":
            raise RuntimeError("simulated: database is locked")
        return original_mark_error(session, adapter, code, paused_until)

    monkeypatch.setattr(monitor_mod.st, "mark_error", flaky_mark_error)
    yp = RoutedVerify({2020: YoupinError("500", "server error")}, my_orders=my_orders)
    m = Monitor(Session, yp, FakeNtfy(), settings, sleep=lambda s: None)

    r = m.run_round()  # must not raise

    assert isinstance(r, RoundReport)
    assert r.alerts_sent == 1  # A's notification was still delivered despite B's handler blowing up
    with Session() as s:
        row = s.scalar(select(NotificationLog).where(NotificationLog.fingerprint == "A"))
        assert row.status == "sent"
        assert s.get(AlertStateRow, "A").state == "ALERTING"


# ---- pending-notification durability (finding 2, adapted: peek during pass B) ----

def test_pending_notification_log_exists_before_notify_runs(tmp_path, settings):
    settings.alerts.confirm_rounds = 1
    settings.polling.verify_sample_size = 1
    Session = _setup(tmp_path)
    my_orders = [_my_order("A", rank="1")]  # rank 1 both fires the alert and is pass B's one sample

    captured = {}

    class SpyYoupin(FakeYoupin):
        def get_template_purchase_orders(self, template_id, page_size=20):
            # Pass B runs strictly after pass A's commit and strictly before _notify -
            # peek from a fresh session to prove the pending row is already durable here.
            with Session() as peek:
                rows = peek.scalars(select(NotificationLog)).all()
                captured["rows"] = [(r.fingerprint, r.status, r.event) for r in rows]
            return super().get_template_purchase_orders(template_id, page_size)

    yp = SpyYoupin(my_orders=my_orders)
    nt = FakeNtfy()
    m = Monitor(Session, yp, nt, settings, sleep=lambda s: None)

    r = m.run_round()

    assert "rows" in captured
    assert len(captured["rows"]) == 1
    fingerprint, status, event = captured["rows"][0]
    assert fingerprint == "A" and status == "pending" and event == "ENTER_TOP2"

    assert r.alerts_sent == 1
    with Session() as s:
        rows = s.scalars(select(NotificationLog)).all()
        assert len(rows) == 1  # no duplicate row
        assert rows[0].status == "sent"


# ---- notification pipeline (unchanged - exercised directly via _notify) ----

def _synthetic_events(n: int) -> list[tuple[AlertEvent, OrderView]]:
    out = []
    for i in range(n):
        purchase_no = f"MULTI-{i}"
        view = OrderView(purchase_no=purchase_no, commodity_name=f"AK-47 | 红线 (久经沙场) #{i}",
                         template_id=1414, queue_label="不限", my_price=Decimal("358"), rank=2,
                         top1_price=Decimal("359"), top2_price=Decimal("358"), top1_qty=1,
                         sell_min=Decimal("357"), remaining_qty=1)
        out.append((AlertEvent("ENTER_TOP2", purchase_no, 2), view))
    return out


def test_many_simultaneous_alerts_consume_one_cap_unit_and_one_push(tmp_path, settings):
    settings.alerts.hourly_cap = 3
    Session = _setup(tmp_path)
    nt = FakeNtfy()
    m = Monitor(Session, FakeYoupin(), nt, settings, sleep=lambda s: None)
    events = _synthetic_events(5)

    with Session() as s:
        sent = m._notify(s, events)

    assert sent == 5
    assert len(nt.sent) == 1
    body = nt.sent[0].body
    assert all(f"#{i}" in body for i in range(5))
    with Session() as s:
        rows = s.scalars(select(NotificationLog)).all()
        assert len(rows) == 5
        assert all(r.status == "sent" for r in rows)
    assert m.hourly_cap.allow(1) is True
    assert m.hourly_cap.allow(1) is True
    assert m.hourly_cap.allow(1) is False


def test_exhausted_hourly_cap_suppresses_but_leaves_notification_log_trail(tmp_path, settings):
    settings.alerts.hourly_cap = 1
    Session = _setup(tmp_path)
    nt = FakeNtfy()
    m = Monitor(Session, FakeYoupin(), nt, settings, sleep=lambda s: None)
    assert m.hourly_cap.allow(1) is True  # exhaust the one-per-hour budget

    events = _synthetic_events(2)
    with Session() as s:
        sent = m._notify(s, events)
        assert sent == 0
        assert nt.sent == []
        rows = s.scalars(select(NotificationLog)).all()
        assert len(rows) == 2
        assert all(r.status == "suppressed" for r in rows)
        assert {r.fingerprint for r in rows} == {"MULTI-0", "MULTI-1"}
        assert all(r.event == "ENTER_TOP2" for r in rows)


def test_exhausted_hourly_cap_via_run_round_reports_zero_alerts(tmp_path, settings):
    settings.alerts.hourly_cap = 1
    settings.alerts.confirm_rounds = 1
    settings.polling.verify_sample_size = 0
    Session = _setup(tmp_path)
    nt = FakeNtfy()
    my_orders = [_my_order("A", rank="1")]
    m = Monitor(Session, FakeYoupin(my_orders=my_orders), nt, settings, sleep=lambda s: None)
    r1 = m.run_round()
    assert r1.alerts_sent == 1 and len(nt.sent) == 1

    with Session() as s:
        s.get(AlertStateRow, "A").state = "NORMAL"  # force a fresh ENTER_TOP2 next round
        s.commit()
    r2 = m.run_round()
    assert r2.alerts_sent == 0
    assert len(nt.sent) == 1  # nothing new was published
    with Session() as s:
        suppressed = s.scalars(select(NotificationLog).where(NotificationLog.status == "suppressed")).all()
        assert len(suppressed) == 1
        assert suppressed[0].fingerprint == "A"


def test_failed_notification_resets_alert_state_to_normal(tmp_path, settings):
    class BadNtfy:
        def publish(self, msg):
            raise RuntimeError("down")

    Session = _setup(tmp_path)
    with Session() as s:
        s.add(AlertStateRow(purchase_no="MULTI-0", state="ALERTING", streak=3, last_rank=2, updated_at=utcnow()))
        s.commit()

    m = Monitor(Session, FakeYoupin(), BadNtfy(), settings, sleep=lambda s: None)
    events = _synthetic_events(1)
    with Session() as s:
        sent = m._notify(s, events)
        assert sent == 0
        assert s.scalar(select(NotificationLog)).status == "failed"
        row = s.get(AlertStateRow, "MULTI-0")
        assert row.state == "NORMAL" and row.streak == 0


def test_suppressed_notification_resets_alert_state_to_normal(tmp_path, settings):
    settings.alerts.hourly_cap = 1
    Session = _setup(tmp_path)
    m = Monitor(Session, FakeYoupin(), FakeNtfy(), settings, sleep=lambda s: None)
    assert m.hourly_cap.allow(1) is True  # exhaust the one-per-hour budget

    with Session() as s:
        s.add(AlertStateRow(purchase_no="MULTI-0", state="ALERTING", streak=3, last_rank=2, updated_at=utcnow()))
        s.commit()

    events = _synthetic_events(1)
    with Session() as s:
        sent = m._notify(s, events)
        assert sent == 0
        assert s.scalar(select(NotificationLog)).status == "suppressed"
        row = s.get(AlertStateRow, "MULTI-0")
        assert row.state == "NORMAL" and row.streak == 0


def test_sent_notification_leaves_alert_state_alone(tmp_path, settings):
    Session = _setup(tmp_path)
    with Session() as s:
        s.add(AlertStateRow(purchase_no="MULTI-0", state="ALERTING", streak=3, last_rank=2, updated_at=utcnow()))
        s.commit()

    m = Monitor(Session, FakeYoupin(), FakeNtfy(), settings, sleep=lambda s: None)
    events = _synthetic_events(1)
    with Session() as s:
        sent = m._notify(s, events)
        assert sent == 1
        row = s.get(AlertStateRow, "MULTI-0")
        assert row.state == "ALERTING" and row.streak == 3  # untouched - delivery actually succeeded


def test_transport_level_ntfy_failure_does_not_log_topic(tmp_path, settings, caplog):
    settings.alerts.confirm_rounds = 1
    settings.polling.verify_sample_size = 0

    class TransportFailNtfy:
        def publish(self, msg):
            raise ConnectionError("Failed to establish a new connection: https://ntfy.sh/super-secret-topic-abc123")

    Session = _setup(tmp_path)
    my_orders = [_my_order("A", rank="1")]
    m = Monitor(Session, FakeYoupin(my_orders=my_orders), TransportFailNtfy(), settings, sleep=lambda s: None)
    with caplog.at_level(logging.DEBUG, logger="skinpulse.services.monitor"):
        r = m.run_round()
    assert r.alerts_sent == 0
    logged = caplog.text
    assert "super-secret-topic-abc123" not in logged
    assert "ConnectionError" in logged
    with Session() as s:
        assert s.scalar(select(NotificationLog)).status == "failed"


def test_missing_ntfy_client_suppresses_rows_and_does_not_mark_healthy(tmp_path, settings):
    settings.alerts.confirm_rounds = 1
    settings.polling.verify_sample_size = 0
    Session = _setup(tmp_path)
    my_orders = [_my_order("A", rank="1")]
    m = Monitor(Session, FakeYoupin(my_orders=my_orders), None, settings, sleep=lambda s: None)
    r = m.run_round()
    assert r.alerts_sent == 0
    with Session() as s:
        row = s.scalar(select(NotificationLog))
        assert row.status == "suppressed"
        assert s.get(SystemStatus, "ntfy") is None  # never marked healthy - there was no adapter to mark


def test_notify_stage_failure_does_not_escape_run_round(tmp_path, settings, monkeypatch):
    settings.alerts.confirm_rounds = 1
    settings.polling.verify_sample_size = 0
    Session = _setup(tmp_path)
    my_orders = [_my_order("A", rank="1")]

    import skinpulse.services.monitor as monitor_mod

    def boom(*args, **kwargs):
        raise RuntimeError("simulated: database is locked")

    monkeypatch.setattr(monitor_mod, "build_enter_top2_message", boom)

    m = Monitor(Session, FakeYoupin(my_orders=my_orders), FakeNtfy(), settings, sleep=lambda s: None)
    r = m.run_round()  # must not raise

    assert r.checked == 1
    assert r.alerts_sent == 0
    assert r.errors == 1


def test_db_error_after_successful_publish_leaves_rows_sent_not_failed(tmp_path, settings, monkeypatch):
    settings.alerts.confirm_rounds = 1
    settings.polling.verify_sample_size = 0
    Session = _setup(tmp_path)
    my_orders = [_my_order("A", rank="1")]

    import skinpulse.services.monitor as monitor_mod
    original_mark_ok = monitor_mod.st.mark_ok

    def flaky_mark_ok(session, adapter):
        if adapter == "ntfy":
            raise RuntimeError("simulated: database is locked")
        return original_mark_ok(session, adapter)

    monkeypatch.setattr(monitor_mod.st, "mark_ok", flaky_mark_ok)

    nt = FakeNtfy()
    m = Monitor(Session, FakeYoupin(my_orders=my_orders), nt, settings, sleep=lambda s: None)
    r = m.run_round()  # must not raise, despite the post-publish DB error

    assert len(nt.sent) == 1
    with Session() as s:
        row = s.scalar(select(NotificationLog))
        assert row.status == "sent"
    assert r.errors == 1


# ---- settings edits must actually reach the running Monitor (unchanged mechanics) ----

def test_apply_settings_rebuilds_alert_engine_thresholds(tmp_path, settings):
    Session = _setup(tmp_path)
    m = Monitor(Session, FakeYoupin(), FakeNtfy(), settings, sleep=lambda s: None)
    assert m.engine.enter_rank == settings.alerts.enter_rank

    new = settings.model_copy(deep=True)
    new.alerts.enter_rank = 1
    new.alerts.exit_rank = 3
    new.alerts.confirm_rounds = 5
    m.apply_settings(new)

    assert m.s is new
    assert m.engine.enter_rank == 1
    assert m.engine.exit_rank == 3
    assert m.engine.confirm_rounds == 5


def test_apply_settings_preserves_token_bucket_when_cap_unchanged(tmp_path, settings):
    Session = _setup(tmp_path)
    m = Monitor(Session, FakeYoupin(), FakeNtfy(), settings, sleep=lambda s: None)
    assert m.bucket.try_acquire(5) is True
    spent_tokens = m.bucket.tokens
    bucket_before = m.bucket

    new = settings.model_copy(deep=True)
    new.polling.request_interval_s = 9.0
    m.apply_settings(new)

    assert m.bucket is bucket_before
    assert m.bucket.tokens == spent_tokens


def test_apply_settings_rebuilds_token_bucket_when_cap_changed(tmp_path, settings):
    Session = _setup(tmp_path)
    m = Monitor(Session, FakeYoupin(), FakeNtfy(), settings, sleep=lambda s: None)
    assert m.bucket.try_acquire(5) is True

    new = settings.model_copy(deep=True)
    new.polling.hourly_request_cap = settings.polling.hourly_request_cap + 100
    m.apply_settings(new)

    assert m.bucket.capacity == new.polling.hourly_request_cap
    assert m.bucket.tokens == new.polling.hourly_request_cap


def test_apply_settings_preserves_hourly_cap_window_when_unchanged(tmp_path, settings):
    settings.alerts.hourly_cap = 2
    Session = _setup(tmp_path)
    m = Monitor(Session, FakeYoupin(), FakeNtfy(), settings, sleep=lambda s: None)
    assert m.hourly_cap.allow(2) is True
    assert m.hourly_cap.allow(1) is False
    cap_before = m.hourly_cap

    new = settings.model_copy(deep=True)
    new.polling.request_interval_s = 9.0
    m.apply_settings(new)

    assert m.hourly_cap is cap_before
    assert m.hourly_cap.allow(1) is False


def test_apply_settings_rebuilds_hourly_cap_when_changed(tmp_path, settings):
    settings.alerts.hourly_cap = 1
    Session = _setup(tmp_path)
    m = Monitor(Session, FakeYoupin(), FakeNtfy(), settings, sleep=lambda s: None)
    assert m.hourly_cap.allow(1) is True
    assert m.hourly_cap.allow(1) is False

    new = settings.model_copy(deep=True)
    new.alerts.hourly_cap = 5
    m.apply_settings(new)

    assert m.hourly_cap.allow(1) is True


def test_mark_ok_recorded_once_per_round_not_once_per_order_or_sample(tmp_path, settings, monkeypatch):
    settings.polling.verify_sample_size = 3
    Session = _setup(tmp_path)
    my_orders = [_my_order(f"A{i}") for i in range(3)]

    import skinpulse.services.monitor as monitor_mod
    calls: list[str] = []
    original_mark_ok = monitor_mod.st.mark_ok

    def spy_mark_ok(session, adapter):
        calls.append(adapter)
        return original_mark_ok(session, adapter)

    monkeypatch.setattr(monitor_mod.st, "mark_ok", spy_mark_ok)

    m = Monitor(Session, FakeYoupin(my_orders=my_orders), FakeNtfy(), settings, sleep=lambda s: None)
    r = m.run_round()

    assert r.checked == 3 and r.verified == 3
    assert calls.count(ACCOUNT) == 1  # pass A: once per round, not once per order
    assert calls.count(ANON) == 1  # pass B: once per round, not once per sample


def test_default_local_hour_follows_timezone_via_apply_settings(tmp_path, settings):
    """With no local_hour override, changing timezone via apply_settings changes what
    local_hour() returns. This test is clock-independent: it freezes the system clock
    at a specific UTC instant and verifies the lambda returns the correct local hour for
    two different timezones at that instant, computed directly from the frozen time."""
    import freezegun
    from datetime import datetime as dt

    test_instant = dt(2025, 6, 15, 12, 0, 0)

    with freezegun.freeze_time(test_instant, tz_offset=0):
        Session = _setup(tmp_path)
        settings_tokyo = settings.model_copy(deep=True)
        settings_tokyo.timezone = "Asia/Tokyo"
        m = Monitor(Session, FakeYoupin(), FakeNtfy(), settings_tokyo)

        assert m.local_hour() == 21  # Asia/Tokyo (UTC+9, no DST)

        settings_phoenix = settings.model_copy(deep=True)
        settings_phoenix.timezone = "America/Phoenix"
        m.apply_settings(settings_phoenix)

        assert m.local_hour() == 5  # America/Phoenix (UTC-7, no DST)


def test_explicit_local_hour_override_survives_apply_settings(tmp_path, settings):
    Session = _setup(tmp_path)

    custom_hour = 15
    def fixed_local_hour():
        return custom_hour

    settings_before = settings.model_copy(deep=True)
    settings_before.timezone = "Asia/Shanghai"
    m = Monitor(Session, FakeYoupin(), FakeNtfy(), settings_before, local_hour=fixed_local_hour)

    assert m.local_hour is fixed_local_hour
    assert m.local_hour() == 15

    settings_after = settings.model_copy(deep=True)
    settings_after.timezone = "America/New_York"
    m.apply_settings(settings_after)

    assert m.local_hour is fixed_local_hour
    assert m.local_hour() == 15


# ---- finding 2 (important, pre-existing): a dangling pending NotificationLog row must be recoverable ----

def test_recover_pending_marks_failed_and_resets_alert_state(tmp_path, settings):
    Session = _setup(tmp_path)
    with Session() as s:
        s.add(AlertStateRow(purchase_no="TEST-MINE-1", state="ALERTING", streak=0, last_rank=2, updated_at=utcnow()))
        s.add(NotificationLog(fingerprint="TEST-MINE-1", event="ENTER_TOP2", rank=2, payload_json="{}",
                              created_at=utcnow(), status="pending"))
        # An earlier round's already-delivered notification must be left untouched.
        s.add(AlertStateRow(purchase_no="TEST-MINE-2", state="ALERTING", streak=0, last_rank=1, updated_at=utcnow()))
        s.add(NotificationLog(fingerprint="TEST-MINE-2", event="ENTER_TOP2", rank=1, payload_json="{}",
                              created_at=utcnow(), status="sent", sent_at=utcnow()))
        s.commit()

    with Session() as s:
        count = recover_pending(s)
        assert count == 1

    with Session() as s:
        pending = s.scalar(select(NotificationLog).where(NotificationLog.fingerprint == "TEST-MINE-1"))
        assert pending.status == "failed"
        untouched = s.scalar(select(NotificationLog).where(NotificationLog.fingerprint == "TEST-MINE-2"))
        assert untouched.status == "sent"
        recovered_state = s.get(AlertStateRow, "TEST-MINE-1")
        assert recovered_state.state == "NORMAL" and recovered_state.streak == 0
        untouched_state = s.get(AlertStateRow, "TEST-MINE-2")
        assert untouched_state.state == "ALERTING"


# ---- enter_rank 收窄（ADR-0008 改成 1）：留在 ALERTING 的旧单不能吞掉第 1 名告警 ----

def _seed_alert(Session, purchase_no, state, last_rank, streak=0):
    with Session() as s:
        s.add(AlertStateRow(purchase_no=purchase_no, state=state, streak=streak, last_rank=last_rank,
                            updated_at=utcnow()))
        s.commit()


def _alert_states(Session):
    with Session() as s:
        return {r.purchase_no: (r.state, r.streak) for r in s.scalars(select(AlertStateRow))}


def _with_enter_rank(settings, enter_rank):
    new = settings.model_copy(deep=True)
    new.alerts.enter_rank = enter_rank
    return new


def test_reset_alerting_above_enter_rank_only_touches_alerting_rows_outside_the_band(tmp_path):
    from skinpulse.services.monitor import reset_alerting_above_enter_rank

    Session = _setup(tmp_path)
    _seed_alert(Session, "R2", "ALERTING", 2)
    _seed_alert(Session, "R3", "ALERTING", 3)
    _seed_alert(Session, "R1", "ALERTING", 1)                 # 仍在新门槛内：不动
    _seed_alert(Session, "N2", "NORMAL", 2, streak=1)         # 不是 ALERTING：不动
    with Session() as s:
        assert reset_alerting_above_enter_rank(s, 1) == 2
    assert _alert_states(Session) == {"R2": ("NORMAL", 0), "R3": ("NORMAL", 0), "R1": ("ALERTING", 0),
                                      "N2": ("NORMAL", 1)}


def test_apply_settings_shrinking_enter_rank_resets_stale_alerting_rows(tmp_path, settings, caplog):
    settings.alerts.enter_rank = 2
    Session = _setup(tmp_path)
    _seed_alert(Session, "R2", "ALERTING", 2)
    _seed_alert(Session, "R3", "ALERTING", 3)
    _seed_alert(Session, "R1", "ALERTING", 1)
    m = Monitor(Session, FakeYoupin(), FakeNtfy(), settings, sleep=lambda s: None)

    with caplog.at_level(logging.INFO, logger="skinpulse.services.monitor"):
        m.apply_settings(_with_enter_rank(settings, 1))

    assert _alert_states(Session) == {"R2": ("NORMAL", 0), "R3": ("NORMAL", 0), "R1": ("ALERTING", 0)}
    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert len(lines) == 1 and "2" in lines[0]
    assert "R2" not in lines[0] and "R3" not in lines[0]      # 只报数量，不报单号


@pytest.mark.parametrize("new_enter_rank", [2, 3])
def test_apply_settings_unchanged_or_growing_enter_rank_resets_nothing(tmp_path, settings, new_enter_rank):
    settings.alerts.enter_rank = 2
    Session = _setup(tmp_path)
    _seed_alert(Session, "R3", "ALERTING", 3)
    m = Monitor(Session, FakeYoupin(), FakeNtfy(), settings, sleep=lambda s: None)

    m.apply_settings(_with_enter_rank(settings, new_enter_rank))

    assert _alert_states(Session) == {"R3": ("ALERTING", 0)}


def test_order_alerting_at_rank_2_alerts_once_it_reaches_rank_1_after_enter_rank_shrinks(tmp_path, settings):
    """复审 F1：enter_rank=2 时在第 2 名进了 ALERTING 的单，改成 enter_rank=1 后升到第 1 名必须推送，
    不能等它先掉到第 4 名以后。"""
    settings.alerts.enter_rank = 2
    settings.alerts.confirm_rounds = 1
    settings.alerts.confirm_with_live_queue = False
    settings.polling.verify_sample_size = 0
    Session = _setup(tmp_path)
    _seed_alert(Session, "A", "ALERTING", 2)
    nt = FakeNtfy()
    m = Monitor(Session, FakeYoupin(my_orders=[_my_order("A", rank="1", price="360")]), nt, settings,
                sleep=lambda s: None)

    m.apply_settings(_with_enter_rank(settings, 1))
    r = m.run_round()

    assert r.alerts_sent == 1 and len(nt.sent) == 1


def test_apply_settings_enter_rank_reaches_the_grouped_alert_title(tmp_path, settings):
    """复审 Minor：`_notify` 从 self.s 现读 enter_rank，改设置之后标题要跟着变。"""
    settings.alerts.enter_rank = 2
    settings.alerts.confirm_rounds = 1
    settings.alerts.confirm_with_live_queue = False
    settings.polling.verify_sample_size = 0
    Session = _setup(tmp_path)
    nt = FakeNtfy()
    orders = [_my_order("A", rank="1", price="360"), _my_order("B", rank="1", price="360")]
    m = Monitor(Session, FakeYoupin(my_orders=orders), nt, settings, sleep=lambda s: None)

    m.apply_settings(_with_enter_rank(settings, 1))
    m.run_round()

    assert [msg.title for msg in nt.sent] == ["2 张求购升到第 1 名"]



# ---- 成交推送 spec §4.3：交易中、求购成交 ----

def _local(dt):
    from zoneinfo import ZoneInfo
    return dt.astimezone(ZoneInfo("Asia/Shanghai"))


def _done_row(no, created=None, qty=1):
    # 默认 10 分钟前成交（悠悠「已完成」列表的 createTime 是本地时间）；写死日期的话，测试过了 24 小时的
    # 推送时间窗就会失败（2026-10-05 就这样挂过两条）
    created = created or _local(utcnow() - timedelta(minutes=10)).strftime("%Y.%m.%d %H:%M:%S")
    return MyPurchaseOrder(no, 1414, "h", "AK-47 | 红线 (久经沙场)", Decimal("358"), qty, 0, "0.15-0.18", -1,
                           None, None, {"status": 40, "quantity": qty, "buyQuantity": qty, "createTime": created})


def _fills_monitor(tmp_path, settings, yp, ntfy=None):
    Session = _setup(tmp_path)
    return Session, Monitor(Session, yp, FakeNtfy() if ntfy is None else ntfy, settings, sleep=lambda s: None)


def _titles(ntfy):
    return [m.title for m in ntfy.sent]


def test_in_trade_is_pushed_once_and_does_not_trigger_rank1(tmp_path, settings):
    yp = FakeYoupin(my_orders=[_my_order("A", rank="*", max_price="¥357")])
    ntfy = FakeNtfy()
    Session, m = _fills_monitor(tmp_path, settings, yp, ntfy)
    rep = m.run_round()
    assert _titles(ntfy) == ["交易中：AK-47 | 红线 (久经) [0.15-0.18]"]
    assert rep.alerts_sent == 0 and rep.fills_notified == 1
    m.run_round()                                            # 还在交易中：不再推
    assert len(ntfy.sent) == 1
    with Session() as s:
        o = s.get(MyBuyOrder, "A")
        assert o.in_trade_notice == "sent" and o.reported_rank is None
        logs = s.scalars(select(NotificationLog)).all()
        assert [(lg.fingerprint, lg.event, lg.rank, lg.status) for lg in logs] == [("A", "IN_TRADE", None, "sent")]


def test_in_trade_push_failure_retries_next_round(tmp_path, settings):
    yp = FakeYoupin(my_orders=[_my_order("A", rank="*")])
    broken = BrokenNtfy()
    Session, m = _fills_monitor(tmp_path, settings, yp, broken)
    rep = m.run_round()
    assert rep.notify_failed == 1
    with Session() as s:
        assert s.get(MyBuyOrder, "A").in_trade_notice == "failed"
    ntfy = FakeNtfy()
    m.ntfy = ntfy
    m.run_round()
    assert _titles(ntfy) == ["交易中：AK-47 | 红线 (久经) [0.15-0.18]"]


def test_fill_found_in_done_list_is_pushed(tmp_path, settings):
    yp = FakeYoupin(my_orders=[_my_order("A", rank="3")])
    ntfy = FakeNtfy()
    Session, m = _fills_monitor(tmp_path, settings, yp, ntfy)
    m.run_round()
    yp.my_orders = []                                        # A 消失
    yp.fill_pages = {40: [[_done_row("A")]]}
    rep = m.run_round()
    assert rep.fills_resolved == 1 and rep.fills_notified == 1
    assert _titles(ntfy) == ["求购成交：AK-47 | 红线 (久经) [0.15-0.18]"]
    assert "成交于 " in ntfy.sent[0].body and "晚了" not in ntfy.sent[0].body
    m.run_round()                                            # 推过了：不再推、也不再查
    assert len(ntfy.sent) == 1 and yp.fill_calls == [(40, 1)]


def test_fill_after_in_trade_push_is_not_pushed_again(tmp_path, settings):
    yp = FakeYoupin(my_orders=[_my_order("A", rank="*")])
    ntfy = FakeNtfy()
    Session, m = _fills_monitor(tmp_path, settings, yp, ntfy)
    m.run_round()
    yp.my_orders = []
    yp.fill_pages = {40: [[_done_row("A")]]}
    m.run_round()
    assert _titles(ntfy) == ["交易中：AK-47 | 红线 (久经) [0.15-0.18]"]     # 一次成交只推最早的一条
    with Session() as s:
        from skinpulse.db.models import OrderEvent
        ev = s.scalar(select(OrderEvent).where(OrderEvent.kind == "gone"))
        assert (ev.outcome, ev.notice) == ("done", "skipped")


def test_paused_order_is_not_pushed(tmp_path, settings):
    yp = FakeYoupin(my_orders=[_my_order("A", rank="3")])
    ntfy = FakeNtfy()
    Session, m = _fills_monitor(tmp_path, settings, yp, ntfy)
    m.run_round()
    yp.my_orders = []
    paused = _done_row("A")
    paused.raw.update(status=30, buyQuantity=0)
    yp.fill_pages = {30: [[paused]]}
    m.run_round()
    assert ntfy.sent == []


def test_in_trade_and_fill_in_one_round_are_two_messages(tmp_path, settings):
    yp = FakeYoupin(my_orders=[_my_order("A", rank="3"), _my_order("B", rank="4")])
    ntfy = FakeNtfy()
    Session, m = _fills_monitor(tmp_path, settings, yp, ntfy)
    m.run_round()
    yp.my_orders = [_my_order("B", rank="*")]
    yp.fill_pages = {40: [[_done_row("A")]]}
    m.run_round()
    assert sorted(_titles(ntfy)) == ["交易中：AK-47 | 红线 (久经) [0.15-0.18]", "求购成交：AK-47 | 红线 (久经) [0.15-0.18]"]


def test_fill_push_hits_the_hourly_cap_and_is_suppressed(tmp_path, settings):
    settings.alerts.hourly_cap = 1
    yp = FakeYoupin(my_orders=[_my_order("A", rank="3"), _my_order("B", rank="4")])
    ntfy = FakeNtfy()
    Session, m = _fills_monitor(tmp_path, settings, yp, ntfy)
    m.run_round()
    yp.my_orders = [_my_order("B", rank="*")]
    yp.fill_pages = {40: [[_done_row("A")]]}
    m.run_round()
    assert len(ntfy.sent) == 1                               # 交易中占掉了这一小时唯一的名额
    with Session() as s:
        from skinpulse.db.models import OrderEvent
        assert s.scalar(select(OrderEvent).where(OrderEvent.kind == "gone")).notice == "suppressed"


def test_stale_pending_fill_is_suppressed_not_pushed(tmp_path, settings, caplog):
    from skinpulse.db.models import OrderEvent

    ntfy = FakeNtfy()
    Session, m = _fills_monitor(tmp_path, settings, FakeYoupin(my_orders=[]), ntfy)
    old = utcnow() - timedelta(hours=30)
    with Session() as s:
        s.add(OrderEvent(purchase_no="A", template_id=1414, kind="filled", qty_before=2, qty_after=1,
                         price=Decimal("358"), at=old, notice="pending"))
        s.commit()
    with caplog.at_level(logging.WARNING):
        m.run_round()
    assert ntfy.sent == []
    with Session() as s:
        assert s.scalar(select(OrderEvent)).notice == "suppressed"
    assert "notify_window_h" in caplog.text


def test_resolve_risk_control_pauses_account_but_keeps_pass_a(tmp_path, settings):
    yp = FakeYoupin(my_orders=[_my_order("A", rank="3"), _my_order("B", rank="1", max_price="¥358")])
    ntfy = FakeNtfy()
    Session, m = _fills_monitor(tmp_path, settings, yp, ntfy)
    m.run_round()
    yp.my_orders = [_my_order("B", rank="1", max_price="¥358")]
    yp.fill_exc = RiskControl("CODE", "风控")
    rep = m.run_round()
    assert rep.errors == 1 and rep.checked == 1
    with Session() as s:
        assert s.get(SystemStatus, ACCOUNT).paused_until is not None
        assert s.scalar(select(RankSnapshot).where(RankSnapshot.purchase_no == "B").order_by(RankSnapshot.id.desc())).rank == 1


def test_fills_disabled_does_nothing(tmp_path, settings):
    settings.fills.enabled = False
    yp = FakeYoupin(my_orders=[_my_order("A", rank="*")])
    ntfy = FakeNtfy()
    Session, m = _fills_monitor(tmp_path, settings, yp, ntfy)
    m.run_round()
    yp.my_orders = []
    m.run_round()
    assert ntfy.sent == [] and yp.fill_calls == []


def test_unfinished_lookup_backs_off_for_an_hour(tmp_path, settings):
    """评审 2026-10-02：翻到页数上限还没找到的单，不能每轮都把两个列表翻满。"""
    settings.fills.max_pages = 1
    yp = FakeYoupin(my_orders=[_my_order("A", rank="3")])
    full = [_done_row(f"X{i}") for i in range(40)]
    Session, m = _fills_monitor(tmp_path, settings, yp)
    m.run_round()
    yp.my_orders = []
    yp.fill_pages = {40: [full, full], 30: [full, full]}
    m.run_round()
    assert yp.fill_calls == [(40, 1), (30, 1)]
    m.run_round()                                        # 一小时内不再查
    assert yp.fill_calls == [(40, 1), (30, 1)]
    m.now = lambda: utcnow() + timedelta(hours=1, minutes=1)
    m.run_round()
    assert yp.fill_calls == [(40, 1), (30, 1), (40, 1), (30, 1)]


def test_fill_pushed_long_after_it_happened_says_how_late(tmp_path, settings):
    """2026-10-05：旧代码在 10:37 记下消失，新代码 21:36 才推，正文却没写晚了多久。晚多少要量到推送这一刻。"""
    from skinpulse.db.models import OrderEvent

    ntfy = FakeNtfy()
    yp = FakeYoupin(my_orders=[])
    Session, m = _fills_monitor(tmp_path, settings, yp, ntfy)
    _seed_order(Session, "A", active=False, commodity_name="AK-47 | 红线 (久经沙场)")
    filled = utcnow() - timedelta(hours=11, minutes=2)
    with Session() as s:
        s.add(OrderEvent(purchase_no="A", template_id=1414, kind="gone", qty_before=1, price=Decimal("358"),
                         at=filled - timedelta(minutes=3)))
        s.commit()
    yp.fill_pages = {40: [[_done_row("A", created=_local(filled).strftime("%Y.%m.%d %H:%M:%S"))]]}
    m.run_round()
    assert len(ntfy.sent) == 1 and "晚了 11 小时 2 分钟" in ntfy.sent[0].body


def test_far_from_market_orders_are_not_live_confirmed_every_round(tmp_path, settings):
    """难以成交 spec §3：离市价太远的单到不了 ALERTING，不能每轮都拿去公开队列白查。"""
    settings.alerts.confirm_with_live_queue = True
    settings.alerts.enter_rank = 1
    settings.alerts.confirm_rounds = 1
    settings.polling.verify_sample_size = 0      # 只看告警前的实时核对，不看抽样复核
    Session = _setup(tmp_path)
    _seed_order(Session, "TEST-MINE-1", price=Decimal("10"), sell_min=Decimal("4871"), sell_depth=1)
    client = FakeYoupin(my_orders=[_my_order("TEST-MINE-1", rank="1", price="10", max_price="¥10")])
    m = Monitor(Session, client, FakeNtfy(), settings, sleep=lambda s: None)

    for _ in range(2):
        r = m.run_round()
        assert r.live_confirmed == 0 and r.market_gated == 1 and r.alerts_sent == 0
    assert client.verify_calls == 0


def test_in_trade_is_still_pushed_when_far_from_market(tmp_path, settings):
    """交易中、成交不看「难以成交」：有人卖给我了就要推。"""
    yp = FakeYoupin(my_orders=[_my_order("A", rank="*", price="10", max_price="¥10")])
    ntfy = FakeNtfy()
    Session = _setup(tmp_path)
    _seed_order(Session, "A", price=Decimal("10"), sell_min=Decimal("4871"), sell_depth=1)
    m = Monitor(Session, yp, ntfy, settings, sleep=lambda s: None)
    rep = m.run_round()
    assert rep.fills_notified == 1 and [t.title.startswith("交易中") for t in ntfy.sent] == [True]
