"""看板三期：`POST /api/settings`（看板 spec §4.6、§5）——只收三组、告诉你改了什么、错在哪一项、改完立即生效。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from skinpulse.scheduler import build_scheduler
from skinpulse.web.app import create_app
from tests.test_web import _client


def _with_sched(tmp_path, settings):
    _, rt = _client(tmp_path, settings)
    sched = build_scheduler(rt)          # 不 start：只看排班
    c = TestClient(create_app(rt, sched), base_url=f"http://127.0.0.1:{settings.web.port}")
    return c, rt, sched


def test_returns_what_changed(tmp_path, settings):
    c, rt = _client(tmp_path, settings)
    r = c.post("/api/settings", json={"polling": {"round_interval_min": 6, "request_interval_s": 3.0},
                                      "brief": {"send_at": "8:00"}})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["changed"] == ["polling.round_interval_min", "brief.send_at"]     # 3.0 没变，不算
    assert j["settings"]["polling"]["round_interval_min"] == 6
    assert j["settings"]["brief"]["send_at"] == "08:00"                        # 规范化后的值


def test_only_the_three_sections_are_editable(tmp_path, settings):
    c, rt = _client(tmp_path, settings)
    before = rt.settings_path.read_bytes()
    for body in ({"web": {"port": 1}}, {"youpin": {"user_agent": "x"}}, {"polling": {"no_such_field": 1}}, ["x"]):
        r = c.post("/api/settings", json=body)
        assert r.status_code == 422, body
    assert rt.settings_path.read_bytes() == before


def test_errors_point_at_the_field_in_plain_chinese(tmp_path, settings):
    c, rt = _client(tmp_path, settings)
    r = c.post("/api/settings", json={"polling": {"round_interval_min": 0, "jitter_pct": "abc"},
                                      "alerts": {"enter_rank": 1, "exit_rank": 1},
                                      "brief": {"send_at": "25:00"}})
    assert r.status_code == 422
    errs = {e["field"]: e["message"] for e in r.json()["detail"]}
    assert errs["polling.round_interval_min"] == "不能小于 1"
    assert errs["polling.jitter_pct"] == "要填数字"
    assert "brief.send_at" in errs
    # exit_rank 自己还有 ≥ 2 的下限；过了下限、但不大于 enter_rank 时，是跨字段的那条规则
    assert c.post("/api/settings", json={"alerts": {"enter_rank": 3, "exit_rank": 2}}).json()["detail"] == [
        {"field": "alerts.exit_rank", "message": "要大于「升到第几名推送」（3）"}]


def test_brief_time_changes_take_effect_without_a_restart(tmp_path, settings):
    c, rt, sched = _with_sched(tmp_path, settings)
    assert c.post("/api/settings", json={"brief": {"send_at": "08:30", "snapshot_at": "07:10"}}).status_code == 200
    brief = str(sched.get_job("daily_brief").trigger)
    snap = str(sched.get_job("price_snapshot").trigger)
    assert "hour='8'" in brief and "minute='30'" in brief
    assert "hour='7'" in snap and "minute='10'" in snap


def test_turning_the_brief_off_and_on_again(tmp_path, settings):
    c, rt, sched = _with_sched(tmp_path, settings)
    assert c.post("/api/settings", json={"brief": {"enabled": False}}).status_code == 200
    assert sched.get_job("daily_brief") is None and sched.get_job("price_snapshot") is None
    assert c.post("/api/settings", json={"brief": {"enabled": True}}).status_code == 200
    assert sched.get_job("daily_brief") is not None and sched.get_job("price_snapshot") is not None


def test_brief_jobs_are_left_alone_when_brief_did_not_change(tmp_path, settings):
    c, rt, sched = _with_sched(tmp_path, settings)
    before = sched.get_job("daily_brief")
    assert c.post("/api/settings", json={"polling": {"round_interval_min": 7}}).status_code == 200
    assert sched.get_job("daily_brief") is before


def test_hard_to_fill_thresholds_are_editable(tmp_path, settings):
    """难以成交 spec §2：两个新阈值能改、立即生效，负数被拒。"""
    c, rt = _client(tmp_path, settings)
    r = c.post("/api/settings", json={"alerts": {"max_market_ratio": 0, "min_market_gap": 50}})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == ["alerts.max_market_ratio", "alerts.min_market_gap"]
    assert rt.settings.alerts.max_market_ratio == 0 and rt.settings.alerts.min_market_gap == 50
    assert c.post("/api/settings", json={"alerts": {"min_market_gap": -1}}).status_code == 422
