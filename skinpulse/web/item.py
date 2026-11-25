"""`GET /api/item/{template_id}`：饰品详情页（看板 spec §4.2、§5）。

我的求购直接用总览的行（`overview.template_rows`）：同一个 rank 来源、同一套差价过滤，两页不会
对不上。多平台比价和 K 线走 MarketData 缓存，约束见 `web/market.py`。
"""
from __future__ import annotations

from sqlalchemy import select

from skinpulse.adapters.steamdt.models import KlinePeriod
from skinpulse.core.names import split_item_name
from skinpulse.db.models import Item, MyBuyOrder
from skinpulse.db.session import utcnow
from skinpulse.web.market import fetch, kline_block, prices_block, weekly_block
from skinpulse.web.overview import _week, template_rows
from skinpulse.web.value import item_valuation


def item_payload(rt, template_id: int) -> dict | None:
    """模板既没有单也没有 SteamDT 映射时返回 None（接口给 404）。"""
    now = utcnow()
    rows = template_rows(rt, template_id, now=now)
    with rt.session_factory() as s:
        item = s.scalar(select(Item).where(Item.youpin_template_id == template_id))
        first = None
        if rows:
            first = s.get(MyBuyOrder, rows[0]["purchase_no"])
    if item is None and not rows:
        return None
    # 名字跟总览一致：有单就用悠悠的 commodity_name（排名最好的那张）；SteamDT 的中文名和悠悠的
    # 写法有出入（空格、全角逗号），只在没单时兜底。
    name = split_item_name(first.commodity_name if first is not None else item.cn_name)
    mhn = item.hash_name if item else None

    out = {
        "template_id": template_id,
        "title": name.title, "star": name.star, "stattrak": name.stattrak, "tag": name.tag,
        "market_hash_name": mhn,
        "youpin_url": rt.settings.ntfy.click_url_template.format(template_id=template_id),
        "orders": rows,
        # orders[].rank_7d 对应的 7 个本地日：前端画排名折线用，不自己按浏览器时间推算
        "days": [d.isoformat() for d in _week(rt.settings.timezone, now)],
        "market": {"configured": rt.market is not None, "mapped": mhn is not None},
        "prices": None, "kline": None, "weekly": None,
        # 「当前价位」卡片（价位 spec §6.2）：只读自有表，没配 SteamDT key 也照样有（如果算过）
        "valuation": item_valuation(rt, template_id, rows),
    }
    # 没配 key、没映射都不去 SteamDT：前端按 market 的两个标志说明原因
    if rt.market is None or mhn is None:
        return out
    out["prices"] = prices_block(fetch(rt.market.prices, mhn))
    out["kline"] = kline_block(fetch(rt.market.item_kline, mhn, KlinePeriod.DAY, "ALL"))
    out["weekly"] = weekly_block(fetch(rt.market.item_kline, mhn, KlinePeriod.WEEK, "ALL"))
    return out
