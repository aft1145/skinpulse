from decimal import Decimal

import pytest

from skinpulse.adapters.youpin.models import PublicPurchaseOrder
from skinpulse.core.rank import (
    AGREED,
    DISAGREED,
    INCONCLUSIVE,
    QueueKey,
    compute_rank,
    normalize_condition,
    queue_key_from_str,
    queue_key_of,
    queue_key_str,
    verification_outcome,
)
from tests.conftest import load_fixture


def _public():
    rows = load_fixture("youpin/template_purchase_orders_ak_redline.json")["data"]["purchaseOrderResponseList"]
    return [PublicPurchaseOrder.from_api(r) for r in rows]


def _mk(purchase_no, user_id, price, *, template_id=1414, abrade_text="0.15-0.18", type_id=-1,
        special_style=None, fade_text=None, remaining_qty=1):
    """直接构造一行公开求购单，绕开 fixture，方便测试构造子队列边界场景。"""
    return PublicPurchaseOrder(
        purchase_no=purchase_no, user_id=user_id, user_name="u", price=Decimal(str(price)),
        remaining_qty=remaining_qty, abrade_text=abrade_text, type_id=type_id,
        special_style=special_style, fade_text=fade_text, is_rank_first=None, template_id=template_id,
    )


def test_normalize_condition():
    assert normalize_condition(None) == "ANY"
    assert normalize_condition("") == "ANY"
    assert normalize_condition("不限") == "ANY"
    assert normalize_condition(" 0.15 - 0.18 ") == "0.15-0.18"
    assert normalize_condition("0.15－0.18") == "0.15-0.18"
    assert normalize_condition("红宝石") == "红宝石"


def test_queue_key_and_str():
    k = queue_key_of(1414, "0.15-0.18", -1, None, None)
    assert k == QueueKey(1414, "0.15-0.18", -1, "ANY", "ANY")
    assert queue_key_str(k) == "1414|0.15-0.18|-1|ANY|ANY"


def test_rank_in_subqueue_ignores_other_wear_ranges():
    # 子队列 0.15-0.18：359(u6), 358(me), 357(u8) → 我排第 2；整条列表里我是第 7
    r = compute_rank(99999999, "TEST-MINE-1", queue_key_of(1414, "0.15-0.18", -1, None, None), _public(), 20)
    assert r.rank == 2 and r.queue_size == 3 and r.sample_size == 9
    assert r.top1_price == Decimal("359.00") and r.top2_price == Decimal("358.00") and r.top1_qty == 1


def test_rank_first_when_top_of_subqueue():
    pub = [o for o in _public() if o.purchase_no != "2026082300000006"]  # 去掉 359 的单
    r = compute_rank(99999999, "TEST-MINE-1", queue_key_of(1414, "0.15-0.18", -1, None, None), pub, 20)
    assert r.rank == 1


def test_same_price_keeps_api_order():
    pub = _public()
    # 把我的价改成 359（与 u6 同价，u6 在前）→ 我排第 2
    pub = [PublicPurchaseOrder(**{**o.__dict__, "price": Decimal("359")}) if o.purchase_no == "TEST-MINE-1" else o for o in pub]
    r = compute_rank(99999999, "TEST-MINE-1", queue_key_of(1414, "0.15-0.18", -1, None, None), pub, 20)
    assert r.rank == 2


def test_missing_vs_beyond():
    pub = [o for o in _public() if o.user_id != 99999999]
    r = compute_rank(99999999, "TEST-MINE-1", queue_key_of(1414, "0.15-0.18", -1, None, None), pub, 20)
    assert r.rank is None and r.missing is True and r.beyond is None
    r2 = compute_rank(99999999, "TEST-MINE-1", queue_key_of(1414, "0.15-0.18", -1, None, None), pub, visible_limit=8)
    assert r2.rank is None and r2.missing is False and r2.beyond == 8


def test_any_queue_separate_from_ranges():
    r = compute_rank(10000009, "2026082300000009", queue_key_of(1414, "不限", -1, None, None), _public(), 20)
    assert r.rank == 1 and r.queue_size == 1


def test_fallback_to_user_id_when_purchase_no_unknown():
    r = compute_rank(99999999, "UNKNOWN", queue_key_of(1414, "0.15-0.18", -1, None, None), _public(), 20)
    assert r.rank == 2


# ---------------------------------------------------------------------------
# Finding 1: "%" 折叠必须去掉所有位置的 %，否则同一褪色区间的两种写法会分裂成两个子队列
# ---------------------------------------------------------------------------

def test_normalize_condition_percent_folds_fully():
    # "80%-90%" / "80-90%" / "80-90" 是同一个褪色区间的三种写法，必须折叠成同一个 key
    assert normalize_condition("80%-90%") == normalize_condition("80-90%") == normalize_condition("80-90")
    assert normalize_condition("80%-90%") == "80-90"


def test_normalize_condition_case_insensitive_any_and_fullwidth_space():
    # "不限" 的同义写法大小写不敏感；区间里的全角空格（U+3000）也要和半角空格一样被折叠
    assert normalize_condition("Any") == "ANY"
    assert normalize_condition("0.15　-　0.18") == "0.15-0.18"


# ---------------------------------------------------------------------------
# final-review finding 3: en dash / wave dash / NBSP / tab must fold identically to
# their plain equivalents, or the same wear range rendered by two different
# YouPin endpoints (searchPurchaseOrderList vs getTemplatePurchaseOrderListPC) can
# produce two different queue_key strings and match zero rows in the sub-queue.
# ---------------------------------------------------------------------------

def test_normalize_condition_folds_en_dash_wave_dash_nbsp_and_tab():
    baseline = normalize_condition("0.15-0.18")
    assert normalize_condition("0.15–0.18") == baseline  # en dash U+2013
    assert normalize_condition("0.15〜0.18") == baseline  # wave dash U+301C
    assert normalize_condition("0.15 - 0.18") == baseline  # NBSP around the dash
    assert normalize_condition("0.15\t-\t0.18") == baseline  # tab around the dash


def test_normalize_condition_existing_folding_behaviors_still_hold():
    # Every behavior test_normalize_condition and friends already pin, re-asserted
    # here so a regression in the whitespace/dash rewrite can't silently break them.
    assert normalize_condition(None) == "ANY"
    assert normalize_condition("") == "ANY"
    assert normalize_condition("不限") == "ANY"
    assert normalize_condition("Any") == "ANY"
    assert normalize_condition("全部") == "ANY"
    assert normalize_condition(" 0.15 - 0.18 ") == "0.15-0.18"
    assert normalize_condition("0.15－0.18") == "0.15-0.18"
    assert normalize_condition("0.15～0.18") == "0.15-0.18"
    assert normalize_condition("0.15~0.18") == "0.15-0.18"
    assert normalize_condition("红宝石") == "红宝石"
    assert normalize_condition("80%-90%") == normalize_condition("80-90%") == normalize_condition("80-90") == "80-90"
    assert normalize_condition("AK-47 | 红线") == "AK-47红线"


# ---------------------------------------------------------------------------
# Finding 2: 空 purchase_no / user_id==0 是哨兵值，不能当成有效的匹配键
# ---------------------------------------------------------------------------

def test_empty_purchase_no_does_not_match_sentinel_row():
    key = queue_key_of(1414, "0.15-0.18", -1, None, None)
    pub = [_mk("", 42, 500), _mk("2026082300000099", 123, 400)]
    # my_purchase_no 为空：不应该命中 purchase_no 也是空字符串的哨兵行；
    # my_user_id 与队列里任何一行都不同，fallback 也找不到人 → not found
    r = compute_rank(my_user_id=99999999, my_purchase_no="", my_key=key, public=pub, visible_limit=20)
    assert r.rank is None
    assert r.missing is True


def test_zero_user_id_does_not_match_sentinel_row():
    key = queue_key_of(1414, "0.15-0.18", -1, None, None)
    pub = [_mk("SOME-NO", 0, 500), _mk("2026082300000099", 123, 400)]
    # my_purchase_no 未知（不匹配任何行），fallback 到 my_user_id==0：
    # 不应该命中 user_id 也是 0 的哨兵行
    r = compute_rank(my_user_id=0, my_purchase_no="UNKNOWN", my_key=key, public=pub, visible_limit=20)
    assert r.rank is None
    assert r.missing is True


# ---------------------------------------------------------------------------
# Finding 3: user_id fallback 命中多行时必须视为未找到，不能取价格最高的那单
# ---------------------------------------------------------------------------

def test_user_id_fallback_ambiguous_with_two_orders_reports_not_found():
    key = queue_key_of(1414, "0.15-0.18", -1, None, None)
    pub = [
        _mk("A-HIGH", 555, 500),   # 该用户在本子队列的最高价单
        _mk("A-LOW", 555, 100),    # 同一用户在本子队列的第二笔单
        _mk("OTHER", 777, 300),
    ]
    r = compute_rank(my_user_id=555, my_purchase_no="UNKNOWN-NO", my_key=key, public=pub, visible_limit=20)
    assert r.rank is None
    assert r.missing is True


# ---------------------------------------------------------------------------
# Finding 4: queue_key_str 的 "|" 分隔符必须被转义防护，并且要有可逆的解析函数
# ---------------------------------------------------------------------------

def test_queue_key_round_trip_and_pipe_safe():
    k = queue_key_of(1414, "0.15-0.18", -1, None, None)
    assert queue_key_from_str(queue_key_str(k)) == k

    # commodityName 这类字段里常见竖线（如 "AK-47 | 红线"）；normalize_condition 必须吞掉它，
    # 否则 queue_key_str 产出的字符串按 "|" split 回来会得到 6 段而不是 5 段，错配到别的队列
    k2 = queue_key_of(1414, "AK-47 | 红线", -1, None, None)
    s2 = queue_key_str(k2)
    assert s2.count("|") == 4
    assert queue_key_from_str(s2) == k2


def test_queue_key_from_str_raises_on_malformed_input():
    with pytest.raises(ValueError):
        queue_key_from_str("1414|0.15-0.18|-1|ANY")  # 少一个字段


# ---------------------------------------------------------------------------
# 覆盖率补漏 1：按 type_id / special_style 划分子队列（Doppler 风格队列）
# ---------------------------------------------------------------------------

def test_partition_by_type_id_and_special_style():
    key_ruby = queue_key_of(1414, "0.00-0.01", 108, "红宝石", None)
    key_pearl = queue_key_of(1414, "0.00-0.01", 107, "黑珍珠", None)
    assert key_ruby != key_pearl

    pub = [
        _mk("PEARL-HIGH", 1, 2000, abrade_text="0.00-0.01", type_id=107, special_style="黑珍珠"),
        _mk("RUBY-HIGH", 2, 1500, abrade_text="0.00-0.01", type_id=108, special_style="红宝石"),
        _mk("RUBY-MINE", 3, 1000, abrade_text="0.00-0.01", type_id=108, special_style="红宝石"),
        _mk("RUBY-LOW", 4, 500, abrade_text="0.00-0.01", type_id=108, special_style="红宝石"),
    ]
    r = compute_rank(my_user_id=3, my_purchase_no="RUBY-MINE", my_key=key_ruby, public=pub, visible_limit=20)
    # 只与同款（typeId=108/红宝石）竞争：黑珍珠那单价格更高，但不在同一子队列里，不影响排名
    assert r.rank == 2
    assert r.queue_size == 3
    assert r.top1_price == Decimal(1500)


# ---------------------------------------------------------------------------
# 覆盖率补漏 2：强化同价稳定排序的回归测试，让潜在的 tiebreaker 无所遁形
# ---------------------------------------------------------------------------

def test_stable_sort_survives_secondary_key_temptations():
    # 原 test_same_price_keeps_api_order 里，API 顺序在前的那行恰好 purchase_no/user_id
    # 也更小，就算加了按这两个键排序的 tiebreaker 也测不出来。这里反过来构造：
    # API 顺序在前的行，purchase_no（字典序）和 user_id（数值）都更大，
    # 这样任何新增的次级排序键都会把它排到后面，从而真正打破这个断言。
    key = queue_key_of(1414, "0.15-0.18", -1, None, None)
    pub = [
        _mk("Z-EARLIER-BIGGER-NO", 9999999, 300),  # API 顺序里在前，purchase_no/user_id 都更大
        _mk("A-LATER-SMALLER-NO", 1, 300),         # API 顺序里在后，purchase_no/user_id 都更小
    ]
    r = compute_rank(my_user_id=9999999, my_purchase_no="Z-EARLIER-BIGGER-NO", my_key=key, public=pub,
                      visible_limit=20)
    assert r.rank == 1


# ---- verification_outcome: 一致 / 不一致 / 无法判定 ----


def test_verification_outcome_agrees_when_both_ranks_known_and_equal():
    assert verification_outcome(4, 4, sample_size=20, visible_limit=20) == AGREED


def test_verification_outcome_disagrees_when_both_known_and_different():
    assert verification_outcome(5, 6, sample_size=7, visible_limit=20) == DISAGREED


def test_missing_from_a_full_page_is_inconclusive_not_disagreement():
    """公开队列这一页取满了（sample_size >= visible_limit）却没找到自己的单：说明它排在页外，
    我们无从核对。这是 2026-09-06 那批"不一致"里最多的一种，真实原因是视野盲区而不是悠悠算错。"""
    assert verification_outcome(5, None, sample_size=20, visible_limit=20) == INCONCLUSIVE


def test_missing_from_a_partial_page_is_a_real_disagreement():
    """整个队列都取回来了（没被截断）还是找不到自己的单，那就是真的对不上，必须报出来。"""
    assert verification_outcome(5, None, sample_size=7, visible_limit=20) == DISAGREED
    assert verification_outcome(5, None, sample_size=0, visible_limit=20) == DISAGREED


def test_unknown_reported_rank_never_counts_as_agreement():
    """悠悠没给排名时无从比对，不能算一致；页没满就说明本地这边是有结论的，算不一致。"""
    assert verification_outcome(None, 3, sample_size=7, visible_limit=20) == DISAGREED
    assert verification_outcome(None, None, sample_size=20, visible_limit=20) == INCONCLUSIVE
