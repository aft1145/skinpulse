## 改了什么

## 为什么

## 怎么测的

- [ ] `uv run pytest -q` 全绿、无 warning
- [ ] `uv run ruff check --select F,E9 .` 是 0
- [ ] 改了前端：`npm --prefix frontend test` 通过，重新构建并提交了 `skinpulse/web/dist/`
- [ ] 没有带 token、cookie、key、手机号、单号等真实数据（fixture 已脱敏）
- [ ] 没有向悠悠或 BUFF 发写请求（下单、改价、撤单）

> 公开仓库是上游私有仓库的导出快照，PR 不在这里直接合并：维护者会把改动搬回上游，下次同步时带回来，并在提交里写上 `Co-authored-by: 你`。详见 [CONTRIBUTING.md](https://github.com/aft1145/skinpulse/blob/main/CONTRIBUTING.md)。
