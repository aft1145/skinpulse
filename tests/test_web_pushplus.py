"""首次使用：`POST /api/pushplus/token`，看板里录 PushPlus（首次使用 spec §2.4）。

要紧的几条：发测试消息成功才保存；失败一律不保存、原来的不受影响；格式不对不发请求；保存后不用重启、
下一条告警就走 PushPlus；token 的任何片段都不进响应和日志。"""
from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from skinpulse.adapters.pushplus.client import PushPlusAuthError, PushPlusNeedsRealName, PushPlusRestricted
from skinpulse.secrets import KEY_PUSHPLUS
from skinpulse.services.notifier import FanoutNotifier
from skinpulse.web.app import create_app
from tests.test_web import _client

TOKEN = "pp0123456789abcdefSECRET"


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class _PP:
    """替身 PushPlusClient：按顺序给结果，记下每条消息。所有实例共享一个记录。"""
    outcomes: list = []
    sent: list = []

    def __init__(self, token):
        self.token = token

    def publish(self, msg):
        _PP.sent.append((self.token, msg.title))
        o = _PP.outcomes.pop(0) if _PP.outcomes else "sc-ok"
        if isinstance(o, Exception):
            raise o
        return o


def _setup(tmp_path, settings, *outcomes, ntfy=None):
    _PP.outcomes, _PP.sent = list(outcomes), []
    _, rt = _client(tmp_path, settings)
    rt.secrets.data = {}
    rt.ntfy = ntfy
    rt.monitor.ntfy = ntfy
    clock = _Clock()
    tc = TestClient(create_app(rt, None, pushplus_factory=_PP, clock=clock),
                    base_url=f"http://127.0.0.1:{settings.web.port}")
    return tc, rt, clock


def _post(tc, body):
    return tc.post("/api/pushplus/token", json=body)


def test_success_saves_and_attaches_without_restart(tmp_path, settings):
    tc, rt, _ = _setup(tmp_path, settings)
    r = _post(tc, {"token": f"  {TOKEN}\n"})
    assert r.status_code == 200 and r.json() == {"sent": True}
    assert rt.secrets.data[KEY_PUSHPLUS] == TOKEN
    assert _PP.sent == [(TOKEN, "SkinPulse 通道测试")]
    # 原来一条通道都没有：新建的推送器同时交给 Monitor（它启动时存了引用）
    assert rt.ntfy is not None and rt.ntfy.names == ["pushplus"] and rt.monitor.ntfy is rt.ntfy
    rt.ntfy.publish(type("M", (), {"title": "告警"})())
    assert _PP.sent[-1] == (TOKEN, "告警")
    assert tc.get("/api/meta").json()["setup"]["push"] is True


def test_existing_notifier_is_updated_in_place_with_pushplus_first(tmp_path, settings):
    class _Ntfy:
        def publish(self, msg):
            return "ok"
    ntfy = FanoutNotifier([("ntfy", _Ntfy())])
    tc, rt, _ = _setup(tmp_path, settings, ntfy=ntfy)
    assert _post(tc, {"token": TOKEN}).status_code == 200
    assert rt.ntfy is ntfy and ntfy.names == ["pushplus", "ntfy"]


def test_replacing_pushplus_keeps_one_channel(tmp_path, settings):
    ntfy = FanoutNotifier([("pushplus", _PP("old"))])
    tc, rt, clock = _setup(tmp_path, settings, ntfy=ntfy)
    assert _post(tc, {"token": TOKEN}).status_code == 200
    assert ntfy.names == ["pushplus"] and ntfy.channels[0][1].token == TOKEN


def test_failures_never_save(tmp_path, settings):
    cases = [
        (PushPlusAuthError("903", "token 不对"), 422, "903"),
        (PushPlusNeedsRealName("905", "实名"), 422, "实名"),
        (PushPlusRestricted("900", "限制"), 422, "2 天"),
        (OSError("connect failed to https://www.pushplus.plus/send?token=" + TOKEN), 503, "连不上"),
    ]
    for i, (exc, status, word) in enumerate(cases):
        tc, rt, clock = _setup(tmp_path / str(i), settings, exc)
        r = _post(tc, {"token": TOKEN})
        assert r.status_code == status and word in r.json()["detail"] and "未保存" in r.json()["detail"]
        assert KEY_PUSHPLUS not in rt.secrets.data and rt.ntfy is None
        assert TOKEN not in r.text and "SECRET" not in r.text


def test_903_reports_the_shape_not_the_token(tmp_path, settings):
    tc, _, _ = _setup(tmp_path, settings, PushPlusAuthError("903"))
    detail = _post(tc, {"token": TOKEN}).json()["detail"]
    assert f"{len(TOKEN)} 位" in detail and TOKEN not in detail


def test_bad_format_is_refused_without_sending(tmp_path, settings):
    tc, _, _ = _setup(tmp_path, settings)
    for body in ({"token": ""}, {"token": "a b"}, {"token": 12}, {}, []):
        assert _post(tc, body).status_code == 400
    r = _post(tc, {"token": "abc" + chr(22) + "def"})
    assert r.status_code == 400 and "控制字符" in r.json()["detail"]
    assert _PP.sent == []


def test_throttled(tmp_path, settings):
    tc, _, clock = _setup(tmp_path, settings, PushPlusAuthError("903"))
    _post(tc, {"token": TOKEN})
    r = _post(tc, {"token": TOKEN})
    assert r.status_code == 429


def test_token_never_logged(tmp_path, settings, caplog):
    caplog.set_level(logging.DEBUG)
    tc, _, _ = _setup(tmp_path, settings, PushPlusAuthError("903"))
    _post(tc, {"token": TOKEN})
    _setup(tmp_path / "b", settings)[0].post("/api/pushplus/token", json={"token": TOKEN})
    assert TOKEN not in caplog.text and "SECRET" not in caplog.text


def test_cross_site_post_is_refused(tmp_path, settings):
    tc, rt, _ = _setup(tmp_path, settings)
    r = tc.post("/api/pushplus/token", json={"token": TOKEN}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and _PP.sent == []


def test_restricted_holds_off_further_requests(tmp_path, settings):
    """900：官方让停手，继续请求只会延长限制。看板这边几小时内不再替用户发。"""
    tc, _, clock = _setup(tmp_path, settings, PushPlusRestricted("900"), "sc")
    assert _post(tc, {"token": TOKEN}).status_code == 422
    clock.t += 3600
    r = _post(tc, {"token": TOKEN})
    assert r.status_code == 429 and "900" in r.json()["detail"] and len(_PP.sent) == 1
    clock.t += 6 * 3600
    assert _post(tc, {"token": TOKEN}).status_code == 200
