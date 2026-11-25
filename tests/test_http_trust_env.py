"""四个 HTTP 客户端默认不读环境里的代理变量（ADR-0011）。

2026-09-24 在 Mac 上接手时，`~/.zshrc` 在 Clash 运行时自动设 `ALL_PROXY=socks5://…`，
httpx 默认 `trust_env=True`，读到 socks5 就要 `socksio`，每条命令构造客户端时直接 ImportError。
launchd 拉起的进程又不读 `.zshrc`——两种启动方式网络行为不一致。所以一律直连。
"""
from __future__ import annotations

import httpx
import pytest

from skinpulse.adapters.buff.client import BuffClient
from skinpulse.adapters.ntfy.client import NtfyClient
from skinpulse.adapters.pushplus.client import PushPlusClient
from skinpulse.adapters.steamdt.client import SteamDTClient
from skinpulse.adapters.youpin.client import YoupinClient
from skinpulse.config import BuffSettings, YoupinSettings

FACTORIES = {
    "youpin": lambda **kw: YoupinClient(YoupinSettings(), **kw),
    "steamdt": lambda **kw: SteamDTClient("k", **kw),
    "pushplus": lambda **kw: PushPlusClient("tok", **kw),
    "ntfy": lambda **kw: NtfyClient("https://ntfy.sh", "topic", **kw),
    "buff": lambda **kw: BuffClient(BuffSettings(), "c", "d", **kw),
}


@pytest.fixture
def proxy_env(monkeypatch):
    for name in ("ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "socks5://127.0.0.1:7890")
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:7890")


@pytest.mark.parametrize("name", sorted(FACTORIES))
def test_default_client_ignores_proxy_env(name, proxy_env):
    client = FACTORIES[name]()
    try:
        assert client.http.trust_env is False
    finally:
        client.http.close()


@pytest.mark.parametrize("name", sorted(FACTORIES))
def test_injected_http_is_used_as_is(name):
    injected = httpx.Client()
    try:
        assert FACTORIES[name](http=injected).http is injected
    finally:
        injected.close()
