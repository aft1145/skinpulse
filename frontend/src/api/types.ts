// /api/meta 和 /api/overview 的形状（看板改版 spec §5）。金额是数字或 null——null 就是「没有」，
// 绝不当 0 用；时间是带 Z 的 UTC 串，显示前按 Meta.timezone 格式化。

export type Issue = 'stale' | 'paused' | 'errors' | 'token_invalid' | 'not_logged_in' | 'sync_slow' | 'buff_paused'

/** 一个悠悠号（多账号 spec §7）。不含 token，只有到期时间 */
export interface Account {
  id: number
  /** youpin / buff（BUFF spec §8）；旧后端没有，当作 youpin */
  platform?: 'youpin' | 'buff'
  /** 显示名：简称，没有就是昵称 */
  label: string
  /** 账号切换、表格标签上的写法：默认同 label；[dashboard] platform_names 打开时是「悠悠」「BUFF」（旧后端没有） */
  tag?: string
  custom_label: string | null
  nickname: string | null
  enabled: boolean
  logged_in: boolean
  token: { valid: boolean; expires_at: string | null; days_left: number | null }
  last_sync_at: string | null
  paused_until: string | null
  /** 现在挂着的求购单数 */
  orders: number
}

export interface LastRound {
  at: string | null
  checked: number
  alerts_sent: number
  errors: number
  paused: boolean
  gap_gated: number
  /** 离市价太远没进告警的单数（难以成交 spec §3）；旧后端没有 */
  market_gated?: number
  notify_failed: number
}

export interface Meta {
  timezone: string
  now: string
  service: {
    healthy: boolean
    issues: Issue[]
    last_round_at: string | null
    last_round: LastRound | null
    paused_until: string | null
  }
  token: { valid: boolean; expires_at: string | null; days_left: number | null }
  thresholds: { enter_rank: number; top_rank: number; max_gap_ratio: number; max_market_ratio?: number; min_market_gap?: number }
  links: { youpin_item: string }
  /** 「开始使用」卡片的四步（首次使用 spec §2.1）；旧后端没有这个字段 */
  setup?: { youpin: boolean; push: boolean; first_round: boolean; market: boolean }
  /** `skinpulse demo` 的假数据 */
  demo?: boolean
  /** 悠悠号，启用的在前（多账号 spec §7）；旧后端没有这个字段 */
  accounts?: Account[]
}

export interface Order {
  purchase_no: string
  template_id: number
  /** 这张单是哪个号的（多账号）；旧后端没有 */
  account_id?: number
  /** 同一个子队列里，我另一个号的单排在这张前面 */
  own_ahead?: { label: string; price: number | null } | null
  /** BUFF 单（BUFF spec §8）：平台、名次前面挡路的条件单张数、BUFF 商品页；旧后端没有 */
  platform?: 'youpin' | 'buff'
  cond_ahead?: number | null
  buff_url?: string | null
  title: string
  star: boolean
  stattrak: boolean
  tag: string | null
  queue: string
  rank: number | null
  beyond: number | null
  checked: boolean
  rank_7d: (number | null)[]
  rank_change: number | null
  my_price: number | null
  top1_price: number | null
  gap: number | null
  gap_ratio: number | null
  gated: boolean
  /** 难以成交的原因（难以成交 spec §4）：离第 1 太远 / 离市价太远；旧后端没有这几个字段 */
  unreachable?: 'top' | 'market' | null
  /** 市价：悠悠在售最低（sell），没人在卖时用别处买盘（bid）；不知道为 null */
  market?: number | null
  market_src?: 'sell' | 'bid' | null
  market_gap?: number | null
  market_ratio?: number | null
  sell_min: number | null
  sell_depth: number | null
  ref_bid: number | null
  ref_bid_platforms: string | null
  ref_bid_pct: number | null
  ref_bid_at: string | null
  remaining_qty: number
  checked_at: string | null
  sell_checked_at: string | null
  /** 价位（价位 spec §6.1）。没估值、或后端还是旧版本时为 null / 没有这个字段 */
  value?: OrderValue | null
  /** 悠悠显示「交易中」（rank 是 *）从哪一刻开始；这时 rank 是 null（成交推送 spec §6） */
  in_trade_since?: string | null
  /** 市价提醒（市价提醒 spec §4）：榜首高于在售 / 我的价贴近在售 / 两者都有；后端旧版本没有这个字段 */
  market_flag?: 'inverted' | 'near' | 'both' | null
  /** 品类（看板 spec §4.1）：大类键、第二层英文键和中文名。后端还是旧版本时没有这几个字段 */
  category?: string
  subcategory?: string | null
  subcategory_label?: string | null
}

export interface CategoryGroup {
  key: string
  label: string
}

/** 一次成交（成交推送 spec §6）。filled_at：exact 时是悠悠给的成交时间，否则是我们发现的时间。 */
export interface Fill {
  purchase_no: string
  template_id: number
  account_id?: number
  title: string
  tag: string | null
  star: boolean
  stattrak: boolean
  price: number | null
  qty: number
  filled_at: string
  exact: boolean
  /** 推测成交：交易中过、之后悠悠哪个列表里都没有（intent 2026-11-23）；filled_at 是被卖的时刻。老后端没有这个字段 */
  likely?: boolean
  detected_at: string
  notice: string | null
  /** 这次成交靠哪条推送知道的；null = 没有推送（回填的、超时的） */
  notice_kind: 'IN_TRADE' | 'FILLED' | null
  notice_at: string | null
}

export interface Fills {
  fills: Fill[]
  total_30d: { count: number; amount: number; largest: { title: string; price: number | null } | null }
  /** 每天的成交件数（本地日期），没有成交的日子也列出来 */
  daily: [string, number][]
}

/** 一张单的价位和扣费差价。比率都是小数（0.15 = 15%），ex30 是百分点 / 100。null 就是算不出来，绝不当 0。 */
export interface OrderValue {
  as_of: string
  bars: number
  pct: number | null
  pct_label: '一年低位' | '一年中位' | '一年高位' | null
  lo: number | null
  hi: number | null
  cur: number | null
  trend: '上升' | '横盘' | '下降' | null
  rel: '跑赢' | '持平' | '跑输' | null
  c30: number | null
  c90: number | null
  b30: number | null
  b90: number | null
  ex30: number | null
  vol7: number | null
  vol_label: '波动小' | '波动中' | '波动大' | null
  q10: number | null
  liq: '好出手' | '一般' | '难出手' | null
  listed: number | null
  spread: number | null
  exit: number | null
  exit_where: 'YOUPIN' | 'BUFF' | null
  exit_sell: number | null
  m_my: number | null
  m_top: number | null
  m_bad: number | null
  conditional: boolean
  /** 求购价上限（坏情况下扣完目标差价和最低利润还不亏）；二期起才给 */
  cap?: number | null
  /** 二期建议（价位 spec §9.5）；配置关掉、或后端还是旧版本时为 null / 没有 */
  advice?: Advice | null
}

export type AdviceKind = 'cancel' | 'lower' | 'raise' | 'hold' | 'none'
export interface Advice {
  kind: AdviceKind
  label: string
  /** 触发它的规则，一句话，带数字 */
  rule: string
  /** 降到 / 加到的价；其余为 null */
  price: number | null
}

export interface ValuationSummary {
  as_of: string
  computed_at: string
  snapshot_date: string | null
  counts: {
    take: number; take_loss: number; low: number; fall: number; hard: number; evaluated: number; insufficient: number
    /** 各建议的张数；建议关掉（或后端旧版本）时为 null / 没有 */
    advice?: Record<AdviceKind, number> | null
  }
  target_margin: number
  fees: { YOUPIN: number; BUFF: number; withdraw: number; undercut: number }
}

/** 详情页的「当前价位」：外加近 90 天单品和大盘收盘（[日期, 单品, 大盘]，缺那天为 null） */
export interface ItemValuation extends OrderValue {
  series90: [string, number | null, number | null][]
  computed_at: string
  snapshot_date: string | null
  target_margin: number
  fees: ValuationSummary['fees']
}

export interface Headline {
  counts: Record<string, number>
  top_rank: number
  best: { rank: number; count: number } | null
}

export interface Insights {
  total: number
  unreachable_count: number
  unreachable_top: number
  unreachable_market: number
  moved_up: number
  moved_down: number
  top3_series: number[]
  days: string[]
}

export interface Overview {
  generated_at: string
  headline: Headline
  focus: Order[]
  insights: Insights
  orders: Order[]
  /** 大类的顺序和中文名；后端还是旧版本时没有，品类按钮就不显示 */
  category_groups?: CategoryGroup[]
  /** 价位视图的汇总；从没算过估值（或后端还是旧版本）时为 null / 没有 */
  valuation?: ValuationSummary | null
  /** 启用的号 ≥ 2 时每个号的简称（键是字符串 id）；一个号时是空对象（多账号 spec §7） */
  accounts?: Record<string, string>
}

// ---- 二期：/api/item/{id}、/api/market、/api/analysis ----

/** SteamDT 日线，顺序照接口的 [时间, 开, 收, 高, 低]（不是 OHLC）。转 ECharts 用 charts/kline.ts。 */
export type Bar = [string, number, number, number, number]
/** 1 月 / 3 月 / 1 年用日线；all（「全部」）用周线 */
export type Window = '1m' | '3m' | '1y' | 'all'

export interface YRange {
  lo: number
  hi: number
  clipped: number
  extreme: number | null
}

export interface Signals {
  last_close: number | null
  ma7: number | null
  ma30: number | null
  ma_gap_pct: number | null
  trend: string | null
  change_7d_pct: number | null
  change_30d_pct: number | null
  bars: number
  trend_threshold_pct: number
}

interface Sourced {
  fetched_at: string | null
  stale: boolean
  error: string | null
}

export interface Kline extends Sourced {
  bars: Bar[]
  ranges: Record<Exclude<Window, 'all'>, YRange | null>
  signals: Signals | null
}

/** 周线（「全部」范围）：大盘从 2023-07 起，单品从 2022-07 起，中间可能缺几段。纵轴范围整段一份。 */
export interface Weekly extends Sourced {
  bars: Bar[]
  range: YRange | null
}

export interface PriceRow {
  platform: string
  name: string
  sell: number | null
  sell_count: number | null
  bid: number | null
  bid_count: number | null
  updated_at: string | null
}

export interface ItemDetail {
  template_id: number
  title: string
  star: boolean
  stattrak: boolean
  tag: string | null
  market_hash_name: string | null
  youpin_url: string
  orders: Order[]
  /** orders[].rank_7d 对应的 7 个本地日（YYYY-MM-DD） */
  days: string[]
  market: { configured: boolean; mapped: boolean }
  prices: (Sourced & { rows: PriceRow[] }) | null
  kline: Kline | null
  weekly: Weekly | null
  valuation?: ItemValuation | null
}

export interface MarketData {
  configured: boolean
  index:
    | (Sourced & {
        value: number | null
        diff: number | null
        /** 已经是百分数（0.4482 = 0.4482%），不要再乘 100 */
        diff_pct: number | null
        updated_at: string | null
        history: [string, number][]
      })
    | null
  kline: (Kline & { year_from: number | null; year_change_pct: number | null }) | null
  weekly: Weekly | null
}

export interface DailyClose {
  date: string
  /** null = 那天没有快照（服务没在跑），不是 0 */
  rank1: number | null
  top2: number | null
  top3: number | null
  total: number
}

export interface Mover {
  purchase_no: string
  template_id: number
  title: string
  star: boolean
  stattrak: boolean
  tag: string | null
  rank: number
  prev_rank: number
  rank_change: number
}

export interface Analysis {
  generated_at: string
  summary: {
    top3: number
    compare: { date: string; top3: number } | null
    moved_up: number
    moved_down: number
  }
  distribution: { label: string; min: number; max: number | null; count: number }[]
  unranked: { beyond: number; not_found: number; unchecked: number }
  daily: DailyClose[]
  movers: { up: Mover[]; down: Mover[] }
}

// ---- 三期：/api/status、/api/brief、/api/settings、/api/youpin/token ----

export type ChannelState = 'ok' | 'warn' | 'error' | 'idle'
export interface Channel {
  id: 'youpin_anon' | 'youpin_account' | 'pushplus' | 'ntfy'
  name: string
  configured: boolean
  state: ChannelState
  last_ok_at: string | null
  last_error_code: string | null
  last_error_at: string | null
  paused_until: string | null
}

export type StatusIssue = Issue | 'pushplus_failing' | 'code_outdated'

export interface NotificationItem {
  id: number
  rank: number | null
  title: string
  star: boolean
  stattrak: boolean
  tag: string | null
  /** RANK = 升到第 1 名；IN_TRADE、FILLED 是成交推送（没有名次，用图标徽章） */
  kind?: 'RANK' | 'IN_TRADE' | 'FILLED' | 'MARKET'
  /** FILLED 时这次成交的时间 */
  filled_at?: string | null
  event: string
  status: 'sent' | 'failed' | 'suppressed'
  at: string | null
}

export type BriefKind = 'sent' | 'late' | 'not_sent' | 'failed' | 'gap'

export interface StatusData {
  now: string
  headline: { issue: StatusIssue | null; paused_until: string | null; pushplus_error_at: string | null }
  service: Meta['service']
  channels: Channel[]
  account: {
    nickname: string | null
    user_id: number | null
    token_valid: boolean
    expires_at: string | null
    days_left: number | null
    last_sync_at: string | null
  }
  /** 每个号一张卡片（多账号 spec §8）；旧后端没有 */
  accounts?: Account[]
  verification: {
    checked_24h: number
    agreed_24h: number
    inconclusive_24h: number
    last_disagreement: { purchase_no: string; reported_rank: number | null; computed_rank: number | null; ts: string } | null
  }
  notifications: { totals: { sent: number; failed: number; suppressed: number }; recent: NotificationItem[] }
  briefs: { date: string; kind: BriefKind; label: string }[]
  brief: { enabled: boolean; send_at: string; top_rank: number }
  valuation?: { enabled: boolean; run_at: string; last: { as_of: string; computed_at: string; valued: number } | null }
  /** code_outdated：工作区的代码改了、服务还没重启 */
  runtime: {
    started_at: string | null; supervisor: string | null; platform: string; db_bytes: number; db_path: string; code_outdated?: boolean
    /** 由 LaunchAgent 拉起、能在看板上重启（intent 2026-11-23） */
    can_restart?: boolean
    /** 装好的依赖和 uv.lock 对不上：要先 uv sync */
    deps_outdated?: boolean
  }
}

export interface BriefTableRow {
  rank: number
  title: string
  star: boolean
  stattrak: boolean
  tag: string | null
  my_price: number
  /** 榜首是我（第 1 名）时为 null，看 is_me */
  top1_price: number | null
  is_me: boolean
  /** null = 那天没有在售价，不是 0 */
  sell_price: number | null
  changes: string[]
}

export interface BriefRow {
  date: string
  decision: string | null
  kind: BriefKind
  label: string
  summary: string
  delivery: string | null
  title: string | null
  sent_at: string | null
  table: BriefTableRow[] | null
  note: string | null
  /** 没法解析成表格的老记录，原样显示 */
  body: string | null
}

export interface BriefData {
  rows: BriefRow[]
  enabled: boolean
  send_at: string
  top_rank: number
  summary: { sent_30d: number }
  next_at: string | null
}

export type SettingsSection = 'polling' | 'alerts' | 'brief' | 'buff' | 'dashboard'
export type SettingsValues = Record<SettingsSection, Record<string, number | string | boolean>> & Record<string, unknown>
export interface SettingsSave {
  settings: SettingsValues
  changed: string[]
}
export interface FieldError {
  field: string
  message: string
}

export interface TokenResult {
  nickname: string
  user_id: number
  expires_at: string | null
  /** 下一轮监控已经提前开始（它开头会先同步求购单）；`run --web-only` 没有调度器时为 false */
  round_started: boolean
}

// ---- 价位三期：选品（价位 spec §9b）----

export type PickVerdict = 'go' | 'wait' | 'no' | 'none'
export interface PickFilters {
  groups: string[]
  subs: string[]
  wears: string[]
  versions: string[]
  price_min: number | null
  price_max: number | null
}
export interface PickOption {
  key: string
  label: string
}
export interface PickOptions {
  groups: (PickOption & { default: boolean })[]
  /** 大类 → 第二层（武器、刀型、手套类型），带目录里的模板数 */
  subs: Record<string, (PickOption & { n: number })[]>
  wears: PickOption[]
  versions: PickOption[]
  /** 每个大类在目录里实际有的磨损、版本键（intent 2026-11-23）；老后端没有 */
  group_facets?: Record<string, { wears: string[]; versions: string[] }>
  max: number
  /** 夜间窗口，例如「23:40～00:50」 */
  night: string
}
export interface PickEstimate {
  count: number
  max: number
  over: boolean
  minutes: number
}
export interface PickRunBrief {
  id: number
  created_at: string
  finished_at: string | null
  status: 'running' | 'done' | 'failed' | 'interrupted'
  phase: 'pool' | 'prices' | 'klines' | 'done'
  done: number
  total: number
  pool: number
  priced: number
  failed: number
  /** 没取到价格的模板（那一批出错了）：设了价格区间时它们被筛掉，没设时都是「不评价」 */
  price_failed: number
  error: string | null
  filters: PickFilters
  counts: Record<PickVerdict, number>
}
export interface PickRow {
  template_id: number
  title: string
  star: boolean
  stattrak: boolean
  tag: string | null
  yp_sell: number | null
  yp_count: number | null
  yp_bid: number | null
  bars: number
  pct: number | null
  pct_label: '一年低位' | '一年中位' | '一年高位' | null
  trend: string | null
  rel: string | null
  q10: number | null
  liq: string | null
  exit: number | null
  exit_where: string | null
  cap: number | null
  /** 现价比近 rebound_window 天最低高多少（R3）；日线不够、老结果为 null */
  rebound?: number | null
  /** 一年最高 ÷ 一年最低（「炒过」，只作描述） */
  amp?: number | null
  verdict: PickVerdict
  label: string
  rule: string
  bid: number | null
  margin: number | null
  first: boolean
  mine: boolean
}
export interface PickRunDetail extends PickRunBrief {
  results: PickRow[]
  busy: boolean
  /** R3 的窗口天数和门槛（[valuation]）；老后端没有 */
  rebound_window?: number
  rebound_max?: number
}

// ---- 盈亏（盈亏 spec §6）----
export type PnlBasis = 'market' | 'net'
export interface PnlCategory {
  key: string
  label: string
  qty: number
  cost: number
  cost_priced: number
  /** 一件都没有市价时为 null（不是 0） */
  value_mkt: number | null
  value_net: number | null
}
export interface PnlSummary {
  /** 最近一次快照的本地日期；还没跑过为 null，其余字段也都是 null / 空 */
  as_of: string | null
  computed_at: string | null
  basis_default: PnlBasis
  realized: { total: number; matched: number; unknown: number; unknown_net: number; sells: number } | null
  holding: {
    qty: number
    cost: number
    /** 有市价的那部分持仓的成本：浮动只拿它比 */
    cost_priced: number
    value_mkt: number
    value_net: number
    unpriced_qty: number
    flagged: number
  } | null
  where: { inv: number; lease: number; unknown: number; unknown_cost: number } | null
  youpin: { pnl: number; n: number | null } | null
  months: [string, number][]
  categories: PnlCategory[]
  notes: { lease_qty: number; lease_rent_day: number | null; unbooked_inv_qty: number; unbooked_inv_value: number | null } | null
}
export interface PnlItem {
  /** 按英文名合并的键（BUFF 独有的饰品没有悠悠模板 ID） */
  key: string
  /** 悠悠模板 ID；BUFF 独有的饰品为 null（没有详情页） */
  template_id: number | null
  /** 在哪些平台买的：YOUPIN、BUFF */
  platforms: string[]
  name: string
  sub: string | null
  category: string
  category_label: string
  qty: number
  cost: number
  price: number | null
  price_flag: 'thin' | 'spread' | null
  where: { inv: number; lease: number; unknown: number }
  value_mkt: number | null
  value_net: number | null
  /** 按请求的 basis 算的浮动；没市价为 null */
  float: number | null
  realized: number | null
}
export interface PnlItems {
  as_of: string | null
  total: number
  items: PnlItem[]
}
export interface PnlLot {
  bought_at: string
  unit: number
  qty: number
  /** 这批是在哪个平台、哪个号买的（跨平台配对，ADR-0025） */
  platform: string
  account_id: number
}
export interface PnlSell {
  at: string
  order_no: string
  account_id: number
  /** 在哪个平台卖的 */
  platform: string
  template_id: number | null
  name: string
  sub: string | null
  qty: number
  net: number
  net_exact: boolean
  cost: number | null
  pnl: number | null
  lots: PnlLot[]
}
export interface PnlSells {
  total: number
  sells: PnlSell[]
}

// ---- 充值前检查暂停单（暂停单 spec §5.3）----

export type PausedTier = 'inverted' | 'top1' | 'near' | 'near_sell' | 'other'

export interface PausedRun {
  id: number
  status: 'running' | 'done' | 'failed' | 'interrupted'
  source: 'dashboard' | 'cli'
  started_at: string
  finished_at: string | null
  /** 检查中的那一步：翻暂停单 / 查 SteamDT 在售价 / 悠悠在售接口核对 */
  phase: 'pages' | 'prices' | 'verify' | null
  done: number
  total: number
  /** 某个号翻到了页数上限 */
  incomplete: boolean
  steamdt: boolean
  /** 这次没查成的号（登录失效、限流） */
  skipped: number[]
  error: string | null
}

export interface PausedItem {
  purchase_no: string
  account_id: number
  template_id: number
  title: string
  star: boolean
  stattrak: boolean
  tag: string | null
  price: number
  remaining_qty: number
  paused_at: string | null
  paused_days: number | null
  old: boolean
  top_bid: number | null
  /** 悠悠说「暂无」：同条件子队列没人求购 */
  top_empty: boolean
  top1_kind: 'alone' | 'ahead' | 'tie' | null
  gap_ratio: number | null
  /** 比第 1 名高出多少（只在我的价 ≥ 榜首时有） */
  over: number | null
  sell_min: number | null
  sell_depth: number | null
  sell_src: 'youpin' | 'steamdt' | null
  sell_at: string | null
  sell_ratio: number | null
  tier: PausedTier
}

export interface PausedPayload {
  /** 最近一次检查（可能正在跑） */
  run: PausedRun | null
  /** 最近一次查完的检查：卡片显示的是它 */
  result: PausedRun | null
  summary: { total: number; amount: number; old: number; tiers: Record<PausedTier, { count: number; amount: number }> } | null
  /** 不含 other 档，已按危险程度排好 */
  items: PausedItem[]
  /** 最近一次查完之后又被暂停的张数 */
  paused_since: number
  /** 库里知道的、还在暂停中的张数（服务上线前暂停的看不到，只是下限） */
  known_paused: number
  old_days: number
  stale_h: number
  accounts: Record<string, string>
}
