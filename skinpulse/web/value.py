"""价位一期的接口部分（价位 spec §6）：给总览、详情页的行挂上 `value`，以及顶层的 `valuation` 汇总。

只读 `valuation`、`daily_bar`、`index_daily` 三张表，不请求任何外部服务。估值表里只存和单无关
的部分；按我的价、按榜首价算的三项差价在这里现算（`analysis.valuation.margins`），这样改价以后
不用等第二天。算不出来的一律 null，绝不填 0。
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_, func, select

from skinpulse.analysis import valuation as va
from skinpulse.analysis.advice import LABELS as ADVICE_LABELS
from skinpulse.analysis.advice import advise
from skinpulse.db.models import DailyBar, IndexDaily, Valuation
from skinpulse.services.valuation import PLATFORM
from skinpulse.web.common import iso_z

SERIES_DAYS = 90
UNCONDITIONAL = "不限"      # core.messages.queue_label_of：没有磨损、款式条件的队列


def _f(x: Decimal | None, nd: int | None = None) -> float | None:
    if x is None:
        return None
    return float(x) if nd is None else round(float(x), nd)


def _dec(x) -> Decimal | None:
    # 行里的金额是从 Decimal 转成的两位小数 float，经 str 回到 Decimal 是精确的
    return None if x is None else Decimal(str(x))


def latest_valuations(session, template_ids) -> dict[int, Valuation]:
    """每个模板最新一天的估值。一条查询（带子查询），不逐单查。"""
    ids = set(template_ids)
    if not ids:
        return {}
    last = (select(Valuation.template_id, func.max(Valuation.date).label("d"))
            .where(Valuation.template_id.in_(ids)).group_by(Valuation.template_id).subquery())
    q = select(Valuation).join(last, and_(Valuation.template_id == last.c.template_id, Valuation.date == last.c.d))
    return {v.template_id: v for v in session.scalars(q)}


def _advice(v: Valuation, row: dict, m: va.Margins, cfg) -> dict | None:
    """价位二期的建议（价位 spec §9.5）。和三项差价一样现算，不存表；配置关掉时为 None。"""
    if cfg is None or not cfg.advice:
        return None
    a = advise(q10=v.q10, exit=v.exit, cap=v.cap, m_bad=m.m_bad, my_price=_dec(row["my_price"]),
               top_price=_dec(row["top1_price"]), rank=row["rank"],
               conditional=row["queue"] != UNCONDITIONAL, in_trade=row.get("in_trade_since") is not None,
               target_margin=Decimal(str(cfg.target_margin)), min_profit=Decimal(str(cfg.min_profit)))
    return {"kind": a.kind, "label": a.label, "rule": a.rule, "price": _f(a.price)}


def value_block(v: Valuation | None, row: dict, cfg=None) -> dict | None:
    """`cfg` 是 `settings.valuation`；不给就不算建议。"""
    if v is None:
        return None
    m = va.margins(v.exit, v.q10, _dec(row["my_price"]), _dec(row["top1_price"]), row["rank"])
    return {
        "as_of": v.date, "bars": v.bars,
        "pct": _f(v.pct), "pct_label": va.pct_label(v.pct),
        "lo": _f(v.lo), "hi": _f(v.hi), "cur": _f(v.cur),
        "trend": v.trend, "rel": v.rel,
        "c30": _f(v.c30), "c90": _f(v.c90), "b30": _f(v.b30), "b90": _f(v.b90), "ex30": _f(v.ex30),
        "vol7": _f(v.vol7), "vol_label": va.vol_label(v.vol7), "q10": _f(v.q10),
        "liq": v.liq, "listed": v.listed, "spread": _f(v.spread),
        "exit": _f(v.exit), "exit_where": v.exit_where, "exit_sell": _f(v.exit_sell),
        "m_my": _f(m.m_my), "m_top": _f(m.m_top), "m_bad": _f(m.m_bad),
        "conditional": row["queue"] != UNCONDITIONAL,
        "cap": _f(v.cap),
        "advice": _advice(v, row, m, cfg),
    }


def attach_values(rt, rows: list[dict]) -> dict | None:
    """给每行挂上 `value`（没估值为 null），返回总览顶层的 `valuation` 汇总（从没算过为 null）。"""
    with rt.session_factory() as s:
        vals = latest_valuations(s, [r["template_id"] for r in rows])
    for r in rows:
        # BUFF 单不给价位（BUFF spec §8：到手价只按悠悠算，ADR-0019）
        r["value"] = None if r.get("platform") == "buff" else value_block(vals.get(r["template_id"]), r,
                                                                          rt.settings.valuation)
    if not vals:
        return None
    cfg = rt.settings.valuation
    target = Decimal(str(cfg.target_margin))
    evaluated = [r["value"] for r in rows if r["value"] is not None]
    newest = max(vals.values(), key=lambda v: (v.date, v.computed_at))
    return {
        "as_of": newest.date,
        "computed_at": iso_z(newest.computed_at),
        "snapshot_date": max((v.snapshot_date for v in vals.values() if v.snapshot_date), default=None),
        "counts": {
            "take": sum(1 for x in evaluated if x["m_top"] is not None and Decimal(str(x["m_top"])) >= target),
            "take_loss": sum(1 for x in evaluated if x["m_top"] is not None and x["m_top"] < 0),
            "low": sum(1 for x in evaluated if x["pct_label"] == "一年低位"),
            "fall": sum(1 for x in evaluated if x["trend"] == "下降" and x["rel"] == "跑输"),
            "hard": sum(1 for x in evaluated if x["liq"] == "难出手"),
            "evaluated": len(evaluated),
            "insufficient": sum(1 for x in evaluated if x["pct"] is None),
            # 各建议的张数；建议关掉时为 null
            "advice": None if not cfg.advice else {
                k: sum(1 for x in evaluated if x["advice"] and x["advice"]["kind"] == k) for k in ADVICE_LABELS},
        },
        **_rules(rt),
    }


def _rules(rt) -> dict:
    cfg = rt.settings.valuation
    return {"target_margin": cfg.target_margin,
            "fees": {**cfg.fees, "withdraw": cfg.withdraw_fee, "undercut": cfg.undercut}}


def item_valuation(rt, template_id: int, rows: list[dict]) -> dict | None:
    """详情页的 `valuation`：按排名最好的那张单算三项差价（和「我的求购」卡片默认那张一致），
    外加近 90 天单品和大盘的收盘（按日期对齐，缺那天为 null）。"""
    with rt.session_factory() as s:
        v = latest_valuations(s, [template_id]).get(template_id)
        if v is None:
            return None
        end = date.fromisoformat(v.date)
        start = end - timedelta(days=SERIES_DAYS - 1)
        rng = (start.isoformat(), end.isoformat())
        item = dict(s.execute(select(DailyBar.date, DailyBar.close).where(
            DailyBar.template_id == template_id, DailyBar.platform == PLATFORM, DailyBar.date.between(*rng))).all())
        broad = dict(s.execute(select(IndexDaily.date, IndexDaily.close).where(IndexDaily.date.between(*rng))).all())
    row = rows[0] if rows else {"my_price": None, "top1_price": None, "rank": None, "queue": UNCONDITIONAL}
    out = value_block(v, row, rt.settings.valuation)
    days = [(start + timedelta(days=k)).isoformat() for k in range(SERIES_DAYS)]
    out["series90"] = [[d, _f(item.get(d)), _f(broad.get(d))] for d in days]
    out["computed_at"] = iso_z(v.computed_at)
    out["snapshot_date"] = v.snapshot_date
    out.update(_rules(rt))      # 扣费明细的文字要用
    return out


def status_valuation(rt) -> dict:
    """系统页那一行：开关、时间、最近一次算到哪天、算了几件。"""
    cfg = rt.settings.valuation
    with rt.session_factory() as s:
        day = s.scalar(select(func.max(Valuation.date)))       # date 有索引：不扫全表
        last = None if day is None else s.execute(
            select(func.max(Valuation.computed_at), func.count()).where(Valuation.date == day)).one()
    return {
        "enabled": cfg.enabled, "run_at": cfg.run_at,
        "last": None if last is None else {"as_of": day, "computed_at": iso_z(last[0]), "valued": last[1]},
    }
