"""看板改版一期的两个接口：`/api/meta` 和 `/api/overview`（看板改版 spec §4.1、§5）。

约定：金额是 JSON 数字（后端 Decimal 转 float，只用于显示）或 null——**缺失绝不填 0**；时间是
带 `Z` 的 UTC ISO 串，由前端按 `timezone` 格式化；所有业务判断（差价过滤、排名升降、焦点卡片）
都在这里用 Decimal 算好，前端只管展示。只读数据库和内存，绝不请求悠悠。
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, select

from skinpulse.adapters.youpin.token import jwt_expiry
from skinpulse.analysis.reach import market_ref, reach_kwargs, unreachable
from skinpulse.analysis.category import GROUPS, categorize, sub_label
from skinpulse.core.localday import local_day_bounds, local_today
from skinpulse.core.messages import ref_bid_pct
from skinpulse.core.names import split_item_name
from skinpulse.core.planner import _is_night
from skinpulse.db.models import Account, Item, MyBuyOrder, RankSnapshot, SystemStatus
from skinpulse.db.session import utcnow
from skinpulse.secrets import youpin_token_key
from skinpulse.services.account import _create_time, account_status_key
from skinpulse.core.buff_rank import is_buff
from skinpulse.services.brief import account_labels
from skinpulse.services.buff import buff_status_key
from skinpulse.services.monitor import ROUND, order_view
from skinpulse.web.common import _num, iso_z  # noqa: F401  其他接口模块也从这里拿
from skinpulse.web.value import attach_values

log = logging.getLogger(__name__)

ADAPTERS = ("youpin_anon", "youpin_account", "pushplus", "ntfy")   # 一个号时的通道 id；多号时见 channel_keys
DAYS = 7
FOCUS_MAX = 3
# 最近一轮距今超过「这么多个轮询周期」就算停摆（夜间周期按 night_multiplier 放大）。
STALE_ROUNDS = 3


# ---------------------------------------------------------------- /api/meta

# token 到期时间缓存 10 分钟：每个打开的标签页每 60 秒轮询一次 /api/meta，没必要每次都读钥匙串。
# set-token 在另一个进程里跑，这里没法立刻知道；最多晚 10 分钟看到新的到期时间。
TOKEN_TTL = timedelta(minutes=10)
_token_cache: dict = {}


def _token_expiry(rt, now: datetime, account_id: int = 1) -> datetime | None:
    hit = _token_cache.get(account_id)
    if hit is not None and hit[0] is rt and now - hit[1] < TOKEN_TTL:
        return hit[2]
    value = jwt_expiry(rt.secrets.get(youpin_token_key(account_id)) if rt.secrets is not None else None)
    _token_cache[account_id] = (rt, now, value)
    return value


def _account_ids(rt) -> list[int]:
    accs = getattr(rt, "accounts", None) or {1: None}
    return sorted(accs)


def channel_keys(rt) -> list[tuple[str, str]]:
    """系统页的通道：(看板上的 id, system_status 的 key)。一个号时账号通道的 id 还是 `youpin_account`
    （和以前一样）；多号时每个号一条 `youpin_account:N`（多账号 spec §7）。"""
    ids = _account_ids(rt)
    acct = ([("youpin_account", account_status_key(ids[0]))] if len(ids) <= 1
            else [(account_status_key(i), account_status_key(i)) for i in ids])
    return [("youpin_anon", "youpin_anon"), *acct, ("pushplus", "pushplus"), ("ntfy", "ntfy")]


def accounts_payload(rt, s, now: datetime) -> list[dict]:
    """库里的号：悠悠号在前、BUFF 号在后，各自启用的在前（多账号 spec §7、BUFF spec §8）。不含 token / cookie，
    只有到期时间（BUFF 不给到期时间，恒为 null）。"""
    rows = sorted(s.scalars(select(Account)), key=lambda a: (a.platform == "buff", not a.enabled, a.id))
    counts = dict(s.execute(select(MyBuyOrder.account_id, func.count()).where(MyBuyOrder.active.is_(True))
                            .group_by(MyBuyOrder.account_id)).all())
    tags = short_labels([a for a in rows if a.enabled], rt.settings.dashboard.platform_names)
    out = []
    for a in rows:
        buff = a.platform == "buff"
        st_row = s.get(SystemStatus, buff_status_key(a.id) if buff else account_status_key(a.id))
        expires = _token_expiry(rt, now, a.id) if a.enabled and not buff else None
        out.append({
            "id": a.id, "platform": "buff" if buff else "youpin",
            "label": (a.label or "BUFF") if buff else a.display_name(), "custom_label": a.label, "nickname": a.nickname,
            "tag": tags.get(a.id, _own_name(a)),   # 顶栏账号切换上的写法（[dashboard] platform_names）
            "enabled": a.enabled, "logged_in": (a.platform_uid is not None or bool(a.token_valid)) if buff
            else a.user_id is not None,
            "token": {"valid": bool(a.token_valid), "expires_at": iso_z(expires),
                      "days_left": None if expires is None else max((expires - now) // timedelta(days=1), 0)},
            "last_sync_at": iso_z(a.last_sync_at),
            "paused_until": iso_z(st_row.paused_until) if st_row is not None else None,
            "orders": counts.get(a.id, 0),
        })
    return out

def meta_payload(rt, *, now: datetime | None = None) -> dict:
    now = now or utcnow()
    s_ = rt.settings
    with rt.session_factory() as s:
        round_row = s.get(SystemStatus, ROUND)
        rows = [s.get(SystemStatus, key) for _, key in channel_keys(rt)]
        all_accounts = accounts_payload(rt, s, now)
        acc = s.get(Account, 1)
    # token、登录状态、首次使用只看悠悠号（BUFF 号没有 token 到期，在自己的卡片上显示）
    accounts = [a for a in all_accounts if a["platform"] == "youpin"]
    last_round_at = round_row.last_ok_at if round_row else None
    pauses = [r.paused_until for r in rows if r is not None and r.paused_until is not None]
    paused_until = max(pauses) if pauses else None

    p = s_.polling
    local_hour = now.astimezone(ZoneInfo(s_.timezone)).hour
    allowed_s = p.round_interval_min * 60 * STALE_ROUNDS * (p.night_multiplier if _is_night(local_hour, p) else 1)
    issues = []
    if last_round_at is None or (now - last_round_at).total_seconds() > allowed_s:
        issues.append("stale")
    if paused_until is not None and paused_until > now:
        issues.append("paused")
    last_round = rt.last_round
    if last_round and last_round.get("errors", 0) > 0:
        issues.append("errors")
    enabled = [a for a in accounts if a["enabled"]]
    # 从来没有验证通过过（user_id 只在验证成功时写；没 token 时第一轮也会建一行 Account）：
    # 是「还没登录」，不是「失效」，两者互斥（首次使用 spec §2.3）。多号时：一个登录过的号都没有才是
    # 「还没登录」，任何一个启用的号失效就是「失效」（多账号 spec §7）。
    logged = [a for a in enabled if a["logged_in"]]
    logged_in = bool(logged)
    if not logged_in:
        issues.append("not_logged_in")
    elif any(not a["token"]["valid"] for a in logged):
        issues.append("token_invalid")
    if last_round and last_round.get("sync_slow"):
        issues.append("sync_slow")
    # BUFF 号撞到限流、验证码正在暂停中（BUFF spec §8）
    if any(a["platform"] == "buff" and a["enabled"] and a["paused_until"] and a["paused_until"] > iso_z(now)
           for a in all_accounts):
        issues.append("buff_paused")
    # 顶栏的 token 提醒盯最紧的那个号：先是失效的，再是最先到期的（一个号时就是它自己）
    worst = min(logged, default=None, key=lambda a: (a["token"]["valid"], a["token"]["expires_at"] or "9999"))
    if len(enabled) == 1:
        token_valid = enabled[0]["token"]["valid"]           # 唯一启用的号（不一定是号 1）
    elif not enabled:
        token_valid = bool(acc and acc.token_valid)
    else:
        token_valid = bool(worst and worst["token"]["valid"])
    if len(enabled) <= 1:
        expires_at = _token_expiry(rt, now, enabled[0]["id"] if enabled else 1)
    else:
        expires_at = _token_expiry(rt, now, worst["id"]) if worst else None
    days_left = None if expires_at is None else max((expires_at - now) // timedelta(days=1), 0)

    return {
        "timezone": s_.timezone,
        "now": iso_z(now),
        "service": {
            "healthy": not issues,
            "issues": issues,
            "last_round_at": iso_z(last_round_at),
            "last_round": None if last_round is None else {**last_round, "at": iso_z(last_round.get("at"))},
            "paused_until": iso_z(paused_until),
        },
        "token": {"valid": token_valid, "expires_at": iso_z(expires_at), "days_left": days_left},
        "thresholds": {"enter_rank": s_.alerts.enter_rank, "top_rank": s_.brief.top_rank,
                       "max_gap_ratio": s_.alerts.max_gap_ratio,
                       "max_market_ratio": s_.alerts.max_market_ratio, "min_market_gap": s_.alerts.min_market_gap},
        # 「在悠悠打开」的链接模板，和告警消息里的是同一个
        "links": {"youpin_item": s_.ntfy.click_url_template},
        "setup": _setup(rt, acc, logged_in, accounts),
        "accounts": all_accounts,
        "demo": bool(getattr(rt, "demo", False)),
    }


def _setup(rt, acc, logged_in: bool, accounts: list[dict] | None = None) -> dict:
    """「开始使用」卡片的四步（首次使用 spec §2.1）。前三步必需，行情可选；demo 下全算完成。"""
    if getattr(rt, "demo", False):
        return {"youpin": True, "push": True, "first_round": True, "market": True}
    return {
        "youpin": logged_in,
        "push": bool(getattr(rt.ntfy, "names", None)),
        # monitor_round.last_ok_at 每轮开头就打、没登录也有，不能用；同步成功才写 last_sync_at
        "first_round": (acc is not None and acc.last_sync_at is not None)
                       or any(a["last_sync_at"] for a in accounts or []),
        "market": rt.market is not None,
    }


# ---------------------------------------------------------------- /api/overview

def _latest_snapshots(s, nos: list[str]) -> dict[str, RankSnapshot]:
    """每张单最新的一条快照，一条查询取完（原来是每张单一次，N+1）。"""
    if not nos:
        return {}
    last = (select(RankSnapshot.purchase_no, func.max(RankSnapshot.ts).label("mts"))
            .where(RankSnapshot.purchase_no.in_(nos)).group_by(RankSnapshot.purchase_no).subquery())
    q = select(RankSnapshot).join(last, and_(RankSnapshot.purchase_no == last.c.purchase_no,
                                             RankSnapshot.ts == last.c.mts))
    return {r.purchase_no: r for r in s.scalars(q)}


def _daily_ranks(s, nos: list[str], tz: str, days: list) -> dict[str, list[int | None]]:
    """最近几天每天「收盘」的排名：本地日界内最后一条有名次的快照。

    快照是每轮每张单一条，7 天就是几十万行（2026-09-27 实测 32 万），搬进 Python 分组要 0.6 秒。
    所以分组放在 SQL 里做：日界在 Python 里按 `local_day_bounds` 算好（夏令时也对），SQL 用
    CASE 把每条快照归到第几天，再按（单号，第几天）取 max(ts) 关联回来拿名次。
    """
    out = {no: [None] * len(days) for no in nos}
    if not nos:
        return out
    bounds = [local_day_bounds(d, tz) for d in days]
    day_idx = case(*((RankSnapshot.ts < end, i) for i, (_, end) in enumerate(bounds)), else_=None).label("day")
    last = (select(RankSnapshot.purchase_no, day_idx, func.max(RankSnapshot.ts).label("mts"))
            .where(RankSnapshot.purchase_no.in_(nos), RankSnapshot.rank.is_not(None),
                   RankSnapshot.ts >= bounds[0][0], RankSnapshot.ts < bounds[-1][1])
            .group_by(RankSnapshot.purchase_no, "day").subquery())
    q = (select(last.c.purchase_no, last.c.day, RankSnapshot.rank)
         .join(RankSnapshot, and_(RankSnapshot.purchase_no == last.c.purchase_no, RankSnapshot.ts == last.c.mts))
         .where(RankSnapshot.rank.is_not(None)))
    for no, day, rank in s.execute(q):
        if day is not None:
            out[no][day] = rank
    return out


# 7 天历史的缓存：只在有新快照（新的一轮）或单子集合 / 日期变化时重算。单条目，进程内。
_daily_cache: dict = {}


def _daily_ranks_cached(s, nos: list[str], tz: str, days: list) -> dict[str, list[int | None]]:
    newest = s.scalar(select(func.max(RankSnapshot.ts)))
    key = (str(s.get_bind().url), tz, tuple(days), newest, tuple(sorted(nos)))
    if _daily_cache.get("key") != key:
        _daily_cache.clear()
        _daily_cache.update(key=key, value=_daily_ranks(s, nos, tz, days))
    return {no: list(v) for no, v in _daily_cache["value"].items()}


def _hash_names(s, template_ids: list[int]) -> dict[int, str]:
    """模板号 → 市场英文名，一条查询。`my_buy_order.template_hash_name` 悠悠不给（库里是空的），
    品类只能从 `item` 表推。对不上的模板不在结果里，那一行进「其他」。"""
    if not template_ids:
        return {}
    q = (select(Item.youpin_template_id, Item.hash_name)
         .where(Item.youpin_template_id.in_(set(template_ids))).order_by(Item.id))
    out: dict[int, str] = {}
    for tid, name in s.execute(q):
        out.setdefault(tid, name)   # 模板号不唯一时固定取最早那一条，免得品类随行序跳
    return out


def _order_row(o: MyBuyOrder, snap: RankSnapshot | None, hist: list[int | None], limits: dict,
               hash_name: str | None = None, own_ahead: dict | None = None) -> dict:
    """一张单在看板上的一行。总览、饰品详情、分析页共用，字段和规则只有这一份。

    queue_key 坏了会抛 ValueError（`order_view`），由调用方跳过这一行。"""
    v = order_view(o, snap)
    name = split_item_name(o.commodity_name)
    top, my = v.top1_price, v.my_price
    ratio = (top / my) if (top is not None and my and my > 0) else None
    # 难以成交（难以成交 spec §4）：和推送、简报同一条规则（analysis/reach）
    market, market_src = market_ref(v.sell_min, v.sell_depth, o.ref_bid)
    mk_ok = market is not None and my is not None and my > 0
    reason = unreachable(my, top, v.sell_min, v.sell_depth, o.ref_bid, **limits)
    change = hist[-1] - hist[-2] if hist[-1] is not None and hist[-2] is not None else None
    cat = categorize(hash_name)
    return {
        "purchase_no": o.purchase_no, "template_id": o.template_id, "account_id": o.account_id,
        # BUFF 单（BUFF spec §8）：平台、前面挡路的条件单、BUFF 商品页
        "platform": "buff" if is_buff(o.purchase_no) else "youpin",
        "cond_ahead": o.cond_ahead if is_buff(o.purchase_no) else None,
        "buff_url": v.url,
        # 同一个子队列里，我另一个号的单排在这张前面（多账号 spec §7）：{label, price} 或 null
        "own_ahead": own_ahead,
        "title": name.title, "star": name.star, "stattrak": name.stattrak, "tag": name.tag,
        "queue": v.queue_label,
        "rank": v.rank, "beyond": snap.beyond if snap else None,
        # 没排名时，前端要分得清「还没检查过」和「查过但没找到」
        "checked": snap is not None,
        "rank_7d": hist, "rank_change": change,
        "my_price": _num(my), "top1_price": _num(top),
        "gap": _num(top - my) if top is not None else None,
        "gap_ratio": None if ratio is None else round(float(ratio), 4),
        # 「离第 1」太远（详情页还在用这个字段）；unreachable 是两条规则合起来的原因
        "gated": reason == "top",
        "unreachable": reason,
        "market": _num(market), "market_src": market_src,
        "market_gap": _num(market - my) if mk_ok else None,
        "market_ratio": round(float(market / my), 4) if mk_ok else None,
        "sell_min": _num(v.sell_min), "sell_depth": v.sell_depth,
        "ref_bid": _num(o.ref_bid), "ref_bid_platforms": o.ref_bid_platforms,
        # 和告警消息同一个算法：只和悠悠榜首比（core.messages.ref_bid_pct）
        "ref_bid_pct": None if (pct := ref_bid_pct(v)) is None else round(float(pct), 1),
        "ref_bid_at": iso_z(o.ref_bid_at),
        "remaining_qty": o.remaining_qty,
        "checked_at": iso_z(o.last_checked_at), "sell_checked_at": iso_z(o.sell_checked_at),
        # 悠悠显示「交易中」（rank = "*"）从哪一刻开始；这时 rank 是 null（成交推送 spec §6）
        "in_trade_since": iso_z(o.in_trade_since),
        # 市价提醒（市价提醒 spec §4）：inverted / near / both / null，来自每轮的判断，看板只读
        "market_flag": o.market_flag,
        # 品类筛选（看板 spec §4.1）：大类键、第二层英文键和中文显示名
        "category": cat.group, "subcategory": cat.sub,
        "subcategory_label": sub_label(o.commodity_name, cat.sub),
    }


def own_ahead_map(orders: list[MyBuyOrder], labels: dict[int, str]) -> dict[str, dict]:
    """同一个 queue_key 上，**另一个号**排在我前面的单（价更高；同价按 createTime 早）。几张都在前面时
    取价最高的。只查库，不发请求；一个号时（labels 为空）恒为空。"""
    if not labels:
        return {}
    by_key: dict[str, list[MyBuyOrder]] = {}
    for o in orders:
        by_key.setdefault(o.queue_key, []).append(o)
    created = {o.purchase_no: _create_time(o.conditions_json) or "" for o in orders}
    out: dict[str, dict] = {}
    for o in orders:
        best = None
        for p in by_key[o.queue_key]:
            if p.account_id == o.account_id:
                continue
            ahead = p.price > o.price or (p.price == o.price and created[p.purchase_no]
                                          and created[p.purchase_no] < created[o.purchase_no])
            if ahead and (best is None or p.price > best.price):
                best = p
        if best is not None:
            out[o.purchase_no] = {"label": labels.get(best.account_id, f"账号 {best.account_id}"),
                                  "price": _num(best.price)}
    return out


def _build_rows(orders, snaps, daily, limits: dict, hashes, *, where: str,
                ahead: dict[str, dict] | None = None) -> list[dict]:
    items: list[dict] = []
    malformed: list[str] = []
    ahead = ahead or {}
    for o in orders:
        try:
            items.append(_order_row(o, snaps.get(o.purchase_no), daily[o.purchase_no], limits,
                                    hashes.get(o.template_id), ahead.get(o.purchase_no)))
        except ValueError:
            # queue_key 坏了的单跳过，别让一行拖垮整页（和原来的 overview 一样）。
            malformed.append(o.purchase_no)
    if malformed:
        log.warning("%s: skipped %d order(s) with malformed queue_key: %s",
                    where, len(malformed), ", ".join(malformed))
    items.sort(key=lambda d: (d["rank"] is None, d["rank"] or 0, d["title"]))
    return items


def _week(tz: str, now: datetime) -> list:
    today = local_today(tz, now)
    return [today - timedelta(days=DAYS - 1 - i) for i in range(DAYS)]


def active_rows(rt, *, now: datetime | None = None, account_id: int | None = None) -> tuple[list[dict], list]:
    """全部活跃单的行（按排名排好）和对应的 7 天日期。7 天历史走缓存，总览和分析页共用。
    account_id：只要这个号的单（总览顶栏选了某个号，多账号 spec §8）。"""
    now = now or utcnow()
    tz = rt.settings.timezone
    days = _week(tz, now)
    with rt.session_factory() as s:
        orders = list(s.scalars(select(MyBuyOrder).where(MyBuyOrder.active.is_(True))))
        ahead = own_ahead_map(orders, account_labels(s))
        if account_id is not None:
            orders = [o for o in orders if o.account_id == account_id]
        nos = [o.purchase_no for o in orders]
        snaps = _latest_snapshots(s, nos)
        daily = _daily_ranks_cached(s, nos, tz, days)
        hashes = _hash_names(s, [o.template_id for o in orders])
    return _build_rows(orders, snaps, daily, reach_kwargs(rt.settings.alerts), hashes, where="overview_payload",
                       ahead=ahead), days


def template_rows(rt, template_id: int, *, now: datetime | None = None) -> list[dict]:
    """一个饰品模板下的活跃单（详情页用）。单子少，7 天历史直接查、不走缓存：
    缓存只存一份，换一组单号去调会把总览的那份挤掉，每 60 秒一次的总览轮询就全变成冷算。"""
    now = now or utcnow()
    tz = rt.settings.timezone
    with rt.session_factory() as s:
        orders = list(s.scalars(select(MyBuyOrder).where(MyBuyOrder.active.is_(True),
                                                         MyBuyOrder.template_id == template_id)))
        ahead = own_ahead_map(orders, account_labels(s))
        nos = [o.purchase_no for o in orders]
        snaps = _latest_snapshots(s, nos)
        daily = _daily_ranks(s, nos, tz, _week(tz, now))
        hashes = _hash_names(s, [template_id])
    rows = _build_rows(orders, snaps, daily, reach_kwargs(rt.settings.alerts), hashes, where="template_rows",
                       ahead=ahead)
    attach_values(rt, rows)     # 和总览的行一字不差，包括 value
    return rows


PLATFORM_NAMES = {"youpin": "悠悠", "buff": "BUFF"}


def _own_name(a: Account) -> str:
    return (a.label or "BUFF") if a.platform == "buff" else a.display_name()


def short_labels(rows: list[Account], platform_names: bool) -> dict[int, str]:
    """看板上每个号怎么写。默认是简称或昵称；`[dashboard] platform_names` 打开时写平台名，同一平台有几个号时
    写「悠悠 · 简称」（简称没改过就用昵称）。rows 是启用的号。"""
    if not platform_names:
        return {a.id: _own_name(a) for a in rows}
    per = Counter(a.platform for a in rows)
    out = {}
    for a in rows:
        plat = PLATFORM_NAMES.get(a.platform, a.platform)
        out[a.id] = plat if per[a.platform] < 2 else f"{plat} · {a.label or a.nickname or f'账号 {a.id}'}"
    return out


def youpin_labels(session, platform_names: bool = False) -> dict[int, str]:
    """只有悠悠号的地方（暂停单卡片）：启用的悠悠号 ≥ 2 时每个号一个，写法跟 [dashboard] platform_names 走。"""
    rows = list(session.scalars(select(Account).where(Account.platform == "youpin", Account.enabled.is_(True))
                                .order_by(Account.id)))
    return short_labels(rows, platform_names) if len(rows) >= 2 else {}


def dashboard_labels(session, platform_names: bool = False) -> dict[int, str]:
    """看板的账号简称：启用的号（悠悠、BUFF 一起算）≥ 2 时每个号一个，一个号时空（多账号 spec §8、BUFF spec §8）。"""
    rows = list(session.scalars(select(Account).where(Account.enabled.is_(True)).order_by(Account.id)))
    if len(rows) < 2:
        return {}
    return short_labels(rows, platform_names)


def overview_payload(rt, *, now: datetime | None = None, account_id: int | None = None) -> dict:
    now = now or utcnow()
    top_rank = rt.settings.brief.top_rank
    items, days = active_rows(rt, now=now, account_id=account_id)
    with rt.session_factory() as s:
        labels = dashboard_labels(s, rt.settings.dashboard.platform_names)
    valuation = attach_values(rt, items)

    counts = {str(r): 0 for r in range(1, top_rank + 1)}
    # 标题只数可成交的单（难以成交 spec §4）：被藏的第 1 名算进标题会误导
    ranked = [d["rank"] for d in items if d["rank"] is not None and not d["unreachable"]]
    for r in ranked:
        if r <= top_rank:
            counts[str(r)] += 1
    best = min(ranked) if ranked else None

    rank1 = [d for d in items if d["rank"] == 1 and not d["unreachable"]]
    rank2 = sorted((d for d in items if d["rank"] == 2 and not d["unreachable"]),
                   key=lambda d: (d["gap_ratio"] is None, d["gap_ratio"] or 0))
    focus = rank1 + rank2

    top3_series = [sum(1 for d in items if d["rank_7d"][i] is not None and d["rank_7d"][i] <= 3)
                   for i in range(DAYS)]
    return {
        "generated_at": iso_z(now),
        "headline": {"counts": counts, "top_rank": top_rank,
                     "best": None if best is None else {"rank": best, "count": ranked.count(best)}},
        "focus": focus[:FOCUS_MAX],
        "insights": {
            "total": len(items),
            "unreachable_count": sum(1 for d in items if d["unreachable"]),
            "unreachable_top": sum(1 for d in items if d["unreachable"] == "top"),
            "unreachable_market": sum(1 for d in items if d["unreachable"] == "market"),
            "moved_up": sum(1 for d in items if d["rank_change"] is not None and d["rank_change"] < 0),
            "moved_down": sum(1 for d in items if d["rank_change"] is not None and d["rank_change"] > 0),
            "top3_series": top3_series,
            "days": [d.isoformat() for d in days],
        },
        "orders": items,
        "category_groups": [{"key": k, "label": label} for k, label in GROUPS],
        # 价位视图（价位 spec §6.1）：从没算过估值时为 null，每行的 value 也是 null
        "valuation": valuation,
        # 启用的号 ≥ 2 时每个号的简称（键是字符串 id）；一个号时空对象，前端不显示账号相关的东西
        "accounts": {str(k): v for k, v in labels.items()},
    }
