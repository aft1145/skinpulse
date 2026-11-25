"""演示模式：`skinpulse demo` 在临时目录里建一个全是假数据的库，起一个只读看板。

不联网、不读系统钥匙串（凭据只看临时目录里空的 .env），写接口一律拒绝。"""
from __future__ import annotations

import threading
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from skinpulse.config import Settings, save_settings
from skinpulse.db.models import SystemStatus
from skinpulse.db.session import make_engine, make_session_factory, upgrade_db, utcnow
from skinpulse.runtime import Runtime
from skinpulse.secrets import SecretStore
from skinpulse.services.monitor import ROUND, Monitor


MARKER = ".skinpulse-demo"          # demo 建的目录才有；没有它的库一律不碰


def demo_settings(data_dir: Path, port: int) -> Settings:
    s = Settings()
    s.data_dir = str(data_dir)
    s.web.port = port
    return s


def build_demo_runtime(data_dir: Path, port: int, *, now: datetime) -> Runtime:
    from skinpulse.demo.market import DemoMarket
    from skinpulse.demo.seed import CATALOG, seed

    data_dir.mkdir(parents=True, exist_ok=True)
    marker = data_dir / MARKER
    if (data_dir / "skinpulse.db").exists() and not marker.exists():
        raise ValueError(f"{data_dir} 里已经有一个不是 demo 建的 skinpulse.db，不碰它：换个目录，或者不加 --dir")
    marker.write_text("skinpulse demo\n", encoding="utf-8")
    settings = demo_settings(data_dir, port)
    settings_path = data_dir / "settings.toml"
    save_settings(settings, settings_path)
    db = data_dir / "skinpulse.db"
    fresh = not db.exists()
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))
    market = DemoMarket({mhn: Decimal(p) for mhn, _, _, p in CATALOG}, tz=settings.timezone, now=now)
    if fresh:
        seed(Session, settings, market, now)
    secrets = SecretStore(data_dir / ".env", use_keyring=False)
    # 演示两个号（多账号 spec §9）：只为看板显示，不请求任何东西，所以没有 client
    from sqlalchemy import select

    from skinpulse.db.models import Account
    from skinpulse.services.account import AccountRuntime
    from skinpulse.services.buff import BuffAccountRuntime
    with Session() as s:
        rows = list(s.scalars(select(Account).where(Account.enabled.is_(True)).order_by(Account.id)))
        accounts = {a.id: AccountRuntime(a.id, None, label=a.display_name()) for a in rows if a.platform != "buff"}
        buff_accounts = {a.id: BuffAccountRuntime(a.id, None, label=a.label or "BUFF") for a in rows if a.platform == "buff"}
    monitor = Monitor(Session, None, None, settings, sleep=lambda _: None, accounts=accounts or None,
                      buff_accounts=buff_accounts)
    rt = Runtime(settings, settings_path, secrets, Session, None, None, monitor, market, None, demo=True)
    rt.started_at = now
    mark_round(rt)
    return rt


def mark_round(rt: Runtime) -> None:
    """假装刚跑完一轮，顶栏和系统页才不会报「监控停了」。"""
    at = utcnow()
    with rt.session_factory() as s:
        row = s.get(SystemStatus, ROUND) or SystemStatus(adapter=ROUND)
        row.last_ok_at, row.updated_at = at, at
        s.merge(row)
        s.commit()
    rt.last_round = {"at": at, "checked": 23, "alerts_sent": 0, "errors": 0, "paused": False,
                     "gap_gated": 1, "market_gated": 2, "notify_failed": 0}


def keep_fresh(rt: Runtime, every_s: float = 60) -> threading.Event:
    """后台每分钟 mark_round 一次；返回的 Event set 之后停。"""
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(every_s):
            mark_round(rt)

    threading.Thread(target=loop, name="demo-fresh", daemon=True).start()
    return stop
