# SkinPulse

悠悠有品求购排名监控：你的求购单在同条件子队列里升到第 1 名、交易中、成交、市价倒挂时推送到微信，第 2 名等每天 00:00 的前 2 名表；附带本地看板（总览、单品行情、大盘、价位、选品）。Python 单进程服务，个人使用，**对悠悠只读**。

这份文件是给在本仓库里干活的 AI 编码助手（和人）看的：第一天就要知道的命令、约定和坑。使用说明见 [README.md](README.md)。

## 命令

```bash
uv run pytest -q                      # 全量；健康输出：N passed、无 warning（约 30 秒）。局部改动先跑相关文件
npm --prefix frontend test           # 前端 vitest；改了 frontend/ 还要 npm --prefix frontend run build 并提交 dist
uv run python -m skinpulse demo       # 假数据 + 只读看板，不需要悠悠账号
uv run python -m skinpulse run        # 调度器 + 看板 http://127.0.0.1:8765
uv run python -m skinpulse round      # 手动跑一轮（真的请求悠悠、可能推送）
uv run python -m skinpulse sync       # 只同步求购单
uv run python -m skinpulse backtest --out r.md   # 价位回测（只读库）
uv run python -m skinpulse dump my-orders | dump template <id>   # 看原始响应
```

- `notify-test` 会真的发消息。
- ruff 全量有历史遗留（见 [known-issues](docs/known-issues.md)），别全量 `--fix`；**F / E9 基线是 0**，CI 和 `.claude/hooks` 都会查。

## 什么时候读什么

| 要做的事 | 先读 |
|---|---|
| 动悠悠适配器 / 排名 / 告警 | [live-api-findings](docs/research/2026-09-06-live-api-findings.md) |
| 动 BUFF 适配器 / BUFF 轮次 | [BUFF 调研](docs/research/2026-11-24-buff-readonly-api.md) §8、§9；[BUFF spec](docs/superpowers/specs/2026-11-24-buff.md) |
| 动 SteamDT / PushPlus / ntfy | [steamdt-pushplus-ntfy-findings](docs/research/2026-09-23-steamdt-pushplus-ntfy-findings.md) |
| 动总览表格、筛选、排序 | [看板 spec §4.1](docs/superpowers/specs/2026-09-27-dashboard-redesign.md)；代码 `frontend/src/pages/overview/`（纯逻辑在 `logic.ts`） |
| 动看板其它页面 / `/api/*` | [看板 spec](docs/superpowers/specs/2026-09-27-dashboard-redesign.md) 对应小节 |
| 动价位、建议、回测、选品 `/pick` | [价位 spec](docs/superpowers/specs/2026-09-27-price-position.md) §4（计算）/ §9（建议）/ §9b（选品） |
| 动盈亏 `/pnl`、`pnl import` | [盈亏 spec](docs/superpowers/specs/2026-11-24-pnl.md)（口径 §4、任务 §5）、[ADR-0024](docs/decisions/0024-pnl-ledger-fifo-per-platform.md) |
| 动交易中 / 成交推送 | [成交推送 spec](docs/superpowers/specs/2026-10-02-fill-alerts.md) |
| 动市价提醒 | [市价提醒 spec](docs/superpowers/specs/2026-10-15-market-alerts.md) |
| 整体设计、为什么是现在这样 | [主 spec](docs/superpowers/specs/2026-08-23-skinpulse-design.md)、[decisions/](docs/decisions/) |
| 已知问题 | [docs/known-issues.md](docs/known-issues.md) |

## 工作方式

- 定下来的「选 A 还是 B」写 ADR（`docs/decisions/`）。实现偏离 spec 时，**同一个提交**改回文档。
- 修 bug 先写失败的测试；测试全绿、无 warning 才提交；Conventional Commits（英文）。
- 评审按 [REVIEW.md](REVIEW.md)。看板改动用 skill **verify-dashboard** 在库副本上验收。

## 模块地图

- `adapters/`：HTTP → 规范化 dataclass 和各自的错误码（`youpin/`、`buff/`、`steamdt/`、`pushplus/`、`ntfy/`）。
- `core/`、`analysis/`：纯逻辑、无 IO，用 fixture 测。
- `services/monitor.py` 每轮主流程；`notifier.py` 多通道推送；`market_data.py` 看板用的 SteamDT 缓存；`valuation.py` / `pick.py` / `backtest.py` 价位三期。
- `web/`：看板 `/api/*`，只读库和行情缓存；写操作只有参数设置、立即同步、换 token、开始选品。前端源码 `frontend/`，构建产物 `skinpulse/web/dist/`（提交进仓库）。
- `demo/`：`skinpulse demo` 的假数据和假行情。
- `scheduler.py` 调度与每轮日志；`runtime.py` 所有命令共用的装配（`rt.accounts`：启用的悠悠号，和 Monitor 共用一份）。

## 工程约定

- Python ≥ 3.12，`uv` 管依赖，路径一律 `pathlib`。
- **悠悠接口只读**：下单 / 改价 / 撤单接口一个都不能出现（`.claude/hooks/guard_youpin_writes.py` 会拦）。
- 悠悠请求头全部来自 `settings.toml [youpin]`，不硬编码。
- **token / key / authorization / uk / ntfy topic / 手机号 / 短信码绝不进日志、异常消息或 HTTP 响应**；传输层异常只记类型名（URL 里可能带 key）。
- 凭据存系统钥匙串（`SecretStore`），绝不进仓库。
- 解析外部接口以**实测响应**为准，fixture 存原始响应（脱敏）。

## 必须知道的坑

1. **「sent」≠ 送到了**：推送有没有到，以在微信上亲眼看到为准。
2. **悠悠、SteamDT、PushPlus 都是 HTTP 永远 200**，成败只看业务码。
3. **价格 0 = 没有数据**；`sell_depth=0`（没人在卖）和 `None`（还没查）要分开，渲染走 `sellLabel` / `sell_label`，缺失绝不填 0。
4. **悠悠的 `rank` 是同条件子队列排名、滞后几分钟**：告警前用公开队列实时核对。
5. **`recover_pending` 只在 `scheduler.build_scheduler` 里调**（挂到 `build_runtime` 上会让一条 `sync` 吞掉在途告警）。
6. **看板页面绝不直接请求悠悠**（风控）；外部行情一律走 `market_data` 缓存。
7. **SteamDT K 线是 `[时间, 开, 收, 高, 低]`**；大盘接口配额很紧（几分钟 3 次就 4005）。
8. **时间**：库里一律 UTC aware；显示前转 `settings.timezone`。
9. **前端产物要提交**（`tests/test_frontend_build.py` 守着）；写接口只收同源 JSON，测试客户端用 `base_url="http://127.0.0.1:{port}"`；TypeScript 钉在 5.9。
10. **服务跑的是工作区代码**：切分支就是切运行中的代码；`dist` 构建后看板立即生效，后端要重启；合并后记得重启服务（系统页会提示「代码已更新，服务还没重启」）。
11. **Vue**：父组件 scoped 样式会作用到子组件根元素；布尔 prop 没传是 `false`，「默认开」用 `hideXxx` 反着起名。
12. **正式看板上不点写操作**：验收用 skill verify-dashboard。
13. **悠悠里改价 = 撤单重挂**：旧单号消失、新单号出现，告警和提醒状态不继承；过渡时旧单会短暂显示 `rank = "*"`，靠 `createTime` 没变和真交易中区分。
14. **SteamDT 的「悠悠最高求购」是所有子队列里最高的**（含条件单）；刀、手套常比在售价还高。
15. **多账号：「消失」只在本号的单里判**（`sync_my_orders` 带 `account_id` 条件）；号 1 的 token 仍在钥匙串的 `youpin_token`，别的号是 `youpin_token:N`。
16. **Windows 上 Claude 会话写的注册表，系统看不到**：会话里的命令写 HKCU 进的是隔离的一份，Explorer、任务管理器都看不到，会话里读到的也不是真实值。`service install / uninstall / status` 一律让用户在自己的终端里跑；要看真实注册表，用 WMI `StdRegProv`（`Invoke-CimMethod`）读。
17. **BUFF 的订单不带求购单号**：成交只能按「type=2、同 goods_id、同价、建于求购单之后、没配过」配对（`services/buff.py`）；公开队列里的 user_id 打过码，认「我这张单」只能靠单号。BUFF 号不在 `rt.accounts`（那里全是悠悠 client），在 `rt.buff_accounts`。

## 运维（macOS 和 Windows，[ADR-0010](docs/decisions/0010-macos-launchagent-caffeinate-on-ac.md)、[ADR-0021](docs/decisions/0021-windows-autostart-supervisor.md)）

- **任何时候只能有一个实例在跑**（两个会各推一遍、请求量翻倍）。`run` 有单实例锁，一次性命令不受影响。
- LaunchAgent（[ADR-0010](docs/decisions/0010-macos-launchagent-caffeinate-on-ac.md)）：`scripts/macos/launchagent.sh install | status | restart | stop | start | uninstall`。只是重启的话，也可以点看板「系统」页的「重启服务」。拉新代码后 `uv sync` 再 `restart`。
- Windows：`uv run python -m skinpulse service install | status | restart | stop | start | uninstall`（注册表 Run 键 + 守护进程 `supervise --root`，日志 `data/logs/supervisor.log`）；让用户在自己的终端里装（坑 16）。
- 日志：`data/logs/skinpulse.jsonl`（全量，每轮一行 `round: checked=…`）；`data/logs/launchd.log`（只有警告、报错、traceback）。
- 接电源时 `caffeinate -s` 防睡眠；用电池或合盖时监控停，23:50 的价格快照错过就没了，00:00 简报 12 小时内启动补发。
- **换悠悠 token**：浏览器里退出重登 → 看板「系统」页 →「更新 token」，立即生效。同一个浏览器会话复制的 token 不续期；token 会被服务端提前作废，`exp` 只当上限。
- HTTP 客户端不读代理环境变量（[ADR-0011](docs/decisions/0011-http-clients-ignore-env-proxy.md)）。
