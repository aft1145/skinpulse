import logging
from decimal import Decimal

from fastapi.testclient import TestClient

from skinpulse.adapters.youpin.models import MyPurchaseOrder, UserInfo
from skinpulse.db.models import Account, MyBuyOrder, RankCheck, RankSnapshot
from skinpulse.db.session import make_engine, make_session_factory, upgrade_db, utcnow
from skinpulse.runtime import Runtime
from skinpulse.services.monitor import Monitor
from skinpulse.web.app import create_app


def _client(tmp_path, settings):
    db = tmp_path / "w.db"
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))
    with Session() as s:
        s.add(Account(id=1, user_id=1, nickname="me", token_valid=True))
        for no, rank in (("A", 3), ("B", 1), ("C", None)):
            s.add(MyBuyOrder(purchase_no=no, template_id=1414, commodity_name=f"item {no}", price=Decimal("100"),
                             qty=1, remaining_qty=1, type_id=-1, queue_key="1414|ANY|-1|ANY|ANY",
                             synced_at=utcnow(), active=True))
            s.add(RankSnapshot(purchase_no=no, ts=utcnow(), rank=rank, beyond=20 if rank is None else None,
                               top1_price=Decimal("110"), top2_price=Decimal("105"), top1_qty=1, sample_size=20))
        s.commit()

    class FakeSecrets:
        data = {}
        def get(self, k): return self.data.get(k)
        def set(self, k, v): self.data[k] = v

    from skinpulse.config import save_settings
    sp = tmp_path / "settings.toml"
    save_settings(settings, sp)
    rt = Runtime(settings, sp, FakeSecrets(), Session, client=None, ntfy=None,
                 monitor=Monitor(Session, None, None, settings, sleep=lambda s: None))
    return TestClient(create_app(rt), base_url=f"http://127.0.0.1:{settings.web.port}"), rt


def test_overview_sorted_by_rank(tmp_path, settings):
    c, _ = _client(tmp_path, settings)
    data = c.get("/api/overview").json()
    assert [o["purchase_no"] for o in data["orders"]] == ["B", "A", "C"]
    assert data["orders"][2]["rank"] is None and data["orders"][2]["beyond"] == 20


def test_overview_marks_gap_gated_rows(tmp_path, settings):
    """The dashboard mirrors Monitor._is_eligible_for_alert's gap-filter rule (see
    config.AlertSettings.max_gap_ratio) so the owner can see, at a glance, which rows
    the filter is currently holding back from alerting."""
    c, rt = _client(tmp_path, settings)
    with rt.session_factory() as s:
        s.add(MyBuyOrder(purchase_no="D", template_id=1414, commodity_name="item D", price=Decimal("10"),
                         qty=1, remaining_qty=1, type_id=-1, queue_key="1414|ANY|-1|ANY|ANY",
                         synced_at=utcnow(), active=True))
        s.add(RankSnapshot(purchase_no="D", ts=utcnow(), rank=2, beyond=None,
                           top1_price=Decimal("1500"), top2_price=Decimal("1490"), top1_qty=1, sample_size=20))
        s.commit()

    data = c.get("/api/overview").json()
    by_no = {o["purchase_no"]: o for o in data["orders"]}
    assert by_no["D"]["gated"] is True  # ratio 150 >= default max_gap_ratio 3.0
    assert by_no["A"]["gated"] is False  # ratio 1.1, well under 3.0


def test_overview_max_gap_ratio_zero_marks_nothing(tmp_path, settings):
    settings.alerts.max_gap_ratio = 0
    c, rt = _client(tmp_path, settings)
    with rt.session_factory() as s:
        s.add(MyBuyOrder(purchase_no="D", template_id=1414, commodity_name="item D", price=Decimal("10"),
                         qty=1, remaining_qty=1, type_id=-1, queue_key="1414|ANY|-1|ANY|ANY",
                         synced_at=utcnow(), active=True))
        s.add(RankSnapshot(purchase_no="D", ts=utcnow(), rank=2, beyond=None,
                           top1_price=Decimal("1500"), top2_price=Decimal("1490"), top1_qty=1, sample_size=20))
        s.commit()

    data = c.get("/api/overview").json()
    assert all(o["gated"] is False for o in data["orders"])


def test_settings_roundtrip_and_validation(tmp_path, settings):
    c, rt = _client(tmp_path, settings)
    r = c.post("/api/settings", json={"polling": {"round_interval_min": 20}})
    assert r.status_code == 200 and r.json()["settings"]["polling"]["round_interval_min"] == 20
    assert rt.settings.polling.round_interval_min == 20
    assert "round_interval_min = 20" in rt.settings_path.read_text(encoding="utf-8")
    assert c.post("/api/settings", json={"polling": {"round_interval_min": 0}}).status_code == 422


def test_status_page_has_adapters(tmp_path, settings):
    c, _ = _client(tmp_path, settings)
    j = c.get("/api/status").json()
    assert [ch["id"] for ch in j["channels"]] == ["youpin_anon", "youpin_account", "pushplus", "ntfy"]
    assert j["account"]["user_id"] == 1 and "token" not in j["account"]


def test_status_page_shows_verification_summary(tmp_path, settings):
    """Pass B (sampled verification - see Monitor.run_round) surfaces a short summary
    on the system-status page: how many checks ran recently, how many agreed, and the
    most recent disagreement if any."""
    c, rt = _client(tmp_path, settings)
    with rt.session_factory() as s:
        s.add(RankCheck(purchase_no="A", ts=utcnow(), reported_rank=2, computed_rank=2,
                        queue_size=5, sample_size=20, agreed=True))
        s.add(RankCheck(purchase_no="B", ts=utcnow(), reported_rank=3, computed_rank=7,
                        queue_size=5, sample_size=20, agreed=False))
        s.commit()

    j = c.get("/api/status").json()
    assert j["verification"]["checked_24h"] == 2
    assert j["verification"]["agreed_24h"] == 1
    assert j["verification"]["last_disagreement"]["purchase_no"] == "B"
    assert j["verification"]["last_disagreement"]["reported_rank"] == 3
    assert j["verification"]["last_disagreement"]["computed_rank"] == 7



def test_status_page_counts_unjudgeable_checks_apart_from_disagreements(tmp_path, settings):
    """页被取满（sample_size >= visible_limit）却没找到自己的单，说明这张单排在可见的一页之外，
    核对无从进行。它不能算进"不一致"，否则看板会把我们自己的视野盲区显示成悠悠的 rank 有问题——
    2026-09-06 那批 9 次不一致里有 5 次都是同一张单反复撞上这个盲区。"""
    c, rt = _client(tmp_path, settings)
    with rt.session_factory() as s:
        s.add(RankCheck(purchase_no="REAL", ts=utcnow(), reported_rank=3, computed_rank=7,
                        queue_size=5, sample_size=7, agreed=False))          # 真的不一致
        s.add(RankCheck(purchase_no="BLIND", ts=utcnow(), reported_rank=5, computed_rank=None,
                        queue_size=4, sample_size=20, agreed=False))         # 排在页外，无法判定
        s.commit()

    j = c.get("/api/status").json()
    assert j["verification"]["checked_24h"] == 2
    assert j["verification"]["agreed_24h"] == 0
    assert j["verification"]["inconclusive_24h"] == 1
    # 最近一条是 BLIND，但它不该被当成"最近一次不一致"顶掉真正的那条
    assert j["verification"]["last_disagreement"]["purchase_no"] == "REAL"


def test_status_page_verification_summary_with_no_checks_yet(tmp_path, settings):
    c, _ = _client(tmp_path, settings)
    j = c.get("/api/status").json()
    assert j["verification"] == {"checked_24h": 0, "agreed_24h": 0, "inconclusive_24h": 0,
                                 "last_disagreement": None}


def test_loopback_no_warning_127_0_0_1(tmp_path, settings, caplog):
    """127.0.0.1 should produce no warning."""
    caplog.clear()
    settings.web.host = "127.0.0.1"
    c, _ = _client(tmp_path, settings)
    c.get("/api/overview")  # Ensure app is working
    assert not any("non-loopback" in record.message for record in caplog.records)


def test_loopback_no_warning_localhost(tmp_path, settings, caplog):
    """localhost should produce no warning."""
    caplog.clear()
    settings.web.host = "localhost"
    c, _ = _client(tmp_path, settings)
    c.get("/api/overview")  # Ensure app is working
    assert not any("non-loopback" in record.message for record in caplog.records)


def test_loopback_no_warning_ipv6(tmp_path, settings, caplog):
    """::1 (IPv6 loopback) should produce no warning."""
    caplog.clear()
    settings.web.host = "::1"
    c, _ = _client(tmp_path, settings)
    c.get("/api/overview")  # Ensure app is working
    assert not any("non-loopback" in record.message for record in caplog.records)


def test_non_loopback_ipv4_warns(tmp_path, settings, caplog):
    """Non-loopback IPv4 addresses should produce exactly one warning."""
    caplog.clear()
    settings.web.host = "192.168.1.100"
    c, rt = _client(tmp_path, settings)
    c.get("/api/overview")  # Ensure app is working
    warnings = [r for r in caplog.records if "non-loopback" in r.message]
    assert len(warnings) == 1
    assert "no authentication" in warnings[0].message
    assert "192.168.1.100" in warnings[0].message


def test_wildcard_0_0_0_0_warns(tmp_path, settings, caplog):
    """0.0.0.0 (wildcard) should produce exactly one warning."""
    caplog.clear()
    settings.web.host = "0.0.0.0"
    c, rt = _client(tmp_path, settings)
    c.get("/api/overview")  # Ensure app is working
    warnings = [r for r in caplog.records if "non-loopback" in r.message]
    assert len(warnings) == 1
    assert "no authentication" in warnings[0].message


# ---- important finding: settings edits must actually reach the running Monitor ----

def test_settings_update_applies_new_thresholds_to_running_alert_engine(tmp_path, settings):
    c, rt = _client(tmp_path, settings)
    assert rt.monitor.engine.enter_rank == settings.alerts.enter_rank

    r = c.post("/api/settings", json={"alerts": {"enter_rank": 1, "exit_rank": 3}})

    assert r.status_code == 200
    assert rt.monitor.engine.enter_rank == 1
    assert rt.monitor.engine.exit_rank == 3


def test_settings_update_preserves_token_bucket_when_cap_unchanged(tmp_path, settings):
    c, rt = _client(tmp_path, settings)
    assert rt.monitor.bucket.try_acquire(5) is True
    spent = rt.monitor.bucket.tokens

    r = c.post("/api/settings", json={"polling": {"request_interval_s": 9.0}})

    assert r.status_code == 200
    assert rt.monitor.bucket.tokens == spent  # spent tokens are still spent


# ---- coverage gap 1: a rejected patch must change nothing ----

def test_settings_update_rejected_patch_changes_nothing(tmp_path, settings):
    c, rt = _client(tmp_path, settings)
    before_settings = rt.settings.model_dump(mode="json")
    before_monitor_settings = rt.monitor.s.model_dump(mode="json")
    before_file = rt.settings_path.read_bytes()

    r = c.post("/api/settings", json={"alerts": {"enter_rank": 5, "exit_rank": 3}})

    assert r.status_code == 422
    assert rt.settings.model_dump(mode="json") == before_settings
    assert rt.monitor.s.model_dump(mode="json") == before_monitor_settings
    assert rt.settings_path.read_bytes() == before_file  # byte-identical: nothing was written


def test_settings_update_one_section_leaves_other_sections_untouched(tmp_path, settings):
    c, rt = _client(tmp_path, settings)
    before_alerts = rt.settings.alerts.model_dump(mode="json")
    before_web = rt.settings.web.model_dump(mode="json")

    r = c.post("/api/settings", json={"polling": {"round_interval_min": 30}})

    assert r.status_code == 200
    assert rt.settings.polling.round_interval_min == 30
    assert rt.settings.alerts.model_dump(mode="json") == before_alerts
    assert rt.settings.web.model_dump(mode="json") == before_web


# ---- coverage gap 2: the three consequential YouPin POST endpoints ----

class FakeYoupinClient:
    """Stands in for `YoupinClient` for the account-sync calls (`get_user_info` /
    `search_my_purchase_orders`, as `sync_my_orders` calls them) without touching the
    network. `posts` records any raw `_post` call so tests can prove nothing was sent."""

    def __init__(self, n_orders: int = 2):
        self.token = None
        self.posts: list[tuple[str, dict]] = []
        self.n_orders = n_orders

    def _post(self, path, data, **kwargs):
        self.posts.append((path, dict(data)))
        return {}

    def get_user_info(self):
        return UserInfo(user_id=555000111, nickname="tester")

    def search_my_purchase_orders(self, *args, **kwargs):
        return [
            MyPurchaseOrder(purchase_no=f"P-{i}", template_id=1414, template_hash_name="h",
                            commodity_name="AK-47 | 红线", price=Decimal("100"), qty=1, remaining_qty=1,
                            abrade_text=None, type_id=-1, special_style=None, fade_text=None, raw={})
            for i in range(self.n_orders)
        ]


def _client_with_youpin(tmp_path, settings, youpin_client, db_name="w2.db"):
    db = tmp_path / db_name
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))

    class FakeSecrets:
        data = {}
        def get(self, k): return self.data.get(k)
        def set(self, k, v): self.data[k] = v

    from skinpulse.config import save_settings
    sp = tmp_path / f"{db_name}.settings.toml"
    save_settings(settings, sp)
    rt = Runtime(settings, sp, FakeSecrets(), Session, client=youpin_client, ntfy=None,
                 monitor=Monitor(Session, youpin_client, None, settings, sleep=lambda s: None))
    app = create_app(rt)
    return TestClient(app, base_url=f"http://127.0.0.1:{settings.web.port}"), rt, app


def test_youpin_sync_reports_synced_count(tmp_path, settings):
    yp = FakeYoupinClient(n_orders=5)
    c, rt, app = _client_with_youpin(tmp_path, settings, yp)

    r = c.post("/api/youpin/sync", json={})

    assert r.status_code == 200
    assert r.json() == {"synced": 5}


# ---- final-review finding 6: one malformed queue_key must not 500 the whole dashboard ----


def test_overview_renders_remaining_orders_when_one_has_malformed_queue_key(tmp_path, settings, caplog):
    c, rt = _client(tmp_path, settings)
    with rt.session_factory() as s:
        s.add(MyBuyOrder(purchase_no="BAD", template_id=1414, commodity_name="broken row",
                         price=Decimal("100"), qty=1, remaining_qty=1, type_id=-1,
                         queue_key="not-a-valid-key", synced_at=utcnow(), active=True))
        s.commit()

    with caplog.at_level(logging.WARNING):
        r = c.get("/api/overview")

    assert r.status_code == 200
    purchase_nos = {o["purchase_no"] for o in r.json()["orders"]}
    assert {"A", "B", "C"} <= purchase_nos  # the good rows still render
    assert "BAD" not in purchase_nos  # the malformed row was skipped, not fatal
    assert any("queue_key" in rec.message.lower() or "malformed" in rec.message.lower()
              for rec in caplog.records)



def test_overview_shows_nobody_selling_apart_from_not_yet_checked(tmp_path, settings):
    """看板上"无人在售"和"—"必须是两个不同的格子：前者是查过的结论（这张单排名再高也
    换不来成交），后者只是还没轮到它刷新市场价。两者在 sell_min 上都是 NULL。"""
    c, rt = _client(tmp_path, settings)
    with rt.session_factory() as s:
        s.get(MyBuyOrder, "A").sell_min, s.get(MyBuyOrder, "A").sell_depth = Decimal("64.49"), 3
        s.get(MyBuyOrder, "B").sell_min, s.get(MyBuyOrder, "B").sell_depth = None, 0   # 查过了，没人卖
        # C 保持 sell_depth=None：还没查过
        s.commit()

    j = c.get("/api/overview").json()
    cells = {o["purchase_no"]: (o["sell_min"], o["sell_depth"]) for o in j["orders"]}
    assert cells["A"] == (64.49, 3)
    assert cells["B"] == (None, 0)      # 查过了，没人卖 → 前端写「无人在售」
    assert cells["C"] == (None, None)   # 还没查 → 前端写「—」；两者绝不能混成 0


def test_overview_shows_the_cross_platform_bid_against_the_youpin_leader(tmp_path, settings):
    """「别处买盘」一格给出参照价和它比悠悠榜首高/低多少；来源平台放在悬停提示里——
    不报来源等于把一家的报价说成几家的共识。"""
    c, rt = _client(tmp_path, settings)
    with rt.session_factory() as s:
        o = s.get(MyBuyOrder, "A")                 # _client 给它的快照榜首是 110
        o.ref_bid, o.ref_bid_platforms, o.ref_bid_at = Decimal("121"), "BUFF/C5", utcnow()
        s.commit()

    row = next(r for r in c.get("/api/overview").json()["orders"] if r["purchase_no"] == "A")
    # 「121（+10%）」这类文案由前端按 ref_bid 和 top1_price 算（frontend/src/format）
    assert row["ref_bid"] == 121.0 and row["top1_price"] == 110.0
    assert row["ref_bid_platforms"] == "BUFF/C5"


def test_empty_cross_platform_cell_explains_why_it_is_empty(tmp_path, settings):
    """这一列天生稀疏：只有冲进告警带的单才去查，几百行里绝大多数一直是空的。
    空格子不解释原因，看起来就像功能坏了。"""
    c, _ = _client(tmp_path, settings)

    row = c.get("/api/overview").json()["orders"][0]
    # 空就是 null；「只有冲进告警带的单才去查」的解释由前端的悬停提示给出
    assert row["ref_bid"] is None and row["ref_bid_platforms"] is None
