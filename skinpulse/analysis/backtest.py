"""价位二期的 walk-forward 回测（纯逻辑，无 IO）。价位 spec §9.2、§9.3，ADR-0014。

要回答两个问题，标准在正式跑之前就定死（spec §9.3），这里只照着判，不为了让规则通过而调：
- **R1**：「7 天坏情况」（`q10`）准不准。覆盖率 = 之后 7 根里跌得比它还狠的样本占比。
- **R2**：「在跌且跑输大盘」能不能单独当撤单理由：被标出的样本，之后 14 根是不是明显更差。
- **R3**（2026-11-23）：「从近 60 根最低涨了 ≥ 30%」能不能当选品「观望」的理由：标准和 R2 一样。

不另写算法：每个样本都在「截到 t 为止」的日线上调 `valuation.compute`，回测的和页面上的是同一个东西，
也就不可能偷看到 t 之后的数据。前瞻收益按**根数**算（和 `q10` 的 7 天口径一致），日线缺的日子不补。
"""
from __future__ import annotations

import statistics
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from skinpulse.analysis import valuation as va

H2_START = date(2026, 4, 1)   # 默认分界：H1 选参数、H2 只验证；2026-10 的一年数据对半分（spec §9.2）。重跑时用 CLI 的 --split 挪
STEP = 5                      # 每 5 根取一个样本：相邻样本的前瞻窗口大半重叠，取太密只是在数同一件事
SHORT, LONG = va.BAD_WINDOW, 14   # 7 天交易保护期；再加上挂出去卖的时间
LOSS_LINE = -0.05             # 「14 天后跌超 5%」算吃亏

# R1：覆盖率上限。设计上第 10 百分位应该 ≈ 10%；低于 10% 只是偏保守，不算失败
R1_MAX = 0.13
R1_GROUP_MAX = 0.15
R1_GROUP_MIN_N = 1000         # 样本太少的大类不单独判
# R2：标出的样本 P(r14 < −5%) 至少高出这么多，并且 r14 中位数更低，H1、H2 都要满足
R2_MIN_GAP = 0.05
# R1 不过时依次试的分位（从宽到严），取第一个过的
QUANTILES = (0.10, 0.075, 0.05, 0.025)

FALLING, LAGGING = "下降", "跑输"


@dataclass(frozen=True)
class Sample:
    day: date
    template_id: int
    group: str
    q: Mapping[float, float]      # 分位 → 当时算出的 7 天坏情况
    r7: float
    r14: float
    trend: str | None
    rel: str | None
    pct: float
    rebound: float | None = None
    amp: float | None = None

    def cover(self, quantile: float) -> bool:
        return self.r7 < self.q[quantile]

    @property
    def flagged(self) -> bool:
        return self.trend == FALLING and self.rel == LAGGING


def samples(template_id: int, group: str, closes: list[tuple[date, Decimal]],
            broad: Mapping[date, Decimal], cfg: va.ValuationConfig, *,
            quantiles: Iterable[float] = QUANTILES, step: int = STEP) -> list[Sample]:
    """一个模板的回测样本。`closes` 是已收盘的 (日期, 收盘价)，按日期升序、没有 0 价（`daily_bar` 写库时已丢掉）。"""
    qs_wanted = sorted(set(quantiles), reverse=True)
    out: list[Sample] = []
    for t in range(cfg.min_bars - 1, len(closes) - LONG, step):
        hist = closes[:t + 1]
        main = va.compute(hist, broad, {}, replace(cfg, bad_quantile=Decimal(str(qs_wanted[0]))))
        if main.q10 is None or main.pct is None:
            continue
        # 其余分位只差最后一步：和 compute 里同一份 7 天涨跌、同一个取分位函数，不用整个重算
        cs = [c for _, c in hist if c > 0][-va.YEAR_BARS:]
        r7 = [cs[i + va.BAD_WINDOW] / cs[i] - 1 for i in range(len(cs) - va.BAD_WINDOW)]
        qs = {q: float(main.q10) if q == qs_wanted[0] else float(va._r(va._quantile(r7, Decimal(str(q)))))
              for q in qs_wanted}
        c0 = closes[t][1]
        out.append(Sample(day=closes[t][0], template_id=template_id, group=group, q=qs,
                          r7=float(closes[t + SHORT][1] / c0 - 1), r14=float(closes[t + LONG][1] / c0 - 1),
                          trend=main.trend, rel=main.rel, pct=float(main.pct),
                          rebound=None if main.rebound is None else float(main.rebound),
                          amp=None if main.amp is None else float(main.amp)))
    return out


@dataclass(frozen=True)
class Stats:
    n: int
    cover: float | None        # 覆盖率：r7 < 坏情况 的占比（没给分位时为 None）
    r7_mean: float
    r14_median: float
    p_loss14: float


def stats(ss: list[Sample], quantile: float | None = None) -> Stats | None:
    if not ss:
        return None
    n = len(ss)
    return Stats(
        n=n,
        cover=None if quantile is None else sum(s.cover(quantile) for s in ss) / n,
        r7_mean=statistics.fmean(s.r7 for s in ss),
        r14_median=statistics.median(s.r14 for s in ss),
        p_loss14=sum(s.r14 < LOSS_LINE for s in ss) / n,
    )


def h1(ss: Iterable[Sample], split: date = H2_START) -> list[Sample]:
    return [s for s in ss if s.day < split]


def h2(ss: Iterable[Sample], split: date = H2_START) -> list[Sample]:
    return [s for s in ss if s.day >= split]


def by(ss: Iterable[Sample], key: Callable[[Sample], str]) -> dict[str, list[Sample]]:
    out: dict[str, list[Sample]] = {}
    for s in ss:
        out.setdefault(key(s), []).append(s)
    return out


@dataclass(frozen=True)
class Verdict:
    passed: bool
    reasons: list[str]          # 每条标准一句：过了的、没过的都写，报告里照抄


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _r1_lines(ss: list[Sample], quantile: float, *, halves: bool, split: date = H2_START) -> list[tuple[bool, str]]:
    lines: list[tuple[bool, str]] = []
    parts = {"H1": h1(ss, split), "H2": h2(ss, split)} if halves else {"H1": ss}
    for name, part in parts.items():
        st = stats(part, quantile)
        if st is None:
            lines.append((False, f"{name} 没有样本"))
            continue
        lines.append((st.cover <= R1_MAX, f"{name} 覆盖率 {_pct(st.cover)}（上限 {_pct(R1_MAX)}，n={st.n}）"))
    for g, part in sorted(by(ss, lambda s: s.group).items()):
        if len(part) < R1_GROUP_MIN_N:
            continue
        cover = stats(part, quantile).cover
        lines.append((cover <= R1_GROUP_MAX, f"大类 {g} 覆盖率 {_pct(cover)}（上限 {_pct(R1_GROUP_MAX)}，n={len(part)}）"))
    return lines


def check_r1(ss: list[Sample], quantile: float, split: date = H2_START) -> Verdict:
    lines = _r1_lines(ss, quantile, halves=True, split=split)
    return Verdict(passed=all(ok for ok, _ in lines), reasons=[("✓ " if ok else "✗ ") + t for ok, t in lines])


def best_quantile(ss: list[Sample], quantiles: Iterable[float] = QUANTILES,
                  split: date = H2_START) -> tuple[float | None, Verdict | None]:
    """R1 的兜底：只在 H1 上挑第一个（最宽的）满足标准的分位，再拿全部（含 H2）按 R1 验证。
    调用方只传比已失败的分位更严的那些。返回 (分位, 验证结果)；H1 上一个都不满足时是 (None, None)。"""
    first = h1(ss, split)
    for q in sorted(quantiles, reverse=True):
        if all(ok for ok, _ in _r1_lines(first, q, halves=False)):
            return q, check_r1(ss, q, split)
    return None, None


def check_r2(ss: list[Sample], split: date = H2_START) -> Verdict:
    return _check_flag(ss, lambda s: s.flagged, split)


def rebounded(s: Sample, threshold: float) -> bool:
    return s.rebound is not None and s.rebound >= threshold


def check_r3(ss: list[Sample], threshold: float, split: date = H2_START) -> Verdict:
    """R3：从近期低点反弹 ≥ threshold 的样本，14 天后是不是明显更差（标准同 R2，正式跑之前定死）。"""
    return _check_flag(ss, lambda s: rebounded(s, threshold), split)


def _check_flag(ss: list[Sample], flag_of: Callable[[Sample], bool], split: date) -> Verdict:
    lines: list[tuple[bool, str]] = []
    for name, part in (("H1", h1(ss, split)), ("H2", h2(ss, split))):
        base, flag = stats(part), stats([s for s in part if flag_of(s)])
        if base is None or flag is None:
            lines.append((False, f"{name} 没有被标出的样本"))
            continue
        lower = flag.r14_median < base.r14_median
        gap = flag.p_loss14 - base.p_loss14
        lines.append((lower, f"{name} r14 中位数：标出 {_pct(flag.r14_median)}，全部 {_pct(base.r14_median)}"))
        lines.append((gap >= R2_MIN_GAP, f"{name} P(r14<−5%)：标出 {_pct(flag.p_loss14)}，全部 {_pct(base.p_loss14)}，"
                                         f"差 {gap * 100:+.1f} 个百分点（至少 +{R2_MIN_GAP * 100:.0f}，n={flag.n}）"))
    return Verdict(passed=all(ok for ok, _ in lines), reasons=[("✓ " if ok else "✗ ") + t for ok, t in lines])
