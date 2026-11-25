# 决策记录（ADR）

每条记录一件**已经定下来**的事：定了什么、为什么、否决了哪些方案、影响到哪里。

- 文件名 `NNNN-短横线主题.md`，编号递增，不复用。
- **写好之后不改内容**。决定变了就新写一条，在旧的那条状态行写「被 NNNN 取代」——这是唯一允许的修改。
- 还没定、等用户拍板的事不写在这里，写成 ../intent/（未公开）；定下来之后再落成 ADR。
- 需求基线（R1–R10）在 [spec §1](../superpowers/specs/2026-08-23-skinpulse-design.md#1-已确认的需求与约束)，这里只记之后的决策。

## 索引

0001–0005 是 2026-09-23 根据 research 文档、提交记录和当时的交接页（现 STATUS.md）**补记**的；原始讨论没留档的地方，记录里写明了是推断。

| # | 决策 | 日期 | 状态 |
|---|---|---|---|
| [0001](0001-rank-from-youpin-with-live-confirm.md) | 排名用悠悠上报的 rank，推送前用公开队列实时核对 | 2026-09-21 | 生效 |
| [0002](0002-push-via-wechat-pushplus.md) | 推送以 PushPlus（微信）为主，ntfy 兜底 | 2026-09-23 | 生效 |
| [0003](0003-market-data-steamdt-only.md) | M2 行情只接 SteamDT | 2026-09-23 | 生效 |
| [0004](0004-server-side-svg-charts.md) | 看板图表服务端生成 SVG，不引前端图表库 | 2026-09-23 | 被 0013 取代 |
| [0005](0005-windows-autostart-startup-folder.md) | Windows 自启用启动目录，不用计划任务 | 2026-09-21 | 被 0021 取代 |
| [0006](0006-no-csqaq.md) | 不接 CSQAQ | 2026-09-23 | 生效 |
| [0007](0007-market-history-with-m3-brief.md) | 行情历史入库随 M3 每日简报一起做 | 2026-09-23 | 生效 |
| [0008](0008-realtime-alert-rank1-brief-top2.md) | 实时告警只推第 1 名；第 2 名交给每日简报 | 2026-09-24 | 生效（「只推第 1 名」被 0015 补充） |
| [0009](0009-run-service-on-macos.md) | 服务改在 macOS 上运行，Windows 实例退役 | 2026-09-24 | 生效 |
| [0010](0010-macos-launchagent-caffeinate-on-ac.md) | macOS 自启用 LaunchAgent，接电源时用 caffeinate -s 防睡眠 | 2026-09-24 | 生效 |
| [0011](0011-http-clients-ignore-env-proxy.md) | HTTP 客户端忽略环境里的代理变量，全部直连 | 2026-09-24 | 生效 |
| [0012](0012-dashboard-vue-spa.md) | 看板改为 Vue 3 前后端分离，构建产物进仓库，由 FastAPI 同端口提供 | 2026-09-27 | 生效 |
| [0013](0013-echarts-replaces-server-svg.md) | 看板图表改用 ECharts，取代服务端生成的 SVG | 2026-09-27 | 生效 |
| [0014](0014-valuation-advice-three-phases.md) | 「值不值得买」分三期做，建议先过回测再上线 | 2026-09-27 | 生效 |
| [0015](0015-push-fills-and-in-trade.md) | 实时推送加上「交易中」和「求购成交」，一次成交只推最早的一条 | 2026-10-02 | 生效 |
| [0016](0016-market-price-alerts.md) | 榜首高于在售价、或我的价贴近在售价时推送（前三名一律，第 4～10 名要达到在售的 85%） | 2026-10-15 | 生效 |
| [0017](0017-pick-go-within-90-percent-of-top.md) | 选品「可以挂」不要求排第 1：上限够得着悠悠最高求购的 90% | 2026-10-09 | 生效 |
| [0018](0018-reprice-star-and-likely-fills.md) | 改价时的「*」靠 createTime 区分；交易中过又查不到去向的记「推测成交」 | 2026-11-23 | 生效 |
| [0019](0019-pick-exit-youpin-and-rebound-wait.md) | 到手价只按悠悠算；选品里刚从低点涨上来的判观望 | 2026-11-23 | 生效 |
| [0020](0020-open-source-via-filtered-export.md) | 开源：私有仓库开发，过滤后导出到组织下的公开仓库 | 2026-11-23 | 生效 |
| [0021](0021-windows-autostart-supervisor.md) | Windows 自启：启动目录 + Python 守护进程 | 2026-11-24 | 生效（「登录即起」被 0023 取代） |
| [0022](0022-multi-account-first-then-buff.md) | 先做悠悠多账号，BUFF 第二期；BUFF 只读、慢轮询、自己数名次 | 2026-11-24 | 生效 |
| [0023](0023-windows-autostart-run-key.md) | Windows 自启改用注册表 Run 键，不用启动目录 | 2026-11-24 | 生效 |
| [0024](0024-pnl-ledger-fifo-per-platform.md) | 盈亏：自记批次账，同账号同平台同模板先进先出；提现费不扣，浮动默认按市价 | 2026-11-24 | 生效（同平台配对被 0025 取代） |
| [0025](0025-pnl-cross-platform-matching.md) | 盈亏：悠悠买、BUFF 卖也配成一对（同号同平台优先，其次跨号跨平台） | 2026-11-24 | 生效 |

## 模板

```markdown
# NNNN. 标题（一句话写出决定本身）

- 日期：YYYY-MM-DD
- 状态：生效 | 被 NNNN 取代
- 拍板：用户 / 用户认可 Claude 的建议

## 背景
什么事实或问题逼出了这个决定（链接 research / intent）。

## 决定
具体定了什么。

## 否决的方案
- 方案 A：为什么不选

## 影响
代码、配置、文档里哪些地方因此是现在这个样子；以后什么情况下值得重新考虑。
```
