from __future__ import annotations

import logging
import shutil
import tomllib
from typing import Literal
from pathlib import Path

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

log = logging.getLogger(__name__)


class PollingSettings(BaseModel):
    request_interval_s: float = Field(3.0, ge=0.5)
    jitter_pct: float = Field(0.3, ge=0, le=1)
    # A round is now one authenticated call (refreshes every order's YouPin-reported
    # rank in one shot) plus verify_sample_size sampled public-queue checks - not one
    # public-queue request per order like the old per-order polling loop - so it
    # completes in well under a minute even at hundreds of orders. See
    # Monitor.run_round for what a round actually does.
    round_interval_min: int = Field(5, ge=1)
    # Pass B's near-threshold sampling priority (see Monitor._select_verify_targets):
    # an active order whose reported_rank is at or below this is sampled every round,
    # ahead of the least-recently-verified rotation. Dormant from the old per-order
    # "fast lane" polling design (see core/planner.py) until revived for this purpose -
    # deliberately independent of alerts.exit_rank, so sampling focus can be tuned
    # without touching alert hysteresis.
    fast_lane_rank_threshold: int = Field(5, ge=1)
    # Quiet-hours cadence for the monitoring round (see Monitor.run_round and
    # core.planner._is_night, revived from the old per-order polling design): during
    # local time [night_start_hour, night_end_hour) - a window crossing midnight if
    # night_start_hour > night_end_hour - the round is skipped until
    # night_multiplier x round_interval_min minutes have elapsed since the last round
    # that actually ran.
    night_start_hour: int = Field(0, ge=0, le=23)
    night_end_hour: int = Field(8, ge=0, le=24)
    night_multiplier: float = Field(2.0, ge=1)
    hourly_request_cap: int = Field(300, ge=10)
    heartbeat_interval_min: int = Field(30, ge=5)
    # 多账号（多账号 spec §5）：一轮里、心跳里号与号之间隔多少秒。只有一个号时用不到。
    account_gap_s: float = Field(10.0, ge=0)
    visible_limit: int = Field(20, ge=1)
    # Pass B (sampled verification, see Monitor.run_round): how many active orders to
    # re-check per round against the public queue, to confirm YouPin's own reported
    # rank still means what we assume. 0 disables pass B entirely.
    verify_sample_size: int = Field(5, ge=0)
    verify_enabled: bool = Field(True)
    # 市场价刷新（Monitor._refresh_sell_prices）：每轮给几张单去查在售最低价。和 pass B 一样
    # 是抽样轮换，不是每张单每轮都查——363 张单每轮各查一次就是 363 次请求，正是当初把排名
    # 方案压垮的那个量级。0 关闭。
    sell_sample_size: int = Field(5, ge=0)
    # 公开求购队列匿名只给前 20 条，排在 20 名之外的自己的单因此算不出排名（`compute_rank`
    # 只能返回 None）。带 token 请求时 pageSize 才真的生效，能一次取回整个队列。
    #
    # 只在"匿名那一页装满了、却没找到自己"时才升级成认证请求——认证请求是拿账号在换深度，
    # 风控盯的正是账号。0 关闭这个兜底（那就退回老行为：算不出来就是算不出来）。
    deep_page_size: int = Field(200, ge=0)


class AlertSettings(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    enter_rank: int = Field(2, ge=1)
    exit_rank: int = Field(4, ge=2)
    confirm_rounds: int = Field(2, ge=1)
    group_wait_s: int = Field(60, ge=0)
    hourly_cap: int = Field(10, ge=1)
    # An order whose leading bid is at least this many times the owner's own price is
    # not alert-worthy (see Monitor.run_round's eligibility check and AlertEngine.step's
    # `eligible` parameter): technically top-`enter_rank`, but outbidding would mean
    # paying an absurd multiple more, so it is filtered before the alert state machine
    # ever sees it - not after, which would strand the order ALERTING forever (see
    # AlertEngine.step's docstring). Measured against the owner's own 371 live orders:
    # ratios at rank<=2 were bimodal (~1x-2x for genuinely actionable orders, 150x for
    # noise), with nothing in between, and the portfolio-wide median ratio is 2.24x - so
    # 3.0 mutes the obvious 150x noise while keeping both near-miss orders. 0 disables
    # the filter entirely (every order is eligible regardless of gap).
    max_gap_ratio: float = Field(3.0, ge=0)
    # 离市价太远也算「难以成交」（难以成交 spec §1，analysis/reach.py）：市价（悠悠在售最低；没人在卖时用
    # 别处买盘）÷ 我的价 ≥ max_market_ratio **且** 市价 − 我的价 ≥ min_market_gap（元）时，不推「升到第 1
    # 名」、不进简报、看板可隐藏。2026-11-25 正式库 713 张：2 倍且 ¥20 新增 55 张（用户定的默认值）。
    # 差价下限挡住几块钱的便宜货（7 元对 9.46 这种比例高、差价小的不算）。倍数 0 = 关掉这条。
    max_market_ratio: float = Field(2.0, ge=0)
    min_market_gap: float = Field(20.0, ge=0)
    # 推送之前，拿公开队列把即将告警的那几张单的排名和榜首价重算一遍（见
    # Monitor._confirm_with_live_queue）。
    #
    # 为什么需要：悠悠在「我的求购单」里给的 rank / maxPurchasePrice **不是实时算的**，
    # 在有自动改价机器人的队列里会落后几分钟（2026-09-21 实测：悠悠说第 2 名、榜首 351，
    # 而实时队列是 352 / 351 / 我的 158，实际第 3 名）。告警本来整个建在这个值上，于是
    # 最值得抢的那些队列——刀、高价皮肤，恰好也是机器人最多的——推给用户的数字可能是错的。
    confirm_with_live_queue: bool = Field(True)
    # 每轮最多为几张单做这个实时核对。只有"排名进了告警带、且还没在 ALERTING"的单才会被
    # 选中——已经在 ALERTING 的单这一轮不会再产生事件，核对它没有意义。稳态下这个集合很小。
    live_confirm_max: int = Field(10, ge=0)

    @model_validator(mode="after")
    def validate_exit_rank_greater_than_enter_rank(self) -> AlertSettings:
        if self.exit_rank <= self.enter_rank:
            raise ValueError(
                f"exit_rank ({self.exit_rank}) must be strictly greater than "
                f"enter_rank ({self.enter_rank}) to maintain a dead zone"
            )
        return self


class YoupinPcHeaders(BaseModel):
    app_version: str = "5.26.0"
    app_type: str = "1"
    platform: str = "pc"
    secret_v: str = "h5_v1"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    )


class YoupinAppHeaders(BaseModel):
    """Android App 渠道请求头配置。2026-08 起该渠道被服务端强制版本网关卡死
    （HTTP 429 + code 85100 "当前app版本过低，请前往更新"，即使把 app_version 改成更新的
    号码也一样），账号相关调用已全部切到 PC 渠道（见 client.py 的 _post/_get 默认值）。
    这里保留不删，是给 PC 渠道将来也被同样卡版本时留的应急后备。"""

    app_version: str = "5.28.3"
    app_type: str = "4"
    platform: str = "android"
    package_type: str = "uuyp"
    device_type: str = "1"
    user_agent: str = "okhttp/3.14.9"
    game_id: str = "730"


class YoupinSettings(BaseModel):
    pc_api_base: str = "https://pc-api.youpin898.com"
    app_api_base: str = "https://api.youpin898.com"
    uk_ttl_s: int = Field(30, ge=5)
    page_sleep_s: float = Field(2.5, ge=0)
    pc_headers: YoupinPcHeaders = YoupinPcHeaders()
    app_headers: YoupinAppHeaders = YoupinAppHeaders()  # 当前未使用；应急后备，见 YoupinAppHeaders 的说明


class NtfySettings(BaseModel):
    base_url: str = "https://ntfy.sh"
    priority_rank1: int = Field(4, ge=1, le=5)
    priority_rank2: int = Field(3, ge=1, le=5)
    priority_system: int = Field(4, ge=1, le=5)
    click_url_template: str = (
        "https://www.youpin898.com/market/goods-list?listType=20&templateId={template_id}&gameId=730"
    )


class SteamDTSettings(BaseModel):
    """SteamDT 开放平台（多平台行情源）。API key 不在这里——它和悠悠 token 一样走 SecretStore
    （系统钥匙串，失败才退回 data/.env），绝不进 settings.toml，更不进仓库。"""

    base_url: str = "https://open.steamdt.com"
    # 一次批量询价塞多少个 marketHashName。**服务端硬上限 100**（文档 minItems:1 / maxItems:100；
    # 超了只回一句 errorCode 100002「参数错误」，不会告诉你是哪个参数）。配额 1 次/分钟，所以
    # 就该顶着上限走，批越小跑得越久。
    batch_size: int = Field(100, ge=1, le=100)


def _hhmm(v: str) -> str:
    hh, sep, mm = str(v).partition(":")
    if sep != ":" or not (hh.isdigit() and mm.isdigit()) or not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
        raise ValueError(f"expected HH:MM (00:00-23:59), got {v!r}")
    return f"{int(hh):02d}:{int(mm):02d}"


class BriefSettings(BaseModel):
    """每日简报（M3a，spec §7a）。时间按 settings.timezone 的本地时间理解。

    简报是一张「前一天收盘时排在前 top_rank 名」的表，表里有变动（新进、名次变化、榜首价变化）
    才发；连续 quiet_days 天没发过，就发一行兜底，证明服务还活着。top_rank 和实时告警的
    alerts.enter_rank 分开（ADR-0008：实时只推第 1 名，第 2 名看第二天的表）。"""

    enabled: bool = True
    send_at: str = "00:00"
    snapshot_at: str = "23:50"
    top_rank: int = Field(2, ge=1)
    quiet_days: int = Field(7, ge=1)

    @field_validator("send_at", "snapshot_at")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        return _hhmm(v)

    def hm(self, which: str) -> tuple[int, int]:
        h, m = getattr(self, which).split(":")
        return int(h), int(m)


# 卖出成交时平台扣的交易手续费。用户 2026-09-27 按自己的真实成交给出：悠悠 1%（4 笔），
# BUFF 1.5%（2026-04-14 起统一；之前刀是 2.5%）。只认这两个平台——实际卖出只在这两家。
DEFAULT_SELL_FEES = {"YOUPIN": 0.01, "BUFF": 0.015}
MAX_FEE = 0.2   # 超过 20% 的费率多半是填错了（比如把 1% 写成 1）


class ValuationSettings(BaseModel):
    """每日估值（价位 spec §8）：给活跃求购单的饰品存悠悠日线，算价位标签和扣费差价。

    一期只能改 settings.toml，看板只读展示。费率、让价、提现费都按「卖出到手」的方向扣。"""

    enabled: bool = True
    run_at: str = "00:30"
    # 单品 K 线请求限速。SteamDT 配额 120 次/分钟，留一半给看板。
    rate_per_min: int = Field(60, ge=1, le=120)
    min_bars: int = Field(60, ge=30)
    undercut: float = Field(0.01, ge=0, le=0.2)        # 挂卖让价
    withdraw_fee: float = Field(0.01, ge=0, le=MAX_FEE)
    target_margin: float = Field(0.15, ge=0, le=5)
    min_profit: float = Field(1.0, ge=0)               # 元
    bad_quantile: float = Field(0.10, gt=0, le=0.5)
    # 二期建议（价位 spec §9.5）。2026-10-09 回测「7 天坏情况」通过，默认打开；关掉时接口不给 advice
    advice: bool = True
    fees: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_SELL_FEES))
    # 到手价只按这些平台的在售价算（intent 2026-11-23：只在悠悠出货）；要按「哪家到手多」算就写 ["YOUPIN", "BUFF"]
    exit_platforms: list[str] = Field(default_factory=lambda: ["YOUPIN"], min_length=1)
    # 选品：现价比近 rebound_window 天最低收盘高出 rebound_max 以上，判「观望」（R3 回测，2026-11-23）
    rebound_window: int = Field(60, ge=20, le=365)
    rebound_max: float = Field(0.30, gt=0, le=5)

    @field_validator("run_at")
    @classmethod
    def _run_at(cls, v: str) -> str:
        return _hhmm(v)

    @field_validator("exit_platforms")
    @classmethod
    def _exit_platforms(cls, v: list[str]) -> list[str]:
        bad = [p for p in v if p not in DEFAULT_SELL_FEES]
        if bad:
            raise ValueError(f"unknown platform(s) {bad}; expected some of {sorted(DEFAULT_SELL_FEES)}")
        return v

    @field_validator("fees")
    @classmethod
    def _fees(cls, v: dict[str, float]) -> dict[str, float]:
        for platform, rate in v.items():
            if platform not in DEFAULT_SELL_FEES:
                raise ValueError(f"unknown platform {platform!r}; expected one of {sorted(DEFAULT_SELL_FEES)}")
            if not 0 <= rate <= MAX_FEE:
                raise ValueError(f"fee for {platform} must be within 0..{MAX_FEE}, got {rate}")
        # 只写了一个平台时，另一个仍按默认：漏写不能变成「这家不收费」
        return {**DEFAULT_SELL_FEES, **v}

    def hm(self) -> tuple[int, int]:
        h, m = self.run_at.split(":")
        return int(h), int(m)


class FillsSettings(BaseModel):
    """求购成交、交易中推送（成交推送 spec §8）：单子从「求购中」消失后，去「已完成」「暂停中」里按单号
    查它的去向。这两个列表不按时间排，只能一页页翻（每页 40 条，2026-10-02 各有 250 条以上）。"""

    enabled: bool = True
    max_pages: int = Field(15, ge=1, le=50)          # 每轮每个列表最多翻几页
    resolve_window_h: int = Field(24, ge=1, le=168)  # 消失后多久内继续查去向
    notify_window_h: int = Field(24, ge=1, le=168)   # 成交后多久内还推送（覆盖 Mac 睡一夜）


class PnlSettings(BaseModel):
    """盈亏统计（盈亏 spec §8、ADR-0024）：每天 run_at 同步悠悠买卖订单、库存、租出，先进先出配对，
    写持仓快照。只读：只用查询接口。"""

    enabled: bool = True
    run_at: str = "00:45"                             # 算前一天（23:50 的价格快照之后、00:30 估值之后）
    basis: Literal["market", "net"] = "market"        # 浮动默认口径：按市价 / 按到手价（用户 2026-11-24 定）
    max_pages: int = Field(10, ge=1, le=200)          # 每日同步每个列表最多翻几页（首次导入另用 200）
    detail_per_run: int = Field(10, ge=0, le=100)     # 每天最多补几张卖出详情（到手价）
    # 异常价只标不剔（spec §4.4）。2026-11-24 试算 258 个持仓模板：在售 < 3 件的 1 个；悠悠 / BUFF 在售价比的
    # 第 5、95 百分位 0.88、1.21，超出 1.5 倍的 3 个
    thin_count: int = Field(3, ge=1, le=50)
    spread_ratio: float = Field(1.5, gt=1, le=10)

    @field_validator("run_at")
    @classmethod
    def _run_at(cls, v: str) -> str:
        return _hhmm(v)

    def hm(self) -> tuple[int, int]:
        h, m = self.run_at.split(":")
        return int(h), int(m)


class MarketAlertSettings(BaseModel):
    """市价提醒（市价提醒 spec §5）：榜首高于悠悠在售价、或我的价贴近在售价时推送。推送前用公开接口
    核对一次实时在售价（每轮最多 verify_max 张，计入每小时请求上限）。"""

    enabled: bool = True
    # 「榜首高于在售」：第 2～rank_always 名一律提醒；rank_always+1～rank_max 名，我的价 ≥ 在售最低 ×
    # inverted_ratio 才提醒（用户 2026-10-15：「改成前三名或者达到一个比例才提醒」）。0.85 的来历：16 笔
    # 真实成交的「求购价 ÷ 当天收盘」有四分之三在 0.85 以上（价位回测报告的成交描述）
    rank_always: int = Field(3, ge=2, le=50)
    rank_max: int = Field(10, ge=2, le=50)
    inverted_ratio: float = Field(0.85, gt=0, le=2)
    near_ratio: float = Field(0.9, gt=0, le=2)         # 「贴近市价」：我的价 ≥ 在售最低 × near_ratio
    verify_max: int = Field(5, ge=1, le=20)            # 每轮推送前最多实时核对几张


class PausedCheckSettings(BaseModel):
    """充值前检查暂停单（暂停单 spec §7）：看板「检查一次」和 `check-paused` 共用。只读、不推送。"""

    near_gap: float = Field(0.05, gt=0, le=1)       # 离第 1 差多少以内算「差 5% 内」
    sell_ratio: float = Field(0.9, gt=0, le=2)      # 我的价 ≥ 在售最低 × 这个值算「贴近在售」（同市价提醒 near_ratio）
    verify_max: int = Field(30, ge=0, le=100)       # 一次检查最多用悠悠在售接口实时核对几张
    old_days: int = Field(60, ge=1, le=3650)        # 暂停超过几天用醒目标签（11-24：07-26 那批 121 天，10 月初的 47 天）
    max_pages: int = Field(30, ge=1, le=100)        # 每个号最多翻几页暂停单（每页 40 张）
    stale_h: float = Field(2, gt=0, le=168)         # 结果超过几小时提示「再查一次」


class BuffSettings(BaseModel):
    """BUFF 求购监控（BUFF spec §11）。只读、慢：一轮 15 分钟，同一个号两次请求至少隔 5 秒。"""

    base: str = "https://buff.163.com"
    round_interval_min: int = Field(15, ge=5, le=240)
    request_gap_s: float = Field(6.0, ge=5)           # 同一个号两次请求的间隔（另加 30% 抖动）
    queue_per_round: int = Field(10, ge=0, le=50)     # 一轮最多查几次队列
    queue_max_pages: int = Field(3, ge=1, le=10)      # 一张单最多翻几页队列（每页 100）
    pause_hours: float = Field(2.0, gt=0, le=48)      # 撞到限流、验证码后暂停多久
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    )
    accept_language: str = "zh-CN,zh;q=0.9"


class DashboardSettings(BaseModel):
    """看板上的写法，只影响看板（不影响推送、简报）。"""

    # 顶栏账号切换、表格标签写「悠悠」「BUFF」，不写昵称；同一平台有几个号时写「悠悠 · 简称」
    platform_names: bool = False


class WebSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(8765, ge=1, le=65535)


class Settings(BaseModel):
    data_dir: str = "data"
    timezone: str = "Asia/Shanghai"
    polling: PollingSettings = PollingSettings()
    alerts: AlertSettings = AlertSettings()
    youpin: YoupinSettings = YoupinSettings()
    ntfy: NtfySettings = NtfySettings()
    steamdt: SteamDTSettings = SteamDTSettings()
    web: WebSettings = WebSettings()
    brief: BriefSettings = BriefSettings()
    valuation: ValuationSettings = ValuationSettings()
    fills: FillsSettings = FillsSettings()
    market_alerts: MarketAlertSettings = MarketAlertSettings()
    pnl: PnlSettings = PnlSettings()
    paused_check: PausedCheckSettings = PausedCheckSettings()
    buff: BuffSettings = BuffSettings()
    dashboard: DashboardSettings = DashboardSettings()

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


EXAMPLE_PATH = Path("settings.example.toml")


def default_settings_path() -> Path:
    p = Path("settings.toml")
    if not p.exists() and EXAMPLE_PATH.exists():
        shutil.copy(EXAMPLE_PATH, p)
    return p


def _find_unknown_keys(raw: dict, model: type[BaseModel], prefix: str = "") -> list[str]:
    """Recursively collect dotted key paths present in `raw` that `model` (and its
    nested BaseModel fields, e.g. polling/alerts/youpin.pc_headers) doesn't declare.
    Pydantic already ignores unknown fields by default rather than raising, so this
    doesn't change what load_settings accepts - it exists so load_settings can warn
    about them instead of dropping a stale settings.toml entry (e.g. a PollingSettings
    field removed after the monitor rework) with no trace at all."""
    unknown: list[str] = []
    if not isinstance(raw, dict):
        return unknown
    fields = model.model_fields
    for key, value in raw.items():
        path = f"{prefix}{key}"
        field = fields.get(key)
        if field is None:
            unknown.append(path)
            continue
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel) and isinstance(value, dict):
            unknown.extend(_find_unknown_keys(value, annotation, prefix=f"{path}."))
    return unknown


def load_settings(path: Path | None = None) -> Settings:
    path = path or default_settings_path()
    if not path.exists():
        return Settings()
    with path.open("rb") as f:
        raw = tomllib.load(f)
    unknown = _find_unknown_keys(raw, Settings)
    if unknown:
        log.warning("%s: ignoring unknown setting(s) not recognized by this version: %s",
                   path, ", ".join(sorted(unknown)))
    return Settings.model_validate(raw)


def save_settings(settings: Settings, path: Path | None = None) -> None:
    path = path or default_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        tomli_w.dump(settings.model_dump(mode="json"), f)
