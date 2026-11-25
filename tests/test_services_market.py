from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from skinpulse.adapters.steamdt.models import BaseItem, ItemPrices, PlatformPrice
from skinpulse.db.models import Item, MyBuyOrder
from skinpulse.db.session import utcnow
from skinpulse.services.market import (
    ComparisonReport,
    compare_platforms,
    format_report,
    resolve_names,
    sync_base_items,
)


def _base(name, mhn, **platform_ids):
    return BaseItem.from_api({"name": name, "marketHashName": mhn,
                              "platformList": [{"name": k, "itemId": v} for k, v in platform_ids.items()]})


def _order(session, purchase_no, template_id):
    session.add(MyBuyOrder(purchase_no=purchase_no, template_id=template_id, commodity_name="x",
                           price=Decimal("100"), qty=1, remaining_qty=1, type_id=-1,
                           queue_key=f"{template_id}|ANY|-1|ANY|ANY", synced_at=utcnow(), active=True))


def _p(platform, sell=None, bid=None):
    return PlatformPrice(platform=platform, platform_item_id="1",
                         sell_price=None if sell is None else Decimal(str(sell)), sell_count=1,
                         bidding_price=None if bid is None else Decimal(str(bid)), bidding_count=1,
                         update_time=datetime.now(timezone.utc))


class FakeSteamDT:
    def __init__(self, by_name):
        self.by_name = by_name
        self.batches = []

    def get_prices_batch(self, names):
        names = list(names)
        self.batches.append(names)
        return [ItemPrices(market_hash_name=n, platforms=self.by_name.get(n, [])) for n in names]


# ---- 目录同步 ----


def test_sync_stores_only_items_listed_on_youpin(db_session):
    """没有 YOUPIN itemId 的饰品我们既监控不到也对不上账。实测 4.4 万条里约 9 千条是这样，
    存进来只会让这张表白白多出上万行。"""
    stored, skipped = sync_base_items(db_session, [
        _base("AK-47 | 红线 (久经沙场)", "AK-47 | Redline (Field-Tested)", YOUPIN="1414", BUFF="33960"),
        _base("只在 BUFF 有", "Only On Buff", BUFF="1"),
    ])

    assert (stored, skipped) == (1, 1)
    rows = list(db_session.scalars(select(Item)))
    assert len(rows) == 1 and rows[0].youpin_template_id == 1414


def test_sync_is_idempotent_and_refreshes_names(db_session):
    """/base 每天只能调一次，缓存文件会被反复重放，所以重跑必须不产生重复行。"""
    sync_base_items(db_session, [_base("旧名", "A", YOUPIN="7")])
    sync_base_items(db_session, [_base("新名", "A", YOUPIN="7")])

    rows = list(db_session.scalars(select(Item)))
    assert len(rows) == 1 and rows[0].cn_name == "新名"


def test_resolve_names_maps_template_ids(db_session):
    sync_base_items(db_session, [_base("n", "A", YOUPIN="1"), _base("m", "B", YOUPIN="2")])
    assert resolve_names(db_session, [1, 2, 999]) == {1: "A", 2: "B"}


# ---- 对账 ----


def test_compare_counts_templates_it_could_not_map(db_session):
    """映射不到的单必须如实计数。静默丢掉会让「对账覆盖了多少」这个数字失真——报告里
    看起来全覆盖，实际只算了一半。"""
    sync_base_items(db_session, [_base("n", "A", YOUPIN="1")])
    _order(db_session, "P1", 1)
    _order(db_session, "P2", 2)          # 2 号模板没有映射
    db_session.commit()

    rep = compare_platforms(db_session, FakeSteamDT({"A": [_p("YOUPIN", sell=90), _p("BUFF", sell=100)]}),
                            sleep=lambda s: None)

    assert rep.requested == 2 and rep.unmapped == 1 and rep.fetched == 1


def test_compare_deduplicates_templates_before_spending_quota(db_session):
    """/price/batch 是 1 次/分钟。几百张单里同一个模板会重复出现，逐单查等于白烧配额。"""
    sync_base_items(db_session, [_base("n", "A", YOUPIN="1")])
    for i in range(5):
        _order(db_session, f"P{i}", 1)   # 五张单，同一个模板
    db_session.commit()

    client = FakeSteamDT({"A": [_p("YOUPIN", sell=90), _p("BUFF", sell=100)]})
    compare_platforms(db_session, client, sleep=lambda s: None)

    assert client.batches == [["A"]]


def test_compare_waits_between_batches(db_session):
    """批与批之间必须等够一分钟，否则第二批直接撞配额；而配额错误重试只会把下一个窗口也烧掉。"""
    sync_base_items(db_session, [_base(f"n{i}", f"N{i}", YOUPIN=str(i)) for i in range(1, 4)])
    for i in range(1, 4):
        _order(db_session, f"P{i}", i)
    db_session.commit()

    waits = []
    compare_platforms(db_session, FakeSteamDT({}), batch_size=1, sleep=waits.append)

    assert len(waits) == 2 and all(w >= 60 for w in waits)   # 三批之间等两次


def test_items_with_no_platform_data_are_not_counted_as_fetched(db_session):
    sync_base_items(db_session, [_base("n", "A", YOUPIN="1")])
    _order(db_session, "P1", 1)
    db_session.commit()

    rep = compare_platforms(db_session, FakeSteamDT({"A": [_p("WAXPEER"), _p("DMARKET")]}),
                            sleep=lambda s: None)

    assert rep.fetched == 0 and rep.sell is None


def test_format_report_survives_having_nothing_to_report():
    assert "没有可比数据" in format_report(ComparisonReport(requested=3, unmapped=3))


# ---- /price/batch 的共用时间槽（2026-11-24：pnl import 和暂停单检查同时跑，撞了 4005）----


def test_batch_slot_waits_out_the_last_batch_across_sessions(tmp_path):
    from datetime import datetime, timedelta, timezone

    from skinpulse.db.models import SystemStatus
    from skinpulse.db.session import make_engine, make_session_factory, upgrade_db
    from skinpulse.services.market import BATCH_SLOT, wait_batch_slot

    db = tmp_path / "t.db"
    upgrade_db(db)
    S = make_session_factory(make_engine(db))
    t = [datetime(2026, 11, 24, 12, 49, tzinfo=timezone.utc)]
    slept = []
    with S() as a:
        assert wait_batch_slot(a, slept.append, now=lambda: t[0]) == 0     # 第一次：不等
    t[0] += timedelta(seconds=10)
    with S() as b:                                                        # 另一个会话（另一个进程）看得到
        assert wait_batch_slot(b, slept.append, now=lambda: t[0]) == 55
    assert slept == [55]
    t[0] += timedelta(seconds=120)
    with S() as c:
        assert wait_batch_slot(c, slept.append, now=lambda: t[0]) == 0
        assert c.get(SystemStatus, BATCH_SLOT).last_ok_at == t[0]
    assert slept == [55]
