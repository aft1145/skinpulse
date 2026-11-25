"""系统页「需要重启」：运行中的代码和工作区里的代码是不是同一份（skinpulse.codeversion）。"""
from pathlib import Path

from skinpulse.codeversion import code_fingerprint


def _tree(root: Path) -> Path:
    pkg = root / "pkg"
    (pkg / "web" / "dist").mkdir(parents=True)
    (pkg / "a.py").write_text("x = 1\n", encoding="utf-8")
    (pkg / "web" / "b.py").write_text("y = 2\n", encoding="utf-8")
    (pkg / "web" / "dist" / "c.py").write_text("ignored\n", encoding="utf-8")
    return pkg


def test_fingerprint_changes_with_python_sources_only(tmp_path):
    pkg = _tree(tmp_path)
    fp = code_fingerprint([pkg], exclude=[pkg / "web" / "dist"])
    assert fp == code_fingerprint([pkg], exclude=[pkg / "web" / "dist"])
    (pkg / "web" / "dist" / "c.py").write_text("still ignored\n", encoding="utf-8")   # 前端产物立刻生效，不算
    (pkg / "notes.md").write_text("not code\n", encoding="utf-8")
    assert code_fingerprint([pkg], exclude=[pkg / "web" / "dist"]) == fp
    (pkg / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert code_fingerprint([pkg], exclude=[pkg / "web" / "dist"]) != fp


def test_new_module_changes_the_fingerprint(tmp_path):
    pkg = _tree(tmp_path)
    fp = code_fingerprint([pkg], exclude=[pkg / "web" / "dist"])
    (pkg / "new.py").write_text("", encoding="utf-8")
    assert code_fingerprint([pkg], exclude=[pkg / "web" / "dist"]) != fp


def test_default_roots_cover_the_package():
    assert len(code_fingerprint()) == 64
