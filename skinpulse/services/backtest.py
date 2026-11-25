"""价位二期回测的取数和报告（价位 spec §9.2–§9.4）。

只读库：`daily_bar`（悠悠日线）、`index_daily`（大盘）、`item`（品类）、`order_event` 和
`rank_snapshot`（成交描述）。**不请求任何外部接口**，随时可以重跑。算法全在 `analysis.backtest`。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from skinpulse.analysis import backtest as bt
from skinpulse.analysis import valuation as va
from skinpulse.analysis.category import GROUP_LABELS, categorize
from skinpulse.db.models import DailyBar, IndexDaily, Item, OrderEvent, RankSnapshot
from skinpulse.services.valuation import PLATFORM


@dataclass(frozen=True)
class Fill:
    at: datetime
    rank: int | None           # 消失前最后一次有名次的快照
    price: Decimal | None      # 我的求购价
    close: Decimal | None      # 那天（本地日期）的悠悠收盘价


@dataclass
class BacktestResult:
    quantile: float                         # 配置里的坏情况分位
    templates: int
    first_day: date | None
    last_day: date | None
    samples: list[bt.Sample] = field(default_factory=list)
    r1: bt.Verdict | None = None
    fallback: tuple[float | None, bt.Verdict | None] | None = None   # R1 不过时 best_quantile 的结果
    r2: bt.Verdict | None = None
    r3: bt.Verdict | None = None
    rebound_max: float = 0.3                # R3 的门槛（[valuation] rebound_max）
    rebound_window: int = 60
    fills: list[Fill] = field(default_factory=list)
    split: date = bt.H2_START               # H2 从哪天起

    @property
    def chosen(self) -> float | None:
        """上线用的分位：R1 直接过就是配置的；否则是兜底找到并验证通过的；都不行为 None。"""
        if self.r1 is not None and self.r1.passed:
            return self.quantile
        if self.fallback and self.fallback[0] is not None and self.fallback[1].passed:
            return self.fallback[0]
        return None


def load_series(s: Session) -> tuple[dict[int, list[tuple[date, Decimal]]], dict[date, Decimal]]:
    series: dict[int, list[tuple[date, Decimal]]] = defaultdict(list)
    q = (select(DailyBar.template_id, DailyBar.date, DailyBar.close)
         .where(DailyBar.platform == PLATFORM).order_by(DailyBar.template_id, DailyBar.date))
    for tid, d, close in s.execute(q):
        if close is not None and close > 0:
            series[tid].append((date.fromisoformat(d), close))
    broad = {date.fromisoformat(d): c for d, c in s.execute(select(IndexDaily.date, IndexDaily.close))
             if c is not None and c > 0}
    return dict(series), broad


def load_fills(s: Session, tz: str) -> list[Fill]:
    zone = ZoneInfo(tz)
    out = []
    events = s.scalars(select(OrderEvent).where(OrderEvent.kind == "gone", OrderEvent.outcome == "done")
                       .order_by(OrderEvent.at))
    for e in events:
        rank = s.scalar(select(RankSnapshot.rank).where(
            RankSnapshot.purchase_no == e.purchase_no, RankSnapshot.ts <= e.at, RankSnapshot.rank.is_not(None))
            .order_by(RankSnapshot.ts.desc()).limit(1))
        day = e.at.astimezone(zone).date().isoformat()
        close = s.scalar(select(DailyBar.close).where(
            DailyBar.template_id == e.template_id, DailyBar.platform == PLATFORM, DailyBar.date <= day)
            .order_by(DailyBar.date.desc()).limit(1))
        out.append(Fill(at=e.at, rank=rank, price=e.price, close=close))
    return out


def run_backtest(s: Session, settings, split: date = bt.H2_START) -> BacktestResult:
    cfg = va.ValuationConfig.from_settings(settings.valuation)
    quantile = float(cfg.bad_quantile)
    quantiles = tuple(sorted({quantile, *bt.QUANTILES}, reverse=True))
    series, broad = load_series(s)
    names = dict(s.execute(select(Item.youpin_template_id, Item.hash_name).order_by(Item.id.desc())).tuples().all())
    samples: list[bt.Sample] = []
    for tid, closes in series.items():
        samples += bt.samples(tid, categorize(names.get(tid)).group, closes, broad, cfg, quantiles=quantiles)
    days = [d for cl in series.values() for d, _ in cl]
    res = BacktestResult(quantile=quantile, templates=len(series), first_day=min(days, default=None),
                         last_day=max(days, default=None), samples=samples,
                         fills=load_fills(s, settings.timezone), split=split,
                         rebound_max=float(cfg.rebound_max), rebound_window=cfg.rebound_window)
    if samples:
        res.r1 = bt.check_r1(samples, quantile, split)
        if not res.r1.passed:
            # 只试比配置更严的分位：配置的那个刚刚没过，再挑一遍只会原样失败
            res.fallback = bt.best_quantile(samples, [q for q in quantiles if q < quantile], split)
        res.r2 = bt.check_r2(samples, split)
        res.r3 = bt.check_r3(samples, res.rebound_max, split)
    return res


# ---------------------------------------------------------------- 报告

def _p(x: float | None) -> str:
    if x is None:
        return "—"
    s = f"{x * 100:+.1f}%"
    return "0.0%" if s in ("+0.0%", "-0.0%") else s


def _cov(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _row(name: str, ss: list[bt.Sample], q: float) -> str:
    st = bt.stats(ss, q)
    if st is None:
        return f"| {name} | 0 | — | — | — | — |"
    return f"| {name} | {st.n} | {_cov(st.cover)} | {_p(st.r7_mean)} | {_p(st.r14_median)} | {_cov(st.p_loss14)} |"


def _table(title: str, rows: list[tuple[str, list[bt.Sample]]], q: float) -> list[str]:
    return [f"### {title}", "", f"| | n | 覆盖率（{q:g} 分位） | r7 均值 | r14 中位数 | P(r14<−5%) |",
            "|---|---|---|---|---|---|", *(_row(n, ss, q) for n, ss in rows), ""]


def render(res: BacktestResult) -> str:
    ss = res.samples
    q = res.chosen or res.quantile
    out = [f"# 价位回测（{res.first_day} 至 {res.last_day}）", "",
           f"- 模板 {res.templates} 个，样本 {len(ss)} 个（每 {bt.STEP} 根取一个；H2 从 {res.split} 起）",
           f"- 配置的坏情况分位 {res.quantile:g}；上线用的分位：{'—（不上线靠 cap 的建议）' if res.chosen is None else f'{res.chosen:g}'}",
           ""]
    if not ss:
        return "\n".join([*out, "没有样本：日线不够长。", ""])
    out += [f"## R1：7 天坏情况准不准（{'通过' if res.r1.passed else '不通过'}，分位 {res.quantile:g}）", "",
            *(f"- {r}" for r in res.r1.reasons), ""]
    if res.fallback is not None:
        fq, fv = res.fallback
        if fq is None:
            out += ["H1 上没有一个分位能满足标准。", ""]
        else:
            out += [f"### 换分位：H1 上第一个满足的是 {fq:g}，全部样本验证{'通过' if fv.passed else '不通过'}", "",
                    *(f"- {r}" for r in fv.reasons), ""]
    out += [f"## R2：「在跌且跑输大盘」能不能单独当撤单理由（{'通过' if res.r2.passed else '不通过'}）", "",
            *(f"- {r}" for r in res.r2.reasons), ""]
    out += [f"## R3：「从近 {res.rebound_window} 根最低涨了 ≥ {res.rebound_max:.0%}」能不能当选品「观望」的理由"
            f"（{'通过' if res.r3.passed else '不通过'}）", "", *(f"- {r}" for r in res.r3.reasons), ""]
    out += ["## 明细", ""]
    out += _table("分段", [("全部", ss), ("H1", bt.h1(ss, res.split)), ("H2", bt.h2(ss, res.split)),
                          ("一年低位（分位 ≤ 0.2）", [s for s in ss if s.pct <= float(va.LOW_PCT)]),
                          ("一年高位（分位 ≥ 0.8）", [s for s in ss if s.pct >= float(va.HIGH_PCT)]),
                          ("在跌且跑输大盘", [s for s in ss if s.flagged]),
                          (f"从近 {res.rebound_window} 根最低涨了 ≥ {res.rebound_max:.0%}（R3）",
                           [s for s in ss if bt.rebounded(s, res.rebound_max)]),
                          ("炒过：一年最高 ≥ 最低 × 3（只作描述）", [s for s in ss if s.amp is not None and s.amp >= 3])], q)
    groups = bt.by(ss, lambda s: s.group)
    out += _table("按大类", [(GROUP_LABELS.get(g, g), groups[g]) for g in GROUP_LABELS if g in groups], q)
    months = bt.by(ss, lambda s: s.day.strftime("%Y-%m"))
    out += _table("按月（样本日期）", sorted(months.items()), q)
    out += ["## 成交描述（不设标准）", "", "| 消失时间（UTC） | 消失前名次 | 我的价 | 当天收盘 | 价 / 收盘 |", "|---|---|---|---|---|"]
    for f in res.fills:
        ratio = "—" if not (f.price and f.close) else f"{float(f.price / f.close):.2f}"
        out.append(f"| {f.at:%Y-%m-%d %H:%M} | {f.rank if f.rank is not None else '—'} | {f.price if f.price is not None else '—'} | "
                   f"{f.close if f.close is not None else '—'} | {ratio} |")
    ranks = [f.rank for f in res.fills if f.rank is not None]
    if ranks:
        out += ["", f"共 {len(res.fills)} 条；第 1 名 {ranks.count(1)} 条，第 2～6 名 {sum(2 <= r <= 6 for r in ranks)} 条，"
                    f"第 7 名以后 {sum(r > 6 for r in ranks)} 条。"]
    out.append("")
    return "\n".join(out)
