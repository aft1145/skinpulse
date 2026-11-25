import { describe, expect, it } from 'vitest'
import type { OrderValue } from '@/api/types'
import { chips, marginsFor, relSeries } from './valueLogic'

const V = {
  pct: 0.1, pct_label: '一年低位', trend: '下降', rel: '跑输', vol_label: '波动中', liq: '难出手',
  exit: 150, q10: -0.1, conditional: false,
} as OrderValue

describe('margins for the picked order', () => {
  it('matches the backend rule', () => {
    expect(marginsFor(V, { my_price: 100, top1_price: 110, rank: 3 })).toEqual({ m_my: 0.5, m_top: 0.363512, m_bad: 0.35 })
    expect(marginsFor(V, { my_price: 100, top1_price: 110, rank: 1 }).m_top).toBeNull()      // 已是第 1
    expect(marginsFor(V, { my_price: 100, top1_price: null, rank: 3 }).m_top).toBeNull()
    expect(marginsFor({ ...V, q10: null }, { my_price: 100, top1_price: 110, rank: 3 }).m_bad).toBeNull()
    expect(marginsFor({ ...V, exit: null }, { my_price: 100, top1_price: 110, rank: 3 })).toEqual({ m_my: null, m_top: null, m_bad: null })
    expect(marginsFor(V, { my_price: 0, top1_price: 110, rank: 3 }).m_my).toBeNull()        // 0 = 没有数据
  })
})

describe('90-day item vs market', () => {
  it('rebases both to 100 at their first known close and keeps gaps', () => {
    const s = relSeries([
      ['2026-06-29', null, 1000],
      ['2026-06-30', 50, 1010],
      ['2026-07-01', 55, null],
      ['2026-07-02', 45, 900],
    ])
    expect(s.dates).toEqual(['06-29', '06-30', '07-01', '07-02'])
    expect(s.item).toEqual([null, 100, 110, 90])
    expect(s.broad).toEqual([100, 101, null, 90])
  })
  it('is empty when there is nothing to draw', () => {
    expect(relSeries([['2026-06-29', null, null]]).item).toEqual([null])
  })
})

describe('chips', () => {
  it('lists the labels with tones, skipping unknown ones', () => {
    expect(chips(V).map((c) => [c.label, c.tone])).toEqual([
      ['一年低位', 'accent'], ['下降', 'down'], ['跑输大盘', 'warn'], ['波动中', 'neutral'], ['难出手', 'warn'],
    ])
    const few = chips({ ...V, pct_label: null, trend: null, rel: null, vol_label: null, liq: null, conditional: true })
    expect(few.map((c) => c.label)).toEqual(['条件单：K 线按整个磨损档算'])
  })
})
