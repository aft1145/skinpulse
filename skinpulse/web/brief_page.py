"""`GET /api/brief`：每天的简报判定（spec §7a.6；看板 spec §4.5）。只读 brief_log，不请求任何外部服务。

每一行给出当天发出去的那张表：
- 从现在起发的简报，表存在 `brief_log.rows_json` 里（发送那一刻的样子，撤单、改阈值都不影响历史）；
- 更早的记录只有纯文本 body。现在的正文是一行一张单的固定格式，**严格**解析回表格；任何一行对不上
  （比如 09-22 那条旧版日报）就整条退回原文，不猜。
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import desc, select

from skinpulse.analysis.brief import FAILED, NO_CHANNEL, NO_DATA, QUIET_SENT, SENT, SKIPPED
from skinpulse.core.localday import last_due_brief, local_today
from skinpulse.core.messages import short_item_name
from skinpulse.db.models import BriefLog
from skinpulse.db.session import utcnow
from skinpulse.web.overview import iso_z

GAP_LABEL = "无记录"
GAP_SUMMARY = "{send_at} 服务未运行，当天无判定"
# 过了该发的时刻多久才发，就算「补发」（服务那晚没在跑，第二天启动时补的）
LATE_AFTER = timedelta(hours=1)


def decision_labels(top_rank: int) -> dict[str, str]:
    return {
        SENT: "已发送",
        QUIET_SENT: "已发兜底",
        SKIPPED: f"未发（前 {top_rank} 名无变动）",
        NO_DATA: "未发（当天无监控数据）",
        NO_CHANNEL: "未发（无推送通道）",
        FAILED: "推送失败",
    }


def _fmt(v: float | None) -> str:
    return "" if v is None else f"{v:.2f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------- 表格

def _split_short(name: str) -> tuple[str, bool, bool, str | None]:
    """推送里的简称（`CZ75(ST) | 短趾雕 (崭新)`）→ (标题, ★, StatTrak, 磨损)。

    存下来的表和从老正文解析的表都走这一条，页面上同一件饰品的写法才一致（和推送里一样的简称）。"""
    marks = _MARKS.search(name)
    mark = marks.group(1) if marks else ""
    name = _MARKS.sub("", name, count=1)
    tag = None
    if " | " in name and (t := _TAG.match(name)):
        name, tag = t.group(1), t.group(2)
    return name.strip(), "★" in mark, "ST" in mark, tag


def _row_from_stored(r: dict) -> dict:
    title, star, stattrak, tag = _split_short(short_item_name(r["name"]))
    me = r["rank"] == 1
    changes = []
    if r.get("is_new"):
        changes.append("新进")
    if r.get("rank_changed"):
        changes.append(f"名次 {r.get('open_rank')} → {r['rank']}")
    if r.get("top1_changed"):
        changes.append(f"榜首 {_fmt(r.get('open_top1'))} → {_fmt(r.get('top1_price'))}")
    return {"rank": r["rank"], "title": title, "star": star, "stattrak": stattrak, "tag": tag,
            "my_price": r["my_price"], "top1_price": None if me else r.get("top1_price"), "is_me": me,
            "sell_price": r.get("sell_price"), "changes": changes}


# 现在的正文一行一张单：第N名 | 简称（可能自带「 | 」） | 求购 x | 榜首 y|我 | 在售 z|— | 变动
_LINE = re.compile(r"^第(\d+)名 \| (.+?) \| 求购 ([\d.]+) \| 榜首 (我|[\d.]+) \| 在售 (—|[\d.]+)(?: \| (.+))?$")
_MARKS = re.compile(r"\((★ ST|★|ST)\)")
_TAG = re.compile(r"^(.*\S)\s*\(([^()]+)\)$")


def _parse_body(body: str | None) -> tuple[list[dict], str | None] | None:
    if not body:
        return None
    rows, note = [], None
    for line in body.splitlines():
        if line.startswith("在售："):
            note = line
            continue
        m = _LINE.match(line)
        if not m:
            return None
        rank, name, my, top, sell, change = m.groups()
        title, star, stattrak, tag = _split_short(name)
        changes = []
        for part in (change or "").split("，"):
            part = part.strip()
            if not part:
                continue
            changes.append("新进" if part.startswith("🆕") else part.replace("→", " → "))
        me = top == "我"
        rows.append({"rank": int(rank), "title": title, "star": star, "stattrak": stattrak,
                     "tag": tag, "my_price": float(my), "top1_price": None if me else float(top), "is_me": me,
                     "sell_price": None if sell == "—" else float(sell), "changes": changes})
    return (rows, note) if rows else None


# ---------------------------------------------------------------- 每一行

def _kind(r: BriefLog, send_hm: tuple[int, int], tz: ZoneInfo) -> str:
    if r.decision in (SENT, QUIET_SENT):
        due = datetime.combine(date.fromisoformat(r.date) + timedelta(days=1), time(*send_hm), tzinfo=tz)
        return "late" if r.sent_at is not None and r.sent_at - due > LATE_AFTER else "sent"
    return "failed" if r.decision == FAILED else "not_sent"


def _gap_row(d: date, send_at: str) -> dict:
    return {"date": d.isoformat(), "decision": None, "kind": "gap", "label": GAP_LABEL,
            "summary": GAP_SUMMARY.format(send_at=send_at), "delivery": None, "title": None,
            "sent_at": None, "table": None, "note": None, "body": None}


def _record_row(r: BriefLog, labels: dict[str, str], send_hm: tuple[int, int], tz: ZoneInfo,
                tables: bool = True) -> dict:
    reasons = json.loads(r.reasons_json or "{}")
    table = note = None
    # tables=False：只要日期和结果（系统页的 7 天格子），不解析表格
    if tables and r.rows_json:
        stored = json.loads(r.rows_json)
        table, note = [_row_from_stored(x) for x in stored.get("rows", [])], stored.get("sell_note")
    elif tables and (parsed := _parse_body(r.body)) is not None:
        table, note = parsed
    return {"date": r.date, "decision": r.decision, "kind": _kind(r, send_hm, tz),
            "label": labels.get(r.decision, r.decision), "summary": reasons.get("summary", ""),
            "delivery": reasons.get("delivery"), "title": r.title, "sent_at": iso_z(r.sent_at),
            "table": table, "note": note, "body": None if table is not None else r.body}


def brief_payload(rt, limit: int = 30, *, tables: bool = True) -> dict:
    st = rt.settings
    tz = ZoneInfo(st.timezone)
    top_rank = st.brief.top_rank
    labels = decision_labels(top_rank)
    send_hm = st.brief.hm("send_at")
    now = utcnow()
    with rt.session_factory() as s:
        rows = list(s.scalars(select(BriefLog).order_by(desc(BriefLog.date)).limit(limit)))
    if not st.brief.enabled or not rows:
        out = [_record_row(r, labels, send_hm, tz, tables) for r in rows]
    else:
        # 没有 brief_log 行的日子（send_at 时服务没在跑）不能直接消失，补占位行：从最近一份已到点、
        # 该有记录的那天（send_at 00:00 时就是本地昨天）往回走到列出的最旧一条——最早那条之前不补；
        # 连续停摆很久时，总行数仍以 limit 为限（页面只看最近 limit 天）。
        due_day, _ = last_due_brief(st.timezone, send_hm, now)
        cursor = max(due_day, date.fromisoformat(rows[0].date)) + timedelta(days=1)
        out = []
        for r in rows:
            d = date.fromisoformat(r.date)
            while (cursor := cursor - timedelta(days=1)) > d:
                out.append(_gap_row(cursor, st.brief.send_at))
            out.append(_record_row(r, labels, send_hm, tz, tables))
        out = out[:limit]

    since = (local_today(st.timezone, now) - timedelta(days=30)).isoformat()
    sent_30d = sum(1 for r in rows if r.date >= since and r.decision in (SENT, QUIET_SENT))
    next_at = None
    if st.brief.enabled:
        _, last_due = last_due_brief(st.timezone, send_hm, now)
        next_at = iso_z(last_due + timedelta(days=1))
    return {"rows": out, "enabled": st.brief.enabled, "send_at": st.brief.send_at, "top_rank": top_rank,
            "summary": {"sent_30d": sent_30d}, "next_at": next_at}
