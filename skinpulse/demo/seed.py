"""`skinpulse demo` 的假数据：一个虚构账号的求购单、两周的排名快照、成交、估值、简报和一次选品。

估值、简报、选品都走真的计算（services.valuation / brief / pick），喂的是 DemoMarket 的假行情，
所以页面上的数字彼此自洽。随机数固定种子，每次生成的内容一样（日期跟着今天走）。"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from decimal import Decimal

from skinpulse.analysis.pick import PoolFilter
from skinpulse.core.localday import local_today
from skinpulse.db.models import (
    Account, Item, MyBuyOrder, NotificationLog, OrderEvent, PausedCheckItem, PausedCheckRun, PriceDaily,
    RankSnapshot, SystemStatus,
)
from skinpulse.demo.market import DemoMarket, _money
from skinpulse.services import brief as brief_svc
from skinpulse.services import pick as pick_svc
from skinpulse.services.notifier import ChannelResult
from skinpulse.services.valuation import run_valuation

# (market_hash_name, 中文名, 模板号, 现价)。模板号是虚构的，点进悠悠不会对上真实页面。
CATALOG: list[tuple[str, str, int, str]] = [
    ("AK-47 | Redline (Field-Tested)", "AK-47 | 红线 (久经沙场)", 900001, "175"),
    ("AWP | Asiimov (Field-Tested)", "AWP | 二西莫夫 (久经沙场)", 900002, "620"),
    ("M4A1-S | Hyper Beast (Minimal Wear)", "M4A1 消音型 | 次时代 (略有磨损)", 900003, "88"),
    ("USP-S | Kill Confirmed (Field-Tested)", "USP 消音版 | 枪响人亡 (久经沙场)", 900004, "260"),
    ("Desert Eagle | Printstream (Field-Tested)", "沙漠之鹰 | 印花集 (久经沙场)", 900005, "310"),
    ("Glock-18 | Water Elemental (Minimal Wear)", "格洛克 18 型 | 水灵 (略有磨损)", 900006, "24"),
    ("AK-47 | Slate (Factory New)", "AK-47 | 墨岩 (崭新出厂)", 900007, "38"),
    ("M4A4 | Desolate Space (Field-Tested)", "M4A4 | 死寂空间 (久经沙场)", 900008, "56"),
    ("StatTrak™ P250 | Asiimov (Field-Tested)", "P250（StatTrak™） | 二西莫夫 (久经沙场)", 900009, "19"),
    ("MP9 | Starlight Protector (Factory New)", "MP9 | 星使 (崭新出厂)", 900010, "12.5"),
    ("Five-SeveN | Angry Mob (Minimal Wear)", "FN57 | 暴民 (略有磨损)", 900011, "9.8"),
    ("SSG 08 | Dragonfire (Field-Tested)", "SSG 08 | 浴火之龙 (久经沙场)", 900012, "45"),
    ("AUG | Momentum (Minimal Wear)", "AUG | 动量 (略有磨损)", 900013, "33"),
    ("FAMAS | Commemoration (Minimal Wear)", "法玛斯 | 纪念碑 (略有磨损)", 900014, "72"),
    ("P90 | Asiimov (Field-Tested)", "P90 | 二西莫夫 (久经沙场)", 900015, "28"),
    ("Tec-9 | Fuel Injector (Field-Tested)", "Tec-9 | 燃料喷射器 (久经沙场)", 900016, "8.6"),
    ("MAC-10 | Neon Rider (Minimal Wear)", "MAC-10 | 霓虹骑士 (略有磨损)", 900017, "15"),
    ("UMP-45 | Primal Saber (Field-Tested)", "UMP-45 | 原始军刀 (久经沙场)", 900018, "6.2"),
    ("★ Karambit | Doppler (Factory New)", "★ 爪子刀 | 多普勒 (崭新出厂)", 900019, "4800"),
    ("★ Butterfly Knife | Slaughter (Minimal Wear)", "★ 蝴蝶刀 | 屠夫 (略有磨损)", 900020, "9600"),
    ("★ Sport Gloves | Vice (Field-Tested)", "★ 运动手套 | 迈阿密风云 (久经沙场)", 900021, "7200"),
    ("★ Driver Gloves | King Snake (Field-Tested)", "★ 驾驶手套 | 王蛇 (久经沙场)", 900022, "2100"),
    ("CZ75-Auto | Victoria (Minimal Wear)", "CZ75 | 维多利亚 (略有磨损)", 900023, "21"),
    ("Dual Berettas | Melondrama (Factory New)", "双持贝瑞塔 | 瓜瓞绵绵 (崭新出厂)", 900024, "3.2"),
    # 以下只在目录里（选品用），没有求购单
    ("Nova | Hyper Beast (Minimal Wear)", "新星 | 暴怒野兽 (略有磨损)", 900025, "4.5"),
    ("XM1014 | Tranquility (Minimal Wear)", "XM1014 | 埋葬之影 (略有磨损)", 900026, "7.8"),
    ("M4A1-S | Decimator (Field-Tested)", "M4A1 消音型 | 毁灭者 2000 (久经沙场)", 900027, "64"),
    ("AK-47 | Phantom Disruptor (Minimal Wear)", "AK-47 | 幻影破坏者 (略有磨损)", 900028, "26"),
    ("AWP | Fever Dream (Field-Tested)", "AWP | 狂热幻梦 (久经沙场)", 900029, "41"),
    ("StatTrak™ Glock-18 | Bullet Queen (Field-Tested)", "格洛克 18 型（StatTrak™） | 子弹皇后 (久经沙场)", 900030, "95"),
    ("Galil AR | Eco (Minimal Wear)", "加利尔 AR | 经济 (略有磨损)", 900031, "11"),
]
ORDERED = 24                      # CATALOG 前 24 个挂了求购

# 每张求购单的名次和「离第 1」（榜首比我高多少）：覆盖已是第 1、很近、一般、较远、不告警、没查过
RANKS: list[tuple[int | None, float]] = [
    (1, 0), (1, 0), (1, 0), (1, 0), (2, 0.02), (2, 0.04), (2, 0.09), (2, 0.13), (2, 0.31),
    (3, 0.05), (3, 0.11), (3, 0.22), (4, 0.07), (4, 0.18), (5, 0.26), (6, 0.12), (7, 0.35),
    (9, 0.41), (12, 0.6), (15, 3.5), (None, 0), (2, 0.06), (1, 0), (3, 0.16),
]
IN_TRADE = 22                     # 这一张正在交易中
FAR = 1                           # 这张第 1 名挂得远低于市价：难以成交（难以成交 spec §5），看板上变暗
SMALL = 2                         # 演示小号的 id
SMALL_ORDERS = {3, 7, 11, 16, 20}  # 这几张是小号的
AHEAD = 4                         # 这张（第 2 名）前面是小号的单
BUFF_ACCOUNT = 3                  # 演示 BUFF 号（BUFF spec §13）：三张单，一张条件单、一张对不上悠悠模板
FILLS = [(5, 2, 0.95), (9, 4, 0.92), (12, 7, 0.97), (15, 11, 0.9), (10, 16, 0.94), (6, 23, 0.96)]  # (目录下标, 几天前, 价/现价)


def _queue(tid: int) -> str:
    return f"{tid}|ANY|-1|ANY|ANY"


class _Notifier:
    def publish(self, msg):
        return [ChannelResult("pushplus")]


def seed(session_factory, settings, market: DemoMarket, now: datetime) -> None:
    rng = random.Random(7)
    tz = settings.timezone
    yesterday = local_today(tz, now) - timedelta(days=1)
    with session_factory() as s:
        s.add(Account(id=1, user_id=10000000, nickname="演示账号", token_valid=True, label="主号",
                      logged_in_at=now - timedelta(days=3), last_sync_at=now - timedelta(minutes=4)))
        # 第二个号（多账号 spec §9）：几张单是它的，还有一张和主号挂在同一个子队列、价更高（own_ahead）
        s.add(Account(id=2, user_id=10000001, nickname="演示小号", token_valid=True, label="小号",
                      logged_in_at=now - timedelta(days=2), last_sync_at=now - timedelta(minutes=4)))
        s.add(Account(id=BUFF_ACCOUNT, platform="buff", nickname="演示 BUFF", token_valid=True, platform_uid="U0000000001",
                      created_at=now - timedelta(days=1), last_sync_at=now - timedelta(minutes=6)))
        s.add(SystemStatus(adapter=f"buff_account:{BUFF_ACCOUNT}", last_ok_at=now - timedelta(minutes=6), updated_at=now))
        for adapter in ("youpin", "steamdt", "pushplus"):
            s.add(SystemStatus(adapter=adapter, last_ok_at=now - timedelta(minutes=4), updated_at=now))
        for mhn, cn, tid, _ in CATALOG:
            s.add(Item(hash_name=mhn, cn_name=cn, youpin_template_id=tid))
        s.commit()

        _orders(s, rng, market, now)
        _buff_orders(s, market, now)
        _fills(s, market, now)
        _price_snapshot(s, market, yesterday, now)
        _paused(s, settings, market, now)
        s.commit()

        run_valuation(s, market, market, settings, day=yesterday, now=now, sleep=lambda _: None)
        for back in (2, 1):
            day = local_today(tz, now) - timedelta(days=back)
            brief_svc.run_daily_brief(s, _Notifier(), settings, day, now=now - timedelta(days=back - 1, hours=1))

        pick_at = now if not pick_svc.in_night_window(settings, now) else now - timedelta(hours=2)
        run = pick_svc.create_run(s, PoolFilter(price_min=Decimal("3"), price_max=Decimal("120")), pick_at)
        pick_svc.run_pick(s, market, settings, run.id, sleep=lambda _: None, now=lambda: pick_at)


def _orders(s, rng: random.Random, market: DemoMarket, now: datetime) -> None:
    steps = 14 * 6                                   # 两周，每 4 小时一条快照
    for i, (mhn, cn, tid, _) in enumerate(CATALOG[:ORDERED]):
        prices = market.prices(mhn).value
        yp, buff = prices.of("YOUPIN"), prices.of("BUFF")
        rank, gap = RANKS[i]
        mine = _money(float(yp.bidding_price) * rng.uniform(0.82, 0.99))
        if i == FAR:
            mine = _money(float(yp.sell_price) * 0.3)
        top = mine if rank in (1, None) else _money(float(mine) * (1 + gap))
        no = f"DEMO-{i + 1:03d}"
        checked = None if rank is None else now - timedelta(minutes=rng.randint(1, 9))
        s.add(MyBuyOrder(
            purchase_no=no, template_id=tid, template_hash_name=mhn, commodity_name=cn, price=mine,
            account_id=SMALL if i in SMALL_ORDERS else 1,
            qty=1, remaining_qty=1, type_id=-1, queue_key=_queue(tid), synced_at=now - timedelta(minutes=4),
            active=True, last_checked_at=checked, last_rank=rank, reported_rank=rank,
            top_bid=None if rank is None else top,
            sell_min=yp.sell_price, sell_depth=yp.sell_count, sell_checked_at=now - timedelta(minutes=20),
            ref_bid=buff.bidding_price, ref_bid_platforms="BUFF", ref_bid_at=now - timedelta(minutes=20),
            in_trade_since=now - timedelta(minutes=11) if i == IN_TRADE else None))
        if i == AHEAD:
            # 小号在同一个子队列里挂了榜首价：主号这张前面就是自己的小号
            s.add(MyBuyOrder(
                purchase_no="DEMO-S01", template_id=tid, template_hash_name=mhn, commodity_name=cn, price=top,
                account_id=SMALL, qty=1, remaining_qty=1, type_id=-1, queue_key=_queue(tid),
                synced_at=now - timedelta(minutes=4), active=True, last_checked_at=checked, reported_rank=1,
                top_bid=top, sell_min=yp.sell_price, sell_depth=yp.sell_count, sell_checked_at=now - timedelta(minutes=20)))
            s.add(RankSnapshot(purchase_no="DEMO-S01", ts=now - timedelta(minutes=4), rank=1, beyond=None, top1_price=top,
                               top2_price=None, top1_qty=1, sample_size=20, sell_min=yp.sell_price,
                               sell_depth=yp.sell_count))
        if rank is None:
            continue
        r = min(rank + rng.randint(-1, 6), 20) if rank > 1 else rng.randint(2, 5)
        for k in range(steps):
            ts = now - timedelta(hours=4 * (steps - 1 - k), minutes=rng.randint(0, 30))
            left = steps - 1 - k
            if left == 0:
                r = rank
            elif rng.random() < 0.25:
                r = max(1, r + (1 if r < rank else -1 if r > rank else rng.choice((-1, 1))))
            t1 = top if r <= rank else _money(float(top) * rng.uniform(0.97, 1.0))
            s.add(RankSnapshot(purchase_no=no, ts=ts, rank=r, beyond=None, top1_price=t1, top2_price=None,
                               top1_qty=1, sample_size=20, sell_min=yp.sell_price, sell_depth=yp.sell_count))


def _buff_orders(s, market: DemoMarket, now: datetime) -> None:
    """演示 BUFF 号的三张单（BUFF spec §13）：普通单第 1（前面有 4 张条件单）、条件单第 2、对不上悠悠模板的第 4。"""
    rows = []
    for k, (idx, rank, cond, spec, ahead) in enumerate([(1, 1, None, [], 4), (8, 2, "paintwear=0.07-0.08",
                                                        [{"type": "paintwear", "text": "磨损: 0.07-0.08",
                                                          "values": ["0.07", "0.08"]}], 0)]):
        mhn, cn, tid, _ = CATALOG[idx]
        buff = market.prices(mhn).value.of("BUFF")
        mine = _money(float(buff.bidding_price) * (1.0 if rank == 1 else 0.97))
        gid = 900000 + idx
        rows.append((f"buff:DEMO-B{k + 1:02d}", tid, mhn, cn, mine, rank, buff.bidding_price if rank > 1 else mine,
                     f"buff:{gid}:{cond or 'plain'}", gid, spec, ahead, "磨损 0.07-0.08" if cond else None))
    rows.append(("buff:DEMO-B03", -967946, "Charm | Lil' Ava", "挂件 | 小爱娃", Decimal("0.4"), 4, Decimal("0.43"),
                 "buff:967946:plain", 967946, [], 0, None))
    for no, tid, mhn, cn, price, rank, top, key, gid, spec, ahead, abrade in rows:
        s.add(MyBuyOrder(
            purchase_no=no, template_id=tid, template_hash_name=mhn, commodity_name=cn, price=price,
            account_id=BUFF_ACCOUNT, qty=1, remaining_qty=1, type_id=-1, abrade_text=abrade, queue_key=key,
            conditions_json=json.dumps({"goods_id": gid, "specific": spec, "buy_max_price": str(top)}),
            synced_at=now - timedelta(minutes=6), active=True, last_checked_at=now - timedelta(minutes=6),
            reported_rank=rank, top_bid=top, cond_ahead=ahead))
        s.add(RankSnapshot(purchase_no=no, ts=now - timedelta(minutes=6), rank=rank, beyond=None, top1_price=top,
                           top2_price=None, top1_qty=1, sample_size=30, sell_min=None, sell_depth=None))


def _fills(s, market: DemoMarket, now: datetime) -> None:
    for n, (idx, days_ago, ratio) in enumerate(FILLS):
        mhn, cn, tid, _ = CATALOG[idx]
        price = _money(float(market.prices(mhn).value.of("YOUPIN").sell_price) * ratio)
        no = f"DEMO-F{n + 1:02d}"
        done_at = now - timedelta(days=days_ago, hours=3 + n)
        s.add(MyBuyOrder(purchase_no=no, template_id=tid, template_hash_name=mhn, commodity_name=cn, price=price,
                         qty=1, remaining_qty=1, type_id=-1, queue_key=_queue(tid), synced_at=done_at,
                         active=False))
        s.add(OrderEvent(purchase_no=no, template_id=tid, kind="gone", qty_before=1, qty_after=0, price=price,
                         at=done_at + timedelta(minutes=4), outcome="done", outcome_at=done_at, outcome_qty=1,
                         notice="sent"))
        s.add(NotificationLog(fingerprint=no, event="FILLED", rank=None, payload_json=json.dumps({"demo": True}),
                              created_at=done_at + timedelta(minutes=5), sent_at=done_at + timedelta(minutes=5),
                              status="sent"))


# 一次查完的「充值前检查暂停单」（暂停单 spec §8）：（目录下标，我的价 ÷ 在售，榜首 ÷ 在售（None = 没人求购），
# 暂停了几天，号）。四档都有，两张暂停 121 天的老单；检查之后又有两张被暂停
PAUSED = [
    (21, 3.0, 0.9, 121, 1), (4, 1.08, 0.82, 47, 1),          # 倒挂
    (19, 0.97, 0.93, 2, 1), (11, 0.72, None, 121, 2),        # 直接第 1
    (2, 0.78, 0.80, 1, 1), (13, 0.70, 0.73, 1, 1),           # 差 5% 内
    (7, 0.92, 1.0, 3, 1),                                    # 贴近在售
    (0, 0.6, 0.9, 47, 1), (9, 0.5, 0.8, 3, 1), (15, 0.55, 0.7, 2, 1),
]


def _paused(s, settings, market: DemoMarket, now: datetime) -> None:
    from skinpulse.core.paused import classify

    cfg = settings.paused_check
    started = now - timedelta(minutes=40)
    run = PausedCheckRun(started_at=started, finished_at=started + timedelta(minutes=8), status="done",
                         source="dashboard", phase=None, done=0, total=0, incomplete=False, steamdt=True)
    s.add(run)
    s.flush()
    for n, (idx, mine, top, days, acc) in enumerate(PAUSED):
        mhn, cn, tid, _ = CATALOG[idx]
        yp = market.prices(mhn).value.of("YOUPIN")
        price = _money(float(yp.sell_price) * mine)
        top_bid = None if top is None else _money(float(yp.sell_price) * top)
        v = classify(price, top_bid, top is None, yp.sell_price, near_gap=Decimal(str(cfg.near_gap)),
                     sell_ratio=Decimal(str(cfg.sell_ratio)))
        live = n % 2 == 0
        s.add(PausedCheckItem(run_id=run.id, account_id=acc, purchase_no=f"DEMO-Z{n + 1:02d}", template_id=tid,
                              commodity_name=cn, price=price, remaining_qty=1,
                              paused_at=now - timedelta(days=days, hours=2), top_bid=top_bid, top_empty=top is None,
                              sell_min=yp.sell_price, sell_depth=yp.sell_count if not live else 10,
                              sell_src="youpin" if live else "steamdt",
                              sell_at=started + timedelta(minutes=7) if live else now - timedelta(hours=3),
                              tier=v.tier, top1_kind=v.top1_kind))
    for n in range(2):
        mhn, cn, tid, _ = CATALOG[5 + n]
        s.add(OrderEvent(purchase_no=f"DEMO-ZN{n + 1}", template_id=tid, kind="gone", qty_before=1, qty_after=None,
                         price=Decimal("1"), at=now - timedelta(minutes=20 - n), outcome="paused",
                         outcome_at=now - timedelta(minutes=22 - n)))


def _price_snapshot(s, market: DemoMarket, day, now: datetime) -> None:
    for mhn, _, tid, _ in CATALOG[:ORDERED]:
        for p in market.prices(mhn).value.platforms:
            s.add(PriceDaily(date=day.isoformat(), template_id=tid, market_hash_name=mhn, platform=p.platform,
                             sell_price=p.sell_price, sell_count=p.sell_count, bidding_price=p.bidding_price,
                             bidding_count=p.bidding_count, update_time=p.update_time, fetched_at=now))
