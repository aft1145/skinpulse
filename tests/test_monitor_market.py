"""市价提醒（市价提醒 spec §2）：Monitor 一轮里的候选 → 实时核对 → 推送 → 一段只推一次。"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal as D

from sqlalchemy import select

from skinpulse.adapters.youpin.errors import RiskControl
from skinpulse.adapters.youpin.models import OnSaleItem
from skinpulse.db.models import MyBuyOrder, NotificationLog
from skinpulse.services.monitor import Monitor
from skinpulse.db.session import utcnow
from tests.test_services_monitor import FakeNtfy, FakeYoupin, _my_order, _setup

TID = 108625


def hideout(price="1.10", rank="4", top="¥1.30", no="H", tid=TID, abrade=None):
    o = _my_order(no, template_id=tid, price=price, abrade=abrade, rank=rank, max_price=top)
    return replace(o, commodity_name="双持贝瑞塔 | 藏身处 (崭新出厂)")


def on_sale(*prices):
    return [OnSaleItem(f"c{i}", 1, D(p), "", False) for i, p in enumerate(prices)]


class FailingNtfy(FakeNtfy):
    def publish(self, msg):
        raise RuntimeError("https://ntfy.sh/SECRET-TOPIC")


def setup(tmp_path, settings, orders, sale, ntfy=None, stale_sell="1.17"):
    settings.polling.verify_sample_size = 0
    settings.polling.sell_sample_size = 0
    Session = _setup(tmp_path)
    yp = FakeYoupin(my_orders=orders, on_sale={})
    m = Monitor(Session, yp, ntfy or FakeNtfy(), settings, sleep=lambda s: None)
    m.run_round()                         # 第一轮：建行；第 2～5 名会被「探一下」，这时没人在卖，不提醒
    with Session() as s:
        for o in s.scalars(select(MyBuyOrder)):
            o.sell_min, o.sell_depth = D(stale_sell), 10    # 市场价刷新留下的值（刚查过，不算旧）
            o.sell_checked_at = utcnow()
        s.commit()
    yp.on_sale, yp.sell_calls = sale, 0
    return Session, yp, m


def logs(Session):
    with Session() as s:
        return [(lg.event, lg.status) for lg in s.scalars(select(NotificationLog).where(NotificationLog.event == "MARKET"))]


def test_hideout_case_pushes_once_with_both_flags(tmp_path, settings):
    Session, yp, m = setup(tmp_path, settings, [hideout()], {TID: on_sale("1.17", "1.20")})
    nt = m.ntfy
    r = m.run_round()
    assert r.market_checked == 1 and r.market_notified == 1 and yp.sell_calls == 1
    msg = nt.sent[-1]
    assert msg.title.startswith("榜首高于在售：双持贝瑞塔 | 藏身处") and "我的价是在售的 94%" in msg.body
    with Session() as s:
        o = s.get(MyBuyOrder, "H")
        assert (o.market_flag, o.market_notice, o.sell_min) == ("both", "sent", D("1.17"))
    r = m.run_round()                     # 同一段：不再核对、不再推
    assert r.market_notified == 0 and yp.sell_calls == 1 and logs(Session) == [("MARKET", "sent")]


def test_lowering_my_price_drops_near_without_a_new_push(tmp_path, settings):
    Session, yp, m = setup(tmp_path, settings, [hideout(rank="2")], {TID: on_sale("1.17")})
    m.run_round()
    yp.my_orders = [hideout(price="0.90", rank="2")]  # 第 2 名改到 0.90：倒挂照样算（前三名一律提醒）
    r = m.run_round()
    assert r.market_notified == 0
    with Session() as s:
        o = s.get(MyBuyOrder, "H")
        assert (o.market_flag, o.market_notice) == ("inverted", "sent")   # 倒挂还在，这一段继续


def test_episode_ends_when_nothing_holds_then_re_arms(tmp_path, settings):
    Session, yp, m = setup(tmp_path, settings, [hideout()], {TID: on_sale("1.17")})
    m.run_round()
    yp.my_orders = [hideout(price="0.90", top="¥1.00")]   # 榜首回到在售之下、我的价也远了
    m.run_round()
    with Session() as s:
        assert s.get(MyBuyOrder, "H").market_notice is None
    yp.my_orders = [hideout(price="0.90", top="¥1.30", rank="3")]   # 又倒挂、进了前三：新的一段，再推
    r = m.run_round()
    assert r.market_notified == 1 and len(logs(Session)) == 2


def test_live_listing_overrules_a_stale_one(tmp_path, settings):
    # 库里的在售价 1.17 是旧的，实时已经 1.50：既不倒挂、也不贴近 → 不推
    Session, yp, m = setup(tmp_path, settings, [hideout()], {TID: on_sale("1.50")})
    r = m.run_round()
    assert r.market_checked == 1 and r.market_notified == 0 and m.ntfy.sent == []
    with Session() as s:
        o = s.get(MyBuyOrder, "H")
        assert o.market_flag is None and o.sell_min == D("1.50")


def test_nobody_selling_now_is_not_an_alert(tmp_path, settings):
    Session, yp, m = setup(tmp_path, settings, [hideout()], {TID: []})
    assert m.run_round().market_notified == 0
    with Session() as s:
        assert s.get(MyBuyOrder, "H").sell_depth == 0


def test_failed_push_retries_next_round(tmp_path, settings):
    Session, yp, m = setup(tmp_path, settings, [hideout()], {TID: on_sale("1.17")}, ntfy=FailingNtfy())
    r = m.run_round()
    assert r.market_notified == 0
    with Session() as s:
        assert s.get(MyBuyOrder, "H").market_notice == "failed"
    m.ntfy = FakeNtfy()
    assert m.run_round().market_notified == 1


def test_verify_max_caps_live_checks_and_rank_order_wins(tmp_path, settings):
    settings.market_alerts.verify_max = 2
    orders = [hideout(no=f"O{i}", tid=TID + i, rank=str(r)) for i, r in enumerate((5, 2, 3, 4))]
    sale = {TID + i: on_sale("1.17") for i in range(4)}
    Session, yp, m = setup(tmp_path, settings, orders, sale)
    r = m.run_round()
    assert yp.sell_calls == 2 and r.market_notified == 2
    with Session() as s:
        sent = {o.purchase_no for o in s.scalars(select(MyBuyOrder).where(MyBuyOrder.market_notice == "sent"))}
    assert sent == {"O1", "O2"}                     # 第 2、第 3 名先核对
    r = m.run_round()                               # 剩下的下一轮核
    assert r.market_notified == 2 and yp.sell_calls == 4


def test_conditional_and_disabled_are_skipped(tmp_path, settings):
    Session, yp, m = setup(tmp_path, settings, [hideout(abrade="0.00-0.07")], {TID: on_sale("1.17")})
    assert m.run_round().market_checked == 0
    settings.market_alerts.enabled = False
    Session, yp, m = setup(tmp_path / "b", settings, [hideout()], {TID: on_sale("1.17")})
    assert m.run_round().market_checked == 0 and yp.sell_calls == 0


def test_risk_control_on_the_check_pauses_the_anon_channel(tmp_path, settings):
    Session, yp, m = setup(tmp_path, settings, [hideout()], {TID: on_sale("1.17")})
    yp.sell_exc = RiskControl("1001", "风控")
    r = m.run_round()
    assert r.market_notified == 0 and r.errors >= 1
    with Session() as s:
        assert s.get(MyBuyOrder, "H").market_notice is None    # 没核到：下一轮再来


def test_stale_listing_in_band_is_probed_and_new_inversion_caught(tmp_path, settings):
    # 库里的在售价是 3 小时前的 1.40（看不出倒挂），实时已经跌到 1.17 < 榜首 1.30：探一下就抓到
    from datetime import timedelta
    Session, yp, m = setup(tmp_path, settings, [hideout(price="0.80", rank="2")], {TID: on_sale("1.17")}, stale_sell="1.40")
    with Session() as s:
        s.get(MyBuyOrder, "H").sell_checked_at = utcnow() - timedelta(hours=3)
        s.commit()
    r = m.run_round()
    assert r.market_checked == 1 and r.market_notified == 1
    with Session() as s:
        assert s.get(MyBuyOrder, "H").market_flag == "inverted"
    assert m.run_round().market_checked == 0       # 刚核过、这一段推过：不再探


def test_cap_suppressed_push_is_retried(tmp_path, settings):
    Session, yp, m = setup(tmp_path, settings, [hideout()], {TID: on_sale("1.17")})
    m.hourly_cap.allow = lambda n: False
    m.run_round()
    with Session() as s:
        assert s.get(MyBuyOrder, "H").market_notice == "suppressed"
    m.hourly_cap.allow = lambda n: True
    assert m.run_round().market_notified == 1


def test_unexpected_error_skips_only_that_order(tmp_path, settings):
    orders = [hideout(no="A", tid=TID, rank="2"), hideout(no="B", tid=TID + 1, rank="3")]
    Session, yp, m = setup(tmp_path, settings, orders, {TID: on_sale("1.17"), TID + 1: on_sale("1.17")})
    real = yp.get_on_sale

    def flaky(template_id, page_size=10):
        if template_id == TID:
            raise ConnectionError("https://api.youpin898.com/x?uk=SECRET")
        return real(template_id, page_size)
    yp.get_on_sale = flaky
    r = m.run_round()
    assert r.errors >= 1 and r.market_notified == 1
    with Session() as s:
        assert s.get(MyBuyOrder, "B").market_notice == "sent" and s.get(MyBuyOrder, "A").market_notice is None


def test_rank_4_needs_the_ratio(tmp_path, settings):
    # 第 4 名、我出 0.90、在售 1.17（77%）：倒挂但离市价远，不提醒；出 1.00（85% 以上）才提醒
    Session, yp, m = setup(tmp_path, settings, [hideout(price="0.90")], {TID: on_sale("1.17")})
    assert m.run_round().market_notified == 0
    yp.my_orders = [hideout(price="1.00")]
    assert m.run_round().market_notified == 1
