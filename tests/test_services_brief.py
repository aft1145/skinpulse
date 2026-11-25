import json
from datetime import date, datetime, timezone
from decimal import Decimal as D

from skinpulse.db.models import BriefLog, MyBuyOrder, PriceDaily, RankSnapshot, SystemStatus
from skinpulse.db.session import utcnow
from skinpulse.services.brief import load_top_table, plan_brief, run_daily_brief
from skinpulse.services.notifier import AllChannelsFailed, ChannelResult

DAY = date(2026, 9, 22)                     # 本地 2026-09-22 == UTC 09-21 16:00 ~ 09-22 16:00
NOW = datetime(2026, 9, 22, 16, 0, 5, tzinfo=timezone.utc)


def utc(d, h, m=0):
    return datetime(2026, 9, d, h, m, tzinfo=timezone.utc)


def add_order(s, no, tid=1, price="100", active=True, name=None):
    s.add(MyBuyOrder(purchase_no=no, template_id=tid, commodity_name=name or f"item {no}", price=D(price), qty=1,
                     remaining_qty=1, type_id=-1, queue_key=f"{tid}|ANY|-1|ANY|ANY", synced_at=utcnow(),
                     active=active))


def add_snap(s, no, ts, rank, top="110"):
    s.add(RankSnapshot(purchase_no=no, ts=ts, rank=rank, beyond=None, top1_price=D(top), top2_price=None,
                       top1_qty=None, sample_size=0))


class FakeNotifier:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def publish(self, msg):
        self.sent.append(msg)
        if self.fail:
            raise AllChannelsFailed([ChannelResult("pushplus", RuntimeError("x"))])
        return [ChannelResult("pushplus")]


def seed_entry(s):
    add_order(s, "A")
    add_snap(s, "A", utc(20, 10), 5)       # 两天前（开盘值要靠「当天之前最后一条」查出来）
    add_snap(s, "A", utc(22, 1), 2)        # 本地 09-22 09:00 进前 2
    add_order(s, "B")
    add_snap(s, "B", utc(22, 2), 7)
    s.commit()


def test_entry_sends_and_records(db_session, settings):
    seed_entry(db_session)
    n = FakeNotifier()
    row = run_daily_brief(db_session, n, settings, DAY, now=NOW)
    assert row.decision == "sent" and row.sent_at == NOW
    assert n.sent[0].title == "SkinPulse 简报 09-22：前 2 共 1 张，1 张有变动"
    assert n.sent[0].html and "<table" in n.sent[0].html          # PushPlus 走 html 表格
    assert row.body == n.sent[0].body == ("第2名 | item A | 求购 100 | 榜首 110 | 在售 — | 🆕新进\n"
                                          "在售：09-22 没有价格快照")
    assert json.loads(row.reasons_json)["new"] == 1
    assert db_session.get(SystemStatus, "pushplus").last_ok_at is not None


def test_same_day_is_not_sent_twice_unless_forced(db_session, settings):
    seed_entry(db_session)
    n = FakeNotifier()
    run_daily_brief(db_session, n, settings, DAY, now=NOW)
    run_daily_brief(db_session, n, settings, DAY, now=NOW)
    assert len(n.sent) == 1
    run_daily_brief(db_session, n, settings, DAY, now=NOW, force=True)
    assert len(n.sent) == 2 and db_session.query(BriefLog).count() == 1


def test_nothing_entered_is_skipped_without_pushing(db_session, settings):
    add_order(db_session, "B")
    add_snap(db_session, "B", utc(22, 2), 7)
    db_session.commit()
    n = FakeNotifier()
    row = run_daily_brief(db_session, n, settings, DAY, now=NOW)
    assert row.decision == "skipped" and n.sent == [] and row.body is None


def test_top_row_without_change_is_skipped(db_session, settings):
    add_order(db_session, "A")
    add_snap(db_session, "A", utc(20, 10), 2)
    add_snap(db_session, "A", utc(22, 1), 2)
    db_session.commit()
    n = FakeNotifier()
    row = run_daily_brief(db_session, n, settings, DAY, now=NOW)
    assert row.decision == "skipped" and n.sent == []


def test_table_uses_brief_top_rank_not_alert_enter_rank(db_session, settings):
    """实时告警改成只推第 1 名（enter_rank = 1）之后，简报照样列前 2（ADR-0008）。"""
    settings.alerts.enter_rank = 1
    seed_entry(db_session)
    assert [r.purchase_no for r in load_top_table(db_session, settings, DAY).rows] == ["A"]
    settings.brief.top_rank = 1
    assert load_top_table(db_session, settings, DAY).rows == []


def test_opening_skips_unranked_snapshots_before_the_day(db_session, settings):
    """开盘子查询只看 rank 不为空的快照：D 之前最后一条是 None 时，往前取到第 5 名那条。"""
    add_order(db_session, "A")
    add_snap(db_session, "A", utc(21, 11), 5)      # 本地 09-21 19:00（D 前 5 小时）
    db_session.add(RankSnapshot(purchase_no="A", ts=utc(21, 15), rank=None, beyond=None, top1_price=None,
                                top2_price=None, top1_qty=None, sample_size=0))   # D 前 1 小时，排名未知
    add_snap(db_session, "A", utc(22, 1), 2)
    db_session.commit()
    r = load_top_table(db_session, settings, DAY).rows[0]
    assert r.open_rank == 5 and r.is_new


def test_no_snapshots_is_no_data(db_session, settings):
    add_order(db_session, "B")
    db_session.commit()
    assert run_daily_brief(db_session, FakeNotifier(), settings, DAY, now=NOW).decision == "no_data"


def test_no_channel_and_failed_delivery_are_recorded(db_session, settings):
    seed_entry(db_session)
    assert run_daily_brief(db_session, None, settings, DAY, now=NOW).decision == "no_channel"
    row = run_daily_brief(db_session, FakeNotifier(fail=True), settings, DAY, now=NOW, force=True)
    assert row.decision == "failed" and row.sent_at is None
    assert "pushplus" in json.loads(row.reasons_json)["delivery"]
    assert db_session.get(SystemStatus, "pushplus").last_error_code == "publish_failed"


def test_quiet_note_after_seven_days(db_session, settings):
    add_order(db_session, "B")
    add_snap(db_session, "B", utc(22, 2), 7)
    db_session.add(BriefLog(date="2026-09-15", decision="sent", reasons_json="{}", title="t", body="b",
                            created_at=NOW, sent_at=NOW))
    db_session.commit()
    n = FakeNotifier()
    row = run_daily_brief(db_session, n, settings, DAY, now=NOW)
    assert row.decision == "quiet_sent" and n.sent[0].title == "SkinPulse：近 7 天前 2 名单没有变动"


def test_sell_price_comes_from_youpin_snapshot_of_that_day(db_session, settings):
    add_order(db_session, "A", tid=1)
    add_snap(db_session, "A", utc(22, 1), 2)
    add_order(db_session, "B", tid=2)
    add_snap(db_session, "B", utc(22, 1), 2)
    add_order(db_session, "C", tid=3)
    add_snap(db_session, "C", utc(22, 1), 2)
    db_session.add_all([
        _price_row("2026-09-22", tid=1, sell="52"),
        _price_row("2026-09-22", tid=1, sell="40", platform="BUFF"),     # 别的平台不算
        _price_row("2026-09-21", tid=2, sell="60"),                      # 前一天的不算
        _price_row("2026-09-22", tid=3, sell=None),                      # 悠悠那天没人挂
    ])
    db_session.commit()
    t = load_top_table(db_session, settings, DAY)
    assert {r.purchase_no: r.sell_price for r in t.rows} == {"A": D("52"), "B": None, "C": None}
    body = plan_brief(db_session, settings, DAY).full_body
    assert "item A | 求购 100 | 榜首 110 | 在售 52 |" in body
    assert body.count("在售 —") == 2 and "在售 0" not in body


def test_archive_keeps_the_full_text_when_the_push_is_cut(db_session, settings):
    for i in range(80):
        no = f"N{i:02d}"
        add_order(db_session, no, tid=i, name=f"很长很长很长很长的饰品名字 {i:02d}")
        add_snap(db_session, no, utc(22, 1), 2)
    db_session.commit()
    n = FakeNotifier()
    row = run_daily_brief(db_session, n, settings, DAY, now=NOW)
    pushed = n.sent[0].body
    assert len(pushed.encode("utf-8")) <= 4000 and "未列出" in pushed
    assert "未列出" not in row.body and len(row.body.splitlines()) == 81     # 80 行 + 在售脚注
    assert pushed.splitlines()[-1] == row.body.splitlines()[-1] == "在售：09-22 没有价格快照"


def test_plan_brief_is_read_only(db_session, settings):
    seed_entry(db_session)
    plan = plan_brief(db_session, settings, DAY)
    assert plan.decision.decision == "sent" and plan.push is not None
    assert db_session.query(BriefLog).count() == 0


def test_failed_push_logs_warning(db_session, settings, caplog):
    import logging

    seed_entry(db_session)
    with caplog.at_level(logging.WARNING, logger="skinpulse.services.brief"):
        row = run_daily_brief(db_session, FakeNotifier(fail=True), settings, DAY, now=NOW)
    assert row.decision == "failed"
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert warnings and any("pushplus" in m for m in warnings)


def test_rerun_after_failed_without_force_returns_stored_row_and_does_not_push_again(db_session, settings):
    seed_entry(db_session)
    n = FakeNotifier(fail=True)
    row1 = run_daily_brief(db_session, n, settings, DAY, now=NOW)
    assert row1.decision == "failed" and len(n.sent) == 1
    row2 = run_daily_brief(db_session, n, settings, DAY, now=NOW)
    assert row2.decision == "failed" and row2.date == row1.date
    assert len(n.sent) == 1                     # 没有重发


def test_local_day_boundary_matches_settings_timezone(db_session, settings):
    """本地 2026-09-22（Asia/Shanghai，UTC+8）的日界是 [09-21 16:00, 09-22 16:00) UTC。
    09-21 17:00 UTC（本地 09-22 01:00）算这一天；09-22 17:00 UTC（本地 09-23 01:00）不算。"""
    add_order(db_session, "A")
    add_snap(db_session, "A", datetime(2026, 9, 21, 17, 0, tzinfo=timezone.utc), 1)
    add_order(db_session, "B")
    add_snap(db_session, "B", datetime(2026, 9, 22, 17, 0, tzinfo=timezone.utc), 1)
    db_session.commit()
    t = load_top_table(db_session, settings, DAY)
    assert t.orders_with_data == 1
    assert [r.purchase_no for r in t.rows] == ["A"]


def _price_row(d, tid=1, sell="100", platform="YOUPIN", fetched_at=NOW):
    return PriceDaily(date=d, template_id=tid, market_hash_name=f"h{tid}", platform=platform,
                      sell_price=D(sell) if sell else None, sell_count=1, bidding_price=None, bidding_count=0,
                      update_time=None, fetched_at=fetched_at)


def test_sell_note_shows_when_the_youpin_snapshot_was_taken(db_session, settings):
    """复审 F2：白天手动跑过一次 price-snapshot，23:50 那次就全跳过了——在售列其实是 10:01 的价格，
    表下必须写出来，不能看着像 23:50 的。"""
    seed_entry(db_session)
    db_session.add_all([
        _price_row("2026-09-22", tid=1, sell="52", fetched_at=utc(22, 2, 1)),    # 本地 10:01
        _price_row("2026-09-22", tid=2, sell="53", fetched_at=utc(22, 1, 59)),   # 本地 09:59
        _price_row("2026-09-22", tid=3, platform="BUFF", fetched_at=utc(22, 15, 50)),   # 别的平台不算
        _price_row("2026-09-21", tid=4, fetched_at=utc(21, 15, 50)),             # 前一天的不算
    ])
    db_session.commit()
    plan = plan_brief(db_session, settings, DAY)
    assert plan.full_body.splitlines()[-1] == "在售：09-22 10:01 悠悠价格快照"
    assert plan.push.body.splitlines()[-1] == "在售：09-22 10:01 悠悠价格快照"
    assert plan.push.html.endswith("</table><p>在售：09-22 10:01 悠悠价格快照</p>")


def test_sell_note_says_so_when_that_day_has_no_youpin_snapshot(db_session, settings):
    seed_entry(db_session)
    db_session.add(_price_row("2026-09-22", tid=1, platform="BUFF", fetched_at=utc(22, 15, 50)))
    db_session.commit()
    plan = plan_brief(db_session, settings, DAY)
    assert plan.full_body.splitlines()[-1] == "在售：09-22 没有价格快照"
    assert plan.push.html.endswith("</table><p>在售：09-22 没有价格快照</p>")


def test_brief_log_is_committed_before_channel_status_and_survives_its_failure(db_session, settings, monkeypatch,
                                                                              caplog):
    """推送成功之后最要紧的是记下「这天发过了」：状态表写失败（database is locked）不能把 brief_log 带走，
    否则启动补发会再发一次。"""
    import logging

    import skinpulse.services.brief as svc

    seed_entry(db_session)
    seen = []

    def locked(session, name):
        seen.append(session.get(BriefLog, "2026-09-22") is not None)
        raise RuntimeError("database is locked")

    monkeypatch.setattr(svc.st, "mark_ok", locked)
    with caplog.at_level(logging.WARNING, logger="skinpulse.services.brief"):
        row = run_daily_brief(db_session, FakeNotifier(), settings, DAY, now=NOW)   # must not raise
    assert seen == [True]
    assert row.decision == "sent" and row.sent_at == NOW
    db_session.close()
    assert row.decision == "sent" and row.body              # 离开 session 后仍可读（CLI 在 with 块外读它）
    with db_session.bind.connect() as c:                  # 另一条连接也能看到：真的提交了
        from sqlalchemy import text
        assert c.execute(text("select decision from brief_log where date = '2026-09-22'")).scalar() == "sent"
    assert "RuntimeError" in caplog.text and "database is locked" not in caplog.text


def test_sent_brief_stores_its_table_for_the_dashboard(db_session, settings):
    """看板三期：简报页按表格展示当天发出去的那张表。存的是发送那一刻的表（撤单、改阈值都不影响历史）。"""
    seed_entry(db_session)
    row = run_daily_brief(db_session, FakeNotifier(), settings, DAY, now=NOW)
    stored = json.loads(row.rows_json)
    assert stored["sell_note"] == "在售：09-22 没有价格快照"
    assert stored["rows"] == [{"rank": 2, "name": "item A", "my_price": 100.0, "top1_price": 110.0,
                               "sell_price": None, "is_new": True, "open_rank": 5, "open_top1": 110.0,
                               "rank_changed": False, "top1_changed": False}]


def test_skipped_brief_stores_no_table(db_session, settings):
    add_order(db_session, "B")
    add_snap(db_session, "B", utc(22, 2), 7)
    db_session.commit()
    assert run_daily_brief(db_session, FakeNotifier(), settings, DAY, now=NOW).rows_json is None
