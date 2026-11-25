"""FastAPI 提供前端单页应用（看板改版 spec §2.3）。用临时 dist 目录测，不依赖真实构建产物。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_web import _client


def _dist(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text('<!doctype html><div id="app"></div><script src="/assets/app-1a2b.js"></script>',
                                  encoding="utf-8")
    (d / "assets" / "app-1a2b.js").write_text("console.log(1)", encoding="utf-8")
    (d / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    return d


def _spa_client(tmp_path, settings, dist):
    from skinpulse.web.app import create_app
    _, rt = _client(tmp_path, settings)
    return TestClient(create_app(rt, dist_dir=dist), base_url=f"http://127.0.0.1:{settings.web.port}")


def test_root_serves_index_with_csp_and_no_cache(tmp_path, settings):
    c = _spa_client(tmp_path, settings, _dist(tmp_path))
    r = c.get("/")
    assert r.status_code == 200 and 'id="app"' in r.text
    assert r.headers["cache-control"] == "no-cache"
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp


def test_client_side_routes_fall_back_to_index(tmp_path, settings):
    c = _spa_client(tmp_path, settings, _dist(tmp_path))
    r = c.get("/analysis")
    assert r.status_code == 200 and 'id="app"' in r.text


def test_hashed_assets_are_served_with_long_cache(tmp_path, settings):
    c = _spa_client(tmp_path, settings, _dist(tmp_path))
    r = c.get("/assets/app-1a2b.js")
    assert r.status_code == 200 and r.text == "console.log(1)"
    assert "immutable" in r.headers["cache-control"]


def test_root_level_files_in_dist_are_served(tmp_path, settings):
    c = _spa_client(tmp_path, settings, _dist(tmp_path))
    assert c.get("/favicon.svg").text == "<svg/>"


def test_paths_outside_dist_are_never_served(tmp_path, settings):
    c = _spa_client(tmp_path, settings, _dist(tmp_path))
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"):
        assert "nope" not in c.get(path).text


def test_unknown_api_paths_are_json_404(tmp_path, settings):
    c = _spa_client(tmp_path, settings, _dist(tmp_path))
    r = c.get("/api/nope")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


def test_missing_build_explains_instead_of_500(tmp_path, settings):
    c = _spa_client(tmp_path, settings, tmp_path / "no-dist")
    r = c.get("/")
    assert r.status_code == 503
    assert "npm --prefix frontend run build" in r.text


def test_old_system_and_settings_addresses_redirect_to_the_system_page(tmp_path, settings):
    """三期：旧的 /status、/settings 并进了前端的 /system，旧书签跳过去。"""
    c = _spa_client(tmp_path, settings, _dist(tmp_path))
    for path in ("/status", "/settings"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/system"


def test_every_page_is_the_frontend_now(tmp_path, settings):
    c = _spa_client(tmp_path, settings, _dist(tmp_path))
    for path in ("/brief", "/system", "/item/1414", "/market", "/analysis"):
        r = c.get(path)
        assert r.status_code == 200 and 'id="app"' in r.text, path


def test_csp_is_on_every_response_not_just_the_index(tmp_path, settings):
    c = _spa_client(tmp_path, settings, _dist(tmp_path))
    for path in ("/", "/api/meta", "/api/nope"):
        csp = c.get(path).headers["content-security-policy"]
        assert "default-src 'self'" in csp and "script-src 'self';" in csp


def test_no_server_rendered_templates_are_left():
    """三期验收：全局搜不到 TemplateResponse，模板和 HTMX 都删了。"""
    from pathlib import Path

    web = Path(__file__).resolve().parents[1] / "skinpulse" / "web"
    assert not (web / "templates").exists() and not (web / "static").exists()
    for f in web.rglob("*.py"):
        assert "TemplateResponse" not in f.read_text(encoding="utf-8"), f
