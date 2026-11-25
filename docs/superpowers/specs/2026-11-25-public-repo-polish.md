# 公开仓库装修

- 日期：2026-11-25 · 状态：定稿
- intent：2026-11-25-public-repo-polish（未公开）（1～7 全做，图标定 A）
- 视觉稿（私有画布）：「仓库图标」一行的 A · 登顶，「README 顶部」一块
- 前置：[难以成交](2026-11-25-hard-to-fill.md) 先合并——截图和动图要拍新界面

## 1. 图标 A · 登顶

- 图形（viewBox 32）：圆角方块 `rx=8`；线 `M4 20h6l3 4 5-15 3.5 8H28`，白色，线宽 2.6，圆头圆角；金点 `circle(18, 4.9, r=2.1)`。
  - 浅色版：底 `#0A6B69`，金 `#F2C14E`；深色版：底 `#14928F`，金 `#F7D06B`。
  - 16 px 用的那份线宽 3.2、金点 r=2.6（视觉稿里小尺寸就是这样画的）。
- 文件：
  - `frontend/public/favicon.svg`：16 px 版（浏览器标签用）；
  - `docs/images/logo.svg`、`docs/images/logo-dark.svg`：README 顶部用；
  - `docs/images/logo-512.png`：组织 `aft1145` 头像，满幅方块、不带圆角（GitHub 自己裁圆角），用无头 Chrome 截成 512×512（用户在 GitHub 网页上传）。
- 看板顶栏 `AppShell.vue` 的 `.logo`：保留 CSS 方块底（`--sp-accent`），里面的 SVG 换成 A 的线和金点（金点用 `--sp-gold`）。
- 社交预览图 `docs/images/social-preview.png`（1280×640）：照 11-24 的模板重出，左边换新图标，右边换新截图。

## 2. README 顶部

中文 `README.md` 和英文 `README.en.md` 都用同一个结构，照视觉稿：

```html
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/logo-dark.svg">
    <img src="docs/images/logo.svg" width="88" alt="SkinPulse">
  </picture>
</p>
<h1 align="center">SkinPulse</h1>
<p align="center">一句话介绍</p>
<p align="center">徽章：CI · Release · License · Platform · Python</p>
<p align="center">快速开始 · 使用手册 · 更新记录 · English</p>
```

- 一句话：「悠悠有品 / BUFF 求购排名监控：升到第 1 名、交易中、成交、市价倒挂时推送到微信。只读，不下单。」
- 新加 Release 徽章：`https://img.shields.io/github/v/release/aft1145/skinpulse`。
- 下面接：动图 → 「能做什么」表 → 截图两张 → 「先读这个」→ 其余照旧。
- 现有的英文简介段落删掉，换成顶部导航里的 English 链接。

## 3. 内容修正

- 「能做什么」：
  - 总览一行去掉「充值前检查暂停中的单」，挪到「简报 · 分析 · 系统」一行（分析页）；
  - 总览一行加「难以成交的单可隐藏」；
  - 盈亏一行改成「悠悠和 BUFF 的买卖跨平台先进先出配对」。
- 「需要什么 › 账号」加「BUFF（可选，只读，网页 cookie）」。
- 「先读这个 › 非官方」加上 BUFF / 网易。
- issue 模板里的「macOS 版本」改成「系统（macOS / Windows 版本）」。

## 4. 英文 README

- `README.en.md`：中文版逐节翻译，同样的标题顺序、表格、图片。界面是中文，所以截图不另拍；文中界面上的字写成「中文原文（英文意思）」。
- 守护测试 `tests/test_readme.py`：两份的二级标题数量相同，图片引用集合相同，都链接到对方。漂了就失败，提醒同一个提交两边一起改。

## 5. 动图和截图

- 用 `uv run python -m skinpulse demo`（8799，假数据）。
- 截图工具：无头 Chrome（`--headless=new --screenshot --window-size=1440,900 --force-device-scale-factor=2`），脚本放 `scripts/shots.py`，不进公开仓库（`.publicignore`）。
- 截图：`item.jpg`（详情）、`pick.jpg`（选品），浅色，压到每张 ≤ 250 KB。总览只进动图和社交预览图（2026-11-25 实现时改：README 顶部已经是总览的动图，再放一张静态图重复）；无头 Chrome 只截首屏，表格里的离市价列不在画面里。
- 动图 `docs/images/demo.gif`：总览 → 详情 → 选品 → 盈亏四帧，每帧停 2.5 秒、0.4 秒淡入淡出，宽 960，ffmpeg 两遍调色板，≤ 3 MB。
- 截图前确认 demo 数据里有：BUFF 单、难以成交的单（变暗）、平台名。缺了就补 demo 数据（`skinpulse/demo/`）。

## 6. 发版

- `CHANGELOG.md`（Keep a Changelog 格式，中文）：`## [1.0.0] - 推送当天`（先写 2026-11-25，推送那天改成当天），按「推送 · 看板 · 价位与选品 · 多账号与 BUFF · 盈亏 · Windows」分组汇总现有功能。之后每次发版加一节。
- `pyproject.toml` 的 `version` 改成 `1.0.0`（现在是什么值，以文件为准）。
- 推送后打 tag `v1.0.0`，GitHub Release 正文 = CHANGELOG 那一节（用 `gh release create`，用户点头后做）。

## 7. 社区文件（`public/` 叠加层，只出现在公开仓库）

- `public/.github/ISSUE_TEMPLATE/`：
  - `bug.yml`：表单字段：发生了什么、期望、复现步骤、系统（下拉 macOS / Windows）、`git rev-parse --short HEAD`、脱敏日志；顶部醒目提示先脱敏。替换 `bug.md`。
  - `feature.yml`：想解决什么问题、设想的做法、愿不愿意自己提 PR。
  - `config.yml`：`blank_issues_enabled: false`；链接到 Discussions（提问）和 Security（私密报告漏洞）。
- `public/.github/PULL_REQUEST_TEMPLATE.md`：改了什么、为什么、怎么测的；勾选：测试全绿、没有带凭据、只读约束没破。
- `public/CODE_OF_CONDUCT.md`：参考 Contributor Covenant 2.1 改写的简版（CC BY 4.0，开头署名；2026-11-25 实现时改：不照搬译文）。举报渠道写仓库 Security 页的私密报告入口，不写个人邮箱。
- `CONTRIBUTING.md` 补一行链到行为准则和 Discussions。

## 8. 维护信号

- `.github/dependabot.yml`（私有仓库根目录，加进 `.publicignore`）：
  - `uv`（`/`）、`npm`（`/frontend`）、`github-actions`（`/`），每周一次，各自分组成一个 PR；
  - npm 忽略 `typescript` 的 minor 以上升级（坑 9：钉在 5.9）。
- `public/.github/workflows/codeql.yml`：`python`、`javascript-typescript`，push 到 main 和每周一次。公开仓库免费；私有仓库没有这个文件。
- 推送后在公开仓库设置里开 Dependabot alerts（用户点头后由我用 `gh api` 开，或用户在网页上开）。

## 9. 导出脚本

- `scripts/export_public.py` 的断链检查补上 HTML 写法：`src="…"`、`srcset="…"`、`href="…"` 里的相对路径也要存在（README 顶部全是 HTML）。
- 泄露检查照旧覆盖新文件；`tests/test_export_public.py` 补一个 HTML 断链用例。
- `.publicignore` 加 `scripts/shots.py`、`.github/dependabot.yml`。

## 10. 推送后的仓库设置（用户点头后）

- About：一句话介绍；Website 留空；Topics：`cs2`、`csgo`、`skins`、`youpin`、`buff`、`wechat`、`pushplus`、`monitoring`、`dashboard`、`python`、`fastapi`、`vue`。
- 开 Discussions、Private vulnerability reporting、Dependabot alerts。
- 上传社交预览图、组织头像（这两个只能在网页上传，由用户做）。
- `gh release create v1.0.0`。

## 11. 验收

- 公开快照：`export_public.py --check` 通过，快照目录里独立跑 pytest 全绿。
- README 中英文在 GitHub 浅色、深色主题下各看一次（推送前用本地 Markdown 预览看结构；`<picture>` 切换要推送后在 GitHub 上确认）。
- 看板 favicon 和顶栏 logo 在浅色、深色下各看一次（verify-dashboard）。
- 动图 ≤ 3 MB，截图都是新界面。
