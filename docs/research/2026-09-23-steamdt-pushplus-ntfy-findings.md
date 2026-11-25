# SteamDT / PushPlus / ntfy：真实对接记录（2026-09-21 ~ 09-23）

> 悠悠有品那边的对接记录在 [2026-09-06-live-api-findings.md](2026-09-06-live-api-findings.md)。这一份记的是后来接进来的三个外部服务：
> **SteamDT**（多平台行情，M2 的数据源）、**PushPlus**（微信推送，现在的主推送通道）、**ntfy**（原推送通道，查实从未实时送达）。
>
> 和悠悠那份一样，这里每一条都是**对真实接口打出来的结果**，不是照文档抄的。其中好几条和文档说的不一样，或者文档根本没写。**改这三个适配器之前先读这一份。**

三个服务有一个共同点，值得先说：**HTTP 永远返回 200，成败只看响应体里的业务码。** 悠悠那边 HTTP 429 其实是版本闸门，SteamDT 和 PushPlus 出错也一律回 200。按状态码判断成败的写法，在这个项目里对哪个服务都是错的。

---

## 1. SteamDT 开放平台

- Base：`https://open.steamdt.com`；鉴权：`Authorization: Bearer <API_KEY>`；文档：doc.steamdt.com
- 信封：`{success, data, errorCode, errorMsg, errorData, errorCodeStr}`，**HTTP 永远 200**
- 适配器：`skinpulse/adapters/steamdt/`；实测响应原样存在 `tests/fixtures/steamdt/`

### 1.1 错误码（实测）

| errorCode | 文案 | 真实含义 | 映射 |
|---|---|---|---|
| 2007 | 请先申请ApiKey | **key 不认识**（拼错，或已被「重新生成」作废）。字面意思会把人引去重新申请一个自己已经有的 key | `SteamDTAuthError` |
| 4002 | apiKey不能为空 | 没带 key | `SteamDTAuthError` |
| 4005 | 当前接口请求已达到上限。详细规则请参阅接口文档。 | 配额用完 | `SteamDTQuotaExceeded` |
| 100002 | 参数错误 | 实测是 `/price/batch` 一次塞了超过 100 个名字。**不说是哪个参数，也照样扣一次配额** | 客户端发出前就拦住 |

4005 的文案用的是「上限」，而最初写的配额关键词兜底（配额 / 频率 / 限流）一个都没覆盖到——要不是真撞上一次，配额错误会被当成普通错误去重试。

### 1.2 配额

| 接口 | 配额 | 备注 |
|---|---|---|
| `/open/cs2/v1/price/single` | 60 次/分钟 | 单品各平台价格 |
| `/open/cs2/v1/price/batch` | **1 次/分钟，每次最多 100 个名字** | 全库对账唯一划算的接口 |
| `/open/cs2/v1/base` | **1 次/天** | 约 11 MB / 4.4 万条，必须落盘缓存 |
| `/open/cs2/item/v1/kline` | 120 次/分钟 | 单品 K 线 |
| `/open/cs2/v1/price/avg` | 未公布 | 7 日均价 |
| `/open/cs2/broad/v1/index`、`/broad/v1/kline` | **未公布，实测很紧** | 大盘 K 线几分钟内调 3 次就回 4005 |

**官方口径（2026-09-24 核对 doc.steamdt.com「接口权限列表(请优先查看)」，页面修改于 2026-06-08）：** 只有按分钟 / 小时 / 日的频率限制，**没有总量或按月的额度**。上表的数字与官方一致；官方另列了 `/v1|v2/wear` 每小时 36000 次、`/v1|v2/inspect` 每日 100 次（本项目没用）。`price/avg` 与大盘两个接口不在官方列表里。首页写明 API_KEY「目前限时免费」——以后可能收费，届时需要重新评估（[ADR-0003](../decisions/0003-market-data-steamdt-only.md)）。

`/base` 当天的那一次调用结果存在 `data/steamdt_base.json`，`steamdt-items --from-file` 可以重建映射而不消耗额度。

### 1.3 价格 0 = 没有数据，不是零元

`/price/single` 会把没覆盖到的平台也列出来，值是 `sellPrice: 0, sellCount: 0` 外加一个几个月前的 `updateTime`（WAXPEER / DMARKET / CSMONEY / SKINPORT）。解析时 0 一律转成 `None`。本项目已经因为「价格静悄悄变成 0」吃过一次大亏（悠悠那边 371 张单价格全存成 0.00），这里不能再来一次。

### 1.4 映射：悠悠 templateId ↔ marketHashName

- `/base` 每条带 `platformList[{name, itemId}]`，**YOUPIN 的 itemId 就是悠悠的 templateId**。用自己的单核对过：1414 = AK-47 红线（久经），1022 = 裹手防水布胶带（久经）。
- 4.4 万条里 35,023 条有 YOUPIN id；我们在册的 425 个模板 **100% 都能映射**。
- **映射必须走 ID，不能走名字**：凭中文名手写英文名，三个样本当场错一个——「人工染色」是 `Stained`，不是 `Case Hardened`（那是「表面淬火」）。

### 1.5 平台覆盖（在我们的饰品上实测）

| | 有数据的平台 |
|---|---|
| 在售价 | C5 / HALOSKINS / BUFF / STEAM / YOUPIN 全覆盖 |
| 求购价 | **只有 C5 / BUFF / YOUPIN**，其余平台一件都没有 |

所以对账的参照集按买卖两侧分开定：卖方 BUFF/C5/HALOSKINS，买方 BUFF/C5。**Steam 故意不进参照**：15% 手续费、余额提不出来，不是同一个市场，纳进来会系统性抬高参照。

### 1.6 K 线（文档没写或写得不全的部分）

- `POST /open/cs2/item/v1/kline`，body `{marketHashName, type, platform?}`；platform 只认 ALL / BUFF / YOUPIN / C5 / STEAM / HALOSKINS
- **数组顺序是 `[时间, 开, 收, 高, 低]`，不是 OHLC。** AK-47 红线久经的 365 根日线，按这个顺序读全部满足「高 ≥ 开/收 ≥ 低」，按 OHLC 读一根都不满足。解析器用这条恒等关系做守卫，超过 5% 的行不满足就报错。
- `type`：**1 = 小时（约 90 天）、2 = 日（365 天）、3 = 周（从 2022-07 起）**。文档只说「1~3」。
  - 2026-09-27 补测周线（fixture `kline_item_weekly.json`、`kline_broad_weekly.json`）：AK-47 红线久经 171 根，从 2022-07-02 起，但 **2023-12 到 2024-05、2024-06 到 2024-12 两段没有数据**（09-23 测时还是 222 根、没有空档——SteamDT 的历史会变）。**大盘周线从 2023-07-02 起**（170 根，基本连续），不是 2022-07。看板的「全部」范围画周线，空档处标「缺 N 周」。
- 时间戳是**秒级字符串**，UTC。转本地时间再显示，否则整条轴标早一天。
- 日线最后一根是**今天、尚未收盘**。
- **不含成交量**（文档原话「不包含成交数据」），所以没有量比这类信号。
- **流动性差的饰品会有离谱的影线**：暗影双匕 | 北方森林（崭新）平时 600~1000，有两天最高价记成了 11178（多半是错价挂单）。图表按实体定纵轴，离谱影线截断并在图上注明。

### 1.7 大盘

- `GET /open/cs2/broad/v1/index`：`broadMarketIndex`、`updateTime`、`diffYesterday`、`diffYesterdayRatio`、`historyMarketIndexList`（最近约 25 个整点值）
- **`diffYesterdayRatio` 已经是百分数**：涨 3.55、指数 795.52，给的是 0.4482（即 0.448%）。再乘 100 就会显示成「涨 44.8%」。
- `POST /open/cs2/broad/v1/kline`，body `{type}`，K 线格式同单品
- 大盘 K 线和最新指数是**同一条序列**：最后一根收盘价就是最新指数。一年前 1560.73，2026-09-23 为 795.71，**跌了 49%**。一开始看起来像两个接口对不上，其实是市场真的跌了一半。

### 1.8 MCP 服务（`https://mcp.steamdt.com/mcp`）

- 工具是 **6 个**，不是官方提示词里说的 5 个：多一个 `search_items`（中文名/片段模糊搜 marketHashName）。
- **`initialize` 握手不校验鉴权**，只有 `tools/call` 才校验。所以 `claude mcp list` 显示 ✔ Connected 只说明服务器可达，不说明 key 有效。验证 key 必须真发一次取数请求。
- 用已「重新生成」作废的旧 key 调工具，返回「当前账号尚未申请 API_KEY」——同 2007，字面意思有误导。
- MCP 只是给会话里交互式查询用的；SkinPulse 进程走的是上面的 REST 接口。

### 1.9 首轮全量对账结果（2026-09-23，425 个模板）

| 侧 | 参照 | 悠悠相对参照中位数 | 悠悠更低占比 |
|---|---|---|---|
| 卖方（在售价） | BUFF / C5 / HALOSKINS | **低 1.6%**（IQR 0.960 ~ 1.008） | 70% |
| 买方（求购价） | BUFF / C5 | **低 9.1%**（IQR 0.783 ~ 0.984） | 79% |

- 卖方几乎没有系统性偏差：看板「市场价」取悠悠在售最低价，这个基准站得住。
- 买方低得多且分散：悠悠挂求购的竞争比 BUFF/C5 弱——这是「在悠悠排进前 2」相对容易的原因，但也意味着真实买盘可能在别处。告警正文里的「别处买盘」一行就是为此加的。
- **别用 `/price/avg` 的全平台均价来比**：它把 Steam、V5ITEM 这类价格水平完全不同的平台混进来。一件 AK-47 按那个口径看悠悠「低 13.7%」，对国内同行的真实差距只有 1.6%。

---

### 1.10 `YOUPIN` 那一行的 `bidding_price` 是所有子队列里的最高求购（2026-10-09 实测）

- 10-08 价格快照 356 个模板里，83% 有这个价。
- 和我们实时核对的「不限条件」子队列榜首比：相等 27%、SteamDT 更低 13%、**更高 60%**。它含了磨损区间、图案这些条件单。
- 刀、手套最明显：SteamDT 给的最高求购常常比在售价还高（例：专业手套 | 狩鹿，在售 235、最高求购 257）。
- 用法：当「排第 1 要出的价」是偏保守的；不能当「不限条件」队列的榜首。选品（价位 spec §9b）就是按这个口径用的。

## 2. PushPlus（推送加）——现在的主推送通道

- `POST https://www.pushplus.plus/send`，body `{token, title, content, template}`；本项目用 `template: "txt"`
- 信封：`{code, msg, data}`，**HTTP 永远 200**。成功时 `data` 是消息流水号（shortCode）；**出错时细节在 `data` 里，`msg` 只是类别**（例：`msg` =「用户令牌不正确」，`data` =「无效的用户token」）
- 适配器：`skinpulse/adapters/pushplus/client.py`

| code | 含义 | 处理 |
|---|---|---|
| 200 | 服务端收下了 | — |
| 903 | token 不对（实测：乱填 token） | `PushPlusAuthError` |
| 905 | 账号未实名认证 | `PushPlusNeedsRealName` |
| 900 | 账号被限制（通常一天超 1000 次，或封 7 天） | `PushPlusRestricted`，**绝不重试**：官方要求停手，约 2 天自动恢复 |
| 999 | 服务端校验错误（实测：不带 token） | `PushPlusError` |

几条要记住的：

- **返回 200 只代表收下了，不代表送到微信**——官方文档原话。查真实送达状态要走「开放接口」（另外申请 secretKey 并绑 IP 白名单），暂未接入。`pushplus-token` 设置时发一条测试消息，靠用户在微信里亲眼确认。
- **消息 token 和用户 token 都能发消息**（只有开放接口必须用用户 token）。用户用的是消息 token，没问题。
- `txt` 模板下 `\n` 换行在微信里正常显示，空行也在（用户 2026-09-23 确认）。用 txt 而不用默认的 html，是因为饰品名里有 `|` `★` `™`，将来万一出现 `<` 或 `&` 会被 html 模板吃掉。
- **第一次设置失败的真实原因**：用户在 Windows 的隐藏输入框里粘贴 token，拿到 903。文档排除了 token 类型问题，最可能是隐藏输入框里按 Ctrl+V 进来的是控制字符而不是剪贴板内容。改用 `--from-file` 一次成功。现在命令会拦下控制字符，903 时报出收到的 token 位数。
- PowerShell 5.1 的 `Out-File -Encoding utf8` 会在文件开头写 BOM，读 token 文件时用 `utf-8-sig` 去掉。

---

## 3. ntfy——为什么不再是主通道

**结论：ntfy 的推送从来没有实时到过用户手机。**

- 库里标成 `sent` 的推送去重后 **20 次**（09-06 ~ 09-23），用户手机上只看到 **2 次**。
- `sent` 的意思只是「ntfy.sh 收下了」（HTTP 200）。断的是第二段：**ntfy.sh → 手机**。
- 原因：Google Play 版 ntfy App 对 ntfy.sh 的订阅走 **FCM**，国内不通、国产手机也多半没有 Google 服务。FCM 不通时，App 只能自己常驻后台保持长连接——需要自启动和后台运行权限，而**用户明确不愿意给**（见 memory `feedback-skinpulse-notifications`）。
- **ntfy.sh 只把消息存 12 小时**：2026-09-23 直接轮询 topic 的缓存，服务器上只剩两条，过期时间都恰好是发布时间 +12 小时。App 没在 12 小时内被打开，消息就永久没了。用户跑 `ntfy-test` 时 App 被唤起，才拉到了 10 小时前那条。
- 另外从国内连 ntfy.sh 单次请求要 5 秒左右；2026-09-22 夜里有 3 次推送直接 `ConnectError`（这一段已加了连接阶段重试）。
- 在「不给后台权限」这个前提下，这是**机制性走不通**，不是配置问题。所以主通道换成借微信通道的 PushPlus；ntfy 保留并行发送，只作兜底。

**通用教训**：任何推送服务返回「成功」，都只代表它的服务器收下了。有没有到手机，只能以手机上实际收到为准。
