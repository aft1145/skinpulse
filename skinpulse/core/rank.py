from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import NamedTuple

from skinpulse.adapters.youpin.models import PublicPurchaseOrder

_ANY = {"", "不限", "any", "全部"}
_WS_RE = re.compile(r"\s+")


def normalize_condition(value: str | None) -> str:
    if value is None:
        return "ANY"
    # queue_key is built from two different endpoints (searchPurchaseOrderList for our
    # own orders, getTemplatePurchaseOrderListPC for the public queue) - if either one
    # renders the same wear range with a different dash or whitespace character, the
    # two sides fail to match and the sub-queue filter finds zero rows. Fold every dash
    # variant we've seen (halfwidth "－", em dash "—", fullwidth tilde "～", ASCII tilde
    # "~", en dash, wave dash) to plain "-".
    v = (str(value).strip().replace("－", "-").replace("—", "-").replace("～", "-").replace("~", "-")
         .replace("–", "-").replace("〜", "-"))
    # \s+ covers the whole Unicode whitespace class in one pass (ASCII space, fullwidth
    # space U+3000, NBSP, tab, ...) rather than enumerating individual characters, so a
    # variant neither endpoint has needed yet still folds correctly. "%" is stripped
    # everywhere (not just the ends), or "80%-90%" / "80-90%" / "80-90" would be judged
    # three different keys, splitting one fade range into multiple sub-queues; "|" is
    # stripped too, because queue_key_str uses it as the field separator (see that
    # function's docstring).
    v = _WS_RE.sub("", v).replace("%", "").replace("|", "")
    return "ANY" if v.lower() in _ANY else v


class QueueKey(NamedTuple):
    template_id: int
    abrade: str
    type_id: int
    special: str
    fade: str


def queue_key_of(template_id: int, abrade_text: str | None, type_id: int | None,
                  special_style: str | None, fade_text: str | None) -> QueueKey:
    return QueueKey(int(template_id), normalize_condition(abrade_text),
                     int(type_id) if type_id is not None else -1,
                     normalize_condition(special_style), normalize_condition(fade_text))


def queue_key_str(k: QueueKey) -> str:
    # 加载点：这个 "|" 分隔格式是固定的，后续任务会持久化并按 "|" 切分还原。
    # normalize_condition 已经把每个分量里的 "|" 吞掉了，所以这里产出的字符串
    # 保证恰好有 4 个 "|"，切回来一定是 5 段。
    return f"{k.template_id}|{k.abrade}|{k.type_id}|{k.special}|{k.fade}"


def queue_key_from_str(s: str) -> QueueKey:
    """queue_key_str 的逆函数：把 "|" 分隔的字符串解析回 QueueKey。

    格式不对（不是恰好 5 段）或数值字段解析失败时抛 ValueError。
    """
    if s.startswith("buff:"):
        # BUFF 的队列（BUFF spec §4）：`buff:<goods_id>:<条件指纹>`。还原成一个只用来出标签的 QueueKey：
        # template_id 0，条件放在 abrade 那一格（「磨损 0.07-0.08」），其余是 ANY
        from skinpulse.core.buff_rank import label_of_fingerprint, split_queue_key
        _, fp = split_queue_key(s)
        label = label_of_fingerprint(fp)
        return QueueKey(0, "ANY" if label == "不限" else label, -1, "ANY", "ANY")
    parts = s.split("|")
    if len(parts) != 5:
        raise ValueError(f"invalid queue key string (expected 5 fields, got {len(parts)}): {s!r}")
    template_id_s, abrade, type_id_s, special, fade = parts
    try:
        template_id = int(template_id_s)
        type_id = int(type_id_s)
    except ValueError as e:
        raise ValueError(f"invalid queue key string: {s!r}") from e
    return QueueKey(template_id, abrade, type_id, special, fade)


AGREED = "agreed"
DISAGREED = "disagreed"
INCONCLUSIVE = "inconclusive"


def verification_outcome(reported_rank: int | None, computed_rank: int | None,
                          sample_size: int, visible_limit: int) -> str:
    """把一次 pass B 抽样核对判成三种结果之一，而不是"一致/不一致"两种。

    第三种结果是必要的：公开队列一页最多只返 `visible_limit` 行（未登录时翻页参数被忽略），
    自己的单排在这一页之外时 `compute_rank` 只能返回 `rank=None`。把这种"我们看不见"记成
    "不一致"，等于用悠悠没犯的错误去污染一致率——2026-09-06 那批 9 次不一致里有 5 次全部
    是同一张单（磨损"不限"的裹手，真实排名第 5，而它在整表里排到第 20 名开外）反复撞上这个
    盲区，看板上却显示成悠悠的 rank 有问题。

    判定依据只用 RankCheck 行里已有的字段，所以历史行也能照同一套规则重新解释：
    `sample_size` 就是 `compute_rank` 看到的 `len(public)`，它 >= `visible_limit` 意味着这一页
    被截断了、自己的单可能就在后面；反之整个队列都取回来了还找不到自己，那才是真的对不上
    （比如队列里根本没有这张单），值得报警。
    """
    if reported_rank is not None and computed_rank is not None and reported_rank == computed_rank:
        return AGREED
    if computed_rank is None and sample_size >= visible_limit:
        return INCONCLUSIVE
    return DISAGREED


@dataclass(frozen=True)
class RankResult:
    rank: int | None
    beyond: int | None
    missing: bool
    top1_price: Decimal | None
    top2_price: Decimal | None
    top1_qty: int | None
    sample_size: int
    queue_size: int


def compute_rank(my_user_id: int, my_purchase_no: str, my_key: QueueKey,
                  public: list[PublicPurchaseOrder], visible_limit: int) -> RankResult:
    queue = [o for o in public
             if queue_key_of(o.template_id, o.abrade_text, o.type_id, o.special_style, o.fade_text) == my_key]
    queue.sort(key=lambda o: o.price, reverse=True)  # 稳定排序：同价保持接口顺序
    idx = None
    # purchase_no=="" 和 user_id==0 是"字段缺失"的哨兵值（from_api 在字段缺失时的默认值），
    # 不是真实的求购单标识；空/零两侧一旦都退化成哨兵，就会误判成第一条命中的行。
    if my_purchase_no:
        idx = next((i for i, o in enumerate(queue) if o.purchase_no == my_purchase_no), None)
    if idx is None and my_user_id:
        matches = [i for i, o in enumerate(queue) if o.user_id == my_user_id]
        # fallback 只有在“恰好一条”命中时才可信：命中多条时无法区分是哪一单，
        # 若贸然取第一条（价格最高的那条）会把用户较低价的那单误报成更高的排名。
        if len(matches) == 1:
            idx = matches[0]
    top1 = queue[0] if queue else None
    top2 = queue[1] if len(queue) > 1 else None
    if idx is None:
        beyond = visible_limit if len(public) >= visible_limit else None
        return RankResult(None, beyond, beyond is None, top1.price if top1 else None,
                           top2.price if top2 else None, top1.remaining_qty if top1 else None,
                           len(public), len(queue))
    return RankResult(idx + 1, None, False, top1.price if top1 else None, top2.price if top2 else None,
                       top1.remaining_qty if top1 else None, len(public), len(queue))
