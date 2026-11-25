from __future__ import annotations

import random
import time
from typing import Callable


def jittered(seconds: float, pct: float, rnd: Callable[[], float] = random.random) -> float:
    return seconds * (1 + (2 * rnd() - 1) * pct)


class TokenBucket:
    def __init__(self, capacity: int, refill_per_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.capacity = capacity
        self.refill = refill_per_s
        self.clock = clock
        self.tokens = float(capacity)
        self._last = clock()

    def _refill(self) -> None:
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self._last) * self.refill)
        self._last = now

    def try_acquire(self, n: int = 1) -> bool:
        self._refill()
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False

    def seconds_until_available(self, n: int = 1) -> float:
        self._refill()
        if self.tokens >= n:
            return 0.0
        return (n - self.tokens) / self.refill


_SCHEDULE = {
    "risk_control": ([30 * 60, 60 * 60, 120 * 60, 240 * 60], 240 * 60),
    "rate_limited": ([300, 600, 1200, 1800], 1800),
    "uk_expired": ([60], 60),
    "login_required": ([0], 0),
    "other": ([30], 30),
}


class BackoffGuard:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._paused_until = 0.0
        self._streak: dict[str, int] = {}

    def paused_for(self) -> float:
        return max(0.0, self._paused_until - self.clock())

    def on_success(self) -> None:
        self._streak.clear()

    def on_error(self, kind: str) -> float:
        steps, cap = _SCHEDULE.get(kind, _SCHEDULE["other"])
        i = self._streak.get(kind, 0)
        pause = steps[i] if i < len(steps) else cap
        self._streak[kind] = i + 1
        self._paused_until = max(self._paused_until, self.clock() + pause)
        return pause
