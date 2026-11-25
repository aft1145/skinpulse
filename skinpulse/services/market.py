"""多平台行情对账：把 SteamDT 的价格接到我们自己的求购单上。

两段独立的活：

1. `sync_base_items` —— 把 SteamDT 的全量饰品目录灌进 `Item` 表，建立
   **悠悠 templateId ↔ marketHashName** 的映射。这是后面一切的前提，因为我们手上只有
   templateId 和中文名，而 SteamDT 只认 marketHashName。
2. `compare_platforms` —— 拿我们在册求购单对应的饰品，批量查各平台价格，算出悠悠相对
   同类平台偏高还是偏低。
"""
from __future__ import annotations

import math

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from skinpulse.adapters.steamdt.models import BaseItem, ItemPrices
from skinpulse.analysis.platform_gap import Gap, Summary, bid_gap, sell_gap, summarize
from skinpulse.db.models import Item, MyBuyOrder
from skinpulse.services.buff import youpin_orders

log = logging.getLogger(__name__)

# /price/batch 的配额是 1 次/分钟。批与批之间必须等够，否则第二批直接撞配额——而配额错误
# 和普通失败不一样，重试只会把下一个窗口也烧掉。留 5 秒余量。
BATCH_COOLDOWN_S = 65
# 几个任务共用这一个配额（23:50 价格快照、选品、暂停单检查、盈亏首次导入、对账 CLI），而且可能在不同进程里
# （服务、命令行）。每发一批之前看 system_status 里这一行记的上一批时间，不够 BATCH_COOLDOWN_S 就等够，
# 发之前把「现在」写回去（2026-11-24：命令行的 `pnl import` 和看板上的暂停单检查同时跑，撞了 3 次 4005）。
BATCH_SLOT = "steamdt_batch"


def wait_batch_slot(session: Session, sleep, *, now=None) -> float:
    """等到可以发下一批 /price/batch，并记下这一批的时间。返回等了几秒（向上取整；没等是 0）。

    只等一次、不循环：调用方的 `sleep` 在测试里是假的，循环会一直等不到。两个进程同一刻读到同一个时间
    还是会撞，概率很小，撞了也只是那一批 4005（各调用方都把失败的批当作没取到，不会重试烧配额）。"""
    from skinpulse.db.models import SystemStatus
    from skinpulse.db.session import utcnow

    clock = now or utcnow
    row = session.get(SystemStatus, BATCH_SLOT)
    waited = 0.0
    if row is not None and row.last_ok_at is not None:
        left = BATCH_COOLDOWN_S - (clock() - row.last_ok_at).total_seconds()
        if left > 0:
            waited = float(min(math.ceil(left), BATCH_COOLDOWN_S))   # 记录的时间在将来（改过系统时间）也最多等一个周期
            sleep(waited)
    if row is None:
        row = SystemStatus(adapter=BATCH_SLOT)
        session.add(row)
    row.last_ok_at = row.updated_at = clock()
    session.commit()
    return waited


def sync_base_items(session: Session, items: Iterable[BaseItem]) -> tuple[int, int]:
    """把 SteamDT 的饰品目录写进 `Item`，返回 (写入条数, 因为没有悠悠 itemId 而跳过的条数)。

    只收**在悠悠上架**的条目：没有 YOUPIN itemId 的饰品我们既监控不到也对不上账，存进来
    只会让这张表白白多出上万行（实测 4.4 万条里有约 9 千条没有悠悠 id）。

    按 `hash_name` 幂等 upsert，所以可以反复跑。`/base` 每天只能调一次，调用方通常应该
    从落盘的响应里读（见 CLI 的 --from-file）。
    """
    existing = {i.hash_name: i for i in session.scalars(select(Item))}
    stored = skipped = 0
    for it in items:
        tid = it.youpin_template_id()
        if tid is None:
            skipped += 1
            continue
        if not it.market_hash_name:
            skipped += 1
            continue
        row = existing.get(it.market_hash_name)
        if row is None:
            row = Item(hash_name=it.market_hash_name)
            session.add(row)
            existing[it.market_hash_name] = row
        row.cn_name = it.name or row.cn_name
        row.youpin_template_id = tid
        # BUFF 那一项就是 BUFF 的 goods_id（BUFF 调研 §8.6，4/4 对上）：BUFF 单靠它对到悠悠模板
        buff = it.platform_item_ids.get("BUFF")
        if buff and buff.isdigit():
            row.buff_goods_id = int(buff)
        stored += 1
    session.commit()
    return stored, skipped


def resolve_names(session: Session, template_ids: Iterable[int]) -> dict[int, str]:
    """templateId -> marketHashName，只返回映射得到的。映射不到的由调用方决定怎么报告——
    静默丢掉会让「对账覆盖了多少单」这个数字失真。"""
    want = set(template_ids)
    out = {}
    for row in session.scalars(select(Item).where(Item.youpin_template_id.in_(want))):
        if row.youpin_template_id is not None:
            out[row.youpin_template_id] = row.hash_name
    return out


@dataclass
class ComparisonReport:
    requested: int = 0          # 参与对账的模板数
    unmapped: int = 0           # 没有 marketHashName、无从对账的
    fetched: int = 0            # SteamDT 真的返回了数据的
    sell_gaps: list[Gap] = field(default_factory=list)
    bid_gaps: list[Gap] = field(default_factory=list)
    sell: Summary | None = None
    bid: Summary | None = None


def compare_platforms(session: Session, client, *, batch_size: int = 200,
                      limit: int | None = None,
                      sleep: Callable[[float], None] = time.sleep,
                      progress: Callable[[str], None] | None = None) -> ComparisonReport:
    """对在册求购单涉及的饰品做一次多平台对账。

    按模板去重后分批查询：几百张单里同一个模板可能出现多次，逐单查纯属浪费那 1 次/分钟的
    配额。
    """
    rep = ComparisonReport()
    orders = list(session.scalars(select(MyBuyOrder).where(MyBuyOrder.active.is_(True), youpin_orders())))
    tids = sorted({o.template_id for o in orders})
    if limit is not None:
        tids = tids[:limit]
    rep.requested = len(tids)

    names = resolve_names(session, tids)
    rep.unmapped = len(tids) - len(names)
    wanted = sorted(set(names.values()))

    batches = [wanted[i:i + batch_size] for i in range(0, len(wanted), batch_size)]
    for n, batch in enumerate(batches):
        # 配额是按分钟算的，等够再发（和别的任务共用，见 wait_batch_slot）。
        if wait_batch_slot(session, sleep) and progress:
            progress("等了一会儿（/price/batch 配额 1 次/分钟）…")
        if progress:
            progress(f"批次 {n + 1}/{len(batches)}：{len(batch)} 个饰品")
        for prices in client.get_prices_batch(batch):
            _accumulate(rep, prices)

    rep.sell = summarize(rep.sell_gaps)
    rep.bid = summarize(rep.bid_gaps)
    return rep


def _accumulate(rep: ComparisonReport, prices: ItemPrices) -> None:
    if not prices.with_data():
        return
    rep.fetched += 1
    s = sell_gap(prices)
    if s is not None:
        rep.sell_gaps.append(s)
    b = bid_gap(prices)
    if b is not None:
        rep.bid_gaps.append(b)


def format_report(rep: ComparisonReport) -> str:
    lines = [f"对账模板 {rep.requested} 个：映射成功 {rep.requested - rep.unmapped}，"
             f"取到行情 {rep.fetched}，未映射 {rep.unmapped}"]

    def block(title: str, summary: Summary | None, gaps: list[Gap], direction: str) -> None:
        lines.append("")
        if summary is None:
            lines.append(f"{title}：没有可比数据")
            return
        pct = summary.median_pct_diff
        word = "低" if pct < 0 else "高"
        lines.append(f"{title}（{summary.n} 件，参照 {'/'.join(gaps[0].reference_platforms)}）")
        lines.append(f"  中位数：悠悠比参照{word} {abs(pct):.1f}%"
                     f"（比值 {summary.median_ratio:.3f}）")
        lines.append(f"  四分位：{summary.p25_ratio:.3f} ~ {summary.p75_ratio:.3f}")
        lines.append(f"  悠悠更低的占比：{summary.share_below * 100:.0f}%")
        lines.append(f"  含义：{direction}")

    block("卖方（在售最低价）", rep.sell, rep.sell_gaps,
          "悠悠在售价偏低 = 看板上那个「市场价」本身就是个偏保守的基准")
    block("买方（最高求购价）", rep.bid, rep.bid_gaps,
          "悠悠求购价偏低 = 同一件东西在悠悠挂求购的竞争更弱，更容易排到前面")
    return "\n".join(lines)
