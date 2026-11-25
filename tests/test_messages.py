import re
from dataclasses import replace
from decimal import Decimal

import pytest

from skinpulse.config import NtfySettings
from skinpulse.core.messages import OrderView, build_enter_top2_message, build_system_message, queue_label_of


def _view(rank=1, no="P1", queue_label="0.15-0.18", my_price=Decimal("358"), top1_price=Decimal("359")):
    return OrderView(purchase_no=no, commodity_name="AK-47 | 红线 (久经沙场)", template_id=1414,
                     queue_label=queue_label, my_price=my_price, rank=rank, top1_price=top1_price,
                     # top2_price/top1_qty/sell_min are never populated for a live order any more
                     # (see the comment on OrderView) and are never rendered - kept non-empty here
                     # only to prove the renderer ignores them, not because they matter.
                     top2_price=Decimal("358"), top1_qty=1, sell_min=Decimal("355.5"), remaining_qty=1)


def test_queue_label():
    assert queue_label_of("ANY", -1, "ANY", "ANY") == "不限"
    assert queue_label_of("0-0.01", 108, "红宝石", "ANY") == "0-0.01 / 红宝石"


def test_single_message():
    m = build_enter_top2_message([_view(rank=1)], NtfySettings())
    assert m.title == "求购第1名：AK-47 | 红线 (久经) [0.15-0.18]"
    assert m.priority == 4 and "trophy" in m.tags
    assert "我的价 358" in m.body
    assert m.click == "https://www.youpin898.com/market/goods-list?listType=20&templateId=1414&gameId=730"


def test_single_message_body_is_name_line_then_stats_line():
    """Requirement: the skin name must be on its own line, with the numbers on a
    following line, so a wrapped name on a phone can't be confused with the numbers."""
    m = build_enter_top2_message([_view(rank=2)], NtfySettings())
    lines = m.body.split("\n")
    assert len(lines) == 2
    assert lines[0] == "AK-47 | 红线 (久经) [0.15-0.18]"
    assert "第2名" in lines[1]


# Task 14 (ADR-0008): real-time alerts also use the short item name from the daily
# brief (Task 13's short_item_name) - both the single-order title and the body.
def test_single_message_uses_short_item_name_in_title_and_body():
    v = replace(_view(rank=1), commodity_name="沙漠之鹰（StatTrak™） | 后发制人 (崭新出厂)")
    m = build_enter_top2_message([v], NtfySettings())
    assert m.title == "求购第1名：沙漠之鹰(ST) | 后发制人 (崭新) [0.15-0.18]"
    assert "沙漠之鹰(ST) | 后发制人 (崭新)" in m.body
    assert "崭新出厂" not in m.title and "崭新出厂" not in m.body
    assert "StatTrak™" not in m.title and "StatTrak™" not in m.body


def test_grouped_message_uses_short_item_name_in_body():
    v = replace(_view(rank=2), commodity_name="沙漠之鹰（StatTrak™） | 后发制人 (崭新出厂)")
    m = build_enter_top2_message([v, _view(rank=1, no="P2")], NtfySettings())
    assert "沙漠之鹰(ST) | 后发制人 (崭新)" in m.body
    assert "崭新出厂" not in m.body


def test_grouped_message():
    m = build_enter_top2_message([_view(rank=2, no="P1"), _view(rank=1, no="P2")], NtfySettings())
    assert m.title == "2 张求购进入前 2" and m.priority == 4 and m.click is None
    # Requirement: one order visually separable from the next via a blank line
    # between blocks (not just a bare newline, which is what a wrapped name would
    # also produce on a phone).
    assert "\n\n" in m.body


# Task 14 (ADR-0008): the grouped title must reflect settings.alerts.enter_rank,
# not a hard-coded "前 2" - the owner is moving real-time alerts to rank 1 only.
def test_grouped_title_reflects_enter_rank_1():
    m = build_enter_top2_message([_view(rank=1, no="P1"), _view(rank=1, no="P2")], NtfySettings(), enter_rank=1)
    assert m.title == "2 张求购升到第 1 名"


def test_grouped_title_reflects_enter_rank_default_2():
    m = build_enter_top2_message([_view(rank=2, no="P1"), _view(rank=1, no="P2")], NtfySettings())
    assert m.title == "2 张求购进入前 2"


def test_grouped_title_reflects_other_enter_rank():
    m = build_enter_top2_message([_view(rank=3, no="P1"), _view(rank=2, no="P2")], NtfySettings(), enter_rank=3)
    assert m.title == "2 张求购进入前 3"


def test_grouped_message_renders_one_block_per_order_with_blank_line_between():
    v1, v2 = _view(rank=2, no="P1"), _view(rank=1, no="P2")
    m = build_enter_top2_message([v1, v2], NtfySettings())
    blocks = m.body.split("\n\n")
    assert len(blocks) == 2
    assert blocks[0].count("\n") == 1 and blocks[1].count("\n") == 1
    assert blocks[0] == build_enter_top2_message([v1], NtfySettings()).body
    assert blocks[1] == build_enter_top2_message([v2], NtfySettings()).body


# Finding 1: the published interface names this parameter `ntfy_settings`; callers
# passing it by keyword must not get a TypeError.
def test_functions_accept_ntfy_settings_as_keyword_argument():
    m1 = build_enter_top2_message([_view(rank=1)], ntfy_settings=NtfySettings())
    assert m1.title == "求购第1名：AK-47 | 红线 (久经) [0.15-0.18]"

    m2 = build_system_message("系统提示", "内容", ntfy_settings=NtfySettings())
    assert m2.title == "系统提示" and m2.body == "内容"


# Finding 2: an empty `views` list must raise a clear, actionable ValueError instead
# of the obscure `max() arg is an empty sequence` from the priority computation.
def test_empty_views_raises_clear_value_error():
    with pytest.raises(ValueError) as exc_info:
        build_enter_top2_message([], NtfySettings())
    message = str(exc_info.value)
    assert "build_enter_top2_message" in message
    assert "empty" in message.lower()


# Finding 3: rank=None must never render as the Python literal "None".
def test_rank_none_renders_placeholder_not_python_none():
    v = _view(rank=None)
    m = build_enter_top2_message([v], NtfySettings())
    assert "None" not in m.title
    assert "None" not in m.body
    assert "第?名" in m.title
    assert "第?名" in m.body


# Finding 4: an oversized grouped body must never get the whole batch rejected by
# ntfy's 4096-byte body cap. It should render as many whole order blocks as fit
# within a budget, then a final line reporting how many more were omitted.
def test_grouped_body_that_fits_has_no_omission_line():
    views = [_view(rank=2, no="P1"), _view(rank=1, no="P2")]
    m = build_enter_top2_message(views, NtfySettings())
    assert "另有" not in m.body
    assert m.body.count("\n\n") == len(views) - 1


def test_grouped_body_over_budget_is_capped_with_omission_line():
    def distinct_view(i):
        return OrderView(purchase_no=f"P{i}", commodity_name=f"Skin-{i:03d} | 红线 (久经沙场)", template_id=1414,
                         queue_label="0.15-0.18", my_price=Decimal("358"), rank=2, top1_price=Decimal("359"),
                         top2_price=Decimal("358"), top1_qty=1, sell_min=Decimal("355.5"), remaining_qty=1)

    views = [distinct_view(i) for i in range(50)]
    m = build_enter_top2_message(views, NtfySettings())

    body_bytes = m.body.encode("utf-8")
    assert len(body_bytes) <= 4000

    blocks = m.body.split("\n\n")
    last_block = blocks[-1]
    match = re.fullmatch(r"…另有 (\d+) 张未列出", last_block)
    assert match is not None, f"last block was not a well-formed omission line: {last_block!r}"
    omitted = int(match.group(1))

    rendered_blocks = blocks[:-1]
    assert omitted + len(rendered_blocks) == len(views)
    assert omitted > 0  # this group must actually exceed the budget

    # Every rendered block must be a *whole* order block, never a truncated partial one.
    for i, block in enumerate(rendered_blocks):
        expected = build_enter_top2_message([views[i]], NtfySettings()).body
        assert block == expected


# The redesigned body must never show the three fields that are structurally always
# empty since the rank/top-bid switched to YouPin's my-orders response: top2_price,
# top1_qty (rendered as the meaningless "×0"), and sell_min.
def test_dead_fields_never_rendered_single():
    m = build_enter_top2_message([_view(rank=2)], NtfySettings())
    # "第2 <price>" (no "名") was the old second-place-bid field; "第2名" (the rank
    # label for a rank-2 order) legitimately remains and must not be confused with it.
    assert "第2 " not in m.body
    assert "×" not in m.body
    assert "在售最低" not in m.body


def test_dead_fields_never_rendered_grouped():
    m = build_enter_top2_message([_view(rank=2, no="P1"), _view(rank=1, no="P2")], NtfySettings())
    assert "第2 " not in m.body
    assert "×" not in m.body
    assert "在售最低" not in m.body


# The gap to the leading bid is the number that tells the owner whether outbidding
# is worth it - it must be present, and rank 1 must read as "you are the top bid"
# rather than a gap of zero or a negative number.
def test_rank2_shows_gap_to_leader():
    v = _view(rank=2, my_price=Decimal("358"), top1_price=Decimal("359"))
    m = build_enter_top2_message([v], NtfySettings())
    assert "最高价 359" in m.body
    assert "差价 1" in m.body


def test_rank1_reads_as_top_bid_not_a_zero_or_negative_gap():
    v = _view(rank=1, my_price=Decimal("372"), top1_price=Decimal("372"))
    m = build_enter_top2_message([v], NtfySettings())
    assert "差价" not in m.body
    assert "最高价" in m.body  # stated as a fact about being on top, not subtracted
    assert "0" not in m.body.replace("0.15-0.18", "")  # no spurious zero gap


def test_gap_omitted_when_top1_price_missing():
    v = _view(rank=2, top1_price=None, queue_label="不限")
    m = build_enter_top2_message([v], NtfySettings())
    assert "差价" not in m.body
    assert "最高价" not in m.body
    # Exact match proves no placeholder dash is rendered for the missing top1_price.
    assert m.body == "AK-47 | 红线 (久经)\n第2名 | 我的价 358"


# The condition label is dropped when it is the unrestricted default, since it
# carries no information there, but shown when the order genuinely has a filter.
def test_condition_shown_only_when_restricted():
    restricted = build_enter_top2_message([_view(rank=2, queue_label="0.15-0.18")], NtfySettings())
    assert "[0.15-0.18]" in restricted.body

    unrestricted = build_enter_top2_message([_view(rank=2, queue_label="不限")], NtfySettings())
    assert "不限" not in unrestricted.body


# ---- 市场价：无人在售 / 还没查到，必须分得开 ----


def test_sell_label_distinguishes_nobody_selling_from_not_yet_checked():
    """两者在 sell_min 上都是 None，但对决策的意义相反：不知道 = 再等一轮；
    无人在售 = 排名第 2 也换不来成交。"""
    from skinpulse.core.messages import SELL_NONE_LISTED, SELL_UNKNOWN, sell_label

    assert sell_label(None, None) == SELL_UNKNOWN
    assert sell_label(None, 0) == SELL_NONE_LISTED
    assert sell_label(Decimal("64.49"), 3) == "64.49"
    # 深度已知但价格莫名缺失：宁可说"不知道"，也不要编一个数字
    assert sell_label(None, 3) == SELL_UNKNOWN


def test_alert_body_shows_market_price_once_known():
    v = _view(rank=2)
    body = build_enter_top2_message([replace(v, sell_min=Decimal("64.49"), sell_depth=3)], NtfySettings()).body
    assert "市场价 64.49" in body

    nobody = build_enter_top2_message([replace(v, sell_min=None, sell_depth=0)], NtfySettings()).body
    assert "市场价 无人在售" in nobody


def test_alert_body_omits_market_price_entirely_when_unknown():
    """还没轮到这张单刷新市场价时，整段省略，而不是推一个"市场价 —"给用户看——
    推送里的每个数字都应该是能据以行动的。"""
    body = build_enter_top2_message([_view(rank=2)], NtfySettings()).body
    assert "市场价" not in body



# ---- 触发时间 ----


def test_body_ends_with_the_trigger_time_in_local_time():
    """通道在路上耽搁多久都不影响这一行：它是排名被观察到的那一刻。
    实测 ntfy 上一条 00:51 的告警，十个小时后才被手机拉到。"""
    from datetime import datetime, timezone

    m = build_enter_top2_message([_view(rank=2)], NtfySettings(),
                                 triggered_at=datetime(2026, 9, 22, 16, 51, tzinfo=timezone.utc))
    assert m.body.splitlines()[-1] == "触发于 09-23 00:51"     # UTC 16:51 = 北京 00:51


def test_grouped_body_still_fits_with_the_trigger_line():
    """触发时间那一行要占预算：正文刚好顶满时不能被它挤爆 ntfy 的 4096 字节上限。"""
    from datetime import datetime, timezone

    views = [replace(_view(rank=2, no=f"P{i}"), commodity_name=f"Skin-{i:03d} | 红线 (久经沙场)")
             for i in range(80)]
    m = build_enter_top2_message(views, NtfySettings(), triggered_at=datetime(2026, 9, 22, 16, 51, tzinfo=timezone.utc))
    assert len(m.body.encode("utf-8")) <= 4000
    assert m.body.endswith("触发于 09-23 00:51")


def test_no_trigger_time_means_no_trigger_line():
    assert "触发于" not in build_enter_top2_message([_view(rank=2)], NtfySettings()).body


# ---- 成交推送 spec §5：交易中、求购成交 ----

from datetime import datetime, timedelta, timezone  # noqa: E402

from skinpulse.core.messages import TradeView, build_fill_message, build_in_trade_message  # noqa: E402

UTC = timezone.utc


def _tv(no="P1", name="AWP | 树蝰 (崭新出厂)", price="150", qty=1, queue="不限",
        at=datetime(2026, 10, 1, 9, 50, tzinfo=UTC), detected=None, exact=True, tid=2230):
    return TradeView(purchase_no=no, commodity_name=name, template_id=tid, queue_label=queue,
                     my_price=Decimal(price), qty=qty, at=at, detected_at=detected or at, exact=exact)


def test_in_trade_single():
    m = build_in_trade_message([_tv()], NtfySettings(), tz="Asia/Shanghai")
    assert m.title == "交易中：AWP | 树蝰 (崭新)"
    assert m.body == "有卖家卖给了这张求购，交易进行中\n我的价 150\n\n触发于 10-01 17:50"
    assert m.priority == NtfySettings().priority_rank1
    assert m.click and "2230" in m.click


def test_in_trade_single_with_condition():
    m = build_in_trade_message([_tv(queue="0.15-0.18")], NtfySettings(), tz="Asia/Shanghai")
    assert m.title == "交易中：AWP | 树蝰 (崭新) [0.15-0.18]"


def test_in_trade_grouped():
    m = build_in_trade_message([_tv(no="A"), _tv(no="B", name="CZ75 | 短趾雕 (崭新出厂)", price="5")],
                               NtfySettings(), tz="Asia/Shanghai")
    assert m.title == "2 张求购交易中" and m.click is None
    assert m.body == ("有卖家卖给了这些求购，交易进行中\n\nAWP | 树蝰 (崭新)\n我的价 150\n\n"
                      "CZ75 | 短趾雕 (崭新)\n我的价 5\n\n触发于 10-01 17:50")


def test_fill_single_on_time():
    filled = datetime(2026, 10, 2, 1, 55, 14, tzinfo=UTC)
    m = build_fill_message([_tv(name="SSG 08 | 渐变强酸 (崭新出厂)", price="3.9", at=filled,
                                detected=filled + timedelta(minutes=10))], NtfySettings(), tz="Asia/Shanghai")
    assert m.title == "求购成交：SSG 08 | 渐变强酸 (崭新)"
    assert m.body == "我的价 3.9 × 1\n成交于 10-02 09:55"


def test_fill_single_found_late_says_so():
    filled = datetime(2026, 10, 2, 1, 55, 14, tzinfo=UTC)
    m = build_fill_message([_tv(price="3.9", at=filled, detected=filled + timedelta(minutes=25))],
                           NtfySettings(), tz="Asia/Shanghai")
    assert m.body == "我的价 3.9 × 1\n成交于 10-02 09:55\n发现于 10-02 10:20，晚了 25 分钟"


def test_fill_late_by_hours():
    filled = datetime(2026, 10, 2, 1, 0, tzinfo=UTC)
    m = build_fill_message([_tv(at=filled, detected=filled + timedelta(hours=2, minutes=5))],
                           NtfySettings(), tz="Asia/Shanghai")
    assert m.body.endswith("晚了 2 小时 5 分钟")


def test_partial_fill_has_only_the_detection_time():
    seen = datetime(2026, 10, 2, 2, 20, tzinfo=UTC)
    m = build_fill_message([_tv(qty=2, at=seen, exact=False)], NtfySettings(), tz="Asia/Shanghai")
    assert m.body == "我的价 150 × 2\n发现于 10-02 10:20"


def test_fill_grouped_respects_the_byte_budget():
    views = [_tv(no=f"P{i}", name=f"Skin-{i:03d} | 红线 (久经沙场)") for i in range(120)]
    m = build_fill_message(views, NtfySettings(), tz="Asia/Shanghai")
    assert m.title == "120 张求购成交"
    assert len(m.body.encode("utf-8")) <= 4000
    assert re.fullmatch(r"…另有 \d+ 张未列出", m.body.split("\n\n")[-1])


# ---- 市价提醒（市价提醒 spec §3） ----

def _mv(**kw):
    from datetime import datetime, timezone

    from skinpulse.core.messages import MarketView
    base = dict(purchase_no="P", commodity_name="双持贝瑞塔 | 藏身处 (崭新出厂)", template_id=108625,
                my_price=Decimal("1.10"), rank=4, top=Decimal("1.30"), sell_min=Decimal("1.17"), sell_depth=10,
                flags=frozenset({"inverted", "near"}), margin=Decimal("0.023"))
    base.update(kw)
    return MarketView(**base), datetime(2026, 10, 15, 6, 58, tzinfo=timezone.utc)


def test_market_single_both():
    from skinpulse.core.messages import build_market_message
    v, at = _mv()
    m = build_market_message([v], NtfySettings(), checked_at=at, tz="Asia/Shanghai")
    assert m.title == "榜首高于在售：双持贝瑞塔 | 藏身处 (崭新)"
    # 金额照其它推送的写法去掉末尾的 0；「核对于」和正文之间空一行，同「触发于」
    assert m.body.splitlines() == ["我的价 1.1 · 第 4 名", "榜首 1.3 · 悠悠在售最低 1.17（10 件）",
                                   "榜首比在售还高，前面的单可能很快被卖掉，轮到你",
                                   "我的价是在售的 94%，成交后扣费差价约 +2%", "", "核对于 10-15 14:58"]
    assert m.click and "108625" in m.click


def test_market_single_near_only_and_grouped():
    from skinpulse.core.messages import build_market_message
    v, at = _mv(flags=frozenset({"near"}), rank=12, margin=None)
    m = build_market_message([v], NtfySettings(), checked_at=at, tz="Asia/Shanghai")
    assert m.title.startswith("贴近市价：") and "第 12 名" in m.body and "我的价是在售的 94%" in m.body and "扣费" not in m.body
    w, _ = _mv(purchase_no="Q", commodity_name="AK-47 | 二西莫夫 (略有磨损)", flags=frozenset({"inverted"}))
    g = build_market_message([v, w], NtfySettings(), checked_at=at, tz="Asia/Shanghai")
    assert g.title == "2 张求购接近成交价" and g.click is None and "AK-47 | 二西莫夫" in g.body and g.body.endswith("核对于 10-15 14:58")
    with pytest.raises(ValueError):
        build_market_message([], NtfySettings(), checked_at=at, tz="Asia/Shanghai")
