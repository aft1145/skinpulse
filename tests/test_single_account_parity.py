"""只有一个号时，多账号改动前后一个字不变（多账号 spec §11）。

快照 `fixtures/parity/single_account.json` 是在多账号改动**之前**录下的。改动后新加的字段（`accounts`、
`account_id`、`own_ahead`）从比对里剔掉，其余必须逐字相同。要重录（只在有意改了单号行为时）：
`SKINPULSE_UPDATE_PARITY=1 uv run pytest tests/test_single_account_parity.py`。"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from skinpulse.analysis.brief import TopRow, TopTable
from skinpulse.config import NtfySettings
from skinpulse.core.messages import (
    MarketView, OrderView, TradeView, build_brief_message, build_enter_top2_message, build_fill_message,
    build_in_trade_message, build_market_message,
)
from skinpulse.web.overview import meta_payload, overview_payload
from tests.test_web_overview import NOW, FakeSecrets, _jwt, _order, _rt, _snap, _with_round

SNAPSHOT = Path(__file__).parent / "fixtures" / "parity" / "single_account.json"
NEW_KEYS = {"accounts", "account_id", "own_ahead", "platform", "cond_ahead", "buff_url"}  # BUFF spec §8 加了后三个
TZ = "Asia/Shanghai"
NS = NtfySettings()


def _strip(x):
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items() if k not in NEW_KEYS}
    if isinstance(x, list):
        return [_strip(v) for v in x]
    return x


def _msg(m) -> dict:
    return {"title": m.title, "body": m.body, "html": getattr(m, "html", None), "click": m.click}


def _ov(no, rank, top, queue="不限"):
    return OrderView(purchase_no=no, commodity_name="AK-47 | 红线 (久经沙场)", template_id=1414, queue_label=queue,
                     my_price=Decimal("100"), rank=rank, top1_price=None if top is None else Decimal(top),
                     top2_price=None, top1_qty=None, sell_min=Decimal("120"), remaining_qty=1, sell_depth=3)


def _tv(no, queue="不限"):
    at = NOW - timedelta(minutes=30)
    return TradeView(purchase_no=no, commodity_name="AWP | 树蝰 (崭新出厂)", template_id=99, queue_label=queue,
                     my_price=Decimal("150"), qty=1, at=at, detected_at=NOW)


def _mv(no, flags):
    return MarketView(purchase_no=no, commodity_name="M4A4 | 涡轮 (略有磨损)", template_id=7, my_price=Decimal("13"),
                      rank=2, top=Decimal("16.6"), sell_min=Decimal("17.9"), sell_depth=10, flags=frozenset(flags),
                      margin=Decimal("0.05"))


def _row(no, rank):
    return TopRow(purchase_no=no, name="FN57 | 怒氓 (破损不堪)", my_price=Decimal("18"), rank=rank, open_rank=2,
                  top1_price=Decimal("18"), open_top1=Decimal("19"), sell_price=None, is_new=False,
                  rank_changed=True, top1_changed=True)


def _messages() -> dict:
    return {
        "rank1_one": _msg(build_enter_top2_message([_ov("P1", 1, "100")], NS, triggered_at=NOW, tz=TZ, enter_rank=1)),
        "rank1_many": _msg(build_enter_top2_message([_ov("P1", 1, "100"), _ov("P2", 1, "100", "0.15-0.18")], NS,
                                                    triggered_at=NOW, tz=TZ, enter_rank=1)),
        "in_trade_one": _msg(build_in_trade_message([_tv("P1")], NS, tz=TZ)),
        "in_trade_many": _msg(build_in_trade_message([_tv("P1"), _tv("P2", "崭新")], NS, tz=TZ)),
        "fill_one": _msg(build_fill_message([_tv("P1")], NS, tz=TZ)),
        "fill_many": _msg(build_fill_message([_tv("P1"), _tv("P2")], NS, tz=TZ)),
        "market_one": _msg(build_market_message([_mv("P1", {"near"})], NS, checked_at=NOW, tz=TZ)),
        "market_many": _msg(build_market_message([_mv("P1", {"near"}), _mv("P2", {"inverted"})], NS,
                                                 checked_at=NOW, tz=TZ)),
        "brief": _msg(build_brief_message(date(2026, 9, 27), TopTable([_row("P1", 1), _row("P2", 2)], 2), top_rank=2)),
    }


def _dashboard(tmp_path, settings) -> dict:
    secrets = FakeSecrets()
    secrets.data["youpin_token"] = _jwt(NOW + timedelta(days=3))
    rows = [
        _order("P1", 100), _snap("P1", NOW - timedelta(hours=1), 1, 110),
        _order("P2", 50), _snap("P2", NOW - timedelta(hours=1), 2, 60),
        _order("P3", 9), _snap("P3", NOW - timedelta(hours=1), None, 30, beyond=20),
    ]
    rt, _ = _rt(tmp_path, settings, rows, secrets=secrets)
    _with_round(rt, NOW - timedelta(minutes=4), last_round={"at": NOW, "checked": 3, "errors": 0})
    return {"meta": meta_payload(rt, now=NOW), "overview": overview_payload(rt, now=NOW)}


def _current(tmp_path, settings) -> dict:
    return json.loads(json.dumps({"messages": _messages(), **_dashboard(tmp_path, settings)},
                                 ensure_ascii=False, default=str))


def test_single_account_output_is_unchanged(tmp_path, settings):
    got = _current(tmp_path, settings)
    if os.environ.get("SKINPULSE_UPDATE_PARITY"):
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(got, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    want = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert _strip(got) == _strip(want)


def test_snapshot_was_recorded_with_a_token_and_a_round():
    want = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert want["meta"]["token"]["days_left"] == 3
    assert want["meta"]["service"]["healthy"] is True
    assert [o["purchase_no"] for o in want["overview"]["orders"]][:2] == ["P1", "P2"]
    assert datetime.fromisoformat(want["meta"]["now"].replace("Z", "+00:00")).tzinfo == timezone.utc
