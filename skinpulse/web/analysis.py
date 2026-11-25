"""`GET /api/analysis`：「整体排名在变好还是变差？」（看板 spec §4.3、§5）

- **分布**：现在每张活跃单的名次，第 1 到 10 名各一档，外加 11–20、21–50、51+。查过但不在抽样里
  （`beyond`）的单只知道「比第 N 名差」，查过没找到的、没检查过的更是不知道——三者单独计数，不硬塞进某一档。
- **30 天每天收盘**：当天有名次快照的**所有**单（包括现在已经撤掉的，历史要真实），各取当天最后一条
  有名次的快照，数第 1 名、前 2 名、前 3 名各几张。服务没跑的日子没有快照，给 null 不给 0。
- **升降最多的**：直接用总览的行（`overview.active_rows`，走它的 7 天缓存，不另起一份、也不挤掉它）。

过去的日子不会再变，每天只算一次（按 数据库 + 时区 + 今天 缓存）；今天那一天每次现算——每一轮
监控都可能改变今天的收盘。只读数据库，不碰悠悠。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, case, func, select

from skinpulse.core.localday import local_day_bounds, local_today
from skinpulse.db.models import RankSnapshot
from skinpulse.db.session import utcnow
from skinpulse.web.overview import active_rows, iso_z

DAYS = 30
MOVERS = 10
# 分布的档位：(标签, 最低名次, 最高名次)；最高为 None 表示不封顶
BUCKETS = [(str(r), r, r) for r in range(1, 11)] + [("11–20", 11, 20), ("21–50", 21, 50), ("51+", 51, None)]


def _closes(s, bounds: list[tuple[datetime, datetime]]) -> dict[int, list[int]]:
    """每个本地日（`bounds` 里的下标）收盘时各单的名次。分组放在 SQL 里做，写法同 overview._daily_ranks。"""
    out: dict[int, list[int]] = {i: [] for i in range(len(bounds))}
    day_idx = case(*((RankSnapshot.ts < end, i) for i, (_, end) in enumerate(bounds)), else_=None).label("day")
    last = (select(RankSnapshot.purchase_no, day_idx, func.max(RankSnapshot.ts).label("mts"))
            .where(RankSnapshot.rank.is_not(None),
                   RankSnapshot.ts >= bounds[0][0], RankSnapshot.ts < bounds[-1][1])
            .group_by(RankSnapshot.purchase_no, "day").subquery())
    q = (select(last.c.day, RankSnapshot.rank)
         .join(RankSnapshot, and_(RankSnapshot.purchase_no == last.c.purchase_no, RankSnapshot.ts == last.c.mts))
         .where(RankSnapshot.rank.is_not(None)))
    for day, rank in s.execute(q):
        if day is not None:
            out[day].append(rank)
    return out


# 过去 29 天的收盘：单条目，进程内；跨过零点（今天变了）就重算。
_past_cache: dict = {}


def _daily(rt, days: list) -> list[dict]:
    tz = rt.settings.timezone
    bounds = [local_day_bounds(d, tz) for d in days]
    with rt.session_factory() as s:
        # 键里带上「今天零点以前最新的一条快照时间」：跨零点的一轮，23:59 的快照可能零点后才提交，
        # 那时昨天的收盘要重算，不能一整天都缺那一轮。
        before_today = s.scalar(select(func.max(RankSnapshot.ts)).where(RankSnapshot.ts < bounds[-1][0]))
        key = (str(s.get_bind().url), tz, days[-1], before_today)
        if _past_cache.get("key") != key:
            _past_cache.clear()
            _past_cache.update(key=key, value=_closes(s, bounds[:-1]))
        past = _past_cache["value"]
        today = _closes(s, bounds[-1:])[0]

    out = []
    for i, d in enumerate(days):
        ranks = today if i == len(days) - 1 else past[i]
        has = bool(ranks)
        out.append({
            "date": d.isoformat(),
            "rank1": sum(1 for r in ranks if r == 1) if has else None,
            "top2": sum(1 for r in ranks if r <= 2) if has else None,
            "top3": sum(1 for r in ranks if r <= 3) if has else None,
            "total": len(ranks),
        })
    return out


def _mover(d: dict) -> dict:
    # 名次用今天的收盘（rank_7d[-1]），和 rank_change 同一个口径：最新一次检查可能跑出了抽样（rank 为 None）
    keys = ("purchase_no", "template_id", "title", "star", "stattrak", "tag", "rank_change")
    return {k: d[k] for k in keys} | {"rank": d["rank_7d"][-1], "prev_rank": d["rank_7d"][-2]}


def analysis_payload(rt, *, now: datetime | None = None) -> dict:
    now = now or utcnow()
    rows, _ = active_rows(rt, now=now)

    counts = {label: 0 for label, _, _ in BUCKETS}
    beyond = not_found = unchecked = 0
    for d in rows:
        r = d["rank"]
        if r is None:
            if not d["checked"]:
                unchecked += 1
            elif d["beyond"] is not None:
                beyond += 1
            else:
                not_found += 1
            continue
        for label, lo, hi in BUCKETS:
            if r >= lo and (hi is None or r <= hi):
                counts[label] += 1
                break

    today = local_today(rt.settings.timezone, now)
    days = [today - timedelta(days=DAYS - 1 - i) for i in range(DAYS)]
    daily = _daily(rt, days)

    moved = [d for d in rows if d["rank_change"] is not None and d["rank_change"] != 0]
    # rank_change 不为 None 时 rank_7d[-1] 一定有值（今天的收盘）
    up = sorted((d for d in moved if d["rank_change"] < 0), key=lambda d: (d["rank_change"], d["rank_7d"][-1]))
    down = sorted((d for d in moved if d["rank_change"] > 0), key=lambda d: (-d["rank_change"], d["rank_7d"][-1]))

    # 标题：今天前 3 名的张数，和本周（今天之前 6 天）里最早有数据的那天比
    week = [x for x in daily[-7:-1] if x["top3"] is not None]
    return {
        "generated_at": iso_z(now),
        "summary": {
            "top3": sum(1 for d in rows if d["rank"] is not None and d["rank"] <= 3),
            "compare": {"date": week[0]["date"], "top3": week[0]["top3"]} if week else None,
            "moved_up": len(up), "moved_down": len(down),
        },
        "distribution": [{"label": label, "min": lo, "max": hi, "count": counts[label]} for label, lo, hi in BUCKETS],
        "unranked": {"beyond": beyond, "not_found": not_found, "unchecked": unchecked},
        "daily": daily,
        "movers": {"up": [_mover(d) for d in up[:MOVERS]], "down": [_mover(d) for d in down[:MOVERS]]},
    }
