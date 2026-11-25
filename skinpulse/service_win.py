"""`skinpulse service install | status | stop | start | restart | uninstall`：Windows 上的开机自启（Windows spec §3）。

对应 macOS 的 `scripts/macos/launchagent.sh`。登录时由注册表 `HKCU\\...\\Run` 里的 `SkinPulse` 值拉起守护进程
（`pythonw -m skinpulse supervise --root <仓库>`，见 supervise.py）；崩溃重启、防睡眠都由守护进程做。不需要管理员。

以前（ADR-0021）用的是启动目录里的 `SkinPulse.lnk`，真机上登录后没被执行，改成 Run 键（ADR-0023）；
`install` / `uninstall` 会顺手删掉旧版留下的快捷方式。

所有外部动作（读写注册表、起进程、结束进程、查锁）都可注入，测试在 macOS 上也能跑。"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from skinpulse.runlock import probe_lock
from skinpulse.supervise import LOCK_FILE, LOG_FILE, RUN_LOCK_FILE, STOP_FILE

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# 任务管理器「启动应用」里关掉时，Windows 在这里写同名的 REG_BINARY：第一个字节奇数 = 关掉
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
VALUE_NAME = "SkinPulse"
LNK_NAME = "SkinPulse.lnk"          # 旧版（ADR-0021）的启动目录快捷方式，只用来删
STOP_WAIT_S = 10.0


class ServiceError(Exception):
    """给用户看的错误（中文，已经说清楚下一步怎么做）。"""


# ---------- 命令行 ----------

def quote_arg(s: str) -> str:
    """按 CommandLineToArgvW 的规则加引号：总是包上双引号，紧挨着结尾引号的反斜杠成对（`D:\\` → `"D:\\\\"`）。

    Windows 路径里不会有双引号；真有就拒绝，免得拼出拆不回来的命令行。"""
    if '"' in s:
        raise ValueError("路径里有双引号")
    doubled = "\\" * (len(s) - len(s.rstrip("\\")))
    return f'"{s}{doubled}"'


def run_command(pythonw: Path, root: Path) -> str:
    return f"{quote_arg(str(pythonw))} -m skinpulse supervise --root {quote_arg(str(root))}"


_RUN_RE = re.compile(r'^"([^"]+)" -m skinpulse supervise --root "([^"]*)"$')


def parse_run_command(value: object) -> tuple[Path, Path] | None:
    """只认 `run_command` 自己拼出来的格式；认不出（别人写的、手改过的）返回 None。"""
    if not isinstance(value, str):
        return None
    m = _RUN_RE.match(value)
    if not m:
        return None
    root = m.group(2)
    trailing = len(root) - len(root.rstrip("\\"))
    return Path(m.group(1)), Path(root[: len(root) - trailing // 2])


def approved_disabled(data: object) -> bool:
    return isinstance(data, (bytes, bytearray)) and len(data) > 0 and bool(data[0] & 1)


# ---------- 注册表 ----------

class Registry(Protocol):
    def get(self, key: str, name: str) -> str | bytes | None: ...

    def set_str(self, key: str, name: str, value: str) -> None: ...

    def delete(self, key: str, name: str) -> None: ...      # 不存在不报错


class WinRegistry:
    """`HKEY_CURRENT_USER` 下的读写，标准库 winreg；只在 Windows 上真的调用。"""

    def get(self, key: str, name: str) -> str | bytes | None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                return winreg.QueryValueEx(k, name)[0]
        except FileNotFoundError:
            return None

    def set_str(self, key: str, name: str, value: str) -> None:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)

    def delete(self, key: str, name: str) -> None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, name)
        except FileNotFoundError:
            pass


# ---------- 外部动作 ----------

def startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise ServiceError("找不到 APPDATA 环境变量，没法定位「启动」文件夹")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _start_detached(argv: list[str], cwd: Path) -> None:
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(argv, cwd=cwd, creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _terminate(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)        # Windows 上就是 TerminateProcess；在途通知由下次启动的 recover_pending 收尾


@dataclass
class Service:
    root: Path                          # 仓库根目录（settings.toml、data/ 都相对它）
    data_dir: Path
    reg: Registry = field(default_factory=WinRegistry)
    startup: Callable[[], Path] = startup_dir
    start_detached: Callable[[list[str], Path], None] = _start_detached
    terminate: Callable[[int], None] = _terminate
    probe: Callable[[Path], int | None] = probe_lock
    sleep: Callable[[float], None] = time.sleep
    out: list[str] = field(default_factory=list)

    # ---------- 路径 ----------

    @property
    def pythonw(self) -> Path:
        return self.root / ".venv" / "Scripts" / "pythonw.exe"

    @property
    def lnk(self) -> Path:
        return self.startup() / LNK_NAME

    def _say(self, msg: str) -> None:
        self.out.append(msg)

    def supervisor_pid(self) -> int | None:
        return self.probe(self.data_dir / LOCK_FILE)

    def run_pid(self) -> int | None:
        return self.probe(self.data_dir / RUN_LOCK_FILE)

    def _remove_old_lnk(self) -> None:
        if self.lnk.exists():
            self.lnk.unlink()
            self._say(f"已删除旧版快捷方式：{self.lnk}")

    # 写注册表被拒（安全软件盯着 Run 键、组策略）时给一句人话，不甩 traceback；只记异常类型名
    def _reg_set(self, key: str, value: str) -> None:
        try:
            self.reg.set_str(key, VALUE_NAME, value)
        except OSError as e:
            raise ServiceError(self._denied(key, e)) from None

    def _reg_delete(self, key: str) -> None:
        try:
            self.reg.delete(key, VALUE_NAME)
        except OSError as e:
            raise ServiceError(self._denied(key, e)) from None

    @staticmethod
    def _denied(key: str, e: OSError) -> str:
        return (f"改不了注册表 HKCU\\{key}\\{VALUE_NAME}（{type(e).__name__}）：多半是安全软件或组策略拦了，"
                "在安全软件里放行后再跑一次；另外要在你自己的终端里跑，别让 AI 助手代跑")

    # ---------- 子命令 ----------

    def install(self) -> None:
        if not self.pythonw.exists():
            raise ServiceError(f"找不到 {self.pythonw}：先在仓库根目录跑 uv sync")
        if not (self.root / "settings.toml").exists():
            raise ServiceError(f"找不到 {self.root / 'settings.toml'}：先跑 uv run python -m skinpulse init-config")
        old = parse_run_command(self.reg.get(RUN_KEY, VALUE_NAME))
        if old is not None and old[1] != self.root:
            self._say(f"原来指向 {old[1]}，已改成这里")
        self._reg_set(RUN_KEY, run_command(self.pythonw, self.root))
        self._say(f"已写入开机自启：注册表 HKCU\\{RUN_KEY}\\{VALUE_NAME}")
        if approved_disabled(self.reg.get(APPROVED_KEY, VALUE_NAME)):
            self._say("它在任务管理器「启动应用」里是关着的，已重新打开")
        self._reg_delete(APPROVED_KEY)
        self._remove_old_lnk()
        self.start()

    def start(self) -> None:
        if self.lnk.exists():
            # 按 guide 升级（uv sync + restart）不会经过 install，旧版的快捷方式还在
            self._say(f"还在用旧版的启动目录快捷方式（{self.lnk}）：跑一次 service install，换成注册表 Run 键")
        pid = self.supervisor_pid()
        if pid is not None:
            self._say(f"守护进程已经在跑（pid {pid}）")
            return
        if not self.pythonw.exists():
            raise ServiceError(f"找不到 {self.pythonw}：先在仓库根目录跑 uv sync")
        (self.data_dir / STOP_FILE).unlink(missing_ok=True)
        self.start_detached([str(self.pythonw), "-m", "skinpulse", "supervise", "--root", str(self.root)], self.root)
        self._say("已启动：崩溃 30 秒内自动拉起，接电源时防睡眠。日志 data/logs/" + LOG_FILE)

    def stop(self, *, uninstalling: bool = False) -> None:
        sup = self.supervisor_pid()
        if sup is None:
            run = self.run_pid()
            if run is not None:
                raise ServiceError(f"守护进程没在跑，但有一个 skinpulse run（pid {run}）：它是手动起的，"
                                   "去那个终端里按 Ctrl+C")
            self._say("没有在跑")
            return
        (self.data_dir / STOP_FILE).write_text("stop\n", encoding="utf-8")
        run = self.run_pid()
        if run:
            self.terminate(run)
        waited = 0.0
        while self.supervisor_pid() is not None:
            if waited >= STOP_WAIT_S:
                raise ServiceError(f"等了 {STOP_WAIT_S:.0f} 秒守护进程（pid {sup}）还没退出：在任务管理器里结束它")
            self.sleep(0.5)
            waited += 0.5
        self._say("已停止" if uninstalling else "已停止；下次登录会自动启动（不想要就 service uninstall）")

    def restart(self) -> None:
        self.stop()
        self.start()

    def uninstall(self) -> None:
        # 先删自启再停：停不下来（有手动起的 run、守护进程不退）时，至少下次登录不会再起来
        had_value = self.reg.get(RUN_KEY, VALUE_NAME) is not None
        had_lnk = self.lnk.exists()
        self._reg_delete(RUN_KEY)
        self._reg_delete(APPROVED_KEY)
        if had_value:
            self._say("已删除开机自启（注册表 Run 键）")
        self._remove_old_lnk()
        if not had_value and not had_lnk:
            self._say("开机自启本来就没装")
        self.stop(uninstalling=True)

    def _autostart_line(self) -> list[str]:
        value = self.reg.get(RUN_KEY, VALUE_NAME)
        if value is None:
            return ["开机自启：没装"]
        parsed = parse_run_command(value)
        if parsed is None:
            return [f"开机自启：注册表里的 {VALUE_NAME} 项认不出来（{value}）：重新 service install"]
        pythonw, root = parsed
        for p in (pythonw, root / "settings.toml"):
            if not p.exists():
                return [f"开机自启：已安装，但 {p} 不在了，登录后起不来：重新 service install"]
        if root != self.root:
            return [f"开机自启：指向另一个目录 {root}（当前是 {self.root}）：要用这里的就在这里 service install"]
        if approved_disabled(self.reg.get(APPROVED_KEY, VALUE_NAME)):
            return ["开机自启：已安装，但在任务管理器「启动应用」里被关掉了：在那里打开，或重新 service install"]
        return ["开机自启：已安装（注册表 Run 键）", f"  命令：{value}"]

    def status(self, tail: int = 20) -> None:
        self.out.extend(self._autostart_line())
        if self.lnk.exists():
            self._say(f"旧版快捷方式还在：{self.lnk}（重新 service install 会删掉）")
        sup, run = self.supervisor_pid(), self.run_pid()
        self._say(f"守护进程：{f'在跑（pid {sup}）' if sup is not None else '没在跑'}")
        self._say(f"服务：{f'在跑（pid {run}）' if run is not None else '没在跑'}")
        log = self.data_dir / "logs" / LOG_FILE
        if log.exists():
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-tail:]
            self._say(f"--- {log} 最后 {len(lines)} 行 ---")
            self.out.extend(lines)
