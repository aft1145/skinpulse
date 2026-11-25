# 难以成交：离第 1 或离市价太远的单

- 日期：2026-11-25 · 状态：定稿
- intent：2026-11-25-hard-to-fill-orders（未公开）（默认值、不推送都由用户定了）
- 视觉稿（私有画布，不进公开仓库）：「难以成交」一行的深色未勾选、浅色勾选加推送规则设置两块，2026-11-25 用户认可

## 1. 规则（一处定义，四处共用）

新纯模块 `skinpulse/analysis/reach.py`，取代 `analysis/brief.is_muted`：

```python
Reason = Literal["top", "market"]

def market_ref(sell_min, sell_depth, ref_bid) -> tuple[Decimal | None, Literal["sell", "bid"] | None]
def unreachable(my, top1, sell_min, sell_depth, ref_bid, *, max_gap_ratio, max_market_ratio, min_market_gap) -> Reason | None
```

- **市价** `market_ref`：
  - `sell_min > 0` → 在售最低，来源 `sell`；
  - `sell_depth == 0`（查过、没人在卖）且 `ref_bid > 0` → 别处买盘，来源 `bid`；
  - 其余（`sell_depth` 为 None 没查过、BUFF 单、缺价）→ `(None, None)`。价格 0 = 没有数据（坑 3）。
- **离第 1**（`top`）：`max_gap_ratio > 0` 且 `top1 / my ≥ max_gap_ratio`。和现在的 `is_muted` 完全一样。
- **离市价**（`market`）：`max_market_ratio > 0` 且市价已知且 `market / my ≥ max_market_ratio` **且** `market − my ≥ min_market_gap`。
- 两条都成立时报 `top`（先判）。`my` 为 None 或 ≤ 0 时一律 None（判断不了，不藏）。
- 比较一律 `Decimal`，设置里的 float 走 `Decimal(str(x))`（同现在的写法）。

## 2. 设置

`AlertSettings`（`[alerts]`，系统页「参数设置 › 推送规则」，热生效）：

| 键 | 默认 | 范围 | 标签 / 说明 |
|---|---|---|---|
| `max_gap_ratio`（现有） | 3.0 | ≥ 0 | 离第 1 倍数：榜首价达到我的价的这个倍数。0 = 不按这条判断 |
| `max_market_ratio`（新） | 2.0 | ≥ 0 | 离市价倍数：市价达到我的价的这个倍数，且差价不低于下一项。0 = 不按这条判断 |
| `min_market_gap`（新） | 20 | ≥ 0 | 离市价差价（元）：差价太小的不算 |

- `settings.example.toml` 补两行和注释。`settings_api` 不用改（`alerts` 已可编辑）。
- 前端 `system/fields.ts`：现有 `max_gap_ratio` 改标签「离第 1 倍数」、说明照上表；新加两行。「推送规则」组的 hint 补一句「难以成交的单不推『升到第 1 名』、不进简报」。

## 3. 推送和简报

- `Monitor._is_eligible_for_alert(o, top)` 改为 `unreachable(o.price, top, o.sell_min, o.sell_depth, o.ref_bid, …) is None`。BUFF 轮次走同一个 `_step_orders`，BUFF 单没有在售价，只有「离第 1」那条生效。
- `RoundReport`：`gap_gated` 仍只数 `top`，新增 `market_gated` 数 `market`。`scheduler` 每轮日志行加 `market_gated=%d`，`monitor_round` 状态里也加；看板 `types.ts` 的 `last_round` 补字段。每轮的汇总日志 `gap filter: …` 改成两个数一起报。
- 已在 ALERTING 的单变成难以成交：照现有 `eligible=False` 机制静默退出（`core/alerts.py`），不补发。
- 交易中、成交、市价提醒不看这条规则。
- 实时核对、别处买盘的候选名单（`_alert_band_candidates`）排除离市价太远的单：它们到不了 ALERTING，不排除的话每轮都会被白查。离第 1 那条仍不预先排除（它吃滞后的 top_bid）。
- 简报 `analysis/brief.top_table`：`OrderInfo` 加 `sell_min / sell_depth / ref_bid`（取订单行当前值，和 Monitor、看板同一份），`max_gap_ratio` 参数换成三个阈值，过滤用 `unreachable(… close.top1_price …)`。`services/brief.load_top_table` 相应传参。

## 4. 看板接口

`web/overview._order_row` 每行新增：

- `market`（市价，数）、`market_src`（`"sell"` / `"bid"` / null）；
- `market_gap` = market − my（未知为 null）、`market_ratio` = market / my（4 位小数）；
- `unreachable`：`"top"` / `"market"` / null；
- `gated` 保留，等于 `unreachable == "top"`（详情页 `MyOrderCard` 还在用）。

`overview_payload`：

- 焦点卡片：第 1 名和第 2 名都排除 `unreachable` 非空的（现在只排除 `gated`）。
- 标题「N 张求购排名第 1、M 张求购排名第 2」也只数可成交的；被排除的张数不在标题里提（看板偏好：标题短）。
- `insights.gated_count` 换成 `unreachable_count`，另给 `unreachable_top`、`unreachable_market` 两个分项。
- `meta.thresholds` 加 `max_market_ratio`、`min_market_gap`。

## 5. 看板前端（照视觉稿）

- **筛选**：`Filters.hideGated` 改名 `hideUnreachable`，网址参数仍是 `hide=1`（旧书签照样有效）；复选框文字「隐藏难以成交的」，后面跟张数（小号等宽字）。勾选后列表标题旁写「已隐藏 N 张难以成交的单」。
- **离第 1 列**：`gapTier` 的 `gated` 档改标签「难成交」（只在 `unreachable == "top"` 时出现）；工具栏「离第 1」筛选里的这一档同步改名。
- **新列「离市价」**（在「离第 1」右边，可排序）：
  - 主数字「差 x」（`money` 格式，和「离第 1」列一致），下一行小字「N 倍」（≥ 10 倍保留 1 位小数）；来源是别处买盘时小字写「别处买盘 · N 倍」；
  - `unreachable == "market"` 时小字后面加「难成交」胶囊（和离第 1 列同一个样式）；
  - 市价未知写「—」，小字「无在售、无别处买盘」（`sell_depth == 0` 时）或不写（没查过）；
  - 排序键：`market_ratio`，未知排最后（`nullsLast`）。
- **未勾选时**，难以成交的行整行变暗（名字、价格、数字降到次要色，名次徽章半透明），原因写在触发它的那一列。
- **洞察卡**：标题「N 张难以成交」，正文「离第 1 太远 a 张、离市价太远 b 张」。
- **离第 1 筛选**：「难成交」那一档只含离第 1 太远的单，筛选菜单里写「离第 1 太远」。
- **展开行**：离市价太远时说明「市价是我的 N 倍、差 x，离市价太远：升到第 1 名也不会推送」。
- 「难成交」胶囊、价格梯斜纹、筛选圆点统一用次要灰（原来是琥珀色）。
- **详情页** `MyOrderCard`：`gapText` 的「不告警」改「难成交」；离市价太远时同样标「难成交 · 离市价 N 倍」。
- 手机宽度：表格本来就在横向滚动容器里，新列不改变页面宽度（390 宽不横向滚动的验收照旧）。

## 6. 测试

- `tests/test_reach.py`（新）：
  - 市价来源三种（在售、没人在卖用别处买盘、没查过）；
  - 阈值边界（正好 2 倍、正好 ¥20）、两个条件缺一不藏；
  - `top` 优先；任一倍数为 0 时关掉对应规则；价格 0 / None 不藏；
  - 用 intent 里的真实样本：纪念包 10 对 4871 → market；CZ75 猛虎 25 对 49 → None；音乐盒 7 对 9.46 → None；R8 17 对 67.35 → market；EMS One 10、没人在卖、别处买盘 1500 → market。
- `test_monitor`：第 1 名但离市价太远 → 不推「升到第 1 名」、`market_gated` 计数；交易中、成交照推；改设置后下一轮生效。
- `test_brief`：离市价太远的单不进前 N 名表。
- `test_web_overview`：新字段、焦点卡片和标题排除、`unreachable_count` 分项、`gated` 兼容。
- `test_config` / 设置接口：两个新键能读写、负数被拒。
- vitest：`filterOrders` 的隐藏、旧 `hide=1` 参数、离市价排序键、单元格文案（倍数、别处买盘、未知）、洞察文案。

## 7. 验收

skill verify-dashboard（8799，正式库副本）：

- 默认设置下 713 张里隐藏 215 张左右（离第 1 约 160、离市价新增约 55，库在变，数字会有出入）；
- 截图里那几张第 1 名（纪念包、怒氓、狮鹫）被藏，CZ75 猛虎、MAG-7 炽热留着；
- 系统页把离市价倍数改成 0 → 刷新后离市价规则不再藏（测试实例上改，正式看板不点写操作）；
- 浅色、深色各看一遍；390 宽不横向滚动；控制台无错误。
