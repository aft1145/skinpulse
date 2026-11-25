"""价位三期选品的接口（价位 spec §9b.4）。只读库；开始选品交给 `services.pick.PickRunner`（只请求 SteamDT）。

缺失为 null，**绝不填 0**；金额是 JSON 数字（Decimal 转 float，只用于显示）。
"""
from __future__ import annotations

import json
from collections import Counter

from sqlalchemy import func, select

from skinpulse.analysis import pick as pk
from skinpulse.analysis import valuation as va
from skinpulse.analysis.category import GROUPS, sub_label
from skinpulse.core.names import split_item_name
from skinpulse.db.models import Item, PickResult, PickRun
from skinpulse.services import pick as sp
from skinpulse.web.common import _num, iso_z


def filters_from_query(q) -> pk.PoolFilter:
    """GET 参数（逗号分隔）→ 条件，和 POST 的 JSON 走同一套校验。"""
    def lst(k):
        v = q.get(k)
        return [x for x in v.split(",") if x] if v else []
    return pk.PoolFilter.from_dict({"groups": lst("groups"), "subs": lst("subs"), "wears": lst("wears"),
                                    "versions": lst("versions"), "price_min": q.get("price_min"),
                                    "price_max": q.get("price_max")})


_subs_cache: dict = {}


def _subs(s) -> dict[str, list[dict]]:
    """每个大类下的第二层（武器、刀型、手套类型）和中文名、模板数。和 `services.pick.catalog` 同一个缓存键。"""
    key = (str(s.get_bind().url),
           *s.execute(select(func.count(Item.id), func.max(Item.id)).where(Item.youpin_template_id.is_not(None))).one())
    if _subs_cache.get("key") == key:
        return _subs_cache["value"]
    rows = s.execute(select(Item.hash_name, Item.cn_name).where(Item.youpin_template_id.is_not(None))).tuples()
    by: dict[str, Counter] = {}
    labels: dict[str, str] = {}
    for hash_name, cn in rows:
        p = pk.parse_name(hash_name)
        if p.sub is None:
            continue
        by.setdefault(p.group, Counter())[p.sub] += 1
        labels.setdefault(p.sub, sub_label(cn, p.sub) or p.sub)
    value = {g: [{"key": k, "label": labels[k], "n": n} for k, n in sorted(c.items(), key=lambda kv: labels[kv[0]])]
             for g, c in by.items()}
    _subs_cache.update(key=key, value=value)
    return value


# 某个磨损 / 版本在一个大类里少于这么多个模板就不列：步枪里有 2 个「无磨损」、手枪里有 4 个，都是目录里的
# 零星特例，为它们在步枪下面多列一个「无磨损」只会让人困惑（不选 = 全部，它们照样圈得到）
FACET_MIN = 10
_facets_cache: dict = {}


def _facets(s) -> dict[str, dict[str, list[str]]]:
    """每个大类在目录里实际有哪些磨损、版本（intent 2026-11-23：探员没有磨损和版本，选了「崭新出厂」就圈不到）。
    按 WEAR_LABELS / VERSIONS 的顺序。和 `_subs` 同一个缓存键。"""
    key = (str(s.get_bind().url),
           *s.execute(select(func.count(Item.id), func.max(Item.id)).where(Item.youpin_template_id.is_not(None))).one())
    if _facets_cache.get("key") == key:
        return _facets_cache["value"]
    wears: dict[str, Counter] = {}
    versions: dict[str, Counter] = {}
    for (hash_name,) in s.execute(select(Item.hash_name).where(Item.youpin_template_id.is_not(None))).tuples():
        p = pk.parse_name(hash_name)
        wears.setdefault(p.group, Counter())[p.wear] += 1
        versions.setdefault(p.group, Counter())[p.version] += 1
    value = {g: {"wears": [k for k in pk.WEAR_LABELS if wears[g][k] >= FACET_MIN],
                 "versions": [k for k in pk.VERSIONS if versions[g][k] >= FACET_MIN]}
             for g in wears}
    _facets_cache.update(key=key, value=value)
    return value


def options_payload(rt) -> dict:
    with rt.session_factory() as s:
        subs = _subs(s)
        facets = _facets(s)
    return {
        "groups": [{"key": k, "label": label, "default": k in pk.DEFAULT_GROUPS} for k, label in GROUPS],
        "subs": subs,
        "wears": [{"key": k, "label": v} for k, v in pk.WEAR_LABELS.items()],
        "versions": [{"key": k, "label": v} for k, v in pk.VERSIONS.items()],
        "group_facets": facets,
        "max": pk.MAX_POOL,
        "night": sp.night_label(rt.settings),
    }


def estimate_payload(rt, f: pk.PoolFilter) -> dict:
    with rt.session_factory() as s:
        return sp.estimate(s, f, rt.settings)


def _run_brief(r: PickRun, counts: dict[str, int]) -> dict:
    return {"id": r.id, "created_at": iso_z(r.created_at), "finished_at": iso_z(r.finished_at),
            "status": r.status, "phase": r.phase, "done": r.done, "total": r.total,
            "pool": r.pool, "priced": r.priced, "failed": r.failed, "price_failed": r.price_failed, "error": r.error,
            "filters": json.loads(r.filters_json), "counts": counts}


def _counts(s, run_ids) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {i: {k: 0 for k in pk.VERDICT_LABELS} for i in run_ids}
    if run_ids:
        q = (select(PickResult.run_id, PickResult.verdict, func.count())
             .where(PickResult.run_id.in_(run_ids)).group_by(PickResult.run_id, PickResult.verdict))
        for rid, v, n in s.execute(q):
            out[rid][v] = n
    return out


def runs_payload(rt, runner=None) -> dict:
    with rt.session_factory() as s:
        runs = list(s.scalars(select(PickRun).order_by(PickRun.id.desc()).limit(sp.KEEP_RUNS)))
        counts = _counts(s, [r.id for r in runs])
    return {"runs": [_run_brief(r, counts[r.id]) for r in runs], "busy": bool(runner and runner.busy)}


def run_payload(rt, run_id: int) -> dict | None:
    with rt.session_factory() as s:
        r = s.get(PickRun, run_id)
        if r is None:
            return None
        results = list(s.scalars(select(PickResult).where(PickResult.run_id == run_id)))
        names = dict(s.execute(select(Item.youpin_template_id, Item.cn_name)
                               .where(Item.youpin_template_id.in_({x.template_id for x in results}))).tuples().all()) \
            if results else {}
        counts = _counts(s, [run_id])[run_id]
    rows = []
    for x in results:
        n = split_item_name(names.get(x.template_id) or "")
        rows.append({
            "template_id": x.template_id, "title": n.title or str(x.template_id), "star": n.star,
            "stattrak": n.stattrak, "tag": n.tag,
            "yp_sell": _num(x.yp_sell), "yp_count": x.yp_count, "yp_bid": _num(x.yp_bid),
            "bars": x.bars, "pct": _num(x.pct), "pct_label": va.pct_label(x.pct),
            "trend": x.trend, "rel": x.rel, "q10": _num(x.q10), "liq": x.liq,
            "exit": _num(x.exit), "exit_where": x.exit_where, "cap": _num(x.cap),
            "rebound": _num(x.rebound), "amp": _num(x.amp),
            "verdict": x.verdict, "label": pk.VERDICT_LABELS[x.verdict], "rule": x.rule,
            "bid": _num(x.bid), "margin": _num(x.margin), "first": x.first, "mine": x.mine,
        })
    # 「刚从低点涨上来」的窗口和门槛（R3），页面写「近 60 天低点 +71%」时用
    v = rt.settings.valuation
    return {**_run_brief(r, counts), "results": rows, "rebound_window": v.rebound_window, "rebound_max": v.rebound_max}
