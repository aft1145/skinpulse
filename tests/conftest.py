import json
import os
from pathlib import Path

# typer 在 GitHub Actions 上（有 GITHUB_ACTIONS 环境变量）会强制彩色输出，ANSI 码把「--date」这类字样拆开，
# 断言输出内容的 CLI 测试就挂了（2026-11-24 私有仓库 CI 第一次跑时发现）。这个变量在 typer 导入时读，所以放最上面。
os.environ.setdefault("_TYPER_FORCE_DISABLE_TERMINAL", "1")

import pytest

from skinpulse.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"

_PROXY_VARS = ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy")


@pytest.fixture(autouse=True)
def _no_proxy_env(monkeypatch):
    """测试不受 shell 代理变量影响：开着 Clash 时 `.zshrc` 会设 `ALL_PROXY=socks5://…`，测试里直接
    `httpx.Client()` 构造的客户端（默认读环境）就要 socksio，直接 ImportError（2026-11-20 一次挂了 22 个）。
    生产客户端本来就不读代理（ADR-0011，test_http_trust_env 单独用 proxy_env 验证）。"""
    for name in _PROXY_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings()
    s.data_dir = str(tmp_path / "data")
    # Neutralize the night-window round gate (see Monitor.run_round / core.planner._is_night)
    # by default: night_start_hour == night_end_hour means no hour counts as night (see
    # test_planner.test_is_night_start_equals_end_means_never_night), so tests that call
    # run_round() more than once aren't flaky depending on the wall-clock local hour the
    # suite happens to run at. Tests that specifically exercise night gating override these
    # on top of this fixture.
    s.polling.night_start_hour = 0
    s.polling.night_end_hour = 0
    # 市场价刷新（Monitor._refresh_sell_prices）默认关掉：它会给每轮多打几次在售接口，
    # 绝大多数测试跟它无关，开着只会让调用计数和断言无端变复杂。需要它的测试自己打开。
    s.polling.sell_sample_size = 0
    # 告警前的实时核对（Monitor._confirm_with_live_queue）默认关掉：它会在 pass A 之前多打
    # 几次公开队列，改变调用计数，而且会用实时排名/榜首价覆盖"悠悠上报值"——绝大多数告警
    # 测试恰恰是拿上报值构造场景的。要它的测试自己打开。
    s.alerts.confirm_with_live_queue = False
    return s


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def db_session(tmp_path: Path):
    from skinpulse.db.session import make_engine, make_session_factory, upgrade_db

    db = tmp_path / "test.db"
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))
    with Session() as s:
        yield s


import base64

import httpx
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

PC = "https://pc-api.youpin898.com"
APP = "https://api.youpin898.com"
FIXED_AES_KEY = b"0123456789abcdef"


def device_w2_responder(uk: str = "U" * 65):
    """服务端用我们发过去的 AES key 加密回包；测试里把 key 固定成 FIXED_AES_KEY
    （配合 monkeypatch random_aes_key），这样才能构造出可解密的假响应。"""

    def handler(request: httpx.Request) -> httpx.Response:
        aes = AES.new(FIXED_AES_KEY, AES.MODE_ECB)
        payload = json.dumps({"u": uk, "deviceUk": "D" * 65}).encode()
        return httpx.Response(200, content=base64.b64encode(aes.encrypt(pad(payload, 16))))

    return handler


@pytest.fixture
def youpin_client(monkeypatch):
    """匿名通道客户端：AES key 固定、时钟可推进（c.tick(seconds)）、不真正 sleep。"""
    from skinpulse.adapters.youpin import crypto as yc
    from skinpulse.adapters.youpin.client import YoupinClient
    from skinpulse.config import YoupinSettings

    monkeypatch.setattr(yc, "random_aes_key", lambda: FIXED_AES_KEY)
    t = {"now": 1000.0}
    c = YoupinClient(YoupinSettings(), clock=lambda: t["now"], sleep=lambda s: None, device_id="dev-1")
    c.tick = lambda seconds: t.__setitem__("now", t["now"] + seconds)
    return c
