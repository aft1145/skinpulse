// 详情页「当前价位」卡片的纯逻辑（价位 spec §7.2）。vitest 覆盖（valueLogic.test.ts）。
import type { Order, OrderValue } from '@/api/types'

const TICK = 0.01
const r6 = (x: number) => Math.round(x * 1e6) / 1e6

/** 卡片跟着「我的求购」切换单：按这张单的价格、榜首、名次重算三项差价，规则和后端
 *  analysis.valuation.margins 一样（到手价 exit 和坏情况 q10 与单无关，后端已给）。 */
export function marginsFor(
  v: Pick<OrderValue, 'exit' | 'q10'>,
  o: Pick<Order, 'my_price' | 'top1_price' | 'rank'>,
): { m_my: number | null; m_top: number | null; m_bad: number | null } {
  const ok = v.exit != null && o.my_price != null && o.my_price > 0
  const m_my = ok ? r6(v.exit! / o.my_price! - 1) : null
  const m_bad = ok && v.q10 != null ? r6((v.exit! * (1 + v.q10)) / o.my_price! - 1) : null
  const m_top =
    v.exit == null || o.rank === 1 || o.top1_price == null || o.top1_price <= 0 ? null : r6(v.exit / (o.top1_price + TICK) - 1)
  return { m_my, m_top, m_bad }
}

/** 近 90 天：单品、大盘各自以第一个有数的收盘记作 100，缺的日子留 null（图上断开，不补）。 */
export function relSeries(rows: [string, number | null, number | null][]): {
  dates: string[]
  item: (number | null)[]
  broad: (number | null)[]
} {
  const base = (i: 1 | 2) => rows.find((r) => r[i] != null)?.[i] ?? null
  const bi = base(1), bb = base(2)
  const idx = (v: number | null, b: number | null) => (v == null || b == null ? null : Math.round((v / b) * 1000) / 10)
  return {
    dates: rows.map((r) => r[0].slice(5)),
    item: rows.map((r) => idx(r[1], bi)),
    broad: rows.map((r) => idx(r[2], bb)),
  }
}

export type ChipTone = 'accent' | 'warn' | 'up' | 'down' | 'ok' | 'neutral'

export function chips(v: OrderValue): { label: string; tone: ChipTone }[] {
  const out: { label: string; tone: ChipTone }[] = []
  if (v.pct_label) out.push({ label: v.pct_label, tone: v.pct_label === '一年低位' ? 'accent' : v.pct_label === '一年高位' ? 'warn' : 'neutral' })
  if (v.trend) out.push({ label: v.trend, tone: v.trend === '下降' ? 'down' : v.trend === '上升' ? 'up' : 'neutral' })
  if (v.rel) out.push({ label: `${v.rel}大盘`, tone: v.rel === '跑输' ? 'warn' : v.rel === '跑赢' ? 'ok' : 'neutral' })
  if (v.vol_label) out.push({ label: v.vol_label, tone: 'neutral' })
  if (v.liq) out.push({ label: v.liq, tone: v.liq === '难出手' ? 'warn' : 'neutral' })
  if (v.conditional) out.push({ label: '条件单：K 线按整个磨损档算', tone: 'warn' })
  return out
}
