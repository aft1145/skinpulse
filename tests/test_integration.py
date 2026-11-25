"""端到端集成测试：一次 Monitor.run_round 走完整条链路（pass A 的 sync_my_orders + pass B 的
抽样核对，见 services/monitor.py）。

respx 同时 mock 认证通道（deviceW2 走 App host；getUserInfo 走 App host、PC 请求头，见
client.PATH_USER_INFO_LEGACY 的说明——旧的 getUserInfoForApp 在 sk 加密通道后面，已经不再
调用；searchPurchaseOrderList 走 PC 渠道，pass A 用它刷新 reported_rank）与匿名通道
（getTemplatePurchaseOrderListPC，pass B 用它抽样核对），验证的是各层之间真实的接线
（wiring），而不是重复各层自己的单测：sync 出三张求购单、账号 userId 解析、pass A 直接从
reported_rank 触发状态机、pass B 抽样记录 RankCheck、ntfy 消息，全部串起来跑一遍。
"""

import httpx
import respx
from sqlalchemy import select

from skinpulse.adapters.youpin.client import YoupinClient
from skinpulse.config import YoupinSettings
from skinpulse.db.models import Account, RankCheck
from skinpulse.db.session import make_engine, make_session_factory, upgrade_db
from skinpulse.services.monitor import Monitor
from tests.conftest import APP, PC, device_w2_responder, load_fixture


class SpyNtfy:
    def __init__(self):
        self.sent = []

    def publish(self, msg):
        self.sent.append(msg)


@respx.mock
def test_end_to_end_enter_top2(tmp_path, settings, monkeypatch):
    from skinpulse.adapters.youpin import crypto as yc

    monkeypatch.setattr(yc, "random_aes_key", lambda: b"0123456789abcdef")
    settings.alerts.confirm_rounds = 1  # 单轮即可触发；默认是 2 轮确认
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    respx.get(f"{APP}/api/user/Account/getUserInfo").mock(
        return_value=httpx.Response(200, json={"Code": 0, "Data": {"UserId": 99999999, "NickName": "me"}}))
    sync_route = respx.post(f"{PC}/api/youpin/bff/trade/purchase/order/searchPurchaseOrderList").mock(
        return_value=httpx.Response(200, json=load_fixture("youpin/search_purchase_order_list.json")))
    verify_route = respx.post(f"{PC}/api/youpin/bff/trade/purchase/order/getTemplatePurchaseOrderListPC").mock(
        return_value=httpx.Response(200, json=load_fixture("youpin/template_purchase_orders_ak_redline.json")))

    db = tmp_path / "e2e.db"
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))
    client = YoupinClient(YoupinSettings(), sleep=lambda s: None)
    client.token = "tok"

    ntfy = SpyNtfy()
    rep = Monitor(Session, client, ntfy, settings, sleep=lambda s: None).run_round()

    assert sync_route.call_count == 1  # pass A: one authenticated call refreshes every order
    assert verify_route.call_count == 3  # pass B: all 3 orders sampled (within verify_sample_size)
    assert rep.checked == 3 and rep.alerts_sent == 1
    # TEST-MINE-1's YouPin-reported rank (2, from the my-orders fixture) drives the alert
    # directly now (pass A), not a locally-computed rank; TEST-MINE-2 (rank 15) and
    # TEST-MINE-3 (rank 22) don't cross the enter_rank=2 threshold.
    with Session() as s:
        assert s.get(Account, 1).user_id == 99999999
        checks = list(s.scalars(select(RankCheck)))
        assert len(checks) == 3  # pass B recorded a cross-check for every sampled order
    assert len(ntfy.sent) == 1
    assert "AK-47 | 红线" in ntfy.sent[0].title
    assert ntfy.sent[0].priority == 3
