import { describe, expect, it } from 'vitest'
import type { PnlSummary } from '@/api/types'
import { categoryRows, flagText, itemSub, lotText, platformLabel, monthBars, sellSub, signedPercent, signedYuan, summaryView, tone, whereText, yuan } from './logic'

const S: PnlSummary = {
  as_of: '2026-11-24', computed_at: '2026-11-24T16:45:00Z', basis_default: 'market',
  realized: { total: 322.77, matched: 14, unknown: 24, unknown_net: 568.02, sells: 38 },
  holding: { qty: 316, cost: 10191.52, cost_priced: 10191.52, value_mkt: 7133.76, value_net: 6991.8, unpriced_qty: 0, flagged: 4 },
  where: { inv: 222, lease: 39, unknown: 55, unknown_cost: 3502.4 },
  youpin: { pnl: -2063.27, n: 201 }, months: [], categories: [], notes: null,
}

describe('金额', () => {
  it('千分位、负号用 −、缺失写 —', () => {
    expect(yuan(10191.52)).toBe('¥10,191.52')
    expect(yuan(-2734.99)).toBe('−¥2,734.99')
    expect(yuan(null)).toBe('—')
    expect(signedYuan(322.77)).toBe('+¥322.77')
    expect(signedYuan(-3057.76)).toBe('−¥3,057.76')
    expect(signedYuan(0)).toBe('¥0.00')
    expect(signedPercent(-0.30003)).toBe('−30.0%')
    expect(signedPercent(null)).toBe('—')
    expect([tone(-1), tone(1), tone(0), tone(null)]).toEqual(['loss', 'gain', 'flat', 'flat'])
  })
})

describe('焦点卡片', () => {
  it('按市价：总盈亏 = 已实现 + 浮动', () => {
    const v = summaryView(S, 'market')!
    expect(v.float).toBeCloseTo(-3057.76, 2)
    expect(v.total).toBeCloseTo(-2734.99, 2)
    expect(v.floatPct).toBeCloseTo(-0.30003, 4)
    expect(v.valueLabel).toBe('市值')
    expect(v.valueRatio).toBeCloseTo(0.7, 2)
  })
  it('按到手价只改市值相关的数', () => {
    const v = summaryView(S, 'net')!
    expect(v.realized).toBe(322.77)
    expect(v.float).toBeCloseTo(-3199.72, 2)
    expect(v.basisLabel).toBe('浮动按到手价')
  })
  it('没快照时为 null；没价的成本不进浮动', () => {
    expect(summaryView({ ...S, holding: null }, 'market')).toBeNull()
    const v = summaryView({ ...S, holding: { ...S.holding!, cost: 100, cost_priced: 60, value_mkt: 50 } }, 'market')!
    expect(v.float).toBe(-10)
  })
})

describe('每月柱子', () => {
  it('有值的归一，没卖出的是底线，横轴每 3 个月一标', () => {
    const bars = monthBars([['2025-05', 48.36], ['2025-06', 0], ['2025-07', 103.91], ['2025-08', -5], ['2025-09', 1.64]], 170)
    expect(bars.map((b) => b.height)).toEqual([79, 3, 170, 8, 4])
    expect(bars.map((b) => b.label)).toEqual(['48', '', '104', '−5.00', '1.64'])
    expect(bars.map((b) => b.axis)).toEqual(['25.05', '', '', '25.08', '25.09'])
    expect(bars[3].tone).toBe('loss')
    expect(bars[1].title).toBe('2025-06：无卖出')
  })
})

describe('按品类', () => {
  it('浮动升序，条长按最大绝对值', () => {
    const rows = categoryRows([
      { key: 'knife', label: '刀', qty: 4, cost: 2559, cost_priced: 2559, value_mkt: 1180.22, value_net: 1156.73 },
      { key: 'graffiti', label: '涂鸦', qty: 13, cost: 10.83, cost_priced: 10.83, value_mkt: 45.38, value_net: 44.48 },
    ], 'market')
    expect(rows.map((r) => r.key)).toEqual(['knife', 'graffiti'])
    expect(rows[0].width).toBe('100.0%')
    expect(rows[1].text).toBe('+¥34.55')
    expect(rows[1].tone).toBe('gain')
  })
  it('一件都没价的品类写「—」、排最后（评审 2026-11-24）', () => {
    const rows = categoryRows([
      { key: 'sticker', label: '印花', qty: 5, cost: 5, cost_priced: 0, value_mkt: null, value_net: null },
      { key: 'rifle', label: '步枪', qty: 1, cost: 10, cost_priced: 10, value_mkt: 12, value_net: 11.76 },
    ], 'market')
    expect(rows.map((r) => [r.key, r.text, r.width])).toEqual([['rifle', '+¥2.00', '100.0%'], ['sticker', '—', '0%']])
  })
})

describe('在哪、标记、配到的买入', () => {
  it('一处写一处，几处写件数', () => {
    expect(whereText({ inv: 1, lease: 0, unknown: 0 })).toEqual({ text: '库存', key: 'inv' })
    expect(whereText({ inv: 0, lease: 0, unknown: 2 })).toEqual({ text: '不在库存', key: 'unknown' })
    expect(whereText({ inv: 1, lease: 1, unknown: 0 })).toEqual({ text: '库存 1 · 租出 1', key: 'mixed' })
  })
  it('价格标记', () => {
    expect(flagText('thin')).toContain('在售数量少')
    expect(flagText(null)).toBeNull()
  })
  it('配到的买入按本地日期', () => {
    expect(lotText([{ bought_at: '2026-10-04T17:00:00Z', unit: 100, qty: 1, platform: 'YOUPIN', account_id: 1 }], 'Asia/Shanghai')).toBe('2026-10-05 悠悠买入 ¥100')
    expect(lotText([{ bought_at: '2026-10-04T01:00:00Z', unit: 2.5, qty: 1, platform: 'BUFF', account_id: 2 }, { bought_at: '2026-10-05T01:00:00Z', unit: 3, qty: 1, platform: 'YOUPIN', account_id: 1 }], 'Asia/Shanghai'))
      .toBe('2026-10-04 BUFF买入 ¥2.50 等 2 批')
    expect(lotText([], 'Asia/Shanghai')).toBe('')
    expect(sellSub('崭新出厂', 2)).toBe('崭新出厂 × 2')
    expect(sellSub(null, 3)).toBe('× 3')
  })
})

describe('平台（盈亏接 BUFF）', () => {
  it('平台名和单品副标题', () => {
    expect(platformLabel('YOUPIN')).toBe('悠悠')
    expect(platformLabel('BUFF')).toBe('BUFF')
    expect(itemSub('久经沙场', '步枪', ['YOUPIN'])).toBe('久经沙场')
    expect(itemSub(null, '刀', ['YOUPIN', 'BUFF'])).toBe('刀 · BUFF 买')
  })
})
