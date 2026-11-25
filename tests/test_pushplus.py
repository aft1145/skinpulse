"""PushPlus 适配器与多通道推送。错误信封的形状来自 2026-09-23 的实测响应。"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from skinpulse.adapters.ntfy.client import NtfyMessage
from skinpulse.adapters.pushplus.client import (
    BASE_URL, PATH_SEND, PushPlusAuthError, PushPlusClient, PushPlusError, PushPlusNeedsRealName,
    PushPlusRestricted,
)
from skinpulse.services.notifier import AllChannelsFailed, FanoutNotifier


def _pp(**kw):
    return PushPlusClient("tok-123", sleep=lambda s: None, **kw)


@respx.mock
def test_publish_sends_title_body_as_plain_text_and_returns_short_code():
    """txt 模板：饰品名里有 | ★ ™，万一出现 < 或 & 会被 html 模板吃掉。"""
    route = respx.post(BASE_URL + PATH_SEND).mock(return_value=httpx.Response(
        200, json={"code": 200, "msg": "请求成功", "data": "3cbc5eab"}))

    assert _pp().publish(NtfyMessage("标题", "第一行\n第二行")) == "3cbc5eab"

    sent = json.loads(route.calls[0].request.content)
    assert sent == {"token": "tok-123", "title": "标题", "content": "第一行\n第二行", "template": "txt"}


@respx.mock
def test_publish_with_html_uses_html_template():
    """simple 简报表格用 html 模板；调用方已经 html.escape 过内容。"""
    route = respx.post(BASE_URL + PATH_SEND).mock(return_value=httpx.Response(
        200, json={"code": 200, "msg": "请求成功", "data": "3cbc5eab"}))

    msg = NtfyMessage("标题", "纯文本兜底", html="<table><tr><td>x</td></tr></table>")
    assert _pp().publish(msg) == "3cbc5eab"

    sent = json.loads(route.calls[0].request.content)
    assert sent == {"token": "tok-123", "title": "标题",
                     "content": "<table><tr><td>x</td></tr></table>", "template": "html"}


@pytest.mark.parametrize("code,data,exc", [
    (903, "无效的用户token", PushPlusAuthError),     # 实测：乱填 token 的真实响应
    (905, "未实名", PushPlusNeedsRealName),
    (900, "用户账号使用受限", PushPlusRestricted),
    (999, "token不能为空", PushPlusError),          # 实测：不带 token 的真实响应
])
@respx.mock
def test_http_200_with_an_error_code_is_still_an_error(code, data, exc):
    """PushPlus 永远回 HTTP 200，成败只看 code——这个项目第三次遇到这种服务了。"""
    respx.post(BASE_URL + PATH_SEND).mock(return_value=httpx.Response(
        200, json={"code": code, "msg": "x", "data": data}))

    with pytest.raises(exc) as e:
        _pp().publish(NtfyMessage("t", "b"))
    assert data in str(e.value)       # 细节在 data 里，不在 msg 里


@respx.mock
def test_restricted_account_is_never_retried():
    """900 时官方要求停止请求，继续发只会延长限制。"""
    route = respx.post(BASE_URL + PATH_SEND).mock(return_value=httpx.Response(
        200, json={"code": 900, "msg": "受限", "data": ""}))
    with pytest.raises(PushPlusRestricted):
        _pp().publish(NtfyMessage("t", "b"))
    assert route.call_count == 1


@respx.mock
def test_connect_error_is_retried():
    route = respx.post(BASE_URL + PATH_SEND).mock(side_effect=[
        httpx.ConnectError("x"), httpx.Response(200, json={"code": 200, "msg": "ok", "data": "sc"})])
    assert _pp().publish(NtfyMessage("t", "b")) == "sc"
    assert route.call_count == 2


@respx.mock
def test_read_timeout_is_not_retried():
    """读超时时消息可能已经落地，重试会推两遍。"""
    route = respx.post(BASE_URL + PATH_SEND).mock(side_effect=httpx.ReadTimeout("x"))
    with pytest.raises(httpx.ReadTimeout):
        _pp().publish(NtfyMessage("t", "b"))
    assert route.call_count == 1


# ---- 多通道 ----


class _Ok:
    def __init__(self):
        self.sent = []

    def publish(self, msg):
        self.sent.append(msg)


class _Fail:
    def __init__(self, exc):
        self.exc = exc

    def publish(self, msg):
        raise self.exc


def test_one_channel_succeeding_is_enough():
    """只要有一条通道收下就算发出去了——主通道 PushPlus 挂了、ntfy 兜住，不该判成失败。"""
    ok = _Ok()
    results = FanoutNotifier([("pushplus", _Fail(PushPlusError("500", "x"))), ("ntfy", ok)]).publish(
        NtfyMessage("t", "b"))

    assert [(r.name, r.ok) for r in results] == [("pushplus", False), ("ntfy", True)]
    assert len(ok.sent) == 1          # 前一条失败没有拦住后一条


def test_every_channel_failing_raises_with_per_channel_detail():
    with pytest.raises(AllChannelsFailed) as e:
        FanoutNotifier([("pushplus", _Fail(PushPlusError("903", "x"))),
                        ("ntfy", _Fail(httpx.ConnectError("x")))]).publish(NtfyMessage("t", "b"))
    assert [r.name for r in e.value.results] == ["pushplus", "ntfy"]


def test_channel_failure_log_never_leaks_the_ntfy_topic(caplog):
    """httpx 传输层异常的消息里带着完整 URL，ntfy 的 URL 里就是 topic。"""
    import logging

    topic = "my-super-secret-topic"
    with caplog.at_level(logging.WARNING, logger="skinpulse.services.notifier"):
        FanoutNotifier([("ntfy", _Fail(httpx.ConnectError(f"https://ntfy.sh/{topic}"))),
                        ("pushplus", _Ok())]).publish(NtfyMessage("t", "b"))
    assert "ConnectError" in caplog.text and topic not in caplog.text
