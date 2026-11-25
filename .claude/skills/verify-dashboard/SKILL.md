---
name: verify-dashboard
description: 在库副本上起一个只开看板的测试实例（8799），用内置浏览器验收看板改动，浅色、深色各看一遍，用完收干净。改了 frontend/ 或 web/ 的接口、要「测试实例验收」「对照视觉稿」「点一下看看」时使用。不碰正式实例，不跑监控。
---

# 看板验收（测试实例）

正式看板（8766）上**不点写操作**（CLAUDE.md 坑 12）。验收一律在库副本上起的测试实例里做。

## 1. 起实例

```bash
S=<scratchpad>/inst                     # 会话的 scratchpad 目录
mkdir -p $S/data
rm -f $S/data/skinpulse.db*
sqlite3 -readonly data/skinpulse.db ".backup '$S/data/skinpulse.db'"   # 在线库也能安全复制（WAL）
cp data/steamdt_base.json $S/data/
sed -e "s#^data_dir = .*#data_dir = \"$S/data\"#" -e "s/^port = 8766/port = 8799/" settings.toml > $S/settings.toml
```

写 `.claude/launch.json`（**用完删掉**，它不进仓库）：

```json
{ "version": "0.0.1", "configurations": [ { "name": "skinpulse-test",
  "runtimeExecutable": "uv",
  "runtimeArgs": ["run", "python", "-m", "skinpulse", "run", "--web-only", "--settings", "<S>/settings.toml"],
  "port": 8799 } ] }
```

用 `preview_start`（名字 `skinpulse-test`）起服务。启动时会自动跑迁移，不用手动升级副本。

## 2. 看

- 内置浏览器的面板很小：先 `resize_window` 设成 1360×850 或 1440×900，再截图；看完 `preset: desktop` 还原。
- 浅色、深色各看一遍。切换配色后要**重新加载页面**，不然 Naive UI 的主题会一半一半。
- 用 `javascript_tool` 读宽度、行数、网址参数，比看截图靠谱：表格有没有横向溢出（`scrollWidth > clientWidth`）、筛选后剩几行。
- `read_console_messages`（只看错误）必须为空。
- 只读的检查可以直接在正式看板 8766 上做（纯前端改动、`dist` 构建后立即生效时）；**写操作只在 8799**。

## 3. 注意

- 测试实例**不跑监控**，但 SteamDT 和正式服务吃同一份配额：大盘接口几分钟 3 次就 4005；选品真跑一次只用小池子（例如「手套 · 久经沙场」94 个，约 2 分钟），避开 23:40～00:50。
- 测试实例和正式服务**不能指向同一个库**：在正式库上起 `run --web-only` 会把正在跑的选品标成中断。
- 内置浏览器打不开 claude.ai 上的视觉稿（要登录）；对照视觉稿靠记下来的布局要点和截图。

## 4. 收尾

```bash
rm .claude/launch.json
```

`launch.json` 在工作区里的时候，提交只 `git add` 指定路径，别用 `git add -A`（2026-11-23 差点把它提交进去）。

`preview_stop` 停掉实例，`resize_window` 还原成 `desktop`。在 plan 的进度（或 PR 描述）里写一句验收看了什么（宽度、配色、哪些交互）。
