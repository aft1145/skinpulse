# SkinPulse 设计规格（Design Spec）

- 起草：2026-08-23（与发起人逐段确认）
- **最后按实现核对：2026-09-23**。这是活文档：实现偏离设计时，在同一个提交里改这里；为什么改，记在 [docs/decisions/](../../decisions/)。
- 依据：[前期调研报告](../../research/2026-08-23-preliminary-research.md)、[第一手接口验证](../../research/2026-08-23-firsthand-api-probe.md)；接口真实行为以 [悠悠对接记录](../../research/2026-09-06-live-api-findings.md)、[SteamDT / PushPlus / ntfy 对接记录](../../research/2026-09-23-steamdt-pushplus-ntfy-findings.md) 为准

标记约定：**〔未实现〕** = 仍是计划；**〔待决〕** = 等用户决定，见 docs/intent/（未公开）。

## 0. 一句话

一个跨平台（Windows / macOS / NAS）的 Python 单进程服务：监控我在悠悠有品挂出的全部求购单在**同条件子队列**中的排名，升到第 1 名时立即推送到微信（PushPlus，ntfy 兜底），第 2 名等每天 00:00 的前 2 名表（[ADR-0008](../../decisions/0008-realtime-alert-rank1-brief-top2.md)）；同时用 SteamDT 行情做多平台比价、走势信号、大盘展示，并提供本地 Web 看板。M3a 是每日简报（§7a），LLM 问答是 M3b。

## 1. 已确认的需求与约束

| # | 决策 | 说明 |
|---|---|---|
| R1 | 排名定义 = 同筛选条件子队列内位置 | "不限"是独立队列；悠悠上报的 `rank` 已查实就是这个语义 |
| R2 | 只提醒"升到第 1 名" | 不做被压价提醒、1↔2 变化提醒、重复提醒（数据与状态机已支持，后续可开）；实时阈值原为"进入前 2"，2026-09-24 收窄为 `enter_rank=1`，第 2 名改进每天 00:00 的简报表（[ADR-0008](../../decisions/0008-realtime-alert-rank1-brief-top2.md)） |
| R3 | 看板提供"全部求购按排名升序总览" | 非提醒功能 |
| R4 | 我的求购清单自动同步 | 原定短信登录；实测被滑块挡死，改为用户从浏览器复制 token（§3.2）。失效时提醒重设 |
| R5 | 规模 >50 单；延迟 ≥15 min 可接受 | 轮询参数全部可配；现在实际 5 分钟一轮 |
| R6 | 行情主源 SteamDT + CSQAQ（免费 key） | 实际只接了 SteamDT，已覆盖比价/走势/指数（[ADR-0003](../../decisions/0003-market-data-steamdt-only.md)）；CSQAQ 不接（[ADR-0006](../../decisions/0006-no-csqaq.md)） |
| R7 | 交付：本地 Web 看板 + 手机推送；Python | 推送原定 ntfy，现为 PushPlus（微信）为主、ntfy 兜底（[ADR-0002](../../decisions/0002-push-via-wechat-pushplus.md)）。跨 Win（现在）/ Mac（未来）/ NAS；手机 Android |
| R8 | 先用悠悠小号运行，稳定后切主号 | 程序只调用只读接口，不做自动改价/下单 |
| R9 | LLM 支持 OpenAI 兼容 / Anthropic / Gemini | 多 provider 可配，含降级顺序〔未实现，M3b；先做 OpenAI 兼容〕 |
| R10 | 分析能力：多平台比价、大盘指数+涨跌榜、单品走势+均线/动量、LLM 日报/问答 | 只产出信息，不产出自动交易。涨跌榜不做（依赖 CSQAQ，[ADR-0006](../../decisions/0006-no-csqaq.md)）；日报改为每天 00:00 的「前 2 名表」、有变动才发、不用 LLM（§7a） |

## 2. 总体架构

**Python 单体**：FastAPI（JSON API + 提交在仓库里的 Vue 前端产物）+ APScheduler + SQLite(WAL) + 适配器层。

```
skinpulse/
  __main__.py / cli.py   # python -m skinpulse <命令>（Typer）
  config.py              # settings.toml 加载、校验、热更新
  secrets.py             # SecretStore：系统钥匙串，失败退回 data/.env
  runtime.py             # 所有命令共用的装配（db、客户端、notifier）
  scheduler.py           # APScheduler：监控轮次、心跳；每轮一行摘要日志
  db/                    # SQLAlchemy 模型（UtcDateTime）+ Alembic 迁移
  adapters/
    youpin/              # uk 生成与加解密、匿名 + 账号通道、错误码
    steamdt/             # 价格、7 日均价、饰品目录、单品/大盘 K 线、大盘指数
    pushplus/            # 微信推送（主）
    ntfy/                # ntfy 推送（兜底）
  core/                  # 纯逻辑：rank（队列键、排名）、alerts（状态机）、planner、ratelimit、messages（文案）
  analysis/              # 纯逻辑：platform_gap（多平台价差）、signals（均线/涨跌）、kline（纵轴范围与影线截断）
  services/              # monitor（每轮主流程）、account（同步）、notifier（多通道）、market（目录与对账）、market_data（看板缓存）、status
  web/                   # 路由、/api/*（overview、item、market、analysis）、剩下的旧模板；前端产物在 web/dist
tests/                   # 单元/回放/集成 + fixtures（真实响应，脱敏）
data/                    # 运行时：skinpulse.db、日志、缓存（不提交）
```

模块边界：适配器只做"HTTP → 规范化 dataclass"并处理该源的错误码与限流；`core` 与 `analysis` 不依赖网络；`web` 只读数据库和缓存，**绝不直接请求悠悠**。

〔未实现〕`adapters/llm`（M3b）、BUFF / Steam 直连校验（SteamDT 已覆盖，暂无必要）。

## 3. 悠悠有品适配层

### 3.1 两条通道

App 通道被版本闸门锁死（HTTP 429 + 85100），**账号请求也走 PC 头**（`platform=pc, appType=1`）。详见对接记录 §1。

| 通道 | 鉴权 | 调用 | 频率 | 风险落点 |
|---|---|---|---|---|
| 匿名通道 | 仅 `uk`（`POST /api/deviceW2`，AES-ECB+RSA 加密，缓存 30 s） | `getTemplatePurchaseOrderListPC`（**硬上限 20 条**，分页参数被忽略）、`queryOnSaleCommodityList` | 实时核对、抽样核对、市场价刷新 | IP 层面 |
| 账号通道 | `authorization: Bearer <token>` | `searchPurchaseOrderList`（我的全部求购单 + `rank` + 子队列榜首价）、`getUserInfo`（心跳）、带 token 的公开队列深取（`pageSize` 此时才生效） | 每轮 1 次同步；心跳 30 min；深取只在匿名 20 行装不下时 | 账号层面 |

请求头模板全部来自 `settings.toml [youpin]`，版本号不写死。

### 3.2 登录

- 短信登录在发短信前要求过滑块（`1110205 SLIDER`），**不绕过**。`login` 命令保留但目前走不通。
- 实际方式：用户在浏览器登录 → 复制请求头 `authorization`（JWT）→ 在看板「系统」页的「更新 token」粘贴（2026-09-27 起），或者终端 `set-token --from-file`。两条路都先校验再保存，存入系统钥匙串；看板上换的立即生效，不用重启。
- token 寿命以 JWT `exp` 为准（勾"10 天免登录"时为签发后 10 天）。失效（84101）→ 标记账号通道不可用、推送"登录已失效"（发现时立刻推一条，仍失效就每 24 小时再推一条，token 恢复后清零；写死不进设置）；匿名通道继续工作。

### 3.3 错误码 → 行为

| 码 | 含义 | 行为 |
|---|---|---|
| 84101 | 登录失效 | 停账号通道 + 提醒 |
| 84103 | 需登录（翻页/深页） | 记录，不重试 |
| 84104 | 风控 | 暂停该通道 30 min，之后 60/120 min 指数，上限 4 h |
| 85100 | 版本过低 / 缺 uk | 先刷新 uk 重试 1 次；仍失败则标记"需更新 AppVersion 配置"并提醒。注意它可能伴随 HTTP 429，以业务码为准 |
| HTTP 405 | uk 失效 | 刷新 uk，等 60 s |
| HTTP 429 / 空响应 | 限流 | 5 min × 2ⁿ，上限 30 min |
| 5050 | 机房 IP/版本 | 提醒检查网络环境 |

## 4. 排名：用悠悠上报的 rank，推送前实时核对

原设计是每轮逐张单抓公开队列、自己算排名。真实账号（371 张单）一轮要 18 分钟以上，而 `searchPurchaseOrderList` 本身就带着 `rank` 与子队列榜首价，于是改为以上报值为准（[ADR-0001](../../decisions/0001-rank-from-youpin-with-live-confirm.md)）。

### 4.1 每一轮（默认 5 分钟，夜间 0–8 点周期 ×2）

```
1. 同步（pass A）      1 次认证请求，拿全部求购单 + 悠悠上报的 rank / 榜首价
2. 实时核对            对「排名进了告警带、且还没在告警中」的单，用公开队列现算排名和榜首价
                       （上报 rank 会滞后几分钟）；匿名 20 行里没找到自己时，用认证请求深取
                       上限 live_confirm_max；核对失败退回上报值，绝不因此变成"不告警"
3. 别处买盘            同一批单，查 SteamDT 的 BUFF/C5 最高求购价，写进告警正文；查不到只是少一行
4. 写快照 + 告警状态机  用第 2 步的实时值（有的话）判断是否升到第 `enter_rank` 名（§5.1）
5. 抽样核对（pass B）   每轮 verify_sample_size 张，和公开队列对一遍，结果写 rank_check
                       ——角色是「rank 语义变化哨兵」
6. 市场价刷新          每轮 sell_sample_size 张，查悠悠在售最低价（sell_min / sell_depth）
7. 推送                FanoutNotifier：PushPlus + ntfy 并行，任一收下即算发出
```

另有每 30 分钟一次心跳。每轮日志摘要里的 `live_corrected=` 是上报 rank 滞后被纠正的次数。

### 4.2 队列键与本地排名计算（实时核对、抽样核对用）

- `queue_key = (templateId, norm(abradeText), typeId, norm(specialStyle), norm(fadeText))`；`norm` 把 `None`/空/"不限" 统一为 `"ANY"`。映射是纯函数 `core.rank.queue_key_of`。
- 过滤公开列表中 `queue_key` 相同的条目，按价格降序、同价保持接口返回顺序；`rank` = 我的 `userId` 在子队列中的 1-based 位置。
- 抽样核对选单：上报排名 ≤ `fast_lane_rank_threshold` 的每轮必抽，其余按"最久没核对过"轮换。市场价刷新用同一套优先级、独立轮换。

### 4.3 已放弃的设计

- 逐张单轮询公开队列算排名（太慢，见上）。
- 快慢档调度与 biddingPrice 预筛（原 §4.2/§4.5）：每轮只剩 1 次认证请求，不再需要分档。`MyBuyOrder.lane` 因此恒为 `normal`，是待清理的遗留字段。

## 5. 告警

### 5.1 状态机（每张求购单一个实例，指纹 = purchaseNo）

```
Normal --(rank<=enter_rank, 连续 confirm_rounds=2 轮)--> Alerting  [发送 ENTER_TOP2]
Alerting --(rank>=exit_rank 或 单子消失)--> Normal        [不发送]
enter_rank < rank < exit_rank：死区，不改变状态（当前配置 enter_rank=1、exit_rank=4，即 rank 2、3 是死区；ADR-0008）
rank unknown（接口失败/字段缺失）：保持状态，不计入连续计数
```

- **差价过滤**在状态机之前：榜首价 / 我的价 ≥ `max_gap_ratio`（默认 3.0）的单当作"没进前 `enter_rank` 名"处理（连续计数清零、Alerting 退回 Normal），不在事件产生后丢弃——否则订单会卡在 Alerting 永不重新触发。
- 推送失败：状态退回 Normal，满 `confirm_rounds` 轮后若仍在前 `enter_rank` 名则重推。
- **`enter_rank` 变小**（2026-09-24，ADR-0008 从 2 改成 1）：Alerting 且 `last_rank > enter_rank` 的单复位成 Normal（连续计数 0），否则它们要先掉到 `exit_rank` 以后才重新布防，升到第 1 名时什么都不推。两处做：服务启动时（`scheduler.build_scheduler`，紧跟在 pending 清扫之后；不知道旧值，所以每次都跑，没有这种单就什么都不改），看板改设置把 `enter_rank` 调小时（`Monitor.apply_settings`）。`build_runtime` 不做：一次性 CLI 命令不能改告警状态。只记一行 INFO，写数量、不写单号。它只把 Alerting 改成 Normal、不碰 `notification_log`，在途的 pending 照旧由本轮 `_notify` 或启动清扫收尾。已知副作用：`enter_rank=1` 时，在第 1 名推过、现在排第 2～3 名的单每次重启都会被复位，之后再升回第 1 名会再推一次——重启少见，重复一次好过漏掉。
- 预留事件（默认关闭）：`OUTBID`、`RANK_CHANGE`（1↔2）、`REPEAT`。

### 5.2 去抖与合并

- 同一轮内多单触发合并为一条通知；全局每小时上限 `hourly_cap`（默认 30；2026-11-23 改为 10）。
- 发送前写 `notification_log(status=pending)`，发完改 `sent` / `failed`。重启时由调度器（且只由调度器）把在途的 pending 判死，避免重复推送。

### 5.3 推送

| 通道 | 角色 | 说明 |
|---|---|---|
| PushPlus（微信公众号消息） | **主** | 微信本身被系统放行，不需要给任何 App 后台权限。返回 `900` 账号受限时绝不重试 |
| ntfy | 兜底，并行发送 | 国内 FCM 不通、App 不常驻就收不到、ntfy.sh 只存 12 h——**从未实时送达过**。连接阶段失败重试 2 次（3 s、10 s），读超时和 5xx 不重试（可能已落地） |

- 标题：`求购第{rank}名：{中文名} [{队列条件}]`（中文名用简写，`core.messages.short_item_name`，见 §7a.3）；多单合并时，`enter_rank==1` 为 `N 张求购升到第 1 名`，否则为 `N 张求购进入前 {enter_rank}`。正文：我的价 / 榜首价 / 市场价 / 别处买盘；**末行「触发于 MM-DD HH:MM」**（本地时间，排名被观察到的时刻）。
- 每条通道在 `/status` 单独记健康状态；每轮摘要带 `notify_failed=N`。
- 两个服务返回成功都只代表"服务端收下"，**送达以用户在手机上看到为准**。PushPlus 真实送达状态需要它的开放接口凭证 + IP 白名单，〔未实现〕。

### 5.4 轮询参数（settings.toml `[polling]` / `[alerts]`）

现行默认值见 `settings.example.toml`，含义与推荐范围见 [使用手册](../../guide.md#轮询参数)。要点：`round_interval_min=5`、夜间 ×2、`verify_sample_size=5`、`sell_sample_size=5`、`deep_page_size=200`、`hourly_request_cap=300`、`enter_rank/exit_rank=2/4`、`confirm_rounds=2`、`max_gap_ratio=3.0`、`live_confirm_max=10`。

已知口径问题：`hourly_request_cap` 只统计抽样核对、实时核对、市场价刷新这几类匿名请求。

## 6. 行情数据层

### 6.1 数据源

| 源 | 接口 | 用途 | 状态 |
|---|---|---|---|
| SteamDT | `price/batch`（100 件/次，1 次/min）、`price/single`（60/min）、`price/avg`、`/base`（1 次/天）、单品 K 线、大盘 K 线与最新指数 | 多平台比价、告警里的别处买盘、单品走势、大盘 | ✅ 已接 |
| 悠悠（匿名） | `queryOnSaleCommodityList` | 在售最低价（看板"市场价"） | ✅ 已接 |
| CSQAQ | `info/good`、`info/chart`、`get_rank_list`、`current_data` | 涨跌榜、23 个子指数（其余 SteamDT 已覆盖） | 不接（[ADR-0006](../../decisions/0006-no-csqaq.md)） |
| BUFF / Steam 直连 | — | 原定做校验 | 不做（SteamDT 已给出多平台价） |

映射走 ID 不走名字：SteamDT `/base` 里 YOUPIN 的 itemId 就是悠悠 templateId。比价参照：卖方 BUFF/C5/HALOSKINS，买方 BUFF/C5；**Steam 不纳入参照**（15% 手续费、钱包结算，不是同一市场）。

### 6.2 取数方式

- 现在：看板**按需**向 SteamDT 取，经 `services/market_data.py` 缓存（价格 5 min、单品日线 1 h、大盘最新 10 min、大盘日线 6 h）；撞配额时返回旧数据并标明过期。
- 自有历史入库：每天 23:50 给活跃求购单涉及的模板存一份多平台价格（`price_daily`，§7a.1；[ADR-0007](../../decisions/0007-market-history-with-m3-brief.md)）。原定的 Tier A/B/C 分层不做；热门池、全量日线等有消费方时再议。

### 6.3 数据模型

已有表：

- `account`：user_id、nickname、phone_masked、token_valid、last_sync_at。
- `item`：hash_name、cn_name、youpin_template_id（SteamDT 目录同步写入）。
- `my_buy_order`：purchase_no、template_id、价格/数量、条件字段、queue_key、active；上报值 `reported_rank`、`top_bid`；市场价 `sell_min`、`sell_depth`、`sell_checked_at`；别处买盘 `ref_bid`、`ref_bid_platforms`。遗留死字段：`lane`、`last_rank`。
- `rank_snapshot`：purchase_no、ts、rank、beyond、top1/top2 价、sample_size、sell_min、sell_depth。
- `rank_check`：抽样核对记录（reported_rank、computed_rank、agreed）。
- `alert_state`：purchase_no、state、streak、last_rank。
- `notification_log`：fingerprint、event、rank、payload、status（pending / sent / failed / suppressed）。
- `system_status`：各通道 last_ok_at、最近错误、暂停到期。

- `price_daily`：date（本地日期）、template_id、market_hash_name、platform、sell_price、sell_count、bidding_price、bidding_count、update_time、fetched_at；唯一键 (date, template_id, platform)。价格 None = 该平台无数据。
- `brief_log`：date（覆盖的本地日期，唯一）、decision（sent / quiet_sent / skipped / no_data / no_channel / failed）、reasons_json（触发了什么、或为什么没发）、title、body、created_at、sent_at。

`daily_bar`、`index_daily`、`valuation`、`order_event`：价位一期建（迁移 0008，[价位 spec §3](2026-09-27-price-position.md)）。〔未实现，M3c〕`event`、Parquet 导出。原定的 `quote` 由 `price_daily` 取代。

时间一律 UTC aware 入库（`UtcDateTime` 拒绝 naive），显示前转 `settings.timezone`。

## 7. 分析层（只产出信息）

- 多平台价差（`analysis/platform_gap`）：悠悠相对参照平台中位数的卖价差、买价差，全量对账汇总。首轮结论：悠悠求购价比 BUFF/C5 中位数低 9.1%。
- 单品信号（`analysis/signals`）：MA7/MA30 偏离、7/30 日涨跌，输出上升/下降/横盘。K 线不含成交量，所以没有量比。
- 简报的前 N 名表与判定（`analysis/brief`，见 §7a）。
- 价位与扣费差价（`analysis/valuation`，2026-09-28 一期完成）：一年分位、趋势、相对大盘、7 天坏情况、出手难易、两平台扣费后的净差价。见 [价位 spec](2026-09-27-price-position.md)、[ADR-0014](../../decisions/0014-valuation-advice-three-phases.md)。
- 〔未实现〕挂刀比例（需要 Steam 价，而 Steam 被排除在参照之外，需重新评估）、自建等权指数（M3c）、事件表。

## 7a. 每日简报（M3a）

来源：M3a intent（未公开）（2026-09-23 接受）。**不含 LLM**。

### 7a.1 两个每日任务

| 任务 | 时间（`settings.timezone`） | 做什么 |
|---|---|---|
| 价格快照 | 每天 23:50 | 取所有**活跃求购单**涉及的模板 → `resolve_names` → SteamDT `price/batch`（每批 ≤100，批间等 65 s，425 个模板约 5 批）→ 每个模板每个平台一行写 `price_daily`。映射不到的模板计数写日志 |
| 简报 | 每天 00:00 | 覆盖刚结束的那一天 D（本地日界）：算前 N 名表 → 判定要不要发 → 组装 → 推送 → 写 `brief_log` |

- 调度器本身用 UTC，这两个任务用 cron 触发并显式带 `settings.timezone`。
- **补发**：服务启动时另跑一次性任务 `daily_brief_catchup`——取最近一个已到点的 `send_at`（今天的还没到就取昨天的，所以 `send_at` 在晚上时刚过零点启动也算），没过 12 小时就补判它对应的那天（00:00 电脑关着、早上才启动的情况）；超过 12 小时就算了，只记一行日志。`brief_log` 按日期唯一，已有记录直接返回，同一天不重复发；补发和 00:00 的 cron 共用一把进程内锁。00:00 任务上的 `misfire_grace_time` 12 小时 + `coalesce` 只管「进程活着但到点时睡着了（休眠）」——APScheduler 的内存 jobstore 在注册时才算下一次触发，重启的进程不会自己补跑错过的那次。
- 快照**不补**：错过 23:50～23:59 就算了，第二天补拍会把今天的价格记成昨天的历史（同理，`price-snapshot` 命令也只能拍今天）。
- 两个任务和监控轮次互不阻塞（`max_instances=1`，失败只记日志）。快照失败时简报照常判定，在售列写「—」。

### 7a.2 前 N 名表怎么从快照算

2026-09-24 改版（[ADR-0008](../../decisions/0008-realtime-alert-rank1-brief-top2.md)）：简报只有一张表。N = `brief.top_rank`（默认 2），和实时告警的 `alerts.enter_rank` 分开（实时只推第 1 名，第 2 名第二天看表）。

对每张**当前活跃**的单，在 `rank_snapshot` 里取（rank 为 None 的快照不参与，未知不等于落后）：

- **收盘**：D 内最后一条有排名的快照。
- **开盘**：D 开始之前最后一条有排名的快照，即前一天收盘；监控停过就继续往前找。新单没有开盘。

**进表**：收盘名次 ≤ `top_rank`，且不是「难以成交」（`analysis/reach.unreachable`：离第 1 或离市价太远，见 [难以成交 spec](2026-11-25-hard-to-fill.md)；和实时告警 `Monitor._is_eligible_for_alert` 同一条规则；判断不了时不算被挡）。被挡的单不进表。**排序**：名次升序，同名次按我的价降序，再按名称。

**变动**（和开盘比，一行可以同时有几种）：

| 变动 | 规则 | 显示 |
|---|---|---|
| 新进 | 没有开盘（新单），或开盘名次 > `top_rank` | `🆕新进` |
| 名次变化 | 不是新进，且开盘名次 ≠ 收盘名次 | `名次 3→2` |
| 榜首价变化 | 收盘名次 ≠ 1、有开盘、开盘和收盘的榜首价都已知（> 0；0 = 没有数据）且不相等。收盘第 1 时榜首就是我，不标（用户没选「我的价变了」） | `榜首 250→248` |

「离开前 N」既不进表，也不触发。

### 7a.3 在售列与饰品名简写

**在售**：D 当天 23:50 价格快照（`price_daily`）里悠悠（`platform = 'YOUPIN'`）的 `sell_price`，按模板取。那天没拍到、或值为空（含 0），写 `—`（`core.messages.SELL_UNKNOWN`），**绝不填 0**。在售不算变动。

**在售脚注**：表下固定一行，写在售价是什么时候拍的。D 有悠悠快照：`在售：MM-DD HH:MM 悠悠价格快照`，时间是 D 当天悠悠那些行里最晚的 `fetched_at`（`settings.timezone` 本地时间）；没有：`在售：MM-DD 没有价格快照`。原因：快照只补当天缺的模板，白天手动跑过一次 `price-snapshot`，23:50 那次就全跳过了，在售列其实是白天的价格；没拍、没配 SteamDT key 也不能只剩一列「—」。脚注由 `services.brief.sell_note` 算，`core.messages` 只负责排版。

**饰品名简写**（`core.messages.short_item_name`，Task 14 起实时告警也用）：全角括号 `（）` 换成 `()`；磨损 `崭新出厂/略有磨损/久经沙场/破损不堪/战痕累累` 写 `崭新/略磨/久经/破损/战痕`；`StatTrak™` 写 `ST`。例：`沙漠之鹰（StatTrak™） | 后发制人 (崭新出厂)` → `沙漠之鹰(ST) | 后发制人 (崭新)`。其余原样。

改版前的「离前 2 最近」「排名变化最大」「我求购的饰品价格」三块及其配置（`near_top_count` / `movers_count` / `price_movers_count`）已删除；23:50 价格快照保留，给在售列用，也留作历史（ADR-0007）。

### 7a.4 发不发

自上而下，命中第一条就停：

| # | 情况 | 动作 | `brief_log.decision` |
|---|---|---|---|
| 1 | D 内没有任何单有排名快照 | 不发（监控那天没在跑） | `no_data` |
| 2 | 表里任一行有变动 | 发前 N 名表 | `sent`（推送失败为 `failed`） |
| 3 | 距上一次**发出**（表或兜底）已满 `quiet_days`（7）天 | 发一行「近 7 天前 N 名单没有变动（新进 / 名次 / 榜首价），监控在正常运行」 | `quiet_sent` |
| 4 | 其余 | 不发，记下判断依据 | `skipped` |

- 要发但没有可用推送通道：`no_channel`。
- `sent` 的 `reasons_json` 带 `new` / `rank_changed` / `top1_changed` 三个计数和 `summary`。
- 「满 7 天」从上一次发出算；从没发过就从第一条 `brief_log` 算，全新安装不会第一天就发兜底。

### 7a.5 推送与格式

- 走现有 `FanoutNotifier`（PushPlus + ntfy），**不占**告警的每小时上限；每条通道的结果照常记到 `system_status`。
- 推送之后**先提交 `brief_log`**，再记各通道状态：记状态失败（如 database is locked）只按异常类型名记 WARNING，不能带走「这天已发」的记录（否则启动补发会重发）。
- 标题 `SkinPulse 简报 MM-DD：前 N 共 X 张，Y 张有变动`。
- **纯文本正文**（给 ntfy，也存进 `brief_log.body`）：一行一张单，`第1名 | 名称 | 求购 48 | 榜首 我 | 在售 52 | 🆕新进`；收盘第 1 时榜首写「我」；没变动的行没有最后一段，几种变动用「，」连起来。按整行截到 UTF-8 **4000 字节**（ntfy 上限 4096），截掉的写「另有 K 张未列出，见看板 /brief」。`brief_log.body` 存不截断的全文。
- **HTML 正文**（给 PushPlus 的 `html` 模板，`NtfyMessage.html`；ntfy 不读它）：一张 `<table>`，列为「名次 | 饰品 | 求购 | 榜首 | 在售 | 变动」，有变动的行加粗；所有文本 `html.escape`。按整行截到 **16000 字节**（`BRIEF_HTML_BYTE_BUDGET`），截掉的张数写在表后。
- 两份正文的最后一行都是在售脚注（§7a.3；html 里是 `</table>` 之后、转义过的 `<p>`）。它和截断行一样在字节预算里先留好位置，截断时也不会被挤掉。
- 兜底消息：标题 `SkinPulse：近 7 天前 N 名单没有变动`，正文 `近 7 天前 N 名单没有变动（新进 / 名次 / 榜首价），监控在正常运行（统计到 MM-DD）。`，只有纯文本。
- 价格缺失写「—」，不填 0。

### 7a.6 代码落点

- `analysis/brief.py`（纯逻辑、可用 fixture 测）：输入快照与在售价，输出前 N 名表（`TopTable` / `TopRow`）与「发不发」判定。
- `core/messages.py`：简报文案（纯文本 + html 两份）与 `short_item_name`。
- `services/brief.py`：取数（排名快照、D 当天悠悠在售价）、调用上面两者、推送、写 `brief_log`；`services/price_snapshot.py`：每日价格快照。
- `scheduler.py`：两个 cron 任务 + 启动时的一次性补发任务（§7a.1）。
- `cli.py`：`brief --date YYYY-MM-DD [--send]`，默认只打印不发（预览 / 补发用）；`price-snapshot`（手动跑一次**今天**的快照；不带日期参数——`price/batch` 只给当前价，记到别的日期就是假历史）。
- `web/`：`/brief` 页与 `/api/brief`。

## 8. LLM 层〔未实现，M3b〕

- `adapters/llm`：`complete(messages, *, json_mode=False, provider=None)`；驱动 `openai_compat`（base_url + key + model）、`anthropic`、`gemini`；`[llm] providers = [...]`，`default`、`fallback_order`。
- 用途：看板问答（先本地算数，再让 LLM 组织语言）。每日简报（§7a）**不用 LLM**（用户 2026-09-23 选择）。
- 第一个驱动做 `openai_compat`（用户持有这类 key）。
- 约束：prompt 注入全部数字，要求不得自报数字；输出做字段校验；LLM 失败不影响其他功能。

## 9. Web 看板

看板是 Vue 3 单页应用，页面、设计系统、接口、安全都写在 [看板改版 spec](2026-09-27-dashboard-redesign.md)（[ADR-0012](../../decisions/0012-dashboard-vue-spa.md)、[ADR-0013](../../decisions/0013-echarts-replaces-server-svg.md)），2026-09-27 三期全部完成。

- 页面：总览 `/`、饰品详情 `/item/<templateId>`、分析 `/analysis`、大盘 `/market`、简报 `/brief`、系统 `/system`（含参数设置和网页换 token）。旧的 `/status`、`/settings` 跳到 `/system`。每页的数据都来自对应的 `/api/*`。
- 技术：构建产物提交在 `skinpulse/web/dist/`，由 FastAPI 在同一个端口提供；没有服务端模板。图表用前端 ECharts，涨红跌绿，K 线纵轴按实体定范围（`analysis/kline.py`），离谱的影线截断，并在图上注明。
- 默认监听 127.0.0.1:8765（这台机器配置成 8766）。系统页的参数设置写回 `settings.toml`，并且立即生效。
- `/brief`：发过的简报，以及每天「没发」的判断依据（读 `brief_log`）。
  - 没有记录的日子补一行「无记录 / {send_at} 服务没在运行，这天没有判定」。
  - 补到哪里：从最近一个已到点、应该有记录的日子往回补，一直补到列出的最旧一条。「最近一个已到点的日子」在 `send_at` 为 00:00 时就是本地昨天。最早一条之前不补，总行数不超过 30。
  - 简报关闭时不补。
  - 说明文字里的「前 N」按 `brief.top_rank` 写。
- 〔未实现〕问答页（M3b）和事件页。涨跌榜不做（ADR-0006）。

## 10. 配置与密钥

- `settings.toml`（提交的是 `settings.example.toml`）：polling、alerts、youpin、steamdt、ntfy（非密钥部分）、web、timezone。
- `[brief]`：`enabled`、`send_at`（"00:00"）、`snapshot_at`（"23:50"）、`top_rank`（2）、`quiet_days`（7）。
- 密钥走 `SecretStore`（系统钥匙串，失败退回 `data/.env`）：悠悠 token、PushPlus token、SteamDT key、ntfy topic。设置命令一律支持 `--from-file`。

## 11. 部署

- 入口 `python -m skinpulse run`；`uv` + `pyproject.toml` 锁定；Python ≥ 3.12。
- Windows：`scripts/register_windows_task.ps1` 注册计划任务（带失败重启）；家庭版非管理员会 Access denied，改用 `-UseStartupFolder`（启动目录快捷方式，**无失败重启**）。这套 Windows 实例用的是后者（[ADR-0005](../../decisions/0005-windows-autostart-startup-folder.md)），现已按 [ADR-0009](../../decisions/0009-run-service-on-macos.md) 退役，唯一实例迁到 macOS。2026-11-24 起 Windows 改用 `skinpulse service install`（启动目录 + Python 守护进程，[ADR-0021](../../decisions/0021-windows-autostart-supervisor.md)），旧脚本已删。
- macOS（当前生产环境）：用户级 LaunchAgent，见 §11.1（[ADR-0010](../../decisions/0010-macos-launchagent-caffeinate-on-ac.md)）。〔未实现〕NAS Docker。
- 日志：控制台 + 文件；不记录任何凭据，传输层异常只记类型名。
- **网络**：四个 HTTP 客户端 `trust_env=False`，不读环境里的代理变量，全部直连（[ADR-0011](../../decisions/0011-http-clients-ignore-env-proxy.md)）。这样从终端启动还是由 launchd 拉起，网络行为都一样。
- **单实例锁**：`run` 在做任何联网或建库之前，先对 `data/skinpulse.run.lock` 加一把非阻塞的独占锁（POSIX 用 `fcntl.flock`，Windows 用 `msvcrt.locking`），锁文件里写入 pid。加锁失败就打印「已有实例在跑（pid N）」，以退出码 1 退出。进程退出（包括崩溃、被 kill）时，锁由操作系统自动释放，不会留下需要手动清理的陈旧锁。只有 `run` 加这把锁：`round`、`sync`、`brief` 这些一次性命令照旧可以和服务并行跑。
- **`run --web-only`**：只起看板，不建调度器（不跑监控、不发简报，`recover_pending` 也不调），照样占单实例锁。用于在另一台机器上看看板；和 `--no-web` 互斥。

### 11.1 macOS：LaunchAgent

| 文件 | 作用 |
|---|---|
| `scripts/macos/skinpulse-launchd.sh` | launchd 实际执行的程序：`cd` 到仓库根目录 → 后台启动 `/usr/bin/caffeinate -s -w $$` → `exec .venv/bin/python -m skinpulse run --console-level WARNING` |
| `scripts/macos/launchagent.sh` | 管理命令：`install` / `uninstall` / `start` / `stop` / `restart` / `status` |
| `~/Library/LaunchAgents/local.skinpulse.plist` | 由 `install` 生成，不进仓库 |

- **plist**：`Label=local.skinpulse`；`ProgramArguments` = `["/bin/bash", 包装脚本的绝对路径]`（仓库路径里有空格，所以用数组，不用 shell 字符串；经 `/bin/bash` 启动，不依赖脚本的可执行位）；`WorkingDirectory` = 仓库根目录；`RunAtLoad=true`；`KeepAlive=true`；`ThrottleInterval=30`；`StandardOutPath` 和 `StandardErrorPath` 都指向 `data/logs/launchd.log`。plist 用 `plutil -create` / `plutil -insert` 逐项写入（路径里的空格和 `&` 不用手动转义），写完用 `plutil -lint` 检查一遍。脚本只用 bash 3.2 的语法（macOS 自带的 `/bin/bash`），变量一律写成 `${VAR}`：bash 3.2 在 UTF-8 环境下会把紧跟在 `$VAR` 后面的全角标点当成变量名的一部分（有测试守着）；`.gitattributes` 强制 `*.sh` 用 LF。
- **为什么 `exec` 虚拟环境里的 python，而不用 `uv run`**：`exec` 之后 launchd 盯的 pid 就是 python 本身，停止时的 SIGTERM 直接送到服务进程。`caffeinate -w $$` 盯的也正好是这个 pid（2026-09-24 实测：`exec` 前后 pid 不变，进程退出后 caffeinate 跟着退出）。另外 launchd 的 PATH 里不需要有 uv。代价：拉新代码后要照常 `uv sync`，这本来就是要做的。
- **SIGTERM**：`run` 在启动调度器前装一个 SIGTERM handler，里面抛 `SystemExit(143)`，退出时再恢复原来的 handler。原因：默认处理会直接杀掉进程，`finally: sched.shutdown` 跑不到；uvicorn 0.52 优雅退出后，也会把「原来的」handler 装回去，再把信号重发一次。装了这个 handler 后，用 uvicorn 和 `--no-web` 两种方式跑，收到 SIGTERM 都会走到 `finally`，退出码 143（2026-09-24 用真 uvicorn 在子进程里测过：不装的话进程直接 -15 死掉）。`finally` 里 `sched.shutdown(wait=False)` 之后，如果是 SIGTERM，就 flush 日志后 `os._exit(143)`：在跑的那一轮监控在线程池的非守护线程里，解释器正常退出会等它跑完（一轮要几分钟，实测会一直等），而 launchd 默认 20 秒后就 SIGKILL。在途的 pending 通知由下次启动时的 `recover_pending` 收尾。
- **`caffeinate -s`**：只在接着电源时阻止系统睡眠，屏幕照常熄灭；用电池或合盖时不起作用。
- **`KeepAlive=true`**：不管是崩溃还是正常退出，launchd 都会在 `ThrottleInterval` 秒后重新拉起。`launchagent.sh stop`（`launchctl bootout`）才是真正的停止，下次登录时会自动恢复。如果用户在终端里手动跑了一个 `run`，launchd 拉起的那个会因为抢不到单实例锁而退出（退出码 1），之后每 30 秒重试一次。手动的那个一退出，launchd 的就接上了。重试发生在联网之前，不会碰悠悠。
- **`run --console-level`**：控制台 handler 的级别，默认 `INFO`。launchd 下传 `WARNING`，所以 `launchd.log` 里只有警告、错误和未捕获的 traceback。例外：有手动实例占着锁时，launchd 那边每 30 秒被拒绝一次，每次记一行（一天约 2900 行）；手动实例一退出就停。完整日志照旧写在 `data/logs/skinpulse.jsonl`（按大小轮转）。
- **钥匙串**：LaunchAgent 跑在用户的登录会话里，登录钥匙串已经解锁。凭据是同一个 python 程序写进去的，launchd 拉起的进程读它，预期不会弹框。验收时实测：`/status` 里各通道都已配置，日志里没有「not configured」。如果 Homebrew 升级了 python，程序路径会变，第一次读可能会弹「允许」框。
- **`install`** 前先检查：`.venv/bin/python` 和 `settings.toml` 都存在；plist 已经加载过的话，先 `bootout` 再 `bootstrap`（可以重复执行）。`status` 显示 launchd 的 state、pid、上次退出码，以及 `launchd.log` 的最后几行。

## 12. 错误处理原则

- 适配器相互隔离；匿名通道与账号通道互不阻塞；行情缺失不影响告警。
- 退避期间看板显示"暂停至 HH:MM"。
- 排名 unknown 不触发告警、不清除状态。
- 写入幂等；通知先落库后发送；推送失败计入 `notify_failed` 并打 WARNING，不静默。
- 价格缺失就是缺失，绝不填 0。

## 13. 测试

- 单元：`core.rank`、`core.alerts`、`core.messages`、`analysis`；fixture 为真实响应（脱敏）。
- 适配器：`respx` 回放测试解析与错误码分支；`@pytest.mark.live` 默认跳过。
- 集成：`tests/test_integration.py`，清单 + 录制列表 → 状态机 → 推送 mock。
- 标准：`uv run pytest -q` 全绿且无 warning。
- 人工：推送是否送达只能由用户在手机上确认。

## 14. 分期

| 期 | 内容 | 完成标准 | 状态 |
|---|---|---|---|
| M1 核心 | 悠悠双通道、求购同步、排名、状态机、推送、SQLite、看板总览/设置/状态、Windows 自启 | 真实账号稳定运行 | ✅ 真实账号在跑（四百多张单） |
| M2 行情 | SteamDT 适配、饰品目录、详情页、大盘页 | 看板具备比价、走势、指数 | ✅ 达成（只用 SteamDT；CSQAQ 不接，历史入库移到 M3） |
| M3a 简报 | 每日价格快照、每日简报（前 2 名表，有变动才发，7 天静默兜底）、`/brief` | 连续 7 天：每天 00:00 都有一条 `brief_log`，该发的都发到微信并经用户确认 | 代码已完成，验收中 |
| M3b 问答 | LLM 层（先 OpenAI 兼容）、看板问答 | 待 intent | 未开始 |
| M3c 指数 | 自建指数、Parquet 导出 | 待 intent | 未开始 |
| M4 可选 | Mac/NAS 部署、Vue 前端、回测 | 按需 | Mac：2026-09-24 迁完，LaunchAgent 自启已做（§11.1）；Vue 前端：看板改版三期都已完成（2026-09-27，[spec](2026-09-27-dashboard-redesign.md)）；NAS、回测未开始 |

## 15. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 悠悠条款/封号 | 只读、低频、家庭 IP、小号先行、版本号配置化、异常码监控 |
| `searchPurchaseOrderList` 被移到 `sk` 加密通道后 | 整个项目失去主数据源；退路是匿名队列自算排名（慢）或复刻加密 |
| 上报 rank 语义变化 | 每轮抽样核对（pass B）当哨兵 |
| 接口变更 | 适配器独立、fixture 回归、解析守卫（字段缺失直接报错而不是静默变 0） |
| SteamDT 变更/收费 | 本地缓存；行情缺失不影响告警；CSQAQ 可作备选（目前不接） |
| 推送"成功"但没送到 | 以用户确认为准；PushPlus 为主（微信通道） |

## 16. 未决事项

已查实：`searchPurchaseOrderList` 字段、`rank` 语义（同条件子队列）、SteamDT 限额。

仍开放：

- token 真实失效时间 → 等当前 token 的 JWT `exp` 到期时观察。
- 同价单是否时间优先 → 未专门验证。
- 款式/异色/纹理维度的子队列语义 → 账号里没有对应的单，未验证；抽样核对保留为哨兵。
- PushPlus 正文长度上限未实测：简报走 `html` 模板、按 16000 字节截断（目前一张表 5–9 行，约 2 KB）；告警走 `txt`、按 4000 字节截断。
- M3b（LLM 问答）、M3c（自建指数 / 导出）的范围 → 开工前各写一份 intent（未公开）。
