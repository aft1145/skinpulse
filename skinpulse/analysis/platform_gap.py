"""悠悠 vs 其它平台的价差分析（纯逻辑，无 IO）。

要回答的问题只有一个：**悠悠的价格相对同类平台是系统性偏低还是偏高，偏多少。**

这件事对本项目有直接后果。看板上的「市场价」取的是悠悠的在售最低价，你用它判断一张求购单
值不值得加价；如果悠悠整体就比别家低一截，那这个基准本身是偏的，你以为的「贴近市场」其实
偏保守。要知道偏多少，只能拿别的平台对一遍。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from statistics import median

from skinpulse.adapters.steamdt.models import ItemPrices

YOUPIN = "YOUPIN"

# 参照平台，按买卖两侧分开定——2026-09-23 在我们自己 12 件随机饰品上实测的覆盖率决定的：
#
#   在售价：C5 / HALOSKINS / BUFF / STEAM / YOUPIN 全部 12/12 有数据
#   求购价：只有 C5 / BUFF / YOUPIN 有，其余平台一件都没有
#
# **Steam 故意不算进在售参照**：它收 15% 手续费、结算在 Steam 钱包里出不来，和国内这几家
# 不是同一个市场，纳进来只会系统性抬高参照、把「悠悠偏低」这个结论夸大。想看 Steam 就单独看。
SELL_REFERENCE = ("BUFF", "C5", "HALOSKINS")
BID_REFERENCE = ("BUFF", "C5")


@dataclass(frozen=True)
class Gap:
    """一件饰品的一侧（卖方或买方）对比结果。`ratio` = 悠悠 / 参照中位数。

    ratio < 1 表示悠悠比同行低。买卖两侧的含义是相反的：卖方低意味着**在悠悠买更便宜**，
    买方低意味着**在悠悠卖更亏**。
    """

    market_hash_name: str
    youpin: Decimal
    reference: Decimal
    reference_platforms: tuple[str, ...]

    @property
    def ratio(self) -> Decimal:
        return self.youpin / self.reference

    @property
    def pct_diff(self) -> Decimal:
        """悠悠相对参照高/低百分之多少（负数 = 低）。"""
        return (self.ratio - 1) * 100


def reference_price(prices: ItemPrices, side: str,
                    reference: tuple[str, ...]) -> tuple[Decimal, tuple[str, ...]] | None:
    """参照平台在 `side`（"sell" / "bid"）上的价格中位数，以及实际用到了哪几家。

    和 `_gap` 分开是有原因的：告警那条路只要这个参照值，**不需要 SteamDT 那边的悠悠报价**
    ——悠悠的数字我们自己从公开队列实时算，比 SteamDT 的缓存新鲜得多（它本身就落后几分钟）。
    拿 `bid_gap` 去取参照，会在 SteamDT 恰好没有悠悠报价时整条信息一起丢掉，而那跟参照在不在
    完全无关。

    返回的平台名单必须和价格一起用：参照到底是几家的共识还是一家的报价，差别很大。
    """
    attr = "sell_price" if side == "sell" else "bidding_price"
    values, used = [], []
    for name in reference:
        p = prices.of(name)
        v = getattr(p, attr) if p else None
        if v is not None:
            values.append(v)
            used.append(name)
    if not values:
        return None
    return Decimal(median(values)), tuple(used)


def reference_bid(prices: ItemPrices, reference: tuple[str, ...] = BID_REFERENCE):
    return reference_price(prices, "bid", reference)


def _gap(prices: ItemPrices, side: str, reference: tuple[str, ...]) -> Gap | None:
    """取悠悠和参照平台在 `side`（"sell" / "bid"）上的价格，算出一个 Gap。

    任何一侧缺数据就返回 None——**不要**拿 0 或者别的默认值顶上。SteamDT 对没覆盖到的平台
    返回的价格就是 0，模型层已经把它转成 None（见 models._money）；在这里再默认一次，等于
    把「没有数据」算成「价格是零」，比没有结论糟得多。
    """
    attr = "sell_price" if side == "sell" else "bidding_price"
    yp = prices.of(YOUPIN)
    mine = getattr(yp, attr) if yp else None
    if mine is None:
        return None
    ref = reference_price(prices, side, reference)
    if ref is None:
        return None
    value, used = ref
    return Gap(market_hash_name=prices.market_hash_name, youpin=mine,
               reference=value, reference_platforms=used)


def sell_gap(prices: ItemPrices, reference: tuple[str, ...] = SELL_REFERENCE) -> Gap | None:
    return _gap(prices, "sell", reference)


def bid_gap(prices: ItemPrices, reference: tuple[str, ...] = BID_REFERENCE) -> Gap | None:
    return _gap(prices, "bid", reference)


@dataclass(frozen=True)
class Summary:
    """一批 Gap 的汇总。用**中位数**而不是平均数：饰品价格跨度从 1 块到几千，个别极端比值
    会把平均数拉到没有意义。"""

    n: int
    median_ratio: Decimal
    share_below: Decimal   # 悠悠低于参照的占比（0~1）
    p25_ratio: Decimal
    p75_ratio: Decimal

    @property
    def median_pct_diff(self) -> Decimal:
        return (self.median_ratio - 1) * 100


def summarize(gaps: list[Gap]) -> Summary | None:
    if not gaps:
        return None
    ratios = sorted(g.ratio for g in gaps)
    n = len(ratios)

    def q(frac: float) -> Decimal:
        # 最近秩次法，不插值：样本量小的时候插值只是把噪音修饰得好看一点。
        i = min(n - 1, max(0, round(frac * (n - 1))))
        return ratios[i]

    below = sum(1 for r in ratios if r < 1)
    return Summary(n=n, median_ratio=Decimal(median(ratios)),
                   share_below=Decimal(below) / Decimal(n),
                   p25_ratio=q(0.25), p75_ratio=q(0.75))
