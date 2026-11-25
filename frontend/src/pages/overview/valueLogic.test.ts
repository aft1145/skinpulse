import { describe, expect, it } from 'vitest'
import type { Order, OrderValue, ValuationSummary } from '@/api/types'
import {
  ADVICE,
  VALUE_FILTERS,
  adviceTone,
  filterByAdvice,
  filterByValue,
  marginTone,
  rangeMarks,
  sortByTakeMargin,
  valueHeadline,
  valueStateFromQuery,
  valueStateToQuery,
  waterfall,
} from './valueLogic'

const V: OrderValue = {
  as_of: '2026-09-26', bars: 365, pct: 0.1, pct_label: '一年低位', lo: 90, hi: 200, cur: 100,
  trend: '下降', rel: '持平', c30: -0.08, c90: -0.2, b30: -0.05, b90: -0.17, ex30: -0.03,
  vol7: 0.06, vol_label: '波动中', q10: -0.1, liq: '一般', listed: 40, spread: 0.04,
  exit: 97.03, exit_where: 'YOUPIN', exit_sell: 100, m_my: 0.9406, m_top: 0.617, m_bad: 0.7466, conditional: false,
}
const order = (no: string, value: Partial<OrderValue> | null, extra: Partial<Order> = {}) =>
  ({ purchase_no: no, title: no, tag: null, rank: 3, my_price: 50, top1_price: 60, value: value && { ...V, ...value }, ...extra }) as Order

const SUMMARY: ValuationSummary = {
  as_of: '2026-09-26', computed_at: '2026-09-26T16:38:00Z', snapshot_date: '2026-09-26',
  counts: { take: 2, take_loss: 1, low: 3, fall: 1, hard: 1, evaluated: 4, insufficient: 0 },
  target_margin: 0.15, fees: { YOUPIN: 0.01, BUFF: 0.015, withdraw: 0.01, undercut: 0.01 },
}

describe('view state in the URL', () => {
  it('round-trips and defaults to the rank view', () => {
    expect(valueStateFromQuery({})).toEqual({ view: 'rank', vf: null, adv: null })
    expect(valueStateToQuery({ view: 'rank', vf: null, adv: null })).toEqual({})
    const s = { view: 'value' as const, vf: 'fall' as const, adv: 'cancel' as const }
    expect(valueStateFromQuery(valueStateToQuery(s))).toEqual(s)
    expect(valueStateToQuery(s)).toEqual({ view: 'value', vf: 'fall', adv: 'cancel' })
    expect(valueStateFromQuery({ view: 'bogus', vf: 'nope' })).toEqual({ view: 'rank', vf: null, adv: null })
    expect(valueStateFromQuery({ view: 'value', adv: 'sell' })).toEqual({ view: 'value', vf: null, adv: null })
    // 筛选只在价位视图里有意义
    expect(valueStateToQuery({ view: 'rank', vf: 'low', adv: 'hold' })).toEqual({})
    expect(valueStateFromQuery({ view: 'rank', adv: 'hold' }).adv).toBeNull()
  })
})

describe('filters and sorting', () => {
  const orders = [
    order('a', { pct: 0.1, trend: '下降', rel: '跑输', liq: '一般', m_top: 0.2 }),
    order('b', { pct: 0.5, pct_label: '一年中位', trend: '下降', rel: '持平', liq: '难出手', m_top: -0.05 }),
    order('c', { pct: 0.2, trend: '横盘', rel: '跑输', liq: '好出手', m_top: null }),
    order('d', null),
    order('e', { pct: null, pct_label: null, m_top: 0.9 }),
  ]
  it('filters by value labels, skipping orders without a value', () => {
    expect(filterByValue(orders, 'low').map((o) => o.purchase_no)).toEqual(['a', 'c'])
    expect(filterByValue(orders, 'fall').map((o) => o.purchase_no)).toEqual(['a'])
    expect(filterByValue(orders, 'hard').map((o) => o.purchase_no)).toEqual(['b'])
    expect(filterByValue(orders, null)).toHaveLength(5)
    expect(VALUE_FILTERS.map((f) => f.key)).toEqual(['low', 'fall', 'hard'])
  })
  it('sorts by the take-first margin, nulls last', () => {
    expect(sortByTakeMargin(orders).map((o) => o.purchase_no)).toEqual(['e', 'a', 'b', 'c', 'd'])
  })
})

describe('headline', () => {
  it('counts orders that keep the target margin after taking first place', () => {
    const orders = [
      order('a', { m_top: 0.2 }), order('b', { m_top: 0.15 }), order('c', { m_top: 0.05 }),
      order('d', { m_top: -0.01 }), order('e', { m_top: null }), order('f', null),
    ]
    const h = valueHeadline(SUMMARY, orders)
    expect(h.n).toBe(2)
    expect(h.target).toBe('15%')
    expect(h.sub).toBe('其余 2 张抢到第 1 后差价不到 15%，其中 1 张会亏 · 按 09-26 价格快照')
  })
  it('says when there is no snapshot', () => {
    expect(valueHeadline({ ...SUMMARY, snapshot_date: null }, []).sub).toContain('无价格快照')
  })
})

describe('tones and ranges', () => {
  it('colours margins: loss, target met, plain, missing', () => {
    expect(marginTone(-0.01, 0.15)).toBe('loss')
    expect(marginTone(0.15, 0.15)).toBe('accent')
    expect(marginTone(0.05, 0.15)).toBe('plain')
    expect(marginTone(null, 0.15)).toBe('muted')
  })
  it('places low, now and high on the one-year rail', () => {
    const m = rangeMarks({ lo: 90, hi: 200, cur: 101 })
    expect(m.map((x) => x.key)).toEqual(['lo', 'cur', 'hi'])
    expect(m[0].left).toBe(3)
    expect(m[2].left).toBe(97)
    expect(m[1].left).toBeCloseTo(3 + (11 / 110) * 94)
    expect(rangeMarks({ lo: null, hi: null, cur: null })).toEqual([])
    expect(rangeMarks({ lo: 5, hi: 5, cur: 5 })[1].left).toBe(3)       // 一年没动过：别除以 0
  })
})

describe('waterfall', () => {
  it('walks from the listing price to what lands in the wallet and the three margins', () => {
    const rows = waterfall({ ...V, exit_where: 'BUFF', exit_sell: 105, exit: 101.37 }, { my_price: 50, top1_price: 60, rank: 3 }, SUMMARY.fees)
    expect(rows.map((r) => [r.label, r.value])).toEqual([
      ['BUFF 在售最低', '105.00'],
      ['让价 1%、手续费 1.5%、提现 1%', '−3.63'],
      ['到手', '101.37'],
      ['按我的价 50.00 成交', '+94.1%'],
      ['抢到第 1（出价 60.01）', '+61.7%'],
      ['按我的价，7 天后碰上坏情况（−10.0%）', '+74.7%'],
    ])
    expect(rows[2].strong).toBe(true)
  })
  it('says already first and skips missing pieces', () => {
    const rows = waterfall({ ...V, m_top: null, q10: null, m_bad: null }, { my_price: 50, top1_price: 50, rank: 1 }, SUMMARY.fees)
    expect(rows.map((r) => r.label)).toContain('已是第 1')
    expect(rows.some((r) => r.label.startsWith('按我的价，7 天后'))).toBe(false)
    expect(waterfall({ ...V, exit: null, exit_sell: null, exit_where: null }, { my_price: 50, top1_price: 60, rank: 3 }, SUMMARY.fees)).toEqual([])
  })
  it('never shows a negative margin without its minus sign', () => {
    const rows = waterfall({ ...V, m_top: -0.02 }, { my_price: 50, top1_price: 99, rank: 2 }, SUMMARY.fees)
    const take = rows.find((r) => r.label.startsWith('抢到第 1'))!
    expect(take.value).toBe('−2.0%')
    expect(take.tone).toBe('loss')
  })
})

describe('advice', () => {
  const adv = (kind: 'cancel' | 'lower' | 'raise' | 'hold' | 'none') => ({ kind, label: '', rule: '', price: null })
  it('filters by advice kind; orders without valuation or advice drop out', () => {
    const orders = [order('a', { advice: adv('cancel') }), order('b', { advice: adv('hold') }), order('c', null), order('d', { advice: null })]
    expect(filterByAdvice(orders, 'cancel').map((o) => o.purchase_no)).toEqual(['a'])
    expect(filterByAdvice(orders, null)).toHaveLength(4)
  })
  it('order and tones follow the mockup', () => {
    expect(ADVICE.map((a) => a.label)).toEqual(['考虑撤单', '考虑降价', '可以加价', '保持', '不评价'])
    expect([adviceTone('cancel'), adviceTone('lower'), adviceTone('raise'), adviceTone('hold'), adviceTone(null)])
      .toEqual(['warn', 'warn', 'accent', 'neutral', 'muted'])
  })
})
