import { describe, expect, it } from 'vitest'
import type { Bar } from '@/api/types'
import { TOKENS } from '@/theme/tokens'
import { candleColors, clipNote, lastN, maSeries, toCandle, weekGaps, windowBars } from './kline'

describe('toCandle', () => {
  it('reorders SteamDT [ts, open, close, high, low] into ECharts [open, close, low, high]', () => {
    const bar: Bar = ['2026-09-20T05:00:00Z', 10, 12, 13, 9]
    expect(toCandle(bar)).toEqual([10, 12, 9, 13])
  })
})

describe('maSeries', () => {
  it('is null until there are n closes, then the plain average', () => {
    expect(maSeries([1, 2, 3, 4], 3)).toEqual([null, null, 2, 3])
  })
  it('handles n longer than the series', () => {
    expect(maSeries([1, 2], 7)).toEqual([null, null])
  })
})

describe('windows', () => {
  it('maps windows to bar counts and slices the tail', () => {
    expect(windowBars('1m')).toBe(30)
    expect(windowBars('3m')).toBe(90)
    expect(windowBars('1y')).toBe(365)
    expect(lastN([1, 2, 3, 4], 2)).toEqual([3, 4])
    expect(lastN([1, 2], 30)).toEqual([1, 2])
  })
})

describe('clipNote', () => {
  it('says how many wicks were clipped and the extreme', () => {
    expect(clipNote({ lo: 900, hi: 931.5, clipped: 1, extreme: 11178 })).toBe(
      '1 根 K 线的影线远超正常范围（极值 11178.00），已截断显示',
    )
  })
  it('is silent when nothing was clipped or there is no range', () => {
    expect(clipNote({ lo: 1, hi: 2, clipped: 0, extreme: null })).toBeNull()
    expect(clipNote(null)).toBeNull()
  })
})

describe('candleColors', () => {
  it('draws up (close >= open) in the up token (red) and down in the down token (green)', () => {
    for (const t of [TOKENS.light, TOKENS.dark]) {
      expect(candleColors(t)).toEqual({ color: t.up, color0: t.down, borderColor: t.up, borderColor0: t.down })
    }
    expect(TOKENS.light.up).toBe('#C8313A')
    expect(TOKENS.light.down).toBe('#0E7A4F')
  })
})

describe('weekGaps', () => {
  it('finds stretches where weekly bars are missing', () => {
    const w = (iso: string): Bar => [iso, 1, 1, 1, 1]
    const bars = [w('2023-11-25T00:00:00Z'), w('2023-12-02T00:00:00Z'), w('2024-05-11T00:00:00Z'), w('2024-05-18T00:00:00Z')]
    // 12-02 → 05-11 隔了 161 天 = 23 周，中间缺 22 周
    expect(weekGaps(bars)).toEqual([{ index: 2, weeks: 22 }])
  })
  it('ignores the normal 7-day step', () => {
    expect(weekGaps([['2026-09-13T00:00:00Z', 1, 1, 1, 1], ['2026-09-20T00:00:00Z', 1, 1, 1, 1]])).toEqual([])
  })
})
