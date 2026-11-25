"""换 token 的共用工具（看板三期 Task 1）：规范化、校验（只重试限流）、网页入口的限流。"""
from __future__ import annotations

import pytest

from skinpulse.adapters.youpin.errors import LoginRequired, RateLimited, YoupinError
from skinpulse.adapters.youpin.models import UserInfo
from skinpulse.adapters.youpin.token import normalize_token
from skinpulse.services.account import validate_token
from skinpulse.web.ratelimit import Throttle

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjE3OTB9.sig"


def test_normalize_token_is_shared_with_the_cli():
    from skinpulse import cli

    assert normalize_token(f"authorization: Bearer {JWT}") == JWT
    assert cli._normalize_token is normalize_token


class _Client:
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


def test_validate_uses_a_fresh_client_with_the_candidate_token():
    made = []

    def factory():
        made.append(_Client(UserInfo(7, "me")))
        return made[-1]

    assert validate_token(JWT, factory, sleep=lambda s: None) == UserInfo(7, "me")
    assert made[0].seen == [JWT]


def test_validate_retries_only_rate_limits():
    c = _Client(RateLimited("200", "empty"), RateLimited("200", "empty"), UserInfo(1, "a"))
    retries = []
    assert validate_token(JWT, lambda: c, sleep=lambda s: None, on_retry=lambda n, e: retries.append(n)).user_id == 1
    assert retries == [2, 3]


def test_validate_gives_up_after_three_rate_limits():
    c = _Client(*[RateLimited("200", "empty")] * 3)
    with pytest.raises(RateLimited):
        validate_token(JWT, lambda: c, sleep=lambda s: None)
    assert len(c.seen) == 3


@pytest.mark.parametrize("err", [LoginRequired("84101", "登录信息已失效"), YoupinError("x", "boom")])
def test_validate_does_not_retry_real_errors(err):
    c = _Client(err, UserInfo(1, "a"))
    with pytest.raises(type(err)):
        validate_token(JWT, lambda: c, sleep=lambda s: None)
    assert len(c.seen) == 1


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_throttle_spaces_attempts():
    clock = _Clock()
    th = Throttle(min_interval_s=5, max_failures=5, lockout_s=60, clock=clock)
    assert th.acquire() is None
    th.record(ok=False)
    clock.t = 4
    assert th.acquire() == pytest.approx(1)          # 还要等 1 秒
    clock.t = 5
    assert th.acquire() is None


def test_throttle_locks_after_repeated_failures_and_success_resets():
    clock = _Clock()
    th = Throttle(min_interval_s=5, max_failures=5, lockout_s=60, clock=clock)
    for i in range(5):
        clock.t = i * 5
        assert th.acquire() is None
        th.record(ok=False)
    clock.t = 25
    assert th.acquire() == pytest.approx(60 - 5)       # 第 5 次失败在 t=20，锁到 t=80
    clock.t = 80
    assert th.acquire() is None
    th.record(ok=True)
    clock.t = 85
    for i in range(4):
        th.record(ok=False)
    clock.t = 200
    assert th.acquire() is None                       # 成功清零过，4 次失败不够锁


def test_concurrent_attempts_cannot_both_pass():
    """同时到的两个请求：第一个占了名额，第二个就得等（判断和占位在同一把锁里）。"""
    th = Throttle(min_interval_s=5, clock=_Clock())
    assert th.acquire() is None
    assert th.acquire() == pytest.approx(5)
