from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from skinpulse.adapters.buff.client import BuffClient
from skinpulse.adapters.ntfy.client import NtfyClient
from skinpulse.adapters.pushplus.client import PushPlusClient
from skinpulse.adapters.steamdt.client import SteamDTClient
from skinpulse.adapters.youpin.client import YoupinClient
from skinpulse.config import Settings, load_settings
from skinpulse.db.session import make_engine, make_session_factory, upgrade_db
from sqlalchemy import select

from skinpulse.db.models import Account
from skinpulse.secrets import (
    KEY_NTFY_TOKEN, KEY_NTFY_TOPIC, KEY_PUSHPLUS, KEY_STEAMDT, SecretStore, buff_cookie_key, youpin_token_key,
)
from skinpulse.services.account import AccountRuntime
from skinpulse.services.buff import BuffAccountRuntime
from skinpulse.services.market_data import MarketData
from skinpulse.services.monitor import Monitor
from skinpulse.services.notifier import FanoutNotifier

log = logging.getLogger(__name__)


@dataclass
class Runtime:
    settings: Settings
    settings_path: Path | None
    secrets: SecretStore
    session_factory: object
    client: YoupinClient
    ntfy: NtfyClient | None
    monitor: Monitor
    # 看板用的 SteamDT 行情读取层（带缓存，见 services/market_data.py）。没配 SteamDT key 时为 None，
    # 详情页/大盘页会如实说明，而不是报错。
    market: MarketData | None = None
    # 每日价格快照用（scheduler.job_price_snapshot）。没配 SteamDT key 时为 None，快照任务跳过。
    steamdt: SteamDTClient | None = None
    # 最近一轮监控的摘要（scheduler.job_monitor_round 写，/api/meta 读）。只在内存里，重启后为 None。
    last_round: dict | None = None
    # 最近一轮 BUFF 监控的摘要（scheduler.job_buff_round 写）
    last_buff_round: dict | None = None
    # 服务（`run`）启动的时刻，系统页显示「已连续运行多久」。一次性命令不设。
    started_at: datetime | None = None
    # 服务启动时代码的指纹（skinpulse.codeversion）。和当前文件不一样 = 代码改了还没重启。一次性命令不设。
    code_fingerprint: str | None = None
    # `skinpulse demo`：全是假数据，看板的写接口一律拒绝（web/app.py）。
    demo: bool = False
    # 悠悠请求的互斥：每轮监控整轮持有，充值前检查暂停单只在请求悠悠的两步持有（暂停单 spec §3.4）。两边用同一个
    # client，不能同时发请求；检查查 SteamDT 的那几分钟放开，监控照常跑。
    youpin_lock: threading.Lock = field(default_factory=threading.Lock)
    # BUFF 的 client 同一时刻只给一个任务用：BUFF 一轮整轮持有，00:45 的盈亏任务在请求 BUFF 的那段持有
    buff_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def accounts(self) -> dict[int, AccountRuntime]:
        """启用的悠悠号（多账号 spec §4）。和 Monitor 共用同一份：看板加号、停用时 `load_accounts` 整个换掉。"""
        accs = getattr(self.monitor, "_accounts", None)
        if accs is not None:
            return accs
        return {1: AccountRuntime(1, self.client)} if self.client is not None else {}

    @property
    def buff_accounts(self) -> dict[int, BuffAccountRuntime]:
        """启用的 BUFF 号（BUFF spec §4）。不在 `accounts` 里：那份全是悠悠 client。"""
        return getattr(self.monitor, "buff_accounts", None) or {}


def build_ntfy(settings: Settings, secrets: SecretStore) -> NtfyClient | None:
    topic = secrets.get(KEY_NTFY_TOPIC)
    if not topic:
        return None
    return NtfyClient(settings.ntfy.base_url, topic, token=secrets.get(KEY_NTFY_TOKEN))


def build_notifier(settings: Settings, secrets: SecretStore) -> FanoutNotifier | None:
    """把配置了的推送通道组装成一个 FanoutNotifier。PushPlus 排在前面：它是主通道。

    ntfy 留作兜底——2026-09-23 查实它在用户手机上从没实时送达过（国内 FCM 不通、App 不常驻
    后台、ntfy.sh 只存 12 小时），见 services/notifier.py。
    """
    channels: list[tuple[str, object]] = []
    token = secrets.get(KEY_PUSHPLUS)
    if token:
        channels.append(("pushplus", PushPlusClient(token)))
    ntfy = build_ntfy(settings, secrets)
    if ntfy is not None:
        channels.append(("ntfy", ntfy))
    if not channels:
        return None
    return FanoutNotifier(channels)


def attach_channel(rt, name: str, client: object) -> None:
    """服务在跑的时候加上一条推送通道（看板里录入 PushPlus），不用重启。

    已经有推送器就原地换；原来一条都没有（`rt.ntfy is None`）就新建一个，同时交给 Monitor：
    它在启动时存了推送器的引用，scheduler、简报每次现读 `rt.ntfy`。"""
    first = name == "pushplus"           # PushPlus 是主通道，排在前面（和 build_notifier 一致）
    if rt.ntfy is not None:
        rt.ntfy.set_channel(name, client, first=first)
        return
    rt.ntfy = FanoutNotifier([(name, client)])
    if getattr(rt, "monitor", None) is not None:
        rt.monitor.ntfy = rt.ntfy


def warn_about_channels(notifier: FanoutNotifier | None) -> None:
    """推送通道配置不全时的提醒。**只在启动监控服务时调用**（见 cli.run）。

    它原本在 build_notifier 里，而每条命令都会构造 runtime——于是用户运行 `pushplus-token`
    去配置 PushPlus 的那一刻，屏幕上先跳出一句「PushPlus 没配置，请运行 pushplus-token」。
    """
    names = notifier.names if notifier else []
    if not names:
        log.warning("no notification channel configured (pushplus/ntfy); alerts will be logged only")
    elif "pushplus" not in names:
        log.warning("PushPlus not configured: alerts go to ntfy only, which does not reach the phone "
                    "in real time on this setup - run `pushplus-token`")


def _account_runtimes(settings: Settings, secrets: SecretStore, Session,
                      old: dict[int, AccountRuntime] | None = None) -> dict[int, AccountRuntime]:
    """库里启用的悠悠号 → AccountRuntime（多账号 spec §3、§4）。已经在跑的号沿用原来的对象（client 的
    uk、登录失效提醒的状态都留着），只更新 token 和简称。库里一个号都没有（新装）时就是号 1。

    device_id 按号持久化：第一次用到时生成、写进库，之后一直用它。"""
    old = old or {}
    with Session() as s:
        rows = list(s.scalars(select(Account).where(Account.platform == "youpin", Account.enabled.is_(True))
                              .order_by(Account.id)))
        if not rows and s.get(Account, 1) is None:
            rows = [Account(id=1)]
        out: dict[int, AccountRuntime] = {}
        for row in rows:
            if row.device_id is None and row.user_id is not None:
                row.device_id = str(uuid.uuid4())
            ar = old.get(row.id)
            if ar is None:
                client = YoupinClient(settings.youpin, device_id=row.device_id)
                ar = AccountRuntime(row.id, client)
            ar.label = row.display_name()
            ar.client.token = secrets.get(youpin_token_key(row.id))
            out[row.id] = ar
        if s.dirty:
            s.commit()
    return out


def _buff_runtimes(settings: Settings, secrets: SecretStore, Session,
                   old: dict[int, BuffAccountRuntime] | None = None) -> dict[int, BuffAccountRuntime]:
    """库里启用的 BUFF 号 → BuffAccountRuntime（BUFF spec §4）。在跑的号沿用原来的对象（提醒状态留着），
    只更新 cookie、简称和设置。device_id 第一次用到时生成、写库。"""
    old = old or {}
    with Session() as s:
        rows = list(s.scalars(select(Account).where(Account.platform == "buff", Account.enabled.is_(True))
                              .order_by(Account.id)))
        out: dict[int, BuffAccountRuntime] = {}
        for row in rows:
            if row.device_id is None:
                row.device_id = uuid.uuid4().hex.upper()
            ar = old.get(row.id)
            if ar is None:
                ar = BuffAccountRuntime(row.id, BuffClient(settings.buff, None, row.device_id))
            ar.label = row.label or "BUFF"
            ar.client.s = settings.buff
            ar.client.cookie = secrets.get(buff_cookie_key(row.id))
            out[row.id] = ar
        if s.dirty:
            s.commit()
    return out


def load_buff_accounts(rt) -> None:
    """看板加 BUFF 号、换 cookie、停用、启用、改了 [buff] 设置之后调：整个换掉 Monitor 手里那份。"""
    rt.monitor.buff_accounts = _buff_runtimes(rt.settings, rt.secrets, rt.session_factory, rt.monitor.buff_accounts)


def load_accounts(rt) -> None:
    """看板加号、改简称、停用、启用之后调：重读启用的号，整个换掉 Monitor 手里那份（赋值是原子的，
    正在跑的一轮用的是它开始时拿到的那份）。匿名通道跟着第一个启用的号走。"""
    accounts = _account_runtimes(rt.settings, rt.secrets, rt.session_factory, rt.monitor._accounts)
    first = next(iter(accounts.values()), None)
    if first is not None:
        rt.client = first.client
        rt.monitor.client = first.client
    rt.monitor._accounts = accounts


def build_runtime(settings_path: Path | None = None) -> Runtime:
    settings = load_settings(settings_path)
    data = settings.data_path
    upgrade_db(data / "skinpulse.db")
    Session = make_session_factory(make_engine(data / "skinpulse.db"))
    # 注意这里**不**做 pending 通知的清扫。它曾经在这个位置，而 cli._rt 每条命令都会调
    # build_runtime，于是后台服务跑着的时候，随手一条 `sync`/`dump` 都会把服务当时正在发送
    # 的通知标成 failed、并把告警状态重置成 NORMAL。2026-09-21 真撞上过一次：服务写下 12 行
    # pending 的同一秒，另一个进程跑了条只读命令，那 12 行立刻被判死；那次靠服务自己重走一遍
    # 补上了，但时序稍微错开就会变成告警被静默吞掉，或者同一条推两遍。
    # 清扫现在归调度器启动路径（scheduler.build_scheduler），也只有它该做——recover_pending
    # 自己的 docstring 写的就是这个前提：只在第一次 run_round 之前调一次。
    secrets = SecretStore(data / ".env")
    accounts = _account_runtimes(settings, secrets, Session)
    # 匿名通道用第一个启用的号的 client（多账号 spec §3）；一个号时和以前完全一样
    client = next(iter(accounts.values())).client if accounts else YoupinClient(settings.youpin)
    ntfy = build_notifier(settings, secrets)
    # SteamDT 是可选依赖：没配 key 就传 None，告警照发，只是少「别处买盘」那一行。
    steamdt_key = secrets.get(KEY_STEAMDT)
    steamdt = SteamDTClient(steamdt_key, base_url=settings.steamdt.base_url) if steamdt_key else None
    if steamdt is None:
        log.info("SteamDT api key not configured; alerts will not carry cross-platform bids")
    buff_accounts = _buff_runtimes(settings, secrets, Session)
    monitor = Monitor(Session, client, ntfy, settings, steamdt=steamdt, accounts=accounts, buff_accounts=buff_accounts)
    # 看板和监控共用同一个 SteamDT 客户端：两边吃的是同一份配额，分开建客户端不会多出额度。
    market = MarketData(steamdt) if steamdt is not None else None
    return Runtime(settings, settings_path, secrets, Session, client, ntfy, monitor, market, steamdt)
