from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from skinpulse.adapters.steamdt.models import ItemPrices, PlatformPrice
from skinpulse.analysis.platform_gap import bid_gap, reference_bid, sell_gap, summarize


def _p(platform, sell=None, bid=None):
    return PlatformPrice(platform=platform, platform_item_id="1",
                         sell_price=None if sell is None else Decimal(str(sell)), sell_count=1,
                         bidding_price=None if bid is None else Decimal(str(bid)), bidding_count=1,
                         update_time=datetime.now(timezone.utc))


def _prices(*rows):
    return ItemPrices(market_hash_name="X", platforms=list(rows))


def test_sell_gap_compares_youpin_against_the_reference_median():
    g = sell_gap(_prices(_p("YOUPIN", sell=90), _p("BUFF", sell=100),
                         _p("C5", sell=110), _p("HALOSKINS", sell=120)))
    assert g is not None
    assert g.reference == Decimal("110")       # 100/110/120 的中位数
    assert g.ratio == Decimal("90") / Decimal("110")
    assert g.pct_diff < 0                       # 悠悠更低


def test_missing_youpin_price_yields_no_comparison_rather_than_a_zero():
    """悠悠那边没有数据时不能拿 0 顶上——那会算出「比参照低 100%」这种假结论。"""
    assert sell_gap(_prices(_p("YOUPIN"), _p("BUFF", sell=100))) is None


def test_no_reference_platform_with_data_yields_no_comparison():
    assert sell_gap(_prices(_p("YOUPIN", sell=90), _p("BUFF"), _p("C5"))) is None


def test_reference_only_counts_platforms_that_actually_had_data():
    """三家参照里只有一家有数据时，中位数就是那一家——但要如实记下用的是谁，
    否则报告里「参照 BUFF/C5/HALOSKINS」会把一个单点谎报成三家的共识。"""
    g = sell_gap(_prices(_p("YOUPIN", sell=90), _p("BUFF", sell=100), _p("C5"), _p("HALOSKINS")))
    assert g is not None and g.reference_platforms == ("BUFF",)


def test_bid_side_uses_its_own_reference_set():
    """实测只有 BUFF / C5 / YOUPIN 有求购价，HALOSKINS 一件都没有——所以买方参照不能沿用
    卖方那一组，否则大部分饰品都会因为「参照没数据」被丢掉。"""
    g = bid_gap(_prices(_p("YOUPIN", bid=80), _p("BUFF", bid=100), _p("C5", bid=100),
                        _p("HALOSKINS", sell=120)))
    assert g is not None and set(g.reference_platforms) == {"BUFF", "C5"}


def test_sell_and_bid_do_not_bleed_into_each_other():
    """同一件饰品上，卖方有数据而买方没有是常态（HALOSKINS 就是）。两侧必须各算各的。"""
    p = _prices(_p("YOUPIN", sell=90, bid=70), _p("BUFF", sell=100), _p("C5", sell=100, bid=80))
    s, b = sell_gap(p), bid_gap(p)
    assert s is not None and s.reference == Decimal("100")
    assert b is not None and b.reference == Decimal("80")   # 只有 C5 有求购价


def test_summary_uses_medians_not_means():
    """饰品价格从 1 块到几千都有，个别极端比值会把平均数拉到没有意义。"""
    gaps = [sell_gap(_prices(_p("YOUPIN", sell=s), _p("BUFF", sell=100))) for s in (95, 98, 100, 102, 10000)]
    summary = summarize([g for g in gaps if g])
    assert summary is not None and summary.n == 5
    assert summary.median_ratio == Decimal("1.0")       # 中位数不被那个 100x 的异常值带走
    assert summary.share_below == Decimal(2) / Decimal(5)


def test_summary_of_nothing_is_none_not_a_fake_zero():
    assert summarize([]) is None


def test_reference_bid_does_not_need_youpin_to_be_present():
    """告警那条路只要参照值。悠悠的数字我们自己从公开队列实时算，比 SteamDT 的缓存新鲜；
    要是沿用 bid_gap，SteamDT 恰好没有悠悠报价时整条信息会一起丢掉——而那跟参照在不在无关。"""
    ref = reference_bid(_prices(_p("BUFF", bid=100), _p("C5", bid=120)))
    assert ref == (Decimal(110), ("BUFF", "C5"))
    assert bid_gap(_prices(_p("BUFF", bid=100), _p("C5", bid=120))) is None   # 没有悠悠就算不出 gap


def test_reference_bid_reports_which_platforms_it_actually_used():
    ref = reference_bid(_prices(_p("BUFF", bid=100), _p("C5")))
    assert ref == (Decimal(100), ("BUFF",))


def test_reference_bid_is_none_when_nobody_quotes_a_bid():
    assert reference_bid(_prices(_p("BUFF", sell=100), _p("C5", sell=100))) is None
