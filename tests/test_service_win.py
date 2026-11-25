"""`skinpulse service ...`（Windows spec §3，ADR-0023）：注册表和进程都换成替身，任何平台都能跑；
真的读写注册表、真的按 Windows 规则拆命令行只在 Windows 上跑。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from skinpulse import service_win as sw
from skinpulse.service_win import APPROVED_KEY, RUN_KEY, VALUE_NAME, Service, ServiceError
from skinpulse.supervise import LOCK_FILE, LOG_FILE, RUN_LOCK_FILE, STOP_FILE

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="只在 Windows 上有")


class DictRegistry:
    def __init__(self):
        self.d: dict[tuple[str, str], str | bytes] = {}

    def get(self, key, name):
        return self.d.get((key, name))

    def set_str(self, key, name, value):
        self.d[(key, name)] = value

    def delete(self, key, name):
        self.d.pop((key, name), None)


def _make_repo(root: Path) -> None:
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / ".venv" / "Scripts").mkdir(parents=True, exist_ok=True)
    (root / ".venv" / "Scripts" / "pythonw.exe").write_bytes(b"")
    (root / "settings.toml").write_text("", encoding="utf-8")


class _World:
    """假装的进程表：锁文件名 → pid。terminate 结束 run；守护进程看到 stop 文件后也退出。"""

    def __init__(self, tmp_path, *, sup=None, run=None, sup_ignores_stop=False):
        self.root = tmp_path / "Skin & Pulse"
        self.data = self.root / "data"
        _make_repo(self.root)
        self.startup = tmp_path / "Startup"
        self.reg = DictRegistry()
        self.pids = {LOCK_FILE: sup, RUN_LOCK_FILE: run}
        self.sup_ignores_stop = sup_ignores_stop
        self.started, self.killed = [], []

    def probe(self, path):
        pid = self.pids[path.name]
        if path.name == LOCK_FILE and pid and (self.data / STOP_FILE).exists() and not self.sup_ignores_stop \
                and self.pids[RUN_LOCK_FILE] is None:
            self.pids[LOCK_FILE] = pid = None
        return pid

    def terminate(self, pid):
        self.killed.append(pid)
        self.pids[RUN_LOCK_FILE] = None

    def service(self, root=None):
        root = root or self.root
        return Service(root=root, data_dir=root / "data", reg=self.reg, startup=lambda: self.startup,
                       start_detached=lambda argv, cwd: self.started.append((argv, cwd)),
                       terminate=self.terminate, probe=self.probe, sleep=lambda s: None)

    @property
    def run_value(self):
        return self.reg.get(RUN_KEY, VALUE_NAME)

    def old_lnk(self):
        self.startup.mkdir(exist_ok=True)
        (self.startup / sw.LNK_NAME).write_bytes(b"lnk")
        return self.startup / sw.LNK_NAME


def _status(w, root=None) -> str:
    s = w.service(root)
    s.status()
    return "\n".join(s.out)


# ---------- 命令行拼装 ----------

@pytest.mark.parametrize("s, quoted", [
    (r"C:\Repo", r'"C:\Repo"'),
    (r"D:\\"[:-1], r'"D:\\"'),                                  # 盘符根：结尾反斜杠要成对
    (r"C:\My Projects\Skin & Pulse", r'"C:\My Projects\Skin & Pulse"'),
    (r"C:\a'b\中文", r'''"C:\a'b\中文"'''),
    (r"C:\trail\\"[:-1], r'"C:\trail\\"'),
])
def test_quote_arg(s, quoted):
    assert sw.quote_arg(s) == quoted


def test_quote_arg_refuses_a_double_quote():
    with pytest.raises(ValueError):
        sw.quote_arg('C:\\a"b')


@pytest.mark.parametrize("root", [r"C:\Repo", "D:\\", r"C:\My Projects\Skin & Pulse", r"E:\a'b\中文"])
def test_run_command_round_trips(root):
    py = Path(root) / ".venv" / "Scripts" / "pythonw.exe"
    cmd = sw.run_command(py, Path(root))
    assert " -m skinpulse supervise --root " in cmd
    assert sw.parse_run_command(cmd) == (py, Path(root))


@pytest.mark.parametrize("value", ["", "notepad.exe", '"C:\\x\\pythonw.exe" -m other', 42])
def test_parse_run_command_rejects_what_it_did_not_write(value):
    assert sw.parse_run_command(value) is None


@pytest.mark.parametrize("data, disabled", [
    (bytes([2] + [0] * 11), False), (bytes([6] + [0] * 11), False),
    (bytes([3] + [0] * 11), True), (bytes([7] + [0] * 11), True),
    (b"", False), (None, False), ("03", False),
])
def test_approved_disabled(data, disabled):
    assert sw.approved_disabled(data) is disabled


# ---------- install ----------

def test_install_writes_the_run_value_and_starts(tmp_path):
    w = _World(tmp_path)
    s = w.service()
    s.install()
    assert w.run_value == sw.run_command(w.root / ".venv" / "Scripts" / "pythonw.exe", w.root)
    [(argv, cwd)] = w.started
    assert argv == [str(w.root / ".venv" / "Scripts" / "pythonw.exe"), "-m", "skinpulse", "supervise",
                    "--root", str(w.root)]
    assert cwd == w.root
    assert any("已写入开机自启" in line for line in s.out)


def test_install_clears_the_disabled_flag_and_removes_the_old_shortcut(tmp_path):
    w = _World(tmp_path)
    w.reg.set_str(APPROVED_KEY, VALUE_NAME, bytes([3] + [0] * 11))
    lnk = w.old_lnk()
    s = w.service()
    s.install()
    assert w.reg.get(APPROVED_KEY, VALUE_NAME) is None
    assert not lnk.exists() and any("旧版快捷方式" in line for line in s.out)


def test_install_from_another_clone_repoints_and_says_so(tmp_path):
    w = _World(tmp_path)
    w.service().install()
    other = tmp_path / "other clone"
    _make_repo(other)
    s = w.service(other)
    s.install()
    assert sw.parse_run_command(w.run_value)[1] == other
    assert any("原来指向" in line and str(w.root) in line for line in s.out)


class _DenyingRegistry(DictRegistry):
    """安全软件或组策略拦住写 Run 键：winreg 抛 PermissionError。"""

    def set_str(self, key, name, value):
        raise PermissionError(5, "Access is denied")

    def delete(self, key, name):
        raise PermissionError(5, "Access is denied")


def test_install_explains_a_denied_registry_write(tmp_path):
    w = _World(tmp_path)
    w.reg = _DenyingRegistry()
    with pytest.raises(ServiceError, match="安全软件"):
        w.service().install()
    assert w.started == []


def test_uninstall_explains_a_denied_registry_write(tmp_path):
    w = _World(tmp_path)
    w.reg = _DenyingRegistry()
    w.reg.d[(RUN_KEY, VALUE_NAME)] = "x"
    with pytest.raises(ServiceError, match="安全软件"):
        w.service().uninstall()


def test_install_refuses_without_venv_or_settings(tmp_path):
    w = _World(tmp_path)
    (w.root / ".venv" / "Scripts" / "pythonw.exe").unlink()
    with pytest.raises(ServiceError, match="uv sync"):
        w.service().install()
    w2 = _World(tmp_path / "b")
    (w2.root / "settings.toml").unlink()
    with pytest.raises(ServiceError, match="init-config"):
        w2.service().install()
    assert w.reg.d == {} and w2.reg.d == {} and w.started == [] and w2.started == []


# ---------- start / stop / restart ----------

def test_start_is_a_noop_when_already_running(tmp_path):
    w = _World(tmp_path, sup=111)
    s = w.service()
    s.start()
    assert w.started == [] and "已经在跑" in s.out[0]


def test_start_clears_a_leftover_stop_file(tmp_path):
    w = _World(tmp_path)
    (w.data / STOP_FILE).write_text("stop", encoding="utf-8")
    w.service().start()
    assert not (w.data / STOP_FILE).exists() and len(w.started) == 1
    assert w.started[0][0][-2:] == ["--root", str(w.root)]


def test_stop_writes_stop_file_kills_run_and_waits(tmp_path):
    w = _World(tmp_path, sup=111, run=222)
    s = w.service()
    s.stop()
    assert w.killed == [222] and w.pids[LOCK_FILE] is None
    assert any("已停止" in line for line in s.out)


def test_stop_gives_up_after_waiting(tmp_path):
    w = _World(tmp_path, sup=111, run=222, sup_ignores_stop=True)
    with pytest.raises(ServiceError, match="任务管理器"):
        w.service().stop()


def test_stop_does_not_kill_a_manually_started_run(tmp_path):
    w = _World(tmp_path, run=222)
    with pytest.raises(ServiceError, match="手动起的"):
        w.service().stop()
    assert w.killed == []


def test_restart(tmp_path):
    w = _World(tmp_path, sup=111, run=222)
    s = w.service()
    s.restart()
    assert w.killed == [222] and len(w.started) == 1
    assert not any("旧版" in line for line in s.out)


def test_restart_with_the_old_shortcut_says_to_reinstall(tmp_path):
    """按 guide 升级（uv sync + restart）的人不会经过 install：提示他跑一次 install 换成 Run 键。"""
    w = _World(tmp_path, sup=111, run=222)
    lnk = w.old_lnk()
    s = w.service()
    s.restart()
    assert lnk.exists()                                     # restart 不动自启
    assert any("旧版" in line and "service install" in line for line in s.out)


# ---------- uninstall ----------

def test_uninstall_removes_run_value_flag_and_old_shortcut(tmp_path):
    w = _World(tmp_path, sup=111, run=222)
    w.service().install()
    w.reg.set_str(APPROVED_KEY, VALUE_NAME, bytes([3] + [0] * 11))
    lnk = w.old_lnk()
    s = w.service()
    s.uninstall()
    assert w.reg.d == {} and not lnk.exists() and w.killed == [222]
    text = "\n".join(s.out)
    assert "已删除开机自启" in text and "旧版快捷方式" in text
    assert "已停止" in text and "下次登录会自动启动" not in text      # 卸载时别说「下次登录会自动启动」


def test_uninstall_removes_autostart_even_when_stop_fails(tmp_path):
    """有手动起的 run 时 stop 会拒绝；自启项照样要删掉，再报停不了。"""
    w = _World(tmp_path, run=222)
    w.reg.set_str(RUN_KEY, VALUE_NAME, "x")
    lnk = w.old_lnk()
    s = w.service()
    with pytest.raises(ServiceError, match="手动起的"):
        s.uninstall()
    assert w.reg.d == {} and not lnk.exists() and w.killed == []
    assert any("已删除开机自启" in line for line in s.out)


def test_uninstall_when_nothing_is_installed(tmp_path):
    w = _World(tmp_path)
    s = w.service()
    s.uninstall()
    assert any("本来就没装" in line for line in s.out)


# ---------- status ----------

def test_status_not_installed_and_log_tail(tmp_path):
    w = _World(tmp_path, sup=111, run=None)
    (w.data / "logs").mkdir()
    (w.data / "logs" / LOG_FILE).write_text("\n".join(f"line {i}" for i in range(30)), encoding="utf-8")
    text = _status(w)
    assert "开机自启：没装" in text and "守护进程：在跑（pid 111）" in text and "服务：没在跑" in text
    assert "line 29" in text and "line 9" not in text


def test_status_installed_shows_the_command(tmp_path):
    w = _World(tmp_path)
    w.service().install()
    text = _status(w)
    assert "开机自启：已安装（注册表 Run 键）" in text and w.run_value in text


def test_status_disabled_in_startup_apps(tmp_path):
    w = _World(tmp_path)
    w.service().install()
    w.reg.set_str(APPROVED_KEY, VALUE_NAME, bytes([3] + [0] * 11))
    assert "被关掉了" in _status(w)
    w.reg.set_str(APPROVED_KEY, VALUE_NAME, bytes([2] + [0] * 11))
    assert "开机自启：已安装（注册表 Run 键）" in _status(w)


def test_status_points_at_another_directory(tmp_path):
    w = _World(tmp_path)
    other = tmp_path / "other clone"
    _make_repo(other)
    w.service(other).install()
    text = _status(w)
    assert "指向另一个目录" in text and str(other) in text and str(w.root) in text


@pytest.mark.parametrize("gone", ["pythonw", "settings"])
def test_status_target_is_gone(tmp_path, gone):
    w = _World(tmp_path)
    w.service().install()
    if gone == "pythonw":
        (w.root / ".venv" / "Scripts" / "pythonw.exe").unlink()
    else:
        (w.root / "settings.toml").unlink()
    text = _status(w)
    assert "不在了" in text and "重新 service install" in text


def test_status_unrecognised_value(tmp_path):
    w = _World(tmp_path)
    w.reg.set_str(RUN_KEY, VALUE_NAME, "notepad.exe")
    assert "认不出来" in _status(w)


def test_status_mentions_a_leftover_shortcut(tmp_path):
    w = _World(tmp_path)
    lnk = w.old_lnk()
    text = _status(w)
    assert "开机自启：没装" in text and "旧版快捷方式还在" in text and str(lnk) in text


# ---------- CLI ----------

@pytest.mark.skipif(sys.platform == "win32", reason="Windows 上真的会去装")
def test_cli_service_refuses_off_windows():
    from skinpulse.cli import app

    r = CliRunner().invoke(app, ["service", "status"])
    assert r.exit_code == 1 and "launchagent.sh" in r.output


def test_cli_service_needs_settings_in_the_current_dir(tmp_path, monkeypatch):
    """load_settings 会悄悄拷示例配置、建 data/：service 先确认自己在仓库根目录。"""
    from skinpulse.cli import app

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["service", "install"])
    assert r.exit_code == 1 and "init-config" in r.output
    assert not (tmp_path / "settings.toml").exists() and not (tmp_path / "data").exists()


# ---------- 只在 Windows 上 ----------

TEST_KEY = r"Software\SkinPulseTest\Run"


@windows_only
def test_real_registry_round_trip():
    import winreg

    reg = sw.WinRegistry()
    try:
        assert reg.get(TEST_KEY, VALUE_NAME) is None
        reg.set_str(TEST_KEY, VALUE_NAME, r'"C:\x y\pythonw.exe" -m skinpulse supervise --root "D:\\"')
        assert reg.get(TEST_KEY, VALUE_NAME) == r'"C:\x y\pythonw.exe" -m skinpulse supervise --root "D:\\"'
        reg.delete(TEST_KEY, VALUE_NAME)
        reg.delete(TEST_KEY, VALUE_NAME)                   # 不存在也不报错
        assert reg.get(TEST_KEY, VALUE_NAME) is None
    finally:
        for k in (TEST_KEY, r"Software\SkinPulseTest"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, k)
            except OSError:
                pass


@windows_only
@pytest.mark.parametrize("root", ["D:\\", r"C:\My Projects\Skin & Pulse", os.getcwd()])
def test_run_command_splits_back_the_windows_way(root):
    import ctypes
    from ctypes import wintypes

    shell32 = ctypes.WinDLL("shell32")
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    kernel32 = ctypes.WinDLL("kernel32")
    py = str(Path(root) / ".venv" / "Scripts" / "pythonw.exe")
    n = ctypes.c_int()
    argv = shell32.CommandLineToArgvW(sw.run_command(Path(py), Path(root)), ctypes.byref(n))
    try:
        assert [argv[i] for i in range(n.value)] == [py, "-m", "skinpulse", "supervise", "--root", root]
    finally:
        kernel32.LocalFree(argv)
