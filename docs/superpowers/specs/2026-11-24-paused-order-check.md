# 充值前检查暂停单（Paused Order Check Spec）

- 日期：2026-11-24
- 来源：intent（未公开）（接受：做法 A + B，卡片放在总览）
- 状态：定稿（用户 2026-11-24 看过视觉稿：「可以，继续做」）。plan（未公开）
- 视觉基准：claude.ai 画布「SkinPulse 暂停单检查视觉稿」（2026-11-24）：展开倒挂（深色）、收起 + 结果过期（浅色）、另外三种状态（浅色）。实现和视觉稿有出入时以本 spec 为准，本 spec 没写到的以视觉稿为准。
- 相关：[多账号 spec](2026-11-24-multi-account.md) §12（暂停中按号查）、[市价提醒 spec](2026-10-15-market-alerts.md)（在售价核对的做法）、live-api-findings §6（公开队列会被 403）、§7（status 30）

## 0. 一句话

充值前点一下，看每张「暂停中」的单恢复后会不会倒挂（≥ 悠悠在售最低）、会不会直接第 1、离第 1 有多近；按危险程度排好，用户自己去悠悠里改价或撤单。**只读、不推送。**

## 1. 范围

**做**：
- 服务函数 `services/paused.py::check_paused`：拉暂停单 → 算档位 → 落库。
- 命令 `skinpulse check-paused`（做法 A）。
- 总览卡片 +「检查一次」按钮（做法 B），接口 `GET /api/paused`、`POST /api/paused/check`。
- 演示数据带一次检查结果。

**不做**：
- 推送、定时自动检查（intent 做法 C，不做）。
- 逐模板查公开求购队列（2026-11-24 查 77 次就被 403 了，见 §3.2），所以**名次只分「第 1 / 并列第 1 / 不是第 1」**，不是第 1 的给差距，不给具体第几名。
- 悠悠的任何写操作。

## 2. 档位

每张暂停单算出下面这些量（金额都用 Decimal）：

| 量 | 来源 |
|---|---|
| `price`、`remaining_qty` | 暂停单列表的 `unitPrice`、`quantity − buyQuantity` |
| `paused_at` | 列表的 `createTime`：**暂停单的这个字段是被暂停的时刻**（2026-11-24 实测，和库里 `outcome = paused` 的日期一一对上） |
| `top_bid` | 列表的 `maxPurchasePrice`（悠悠给的同条件子队列榜首，不含自己这张）；`暂无` → `None`（子队列没人） |
| `sell_min`、`sell_depth`、`sell_src`、`sell_at` | §3.3 |

档位（取第一个成立的）：

| 档 `tier` | 条件 | 卡片上叫 |
|---|---|---|
| `inverted` | `sell_min` 有值且 `price ≥ sell_min` | 倒挂 |
| `top1` | `top_bid is None`，或 `price ≥ top_bid` | 直接第 1 |
| `near` | `(top_bid − price) / top_bid ≤ near_gap`（默认 5%） | 差 5% 内 |
| `near_sell` | `price ≥ sell_min × sell_ratio`（默认 0.9） | 贴近在售 |
| `other` | 其余 | 不列出，只给张数和占用金额 |

- `top1` 里再分 `alone`（子队列没人）、`ahead`（高于榜首）、`tie`（等于榜首；同价恢复后排前排后没验证过，显示「并列第 1」）。
- **暂停天数**：每行都写「暂停 N 天」；超过 `old_days`（默认 60）天的用 amber 标签突出。2026-11-24 的数据：07-26 暂停的 102 张是 121 天（倒挂最离谱的都在这批），10 月初暂停的约 47 天，所以门槛定 60，只圈出服务上线前就暂停了的那批。
- 同档内排序：`inverted` 按 `price / sell_min` 降序；`top1` 按 `price / sell_min` 降序（没有在售价的排后面）；`near` 按差距升序；`near_sell` 按 `price / sell_min` 降序。
- 多账号：每张单带 `account_id`，档位和排序不分号。

## 3. 一次检查怎么跑

### 3.1 步骤

1. **翻暂停单**：对每个启用、已登录、账号通道没暂停的号，用它自己的 client 翻 `search_my_purchase_orders_page(30, page, 40)`，页间 `youpin.page_sleep_s`，号间 `polling.account_gap_s`，每个号最多 `max_pages` 页（默认 30）。撞上 `max_pages` 时结果标「不完整」。
2. **在售价（SteamDT）**：所有暂停单的模板去重，经 `item` 表映射成 `market_hash_name`，用 `/price/batch` 取 `YOUPIN` 的 `sellPrice` / `sellCount`（每批 100，批间 65 秒，同价格快照）。没配 SteamDT key 时跳过这一步。
3. **实时核对（悠悠在售接口）**：挑出「SteamDT 在售价说是 `inverted` / `near_sell`」以及「`top1` 但 SteamDT 没有在售价」的单，按危险程度排，前 `verify_max`（默认 30）张用 `get_on_sale` 查实时在售最低，写回 `sell_min / sell_depth`、`sell_src = youpin`，再重判档位。请求间隔 `polling.request_interval_s`（带 jitter），计入服务的每小时请求上限，匿名通道暂停中就跳过这一步。
4. **落库**（§4），旧的检查只留最近 5 次。

### 3.2 为什么不查公开求购队列

2026-11-24 一次性检查时，匿名公开求购队列连查 77 次（约 5 分钟）后一律 HTTP 403，正式服务每轮的抽样也跟着失败，至少持续约 50 分钟。列表里白送的 `maxPurchasePrice` 和实时榜首 77 张里 65 张一致，其余多数差 1 元，判「会不会第 1」够用。代价是拿不到具体名次（§1）。

### 3.3 在售价的口径

- `sell_src = youpin`：悠悠在售接口实时查的，`sell_at` 是查询时刻。
- `sell_src = steamdt`：SteamDT 的悠悠在售最低，`sell_at` 是它的 `updateTime`（几分钟到几小时前）。
- 没有：映射不到、SteamDT 没数据又没轮到核对。`sell_min = None`，**不填 0**；卡片上写「—」。
- `sell_depth = 0`（实时查到没人在卖）和 `None`（没查）分开，渲染走 `sellLabel` / `sell_label`。

### 3.4 由谁执行、和每轮监控怎么错开

- **看板触发**：`POST /api/paused/check` → 建一行 `status = running` 的检查记录 → 在调度器里加一个立即执行的一次性 job `paused_check`（`max_instances=1`）→ 返回 `{run_id}`。
- **新增 `rt.youpin_lock`**（`threading.Lock`）：`job_monitor_round` 整轮持有；`paused_check` 只在第 1 步、第 3 步持有，第 2 步（SteamDT，约 6 分钟）放开，期间监控照常跑。两边都用同一个 client，所以不能同时发请求。
- **CLI** `check-paused` 在自己的进程里跑同一个函数（和 `sync` 一样是一次性命令），结果同样落库，看板能看到。它和服务的请求不互斥，所以使用手册里写「服务在跑时优先用看板的按钮」。
- 拒绝（都返回中文 `detail`）：已有一次在跑 409；选品在跑 409（两边抢 SteamDT 的批量配额）；价格快照、估值前后的夜间窗口 423（同选品，`in_night_window`）；一个已登录的号都没有 409；demo 403（同其他写接口）。限流同「立即同步」。
- 进度：检查记录里写 `phase`（`pages` / `prices` / `verify`）和 `done / total`，前端检查中每 3 秒拉一次 `GET /api/paused`。
- 服务重启时，`running` 的记录标成 `interrupted`（同选品）；命令行里按 Ctrl-C 也当场标成 `interrupted`，不然看板会一直「正在检查…」、再也开不了新的检查。
- 反过来，选品在暂停单检查跑着时也拒绝（409），两边都用 SteamDT 的批量价。
- 心跳和看板「立即同步」也持 `rt.youpin_lock`（评审 2026-11-24：它们和检查的翻页用同一个号）。
- **`/price/batch` 的共用时间槽**（2026-11-24，命令行的 `pnl import` 和检查同时跑撞了 3 次 4005）：每批之前调 `services.market.wait_batch_slot`，看 `system_status` 里 `steamdt_batch` 那一行记的上一批时间，不够 65 秒就等够、再记下这一批。价格快照、选品、盈亏导入、对账 CLI 都走它，跨进程也有效。盈亏导入看到检查（或选品）在跑，就干脆不补价。
- 暂停单列表里价格或单号读不出来的行：只缺几张就标「不完整」，整页都缺就整次失败——多半是悠悠改了字段名，不能出一份「没有暂停单」的假结果。

### 3.5 请求量（以 2026-11-24 一个号、518 张为例）

| 步骤 | 次数 | 通道 |
|---|---|---|
| 翻暂停单 | 13 页 | 悠悠账号（和一轮同步的量相当） |
| SteamDT | 6 批，约 6 分钟 | SteamDT `/price/batch` |
| 实时核对 | ≤ 30 次，约 2 分钟 | 悠悠匿名在售接口（11-24 连查 126 次都正常） |

一次检查约 8～9 分钟。

## 4. 数据（迁移：多账号占了 0014，从 0015 起；和盈亏统计谁后合并谁改编号）

**`paused_check_run`**：`id`、`started_at`、`finished_at`、`status`（running / done / failed / interrupted）、`source`（dashboard / cli）、`phase`、`done`、`total`、`incomplete`（Boolean：某个号翻到了页数上限）、`skipped`（没查成的号，逗号分隔的 id）、`error`（中文一句，不含任何凭据）、`steamdt`（Boolean：这次有没有 SteamDT 价）。

**`paused_check_item`**：`run_id`、`account_id`、`purchase_no`、`template_id`、`commodity_name`、`price`、`remaining_qty`、`paused_at`、`top_bid`（可空）、`sell_min`（可空）、`sell_depth`（可空）、`sell_src`（可空）、`sell_at`（可空）、`tier`、`top1_kind`（alone / ahead / tie / 空）。`(run_id, purchase_no)` 唯一。

不动 `my_buy_order`：暂停单不是 active 的单，混进去会影响总览、简报、成交判断。

## 5. 看板：总览卡片

### 5.1 位置和什么时候出现

- 在「开始使用」卡片之后、标题之前（两个视图都显示）：它回答的是「现在能不能充值」，比排名更急。
  - **2026-11-25 改（用户：不常用，不该占总览最上面）**：卡片挪到「分析」页最后。只有单子全被暂停（求购中 0 张、「开始使用」写着暂无求购单）时，总览上仍显示它。
- 「开始使用」卡片在时不显示，但「还没有求购单」那种例外：余额花光、单子全被暂停时求购中是 0 张，正是最该显示它的时候。
- 出现条件（任一）：
  - 最近一次检查有暂停单；
  - 最近一次检查之后，又有单被暂停（`order_event.outcome = paused`、事件时间晚于检查开始、而且不在这次检查的结果里——翻页那半分钟里被暂停的可能已经翻到了）：卡片写「检查之后又有 N 张被暂停」；
  - 从没检查过、而库里知道有暂停单（有 `outcome = paused` 的事件）。
- 最近一次检查暂停单为 0、之后也没有新的暂停：不显示。

### 5.2 内容（一个焦点）

- **标题**：最严重的那一档，例如「36 张恢复后会倒挂」；没有倒挂时「52 张恢复后直接第 1」；都没有时「暂停中的 518 张恢复后都不靠前」。从没检查过时「有求购单被暂停了，充值前查一下」。
- **副标题**：`检查于 14:07 · 暂停中 518 张 · 102 张暂停超过 60 天`。结果超过 `stale_h` 小时或检查之后又有单被暂停：前面加一句 warn 色的「结果是 5 小时前的，检查之后又有 3 张被暂停」（两件事有哪件写哪件），这时按钮变成主色。不完整时加「只翻到前 30 页」。
- **右边按钮**：「检查一次」/「重新检查」。
- **检查中**：卡片换成进度（视觉稿状态 2）：标题「正在检查…」、当前步骤和进度条（「翻暂停单 第 5 页」「查在售价 2/6 批」「核对在售 12/30」）、三步的完成情况、「还要约 N 分钟。可以离开这个页面，查完结果会留着」。上一次的结果在查完前不显示。
- **都安全**（没有前四档的单）：卡片缩成一行（视觉稿状态 3）：对勾、「暂停中的 41 张恢复后都不靠前」「检查于 21:30 · 可以放心充值」、次要按钮「重新检查」。
- **从没检查过**：「有求购单被暂停了，充值前查一下」、一句说明、主按钮「检查一次」。出现条件用库里 `outcome = paused` 且之后没回到求购中的单（`known_paused`），但**标题不写这个数**：暂停期间被撤掉的单也算在里面，会偏大（11-24 库副本：498 对实际 416，见 plan 偏离 1）。
- **档位条**：倒挂 / 直接第 1 / 差 5% 内 / 贴近在售四个计数块（各带占用金额），点一个展开下面的明细并只看这一档，再点收起；默认收起。数为 0 的块变灰、不可点。块下面一行「点一档看明细 · 其余 395 张恢复后不靠前」。
- **明细表**（展开后）：饰品（★、简称；第二行 StatTrak™ · 磨损 ·「暂停 N 天」；多号时带账号简称，规则同总览表格）、我的价（剩余 > 1 时写「× N」）、恢复后（「第 1 · 没人求购」「第 1 · 高出 ¥460」「并列第 1」「差 3.2%」）、第 1 名、在售最低（下面一行「实时 · 10 件」或「SteamDT · 3 小时前」，没人在卖照 `sellLabel`）、我的价 / 在售（百分比 + 一条 0～200% 的小横条，100% 处一条竖线；≥ 100% 用 warn 色）。行尾一个跳到悠悠商品页的图标按钮（复用 `youpinUrl`，带 `aria-label`）。先显示 8 行，「显示全部」。表下一行说明「竖线 = 在售最低；『实时』是悠悠在售接口查的，『SteamDT』带它的更新时间」。
- 最近一次检查没完成（失败、中途停了）而卡片显示的是更早的结果，副标题最前面写「上次检查没完成：原因」，按钮变主色；从没查成过时写在「查一下」的说明上面。
- 卡片和表格的文案不写推送。

### 5.3 接口

`GET /api/paused`：

```json
{
  "run": {"id": 3, "status": "done", "source": "dashboard", "started_at": "…Z", "finished_at": "…Z",
          "phase": null, "done": 0, "total": 0, "incomplete": false, "steamdt": true, "error": null},
  "summary": {"total": 518, "amount": 77615.9, "old": 102, "old_days": 60,
              "tiers": {"inverted": {"count": 36, "amount": 11798.0}, "top1": {…}, "near": {…},
                        "near_sell": {…}, "other": {…}}},
  "paused_since": 0,
  "known_paused": 124,
  "items": [{"purchase_no": "…", "account_id": 1, "template_id": 295, "title": "…", "star": true,
             "stattrak": false, "wear": "久经沙场", "price": 650.0, "remaining_qty": 1,
             "paused_at": "…Z", "paused_days": 121, "old": true, "top_bid": 190.0, "top1_kind": "ahead",
             "gap_ratio": null, "sell_min": 205.0, "sell_depth": 1, "sell_src": "youpin", "sell_at": "…Z",
             "sell_ratio": 3.17, "tier": "inverted"}],
  "accounts": {"1": "主号"}
}
```

- `run` 是最近一次检查（可能正在跑），`result` 是最近一次查完的（卡片显示它，形状同 `run`）；都为 null 表示从没检查过。
- `items` 只含 `other` 以外的档，按 §2 排好。
- `POST /api/paused/check`：body `{}`（同源 JSON），返回 `{run_id}`；拒绝见 §3.4。

## 6. 命令 `skinpulse check-paused`

```
uv run python -m skinpulse check-paused [--account N] [--json] [--no-steamdt]
```

- 跑 §3.1 全部步骤（`--no-steamdt` 跳过第 2 步，只做实时核对），进度打在终端，结束打印和卡片一样的分档表（`other` 只打计数）；`--json` 输出 `GET /api/paused` 同样的结构。
- 不推送、不写 `my_buy_order`。

## 7. 设置 `[paused_check]`

```toml
[paused_check]
near_gap = 0.05     # 离第 1 差多少以内算「差 5% 内」
sell_ratio = 0.9    # 我的价 ≥ 在售最低 × 这个值算「贴近在售」（同市价提醒的 near_ratio）
verify_max = 30     # 一次检查最多用悠悠在售接口实时核对几张
old_days = 60       # 暂停超过几天用 amber 标签突出
max_pages = 30      # 每个号最多翻几页暂停单（每页 40 张）
stale_h = 2         # 结果超过几小时提示「再查一次」
```

看板参数页不放（不常改）。

## 8. 测试

- 纯逻辑（`core/paused.py`）：档位边界（正好等于在售、正好等于榜首、`暂无`、差正好 5%、没有在售价、`sell_depth = 0`）、`top1_kind`、排序、暂停天数、`maxPurchasePrice` 解析（`¥49`、`¥1,234.5`、`暂无`、空）。
- 服务：假 client 两个号翻页（含页数上限）、SteamDT 映射不到 / 失败 / 撞配额照样出结果、实时核对上限和改档、匿名通道暂停时跳过核对、风控时整次 `failed` 但已翻到的不写一半、旧记录只留 5 次、`interrupted`。
- 锁：检查持锁时监控轮等待；SteamDT 阶段不持锁。
- 接口：`GET` 的形状、从没检查过、`paused_since`、拒绝的几种状态码、demo 拒绝。
- 前端（`overview/pausedLogic.ts`）：标题选档、副标题（过期、检查后新暂停、不完整、老单）、出现条件、五种状态（从没检查、检查中、有危险、都安全、不显示）、档位条筛选、恢复后文案、比值横条宽度。
- 命令：`--json` 和接口一致。

## 9. 验收

1. 测试实例（skill verify-dashboard）：演示数据和库副本各看一遍，浅色、深色；对照视觉稿。
2. 正式环境用户点一次「检查一次」：结果和 11-24 的一次性检查大体一致（倒挂、直接第 1 的单对得上），服务日志在检查期间没有 403、每轮不报错。

## 10. 未决

- 同价恢复后排前还是排后（显示成「并列第 1」先不分）。
- 余额只够恢复一部分时悠悠按什么顺序恢复（不影响本功能，影响「先充多少」）。
