from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from skinpulse.adapters.ntfy.client import NtfyMessage
from skinpulse.analysis.brief import TopRow, TopTable
from skinpulse.config import NtfySettings


@dataclass
class OrderView:
    purchase_no: str
    commodity_name: str
    template_id: int
    queue_label: str
    my_price: Decimal
    rank: int | None
    top1_price: Decimal | None
    # top2_price, top1_qty and sell_min come from the old public-queue rank fetch.
    # Since the rank/top-bid switched to YouPin's own my-orders response (see
    # services/monitor.py order_view), YouPin never populates these three for a
    # live order, so messages.py and the dashboard no longer render them - the
    # only field they're ever seen with is `-`/`×0`. They stay on OrderView (and
    # on RankSnapshot) unremoved because a later milestone is expected to
    # repopulate `sell_min` from the on-sale endpoint.
    top2_price: Decimal | None
    top1_qty: int | None
    # sell_min 为空时，只有 sell_depth 能说明是"没人在卖"(0) 还是"还没查到"(None)，
    # 所以这两个字段必须一起读；渲染统一走 sell_label，不要各处自己写 `or '-'`。
    # sell_depth 带默认值排在最后，是为了让只关心排名的调用方（测试、旧代码）不必提供它。
    sell_min: Decimal | None
    remaining_qty: int
    sell_depth: int | None = None
    # 别的平台的最高求购价，和它的来源平台。两者必须一起读——不报来源就等于把一家的报价
    # 说成几家的共识。
    ref_bid: Decimal | None = None
    ref_bid_platforms: str | None = None
    # 多账号（多账号 spec §6）：启用的号 ≥ 2 时是这张单所属号的简称，名字前加「[简称] 」；一个号时为 None，文案不变
    account_label: str | None = None
    # BUFF 单（BUFF spec §7）：点开跳 BUFF 商品页；前面挡路的条件单张数（> 0 时正文多一行）
    url: str | None = None
    cond_ahead: int | None = None


def _acct(label: str | None) -> str:
    return f"[{label}] " if label else ""


def queue_label_of(abrade: str, type_id: int, special: str, fade: str) -> str:
    parts = [p for p in (abrade, special, fade) if p and p != "ANY"]
    return " / ".join(parts) if parts else "不限"


def _fmt(d: Decimal) -> str:
    return f"{d.normalize():f}"


def _rank_str(rank: int | None) -> str:
    return "?" if rank is None else str(rank)


SELL_UNKNOWN = "—"
SELL_NONE_LISTED = "无人在售"


def sell_label(sell_min: Decimal | None, sell_depth: int | None) -> str:
    """把市场价渲染成一句人话，并且**不把"没人在卖"和"不知道"混为一谈**。

    - `sell_depth` 为 None：这张单还没轮到市场价刷新，或者上次查失败了 -> "—"（不知道）
    - `sell_depth == 0`：查过了，市场上一件在售都没有 -> "无人在售"（一个确定的结论）
    - 否则：最低在售价

    这两者对决策的意义完全相反：不知道意味着"再等一轮看看"，无人在售意味着"排名第 2 也
    换不来成交，这张单的排名暂时没有意义"。真实遇到过：SSG 08（StatTrak™）| 巨铁
    (破损不堪) 连查三次都是 0 件在售，而同一皮肤换个磨损有人挂——所以也不能拿别的磨损
    的价格顶上来充数。"""
    if sell_depth is None:
        return SELL_UNKNOWN
    if sell_depth == 0:
        return SELL_NONE_LISTED
    return SELL_UNKNOWN if sell_min is None else _fmt(sell_min)


def ref_bid_pct(v: OrderView) -> Decimal | None:
    """别处买盘比悠悠榜首高/低百分之多少（负数 = 别处更低）。两边任一缺数据就是 None。

    只和**悠悠榜首**比，不和自己的出价比：两者都是「该平台买方的最高出价」，是同向的数字；
    拿别处买盘去比自己的出价，比出来的东西含义完全不同，混用会把结论读反。
    """
    if v.ref_bid is None or v.top1_price is None or v.top1_price <= 0:
        return None
    return (v.ref_bid / v.top1_price - 1) * 100


def cross_platform_note(v: OrderView) -> str:
    """一句「别处买盘」：别的平台的最高求购价，以及它比悠悠榜首高/低多少。

    为什么值得占推送里的一行：2026-09-23 对 425 个模板全量对账，**悠悠的求购价比 BUFF/C5
    中位数低 9.1%**，79% 的饰品都低。所以「在悠悠排进前 2」这件事本身不等于「这件东西抢手」
    ——真实买盘可能在别的平台。看到「别处买盘 68（BUFF/C5，高 31%）」就能当场判断：悠悠这个
    队列是不是整体出价偏低，自己这个名次到底值多少。

    没有参照数据就整段省略，不渲染占位符——推送里的每个数字都应该是能据以行动的。
    比值只在两边都有值时才算：拿榜首价和别处买盘比，是同向的（都是「买方最高出价」），
    而和自己的出价比不是，混用会把结论读反。
    """
    if v.ref_bid is None:
        return ""
    src = v.ref_bid_platforms or "参照"
    pct = ref_bid_pct(v)
    if pct is None:
        return f"别处买盘 {_fmt(v.ref_bid)}（{src}）"
    word = "高" if pct >= 0 else "低"
    return f"别处买盘 {_fmt(v.ref_bid)}（{src}，比悠悠榜首{word} {abs(pct):.0f}%）"


def _block(v: OrderView) -> str:
    """Render one order as a two-line block: the skin name (with its condition
    only when it is a real filter, not the unrestricted default) on its own
    line, then the decision-relevant numbers on the next - rank, own price,
    and however far behind the leading bid we are. That gap is the number
    that tells the owner whether outbidding is worth it, so it leads over the
    now-always-empty top2/qty fields, which are simply left out.

    市场价（在售最低价）跟在后面，但只在**知道**的时候才出现：不知道就整段省略，
    而不是渲染一个破折号占位——一条推送里的每个数字都应该是能据以行动的。
    """
    short_name = _acct(v.account_label) + short_item_name(v.commodity_name)
    name = short_name if v.queue_label == "不限" else f"{short_name} [{v.queue_label}]"
    rank_str = _rank_str(v.rank)
    if v.rank == 1:
        # Being rank 1 means we *are* the leading bid - a "gap" of 0 (or the
        # occasional negative number from a stale top1_price) would misread as
        # a problem, so say it in words instead of doing the subtraction.
        stats = f"第1名（最高价）| 我的价 {_fmt(v.my_price)}"
    elif v.top1_price is not None:
        gap = v.top1_price - v.my_price
        stats = (f"第{rank_str}名 | 我的价 {_fmt(v.my_price)} | "
                 f"最高价 {_fmt(v.top1_price)} | 差价 {_fmt(gap)}")
    else:
        # top1_price is unset (shouldn't happen for a live order, but guard
        # anyway) - omit the gap rather than render a placeholder dash.
        stats = f"第{rank_str}名 | 我的价 {_fmt(v.my_price)}"
    if v.sell_depth is not None:
        stats += f" | 市场价 {sell_label(v.sell_min, v.sell_depth)}"
    cross = cross_platform_note(v)
    if cross:
        stats += f" | {cross}"
    if v.cond_ahead:
        stats += f"\n前面还有 {v.cond_ahead} 张条件单"
    return f"{name}\n{stats}"


_GROUPED_BODY_BYTE_BUDGET = 4000
_BLOCK_SEP = "\n\n"


def _byte_len(s: str) -> int:
    return len(s.encode("utf-8"))


def _build_grouped_body(views: list[OrderView], budget: int = _GROUPED_BODY_BYTE_BUDGET) -> str:
    """Render whole order blocks, separated by a blank line, up to a UTF-8 byte
    budget - never a partial block.

    ntfy caps a message body at 4096 bytes; with 50+ concurrent orders a broad
    market move can produce more blocks than fit. Rather than let `publish`
    reject the whole batch (leaving the user with nothing for that round), we
    render as many complete blocks as fit and summarize the rest in a final
    line, backing off further blocks if needed so that summary line itself
    always fits too.
    """
    return _fit_blocks([_block(v) for v in views], budget)


def _fit_blocks(rendered: list[str], budget: int) -> str:
    """把已经渲染好的每张单的文本块按字节预算拼起来，放不下的合成「…另有 N 张未列出」（见上）。"""
    sep_len = _byte_len(_BLOCK_SEP)
    blocks: list[str] = []
    used = 0
    for block in rendered:
        added = _byte_len(block) + (sep_len if blocks else 0)
        if used + added > budget:
            break
        blocks.append(block)
        used += added

    omitted = len(rendered) - len(blocks)
    if omitted == 0:
        return _BLOCK_SEP.join(blocks)

    while True:
        omission_line = f"…另有 {omitted} 张未列出"
        added = _byte_len(omission_line) + (sep_len if blocks else 0)
        if used + added <= budget or not blocks:
            break
        removed = blocks.pop()
        sep = sep_len if blocks else 0
        used -= _byte_len(removed) + sep
        omitted += 1

    blocks.append(omission_line)
    return _BLOCK_SEP.join(blocks)


def _trigger_line(triggered_at: datetime | None, tz: str) -> str | None:
    """「触发于 09-23 00:51」——告警真正发生的时间，写进正文最后一行。

    为什么不靠推送通道自带的时间：2026-09-23 查实，ntfy 那条通道上消息要等手机 App 被打开
    才补拉，你看到它的时刻可能比它发生晚十个小时。不管走哪条通道、在路上耽搁了多久，正文
    里这一行都是排名被观察到的那一刻。按 settings.timezone 显示，不用 UTC。
    """
    if triggered_at is None:
        return None
    return "触发于 " + triggered_at.astimezone(ZoneInfo(tz)).strftime("%m-%d %H:%M")


def build_enter_top2_message(views: list[OrderView], ntfy_settings: NtfySettings, *,
                             triggered_at: datetime | None = None,
                             tz: str = "Asia/Shanghai",
                             enter_rank: int = 2) -> NtfyMessage:
    if not views:
        raise ValueError("build_enter_top2_message: views must not be empty")
    footer = _trigger_line(triggered_at, tz)
    pr = {1: ntfy_settings.priority_rank1, 2: ntfy_settings.priority_rank2}
    priority = max(pr.get(v.rank or 2, ntfy_settings.priority_rank2) for v in views)
    tags = ["trophy"] if any(v.rank == 1 for v in views) else ["bell"]
    if len(views) == 1:
        v = views[0]
        title = f"求购第{_rank_str(v.rank)}名：{_acct(v.account_label)}{short_item_name(v.commodity_name)} [{v.queue_label}]"
        return NtfyMessage(title=title,
                           body=_with_footer(_block(v), footer),
                           priority=priority, tags=tags,
                           click=v.url or ntfy_settings.click_url_template.format(template_id=v.template_id))
    # 触发时间那一行要占预算：先给它让出位置，免得正文刚好顶满 4096 字节时被它挤爆。
    reserve = _byte_len(_BLOCK_SEP + footer) if footer else 0
    group_title = f"{len(views)} 张求购升到第 1 名" if enter_rank == 1 else f"{len(views)} 张求购进入前 {enter_rank}"
    return NtfyMessage(title=group_title,
                       body=_with_footer(_build_grouped_body(views, budget=_GROUPED_BODY_BYTE_BUDGET - reserve),
                                         footer),
                       priority=priority, tags=tags, click=None)


def _with_footer(body: str, footer: str | None) -> str:
    return body if not footer else body + _BLOCK_SEP + footer


# ---- 交易中、求购成交（成交推送 spec §5） ----

# 发现比成交晚这么久以上（比如 Mac 睡着了），正文里说一声。正常一轮最多隔 10 分钟（夜间），
# 2026-10-02 SSG 那次 Mac 睡着晚了 25 分钟，要能标出来
LATE_NOTE_AFTER = timedelta(minutes=20)


@dataclass
class TradeView:
    """一条「交易中」或「求购成交」推送里的一张单。at：交易中是开始时刻；成交是成交时间
    （exact=True，悠悠「已完成」列表给的）或我们发现的时间（exact=False，部分成交）。"""
    purchase_no: str
    commodity_name: str
    template_id: int
    queue_label: str
    my_price: Decimal
    qty: int
    at: datetime
    detected_at: datetime
    exact: bool = True
    account_label: str | None = None
    url: str | None = None          # BUFF 单：BUFF 商品页（BUFF spec §7）


def _local(t: datetime, tz: str) -> str:
    return t.astimezone(ZoneInfo(tz)).strftime("%m-%d %H:%M")


def _trade_name(v: TradeView) -> str:
    name = _acct(v.account_label) + short_item_name(v.commodity_name)
    return name if v.queue_label == "不限" else f"{name} [{v.queue_label}]"


def _span(d: timedelta) -> str:
    minutes = int(d.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} 分钟"
    h, m = divmod(minutes, 60)
    return f"{h} 小时" + (f" {m} 分钟" if m else "")


def _fill_lines(v: TradeView, tz: str) -> list[str]:
    lines = [f"我的价 {_fmt(v.my_price)} × {v.qty}"]
    if not v.exact:
        return lines + [f"发现于 {_local(v.detected_at, tz)}"]
    lines.append(f"成交于 {_local(v.at, tz)}")
    late = v.detected_at - v.at
    if late >= LATE_NOTE_AFTER:
        lines.append(f"发现于 {_local(v.detected_at, tz)}，晚了 {_span(late)}")
    return lines


def _trade_message(title_one: str, title_many: str, views: list[TradeView], ntfy_settings: NtfySettings,
                   blocks: list[str], *, head: str | None, footer: str | None, single_body: str) -> NtfyMessage:
    common = {"priority": ntfy_settings.priority_rank1, "tags": ["trophy"]}
    if len(views) == 1:
        v = views[0]
        return NtfyMessage(title=f"{title_one}：{_trade_name(v)}", body=_with_footer(single_body, footer),
                           click=v.url or ntfy_settings.click_url_template.format(template_id=v.template_id), **common)
    reserve = sum(_byte_len(x + _BLOCK_SEP) for x in (head, footer) if x)
    body = _fit_blocks(blocks, _GROUPED_BODY_BYTE_BUDGET - reserve)
    if head:
        body = head + _BLOCK_SEP + body
    return NtfyMessage(title=title_many.format(n=len(views)), body=_with_footer(body, footer), click=None, **common)


def build_in_trade_message(views: list[TradeView], ntfy_settings: NtfySettings, *, tz: str) -> NtfyMessage:
    """悠悠显示「交易中」（rank = "*"）：有卖家卖给了这张求购。触发时间取最早的那一段开始时刻。"""
    if not views:
        raise ValueError("build_in_trade_message: views must not be empty")
    footer = _trigger_line(min(v.at for v in views), tz)
    blocks = [f"{_trade_name(v)}\n我的价 {_fmt(v.my_price)}" for v in views]
    v = views[0]
    return _trade_message("交易中", "{n} 张求购交易中", views, ntfy_settings, blocks,
                          head="有卖家卖给了这些求购，交易进行中", footer=footer,
                          single_body=f"有卖家卖给了这张求购，交易进行中\n我的价 {_fmt(v.my_price)}")


def build_fill_message(views: list[TradeView], ntfy_settings: NtfySettings, *, tz: str) -> NtfyMessage:
    """求购成交：没赶上「交易中」的成交（太快、Mac 睡着了）才走这条；时间写在每张单里。"""
    if not views:
        raise ValueError("build_fill_message: views must not be empty")
    blocks = ["\n".join([_trade_name(v), *_fill_lines(v, tz)]) for v in views]
    return _trade_message("求购成交", "{n} 张求购成交", views, ntfy_settings, blocks, head=None, footer=None,
                          single_body="\n".join(_fill_lines(views[0], tz)))


# ---- 市价提醒（市价提醒 spec §3） ----

@dataclass
class MarketView:
    """一条市价提醒里的一张单。价格都是推送前刚核对过的实时值；margin 是按我的价成交、按悠悠在售最低
    扣掉让价、手续费、提现后的差价（只在「贴近市价」时写）。"""
    purchase_no: str
    commodity_name: str
    template_id: int
    my_price: Decimal
    rank: int | None
    top: Decimal | None
    sell_min: Decimal
    sell_depth: int
    flags: frozenset[str]
    margin: Decimal | None = None
    account_label: str | None = None
    url: str | None = None


def _market_lines(v: MarketView) -> list[str]:
    lines = [f"我的价 {_fmt(v.my_price)}" + (f" · 第 {v.rank} 名" if v.rank is not None else ""),
             (f"榜首 {_fmt(v.top)} · " if v.top is not None else "") + f"悠悠在售最低 {_fmt(v.sell_min)}（{v.sell_depth} 件）"]
    if "inverted" in v.flags:
        lines.append("榜首比在售还高，前面的单可能很快被卖掉，轮到你")
    if "near" in v.flags:
        pct = int((v.my_price / v.sell_min * 100).to_integral_value())
        tail = "" if v.margin is None else f"，成交后扣费差价约 {v.margin * 100:+.0f}%"
        lines.append(f"我的价是在售的 {pct}%{tail}")
    return lines


def build_market_message(views: list[MarketView], ntfy_settings: NtfySettings, *, checked_at: datetime,
                         tz: str) -> NtfyMessage:
    if not views:
        raise ValueError("build_market_message: views must not be empty")
    footer = f"核对于 {checked_at.astimezone(ZoneInfo(tz)).strftime('%m-%d %H:%M')}"
    common = {"priority": ntfy_settings.priority_rank1, "tags": ["warning"]}
    if len(views) == 1:
        v = views[0]
        kind = "榜首高于在售" if "inverted" in v.flags else "贴近市价"
        return NtfyMessage(title=f"{kind}：{_acct(v.account_label)}{short_item_name(v.commodity_name)}",
                           body=_with_footer("\n".join(_market_lines(v)), footer),
                           click=v.url or ntfy_settings.click_url_template.format(template_id=v.template_id), **common)
    blocks = ["\n".join([_acct(v.account_label) + short_item_name(v.commodity_name), *_market_lines(v)]) for v in views]
    body = _fit_blocks(blocks, _GROUPED_BODY_BYTE_BUDGET - _byte_len(footer + _BLOCK_SEP))
    return NtfyMessage(title=f"{len(views)} 张求购接近成交价", body=_with_footer(body, footer), click=None, **common)


def build_system_message(title: str, body: str, ntfy_settings: NtfySettings) -> NtfyMessage:
    return NtfyMessage(title=title, body=body, priority=ntfy_settings.priority_system, tags=["warning"])


# ---- 饰品名简写（每日简报；Task 14 起实时告警也用） ----

_WEAR_SHORT = {"崭新出厂": "崭新", "略有磨损": "略磨", "久经沙场": "久经", "破损不堪": "破损", "战痕累累": "战痕"}


def short_item_name(name: str) -> str:
    """推送里的饰品名简写（用户 2026-09-24 选定）：全角括号换英文括号，`StatTrak™` 写 `ST`，
    磨损写两个字。`沙漠之鹰（StatTrak™） | 后发制人 (崭新出厂)` -> `沙漠之鹰(ST) | 后发制人 (崭新)`。"""
    s = name.replace("（", "(").replace("）", ")").replace("StatTrak™", "ST")
    for full, short in _WEAR_SHORT.items():
        s = s.replace(full, short)
    return s


# ---- 每日简报（M3a，spec §7a.5） ----

BRIEF_BYTE_BUDGET = 4000          # 纯文本正文（ntfy 上限 4096）
BRIEF_HTML_BYTE_BUDGET = 16000    # PushPlus html 正文
BRIEF_PRIORITY = 2


def _price_or_unknown(v: Decimal | None) -> str:
    # 价格 0 = 没有数据（SteamDT 的 0 已在适配层转成 None，这里再兜一次），绝不显示 0。
    return SELL_UNKNOWN if v is None or v <= 0 else _fmt(v)


def _brief_top1(r: TopRow) -> str:
    return "我" if r.rank == 1 else _price_or_unknown(r.top1_price)


def _brief_changes(r: TopRow) -> str:
    parts = []
    if r.is_new:
        parts.append("🆕新进")
    if r.rank_changed:
        parts.append(f"名次 {r.open_rank}→{r.rank}")
    if r.top1_changed:
        parts.append(f"榜首 {_fmt(r.open_top1)}→{_fmt(r.top1_price)}")
    return "，".join(parts)


def _brief_text_line(r: TopRow) -> str:
    cells = [f"第{r.rank}名", short_item_name(r.name), f"求购 {_fmt(r.my_price)}", f"榜首 {_brief_top1(r)}",
             f"在售 {_price_or_unknown(r.sell_price)}"]
    change = _brief_changes(r)
    return " | ".join(cells + ([change] if change else []))


def _brief_html_row(r: TopRow) -> str:
    cells = [str(r.rank), short_item_name(r.name), _fmt(r.my_price), _brief_top1(r),
             _price_or_unknown(r.sell_price), _brief_changes(r)]
    style = ' style="font-weight:bold"' if r.changed else ""
    return f"<tr{style}>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr>"


_BRIEF_HTML_HEAD = ('<table border="1" cellspacing="0" cellpadding="4" style="border-collapse:collapse">'
                    + "<tr>" + "".join(f"<th>{c}</th>" for c in ("名次", "饰品", "求购", "榜首", "在售", "变动"))
                    + "</tr>")
_BRIEF_HTML_TAIL = "</table>"


def _omitted_note(n: int) -> str:
    return f"另有 {n} 张未列出，见看板 /brief"


def _fit_rows(items: list[str], fixed: int, sep: str, budget: int, note_len) -> tuple[list[str], int]:
    """按整行放进 budget 字节：`fixed` 是表头表尾等固定开销，`note_len(n)` 是「另有 n 张未列出」
    那一行（含分隔符）的字节数。全放得下就不留那一行；放不下时给它留足位置。"""
    sep_len = _byte_len(sep)
    total = fixed + sum(_byte_len(i) for i in items) + sep_len * max(len(items) - 1, 0)
    if total <= budget:
        return items, 0
    reserve = note_len(len(items))        # 按最多的条数留位置：位数只会更少
    kept: list[str] = []
    used = fixed
    for item in items:
        added = _byte_len(item) + (sep_len if kept else 0)
        if used + added + reserve > budget:
            break
        kept.append(item)
        used += added
    return kept, len(items) - len(kept)


def _brief_text_body(rows: list[TopRow], budget: int, sell_note: str | None) -> str:
    # 在售脚注是固定开销，和截断行一样先把位置留出来：截得再狠它也在最后一行。
    fixed = _byte_len("\n" + sell_note) if sell_note else 0
    lines, omitted = _fit_rows([_brief_text_line(r) for r in rows], fixed, "\n", budget,
                               lambda n: _byte_len("\n" + _omitted_note(n)))
    tail = ([_omitted_note(omitted)] if omitted else []) + ([sell_note] if sell_note else [])
    return "\n".join(lines + tail)


def _brief_html_body(rows: list[TopRow], budget: int, sell_note: str | None) -> str:
    foot = f"<p>{html.escape(sell_note)}</p>" if sell_note else ""
    fixed = _byte_len(_BRIEF_HTML_HEAD + _BRIEF_HTML_TAIL + foot)
    trs, omitted = _fit_rows([_brief_html_row(r) for r in rows], fixed, "", budget,
                             lambda n: _byte_len(f"<p>{html.escape(_omitted_note(n))}</p>"))
    note = f"<p>{html.escape(_omitted_note(omitted))}</p>" if omitted else ""
    return _BRIEF_HTML_HEAD + "".join(trs) + _BRIEF_HTML_TAIL + note + foot


def build_brief_message(day: date, table: TopTable, *, top_rank: int, sell_note: str | None = None,
                        budget: int = BRIEF_BYTE_BUDGET, html_budget: int = BRIEF_HTML_BYTE_BUDGET) -> NtfyMessage:
    """每日简报：前 top_rank 名表。`body` 是纯文本（给 ntfy，也存进 brief_log.body），`html` 是给
    PushPlus 的表格（所有文本都 html.escape 过）。两份各自按整行截到自己的字节预算，截掉的张数
    写在表后；存档用 budget 足够大的全文版本。价格缺失一律写「—」，绝不填 0。

    `sell_note` 是在售列的来源脚注（`services.brief` 算好传进来，如「在售：09-22 23:50 悠悠价格快照」），
    两份正文都放在最后一行，字节预算里先给它留位置。"""
    rows = table.rows
    return NtfyMessage(
        title=f"SkinPulse 简报 {day:%m-%d}：前 {top_rank} 共 {len(rows)} 张，{len(table.changed_rows)} 张有变动",
        body=_brief_text_body(rows, budget, sell_note), priority=BRIEF_PRIORITY,
        html=_brief_html_body(rows, html_budget, sell_note))


def build_quiet_brief_message(day: date, quiet_days: int, top_rank: int) -> NtfyMessage:
    # 规则是「前 N 名表没有任何变动」（新进 / 名次 / 榜首价），不只是「没有新进」。
    return NtfyMessage(title=f"SkinPulse：近 {quiet_days} 天前 {top_rank} 名单没有变动",
                       body=f"近 {quiet_days} 天前 {top_rank} 名单没有变动（新进 / 名次 / 榜首价），"
                            f"监控在正常运行（统计到 {day:%m-%d}）。",
                       priority=BRIEF_PRIORITY)
