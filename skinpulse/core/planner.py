from __future__ import annotations

from skinpulse.config import PollingSettings

# lane_for/interval_for/plan_round/OrderPlanInput/PlanItem used to live here: the old
# per-order polling design computed a fast/normal "lane" and a per-order interval for
# every order, and plan_round decided which orders were due each round. The monitor
# rework (see services/monitor.py's run_round) replaced that with one authoritative
# sync (pass A) plus a small verification sample (pass B), so nothing calls those
# helpers any more - removed as unreachable dead code, along with their tests (see
# tests/test_planner.py).
#
# _is_night survives: Monitor.run_round revived it to gate the round's own cadence
# during quiet hours (see PollingSettings.night_start_hour/night_end_hour/night_multiplier).


def _is_night(h: int, p: PollingSettings) -> bool:
    if p.night_start_hour > p.night_end_hour:
        # Wrapping window: night from start hour through midnight to end hour
        return h >= p.night_start_hour or h < p.night_end_hour
    else:
        # Non-wrapping window: night from start to end hour
        return p.night_start_hour <= h < p.night_end_hour
