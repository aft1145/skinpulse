"""运行中的服务和仓库工作区里的代码是不是同一份（系统页「需要重启」）。

服务跑的是工作区的代码（CLAUDE.md 坑 10），但 Python 只在启动时加载一次：合并、切分支、拉新代码以后，
不重启的话跑的还是旧的。2026-10-02 合并的成交推送就因为没重启白等了三天。所以启动时记下一份指纹，
系统页每次拿当前文件的指纹比一比。

只算 `skinpulse/`、`alembic/` 下的 .py：前端 dist 重新构建后看板立刻生效，不需要重启。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

PKG = Path(__file__).resolve().parent
DEFAULT_ROOTS = (PKG, PKG.parent / "alembic")
DEFAULT_EXCLUDE = (PKG / "web" / "dist",)

# 文件的 (路径, mtime, 大小) 没变就不重新读内容：系统页每分钟刷新一次
_cache: dict = {}


def _sources(roots, exclude) -> list[Path]:
    out = []
    for root in roots:
        for p in Path(root).rglob("*.py"):
            if any(p.is_relative_to(x) for x in exclude) or "__pycache__" in p.parts:
                continue
            out.append(p)
    return sorted(out)


def code_fingerprint(roots=DEFAULT_ROOTS, exclude=DEFAULT_EXCLUDE) -> str:
    files = _sources(roots, exclude)
    stats = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    key = (tuple(map(str, roots)), stats)
    if _cache.get("key") == key:
        return _cache["fp"]
    h = hashlib.sha256()
    for p in files:
        h.update(str(p).encode())
        h.update(b"\0")
        h.update(p.read_bytes())
    fp = h.hexdigest()
    _cache.update(key=key, fp=fp)
    return fp
