"""看板三期：`GET /api/status`（看板 spec §4.6、§5）——「服务健康吗？」"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from skinpulse.db.models import MyBuyOrder, NotificationLog, SystemStatus
from skinpulse.db.session import utcnow
from skinpulse.web import system
from tests.test_web import _client


class _Notifier:
    def __init__(self, *names):
        self.names = list(names)


class _Yp:
    def __init__(self, token="eyJtoken.secret.part"):
        self.token = token


def _rt(tmp_path, settings, *rows, channels=("pushplus",)):
    c, rt = _client(tmp_path, settings)
    rt.ntfy = _Notifier(*channels) if channels else None
    rt.client = _Yp()
    rt.secrets.data = {}
    with rt.session_factory() as s:
        for r in rows:
            s.add(r)
        s.commit()
    return c, rt


def _st(adapter, ok_ago=None, err_ago=None, paused_for=None, code="x"):
    now = utcnow()
    return SystemStatus(adapter=adapter, error_counts_json="{}",
                        last_ok_at=None if ok_ago is None else now - timedelta(minutes=ok_ago),
                        last_error_at=None if err_ago is None else now - timedelta(minutes=err_ago),
                        last_error_code=None if err_ago is None else code,
                        paused_until=None if paused_for is None else now + timedelta(minutes=paused_for))


def _round(ago=1):
    return SystemStatus(adapter="monitor_round", error_counts_json="{}", last_ok_at=utcnow() - timedelta(minutes=ago))


def test_healthy_system_has_no_issue(tmp_path, settings):
    _, rt = _rt(tmp_path, settings, _round(), _st("youpin_anon", ok_ago=1), _st("youpin_account:1", ok_ago=1),
                _st("pushplus", ok_ago=30))
    d = system.status_payload(rt)
    assert d["headline"]["issue"] is None
    states = {ch["id"]: ch["state"] for ch in d["channels"]}
    assert states == {"youpin_anon": "ok", "youpin_account": "ok", "pushplus": "ok", "ntfy": "idle"}
    ntfy = next(ch for ch in d["channels"] if ch["id"] == "ntfy")
    assert ntfy["configured"] is False


def test_headline_picks_the_most_serious_issue(tmp_path, settings):
    # 暂停 + PushPlus 失败 + 监控停摆同时存在：先说暂停（token 有效时）
    _, rt = _rt(tmp_path, settings, _round(ago=60), _st("youpin_anon", ok_ago=60, err_ago=5, paused_for=30),
                _st("pushplus", ok_ago=600, err_ago=10, code="publish_failed"))
    d = system.status_payload(rt)
    assert d["headline"]["issue"] == "paused" and d["headline"]["paused_until"].endswith("Z")
    anon = next(ch for ch in d["channels"] if ch["id"] == "youpin_anon")
    push = next(ch for ch in d["channels"] if ch["id"] == "pushplus")
    assert anon["state"] == "warn" and push["state"] == "error" and push["last_error_code"] == "publish_failed"


def test_pushplus_failing_outranks_round_errors(tmp_path, settings):
    _, rt = _rt(tmp_path, settings, _round(), _st("pushplus", ok_ago=600, err_ago=10))
    rt.last_round = {"at": utcnow(), "checked": 3, "alerts_sent": 0, "errors": 2, "paused": False,
                     "gap_gated": 0, "notify_failed": 1}
    d = system.status_payload(rt)
    assert d["headline"]["issue"] == "pushplus_failing"
    assert d["headline"]["pushplus_error_at"].endswith("Z")


def test_invalid_token_is_the_top_issue(tmp_path, settings):
    from skinpulse.db.models import Account

    _, rt = _rt(tmp_path, settings, _round(), _st("youpin_anon", ok_ago=1, paused_for=10))
    with rt.session_factory() as s:
        s.get(Account, 1).token_valid = False
        s.commit()
    assert system.status_payload(rt)["headline"]["issue"] == "token_invalid"


def test_notifications_totals_and_recent_with_item_names(tmp_path, settings):
    now = utcnow()
    order = MyBuyOrder(purchase_no="P1", template_id=1, commodity_name="格洛克 18 型（StatTrak™） | 崩络克-18 (崭新出厂)",
                       price=1, qty=1, remaining_qty=1, type_id=-1, queue_key="1|ANY|-1|ANY|ANY", synced_at=now, active=True)
    logs = [NotificationLog(fingerprint="P1", event="ENTER_TOP2", rank=1, payload_json="{}", created_at=now - timedelta(hours=i),
                            sent_at=now - timedelta(hours=i) if st == "sent" else None, status=st)
            for i, st in enumerate(["sent", "failed", "suppressed", "sent", "pending"] + ["sent"] * 8)]
    _, rt = _rt(tmp_path, settings, order, *logs)
    n = system.status_payload(rt)["notifications"]
    assert n["totals"] == {"sent": 10, "failed": 1, "suppressed": 1}
    assert len(n["recent"]) == 10
    first = n["recent"][0]
    assert first["title"] == "格洛克 18 型 | 崩络克-18" and first["stattrak"] is True and first["tag"] == "崭新出厂"
    assert first["event"] == "升到第 1 名" and first["status"] == "sent" and first["at"].endswith("Z")


def test_notification_for_a_gone_order_falls_back_to_its_payload_title(tmp_path, settings):
    now = utcnow()
    log = NotificationLog(fingerprint="GONE", event="ENTER_TOP2", rank=2,
                          payload_json=json.dumps({"title": "求购第2名：AK-47 | 红线 (久经) [不限]", "body": "x"}),
                          created_at=now, sent_at=now, status="sent")
    _, rt = _rt(tmp_path, settings, log)
    r = system.status_payload(rt)["notifications"]["recent"][0]
    assert r["title"] == "求购第2名：AK-47 | 红线 (久经) [不限]" and r["event"] == "进入前 2"


def test_account_runtime_and_briefs(tmp_path, settings, monkeypatch):
    monkeypatch.setenv("SKINPULSE_SUPERVISOR", "launchd")
    _, rt = _rt(tmp_path, settings, _round())
    rt.started_at = datetime(2026, 9, 24, 9, 38, tzinfo=timezone.utc)
    d = system.status_payload(rt)
    assert {"nickname", "user_id", "token_valid", "expires_at", "days_left", "last_sync_at"} <= set(d["account"])
    assert d["runtime"]["started_at"] == "2026-09-24T09:38:00Z"
    assert d["runtime"]["supervisor"] == "launchd"
    assert d["runtime"]["db_bytes"] > 0
    assert isinstance(d["briefs"], list) and len(d["briefs"]) <= 7
    assert d["brief"] == {"enabled": True, "send_at": "00:00", "top_rank": 2}
    assert {"checked_24h", "agreed_24h", "inconclusive_24h", "last_disagreement"} <= set(d["verification"])


def test_status_never_carries_secrets(tmp_path, settings):
    c, rt = _rt(tmp_path, settings, _round())
    rt.secrets.data = {"youpin_token": "eyJtoken.secret.part", "pushplus_token": "PPKEY123", "ntfy_topic": "TOPIC999"}
    text = c.get("/api/status").text
    assert "secret" not in text and "PPKEY123" not in text and "TOPIC999" not in text


def test_api_status_endpoint(tmp_path, settings):
    c, rt = _rt(tmp_path, settings, _round())
    j = c.get("/api/status").json()
    assert {"headline", "service", "channels", "account", "verification", "notifications", "briefs", "runtime"} <= set(j)


def test_recent_notifications_know_in_trade_and_fill_events(tmp_path, settings):
    from decimal import Decimal

    from skinpulse.db.models import OrderEvent

    now = utcnow()
    filled = now - timedelta(minutes=30)
    order = MyBuyOrder(purchase_no="F", template_id=1, commodity_name="SSG 08 | 渐变强酸 (崭新出厂)", price=1, qty=1,
                       remaining_qty=1, type_id=-1, queue_key="1|ANY|-1|ANY|ANY", synced_at=now, active=False)
    rows = [
        order,
        OrderEvent(purchase_no="F", template_id=1, kind="gone", qty_before=1, price=Decimal("3.9"),
                   at=now - timedelta(minutes=5), outcome="done", outcome_at=filled, notice="sent"),
        NotificationLog(fingerprint="F", event="FILLED", rank=None, payload_json="{}", created_at=now - timedelta(minutes=4),
                        sent_at=now - timedelta(minutes=4), status="sent"),
        NotificationLog(fingerprint="T", event="IN_TRADE", rank=None, payload_json="{}", created_at=now - timedelta(minutes=9),
                        sent_at=now - timedelta(minutes=9), status="sent"),
        NotificationLog(fingerprint="R", event="ENTER_TOP2", rank=1, payload_json="{}", created_at=now - timedelta(minutes=20),
                        sent_at=now - timedelta(minutes=20), status="sent"),
        NotificationLog(fingerprint="T", event="MARKET", rank=None, payload_json="{}", created_at=now - timedelta(minutes=30),
                        sent_at=now - timedelta(minutes=30), status="sent"),
    ]
    _, rt = _rt(tmp_path, settings, *rows)
    recent = system.status_payload(rt)["notifications"]["recent"]
    got = [(r["kind"], r["event"], r["filled_at"]) for r in recent]
    assert got == [("FILLED", "求购成交", filled.isoformat().replace("+00:00", "Z")),
                   ("IN_TRADE", "交易中", None), ("RANK", "升到第 1 名", None), ("MARKET", "接近成交价", None)]
    assert recent[0]["title"] == "SSG 08 | 渐变强酸" and recent[0]["rank"] is None


def test_outdated_code_is_the_headline_issue(tmp_path, settings, monkeypatch):
    """服务启动时记下代码指纹；工作区的代码变了（合并、切分支）还没重启，系统页标题要说出来。"""
    _, rt = _rt(tmp_path, settings, _round(), _st("youpin_anon", ok_ago=1), _st("youpin_account:1", ok_ago=1),
                _st("pushplus", ok_ago=30))
    monkeypatch.setattr(system, "code_fingerprint", lambda: "new")
    rt.code_fingerprint = "old"
    d = system.status_payload(rt)
    assert d["headline"]["issue"] == "code_outdated" and d["runtime"]["code_outdated"] is True
    rt.code_fingerprint = "new"
    d = system.status_payload(rt)
    assert d["headline"]["issue"] is None and d["runtime"]["code_outdated"] is False
    rt.code_fingerprint = None                      # 一次性命令、测试：不知道启动时的指纹，不报
    assert system.status_payload(rt)["runtime"]["code_outdated"] is False


# ---- 系统页「重启服务」（intent 2026-11-23-restart-button）----

def _restart_client(tmp_path, settings):
    from fastapi.testclient import TestClient

    from skinpulse.web.app import create_app

    _, rt = _rt(tmp_path, settings, _round())
    kills = []
    c = TestClient(create_app(rt, restart_kill=lambda: kills.append(1)), base_url=f"http://127.0.0.1:{settings.web.port}")
    return c, rt, kills


def test_restart_under_launchd_signals_after_answering(tmp_path, settings, monkeypatch):
    monkeypatch.setenv("SKINPULSE_SUPERVISOR", "launchd")
    monkeypatch.setattr(system, "deps_outdated", lambda: False)
    c, rt, kills = _restart_client(tmp_path, settings)
    r = c.post("/api/system/restart", json={})
    assert r.status_code == 200 and r.json() == {"restarting": True} and kills == [1]
    rt_ = system.status_payload(rt)["runtime"]
    assert rt_["can_restart"] is True and rt_["deps_outdated"] is False


def test_restart_refused_when_nobody_would_bring_it_back(tmp_path, settings, monkeypatch):
    monkeypatch.delenv("SKINPULSE_SUPERVISOR", raising=False)
    c, rt, kills = _restart_client(tmp_path, settings)
    r = c.post("/api/system/restart", json={})
    assert r.status_code == 409 and "终端" in r.json()["detail"] and kills == []
    assert system.status_payload(rt)["runtime"]["can_restart"] is False


def test_restart_refused_when_dependencies_changed(tmp_path, settings, monkeypatch):
    monkeypatch.setenv("SKINPULSE_SUPERVISOR", "launchd")
    monkeypatch.setattr(system, "deps_outdated", lambda: True)
    c, _, kills = _restart_client(tmp_path, settings)
    r = c.post("/api/system/restart", json={})
    assert r.status_code == 409 and "uv sync" in r.json()["detail"] and kills == []


def test_restart_refused_while_a_pick_is_running(tmp_path, settings, monkeypatch):
    from skinpulse.web.system import RestartRefused, request_restart

    monkeypatch.setenv("SKINPULSE_SUPERVISOR", "launchd")
    monkeypatch.setattr(system, "deps_outdated", lambda: False)
    kills = []
    try:
        request_restart(None, pick_busy=True, kill=lambda: kills.append(1))
        raise AssertionError("should refuse")
    except RestartRefused as e:
        assert e.status == 409 and "选品" in e.message
    assert kills == []


def test_restart_rejects_cross_site_form_posts(tmp_path, settings, monkeypatch):
    monkeypatch.setenv("SKINPULSE_SUPERVISOR", "launchd")
    monkeypatch.setattr(system, "deps_outdated", lambda: False)
    c, _, kills = _restart_client(tmp_path, settings)
    r = c.post("/api/system/restart", data={"x": "1"})
    assert r.status_code == 403 and kills == []


def test_restart_under_the_windows_supervisor(tmp_path, settings, monkeypatch):
    monkeypatch.setenv("SKINPULSE_SUPERVISOR", "windows")
    monkeypatch.setattr(system, "deps_outdated", lambda: False)
    c, rt, kills = _restart_client(tmp_path, settings)
    r = c.post("/api/system/restart", json={})
    assert r.status_code == 200 and kills == [1]
    assert system.status_payload(rt)["runtime"]["supervisor"] == "windows"


def test_windows_restart_exits_with_the_restart_code(monkeypatch):
    """Windows 上不发 SIGTERM（那就是 TerminateProcess），而是刷完日志以 RESTART_EXIT 退出，守护进程重拉。"""
    from skinpulse.supervise import RESTART_EXIT
    from skinpulse.web.system import request_restart

    monkeypatch.setenv("SKINPULSE_SUPERVISOR", "windows")
    monkeypatch.setattr(system, "deps_outdated", lambda: False)
    timers, exits = [], []

    class _Timer:
        def __init__(self, delay, fn, args=()):
            timers.append((delay, fn, args))

        def start(self):
            pass

    monkeypatch.setattr(system.threading, "Timer", _Timer)
    monkeypatch.setattr(system.os, "_exit", exits.append)
    monkeypatch.setattr(system.logging, "shutdown", lambda: None)     # 别真把测试进程的日志关了
    monkeypatch.setattr(system.os, "kill", lambda *a: (_ for _ in ()).throw(AssertionError("不该发信号")))
    request_restart(None, pick_busy=False)
    [(delay, fn, args)] = timers
    fn(*args)
    assert exits == [RESTART_EXIT]
