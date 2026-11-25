// 总览页的纯逻辑：筛选、刷新前后的排名对比、提示行文案。vitest 覆盖（logic.test.ts）。
import type { CategoryGroup, Insights, Meta, Order } from '@/api/types'
import { DASH, money } from '@/format'

export type Segment = 'all' | 'top2' | 'top3' | 'rest'
export interface Filters {
  seg: Segment
  /** 隐藏难以成交的单（难以成交 spec §5）；网址参数还是 `hide=1`，旧书签照样有效 */
  hideUnreachable: boolean
  q: string
  /** 品类：大类键和第二层英文键（看板 spec §4.1），null 是不筛 */
  cat: string | null
  sub: string | null
  /** 「离第 1」档位（intent 2026-11-20），null 是不筛 */
  gap: GapFilter | null
}

// 「前 2 名」排最前、也是默认；「全部」排最后（用户 2026-09-27）
export const SEGMENTS: { key: Segment; label: string }[] = [
  { key: 'top2', label: '前 2 名' },
  { key: 'top3', label: '前 3 名' },
  { key: 'rest', label: '第 4 名以后' },
  { key: 'all', label: '全部' },
]
export const DEFAULT_SEGMENT: Segment = 'top2'

/** 悠悠显示「交易中」（rank 是 *）：有卖家卖给了这张单。它比第 1 名还靠前，前 2、前 3 名里都要看得到。 */
export const inTrade = (o: Order) => o.in_trade_since != null

/** 难以成交的原因（难以成交 spec §4）。旧后端没有 `unreachable` 字段时退回只看「离第 1」的 `gated`。 */
export const unreachableOf = (o: Order): 'top' | 'market' | null =>
  o.unreachable !== undefined ? o.unreachable ?? null : o.gated ? 'top' : null

/** 排名列的排序键：交易中排在第 1 名之前，没有排名的是 null。 */
export const rankSortKey = (o: Order): number | null => (inTrade(o) ? 0 : o.rank)

export function filterOrders(orders: Order[], f: Filters): Order[] {
  const q = f.q.trim().toLowerCase()
  return orders.filter((o) => {
    const t = inTrade(o)
    if (f.seg === 'top2' && !t && !(o.rank != null && o.rank <= 2)) return false
    if (f.seg === 'top3' && !t && !(o.rank != null && o.rank <= 3)) return false
    if (f.seg === 'rest' && (t || (o.rank != null && o.rank <= 3))) return false
    if (f.hideUnreachable && unreachableOf(o)) return false
    if (q && !`${o.title} ${o.tag ?? ''}`.toLowerCase().includes(q)) return false
    if (f.cat && (o.category ?? 'other') !== f.cat) return false
    if (f.cat && f.sub && o.subcategory !== f.sub) return false
    if (f.gap && gapTier(o) !== f.gap) return false
    return true
  })
}

/** 自动刷新后，名次变了的单：数字变小是升（up），变大是降（down）。第一次加载不算变化。 */
export function rankFlashes(prev: Order[] | null, next: Order[]): Map<string, 'up' | 'down'> {
  const out = new Map<string, 'up' | 'down'>()
  if (!prev) return out
  const before = new Map(prev.map((o) => [o.purchase_no, o.rank]))
  for (const o of next) {
    const was = before.get(o.purchase_no)
    if (was == null || o.rank == null || was === o.rank) continue
    out.set(o.purchase_no, o.rank < was ? 'up' : 'down')
  }
  return out
}

/** URL 查询参数 ⇄ 筛选条件，刷新页面后保留。 */
export function filtersFromQuery(q: Record<string, unknown>): Filters {
  const seg = SEGMENTS.some((s) => s.key === q.rank) ? (q.rank as Segment) : DEFAULT_SEGMENT
  const cat = typeof q.cat === 'string' && q.cat ? q.cat : null
  const sub = cat && typeof q.sub === 'string' && q.sub ? q.sub : null
  const gap = GAP_FILTERS.some((g) => g.key === q.gap) ? (q.gap as GapFilter) : null
  return { seg, hideUnreachable: q.hide === '1', q: typeof q.q === 'string' ? q.q : '', cat, sub, gap }
}

export function filtersToQuery(f: Filters): Record<string, string> {
  const out: Record<string, string> = {}
  if (f.seg !== DEFAULT_SEGMENT) out.rank = f.seg
  if (f.hideUnreachable) out.hide = '1'
  if (f.q.trim()) out.q = f.q.trim()
  if (f.cat) out.cat = f.cat
  if (f.cat && f.sub) out.sub = f.sub
  if (f.gap) out.gap = f.gap
  return out
}

export interface CategoryItem {
  key: string
  label: string
  n: number
}
export interface CategoryMenuGroup extends CategoryItem {
  /** 枪、刀、手套才有第二层；按张数从多到少 */
  subs: CategoryItem[]
}

/**
 * 品类面板的两层张数。`orders` 由调用方先按品类以外的条件（分段、隐藏、搜索）筛好，所以张数和
 * 选了之后表格里的行数一致。张数为 0 的不出现，当前选中的除外（不然按钮上的选择在面板里找不到）。
 */
export function categoryMenu(orders: Order[], groups: CategoryGroup[], sel: { cat: string | null; sub: string | null }): CategoryMenuGroup[] {
  const n = new Map<string, number>()
  const subs = new Map<string, Map<string, CategoryItem>>()
  for (const o of orders) {
    const g = o.category ?? 'other'
    n.set(g, (n.get(g) ?? 0) + 1)
    if (!o.subcategory) continue
    const m = subs.get(g) ?? new Map<string, CategoryItem>()
    subs.set(g, m)
    const it = m.get(o.subcategory) ?? { key: o.subcategory, label: o.subcategory_label || o.subcategory, n: 0 }
    it.n += 1
    m.set(o.subcategory, it)
  }
  const out: CategoryMenuGroup[] = []
  for (const g of groups) {
    const count = n.get(g.key) ?? 0
    if (!count && sel.cat !== g.key) continue
    const list = [...(subs.get(g.key)?.values() ?? [])]
    if (sel.cat === g.key && sel.sub && !list.some((s) => s.key === sel.sub)) list.push({ key: sel.sub, label: sel.sub, n: 0 })
    list.sort((a, b) => b.n - a.n || a.label.localeCompare(b.label, 'zh'))
    out.push({ key: g.key, label: g.label, n: count, subs: list })
  }
  return out
}

/** 按钮上的文字：「全部」「步枪」「步枪 › AK-47」。第二层的中文名从面板里找，找不到用英文键。 */
export function categoryText(menu: CategoryMenuGroup[], groups: CategoryGroup[], cat: string | null, sub: string | null): string {
  if (!cat) return '全部'
  const g = menu.find((x) => x.key === cat)
  const label = g?.label ?? groups.find((x) => x.key === cat)?.label ?? cat
  if (!sub) return label
  return `${label} › ${g?.subs.find((s) => s.key === sub)?.label ?? sub}`
}

export interface InsightView {
  title: string
  text: string
  tone: 'warn' | 'neutral'
}

export function top3Insight(i: Insights): InsightView {
  const s = i.top3_series
  const first = s[0] ?? 0
  const last = s[s.length - 1] ?? 0
  const title = last < first ? '前 3 名在变少' : last > first ? '前 3 名在变多' : '前 3 名持平'
  return { title, text: `7 天里从 ${first} 张到 ${last} 张`, tone: last < first ? 'warn' : 'neutral' }
}

export function movesInsight(i: Insights): InsightView {
  if (!i.moved_up && !i.moved_down) return { title: '今日名次无变化', text: '与昨日收盘一致', tone: 'neutral' }
  return { title: '今天名次有升有降', text: `比昨天升 ${i.moved_up} 张、降 ${i.moved_down} 张`, tone: 'neutral' }
}

export function unreachableInsight(i: Insights): InsightView {
  return {
    title: `${i.unreachable_count} 张难以成交`,
    text: `离第 1 太远 ${i.unreachable_top} 张、离市价太远 ${i.unreachable_market} 张`,
    tone: 'neutral',
  }
}

/** 「离市价」列（难以成交 spec §5）：主数字是差价，小字是倍数；没人在卖时市价是别处买盘，小字写来源。
 * 市价不知道写「—」：查过没人在卖、也没有别处买盘时说明原因，没查过就不写。 */
export function marketCellText(o: Order): { main: string; sub: string; hard: boolean } {
  if (o.market == null || o.market_gap == null || o.market_ratio == null) {
    return { main: DASH, sub: o.sell_depth === 0 ? '无在售、无别处买盘' : '', hard: false }
  }
  const r = o.market_ratio
  const ratio = `${r >= 10 ? r.toFixed(1) : r.toFixed(2)} 倍`
  return {
    main: `差 ${money(Math.max(0, o.market_gap))}`,
    sub: o.market_src === 'bid' ? `别处买盘 · ${ratio}` : ratio,
    hard: unreachableOf(o) === 'market',
  }
}

/** 「离市价」列的排序键：市价 ÷ 我的价，不知道的为 null（排最后）。 */
export const marketSortKey = (o: Order): number | null => o.market_ratio ?? null

// ---- 市价提醒（市价提醒 spec §4）----

/** 「离第 1」列下面的标签：榜首高于在售用 warn，贴近市价用 amber。 */
export function marketTags(flag: Order['market_flag']): { label: string; tone: 'warn' | 'amber' }[] {
  const out: { label: string; tone: 'warn' | 'amber' }[] = []
  if (flag === 'inverted' || flag === 'both') out.push({ label: '榜首高于在售', tone: 'warn' })
  if (flag === 'near' || flag === 'both') out.push({ label: '贴近市价', tone: 'amber' })
  return out
}

// ---- 价格梯（看板 spec §4.1，2026-10-09）----

/** 「离第 1」的分档界线：差价占榜首价 ≤ 5% 很近，≤ 15% 一般，再多是较远（用户 2026-10-09 认可视觉稿） */
export const GAP_NEAR = 0.05
export const GAP_MID = 0.15

export type GapTier = 'first' | 'trade' | 'none' | 'gated' | 'near' | 'mid' | 'far'
export const GAP_TIER_LABEL: Record<GapTier, string> = {
  first: '已是第 1', trade: '交易中', none: '—', gated: '难成交', near: '很近', mid: '一般', far: '较远',
}

/** 差价占榜首价的比例：(榜首 − 我的) ÷ 榜首。缺价或价格 ≤ 0 为 null；榜首比我还低（快照滞后）记 0。 */
export function gapPct(o: Pick<Order, 'my_price' | 'top1_price'>): number | null {
  const my = o.my_price, top = o.top1_price
  if (my == null || top == null || my <= 0 || top <= 0) return null
  return Math.max(0, (top - my) / top)
}

export function gapTier(o: Order): GapTier {
  if (inTrade(o)) return 'trade'
  if (o.rank === 1) return 'first'
  const g = gapPct(o)
  if (g == null) return 'none'
  if (o.gated) return 'gated'
  return g <= GAP_NEAR ? 'near' : g <= GAP_MID ? 'mid' : 'far'
}

/** 「离第 1」列的排序键：交易中、已是第 1 排最前（−1），其余按差价比例，算不出的为 null（排最后）。 */
export const gapSortKey = (o: Order): number | null => {
  const t = gapTier(o)
  return t === 'trade' || t === 'first' ? -1 : gapPct(o)
}

/**
 * 可排序列的比较函数：值从小到大，null 不管升序还是降序都在最后。Naive 降序时把结果乘 −1，所以 `desc`
 * 为真时 null 那部分先反过来。
 */
export function nullsLast<T>(get: (o: T) => number | null) {
  return (a: T, b: T, desc = false): number => {
    const x = get(a), y = get(b)
    if (x == null || y == null) return x == null && y == null ? 0 : (x == null ? 1 : -1) * (desc ? -1 : 1)
    return x - y
  }
}

/**
 * 排名列的比较函数：名次从小到大（交易中最前、没有排名的最后），同名次按差价比例从小到大、算不出的在
 * 同名次最后（intent 2026-11-20）。Naive 降序时把结果乘 −1；`desc` 为真时先把「没有排名」和同名次那
 * 部分反过来，这样降序只反转名次，同名次里仍然差距小的在前、没有排名的仍在最后。
 */
export function rankCompare(a: Order, b: Order, desc = false): number {
  const s = desc ? -1 : 1
  const x = rankSortKey(a), y = rankSortKey(b)
  if (x == null || y == null) return x == null && y == null ? 0 : (x == null ? 1 : -1) * s
  if (x !== y) return x - y
  const g = gapPct(a), h = gapPct(b)
  if (g == null || h == null) return g == null && h == null ? 0 : (g == null ? 1 : -1) * s
  return (g - h) * s
}

/** 工具栏「离第 1」筛选的档位，和表格里的标签一一对应；已是第 1、交易中、算不出的不参与 */
export type GapFilter = 'near' | 'mid' | 'far' | 'gated'
// 「难成交」这一档只含离第 1 太远的单（离市价太远的另有「隐藏难以成交的」），筛选里写明（难以成交 spec §5）
export const GAP_FILTERS: { key: GapFilter; label: string }[] = (['near', 'mid', 'far', 'gated'] as const).map((key) => ({
  key, label: key === 'gated' ? '离第 1 太远' : GAP_TIER_LABEL[key],
}))

/** 各档的张数。`orders` 由调用方先按档位以外的条件筛好，所以张数和选了之后表格里的行数一致。 */
export function gapCounts(orders: Order[]): Record<GapFilter, number> {
  const out: Record<GapFilter, number> = { near: 0, mid: 0, far: 0, gated: 0 }
  for (const o of orders) {
    const t = gapTier(o)
    if (t in out) out[t as GapFilter] += 1
  }
  return out
}

/** 三个价在轴上的位置（0～100，轴从 0 到三者中最高）。缺的为 null；三个都缺时整个为 null。
 * 无人在售（`sell_depth` 为 0）时不画在售：那时的 `sell_min` 是旧的，不代表市价。 */
export function ladderMarks(o: Pick<Order, 'my_price' | 'top1_price' | 'sell_min'> & { sell_depth?: number | null }):
  { my: number | null; top: number | null; sell: number | null } | null {
  const ok = (v: number | null) => (v != null && v > 0 ? v : null)
  const my = ok(o.my_price), top = ok(o.top1_price), sell = o.sell_depth === 0 ? null : ok(o.sell_min)
  const max = Math.max(my ?? 0, top ?? 0, sell ?? 0)
  if (max <= 0) return null
  const at = (v: number | null) => (v == null ? null : (v / max) * 100)
  return { my: at(my), top: at(top), sell: at(sell) }
}

// ---------- 「开始使用」卡片（首次使用 spec §2） ----------

export type SetupKey = 'youpin' | 'push' | 'first_round' | 'market'
export type SetupState = 'done' | 'current' | 'todo' | 'waiting' | 'optional'
export interface SetupStep {
  key: SetupKey
  state: SetupState
  /** 完成后那一行灰字（「已登录 · token 还有 6 天到期」） */
  doneText: string
}
export interface SetupView {
  steps: SetupStep[]
  /** 还差几步（只数必需的三步） */
  left: number
  /** 三步都做完了，但悠悠上一张求购都没有：卡片只剩一句话（spec §2.2），挂了单就消失 */
  noOrders?: boolean
}

/** 三步必需的都完成（或旧后端没有 setup、或 demo）就返回 null，卡片不出现。顺序固定。 */
export function setupView(meta: Meta | null | undefined, orderCount: number | null = null): SetupView | null {
  const s = meta?.setup
  if (!s || meta?.demo) return null
  const required: SetupKey[] = ['youpin', 'push', 'first_round']
  const left = required.filter((k) => !s[k]).length
  if (left === 0) return orderCount === 0 ? { steps: [], left: 0, noOrders: true } : null
  const current = required.find((k) => !s[k] && (k !== 'first_round' || s.youpin))
  const days = meta?.token.days_left
  const doneText: Record<SetupKey, string> = {
    youpin: days == null ? '已登录' : `已登录 · token 还有 ${days} 天到期`,
    push: '已配置 · 提醒会发到微信',
    first_round: orderCount == null ? '已完成' : `已完成 · 同步了 ${orderCount} 张求购单`,
    market: '已配置',
  }
  const state = (k: SetupKey): SetupState => {
    if (s[k]) return 'done'
    if (k === 'market') return 'optional'
    if (k === 'first_round' && !s.youpin) return 'waiting'
    return k === current ? 'current' : 'todo'
  }
  return { steps: [...required, 'market' as const].map((k) => ({ key: k, state: state(k), doneText: doneText[k] })), left }
}

// ---- 多账号（多账号 spec §8）----

/** 商品名后面的账号标签：只在「全部」、启用的号 ≥ 2 时出现；第一个号（大多数单是它的）不带。 */
export function accountTag(o: Pick<Order, 'account_id'>, accounts: Record<string, string> | undefined, selected: 'all' | number): string | null {
  const ids = Object.keys(accounts ?? {}).map(Number).sort((a, b) => a - b)
  if (ids.length < 2 || selected !== 'all' || o.account_id == null || o.account_id === ids[0]) return null
  return accounts?.[String(o.account_id)] ?? null
}

/** BUFF 单（BUFF spec §8） */
export function isBuff(o: Pick<Order, 'platform'>): boolean {
  return o.platform === 'buff'
}

/** BUFF 单「离第 1」下面的提示：名次是在普通单里数的，价比我高的条件单另外说 */
export function condAheadText(o: Pick<Order, 'platform' | 'cond_ahead'>): string | null {
  return isBuff(o) && o.cond_ahead ? `前面还有 ${o.cond_ahead} 张条件单` : null
}

/** 「离第 1」旁边的提示：同一个子队列里，我另一个号排在这张前面 */
export function ownAheadText(o: Pick<Order, 'own_ahead'>): string | null {
  const a = o.own_ahead
  if (!a) return null
  return a.price == null ? `前面是我的${a.label}` : `前面是我的${a.label}（¥${money(a.price)}）`
}
