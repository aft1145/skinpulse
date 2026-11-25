import { describe, expect, it } from 'vitest'
import type { Bar } from '@/api/types'
import { TOKENS } from '@/theme/tokens'
import { toCandle } from './kline'
import { areaLineOption, klineView, relLineOption, yBounds } from './options'

// 40 根日线：收盘 100, 101, …, 139；每天 UTC 05:00（北京时间 13:00）
const BARS: Bar[] = Array.from({ length: 40 }, (_, i) => {
  const d = new Date(Date.UTC(2026, 7, 19 + i, 5))
  const c = 100 + i
  return [d.toISOString().replace('.000Z', 'Z'), c - 0.5, c, c + 1, c - 1]
})
const base = { bars: BARS, range: { lo: 99, hi: 140, clipped: 0, extreme: null }, tz: 'Asia/Shanghai', t: TOKENS.light }

describe('yBounds', () => {
  it('keeps the backend range and stretches it to include my price', () => {
    const b = yBounds({ lo: 10, hi: 20, clipped: 0, extreme: null }, 5)!
    expect(b.min).toBeLessThan(5)
    expect(b.max).toBeGreaterThan(20)
  })
  it('without my price uses the range plus a small pad', () => {
    expect(yBounds({ lo: 10, hi: 20, clipped: 0, extreme: null }, null)).toEqual({ min: 9.6, max: 20.4 })
    expect(yBounds(null, 5)).toBeNull()
  })
})

describe('klineView', () => {
  it('shows the last 30 bars for 1m, in ECharts candle order', () => {
    const v = klineView({ ...base, win: '1m', myPrice: null })
    expect(v.candles).toHaveLength(30)
    expect(v.candles[0]).toEqual(toCandle(BARS[10]))
    expect(v.dates[0]).toBe('2026-08-29')
  })
  it('computes moving averages on the full series before slicing the window', () => {
    const v = klineView({ ...base, win: '1m', myPrice: null })
    // 窗口第一根（全序列第 11 根）的 MA7 = 收盘 104..110 的平均
    expect(v.ma7[0]).toBe(107)
    // MA30 在全序列第 30 根才有值 = 窗口第 20 根
    expect(v.ma30[18]).toBeNull()
    expect(v.ma30[19]).toBe(114.5)
  })
  it('draws my price as a marked line and frames it on the y axis', () => {
    const v = klineView({ ...base, win: '1y', myPrice: 90 })
    const series = v.option.series as { type: string; markLine?: { data: { yAxis: number }[] } }[]
    expect(series[0].markLine?.data[0].yAxis).toBe(90)
    const y = v.option.yAxis as { min: number; max: number }
    expect(y.min).toBeLessThan(90)
    expect(y.max).toBeGreaterThan(140)
  })
  it('keeps the y axis fixed while zooming', () => {
    const v = klineView({ ...base, win: '3m', myPrice: null })
    expect((v.option.dataZoom as { filterMode: string }[]).map((z) => z.filterMode)).toEqual(['none', 'none'])
  })
})

describe('areaLineOption', () => {
  const pts: [string, number][] = [['2026-09-26T04:00:00Z', 797], ['2026-09-26T16:00:00Z', 797.5], ['2026-09-27T05:00:00Z', 796.33]]
  it('uses the down colour when the series ends lower, and local hour labels', () => {
    const o = areaLineOption(pts, 'Asia/Shanghai', TOKENS.light)
    const s = (o.series as { lineStyle: { color: string } }[])[0]
    expect(s.lineStyle.color).toBe(TOKENS.light.down)
    expect((o.xAxis as { data: string[] }).data).toEqual(['12:00', '09-27 00:00', '13:00'])
  })
})

describe('klineView all (weekly)', () => {
  const weeks: Bar[] = Array.from({ length: 20 }, (_, i) => {
    // 第 10 根之后空了 5 周
    const d = new Date(Date.UTC(2024, 0, 6 + 7 * i + (i >= 10 ? 35 : 0)))
    return [d.toISOString().replace('.000Z', 'Z'), 10 + i, 10 + i, 11 + i, 9 + i]
  })
  const v = klineView({ bars: weeks, win: 'all', range: { lo: 9, hi: 30, clipped: 0, extreme: null }, myPrice: null, tz: 'Asia/Shanghai', t: TOKENS.light })
  it('shows every weekly bar with MA4 / MA13 by the week', () => {
    expect(v.candles).toHaveLength(20)
    expect(v.maNames).toEqual(['MA4 周', 'MA13 周'])
    expect(v.ma7[3]).toBe(11.5) // 前 4 周收盘 10..13 的平均
    expect(v.ma30[11]).toBeNull()
    expect(v.ma30[12]).toBe(16)
  })
  it('marks the missing weeks instead of silently squeezing them out', () => {
    const series = v.option.series as { markLine?: { data: { xAxis: number; name: string }[] } }[]
    expect(series[1].markLine?.data).toEqual([{ xAxis: 10, name: '缺 5 周' }])
  })
})

describe('relLineOption', () => {
  it('draws the item solid in accent and the market dashed, keeping gaps as null', () => {
    const o = relLineOption({ dates: ['06-29', '06-30', '07-01'], item: [null, 100, 110], broad: [100, 101, null] }, TOKENS.light)
    const [item, broad] = o.series as { name: string; data: (number | null)[]; lineStyle: { color: string; type?: string } }[]
    expect([item.name, item.lineStyle.color, item.lineStyle.type]).toEqual(['它', TOKENS.light.accent, undefined])
    expect([broad.name, broad.lineStyle.color, broad.lineStyle.type]).toEqual(['大盘', TOKENS.light.text3, 'dashed'])
    expect(item.data).toEqual([null, 100, 110])
    expect(broad.data).toEqual([100, 101, null])
    expect((o.xAxis as { data: string[] }).data).toEqual(['06-29', '06-30', '07-01'])
  })
})
