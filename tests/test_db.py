import logging
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from skinpulse.db.models import AlertStateRow, MyBuyOrder, RankCheck, RankSnapshot
from skinpulse.db.session import make_engine, make_session_factory, upgrade_db, utcnow
from skinpulse.services.monitor import log as monitor_log


def test_upgrade_creates_tables_and_wal(tmp_path: Path):
    db = tmp_path / "t.db"
    upgrade_db(db)
    engine = make_engine(db)
    with engine.connect() as c:
        mode = c.exec_driver_sql("PRAGMA journal_mode").scalar()
        assert mode.lower() == "wal"
        names = {r[0] for r in c.exec_driver_sql("select name from sqlite_master where type='table'")}
    assert {"my_buy_order", "rank_snapshot", "alert_state", "notification_log", "system_status", "account", "item"} <= names


def test_migration_0002_adds_reported_rank_and_top_bid_columns(tmp_path: Path):
    """0002_add_reported_rank_and_top_bid.py must apply cleanly to a fresh database
    (i.e. run right after 0001, not just against an already-upgraded one) and produce
    exactly the two new nullable columns on my_buy_order."""
    db = tmp_path / "t.db"
    upgrade_db(db)
    engine = make_engine(db)
    with engine.connect() as c:
        cols = {r[1]: r for r in c.exec_driver_sql("PRAGMA table_info(my_buy_order)")}
    assert "reported_rank" in cols and cols["reported_rank"][2].upper() == "INTEGER"
    assert "top_bid" in cols and "NUMERIC" in cols["top_bid"][2].upper()
    # nullable: PRAGMA table_info's "notnull" column (index 3) is 0/false
    assert cols["reported_rank"][3] == 0
    assert cols["top_bid"][3] == 0


def test_migration_0003_adds_rank_check_table_and_last_verified_at(tmp_path: Path):
    """0003_add_rank_check_table_and_last_verified_at.py must apply cleanly to a fresh
    database (i.e. run right after 0001/0002, not just against an already-upgraded
    one). Both new columns are UtcDateTime, so this also exercises the "does this
    migration need `import skinpulse.db.models`" question the custom type raises for
    SQLite (see 0001_init.py vs 0002, which didn't need it)."""
    db = tmp_path / "t.db"
    upgrade_db(db)
    engine = make_engine(db)
    with engine.connect() as c:
        tables = {r[0] for r in c.exec_driver_sql("select name from sqlite_master where type='table'")}
        assert "rank_check" in tables
        my_buy_order_cols = {r[1]: r for r in c.exec_driver_sql("PRAGMA table_info(my_buy_order)")}
        assert "last_verified_at" in my_buy_order_cols
        assert my_buy_order_cols["last_verified_at"][3] == 0  # nullable
        rank_check_cols = {r[1]: r for r in c.exec_driver_sql("PRAGMA table_info(rank_check)")}
        expected = {"id", "purchase_no", "ts", "reported_rank", "computed_rank",
                    "queue_size", "sample_size", "agreed"}
        assert expected <= set(rank_check_cols)
        assert rank_check_cols["reported_rank"][3] == 0  # nullable
        assert rank_check_cols["computed_rank"][3] == 0  # nullable
        assert rank_check_cols["agreed"][3] == 1  # not null

    # And the migration must actually be usable end to end, not just structurally present.
    Session = make_session_factory(engine)
    with Session() as s:
        s.add(MyBuyOrder(purchase_no="P3", template_id=1414, commodity_name="n", price=Decimal("100"),
                         qty=1, remaining_qty=1, type_id=-1, queue_key="1414|ANY|-1|ANY|ANY",
                         synced_at=utcnow(), active=True, last_verified_at=utcnow()))
        s.add(RankCheck(purchase_no="P3", ts=utcnow(), reported_rank=2, computed_rank=2,
                        queue_size=5, sample_size=20, agreed=True))
        s.commit()
        row = s.scalar(select(RankCheck))
        assert row.agreed is True and row.reported_rank == 2
        assert s.get(MyBuyOrder, "P3").last_verified_at is not None


def test_insert_and_query(tmp_path: Path):
    db = tmp_path / "t.db"
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))
    with Session() as s:
        s.add(MyBuyOrder(purchase_no="P1", template_id=1414, template_hash_name="AK-47 | Redline (Field-Tested)",
                         commodity_name="AK-47 | 红线 (久经沙场)", price=Decimal("360.00"), qty=1, remaining_qty=1,
                         abrade_text="0.15-0.18", type_id=-1, special_style=None, fade_text=None,
                         queue_key="1414|0.15-0.18|-1|ANY|ANY", conditions_json="{}", synced_at=utcnow(), active=True))
        s.add(MyBuyOrder(purchase_no="P2", template_id=1414, template_hash_name="", commodity_name="n",
                         price=Decimal("358.00"), qty=1, remaining_qty=1, abrade_text=None, type_id=-1,
                         special_style=None, fade_text=None, queue_key="1414|ANY|-1|ANY|ANY",
                         conditions_json="{}", synced_at=utcnow(), active=True,
                         reported_rank=22, top_bid=Decimal("28.10")))
        s.add(RankSnapshot(purchase_no="P1", ts=utcnow(), rank=2, beyond=None, top1_price=Decimal("372"),
                           top2_price=Decimal("360"), top1_qty=2, sample_size=20, sell_min=None))
        s.add(AlertStateRow(purchase_no="P1", state="NORMAL", streak=0, last_rank=None, updated_at=utcnow()))
        s.commit()
        row = s.scalar(select(MyBuyOrder).where(MyBuyOrder.purchase_no == "P1"))
        assert row.price == Decimal("360.00")
        assert row.reported_rank is None and row.top_bid is None  # not set -> nullable defaults
        row2 = s.get(MyBuyOrder, "P2")
        assert row2.reported_rank == 22
        assert row2.top_bid == Decimal("28.10")
        assert s.scalar(select(RankSnapshot)).rank == 2


def test_datetimes_round_trip_as_utc_aware(tmp_path: Path):
    """SQLite 不存时区；UtcDateTime 必须保证读回来仍是 aware，否则 planner 的
    `now - last_checked_at` 会抛 TypeError。"""
    import pytest

    db = tmp_path / "t.db"
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))
    ts = utcnow()
    with Session() as s:
        s.add(AlertStateRow(purchase_no="P1", state="NORMAL", streak=0, updated_at=ts))
        s.commit()
    with Session() as s:
        got = s.get(AlertStateRow, "P1").updated_at
        assert got.tzinfo is not None
        assert abs((got - ts).total_seconds()) < 1
    with Session() as s:
        from datetime import datetime as _dt
        s.add(AlertStateRow(purchase_no="P2", state="NORMAL", streak=0, updated_at=_dt(2026, 1, 1)))
        with pytest.raises(Exception):
            s.commit()


def test_upgrade_db_does_not_disable_application_loggers(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    """alembic/env.py calls logging.config.fileConfig(...), which defaults to
    disable_existing_loggers=True - that sets `.disabled = True` on every logger that
    already exists at that moment, including every skinpulse.* module logger (they're all
    created at import time via `logging.getLogger(__name__)`, so they exist before
    upgrade_db ever runs). A disabled logger short-circuits before propagation, so it stays
    silent even once the root logger is configured afterwards - and it silently defeats
    caplog too, which is how this was noticed. `monitor_log` here is the real module-level
    logger `skinpulse.services.monitor.log`, imported (and thus created) before this test
    calls upgrade_db, exactly like production import order.
    """
    db = tmp_path / "t.db"

    upgrade_db(db)

    assert monitor_log.disabled is False

    with caplog.at_level(logging.INFO, logger=monitor_log.name):
        monitor_log.info("post-migration probe message")

    assert monitor_log.disabled is False
    assert any(r.message == "post-migration probe message" for r in caplog.records)


def test_migration_0006_price_daily_and_brief_log(tmp_path: Path):
    from sqlalchemy.exc import IntegrityError

    from skinpulse.db.models import BriefLog, PriceDaily

    db = tmp_path / "t.db"
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))
    now = utcnow()
    with Session() as s:
        s.add(PriceDaily(date="2026-09-22", template_id=1, market_hash_name="AK", platform="YOUPIN",
                         sell_price=Decimal("100.5"), sell_count=3, bidding_price=None, bidding_count=0,
                         update_time=None, fetched_at=now))
        s.add(BriefLog(date="2026-09-22", decision="skipped", reasons_json='{"summary": "x"}',
                       title=None, body=None, created_at=now, sent_at=None))
        s.commit()
        row = s.scalar(select(PriceDaily))
        assert row.sell_price == Decimal("100.50") and row.bidding_price is None
        assert row.fetched_at.tzinfo is not None
        assert s.get(BriefLog, "2026-09-22").decision == "skipped"
        s.add(PriceDaily(date="2026-09-22", template_id=1, market_hash_name="AK", platform="YOUPIN",
                         sell_price=None, sell_count=0, bidding_price=None, bidding_count=0,
                         update_time=None, fetched_at=now))
        with pytest.raises(IntegrityError):
            s.commit()


def test_migration_0008_valuation_tables(tmp_path: Path):
    from sqlalchemy.exc import IntegrityError

    from skinpulse.db.models import DailyBar, IndexDaily, OrderEvent, Valuation

    db = tmp_path / "t.db"
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))
    now = utcnow()
    with Session() as s:
        s.add(DailyBar(template_id=1, platform="YOUPIN", date="2026-09-26", open=Decimal("10"),
                       close=Decimal("10.5"), high=Decimal("11"), low=Decimal("9.8"), fetched_at=now))
        s.add(IndexDaily(date="2026-09-26", open=Decimal("800"), close=Decimal("795.71"),
                         high=Decimal("801"), low=Decimal("790"), fetched_at=now))
        s.add(Valuation(date="2026-09-26", template_id=1, computed_at=now, bars=365,
                        pct=Decimal("0.123456"), lo=Decimal("9"), hi=Decimal("20"), cur=Decimal("10.5"),
                        trend="下降", c30=Decimal("-0.08"), c90=None, b30=Decimal("-0.05"), b90=None,
                        ex30=Decimal("-0.03"), rel="持平", vol7=Decimal("0.06"), q10=Decimal("-0.09"),
                        liq="一般", listed=40, spread=Decimal("0.1"), exit=Decimal("12.02"),
                        exit_where="YOUPIN", exit_sell=Decimal("12.39"), cap=Decimal("9.5"),
                        snapshot_date="2026-09-26"))
        s.add(OrderEvent(purchase_no="P1", template_id=1, kind="filled", qty_before=2, qty_after=1,
                         price=Decimal("7"), at=now))
        s.commit()
        v = s.scalar(select(Valuation))
        assert v.pct == Decimal("0.123456") and v.c90 is None and v.computed_at.tzinfo is not None
        assert s.scalar(select(DailyBar)).close == Decimal("10.50")
        assert s.scalar(select(IndexDaily)).close == Decimal("795.71")
        ev = s.scalar(select(OrderEvent))
        assert (ev.kind, ev.qty_before, ev.qty_after, ev.at.tzinfo is not None) == ("filled", 2, 1, True)
        for dup in (
            DailyBar(template_id=1, platform="YOUPIN", date="2026-09-26", open=Decimal("1"), close=Decimal("1"),
                     high=Decimal("1"), low=Decimal("1"), fetched_at=now),
            IndexDaily(date="2026-09-26", open=Decimal("1"), close=Decimal("1"), high=Decimal("1"),
                       low=Decimal("1"), fetched_at=now),
            Valuation(date="2026-09-26", template_id=1, computed_at=now, bars=0),
        ):
            s.add(dup)
            with pytest.raises(IntegrityError):
                s.commit()
            s.rollback()


def test_migration_0009_fill_alert_columns(tmp_path: Path):
    from alembic import command
    from alembic.config import Config

    from skinpulse.db.models import MyBuyOrder, OrderEvent
    from skinpulse.db.session import ALEMBIC_DIR

    db = tmp_path / "t.db"
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))
    now = utcnow()
    with Session() as s:
        s.add(MyBuyOrder(purchase_no="P1", template_id=1, price=Decimal("5"), queue_key="1|ANY",
                         synced_at=now, in_trade_since=now, in_trade_notice="sent"))
        s.add(OrderEvent(purchase_no="P1", template_id=1, kind="gone", qty_before=1, qty_after=None,
                         price=Decimal("5"), at=now, outcome="done", outcome_at=now, outcome_qty=1,
                         notice="pending"))
        s.add(OrderEvent(purchase_no="P2", template_id=2, kind="new", qty_before=None, qty_after=1,
                         price=Decimal("3"), at=now))
        s.commit()
        o = s.get(MyBuyOrder, "P1")
        assert o.in_trade_since.tzinfo is not None and o.in_trade_notice == "sent"
        ev = s.scalar(select(OrderEvent).where(OrderEvent.purchase_no == "P1"))
        assert (ev.outcome, ev.outcome_qty, ev.notice, ev.outcome_at.tzinfo is not None) == ("done", 1, "pending", True)
        plain = s.scalar(select(OrderEvent).where(OrderEvent.purchase_no == "P2"))
        assert (plain.outcome, plain.outcome_at, plain.outcome_qty, plain.notice) == (None, None, None, None)
    from sqlalchemy import inspect
    assert "ix_order_event_notice" in {i["name"] for i in inspect(make_engine(db)).get_indexes("order_event")}
    # 能退回 0008 再升回来（batch_alter_table 在 SQLite 上是重建表）
    cfg = Config(str(ALEMBIC_DIR.parent / "alembic.ini"))
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db.as_posix()}")
    command.downgrade(cfg, "0008")
    command.upgrade(cfg, "head")


def test_migration_0014_multi_account(tmp_path: Path):
    """多账号 spec §2：老库升级后唯一的号变成 youpin、启用、没有简称；单和事件都记到号 1；账号通道状态改名。"""
    from alembic import command
    from alembic.config import Config

    from skinpulse.db.session import ALEMBIC_DIR

    db = tmp_path / "t.db"
    cfg = Config(str(ALEMBIC_DIR.parent / "alembic.ini"))
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db.as_posix()}")
    command.upgrade(cfg, "0013")
    engine = make_engine(db)
    with engine.begin() as c:
        c.exec_driver_sql("INSERT INTO account (id, user_id, nickname, token_valid) VALUES (1, 7, 'me', 1)")
        c.exec_driver_sql("INSERT INTO my_buy_order (purchase_no, template_id, template_hash_name, commodity_name, price,"
                          " qty, remaining_qty, type_id, queue_key, conditions_json, synced_at, active, lane)"
                          " VALUES ('P1', 1, '', '', 1, 1, 1, -1, 'k', '{}', '2026-11-24 00:00:00', 1, 'normal')")
        c.exec_driver_sql("INSERT INTO order_event (purchase_no, template_id, kind, at)"
                          " VALUES ('P1', 1, 'new', '2026-11-24 00:00:00')")
        c.exec_driver_sql("INSERT INTO system_status (adapter, error_counts_json) VALUES ('youpin_account', '{}')")
        c.exec_driver_sql("INSERT INTO system_status (adapter, error_counts_json) VALUES ('youpin_anon', '{}')")
    engine.dispose()
    command.upgrade(cfg, "head")
    engine = make_engine(db)
    with engine.connect() as c:
        assert c.exec_driver_sql("SELECT platform, label, enabled FROM account").one() == ("youpin", None, 1)
        assert c.exec_driver_sql("SELECT account_id FROM my_buy_order").scalar() == 1
        assert c.exec_driver_sql("SELECT account_id FROM order_event").scalar() == 1
        names = {r[0] for r in c.exec_driver_sql("SELECT adapter FROM system_status")}
    assert names == {"youpin_account:1", "youpin_anon"}
    engine.dispose()
    command.downgrade(cfg, "0013")
    engine = make_engine(db)
    with engine.connect() as c:
        assert {r[0] for r in c.exec_driver_sql("SELECT adapter FROM system_status")} == {"youpin_account", "youpin_anon"}


def test_migration_0016_pnl_round_trip(tmp_path: Path):
    """盈亏 spec §3：三张新表，(platform, order_no) 唯一；downgrade 删掉。"""
    import pytest
    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config

    from skinpulse.db.session import ALEMBIC_DIR

    db = tmp_path / "t.db"
    cfg = Config(str(ALEMBIC_DIR.parent / "alembic.ini"))
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db.as_posix()}")
    command.upgrade(cfg, "head")
    engine = make_engine(db)
    row = ("INSERT INTO trade_order (account_id, platform, order_no, side, template_id, hash_name, name, qty, amount,"
           " net_exact, created_at, fetched_at) VALUES (1, 'YOUPIN', 'O1', 'buy', 5, 'h', 'n', 1, 3.5, 0,"
           " '2026-11-24 00:00:00', '2026-11-24 00:00:00')")
    with engine.begin() as c:
        c.exec_driver_sql(row)
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as c:
        c.exec_driver_sql(row)
    engine.dispose()
    command.downgrade(cfg, "0015")
    engine = make_engine(db)
    tables = set(sa.inspect(engine).get_table_names())
    assert not tables & {"trade_order", "pnl_position", "pnl_daily"}
def test_migration_0015_paused_check(tmp_path: Path):
    """暂停单 spec §4：新表能写、能读回 aware 时间和 NULL 价格；同一次检查同一单号只能一行；能降回 0014。"""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.exc import IntegrityError

    from skinpulse.db.models import PausedCheckItem, PausedCheckRun
    from skinpulse.db.session import ALEMBIC_DIR

    db = tmp_path / "t.db"
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))
    now = utcnow()
    with Session() as s:
        run = PausedCheckRun(started_at=now, status="running", source="cli", phase="pages")
        s.add(run)
        s.flush()
        s.add(PausedCheckItem(run_id=run.id, purchase_no="P1", template_id=1, commodity_name="x",
                              price=Decimal("650"), paused_at=now, top_bid=None, top_empty=True, tier="top1",
                              top1_kind="alone"))
        s.commit()
        it = s.scalar(select(PausedCheckItem))
        assert (it.top_bid, it.sell_min, it.sell_depth, it.top_empty) == (None, None, None, True)
        assert it.paused_at.tzinfo is not None and it.account_id == 1 and it.remaining_qty == 1
        r = s.get(PausedCheckRun, run.id)
        assert (r.done, r.total, r.incomplete, r.steamdt) == (0, 0, False, False)
        s.add(PausedCheckItem(run_id=run.id, purchase_no="P1", template_id=1, commodity_name="x",
                              price=Decimal("1"), tier="other"))
        with pytest.raises(IntegrityError):
            s.commit()
    cfg = Config(str(ALEMBIC_DIR.parent / "alembic.ini"))
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db.as_posix()}")
    command.downgrade(cfg, "0014")
    command.upgrade(cfg, "head")
