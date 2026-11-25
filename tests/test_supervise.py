"""Windows 守护进程 `skinpulse supervise`（Windows spec §2）：调度逻辑用替身测，任何平台都跑；
真的拉进程、真的 ctypes 只在 Windows 上跑。"""
from __future__ import annotations

import sys
import textwrap
from datetime import datetime
from pathlib import Path

import pytest

from skinpulse import supervise as sv

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="只在 Windows 上有")


class _Child:
    def __init__(self, pid, code, ticks=1):
        self.pid, self.code, self.ticks = pid, code, ticks
        self.terminated = False

    def wait(self, timeout):
        if self.terminated:
            return 15
        self.ticks -= 1
        return self.code if self.ticks <= 0 else None

    def terminate(self):
        self.terminated = True


class _Harness:
    """按顺序吐出子进程；时钟只在 sleep 和 wait 时走。"""

    def __init__(self, tmp_path, codes, *, run_for=1.0, on_spawn=None):
        self.data = tmp_path
        self.codes = list(codes)
        self.run_for = run_for
        self.now = 0.0
        self.slept = []
        self.on_spawn = on_spawn
        self.power_ticks = 0
        self.log = sv.SupervisorLog(tmp_path / "logs" / sv.LOG_FILE, now=lambda: datetime(2026, 11, 24, 9, 0))

    def spawn(self):
        n = len(self.codes)
        code = self.codes.pop(0)
        if self.on_spawn:
            self.on_spawn(n)
        self.now += self.run_for
        if isinstance(code, Exception):
            raise code
        return _Child(1000 + n, code)

    def sleep(self, s):
        self.slept.append(s)
        self.now += s

    def run(self, max_starts=None):
        power = sv.KeepAwake(on_ac=lambda: True, set_state=lambda f: 1)
        orig = power.tick

        def tick():
            self.power_ticks += 1
            orig()
        power.tick = tick
        return sv.supervise(self.data, spawn=self.spawn, sleep=self.sleep, clock=lambda: self.now, power=power,
                            log=self.log, max_starts=max_starts if max_starts is not None else len(self.codes))

    def logged(self):
        return self.log.path.read_text(encoding="utf-8")


def test_crash_waits_30s_restart_request_waits_2s(tmp_path):
    h = _Harness(tmp_path, [1, sv.RESTART_EXIT, 0])
    assert h.run() == 0
    total = sum(h.slept)
    assert total == sv.CRASH_DELAY_S + sv.RESTART_DELAY_S + sv.CRASH_DELAY_S
    text = h.logged()
    assert "exited with code 1; starting again in 30s" in text
    assert "restart requested; starting again in 2s" in text
    assert "exited with code 0; starting again in 30s" in text     # 正常退出也拉起（和 launchd KeepAlive 一样）


def test_stop_file_ends_the_supervisor_after_the_child_exits(tmp_path):
    def on_spawn(n):
        (tmp_path / sv.STOP_FILE).write_text("stop\n", encoding="utf-8")

    h = _Harness(tmp_path, [15, 1, 1], on_spawn=on_spawn)
    assert h.run() == 0
    assert h.codes == [1, 1]                                         # 只拉了一次
    assert not (tmp_path / sv.STOP_FILE).exists()
    assert "after a stop request; exiting" in h.logged()


def test_stop_file_while_waiting_to_restart(tmp_path):
    h = _Harness(tmp_path, [1, 1])
    orig = h.sleep

    def sleep(s):
        orig(s)
        if len(h.slept) == 3:
            (tmp_path / sv.STOP_FILE).write_text("stop\n", encoding="utf-8")
    h.sleep = sleep
    assert h.run() == 0
    assert len(h.slept) == 3 and h.codes == [1]                     # 等到第 3 秒就收手，没再拉
    assert "stop requested; exiting" in h.logged()


def test_stop_file_while_the_child_runs_terminates_it(tmp_path):
    """子进程刚拉起、还没拿到 run 锁时 service stop 找不到它的 pid：守护进程自己看到 stop 文件就结束它。"""
    children = []

    def spawn():
        c = _Child(1, None, ticks=10**9)                # 自己永远不退出
        children.append(c)
        (tmp_path / sv.STOP_FILE).write_text("stop\n", encoding="utf-8")
        return c

    log = sv.SupervisorLog(tmp_path / "logs" / sv.LOG_FILE)
    code = sv.supervise(tmp_path, spawn=spawn, sleep=lambda s: None, clock=lambda: 0.0,
                        power=sv.no_keep_awake(), log=log, max_starts=3)
    assert code == 0 and len(children) == 1 and children[0].terminated


def test_log_errors_never_kill_the_supervisor(tmp_path, monkeypatch):
    log = sv.SupervisorLog(tmp_path / "logs" / "s.log", max_bytes=1)
    log.line("x")

    def boom(*a, **k):
        raise PermissionError("held by another process")
    monkeypatch.setattr(sv.os, "replace", boom)
    log.rotate_if_big()
    monkeypatch.setattr(type(log.path), "open", boom)
    log.line("y")


def test_child_output_is_forced_to_utf8(tmp_path, monkeypatch):
    seen = {}

    class _P:
        pid = 1

    def fake_popen(cmd, **kw):
        seen.update(kw["env"])
        return _P()
    monkeypatch.setattr(sv.subprocess, "Popen", fake_popen)
    sv.make_spawn(tmp_path / "s.log", None, tmp_path / sv.RUN_LOCK_FILE)()
    assert seen["PYTHONIOENCODING"] == "utf-8" and seen["SKINPULSE_SUPERVISOR"] == "windows"


def test_leftover_stop_file_from_last_time_is_ignored(tmp_path):
    (tmp_path / sv.STOP_FILE).write_text("stop\n", encoding="utf-8")
    h = _Harness(tmp_path, [1])
    h.run()
    assert h.codes == []                                             # 照样拉起了


def test_burst_of_crashes_backs_off_and_a_healthy_run_resets():
    b = sv.Backoff()
    delays = [b.after_exit(1, ran_for=5, now=10.0 * i) for i in range(sv.BURST_MAX)]
    assert delays[:-1] == [sv.CRASH_DELAY_S] * (sv.BURST_MAX - 1) and delays[-1] == sv.BACKOFF_DELAY_S
    assert b.after_exit(1, ran_for=sv.HEALTHY_RUN_S, now=200.0) == sv.CRASH_DELAY_S    # 跑满 10 分钟清零
    assert b.after_exit(sv.RESTART_EXIT, ran_for=1, now=201.0) == sv.RESTART_DELAY_S   # 重启不算崩


def test_old_crashes_fall_out_of_the_window():
    b = sv.Backoff()
    for i in range(sv.BURST_MAX - 1):
        b.after_exit(1, ran_for=5, now=float(i))
    assert b.after_exit(1, ran_for=5, now=sv.BURST_WINDOW_S + 100) == sv.CRASH_DELAY_S


def test_spawn_failure_is_treated_as_a_crash(tmp_path):
    h = _Harness(tmp_path, [FileNotFoundError("pythonw.exe"), 0])
    h.run()
    assert "could not start skinpulse run (FileNotFoundError)" in h.logged()
    assert "pythonw.exe" not in h.logged()                           # 只记类型名


def test_power_is_checked_while_the_child_runs(tmp_path):
    h = _Harness(tmp_path, [1])
    h.run()
    assert h.power_ticks >= 1


def test_keep_awake_only_on_ac_and_releases():
    calls, ac = [], [True]
    k = sv.KeepAwake(on_ac=lambda: ac[0], set_state=lambda f: calls.append(f) or 1)
    k.tick()
    k.tick()                                                         # 没变就不重复调
    ac[0] = None                                                     # 查不到当作没接电源
    k.tick()
    k.tick()
    ac[0] = True
    k.tick()
    k.release()
    on, off = sv.ES_CONTINUOUS | sv.ES_SYSTEM_REQUIRED, sv.ES_CONTINUOUS
    assert calls == [on, off, on, off]


def test_keep_awake_failure_is_logged_and_retried():
    lines = []
    k = sv.KeepAwake(on_ac=lambda: True, set_state=lambda f: 0, log=lines.append)
    k.tick()
    k.tick()
    assert lines == ["SetThreadExecutionState failed"] * 2 and k.awake is None


def test_log_rotates_only_when_big(tmp_path):
    log = sv.SupervisorLog(tmp_path / "logs" / "s.log", max_bytes=50)
    log.line("x")
    log.rotate_if_big()
    assert not (tmp_path / "logs" / "s.log.1").exists()
    log.line("y" * 60)
    log.rotate_if_big()
    assert (tmp_path / "logs" / "s.log.1").exists() and not (tmp_path / "logs" / "s.log").exists()


def test_child_command_passes_settings_through(tmp_path):
    assert sv.child_command(None)[-4:] == ["skinpulse", "run", "--console-level", "WARNING"]
    assert sv.child_command(tmp_path / "s.toml")[-2:] == ["--settings", str(tmp_path / "s.toml")]


def test_second_supervisor_exits(tmp_path):
    from skinpulse.runlock import acquire_run_lock

    with acquire_run_lock(tmp_path / sv.LOCK_FILE):
        assert sv.main(tmp_path) == 1
    assert "another supervisor is running" in (tmp_path / "logs" / sv.LOG_FILE).read_text(encoding="utf-8")


def _record_main(monkeypatch):
    calls = []

    def fake_main(data_dir, settings=None):
        calls.append((Path.cwd(), data_dir.resolve(), settings))
        return 0

    monkeypatch.setattr(sv, "main", fake_main)
    return calls


def test_cli_supervise_root_switches_to_the_repo_first(tmp_path, monkeypatch):
    """Run 键没有工作目录（ADR-0023）：`--root` 先切过去，settings.toml、data/ 都按仓库根解析。"""
    from typer.testing import CliRunner

    from skinpulse.cli import app

    repo = tmp_path / "Skin & Pulse"
    repo.mkdir()
    (repo / "settings.toml").write_text("", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)                    # 结束时 monkeypatch 把工作目录还原
    calls = _record_main(monkeypatch)
    r = CliRunner().invoke(app, ["supervise", "--root", str(repo)])
    assert r.exit_code == 0, r.output
    [(cwd, data_dir, _)] = calls
    assert cwd == repo.resolve() and data_dir == (repo / "data").resolve()
    assert not (elsewhere / "data").exists()


def test_cli_supervise_root_must_exist(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from skinpulse.cli import app

    monkeypatch.chdir(tmp_path)
    calls = _record_main(monkeypatch)
    r = CliRunner().invoke(app, ["supervise", "--root", str(tmp_path / "gone")])
    assert r.exit_code == 2 and calls == [] and Path.cwd() == tmp_path
    assert "No such option" not in r.output                # typer 对不认识的参数也退出 2，别让这条白绿


# ---------- 只在 Windows 上跑：真的进程、真的 ctypes ----------

@windows_only
def test_real_child_restart_code_and_stop(tmp_path, monkeypatch):
    """用一个假的「run」脚本：第一次以 75 退出，第二次写 stop 文件后退出。"""
    script = tmp_path / "fake_run.py"
    script.write_text(textwrap.dedent(f"""
        import os, pathlib, sys
        marker = pathlib.Path(r"{tmp_path}") / "count"
        n = int(marker.read_text()) + 1 if marker.exists() else 1
        marker.write_text(str(n))
        assert os.environ["SKINPULSE_SUPERVISOR"] == "windows"
        if n >= 2:
            (pathlib.Path(r"{tmp_path}") / "{sv.STOP_FILE}").write_text("stop")
        sys.exit({sv.RESTART_EXIT} if n == 1 else 0)
    """), encoding="utf-8")
    monkeypatch.setattr(sv, "child_command", lambda settings: [sys.executable, str(script)])
    log = sv.SupervisorLog(tmp_path / "logs" / sv.LOG_FILE)
    code = sv.supervise(tmp_path, spawn=sv.make_spawn(log.path, None, tmp_path / sv.RUN_LOCK_FILE), sleep=lambda s: None,
                        clock=lambda: 0.0, power=sv.no_keep_awake(), log=log)
    assert code == 0 and (tmp_path / "count").read_text() == "2"


@windows_only
def test_real_power_calls_work():
    k = sv.windows_keep_awake(lambda m: None)
    assert k.on_ac() in (True, False, None)
    k.tick()
    k.release()

