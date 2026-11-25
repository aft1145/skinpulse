# 盈亏统计的数据来源：悠悠买卖订单、库存、租出（2026-11-24 实测）

起因：要做盈亏统计（intent（未公开）），先查清楚悠悠有哪些**只读**接口能拿到买入、卖出、手续费和持有中的库存。

方法：在 CLI 加了几个只读 dump 子命令（`dump trades buy|sell`、`dump order <订单号>`、`dump inventory`、`dump lease-out`，后来又加了 `dump on-sale`），用作者账号的真 token 各跑一到几次，**一共 14 个请求**，间隔 3 秒以上，没有触发限流或风控。全部走 PC 通道（`pc-api.youpin898.com` + `_pc_headers`），和同步用的是同一套请求头。接口路径来自 [Steamauto](https://github.com/Steamauto/Steamauto) 的 `uuyoupinapi` 和 [uuyp-trade-analyzer](https://github.com/youki258/uuyp-trade-analyzer)（MIT，一个导出悠悠账单的小项目）；字段和行为以下面的实测为准。

## 1. 结论

| 要的数据 | 接口（全是 POST、要登录） | 能不能用 |
|---|---|---|
| 买入记录 | `/api/youpin/bff/trade/sale/v1/buy/list` | **能**。求购成交全在这里，和库里的成交事件一一对上 |
| 卖出记录 | `/api/youpin/bff/trade/sale/v1/sell/list` | **能**。列表里是成交价，到手价要看详情 |
| 卖出到手价（扣手续费后） | `/api/youpin/bff/trade/v1/order/query/detail` | **能**，`revenue.amount`；一张单一个请求 |
| 持有中的库存 + 悠悠记的买入价 + 悠悠给的市价 | `/api/youpin/commodity/user/inventory/price/trend` | **能**，`pageSize=1000` 一个请求拿全部 |
| 租出中的饰品 | `/api/youpin/bff/trade/v1/order/lease/out/list` | **能**，只有「租赁中」的；租金历史没查到 |
| 提现、余额流水 | — | **没找到**。开源项目里都没有，要抓 App 包 |

## 2. 买入、卖出订单列表

请求体（两个接口一样）：

```json
{"keys": "", "orderStatus": 340, "pageIndex": 1, "pageSize": 20, "presenterId": 0, "sceneType": 0}
```

- `orderStatus`：340 = 已完成（实测）；200 = 交易中、140 = 待发货（来自开源项目，未实测）。
- `pageSize` 上限 20（开源项目说超过返回空列表，未实测）。
- 响应 `data`：`orderList`、`total`（实测是 null，不能靠它算页数）、`orderRevertInfo`（交易撤回提示，这次全是 null）。
- **按下单时间倒序**（和「我的求购」status 30、40 不一样）。所以增量同步只要从第 1 页翻到见过的订单号就停。
- 金额字段单位是**分**（`totalAmount: 14200` = ¥142）；详情接口里是元的字符串（`"142.00"`）。
- 时间是毫秒时间戳：`createOrderTime`（下单）、`finishOrderTime`（完成）。

每行有用的字段：

| 字段 | 含义 |
|---|---|
| `orderNo` | 买卖订单号（和求购单号不是一个号） |
| `orderStatus` / `orderSubStatus` | 340 / 1301（成功）；1303 也见过（卖出，`saleOrderType=2`，完成时间比下单晚 2～3 天，含义不明） |
| `orderSubType` | **5 = 求购成交**（买入列表 80 条全是 5；卖出列表里 5 是「我卖给别人的求购」）。1、10 只在卖出里见过，应是普通在售成交，两者区别未查 |
| `buyerUserId` / `sellerUserId` | 买家、卖家悠悠 ID；买入列表里买家是自己，卖出列表里卖家是自己 |
| `totalAmount` / `commodityAmount` / `paymentAmount` | 成交总价（分）。多件单是全部件数的总价 |
| `serviceFee` | 实测全是 0，**不是**卖方手续费 |
| `sellGiveFee` | 只在一部分「卖给求购」的单上有：`{"serviceFee": "0.17", "rate": "0.01", ...}` |
| `commodityNum` | 件数。多件单的 `productDetailList` 最多只列 3 件（5 件的单只有 3 行） |
| `productDetail` | `commodityTemplateId`（模板 ID，和库里的 `template_id` 一致）、`commodityName`、`assertId`（拼错了的 assetid）、`abrade`（磨损）、`price`（单价，分） |

**买入一侧没有手续费**：买入详情 `amount = commodityAmount`，`showFeeAmount = false`。

**和库里的成交事件对得上**：买入列表前 3 页（60 条，2026-07 ～ 11-24）里，09-29 以来的每一条都对应库里一条 `order_event` 成交（`done` / `likely_done`，按模板 + 价格配对，39 对 39，没有多也没有少）。11-23 那张记成「推测成交」的纪念包（求购「已完成」列表里一直找不到）**在买入列表里有**，状态 340、16:34 完成。

## 3. 订单详情：卖出到手价

请求体 `{"orderNo": "<买卖订单号>", "userId": <自己的悠悠 ID>}`。

- 卖出：`revenue.amount` 是**到手价**（元，字符串）。实测两张：¥142.00 → 140.58，¥17.50 → 17.33，都是**成交价 × 99%，向下取整到分**。`amount` / `commodityAmount` 是成交价。
- 买入：`leaseNo` 字段装的是**求购单号**（和库里 `my_buy_order.purchase_no` 一致，实测 1 张）。名字很怪，但这是买卖订单和求购单之间唯一的直接连线。列表里没有这个字段。
- 详情里有双方昵称、Steam ID、头像、Steam 报价号，存 fixture 前要脱敏。

## 4. 库存：`inventory/price/trend`

请求体 `{"pageIndex": 1, "pageSize": 1000, "IsMerge": 0}`。

- `pageSize=1000` 一次返回全部（实测近千件，`totalPages = 1`）。Steamauto 的写法；没带刷新参数，不会触发悠悠去 Steam 重新拉库存。
- 顶层：`totalCount`、`marketPriceTotal`（市价合计）、`buyPriceTotal`（买入价合计）、`profitAndLossTotal`（浮动盈亏合计）。`profitAndLossTotal = 有买入价的那些件的市价合计 − buyPriceTotal`（实测到分对得上）。
- 每件 `itemsInfos[]`：`steamAssetId`、`templateId`、`commodityName`、`abrade`、`marketPrice`（悠悠给的市价，元字符串）、`assetBuyPrice`（悠悠记的买入价，元字符串，**不知道就是空串**）、`profitAndLossPrice`、`rentPrice` / `yearRentPrice`（参考租金）、`steamMarketPrice`。
- **`assetBuyPrice` 只对在悠悠买的有**：实测约 20% 的件有买入价，按市值算占了库存的约 87%（没有买入价的多是便宜的印花、箱子，可能是掉落或别处买的）。抽查 37 件近期求购成交的饰品，33 件在库存里能找到同模板、`assetBuyPrice` 等于求购成交价的一件。
- **assetid 一交易就变**：买入列表里的 `assertId` 是卖家那边的旧 assetid，在自己库存里一个都找不到（20 件抽查，0 件对上）。所以「买入 ↔ 现在的库存」不能靠 assetid 配对；悠悠自己的 `assetBuyPrice` 已经替我们配好了。卖出列表的 `assertId` 应是卖出前自己库存里的 assetid（没法回头验证，等有库存快照后再核对）。
- **租出去的饰品不在库存里**（转给了租客）；在售的在（§8）。

## 5. 租出：`lease/out/list`

请求体 `{"gameId": 730, "pageIndex": 1, "pageSize": 20, "sortType": 0, "keywords": ""}`。

- 只有「租赁中」（`orderStatus = 160`）的单，已归还的历史没出现。
- 顶层 `totalCount`、`hasNext`（这个接口的 `hasNext` 看起来可信）、`statisticsDataDesc`（一句话汇总：「件数：N｜价值：¥X｜总租金：¥Y/天」，是文字，要解析）。
- 每单：`leaseDaysDesc`（「100天」）、`leaseAmountDesc`（租金，文字「¥0.07」）、`leaseExpireTimestamp`、`commodityInfo`（`commodityId`、名字、磨损、长短租价……**没有 templateId**，要靠 `commodityHashName` 对模板）。
- `leaseTransferUnsupportedTip` 提到「交易撤回检测结束后」：Steam 的 7 天交易保护期在悠悠这边也有体现。

## 6. 手续费口径（实测 + 现有配置）

| 项 | 数 | 来源 |
|---|---|---|
| 悠悠卖出手续费 | 1%，向下取整到分 | 本次详情实测（普通在售、卖给求购各一张） |
| 悠悠买入手续费 | 0 | 本次详情实测 |
| 提现费 | 1%（`[valuation] withdraw_fee` 默认） | 价位 spec §4.3；没有接口能核实，悠悠的提现规则以 App 为准 |
| BUFF 卖出手续费 | 1.5%（`[valuation] fees`） | 价位 spec §4.3；BUFF 这边留给多账号与 BUFF 那条线 |

## 7. 请求量估算（给 spec 用）

- 首次导入全部历史：买入约每 20 笔一页、卖出同样，加上每张卖出一个详情请求。作者账号大约几十页加几十个详情，按 3 秒一个约几分钟，一次性。
- 之后每天：买入第 1 页、卖出第 1 页（翻到见过的单就停）、库存 1 个、租出 3 页左右，加上新卖出的详情。**每天 10 个以内**，比一轮同步（约 8 页）还少。

## 8. 补充（同一天写 spec 时，又约 28 个只读请求）

- **买入列表翻到底**：17 页，最后一页不满 20 条，最早一笔 2025-04-08。卖出 2 页。
- **在售列表**（`/api/youpin/bff/new/commodity/v1/commodity/list/sell`，`{"pageIndex":1,"pageSize":100,"whetherMerge":0}`，`dump on-sale`）：每件有 `steamAssetId`、`templateId`、`commodityHashName`、`sellAmount`、`statusDesc`（「在售 58 天」）。**在售的饰品全在库存接口里**（按 steamAssetId，14/14），所以算持仓不用单独拉在售。
- **租出列表能逐件对上模板**：`commodityInfo.commodityHashName` 是英文 market_hash_name，和买入订单的 `productDetail.commodityHashName` 同一种写法。按它配，租出中的件里大部分能对上自己的买入记录，对不上的是别处来的饰品。
- **SteamDT 悠悠在售价 ≈ 悠悠库存接口的 `marketPrice`**：同时有两者的 175 个模板，比值中位数 1.00，第 10 百分位 0.97，只有 2 个差出 20%。SteamDT 的悠悠在售价可以当持仓市价用（库存外的饰品也有价）。
- **悠悠和 BUFF 在售价**（SteamDT 同一批）：258 个模板，比值中位数 1.00，第 5、95 百分位 0.88、1.21；超出 1.5 倍的 3 个，全是 1 元以下的饰品。持仓里看着离谱的涨幅（成本十几元、现价几百）两边价格都对得上，是真涨。

## 9. 没查清的

- `orderSubType` 1 和 10、`saleOrderType` 2、`orderSubStatus` 1303 的确切含义。
- 交易被撤回（Steam 7 天保护期内卖家撤回）时，买入订单会变成什么状态、还在不在 340 列表里。`orderRevertInfo` 这次是空的。
- 租出归还后 assetid、`assetBuyPrice` 会不会变。
- 租金收入的历史（已结束的租出单、实际到账）和提现记录：要抓 App 包，或者用户从 App「我的账单」里看。
