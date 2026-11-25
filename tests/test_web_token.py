"""看板三期：`POST /api/youpin/token`，网页粘贴悠悠 token（看板 spec §5、§6）。

最要紧的几条：校验通过之前不动正在用的 token；三种失败都不保存；token 的任何片段都不能出现在
响应和日志里；每 5 秒最多 1 次、失败 5 次锁 1 分钟。
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from skinpulse.adapters.youpin.errors import LoginRequired, RateLimited, YoupinError
from skinpulse.adapters.youpin.models import UserInfo
from skinpulse.db.models import Account
from skinpulse.secrets import KEY_YOUPIN_TOKEN
from skinpulse.web import overview as ov
from skinpulse.web.app import create_app
from tests.test_web import _client

EXP = datetime(2026, 10, 14, 8, 1, tzinfo=timezone.utc)


def _jwt(exp: datetime, marker: str = "SECRETPART") -> str:
    def seg(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"eyJ{seg({'alg': 'HS256'})[3:]}.{seg({'exp': int(exp.timestamp()), 'm': marker})}.{marker}sig"


NEW = _jwt(EXP)
OLD = "eyJold.old.old"


class _Validator:
    """临时校验用的 client：按顺序给出结果，记下看到的 token。"""
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.token = None
        self.seen = []

    def get_user_info(self):
        self.seen.append(self.token)
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


class _Running:
    """服务里正在用的 client：同步时会被调用。"""
    def __init__(self, token=OLD, fail=None):
        self.token = token
        self.fail = fail

    def get_user_info(self):
        if self.fail:
            raise self.fail
        return UserInfo(42, "测试用户")

    def search_my_purchase_orders(self, *a, **kw):
        return []


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class _Sched:
    """只记下「让下一轮监控马上开始」的调用。"""
    def __init__(self):
        self.kicked = []

    def modify_job(self, job_id, **kw):
        self.kicked.append((job_id, kw))


def _setup(tmp_path, settings, *outcomes, running=None, sched=None):
    c, rt = _client(tmp_path, settings)
    rt.secrets.data = {KEY_YOUPIN_TOKEN: OLD}        # 实例自己的一份，别串到别的测试
    rt.client = running or _Running()
    validator = _Validator(*outcomes)
    clock = _Clock()
    app = create_app(rt, sched, youpin_factory=lambda: validator, clock=clock)
    tc = TestClient(app, base_url=f"http://127.0.0.1:{settings.web.port}")
    return tc, rt, validator, clock


def _post(tc, body):
    return tc.post("/api/youpin/token", json=body)


def test_valid_token_is_swapped_in_saved_and_a_round_starts_now(tmp_path, settings):
    """校验通过就返回（一次请求，一两秒）；同步求购单交给马上开始的这一轮监控——每轮开头本来
    就先同步。原来在接口里同步，429 张要分 11 页、每页歇 2.5 秒，用户干等了 30 多秒（2026-09-27）。"""
    ov._token_cache.update(rt=None, at=None, value=None)
    sched = _Sched()
    tc, rt, v, _ = _setup(tmp_path, settings, UserInfo(42, "测试用户"), sched=sched)
    r = _post(tc, {"token": f"authorization: Bearer {NEW}"})     # 连名字带 Bearer 一起粘也行

    assert r.status_code == 200, r.text
    assert r.json() == {"nickname": "测试用户", "user_id": 42, "expires_at": "2026-10-14T08:01:00Z",
                        "round_started": True}
    assert [j for j, _ in sched.kicked] == ["monitor_round"]
    assert v.seen == [NEW]                                  # 校验用的是临时 client
    assert rt.client.token == NEW and rt.secrets.get(KEY_YOUPIN_TOKEN) == NEW
    assert ov._token_cache == {}                            # 到期时间缓存清掉，顶栏马上看到新的
    with rt.session_factory() as s:
        acc = s.get(Account, 1)
        assert (acc.user_id, acc.nickname, acc.token_valid) == (42, "测试用户", True)


def test_invalid_token_is_not_saved_and_the_running_one_is_untouched(tmp_path, settings):
    tc, rt, v, _ = _setup(tmp_path, settings, LoginRequired("84101", "登录信息已失效"))
    r = _post(tc, {"token": NEW})
    assert r.status_code == 422 and "无效或已过期" in r.json()["detail"]
    assert rt.client.token == OLD and rt.secrets.get(KEY_YOUPIN_TOKEN) == OLD


def test_unverifiable_token_is_503_and_not_saved(tmp_path, settings):
    tc, rt, v, _ = _setup(tmp_path, settings, *[RateLimited("200", "empty")] * 3)
    r = _post(tc, {"token": NEW})
    assert r.status_code == 503 and "暂时无法验证" in r.json()["detail"]
    assert rt.client.token == OLD and rt.secrets.get(KEY_YOUPIN_TOKEN) == OLD


def test_transport_errors_are_503_too(tmp_path, settings):
    import httpx

    tc, rt, v, _ = _setup(tmp_path, settings, httpx.ConnectError("boom"))
    assert _post(tc, {"token": NEW}).status_code == 503
    assert rt.client.token == OLD


def test_the_token_never_appears_in_responses_or_logs(tmp_path, settings, caplog):
    caplog.set_level(logging.DEBUG)
    for outcome in (UserInfo(1, "a"), LoginRequired("84101", "x"), YoupinError("x", "boom")):
        d = tmp_path / type(outcome).__name__
        d.mkdir()
        tc, rt, v, clock = _setup(d, settings, outcome)
        r = _post(tc, {"token": NEW})
        assert "SECRETPART" not in r.text and NEW not in r.text
    assert "SECRETPART" not in caplog.text


def test_empty_or_malformed_bodies_are_rejected_without_echoing_them(tmp_path, settings):
    """格式都不对是 400（和悠悠说「无效」的 422 分开），不请求悠悠，也不占限流名额。"""
    tc, rt, v, _ = _setup(tmp_path, settings)
    for body in ({}, {"token": ""}, {"token": 123}, ["x"], {"token": "eyJa b.c"}):
        r = _post(tc, body)
        assert r.status_code == 400, (body, r.text)
        assert "eyJa" not in r.text
    assert v.seen == []                                     # 没去请求悠悠


def test_one_attempt_per_five_seconds(tmp_path, settings):
    tc, rt, v, clock = _setup(tmp_path, settings, LoginRequired("84101", "x"), UserInfo(1, "a"))
    assert _post(tc, {"token": NEW}).status_code == 422
    clock.t += 2
    r = _post(tc, {"token": NEW})
    assert r.status_code == 429 and "3 秒" in r.json()["detail"]
    clock.t += 3
    assert _post(tc, {"token": NEW}).status_code == 200


def test_five_failures_lock_for_a_minute(tmp_path, settings):
    tc, rt, v, clock = _setup(tmp_path, settings, *[LoginRequired("84101", "x")] * 5, UserInfo(1, "a"))
    for _ in range(5):
        assert _post(tc, {"token": NEW}).status_code == 422
        clock.t += 5
    assert _post(tc, {"token": NEW}).status_code == 429
    clock.t += 60
    assert _post(tc, {"token": NEW}).status_code == 200


def test_without_a_scheduler_the_token_is_still_saved(tmp_path, settings):
    """`run --web-only` 没有调度器：token 照样换上、保存，只是没有一轮可以提前。"""
    tc, rt, v, _ = _setup(tmp_path, settings, UserInfo(42, "me"))
    r = _post(tc, {"token": NEW})
    assert r.status_code == 200 and r.json()["round_started"] is False
    assert rt.secrets.get(KEY_YOUPIN_TOKEN) == NEW


def test_cross_site_and_form_posts_are_refused(tmp_path, settings):
    tc, rt, v, _ = _setup(tmp_path, settings, UserInfo(1, "a"))
    r = tc.post("/api/youpin/token", json={"token": NEW}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    r = tc.post("/api/youpin/token", data={"token": NEW})
    assert r.status_code == 403
    assert v.seen == [] and rt.client.token == OLD


def test_programming_errors_are_not_disguised_as_network_trouble(tmp_path, settings):
    tc, rt, v, _ = _setup(tmp_path, settings, TypeError("bug"))
    tc = TestClient(tc.app, base_url=str(tc.base_url), raise_server_exceptions=False)
    assert _post(tc, {"token": NEW}).status_code == 500
    assert rt.client.token == OLD


def test_validation_client_shares_the_live_device_identity(tmp_path, settings):
    """校验用的临时 client 和正在用的同一个设备：不多打一次 deviceW2，悠悠也不会看到新设备。"""
    from skinpulse.adapters.youpin.client import YoupinClient
    from skinpulse.web.youpin_token import validation_client

    _, rt = _client(tmp_path, settings)
    live = YoupinClient(settings.youpin, device_id="dev-1")
    live.token, live.uk, live.device_uk, live._uk_at = OLD, "UK", "DUK", 123.0
    rt.client = live
    c = validation_client(rt)
    assert c is not live and c.token is None
    assert (c.device_id, c.uk, c.device_uk, c._uk_at, c.http) == ("dev-1", "UK", "DUK", 123.0, live.http)
