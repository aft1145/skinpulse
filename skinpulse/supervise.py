"""Windows 上的守护进程：`skinpulse supervise`（Windows spec §2，ADR-0021、ADR-0023）。

相当于 macOS 上 launchd 的 KeepAlive + `caffeinate -s`：拉起 `skinpulse run`，等它退出，按退出码决定什么时候
再拉；接着电源时阻止系统睡眠。登录时由注册表 Run 键拉起（`skinpulse service install` 写入）；Run 键没有工作
目录，所以命令带 `--root <仓库>`，cli 先切过去再进 `main()`，子进程跟着用 `Path.cwd()`。

- 子进程退出码 `RESTART_EXIT`（系统页「重启服务」）→ 2 秒后重拉；
- 有 stop 文件（`skinpulse service stop` 写的）→ 删掉它，守护进程退出；
- 其它退出码（崩了、撞上单实例锁）→ 30 秒后重拉；10 分钟内连崩 10 次，改成 5 分钟一次，正常跑满 10 分钟清零。

调度逻辑只依赖注入进来的 spawn / sleep / clock / power，所以在 macOS 上也能单测；真的进程和 ctypes 只在
`main()` 里装配。"""
from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

RESTART_EXIT = 75
RESTART_DELAY_S = 2.0
CRASH_DELAY_S = 30.0
BACKOFF_DELAY_S = 300.0
BURST_WINDOW_S = 600.0
BURST_MAX = 10
HEALTHY_RUN_S = 600.0
POWER_POLL_S = 60.0
LOG_MAX_BYTES = 5 * 1024 * 1024

STOP_FILE = "skinpulse.stop"
LOCK_FILE = "skinpulse.supervise.lock"
RUN_LOCK_FILE = "skinpulse.run.lock"
LOG_FILE = "supervisor.log"
SUPERVISOR_ENV = "SKINPULSE_SUPERVISOR"
SUPERVISOR_NAME = "windows"


class Child(Protocol):
    pid: int

    def wait(self, timeout: float) -> int | None: ...  # 超时返回 None

    def terminate(self) -> None: ...


# ---------- 日志 ----------

class SupervisorLog:
    """`data/logs/supervisor.log`：子进程的 stdout / stderr 直接追加进来，守护进程自己的事件也写这里。

    只在没有子进程开着它的时候滚动（Windows 上别的进程开着的文件改不了名）。"""

    def __init__(self, path: Path, *, max_bytes: int = LOG_MAX_BYTES,
                 now: Callable[[], datetime] = datetime.now) -> None:
        self.path = path
        self.max_bytes = max_bytes
        self._now = now
        path.parent.mkdir(parents=True, exist_ok=True)

    # 日志只是尽力而为：写不进、改不了名（被编辑器或杀毒软件占着）都不能让守护进程跟着死
    def line(self, msg: str) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(f"{self._now().strftime('%Y-%m-%d %H:%M:%S')} [supervise] {msg}\n")
        except OSError:
            pass

    def rotate_if_big(self) -> None:
        try:
            if self.path.stat().st_size >= self.max_bytes:
                os.replace(self.path, self.path.with_name(self.path.name + ".1"))
        except OSError:
            pass


# ---------- 防睡眠 ----------

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


@dataclass
class KeepAwake:
    """接着电源时 `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`，否则撤掉。

    和 `caffeinate -s` 一样只拦系统睡眠、不拦关屏；状态挂在调用线程上，所以一直在守护进程主线程里调。
    `on_ac` 返回 None 表示查不到（当作没接电源）。"""

    on_ac: Callable[[], bool | None]
    set_state: Callable[[int], int]
    log: Callable[[str], None] = lambda _m: None
    awake: bool | None = field(default=None, init=False)

    def tick(self) -> None:
        want = self.on_ac() is True
        if want == self.awake:
            return
        flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED if want else ES_CONTINUOUS
        if self.set_state(flags) == 0:
            self.log("SetThreadExecutionState failed")
            return
        self.awake = want
        self.log("on AC power: keeping the system awake" if want else "on battery: system may sleep")

    def release(self) -> None:
        if self.awake:
            self.set_state(ES_CONTINUOUS)
            self.awake = False


def windows_keep_awake(log: Callable[[str], None]) -> KeepAwake:
    import ctypes
    from ctypes import wintypes

    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [("ACLineStatus", wintypes.BYTE), ("BatteryFlag", wintypes.BYTE),
                    ("BatteryLifePercent", wintypes.BYTE), ("SystemStatusFlag", wintypes.BYTE),
                    ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetThreadExecutionState.restype = ctypes.c_uint32
    kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint32]

    def on_ac() -> bool | None:
        st = SYSTEM_POWER_STATUS()
        if not kernel32.GetSystemPowerStatus(ctypes.byref(st)):
            return None
        return {0: False, 1: True}.get(st.ACLineStatus & 0xFF)      # 255 = 未知

    return KeepAwake(on_ac=on_ac, set_state=kernel32.SetThreadExecutionState, log=log)


def no_keep_awake() -> KeepAwake:
    return KeepAwake(on_ac=lambda: None, set_state=lambda _f: 1)


# ---------- 调度 ----------

@dataclass
class Backoff:
    """崩溃后等多久：10 分钟内连崩 `BURST_MAX` 次就拉长到 5 分钟；正常跑满 10 分钟清零。"""

    crashes: list[float] = field(default_factory=list)

    def after_exit(self, code: int, ran_for: float, now: float) -> float:
        if ran_for >= HEALTHY_RUN_S:
            self.crashes.clear()
        if code == RESTART_EXIT:
            return RESTART_DELAY_S
        self.crashes = [t for t in self.crashes if now - t < BURST_WINDOW_S] + [now]
        return BACKOFF_DELAY_S if len(self.crashes) >= BURST_MAX else CRASH_DELAY_S


def supervise(data_dir: Path, *, spawn: Callable[[], Child], sleep: Callable[[float], None],
              clock: Callable[[], float], power: KeepAwake, log: SupervisorLog,
              max_starts: int | None = None) -> int:
    """一直跑到 stop 文件出现。`max_starts` 只给测试用。返回进程退出码。"""
    stop = data_dir / STOP_FILE
    stop.unlink(missing_ok=True)           # 上次 stop 留下的（守护进程那时已经不在）不算数
    backoff = Backoff()
    starts = 0
    try:
        while True:
            if stop.exists():
                stop.unlink(missing_ok=True)
                log.line("stop requested; exiting")
                return 0
            if max_starts is not None and starts >= max_starts:
                return 0
            log.rotate_if_big()
            started = clock()
            starts += 1
            try:
                child = spawn()
            except OSError as e:               # 比如 .venv 被删了：别让守护进程跟着死，照崩溃处理
                log.line(f"could not start skinpulse run ({type(e).__name__})")
                code = -1
            else:
                log.line(f"started skinpulse run (pid {child.pid})")
                code = None
                polled = 0
                while code is None:
                    if polled % int(POWER_POLL_S) == 0:
                        power.tick()
                    # stop 文件要自己盯着：子进程刚拉起、还没拿到 run 锁时，service stop 查不到它的 pid
                    if stop.exists():
                        child.terminate()
                    code = child.wait(1.0)
                    polled += 1
            if stop.exists():
                stop.unlink(missing_ok=True)
                log.line(f"skinpulse run exited ({code}) after a stop request; exiting")
                return 0
            delay = backoff.after_exit(code, clock() - started, clock())
            why = "restart requested" if code == RESTART_EXIT else f"exited with code {code}"
            log.line(f"skinpulse run {why}; starting again in {delay:.0f}s")
            waited = 0.0
            while waited < delay and not stop.exists():
                if waited % POWER_POLL_S == 0:
                    power.tick()
                step = min(1.0, delay - waited)
                sleep(step)
                waited += step
    finally:
        power.release()


# ---------- 真的进程 ----------

class _Popen:
    def __init__(self, proc: subprocess.Popen, run_lock: Path) -> None:
        self._p = proc
        self.pid = proc.pid
        self._run_lock = run_lock

    def wait(self, timeout: float) -> int | None:
        try:
            return self._p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def terminate(self) -> None:
        """先按 run 锁里的 pid 结束真正的解释器（venv 的 python.exe 可能只是个启动器），再结束直接子进程。"""
        import signal

        from skinpulse.runlock import probe_lock
        try:
            pid = probe_lock(self._run_lock)
            if pid:
                os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            self._p.terminate()
        except OSError:
            pass


def child_command(settings: Path | None) -> list[str]:
    cmd = [sys.executable, "-m", "skinpulse", "run", "--console-level", "WARNING"]
    if settings is not None:
        cmd += ["--settings", str(settings)]
    return cmd


def make_spawn(log_path: Path, settings: Path | None, run_lock: Path) -> Callable[[], Child]:
    # 子进程的输出直接进 supervisor.log：统一成 UTF-8，不然中文 Windows 上是 GBK，和守护进程自己写的混在一起
    env = {**os.environ, SUPERVISOR_ENV: SUPERVISOR_NAME, "PYTHONIOENCODING": "utf-8"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def spawn() -> Child:
        with log_path.open("ab") as out:    # 子进程拿到自己的句柄；这里关掉不影响它
            proc = subprocess.Popen(child_command(settings), cwd=Path.cwd(), env=env, stdin=subprocess.DEVNULL,
                                    stdout=out, stderr=subprocess.STDOUT, creationflags=flags)
        return _Popen(proc, run_lock)

    return spawn


def main(data_dir: Path, settings: Path | None = None) -> int:
    import time

    from skinpulse.runlock import RunLockHeld, acquire_run_lock

    log = SupervisorLog(data_dir / "logs" / LOG_FILE)
    try:
        lock = acquire_run_lock(data_dir / LOCK_FILE)
    except RunLockHeld as e:
        log.line(f"another supervisor is running (pid {e.pid}); exiting")
        return 1
    with lock:
        power = windows_keep_awake(log.line) if sys.platform == "win32" else no_keep_awake()
        log.line(f"supervisor started (pid {os.getpid()})")
        return supervise(data_dir, spawn=make_spawn(log.path, settings, data_dir / RUN_LOCK_FILE), sleep=time.sleep,
                         clock=time.monotonic, power=power, log=log)
