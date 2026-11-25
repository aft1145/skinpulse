"""scripts/macos 的 LaunchAgent 脚本（spec §11.1）。真 launchctl 不碰：换成 PATH 里的假命令，
只核对「调了什么、按什么顺序」；plist 用真 plutil 校验。"""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts" / "macos"
AGENT = SCRIPTS / "launchagent.sh"
WRAPPER = SCRIPTS / "skinpulse-launchd.sh"

darwin_only = pytest.mark.skipif(sys.platform != "darwin", reason="plutil / launchd 只在 macOS 上有")


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None,
                    reason="没有 bash；Windows 上的 bash 是 WSL 的占位程序，这些脚本只给 macOS 用")
@pytest.mark.parametrize("script", [AGENT, WRAPPER], ids=lambda p: p.name)
def test_script_parses_and_is_executable(script):
    subprocess.run(["bash", "-n", str(script)], check=True)
    assert os.access(script, os.X_OK)
    assert b"\r\n" not in script.read_bytes()  # CRLF 会让 shebang 失效（.gitattributes 管着）


def _fake_repo(tmp_path: Path) -> Path:
    """仓库路径里带空格和 XML 特殊字符（作者的仓库目录名就带空格）。"""
    root = tmp_path / "Skin & <Pulse>"
    shutil.copytree(SCRIPTS, root / "scripts" / "macos")
    return root


def _fake_launchctl(tmp_path: Path, *, loaded: bool) -> tuple[Path, Path]:
    """有状态的假 launchctl：print 看状态文件在不在，bootstrap 建、bootout 删；每次调用记一行。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls, state = tmp_path / "launchctl.calls", tmp_path / "launchctl.loaded"
    if loaded:
        state.touch()
    (bin_dir / "launchctl").write_text(f"""#!/bin/bash
echo "$*" >> "{calls}"
case "$1" in
  print) [ -f "{state}" ] || exit 113 ;;
  bootstrap) touch "{state}" ;;
  bootout) rm -f "{state}" ;;
esac
exit 0
""", encoding="utf-8")
    (bin_dir / "launchctl").chmod(0o755)
    return bin_dir, calls


def _run_agent(root: Path, tmp_path: Path, bin_dir: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(tmp_path / "home"), "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return subprocess.run(["bash", str(root / "scripts" / "macos" / "launchagent.sh"), *args],
                          env=env, capture_output=True, text=True, timeout=60)


@darwin_only
def test_render_writes_a_valid_plist_for_a_path_with_spaces_and_xml_chars(tmp_path):
    root = _fake_repo(tmp_path)
    out = tmp_path / "out.plist"
    subprocess.run(["bash", str(root / "scripts" / "macos" / "launchagent.sh"), "render", str(out)], check=True)
    subprocess.run(["plutil", "-lint", str(out)], check=True, capture_output=True)
    p = plistlib.loads(out.read_bytes())
    real_root = root.resolve()
    assert p["Label"] == "local.skinpulse"
    assert p["ProgramArguments"][0] == "/bin/bash"
    assert Path(p["ProgramArguments"][1]) == real_root / "scripts" / "macos" / "skinpulse-launchd.sh"
    assert Path(p["WorkingDirectory"]) == real_root
    assert p["RunAtLoad"] is True and p["KeepAlive"] is True
    assert p["ThrottleInterval"] == 30
    assert Path(p["StandardOutPath"]) == Path(p["StandardErrorPath"]) == real_root / "data" / "logs" / "launchd.log"


@darwin_only
def test_install_refuses_without_settings_and_never_touches_launchctl(tmp_path):
    root = _fake_repo(tmp_path)
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / ".venv" / "bin" / "python").chmod(0o755)
    bin_dir, calls = _fake_launchctl(tmp_path, loaded=False)
    r = _run_agent(root, tmp_path, bin_dir, "install")
    assert r.returncode != 0
    assert "settings.toml" in r.stderr
    assert not calls.exists()


def _ready_repo(tmp_path: Path) -> Path:
    root = _fake_repo(tmp_path)
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / ".venv" / "bin" / "python").chmod(0o755)
    (root / "settings.toml").write_text("", encoding="utf-8")
    return root


@darwin_only
def test_install_writes_plist_makes_log_dir_and_bootstraps(tmp_path):
    root = _ready_repo(tmp_path)
    bin_dir, calls = _fake_launchctl(tmp_path, loaded=False)
    r = _run_agent(root, tmp_path, bin_dir, "install")
    assert r.returncode == 0, r.stderr
    plist = tmp_path / "home" / "Library" / "LaunchAgents" / "local.skinpulse.plist"
    assert plistlib.loads(plist.read_bytes())["Label"] == "local.skinpulse"
    assert (root / "data" / "logs").is_dir()  # launchd 自己不建日志目录，缺了就起不来
    uid = os.getuid()
    lines = calls.read_text(encoding="utf-8").splitlines()
    assert lines[0] == f"enable gui/{uid}/local.skinpulse"
    assert lines[-1] == f"bootstrap gui/{uid} {plist}"
    assert not any(line.startswith("bootout") for line in lines)


@darwin_only
def test_reinstall_boots_out_the_old_job_first(tmp_path):
    root = _ready_repo(tmp_path)
    bin_dir, calls = _fake_launchctl(tmp_path, loaded=True)
    r = _run_agent(root, tmp_path, bin_dir, "install")
    assert r.returncode == 0, r.stderr
    verbs = [line.split()[0] for line in calls.read_text(encoding="utf-8").splitlines()]
    assert verbs.index("bootout") < verbs.index("bootstrap")


@darwin_only
def test_restart_and_stop_when_not_loaded(tmp_path):
    root = _ready_repo(tmp_path)
    bin_dir, calls = _fake_launchctl(tmp_path, loaded=False)
    r = _run_agent(root, tmp_path, bin_dir, "restart")
    assert r.returncode != 0 and "start" in r.stderr
    r = _run_agent(root, tmp_path, bin_dir, "stop")
    assert r.returncode == 0 and "没在运行" in r.stdout
    assert "kickstart" not in calls.read_text(encoding="utf-8")


@darwin_only
def test_wrapper_execs_the_venv_python_from_the_repo_root(tmp_path):
    """exec 替换：launchd 盯的 pid 必须就是服务进程，SIGTERM 才送得到它、caffeinate -w 才盯得对。"""
    root = _fake_repo(tmp_path)
    (root / ".venv" / "bin").mkdir(parents=True)
    fake_python = root / ".venv" / "bin" / "python"
    fake_python.write_text('#!/bin/bash\necho "cwd=$(pwd -P)"\necho "pid=$$"\necho "args=$*"\n', encoding="utf-8")
    fake_python.chmod(0o755)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    proc = subprocess.Popen(["/bin/bash", str(root / "scripts" / "macos" / "skinpulse-launchd.sh")],
                            cwd=elsewhere, stdout=subprocess.PIPE, text=True)
    out, _ = proc.communicate(timeout=30)
    assert proc.returncode == 0
    got = dict(line.split("=", 1) for line in out.splitlines())
    assert Path(got["cwd"]) == root.resolve()
    assert got["args"] == "-m skinpulse run --console-level WARNING"
    assert int(got["pid"]) == proc.pid


@darwin_only
def test_failed_render_leaves_no_half_written_plist_behind(tmp_path):
    """plist 写到一半失败（set -e 退出）时，LaunchAgents 里不能留下残缺的 plist：下次登录 launchd
    会照着它启动（缺 WorkingDirectory、KeepAlive……）。先写临时文件，写完整了再 mv 过去。"""
    root = _ready_repo(tmp_path)
    bin_dir, calls = _fake_launchctl(tmp_path, loaded=False)
    (bin_dir / "plutil").write_text(
        '#!/bin/bash\ncase "$*" in *ThrottleInterval*) exit 1 ;; esac\nexec /usr/bin/plutil "$@"\n', encoding="utf-8")
    (bin_dir / "plutil").chmod(0o755)
    r = _run_agent(root, tmp_path, bin_dir, "install")
    assert r.returncode != 0
    agents = tmp_path / "home" / "Library" / "LaunchAgents"
    assert list(agents.iterdir()) == []
    assert "bootstrap" not in (calls.read_text(encoding="utf-8") if calls.exists() else "")


@darwin_only
def test_status_stop_uninstall_print_paths_next_to_cjk_punctuation(tmp_path, monkeypatch):
    """macOS 自带的 bash 3.2 在 UTF-8 环境下，会把紧跟在 $VAR 后面的全角标点（「（」「，」）当成变量名
    的一部分：`$LOG（…` 在 set -u 下直接报 "LOG?: unbound variable"（2026-09-24 真机 status 撞到）。
    变量一律写成 ${VAR}。"""
    root = _ready_repo(tmp_path)
    (root / "data" / "logs").mkdir(parents=True)
    (root / "data" / "logs" / "launchd.log").write_text("last line\n", encoding="utf-8")
    bin_dir, calls = _fake_launchctl(tmp_path, loaded=True)
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    monkeypatch.setenv("LC_ALL", "zh_CN.UTF-8")
    r = _run_agent(root, tmp_path, bin_dir, "status")
    assert r.returncode == 0, r.stderr
    assert "last line" in r.stdout
    r = _run_agent(root, tmp_path, bin_dir, "stop")
    assert r.returncode == 0, r.stderr
    assert "已停止" in r.stdout
    r = _run_agent(root, tmp_path, bin_dir, "uninstall")
    assert r.returncode == 0, r.stderr


def test_scripts_never_put_a_bare_variable_right_before_non_ascii():
    import re

    for script in (AGENT, WRAPPER):
        text = script.read_text(encoding="utf-8")
        bad = [m.group(0) for m in re.finditer(r"\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7f]", text)]
        assert bad == [], (script.name, bad)
