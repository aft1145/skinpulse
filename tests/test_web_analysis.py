"""看板二期：`GET /api/analysis`（看板 spec §4.3、§5）——「整体排名在变好还是变差？」"""
from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from skinpulse.web import analysis as an
from skinpulse.web import overview as ov
from skinpulse.web.app import create_app
from tests.test_web_overview import NOW, TODAY, YESTERDAY, _order, _rt, _snap


def _labels(d):
    return {b["label"]: b["count"] for b in d["distribution"]}


# ---- 排名分布 ----

def test_distribution_buckets_and_their_edges(tmp_path, settings):
    ranks = [1, 2, 3, 10, 11, 20, 21, 50, 51, 123]
    rows = []
    for i, r in enumerate(ranks):
        rows += [_order(f"R{i}", 10), _snap(f"R{i}", TODAY, r, 11)]
    rt, _ = _rt(tmp_path, settings, rows)

    d = an.analysis_payload(rt, now=NOW)
    assert [b["label"] for b in d["distribution"]] == \
        ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11–20", "21–50", "51+"]
    got = _labels(d)
    assert got["1"] == got["2"] == got["3"] == got["10"] == 1
    assert got["4"] == 0
    assert got["11–20"] == 2 and got["21–50"] == 2 and got["51+"] == 2


def test_orders_without_a_rank_are_counted_apart_not_guessed_into_a_bucket(tmp_path, settings):
    """查过但不在抽样里（beyond）只知道「比第 N 名差」，归不进任何一档；没检查过的更不能算。"""
    rt, _ = _rt(tmp_path, settings, [
        _order("A", 10), _snap("A", TODAY, 2, 11),
        _order("B", 10), _snap("B", TODAY, None, 30, beyond=20),
        _order("C", 10),
    ])
    d = an.analysis_payload(rt, now=NOW)
    assert sum(b["count"] for b in d["distribution"]) == 1
    assert d["unranked"] == {"beyond": 1, "not_found": 0, "unchecked": 1}


# ---- 30 天每天收盘 ----

def test_daily_counts_top1_top2_top3_per_local_day(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings, [
        _order("A", 10), _snap("A", YESTERDAY, 1, 11), _snap("A", TODAY, 2, 11),
        _order("B", 10), _snap("B", YESTERDAY, 3, 11), _snap("B", TODAY, 3, 11),
        _order("C", 10), _snap("C", TODAY, 7, 11),
    ])
    d = an.analysis_payload(rt, now=NOW)
    days = {x["date"]: x for x in d["daily"]}
    assert len(d["daily"]) == 30 and d["daily"][-1]["date"] == "2026-09-27"
    assert days["2026-09-26"] == {"date": "2026-09-26", "rank1": 1, "top2": 1, "top3": 2, "total": 2}
    assert days["2026-09-27"] == {"date": "2026-09-27", "rank1": 0, "top2": 1, "top3": 2, "total": 3}


def test_days_without_data_are_null_not_zero(tmp_path, settings):
    """服务没跑的日子没有快照：是「不知道」，不是「0 张」，前端画成空档。"""
    rt, _ = _rt(tmp_path, settings, [_order("A", 10), _snap("A", TODAY, 1, 11)])
    d = an.analysis_payload(rt, now=NOW)
    assert d["daily"][0] == {"date": "2026-08-29", "rank1": None, "top2": None, "top3": None, "total": 0}


def test_withdrawn_orders_still_count_in_history(tmp_path, settings):
    """撤掉的单在它还挂着的那些天里照样算：历史要真实，不能拿今天的单子集合回头改写。"""
    gone = _order("G", 10)
    gone.active = False
    rt, _ = _rt(tmp_path, settings, [gone, _snap("G", YESTERDAY, 1, 11), _order("A", 10), _snap("A", TODAY, 5, 11)])
    d = an.analysis_payload(rt, now=NOW)
    assert {x["date"]: x["rank1"] for x in d["daily"]}["2026-09-26"] == 1


def test_local_day_boundary(tmp_path, settings):
    """本地 09-27 00:30（UTC 09-26 16:30）属于 09-27。"""
    just_after_midnight = NOW.replace(hour=16, minute=30) - timedelta(days=1)
    rt, _ = _rt(tmp_path, settings, [_order("A", 10), _snap("A", just_after_midnight, 1, 11)])
    days = {x["date"]: x["rank1"] for x in an.analysis_payload(rt, now=NOW)["daily"]}
    assert days["2026-09-27"] == 1 and days["2026-09-26"] is None


def test_last_snapshot_of_the_day_is_the_close(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings, [_order("A", 10), _snap("A", TODAY - timedelta(hours=2), 1, 11),
                                     _snap("A", TODAY, 4, 11)])
    assert an.analysis_payload(rt, now=NOW)["daily"][-1]["rank1"] == 0


def test_past_days_are_computed_once_per_day(tmp_path, settings, monkeypatch):
    """过去的日子不会再变：同一天里只算一次；今天每次都现算（新的一轮会改变今天的收盘）。"""
    rt, _ = _rt(tmp_path, settings, [_order("A", 10), _snap("A", YESTERDAY, 1, 11), _snap("A", TODAY, 2, 11)])
    an._past_cache.clear()
    spans = []
    real = an._closes

    def spy(s, bounds):
        spans.append(len(bounds))
        return real(s, bounds)

    monkeypatch.setattr(an, "_closes", spy)
    an.analysis_payload(rt, now=NOW)
    an.analysis_payload(rt, now=NOW + timedelta(minutes=5))
    assert spans == [29, 1, 1]


# ---- 今天升降最多的 ----

def test_movers_up_and_down_sorted_by_size_then_rank(tmp_path, settings):
    rows = []
    for no, y, t in (("U1", 9, 3), ("U2", 8, 5), ("U3", 6, 3), ("D1", 2, 9), ("D2", 4, 6), ("S", 5, 5)):
        rows += [_order(no, 10), _snap(no, YESTERDAY, y, 11), _snap(no, TODAY, t, 11)]
    rt, _ = _rt(tmp_path, settings, rows)

    m = an.analysis_payload(rt, now=NOW)["movers"]
    assert [x["purchase_no"] for x in m["up"]] == ["U1", "U3", "U2"]       # -6；-3 时名次好的在前
    assert [x["purchase_no"] for x in m["down"]] == ["D1", "D2"]
    top = m["up"][0]
    assert (top["rank"], top["prev_rank"], top["rank_change"], top["template_id"]) == (3, 9, -6, 1414)
    assert {"title", "star", "stattrak", "tag"} <= set(m["up"][0])


def test_movers_are_capped_at_ten_each(tmp_path, settings):
    rows = []
    for i in range(12):
        rows += [_order(f"U{i}", 10), _snap(f"U{i}", YESTERDAY, 30, 11), _snap(f"U{i}", TODAY, 20 - i, 11)]
    rt, _ = _rt(tmp_path, settings, rows)
    assert len(an.analysis_payload(rt, now=NOW)["movers"]["up"]) == 10


# ---- 标题用的摘要 ----

def test_summary_compares_top3_with_the_earliest_day_that_has_data_this_week(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings, [
        _order("A", 10), _snap("A", NOW - timedelta(days=5), 2, 11), _snap("A", TODAY, 5, 11),
        _order("B", 10), _snap("B", NOW - timedelta(days=5), 3, 11), _snap("B", YESTERDAY, 4, 11), _snap("B", TODAY, 3, 11),
    ])
    s = an.analysis_payload(rt, now=NOW)["summary"]
    assert s["top3"] == 1
    assert s["compare"] == {"date": "2026-09-22", "top3": 2}
    assert s["moved_up"] == 1 and s["moved_down"] == 0


def test_summary_without_history_has_no_comparison(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings, [_order("A", 10), _snap("A", TODAY, 2, 11)])
    assert an.analysis_payload(rt, now=NOW)["summary"]["compare"] is None


# ---- 缓存与接口 ----

def test_analysis_does_not_evict_the_overview_cache(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings, [_order("A", 10), _snap("A", TODAY, 2, 11)])
    ov.overview_payload(rt, now=NOW)
    key = ov._daily_cache["key"]
    an.analysis_payload(rt, now=NOW)
    assert ov._daily_cache["key"] == key


def test_api_analysis_endpoint(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings, [_order("A", 10), _snap("A", TODAY, 2, 11)])
    c = TestClient(create_app(rt), base_url=f"http://127.0.0.1:{settings.web.port}")
    j = c.get("/api/analysis").json()
    assert {"generated_at", "summary", "distribution", "unranked", "daily", "movers"} <= set(j)


# ---- 评审后补的（2026-09-27）----

def test_mover_rank_is_todays_close_even_if_the_latest_check_lost_the_rank(tmp_path, settings):
    """最新一次快照没名次（跑出了抽样），但今天早些时候有名次：升降按收盘算，名次也得用收盘，
    不能是 None——否则和别的单同分时排序会拿 None 和数字比，整个接口 500。"""
    rt, _ = _rt(tmp_path, settings, [
        _order("X", 10), _snap("X", YESTERDAY, 9, 11), _snap("X", TODAY - timedelta(hours=2), 3, 11),
        _snap("X", TODAY, None, 30, beyond=20),
        _order("Y", 10), _snap("Y", YESTERDAY, 10, 11), _snap("Y", TODAY, 4, 11),
    ])
    up = an.analysis_payload(rt, now=NOW)["movers"]["up"]
    assert [(m["purchase_no"], m["rank"], m["prev_rank"], m["rank_change"]) for m in up] == [("X", 3, 9, -6), ("Y", 4, 10, -6)]


def test_checked_but_not_found_is_not_reported_as_unchecked(tmp_path, settings):
    rt, _ = _rt(tmp_path, settings, [_order("N", 10), _snap("N", TODAY, None, 30), _order("U", 10)])
    assert an.analysis_payload(rt, now=NOW)["unranked"] == {"beyond": 0, "not_found": 1, "unchecked": 1}


def test_past_cache_picks_up_late_snapshots_for_yesterday(tmp_path, settings):
    """跨零点的一轮：23:59 的快照可能在零点后才提交。昨天的收盘缓存要认得出来并重算。"""
    rt, engine = _rt(tmp_path, settings, [_order("A", 10), _snap("A", TODAY, 5, 11)])
    an._past_cache.clear()
    assert {x["date"]: x["rank1"] for x in an.analysis_payload(rt, now=NOW)["daily"]}["2026-09-26"] is None
    with rt.session_factory() as s:
        s.add(_snap("A", YESTERDAY, 1, 11))
        s.commit()
    assert {x["date"]: x["rank1"] for x in an.analysis_payload(rt, now=NOW)["daily"]}["2026-09-26"] == 1
