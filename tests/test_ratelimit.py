from skinpulse.core.ratelimit import BackoffGuard, TokenBucket, jittered


def test_token_bucket_basic():
    t = {"now": 0.0}
    b = TokenBucket(capacity=3, refill_per_s=1.0, clock=lambda: t["now"])
    assert all(b.try_acquire() for _ in range(3))
    assert not b.try_acquire()
    assert b.seconds_until_available() == 1.0
    t["now"] += 2
    assert b.try_acquire() and b.try_acquire() and not b.try_acquire()


def test_backoff_risk_control_escalates_and_caps():
    t = {"now": 0.0}
    g = BackoffGuard(clock=lambda: t["now"])
    assert g.paused_for() == 0
    assert g.on_error("risk_control") == 30 * 60
    assert g.paused_for() == 30 * 60
    t["now"] += 30 * 60
    assert g.on_error("risk_control") == 60 * 60
    t["now"] += 60 * 60
    assert g.on_error("risk_control") == 120 * 60
    t["now"] += 120 * 60
    assert g.on_error("risk_control") == 240 * 60
    t["now"] += 240 * 60
    assert g.on_error("risk_control") == 240 * 60  # cap


def test_backoff_rate_limited_and_reset():
    t = {"now": 0.0}
    g = BackoffGuard(clock=lambda: t["now"])
    assert g.on_error("rate_limited") == 300
    t["now"] += 300
    assert g.on_error("rate_limited") == 600
    g.on_success()
    t["now"] += 600
    assert g.on_error("rate_limited") == 300
    assert g.on_error("uk_expired") == 60
    assert g.on_error("login_required") == 0
    assert g.on_error("other") == 30


def test_jittered_range():
    assert jittered(10, 0.3, rnd=lambda: 0.0) == 7.0
    assert jittered(10, 0.3, rnd=lambda: 1.0) == 13.0
