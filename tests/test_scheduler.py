import logging

from skinpulse.adapters.youpin.errors import RateLimited
from skinpulse.scheduler import (
    BRIEF_JOB_IDS, JOB_IDS, PNL_JOB_IDS, VALUATION_JOB_IDS, build_scheduler, job_heartbeat, reschedule,
)


class _EmptySession:
    """够 build_scheduler 启动时那次 recover_pending 用的最小 session：查不到任何 pending 行。

    这些测试只关心排班，但 build_scheduler 现在会先清扫一次悬挂的 pending 通知（那是唯一
    该做这件事的地方，见 scheduler.build_scheduler 的 docstring），所以 fake 的
    session_factory 必须真能当上下文管理器用。"""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def scalars(self, *a, **kw):
        return self

    def all(self):
        return []

    def commit(self):
        pass


class _Rt:
    def __init__(self, settings, *, client=None, ntfy=None, session_factory=None):
        self.settings = settings
        self.monitor = type("M", (), {"run_round": lambda self: None})()
        self.session_factory = session_factory or _EmptySession
        self.client = client
        self.ntfy = ntfy


def test_jobs_registered_with_intervals(settings):
    # account_sync was removed: run_round's pass A (see services/monitor.py) now syncs
    # every round, so the separate periodic sync job was pure duplication.
    sched = build_scheduler(_Rt(settings))
    ids = {j.id for j in sched.get_jobs()}
    assert ids == set(JOB_IDS) | set(BRIEF_JOB_IDS) | set(VALUATION_JOB_IDS) | set(PNL_JOB_IDS) == {
        "monitor_round", "heartbeat", "price_snapshot", "daily_brief", "daily_brief_catchup",
        "valuation", "valuation_catchup", "pnl", "pnl_catchup"}
    pnl = sched.get_job("pnl")
    assert str(pnl.trigger) == "cron[hour='0', minute='45']" and str(pnl.trigger.timezone) == "Asia/Shanghai"
    assert sched.get_job("heartbeat").trigger.interval.total_seconds() == 30 * 60
    # Regression test: monitor_round's interval must be driven by polling.round_interval_min,
    # not a hardcoded 60s (the bug this change fixes - see scheduler.py's build_scheduler).
    assert sched.get_job("monitor_round").trigger.interval.total_seconds() == settings.polling.round_interval_min * 60


def test_monitor_round_interval_matches_a_non_default_setting(settings):
    """Belt-and-suspenders on top of test_jobs_registered_with_intervals: prove the
    interval actually tracks the setting's value, not just its default (5 min), by
    picking a value that isn't the default before building the scheduler."""
    settings.polling.round_interval_min = 20
    sched = build_scheduler(_Rt(settings))
    assert sched.get_job("monitor_round").trigger.interval.total_seconds() == 20 * 60


def test_reschedule_applies_new_interval(settings):
    rt = _Rt(settings)
    sched = build_scheduler(rt)
    settings.polling.heartbeat_interval_min = 45
    reschedule(sched, rt)
    assert sched.get_job("heartbeat").trigger.interval.total_seconds() == 45 * 60


def test_reschedule_applies_new_round_interval(settings):
    """The bug this whole change fixes: reschedule() only updated 'heartbeat', so a
    round_interval_min edit through the dashboard's settings page (which calls
    reschedule - see web/app.py's api_settings_update) silently never took effect on
    the running job."""
    rt = _Rt(settings)
    sched = build_scheduler(rt)
    settings.polling.round_interval_min = 10
    reschedule(sched, rt)
    assert sched.get_job("monitor_round").trigger.interval.total_seconds() == 10 * 60


# ---- finding 2: job_heartbeat must not raise into APScheduler ----


class _FakeClient:
    token = "tok"


class _FakeSession:
    def __enter__(self):
        return object()

    def __exit__(self, *exc):
        return False


def test_job_heartbeat_contains_unexpected_error(settings, monkeypatch, caplog):
    import skinpulse.scheduler as sched_mod

    def boom(session, client):
        raise RateLimited("84900", "too fast")

    monkeypatch.setattr(sched_mod, "heartbeat", boom)
    rt = _Rt(settings, client=_FakeClient(), session_factory=lambda: _FakeSession())
    with caplog.at_level(logging.ERROR, logger="skinpulse.scheduler"):
        job_heartbeat(rt)  # must not raise
    assert "heartbeat" in caplog.text.lower()


def test_job_heartbeat_no_token_is_noop(settings, monkeypatch):
    import skinpulse.scheduler as sched_mod

    calls = []
    monkeypatch.setattr(sched_mod, "heartbeat", lambda session, client: calls.append(1))
    rt = _Rt(settings, client=None)
    job_heartbeat(rt)  # no client at all
    rt2 = _Rt(settings, client=type("C", (), {"token": None})())
    job_heartbeat(rt2)  # client present but no token
    assert calls == []


def test_job_heartbeat_calls_notify_login_lost_on_failure(settings, monkeypatch):
    """job_heartbeat itself no longer owns the login-lost notice or its rate limit
    (see services/account.py - notify_login_lost is now shared with
    Monitor.run_round's pass A), but it must still call it when heartbeat reports the
    token is dead."""
    import skinpulse.scheduler as sched_mod

    calls = []
    monkeypatch.setattr(sched_mod, "heartbeat", lambda session, client: False)
    monkeypatch.setattr(sched_mod, "notify_login_lost", lambda ntfy, ntfy_settings, *a, **k: calls.append((ntfy, ntfy_settings)))
    rt = _Rt(settings, client=_FakeClient(), ntfy=object(), session_factory=lambda: _FakeSession())

    job_heartbeat(rt)

    assert calls == [(rt.ntfy, settings.ntfy)]


def test_job_heartbeat_success_does_not_call_notify_login_lost(settings, monkeypatch):
    import skinpulse.scheduler as sched_mod

    calls = []
    monkeypatch.setattr(sched_mod, "heartbeat", lambda session, client: True)
    monkeypatch.setattr(sched_mod, "notify_login_lost", lambda *a, **k: calls.append(a))
    rt = _Rt(settings, client=_FakeClient(), ntfy=object(), session_factory=lambda: _FakeSession())

    job_heartbeat(rt)

    assert calls == []


# ---- Task 9: price_snapshot / daily_brief cron jobs ----


def test_brief_jobs_are_local_time_crons(settings):
    settings.brief.send_at, settings.brief.snapshot_at = "00:05", "23:40"
    sched = build_scheduler(_Rt(settings))
    brief, snap = sched.get_job("daily_brief"), sched.get_job("price_snapshot")
    assert str(brief.trigger) == "cron[hour='0', minute='5']" and str(brief.trigger.timezone) == "Asia/Shanghai"
    assert str(snap.trigger) == "cron[hour='23', minute='40']"
    assert brief.misfire_grace_time == 12 * 3600 and snap.misfire_grace_time == 4 * 60


def test_brief_jobs_not_registered_when_disabled(settings):
    settings.brief.enabled = False
    assert {j.id for j in build_scheduler(_Rt(settings)).get_jobs()} == \
        set(JOB_IDS) | set(VALUATION_JOB_IDS) | set(PNL_JOB_IDS)


def test_daily_brief_covers_yesterday(settings, monkeypatch):
    from datetime import date

    import skinpulse.scheduler as sch

    seen = {}
    monkeypatch.setattr(sch, "local_today", lambda tz, now=None: date(2026, 9, 23))
    monkeypatch.setattr(sch, "run_daily_brief",
                        lambda s, n, st, day, now, **kw: seen.setdefault("day", day))
    rt = _Rt(settings, ntfy=object())
    sch.job_daily_brief(rt)
    assert seen["day"] == date(2026, 9, 22)


def test_brief_jobs_contain_their_own_failures(settings, monkeypatch, caplog):
    import skinpulse.scheduler as sch

    def boom(*a, **kw):
        raise RuntimeError("db is locked")

    monkeypatch.setattr(sch, "run_daily_brief", boom)
    monkeypatch.setattr(sch, "take_price_snapshot", boom)
    rt = _Rt(settings)
    rt.steamdt = object()
    with caplog.at_level(logging.ERROR, logger="skinpulse.scheduler"):
        sch.job_daily_brief(rt)
        sch.job_price_snapshot(rt)
    assert "daily brief crashed" in caplog.text and "price snapshot crashed" in caplog.text


def test_price_snapshot_is_skipped_without_steamdt(settings, monkeypatch):
    import skinpulse.scheduler as sch

    called = []
    monkeypatch.setattr(sch, "take_price_snapshot", lambda *a, **kw: called.append(1))
    rt = _Rt(settings)
    rt.steamdt = None
    sch.job_price_snapshot(rt)
    assert called == []


# ---- final review I1: the 00:00 brief catches up after a process restart ----
# APScheduler 3 的内存 jobstore 在 add_job 那一刻才算 next_run_time：09:00 才启动的进程根本不知道
# 错过了 00:00，misfire_grace_time 只在「进程活着但睡着了」时有用。所以启动时另跑一次性的补发。


def test_catchup_job_registered_only_when_brief_enabled(settings):
    job = build_scheduler(_Rt(settings)).get_job("daily_brief_catchup")
    assert job is not None and job.trigger.__class__.__name__ == "DateTrigger"
    assert job.misfire_grace_time is None                  # 默认 1 秒：start() 稍晚于 add_job 就会被当成错过丢掉
    settings.brief.enabled = False
    assert build_scheduler(_Rt(settings)).get_job("daily_brief_catchup") is None


def test_catchup_day_is_yesterday_only_within_12h_after_send_at(settings):
    from datetime import date, datetime, timezone

    from skinpulse.scheduler import brief_catchup_day

    settings.brief.send_at = "00:00"                                   # Asia/Shanghai
    at = lambda d, h, m=0: datetime(2026, 9, d, h, m, tzinfo=timezone.utc)  # noqa: E731
    assert brief_catchup_day(settings, at(23, 1)) == date(2026, 9, 22)    # 本地 09:00：补 09-22
    assert brief_catchup_day(settings, at(23, 3, 59)) == date(2026, 9, 22)  # 本地 11:59：还在窗口里
    assert brief_catchup_day(settings, at(23, 4)) is None                 # 本地 12:00：窗口已过
    assert brief_catchup_day(settings, at(22, 15, 30)) is None            # 本地 09-22 23:30：昨天那份早过了
    settings.brief.send_at = "00:30"
    assert brief_catchup_day(settings, at(22, 16, 10)) is None            # 本地 00:10：还没到 00:30，交给 cron


def test_catchup_job_runs_the_brief_inside_the_window_and_not_outside(settings, monkeypatch, caplog):
    from datetime import date, datetime, timezone

    import skinpulse.scheduler as sch

    seen = []
    monkeypatch.setattr(sch, "run_daily_brief", lambda s, n, st, day, now, **kw: seen.append(day))
    rt = _Rt(settings, ntfy=object())
    monkeypatch.setattr(sch, "utcnow", lambda: datetime(2026, 9, 23, 1, 0, tzinfo=timezone.utc))
    sch.job_daily_brief_catchup(rt)
    assert seen == [date(2026, 9, 22)]
    monkeypatch.setattr(sch, "utcnow", lambda: datetime(2026, 9, 23, 6, 0, tzinfo=timezone.utc))
    with caplog.at_level(logging.INFO, logger="skinpulse.scheduler"):
        sch.job_daily_brief_catchup(rt)
    assert seen == [date(2026, 9, 22)]
    assert "catch-up" in caplog.text


def test_catchup_job_contains_its_own_failures(settings, monkeypatch, caplog):
    from datetime import datetime, timezone

    import skinpulse.scheduler as sch

    def boom(*a, **kw):
        raise RuntimeError("db is locked")

    monkeypatch.setattr(sch, "run_daily_brief", boom)
    monkeypatch.setattr(sch, "utcnow", lambda: datetime(2026, 9, 23, 1, 0, tzinfo=timezone.utc))
    with caplog.at_level(logging.ERROR, logger="skinpulse.scheduler"):
        sch.job_daily_brief_catchup(_Rt(settings))            # must not raise
    assert "crashed" in caplog.text


def test_catchup_window_reaches_back_across_midnight(settings):
    """send_at 20:00：本地 09-23 02:00 启动，09-22 20:00 那次（判 09-21）才过去 6 小时，要补。"""
    from datetime import date, datetime, timezone

    from skinpulse.scheduler import brief_catchup_day

    settings.brief.send_at = "20:00"                                        # Asia/Shanghai = UTC+8
    at = lambda d, h, m=0: datetime(2026, 9, d, h, m, tzinfo=timezone.utc)  # noqa: E731
    assert brief_catchup_day(settings, at(22, 18)) == date(2026, 9, 21)     # 本地 09-23 02:00
    assert brief_catchup_day(settings, at(22, 12)) == date(2026, 9, 21)     # 本地 09-22 20:00 整：刚到点
    assert brief_catchup_day(settings, at(22, 11, 59)) is None              # 本地 19:59：前一次（09-21 20:00）已过 12 h
    assert brief_catchup_day(settings, at(23, 0)) is None                   # 本地 09-23 08:00：09-22 20:00 已过 12 h


# ---- 复审 F1：启动时把 enter_rank 收窄后卡在 ALERTING 的旧单复位 ----

def _real_db(tmp_path):
    from skinpulse.db.session import make_engine, make_session_factory, upgrade_db

    db = tmp_path / "sched.db"
    upgrade_db(db)
    return make_session_factory(make_engine(db))


def _states(Session):
    from sqlalchemy import select

    from skinpulse.db.models import AlertStateRow

    with Session() as s:
        return {r.purchase_no: (r.state, r.streak) for r in s.scalars(select(AlertStateRow))}


def test_startup_resets_alerting_rows_ranked_outside_enter_rank(tmp_path, settings, caplog):
    """用户把 enter_rank 从 2 改成 1 后重启：在第 2、3 名停在 ALERTING 的单要回到 NORMAL，
    否则它升到第 1 名时什么都不推（要先掉到 exit_rank 以后才会重新布防）。"""
    from skinpulse.db.models import AlertStateRow, NotificationLog
    from skinpulse.db.session import utcnow

    settings.alerts.enter_rank = 1
    Session = _real_db(tmp_path)
    with Session() as s:
        for no, state, rank in [("R2", "ALERTING", 2), ("R3", "ALERTING", 3), ("R1", "ALERTING", 1),
                                ("P3", "ALERTING", 3)]:
            s.add(AlertStateRow(purchase_no=no, state=state, streak=0, last_rank=rank, updated_at=utcnow()))
        # 上一个进程死在推送前：pending 照旧由 recover_pending 判死，复位不能把它留成 pending。
        s.add(NotificationLog(fingerprint="P3", event="ENTER_TOP2", rank=3, payload_json="{}",
                              created_at=utcnow(), status="pending"))
        s.commit()

    with caplog.at_level(logging.INFO, logger="skinpulse.services.monitor"):
        build_scheduler(_Rt(settings, session_factory=Session))

    assert _states(Session) == {"R2": ("NORMAL", 0), "R3": ("NORMAL", 0), "R1": ("ALERTING", 0),
                                "P3": ("NORMAL", 0)}
    with Session() as s:
        from sqlalchemy import select

        assert [r.status for r in s.scalars(select(NotificationLog))] == ["failed"]
    info = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert len(info) == 1 and "2" in info[0]        # P3 已被 recover_pending 复位，只剩 R2、R3


def test_startup_reset_is_a_no_op_when_every_alerting_row_is_inside_the_band(tmp_path, settings):
    from skinpulse.db.models import AlertStateRow
    from skinpulse.db.session import utcnow

    settings.alerts.enter_rank = 2
    Session = _real_db(tmp_path)
    with Session() as s:
        s.add(AlertStateRow(purchase_no="R2", state="ALERTING", streak=0, last_rank=2, updated_at=utcnow()))
        s.commit()

    build_scheduler(_Rt(settings, session_factory=Session))

    assert _states(Session) == {"R2": ("ALERTING", 0)}


# ---- 看板改版：最近一轮摘要放在内存里给 /api/meta 读（spec §5）----

def _rt_with_report(settings, report):
    from types import SimpleNamespace
    return SimpleNamespace(settings=settings, last_round=None,
                           monitor=SimpleNamespace(run_round=lambda: report))


def test_monitor_round_records_its_summary(settings):
    from skinpulse.scheduler import job_monitor_round
    from skinpulse.services.monitor import RoundReport

    rt = _rt_with_report(settings, RoundReport(checked=429, alerts_sent=1, errors=0, skipped_paused=False,
                                               gap_gated=153, notify_failed=0))
    job_monitor_round(rt)
    lr = rt.last_round
    assert lr["checked"] == 429 and lr["alerts_sent"] == 1 and lr["errors"] == 0
    assert lr["paused"] is False and lr["gap_gated"] == 153 and lr["notify_failed"] == 0
    assert lr["at"].tzinfo is not None


def test_night_skipped_round_leaves_the_last_summary_alone(settings):
    """夜间闸门跳过的「轮」什么都没检查，不能把上一轮的摘要冲掉。"""
    from skinpulse.scheduler import job_monitor_round
    from skinpulse.services.monitor import RoundReport

    rt = _rt_with_report(settings, RoundReport(skipped_night=True))
    rt.last_round = {"checked": 5}
    job_monitor_round(rt)
    assert rt.last_round == {"checked": 5}


def test_runtime_last_round_defaults_to_none(settings):
    from skinpulse.runtime import Runtime
    assert Runtime(settings, None, None, None, None, None, None).last_round is None


# ---- 价位一期：每日估值（价位 spec §5） ----


def test_valuation_jobs_registered_as_local_cron(settings):
    settings.valuation.run_at = "00:45"
    sched = build_scheduler(_Rt(settings))
    job = sched.get_job("valuation")
    assert str(job.trigger) == "cron[hour='0', minute='45']" and str(job.trigger.timezone) == "Asia/Shanghai"
    assert job.misfire_grace_time == 12 * 3600 and job.max_instances == 1
    catchup = sched.get_job("valuation_catchup")
    assert catchup.trigger.__class__.__name__ == "DateTrigger" and catchup.misfire_grace_time is None
    settings.valuation.enabled = False
    assert not {"valuation", "valuation_catchup"} & {j.id for j in build_scheduler(_Rt(settings)).get_jobs()}


def test_valuation_job_values_the_day_that_just_ended(settings, monkeypatch):
    from datetime import date, datetime, timezone

    import skinpulse.scheduler as sch

    seen = []
    monkeypatch.setattr(sch, "run_valuation", lambda s, client, market, st, *, day, now, **kw: seen.append(day))
    monkeypatch.setattr(sch, "utcnow", lambda: datetime(2026, 9, 26, 16, 30, tzinfo=timezone.utc))  # 本地 09-27 00:30
    rt = _Rt(settings)
    rt.steamdt, rt.market = object(), object()
    sch.job_valuation(rt)
    assert seen == [date(2026, 9, 26)]
    rt.steamdt = None                                  # 没配 key：跳过
    sch.job_valuation(rt)
    assert seen == [date(2026, 9, 26)]


def test_valuation_job_contains_its_own_failures(settings, monkeypatch, caplog):
    import skinpulse.scheduler as sch

    def boom(*a, **kw):
        raise RuntimeError("db is locked")

    monkeypatch.setattr(sch, "run_valuation", boom)
    rt = _Rt(settings)
    rt.steamdt, rt.market = object(), None
    with caplog.at_level(logging.ERROR, logger="skinpulse.scheduler"):
        sch.job_valuation(rt)
        sch.job_valuation_catchup(rt)
    assert "valuation crashed" in caplog.text


def test_valuation_catchup_only_within_window_and_when_missing(settings, monkeypatch):
    from datetime import date, datetime, timezone

    import skinpulse.scheduler as sch

    seen, have = [], set()
    monkeypatch.setattr(sch, "run_valuation", lambda s, client, market, st, *, day, now, **kw: seen.append(day))
    monkeypatch.setattr(sch, "_has_valuation", lambda s, day: day in have)
    rt = _Rt(settings)
    rt.steamdt, rt.market = object(), object()
    at = lambda d, h, m=0: datetime(2026, 9, d, h, m, tzinfo=timezone.utc)  # noqa: E731
    monkeypatch.setattr(sch, "utcnow", lambda: at(27, 1))           # 本地 09-27 09:00：00:30 那次错过了 8.5 小时
    sch.job_valuation_catchup(rt)
    assert seen == [date(2026, 9, 26)]
    have.add(date(2026, 9, 26))                                      # 已经算过：不重算
    sch.job_valuation_catchup(rt)
    assert seen == [date(2026, 9, 26)]
    have.clear()
    monkeypatch.setattr(sch, "utcnow", lambda: at(27, 4, 31))       # 本地 12:31：窗口已过
    sch.job_valuation_catchup(rt)
    assert seen == [date(2026, 9, 26)]
    monkeypatch.setattr(sch, "utcnow", lambda: at(26, 16, 20))      # 本地 00:20：今天的还没到，交给 cron；昨天那次早过了
    sch.job_valuation_catchup(rt)
    assert seen == [date(2026, 9, 26)]


def test_valuation_catchup_finishes_a_half_valued_day(tmp_path, settings, monkeypatch):
    """00:30 那次只算了一半就断了（睡眠、重启）：补跑要看是否**算全了**，不能看到有一行就跳过。"""
    from datetime import date
    from decimal import Decimal

    import skinpulse.scheduler as sch
    from skinpulse.db.models import Item, MyBuyOrder, Valuation
    from skinpulse.db.session import make_engine, make_session_factory, upgrade_db, utcnow

    db = tmp_path / "v.db"
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))
    now = utcnow()
    with Session() as s:
        for tid in (1, 2):
            s.add(MyBuyOrder(purchase_no=f"P{tid}", template_id=tid, commodity_name="x", price=Decimal("1"), qty=1,
                             remaining_qty=1, type_id=-1, queue_key=f"{tid}|ANY|-1|ANY|ANY", synced_at=now, active=True))
            s.add(Item(hash_name=f"h{tid}", cn_name=None, youpin_template_id=tid))
        s.add(Valuation(date="2026-09-26", template_id=1, computed_at=now, bars=100))
        s.commit()
        day = date(2026, 9, 26)
        assert sch._has_valuation(s, day) is False            # 模板 2 还没算
        s.add(Valuation(date="2026-09-26", template_id=2, computed_at=now, bars=100))
        s.commit()
        assert sch._has_valuation(s, day) is True


def test_pnl_jobs_not_registered_when_disabled(settings):
    settings.pnl.enabled = False
    assert not {j.id for j in build_scheduler(_Rt(settings)).get_jobs()} & set(PNL_JOB_IDS)


def test_pnl_job_covers_yesterday_and_catchup_window(settings, monkeypatch):
    from datetime import date, datetime, timezone

    import skinpulse.scheduler as sch

    seen, have = [], set()
    monkeypatch.setattr(sch, "run_pnl", lambda rt, day, now, **kw: seen.append(day) or "rep")
    monkeypatch.setattr(sch, "has_snapshot", lambda s, day, accounts: day in have)
    rt = _Rt(settings)
    at = lambda d, h, m=0: datetime(2026, 11, d, h, m, tzinfo=timezone.utc)  # noqa: E731
    monkeypatch.setattr(sch, "utcnow", lambda: at(24, 16, 46))      # 本地 11-25 00:46
    sch.job_pnl(rt)
    assert seen == [date(2026, 11, 24)]
    monkeypatch.setattr(sch, "utcnow", lambda: at(25, 1))           # 本地 09:00：错过 8 小时，补跑
    sch.job_pnl_catchup(rt)
    assert seen == [date(2026, 11, 24), date(2026, 11, 24)]
    have.add(date(2026, 11, 24))
    sch.job_pnl_catchup(rt)                                          # 已经有了：不重算
    monkeypatch.setattr(sch, "utcnow", lambda: at(25, 5))           # 本地 13:00：窗口已过
    have.clear()
    sch.job_pnl_catchup(rt)
    assert len(seen) == 2


def test_pnl_job_failure_is_contained(settings, monkeypatch, caplog):
    import skinpulse.scheduler as sch

    def boom(*a, **kw):
        raise RuntimeError("secret-url?key=SECRET")

    monkeypatch.setattr(sch, "run_pnl", boom)
    with caplog.at_level(logging.WARNING, logger="skinpulse.scheduler"):
        sch.job_pnl(_Rt(settings))
    assert "pnl crashed (RuntimeError)" in caplog.text and "SECRET" not in caplog.text
