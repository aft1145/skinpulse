> **⚠️ 部分内容已被真实对接推翻（2026-09-06）。**
> 接口层的当前事实见 [2026-09-06-live-api-findings.md](2026-09-06-live-api-findings.md)；
> 系统现状与待办见 ../STATUS.md（未公开）。

<!-- 由多 agent 调研工作流生成（22 个 agent，113 个去重仓库，2026-08-23），经人工校对。 -->

> **校对说明（以 [第一手接口验证记录](2026-08-23-firsthand-api-probe.md) 为准）**
>
> 1. 报告 3.1 节称 `getTemplatePurchaseOrderListPC` 需要 Bearer 且"响应字段未经实测"。**实测：免登录、仅带 `uk` 头即可取前 20 条**；响应 `purchaseOrderResponseList[]` 含 `purchaseNo/userId/userName/purchasePrice/surplusQuantity/abradeText/fadeText/specialStyle/typeId/isRankFirst/rankFirstPrice`，按价格降序；不同磨损区间/款式的求购**混排在同一列表**（AK 红线 FT、爪子刀多普勒 FN 等 4 个模板实测）。登录只为自动获取"我的求购"（`searchPurchaseOrderList`）与翻页。
> 2. 报告称 "App-Version 5.28.3 触发 85100"。实测 PC 域名 `pc-api.youpin898.com` 用 5.26.0 正常；**缺少 `uk` 头同样返回 85100**，排查时勿只盯版本号。
> 3. `uk` 生成已在 Python 复现（`deviceW2` 只需加密 `{"iud": uuid}`），`deviceUk` 非必需；`api.youpin898.com` 与 `pc-api.youpin898.com` 均可用。
> 4. BUFF `/api/market/goods` 带 `search=` 参数实测返回 `Login Required`；`sell_order`/`buy_order` 免登录可用。
> 5. 官方开放平台文档已通读（31 篇，2026-02-26 更新）：无求购接口，`goodsQuery`/`batchGetOnSaleCommodityInfo` 1 次/300s、`templateQuery` 1 次/120s、`queryViewChart` 10 次/s、`byTemplateCreateOrderV2` 5 次/s；申请需手持身份证照片、预期日交易流水。
> 6. 两项由评审员提出的补充调研（用真实账号挂单做实验、对悠悠做限速/封禁阈值压测）被安全策略拦截，未执行；前者建议由发起人在确认排名定义后用小号手动完成，后者不建议做。
> 7. 本会话 Web 搜索配额（200 次）与 GitHub 匿名 API 配额在调研中用尽，部分仓库信息来自 README/源码直读，stars 数为当日值。

# SkinPulse 前期调研报告

调研日期：2026-08-23。本报告汇总 GitHub 中英文生态扫描、三平台接口一手探测、5 个重点仓库源码深读、4 项缺口补充调研及评审意见。凡未经实测或仅来自单一推测来源的内容，均标注「待验证」。

# 一、结论摘要

1. **核心需求"悠悠有品求购排名 ≤ 2 提醒"可以做，但只能走非官方 App/PC 接口**：悠悠官方开放平台（2025-06 上线）完全没有求购接口且市场查询限频 1 次/300 秒；所有第三方聚合源（SteamDT/CSQAQ/cs2.sh）只给"最高求购价+求购数"，拿不到求购队列与本人位置。
2. **实现路径已由 Steamauto（1,287★，2026-07-15 仍维护，AGPL-3.0）的 uuyoupinapi 模块给出**：`searchPurchaseOrderList` 取自己的求购单 → `getTemplatePurchaseOrderList(PC)` 取该饰品求购队列 → 按 `purchasePrice` 降序定位自己的 `userId` 即排名。但这 5 个求购方法在仓库内是无人调用的死代码，响应字段、排序规则、磨损分队列均未经实测，**这是项目第一优先级的待验证事项**。
3. **最大风险是悠悠的版本门禁与风控**：服务端按 `App-Version` 拒绝旧版本（5.28.3 已在 2026-05 触发 85100），需跟随 5.45.4/5.46.1 以上；风控码 84104、uk 失效 HTTP 405、机房 IP 发短信必返 5050；官方文档明言私自调用接口干扰运营将永久封禁（尚无只读脚本被封的公开案例，待验证）。
4. **三平台比价与大盘分析不要自建爬虫，优先用 SteamDT 开放平台 + CSQAQ 开放 API**：SteamDT price/batch 每分钟 100 件、含 YOUPIN/BUFF/C5/STEAM 的在售价/求购价/数量，限时免费；CSQAQ 注册即得 token、1 次/秒不限次，含 23 个子指数、涨跌榜、挂刀行情、悠悠求购价日线历史（2021-07 起）。
5. **BUFF 直接爬取有封号先例（buff2steam README），但 2026-08-23 实测匿名 GET sell_order/buy_order 第一页仍免登录可用**；BUFF 已上线官方开发者 API（在售底价 500 次/天、求购单管理 60 次/分），申请条件与费用需登录控制台查看（待验证）。
6. **Steam 社区市场只做低频校验**：itemordershistogram 约 15-20 次/分/IP，429 后软封数分钟至数小时，国内需代理；Steam SPA 已迁移到 `/market/orderbook` RPC，旧接口面临淘汰。
7. **历史数据可免费回填**：iflow.work 站点虽于 2026-05-20 停服，但 `api.iflow.work/export` 存档仍在线（2,990 份 12 小时快照，2022-04-18 → 2026-05-20，约 7-8 GB）；加上 CSQAQ chart 日线（period ≤ 1095 天）与 atalantus 的 BUFF 底价（2021-07 → 2024-01）。小时级悠悠求购价历史只有付费 cs2.sh Scale（$200/月）。
8. **推送用 ntfy 完全可行**：自建（auth deny-all + tk_ 令牌）或直接用 ntfy.sh（匿名 250 条/天/IP，突发 60 后每 5 秒补 1）；Android 须用 F-Droid 构建 APK 开"即时投递"，iOS 需配 `upstream-base-url` 或改用 Bark。
9. **量化部分可借鉴的是"均线偏离/动量/量能/跨平台价差/挂刀比例"等简单因子与 walk-forward 回测**，学术实证（Pettersson 2025，64 万条）显示 7 日均线偏离是最强特征（2026-09-27 更正：多轮检索未找到这篇论文，找到原文前不作依据，见 intent（未公开）），LSTM 远逊于树模型；Valve 政策冲击（2025-07-16 交易保护、2025-10-23 汰换出刀使 SteamDT 指数两日 -68.5%）是最大尾部风险，任何模型都必须接"更新公告"事件源。
10. **推荐技术方案：Python 单体服务 + SQLite/Parquet + APScheduler + ntfy，部署在家庭宽带（非机房 IP）**，悠悠适配层按 Steamauto/CS2TradeMonitor/uuyp-trade-analyzer 的端点与头部重写（避免 AGPL/GPL 传染），分三期交付。

# 二、现成开源项目盘点

| 仓库 | stars | 最近更新 | 平台 | 关键能力 | 对本项目价值 |
|---|---|---|---|---|---|
| Steamauto/Steamauto | 1,287 | 2026-07-15 | 悠悠/BUFF/Steam/C5/ECO | 悠悠短信登录+uk 反爬+5 个求购端点封装、BUFF 扫码/OpenID 登录、Apprise 通知 | 极高：唯一含悠悠求购接口的活跃项目 |
| cs2juece/CS2TradeMonitor | 7 | 2026-07-26 | 悠悠/Steam/CSQAQ/SteamDT | 最完整的悠悠 App 5.45.4 请求头模板、800ms 限频+429 指数退避、大盘提醒、量化页 | 高：鉴权/限流模板；无求购 |
| ModestSerhat/cs2-marketplace-ids | 250 | 2026-08-17 | BUFF/悠悠/buff.market/cs.money | 41,611 条 market_hash_name→buff goods_id/youpin templateId 映射 JSON | 高：ID 对齐必需 |
| EricZhu-42/SteamTradingSiteTracker | 2,324 | 2024-09-04 | BUFF/IGXE/C5/悠悠/ECO/Steam | 挂刀行情站后端；2026-05-20 停服；存档 API 仍可下载 | 中：历史数据+手续费算法 |
| youki258/uuyp-trade-analyzer | 1 | 2026-08-05 | 悠悠 | 三种登录（短信/密码/贴 token）、/api/app 换 uk、IP 风控研究文档 | 中：鉴权与反风控样板 |
| EricZhu-42/SteamTradingSite-ID-Mapper | 152 | 2026-05-20 | Steam/BUFF/IGXE/C5/悠悠 | CC-BY-4.0 ID 映射，含 Steam item_nameid | 高：补 Steam nameid |
| somespecialone/aiosteampy | 106 | 2026-08-19 | Steam | 异步 histogram/pricehistory/下单，If-Modified-Since 节流 | 中 |
| AjaXsb/hridaya_steam_market_tracker | 1 | 2026-07-01 | Steam | 15 req/60s 滑动窗口限速器、双调度器 | 中：限流设计参考 |
| Pgooone/cs-monitor | 8 | 2026-04-30 | SteamDT | 39k 饰品、30 分钟采集、K 线、企微/TG 告警 | 中 |
| BanditleaderTF/CS_MONITOR | 2 | 2026-07-26 | SteamDT | 多平台阈值告警（含最高求购价） | 中 |
| zkcaryq/steamdt-mcp | 1 | 2026-06-04 | SteamDT | 暴露 price/avg、broad index/kline 等端点 | 中 |
| zkcaryq/cs2-market-qq-bot | 1 | 2026-06-25 | SteamDT/CSQAQ | 最完整 CSQAQ 端点清单（24 个） | 中 |
| xfy7711/cs2-index-predictor | 0 | 2026-08-05 | CSQAQ | 约束 Extra Trees + walk-forward + conformal 区间 | 中：唯一有严肃回测 |
| AKA-hhh/cs2-price-vault | 5 | 2026-07-11 | CSQAQ | 规则评分+DeepSeek、布林/RSI/MACD | 低-中 |
| hldh214/buff2steam | 203 | 2024-11-18 | BUFF/Steam | 挂刀扫描；README 警告有用户被封号 | 低：风险证据 |
| EricZhu-42/SteamTradingSiteTracker-Data | 99 | 2025-03-25 | 多平台 | datadump API 文档 | 高：历史回填 |
| atalantus/buff-price-history-archive | 14 | 2025-05-05 | BUFF | 2021-07→2024-01 底价归档 | 中 |
| binwiederhier/ntfy | 33,683 | 2026-08-20 | — | v2.27.0 推送服务 | 高 |
| caronc/apprise | 17,137 | 2026-08-20 | — | 统一通知 URL（ntfy/Bark/Server酱/企微） | 中 |
| Shinian10/CS | 0 | 2025-09-18 | 悠悠 | 轮询 getTemplatePurchaseOrderListPC 解析 purchaseOrderResponseList[].purchasePrice | 中：最接近原型，状态未知 |

**已失效/不建议依赖**：worldofgoo9/AutoUU（自述 API 失效，2024-02）、jiajiaxd/uuyoupinapi（已归档并入 Steamauto）、ShevonKuan/csgo_investment（2023-03，悠悠 API 需鉴权后失效）、perrebser/buff163Prices（2023-11）、markzhdan/buff163-unofficial-api（2024-11）、puppylpg/oddish（2023 归档）、sotolko/csgo-market-sniper 等 Steam 狙击 bot、csgobackpack（2024-06 关站）。

**前 5 名点评：**

**1. Steamauto/Steamauto**。AGPL-3.0，Python，约 830 commits，最新 Release 5.9.1（2026-07-14）。`uuyoupinapi/__init__.py`（1,191 行）实现了悠悠 App 端全套鉴权：短信登录（含上行短信）、`POST /api/deviceW2` 用 AES-ECB + RSA-PKCS1v1.5 本地生成 uk（PR #262，2025-11-17 合入）、30 秒刷新、错误码 84101/84104/405 处理。2025-04-12 提交"添加求购获取接口"，封装了 `savePurchaseOrder/updatePurchaseOrder/getTemplatePurchaseOrderList/getTemplatePurchaseOrderListPC/searchPurchaseOrderList`。**但 plugins/ 目录无任何调用者**，issue #224（求购发布）关闭未实现、#268（2026-01 塞求购）无人回应，响应字段完全未解析。源码硬编码 App-Version 5.28.3 已在 2026-05 触发 85100。结论：不能 import，只能按端点与头部重写（AGPL 传染），且求购接口必须自行抓包验证。其 Apprise 通知层原生支持 `ntfy://`。

**2. cs2juece/CS2TradeMonitor**。GPL-3.0，C#/.NET 10 WinForms，仅 Windows，公开仓库是私有仓库的单向快照（0 issue，不收 PR）。价值在 `YouPinMobileApiClient.cs`：完整模拟 Android App 5.45.4（UA okhttp/4.9.3、deviceUk、requestTag、Device-Info、Sessionid）、设备指纹由 MachineGuid 派生保持稳定、`getUserInfoForApp` 心跳保活、每端点串行 + 800ms 最小间隔、429 按 5/10/20/30 分钟指数冷却、按"访问频繁/风控/签名/加密"关键词告警。悠悠求购价来自 SteamDT 的 `biddingPrice`，**没有任何求购队列端点**。不能复用代码（GPL+C#），但它是 2026-07 仍有效的鉴权事实清单。

**3. ModestSerhat/cs2-marketplace-ids**。无 License（法律上需联系作者）、单人维护（238/240 commits）、每周更新。`cs2_marketplaceids.json` 8.43 MB：40,844 条 buff163_goods_id、39,265 条 youpin_id、39,188 条同时有两平台 ID，另含多普勒相位、印花/挂件 ID、Case Hardened 图案 Tier。BetterFloat 用 youpin_id 直接拼 `youpin898.com/market/goods-list?templateId=`，证明它就是 templateId（与 App JSON 接口一致性待抽样验证）。建议每日拉取一次并随版本快照，注意双空格/★/™ 字符规范化。

**4. EricZhu-42/SteamTradingSiteTracker**。MIT，站点 2026-05-20 停服（www.iflow.work 302 → Notion 公告），付费 API 2026-01-04 停止，但免费存档 `api.iflow.work/export/list?dir_name=priority_archive` 今日仍返回 2,990 个 zip。开源脚本证实旧库字段为各平台在售价梯队 + Steam 买卖盘前 10 档 + 24h 成交量，**不含悠悠求购价**；2024-02 后新库文档称含"各平台最高求购价"（待解包验证）。可借鉴 `utils.calculate_after_fee`（逆推 Steam 5%+10% 手续费）与按 weighted_ratio 的优先级调度。求购功能为 0。

**5. youki258/uuyp-trade-analyzer**。MIT，2026-07 新建，1★。`backend/exporter/client.py` 提供 App-Version 5.46.1 头部、`POST /api/app` 上报伪造 OnePlus 指纹换 uk（作者称实测有效）、短信/密码/贴 token 三种登录、1 秒间隔 + 84104 后 30 秒重试。`docs/ip_risk_control_research.md` 记录：阿里云等机房 IP 调 SendSignInSmsCode 必返 5050，持 token 读数据正常；Python requests 的 TLS 指纹已被识别，建议 curl_cffi。无求购功能；其 `buy/list` 是已成交购买账单，不是求购。

**谁实现了悠悠求购接口、能否直接复用**：只有 Steamauto 封装了端点（死代码），Shinian10/CS 与 i4cobus/cs2-cross-market-arbitrage-analyzer 解析过 `getTemplatePurchaseOrderListPC` 返回的 `data.purchaseOrderResponseList[].purchasePrice`。没有任何项目实现"本人排名"计算。端点可抄，逻辑需自写，响应结构需自验。

# 三、三平台接口与鉴权现状

## 3.1 悠悠有品

三套域名：`api.youpin898.com`（Android App，AppType=4）、`pc-api.youpin898.com`（网页，appType=1、platform=pc）、`gw-openapi.youpin898.com`（官方开放平台）。

| 端点 | 方法 | 用途 | 参数 | 鉴权 | 限速 | 来源 |
|---|---|---|---|---|---|---|
| /api/user/Auth/SendSignInSmsCode | POST | 发登录短信 | {Area:86, Mobile, Sessionid, Code:""} | 无 token，需 uk | 机房 IP 返 5050 | Steamauto、uuyp-trade-analyzer |
| /api/user/Auth/SmsSignIn | POST | 短信登录取 Data.Token | {Area, Code, DeviceName, Sessionid, Mobile, TenDay:1} | 无 | — | 同上 |
| /api/user/Account/getUserInfo 或 /api/youpin/bff/user/Account/getUserInfoForApp | GET | 校验 token、取 UserId、心跳 | — | Bearer | — | Steamauto、CS2TradeMonitor |
| /api/deviceW2 | POST | 生成 uk/deviceUk | {encryptedData: AES-ECB({iud}), encryptedAesKey: RSA(key)} | 无 | 30 秒刷新 | Steamauto UUApiCrypt |
| /api/youpin/bff/trade/purchase/order/searchPurchaseOrderList | POST | 我的求购单 | {pageIndex, pageSize:40, status:20} | Bearer | 翻页 2.5s | Steamauto（status 含义待验证） |
| /api/youpin/bff/trade/purchase/order/getTemplatePurchaseOrderList | POST | 某饰品求购队列（App） | {templateId, pageIndex, pageSize:30, minAbrade:0, maxAbrade:1, typeId:-1} | Bearer | 未知 | Steamauto（字段待验证） |
| .../getTemplatePurchaseOrderListPC | POST | 同上 PC 版 | 加 minFade/maxFade/showMaxPriceFlag | Bearer + uk + platform=pc | 未知 | Steamauto、网页 JS、i4cobus |
| .../getTemplateFilterConfigV2 | POST | 磨损/款式分区配置 | {templateId} | Bearer | — | 网页 JS |
| .../savePurchaseOrder / updatePurchaseOrder / deletePurchaseOrder | POST | 发布/改/删求购（写） | 见 Steamauto | Bearer | — | Steamauto、网页 JS |
| /api/homepage/v2 或 v3/detail/commodity/list/sell | POST | 在售列表/最低价 | {templateId,...} | Bearer | 84104 风控、85100 版本过低 | Steamauto、CS2TradeMonitor |
| /api/homepage/pc/goods/market/queryOnSaleCommodityList | POST | PC 在售列表（含磨损筛选） | 见 Steamauto | Bearer + uk | — | Steamauto |
| /api/homepage/es/commodity/GetCsGoPagedList | POST | 旧在售接口 | {templateId, pageSize...} | **2026-08-23 实测无鉴权可用** | 未知 | SteamTradingSiteTracker 实测 |
| /api/homepage/es/template/GetCsGoPagedList | POST | 模板搜索 | {keyWords...} | 实测返回 84103 需登录 | — | 同上 |
| /api/youpin/bff/trade/sale/v1/buy/list、sell/list | POST | 买入/卖出账单 | {orderStatus, pageIndex, pageSize≤20} | Bearer | 1s | uuyp-trade-analyzer |

网页端 PC 求购列表渲染用到的字段：`headPicUrl, userName, autoReceived, surplusQuantity, purchasePrice, userId, purchaseNo`，网页通过 `userId === 当前 UserId` 判定"我的求购"。**无显式 rank 字段**；排序是否严格按价格降序、同价是否时间优先、是否按磨损/款式分独立队列——全部待验证。

**鉴权获取**：短信登录一次缓存 token（有效期未知，失效返回 84101）；或从 App 抓包贴 token。必备头：`authorization: Bearer`、`App-Version`（建议 ≥5.45.4 做成配置项）、`AppType:4`、`package-type:uuyp`、`DeviceId/DeviceToken`（稳定随机串）、`platform`、`Gameid:730`、`uk`（PC 端点必须）。

**风控与限速（无官方数字）**：Steamauto 求购翻页 2.5s、操作前随机 5-15s；CS2TradeMonitor 800ms 最小间隔、429 后 5→30 分钟指数冷却；uuyp-trade-analyzer 1s 间隔、84104 后 30s。错误码：84101 登录失效、84103 需登录、84104 风控、85100 版本过低、5050 机房 IP/版本、HTTP 405 uk 失效、429 限流。PC 网页已存在 `sk` 头 AES/RSA 加密通道（目前至少覆盖 getUserInfoForApp、rent-out-record-list），若扩散到求购接口需复刻前端加解密（待验证范围）。

**官方开放平台**：需支付宝实名、18-40 周岁、手持身份证、3-5 工作日审核；SHA256withRSA 签名；goodsQuery/batchGetOnSaleCommodityInfo 1 次/300s、templateQuery 1 次/120s；**无求购、无走势接口**。仅当需要合规的在售价/上架能力时考虑。

## 3.2 网易 BUFF

| 端点 | 方法 | 用途 | 参数 | 鉴权 | 限速 | 来源 |
|---|---|---|---|---|---|---|
| /api/market/goods?game=csgo&page_num=1 | GET | 商品列表（含 sell_min_price、buy_max_price、buy_num） | 仅第 1 页免登录 | 翻页/筛选需 session | 匿名 15 次/3s 未限 | 2026-08-23 实测 |
| /api/market/goods/sell_order | GET | 在售列表 | game, goods_id, page_num | 默认排序免登录 | 社区 ≥4s | 实测、Steamauto |
| /api/market/goods/buy_order | GET | 求购队列（价格降序，含 price/num/specific/user_id） | game, goods_id, page_size | 免登录 | 同上 | 实测、ruff、csgo-price-helper |
| /api/market/goods/price_history/buff/v2 | GET | BUFF 价格历史 | days, tier, tag_ids | **Login Required** | — | 实测、main.js |
| /api/market/goods/bill_order | GET | 成交记录 | goods_id | Login Required | — | 实测 |
| /api/market/leaderboard/price_trend/* | GET | 行情榜 | 未知 | Login Required | — | main.js（参数待抓包） |
| /account/api/qr_code_create → qr_code_poll → qr_code_login | — | 扫码登录取 session | code_type:1 | — | 1s 轮询 | Steamauto buff_helper |
| /api/market/items（官方开发者 API） | GET | 全站在售底价 | game, goods_id 可选 | Bearer token | 500 次/24h | /developer/documentation |
| /api/market/enterprise/items | GET | 在售底价+buy_max_price | — | 企业 token | 5000 次/24h | 同上 |
| /api/market/developer/buy_order | GET/POST/DELETE | 我的求购单（返回同类 buy_max_price、buy_order_num） | order_state, page_size≤500 | 开发者 token | 60 次/分 | 同上 |

**鉴权**：cookie `session` + `csrf_token`（POST 加 `X-CSRFToken`）；登录方式扫码/Steam OpenID/短信。cookie 有效期说法不一（24 小时 vs 勾选 remember_me 10 天），待实测。**风控码**：Login Required、Action Forbidden、Rate Limit Exceeded、Captcha Validate Required（网易易盾）、Steam Logged In Needs Verification（2025-11 起手机二次验证）、System Error 系统繁忙。buff2steam、oddish 均有封号/账号冷却记录；bobipoppy/buff-monitor 默认 20 次/分。**官方 API 申请资格与费用待验证**（控制台需登录；cs2.sh 称 $150/月仅限中国账号，可信度低）。

## 3.3 Steam 社区市场

| 端点 | 方法 | 用途 | 参数 | 鉴权 | 限速 | 来源 |
|---|---|---|---|---|---|---|
| /market/priceoverview/ | GET | 最低价/中位价/24h 量 | appid=730, currency=23, market_hash_name | 无 | 约 20 次/分、1000 次/日/IP | InternalSteamWebAPI |
| /market/itemordershistogram | GET | 买卖盘深度、highest_buy_order | item_nameid, currency=23, norender=1 | 无 | 社区 15-20 次/分/IP，429 软封 | 实测 200、多项目 |
| /market/pricehistory/ | GET | 逐小时/日中位价与量 | appid, market_hash_name | **需 sessionid + steamLoginSecure** | ≥3s | hridaya、vladpuz |
| /market/search/render/ | GET | 分页搜索 | count≤100, start | 无 | 10-15 次/分 | 社区 |
| /market/listings/730/{name} | GET | 页面解析 item_nameid / market_bucket_id | — | 无 | 约 5MB/页 | STST、winstongarth |
| /market/orderbook?q=Load&qp=[appid,bucket_id] | GET | 2026 年 SPA 新订单簿 RPC | x-valve-request-type 头 | 无 | 约 2 req/s 第 21 次触发限流 | winstongarth（2026-08） |

currency=23 为 CNY，国区价格可与 BUFF/悠悠直接比较。2022-10 起按 IP 限流，数据中心 IP 常首请求即 429；国内家庭宽带访问 steamcommunity.com 通常需代理（推测）。交易约束：市场购入 7 天冷却（2026-06 起按购买时刻精确解锁）、2025-07-16 Trade Protection 7 天可撤销、卖出手续费 5%+10%（买家价 = 到手价 × 1.15；口径需统一，有项目按 13.04% 或 0.85 计算）。

## 3.4 第三方数据源

| 来源 | 覆盖 | 字段 | 限流 | 费用 | 评价 |
|---|---|---|---|---|---|
| SteamDT 开放平台 open.steamdt.com | BUFF/YOUPIN/C5/STEAM/HALOSKINS 等 11 平台 | sellPrice/sellCount/biddingPrice/biddingCount/updateTime；item kline（无成交量）；broad index/kline | price/single 60/分、batch 100 件 1/分、kline 120/分、base 1/天；avg 与 broad 未列 | "目前限时免费"，无截止日期 | 比价主源；YOUPIN 求购价由多个项目实际解析到（真实延迟待用 key 验证） |
| CSQAQ api.csqaq.com | BUFF/悠悠/Steam/C5/IGXE/ECO/R8 | info/good（各平台在售/求购价与量、yyyp_id/buff_id）、info/chart（platform=2 key=buy_price 悠悠求购日线，period≤1095）、get_rank_list、exchange_detail、current_data（免 token，23 子指数+情绪+涨跌家数） | 1 次/秒/IP，不限次；token 绑单 IP，bind_local_ip 30s/次自动重绑 | 免费，"严禁商用" | 大盘与排行主源；指数公式：2021-01-01 基准 1000 点，11,269 件在售>50 饰品等权 |
| BUFF 官方开发者/企业 API | BUFF | 见 3.2 | 500/5000 次/天 | 未知 | 若可申请，替代 cookie 爬取 |
| cs2.sh | BUFF/悠悠/C5/CSFloat/Steam | ask/bid/volume 1h/1d 自 2023 起；OHLC 5m 自 2025-12-24 | 10 req/s | Developer $75/月，Scale $200/月（含历史） | 唯一小时级悠悠求购价历史来源，USD 计价 |
| Pricempire | 80+ 市场含 youpin_buy | 历史单次 ≤180 天 | 10 万次/月 | Enterprise $239.9/月 | 性价比低 |
| steamwebapi.com | 悠悠仅在售价 | 约 3 小时刷新 | 免费 2 req/min | €25/月起 | 不推荐 |
| iflow.work 存档 | BUFF/IGXE/C5/悠悠在售梯队 + Steam 买卖盘 | 12h 快照 2022-04→2026-05 | — | 免费 | 回填基线 |
| Skinport/CSFloat 官方 API | 海外自家 | — | 8 次/5 分 | 免费 | 与国内三平台无关 |

# 四、"求购排名 ≤ 2 提醒"的实现方案

**排名定义（待与发起人确认）**。候选三种：A）全模板队列按 `purchasePrice` 降序的位置；B）按本单所属磨损区间/款式（typeId、minAbrade/maxAbrade、specialStyle，来自 getTemplateFilterConfigV2）过滤后的位置；C）仅比较价格：我的价 ≥ 队列最高价为第 1，仅低于一个不同价位为第 2。悠悠求购撮合（价格优先/时间优先、供应时匹配哪张单）规则未在帮助中心找到。**默认建议 B**：因为供应者在特定磨损的商品页看到的就是该区间的队列；同价时按返回顺序（假设时间优先）。`surplusQuantity=0` 的单是否仍占位待验证。

**数据流**。每轮：① `searchPurchaseOrderList(status=20)` 拉全部在途求购单（得到 templateId、purchasePrice、purchaseNo、磨损条件）；② 对每个 (templateId, 区间) 调 `getTemplatePurchaseOrderListPC`（或 App 版）取前 30 条；③ 找到 `userId == 本人 UserId` 的条目，计算 rank；若不在前 30 条直接记为 >30。④ 与本地状态比较，进入状态机。附加近似信号：SteamDT/CSQAQ 的 YOUPIN biddingPrice——我的价 < biddingPrice 则必不是第一，可作为低成本预筛，减少对悠悠的直接请求。

**轮询频率与账号风险**。账号风险来源：短信登录/token 本身（官方条款）、请求频率（84104/405/429）、IP 类型（5050）。建议：单账号请求间隔 ≥ 2 秒、每分钟 ≤ 30 次、同一 templateId 不高于每 3 分钟一次；N 个求购单时一轮耗时 ≈ N × 2.5s，10 单约 25 秒，配合 3-5 分钟周期即可；随机抖动 ±30%；每 30 分钟 getUserInfoForApp 心跳；uk 每 30 秒刷新；夜间（00:00-08:00）降为 15 分钟。退避：84104 → 暂停 30 分钟后 5/10/20/30 分钟指数；405 → 刷新 uk 等待 60 秒；429 → 5 分钟 × 2^n 上限 30 分钟；84101 → 停止并推送"需重新登录"。运行在家庭宽带，不在云主机。App-Version、UA、DeviceId 做成配置，定期跟随应用宝版本。

**状态机与去抖**（照搬 Grafana/Alertmanager 原语）。告警实体指纹 = hash(templateId, 区间, purchaseNo)。状态 Normal → Pending → Alerting(TOP2) → Recovering → Normal。迟滞：触发 rank ≤ 2，恢复 rank ≥ 4（3 为死区）；Pending 需连续 2 次轮询为真；Recovering 保持 10 分钟内若回到 ≤2 则静默复位。事件类型：`ENTER_TOP2`（Normal→Alerting）、`RANK_CHANGE`（1↔2，group_interval ≥5 分钟节流）、`OUTBID`（Alerting→Normal，即"被压价跌出前二"，作为 resolved 事件实现）、`REPEAT`（rank 持续 1 时每 4 小时一次，可关）。同一轮多单变化在 60 秒 group_wait 内合并为一条。全局令牌桶每小时 ≤ 30 条通知，超出合并为摘要；接口错误持续 >15 分钟只发一条运维提醒。

**ntfy 消息设计**。Topic 用 32 位随机串（topic 即密码）或自建 + `tk_` 令牌。字段：Title "求购第1名：AK-47 | 红线 (久经沙场) 0.15-0.38"；正文 Markdown 列出我的价、第 2/第 3 名价与数量、当前在售最低价、价差；Priority：rank=1 → 4 + tags `trophy`，rank=2 → 3，OUTBID → 4 + `warning`，日报 → 2；Click → 悠悠商品页 `youpin898.com/market/goods-list?templateId=`；Actions（≤3）：view 打开页面、http 回调"暂停此单 24h"（回调端点需独立鉴权，内网需穿透）；正文 ≤ 4096 字节。夜间用 `At` 头延迟到 08:00 投递。

**失败处理**。发送前写 notification_log(fingerprint, state, rank, sent_at)，ntfy 失败指数退避重试 3 次，重启后用 `/<topic>/json?poll=1&since=<lastId>` 校验幂等；双通道降级（企微/飞书群机器人，20/100 条每分钟）；token 失效/连续风控 → 通过备用通道发"人工介入"。

# 五、价格监控与大盘/交易建议方案

**指标定义**（按平台 p ∈ {BUFF, UU, Steam}，饰品 i，时间 t）：
- 在售底价 sell_min、在售量 sell_count、最高求购 buy_max、求购量 buy_count（SteamDT/CSQAQ 字段一一对应）。
- 买卖价差率 spread = (sell_min − buy_max)/sell_min；跨平台离散度 = (max sell_min − min sell_min)/median（借鉴 steamdb.com 口径）。
- 挂刀比例 = 国内平台 sell_min / (Steam sell_min / 1.15)；反向比例 = Steam buy_max × 0.87 / 国内 buy_max；统一手续费口径 Steam 15%、BUFF 2.5%、悠悠 1.5%（后两者待核对）。
- 动量：7 日均线偏离 (P − MA7)/MA7、7/30 日对数收益、成交量相对 7 日均量（Pettersson 2025 三个最强特征）。
- 自建大盘指数：以流动性池（成交量大、价差小，参考 iflow priority 选法 1000-3000 件）日收益率等权平均，基期 1000；同时拉 CSQAQ 23 个子指数与 SteamDT broad index 作对照。
- 涨跌排行：1/7/30 日涨跌幅、求购量增幅、Steam 日成交量（CSQAQ get_rank_list 已提供，可直接消费）。

**数据模型**。表 `item`（item_key=规范化 market_hash_name，buff_goods_id、youpin_template_id、steam_item_nameid，来源 cs2-marketplace-ids + ID-Mapper + SteamDT base）；表 `quote`（ts, platform, item_key, sell_min, sell_count, buy_max, buy_count, extra JSON 梯队）；表 `my_buy_order`（purchaseNo, templateId, 区间键, price, qty, rank, state）；表 `index_daily`；表 `event`（Valve 更新/公告、箱子变动）。悠悠求购序列的键必须含磨损区间/款式，否则与排名定义不对齐。存储 MVP 用 SQLite WAL + 每日导出 Parquet（DuckDB 回测），Tier B ≥ 1500 件或多进程写入时迁 TimescaleDB。数据量：Tier A（≤50 件，2-5 分钟）+ Tier B（1000-3000 件，60 分钟）+ Tier C（全量每日）≈ 原始 60-120 MB/天，压缩后 15-30 MB/天。

**可借鉴的量化方法**（awesome-systematic-trading 中只取一小部分）：时间序列动量/均值回归（MA 偏离）、成交量确认、walk-forward 滚动验证 + conformal 预测区间（cs2-index-predictor 已示范）、事件研究（Nian 2026：以大盘指数做市场模型算超额收益）、简单风控（单品集中度、7 天持仓锁定的资金占用）。回测框架选 vectorbt 或 backtesting.py（backtrader 2024 起停更）。

**不适用的部分**：高频/微观结构策略（数据粒度分钟级且有风控）、做空与杠杆（不存在）、复杂深度学习（LSTM R² 0.176 vs 随机森林 0.494）、周内效应/Steam 打折/Major 对日收益的预测（实证接近零）、任何忽略 7 天交易锁与可撤销交易的周转模型。

**LLM 辅助的位置**：只做"解释层"——把结构化指标、排行、事件日志生成每日中文简报与"为什么涨/跌"的归因叙述；把 Valve 更新日志/公告文本分类为事件标签（供给冲击/规则变更/赛事）；回答"这件饰品现在挂刀划算吗"之类问答。LLM 不直接产出交易信号，所有数字以结构化数据为唯一事实源并做字段级校验（CSTrader 论文显示流动性与摩擦模块才是稳定收益关键）。

**分期**：MVP（3-4 周）：悠悠登录态 + 求购排名状态机 + ntfy + SQLite + SteamDT/CSQAQ 每小时采样 + 简单 Web 看板（单品三平台价、求购排名）。二期（1-2 月）：iflow/CSQAQ/atalantus 历史回填、自建指数与涨跌榜、挂刀/反向套利扫描、事件源、Parquet 回测。三期：因子回测与信号、LLM 日报、BUFF 官方 API（若申请成功）、多账号。

# 六、技术方案对比

| 维度 | 方案 A：Python 单体（推荐） | 方案 B：TypeScript/Node 单体 | 方案 C：复用 Steamauto 插件框架 |
|---|---|---|---|
| 悠悠适配 | 按 Steamauto/CS2TradeMonitor 头部重写，PyCryptodome 做 deviceW2；可选 curl_cffi 改 TLS 指纹 | 需自写 AES/RSA 与头部；无现成参考代码 | 直接用 uuyoupinapi（AGPL） |
| 数据源客户端 | requests/httpx；aiosteampy（Steam）；已有 cs-monitor/cs2-price-vault 的 SteamDT/CSQAQ Python 客户端可参考 | steamdt-mcp/cs2-market-qq-bot 的 TS 客户端可参考 | 无行情能力，需自加 |
| 调度/限流 | APScheduler + 自写令牌桶（参考 hridaya 滑动窗口） | node-cron + bottleneck | schedule + sleep（无限流器） |
| 存储/分析 | SQLite→Parquet/DuckDB，pandas/vectorbt 回测天然契合 | Prisma/SQLite，回测生态弱 | 无存储 |
| 推送 | python-ntfy/aiontfy 或直接 POST；Apprise 做多通道 | fetch POST | Apprise 原生 ntfy |
| 许可 | 自有代码，MIT/私有均可 | 同左 | 整体被 AGPL 传染 |
| 维护风险 | 中：自行跟随悠悠版本 | 中 | 高：上游对非发货需求不回应，含 cloud_service 自动更新/广告 |
| 开发量 | 中 | 中-高 | 低起步、高后期 |

**推荐方案 A**。理由：求购接口、限流退避、回测分析三块的可参考代码和库几乎都在 Python 生态；单体部署在家庭宽带 NAS/小主机即可；避免 AGPL/GPL 传染；ntfy 只需一个 HTTP POST。悠悠适配层独立成包，版本号/UA/指纹全部配置化，失效时只改一处。

# 七、风险清单与缓解

| 风险 | 等级 | 说明 | 缓解 |
|---|---|---|---|
| 悠悠条款/封号 | 高 | 官方文档称私自调用接口干扰运营永久封禁；token 等同账号控制权 | 只用只读端点；低频；家庭 IP；备用小号先跑；不做自动改价/下单；记录所有请求日志 |
| App 版本门禁 | 高 | 85100/5050 周期性打断（2024-05、2025-06、2026-05 均发生） | App-Version 配置化并监控应用宝版本；异常码自动告警 |
| 风控限速 | 中-高 | 84104/405/429 阈值未知 | 令牌桶 ≤30/分、指数退避、心跳、uk 刷新；先用 SteamDT biddingPrice 预筛 |
| 接口加密扩散 | 中 | PC 端 sk 加密通道已覆盖部分接口 | 优先用 App 端接口；预研前端 AES/RSA 复刻；定期 diff umi.js |
| 求购字段假设错误 | 高 | 排序/分队列/isMine 全部推测 | 立项首周用真实账号抓包验证；按第八节问题定排名定义 |
| BUFF 封号 | 中 | 登录态爬取有先例 | 匿名仅取第 1 页；历史/榜单走 CSQAQ；申请官方开发者 API |
| Steam 429/网络 | 中 | 国内需代理；接口迁移 orderbook | Steam 价格走 SteamDT/CSQAQ，直连 ≤12/分仅校验 |
| 第三方源变更 | 中 | SteamDT 限时免费无期限；CSQAQ 禁商用、token 绑单 IP | 双源互备；bind_local_ip 自动重绑；本地缓存 |
| 数据准确性 | 中 | 聚合源 5-15 分钟延迟；USD 折算；字段名各异 | 关键决策前用悠悠直连复核；统一手续费口径；记录 updateTime |
| 历史数据缺口 | 中 | 2023-08 前无悠悠求购价；小时级仅付费 | 立即开始自采；回填 iflow/CSQAQ/atalantus |
| Valve 政策冲击 | 高 | 2025-10-23 指数两日 -68.5% | 事件源 + 更新日暂停建议 + 集中度上限 |
| 推送可达性 | 低-中 | Android 无 FCM；iOS upstream 占 ntfy.sh 配额 | F-Droid APK 即时投递；iOS 用 Bark 或 Supporter 档；企微/飞书兜底 |
| 许可合规 | 低 | AGPL/GPL/无 License 数据 | 只参考端点事实不抄代码；cs2-marketplace-ids 联系作者或本地快照 |

# 八、需要与发起人确认的问题

**A. 账号与登录方式**
1. 悠悠登录走 App 短信登录（一次性验证码，token 缓存）还是手动从 App 抓包贴 token？默认：短信登录 + 贴 token 兜底。
2. 是否接受非官方接口的条款风险？是否有可用于试跑的小号？默认：先用小号验证 2 周。
3. 是否愿意申请悠悠官方开放平台（需手持身份证、18-40 岁）与 BUFF 开发者 API（需登录查看条件）？默认：先不申请，二期再评估。
4. BUFF 是否提供登录 cookie？默认：不提供，匿名 + CSQAQ。
5. Steam 是否提供 cookie 以回填 pricehistory？默认：不提供。

**B. 求购监控细节**
6. 排名定义选 A/B/C（见第四节）？默认 B（按磨损/款式区间）。
7. 同价单如何处理？默认按返回顺序。
8. 是否需要"被压价跌出前二"提醒？默认需要，作为 resolved 事件。
9. 持续第 1 时是否重复提醒？默认每 4 小时一次，可关。
10. 预计求购单数量（≤10 / 10-50 / >50）？影响轮询周期。默认按 ≤20 设计，周期 3-5 分钟。
11. 是否需要手机端"暂停此单"按钮（需暴露回调端点）？默认一期不做。

**C. 监控范围与频率**
12. 关注饰品池规模：Tier A ≤50、Tier B 1000 或 3000？默认 50 + 1000。
13. Tier B 采样 30 还是 60 分钟？默认 60。
14. 是否接受 Steam 价格仅来自聚合源？默认接受。

**D. 部署环境**
15. 运行位置：家庭 NAS/小主机（推荐）、国内云、境外 VPS？默认家庭宽带（机房 IP 登录会被拦）。
16. 手机系统 Android/iOS？决定 ntfy F-Droid 即时投递 vs Bark/upstream。
17. ntfy 自建还是用 ntfy.sh？默认先用 ntfy.sh 随机 topic，稳定后自建。
18. 是否需要 Web 看板，局域网访问是否够？默认局域网 + Tailscale。

**E. 分析功能期望**
19. 一期是否只要比价 + 走势，大盘/排行直接嵌 CSQAQ 数据？默认是。
20. 是否需要自建指数与回测？默认二期。
21. LLM 日报是否需要、用哪家模型、预算？默认三期，模型待定。
22. 交易建议的风险偏好与持仓周期（考虑 7 天锁）？默认以"挂刀/反向比例 + 动量 + 流动性评级"给信息，不给自动下单。

**F. 预算与付费数据源**
23. 是否愿意为 cs2.sh Scale 付 $200 一次性拉取 2023 起小时级悠悠求购价历史？默认否，用 CSQAQ 日线。
24. 是否接受 SteamDT 将来收费？默认接受低价档，否则切 CSQAQ。
25. ntfy Supporter $6/月（iOS upstream 配额）是否可接受？默认按需。
26. 是否授权下载 iflow 存档（全量 7-8 GB 或 2024-02 后约 3 GB）？默认先下一份验证字段。

# 九、参考资料

**悠悠有品相关**
- https://github.com/Steamauto/Steamauto 、https://raw.githubusercontent.com/Steamauto/Steamauto/master/uuyoupinapi/__init__.py 、.../uuyoupinapi/UUApiCrypt.py 、.../utils/uu_helper.py
- https://github.com/Steamauto/Steamauto/issues/224 、/issues/268 、/issues/271 、/issues/273 、/issues/246 、/issues/164 、/pulls/262
- https://github.com/cs2juece/CS2TradeMonitor （YouPinUrls.cs、YouPinMobileApiClient.cs、YouPinAuthService.cs）
- https://github.com/youki258/uuyp-trade-analyzer （backend/exporter/client.py、docs/ip_risk_control_research.md）
- https://github.com/i4cobus/cs2-cross-market-arbitrage-analyzer/blob/main/app/uu_client.py
- https://github.com/Shinian10/CS/blob/main/main.py
- https://github.com/bernabeu97/youpin898-record-exporter
- https://www.youpin898.com/umi.3b01c53d.js 、https://www.youpin898.com/p__GoodsList__index.a19e0b4f.async.js
- https://www.yuque.com/yyyoupin/azx6iu （官方开放平台文档）
- https://github.com/ShevonKuan/csgo_investment/issues/5 、https://github.com/worldofgoo9/AutoUU 、https://github.com/jiajiaxd/uuyoupinapi

**BUFF 相关**
- https://buff.163.com/developer/documentation 、https://buff.163.com/api/market/goods?game=csgo&page_num=1 、.../goods/sell_order 、.../goods/buy_order
- https://raw.githubusercontent.com/Steamauto/Steamauto/master/BuffApi/__init__.py 、.../utils/buff_helper.py
- https://github.com/hldh214/buff2steam 、https://github.com/puppylpg/oddish 、https://github.com/bobipoppy/buff-monitor 、https://github.com/Clinch-7/buff-market-watch
- https://github.com/Steamauto/Steamauto/issues/259 、/issues/219 、/issues/140
- https://github.com/atalantus/buff-price-history-archive

**Steam 相关**
- https://github.com/Revadike/InternalSteamWebAPI/wiki 、https://github.com/somespecialone/aiosteampy 、https://github.com/somespecialone/steam-market-ids
- https://github.com/AjaXsb/hridaya_steam_market_tracker 、https://github.com/Soniclev/steam_csmoney 、https://github.com/vladpuz/steam-market
- https://github.com/winstongarth/steam-market-inteligence-pipeline/blob/HEAD/docs/DECISIONS.md 、https://github.com/nidea1/task-bar-trade-center/blob/HEAD/CHANGELOG.md
- https://steamcommunity.com/groups/community_market/discussions/0/3781373213476985845/ 、https://www.steamwebapi.com/blog/steam-market-listing-too-many-requests-429
- https://swap.gg/blog/cs2-7-day-trade-lock-explained 、https://allthings.how/cs2-steam-market-trade-cooldown-change-explained/ 、https://tested.gg/blog/cs2-trade-holds-explained

**第三方数据源**
- https://doc.steamdt.com/llms.txt 、https://doc.steamdt.com/6369437m0 、/278832831e0 、/428124801e0 、/450452402e0 、/450452403e0 、/319748133e0
- https://docs.csqaq.com/llms.txt 、https://docs.csqaq.com/doc-4588854 、/doc-4589773 、/api-187131780 、/api-187131781 、/api-187131776 、/api-187131823 、/api-187131779 、/api-342090738
- https://github.com/zkcaryq/steamdt-mcp 、https://github.com/zkcaryq/cs2-market-qq-bot 、https://github.com/CHET-LI/csqaq-mcp-server 、https://github.com/Pgooone/cs-monitor 、https://github.com/BanditleaderTF/CS_MONITOR 、https://github.com/Daydayup4010/monitor
- https://cs2.sh/openapi.yaml 、https://cs2.sh/pricing 、https://pricempire.com/docs 、https://www.steamwebapi.com/pricing 、https://docs.skinport.com/items 、https://docs.csfloat.com/
- https://api.iflow.work/export/list?dir_name=priority_archive 、https://null42.notion.site/iflow-server-notice 、https://github.com/EricZhu-42/SteamTradingSiteTracker 、https://github.com/EricZhu-42/SteamTradingSiteTracker-Data 、https://github.com/EricZhu-42/SteamTradingSiteTracker/issues/99
- https://github.com/ModestSerhat/cs2-marketplace-ids 、https://github.com/EricZhu-42/SteamTradingSite-ID-Mapper 、https://github.com/ByMykel/CSGO-API

**推送**
- https://docs.ntfy.sh/publish/ 、https://docs.ntfy.sh/config/ 、https://docs.ntfy.sh/subscribe/phone/ 、https://ntfy.sh/v1/account 、https://github.com/binwiederhier/ntfy/issues/1373 、/issues/1491
- https://github.com/MatthewCane/python-ntfy 、https://github.com/tr4nt0r/aiontfy 、https://github.com/caronc/apprise
- https://bark.day.app/#/tutorial 、https://github.com/Finb/bark-server 、https://sct.ftqq.com/docs/ 、https://www.pushplus.plus/doc/help/limit.html 、https://developer.work.weixin.qq.com/document/path/91770 、https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot
- https://grafana.com/docs/grafana/latest/alerting/fundamentals/alert-rule-evaluation/ 、https://prometheus.io/docs/alerting/latest/configuration/

**市场分析与量化**
- https://doria.fi/handle/10024/193781 （Pettersson 2025）、https://fieam.org/index.php/ojs/article/view/174 （Nian 2026）、https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2025.1702924/full 、https://arxiv.org/abs/2606.31461 （CSTrader）
- https://steamdb.com/en/skins/cs2/statistics 、https://www.steamanalyst.com/liquidity 、https://csmarketcap.com/
- https://news.qq.com/rain/a/20251024A04X2M00 、https://www.21jingji.com/article/20251029/herald/e4c9e56b45bcc33b6839dd064d98768c.html 、https://www.hotspawn.com/counter-strike/news/cs2-trade-protection-update-death-of-cash-trading
- https://github.com/xfy7711/cs2-index-predictor 、https://github.com/AKA-hhh/cs2-price-vault 、https://github.com/newbeehao/csgo_skinquant 、https://github.com/Croutl/cs2-valueyes 、https://github.com/paperswithbacktest/awesome-systematic-trading
- https://github.com/redlfox/awesome-cs2-trading