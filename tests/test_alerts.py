from skinpulse.core.alerts import AlertEngine, AlertState, HourlyCap, OrderAlert


def test_enter_requires_confirm_rounds():
    e = AlertEngine(enter_rank=2, exit_rank=4, confirm_rounds=2)
    a = OrderAlert("P1")
    assert e.step(a, 2) is None and a.streak == 1 and a.state is AlertState.NORMAL
    ev = e.step(a, 1)
    assert ev is not None and ev.kind == "ENTER_TOP2" and ev.rank == 1
    assert a.state is AlertState.ALERTING and a.streak == 0


def test_streak_resets_when_rank_drops():
    e = AlertEngine(2, 4, 2)
    a = OrderAlert("P1")
    e.step(a, 2)
    e.step(a, 3)
    assert a.streak == 0
    assert e.step(a, 2) is None  # 重新计数


def test_unknown_rank_keeps_everything():
    e = AlertEngine(2, 4, 2)
    a = OrderAlert("P1")
    e.step(a, 1)
    e.step(a, None)
    assert a.streak == 1 and a.last_rank == 1


def test_exit_dead_zone():
    e = AlertEngine(2, 4, 1)
    a = OrderAlert("P1")
    assert e.step(a, 1) is not None
    assert e.step(a, 3) is None and a.state is AlertState.ALERTING  # 死区
    assert e.step(a, 4) is None and a.state is AlertState.NORMAL
    assert e.step(a, 2) is not None  # 可再次触发


def test_hourly_cap_sliding_window():
    t = {"now": 0.0}
    cap = HourlyCap(3, clock=lambda: t["now"])
    assert cap.allow() and cap.allow() and cap.allow() and not cap.allow()
    t["now"] += 3601
    assert cap.allow()


# ---- eligible= (gap filter - see config.AlertSettings.max_gap_ratio and
# Monitor._is_eligible_for_alert). AlertEngine itself doesn't know about prices or
# gap ratios; the caller decides eligibility and passes it in per step(). ----

def test_ineligible_rank_produces_no_event_and_resets_streak():
    e = AlertEngine(2, 4, 2)
    a = OrderAlert("P1")
    e.step(a, 2)  # streak 1, still NORMAL
    assert a.streak == 1
    ev = e.step(a, 2, eligible=False)
    assert ev is None
    assert a.state is AlertState.NORMAL and a.streak == 0


def test_ineligible_then_eligible_alerts_normally_once_narrowed():
    """Gating must not strand the order: once it becomes eligible again, the streak
    accumulates from scratch and the alert fires normally - proving the earlier
    ineligible steps did not leave anything stuck."""
    e = AlertEngine(2, 4, 2)
    a = OrderAlert("P1")
    e.step(a, 2, eligible=False)
    e.step(a, 2, eligible=False)
    assert a.state is AlertState.NORMAL and a.streak == 0
    assert e.step(a, 2) is None and a.streak == 1  # confirm_rounds=2: first eligible step just counts
    ev = e.step(a, 2)
    assert ev is not None and ev.kind == "ENTER_TOP2"
    assert a.state is AlertState.ALERTING


def test_alerting_order_becoming_ineligible_returns_to_normal_and_can_realert():
    e = AlertEngine(2, 4, 1)
    a = OrderAlert("P1")
    assert e.step(a, 1) is not None
    assert a.state is AlertState.ALERTING
    ev = e.step(a, 1, eligible=False)
    assert ev is None
    assert a.state is AlertState.NORMAL and a.streak == 0
    ev2 = e.step(a, 1)  # eligible again - fires right away (confirm_rounds=1)
    assert ev2 is not None and ev2.kind == "ENTER_TOP2"


def test_ineligible_still_records_real_last_rank():
    e = AlertEngine(2, 4, 2)
    a = OrderAlert("P1")
    e.step(a, 2, eligible=False)
    assert a.last_rank == 2
