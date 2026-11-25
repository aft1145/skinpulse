"""给盈亏导入 BUFF 的买卖订单（BUFF spec §9）：只存 SUCCESS、到手价、停翻规则、对手信息不入库。"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from skinpulse.adapters.buff.models import BillPage, parse_bills, parse_inventory
from skinpulse.db.models import TradeOrder
from skinpulse.services.buff_pnl import PAGE, fetch_buff_holdings, sync_buff_trades
from tests.test_services_monitor import _setup

FX = Path(__file__).parent / "fixtures" / "buff"
BUYS = parse_bills(json.loads((FX / "buy_history.json").read_text("utf-8"))["data"]).bills
SELLS = parse_bills(json.loads((FX / "sell_history.json").read_text("utf-8"))["data"]).bills
NOW = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)


class _Client:
    def __init__(self, buys, sells):
        self.pages = {"buy": buys, "sell": sells}
        self.calls = []

    def _page(self, side, page, size):
        self.calls.append((side, page))
        rows = self.pages[side]
        return BillPage(bills=rows[(page - 1) * size: page * size], page_num=page, page_size=size)

    def buy_history(self, page, size):
        return self._page("buy", page, size)

    def sell_history(self, page, size):
        return self._page("sell", page, size)


def test_imports_success_only_with_income_and_hash_names(tmp_path):
    Session = _setup(tmp_path)
    with Session() as s:
        b, sl, pages = sync_buff_trades(s, 2, _Client(BUYS, SELLS), max_pages=5, now=NOW)
        assert (b, sl, pages) == (3, 3, 2)                      # FAIL 那笔不存
        rows = {r.order_no: r for r in s.scalars(select(TradeOrder))}
        sell = rows["260630T1000000044"]
        assert (sell.platform, sell.side, sell.account_id, sell.qty) == ("BUFF", "sell", 2, 1)
        assert sell.amount == Decimal("30") and sell.net_amount == Decimal("29.55") and sell.net_exact
        assert sell.hash_name == "P90 | Deathgaze (Battle-Scarred)" and sell.finished_at > sell.created_at
        assert sell.template_id == -1134230                       # 库里没有这件饰品：负 goods_id
        buy = rows["261027T1000000038"]
        assert buy.side == "buy" and buy.sub_type == 6 and buy.net_amount == buy.amount
        assert all(r.hash_name for r in rows.values())
        dump = json.dumps([[str(v) for v in vars(r).values()] for r in rows.values()], ensure_ascii=False)
        assert "U10000000" not in dump and "用户" not in dump


def test_paging_stops_on_a_fully_known_page_but_import_all_goes_to_the_end(tmp_path):
    Session = _setup(tmp_path)
    many = [replace(SELLS[0], bill_id=f"S{i:04d}") for i in range(PAGE * 2 + 5)]
    with Session() as s:
        c = _Client([], many)
        sync_buff_trades(s, 2, c, max_pages=10, now=NOW, import_all=True)
        assert ("sell", 3) in c.calls and s.query(TradeOrder).count() == PAGE * 2 + 5
        c = _Client([], many)
        sync_buff_trades(s, 2, c, max_pages=10, now=NOW)
        assert [x for x in c.calls if x[0] == "sell"] == [("sell", 1)]   # 第 1 页全是已知单：停
        late = [replace(SELLS[0], bill_id="NEW")] + many               # 晚到的单排在已知单前后都能接上
        c = _Client([], many[:PAGE - 1] + [late[0]] + many[PAGE - 1:])
        _, sl, _ = sync_buff_trades(s, 2, c, max_pages=10, now=NOW)
        assert sl == 1


def test_holdings_from_inventory(tmp_path):
    Session = _setup(tmp_path)
    inv = parse_inventory(json.loads((FX / "inventory.json").read_text("utf-8"))["data"])

    class C:
        def inventory(self, page, size):
            return replace(inv, total_page=1)
    with Session() as s:
        h = fetch_buff_holdings(s, C())
    assert [x.assetid for x in h] == ["90000000046", "90000000049", "90000000053"]
    assert h[0].hash_name == "Desert Eagle | Heat Treated (Minimal Wear)"
