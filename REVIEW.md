# 评审规则

合并前跑 `/code-review`，并按下面几遍逐项看 diff。评审回答两个问题：**这个改动做到了 plan / intent 想做的事吗？有没有碰到下面的红线？**

同一类问题在评审里出现第二次，就把它写进 [CLAUDE.md](CLAUDE.md) 的「必须知道的坑」。

## 严重程度

| 级别 | 含义 | 处理 |
|---|---|---|
| **阻断** | 违反红线、会丢告警、会泄露凭据、会让数据悄悄变错 | 必须修完才能合并 |
| **重要** | 行为错误但不静默（会报错、会在 `/status` 上显示） | 合并前修，或写进 [docs/known-issues.md](docs/known-issues.md) 并说明理由 |
| **建议** | 可读性、重复代码、命名 | 作者自己决定 |

## 第 1 遍：红线（任何一条都是阻断）

- [ ] **只读**：没有任何下单、改价、撤单接口（hook 会拦代码里的接口名，但也要看有没有拼出来的 URL）。
- [ ] **凭据**：token / key / authorization / uk / ntfy topic / 手机号 / 短信码没有进日志、异常消息、HTTP 响应、fixture、测试快照；传输层异常只记类型名。
- [ ] **看板不直接请求悠悠**：`web/` 下只读数据库和 `market_data` 缓存。
- [ ] **没有凭据文件被提交**（`git status` 里没有 `*token*.txt`、`.env`、`settings.toml`）。

## 第 2 遍：会静默出错的地方

- [ ] **价格缺失 ≠ 0**：没有 `or 0`、`Decimal(0)` 顶替缺失值；`sell_depth` 的 0 与 None 分开；渲染走 `core.messages.sell_label`。
- [ ] **成败看业务码**：新接外部接口时，没有只看 HTTP 200 就当成功。
- [ ] **告警状态机**：不会让单子卡在 `ALERTING`（过滤要发生在状态机之前，不在事件产生之后丢弃）；推送失败会退回 `NORMAL`。
- [ ] **`recover_pending`** 仍然只在 `scheduler.build_scheduler` 里调用。
- [ ] **时间**：入库是 UTC aware；显示前转 `settings.timezone`；没有 naive datetime。
- [ ] **SteamDT K 线**按 `[时间, 开, 收, 高, 低]` 解析。
- [ ] **解析外部响应**：字段名来自实测响应（fixture 是原始响应、已脱敏）；业务上不可能为空的字段有守卫，缺了会报错而不是变成默认值。

## 第 3 遍：流程

- [ ] 测试全绿且无 warning；修 bug 的提交带着一个先失败后通过的测试。
- [ ] 实现偏离了 plan / spec 的地方，同一个提交里改了文档。
- [ ] 新的「选 A 还是 B」有 ADR；新踩的坑进了 CLAUDE.md 或 research。
- [ ] 改了命令、配置项或页面的，README 和使用手册（docs/guide.md）同步改了。
- [ ] Conventional Commits，提交信息写清「为什么」。

## 可以跳过

- `uv.lock` 的机器生成改动。
- `tests/fixtures/` 里的原始响应（只检查是否脱敏）。
- `docs/research/` 的叙述性内容（只检查是否与代码矛盾）。
