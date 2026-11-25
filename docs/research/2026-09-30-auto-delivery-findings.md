# 自动发货调研：悠悠有品、BUFF、Steam（2026-09-30）

为 自动发货 intent（未公开） 做的调研。方法：只读公开网页和开源仓库源码（没有 clone、没有运行、没有登录任何账号）。标「未核实」的是推断或只见于二手来源。

## 1. 现成方案

| 项目 | 状态 | 说明 |
|---|---|---|
| [Steamauto/Steamauto](https://github.com/Steamauto/Steamauto) | 约 1.3k 星，2026-09-23 仍在发版，**AGPL-3.0**，Python | 悠悠、BUFF、C5、ECOsteam 自动发货，事实上的标准做法。原 `jiajiaxd/Steamauto` 已 404 |
| lupohan44/Buff-Bot、Steam-Sentinel-Project/buff-bot | 2021–2023 年停更 | 只做 BUFF |
| 官方功能 | 没找到 | BUFF 帮助中心只有绑定 Steam 令牌；悠悠没找到官方自动发货或托管（未核实） |

AGPL：代码不能抄进 SkinPulse（抄了就得整体 AGPL），只借鉴流程，自己实现。

## 2. Steamauto 的悠悠发货流程（源码 f044540，2026-09-23）

- **登录**：短信登录（`SendSignInSmsCode` → `SmsSignIn`，失败退回「用户上行短信」`SmsUpSignIn`），token 存文件。我们已经有 token，不需要这一步。
- **请求头**：冒充安卓 App 5.28.3，头名和我们基本一致（`authorization`、`App-Version`、`DeviceToken`、`DeviceId`、`Device-Info` 等）。**发货相关请求不带 `uk`**。
- **每轮（默认 300 秒一轮）**：
  1. `POST /api/youpin/bff/trade/todo/v1/orderTodo/list`（待办，分页 20）→ `orderNo`、`message`。按 `message` 文案分流：含「赠送」跳过；「有买家下单，待您发送报价」是**卖家发报价**的单；其他是买家已发报价、待卖家接受的单。
  2. 取 Steam 报价号，三种办法依次试：`POST …/trade/sale/v1/sell/list`（`orderStatus:"140"`，取 `offerType==2` 且有 `tradeOfferId` 的）→ `POST /api/youpin/bff/order/v2/detail`（`data.orderDetail.offerId`）→ `POST …/trade/v1/order/query/detail`（`data.tradeOfferId`；响应含「系统验证中」就跳过）。每单之间睡 3 秒。
  3. 卖家发报价的单：`PUT …/trade/v1/order/sell/delivery/send-offer`（**写接口**），再轮询 `…/delivery/get-offer-status` 到 `status==3`。**报价由悠悠服务器替用户发出**（它怎么拿到用户的 Steam 会话，源码里看不到，未核实），下一轮在 Steam 上只剩手机确认。
  4. 对每个报价号：Steam 接受 + 手机确认，每单之间睡 5 秒。
- **匹配**：只核对报价号。不核对物品、assetid、对方账号。
- **错误**：`84101` 登录失效就停；HTTP 405 当作 `uk` 失效，睡 60 秒；`85100 app 版本过低` 没处理（issue #273，2026-05）。

## 3. Steamauto 的 BUFF 发货流程

- **登录**：先用缓存的 `session` cookie；失效就走 **Steam OpenID**（用已登录的 Steam 会话自动完成）；再不行扫码（二维码经第三方渲染服务 `api.cl2wm.cn`，隐私上不可取）。
- **启动时**：`GET /account/api/user/info`、`/account/api/steam/info`，确认登录的 Steam 账号绑定在这个 BUFF 账号上。如果「强制买家发起报价」没开，代码会**替用户打开**（`POST /account/api/prefer/force_buyer_send_offer`，写设置）。
- **每轮**：`GET /api/message/notification` 看待发货数 → `GET /api/market/steam_trade`（只要 type 3/6/8，发货类）→ `GET /api/market/sell_order/to_deliver?game=csgo`（`tradeofferid`、`seller_steamid`、`state`、`is_seller_asked_to_send_offer`）→ 逐个接受。写请求要 CSRF：先 GET 一次刷 `csrf_token` cookie，再带 `X-CSRFToken`、`Referer`。
- **卖家发报价的单**：`POST /api/market/manual_plus/seller_send_offer`，参数里要把**用户的 steamcommunity.com cookie（含 steamLoginSecure）**加密后交给 BUFF，由 BUFF 替用户发报价。等于把 Steam 登录态交出去。
- **匹配**：报价号 + 类型 + `seller_steamid`，不核对物品。

## 4. Steam 这一侧

- **登录**：`IAuthenticationService`（取 RSA 公钥 → 加密密码 → `BeginAuthSessionViaCredentials` → 用 `shared_secret` 算的动态码 `UpdateAuthSessionWithSteamGuardCode` → `PollAuthSessionStatus` 拿 refresh token → `finalizelogin` 设 cookie）。之后用 refresh token（约 200 天）换 access token，不再用密码。
- **查报价**：CS2 的 `IEconService/GetTradeOffer(s)` 自 2024-05 起要 **access token**，Web API key 不行了。**不需要注册 API key**，也就避开了「API key 被盗 → 换包诈骗」。
- **接受**：`POST steamcommunity.com/tradeoffer/{id}/accept`（`sessionid`、`serverid=1`、`partner`）。
- **手机确认**：`GET /mobileconf/getlist`（参数带用 `identity_secret` 算的 HMAC 签名）→ 找到对应报价的那条 → `GET /mobileconf/ajaxop?op=allow`。2023 年中接口从 `/mobileconf/conf` 换成现在这套。
- **频率**：Valve 不公布。社区经验：确认列表 5 秒一查会 429，10 秒以上没事；429 封 IP 可能几小时。
- **Python 库**：

  | 库 | 最近发布 | 许可 | 评价 |
  |---|---|---|---|
  | aiosteampy | 1.0.0b16，2026-09-10 | MIT | 唯一在维护、覆盖新登录 + access token + 接受 + 确认的；**beta、异步** |
  | bukson/steampy | 1.2.0，2024-12 | MIT | 维护者不活跃，登录、接受都有未修的问题 |
  | steamio（steam.py） | 1.1.3，2025-08 | MIT | 重量级机器人框架 |
  | ValvePython/steam | 2022-12 | MIT | 早于新登录，过时 |

## 5. 令牌密钥怎么来（最关键的前提）

- 自动确认要 `identity_secret`，自动登录要 `shared_secret`。**官方 Steam App 不能导出**。
- **一个账号只能有一个令牌**。把令牌「移动」到电脑（steamguard-cli / SDA / Watt Toolkit）后，**手机上的官方 App 就不能再确认交易了**。手机上想继续看动态码，可以用能读 maFile 的第三方 App，或者把动态码导进密码管理器（只有动态码，不能确认）。
- 从官方 App 提取：安卓要 root、厂商备份（vivo、华为现在加密了）或改包；iOS 基本走不通（未核实）。
- **交易冷却**：新加令牌后 7 天内发起的交易会被暂挂最多 15 天；走正规「移动令牌」流程（短信验证）据说更短（社区说法，未核实）。没有手机令牌的账号交易一律暂挂 15 天。
- steamguard-cli（Rust，约 1k 星，2026-09 仍在更新）README 自称「实际上还是 beta」，没提 macOS（能不能在 Mac 上跑未核实）。

## 6. 交易保护（2025-07-16 起）

- 只针对 CS2 物品：物品立即到账，但 7 天内不能用、不能改、不能再交易；**收发双方都能撤回**，撤回会一次撤掉最近 7 天所有受保护的交易，撤回方被限制交易和市场 30 天。
- BUFF（[公告](https://buff.163.com/news/76284)）：7 天保护期结束才给卖家结算；恶意撤回的拉黑、冻结余额、追究法律责任。
- 悠悠：没找到官方页面。二手来源说也是 7 天后结算，恶意撤回「退货不退款」或永久封禁（未核实）。
- 对我们：发货程序**绝不调用任何撤回接口**；货款 7 天后才到。

## 7. 条款与风控

- **Steam 用户协议**（2026-09-10 版）写明不得用脚本、机器人等自动化「以任何方式」与 Steam 内容和服务交互。字面上包括接受报价的机器人；实际执法主要针对刷号、养号机器人，个人发货机器人被封的报道没找到。风险真实但偏低、不确定。
- **悠悠用户协议**：禁止用脚本恶意破坏平台、禁止绕过反爬，处罚包括冻结账号、扣余额。没有点名发货工具。
- **容易突然失效**：悠悠 App 版本门槛（5050、85100）、`uk` 算法变化、BUFF 的「需要身份验证」风控、Steam 按 IP 风控，Steamauto 的 issue 里一年要改好几次。
- **Steamauto 的 issue 里没见到**「因为用了它被封号、冻结」的报告（抽样 60 条）。

## 8. 对 SkinPulse 的含义

1. 悠悠「买家发报价」的单：悠悠这边只用**查询**接口（待办、订单详情），写操作全在 Steam 上。「对悠悠只读」可以原样保留。
2. 悠悠「卖家发报价」的单：要调 `send-offer`（写接口）。要改「只读」原则，得写 ADR。
3. BUFF「卖家发报价」的单：要把 Steam 登录 cookie 交给 BUFF。不建议做，改成在 BUFF 设置里手动打开「强制买家发起报价」。
4. 两个平台都**不核对物品**。我们自己要核对：对方账号对得上、我们一件都不收、给出去的正好是这张单的物品，否则不确认。
5. 工作量大头在 Steam 层（登录、会话续期、接受、确认）和新的 BUFF 适配器。悠悠这边只是加几个查询接口。
