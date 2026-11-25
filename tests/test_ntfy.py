import base64

import httpx
import pytest
import respx

from skinpulse.adapters.ntfy.client import NtfyClient, NtfyError, NtfyMessage


@respx.mock
def test_publish_sets_headers_and_body():
    route = respx.post("https://ntfy.sh/abc").mock(return_value=httpx.Response(200, json={"id": "x"}))
    c = NtfyClient("https://ntfy.sh", "abc", token="tk_1")
    c.publish(NtfyMessage(title="求购第1名", body="正文", priority=4, tags=["trophy"], click="https://x"))
    req = route.calls[0].request
    assert req.headers["Title"] == "=?UTF-8?B?" + base64.b64encode("求购第1名".encode()).decode() + "?="
    assert req.headers["Priority"] == "4" and req.headers["Tags"] == "trophy" and req.headers["Click"] == "https://x"
    assert req.headers["Authorization"] == "Bearer tk_1"
    assert req.content.decode() == "正文"


@respx.mock
def test_publish_raises_on_error():
    respx.post("https://ntfy.sh/abc").mock(return_value=httpx.Response(429))
    with pytest.raises(NtfyError):
        NtfyClient("https://ntfy.sh", "abc").publish(NtfyMessage("t", "b"))


@respx.mock
def test_publish_error_redacts_topic_from_exception_message():
    topic = "my-super-secret-topic-name"
    respx.post(f"https://ntfy.sh/{topic}").mock(
        return_value=httpx.Response(500, text=f"proxy error on path /{topic}/publish")
    )
    with pytest.raises(NtfyError) as exc_info:
        NtfyClient("https://ntfy.sh", topic).publish(NtfyMessage("t", "b"))
    message = str(exc_info.value)
    assert topic not in message
    assert "<topic>" in message
    assert "500" in message



# ---- 连接阶段失败的重试 ----
#
# 2026-09-22 夜里一条告警连续三次投递失败，全是 ConnectError。从国内连 ntfy.sh 平时单次请求就要
# 5 秒左右，一次抖动就丢整条告警不划算——但重试必须只限于「确定没送出去」的失败。


def _client(**kw):
    sleeps = []
    c = NtfyClient("https://ntfy.sh", "abc", sleep=sleeps.append, **kw)
    return c, sleeps


@respx.mock
def test_connect_error_is_retried_and_then_delivered():
    route = respx.post("https://ntfy.sh/abc").mock(side_effect=[
        httpx.ConnectError("boom"), httpx.ConnectError("boom"), httpx.Response(200, json={"id": "x"})])
    c, sleeps = _client()

    c.publish(NtfyMessage("t", "b"))

    assert route.call_count == 3
    assert sleeps == [3.0, 10.0]


@respx.mock
def test_connect_timeout_is_retried_too():
    route = respx.post("https://ntfy.sh/abc").mock(side_effect=[
        httpx.ConnectTimeout("slow"), httpx.Response(200, json={"id": "x"})])
    c, _ = _client()

    c.publish(NtfyMessage("t", "b"))

    assert route.call_count == 2


@respx.mock
def test_retries_give_up_and_raise_after_the_budget():
    route = respx.post("https://ntfy.sh/abc").mock(side_effect=httpx.ConnectError("down"))
    c, sleeps = _client(retries=2)

    with pytest.raises(httpx.ConnectError):
        c.publish(NtfyMessage("t", "b"))

    assert route.call_count == 3 and len(sleeps) == 2


@respx.mock
def test_read_timeout_is_not_retried_because_the_push_may_already_have_landed():
    """读超时时请求可能已经到了 ntfy、只是响应没回来。重试就可能把同一条告警推两遍——
    所以只重试连接阶段的失败，那种一定没送出去。"""
    route = respx.post("https://ntfy.sh/abc").mock(side_effect=httpx.ReadTimeout("no reply"))
    c, sleeps = _client()

    with pytest.raises(httpx.ReadTimeout):
        c.publish(NtfyMessage("t", "b"))

    assert route.call_count == 1 and sleeps == []


@respx.mock
def test_server_errors_are_not_retried():
    """5xx 时不知道服务端存没存下来，重试同样可能重复推送。"""
    route = respx.post("https://ntfy.sh/abc").mock(return_value=httpx.Response(502))
    c, sleeps = _client()

    with pytest.raises(NtfyError):
        c.publish(NtfyMessage("t", "b"))

    assert route.call_count == 1 and sleeps == []


@respx.mock
def test_publish_ignores_html_and_sends_only_body():
    """ntfy 没有 html 模板；带 html 的消息（给 PushPlus 用）到 ntfy 这边只发纯文本 body。"""
    route = respx.post("https://ntfy.sh/abc").mock(return_value=httpx.Response(200, json={"id": "x"}))
    NtfyClient("https://ntfy.sh", "abc").publish(NtfyMessage("t", "正文", html="<table></table>"))
    assert route.calls[0].request.content.decode() == "正文"


@respx.mock
def test_retry_log_never_contains_the_topic(caplog):
    """传输层异常的消息里带着完整 URL，而 URL 里就是 topic——在公共 ntfy 上它等于密码。"""
    import logging

    topic = "my-super-secret-topic-name"
    respx.post(f"https://ntfy.sh/{topic}").mock(side_effect=[
        httpx.ConnectError(f"failed to connect to https://ntfy.sh/{topic}"), httpx.Response(200)])
    c = NtfyClient("https://ntfy.sh", topic, sleep=lambda s: None)

    with caplog.at_level(logging.WARNING, logger="skinpulse.adapters.ntfy.client"):
        c.publish(NtfyMessage("t", "b"))

    assert "ConnectError" in caplog.text and topic not in caplog.text
