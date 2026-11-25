// 总览「价位」视图的纯逻辑（价位 spec §7.1）：视图和筛选的 URL 参数、筛选、排序、标题文案、
// 扣费明细、色调。vitest 覆盖（valueLogic.test.ts）。比率都是小数（0.15 = 15%），null 显示「—」。
import type { AdviceKind, Order, OrderValue, ValuationSummary } from '@/api/types'
import { money, plainPct, ratioPct } from '@/format'

export type View = 'rank' | 'value'
export type ValueFilter = 'low' | 'fall' | 'hard'
export interface ValueState {
  view: View
  vf: ValueFilter | null
  /** 按建议筛选（价位 spec §9.6），null 是不筛 */
  adv: AdviceKind | null
}

export const VIEWS: { key: View; label: string }[] = [
  { key: 'rank', label: '排名' },
  { key: 'value', label: '价位' },
]

export const VALUE_FILTERS: { key: ValueFilter; label: string; test: (v: OrderValue) => boolean }[] = [
  { key: 'low', label: '一年低位', test: (v) => v.pct_label === '一年低位' },
  { key: 'fall', label: '在跌且跑输大盘', test: (v) => v.trend === '下降' && v.rel === '跑输' },
  { key: 'hard', label: '难出手', test: (v) => v.liq === '难出手' },
]

export function valueStateFromQuery(q: Record<string, unknown>): ValueState {
  const view: View = q.view === 'value' ? 'value' : 'rank'
  const vf = VALUE_FILTERS.some((f) => f.key === q.vf) ? (q.vf as ValueFilter) : null
  const adv = ADVICE.some((a) => a.key === q.adv) ? (q.adv as AdviceKind) : null
  return view === 'value' ? { view, vf, adv } : { view, vf: null, adv: null }
}

export function valueStateToQuery(s: ValueState): Record<string, string> {
  if (s.view !== 'value') return {}
  const out: Record<string, string> = { view: 'value' }
  if (s.vf) out.vf = s.vf
  if (s.adv) out.adv = s.adv
  return out
}

/** 建议的顺序和色调（视觉稿「二期」预览）：撤单、降价用 warn，加价用 accent，保持中性，不评价最淡。 */
export type AdviceTone = 'warn' | 'accent' | 'neutral' | 'muted'
export const ADVICE: { key: AdviceKind; label: string; tone: AdviceTone }[] = [
  { key: 'cancel', label: '考虑撤单', tone: 'warn' },
  { key: 'lower', label: '考虑降价', tone: 'warn' },
  { key: 'raise', label: '可以加价', tone: 'accent' },
  { key: 'hold', label: '保持', tone: 'neutral' },
  { key: 'none', label: '不评价', tone: 'muted' },
]

export function adviceTone(kind: AdviceKind | null | undefined): AdviceTone {
  return ADVICE.find((a) => a.key === kind)?.tone ?? 'muted'
}

export function filterByAdvice(orders: Order[], adv: AdviceKind | null): Order[] {
  if (!adv) return orders
  return orders.filter((o) => o.value?.advice?.kind === adv)
}

export function filterByValue(orders: Order[], vf: ValueFilter | null): Order[] {
  if (!vf) return orders
  const test = VALUE_FILTERS.find((f) => f.key === vf)!.test
  return orders.filter((o) => o.value != null && test(o.value))
}

/** 按「抢到第 1」的差价从高到低；算不出来的（已是第 1、没榜首价、没估值）排最后。 */
export function sortByTakeMargin(orders: Order[]): Order[] {
  const key = (o: Order) => o.value?.m_top ?? null
  return [...orders].sort((a, b) => {
    const x = key(a), y = key(b)
    if (x == null && y == null) return 0
    if (x == null) return 1
    if (y == null) return -1
    return y - x
  })
}

export function valueHeadline(s: ValuationSummary, orders: Order[]): { n: number; target: string; sub: string } {
  const tops = orders.map((o) => o.value?.m_top).filter((m): m is number => m != null)
  const n = tops.filter((m) => m >= s.target_margin).length
  const loss = tops.filter((m) => m < 0).length
  const target = plainPct(s.target_margin)
  const snap = s.snapshot_date ? `按 ${s.snapshot_date.slice(5)} 价格快照` : '无价格快照，无法计算差价'
  return { n, target, sub: `其余 ${tops.length - n} 张抢到第 1 后差价不到 ${target}，其中 ${loss} 张会亏 · ${snap}` }
}

export type Tone = 'loss' | 'accent' | 'plain' | 'muted'

export function marginTone(m: number | null | undefined, target: number): Tone {
  if (m == null) return 'muted'
  if (m < 0) return 'loss'
  return m >= target ? 'accent' : 'plain'
}

/** 一年收盘区间上的三个点：一年低、现价、一年高（左右各留 3%，别贴边）。 */
export function rangeMarks(v: Pick<OrderValue, 'lo' | 'hi' | 'cur'>): { key: 'lo' | 'cur' | 'hi'; label: string; value: string; left: number }[] {
  if (v.lo == null || v.hi == null || v.cur == null) return []
  const span = v.hi - v.lo
  const pos = (x: number) => (span > 0 ? 3 + ((x - v.lo!) / span) * 94 : 3)
  return [
    { key: 'lo', label: '一年低', value: money(v.lo), left: 3 },
    { key: 'cur', label: '现价', value: money(v.cur), left: pos(v.cur) },
    { key: 'hi', label: '一年高', value: money(v.hi), left: 97 },
  ]
}

export const PLATFORM_NAME: Record<string, string> = { YOUPIN: '悠悠', BUFF: 'BUFF' }

export interface WaterRow {
  label: string
  value: string
  strong?: boolean
  tone: 'plain' | 'soft' | 'loss'
}

/** 卖出去能拿到多少：在售最低 → 扣掉让价、手续费、提现 → 到手 → 三种差价。没有到手价时整块不画。 */
export function waterfall(
  v: OrderValue,
  o: Pick<Order, 'my_price' | 'top1_price' | 'rank'>,
  fees: ValuationSummary['fees'],
): WaterRow[] {
  if (v.exit == null || v.exit_sell == null || v.exit_where == null) return []
  const fee = fees[v.exit_where]
  const m = (x: number | null): WaterRow['tone'] => (x != null && x < 0 ? 'loss' : 'plain')
  const rows: WaterRow[] = [
    { label: `${PLATFORM_NAME[v.exit_where]} 在售最低`, value: money(v.exit_sell), tone: 'plain' },
    {
      label: `让价 ${feePct(fees.undercut)}、手续费 ${feePct(fee)}、提现 ${feePct(fees.withdraw)}`,
      value: `−${money(v.exit_sell - v.exit)}`,
      tone: 'soft',
    },
    { label: '到手', value: money(v.exit), strong: true, tone: 'plain' },
  ]
  if (o.my_price != null) rows.push({ label: `按我的价 ${money(o.my_price)} 成交`, value: ratioPct(v.m_my).text, tone: m(v.m_my) })
  if (o.rank === 1) rows.push({ label: '已是第 1', value: '—', tone: 'soft' })
  else if (o.top1_price != null) {
    rows.push({ label: `抢到第 1（出价 ${money(o.top1_price + 0.01)}）`, value: ratioPct(v.m_top).text, tone: m(v.m_top) })
  }
  if (v.m_bad != null && v.q10 != null) {
    rows.push({ label: `按我的价，7 天后碰上坏情况（${ratioPct(v.q10).text}）`, value: ratioPct(v.m_bad).text, tone: m(v.m_bad) })
  }
  return rows
}

/** 费率：1.5% 这种留一位小数，1% 不要。 */
export function feePct(r: number): string {
  const p = Math.round(r * 1000) / 10
  return `${Number.isInteger(p) ? p.toFixed(0) : p.toFixed(1)}%`
}
