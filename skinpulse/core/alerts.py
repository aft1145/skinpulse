from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable


class AlertState(str, Enum):
    NORMAL = "NORMAL"
    ALERTING = "ALERTING"


@dataclass
class OrderAlert:
    purchase_no: str
    state: AlertState = AlertState.NORMAL
    streak: int = 0
    last_rank: int | None = None


@dataclass(frozen=True)
class AlertEvent:
    kind: str
    purchase_no: str
    rank: int


class AlertEngine:
    def __init__(self, enter_rank: int, exit_rank: int, confirm_rounds: int) -> None:
        self.enter_rank = enter_rank
        self.exit_rank = exit_rank
        self.confirm_rounds = max(1, confirm_rounds)

    def step(self, alert: OrderAlert, rank: int | None, *, eligible: bool = True) -> AlertEvent | None:
        """`eligible=False` (see Monitor.run_round's gap filter, config.AlertSettings.
        max_gap_ratio) means the order technically sits at `rank`, but is not worth
        alerting on right now - e.g. its leading bid is so far above the owner's own
        price that outbidding would be absurd. It must NOT be handled by simply
        dropping the resulting event: that would leave `alert.state` at ALERTING with
        no way back out (AlertEngine.step only exits ALERTING when rank climbs to
        exit_rank or beyond - a rank that never happens here since the order actually
        sits inside the alerting band). Instead treat it exactly as if `rank` were
        outside the alerting band on the NORMAL side: reset the streak, and drop back
        out of ALERTING to NORMAL if it was there - so the order alerts normally again
        the moment it becomes eligible (the gap narrows, or the leader drops out).
        `last_rank` still records the real rank passed in - the dashboard shows it and
        it must stay truthful regardless of eligibility."""
        if rank is None:
            return None
        alert.last_rank = rank
        if not eligible:
            alert.streak = 0
            alert.state = AlertState.NORMAL
            return None
        if alert.state is AlertState.NORMAL:
            if rank <= self.enter_rank:
                alert.streak += 1
                if alert.streak >= self.confirm_rounds:
                    alert.state = AlertState.ALERTING
                    alert.streak = 0
                    return AlertEvent("ENTER_TOP2", alert.purchase_no, rank)
            else:
                alert.streak = 0
            return None
        # ALERTING
        if rank >= self.exit_rank:
            alert.state = AlertState.NORMAL
            alert.streak = 0
        return None


class HourlyCap:
    def __init__(self, limit: int, clock: Callable[[], float] = time.time) -> None:
        self.limit = limit
        self.clock = clock
        self._stamps: deque[float] = deque()

    def allow(self, n: int = 1) -> bool:
        now = self.clock()
        while self._stamps and now - self._stamps[0] > 3600:
            self._stamps.popleft()
        if len(self._stamps) + n > self.limit:
            return False
        self._stamps.extend([now] * n)
        return True
