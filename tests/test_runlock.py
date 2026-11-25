"""`run` 的单实例锁（spec §11）。同一个 data_dir 同时只能有一个服务在跑：两个实例会各推一遍
告警和简报，对悠悠的请求量也翻倍（ADR-0009）。launchd 拉起一个、终端里又手动跑一个，是 Mac 上
最现实的撞车方式。"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from skinpulse.runlock import RunLockHeld, acquire_run_lock


def test_acquire_writes_own_pid(tmp_path):
    path = tmp_path / "skinpulse.run.lock"
    with acquire_run_lock(path):
        assert path.read_text(encoding="ascii").strip() == str(os.getpid())


def test_second_acquire_is_refused_and_names_the_holder(tmp_path):
    path = tmp_path / "skinpulse.run.lock"
    with acquire_run_lock(path):
        with pytest.raises(RunLockHeld) as exc:
            acquire_run_lock(path)
        assert exc.value.pid == os.getpid()
        # 抢锁失败的一方不能把赢家写的 pid 冲掉（所以打开时不能截断）
        assert path.read_text(encoding="ascii").strip() == str(os.getpid())


def test_lock_can_be_taken_again_after_release(tmp_path):
    path = tmp_path / "skinpulse.run.lock"
    lock = acquire_run_lock(path)
    lock.release()
    lock.release()  # 重复释放无害
    with acquire_run_lock(path):
        pass


def test_stale_pid_file_without_a_holder_is_taken_over(tmp_path):
    """上一个实例崩了/被 kill 后留下的锁文件：没人持锁，就直接接管，不需要人手动清理。"""
    path = tmp_path / "skinpulse.run.lock"
    path.write_text("999999999\n", encoding="ascii")
    with acquire_run_lock(path):
        assert path.read_text(encoding="ascii").strip() == str(os.getpid())


def test_holder_without_a_readable_pid_reports_none(tmp_path):
    path = tmp_path / "skinpulse.run.lock"
    with acquire_run_lock(path):
        path.write_text("", encoding="ascii")
        with pytest.raises(RunLockHeld) as exc:
            acquire_run_lock(path)
        assert exc.value.pid is None


def test_lock_file_is_left_in_place_after_release(tmp_path):
    """不删锁文件：删了的话，新实例会锁住一个新文件，而老实例还攥着旧文件的锁——两个都「拿到了锁」。"""
    path = tmp_path / "skinpulse.run.lock"
    with acquire_run_lock(path):
        pass
    assert path.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="用 POSIX 信号杀子进程")
def test_lock_held_by_another_process_and_freed_when_it_is_killed(tmp_path):
    path = tmp_path / "skinpulse.run.lock"
    child = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys
            from skinpulse.runlock import acquire_run_lock
            lock = acquire_run_lock({str(path)!r})
            print("ready", flush=True)
            sys.stdin.read()
        """)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(RunLockHeld) as exc:
            acquire_run_lock(path)
        assert exc.value.pid == child.pid
    finally:
        child.kill()
        child.wait()
        child.stdin.close()
        child.stdout.close()
    # 被 kill -9 的进程来不及做任何清理，锁由操作系统释放
    with acquire_run_lock(path):
        pass


def test_probe_reports_the_holder_without_touching_the_file(tmp_path):
    from skinpulse.runlock import probe_lock

    path = tmp_path / "skinpulse.run.lock"
    assert probe_lock(path) is None                       # 文件都没有
    with acquire_run_lock(path):
        assert probe_lock(path) == os.getpid()
        assert path.read_text(encoding="ascii").strip() == str(os.getpid())
    assert probe_lock(path) is None                       # 放掉以后文件还在，但没人拿着
    with acquire_run_lock(path):                          # 探过以后照样拿得到
        pass
