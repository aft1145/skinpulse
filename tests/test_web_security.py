"""看板的跨站防护（看板改版 spec §6）。

看板只监听 127.0.0.1，但浏览器里打开的任何网页都能向它发请求：普通表单 POST 不需要预检，
DNS 重绑定还能让恶意域名解析到 127.0.0.1。所以写接口一律：Host 必须是本机、Origin（有的话）
必须同源、Content-Type 必须是 JSON。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_web import FakeYoupinClient, _client_with_youpin


def _c(tmp_path, settings):
    c, rt, app = _client_with_youpin(tmp_path, settings, FakeYoupinClient(n_orders=2))
    return c, rt, app


def test_same_origin_json_post_passes(tmp_path, settings):
    c, _, _ = _c(tmp_path, settings)
    origin = f"http://127.0.0.1:{settings.web.port}"
    r = c.post("/api/youpin/sync", json={}, headers={"Origin": origin})
    assert r.status_code == 200
    assert r.json() == {"synced": 2}


def test_json_post_without_origin_passes(tmp_path, settings):
    """curl 之类的非浏览器客户端不带 Origin；浏览器发起的跨站请求一定带。"""
    c, _, _ = _c(tmp_path, settings)
    assert c.post("/api/youpin/sync", json={}).status_code == 200


def test_cross_site_origin_is_refused(tmp_path, settings):
    c, rt, _ = _c(tmp_path, settings)
    r = c.post("/api/youpin/sync", json={}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert rt.client.posts == []


def test_form_encoded_post_is_refused(tmp_path, settings):
    """跨站表单提交不需要预检，是 CSRF 最常见的形状。"""
    c, _, _ = _c(tmp_path, settings)
    r = c.post("/api/settings", data={"polling": "x"})
    assert r.status_code == 403


def test_post_without_body_or_content_type_is_refused(tmp_path, settings):
    c, _, _ = _c(tmp_path, settings)
    assert c.post("/api/youpin/sync").status_code == 403


def test_foreign_host_is_refused_even_for_get(tmp_path, settings):
    """DNS 重绑定：evil.example 解析到 127.0.0.1 后，请求的 Host 仍是 evil.example。"""
    _, _, app = _c(tmp_path, settings)
    evil = TestClient(app, base_url="http://evil.example:8765")
    assert evil.get("/api/overview").status_code == 403
    assert evil.post("/api/youpin/sync", json={}).status_code == 403


def test_localhost_and_ipv6_loopback_hosts_are_allowed(tmp_path, settings):
    c, _, app = _c(tmp_path, settings)
    port = settings.web.port
    assert TestClient(app, base_url=f"http://localhost:{port}").get("/api/overview").status_code == 200
    # TestClient 解析不了 IPv6 的 base_url，直接写 Host 头
    assert c.get("/api/overview", headers={"host": f"[::1]:{port}"}).status_code == 200


def test_security_headers_on_every_response(tmp_path, settings):
    c, _, _ = _c(tmp_path, settings)
    r = c.get("/api/overview")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"


def test_sms_login_endpoints_are_gone(tmp_path, settings):
    """短信登录走不通（research §2），连接口带表单一起删了；CLI 的 login 命令还在。"""
    c, _, _ = _c(tmp_path, settings)
    for path in ("/api/youpin/login/send", "/api/youpin/login/verify"):
        assert c.post(path, json={"phone": "13800001111"}).status_code in (404, 405)
