"""PostToolUse hook: run ruff's pyflakes and syntax checks on an edited .py file.

Only F (pyflakes: undefined names, unused imports, ...) and E9 (syntax errors) - the
full ruff rule set still has pre-existing findings (see docs/known-issues.md), and reporting
those on every edit would be noise. The F/E9 baseline is zero, so anything reported
here was introduced by the edit.

Exit 2 shows stderr to Claude so it can fix the file; the edit itself already happened.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def ruff_cmd() -> list[str] | None:
    for exe in (ROOT / ".venv" / "Scripts" / "ruff.exe", ROOT / ".venv" / "bin" / "ruff"):
        if exe.exists():
            return [str(exe)]
    if shutil.which("uv"):
        return ["uv", "run", "--no-sync", "ruff"]
    return None


def main() -> int:
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not path.endswith(".py") or not Path(path).exists():
        return 0
    cmd = ruff_cmd()
    if cmd is None:
        return 0
    proc = subprocess.run(
        [*cmd, "check", "--select", "F,E9", "--output-format", "concise", "--no-cache", path],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if proc.returncode == 0:
        return 0
    sys.stderr.reconfigure(encoding="utf-8")
    print(f"ruff（F/E9）在 {path} 里发现问题，请修掉：\n{proc.stdout.strip()}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
