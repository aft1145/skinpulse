"""多通道推送：同一条告警同时发给 PushPlus（微信）和 ntfy。

**只要有一条通道收下，就算发出去了**；全部失败才算失败。

保留 ntfy 不删，是因为多一条通道没有坏处——但要清楚它现在的真实地位：2026-09-23 查实 ntfy
在用户手机上从来没有实时送达过（国内 FCM 不通、App 不常驻后台，而 ntfy.sh 只存 12 小时），
所以它只是兜底，主通道是 PushPlus。每条通道单独记健康状态，某一条挂了在 /status 上看得见，
不会被另一条的成功掩盖。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from skinpulse.adapters.ntfy.client import NtfyError
from skinpulse.adapters.pushplus.client import PushPlusError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChannelResult:
    name: str
    error: Exception | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class AllChannelsFailed(Exception):
    def __init__(self, results: list[ChannelResult]) -> None:
        super().__init__("all notification channels failed: " + ", ".join(
            f"{r.name}={_describe(r.error)}" for r in results))
        self.results = results


def _describe(e: Exception | None) -> str:
    """异常的可记录描述。

    自家的两种错误（NtfyError / PushPlusError）消息里已经去掉了敏感信息，可以原样记；其余
    一律只记类型名——httpx 的传输层异常消息里带着完整 URL，而 ntfy 的 URL 里就是 topic，
    在公共 ntfy 服务器上 topic 等于密码。
    """
    if e is None:
        return "ok"
    if isinstance(e, (NtfyError, PushPlusError)):
        return str(e)
    return type(e).__name__


class FanoutNotifier:
    def __init__(self, channels: list[tuple[str, object]]) -> None:
        if not channels:
            raise ValueError("FanoutNotifier needs at least one channel")
        self.channels = channels

    @property
    def names(self) -> list[str]:
        return [n for n, _ in self.channels]

    def set_channel(self, name: str, channel: object, *, first: bool = False) -> None:
        """换掉（或加上）一条通道。新建一个 list 再赋值，不在原 list 上改：
        另一个线程里正在跑的 publish 还在遍历旧 list，不会被打乱。"""
        rest = [(n, c) for n, c in self.channels if n != name]
        self.channels = [(name, channel), *rest] if first else [*rest, (name, channel)]

    def publish(self, msg) -> list[ChannelResult]:
        results: list[ChannelResult] = []
        for name, ch in self.channels:
            try:
                ch.publish(msg)
                results.append(ChannelResult(name))
            except Exception as e:  # noqa: BLE001 - 一条通道挂了不能拖累另一条
                log.warning("notification channel %s failed: %s", name, _describe(e))
                results.append(ChannelResult(name, e))
        if not any(r.ok for r in results):
            raise AllChannelsFailed(results)
        return results
