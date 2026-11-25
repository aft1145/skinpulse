from skinpulse.config import PollingSettings
from skinpulse.core.planner import _is_night

P = PollingSettings()


# lane_for/interval_for/plan_round/OrderPlanInput/PlanItem described the old per-order
# polling cadence (a request per order, dispatched into fast/normal lanes) that the
# monitor rework replaced with one authoritative sync plus a small verification sample
# (see services/monitor.py's run_round) - they were removed as unreachable dead code.
# _is_night survives: Monitor.run_round revived it to gate the round's own cadence
# during quiet hours (see PollingSettings.night_start_hour/night_end_hour/night_multiplier).


def test_is_night_non_wrapping_default():
    """Default night window (0-8, non-wrapping): hours 0..7 are night, 8..23 are day."""
    assert _is_night(0, P) is True
    assert _is_night(7, P) is True
    assert _is_night(8, P) is False
    assert _is_night(23, P) is False


def test_is_night_wrapping_window():
    """A window crossing midnight (e.g. 22:00-6:00): night hours are 22, 23, 0..5."""
    p = PollingSettings(night_start_hour=22, night_end_hour=6)
    for h in (22, 23, 0, 1, 5):
        assert _is_night(h, p) is True
    for h in (6, 12, 21):
        assert _is_night(h, p) is False


def test_is_night_start_equals_end_means_never_night():
    """Edge case: night_start_hour == night_end_hour means no hour counts as night."""
    p = PollingSettings(night_start_hour=5, night_end_hour=5)
    assert all(_is_night(h, p) is False for h in range(24))
