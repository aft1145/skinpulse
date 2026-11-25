> **⚠️ 部分内容已被真实对接推翻（2026-09-06）。**
> 接口层的当前事实见 [2026-09-06-live-api-findings.md](2026-09-06-live-api-findings.md)；
> 系统现状与待办见 ../STATUS.md（未公开）。

# SkinPulse 前期调研 · 第一手接口验证记录

日期：2026-08-23  
方式：在本机浏览器打开 youpin898.com 抓取 XHR、下载前端 JS 包分析、用 curl 脱离浏览器重放；BUFF / Steam 用 curl 直接探测。以下所有结论均为**当天实测**，接口随时可能变化。

---

## 1. 悠悠有品（youpin898.com）网页端内部接口

### 1.1 基本信息

| 项目 | 结论 |
|---|---|
| API 域名 | `https://pc-api.youpin898.com` |
| 前端框架 | UmiJS SPA，主包 `umi.<hash>.js` ≈ 2.3 MB |
| 请求方式 | 全部 `POST` + JSON body |
| 必需自定义头 | `uk`、`deviceUk`、`deviceId`、`appType: 1`、`platform: pc`、`AppVersion: 5.26.0`、`App-Version: 5.26.0`、`secret-v: h5_v1`；登录后再加 `authorization` |
| 缺少 `uk`/`deviceUk` 时 | 返回 `{"code":85100,"msg":"当前app版本过低，请前往更新"}`（即使 AppVersion 正确） |
| 是否绑定浏览器/IP | **否**。把浏览器里的 `uk`/`deviceUk`/`deviceId` 复制到 curl 可直接调通 |

### 1.2 `uk` / `deviceUk` 的来源（`POST /api/deviceW2`）

从 `umi.js` 还原的流程：

1. 采集浏览器指纹 `needsParams = {...deviceParams, ...extraParams, ...canvasParams, devtoolData, userId}`（UA、屏幕、canvas 指纹等）。
2. 客户端生成随机 AES key；`encryptedData = AES-ECB/PKCS7(JSON.stringify(needsParams), aesKey)`。
3. `encryptedAesKey = RSA(aesKey, 内置公钥)`（jsencrypt，PKCS#1 v1.5）。线上环境公钥以 `MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAv9BDdhCDahZNFuJe...` 开头（完整值在 umi.js 内）。
4. `POST /api/deviceW2 {encryptedData, encryptedAesKey}`，响应用同一 AES key 解密得到 `{u, deviceUk}`。
5. 存入 `localStorage.WEB_UK` / `localStorage.WEB_DEVICE_UK`；`deviceId` 是本地生成的 UUID。

**结论**：可以在 Python/Node 中复现（pycryptodome/ node:crypto），也可以"偷懒"让用户从浏览器 localStorage 复制一次，长期复用。

### 1.3 已验证端点

| 端点（`pc-api.youpin898.com` 下） | 用途 | Body 示例 | 未登录可用？ | 备注 |
|---|---|---|---|---|
| `/api/homepage/pc/goods/market/querySaleTemplate` | 市场模板列表（每个饰品一行） | `{"listSortType":0,"sortType":0,"pageSize":20,"pageIndex":1}` | ✅ 仅前 2 页 | 返回 `id`(templateId)、`commodityName`、`commodityHashName`(= Steam market_hash_name)、`price`(在售最低价)、`steamPrice`、`steamUsdPrice`、`onSaleCount`、`onLeaseCount`、`rarity`、`exterior`。`pageSize` 传 100/500 也只返回 20；`pageIndex>=3` 返回 `84103 登录悠悠有品，解锁更多功能` |
| `/api/homepage/pc/goods/market/queryTemplateDetail` | 单个模板详情 | `{"gameId":"730","listType":"10","templateId":"34966"}` | ✅ | 含 `steamPrice`、`commodityHashName`、`jumpSteamLinkUrl`、可用筛选项（paintSeed/abrade/haveNameTag…） |
| `/api/homepage/pc/goods/market/queryOnSaleCommodityList` | 模板下在售列表 | `{"gameId":"730","listType":"10","templateId":"34966","listSortType":1,"sortType":0,"pageIndex":1,"pageSize":10}` | ✅ 仅 10 条 | 每条含 `price`、`userId`、`commodityNo`、`publishTime`、`haveSticker`、`actions`(检视链接) |
| `/api/youpin/bff/trade/purchase/order/getTemplatePurchaseOrderListPC` | **模板下求购列表** | `{"templateId":"34966","pageIndex":1,"pageSize":10}` | ✅ 仅前 20 条 | 见 1.4 |
| `/api/youpin/bff/trade/purchase/order/getTemplateFilterConfigV2` | 求购筛选配置 | `{"templateId":"34966"}` | ❌ 84101 需登录 | |
| `/api/youpin/price/trend/filter/info` / `/data` / `/dataList` / `/getValidDateTime` | 价格走势 | `{"templateId":"34966"}` | ❌ 84101 需登录 | 页面文案"登录后查看价格走势" |
| `/api/youpin/bff/trade/v1/trade/monitor/price/trend/risefall/query` | 涨跌"猜一猜" | | ❌ 需登录 | |
| `/api/youpin/bff/trade/purchase/order/deletePurchaseOrder` | 删除求购 | | ❌（未测，写操作） | |
| `/api/homepage/pc/goods/market/querySortList` | 排序选项 | `{"listType":"10"}` | ✅ | |
| `/api/homepage/pc/goods/market/lenovoSearch` | 搜索联想 | `{"keyWords":"红线","gameId":730}` | ✅（返回 Data=null，参数名可能不对） | |
| `/api/youpin/commodity/adapter/public/csgo/template/page` | 模板分页 | | ❌ 84004 Not Found（参数未知） | |
| `/api/youpin/bff/user/Account/getUserInfoForApp` | 当前用户信息 | | 需登录 | 登录后可拿到自己的 `userId` |

短时间内连续 ~10 次请求时有 1 次返回空响应，疑似轻度限流，需要做退避。

### 1.4 求购列表响应结构（核心需求）

```json
{
  "code": 0, "msg": "成功",
  "data": {
    "total": 66, "hasNext": false,
    "purchaseOrderResponseList": [
      {
        "purchaseNo": "<19 位单号>",
        "userId": 1234567, "userName": "某用户",
        "purchasePrice": 505.00, "purchasePriceDesc": "505",
        "surplusQuantity": 1,
        "abradeText": null, "fadeText": null, "specialStyle": null,
        "autoReceived": 1,
        "rankFirstPrice": null, "rankFirstPriceText": null, "isRankFirst": null,
        "templateId": 34966, "typeId": -1, "purchaseLabelList": null
      }
    ]
  }
}
```

要点：

- 列表按 `purchasePrice` 降序（同价按时间先后）。未登录只给前 20 条，`pageIndex` 被忽略。
- 有 `userId` 字段 → 登录后知道自己的 `userId`，即可在列表中定位自己的单子；**排名 = 自己所在位置（1-based）**。
- 存在 `isRankFirst` / `rankFirstPrice` 字段（未登录为 null）——悠悠官方本身有"是否排第一"的概念，登录后"我的求购"页很可能直接给出。
- `abradeText` / `fadeText` / `specialStyle` / `typeId` 说明**带筛选条件的求购（指定磨损区间、渐变、特殊款式）是独立队列**，排名需要按同条件比较。这是需要与发起人确认的关键点。

### 1.4.0 求购队列的真实语义（实测 4 个模板）

通过 [SteamTradingSite-ID-Mapper](https://github.com/EricZhu-42/SteamTradingSite-ID-Mapper) 的 `uuyp/730.json`（34417 条 market_hash_name → templateId）定位模板后抓取：

| 模板 | total | 观察 |
|---|---|---|
| AK-47 \| Redline (FT) `1414` | 60 | 前 20 条全部带磨损区间：372 元(0.15-0.16)、361(0.15-0.17)、359(0.15-0.18)、280(0.18-0.19)、226(0.21-0.22)… **不同磨损区间的求购混排在同一列表里，按价格降序** |
| AWP \| Asiimov (FT) `407` | 29 | 全部 `abrade=None`（不限磨损），数量可达 x18/x33 |
| ★ Karambit \| Doppler (FN) `1785` | 102 | `typeId=108 special=红宝石`、`typeId=107 special=黑珍珠` 混排；同一 special 下又分 0-0.01 / 0-0.04 / 不限 |
| M4A4 \| Howl (FT) `47850` | 10 | 0.15-0.18 / 0.15-0.2 / 不限 混排 |

结论：**"求购排名"没有唯一定义**。候选定义：
- A. 整个模板列表中的位置（页面上看到的顺序）——最简单，但一个 0.15-0.16 的高价单会"压"在不限磨损单前面，实际上两者面向不同货源；
- B. 同筛选条件（相同 abradeText/typeId/special/fade）子队列中的位置；
- C. "对某个具体磨损值 X 的卖家而言，我的单子排第几"（所有 filter 覆盖 X 的单子按价格排序）——最贴近"会不会先成交"。
需要发起人确认用哪种（见提问清单）。

### 1.4.1 Python 复现结果（脱离浏览器）

用 pycryptodome 复现 `deviceW2`（参考 Steamauto 的 `uuyoupinapi/UUApiCrypt.py`）：

- 请求体只需 `{"iud": "<uuid4>"}` 经 AES 加密即可，**不需要真实浏览器指纹**；响应解密得到 `{"u": <65位>, "deviceUk": <65位>}`。
- 调 `getTemplatePurchaseOrderListPC` 时**只带 `uk` 一个自定义头 + platform/AppVersion 即可**，`deviceUk` 可省略；`api.youpin898.com` 与 `pc-api.youpin898.com` 两个域名都能调通。
- Steamauto 的做法：`uk` 每 30 秒刷新一次；返回 HTTP 405 视为 uk 失效，`code 84104` 视为"悠悠风控，暂时无法获取价格"，`code 84101` 视为登录失效。
- 未登录状态传 `minAbrade/maxAbrade/typeId`、`pageIndex=2`、`pageSize=30` 结果都与默认一致（固定前 20 条）。

### 1.4.2 Steamauto 已逆向的 App 端接口（`https://api.youpin898.com`，需 `authorization: Bearer <token>`）

来源：[Steamauto/Steamauto](https://github.com/Steamauto/Steamauto) `uuyoupinapi/__init__.py`（1287★，AGPL-3.0，最近推送 2026-07-15）。

| 端点 | 用途 | 关键参数 |
|---|---|---|
| `POST /api/user/Auth/SendSignInSmsCode` → `POST /api/user/Auth/SmsSignIn`（或 `SmsUpSignIn`） | 手机号 + 短信验证码登录，拿 token | phone, code, session |
| `POST /api/youpin/bff/trade/purchase/order/searchPurchaseOrderList` | **我的求购列表** | `{"pageIndex":1,"pageSize":40,"status":20}`（20 = 进行中） |
| `POST /api/youpin/bff/trade/purchase/order/getTemplatePurchaseOrderList` | 模板下求购列表（App 版，登录后可翻页） | `templateId, pageIndex, pageSize, minAbrade, maxAbrade, typeId` |
| `POST /api/youpin/bff/trade/purchase/order/savePurchaseOrder` / `updatePurchaseOrder` | 发布 / 修改求购（写操作） | templateId, purchasePrice, purchaseNum, orderNo… |
| `POST /api/homepage/v2/detail/commodity/list/sell` | 模板最低在售价 | templateId |
| `POST /api/youpin/commodity/user/inventory/price/trend` | 我的库存及走势 | pageIndex, pageSize |
| `POST /api/youpin/bff/trade/sale/v1/buy/list` | 我的购买订单 | orderStatus=340 |

App 端请求头：`App-Version: 5.28.3`、`AppType: 4`、`platform: android`、`DeviceId`、`DeviceToken`、`package-type: uuyp`、`user-agent: okhttp/3.14.9`、`Device-Info: {...}`。

### 1.5 PC 网页端能力边界

- `/mine` 页面文案："悠悠有品网页版交易功能暂未开放，请扫描二维码或者在应用商店下载"。**挂求购、改价、"我的求购"管理在 App/H5 端**，PC 网页端只有查看。
- 因此"我的求购列表"接口需要从 App/H5 抓包（或复用 Steamauto 等项目中的 `api.youpin898.com` App 端接口）。

---

## 2. 悠悠有品官方开放平台（真实存在，但不适合本需求）

- 入口：`https://www.youpin898.com/openPlatform`（需登录），文档：`https://www.yuque.com/yyyoupin/azx6iu`（公开，31 篇，最近更新 2026-02-26）。
- 网关：`https://gw-openapi.youpin898.com/open/v1/api/...`，鉴权为 `appKey + timestamp + RSA sign`，建议 IP 白名单。
- **申请门槛**：支付宝实名、18-40 周岁、上传手持身份证正反面照片、填写"入驻用途"与"预期日交易流水"，3-5 个工作日审核。定位是面向代购/批量交易的"大客户"。
- 2026-01-06 起买家赠送业务收 1% 服务费（单笔最高 500 元）。
- 文档明文："**提倡引导开发者通过官方接口获取信息，私自调用接口干扰网站或系统正常运营，将会被永久封禁**"。
- **没有任何求购相关接口**（目录：市场查询 / 购买 / 购买订单 / 出售 / 出售订单 / 用户）。
- 市场查询限频极低：`templateQuery` 1 次/120s（返回全量模板 txt 下载链接）、`goodsQuery` 1 次/300s（单模板最低价 50 条，支持磨损/多普勒/渐变/淬火筛选，`sortType` 0/1/2）、`batchGetOnSaleCommodityInfo` 1 次/300s（一次最多 200 个模板，返回 `minSellPrice`/`referencePrice`/`sellNum`）。
- 结论：开放平台**不能**用于求购排名监控；可作为"合规的低频价格快照"补充源（每 5 分钟 200 个模板），但申请成本高。

---

## 3. 网易 BUFF（buff.163.com）curl 实测

| 端点 | 未登录 | 备注 |
|---|---|---|
| `GET /api/market/goods?game=csgo&search=...` | ❌ `Login Required` | 搜索/列表需要 cookie |
| `GET /api/market/goods/sell_order?game=csgo&goods_id=33912&page_num=1` | ✅ | 返回在售列表含 `paintwear`、`price`、`user_id`、`asset_info` |
| `GET /api/market/goods/buy_order?game=csgo&goods_id=33912&page_num=1` | ✅ | 返回求购列表含 `price`、`num`、`user_id`、`specific`（如"磨损: 0.07-0.08"筛选） |
| `GET /api/market/goods/price_history/buff?...` | ❌ `Login Required` | |

BUFF 的求购同样带"specific"筛选条件子队列，与悠悠一致。

---

## 4. Steam 社区市场 curl 实测

| 端点 | 结果 |
|---|---|
| `GET /market/priceoverview/?appid=730&currency=23&market_hash_name=...` | ✅ 无需登录，`{"lowest_price":"¥ 263.66","volume":"84","median_price":"¥ 265.56"}`（currency=23 为人民币，1 为美元） |
| `GET /market/itemordershistogram?country=CN&language=schinese&currency=23&item_nameid=...` | ✅ 无需登录，返回买卖挂单表（HTML 片段 + 数组），需先从 listings 页面解析 `item_nameid` |
| `GET /market/pricehistory/?appid=730&market_hash_name=...` | ❌ HTTP 400，需要登录 cookie |

---

## 5. 对方案的直接影响

1. **求购排名 ≤ 2 的检测在未登录状态下就能做**：只要知道自己的 `userId` 与要监控的 `templateId`（以及求购的筛选条件），轮询 `getTemplatePurchaseOrderListPC` 前 20 条即可算出排名。登录只是为了自动获取"我有哪些求购"。
2. 三平台都有**免登录**的在售/求购深度数据（悠悠前 20/10 条、BUFF 分页、Steam histogram），价格监控的 MVP 不依赖任何账号。
3. 悠悠的 `uk/deviceUk` 机制是唯一的"门槛"，可复现或手动导入。
4. 历史走势（悠悠/BUFF）都要登录；Steam 的 pricehistory 也要登录。历史数据更适合**自己持续采集**，或用 SteamDT/csqaq 等第三方。
5. 官方开放平台明确反对私自调用接口，个人自用需控制频率、单账号、只读。
