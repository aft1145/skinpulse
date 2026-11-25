# BUFF 只读接口调研（2026-11-24）

> 给 多账号与 BUFF intent（未公开） 用。§1～§7 的来源只有公开资料（开源项目源码、README、issue）和 **4 次不带 cookie 的匿名 GET**（每次隔 3 秒，没被限流）。**§8 是同一天用户自己登录后的 8 次只读 GET**（在用户登录的浏览器页面里发，cookie 没出过浏览器）。**全程没有调用任何写接口。**
>
> 标注：**实测** = 2026-11-24 匿名请求亲眼看到的；**来源** = 开源项目代码里写的；**推断** = 两者都没直接证明。登录后要实测的点集中列在 §7。

## 结论

可以做，但和悠悠是两种做法：

- 「我的求购单」和「某饰品的求购队列」各有一个 GET 接口，只要浏览器里的 `session` cookie，**没有签名参数**。
- **BUFF 不给名次**（至少公开队列里没有，我的求购单接口还没见过返回）。公开队列每单带订单号、`user_id`、价格、条件和创建时间，名次要**自己数**——这正是悠悠 09 月放弃的「每张单查一次队列」的做法（[live-api-findings §5](2026-09-06-live-api-findings.md)），单多时请求量大，要先筛候选（见 intent）。
- 条件单和普通单**混在同一个队列里**，没有悠悠那种「同条件子队列」的现成口径。
- 社区普遍反映 BUFF 对脚本封号，轮询要比悠悠慢。

## 1. 接口（全部 GET，`https://buff.163.com`）

| 用途 | 路径 | 登录 | 依据 |
|---|---|---|---|
| 我的求购单 | `/api/market/buy_order/wait_supply?game=csgo&page_num=1&page_size=60` | 要 | 来源：网页「我的求购」入口 `/market/buy_order/wait_supply`；[buff-sell](https://github.com/farmer-person/buff-sell)、[Netease-Buff-Trade-Bot](https://github.com/SolitudeRA/Netease-Buff-Trade-Bot)。实测匿名返回 `Login Required`。**没有开源项目解析过它的返回字段** |
| 某饰品的公开求购队列 | `/api/market/goods/buy_order?game=csgo&goods_id=<id>&page_num=1` | 第 1 页不要 | 实测：匿名第 1 页 `code:"OK"`、**每页 10 条**；`page_num=2` 返回 `Login Required`。可选参数 `min_paintwear` / `max_paintwear` / `tag_ids`（来源：[ruff](https://github.com/Lol3rrr/ruff)） |
| 购买记录（求购成交大概率在这里，推断） | `/api/market/buy_order/history?game=csgo&page_num=&page_size=&state=success` | 要 | 来源：[galanhao/steamauto BuffProfitReport](https://github.com/galanhao/steamauto)（page_size 300，翻页隔 15 秒）、[AetherSwap](https://github.com/VexedWilosn/AetherSwap) |
| 单笔订单 | `/api/market/bill_order/batch/info?bill_orders=<订单号>` | 要 | 来源：[buff-trade-helper BUFF_API.md](https://github.com/chushi2333/buff-trade-helper) |
| 状态枚举 | `/account/api/user/info/v2?meta_list=buy_order_state` | 要 | 来源：AetherSwap。登录后用它拿完整状态表 |
| 待处理报价（Steamauto 拿它校验登录） | `/api/market/steam_trade` | 要 | 来源：[Steamauto](https://github.com/Steamauto/Steamauto) |
| 在售列表 | `/api/market/goods/sell_order?game=csgo&goods_id=&page_num=&sort_by=default` | 默认排序第 1 页不要 | 实测：匿名第 1 页 OK、每页 10 条 |
| 饰品列表 | `/api/market/goods?game=csgo&page_num=1` | 不要 | 实测：每条有 `sell_min_price`、`buy_max_price`、`sell_num`、`buy_num` |

### 公开求购队列的字段（实测）

`data` 下有 `items`、`user_infos`、`filters`、`total_count`。每条：

- `id`：订单号，形如 `261124T1525442014`（字符串）
- `user_id`、`price`（元，字符串）、`num`、`real_num`、`frozen_num`、`frozen_amount`（= 价格 × 数量）
- `state:"progressing"`、`state_text:"正在求购"`
- `specific`：条件数组，见 §3
- `created_at`、`updated_at`：Unix 秒
- `pay_method`：93 或 94，网页上都显示「先求后付」；`expire_time: null`
- **没有名次字段**

和 [BetterBuff 的类型定义](https://github.com/GODrums/BetterBuff/blob/main/src/lib/@types/BuffTypes.d.ts)（`BuyOrder.Item`）一致。

### 两个对不上的数（实测，各只有一个样本）

- 饰品列表的 `buy_max_price` 是 204，同一时刻队列里最高的条件单是 233：**`buy_max_price` 不含条件单**——正好和 SteamDT 的「悠悠最高求购」相反（那个含条件单，CLAUDE.md 坑 14）。
- 饰品列表的 `sell_min_price` 是 220，在售列表默认排序第 1 页最低是 230，原因不明。

## 2. 登录与凭据

- 只读请求只要 cookie **`session`**；写操作还要 `csrf_token`（放 `X-CSRFToken` 头），我们用不到（来源：Steamauto、AetherSwap）。
- 匿名访问时服务端会种 `Device-Id`（有效期到 2119 年）、`client_id`、`csrf_token`（实测）。
- 拿 `session` 的三种方式（来源：[Steamauto buff_helper.py](https://github.com/Steamauto/Steamauto/blob/master/utils/buff_helper.py)、[buff2steam](https://github.com/hldh214/buff2steam)）：
  1. 浏览器登录后从开发者工具复制——**和悠悠贴 token 同一个路子，建议用这个**；
  2. Steam OpenID 登录（要 Steam 会话，可能返回 `Steam Logged In Needs Verification` 要短信，[Steamauto #259](https://github.com/Steamauto/Steamauto/issues/259)）；
  3. App 扫码登录（`qr_code_create` → `qr_code_poll` → `qr_code_login`）。
- **有效期没有可靠的公开说法**。Steamauto 每次启动先校验缓存的 session、失效就重登，说明会定期失效，天数不明。登录后实测。

## 3. 求购机制：和悠悠的差别

| | 悠悠 | BUFF |
|---|---|---|
| 条件单 | 有（磨损区间、款式、渐变、`typeId`） | 有。`specific` 数组，实测见到 `{"type":"paintwear","text":"磨损: 0.15-0.18","values":["0.15","0.18"]}`；图案、贴纸、多普勒相位等类型推断存在、没看到 |
| 队列 | 「不限」是独立条件值，**同条件子队列**各自排（[live-api-findings §5](2026-09-06-live-api-findings.md)） | **条件单和普通单混排**（实测：前 4 单是磨损条件单，后面才是普通单） |
| 排序 | 价格高者先；同价先挂先得 | 同（实测：两张 233 的单，先建的在前） |
| 平台给名次 | `searchPurchaseOrderList` 直接给 `rank`（同条件子队列，滞后几分钟） | **不给**，自己数 |
| 公开队列可见深度 | 匿名 20 条；登录后 `pageSize` 生效 | 匿名 10 条（只有第 1 页）；登录后能否翻页、`page_size` 上限待测 |
| 资金 | 求购余额，不够时自动暂停（status 30） | 「先求后付」：`expire_time` null、`pay_expire_timeout` -1，推断不冻结资金、长期有效；但 `frozen_amount` 仍等于总价，含义待测 |

**名次口径要我们自己定**。推断卖家供货时，系统把货给「价格最高、且条件能被这件货满足」的那一单。所以一张普通单前面真正挡路的，是比它价高的普通单，加上比它价高、而且条件能被大多数货满足的条件单。intent 里给了建议口径，要靠之后的真实成交来校。

## 4. 风控

- 限流：**HTTP 429 + nginx 的 HTML 页**，不是 JSON（BetterBuff 类型里有 `TooManyRequests`）。
- 验证码：`code:"Captcha Validate Required"`，`error:"访问异常, 请完成安全验证"`。
- 繁忙：返回文本含「系统繁忙」，Steamauto 等 2 秒重试。AetherSwap 把 403 / 412 也当风控，关键词「请求过于频繁」「人机验证」「页面已过期」。
- 没找到签名参数；有 `Device-Id` cookie。
- 社区间隔：

  | 项目 | 间隔 |
  |---|---|
  | buff2steam | 同一 URL ≥ 4 秒，出错指数退避 8–60 秒 |
  | Steamauto 发货轮询 | 默认 300 秒 |
  | BuffProfitReport | 每页 15–30 秒，注释「避免被封号」 |

- **buff2steam 的 README 明说有用户因它被封号**；AetherSwap 也警告封禁风险。这些项目多半会下单或高频扫价，和我们「只读、慢轮询」不完全可比，但风险是真的。

## 5. HTTP 和业务码

- 成功、未登录都是 **HTTP 200**，看 `code`：`"OK"` / `"Login Required"` / `"Captcha Validate Required"`……；出错时 `error` 是中文说明，`extra` 可能有附加信息（实测）。
- 例外：限流直接 HTTP 429 + HTML。Steamauto 还处理 401、5xx。
- 和 CLAUDE.md 坑 2 同一个原则：**成败只看业务码**，但 BUFF 要多认一个「429 + 非 JSON」。

## 6. 饰品对应：BUFF `goods_id` ↔ 悠悠 `templateId`

SteamDT `/base` 每件饰品带 `platformList`（各平台的 itemId），悠悠那一项就是 `templateId`（[steamdt 对接记录](2026-09-23-steamdt-pushplus-ntfy-findings.md)、`adapters/steamdt/models.py::BaseItem`）。**推断** BUFF 那一项就是 BUFF 的 `goods_id`，这样不用按名字匹配；第二期开工时拿几件饰品核对一下。

## 7. 要登录后实测的点（答案见 §8）

1. `wait_supply` 的真实字段：有没有 `specific`、有没有名次或榜首价、有哪些状态、怎么分页。
2. 求购成交在 `buy_order/history` 里怎么和普通购买区分；完整状态枚举（`user/info/v2?meta_list=buy_order_state`）。
3. `session` 有效期；会不会被别处登录挤掉。
4. 登录后队列能不能翻页、`page_size` 上限；`min_paintwear` 等筛选参数的语义（能不能直接按条件筛出「同条件」的单）。
5. `pay_method` 93 / 94 的区别、`frozen_amount`、`real_num` 的真实含义。
6. 条件单的其它 `type`；供货时到底怎么匹配到哪一单（靠观察真实成交）。

实测原始响应里有别的用户的 `user_id` 和昵称，**没有进仓库**；以后做 fixture 要先脱敏。

## 8. 登录后实测（2026-11-24 晚）

用户在 Claude App 的内置浏览器里自己登录 BUFF（勾了「10 天内免登录」与否没记），我在那个页面里用 `fetch` 发 GET，共 8 次、每次隔 6 秒以上，**没碰到 429、验证码或「系统繁忙」**。用户当时在 BUFF 上挂着 **8 张求购单**，全是普通单（没有条件）。原始响应带用户的 `user_id`、Steam ID、昵称、交易链接，**没进仓库**；下面的单号、价格都是举例。

### 8.1 我的求购单 `wait_supply`（§7 问题 1）

- `page_size=10` 生效；`data` 有 `items`、`goods_infos`（按 `goods_id` 索引的饰品信息，含中英文名、`sell_min_price`、`buy_max_price`、磨损等 `tags`）、`user_infos`、`total_count`、`total_page`、`total_num`、`total_amount`、`sort_by`（默认 `time.desc`，新单在前）。
- 每张单的字段和公开队列一样（`id`、`goods_id`、`price`、`num`、`real_num`、`frozen_num`、`frozen_amount`、`specific`、`state:"progressing"`、`created_at`、`updated_at`、`pay_method`），另外多了：
  - **`buy_max_price`：这件饰品此刻的最高求购价**（和 `goods_infos` 里那件的 `buy_max_price` 相同）；
  - `fee`（都是 `"0"`）、`buy_order_swap_count`、`allow_tradable_cooldown`、`steamid_text`（带昵称，不能进日志）。
- **没有名次字段**。但有 `buy_max_price`，**一次同步就能知道每张单离最高价多远**，不用为每张单查队列。
- `frozen_amount` = 价格 × 件数（先求后付也是这样，含义仍不明）；`real_num` 全是 0（都还没成交，推断是已到手件数）。

### 8.2 `buy_max_price` 不含条件单（§1 推断，这次坐实）

AK-47 | 翔鹤（略有磨损）的公开队列（26 单）：前 7 单都是磨损条件单（151、150、140、132、131、121、112），第 8 单是用户的 101 元普通单，`buy_max_price` 正好是 101。也就是说：

- **`buy_max_price` = 最高的普通单价**，条件单不算。
- 用户这张单「在普通单里排第 1」，但公开队列里排第 8。

### 8.3 公开队列登录后（§7 问题 4）

- `page_size=50`、`page_size=100` 都生效；`page_num=2` 登录后能翻（匿名时第 2 页是 `Login Required`）。梦魇武器箱 `total_count=125`、`page_size=100` 时 2 页。
- 同价按建单先后排：梦魇武器箱 3 元这一档有 43 单，用户的单排在整个队列的第 66 位。
- **`min_paintwear` / `max_paintwear` 的语义：给一件磨损落在这个区间的货，哪些单会要它。** 翔鹤队列加 `min_paintwear=0.08&max_paintwear=0.09` 后剩 23 单：`0.07-0.08` 的条件单被筛掉，`0.07-0.09`、`0.07-0.10` 的和全部普通单留下，顺序不变。所以「一件具体的货先给谁」可以直接问平台，不用自己解析条件。
- `pay_method` 见到 60、78、94、96、101，网页文字有「先求后付」等，具体区别没再查。

### 8.4 成交记录 `buy_order/history`（§7 问题 2）

- 返回的是**订单（bill order）**，不是求购单：共 114 条、`page_size=20` 生效。每条有 `id`（订单号，和求购单号格式相同但不是同一个号）、`goods_id`、`price`、`asset_info`（含磨损 `paintwear`、`assetid`）、`buyer_pay_time`、`transact_time`、`created_at`、`updated_at`、卖家 `seller_id`、`fee`、`income`、`state`、`state_text`、`type`、`mode`、`progress`。
- 这次见到的组合：

  | state_text | state | type | progress |
  |---|---|---|---|
  | 求购成功 | SUCCESS | 2 | 109、111 |
  | 购买成功 | SUCCESS | 6 | 109 |
  | 购买失败-已退款 | FAIL | 2 | 106 |

  推断 **`type=2` 是求购成交、`type=6` 是直接购买**。交易中（卖家已发报价、还没确认）的订单这次没见到，`state` 和 `progress` 待下次有成交时看。
- **订单里没有求购单号**（找遍字段，只有 `sell_order_id`，求购成交时是 `null`）。求购单消失后要判「成交 / 撤单」，只能按「同 `goods_id`、同价、订单建于求购单之后」去配对，和悠悠「消失后查已完成列表」的思路一样，但多一步模糊匹配。
- `/account/api/user/info/v2?meta_list=buy_order_state` **不给状态表**：`meta_list.buy_order_state` 是 `0`（推断是某个计数），其余是用户资料（昵称、打码手机号、Steam 交易链接）。这个接口以后不要调。

### 8.5 登录（§7 问题 3）

- `session` cookie 是 **HttpOnly**，页面脚本读不到（看得到的只有 `Device-Id`、`Locale-Supported`、`game`、`csrf_token`）。监控服务要用它，只能让用户从开发者工具复制、和悠悠贴 token 一样。
- 登录页有「10 天内免登录」勾选项，**推断**勾上时 `session` 约 10 天有效；不勾可能是会话 cookie。实际多久失效要接入后看日志。
- **2026-11-25 实测**：用钥匙串里的 `session` 发一次只读 GET（`wait_supply`），响应的 `Set-Cookie: session` 只有 `HttpOnly; Path=/`，**没有 `Expires` / `Max-Age`**，值和存的一样。所以接口不告诉到期时间；我们存的只有 cookie 值，也没有登录时刻。「10 天」只出现在登录页的勾选项上，服务端实际多久失效仍待观察（known-issues 27）。浏览器开发者工具 Cookies 里 `session` 那一行的 Expires 是登录响应种下的，可以拿来对照。
- 被别处登录挤掉：这次没法测。

### 8.6 饰品对应（§6）

拿用户的 4 件求购饰品核对：SteamDT `/base` 的 `platformList` 里 `name:"BUFF"` 那一项的 `itemId` **就是 BUFF 的 `goods_id`**（4/4 对上，名字一致）。BUFF 的单可以直接映射到悠悠 `templateId`。

### 8.7 对设计的影响

1. **请求量不再是难点**：一次 `wait_supply`（`page_size` 60）就拿到全部单和各自的最高普通单价。只有 `price ≥ buy_max_price` 的单（可能在第 1）才要查一次队列，确认同价有没有更早的单。用户现在 8 张单里只有 1 张要查。
2. **名次口径可以用平台的数**：普通单「升到第 1 名」= 在普通单里第 1，用 `buy_max_price` 判；前面挡着几张条件单另外显示（拿 `min_paintwear` 筛不了普通单，只能数）。intent 待决 8 的建议不变，现在有了数据支撑。
3. **成交检测要模糊配对**：订单不带求购单号，spec 里要写清配对规则和「推测成交」的口径（悠悠 ADR-0018 的做法可以照搬）。
4. **交易中**：`wait_supply` 的 `frozen_num` 和订单的 `progress` 都可能表示「卖家已供货、待确认」，要等一次真实成交再看。

## 9. 给盈亏统计：卖出、库存、出租（2026-11-24 晚，又 6 次只读 GET）

盈亏统计（[ADR-0025](../decisions/0025-pnl-cross-platform-matching.md)）要 BUFF 的卖出记录、出租列表和库存。同一个登录页面里又发了 6 次 GET（另外读了两个网页的 HTML 和脚本，找出租接口），仍然没碰到限流。

### 9.1 卖出记录 `/api/market/sell_order/history?game=csgo&page_num=&page_size=`

- 返回订单（bill order），字段和 §8.4 的购买记录基本相同：`id`、`goods_id`、`price`（成交价）、**`fee`（手续费）、`income`（到手价，= price − fee，平台给的）**、`created_at`、`transact_time`（成交时间；失败的单是 `null`）、`asset_info`（`assetid`、`paintwear`……）、`buyer_id`、`seller_steamid_text`（带昵称，不能进日志）、`state`、`state_text`、`type`、`mode`、`progress`。
- `market_hash_name`、中文名在 `goods_infos[goods_id]` 里（和 `wait_supply` 一样）。
- **没有件数字段，一条订单一件**；`bundle_info` 这次都是空的（打包出售没见到）。
- 见到的状态：`SUCCESS / 出售成功`（progress 109）、`FAIL / 买家支付失败`（104）、`FAIL / 出售失败`（106），`type` 都是 1。只有 SUCCESS 算卖出。
- 手续费率见到 1.5%、2.5%、2.76%、5%，按饰品和时期不同，**一律用 `income`，不要自己算**。
- `page_size=100` 生效；默认按建单时间倒序。奇怪的是 `page_size=20` 时 `total_count=181`、`page_size=100` 时是 301，分页时别信 `total_count`，翻到空页为止。
- **这个号在 BUFF 上最近一笔卖出是 2026-06-30**，最早一页覆盖到 2025-05-22；2026-09-01 之后一笔都没有。

### 9.2 库存 `/api/market/steam_inventory?game=csgo&force=0&page_num=&page_size=&state=all`

- **只能用 `force=0`**（读 BUFF 缓存的库存）；`force=1` 会让 BUFF 去 Steam 重新拉，没必要也没试。
- `page_size=500` 生效。这个号 990 件、320 种；`brief_info` 是「件数：990  估值：¥2804.08」。
- 每件一行（`amount` 都是 1），有 `market_hash_name`、`goods_id`、`assetid`、`state` / `state_text`（`state_desc`：1 不可交易、2 不可出售、3 可出售、4 限制交易、5 上架审核中、6 保护期中）、`progress` / `progress_text`（`progress_desc`：0 库存中、1 出售中、2 待发货、3 存入中、4 不可出售、5 供应中、6 公示中）、`sell_order_id`、`sell_order_price`、`tradable`、`buy_price`（单位不明，大多是 0 或 null，不能当成本用）。
- 出租相关字段：`allow_rent`、`is_renting`、`rent_order`。

### 9.3 出租

- **网页版没有「我的出租」入口**：导航只有「我的库存」「我的出售」「我的求购」；在这两个页面的 HTML 和脚本里也没找到出租相关的接口路径。出租多半只在 BUFF App 里。
- 库存 990 件里 **`is_renting` 全是 false、`rent_order` 全空**。所以要么这个号没在 BUFF 出租，要么出租出去的货不在这个库存列表里。要拿出租列表，得先确认用户在哪个平台出租，或者抓 App 的请求（这次没做）。

### 9.4 对盈亏「去向不明」的提示

这个号在 BUFF 上 2026-07 以后没有卖出、也没有在出租的货，库存里却有 990 件。悠悠上买、盈亏里「去向不明」的那些，**多半还在 Steam 库存里**（BUFF 的库存列表就是 Steam 库存），或者在别的号、别的平台卖掉 / 出租了，不是在这个 BUFF 号上卖掉的。拿库存的 `market_hash_name` 对一下就能分清。

## 10. 写测试样本时又看到的（2026-11-24 夜，5 次只读 GET）

- **公开队列里的 `user_id` 是打过码的**：同一张单，在我的 `wait_supply` 里是我的用户号，在公开队列里是另一个号。所以认「我这张单」只能靠单号（`core/buff_rank.py`）。
- 库存的 `buy_price` 单位是**分**（买入 ¥11 的那件是 `1100`）；大多数是 0 或 null，仍然不能当成本。
- 翔鹤在一个小时里被人以 102 元超过：`wait_supply` 里这张单的 `buy_max_price` 跟着变成 102，名次掉到普通单第 2——和推断一致。
- 测试样本在 `tests/fixtures/buff/`，在浏览器里脱敏后才拿出来（用户号、Steam ID、昵称、头像、交易链接、`assetid`、订单号都换成了假值）。

