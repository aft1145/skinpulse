"""services.fills.resolve_gone：消失的求购单去「已完成」(40)、「暂停中」(30) 里查去向（成交推送 spec §4.2）。"""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from skinpulse.adapters.youpin.errors import RiskControl
from skinpulse.adapters.youpin.models import MyPurchaseOrder
from skinpulse.db.models import OrderEvent
from skinpulse.services.fills import parse_youpin_time, resolve_gone
from tests.conftest import load_fixture

NOW = datetime(2026, 10, 2, 2, 30, tzinfo=timezone.utc)      # 10-02 10:30 上海


def _row(no, status, *, qty=1, bought=None, created="2026.10.02 09:55:14"):
    raw = {"orderNo": no, "status": status, "quantity": qty,
           "buyQuantity": qty if bought is None else bought, "createTime": created, "rank": None}
    return MyPurchaseOrder(no, 1, "", "X", Decimal("5"), qty, 0, None, -1, None, None, raw)


def _filler(status, n):
    return [_row(f"OTHER-{status}-{i}", status) for i in range(n)]


class PagedClient:
    """按 (status, 第几页) 返回行；每页 40 条，最后一页不满。记下每次请求。"""

    def __init__(self, pages: dict[int, list[list]], fail_at=None):
        self.pages = pages
        self.calls: list[tuple[int, int]] = []
        self.sleeps: list[float] = []
        self.fail_at = fail_at
        self.s = SimpleNamespace(page_sleep_s=2.5)

    def sleep(self, s):
        self.sleeps.append(s)

    def search_my_purchase_orders_page(self, status, page_index, page_size=40):
        self.calls.append((status, page_index))
        if self.fail_at == (status, page_index):
            raise RiskControl("CODE", "风控")
        pages = self.pages.get(status, [])
        return pages[page_index - 1] if page_index <= len(pages) else []


def _gone(s, no, *, at=NOW - timedelta(minutes=10), notice=None, qty_before=1, in_trade_at=None):
    ev = OrderEvent(purchase_no=no, template_id=1, kind="gone", qty_before=qty_before, qty_after=None,
                    price=Decimal("5"), at=at, notice=notice, in_trade_at=in_trade_at)
    s.add(ev)
    s.commit()
    return ev


def _outcomes(s):
    return {e.purchase_no: (e.outcome, e.notice) for e in s.scalars(select(OrderEvent).order_by(OrderEvent.id))}


def test_parse_youpin_time_is_local_to_settings_timezone():
    assert parse_youpin_time("2026.10.02 09:55:14", "Asia/Shanghai") == datetime(2026, 10, 2, 1, 55, 14, tzinfo=timezone.utc)
    assert parse_youpin_time(None, "Asia/Shanghai") is None
    assert parse_youpin_time("10/02 09:55", "Asia/Shanghai") is None


def test_found_in_done_list_becomes_a_pending_fill(db_session, settings):
    _gone(db_session, "A")
    client = PagedClient({40: [_filler(40, 39) + [_row("A", 40)]]})
    rep = resolve_gone(db_session, client, settings, now=NOW)
    ev = db_session.scalar(select(OrderEvent))
    assert (ev.outcome, ev.outcome_qty, ev.notice) == ("done", 1, "pending")
    assert ev.outcome_at == datetime(2026, 10, 2, 1, 55, 14, tzinfo=timezone.utc)
    assert client.calls == [(40, 1)]                    # 找到了就不再翻 30
    assert (rep.pending, rep.done, rep.paused, rep.missing) == (1, 1, 0, 0)


def test_in_trade_push_already_sent_keeps_fill_skipped(db_session, settings):
    _gone(db_session, "A", notice="skipped")
    resolve_gone(db_session, PagedClient({40: [[_row("A", 40)]]}), settings, now=NOW)
    assert _outcomes(db_session)["A"] == ("done", "skipped")


def test_paused_and_missing(db_session, settings):
    _gone(db_session, "P", notice="skipped")             # 交易中推过了、最后却是被暂停：不是成交，不推
    _gone(db_session, "M")
    client = PagedClient({40: [_filler(40, 40), _filler(40, 3)],
                          30: [_filler(30, 10) + [_row("P", 30, bought=0, created="2026.10.01 18:29:50")]]})
    rep = resolve_gone(db_session, client, settings, now=NOW)
    out = _outcomes(db_session)
    assert out == {"P": ("paused", None), "M": ("missing", None)}
    p = db_session.scalar(select(OrderEvent).where(OrderEvent.purchase_no == "P"))
    assert p.outcome_at == datetime(2026, 10, 1, 10, 29, 50, tzinfo=timezone.utc)
    # 每个列表只翻一遍；每一页之前都按 page_sleep_s 等（第一页前面紧挨着同步请求）
    assert client.calls == [(40, 1), (40, 2), (30, 1)]
    assert client.sleeps == [2.5, 2.5, 2.5]
    assert (rep.paused, rep.missing, rep.pages) == (1, 1, 3)


def test_page_cap_leaves_it_unresolved(db_session, settings, caplog):
    _gone(db_session, "A")
    settings.fills.max_pages = 2
    full = [_filler(40, 40)] * 5
    client = PagedClient({40: full, 30: [_filler(30, 40)] * 5})
    with caplog.at_level(logging.WARNING):
        rep = resolve_gone(db_session, client, settings, now=NOW)
    assert _outcomes(db_session) == {"A": (None, None)}          # 没翻完：不能断定是撤单
    assert client.calls == [(40, 1), (40, 2), (30, 1), (30, 2)]
    assert rep.incomplete == 1 and "max_pages" in caplog.text


def test_old_events_are_left_for_backfill(db_session, settings):
    _gone(db_session, "OLD", at=NOW - timedelta(hours=30))
    client = PagedClient({40: [[_row("OLD", 40)]]})
    rep = resolve_gone(db_session, client, settings, now=NOW)
    assert client.calls == [] and rep.pending == 0
    assert _outcomes(db_session) == {"OLD": (None, None)}        # 不改成 missing：留给 fills backfill
    rep = resolve_gone(db_session, client, settings, now=NOW, backfill=True)
    assert _outcomes(db_session) == {"OLD": ("done", None)}      # 回填只写去向，绝不推送
    assert rep.done == 1


def test_nothing_pending_makes_no_request(db_session, settings):
    client = PagedClient({})
    resolve_gone(db_session, client, settings, now=NOW)
    assert client.calls == []


def test_risk_control_propagates_but_keeps_what_was_found(db_session, settings):
    _gone(db_session, "A")
    _gone(db_session, "B")
    client = PagedClient({40: [_filler(40, 39) + [_row("A", 40)], _filler(40, 40)]}, fail_at=(40, 2))
    with pytest.raises(RiskControl):
        resolve_gone(db_session, client, settings, now=NOW)
    db_session.expire_all()
    assert _outcomes(db_session) == {"A": ("done", "pending"), "B": (None, None)}


def test_bought_less_than_quantity_warns(db_session, settings, caplog):
    _gone(db_session, "A", qty_before=2)
    with caplog.at_level(logging.WARNING):
        resolve_gone(db_session, PagedClient({40: [[_row("A", 40, qty=3, bought=2)]]}), settings, now=NOW)
    assert _outcomes(db_session)["A"] == ("done", "pending")
    assert "buyQuantity" in caplog.text


def test_same_order_gone_twice_resolves_both_events(db_session, settings):
    _gone(db_session, "A", at=NOW - timedelta(hours=3))
    _gone(db_session, "A", at=NOW - timedelta(minutes=5))
    resolve_gone(db_session, PagedClient({40: [[_row("A", 40)]]}), settings, now=NOW)
    assert [e.outcome for e in db_session.scalars(select(OrderEvent))] == ["done", "done"]


def test_real_fixture_rows_resolve(db_session, settings):
    rows = [MyPurchaseOrder.from_api(x) for x in load_fixture("youpin/my_orders_status40.json")["data"]]
    _gone(db_session, "TEST-DONE-2")                     # AWP | 树蝰，09-28 15:59:06 成交
    resolve_gone(db_session, PagedClient({40: [rows]}), settings, now=NOW)
    ev = db_session.scalar(select(OrderEvent))
    assert ev.outcome == "done" and ev.outcome_at == datetime(2026, 9, 28, 7, 59, 6, tzinfo=timezone.utc)


def test_excluded_events_are_not_looked_up(db_session, settings):
    ev = _gone(db_session, "A")
    client = PagedClient({40: [[_row("A", 40)]]})
    rep = resolve_gone(db_session, client, settings, now=NOW, exclude_ids={ev.id})
    assert client.calls == [] and rep.pending == 0


def test_incomplete_report_lists_event_ids(db_session, settings):
    ev = _gone(db_session, "A")
    settings.fills.max_pages = 1
    rep = resolve_gone(db_session, PagedClient({40: [_filler(40, 40)] * 3, 30: [_filler(30, 40)] * 3}), settings, now=NOW)
    assert rep.incomplete_ids == [ev.id]


# ---- 推测成交（intent 2026-11-23）：交易中过、之后哪个列表里都没有 ----

def test_in_trade_then_vanished_is_a_likely_fill(db_session, settings):
    from skinpulse.db.models import MyBuyOrder

    seen = NOW - timedelta(minutes=20)
    db_session.add(MyBuyOrder(purchase_no="T", template_id=1, price=Decimal("13"), qty=1, remaining_qty=1, active=False, queue_key="1|ANY", synced_at=seen,
                              conditions_json='{"rank": "*", "createTime": "2026.10.02 10:08:30"}'))
    db_session.add(MyBuyOrder(purchase_no="U", template_id=1, price=Decimal("13"), qty=1, remaining_qty=1, active=False, queue_key="1|ANY", synced_at=seen,
                              conditions_json="not json"))
    _gone(db_session, "T", notice="skipped", in_trade_at=seen)     # 交易中推过
    _gone(db_session, "U", in_trade_at=seen)                       # 交易中没推出去（原始记录也坏了）
    _gone(db_session, "M")                                         # 从没交易中过：撤单
    client = PagedClient({40: [_filler(40, 3)], 30: [_filler(30, 3)]})
    rep = resolve_gone(db_session, client, settings, now=NOW)
    # 推测成交绝不推送：交易中推过的保持 skipped，没推过的也不补推
    assert _outcomes(db_session) == {"T": ("likely_done", "skipped"), "U": ("likely_done", None), "M": ("missing", None)}
    evs = {e.purchase_no: e for e in db_session.scalars(select(OrderEvent))}
    assert evs["T"].outcome_at == datetime(2026, 10, 2, 2, 8, 30, tzinfo=timezone.utc)   # 被卖的时刻（createTime）
    assert evs["U"].outcome_at == seen                                                  # 没有就用看到 * 的时刻
    assert evs["T"].outcome_qty is None
    assert (rep.likely, rep.missing) == (2, 1)


def test_in_trade_but_found_in_done_list_is_a_real_fill(db_session, settings):
    _gone(db_session, "A", notice="skipped", in_trade_at=NOW - timedelta(minutes=20))
    resolve_gone(db_session, PagedClient({40: [[_row("A", 40)]]}), settings, now=NOW)
    assert _outcomes(db_session) == {"A": ("done", "skipped")}
