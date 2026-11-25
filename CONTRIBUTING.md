# 参与贡献

欢迎提 issue 和 PR。先读一下这几条，省得来回。

参与本项目请遵守[行为准则](CODE_OF_CONDUCT.md)。使用问题、想法讨论请到 [Discussions](https://github.com/aft1145/skinpulse/discussions)；issue 留给能复现的问题和明确的功能建议。

## 这个仓库是怎么维护的

公开仓库是从上游私有仓库**导出的快照**（[ADR-0020](docs/decisions/0020-open-source-via-filtered-export.md)），每次同步是一个新提交。所以：

- **PR 不会在这里直接合并**：维护者会把改动搬回上游，下次同步时带回来。PR 里会注明是哪次同步带回的，提交信息里写上 `Co-authored-by: 你`，然后关掉 PR。
- 想做大一点的改动（新功能、改交互、改告警规则），先开 issue 说一下要解决什么问题，免得白写。

## 不接受的改动

- **任何向悠悠发写请求的功能**：下单、改价、撤单、自动发货。SkinPulse 对悠悠只读，这是底线（[CLAUDE.md](CLAUDE.md)「工程约定」）。
- 把 token、key、手机号写进日志、异常消息或 HTTP 响应的改动。
- 看板直接请求悠悠（会触发风控）；外部行情要走 `market_data` 缓存。

## 提 issue

- 先按 [SECURITY.md](SECURITY.md)「提 issue 前先脱敏」去掉 token、手机号、单号、userId 和昵称。
- 安全问题不要开公开 issue，走 Security → Report a vulnerability。
- 写清楚系统（macOS / Windows）、`git log -1 --oneline`、怎么复现；日志在 `data/logs/`。

## 开发

```bash
uv sync
uv run pytest -q                        # 全绿、无 warning 才提交
uv run ruff check --select F,E9 .       # 基线是 0
npm --prefix frontend ci && npm --prefix frontend test   # 改了前端才需要
npm --prefix frontend run build         # 改了前端要重新构建并提交 skinpulse/web/dist/
uv run python -m skinpulse demo         # 假数据看板，不联网、不读钥匙串
```

- 约定和「必须知道的坑」在 [CLAUDE.md](CLAUDE.md)，评审清单在 [REVIEW.md](REVIEW.md)。
- 解析外部接口以**实测响应**为准，fixture 存原始响应（脱敏后）。
- 修 bug 先写一个失败的测试。提交信息用 Conventional Commits（英文）。
