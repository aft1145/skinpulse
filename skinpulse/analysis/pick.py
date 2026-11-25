"""价位三期：选品（纯逻辑，无 IO）。价位 spec §9b。

两件事：
- **圈池子**：从 SteamDT 目录（`item` 表）里按大类、武器、磨损、版本圈模板。价格区间要等取完价格才筛得了，
  所以池子按名字圈出来时就不能超过 `MAX_POOL`（`/price/batch` 每分钟 1 次、每次 100 个）。
- **给结论**：用一期的 `valuation.compute` 算描述和求购价上限 `cap`，再按顺序判「可以挂 / 观望 / 不建议 / 不评价」。

只用回测通过的东西（「7 天坏情况」，R1）。「在跌且跑输大盘」不进结论（R2 没过）。「难出手」是当下盘口太薄，
不是预测，当作前提条件。悠悠的最高求购来自 SteamDT，是所有子队列（含条件单）里最高的那个，偏保守。
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from skinpulse.analysis.category import GROUP_LABELS, categorize
from skinpulse.analysis.valuation import TICK, Valuation

MAX_POOL = 500
# 「可以挂」不要求排第 1（用户 2026-10-09）：上限够得着悠悠最高求购的 90% 就算。依据：16 条确认成交里
# 13 条发生在第 2～6 名；按排第 1 算，10-09 的 357 个模板里只有 4 个可以挂，按 0.9 算 35 个。
NEAR_TOP = Decimal("0.9")
# 默认不圈的大类（intent 原定：印花、挂件、武器箱、布章、涂鸦；「收藏品」在目录里归进「其他」）
DEFAULT_EXCLUDED = frozenset({"sticker", "charm", "container", "patch", "graffiti", "other"})
DEFAULT_GROUPS = frozenset(GROUP_LABELS) - DEFAULT_EXCLUDED

WEARS = {"Factory New": "fn", "Minimal Wear": "mw", "Field-Tested": "ft", "Well-Worn": "ww", "Battle-Scarred": "bs"}
WEAR_LABELS = {"fn": "崭新出厂", "mw": "略有磨损", "ft": "久经沙场", "ww": "破损不堪", "bs": "战痕累累", "none": "无磨损"}
VERSIONS = {"normal": "普通", "stattrak": "StatTrak™", "souvenir": "纪念品"}
_WEAR = re.compile(r"\((Factory New|Minimal Wear|Field-Tested|Well-Worn|Battle-Scarred)\)$")


@dataclass(frozen=True)
class ParsedName:
    group: str
    sub: str | None
    wear: str            # fn / mw / ft / ww / bs / none
    version: str         # normal / stattrak / souvenir


def parse_name(hash_name: str | None) -> ParsedName:
    s = (hash_name or "").strip()
    cat = categorize(s)
    m = _WEAR.search(s)
    version = "stattrak" if "StatTrak™" in s else "souvenir" if s.startswith("Souvenir ") else "normal"
    return ParsedName(cat.group, cat.sub, WEARS[m.group(1)] if m else "none", version)


class PoolError(ValueError):
    """圈池子的条件不对。消息是中文，直接给页面看。"""


def _set(d: Mapping, key: str, allowed: Iterable[str] | None) -> frozenset[str]:
    v = d.get(key) or []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise PoolError(f"{key} 要是字符串列表")
    if allowed is not None and (bad := set(v) - set(allowed)):
        raise PoolError(f"{key} 里有不认识的值：{', '.join(sorted(bad))}")
    return frozenset(v)


def _money(d: Mapping, key: str) -> Decimal | None:
    v = d.get(key)
    if v is None or v == "":
        return None
    try:
        out = Decimal(str(v))
    except Exception:  # noqa: BLE001  任何解析失败都是参数错
        raise PoolError(f"{key} 要是数字") from None
    if not out.is_finite() or out < 0:
        raise PoolError(f"{key} 不能是负数")
    return out


@dataclass(frozen=True)
class PoolFilter:
    """空集合 = 不限。`groups` 例外：空就是一个都不圈（页面默认勾 DEFAULT_GROUPS）。"""

    groups: frozenset[str] = DEFAULT_GROUPS
    subs: frozenset[str] = frozenset()
    wears: frozenset[str] = frozenset()
    versions: frozenset[str] = frozenset()
    price_min: Decimal | None = None
    price_max: Decimal | None = None

    @classmethod
    def from_dict(cls, d: Mapping | None) -> PoolFilter:
        if not isinstance(d, Mapping):
            raise PoolError("条件要是一个 JSON 对象")
        unknown = set(d) - {"groups", "subs", "wears", "versions", "price_min", "price_max"}
        if unknown:
            raise PoolError(f"不认识的条件：{', '.join(sorted(unknown))}")
        f = cls(groups=_set(d, "groups", GROUP_LABELS), subs=_set(d, "subs", None),
                wears=_set(d, "wears", WEAR_LABELS), versions=_set(d, "versions", VERSIONS),
                price_min=_money(d, "price_min"), price_max=_money(d, "price_max"))
        if not f.groups:
            raise PoolError("至少选一个大类")
        if f.price_min is not None and f.price_max is not None and f.price_min > f.price_max:
            raise PoolError("价格下限比上限还高")
        return f

    def to_dict(self) -> dict:
        return {"groups": sorted(self.groups), "subs": sorted(self.subs), "wears": sorted(self.wears),
                "versions": sorted(self.versions),
                "price_min": None if self.price_min is None else float(self.price_min),
                "price_max": None if self.price_max is None else float(self.price_max)}

    def matches(self, p: ParsedName) -> bool:
        return (p.group in self.groups
                and (not self.subs or p.sub in self.subs)
                and (not self.wears or p.wear in self.wears)
                and (not self.versions or p.version in self.versions))

    def price_ok(self, sell: Decimal | None) -> bool:
        """按悠悠在售最低筛。没有在售价的：设了区间就筛掉（看不出在不在区间里），没设就留着。"""
        if self.price_min is None and self.price_max is None:
            return True
        if sell is None or sell <= 0:
            return False
        return (self.price_min is None or sell >= self.price_min) and (self.price_max is None or sell <= self.price_max)


def select_pool(catalog: Iterable[tuple[int, str]], f: PoolFilter) -> list[int]:
    """按名字圈出模板号（升序、去重）。不截断：超过 MAX_POOL 由调用方拒绝，让用户收窄。"""
    return sorted({tid for tid, name in catalog if tid is not None and f.matches(parse_name(name))})


GO, WAIT, NO, NONE = "go", "wait", "no", "none"
VERDICT_LABELS = {GO: "可以挂", WAIT: "观望", NO: "不建议", NONE: "不评价"}


@dataclass(frozen=True)
class PickVerdict:
    kind: str
    rule: str
    bid: Decimal | None = None          # 建议出价（只有「可以挂」有）
    margin: Decimal | None = None       # 按建议出价成交、碰上坏情况的扣费差价
    first: bool = False                 # 建议出价是不是就能排第 1

    @property
    def label(self) -> str:
        return VERDICT_LABELS[self.kind]


def _m(x: Decimal) -> str:
    return f"{x:.2f}"


def verdict(v: Valuation, youpin_bid: Decimal | None, *, rebound: tuple[int, Decimal] | None = None) -> PickVerdict:
    """价位 spec §9b.3，按顺序判，命中一条就停。`youpin_bid` 是 SteamDT 给的悠悠最高求购（≤ 0 当作没有）。
    `rebound` 是 (窗口天数, 门槛)：从近期低点涨了门槛以上的判观望（R3，2026-11-23 回测通过）。"""
    if v.pct is None or v.q10 is None:
        return PickVerdict(NONE, "日线不足，算不出 7 天坏情况")
    if v.exit is None or v.cap is None:
        return PickVerdict(NONE, "没有在售价，算不出能卖多少")
    if v.cap <= 0:
        return PickVerdict(NO, f"上限 {_m(v.cap)}：怎么买都不够目标差价和每件最低利润")
    if v.liq == "难出手":
        return PickVerdict(NO, "难出手：在售少，或买卖价差超过 20%")
    if rebound is not None and v.rebound is not None and v.rebound >= rebound[1]:
        return PickVerdict(WAIT, f"从近 {rebound[0]} 天低点涨了 {v.rebound * 100:.0f}%，刚涨上来的先不追")
    bid = youpin_bid if youpin_bid is not None and youpin_bid > 0 else None
    if bid is None:
        return PickVerdict(WAIT, f"没有悠悠求购价，上限 {_m(v.cap)}")
    if v.cap < bid * NEAR_TOP:
        return PickVerdict(WAIT, f"上限 {_m(v.cap)} 不到悠悠最高求购 {_m(bid)} 的 90%")
    top = bid + TICK
    at = min(v.cap, top).quantize(TICK, rounding=ROUND_DOWN)
    margin = v.exit * (1 + v.q10) / at - 1
    where = "排第 1" if at == top else f"（最高求购 {_m(bid)} 之下）"
    return PickVerdict(GO, f"出 {_m(at)} {where}，坏情况后差价 {margin * 100:+.0f}%", bid=at,
                       margin=margin.quantize(Decimal("0.000001")), first=at == top)
