"""前端构建产物和源码一致（看板改版 spec §2.4）。

构建产物提交在仓库里，Mac 和 Windows 拉代码就能用、不用装 Node。代价是改了前端必须重新构建
并提交产物——忘了的话，这里会失败。算法必须和 frontend/scripts/source-hash.mjs 一模一样。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FRONTEND = REPO / "frontend"
DIST = REPO / "skinpulse" / "web" / "dist"
ROOT_FILES = re.compile(r"^(index\.html|package-lock\.json|vite\.config\.ts|tsconfig.*\.json)$")
REBUILD = "改了前端没重新构建：npm --prefix frontend ci && npm --prefix frontend run build，然后提交 skinpulse/web/dist/"


def _walk(d: Path):
    for p in sorted(d.iterdir()):
        if p.name.startswith("."):
            continue
        if p.is_dir():
            yield from _walk(p)
        elif p.is_file():
            yield p


def source_hash() -> str:
    files = (list(_walk(FRONTEND / "src")) + list(_walk(FRONTEND / "public"))
             + [p for p in FRONTEND.iterdir() if p.is_file() and ROOT_FILES.match(p.name)])
    h = hashlib.sha256()
    for rel in sorted(p.relative_to(FRONTEND).as_posix() for p in files):
        h.update(rel.encode("utf-8") + b"\0" + (FRONTEND / rel).read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


def test_dist_matches_frontend_sources():
    recorded = (DIST / ".source-hash").read_text(encoding="utf-8").strip()
    assert recorded == source_hash(), REBUILD


def test_index_references_only_existing_assets():
    html = (DIST / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="(/[^"]+)"', html)
    assert refs, "index.html 里没有引用任何资源"
    for ref in refs:
        assert (DIST / ref.lstrip("/")).is_file(), f"index.html 引用了不存在的 {ref}；{REBUILD}"


def test_windows_crlf_checkout_hashes_the_same(tmp_path, monkeypatch):
    """Windows 上 autocrlf=true 检出的源码是 CRLF，算出来的指纹必须和 LF 一样。"""
    import shutil
    import tests.test_frontend_build as m

    fake = tmp_path / "frontend"
    shutil.copytree(FRONTEND / "src", fake / "src")
    shutil.copytree(FRONTEND / "public", fake / "public")
    for p in FRONTEND.iterdir():
        if p.is_file() and ROOT_FILES.match(p.name):
            shutil.copy(p, fake / p.name)
    for p in fake.rglob("*"):
        if p.is_file() and p.suffix in {".ts", ".vue", ".html", ".json", ".svg"}:
            p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    monkeypatch.setattr(m, "FRONTEND", fake)
    assert m.source_hash() == (DIST / ".source-hash").read_text(encoding="utf-8").strip()


def test_no_inlined_data_uris_in_built_css():
    """前端页面的 CSP 是 font-src 'self'、img-src 'self' data:（app.py 的 SPA_CSP）。vite 默认把 4 KB 以下的
    资源内联成 data: URI——字体一内联就被 CSP 拦掉（2026-09-27 预览时撞到）。vite.config.ts 里关掉了内联。"""
    for css in (DIST / "assets").glob("*.css"):
        text = css.read_text(encoding="utf-8")
        assert "url(data:font" not in text and "url(data:application/font" not in text, f"{css.name} 里有内联字体"


# ---- 首页的包（看板 spec §7 二期验收、§9：首页 JS ≤ 350 KB gzip，ECharts 按路由拆分）----

# ECharts 内部用的属性名前缀，压缩后也保留；首页的包里一出现就说明它被打进来了
ECHARTS_MARK = b"__ec_"
HOME_JS_BUDGET_GZIP = 350 * 1024
_STATIC_IMPORT = re.compile(rb"""(?:\bfrom\s*|\bimport\s*)["'](\./[^"']+\.js)["']""")


def _home_js() -> set[Path]:
    """打开首页会加载的 JS：index.html 引用的入口和预加载，加上总览页的包，再顺着静态 import 走一遍。
    动态 import("./…") 是按路由懒加载的，不算。"""
    html = (DIST / "index.html").read_text(encoding="utf-8")
    todo = [DIST / p.lstrip("/") for p in re.findall(r'(?:src|href)="(/assets/[^"]+\.js)"', html)]
    todo += list((DIST / "assets").glob("OverviewPage-*.js"))
    seen: set[Path] = set()
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        todo += [p.parent / m.decode() for m in _STATIC_IMPORT.findall(p.read_bytes())]
    return seen


def test_echarts_stays_out_of_the_home_page_bundles():
    home = _home_js()
    assert any(ECHARTS_MARK in p.read_bytes() for p in (DIST / "assets").glob("*.js")), \
        "构建产物里找不到 ECharts：特征串变了？更新 ECHARTS_MARK，别让这条检查变成空测"
    leaked = sorted(p.name for p in home if ECHARTS_MARK in p.read_bytes())
    assert not leaked, f"ECharts 被打进了首页会加载的包：{leaked}（只能从带图表的页面 import charts/EChart.vue）"


def test_pnl_page_does_not_pull_in_echarts():
    """盈亏页（盈亏 spec §7.3）的柱子是 HTML，不引入 ECharts。"""
    pages = list((DIST / "assets").glob("PnlPage-*.js"))
    assert pages, "构建产物里没有盈亏页"
    seen: set[Path] = set()
    todo = list(pages)
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        todo += [p.parent / m.decode() for m in _STATIC_IMPORT.findall(p.read_bytes())]
    assert not [p.name for p in seen if ECHARTS_MARK in p.read_bytes()]


def test_home_page_js_fits_the_budget():
    import gzip

    total = sum(len(gzip.compress(p.read_bytes(), 9)) for p in _home_js())
    assert total <= HOME_JS_BUDGET_GZIP, f"首页 JS gzip 后 {total / 1024:.0f} KB，超过 350 KB 预算"
