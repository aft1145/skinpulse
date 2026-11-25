import { describe, expect, it } from 'vitest'
import type { PriceRow } from '@/api/types'
import { compareRows, orderTab, progressPct, progressText, rankRangeText, rankTitle } from './logic'

describe('rankRangeText', () => {
  it('summarises the 7-day ranks', () => {
    expect(rankRangeText([2, 3, 2, null, 3, 2, 2])).toBe('第 2 ↔ 3 名来回')
    expect(rankRangeText([4, 4, null])).toBe('一直第 4 名')
    expect(rankRangeText([null, null])).toBe('近 7 天无名次记录')
  })
})

describe('progress', () => {
  it('is my price over the top price, 100 when already first', () => {
    expect(progressPct({ rank: 2, my_price: 7, top1_price: 10.3 })).toBe(68)
    expect(progressPct({ rank: 1, my_price: 7, top1_price: 7 })).toBe(100)
    expect(progressPct({ rank: 3, my_price: null, top1_price: 10 })).toBe(0)
  })
  it('talks about distance only, never about push notifications', () => {
    const t = progressText({ rank: 2, my_price: 7, top1_price: 10.3, gap_ratio: 1.4714 })
    expect(t).toBe('已到榜首的 68% · 榜首是我的 1.47 倍')
    expect(t).not.toMatch(/推送|告警/)
    expect(progressText({ rank: 1, my_price: 7, top1_price: 7, gap_ratio: 1 })).toBe('排在第 1 名')
  })
})

describe('orderTab', () => {
  it('shows rank and price', () => {
    expect(orderTab({ rank: 2, my_price: 7 })).toBe('第 2 名 · 7.00')
    expect(orderTab({ rank: null, my_price: 7 })).toBe('未上榜 · 7.00')
  })
})

describe('compareRows', () => {
  const row = (platform: string, sell: number | null, bid: number | null, sc: number | null = 5, bc: number | null = 2): PriceRow => ({
    platform, name: platform, sell, sell_count: sell == null ? null : sc, bid, bid_count: bid == null ? null : bc, updated_at: null,
  })
  it('puts sells, bids and my price on one scale', () => {
    const { rows, myPct } = compareRows([row('YOUPIN', 12.39, 10.3), row('STEAM', 16, null)], 7)
    const max = 16 * 1.08
    expect(rows[0].youpin).toBe(true)
    expect(rows[0].sellPct).toBeCloseTo((12.39 / max) * 100, 1)
    expect(myPct).toBeCloseTo((7 / max) * 100, 1)
    expect(rows[1].bidPct).toBeNull()
  })
  it('writes — for a missing side, never 0', () => {
    const { rows } = compareRows([row('STEAM', 16, null)], null)
    expect(rows[0].bidText).toBe('—')
    expect(rows[0].bidSub).toBe('')
    expect(rows[0].sellSub).toBe('5 件')
  })
})

describe('rankTitle', () => {
  it('says why there is no rank instead of splicing a label into 排在…名', () => {
    expect(rankTitle({ rank: 2, beyond: null, checked: true })).toBe('我的求购排名第 2')
    expect(rankTitle({ rank: null, beyond: 20, checked: true })).toBe('我的求购排在第 20 名以后')
    expect(rankTitle({ rank: null, beyond: null, checked: true })).toBe('队列中未找到我的求购')
    expect(rankTitle({ rank: null, beyond: null, checked: false })).toBe('我的求购尚未检查')
  })
})
