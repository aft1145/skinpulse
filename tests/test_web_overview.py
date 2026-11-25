"""看板改版一期：/api/meta 和新的 /api/overview（spec §4.1、§5）。"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import event

from skinpulse.config import save_settings
from skinpulse.db.models import Account, Item, MyBuyOrder, RankSnapshot, SystemStatus
from skinpulse.db.session import make_engine, make_session_factory, upgrade_db
from skinpulse.runtime import Runtime
from skinpulse.secrets import KEY_YOUPIN_TOKEN
from skinpulse.services.monitor import ROUND, Monitor
from skinpulse.web.app import create_app
from skinpulse.web.overview import meta_payload, overview_payload

# 本地（Asia/Shanghai）09-27 12:00
NOW = datetime(2026, 9, 27, 4, 0, tzinfo=timezone.utc)
TODAY = NOW - timedelta(hours=1)                 # 09-27 11:00 本地
YESTERDAY = NOW - timedelta(days=1)              # 09-26 12:00 本地


class FakeSecrets:
    def __init__(self):
        self.data = {}

    def get(self, k):
        return self.data.get(k)

    def set(self, k, v):
        self.data[k] = v


def _jwt(exp: datetime) -> str:
    def seg(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{seg({'alg': 'HS256'})}.{seg({'exp': int(exp.timestamp())})}.sig"


def _order(no, price, *, name=None, tid=1414):
    return MyBuyOrder(purchase_no=no, template_id=tid, commodity_name=name or f"AK-47 | 红线 {no} (久经沙场)",
                      price=Decimal(str(price)), qty=1, remaining_qty=1, type_id=-1,
                      queue_key=f"{tid}|ANY|-1|ANY|ANY", synced_at=NOW, active=True)


def _snap(no, ts, rank, top1, beyond=None):
    return RankSnapshot(purchase_no=no, ts=ts, rank=rank, beyond=beyond,
                        top1_price=None if top1 is None else Decimal(str(top1)),
                        top2_price=None, top1_qty=1, sample_size=20)


def _rt(tmp_path, settings, rows=(), *, secrets=None):
    db = tmp_path / "o.db"
    upgrade_db(db)
    engine = make_engine(db)
    Session = make_session_factory(engine)
    with Session() as s:
        s.add(Account(id=1, user_id=1, nickname="me", token_valid=True))
        for r in rows:
            s.add(r)
        s.commit()
    sp = tmp_path / "settings.toml"
    save_settings(settings, sp)
    rt = Runtime(settings, sp, secrets or FakeSecrets(), Session, client=None, ntfy=None,
                 monitor=Monitor(Session, None, None, settings, sleep=lambda s: None))
    return rt, engine


def _standard(tmp_path, settings):
    rows = [
        _order("P1", 100), _snap("P1", TODAY, 1, 110),
        _order("P2", 50), _snap("P2", YESTERDAY, 1, 55), _snap("P2", TODAY, 2, 60),
        _order("P3", 10), _snap("P3", TODAY, 2, 11),
        _order("P4", 10), _snap("P4", TODAY, 2, 40),          # 榜首是 4 倍：被差价过滤
        _order("P5", 5), _snap("P5", YESTERDAY, 4, 6), _snap("P5", TODAY, 3, 6),
        _order("P6", 8),                                        # 从没被检查过
        _order("P7", 9), _snap("P7", TODAY, None, 30, beyond=20),
    ]
    return _rt(tmp_path, settings, rows)


# ---- /api/overview ----

def test_headline_counts_ranks_up_to_top_rank(tmp_path, settings):
    rt, _ = _standard(tmp_path, settings)
    d = overview_payload(rt, now=NOW)
    assert settings.brief.top_rank == 2
    # P4 离第 1 太远（难以成交），不算进标题（难以成交 spec §4）
    assert d["headline"] == {"counts": {"1": 1, "2": 2}, "top_rank": 2, "best": {"rank": 1, "count": 1}}


def test_headline_best_when_nobody_is_in_the_top(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings, [_order("X", 10), _snap("X", TODAY, 5, 12), _order("Y", 10), _snap("Y", TODAY, 5, 11)])
    d = overview_payload(rt, now=NOW)
    assert d["headline"]["counts"] == {"1": 0, "2": 0}
    assert d["headline"]["best"] == {"rank": 5, "count": 2}


def test_focus_is_rank_one_then_rank_two_by_gap_ratio_skipping_gated(tmp_path, settings):
    rt, _ = _standard(tmp_path, settings)
    focus = overview_payload(rt, now=NOW)["focus"]
    # P1（第 1）在前；第 2 名按 榜首/我的价 升序：P3 ×1.1 < P2 ×1.2；P4 ×4 被过滤不进
    assert [f["purchase_no"] for f in focus] == ["P1", "P3", "P2"]


def test_orders_sorted_by_rank_unranked_last(tmp_path, settings):
    rt, _ = _standard(tmp_path, settings)
    nos = [o["purchase_no"] for o in overview_payload(rt, now=NOW)["orders"]]
    assert nos[:5] == ["P1", "P2", "P3", "P4", "P5"] or nos[:5] == ["P1", "P3", "P2", "P4", "P5"]
    assert set(nos[5:]) == {"P6", "P7"}


def test_order_fields(tmp_path, settings):
    rt, _ = _standard(tmp_path, settings)
    by = {o["purchase_no"]: o for o in overview_payload(rt, now=NOW)["orders"]}
    p2 = by["P2"]
    assert p2["title"] == "AK-47 | 红线 P2" and p2["tag"] == "久经沙场"
    assert p2["star"] is False and p2["stattrak"] is False and p2["queue"] == "不限"
    assert p2["rank"] == 2 and p2["my_price"] == 50.0 and p2["top1_price"] == 60.0
    assert p2["gap"] == 10.0 and p2["gap_ratio"] == 1.2 and p2["gated"] is False
    assert by["P4"]["gated"] is True
    # 从没被检查过：价格类字段是 null，不是 0
    p6 = by["P6"]
    assert p6["rank"] is None and p6["top1_price"] is None and p6["gap"] is None and p6["gap_ratio"] is None
    assert p6["sell_min"] is None and p6["sell_depth"] is None and p6["ref_bid"] is None
    assert by["P7"]["rank"] is None and by["P7"]["beyond"] == 20


def test_rank_7d_and_change(tmp_path, settings):
    rt, _ = _standard(tmp_path, settings)
    by = {o["purchase_no"]: o for o in overview_payload(rt, now=NOW)["orders"]}
    assert by["P2"]["rank_7d"] == [None, None, None, None, None, 1, 2]
    assert by["P2"]["rank_change"] == 1          # 名次数字变大 = 掉了
    assert by["P5"]["rank_change"] == -1         # 4 → 3，升了
    assert by["P1"]["rank_change"] is None       # 昨天没数据，不算
    assert by["P6"]["rank_7d"] == [None] * 7


def test_rank_7d_uses_local_day_boundaries(tmp_path, settings):
    """Asia/Shanghai：UTC 15:59 还是本地前一天 23:59，UTC 16:01 已经是本地当天 00:01。"""
    late_yesterday = datetime(2026, 9, 26, 15, 59, tzinfo=timezone.utc)
    early_today = datetime(2026, 9, 26, 16, 1, tzinfo=timezone.utc)
    rt, _ = _rt(tmp_path, settings, [_order("Q", 10), _snap("Q", late_yesterday, 7, 11), _snap("Q", early_today, 3, 11)])
    q = overview_payload(rt, now=NOW)["orders"][0]
    assert q["rank_7d"][-2:] == [7, 3]
    assert q["rank_change"] == -4


def test_last_snapshot_of_the_day_wins(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings, [_order("Q", 10), _snap("Q", TODAY - timedelta(hours=2), 5, 11),
                                     _snap("Q", TODAY, 2, 11)])
    assert overview_payload(rt, now=NOW)["orders"][0]["rank_7d"][-1] == 2


def test_insights(tmp_path, settings):
    rt, _ = _standard(tmp_path, settings)
    ins = overview_payload(rt, now=NOW)["insights"]
    assert ins["total"] == 7
    # P4 ×4；P7 榜首 30 / 我的 9 ≈ ×3.3（排在页外也照算）；没有在售价，离市价那条判断不了
    assert ins["unreachable_count"] == ins["unreachable_top"] == 2 and ins["unreachable_market"] == 0
    assert ins["moved_up"] == 1 and ins["moved_down"] == 1
    assert ins["top3_series"][-1] == 5            # 今天：P1 P2 P3 P4 P5
    assert ins["top3_series"][-2] == 1            # 昨天：只有 P2（第 1）；P5 是第 4
    assert len(ins["top3_series"]) == 7


def test_times_are_utc_with_z(tmp_path, settings):
    rt, _ = _standard(tmp_path, settings)
    d = overview_payload(rt, now=NOW)
    assert d["generated_at"].endswith("Z")


def test_category_fields(tmp_path, settings):
    """品类从 item 表的英文名推（看板 spec §4.1）；对不上 item 的进「其他」。"""
    rows = [
        Item(hash_name="StatTrak™ Dual Berettas | Flora Carnivora (Field-Tested)", youpin_template_id=11),
        Item(hash_name="Sticker | Clown Nose", youpin_template_id=12),
        _order("C1", 5, name="双持贝瑞塔（StatTrak™） | 瓜瓜 (久经沙场)", tid=11),
        _order("C2", 5, name="印花 | 小丑鼻子", tid=12),
        _order("C3", 5, name="不认识 | 某物", tid=99),
    ]
    rt, _ = _rt(tmp_path, settings, rows)
    d = overview_payload(rt, now=NOW)
    by = {o["purchase_no"]: o for o in d["orders"]}
    assert (by["C1"]["category"], by["C1"]["subcategory"], by["C1"]["subcategory_label"]) == ("pistol", "Dual Berettas", "双持贝瑞塔")
    assert (by["C2"]["category"], by["C2"]["subcategory"], by["C2"]["subcategory_label"]) == ("sticker", None, None)
    assert (by["C3"]["category"], by["C3"]["subcategory"]) == ("other", None)
    groups = d["category_groups"]
    assert groups[0] == {"key": "rifle", "label": "步枪"} and groups[-1]["key"] == "other"


def test_category_uses_the_earliest_item_when_a_template_id_repeats(tmp_path, settings):
    rows = [Item(hash_name="AWP | Pit Viper (Minimal Wear)", youpin_template_id=21),
            Item(hash_name="Sticker | Clown Nose", youpin_template_id=21),
            _order("D1", 5, tid=21)]
    rt, _ = _rt(tmp_path, settings, rows)
    assert overview_payload(rt, now=NOW)["orders"][0]["category"] == "sniper"


def test_market_flag_on_rows(tmp_path, settings):
    o = _order("M1", 1.1)
    o.market_flag = "both"
    rt, _ = _rt(tmp_path, settings, [o, _order("M2", 1)])
    by = {r["purchase_no"]: r for r in overview_payload(rt, now=NOW)["orders"]}
    assert by["M1"]["market_flag"] == "both" and by["M2"]["market_flag"] is None


def test_query_count_does_not_grow_with_orders(tmp_path, settings):
    """原来每张单单独查一次最新快照（N+1）。现在是固定几条查询，429 张单也一样。"""
    def count(n, sub):
        rows = []
        for i in range(n):
            rows += [_order(f"N{i}", 10), _snap(f"N{i}", YESTERDAY, 3, 11), _snap(f"N{i}", TODAY, 2, 11)]
        rt, engine = _rt(sub, settings, rows)
        calls = []
        event.listen(engine, "before_cursor_execute", lambda *a: calls.append(1))
        overview_payload(rt, now=NOW)
        return len(calls)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert count(5, tmp_path / "a") == count(40, tmp_path / "b")


def test_api_overview_endpoint(tmp_path, settings):
    rt, _ = _standard(tmp_path, settings)
    c = TestClient(create_app(rt), base_url=f"http://127.0.0.1:{settings.web.port}")
    j = c.get("/api/overview").json()
    assert {"generated_at", "headline", "focus", "insights", "orders"} <= set(j)


# ---- /api/meta ----

def test_meta_shape_and_thresholds(tmp_path, settings):
    rt, _ = _standard(tmp_path, settings)
    m = meta_payload(rt, now=NOW)
    assert m["timezone"] == "Asia/Shanghai"
    assert m["now"].endswith("Z")
    assert m["thresholds"] == {"enter_rank": settings.alerts.enter_rank, "top_rank": 2,
                               "max_gap_ratio": settings.alerts.max_gap_ratio,
                               "max_market_ratio": 2.0, "min_market_gap": 20.0}


def _with_round(rt, at, *, last_round=None, paused_until=None, token_valid=True):
    with rt.session_factory() as s:
        s.add(SystemStatus(adapter=ROUND, last_ok_at=at))
        if paused_until:
            s.add(SystemStatus(adapter="youpin_account:1", last_ok_at=at, paused_until=paused_until))
        s.get(Account, 1).token_valid = token_valid
        s.commit()
    rt.last_round = last_round


def test_meta_healthy_when_recent_round_and_nothing_wrong(tmp_path, settings):
    rt, _ = _standard(tmp_path, settings)
    _with_round(rt, NOW - timedelta(minutes=4), last_round={"at": NOW, "checked": 7, "errors": 0})
    svc = meta_payload(rt, now=NOW)["service"]
    assert svc["healthy"] is True and svc["issues"] == []
    assert svc["last_round_at"].endswith("Z") and svc["last_round"]["checked"] == 7


def test_meta_unhealthy_reasons(tmp_path, settings):
    rt, _ = _standard(tmp_path, settings)
    _with_round(rt, NOW - timedelta(hours=2), last_round={"at": NOW, "checked": 7, "errors": 2},
                paused_until=NOW + timedelta(minutes=30), token_valid=False)
    svc = meta_payload(rt, now=NOW)["service"]
    assert svc["healthy"] is False
    assert set(svc["issues"]) == {"stale", "paused", "errors", "token_invalid"}
    assert svc["paused_until"].endswith("Z")


def test_meta_with_no_round_yet_is_stale(tmp_path, settings):
    rt, _ = _standard(tmp_path, settings)
    svc = meta_payload(rt, now=NOW)["service"]
    assert svc["last_round_at"] is None and "stale" in svc["issues"]


def test_meta_token_expiry_never_leaks_the_token(tmp_path, settings):
    secrets = FakeSecrets()
    # 接口按真实的当前时间算剩余天数，所以 token 也按真实时间签（按固定 NOW 签，过了那天就挂）
    token = _jwt(datetime.now(timezone.utc) + timedelta(days=7, hours=5))
    secrets.set(KEY_YOUPIN_TOKEN, token)
    rt, _ = _rt(tmp_path, settings, secrets=secrets)
    c = TestClient(create_app(rt), base_url=f"http://127.0.0.1:{settings.web.port}")
    body = c.get("/api/meta").text
    assert token not in body and token.split(".")[1] not in body
    tok = json.loads(body)["token"]
    assert tok["valid"] is True and tok["expires_at"].endswith("Z")
    assert tok["days_left"] == 7  # 边界用固定时间的纯函数测试见下


def test_meta_token_days_left_floor(tmp_path, settings):
    secrets = FakeSecrets()
    secrets.set(KEY_YOUPIN_TOKEN, _jwt(NOW + timedelta(days=7, hours=5)))
    rt, _ = _rt(tmp_path, settings, secrets=secrets)
    assert meta_payload(rt, now=NOW)["token"]["days_left"] == 7


def test_meta_without_token(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings)
    tok = meta_payload(rt, now=NOW)["token"]
    assert tok["expires_at"] is None and tok["days_left"] is None


def test_daily_history_is_cached_until_a_new_snapshot_arrives(tmp_path, settings):
    """7 天历史每轮才变一次（几十万行，冷算约 0.4 秒），看板每 60 秒刷新一次：按「最新快照时间」缓存。"""
    rt, engine = _standard(tmp_path, settings)
    calls = []
    event.listen(engine, "before_cursor_execute", lambda *a: calls.append(a[2]))
    first = overview_payload(rt, now=NOW)
    n_first = len(calls)
    calls.clear()
    assert overview_payload(rt, now=NOW) == first
    assert len(calls) < n_first                      # 第二次没再跑历史查询

    with rt.session_factory() as s:                  # 新的一轮写了快照 → 缓存失效
        s.add(_snap("P5", TODAY + timedelta(minutes=5), 2, 6))
        s.commit()
    by = {o["purchase_no"]: o for o in overview_payload(rt, now=NOW)["orders"]}
    assert by["P5"]["rank_7d"][-1] == 2 and by["P5"]["rank_change"] == -2


def test_meta_carries_the_youpin_item_link_template(tmp_path, settings):
    """前端「在悠悠打开」用它拼链接（和告警里的链接同一个模板）。"""
    rt, _ = _rt(tmp_path, settings)
    tpl = meta_payload(rt, now=NOW)["links"]["youpin_item"]
    assert tpl == settings.ntfy.click_url_template and "{template_id}" in tpl


def test_order_carries_checked_flag_ref_bid_pct_and_age(tmp_path, settings):
    """评审：旧总览分得清「未检查」和「未找到」，别处买盘的提示里有它比悠悠榜首高多少、查于何时。"""
    rt, _ = _standard(tmp_path, settings)
    with rt.session_factory() as s:
        o = s.get(MyBuyOrder, "P2")
        o.ref_bid, o.ref_bid_platforms, o.ref_bid_at = Decimal("66"), "BUFF/C5", TODAY
        s.commit()
    by = {o["purchase_no"]: o for o in overview_payload(rt, now=NOW)["orders"]}
    assert by["P6"]["checked"] is False          # 从没检查过 → 前端写「未检查」
    assert by["P7"]["checked"] is True and by["P7"]["rank"] is None
    assert by["P2"]["ref_bid_pct"] == 10.0       # 66 比榜首 60 高 10%（core.messages.ref_bid_pct）
    assert by["P2"]["ref_bid_at"].endswith("Z")
    assert by["P1"]["ref_bid_pct"] is None and by["P1"]["ref_bid_at"] is None


def test_meta_does_not_hit_the_keychain_on_every_poll(tmp_path, settings):
    """评审：每个打开的标签页每 60 秒轮询一次 /api/meta；token 到期时间缓存 10 分钟。"""
    secrets = FakeSecrets()
    secrets.set(KEY_YOUPIN_TOKEN, _jwt(NOW + timedelta(days=3)))
    calls = []
    orig = secrets.get
    secrets.get = lambda k: (calls.append(k), orig(k))[1]
    rt, _ = _rt(tmp_path, settings, secrets=secrets)
    from skinpulse.web import overview as ov
    ov._token_cache.clear()
    for _ in range(3):
        assert meta_payload(rt, now=NOW)["token"]["days_left"] == 3
    assert calls.count(KEY_YOUPIN_TOKEN) == 1
    assert meta_payload(rt, now=NOW + timedelta(minutes=11))["token"]["days_left"] == 2
    assert calls.count(KEY_YOUPIN_TOKEN) == 2


# ---- 共用的每行序列化（二期：详情页、分析页复用）----

def test_template_rows_match_overview_rows_and_leave_its_cache_alone(tmp_path, settings):
    """详情页只取一个模板的单：每行和总览里的那一行一字不差（同一个 rank，不再用 reported_rank），
    而且不能挤掉总览的 7 天历史缓存（单条目缓存，换一组单号去调就会被清掉）。"""
    from skinpulse.web import overview as ov

    rt, _ = _rt(tmp_path, settings, [
        _order("P1", 100), _snap("P1", TODAY, 1, 110),
        _order("P2", 50), _snap("P2", YESTERDAY, 1, 55), _snap("P2", TODAY, 2, 60),
        _order("Q1", 20, tid=999), _snap("Q1", TODAY, 3, 25),
    ])
    full = {o["purchase_no"]: o for o in overview_payload(rt, now=NOW)["orders"]}
    key = ov._daily_cache["key"]

    rows = ov.template_rows(rt, 1414, now=NOW)

    assert [r["purchase_no"] for r in rows] == ["P1", "P2"]
    assert rows == [full["P1"], full["P2"]]
    assert ov._daily_cache["key"] == key
    assert ov.template_rows(rt, 12345, now=NOW) == []


# ---- 首次使用（首次使用 spec §2）：setup、还没登录 ----

def _account(rt, **kw):
    with rt.session_factory() as s:
        acc = s.get(Account, 1)
        for k, v in kw.items():
            setattr(acc, k, v)
        s.commit()


def test_setup_steps_follow_the_account_and_channels(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings)
    _account(rt, user_id=None, token_valid=False, last_sync_at=None)
    m = meta_payload(rt, now=NOW)
    assert m["setup"] == {"youpin": False, "push": False, "first_round": False, "market": False}
    assert m["demo"] is False

    _account(rt, user_id=7, token_valid=True)
    rt.ntfy = type("N", (), {"names": ["pushplus"]})()
    assert meta_payload(rt, now=NOW)["setup"] == {"youpin": True, "push": True, "first_round": False, "market": False}

    _account(rt, last_sync_at=NOW)
    rt.market = object()
    assert all(meta_payload(rt, now=NOW)["setup"].values())


def test_never_logged_in_is_not_called_invalid(tmp_path, settings):
    """没 token 时第一轮照样会建一行 Account（user_id 为空）：那是「还没登录」，不是「失效」。"""
    rt, _ = _rt(tmp_path, settings)
    _account(rt, user_id=None, token_valid=False)
    issues = meta_payload(rt, now=NOW)["service"]["issues"]
    assert "not_logged_in" in issues and "token_invalid" not in issues

    _account(rt, user_id=7, token_valid=False)                 # 以前登录过、现在失效了
    issues = meta_payload(rt, now=NOW)["service"]["issues"]
    assert "token_invalid" in issues and "not_logged_in" not in issues


def test_no_account_row_at_all_is_not_logged_in(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings)
    with rt.session_factory() as s:
        s.delete(s.get(Account, 1))
        s.commit()
    assert "not_logged_in" in meta_payload(rt, now=NOW)["service"]["issues"]


def test_rank_one_far_below_market_is_unreachable_and_left_out_of_focus(tmp_path, settings):
    """难以成交 spec §4：第 1 名但离市价太远 → unreachable = "market"，不进焦点卡片、不算进标题；
    没人在卖时用别处买盘；市价不知道就不藏。"""
    far = _order("F", 10)
    far.sell_min, far.sell_depth = Decimal("4871"), 1
    bid = _order("B", 10, tid=2020)
    bid.sell_depth, bid.ref_bid = 0, Decimal("1500")
    ok = _order("K", 25, tid=3030)
    ok.sell_min, ok.sell_depth = Decimal("49"), 10
    unknown = _order("U", 10, tid=4040)
    rows = [far, _snap("F", TODAY, 1, 10), bid, _snap("B", TODAY, 1, 10),
            ok, _snap("K", TODAY, 1, 25), unknown, _snap("U", TODAY, 1, 10)]
    rt, _ = _rt(tmp_path, settings, rows)
    d = overview_payload(rt, now=NOW)
    by = {o["purchase_no"]: o for o in d["orders"]}
    assert by["F"]["unreachable"] == "market" and by["F"]["market_src"] == "sell"
    assert by["F"]["market_gap"] == 4861 and by["F"]["market_ratio"] == 487.1
    assert by["B"]["unreachable"] == "market" and by["B"]["market_src"] == "bid" and by["B"]["market"] == 1500
    assert by["K"]["unreachable"] is None and by["K"]["market_ratio"] == 1.96
    assert by["U"]["unreachable"] is None and by["U"]["market"] is None and by["U"]["market_gap"] is None
    assert not by["F"]["gated"]                    # gated 只表示离第 1 太远（详情页用）
    assert {o["purchase_no"] for o in d["focus"]} == {"K", "U"}
    assert d["headline"]["counts"]["1"] == 2
    assert d["insights"]["unreachable_market"] == 2
