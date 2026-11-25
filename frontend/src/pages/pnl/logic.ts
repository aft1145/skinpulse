// 盈亏页（盈亏 spec §7）的纯逻辑：金额文字、口径切换、柱高、在哪、配到的买入。vitest 覆盖。
// 铁律：缺失写「—」不写 0；负号用「−」；颜色从不单独表达含义（正负号总在）。
import type { PnlBasis, PnlCategory, PnlItem, PnlLot, PnlSummary } from '@/api/types'
import { DASH, localDate } from '@/format'

export type Tone = 'gain' | 'loss' | 'flat'

/** ¥1,234.56；null 写「—」 */
export function yuan(v: number | null | undefined): string {
  if (v == null) return DASH
  const s = Math.abs(v).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return (v < 0 ? '−' : '') + '¥' + s
}

/** 带正负号的金额：+¥1.00 / −¥1.00；0 不带号 */
export function signedYuan(v: number | null | undefined): string {
  if (v == null) return DASH
  if (Math.abs(v) < 0.005) return '¥0.00'
  return (v < 0 ? '−' : '+') + yuan(Math.abs(v))
}

export function tone(v: number | null | undefined): Tone {
  if (v == null || Math.abs(v) < 0.005) return 'flat'
  return v < 0 ? 'loss' : 'gain'
}

export function signedPercent(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return DASH
  if (Math.abs(v) < 0.0005) return '0.0%'
  return (v < 0 ? '−' : '+') + Math.abs(v * 100).toFixed(1) + '%'
}

export interface SummaryView {
  total: number
  realized: number
  float: number
  floatPct: number | null
  cost: number
  value: number
  /** 市值（或到手）占成本的比例，画横条用，封顶 1 */
  valueRatio: number
  valueLabel: '市值' | '到手'
  basisLabel: string
}

/** 焦点卡片的数：浮动 = 有价部分的市值（或到手）− 有价部分的成本；总盈亏 = 已实现 + 浮动 */
export function summaryView(s: PnlSummary, basis: PnlBasis): SummaryView | null {
  if (!s.holding || !s.realized) return null
  const h = s.holding
  const value = basis === 'net' ? h.value_net : h.value_mkt
  const float = value - h.cost_priced
  return {
    total: s.realized.total + float,
    realized: s.realized.total,
    float,
    floatPct: h.cost_priced > 0 ? float / h.cost_priced : null,
    cost: h.cost,
    value,
    valueRatio: h.cost > 0 ? Math.min(1, value / h.cost) : 0,
    valueLabel: basis === 'net' ? '到手' : '市值',
    basisLabel: basis === 'net' ? '浮动按到手价' : '浮动按市价',
  }
}

export interface MonthBar {
  key: string
  label: string
  height: number
  on: boolean
  axis: string
  title: string
}

/** 每月已实现的柱子：有值的按最大值归一到 maxH，没有卖出的画 3px 的底线；横轴每 3 个月和最后一个月标一次。
 *  亏的月份也画（高度按绝对值），颜色由调用方按 tone 给。 */
export function monthBars(months: [string, number][], maxH = 170): (MonthBar & { tone: Tone })[] {
  const max = Math.max(0, ...months.map(([, v]) => Math.abs(v)))
  return months.map(([m, v], i) => {
    const on = Math.abs(v) >= 0.005
    const yymm = m.slice(2).replace('-', '.')
    return {
      key: m,
      label: on ? (v < 0 ? '−' : '') + (Math.abs(v) >= 10 ? Math.round(Math.abs(v)).toString() : Math.abs(v).toFixed(2)) : '',
      height: on && max > 0 ? Math.max(4, Math.round((Math.abs(v) / max) * maxH)) : 3,
      on,
      tone: tone(v),
      axis: i % 3 === 0 || i === months.length - 1 ? yymm : '',
      title: `${m}：${on ? signedYuan(v) : '无卖出'}`,
    }
  })
}

export interface CategoryRow {
  key: string
  label: string
  qty: number
  /** null：这个品类一件都没有市价（不是 0） */
  float: number | null
  text: string
  tone: Tone
  width: string
}

/** 按品类：浮动升序（亏得多的在上），条长按最大绝对值归一；一件都没价的品类写「—」、排最后 */
export function categoryRows(cats: PnlCategory[], basis: PnlBasis): CategoryRow[] {
  const rows = cats.map((c) => {
    const v = basis === 'net' ? c.value_net : c.value_mkt
    return { key: c.key, label: c.label, qty: c.qty, float: v == null ? null : v - c.cost_priced }
  })
  rows.sort((a, b) => (a.float == null ? 1 : b.float == null ? -1 : a.float - b.float))
  const max = Math.max(0, ...rows.map((r) => Math.abs(r.float ?? 0)))
  return rows.map((r) => ({
    ...r,
    text: signedYuan(r.float),
    tone: tone(r.float),
    width: r.float == null ? '0%' : max > 0 ? `${Math.max(2, (Math.abs(r.float) / max) * 100).toFixed(1)}%` : '2%',
  }))
}

export type WhereKey = 'inv' | 'lease' | 'unknown' | 'mixed'
// 「不在库存」：库存和悠悠租出里都没有、也没在悠悠或 BUFF 卖掉；多半在 BUFF App 里出租（网页拿不到出租列表）
const WHERE_LABEL = { inv: '库存', lease: '租出', unknown: '不在库存' } as const

/** 单品「在哪」：都在一处就写那一处；分在几处写「库存 1 · 租出 1」 */
export function whereText(w: PnlItem['where']): { text: string; key: WhereKey } {
  const parts = (['inv', 'lease', 'unknown'] as const).filter((k) => w[k] > 0)
  if (parts.length === 1) return { text: WHERE_LABEL[parts[0]], key: parts[0] }
  if (!parts.length) return { text: DASH, key: 'mixed' }
  return { text: parts.map((k) => `${WHERE_LABEL[k]} ${w[k]}`).join(' · '), key: 'mixed' }
}

export function flagText(flag: PnlItem['price_flag']): string | null {
  if (flag === 'thin') return '悠悠在售数量少，价格可能不稳定'
  if (flag === 'spread') return '悠悠与 BUFF 在售价差距较大'
  return null
}

const PLATFORM_LABEL: Record<string, string> = { YOUPIN: '悠悠', BUFF: 'BUFF' }

export function platformLabel(p: string | null | undefined): string {
  return p ? (PLATFORM_LABEL[p] ?? p) : ''
}

/** 卖出配到的买入：「2026-10-05 悠悠买入 ¥100」，多批再加「等 N 批」 */
export function lotText(lots: PnlLot[], tz: string): string {
  if (!lots.length) return ''
  const first = lots[0]
  const unit = Number.isInteger(first.unit) ? String(first.unit) : first.unit.toFixed(2)
  return `${localDate(first.bought_at, tz)} ${platformLabel(first.platform)}买入 ¥${unit}` + (lots.length > 1 ? ` 等 ${lots.length} 批` : '')
}

/** 单品名字下面那行：磨损或类别；不只在悠悠买过时加「BUFF 买」之类 */
export function itemSub(sub: string | null, categoryLabel: string, platforms: string[]): string {
  const base = sub ?? categoryLabel
  const others = platforms.filter((p) => p !== 'YOUPIN')
  return others.length ? `${base} · ${others.map(platformLabel).join('、')} 买` : base
}

/** 卖出数量写在磨损后面：「崭新出厂 × 2」 */
export function sellSub(sub: string | null, qty: number): string {
  const s = sub ?? ''
  return qty > 1 ? (s ? `${s} × ${qty}` : `× ${qty}`) : s
}

const BASIS_KEY = 'skinpulse.pnl.basis'

export function readBasis(fallback: PnlBasis): PnlBasis {
  try {
    const v = localStorage.getItem(BASIS_KEY)
    return v === 'market' || v === 'net' ? v : fallback
  } catch {
    return fallback
  }
}

export function writeBasis(v: PnlBasis): void {
  try {
    localStorage.setItem(BASIS_KEY, v)
  } catch {
    /* 存不了只是下次不记得 */
  }
}
