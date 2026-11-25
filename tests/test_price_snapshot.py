import logging
from datetime import date
from decimal import Decimal as D

import httpx
from sqlalchemy import select

from skinpulse.adapters.steamdt.errors import SteamDTQuotaExceeded
from skinpulse.adapters.steamdt.models import ItemPrices, PlatformPrice
from skinpulse.db.models import Item, MyBuyOrder, PriceDaily
from skinpulse.db.session import utcnow
from skinpulse.services.price_snapshot import take_price_snapshot

DAY = date(2026, 9, 22)


class FakeDT:
    def __init__(self, fail_calls=(), transport_fail_calls=()):
        self.calls: list[list[str]] = []
        self.fail_calls = set(fail_calls)
        self.transport_fail_calls = set(transport_fail_calls)

    def get_prices_batch(self, names):
        self.calls.append(list(names))
        if len(self.calls) in self.fail_calls:
            raise SteamDTQuotaExceeded("4005", "当前接口请求已达到上限")
        if len(self.calls) in self.transport_fail_calls:
            # A real transport error's message/URL can carry the API key (it's a query
            # param on the request) - this string stands in for that so the test can
            # confirm the key never reaches the log.
            raise httpx.ConnectError(
                "Connection refused: https://open.steamdt.com/open/cs2/v1/price/batch?key=SECRETKEY123"
            )
        return [ItemPrices(n, [PlatformPrice("YOUPIN", "", D("100"), 3, D("90"), 2, None),
                               PlatformPrice("BUFF", "", None, 0, D("95"), 1, None)]) for n in names]


def seed(s, n_templates=3, unmapped=1):
    now = utcnow()
    for tid in range(1, n_templates + unmapped + 1):
        s.add(MyBuyOrder(purchase_no=f"P{tid}", template_id=tid, commodity_name=f"item {tid}", price=D("80"),
                         qty=1, remaining_qty=1, type_id=-1, queue_key=f"{tid}|ANY|-1|ANY|ANY",
                         synced_at=now, active=True))
        if tid <= n_templates:
            s.add(Item(hash_name=f"hash {tid}", cn_name=None, youpin_template_id=tid))
    s.add(MyBuyOrder(purchase_no="OLD", template_id=99, commodity_name="gone", price=D("1"), qty=1,
                     remaining_qty=1, type_id=-1, queue_key="99|ANY|-1|ANY|ANY", synced_at=now, active=False))
    s.add(Item(hash_name="hash 99", cn_name=None, youpin_template_id=99))
    s.commit()


def test_snapshot_writes_one_row_per_platform_in_batches(db_session):
    seed(db_session)
    dt, slept = FakeDT(), []
    rep = take_price_snapshot(db_session, dt, day=DAY, batch_size=2, now=utcnow(), sleep=slept.append)
    assert [len(c) for c in dt.calls] == [2, 1] and slept == [65]
    assert (rep.templates, rep.unmapped, rep.fetched, rep.rows, rep.failed_batches) == (4, 1, 3, 6, 0)
    rows = db_session.scalars(select(PriceDaily).where(PriceDaily.template_id == 1)).all()
    assert {(r.platform, r.sell_price, r.bidding_price) for r in rows} == {("YOUPIN", D("100"), D("90")),
                                                                           ("BUFF", None, D("95"))}
    assert all(r.date == "2026-09-22" for r in rows)
    assert not db_session.scalars(select(PriceDaily).where(PriceDaily.template_id == 99)).all()  # 不活跃的单不查


def test_rerun_on_the_same_day_only_fetches_what_is_missing(db_session):
    seed(db_session)
    take_price_snapshot(db_session, FakeDT(), day=DAY, batch_size=100, now=utcnow(), sleep=lambda s: None)
    dt = FakeDT()
    rep = take_price_snapshot(db_session, dt, day=DAY, batch_size=100, now=utcnow(), sleep=lambda s: None)
    assert dt.calls == [] and rep.already == 3 and rep.rows == 0


def test_a_failed_batch_does_not_stop_the_rest(db_session):
    seed(db_session)
    rep = take_price_snapshot(db_session, FakeDT(fail_calls={1}), day=DAY, batch_size=2, now=utcnow(),
                              sleep=lambda s: None)
    assert rep.failed_batches == 1 and rep.fetched == 1


def test_a_transport_error_does_not_stop_the_rest(db_session, caplog):
    seed(db_session)
    dt = FakeDT(transport_fail_calls={1})
    with caplog.at_level(logging.WARNING):
        rep = take_price_snapshot(db_session, dt, day=DAY, batch_size=2, now=utcnow(), sleep=lambda s: None)
    assert [len(c) for c in dt.calls] == [2, 1]
    assert rep.failed_batches == 1 and rep.fetched == 1
    # The transport error's message can carry the API key (it's a query param) - the log
    # must contain only the exception type name, never the URL or key.
    assert "SECRETKEY123" not in caplog.text
    assert "https://" not in caplog.text
    assert "ConnectError" in caplog.text


class FakeDTWithEmptyPlatforms:
    """SteamDT lists platforms it doesn't cover (CSMONEY/DMARKET/SKINPORT/WAXPEER) with
    sellPrice/biddingPrice both 0 -> both None after adapter parsing (see
    adapters/steamdt/models.py's _money). Real data is ~1500 such rows/day; they must
    not be written."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def get_prices_batch(self, names):
        self.calls.append(list(names))
        out = []
        for n in names:
            out.append(ItemPrices(n, [
                PlatformPrice("YOUPIN", "", D("100"), 3, D("90"), 2, None),
                PlatformPrice("CSMONEY", "", None, 0, None, 0, None),
                PlatformPrice("DMARKET", "", None, 0, None, 0, None),
            ]))
        return out


def test_snapshot_skips_platforms_with_no_price_data_at_all(db_session):
    seed(db_session, n_templates=1, unmapped=0)
    dt = FakeDTWithEmptyPlatforms()
    rep = take_price_snapshot(db_session, dt, day=DAY, batch_size=100, now=utcnow(), sleep=lambda s: None)
    assert rep.fetched == 1  # the template is still counted as returned by SteamDT
    rows = db_session.scalars(select(PriceDaily).where(PriceDaily.template_id == 1)).all()
    assert [r.platform for r in rows] == ["YOUPIN"]  # CSMONEY/DMARKET (no data) never written
    assert rep.rows == 1


class FakeDTAllEmpty:
    """A template whose SteamDT response has no platform data at all."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def get_prices_batch(self, names):
        self.calls.append(list(names))
        return [ItemPrices(n, [PlatformPrice("CSMONEY", "", None, 0, None, 0, None)]) for n in names]


def test_a_template_with_no_platform_data_writes_no_rows_and_is_retried_same_day(db_session):
    seed(db_session, n_templates=1, unmapped=0)
    rep1 = take_price_snapshot(db_session, FakeDTAllEmpty(), day=DAY, batch_size=100, now=utcnow(),
                               sleep=lambda s: None)
    assert rep1.fetched == 1 and rep1.rows == 0
    assert not db_session.scalars(select(PriceDaily).where(PriceDaily.template_id == 1)).all()
    # Rerun same day: since no row was written, the template isn't "already done" and
    # gets retried.
    dt2 = FakeDTAllEmpty()
    rep2 = take_price_snapshot(db_session, dt2, day=DAY, batch_size=100, now=utcnow(), sleep=lambda s: None)
    assert dt2.calls == [["hash 1"]]
    assert rep2.already == 0 and rep2.fetched == 1 and rep2.rows == 0


def test_snapshot_also_covers_extra_template_ids(db_session):
    """盈亏 spec §5.1：持仓模板（不一定有活跃求购单）也存价格。"""
    seed(db_session)
    db_session.add(Item(hash_name="held", cn_name=None, youpin_template_id=500))
    db_session.commit()
    dt = FakeDT()
    rep = take_price_snapshot(db_session, dt, day=DAY, batch_size=100, now=utcnow(), sleep=lambda s: None,
                              extra_template_ids={500, 99})
    assert rep.templates == 6 and "held" in dt.calls[0] and "hash 99" in dt.calls[0]
