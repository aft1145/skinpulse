"""每日价格快照（M3a，spec §7a.1）：给活跃求购单涉及的模板，从 SteamDT 批量取一次多平台价格，
每个模板每个**有数据**的平台一行写进 price_daily。

- `/price/batch` 配额 1 次/分钟、每批最多 100 个名字：批间等 BATCH_COOLDOWN_S（65 秒）。
- 同一天重跑只补缺的模板（已有行的跳过），所以中途失败后手动再跑一次是安全的。
- 某一批失败（配额、鉴权、服务端异常）记一条日志继续下一批；失败绝不影响监控和告警。
- SteamDT 对它不覆盖的平台（CSMONEY/DMARKET/SKINPORT/WAXPEER）也会占位返回，`sellPrice`/
  `biddingPrice` 都是 0，adapter 会把两者都解析成 None（见 `adapters/steamdt/models.py` 的
  `_money`）。这种「两者都是 None」的行一律不写：实测这类占位行在真实库里一天约 1500 条，
  存下来毫无意义。一个模板如果**所有**平台都是这样，这个模板这一天就一行都不写——`rep.fetched`
  仍然把它算作「SteamDT 返回了」，但因为没有行落库，`已有行` 判定会认为这天还没补过，同一天
  重跑会重新去查它，这是有意的（对得起「重跑只补缺」这句话本身：没数据 = 没补上）。
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from skinpulse.adapters.steamdt.errors import SteamDTError
from skinpulse.db.models import MyBuyOrder, PriceDaily
from skinpulse.services.market import resolve_names, wait_batch_slot

log = logging.getLogger(__name__)


@dataclass
class SnapshotReport:
    day: str
    templates: int = 0       # 活跃求购单（和盈亏持仓）涉及的模板数
    unmapped: int = 0        # 没有 SteamDT 映射、查不了的
    already: int = 0         # 这一天已经有快照、跳过的
    fetched: int = 0         # SteamDT 真的返回了数据的模板数
    rows: int = 0            # 写进 price_daily 的行数
    failed_batches: int = 0


def take_price_snapshot(session: Session, client, *, day: date, batch_size: int, now: datetime,
                        sleep: Callable[[float], None] = time.sleep,
                        extra_template_ids: Iterable[int] = ()) -> SnapshotReport:
    """`extra_template_ids`：活跃求购单以外也要存价格的模板（盈亏的持仓模板，盈亏 spec §5.1）。"""
    key = day.isoformat()
    rep = SnapshotReport(day=key)
    tids = sorted(set(session.scalars(select(MyBuyOrder.template_id).where(MyBuyOrder.active.is_(True), MyBuyOrder.template_id > 0)))
                  | set(extra_template_ids))
    rep.templates = len(tids)
    names = resolve_names(session, tids)
    rep.unmapped = len(tids) - len(names)
    done = set(session.scalars(select(PriceDaily.template_id).where(PriceDaily.date == key)))
    rep.already = len(done & names.keys())
    by_name = {n: tid for tid, n in names.items() if tid not in done}
    ordered = sorted(by_name)
    for i in range(0, len(ordered), batch_size):
        wait_batch_slot(session, sleep)
        chunk = ordered[i:i + batch_size]
        try:
            items = client.get_prices_batch(chunk)
        except SteamDTError as e:
            # SteamDTError 的消息只有错误码和服务端文案，不含 key，可以记。
            rep.failed_batches += 1
            log.warning("price snapshot %s: batch %d failed (%s); continuing", key, i // batch_size + 1, e)
            continue
        except httpx.HTTPError as e:
            # 传输层异常（连接/超时/……）的消息里可能带上请求 URL，而 URL 上挂着 key 查询参数——
            # 只记类型名，绝不把异常本身（str(e) 或 log.exception 的 traceback）写进日志。
            rep.failed_batches += 1
            log.warning("price snapshot %s: batch %d failed (%s); continuing", key, i // batch_size + 1,
                       type(e).__name__)
            continue
        for item in items:
            tid = by_name.get(item.market_hash_name)
            if tid is None:
                continue
            rep.fetched += 1
            for p in item.with_data():
                session.add(PriceDaily(date=key, template_id=tid, market_hash_name=item.market_hash_name,
                                       platform=p.platform, sell_price=p.sell_price, sell_count=p.sell_count,
                                       bidding_price=p.bidding_price, bidding_count=p.bidding_count,
                                       update_time=p.update_time, fetched_at=now))
                rep.rows += 1
        session.commit()
    if rep.unmapped:
        log.info("price snapshot %s: %d template(s) have no SteamDT mapping (run steamdt-items)", key, rep.unmapped)
    return rep
