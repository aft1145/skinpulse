"""BUFF 名次（BUFF spec §5）：用 2026-11-24 实测的翔鹤队列（条件单、普通单混排）。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from skinpulse.adapters.buff.models import BuffQueueRow, Specific, parse_queue
from skinpulse.core.buff_rank import (
    Candidate, covers_me, fingerprint, label_of_fingerprint, order_no, pick_candidates, queue_key, rank_in_queue,
    raw_order_id,
)
from skinpulse.core.messages import queue_label_of
from skinpulse.core.rank import queue_key_from_str

ROWS = parse_queue(json.loads((Path(__file__).parent / "fixtures/buff/queue_mixed.json").read_text("utf-8"))["data"]).rows
MINE = "261123T1000000003"
T = datetime.fromtimestamp(1795425061, tz=timezone.utc)
W078 = (Specific("paintwear", "磨损: 0.07-0.08", ("0.07", "0.08")),)


def test_plain_order_ranks_among_plain_only_and_counts_conditional_ahead():
    r = rank_in_queue(ROWS, my_id=MINE, price=Decimal("101"), created=T, specific=(), complete=True)
    assert r.rank == 2 and r.top == Decimal("102") and r.cond_ahead == 7 and r.found and r.seen == 28


def test_same_price_earlier_is_ahead_later_is_not():
    # 两张 80 的普通单：建单早的那张在前
    early, late = [x for x in ROWS if x.price == Decimal("80")]
    assert early.created_at < late.created_at
    r_late = rank_in_queue(ROWS, my_id=late.order_id, price=late.price, created=late.created_at, specific=(), complete=True)
    r_early = rank_in_queue(ROWS, my_id=early.order_id, price=early.price, created=early.created_at, specific=(),
                            complete=True)
    assert r_late.rank == r_early.rank + 1


def test_conditional_order_only_compares_identical_conditions():
    r = rank_in_queue(ROWS, my_id="x", price=Decimal("145"), created=T, specific=W078, complete=True)
    # 0.07-0.08 的单有 151、150、140：我 145 排第 3，榜首 151；挡路的其它单没有（都比 145 便宜）
    assert r.rank == 3 and r.top == Decimal("151") and r.cond_ahead == 0 and not r.found


def test_incomplete_queue_gives_no_rank():
    r = rank_in_queue(ROWS[:10], my_id="x", price=Decimal("1"), created=T, specific=(), complete=False)
    assert r.rank is None and r.seen == 10


def test_covers_me():
    assert covers_me(ROWS, my_id=MINE, price=Decimal("101"), page_full=True)
    assert covers_me(ROWS[:5], my_id="x", price=Decimal("101"), page_full=False)
    assert not covers_me(ROWS[:5], my_id="x", price=Decimal("101"), page_full=True)
    assert covers_me(ROWS[:12], my_id="x", price=Decimal("95"), page_full=True)   # 第 12 行 92 < 95


def test_keys_labels_and_order_numbers():
    assert fingerprint(()) == "plain" and queue_key(1134183, ()) == "buff:1134183:plain"
    assert queue_key(1, W078) == "buff:1:paintwear=0.07-0.08"
    assert label_of_fingerprint("paintwear=0.07-0.08") == "磨损 0.07-0.08"
    k = queue_key_from_str("buff:1:paintwear=0.07-0.08")
    assert queue_label_of(k.abrade, k.type_id, k.special, k.fade) == "磨损 0.07-0.08"
    k = queue_key_from_str("buff:1:plain")
    assert queue_label_of(k.abrade, k.type_id, k.special, k.fade) == "不限"
    assert raw_order_id(order_no("261123T1")) == "261123T1"


def _c(no, price, bmp, *, plain=True, alerting=False, last=None):
    return Candidate(no, Decimal(price), None if bmp is None else Decimal(bmp), plain, alerting, last)


def test_pick_candidates_order_and_budget():
    now = datetime(2026, 11, 24, tzinfo=timezone.utc)
    orders = [
        _c("a", "3", "3.4", last=now),                       # 不是第 1，数过
        _c("b", "101", "101", last=now),                     # 可能第 1
        _c("c", "6", "6.7", alerting=True, last=now),        # 告警中
        _c("d", "14", "19.4", last=None),                    # 从没数过
        _c("e", "2", "6.8", last=now - timedelta(hours=1)),  # 很久没数
        _c("f", "50", None, plain=False, last=now),          # 条件单：只能轮换
    ]
    assert pick_candidates(orders, 10) == ["b", "c", "d", "e", "a", "f"]
    assert pick_candidates(orders, 3) == ["b", "c", "d"]
    assert pick_candidates(orders, 0) == []


def test_queue_row_without_created_at_is_not_ahead_at_same_price():
    rows = [BuffQueueRow("o", Decimal("5"), 1, (), None)]
    r = rank_in_queue(rows, my_id="m", price=Decimal("5"), created=T, specific=(), complete=True)
    assert r.rank == 1
