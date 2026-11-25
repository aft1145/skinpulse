import logging
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select

from skinpulse.adapters.youpin.models import MyPurchaseOrder, UserInfo
from skinpulse.db.models import Account, MyBuyOrder
from skinpulse.services.account import LoginLostNotice, notify_login_lost, sync_my_orders


class FakeClient:
    def __init__(self, orders):
        self.orders = orders
        self.token = "tok"

    def get_user_info(self):
        return UserInfo(99999999, "me")

    def search_my_purchase_orders(self, status=20):
        return self.orders


def _order(no, price="358", abrade="0.15-0.18"):
    return MyPurchaseOrder(no, 1414, "AK-47 | Redline (Field-Tested)", "AK-47 | 红线 (久经沙场)",
                           Decimal(price), 1, 1, abrade, -1, None, None, {})


def test_sync_upserts_and_deactivates(db_session):
    n = sync_my_orders(db_session, FakeClient([_order("A"), _order("B", abrade=None)]))
    assert n == 2
    acc = db_session.get(Account, 1)
    assert acc.user_id == 99999999 and acc.token_valid is True
    rows = {r.purchase_no: r for r in db_session.scalars(select(MyBuyOrder))}
    assert rows["A"].queue_key == "1414|0.15-0.18|-1|ANY|ANY"
    assert rows["B"].queue_key == "1414|ANY|-1|ANY|ANY"
    sync_my_orders(db_session, FakeClient([_order("A", price="360")]))
    rows = {r.purchase_no: r for r in db_session.scalars(select(MyBuyOrder))}
    assert rows["A"].price == Decimal("360") and rows["A"].active is True
    assert rows["B"].active is False


def test_order_leaving_the_list_does_not_stay_stuck_alerting(db_session):
    """求购单成交/到期后从悠悠的列表里消失时，它的告警状态必须退回 NORMAL。

    留着不管的话，`alert_state` 的 ALERTING 计数会随着成交一路虚高——而这个计数正是平时
    用来判断告警引擎有没有在正常工作的那个数。（库里真有过这样一条：一张 2026-09-07 成交
    的格洛克，到 09-21 还挂在 ALERTING。）"""
    from skinpulse.db.models import AlertStateRow
    from skinpulse.db.session import utcnow

    sync_my_orders(db_session, FakeClient([_order("A"), _order("B")]))
    db_session.add(AlertStateRow(purchase_no="B", state="ALERTING", streak=2, last_rank=1, updated_at=utcnow()))
    db_session.commit()

    sync_my_orders(db_session, FakeClient([_order("A")]))  # B 成交了，不再返回

    assert db_session.get(MyBuyOrder, "B").active is False
    state = db_session.get(AlertStateRow, "B")
    assert state.state == "NORMAL" and state.streak == 0


def test_still_listed_order_keeps_its_alerting_state(db_session):
    """只清消失的那些单。还在列表里、确实处于告警中的单不能被顺手重置——那会让
    confirm_rounds 重新计数，同一条告警再推一遍。"""
    from skinpulse.db.models import AlertStateRow
    from skinpulse.db.session import utcnow

    sync_my_orders(db_session, FakeClient([_order("A")]))
    db_session.add(AlertStateRow(purchase_no="A", state="ALERTING", streak=2, last_rank=1, updated_at=utcnow()))
    db_session.commit()

    sync_my_orders(db_session, FakeClient([_order("A")]))

    state = db_session.get(AlertStateRow, "A")
    assert state.state == "ALERTING" and state.streak == 2


def test_sync_login_required_marks_account(db_session):
    import pytest
    from skinpulse.adapters.youpin.errors import LoginRequired

    class Bad(FakeClient):
        def search_my_purchase_orders(self, status=20):
            raise LoginRequired("84101", "expired")

    with pytest.raises(LoginRequired):
        sync_my_orders(db_session, Bad([]))
    assert db_session.get(Account, 1).token_valid is False


# ---------------------------------------------------------------------------
# 2026-08-30: searchPurchaseOrderList's real field names are now verified against a
# live 371-order account (see MyPurchaseOrder's docstring) - but a real sync against
# the *old*, guessed mapping stored all 371 rows at price=0.00, because the guard below
# used to check only purchase_no/template_id, never price. A row whose purchase_no or
# template_id fails to parse must be skipped, not silently collapsed onto
# session.get(MyBuyOrder, "") (every "" row overwrites the same primary-key row) or
# stored with template_id=0 (burns a request per round on
# get_template_purchase_orders(0) and never ranks) - and neither must a row whose price
# is missing, unparseable, or <= 0: it can never be ranked either, which is exactly how
# 371 unusable rows looked like a successful sync.
# ---------------------------------------------------------------------------

def test_sync_skips_rows_with_missing_purchase_no_or_template_id(db_session, caplog):
    good = _order("A")
    missing_purchase_no = MyPurchaseOrder("", 1414, "h", "AK-47 | 红线机密数据", Decimal("100"), 1, 1,
                                          "0.15-0.18", -1, None, None, {"secret": "raw-row-data"})
    missing_template_id = MyPurchaseOrder("C", 0, "h", "AK-47 | 红线机密数据", Decimal("100"), 1, 1,
                                          "0.15-0.18", -1, None, None, {"secret": "raw-row-data"})

    with caplog.at_level(logging.WARNING, logger="skinpulse.services.account"):
        n = sync_my_orders(db_session, FakeClient([good, missing_purchase_no, missing_template_id]))

    assert n == 1  # only the good row was actually stored - not len(orders)
    rows = {r.purchase_no: r for r in db_session.scalars(select(MyBuyOrder))}
    assert set(rows) == {"A"}  # neither bad row overwrote or polluted the good one

    assert any("skip" in r.message.lower() for r in caplog.records)
    # must never log the raw API row - it may carry personal data
    assert "raw-row-data" not in caplog.text
    assert "红线机密数据" not in caplog.text


def test_sync_all_rows_unusable_stores_nothing_and_logs_loudly(db_session, caplog):
    missing_purchase_no = MyPurchaseOrder("", 1414, "h", "n", Decimal("100"), 1, 1, None, -1, None, None, {})
    missing_template_id = MyPurchaseOrder("X", 0, "h", "n", Decimal("100"), 1, 1, None, -1, None, None, {})

    with caplog.at_level(logging.WARNING, logger="skinpulse.services.account"):
        n = sync_my_orders(db_session, FakeClient([missing_purchase_no, missing_template_id]))

    assert n == 0
    assert db_session.scalar(select(MyBuyOrder)) is None
    # unmistakable in the log: at least one record at ERROR (not just WARNING) severity
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


# ---------------------------------------------------------------------------
# 2026-08-30 field-mapping fix: unitPrice (the real field, previously guessed as
# purchasePrice) never matched the old mapping, so from_api silently fell back to
# price=Decimal("0") for a whole live 371-order sync - the guard never checked price,
# so it stored anyway, and the sync reported "371 synced" while every row was
# unrankable. price<=0 (missing, unparseable, or a genuine "0") is now unusable, same
# as an empty purchase_no or a zero template_id.
# ---------------------------------------------------------------------------

def test_sync_skips_rows_with_zero_price(db_session, caplog):
    good = _order("A")
    zero_price = _order("Z", price="0")

    with caplog.at_level(logging.WARNING, logger="skinpulse.services.account"):
        n = sync_my_orders(db_session, FakeClient([good, zero_price]))

    assert n == 1  # only the good row was actually stored
    rows = {r.purchase_no: r for r in db_session.scalars(select(MyBuyOrder))}
    assert set(rows) == {"A"}
    assert any("skip" in r.message.lower() for r in caplog.records)


def test_sync_stores_reported_rank_and_top_bid(db_session):
    """YouPin hands back its own rank and current top bid directly on each my-orders
    row (`rank`, `maxPurchasePrice`) - three were hand-verified against the public
    queue and matched exactly. Not derived from anything else on MyPurchaseOrder, so
    sync_my_orders reads them straight off `.raw` and stores them for later use:
    Monitor.run_round's pass A now alerts directly from `reported_rank` (see
    services/monitor.py), with `top_bid` as that round's RankSnapshot.top1_price."""
    good = MyPurchaseOrder("A", 1414, "h", "n", Decimal("358"), 1, 1, None, -1, None, None,
                           {"rank": "22", "maxPurchasePrice": "¥28.1"})
    unparseable = MyPurchaseOrder("B", 1414, "h", "n", Decimal("358"), 1, 1, None, -1, None, None,
                                  {"rank": "not-a-number", "maxPurchasePrice": "n/a"})
    absent = MyPurchaseOrder("C", 1414, "h", "n", Decimal("358"), 1, 1, None, -1, None, None, {})

    sync_my_orders(db_session, FakeClient([good, unparseable, absent]))

    rows = {r.purchase_no: r for r in db_session.scalars(select(MyBuyOrder))}
    assert rows["A"].reported_rank == 22
    assert rows["A"].top_bid == Decimal("28.1")
    assert rows["B"].reported_rank is None
    assert rows["B"].top_bid is None
    assert rows["C"].reported_rank is None
    assert rows["C"].top_bid is None


# ---- notify_login_lost: shared by job_heartbeat and Monitor.run_round's pass A ----
# (moved here from test_scheduler.py once the function moved to this module, so it can
# be called without a scheduler<->monitor import cycle - see services/account.py)


class _TransportFailNtfy:
    def publish(self, msg):
        # A raw httpx transport error carries the full request URL, which embeds the
        # ntfy topic (the password on a public ntfy server) - unlike NtfyError, which
        # NtfyClient itself already redacts before raising.
        raise ConnectionError("Failed to establish a new connection: https://ntfy.sh/super-secret-topic-xyz")


class _NtfyErrorNtfy:
    def publish(self, msg):
        from skinpulse.adapters.ntfy.client import NtfyError

        raise NtfyError("ntfy 500: already redacted by NtfyClient")


def test_notify_login_lost_transport_failure_does_not_log_topic(settings, monkeypatch, caplog):
    import skinpulse.services.account as account_mod

    monkeypatch.setattr(account_mod, "_login_notice", LoginLostNotice())
    with caplog.at_level(logging.DEBUG, logger="skinpulse.services.account"):
        notify_login_lost(_TransportFailNtfy(), settings.ntfy)
    logged = caplog.text
    assert "super-secret-topic-xyz" not in logged
    assert "ConnectionError" in logged


def test_notify_login_lost_ntfy_error_logs_safely(settings, monkeypatch, caplog):
    import skinpulse.services.account as account_mod

    monkeypatch.setattr(account_mod, "_login_notice", LoginLostNotice())
    with caplog.at_level(logging.DEBUG, logger="skinpulse.services.account"):
        notify_login_lost(_NtfyErrorNtfy(), settings.ntfy)
    assert "ntfy 500" in caplog.text


class _RecordingNtfy:
    def __init__(self):
        self.sent = []

    def publish(self, msg):
        self.sent.append(msg)


def _fresh_notice(monkeypatch, start=1_000_000.0):
    import skinpulse.services.account as account_mod

    clock = [start]
    notice = LoginLostNotice(clock=lambda: clock[0])
    monkeypatch.setattr(account_mod, "_login_notice", notice)
    return notice, clock


def test_notify_login_lost_sends_at_once_then_every_24_hours(settings, monkeypatch):
    _, clock = _fresh_notice(monkeypatch)
    ntfy = _RecordingNtfy()
    notify_login_lost(ntfy, settings.ntfy)  # 失效的那一刻
    assert len(ntfy.sent) == 1
    clock[0] += 3600 * 23
    notify_login_lost(ntfy, settings.ntfy)  # 不到 24 小时：不再推
    assert len(ntfy.sent) == 1
    clock[0] += 3600
    notify_login_lost(ntfy, settings.ntfy)  # 满 24 小时：再推一条
    assert len(ntfy.sent) == 2
    notify_login_lost(ntfy, settings.ntfy)
    assert len(ntfy.sent) == 2


def test_notify_login_lost_failed_send_is_retried_next_time(settings, monkeypatch):
    _fresh_notice(monkeypatch)
    notify_login_lost(_NtfyErrorNtfy(), settings.ntfy)  # 没发出去，不算数
    ntfy = _RecordingNtfy()
    notify_login_lost(ntfy, settings.ntfy)
    assert len(ntfy.sent) == 1


def test_login_restored_rearms_the_notice(settings, monkeypatch):
    import skinpulse.services.account as account_mod

    _, clock = _fresh_notice(monkeypatch)
    ntfy = _RecordingNtfy()
    notify_login_lost(ntfy, settings.ntfy)
    account_mod.login_restored()  # 换了新 token
    clock[0] += 60
    notify_login_lost(ntfy, settings.ntfy)  # 新 token 又失效：立刻推
    assert len(ntfy.sent) == 2


def test_heartbeat_success_rearms_the_notice(settings, monkeypatch):
    import skinpulse.services.account as account_mod

    notice, _ = _fresh_notice(monkeypatch)
    notify_login_lost(_RecordingNtfy(), settings.ntfy)
    calls = []
    monkeypatch.setattr(notice, "reset", lambda: calls.append(1))

    class _Session:
        def commit(self):
            pass

    class _Client:
        def get_user_info(self):
            return None

    monkeypatch.setattr(account_mod, "_account", lambda s, account_id=1: SimpleNamespace(token_valid=False))
    assert account_mod.heartbeat(_Session(), _Client()) is True
    assert calls == [1]


def test_notify_login_lost_no_ntfy_client_is_noop(settings, monkeypatch):
    import skinpulse.services.account as account_mod

    monkeypatch.setattr(account_mod, "_login_notice", LoginLostNotice())
    notify_login_lost(None, settings.ntfy)  # must not raise


def _o(no, price="358", qty=1, remaining=1):
    return MyPurchaseOrder(no, 1414, "AK-47 | Redline (Field-Tested)", "AK-47 | 红线 (久经沙场)",
                           Decimal(price), qty, remaining, None, -1, None, None, {})


def _events(s):
    from skinpulse.db.models import OrderEvent
    return [(e.purchase_no, e.kind, e.qty_before, e.qty_after, e.price)
            for e in s.scalars(select(OrderEvent).order_by(OrderEvent.id))]


def test_sync_records_order_events(db_session):
    sync_my_orders(db_session, FakeClient([_o("A", qty=3, remaining=3), _o("B")]))
    assert _events(db_session) == [("A", "new", None, 3, Decimal("358")), ("B", "new", None, 1, Decimal("358"))]
    # 什么都没变：不记
    sync_my_orders(db_session, FakeClient([_o("A", qty=3, remaining=3), _o("B")]))
    assert len(_events(db_session)) == 2
    # A 成交 1 件、B 改价、C 新挂
    sync_my_orders(db_session, FakeClient([_o("A", qty=3, remaining=2), _o("B", price="360"), _o("C")]))
    assert _events(db_session)[2:] == [
        ("A", "filled", 3, 2, Decimal("358")),
        ("B", "repriced", 1, 1, Decimal("360")),
        ("C", "new", None, 1, Decimal("358")),
    ]
    # B 消失：分不清成交还是撤单，记 gone，带上最后已知的剩余数和价格
    sync_my_orders(db_session, FakeClient([_o("A", qty=3, remaining=2), _o("C")]))
    assert _events(db_session)[5:] == [("B", "gone", 1, None, Decimal("360"))]
    # B 又出现：同一个单号的旧行还在（只是不活跃），也算 new
    sync_my_orders(db_session, FakeClient([_o("A", qty=3, remaining=2), _o("B", price="360"), _o("C")]))
    assert _events(db_session)[6:] == [("B", "new", None, 1, Decimal("360"))]


def test_sync_filled_and_repriced_in_one_round(db_session):
    sync_my_orders(db_session, FakeClient([_o("A", qty=2, remaining=2)]))
    sync_my_orders(db_session, FakeClient([_o("A", price="300", qty=2, remaining=1)]))
    kinds = [e[1] for e in _events(db_session)]
    assert kinds == ["new", "filled", "repriced"]


def test_event_failure_never_breaks_sync(db_session, monkeypatch, caplog):
    import skinpulse.services.account as account

    def boom(*a, **k):
        raise RuntimeError("secret-ish detail")
    monkeypatch.setattr(account, "_order_events", boom)
    with caplog.at_level(logging.WARNING):
        assert sync_my_orders(db_session, FakeClient([_o("A")])) == 1
    assert db_session.get(MyBuyOrder, "A").active is True
    assert _events(db_session) == []
    assert "RuntimeError" in caplog.text and "secret-ish detail" not in caplog.text


def test_lowering_the_quantity_is_not_a_fill(db_session):
    """用户在悠悠里把 3 件改成 2 件：qty 和 remaining_qty 一起少 1，那不是成交（评审 2026-09-28）。"""
    sync_my_orders(db_session, FakeClient([_o("A", qty=3, remaining=3)]))
    sync_my_orders(db_session, FakeClient([_o("A", qty=2, remaining=2)]))
    assert _events(db_session)[1:] == [("A", "resized", 3, 2, Decimal("358"))]
    # 加件数不算成交；之后真成交了 1 件才记 filled
    sync_my_orders(db_session, FakeClient([_o("A", qty=4, remaining=4)]))       # 加到 4 件
    sync_my_orders(db_session, FakeClient([_o("A", qty=4, remaining=3)]))       # 成交 1
    kinds = [e[1] for e in _events(db_session)]
    assert kinds == ["new", "resized", "resized", "filled"]


def test_resize_and_fill_in_the_same_round(db_session):
    sync_my_orders(db_session, FakeClient([_o("A", qty=3, remaining=3)]))
    sync_my_orders(db_session, FakeClient([_o("A", qty=2, remaining=1)]))      # 减 1 件，同时成交 1 件
    assert _events(db_session)[1:] == [("A", "resized", 3, 2, Decimal("358")), ("A", "filled", 3, 2, Decimal("358"))]


# ---- 成交推送 spec §4.1：交易中（rank = "*"）和成交事件的 notice ----

def _r(no, rank, qty=1, remaining=1):
    o = _o(no, qty=qty, remaining=remaining)
    o.raw["rank"] = rank
    return o


def _notices(s):
    from skinpulse.db.models import OrderEvent
    return [(e.purchase_no, e.kind, e.notice) for e in s.scalars(select(OrderEvent).order_by(OrderEvent.id))]


def test_in_trade_starts_persists_and_clears(db_session):
    import skinpulse.services.account as account
    from datetime import timedelta

    from skinpulse.db.session import utcnow

    t0 = utcnow()
    clock = iter([t0, t0 + timedelta(minutes=5), t0 + timedelta(minutes=10)])
    orig = account.utcnow
    account.utcnow = lambda: next(clock)
    try:
        sync_my_orders(db_session, FakeClient([_r("A", "*")]))
        row = db_session.get(MyBuyOrder, "A")
        assert row.in_trade_since == t0 and row.in_trade_notice is None and row.reported_rank is None
        row.in_trade_notice = "sent"
        db_session.commit()
        sync_my_orders(db_session, FakeClient([_r("A", " * ")]))         # 还在交易中：开始时间、推送状态都不动
        row = db_session.get(MyBuyOrder, "A")
        assert row.in_trade_since == t0 and row.in_trade_notice == "sent"
        sync_my_orders(db_session, FakeClient([_r("A", "2")]))           # 回到数字名次：清空
        row = db_session.get(MyBuyOrder, "A")
        assert (row.in_trade_since, row.in_trade_notice, row.reported_rank) == (None, None, 2)
    finally:
        account.utcnow = orig


def test_partial_fill_notice_depends_on_in_trade_push(db_session):
    # 没推过交易中：部分成交要推（pending）
    sync_my_orders(db_session, FakeClient([_r("A", "3", qty=3, remaining=3)]))
    sync_my_orders(db_session, FakeClient([_r("A", "3", qty=3, remaining=2)]))
    # 交易中已经推出去了：这次成交不再推（skipped）
    sync_my_orders(db_session, FakeClient([_r("A", "*", qty=3, remaining=2)]))
    row = db_session.get(MyBuyOrder, "A")
    row.in_trade_notice = "sent"
    db_session.commit()
    sync_my_orders(db_session, FakeClient([_r("A", "2", qty=3, remaining=1)]))
    assert _notices(db_session) == [("A", "new", None), ("A", "filled", "pending"), ("A", "filled", "skipped")]
    assert db_session.get(MyBuyOrder, "A").in_trade_since is None


def test_gone_while_in_trade_marks_notice_and_clears(db_session):
    sync_my_orders(db_session, FakeClient([_r("A", "*"), _r("B", "*"), _r("C", "4")]))
    db_session.get(MyBuyOrder, "A").in_trade_notice = "sent"        # A 的交易中推出去了
    db_session.get(MyBuyOrder, "B").in_trade_notice = "failed"      # B 的没推出去
    db_session.commit()
    sync_my_orders(db_session, FakeClient([]))
    assert _notices(db_session)[3:] == [("A", "gone", "skipped"), ("B", "gone", None), ("C", "gone", None)]
    for no in "ABC":
        row = db_session.get(MyBuyOrder, no)
        assert (row.active, row.in_trade_since, row.in_trade_notice) == (False, None, None)


def test_reappearing_order_starts_a_new_in_trade_episode(db_session):
    sync_my_orders(db_session, FakeClient([_r("A", "*")]))
    db_session.get(MyBuyOrder, "A").in_trade_notice = "sent"
    db_session.commit()
    sync_my_orders(db_session, FakeClient([]))                  # 消失
    sync_my_orders(db_session, FakeClient([_r("A", "*")]))      # 又出现、又在交易中：新的一段，要再推
    row = db_session.get(MyBuyOrder, "A")
    assert row.in_trade_since is not None and row.in_trade_notice is None


def test_other_non_numeric_rank_warns_once_and_is_not_in_trade(db_session, caplog, monkeypatch):
    import skinpulse.services.account as account

    monkeypatch.setattr(account, "_reported_odd_ranks", set())
    with caplog.at_level(logging.WARNING):
        sync_my_orders(db_session, FakeClient([_r("A", "排队中很长很长的文字"), _r("B", "--"), _r("C", None)]))
    assert db_session.get(MyBuyOrder, "A").in_trade_since is None
    warns = [r for r in caplog.records if "rank" in r.getMessage()]
    assert len(warns) == 1 and "2 order(s)" in warns[0].getMessage() and "排队中很长很长" in warns[0].getMessage()
    assert "排队中很长很长的文字" not in warns[0].getMessage()     # 只记前 8 个字符
    caplog.clear()
    with caplog.at_level(logging.WARNING):                    # 同样的值下一轮不再报
        sync_my_orders(db_session, FakeClient([_r("A", "排队中很长很长的文字"), _r("B", "--")]))
    assert not [r for r in caplog.records if "rank" in r.getMessage()]


# ---- 改价时旧单短暂显示 *（intent 2026-11-23）：createTime 没变就不算交易中 ----

def _rc(no, rank, created):
    o = _r(no, rank)
    o.raw["createTime"] = created
    return o


def test_star_with_unchanged_create_time_is_a_reprice_not_in_trade(db_session, caplog):
    sync_my_orders(db_session, FakeClient([_rc("A", "3", "2026.11.23 14:18:01")]))
    with caplog.at_level(logging.INFO):
        sync_my_orders(db_session, FakeClient([_rc("A", "*", "2026.11.23 14:18:01")]))
    row = db_session.get(MyBuyOrder, "A")
    assert row.in_trade_since is None and row.reported_rank is None
    assert "createTime unchanged" in caplog.text
    # 再下一轮还是 * 而且 createTime 还是没变：仍然不算
    sync_my_orders(db_session, FakeClient([_rc("A", "*", "2026.11.23 14:18:01")]))
    assert db_session.get(MyBuyOrder, "A").in_trade_since is None
    # createTime 变了（被卖的时刻）：交易中
    sync_my_orders(db_session, FakeClient([_rc("A", "*", "2026.11.23 16:29:37")]))
    assert db_session.get(MyBuyOrder, "A").in_trade_since is not None


def test_star_with_new_create_time_is_in_trade(db_session):
    sync_my_orders(db_session, FakeClient([_rc("A", "4", "2026.11.23 12:26:56")]))
    sync_my_orders(db_session, FakeClient([_rc("A", "*", "2026.11.23 16:29:37")]))
    assert db_session.get(MyBuyOrder, "A").in_trade_since is not None


def test_first_seen_star_counts_as_in_trade(db_session):
    # 建单 5 分钟内就被卖：没有上一次可比，照旧算交易中
    sync_my_orders(db_session, FakeClient([_rc("A", "*", "2026.11.23 16:29:37")]))
    assert db_session.get(MyBuyOrder, "A").in_trade_since is not None


def test_gone_event_records_when_the_order_was_in_trade(db_session):
    from skinpulse.db.models import OrderEvent

    sync_my_orders(db_session, FakeClient([_rc("A", "4", "2026.11.23 12:26:56"), _rc("B", "4", "2026.11.23 12:00:00")]))
    sync_my_orders(db_session, FakeClient([_rc("A", "*", "2026.11.23 16:29:37"), _rc("B", "4", "2026.11.23 12:00:00")]))
    since = db_session.get(MyBuyOrder, "A").in_trade_since
    sync_my_orders(db_session, FakeClient([]))
    gone = {e.purchase_no: e for e in db_session.scalars(select(OrderEvent).where(OrderEvent.kind == "gone"))}
    assert gone["A"].in_trade_at == since and gone["B"].in_trade_at is None
