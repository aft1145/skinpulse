"""行情类接口的共用部分：`/api/item/{id}` 和 `/api/market`（看板 spec §4.2、§4.4、§5）。

两条硬约束：

1. **悠悠的数据只从数据库读，页面绝不直接请求悠悠。** 悠悠的匿名/账号通道有风控，监控服务
   为此排了节奏、配了每小时上限；看板刷新一下就多打一次悠悠，等于绕过了这些保护。
2. **外部行情一律走 MarketData 的缓存**（services/market_data.py）：每个 TTL 最多请求一次
   SteamDT。一次页面请求里每个数据源只取一次；1 月 / 3 月 / 1 年的切换、缩放都在前端切片，
   所以纵轴范围一次把三个窗口都算好返回。SteamDT 的大盘接口实测几分钟三次就触发 4005；
   缓存撞上配额时返回旧数据并标明 stale，页面照常显示。

约定同总览：金额是数字或 null（缺失绝不填 0），时间是带 `Z` 的 UTC。
"""
from __future__ import annotations

from decimal import Decimal

from skinpulse.adapters.steamdt.errors import SteamDTError
from skinpulse.adapters.steamdt.models import KlinePeriod
from skinpulse.analysis import signals as sig
from skinpulse.analysis.kline import windows, y_range
from skinpulse.web.overview import _num, iso_z

PLATFORM_NAMES = {
    "YOUPIN": "悠悠有品", "BUFF": "BUFF", "C5": "C5GAME", "HALOSKINS": "HaloSkins",
    "STEAM": "Steam", "IGXE": "IGXE", "V5ITEM": "V5ITEM",
}
# 悠悠放第一行：这是用户实际挂单的平台，其余按国内常用程度排。
PLATFORM_ORDER = ["YOUPIN", "BUFF", "C5", "HALOSKINS", "IGXE", "V5ITEM", "STEAM"]


def _pct(v: Decimal | None) -> float | None:
    return None if v is None else round(float(v), 2)


def fetch(fn, *args) -> dict:
    """调用 MarketData 的一个方法，把「拿到了 / 拿到的是旧数据 / 彻底失败」统一成一个 dict。"""
    try:
        f = fn(*args)
    except SteamDTError as e:
        return {"ok": False, "value": None, "fetched_at": None, "stale": False, "error": str(e)}
    return {"ok": True, "value": f.value, "fetched_at": iso_z(f.fetched_at), "stale": f.stale, "error": f.error}


def signals_block(daily) -> dict | None:
    s = sig.compute(daily)
    if s is None:
        return None
    return {
        "last_close": _num(s.last_close), "ma7": _num(s.ma7), "ma30": _num(s.ma30),
        "ma_gap_pct": _pct(s.ma_gap_pct), "trend": s.trend,
        "change_7d_pct": _pct(s.change_7d_pct), "change_30d_pct": _pct(s.change_30d_pct),
        "bars": s.bars, "trend_threshold_pct": float(sig.TREND_THRESHOLD_PCT),
    }


def kline_block(f: dict) -> dict:
    """K 线：原始日线（顺序照 SteamDT 的 [时间, 开, 收, 高, 低]，CLAUDE.md 坑 7）、每个时间窗口的
    纵轴范围和截断、信号。均线由前端按 bars 算（ADR-0013）。"""
    daily = f["value"] or []
    ranges = {name: None if r is None else {"lo": _num(r.lo), "hi": _num(r.hi), "clipped": r.clipped,
                                            "extreme": _num(r.extreme)}
              for name, r in windows(daily).items()}
    return {
        "bars": [[iso_z(b.ts), _num(b.open), _num(b.close), _num(b.high), _num(b.low)] for b in daily],
        "ranges": ranges,
        "signals": signals_block(daily) if daily else None,
        "fetched_at": f["fetched_at"], "stale": f["stale"], "error": f["error"],
    }


def weekly_block(f: dict) -> dict:
    """周线（看板上的「全部」范围）：日线只有 365 天，周线能往回看几年（大盘实测从 2023-07 起，
    单品从 2022-07 起，中间可能缺几段）。和日线一起在打开页面时取，纵轴范围整段算一份；
    均线由前端按周算（MA4 / MA13 周）。取不到时不影响日线。"""
    bars = f["value"] or []
    r = y_range(bars)
    return {
        "bars": [[iso_z(b.ts), _num(b.open), _num(b.close), _num(b.high), _num(b.low)] for b in bars],
        "range": None if r is None else {"lo": _num(r.lo), "hi": _num(r.hi), "clipped": r.clipped,
                                         "extreme": _num(r.extreme)},
        "fetched_at": f["fetched_at"], "stale": f["stale"], "error": f["error"],
    }


def prices_block(f: dict) -> dict:
    rows = []
    if f["ok"]:
        for p in sorted(f["value"].with_data(), key=lambda x: (
                PLATFORM_ORDER.index(x.platform) if x.platform in PLATFORM_ORDER else 99, x.platform)):
            # 件数只在这一侧有价格时才有意义：没有求购价时「0 单」和「没数据」要分开，给 null
            rows.append({"platform": p.platform, "name": PLATFORM_NAMES.get(p.platform, p.platform),
                         "sell": _num(p.sell_price),
                         "sell_count": p.sell_count if p.sell_price is not None else None,
                         "bid": _num(p.bidding_price),
                         "bid_count": p.bidding_count if p.bidding_price is not None else None,
                         "updated_at": iso_z(p.update_time)})
    return {"rows": rows, "fetched_at": f["fetched_at"], "stale": f["stale"], "error": f["error"]}


def market_payload(rt) -> dict:
    """`GET /api/market`：大盘指数（约 25 个整点值）和一年日线。"""
    if rt.market is None:
        return {"configured": False, "index": None, "kline": None, "weekly": None}
    i = fetch(rt.market.broad_index)
    bi = i["value"]
    index = {
        "value": _num(bi.value) if bi else None,
        "diff": _num(bi.diff) if bi else None,
        # 接口给的已经是百分数（0.4482 = 0.4482%），原样返回，不再乘 100（research §1.7）
        "diff_pct": _num(bi.diff_pct) if bi else None,
        "updated_at": iso_z(bi.update_time) if bi else None,
        "history": [[iso_z(t), _num(v)] for t, v in bi.history] if bi else [],
        "fetched_at": i["fetched_at"], "stale": i["stale"], "error": i["error"],
    }
    k = fetch(rt.market.broad_kline, KlinePeriod.DAY)
    kline = kline_block(k)
    daily = k["value"] or []
    first, last = (daily[0], daily[-1]) if daily else (None, None)
    kline["year_from"] = _num(first.close) if first else None
    kline["year_change_pct"] = (_pct((last.close / first.close - 1) * 100)
                                if first is not None and first.close else None)
    return {"configured": True, "index": index, "kline": kline,
            "weekly": weekly_block(fetch(rt.market.broad_kline, KlinePeriod.WEEK))}
