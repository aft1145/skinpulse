"""盈亏每日任务（盈亏 spec §5）：同步、补详情、对账、快照、出错处理。"""
import threading
from datetime import date, datetime, timezone
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from skinpulse.adapters.youpin.errors import LoginRequired, RiskControl
from skinpulse.adapters.youpin.models import (
    Inventory, InventoryItem, LeaseOutOrder, LeaseOutPage, TradeOrder as ApiTrade, TradeOrderDetail, UserInfo,
)
from skinpulse.db.models import Item, PnlDaily, PnlPosition, PriceDaily, TradeOrder
from skinpulse.services import status as st
from skinpulse.services.account import AccountRuntime
from skinpulse.services.pnl import (
    fill_sell_details, holding_template_ids, run_pnl, sync_trades, write_snapshots, HoldingsState,
)

NOW = datetime(2026, 11, 24, 17, 0, tzinfo=timezone.utc)      # 本地 11-25 01:00
DAY = date(2026, 11, 24)


def write_snapshot(session, settings, acc, day, state, now):
    """一个悠悠号、没有 BUFF 时的快照（多数测试用）。"""
    return write_snapshots(session, settings, day, {acc: state}, {}, [acc], now)[acc]


def ms(y, mo, d, h=12):
    return int(datetime(y, mo, d, h, tzinfo=timezone.utc).timestamp() * 1000)


def api_row(no, side, tid, cents, at_ms, qty=1, status=340, name=None):
    return ApiTrade.from_api({
        "orderNo": no, "orderStatus": status, "orderSubType": 5, "totalAmount": cents, "commodityNum": qty,
        "createOrderTime": at_ms, "finishOrderTime": at_ms + 60_000,
        "buyerUserName": "SECRET_NICK", "sellerSteamId": "SECRET_STEAM", "tradeOfferId": "SECRET_OFFER",
        "productDetail": {"commodityTemplateId": tid, "commodityHashName": f"H{tid}", "commodityName": name or f"饰品{tid}",
                          "assertId": 123, "sellerUserName": "SECRET_NICK"}}, side)


class FakeClient:
    def __init__(self, buy_pages=(), sell_pages=(), revenue=None, inventory=None, leases=(), fail=None, lock=None):
        self.s = SimpleNamespace(page_sleep_s=2.5)
        self.slept = []
        self.calls = []
        self.pages = {"buy": list(buy_pages), "sell": list(sell_pages)}
        self.revenue = revenue or {}
        self.inventory = inventory or Inventory([], 0, None, None)
        self.leases = list(leases)
        self.fail = fail
        self.lock = lock
        self.lock_held = []

    def sleep(self, s):
        self.slept.append(s)

    def _hit(self, name):
        self.calls.append(name)
        if self.lock is not None:
            self.lock_held.append(self.lock.locked())
        if self.fail and self.fail[0] == name:
            raise self.fail[1]

    def search_trade_orders_page(self, side, page):
        self._hit(f"{side}:{page}")
        pages = self.pages[side]
        return pages[page - 1] if page <= len(pages) else []

    def get_user_info(self):
        self._hit("user")
        return UserInfo(42, "me")

    def get_trade_order_detail(self, no, uid):
        self._hit(f"detail:{no}")
        assert uid == 42
        return TradeOrderDetail(self.revenue.get(no), None)

    def get_inventory_trend(self):
        self._hit("inventory")
        return self.inventory

    def search_lease_out_page(self, page):
        self._hit(f"lease:{page}")
        rows = self.leases[(page - 1) * 2: page * 2]
        return LeaseOutPage(rows, has_next=page * 2 < len(self.leases), total=len(self.leases))


FEES = {"YOUPIN": 0.01, "BUFF": 0.015}


def full_page(prefix, side, n=20, start_day=20):
    return [api_row(f"{prefix}{i}", side, 1, 1000, ms(2026, 11, start_day) - i * 1000) for i in range(n)]


def test_sync_stops_at_known_order_and_skips_non_done(db_session):
    c = FakeClient(buy_pages=[full_page("B", "buy"), full_page("C", "buy")],
                   sell_pages=[[api_row("S1", "sell", 1, 3000, ms(2026, 11, 22)),
                                api_row("S2", "sell", 1, 3000, ms(2026, 11, 22), status=200)]])
    assert sync_trades(db_session, 1, c, fees=FEES, max_pages=10, now=NOW) == (40, 1, 4)
    assert c.calls == ["buy:1", "buy:2", "buy:3", "sell:1"] and c.slept == [2.5] * 3
    # 第二次：第 1 页就碰到已入库的单，停
    c2 = FakeClient(buy_pages=[[api_row("NEW", "buy", 2, 500, ms(2026, 11, 23))] + full_page("B", "buy", 19)],
                    sell_pages=[[api_row("S1", "sell", 1, 3000, ms(2026, 11, 22))]])
    # 第 1 页有新有旧：还要看第 2 页（晚完成的单可能排在已知单后面）；第 2 页空了才停。卖出第 1 页全是已知：停
    assert sync_trades(db_session, 1, c2, fees=FEES, max_pages=10, now=NOW) == (1, 0, 3)
    assert c2.calls == ["buy:1", "buy:2", "sell:1"]
    sell = db_session.scalar(select(TradeOrder).where(TradeOrder.order_no == "S1"))
    assert (sell.amount, sell.net_amount, sell.net_exact) == (D("30.00"), D("29.70"), False)
    buy = db_session.scalar(select(TradeOrder).where(TradeOrder.order_no == "NEW"))
    assert (buy.net_amount, buy.net_exact, buy.hash_name, buy.asset_id) == (D("5.00"), True, "H2", "123")


def test_sync_respects_max_pages(db_session):
    c = FakeClient(buy_pages=[full_page(f"P{p}-", "buy") for p in range(5)])
    sync_trades(db_session, 1, c, fees=FEES, max_pages=2, now=NOW)
    assert [x for x in c.calls if x.startswith("buy")] == ["buy:1", "buy:2"]


def test_counterparty_fields_never_reach_the_db(db_session):
    c = FakeClient(buy_pages=[[api_row("B1", "buy", 1, 1000, ms(2026, 11, 20))]])
    sync_trades(db_session, 1, c, fees=FEES, max_pages=1, now=NOW)
    dump = "\n".join(str(r) for t in ("trade_order", "item") for r in db_session.execute(text(f"SELECT * FROM {t}")))
    assert "SECRET" not in dump


def test_item_upsert_fills_mapping_without_overwriting(db_session):
    db_session.add(Item(hash_name="H1", cn_name="旧名", youpin_template_id=None))
    db_session.commit()
    c = FakeClient(buy_pages=[[api_row("B1", "buy", 1, 1000, ms(2026, 11, 20)),
                               api_row("B2", "buy", 2, 1000, ms(2026, 11, 20), name="新饰品")]])
    sync_trades(db_session, 1, c, fees=FEES, max_pages=1, now=NOW)
    items = {i.hash_name: (i.cn_name, i.youpin_template_id) for i in db_session.scalars(select(Item))}
    assert items == {"H1": ("旧名", 1), "H2": ("新饰品", 2)}


def test_fill_sell_details_sets_exact_revenue(db_session):
    c = FakeClient(sell_pages=[[api_row("S1", "sell", 1, 1750, ms(2026, 11, 22)),
                                api_row("S2", "sell", 1, 1000, ms(2026, 11, 21))]], revenue={"S1": D("17.33")})
    sync_trades(db_session, 1, c, fees=FEES, max_pages=1, now=NOW)
    assert fill_sell_details(db_session, 1, c, limit=10) == 1
    rows = {r.order_no: (r.net_amount, r.net_exact) for r in db_session.scalars(select(TradeOrder))}
    assert rows == {"S1": (D("17.33"), True), "S2": (D("9.90"), False)}
    assert fill_sell_details(db_session, 1, FakeClient(), limit=0) == 0


def _seed_ledger(db_session):
    c = FakeClient(buy_pages=[[api_row("B1", "buy", 1, 10000, ms(2026, 11, 1)),
                               api_row("B2", "buy", 1, 20000, ms(2026, 11, 2)),
                               api_row("B3", "buy", 2, 5000, ms(2026, 11, 3), qty=2),
                               api_row("B4", "buy", 3, 800, ms(2026, 11, 4))]],
                   sell_pages=[[api_row("S1", "sell", 1, 15000, ms(2026, 11, 24, 3)),
                                api_row("S0", "sell", 9, 999, ms(2026, 11, 5))]])
    sync_trades(db_session, 1, c, fees=FEES, max_pages=1, now=NOW)


def _price(db_session, tid, day, yp, count=50, buff=None):
    db_session.add(PriceDaily(date=day, template_id=tid, market_hash_name=f"H{tid}", platform="YOUPIN",
                              sell_price=yp, sell_count=count, bidding_price=None, bidding_count=0, fetched_at=NOW))
    if buff is not None:
        db_session.add(PriceDaily(date=day, template_id=tid, market_hash_name=f"H{tid}", platform="BUFF",
                                  sell_price=buff, sell_count=10, bidding_price=None, bidding_count=0, fetched_at=NOW))
    db_session.commit()


def test_write_snapshot_positions_locations_and_totals(db_session, settings):
    _seed_ledger(db_session)
    _price(db_session, 1, "2026-11-24", D("180"))                    # 当天的快照
    _price(db_session, 2, "2026-11-20", D("30"), count=2)            # 往前找；在售少
    # 模板 3 没有 SteamDT 价，用库存的市价
    inv = Inventory([InventoryItem("a1", 1, "H1", D("170"), D("200")), InventoryItem("a2", 3, "H3", D("6"), None),
                     InventoryItem("a3", 77, "H77", D("5"), None)], 3, D("-30"), D("200"))
    state = HoldingsState(inv, [LeaseOutOrder("L1", "H2", D("0.05")), LeaseOutOrder("L2", "HX", D("0.02"))])
    daily = write_snapshot(db_session, settings, 1, DAY, state, NOW)
    pos = {p.template_id: p for p in db_session.scalars(select(PnlPosition))}
    assert set(pos) == {1, 2, 3}
    p1, p2, p3 = pos[1], pos[2], pos[3]
    assert (p1.qty, p1.cost, p1.inv_qty, p1.price, p1.price_src) == (1, D("200.00"), 1, D("180.00"), "steamdt")
    assert (p2.qty, p2.inv_qty, p2.lease_qty, p2.unknown_qty, p2.price_flag) == (2, 0, 1, 1, "thin")
    assert (p3.price, p3.price_src) == (D("6.00"), "inventory")
    # 已实现：S1 卖 150、到手估 148.50，配 B1（100）→ +48.50；S0 成本未知
    assert daily.realized_total == D("48.50") and daily.realized_day == D("48.50")
    assert (daily.qty, daily.cost, daily.value_mkt) == (4, D("258.00"), D("246.00"))
    assert daily.value_net == D("241.10")                            # 246 × 0.9801
    assert (daily.inv_qty, daily.lease_qty, daily.unknown_qty, daily.unknown_cost) == (2, 1, 1, D("25.00"))
    assert (daily.youpin_pnl, daily.youpin_pnl_n, daily.lease_count, daily.lease_rent_day) == (D("-30.00"), 1, 2, D("0.07"))
    assert (daily.unbooked_inv_qty, daily.unbooked_inv_value) == (1, D("5.00"))
    # 同一天重跑覆盖，不重复
    write_snapshot(db_session, settings, 1, DAY, state, NOW)
    assert len(db_session.scalars(select(PnlDaily)).all()) == 1 and len(db_session.scalars(select(PnlPosition)).all()) == 3
    assert holding_template_ids(db_session) == {1, 2, 3}


def test_snapshot_ignores_orders_after_the_day(db_session, settings):
    _seed_ledger(db_session)
    # 本地 11-25 00:30 卖掉了模板 3：不算进 11-24
    c = FakeClient(sell_pages=[[api_row("S9", "sell", 3, 900, int(datetime(2026, 11, 24, 16, 30, tzinfo=timezone.utc).timestamp() * 1000))]])
    sync_trades(db_session, 1, c, fees=FEES, max_pages=1, now=NOW)
    write_snapshot(db_session, settings, 1, DAY, HoldingsState(Inventory([], 0, None, None), []), NOW)
    assert db_session.scalar(select(PnlPosition).where(PnlPosition.template_id == 3)) is not None


# ---------- run_pnl ----------

class _Monitor:
    def __init__(self):
        self.paused = []

    def pause_after_error(self, session, acc, e):
        self.paused.append((acc.id, type(e).__name__))
        return "halt" if isinstance(e, RiskControl) else "skip"


def _rt(session_factory, settings, clients):
    mon = _Monitor()
    return SimpleNamespace(settings=settings, session_factory=session_factory, monitor=mon,
                           youpin_lock=threading.Lock(),
                           accounts={i: AccountRuntime(i, c) for i, c in clients.items()})


@pytest.fixture
def sf(db_session):
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(bind=db_session.get_bind())


def test_run_pnl_holds_the_account_lock_only_for_youpin_calls(sf, settings):
    rt = _rt(sf, settings, {})
    c = FakeClient(buy_pages=[[api_row("B1", "buy", 1, 1000, ms(2026, 11, 20))]], lock=rt.youpin_lock)
    rt.accounts = {1: AccountRuntime(1, c)}
    rep = run_pnl(rt, DAY, NOW)
    assert rep.snapshots == 1 and rep.new_buys == 1 and all(c.lock_held)
    assert c.calls == ["buy:1", "sell:1", "inventory", "lease:1"]
    assert not rt.youpin_lock.locked()


def test_run_pnl_skips_paused_and_logged_out_accounts(sf, settings):
    ok = FakeClient()
    lost = FakeClient(fail=("buy:1", LoginRequired("84101", "x")))
    rt = _rt(sf, settings, {1: ok, 2: lost, 3: FakeClient()})
    with sf() as s:
        st.mark_error(s, rt.accounts[3].status_key, "84104", datetime(2026, 11, 25, tzinfo=timezone.utc))
        s.commit()
    rep = run_pnl(rt, DAY, NOW)
    # 跨号配对（ADR-0025）：有号没拉成，这一天谁的快照都不写（账缺一块会算错）
    assert rep.snapshots == 0 and rep.skipped == ["2:login", "3:paused"] and rt.monitor.paused == []
    with sf() as s:
        assert s.scalar(select(PnlDaily)) is None


def test_risk_control_pauses_and_halts_without_writing_a_snapshot(sf, settings):
    first = FakeClient(fail=("inventory", RiskControl("84104", "risk")))
    second = FakeClient()
    rt = _rt(sf, settings, {1: first, 2: second})
    rep = run_pnl(rt, DAY, NOW)
    assert rt.monitor.paused == [(1, "RiskControl")] and second.calls == []
    assert rep.snapshots == 0
    with sf() as s:
        assert s.scalar(select(PnlDaily)) is None


def test_import_all_uses_wide_limits(sf, settings):
    c = FakeClient(buy_pages=[full_page(f"P{p}-", "buy") for p in range(12)])
    rt = _rt(sf, settings, {1: c})
    rep = run_pnl(rt, DAY, NOW, import_all=True)
    assert rep.new_buys == 240 and "buy:12" in c.calls


def test_import_fills_missing_prices_live_without_writing_price_daily(sf, settings):
    from skinpulse.adapters.steamdt.models import ItemPrices, PlatformPrice

    class DT:
        def __init__(self):
            self.calls = []

        def get_prices_batch(self, names):
            self.calls.append(list(names))
            return [ItemPrices(n, [PlatformPrice("YOUPIN", "", D("42"), 9, None, 0, None),
                                   PlatformPrice("BUFF", "", D("40"), 9, None, 0, None)]) for n in names]

    c = FakeClient(buy_pages=[[api_row("B1", "buy", 1, 1000, ms(2026, 11, 20)),
                               api_row("B2", "buy", 2, 1000, ms(2026, 11, 20))]])
    rt = _rt(sf, settings, {1: c})
    rt.steamdt = DT()
    with sf() as s:
        _price(s, 2, "2026-11-24", D("7"))
    rep = run_pnl(rt, DAY, NOW, import_all=True)
    assert rep.snapshots == 1 and rt.steamdt.calls == [["H1"]]       # 模板 2 当天已有快照价，不查
    with sf() as s:
        pos = {p.template_id: (p.price, p.price_src) for p in s.scalars(select(PnlPosition))}
        assert pos == {1: (D("42.00"), "steamdt"), 2: (D("7.00"), "steamdt")}
        assert {r.template_id for r in s.scalars(select(PriceDaily))} == {2}
    # 每日任务（不是导入）不现查
    rt.steamdt.calls.clear()
    run_pnl(rt, DAY, NOW)
    assert rt.steamdt.calls == []


def test_late_order_behind_known_ones_is_still_found(db_session):
    """评审 2026-11-24：一张完成得晚的单排在已知单后面（第 2 页），每日同步也要翻到它。"""
    sync_trades(db_session, 1, FakeClient(buy_pages=[full_page("B", "buy")]), fees=FEES, max_pages=10, now=NOW)
    page2 = full_page("B", "buy")[10:] + [api_row("LATE", "buy", 5, 700, ms(2026, 11, 1))] + full_page("Z", "buy", 9)
    c = FakeClient(buy_pages=[[api_row("N1", "buy", 1, 1000, ms(2026, 11, 23))] + full_page("B", "buy", 19), page2,
                              full_page("B", "buy")])
    sync_trades(db_session, 1, c, fees=FEES, max_pages=10, now=NOW)
    assert db_session.scalar(select(TradeOrder).where(TradeOrder.order_no == "LATE")) is not None
    assert c.calls[:3] == ["buy:1", "buy:2", "buy:3"]


def test_import_does_not_stop_at_known_orders(db_session):
    """首次导入中途断了：下次导入要接着翻到底，不能因为第 1 页都是已知单就停。"""
    first = [full_page(f"P{p}-", "buy") for p in range(3)]
    sync_trades(db_session, 1, FakeClient(buy_pages=first[:1]), fees=FEES, max_pages=1, now=NOW)
    c = FakeClient(buy_pages=first)
    assert sync_trades(db_session, 1, c, fees=FEES, max_pages=200, now=NOW, import_all=True)[0] == 40
    assert "buy:4" in c.calls


def test_inventory_pages_by_total_count_and_lease_cap_warns(caplog):
    from skinpulse.services import pnl as svc

    class C(FakeClient):
        def get_inventory_trend(self, page=1):
            self._hit(f"inventory:{page}")
            items = [InventoryItem(f"a{page}-{i}", 1, "H1", D("1"), None) for i in range(3 if page < 3 else 1)]
            return Inventory(items, 7, D("0"), D("0"))

        def search_lease_out_page(self, page):
            self._hit(f"lease:{page}")
            return LeaseOutPage([LeaseOutOrder("L", "H1", None)], has_next=True, total=999)

    c = C()
    with caplog.at_level("WARNING", logger="skinpulse.services.pnl"):
        state = svc.fetch_holdings_state(c)
    assert len(state.inventory.items) == 7 and "inventory:3" in c.calls
    assert len(state.leases) == svc.LEASE_MAX_PAGES and "lease list still has more" in caplog.text


def test_details_checked_once_and_user_info_failure_is_not_fatal(db_session):
    from skinpulse.adapters.youpin.errors import YoupinError

    c = FakeClient(sell_pages=[[api_row("S1", "sell", 1, 1750, ms(2026, 11, 22))]])
    sync_trades(db_session, 1, c, fees=FEES, max_pages=1, now=NOW)
    broken = FakeClient(fail=("user", YoupinError("nouserid", "x")))
    assert fill_sell_details(db_session, 1, broken, limit=10) == 0          # 不抛：快照照写
    c2 = FakeClient()                                                        # 详情里没有到手价
    assert fill_sell_details(db_session, 1, c2, limit=10) == 0 and "detail:S1" in c2.calls
    c3 = FakeClient()
    fill_sell_details(db_session, 1, c3, limit=10)
    assert c3.calls == []                                                    # 查过一次就不再查
    db_session.execute(text("UPDATE trade_order SET detail_checked = 0"))
    db_session.commit()
    with pytest.raises(LoginRequired):                                       # 掉登录照样往上抛（跳过这个号）
        fill_sell_details(db_session, 1, FakeClient(fail=("user", LoginRequired("84101", "x"))), limit=10)


def test_has_snapshot_requires_every_account(db_session, settings):
    from skinpulse.services.pnl import has_snapshot

    write_snapshot(db_session, settings, 1, DAY, HoldingsState(Inventory([], 0, None, None), []), NOW)
    assert has_snapshot(db_session, DAY, [1]) and not has_snapshot(db_session, DAY, [1, 2])
    assert not has_snapshot(db_session, DAY, [])


def test_import_skips_live_prices_while_another_task_uses_steamdt(sf, settings):
    """2026-11-24：暂停单检查（或选品）正在一分钟一批地查 SteamDT 时，导入不再插进去发 /price/batch。"""
    from skinpulse.db.models import PausedCheckRun

    class DT:
        calls = 0

        def get_prices_batch(self, names):
            DT.calls += 1
            return []

    with sf() as s:
        s.add(PausedCheckRun(started_at=NOW, status="running", source="dashboard", phase="prices", done=0, total=3))
        s.commit()
    msgs = []
    rt = _rt(sf, settings, {1: FakeClient(buy_pages=[[api_row("B1", "buy", 1, 1000, ms(2026, 11, 20))]])})
    rt.steamdt = DT()
    rep = run_pnl(rt, DAY, NOW, import_all=True, echo=msgs.append)
    assert rep.snapshots == 1 and DT.calls == 0
    assert any("暂停单检查正在用 SteamDT" in m for m in msgs)



# ---------- BUFF（盈亏接 BUFF plan，ADR-0025）----------

class FakeBuff:
    def __init__(self, sells=(), buys=(), assets=(), lock=None, fail=None):
        from skinpulse.adapters.buff.models import BillPage, BuffAsset, BuffBill, InventoryPage
        self._BillPage, self._InventoryPage = BillPage, InventoryPage
        self.sells = [BuffBill(no, 0, 1, "SUCCESS", "", D(str(price)), None, D(str(income)), at, at, None, h, f"中{h}")
                      for no, h, price, income, at in sells]
        self.buys = [BuffBill(no, 0, 2, "SUCCESS", "", D(str(price)), None, None, at, at, None, h, f"中{h}")
                     for no, h, price, at in buys]
        self.assets = [BuffAsset(aid, 0, h, "", False) for aid, h in assets]
        self.lock, self.fail, self.held = lock, fail, []

    def _hit(self, name):
        if self.lock is not None:
            self.held.append(self.lock.locked())
        if self.fail == name:
            raise RuntimeError("boom")

    def buy_history(self, page, size):
        self._hit("buy")
        return self._BillPage(self.buys if page == 1 else [], page, size)

    def sell_history(self, page, size):
        self._hit("sell")
        return self._BillPage(self.sells if page == 1 else [], page, size)

    def inventory(self, page, size):
        self._hit("inventory")
        return self._InventoryPage(self.assets, page, size, 1)


def _rt_buff(sf, settings, youpin_client, buff_client):
    from skinpulse.services.buff import BuffAccountRuntime
    rt = _rt(sf, settings, {1: youpin_client})
    rt.buff_accounts = {2: BuffAccountRuntime(2, buff_client)}
    rt.buff_lock = threading.Lock()
    return rt


def test_youpin_buy_buff_sell_and_buff_inventory(sf, settings):
    """悠悠买两件、BUFF 卖一件；BUFF 买一件还在 Steam 库存（BUFF、悠悠库存里是同一个 assetid，只算一次）。"""
    at = lambda d: datetime(2026, 11, d, 4, tzinfo=timezone.utc)  # noqa: E731
    yp = FakeClient(buy_pages=[[api_row("Y1", "buy", 1, 30000, ms(2026, 10, 1)),
                                api_row("Y2", "buy", 1, 30000, ms(2026, 10, 2))]],
                    inventory=Inventory([InventoryItem("A9", 7, "H7", D("50"), None)], 1, None, None))
    bf = FakeBuff(sells=[("BS1", "H1", 360, "355.87", at(5))], buys=[("BB1", "H7", 40, at(6))],
                  assets=[("A9", "H7")])
    rt = _rt_buff(sf, settings, yp, bf)
    bf.lock = rt.buff_lock
    rep = run_pnl(rt, DAY, NOW)
    assert rep.snapshots == 2 and all(bf.held)
    with sf() as s:
        d = {x.account_id: x for x in s.scalars(select(PnlDaily))}
        assert d[2].realized_total == D("55.87") and d[1].realized_total == D("0.00")     # 已实现记在卖出的号
        pos = {(p.account_id, p.platform, p.hash_name): p for p in s.scalars(select(PnlPosition))}
        assert pos[(1, "YOUPIN", "H1")].qty == 1                                        # 悠悠的两件被 BUFF 卖掉一件
        b = pos[(2, "BUFF", "H7")]
        assert (b.qty, b.inv_qty, b.unknown_qty) == (1, 1, 0)
        assert d[1].unbooked_inv_qty == 0                                               # 同一个 assetid 只算一次


def test_buff_failure_does_not_block_the_snapshot(sf, settings):
    rt = _rt_buff(sf, settings, FakeClient(buy_pages=[[api_row("Y1", "buy", 1, 1000, ms(2026, 11, 1))]]),
                  FakeBuff(fail="sell"))
    rep = run_pnl(rt, DAY, NOW)
    assert rep.snapshots == 2 and "buff2:RuntimeError" in rep.skipped


def test_buff_round_holds_the_buff_lock(settings):
    import skinpulse.scheduler as sch

    lock = threading.Lock()
    seen = []
    rt = SimpleNamespace(settings=settings, buff_lock=lock, monitor=object())

    def fake_round(m):
        seen.append(lock.locked())
        return SimpleNamespace(skipped_night=True, accounts=0)

    import skinpulse.services.buff_monitor as bm
    orig = bm.run_buff_round
    bm.run_buff_round = fake_round
    try:
        sch.job_buff_round(rt)
    finally:
        bm.run_buff_round = orig
    assert seen == [True] and not lock.locked()


def test_disabled_account_positions_do_not_take_inventory_and_missing_hash_is_filled(db_session, settings):
    """评审 2026-11-25：停用的号（不在要写的号里）的持仓不占库存；悠悠库存缺英文名时按 Item 补上，和持仓对得上。"""
    db_session.add(Item(hash_name="H1", cn_name=None, youpin_template_id=1))
    db_session.commit()
    c = FakeClient(buy_pages=[[api_row("B1", "buy", 1, 1000, ms(2026, 11, 1))]])
    sync_trades(db_session, 1, c, fees=FEES, max_pages=1, now=NOW)
    c3 = FakeClient(buy_pages=[[api_row("B3", "buy", 1, 1000, ms(2026, 10, 1))]])
    sync_trades(db_session, 3, c3, fees=FEES, max_pages=1, now=NOW)       # 号 3 更早买了同一件，现在停用了
    inv = Inventory([InventoryItem("a1", 1, "", D("12"), None)], 1, None, None)   # 没有英文名
    write_snapshots(db_session, settings, DAY, {1: HoldingsState(inv, [])}, {}, [1], NOW)
    [p] = db_session.scalars(select(PnlPosition)).all()
    assert (p.account_id, p.inv_qty, p.unknown_qty, p.price) == (1, 1, 0, D("12.00"))
