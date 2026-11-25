"""`skinpulse run` 的单实例锁（spec §11）。

同一个 data_dir 同一时刻只允许一个服务：两个实例会各推一遍告警和简报，对悠悠的请求量也翻倍
（ADR-0009）。Mac 上最现实的撞车方式是 launchd 拉起了一个、用户又在终端里手动跑一个。

锁是操作系统的文件锁，进程一退出（包括崩溃、被 kill -9）就自动释放，所以不会有要手动清理的
陈旧锁。几条不能改的细节：

- POSIX 用 `fcntl.flock`，**不能用 `lockf`**：`lockf`（fcntl 记录锁）属于进程，同一进程
  再开一个 fd 不冲突，关掉任意一个 fd 锁就没了。
- 打开时**不截断**：抢锁失败的一方要读出赢家的 pid 报给用户，截断会把它冲掉。赢了才截断重写。
- 退出时**不删文件**：删了的话，新实例会锁住一个新文件，老实例还攥着旧文件——两个都「拿到了锁」。
- Windows 的 `msvcrt.locking` 锁的是字节区间，而且被锁的字节别的进程读不了；所以锁 pid 后面
  远处的一个字节，让失败方还能读 pid。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_WIN_LOCK_OFFSET = 4096


class RunLockHeld(Exception):
    def __init__(self, path: Path, pid: int | None) -> None:
        self.path = path
        self.pid = pid
        who = f"pid {pid}" if pid is not None else "pid 未知"
        super().__init__(f"{path} is held by another skinpulse run ({who})")


class RunLock:
    def __init__(self, path: Path, fd: int) -> None:
        self.path = path
        self._fd: int | None = fd

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        if sys.platform == "win32":
            import msvcrt
            try:
                os.lseek(fd, _WIN_LOCK_OFFSET, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        os.close(fd)  # POSIX：关掉 fd 即释放 flock

    def __enter__(self) -> RunLock:
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def _read_pid(fd: int) -> int | None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        return int(os.read(fd, 64).decode("ascii").strip())
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _try_lock(fd: int) -> bool:
    if sys.platform == "win32":
        import msvcrt
        os.lseek(fd, _WIN_LOCK_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt
        os.lseek(fd, _WIN_LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    import fcntl
    fcntl.flock(fd, fcntl.LOCK_UN)


def probe_lock(path: Path | str) -> int | None:
    """锁有人拿着就返回持有者的 pid（读不出来时返回 0），没人拿着返回 None。不改写锁文件。

    `skinpulse service status / stop` 用：锁文件退出时不删，光读文件里的 pid 分不清它还活着没有。"""
    path = Path(path)
    if not path.exists():
        return None
    fd = os.open(path, os.O_RDWR)
    try:
        if _try_lock(fd):
            _unlock(fd)
            return None
        pid = _read_pid(fd)
        return pid if pid is not None else 0
    finally:
        os.close(fd)


def acquire_run_lock(path: Path | str) -> RunLock:
    """拿到锁就返回 RunLock（调用方要一直持有它直到进程结束）；已被别人持有就抛 RunLockHeld。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if not _try_lock(fd):
            raise RunLockHeld(path, _read_pid(fd))
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        raise
    return RunLock(path, fd)
