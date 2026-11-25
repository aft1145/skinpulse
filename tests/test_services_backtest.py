"""价位二期回测的取数和报告（价位 spec §9.4）：临时库，合成日线，不请求任何外部接口。"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

from typer.testing import CliRunner

from skinpulse.db.models import DailyBar, IndexDaily, Item, OrderEvent, RankSnapshot
from skinpulse.services import backtest as bts

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)
START = date(2025, 10, 1)


def seed(s, *, days=300):
    s.add(Item(hash_name="AK-47 | Slate (Factory New)", youpin_template_id=1))
    s.add(Item(hash_name="Sticker | Clown Nose", youpin_template_id=2))
    for i in range(days):
        d = (START + timedelta(days=i)).isoformat()
        for tid, amp in ((1, 10), (2, 3)):
            c = D(str(round(100 + amp * math.sin(i / (4 + tid)) - i * 0.05, 2)))
            s.add(DailyBar(template_id=tid, platform="YOUPIN", date=d, open=c, close=c, high=c, low=c, fetched_at=NOW))
        s.add(IndexDaily(date=d, open=D("1000"), close=D(str(1000 - i)), high=D("1000"), low=D("1000"), fetched_at=NOW))
    # 一条确认成交：消失前排第 3，求购价 60、当天收盘约 9x
    at = datetime(2026, 7, 1, 4, tzinfo=timezone.utc)
    s.add(RankSnapshot(purchase_no="F1", ts=at - timedelta(hours=1), rank=3, top1_price=D("70"), sample_size=20))
    s.add(RankSnapshot(purchase_no="F1", ts=at - timedelta(minutes=10), rank=None, beyond=20, sample_size=20))
    s.add(OrderEvent(purchase_no="F1", template_id=1, kind="gone", price=D("60"), at=at, outcome="done"))
    s.add(OrderEvent(purchase_no="F2", template_id=1, kind="gone", price=D("60"), at=at, outcome="missing"))
    s.commit()


def test_run_backtest_reads_only_the_db_and_judges(db_session, settings):
    seed(db_session)
    res = bts.run_backtest(db_session, settings)
    assert res.templates == 2 and res.first_day == START and res.quantile == 0.10
    assert {s.group for s in res.samples} == {"rifle", "sticker"}
    assert res.r1 is not None and res.r2 is not None and res.r3 is not None
    assert res.rebound_max == 0.3 and res.rebound_window == 60
    assert res.samples and all(set(s.q) == {0.10, 0.075, 0.05, 0.025} for s in res.samples)
    # 成交描述：只有 done 的那条；名次取消失前最后一次「有名次」的快照
    assert [(f.rank, f.price) for f in res.fills] == [(3, D("60"))]
    assert res.fills[0].close is not None


def test_render_has_verdicts_tables_and_fills(db_session, settings):
    seed(db_session)
    text = bts.render(bts.run_backtest(db_session, settings))
    for part in ("# 价位回测（2025-10-01", "## R1", "## R2", "## R3：「从近 60 根最低涨了 ≥ 30%」", "（R3）", "炒过", "### 分段", "### 按大类", "| 步枪 |", "| 印花 |",
                 "### 按月", "## 成交描述", "第 2～6 名 1 条"):
        assert part in text, part


def test_chosen_quantile():
    yes, no = bts.bt.Verdict(True, []), bts.bt.Verdict(False, [])
    r = bts.BacktestResult(quantile=0.10, templates=0, first_day=None, last_day=None, r1=yes)
    assert r.chosen == 0.10
    r.r1, r.fallback = no, (0.05, yes)
    assert r.chosen == 0.05
    r.fallback = (0.05, no)
    assert r.chosen is None
    r.fallback = (None, None)
    assert r.chosen is None


def test_empty_db_renders_without_samples(db_session, settings):
    text = bts.render(bts.run_backtest(db_session, settings))
    assert "没有样本" in text


def test_backtest_command_writes_the_report(tmp_path, settings, monkeypatch):
    from types import SimpleNamespace

    from skinpulse import cli

    monkeypatch.setattr(cli, "_rt", lambda _s, **kw: SimpleNamespace(settings=settings, session_factory=lambda: _Null()))
    monkeypatch.setattr(bts, "run_backtest", lambda s, st, split=None: bts.BacktestResult(0.10, 0, None, None))
    out = tmp_path / "r.md"
    r = CliRunner().invoke(cli.app, ["backtest", "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert "没有样本" in out.read_text(encoding="utf-8")


class _Null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fallback_only_tries_stricter_quantiles(db_session, settings, monkeypatch):
    seen = []
    monkeypatch.setattr(bts.bt, "check_r1", lambda ss, q, split=None: bts.bt.Verdict(False, []))
    monkeypatch.setattr(bts.bt, "best_quantile", lambda ss, qs, split=None: seen.append(sorted(qs)) or (None, None))
    seed(db_session)
    bts.run_backtest(db_session, settings)
    assert seen == [[0.025, 0.05, 0.075]]


def test_percent_format_never_prints_negative_zero():
    assert bts._p(-0.0004) == "0.0%" and bts._p(0.0) == "0.0%" and bts._p(-0.012) == "-1.2%" and bts._p(None) == "—"
