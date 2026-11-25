from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text, Index, UniqueConstraint, true
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator):
    """SQLite 不保存时区。写入时转 UTC 并去掉 tzinfo，读出时补回 UTC，
    保证应用层拿到的永远是 aware datetime（Global Constraints 要求）。"""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime is not allowed; use db.session.utcnow()")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "account"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    nickname: Mapped[str | None] = mapped_column(String(64))
    phone_masked: Mapped[str | None] = mapped_column(String(32))
    logged_in_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    token_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    # 多账号（多账号 spec §2，迁移 0014）。label 是用户起的简称（推送、看板用），空时显示昵称；
    # device_id 按号持久化，免得同一个号每次启动换一台「设备」。BUFF 是第二期（ADR-0022）。
    platform: Mapped[str] = mapped_column(String(16), default="youpin", server_default="youpin")
    label: Mapped[str | None] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    device_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    # 平台自己的用户号（BUFF 是 `U` 开头的字符串，查重用；BUFF spec §2，迁移 0017）。悠悠号仍用 user_id
    platform_uid: Mapped[str | None] = mapped_column(String(32))

    def display_name(self) -> str:
        return self.label or self.nickname or f"账号 {self.id}"


class Item(Base):
    __tablename__ = "item"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hash_name: Mapped[str] = mapped_column(String(255), unique=True)
    cn_name: Mapped[str | None] = mapped_column(String(255))
    youpin_template_id: Mapped[int | None] = mapped_column(Integer, index=True)
    # BUFF 的 goods_id（BUFF spec §4）：第一次对上悠悠模板时记下，下次直接查
    buff_goods_id: Mapped[int | None] = mapped_column(Integer, index=True)


class MyBuyOrder(Base):
    __tablename__ = "my_buy_order"
    purchase_no: Mapped[str] = mapped_column(String(64), primary_key=True)
    # 这张单是哪个号的（迁移 0014，老数据是 1）。同步「消失」只在本号的单里判（多账号 spec §5）
    account_id: Mapped[int] = mapped_column(Integer, default=1, server_default="1", index=True)
    template_id: Mapped[int] = mapped_column(Integer, index=True)
    template_hash_name: Mapped[str] = mapped_column(String(255), default="")
    commodity_name: Mapped[str] = mapped_column(String(255), default="")
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    remaining_qty: Mapped[int] = mapped_column(Integer, default=1)
    abrade_text: Mapped[str | None] = mapped_column(String(64))
    type_id: Mapped[int] = mapped_column(Integer, default=-1)
    special_style: Mapped[str | None] = mapped_column(String(64))
    fade_text: Mapped[str | None] = mapped_column(String(64))
    queue_key: Mapped[str] = mapped_column(String(255), index=True)
    conditions_json: Mapped[str] = mapped_column(Text, default="{}")
    synced_at: Mapped[datetime] = mapped_column(UtcDateTime)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    # 市场价（悠悠在售最低价），由 Monitor._refresh_sell_prices 抽样刷新。
    #
    # 这三列必须一起读，因为"没人在卖"和"我们没查到价格"是两件完全不同的事，而它们在
    # sell_min 上都表现为 NULL：
    #   sell_depth is None  -> 还没轮到这张单，或者上次查失败了。价格未知。
    #   sell_depth == 0     -> 查到了，市场上一件都没有在售。这是一个确定的事实。
    #   sell_depth > 0      -> sell_min 是当时看到的最低在售价。
    # 千万不要在 sell_min 为空时填 0 顶上——这个项目已经因为"价格字段静悄悄变成 0"吃过一次
    # 大亏（371 张单价格全存成 0.00，见 live-api-findings §4），而"0 元在售"恰好又是个看起来
    # 很合理、实际完全错误的值。渲染规则见 core.messages.sell_label。
    sell_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    sell_depth: Mapped[int | None] = mapped_column(Integer)
    sell_checked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    # 别的平台的最高求购价（SteamDT，BUFF/C5 中位数），只给即将告警的单刷新。
    #
    # 为什么要存它：2026-09-23 对 425 个模板全量对账发现，**悠悠的求购价比 BUFF/C5 中位数低
    # 9.1%**（79% 的饰品都低）。也就是说一张单在悠悠排到第 2 名、看着差价不大，真实买盘却可能
    # 在别的平台——那个「第 2 名」的价值就有限。这个数字进告警正文，是为了让人当场看出这件事。
    #
    # `ref_bid_platforms` 必须和价格一起读：参照用了哪几家会随覆盖情况变化（实测只有 BUFF 和
    # C5 有求购数据，HALOSKINS 一件都没有），只报一个数字而不说来源，等于把一家的报价说成
    # 几家的共识。
    ref_bid: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    ref_bid_platforms: Mapped[str | None] = mapped_column(String(64))
    ref_bid_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    # fast | normal | slow, from the old per-order polling plan (core/planner.py's now-
    # deleted lane_for/plan_round). Nothing since the reported-rank redesign (see
    # Monitor.run_round) ever writes anything but the column default "normal" - pass A
    # processes every active order unconditionally and pass B's sampling is
    # priority/rotation-based, not lane-based, so neither pass computes a lane any more.
    # Unused and no longer displayed (2026-09-24, this change): the "档位" column that
    # used to read it in templates/overview.html was dropped from the dashboard, since
    # showing a value that's frozen at "normal" for every row forever was misleading.
    # Left in place rather than dropped (dropping a column in SQLite is disruptive).
    lane: Mapped[str] = mapped_column(String(16), default="normal")
    # Unlike `lane`, genuinely dead: nothing anywhere writes or reads this column any
    # more (confirmed 2026-08-30, this change) - not even for display. It predates
    # `reported_rank` below, which is the field pass A/pass B actually act on today.
    # Left in place rather than dropped (dropping a column in SQLite is disruptive).
    last_rank: Mapped[int | None] = mapped_column(Integer)
    # YouPin's own rank/top-bid for this order, straight from searchPurchaseOrderList's
    # `rank` and `maxPurchasePrice` fields (verified 2026-08-30 - see MyPurchaseOrder's
    # docstring). Populated by sync_my_orders. This is now the primary signal the
    # ranking/alert engine acts on: Monitor.run_round's pass A steps the alert state
    # machine straight from `reported_rank` every round, with `top_bid` as that round's
    # RankSnapshot.top1_price. pass B still re-derives a small sample the old way (public
    # queue + compute_rank) as a cross-check - see RankCheck.
    reported_rank: Mapped[int | None] = mapped_column(Integer)
    top_bid: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # Last time this order was picked for pass B (sampled verification against the
    # public queue) - see RankCheck. Drives "least-recently-verified" rotation so every
    # order eventually gets cross-checked, not just the ones near the alert threshold.
    # None means "never verified" and sorts first in that rotation.
    last_verified_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    # 悠悠给的 rank 是 "*" = 有卖家卖给了这张单、交易还没完成（live-api-findings §7）。记这一段
    # 「交易中」第一次被看到的时刻，名次变回数字就清空；in_trade_notice 是这一段的推送状态
    # （sent / failed / suppressed），和它一起清空。成交推送 spec §3.1。
    in_trade_since: Mapped[datetime | None] = mapped_column(UtcDateTime)
    in_trade_notice: Mapped[str | None] = mapped_column(String(16))
    # 市价提醒（市价提醒 spec §2）：现在成立的情况（inverted / near / both，都不成立为 NULL）和这一段的
    # 推送状态（sent / failed / suppressed）。都不成立、改价、单子消失时一起清空，下次成立是新的一段。
    market_flag: Mapped[str | None] = mapped_column(String(8))
    market_notice: Mapped[str | None] = mapped_column(String(16))
    # BUFF 单：价比我高（同价比我早）的条件单张数，只显示、不进告警（BUFF spec §5）。悠悠单是 NULL
    cond_ahead: Mapped[int | None] = mapped_column(Integer)


class RankSnapshot(Base):
    __tablename__ = "rank_snapshot"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    purchase_no: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    rank: Mapped[int | None] = mapped_column(Integer)
    beyond: Mapped[int | None] = mapped_column(Integer)
    top1_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    top2_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    top1_qty: Mapped[int | None] = mapped_column(Integer)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    # 这一轮写快照时该单已知的市场价，从 MyBuyOrder 上直接抄过来（可能是前几轮查到的值——
    # 市场价是抽样轮换刷新的，不是每轮每单都查）。sell_depth 的三种取值含义与 MyBuyOrder
    # 上那组完全一致，见那里的注释。
    sell_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    sell_depth: Mapped[int | None] = mapped_column(Integer)


Index("ix_rank_snapshot_order_ts", RankSnapshot.purchase_no, RankSnapshot.ts)


class RankCheck(Base):
    """One pass-B sample: an order's YouPin-reported rank cross-checked against a rank
    computed locally from the public queue (the pre-redesign method - see
    Monitor.run_round). `agreed` is only true when both ranks are known and equal - it is
    never true for a cross-check that couldn't be carried out.

    A false `agreed` is *not* the same as "YouPin's rank is wrong": when the public queue
    page came back full (`sample_size >= visible_limit`) and `computed_rank` is None, our
    own order simply sat past the page cut and the check could not be judged at all.
    Readers must split those two cases with `core.rank.verification_outcome`, which
    derives the three-way outcome from the columns stored here - agreed / disagreed /
    inconclusive - so old rows are re-read under the same rule without a backfill."""

    __tablename__ = "rank_check"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    purchase_no: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    reported_rank: Mapped[int | None] = mapped_column(Integer)
    computed_rank: Mapped[int | None] = mapped_column(Integer)
    queue_size: Mapped[int] = mapped_column(Integer, default=0)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    agreed: Mapped[bool] = mapped_column(Boolean, default=False)


class AlertStateRow(Base):
    __tablename__ = "alert_state"
    purchase_no: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), default="NORMAL")
    streak: Mapped[int] = mapped_column(Integer, default=0)
    last_rank: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)


class NotificationLog(Base):
    __tablename__ = "notification_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    event: Mapped[str] = mapped_column(String(32))
    rank: Mapped[int | None] = mapped_column(Integer)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | sent | failed | suppressed


class SystemStatus(Base):
    __tablename__ = "system_status"
    adapter: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_error_code: Mapped[str | None] = mapped_column(String(32))
    last_error_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    paused_until: Mapped[datetime | None] = mapped_column(UtcDateTime)
    error_counts_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class PriceDaily(Base):
    """每日价格快照（M3a，spec §7a.1）：每天 23:50 给活跃求购单涉及的每个模板、SteamDT 返回的
    每个平台存一行。`date` 是 settings.timezone 下的本地日期（YYYY-MM-DD 字符串，字典序即日期序）。

    价格 None = 该平台没有数据（SteamDT 的 0 在解析时已转成 None），**绝不是零元**。"""

    __tablename__ = "price_daily"
    __table_args__ = (UniqueConstraint("date", "template_id", "platform", name="uq_price_daily_day_tpl_platform"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String(10), index=True)
    template_id: Mapped[int] = mapped_column(Integer, index=True)
    market_hash_name: Mapped[str] = mapped_column(String(255))
    platform: Mapped[str] = mapped_column(String(32))
    sell_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    sell_count: Mapped[int] = mapped_column(Integer, default=0)
    bidding_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    bidding_count: Mapped[int] = mapped_column(Integer, default=0)
    update_time: Mapped[datetime | None] = mapped_column(UtcDateTime)
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime)


class BriefLog(Base):
    """每天一行：这一天的简报发了什么、或者为什么没发（spec §7a.4）。`date` 是简报覆盖的本地日期。

    decision：sent / quiet_sent / skipped / no_data / no_channel / failed。reasons_json 里至少有
    "summary"（一句中文），推送出问题时还有 "delivery"。body 存**不截断**的全文——推送出去的那份
    可能被 4000 字节上限截过。"""

    __tablename__ = "brief_log"
    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    decision: Mapped[str] = mapped_column(String(16))
    reasons_json: Mapped[str] = mapped_column(Text, default="{}")
    title: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str | None] = mapped_column(Text)
    # 发出去的那张表（看板三期）：{"rows": [...], "sell_note": "..."}。只有发了表的那天有，老记录为空。
    rows_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    sent_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class DailyBar(Base):
    """自有单品日线（价位 spec §3.1）。`date` 是 settings.timezone 下的本地日期，只存**已收盘**的日子。

    为什么要自己存：SteamDT 的历史会变（周线 4 天里多出过两段缺口），今天算出来的分位、回测
    得能在下个月复现。已有的行不覆盖（最近 3 天除外，那几根 SteamDT 可能还在修正）。
    价格 ≤ 0 的那根整根不存：0 = 没有数据。"""

    __tablename__ = "daily_bar"
    __table_args__ = (UniqueConstraint("template_id", "platform", "date", name="uq_daily_bar_tpl_platform_day"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(Integer)
    platform: Mapped[str] = mapped_column(String(16))
    date: Mapped[str] = mapped_column(String(10))
    open: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    close: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    high: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    low: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime)


class IndexDaily(Base):
    """自有大盘日线（价位 spec §3.1），写入规则同 DailyBar。"""

    __tablename__ = "index_daily"
    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    open: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    close: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    high: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    low: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime)


class Valuation(Base):
    """每天每个模板的估值（价位 spec §3.1、§4）。只存和单无关的部分；按我的价、按榜首价的差价在
    接口里现算，改价后不用等第二天。任何一项算不出来就是 NULL，**绝不填 0**。

    比率（pct、c30、ex30、vol7、q10、spread）都是小数：0.15 = 15%，ex30 是百分点 / 100。"""

    __tablename__ = "valuation"
    __table_args__ = (UniqueConstraint("date", "template_id", name="uq_valuation_day_tpl"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String(10), index=True)
    template_id: Mapped[int] = mapped_column(Integer, index=True)
    computed_at: Mapped[datetime] = mapped_column(UtcDateTime)
    bars: Mapped[int] = mapped_column(Integer, default=0)
    pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    lo: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    hi: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    cur: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    trend: Mapped[str | None] = mapped_column(String(8))
    c30: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    c90: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    b30: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    b90: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    ex30: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    rel: Mapped[str | None] = mapped_column(String(8))
    vol7: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    q10: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    liq: Mapped[str | None] = mapped_column(String(8))
    listed: Mapped[int | None] = mapped_column(Integer)
    spread: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    exit: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    exit_where: Mapped[str | None] = mapped_column(String(16))
    exit_sell: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    cap: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    snapshot_date: Mapped[str | None] = mapped_column(String(10))


class OrderEvent(Base):
    """求购单的变化（价位 spec §3.1），给二期回测校准「多久能成交」用。同步时记：

    - new：第一次同步到（或消失后又出现）
    - filled：remaining_qty 变少、且不是改件数造成的——确定成交了 qty_before − qty_after 件
    - resized：用户改了件数（qty_before / qty_after 是总件数）
    - repriced：价格变了（用户在悠悠里改了价）
    - gone：从列表里消失。成交、被暂停（余额不足）、撤单都有可能；去向由 services.fills.resolve_gone
      到「已完成」「暂停中」列表里查清后写进 outcome（成交推送 spec §3.2）

    notice 是这次成交的推送状态（pending / sent / failed / suppressed / skipped），NULL = 不推送。"""

    __tablename__ = "order_event"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(Integer, default=1, server_default="1", index=True)
    purchase_no: Mapped[str] = mapped_column(String(64), index=True)
    template_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(16))
    qty_before: Mapped[int | None] = mapped_column(Integer)
    qty_after: Mapped[int | None] = mapped_column(Integer)
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    # gone 的去向：done / paused / missing / likely_done（推测成交：交易中过，之后哪个列表里都没有）
    outcome: Mapped[str | None] = mapped_column(String(16))
    outcome_at: Mapped[datetime | None] = mapped_column(UtcDateTime)   # 悠悠给的成交（暂停、被卖）时间
    outcome_qty: Mapped[int | None] = mapped_column(Integer)     # done 时的 buyQuantity
    notice: Mapped[str | None] = mapped_column(String(16), index=True)   # 每轮按它找待推的成交
    in_trade_at: Mapped[datetime | None] = mapped_column(UtcDateTime)   # gone 时还在交易中：第一次看到 * 的时刻
    # BUFF：配上的 BUFF 订单号（订单不带求购单号，只能配对；记下来防止一笔订单配给两次，BUFF spec §6）
    outcome_ref: Mapped[str | None] = mapped_column(String(32))


class PickRun(Base):
    """一次选品（价位 spec §9b.2）。保留最近 10 次，多的连结果一起删。

    status：running / done / failed / interrupted（服务重启时还在跑的）。phase：pool / prices / klines / done。"""

    __tablename__ = "pick_run"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    filters_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), index=True)
    phase: Mapped[str] = mapped_column(String(16))
    done: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    pool: Mapped[int] = mapped_column(Integer, default=0)          # 按名字圈出来的模板数
    priced: Mapped[int] = mapped_column(Integer, default=0)        # 价格区间筛完剩下的
    failed: Mapped[int] = mapped_column(Integer, default=0)        # 日线没取到的（照样用库里已有的估）
    price_failed: Mapped[int] = mapped_column(Integer, default=0)  # 没取到价格的模板（那一批出错了）
    error: Mapped[str | None] = mapped_column(String(255))         # 只记异常类型名或中文原因


class PickResult(Base):
    """一次选品里一个模板的结果。价格来自 SteamDT（悠悠最高求购是所有子队列里最高的）；
    描述字段同 `valuation`；结论见 `analysis.pick.verdict`。缺失为 NULL，**绝不填 0**。"""

    __tablename__ = "pick_result"
    __table_args__ = (UniqueConstraint("run_id", "template_id", name="uq_pick_result_run_tpl"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    template_id: Mapped[int] = mapped_column(Integer)
    yp_sell: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    yp_count: Mapped[int | None] = mapped_column(Integer)
    yp_bid: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    bars: Mapped[int] = mapped_column(Integer, default=0)
    pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    trend: Mapped[str | None] = mapped_column(String(8))
    rel: Mapped[str | None] = mapped_column(String(8))
    q10: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    liq: Mapped[str | None] = mapped_column(String(8))
    exit: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    exit_where: Mapped[str | None] = mapped_column(String(16))
    cap: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    rebound: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))   # 现价比近 60 天最低高多少（迁移 0013）
    amp: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))       # 一年最高 ÷ 一年最低，只作描述
    verdict: Mapped[str] = mapped_column(String(8))
    rule: Mapped[str] = mapped_column(String(255))
    bid: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    margin: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    first: Mapped[bool] = mapped_column(Boolean, default=False)
    mine: Mapped[bool] = mapped_column(Boolean, default=False)     # 已经挂了单


class TradeOrder(Base):
    """悠悠的买卖订单（盈亏 spec §3.1，迁移 0015），只存「已完成」（orderStatus 340）。金额是元。
    对手的昵称、头像、Steam ID、报价号**不入库**。`net_amount`：卖出是扣完手续费的到手价（订单详情
    `revenue.amount`，`net_exact`；拿到详情前按手续费估算），买入等于 `amount`（买方不收费）。"""

    __tablename__ = "trade_order"
    __table_args__ = (UniqueConstraint("platform", "order_no", name="uq_trade_order_platform_no"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    platform: Mapped[str] = mapped_column(String(16))
    order_no: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(4))
    template_id: Mapped[int] = mapped_column(Integer, index=True)
    hash_name: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    qty: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    net_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    net_exact: Mapped[bool] = mapped_column(Boolean, default=False)
    detail_checked: Mapped[bool] = mapped_column(Boolean, default=False)   # 卖出详情查过了（没有到手价也不再查）
    sub_type: Mapped[int | None] = mapped_column(Integer)
    asset_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime)


class PnlPosition(Base):
    """每天一个号一个模板的持仓快照（盈亏 spec §3.2）。`inv_qty + lease_qty + unknown_qty = qty`。
    `price` 缺失为 NULL，**绝不填 0**。"""

    __tablename__ = "pnl_position"
    __table_args__ = (UniqueConstraint("date", "account_id", "platform", "template_id",
                                       name="uq_pnl_position_day_acc_tpl"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String(10), index=True)
    account_id: Mapped[int] = mapped_column(Integer)
    platform: Mapped[str] = mapped_column(String(16))
    template_id: Mapped[int] = mapped_column(Integer)
    hash_name: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    qty: Mapped[int] = mapped_column(Integer)
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    first_bought_at: Mapped[datetime] = mapped_column(UtcDateTime)
    inv_qty: Mapped[int] = mapped_column(Integer, default=0)
    lease_qty: Mapped[int] = mapped_column(Integer, default=0)
    unknown_qty: Mapped[int] = mapped_column(Integer, default=0)
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    price_src: Mapped[str | None] = mapped_column(String(16))
    price_flag: Mapped[str | None] = mapped_column(String(16))


class PnlDaily(Base):
    """每天一个号的盈亏汇总（盈亏 spec §3.3），二期的持仓走势读它。"""

    __tablename__ = "pnl_daily"
    __table_args__ = (UniqueConstraint("date", "account_id", name="uq_pnl_daily_day_acc"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String(10), index=True)
    account_id: Mapped[int] = mapped_column(Integer)
    realized_total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    realized_day: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    qty: Mapped[int] = mapped_column(Integer)
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    value_mkt: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    value_net: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    cost_priced: Mapped[Decimal] = mapped_column(Numeric(12, 2))   # 有市价的那部分持仓的成本（浮动只算它）
    unpriced_qty: Mapped[int] = mapped_column(Integer, default=0)
    inv_qty: Mapped[int] = mapped_column(Integer, default=0)
    lease_qty: Mapped[int] = mapped_column(Integer, default=0)
    unknown_qty: Mapped[int] = mapped_column(Integer, default=0)
    unknown_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    youpin_pnl: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    youpin_pnl_n: Mapped[int | None] = mapped_column(Integer)
    lease_count: Mapped[int] = mapped_column(Integer, default=0)
    lease_rent_day: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    unbooked_inv_qty: Mapped[int] = mapped_column(Integer, default=0)
    unbooked_inv_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    computed_at: Mapped[datetime] = mapped_column(UtcDateTime)
class PausedCheckRun(Base):
    """一次「充值前检查暂停单」（暂停单 spec §4）。保留最近 5 次，多的连明细一起删。

    status：running / done / failed / interrupted（服务重启时还在跑的）。phase：pages / prices / verify。
    `skipped` 是这次没查成的号（登录失效、限流），逗号分隔的 id；`error` 只记中文一句或异常类型名。"""

    __tablename__ = "paused_check_run"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    status: Mapped[str] = mapped_column(String(16), index=True)
    source: Mapped[str] = mapped_column(String(16))               # dashboard / cli
    phase: Mapped[str | None] = mapped_column(String(16))
    done: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    incomplete: Mapped[bool] = mapped_column(Boolean, default=False)  # 某个号翻到了页数上限
    steamdt: Mapped[bool] = mapped_column(Boolean, default=False)     # 这次有没有 SteamDT 价
    skipped: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(String(255))


class PausedCheckItem(Base):
    """一次检查里的一张暂停单。价格缺失为 NULL，**绝不填 0**；`sell_depth = 0` 是「实时查到没人在卖」。"""

    __tablename__ = "paused_check_item"
    __table_args__ = (UniqueConstraint("run_id", "purchase_no", name="uq_paused_check_item_run_no"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    account_id: Mapped[int] = mapped_column(Integer, default=1)
    purchase_no: Mapped[str] = mapped_column(String(64))
    template_id: Mapped[int] = mapped_column(Integer)
    commodity_name: Mapped[str] = mapped_column(String(255))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    remaining_qty: Mapped[int] = mapped_column(Integer, default=1)
    paused_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    top_bid: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    top_empty: Mapped[bool] = mapped_column(Boolean, default=False)   # 悠悠说「暂无」：子队列没人
    sell_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    sell_depth: Mapped[int | None] = mapped_column(Integer)
    sell_src: Mapped[str | None] = mapped_column(String(16))          # youpin（实时）/ steamdt
    sell_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    tier: Mapped[str] = mapped_column(String(16))
    top1_kind: Mapped[str | None] = mapped_column(String(8))
