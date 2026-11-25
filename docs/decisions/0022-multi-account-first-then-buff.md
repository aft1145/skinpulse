# 0022. 先做悠悠多账号，BUFF 第二期；账号是一等公民，BUFF 只读、慢轮询、自己数名次

- 日期：2026-11-24
- 状态：生效
- 拍板：用户认可 Claude 的建议（intent（未公开），「按建议定」）

## 背景

代码处处默认只有一个悠悠号（`account` 表只有 `id=1`、钥匙串一个 `youpin_token`、运行时一个客户端、登录失效提醒是模块级单例）。用户要支持多个悠悠号和 BUFF。BUFF 的公开资料和匿名试探见 [BUFF 只读接口调研](../research/2026-11-24-buff-readonly-api.md)：有只读接口，但不给名次、条件单混排、风控紧、登录有效期不明。

## 决定

**分期**：第一期悠悠多账号，第二期 BUFF。第二期开工前，用户把 BUFF `session` 录进钥匙串，做一次约 20 次的只读实测，回答调研 §7。

**账号模型**（第一期就按两个平台设计）：

- `account` 加 `platform`、`label`、`enabled`、`device_id`（按号持久化）；`my_buy_order`、`order_event` 加 `account_id`，老数据填 1。
- 单号仍是 `purchase_no` 主键；BUFF 单号存成 `buff:<原单号>`，前缀只在适配器里加、去。
- `system_status` 的账号通道按号分 key；匿名通道（按 IP）仍是一个，按模板去重，不随账号数翻倍。
- 钥匙串：第 1 个号沿用 `youpin_token`；新号 `youpin_token:<id>`，BUFF `buff_cookie:<id>`。
- 一轮里各号依次同步、中间隔开；新号先跑一周没有异常再加下一个。

**推送与看板**：

- 启用两个以上的号时，每张单前加 `[简称]`；一个号时一字不改。登录失效按号各推、各自 24 小时重复。PushPlus 配额所有号共用，同一轮照旧合并。
- 看板顶栏「账号」切换，默认「全部」，只有一个号时不显示；系统页每号一张卡片。
- 自己两个号在同一子队列：告警照旧按单判断，看板标出「前面是我自己的号」。

**BUFF**：

- 只读：下单、改价、撤单、上架、付款接口不进代码，写接口 hook 扩到 BUFF。
- 名次自己数公开队列：普通单只和普通单比；条件单只和 `specific` 完全相同的单比；「前面还有 N 张条件单」单独显示。
- 一轮 15 分钟，每次请求隔 ≥ 5 秒；只给「可能在前几名」的单查队列（用 SteamDT 的 BUFF 最高求购预筛）；遇到验证码或 429 就停 BUFF 通道、推一条提醒，不重试。
- 第一期范围：名次、榜首价、升到第 1 名、交易中 / 成交推送。不做价位建议、简报（ADR-0019 的到手价口径不变）。
- 饰品按 SteamDT `/base` 的平台 itemId 对到悠悠 `templateId`（`item` 加 `buff_goods_id`），库里其它地方照旧按 `templateId` 关联。

## 否决的方案

- 先做 BUFF：BUFF 接口要登录实测、不确定性大，和账号抽象叠在一个分支里风险叠加。
- 主键改成 `(platform, order_no)`：干净，但 SQLite 改主键要重建表，按单号关联的 6 张表都要跟着改。
- 多开实例、一个号一个服务：违反「任何时候只有一个实例」，推送和匿名请求都会重复。
- 每个号一个独立看板页面：重复多，「全部」才是最常看的。
- BUFF 每张单每轮都查队列：100 张单按 5 秒一次要 8 分钟以上，悠悠 09 月就是因此放弃这种做法的，在 BUFF 上还多一层封号风险。

## 影响

- 第一期改动集中在 `db`（迁移）、`runtime`、`services/account.py`、`services/monitor.py`、`web/overview.py`、`web/youpin_token.py`、`web/system.py`、`core/messages.py` 和前端三页。
- 第二期新增 `adapters/buff/`，`core/` 里加两个平台共用的 `MyOrder`、`QueueOrder`。
- 值得重新考虑：BUFF 登录实测发现「我的求购单」直接给名次（那就不用自己数）；或者条件单的匹配方式和建议口径对不上（看真实成交）；或者多号出现关联风控迹象。

## 补记（2026-11-24，BUFF spec）

登录后实测（[调研 §8](../research/2026-11-24-buff-readonly-api.md)）发现 `wait_supply` 每张单自带 `buy_max_price`（最高的普通单价），所以 BUFF 候选改用它来筛，不用 SteamDT 的 BUFF 最高求购。「遇到验证码或 429 就停」具体成暂停 `buff.pause_hours` 后自动恢复。见 [BUFF spec](../superpowers/specs/2026-11-24-buff.md) §14。
