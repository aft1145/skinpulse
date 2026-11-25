"""`GET /api/status`：系统页——「服务健康吗？」（看板 spec §4.6、§5；三期）

只读数据库和内存，不请求任何外部服务。token、key、topic 一个都不返回：通道是否配置只看
「有没有」，不看内容。
"""
from __future__ import annotations

import json
import logging
import os
import platform
import signal
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import desc, func, or_, select

from skinpulse.core.names import split_item_name
from skinpulse.core.rank import AGREED, INCONCLUSIVE, verification_outcome
from skinpulse.db.models import Account, MyBuyOrder, NotificationLog, OrderEvent, RankCheck, SystemStatus
from skinpulse.db.session import utcnow
from skinpulse.web.brief_page import brief_payload
from skinpulse.web.overview import channel_keys, iso_z, meta_payload
from skinpulse.codeversion import code_fingerprint
from skinpulse.depcheck import deps_outdated
from skinpulse.web.value import status_valuation

log = logging.getLogger(__name__)

CHANNEL_NAMES = {
    "youpin_anon": "悠悠 · 匿名通道",
    "youpin_account": "悠悠 · 账号通道",
    "pushplus": "PushPlus 微信",
    "ntfy": "ntfy 兜底",
}
# 标题只说最要紧的一件事，按这个顺序挑（token 失效时什么都查不了，排最前）
# code_outdated：工作区的代码改了、服务还没重启（skinpulse.codeversion）。排在「监控没在跑」之后：
# 监控照样在跑，只是新功能没生效
ISSUE_ORDER = ("not_logged_in", "token_invalid", "paused", "stale", "code_outdated", "pushplus_failing", "errors", "sync_slow",
               "buff_paused")
RECENT_NOTIFICATIONS = 10


def verification_payload(s, visible_limit: int) -> dict:
    """抽样核对（Monitor.run_round 的 pass B）近 24 小时的摘要：核对了几次、几次一致、几次无法判定，
    以及最近一次真正的不一致（不受 24 小时限制，免得少见的不一致一过期就看不到了）。

    「无法判定」不是不一致：公开队列一页最多 `visible_limit` 行，排在页外的单在本地根本找不到
    （core.rank.verification_outcome）。这些单独计数、也不算「最近一次不一致」，否则看板会把我们
    自己的盲区当成悠悠排名有问题的证据。"""
    cutoff = utcnow() - timedelta(hours=24)
    recent = list(s.scalars(select(RankCheck).where(RankCheck.ts >= cutoff)))
    outcomes = [verification_outcome(r.reported_rank, r.computed_rank, r.sample_size, visible_limit)
                for r in recent]
    # verification_outcome 里 INCONCLUSIVE 那条的 SQL 版，保持一次走索引的查询
    last_dis = s.scalar(select(RankCheck).where(RankCheck.agreed.is_(False))
                        .where(or_(RankCheck.computed_rank.is_not(None),
                                   RankCheck.sample_size < visible_limit))
                        .order_by(desc(RankCheck.ts)).limit(1))
    return {
        "checked_24h": len(recent),
        "agreed_24h": sum(1 for o in outcomes if o == AGREED),
        "inconclusive_24h": sum(1 for o in outcomes if o == INCONCLUSIVE),
        "last_disagreement": None if last_dis is None else {
            "purchase_no": last_dis.purchase_no,
            "reported_rank": last_dis.reported_rank,
            "computed_rank": last_dis.computed_rank,
            "ts": iso_z(last_dis.ts),
        },
    }


def _failing(row: SystemStatus | None) -> bool:
    """最近一次出错比最近一次成功还晚 = 现在是坏的。"""
    return (row is not None and row.last_error_at is not None
            and (row.last_ok_at is None or row.last_error_at > row.last_ok_at))


def _channel(adapter: str, row: SystemStatus | None, configured: bool, now: datetime, name: str | None = None) -> dict:
    if not configured or row is None:
        state = "idle"
    elif row.paused_until is not None and row.paused_until > now:
        state = "warn"
    elif _failing(row):
        state = "error"
    else:
        state = "ok"
    return {
        "id": adapter, "name": name or CHANNEL_NAMES[adapter], "configured": configured, "state": state,
        "last_ok_at": iso_z(row.last_ok_at) if row else None,
        "last_error_code": row.last_error_code if row else None,
        "last_error_at": iso_z(row.last_error_at) if row else None,
        "paused_until": iso_z(row.paused_until) if row and row.paused_until and row.paused_until > now else None,
    }


# 成交推送 spec §6：两类新事件没有名次，前端用图标徽章代替名次徽章
TRADE_EVENTS = {"IN_TRADE": "交易中", "FILLED": "求购成交", "MARKET": "接近成交价"}


def _event_label(event: str, rank: int | None) -> str:
    if event in TRADE_EVENTS:
        return TRADE_EVENTS[event]
    if rank == 1:
        return "升到第 1 名"
    return f"进入前 {rank}" if rank else event


def _notification(n: NotificationLog, names: dict[str, str], fills: dict[str, list[OrderEvent]] | None = None) -> dict:
    full = names.get(n.fingerprint)
    if full:
        nm = split_item_name(full)
        title, star, stattrak, tag = nm.title, nm.star, nm.stattrak, nm.tag
    else:
        # 单子已经不在库里了（撤掉很久）：用推送时的标题
        try:
            title = json.loads(n.payload_json or "{}").get("title") or n.fingerprint
        except ValueError:
            title = n.fingerprint
        star = stattrak = False
        tag = None
    filled_at = None
    if n.event == "FILLED":
        # 这条推送对应的成交：同一张单、在推送之前发现的最近一次
        before = [ev for ev in (fills or {}).get(n.fingerprint, []) if ev.at <= n.created_at]
        if before:
            ev = max(before, key=lambda e: e.at)
            filled_at = iso_z(ev.outcome_at or ev.at)
    return {"id": n.id, "rank": n.rank, "title": title, "star": star, "stattrak": stattrak, "tag": tag,
            "kind": n.event if n.event in TRADE_EVENTS else "RANK", "filled_at": filled_at,
            "event": _event_label(n.event, n.rank), "status": n.status, "at": iso_z(n.sent_at or n.created_at)}


def _runtime(rt, db_file: str | None) -> dict:
    # 数据库路径以正在用的引擎为准（WAL 模式下还有一个 -wal 文件，一起算）
    db = Path(db_file) if db_file else rt.settings.data_path / "skinpulse.db"
    size = sum(p.stat().st_size for p in (db, db.with_name(db.name + "-wal")) if p.exists())
    if sys.platform == "darwin":
        plat = f"macOS {platform.mac_ver()[0]}".strip()
    else:
        plat = f"{platform.system()} {platform.release()}"
    return {
        "started_at": iso_z(getattr(rt, "started_at", None)),
        # scripts/macos/skinpulse-launchd.sh 导出这个变量：由 LaunchAgent 拉起（开机自启、崩溃重启、接电源防睡眠）
        "supervisor": os.environ.get("SKINPULSE_SUPERVISOR") or None,
        "platform": plat,
        "db_bytes": size,
        "db_path": "data/skinpulse.db",
        "code_outdated": _code_outdated(rt),
        # 系统页的「重启服务」按钮（intent 2026-11-23）：只有 LaunchAgent 拉起的服务才能自己重启
        "can_restart": _supervised(),
        "deps_outdated": deps_outdated(),
    }


class RestartRefused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


RESTART_DELAY_S = 0.5


SUPERVISORS = ("launchd", "windows")      # launchd：scripts/macos；windows：skinpulse supervise（ADR-0021）


def _supervised() -> bool:
    return os.environ.get("SKINPULSE_SUPERVISOR") in SUPERVISORS


def _exit_for_restart() -> None:
    """Windows：刷完日志直接以 RESTART_EXIT 退出，守护进程看到这个码 2 秒后重拉。

    不发信号：Windows 上 SIGTERM 就是 TerminateProcess，日志都来不及刷。和 SIGTERM 那条路一样
    不等正在跑的一轮，在途通知由下次启动的 recover_pending 收尾。"""
    from skinpulse.supervise import RESTART_EXIT
    logging.shutdown()
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.flush()
    os._exit(RESTART_EXIT)


def request_restart(rt, *, pick_busy: bool, kill=None) -> dict:
    """系统页「重启服务」（intent 2026-11-23-restart-button）：先回应，再给自己发 SIGTERM，走 `run` 的正常退出
    流程；LaunchAgent 的 KeepAlive 会把服务拉起来。Windows 上改成以 RESTART_EXIT 退出，由守护进程重拉。只重启，不拉代码、不装依赖。

    手动 `run` 起来的服务没人拉起，拒绝；选品正在跑会被标成中断，拒绝；依赖要先 `uv sync` 的，拒绝
    （重启后会 ImportError 起不来）。`kill` 是测试替身。"""
    if not _supervised():
        how = "skinpulse service install" if sys.platform == "win32" else "LaunchAgent"
        raise RestartRefused(409, f"服务不是由开机自启（{how}）启动的，重启后无法自动恢复；请在终端中重启")
    if pick_busy:
        raise RestartRefused(409, "选品正在跑，重启会把它中断；等它跑完再重启")
    if deps_outdated():
        raise RestartRefused(409, "依赖有变化，要先在终端跑 uv sync，再重启")
    if kill is None:
        def kill():
            if os.environ.get("SKINPULSE_SUPERVISOR") == "windows":
                threading.Timer(RESTART_DELAY_S, _exit_for_restart).start()
            else:
                threading.Timer(RESTART_DELAY_S, os.kill, (os.getpid(), signal.SIGTERM)).start()
    log.warning("restart requested from the dashboard")
    kill()
    return {"restarting": True}


def _code_outdated(rt) -> bool:
    started = getattr(rt, "code_fingerprint", None)
    return started is not None and code_fingerprint() != started


def status_payload(rt, *, now: datetime | None = None) -> dict:
    now = now or utcnow()
    meta = meta_payload(rt, now=now)
    names_configured = set(getattr(rt.ntfy, "names", []) or [])
    token_set = bool(getattr(rt.client, "token", None))
    keys = channel_keys(rt)
    accs = getattr(rt, "accounts", None) or {}
    configured = {"youpin_anon": True, "youpin_account": token_set,
                  "pushplus": "pushplus" in names_configured, "ntfy": "ntfy" in names_configured}
    labels: dict[str, str] = {}
    for cid, _ in keys:
        if cid.startswith("youpin_account:"):
            a = accs.get(int(cid.split(":")[1]))
            configured[cid] = bool(a is not None and getattr(a.client, "token", None))
            labels[cid] = f"悠悠 · 账号通道（{a.label if a is not None else cid}）"

    with rt.session_factory() as s:
        db_file = s.get_bind().url.database
        rows = {cid: s.get(SystemStatus, key) for cid, key in keys}
        acc = s.get(Account, 1)
        verification = verification_payload(s, rt.settings.polling.visible_limit)
        totals = dict(s.execute(select(NotificationLog.status, func.count()).group_by(NotificationLog.status)).all())
        recent = list(s.scalars(select(NotificationLog).where(NotificationLog.status != "pending")
                                .order_by(desc(NotificationLog.created_at)).limit(RECENT_NOTIFICATIONS)))
        fps = {n.fingerprint for n in recent}
        names = dict(s.execute(select(MyBuyOrder.purchase_no, MyBuyOrder.commodity_name)
                               .where(MyBuyOrder.purchase_no.in_(fps))).all()) if fps else {}
        fill_fps = {n.fingerprint for n in recent if n.event == "FILLED"}
        fills: dict[str, list[OrderEvent]] = {}
        if fill_fps:
            for ev in s.scalars(select(OrderEvent).where(
                    OrderEvent.purchase_no.in_(fill_fps),
                    or_(OrderEvent.kind == "filled", OrderEvent.outcome == "done"))):
                fills.setdefault(ev.purchase_no, []).append(ev)

    channels = [_channel(cid, rows[cid], configured[cid], now, labels.get(cid)) for cid, _ in keys]
    issues = set(meta["service"]["issues"])
    push = rows["pushplus"]
    if configured["pushplus"] and _failing(push):
        issues.add("pushplus_failing")
    if _code_outdated(rt):
        issues.add("code_outdated")
    issue = next((i for i in ISSUE_ORDER if i in issues), None)

    return {
        "now": iso_z(now),
        "headline": {"issue": issue, "paused_until": meta["service"]["paused_until"],
                     "pushplus_error_at": iso_z(push.last_error_at) if push is not None and _failing(push) else None},
        "service": meta["service"],
        "channels": channels,
        "account": {
            "nickname": acc.nickname if acc else None, "user_id": acc.user_id if acc else None,
            "token_valid": bool(acc and acc.token_valid),
            "expires_at": meta["token"]["expires_at"], "days_left": meta["token"]["days_left"],
            "last_sync_at": iso_z(acc.last_sync_at) if acc else None,
        },
        # 每个号一张卡片（多账号 spec §8）：和 /api/meta 的 accounts 同一份
        "accounts": meta["accounts"],
        "verification": verification,
        "notifications": {
            "totals": {k: totals.get(k, 0) for k in ("sent", "failed", "suppressed")},
            "recent": [_notification(n, names, fills) for n in recent],
        },
        "briefs": [{"date": r["date"], "kind": r["kind"], "label": r["label"]} for r in brief_payload(rt, limit=7, tables=False)["rows"]],
        "brief": {"enabled": rt.settings.brief.enabled, "send_at": rt.settings.brief.send_at,
                  "top_rank": rt.settings.brief.top_rank},
        "valuation": status_valuation(rt),
        "runtime": _runtime(rt, db_file),
    }
