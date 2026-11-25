"""装好的依赖和仓库里的 `uv.lock` / `pyproject.toml` 对不对得上（系统页「重启服务」按钮，intent 2026-11-23）。

拉新代码后要先 `uv sync` 再重启（CLAUDE.md「运维」）。看板上的重启按钮只重启，不装依赖：依赖有变化时
不该给这个按钮，否则重启后的服务会 ImportError 起不来。判断两件事：

1. `pyproject.toml` 里的直接依赖都装了；
2. `uv.lock` 里**已经装了的**包，版本和锁文件一致（没装的多半是别的平台、别的依赖组才要的，不管）。

结果按两个文件的 (mtime, 大小) 缓存：系统页每分钟刷新一次。
"""
from __future__ import annotations

import re
import tomllib
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_cache: dict = {}
_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _installed() -> dict[str, str]:
    out = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if name:
            out[_norm(name)] = dist.version
    return out


def deps_outdated(root: Path = ROOT, installed: dict[str, str] | None = None) -> bool:
    """依赖要不要先 `uv sync`。文件缺了、读不了时不拦（返回 False）：这只是个提示，不能把按钮弄没了。"""
    lock, proj = root / "uv.lock", root / "pyproject.toml"
    try:
        key = tuple((p.stat().st_mtime_ns, p.stat().st_size) for p in (lock, proj))
    except OSError:
        return False
    if installed is None and _cache.get("key") == (str(root), key):
        return _cache["value"]
    try:
        locked = tomllib.loads(lock.read_text(encoding="utf-8")).get("package", [])
        meta = tomllib.loads(proj.read_text(encoding="utf-8")).get("project", {})
    except (OSError, ValueError):
        return False
    direct, project = meta.get("dependencies", []), _norm(meta.get("name", ""))
    have = _installed() if installed is None else installed
    value = False
    for spec in direct:
        m = _NAME.match(spec)
        if m and _norm(m.group(1)) not in have:
            value = True
    for pkg in locked:
        name, version = _norm(pkg.get("name", "")), pkg.get("version")
        if name == project or not version:
            continue
        if name in have and have[name] != version:
            value = True
    if installed is None:
        _cache.update(key=(str(root), key), value=value)
    return value
