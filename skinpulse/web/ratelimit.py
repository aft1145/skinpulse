"""网页换 token 的限流（看板 spec §6）：每 5 秒最多 1 次，连续失败 5 次锁 1 分钟。

粘贴 token 是唯一一个会让看板去请求悠悠的操作（校验一次、同步一次），不能被刷：既怕把悠悠的
风控招来，也怕被当成撞库入口。时钟可注入，测试不用真等。进程内状态，服务重启就清零。
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable


class Throttle:
    def __init__(self, *, min_interval_s: float = 5, max_failures: int = 5, lockout_s: float = 60,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.min_interval_s = min_interval_s
        self.max_failures = max_failures
        self.lockout_s = lockout_s
        self.clock = clock
        self._last: float | None = None
        self._failures = 0
        self._locked_until: float | None = None
        self._lock = threading.Lock()

    def acquire(self) -> float | None:
        """占一次尝试的名额：能试就返回 None（并记下这次尝试），不能就返回还要等几秒。

        判断和占位在同一把锁里：并发的几个请求只有一个能拿到名额，别的都要等（分开写成
        「先查再记」的话，同时到的请求会一起通过检查）。"""
        now = self.clock()
        with self._lock:
            if self._locked_until is not None and now < self._locked_until:
                return self._locked_until - now
            if self._last is not None and now - self._last < self.min_interval_s:
                return self.min_interval_s - (now - self._last)
            self._last = now
            return None

    def record(self, *, ok: bool) -> None:
        """记下这次尝试的结果：连续失败够数就锁。"""
        now = self.clock()
        with self._lock:
            if ok:
                self._failures = 0
                return
            self._failures += 1
            if self._failures >= self.max_failures:
                self._locked_until = now + self.lockout_s
                self._failures = 0
