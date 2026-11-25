# 看板改版设计规格（Dashboard Redesign Spec）

- 起草：2026-09-27
- 来源：intent（未公开）、[ADR-0012](../../decisions/0012-dashboard-vue-spa.md)、[ADR-0013](../../decisions/0013-echarts-replaces-server-svg.md)
- 视觉基准：claude.ai 画布「SkinPulse 看板视觉稿」第 3 版（求购总览、饰品详情、系统状态，浅色 + 深色）。实现和视觉稿有出入时，以本 spec 为准；本 spec 没写到的地方，以视觉稿为准。
- 替代主 spec §9 的页面部分。主 spec §9 只保留一句话，并链到这里。

标记约定同主 spec：**〔未实现〕** 表示还只是计划。每期完成后，把对应小节的标记去掉。

## 0. 一句话

把看板重写成 Vue 3 单页应用。每页先用一个短标题回答一个问题，再往下给细节。浅色和深色跟随系统，排名用金、银、铜三色，价格涨红跌绿，图表可悬停、可缩放，动效适度。构建产物提交进仓库，由 FastAPI 在原来的端口提供。Mac 和 Windows 拉了代码就能用。

## 1. 范围

**做：**
- 六个页面：总览、饰品详情、分析、大盘、简报、系统。原来的「设置」页并进「系统」页。
- 新接口和改造后的 `/api/*`。
- POST 接口的跨站防护。
- 网页粘贴悠悠 token。
- 设计系统：配色、字体、间距、动效。

**不做：**
- 手机适配。只保证窄窗口（≥ 1024 px）下布局不乱。2026-11-24 起底线放宽到手机宽度（390 px）整页不横向滚动：顶栏 ≤ 1360 px 时收间距，≤ 1180 px 时导航挪到第二行，≤ 760 px 时不吸顶；表格等内容区本身仍按桌面设计。
- 局域网访问和登录口令。
- 多用户。
- 前端的端到端自动化测试。改为每期在浏览器里对照视觉稿人工验收，见 §8。
- 改动监控、告警、简报的业务逻辑。

## 2. 架构

### 2.1 目录

```
frontend/                      # 前端源码（改前端时才需要 Node）
  package.json, package-lock.json, vite.config.ts, tsconfig.json, .nvmrc
  scripts/source-hash.mjs      # 构建时计算 src 的哈希，写进产物（§2.4）
  src/
    main.ts, App.vue, router.ts
    theme/                     # 设计令牌 → Naive UI themeOverrides + CSS 变量（§3）
    api/                       # 带类型的 fetch 封装，每个接口一个函数
    composables/               # useMeta, usePolling, useTheme, useReducedMotion, useCountUp …
    format/                    # money / time / rank / 标题文案，纯函数，vitest 覆盖
    components/                # AppShell, RankMedal, Sparkline, StatusDot, PriceLadder, EChart …
    pages/                     # Overview, Item, Analysis, Market, Brief, System
skinpulse/web/dist/            # 构建产物，提交进仓库（index.html、assets/*、.source-hash）
```

### 2.2 依赖

vue 3、vue-router 4、naive-ui、echarts 与 vue-echarts（按需引入）、@vueuse/core、lucide-vue-next（图标）、@fontsource/sora、@fontsource/jetbrains-mono。开发依赖：vite、typescript、vue-tsc、vitest。

- **不用 Google Fonts CDN**：国内访问不稳定，离线也打不开。数字和标题用自托管的 Sora 与 JetBrains Mono。中文用系统字体：Mac 上是 PingFang SC，Windows 上是 Microsoft YaHei。不打包中文字体，因为动辄几 MB。
- **不引入状态管理库**：每页数据由自己的 composable 获取，跨页面共享的只有 meta 和主题，用 `provide`/`inject` 传递。

### 2.3 提供方式

- FastAPI 把 `skinpulse/web/dist/assets/` 挂到 `/assets`，文件名带哈希，设置长缓存。
- 其余不以 `/api/` 开头、也不是静态文件的 GET 请求，一律返回 `dist/index.html`，并设 `Cache-Control: no-cache`，由前端路由接管。
- **过渡期**（一期到三期之间）：还没迁移的页面继续由 Jinja 路由提供。三期结束后已经没有 Jinja 页面；旧的 `/status`、`/settings` 302 到 `/system`。
- 如果 `dist/index.html` 不存在（开发时只拉了源码、还没构建），`/` 返回一页纯文本说明，告诉你该怎么构建，不报 500。
- 服务照旧只监听 `settings.web.host:port`，默认 127.0.0.1。

### 2.4 构建与跨平台

- 构建：`npm --prefix frontend ci && npm --prefix frontend run build`。先跑 `vue-tsc` 类型检查，再用 vite 打包，输出到 `skinpulse/web/dist/`，然后写入 `.source-hash`。脚本只用 node，不依赖 shell 特性，Mac 和 Windows 都能跑。`.nvmrc` 固定 Node 24。
- 开发：`npm --prefix frontend run dev`，vite 在 5173 端口，把 `/api` 代理到 `127.0.0.1:8766`。
- **产物和源码必须一致**：
  - `.source-hash` 是 `frontend/src/**`、`package-lock.json` 和 `vite.config.ts` 按路径排序后，逐个文件内容（换行统一成 `\n`）的 SHA-256。
  - pytest 用同一套算法重算一遍，和 `.source-hash` 对比。不一致就失败，并提示「改了前端没重新构建」。
  - 换行统一是为了在 Windows 上 `autocrlf=true` 也能算出同样的值。
- `.gitattributes` 加一行 `skinpulse/web/dist/** -text`：产物按字节原样保存，Windows 检出时不会改换行。

## 3. 设计系统

### 3.1 配色令牌（与视觉稿第 3 版一致）

| 令牌 | 浅色 | 深色 | 用途 |
|---|---|---|---|
| bg | #EEF0F3 | #0B0D11 | 页面底色（深色顶部叠一层很淡的青色光晕） |
| surface | #FFFFFF | #13161D | 卡片、表格 |
| line / lineSoft / lineStrong | #DCE0E6 / #EBEEF2 / #C5CBD4 | #232833 / #1C2029 / #343B48 | 卡片边框 / 表格内分隔线 / 输入框边框 |
| text / text2 / text3 | #0E1116 / #3A414D / #586070 | #EEF1F5 / #B2B9C5 / #8C94A3 | 正文 / 次要 / 注释（白底上 ≥ 5.5:1） |
| accent | #0A6B69 | #46D6D3 | 主色：主按钮、进度条、我的价 |
| gold / silver / bronze | #8A5700 / #475569 / #914E24 | #F5C451 / #C3CEDF / #E39A6B | 第 1 / 2 / 3 名；★ 用 gold |
| up / down | #C8313A / #0E7A4F | #FF6B6B / #3DD39B | 涨红跌绿（价格）。排名变化另算：上升用 ok（绿）、下降用 up（红）——down 也是绿色，不能拿来表示「掉了名次」 |
| ok / warn / idle | #0C7A4E / #A1480C / #8A919C | #3DD39B / #FFA05C / #8C94A3 | 健康、需要留意、未配置 |
| amber | #8A5300 | #FFB547 | 差价过滤（2026-11-25 起「难成交」改用虚线灰胶囊，见难以成交 spec §5） |
| stattrak | #A94A0C | #FF8A3D | StatTrak™ |

- 颜色从不单独表达含义：排名同时写数字，涨跌同时写正负号和箭头，状态同时写文字。
- 所有文字对比度 ≥ 4.5:1；24 px 以上的大字 ≥ 3:1。

### 3.2 字体与排版

- **数字**：一律 JetBrains Mono，开启 `tabular-nums`，右对齐。
- **大数字和标题中的数字**：用 Sora。
- **页面标题**：34 px / 700；卡片标题 16–18 px / 700；正文 14 px；注释 12–12.5 px。
- **间距**：页面左右 56 px；区块之间 32–40 px；卡片内边距 24–28 px。
- **圆角**：卡片 18、小卡片 14、按钮 10、标签 6。

### 3.3 动效

| 场景 | 效果 | 时长 / 缓动 |
|---|---|---|
| 首次加载 | 骨架屏 → 内容淡入并上移 8 px，各区块依次错开 40 ms | 240 ms，ease-out |
| 汇总数字 | 从 0 滚动到目标值；刷新时从旧值滚到新值 | 600 ms，ease-out-cubic |
| 自动刷新后排名变了 | 该行底色闪一下（上升用 gold 淡色，下降用 down 淡色），名次数字翻牌 | 1.6 s 淡出 |
| 行展开 / 收起 | 高度过渡；价格轴上的圆点从左侧滑到位 | 200 ms |
| 筛选、搜索 | 行进出用淡入淡出加位移（`TransitionGroup`） | 160 ms |
| 图表 | ECharts 入场绘制；切换时间范围时换一张新图、重新播入场动画（不做新旧图之间的变形：根数不同时 ECharts 按下标对点，均线会被拉成横线、新 K 线直接冒出来，2026-09-28 用户录屏） | 400 ms |
| 明暗切换 | 颜色变量过渡 | 200 ms |
| 悬停 / 按下 | 行底色轻微变化；按钮缩到 0.98 | 120 ms |

`prefers-reduced-motion: reduce` 时，以上全部关闭，只保留颜色的瞬时变化。

## 4. 页面

所有页面共用 **AppShell**，由以下部分组成：
- 顶栏：Logo、导航（总览 · 分析 · 大盘 · 简报 · 系统）；
- 监控状态点：绿 / 黄 / 红加最近一轮时间，点击跳到系统页；
- ⌘K 命令面板：搜索饰品名直接进详情页，也能跳转页面；
- 明暗切换：三态，跟随系统 → 浅 → 深，选择记在 localStorage；
- token 提醒：剩余 ≤ 2 天时，顶栏出现一个 warn 色的「token 还剩 N 天」链接，指向系统页。

时间一律按 `/api/meta` 返回的 `timezone`，用 `Intl.DateTimeFormat` 格式化：
- 当天的时间只写 `HH:mm`；
- 其他日子写 `MM-DD HH:mm`；
- 相对时间写成「3 分钟前」。

金额保留两位小数，缺失写「—」，**绝不写 0**；在售数为 0 写「无人在售」。

### 4.1 求购总览 `/`（一期已实现，2026-09-27）

> 「排名 / 价位」切换和价位视图见 [价位 spec §7.1](2026-09-27-price-position.md)（2026-09-28 一期完成）。
>
> 最上面的「暂停中的求购单」卡片（充值前检查）见[暂停单 spec §5](2026-11-24-paused-order-check.md)（2026-11-24）。

**要回答的问题**：「现在排得最好的是哪几张？」

1. **标题**：
   - 把排名 1 到 `brief.top_rank`（现在是 2）里数量不为 0 的名次列出来，格式「N 张求购排名第 1、M 张求购排名第 2」，数量为 0 的名次省略。
   - 全都为 0 时写「暂无求购进入前 {top_rank}」，副标题改成「排得最好的是第 K 名（N 张）」。
   - 否则副标题写：暂无第 1 名时写「暂无第 1 名」；有第 1 名时不写副标题。标题和焦点卡片都不描述推送的事（用户 2026-09-27）。
   - 标题右边是「立即同步」按钮。
2. **焦点卡片**（最多 3 张）：
   - 先放排第 1 名的单，卡片标金色「已是第 1」；再放排第 2 名的单，按「榜首价 / 我的价」从小到大排。被差价过滤的单不进焦点卡片（2026-11-25 起：难以成交的单都不进，见难以成交 spec §4）。
   - 卡片内容：奖牌、饰品简称、「StatTrak™ · 磨损 · 7 天最好第 X 名」、7 天排名迷你线；最大的数字是「离榜首还差」；另有我的价和榜首价、进度条（我的价 ÷ 榜首价）。
   - 点击卡片进详情页。
3. **提示行**（三条，比焦点区低一级）：
   - 前 3 名的张数和 7 天前比（带迷你趋势线，变少时用 warn 色）；
   - 今天名次升了几张、降了几张（按每天最后一个快照比）；
   - 被差价过滤、不会告警的张数和占比（2026-11-25 起改为「N 张难以成交」，分离第 1、离市价两项）。
4. **全部求购表格**：
   - 列：饰品（★ 前缀 + 简称；第二行是 StatTrak™ · 磨损 · 队列）、排名（奖牌 + 7 天迷你线 + 今天的升降）、价格、离第 1、展开按钮。
   - **价格梯**（2026-10-09 改，intent（未公开））：原来的「我的价 / 榜首价 / 差价 / 悠悠在售」四列合成两列。
     - 「价格（我的 · 榜首 · 在售）」：上面一行三个数字（在售带件数，没人在卖、没查到照 `sellLabel`）；下面一条横轴，0 到三个价里最高的那个：灰底到我的价、我的价到榜首价那段按档位上色、菱形是榜首价、竖线是悠悠在售最低（没有在售价不画）。
     - 「离第 1」：差价占榜首价的百分比（大字、上色）、金额、档位标签；可排序（已是第 1 排最前，算不出的排最后）。档位：≤ 5% 很近（accent）、5%～15% 一般（info 蓝）、> 15% 较远（warn）、被差价过滤的「不告警」（amber 斜纹，写倍数）、已是第 1、交易中、没有榜首价写「—」。
     - 新增颜色令牌 `info` / `infoSoft`（和 MA30 同色）。
     - 排名列的排序（2026-11-20，intent（未公开））：名次相同的按「离第 1」从小到大，算不出差距的排在同名次最后；降序只反转名次，同名次里仍是差距小的在前，没有排名的始终在最后。
     - 「离第 1」筛选（同上，做法 A）：品类按钮右边一个「离第 1」按钮，面板里是全部 / 很近 / 一般 / 较远 / 不告警，带张数（按档位以外的筛选算），和表格里的档位标签一一对应；已是第 1、交易中、算不出差距的单在选了档位后不显示。URL 参数 `gap=near|mid|far|gated`，不认识的值当作没选。
   - 工具栏：排名分段（前 2 名 / 前 3 名 / 第 4 名以后 / 全部，**默认前 2 名**，用自己写的分段控件，圆角焦点框）、「隐藏不会告警的」开关（2026-11-25 起改为「隐藏难以成交的」，另加「离市价」列，见难以成交 spec §5）、搜索框。筛选条件同步到 URL 查询参数（默认值不写进网址），刷新页面后保留。
   - **品类筛选**（2026-10-09 加，intent（未公开））：排名分段左边一个「品类」按钮，点开是两栏面板：左栏「全部品类」+ 大类，右栏是停在（或选中）枪、刀、手套大类时的「全部××」+ 具体武器 / 刀型 / 手套类型。每项带张数，张数按**除品类外**的其他筛选（分段、隐藏、搜索）算；张数为 0 的项不显示，当前选中的除外。选中后按钮变成「步枪 › AK-47 ×」（accent 浅底），× 清除。URL 参数 `cat=<大类>`、`sub=<第二层英文键>`；不认识的值当作没选。只在「排名」视图，价位视图不受影响。键盘：Esc 关面板，方向键在当前栏里移动，→ / ← 在两栏之间切换。
   - Naive UI 的 `n-data-table` 加虚拟滚动；表头可排序，默认按排名升序。
   - 展开行分三栏：近 7 天排名折线、价格位置轴（我的价 / 榜首 / 在售最低）加一句是否会推送、别处买盘 / 队列 / 检查时间 / 两个跳转按钮。
5. **自动刷新**：每 60 秒拉一次 `/api/overview`，页面在后台时暂停。和上一份数据比较，排名变了的行按 §3.3 高亮。

### 4.2 饰品详情 `/item/:templateId`（二期已实现，2026-09-27）

> 「当前价位」卡片见 [价位 spec §7.2](2026-09-27-price-position.md)（2026-09-28 一期完成）。

**要回答的问题**：「我在这件饰品上的求购排第几、还差多少、价格处在什么位置？」

- **面包屑**：求购总览 › 饰品详情。
- **标题**：饰品简称（有单时取悠悠的 `commodity_name`，和总览一致；没单时用 SteamDT 的中文名）；第二行是 StatTrak™、磨损和英文市场名；右边是「在悠悠打开」。
- **左栏「我的求购」卡片**：奖牌加「我的求购排名第 N」，最大的数字是「离榜首还差」；另有进度条（下面一句只讲距离，不讲推送）、我的价 / 悠悠榜首 / 悠悠在售最低 / 别处买盘，以及近 7 天排名折线。同一件饰品挂了多张单时，卡片可以切换。
- **右栏「价格走势」**：
  - ECharts K 线，涨红跌绿，带 MA7 和 MA30。
  - 我的求购价画成一条 accent 色虚线，左端标签「我的求购 x.xx」。
  - 1 月 / 3 月 / 1 年 / 全部切换（前端切片，不再请求）。「全部」画**周线**（2026-09-27 用户要求）：单品从 2022-07 起，均线换成 MA4 / MA13 周（约一个月、一个季度）；SteamDT 缺数据的几段画竖虚线、标「缺 N 周」，不悄悄挤掉；周线取不到时不显示这个选项。十字光标，浮层显示开、收、高、低、MA7、MA30。缩放：拖动图下方的滑块、触控板双指捏合，或在图上拖动平移；滚轮留给页面滚动。纵轴范围在缩放时保持不变。
  - 影线截断沿用主 spec 的规则：纵轴范围由后端按窗口各算一份（`analysis/kline.py`），前端再把我的求购价框进去；有截断时，图上方注明截断了几根、极值是多少。
  - 图上方写最新价、7 日和 30 日涨跌、MA7 和 MA30、趋势。
- **各平台比价**（HTML 画，不用 ECharts：每行旁边就是精确数字，读屏能按行读）：
  - 每个平台一行：条形表示在售最低价，gold 圆点表示最高求购价，一条竖线标出我的价。
  - 悠悠有品那一行加粗；缺的数据写「—」。
  - 附 Steam 的说明（含手续费、不是同一个市场）。

### 4.3 分析 `/analysis`（二期已实现，2026-09-27，视觉稿第 2 版）

**要回答的问题**：「整体排名在变好还是变差？」

- **标题**：「前 3 名 N 张，比 MM-DD 少/多 M 张」，和本周（今天之前 6 天）最早有数据的那天比，只有变化的数字上色（少了用 up 红、多了用 ok 绿）；本周没有数据时只写张数。副标题：今天升了几张、降了几张。
- **近 30 天每天收盘**时第 1 名、前 2 名、前 3 名的张数，画成折线（每天取各单当天最后一条有名次的快照，已撤的单在它挂着的日子里照样算）。
  - **横轴只排有数据的日子**（用户 2026-09-27：服务没跑的日子不要占版面）。中间连着没数据的几天压成一格，标签写「停 N 天」、浅底色；断口两边各画一条淡虚线。开头、结尾没数据的日子不画。
- **排名分布**：第 1 到 10 名各一档，外加 11–20、21–50、51+。1、2、3 名分别用金、银、铜色。查过但不在抽样里（`beyond`）和还没检查的单不进任何一档，在说明里单独写。
- **今天名次变化最大的单**：升得最多的 10 张、降得最多的 10 张（和昨天收盘比），点击进详情页。
- 每 5 分钟自动刷新一次（只读数据库）。

### 4.4 大盘 `/market`（二期已实现，2026-09-27，视觉稿第 2 版）

**要回答的问题**：「CS2 大盘今天怎么样？」

- **主卡片**（一张，左右两栏）：
  - 左：大号指数加今天的涨跌幅（「大盘 {指数} {涨跌幅}」），比昨天的差值；7 天 / 30 天 / 1 年涨跌做成紧凑的小标签（按内容宽，不撑满一行）；「一年区间里的位置」条：按收盘价取一年最高、最低，标出现在的位置和离高点多少。
  - 右：最近 25 小时走势，折线加渐变填充。
- **日线卡片**：一年日线 K 线加 MA7 / MA30，默认 1 年，可切 1 月 / 3 月，以及「全部」（大盘周线，从 2023-07 起，MA4 / MA13 周）；和详情页共用同一个组件（不画求购价）。周线和日线一起在打开页面时取（缓存同为 6 小时），切到「全部」不再请求。
- 注明缓存频率：指数最多 10 分钟取一次，日线最多 6 小时。大盘接口的配额很紧，页面只在打开时请求一次、不轮询，切换范围和缩放都不会重新取数。

### 4.5 简报 `/brief`（三期已实现，2026-09-27）

**要回答的问题**：「这几天的简报都发了吗？」

- 标题写成「最近 30 天发了 N 条」。
- 时间线：每天一个节点，已发、补发、没发（附原因）、无记录分别用不同颜色。
- 点开节点，按表格样式展示当天那张前 N 名表，不再显示纯文本。选中的日子记在网址里（`?day=`）。
  - 表从 2026-09-27 起在发送时存进 `brief_log.rows_json`（发送那一刻的样子）；更早的记录按正文的固定格式**严格**解析回表格，任何一行对不上就原样显示正文（09-22 那条旧版日报就是这样）。
  - 过了该发的时刻 1 小时以后才发出的算「补发」。

### 4.5b 选品 `/pick`（2026-10-10，价位三期）

见[价位 spec §9b.4](2026-09-27-price-position.md)。导航在「简报」和「系统」之间。

### 4.6 系统 `/system`（三期已实现，2026-09-27；旧的 `/status`、`/settings` 跳到这里）

**要回答的问题**：「服务健康吗？」

- **标题**：健康时显示对勾加「一切正常」；否则写最要紧的一件事，按严重程度：token 已失效 > 悠悠风控暂停到 HH:mm > 监控未运行 > PushPlus 最近一次推送失败 > 上一轮有错误。副标题是最近一轮的摘要。（「连续失败 N 次」做不了：`system_status` 只记累计次数和最近一次成败。）
- **四个通道卡片**：悠悠匿名、悠悠账号、PushPlus、ntfy。每张卡片有状态灯（ok / warn / error / idle）、最近成功时间和一句说明。ntfy 没配置时显示 idle「未配置」。
- **悠悠账号卡片**：
  - token 剩余天数环形图和到期时间；
  - **「更新 token」**：打开一个对话框，粘贴 authorization（输入框不回显，使用 `type=password`、`autocomplete=off`，提交后立即清空）→ 校验 → 保存 → 显示昵称和到期时间（一两秒），这一轮监控马上开始，开头会先同步求购单。状态：粘贴、校验中、成功、格式不对、无效或已过期、暂时无法验证。校验用一个临时 client，通过之前正在用的 token 不动；换上后不用重启。
  - 附一段怎么从浏览器里复制 token 的简短说明。
- **排名核对卡片**：近 24 小时的一致率、抽查次数、最近一次不一致是什么情况。
- **最近推送**：最近 10 条，带奖牌、饰品名、事件、状态；有送出、失败、被抑制三项合计；注明「送出只代表 PushPlus 收下了」。
- **每日简报**：最近 7 天的格子，点击进简报页。
- **运行环境**：运行方式（由 LaunchAgent 拉起时写「登录后自动启动」「接电源时防睡眠」，靠 `skinpulse-launchd.sh` 导出的 `SKINPULSE_SUPERVISOR=launchd` 判断）、服务启动时间和已经运行多久、系统平台、数据库大小（含 `-wal`）。
- **重启服务**（2026-11-23，intent（未公开））：由 LaunchAgent 拉起、而且装好的依赖和 `uv.lock` / `pyproject.toml` 对得上（`skinpulse/depcheck.py`）时，「运行环境」底部有「重启服务」；标题是「代码已更新，服务还没重启」时按钮放在标题右边（主色）。点了先确认，再 `POST /api/system/restart`：服务先回应，0.5 秒后给自己发 SIGTERM，走 `run` 的正常退出，KeepAlive 拉起；页面每 2 秒探一次 `/api/status`，`started_at` 变了就刷新，60 秒没回来就提示去终端看 `launchagent.sh status`。手动 `run`、选品正在跑、依赖要先 `uv sync` 时接口返回 409。依赖变了时标题副标题改写成「先在终端跑 uv sync，再 restart」，不给按钮。开机自启不做开关（卸载 LaunchAgent 会把正在回应的服务一起停掉），只写怎么在终端里关。
- **参数设置**（折叠）：`[polling]`、`[alerts]`、`[brief]` 做成表单，每项带中文说明、单位和范围校验，不常改的 6 项收在「更多」里。保存前列出改了哪几项，有不合要求的不能保存；保存时走 `POST /api/settings`，改完立即生效（简报的开关和时间也会重排），并提示改了哪几项。
- 每 60 秒刷新一次。

## 5. 接口

**通用约定：**
- 金额是 JSON 数字（从后端的 Decimal 转成 float，只用于显示）或 `null`。
- 时间是 UTC 的 ISO 8601 字符串，带 `Z`。
- 业务判断（差价过滤、排名、升降）一律在后端用 Decimal 算好，前端只负责展示。
- **token、key、topic、手机号不出现在任何接口的返回里。**

| 接口 | 期 | 内容 |
|---|---|---|
| `GET /api/meta` | 一 | `timezone`、`now`、`service`（最近一轮的时间、检查张数、错误数、是否暂停、是否健康）、`token`（是否有效、`expires_at`、剩余天数）、`thresholds`（`enter_rank`、`top_rank`、`max_gap_ratio`） |
| `GET /api/overview` | 一 | `headline`（名次 → 张数）、`focus[]`、`insights`（前 3 名今天和 7 天前的张数、7 天序列、今天升 / 降张数、被过滤张数）、`orders[]`（每张单：`purchase_no`、`template_id`、`title`、`star`、`stattrak`、`wear`、`queue`、`rank`、`rank_7d[7]`、`rank_change`、`my_price`、`top1_price`、`gap`、`gap_ratio`、`gated`、`sell_min`、`sell_depth`、`ref_bid`、`ref_bid_platforms`、`remaining_qty`、`checked_at`、`category`（大类键）、`subcategory`（第二层英文键或 null）、`subcategory_label`（中文显示名或 null））、`category_groups[]`（`key`、`label`，大类的顺序和中文名，只有一份在后端 `analysis/category.GROUPS`） |
| `GET /api/analysis` | 二 | `summary`（今天前 3 名张数、本周最早有数据那天的对比、今天升 / 降张数）、`distribution[]`（`label`、`min`、`max`、`count`）、`unranked`（`beyond`、`unchecked`）、`daily[30]`（`date`、`rank1`、`top2`、`top3`，没数据的日子为 null；`total`）、`movers`（升 / 降各 10 张，含 `prev_rank`） |
| `GET /api/item/{id}` | 二 | `title`、名称字段、`market_hash_name`、`youpin_url`、`orders[]`（同总览）、`market{configured,mapped}`、`prices`（`rows[]`：件数只在该侧有价格时给，否则 null；`fetched_at`、`stale`、`error`）、`kline`（`bars[[ts,o,c,h,l]]`、`ranges{1m,3m,1y: {lo,hi,clipped,extreme}}`、`signals`、`fetched_at`、`stale`、`error`）、`weekly`（`bars`、整段一份 `range`、`fetched_at`、`stale`、`error`；取不到时 bars 为空、不影响日线）。模板既没有单也没有映射时 404 |
| `GET /api/market` | 二 | `configured`、`index`（`value`、`diff`、`diff_pct`（原样的百分数）、`updated_at`、`history[[ts,v]]`、`fetched_at`、`stale`、`error`）、`kline`（同上，外加 `year_from`、`year_change_pct`）、`weekly`（同 `/api/item`） |
| `GET /api/brief` | 三 | `rows[]`：`date`、`decision`、`kind`（sent / late / not_sent / failed / gap）、`label`、`summary`、`delivery`、`sent_at`（UTC）、`table`（每行：名次、简称、StatTrak、磨损、我的价、榜首或 `is_me`、在售、变动）或 `body`（原文）、`note`；`summary.sent_30d`；`next_at` |
| `GET /api/status` | 三 | `headline`（`issue`、`paused_until`、`pushplus_error_at`）、`service`（同 `/api/meta`）、`channels[]`（`state`、`configured`、最近成败时间、暂停到）、`account`（含 `expires_at`、`days_left`）、`verification`、`notifications`（合计 + 最近 10 条）、`briefs[7]`、`brief`（开关、时间、前 N）、`runtime` |
| `GET/POST /api/settings` | 三 | POST 只收 JSON，只收 `polling` / `alerts` / `brief` 三组，组里不认识的字段也 422；返回 `{settings, changed}`；422 的 `detail` 是 `[{field, message}]`（中文） |
| `POST /api/youpin/sync` | 已有 | 不变 |
| `GET /api/paused`、`POST /api/paused/check` | 2026-11-24 | 充值前检查暂停单，见[暂停单 spec §5.3](2026-11-24-paused-order-check.md) |
| `POST /api/youpin/token` | 三 | 请求体 `{token}` → 规范化（`adapters/youpin/token.normalize_token`，CLI 共用）→ 用临时 client（和正在用的同一个设备身份）`get_user_info` 校验（限流才重试，3 次）→ 换上正在用的 token、存进钥匙串 → 让下一轮监控马上开始（它开头会先同步求购单；接口里不同步：429 张要分 11 页、每页歇 2.5 秒，实测用户要干等 30 多秒）→ 返回 `{nickname, user_id, expires_at, round_started}`。格式不对 400（不请求悠悠、不占限流名额）；无效 422；暂时无法验证（悠悠或网络）503；限流 429；**都不保存**；其他异常是代码问题，照常 500。请求体不回显，日志只记结果类别。 |
| `POST /api/youpin/login/*` | 一期删除 | 短信登录走不通（research §2），接口和表单一起删；CLI 的 `login` 命令保留 |

**实现要点：**
- **7 天排名和升降**：对全部活跃单做一次批量查询：日界在 Python 里按 `local_day_bounds` 算，SQL 用 CASE 归日、按（单号，第几天）取最后一条有名次的快照。禁止逐单查询。快照每轮每单一条（7 天约 32 万行），冷算约 0.4 s，所以结果按「数据库 + 日期 + 最新快照时间 + 单子集合」做进程内缓存：每轮之后第一次请求约 0.4 s，其余约 40 ms。`rank_snapshot(purchase_no, ts)` 的索引早就有了。
- **最近一轮摘要**：由调度器在每轮结束时写进 `Runtime.last_round`，只存在内存里，重启后为空。`/api/meta` 读的就是它；为空时退回 `system_status` 里各通道的 `last_ok_at`。
- **token 到期时间**：只解 JWT 的 payload 读 `exp`，不校验签名，也不返回 token 本身。解析失败时 `expires_at: null`。
- **饰品简称**：复用 `core.messages.short_item_name`。StatTrak™、★ 和磨损从 `commodity_name` 里拆出来，拆分逻辑写成纯函数，用 fixture 测试。

## 6. 安全

所有写接口（POST）都要满足下面三条，否则返回 403。这三条在**一期**就做：

1. `Host` 必须是 `127.0.0.1:{port}`、`localhost:{port}` 或 `[::1]:{port}`，用来防 DNS 重绑定。GET 请求同样要校验。
2. `Origin` 头存在时，必须是同一个来源；不存在时（比如 curl）放行。浏览器发出的跨站请求一定会带 `Origin`。
3. `Content-Type` 必须是 `application/json`。这会让跨站的表单提交和普通请求全部失效，浏览器发不出无需预检的 JSON 请求。

另外：
- `POST /api/youpin/token` 限流：每 5 秒最多 1 次，失败 5 次后锁 1 分钟。请求体和异常信息都不写日志，只记录结果类别。
- 返回头：`X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer`，外加一条只允许同源的 CSP（ECharts 用 canvas 渲染，不需要放开 `unsafe-eval`）。三期删掉 Jinja 页面后，CSP 由安全中间件加在**所有**响应上。

## 7. 分期

| 期 | 分支 | 内容 | 验收 |
|---|---|---|---|
| 一 | `feat/dashboard-p1` | 前端脚手架、构建和一致性检查、设计令牌、AppShell（导航、状态点、明暗切换、⌘K 先只做页面跳转）、总览页；`/api/meta`、`/api/overview`；§6 安全中间件；删除短信登录接口和表单；FastAPI 提供前端页面；README 开发说明 | 浅色和深色都对照视觉稿过一遍；筛选、搜索、展开、自动刷新都能用；关掉动态效果后没有动画；pytest 全绿；**用户在 Windows 上拉代码后能打开新总览** |
| 二 | `feat/dashboard-p2` | 详情、分析、大盘三页；ECharts；`/api/item`、`/api/analysis`、`/api/market`；纵轴范围和截断改由后端返回；⌘K 支持搜饰品 | 同上；K 线的悬停、缩放、切换范围都能用；ECharts 不出现在首页加载的包里 |
| 三 | `feat/dashboard-p3` | 简报、系统两页；token 粘贴；设置表单；`/api/status`、`/api/brief`、`POST /api/youpin/token`；删除剩下的 Jinja 模板、HTMX 和 `static/`（`web/charts.py` 二期已删） | 同上；粘贴一个真实 token 走一遍完整流程；全局搜不到 `TemplateResponse` |

每期合并前，照例跑 `/code-review` 和 REVIEW.md 的清单。

## 8. 测试

- **后端（pytest）**：
  - 每个接口测返回的形状和关键规则：标题的计数、焦点卡片的选择和排序、差价过滤（和 `Monitor._is_eligible_for_alert` 保持一致）、7 天排名和升降、缺失值是 null 而不是 0；
  - 安全中间件：错误的 Host、跨站的 Origin、非 JSON 请求一律 403；
  - token 接口：成功、无效、暂时无法验证三种情况；返回里和日志里都不能出现 token；限流生效；
  - 产物一致性（§2.4）；
  - 前端页面兜底：`/` 返回 index.html，`/api/不存在的路径` 返回 404 JSON。
- **前端（vitest）**：`format/` 下的函数（金额、带时区的时间、相对时间、标题文案、排名升降文案）、均线计算、排名迷你线的坐标。
- **人工验收**：每期在 Claude 内置浏览器里，分别用浅色、深色和「减少动态效果」三种模式截图，对照视觉稿。

## 9. 风险

| 风险 | 缓解 |
|---|---|
| 改了前端忘了重新构建 | §2.4 的哈希检查会让 pytest 失败 |
| 产物体积膨胀 | 首页 JS ≤ 350 KB（gzip）；ECharts 按路由拆分；构建输出每个包的大小，超出预算就报警 |
| 用了新浏览器才有的 API，Windows 上的旧浏览器打不开 | vite 的构建目标设为 `es2020`；三期验收时在 Windows 的 Edge / Chrome 上各打开一次 |
| 429 行加动效导致卡顿 | 表格用虚拟滚动；只给变化的行做高亮；列表进出的动画只在筛选时启用 |
| 跨站请求改设置、换 token | §6 |

## 10. 未决事项

- 总览要不要显示「最近一次推送」的小条？视觉稿第 3 版里没有，先不做，看用户反馈。
