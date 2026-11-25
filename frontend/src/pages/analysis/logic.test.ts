import { describe, expect, it } from 'vitest'
import type { DailyClose } from '@/api/types'
import { bridges, distCaption, headline, series, slots } from './logic'

const day = (date: string, t3: number | null, t2: number | null = t3, r1: number | null = t3): DailyClose => ({
  date, rank1: r1, top2: t2, top3: t3, total: t3 == null ? 0 : 400,
})

describe('headline', () => {
  const sum = (top3: number, c: number | null) => ({ top3, compare: c == null ? null : { date: '2026-09-21', top3: c }, moved_up: 0, moved_down: 0 })
  it('compares with the earliest day of the week that has data', () => {
    expect(headline(sum(17, 33))).toEqual({ top3: 17, lead: '，比 09-21 少 ', n: 16, tail: ' 张', dir: 'down' })
    expect(headline(sum(20, 18))).toMatchObject({ lead: '，比 09-21 多 ', n: 2, dir: 'up' })
    expect(headline(sum(5, 5))).toMatchObject({ lead: '，和 09-21 一样', n: null })
    expect(headline(sum(5, null))).toMatchObject({ lead: '', n: null })
  })
})

describe('slots', () => {
  const daily = [
    day('2026-08-29', null), day('2026-08-30', 13), day('2026-08-31', null), day('2026-09-01', null),
    day('2026-09-02', 18), day('2026-09-03', 15), day('2026-09-04', null),
  ]
  it('drops leading/trailing empty days and collapses inner runs into one break', () => {
    const s = slots(daily)
    expect(s.map((x) => x.label)).toEqual(['08-30', '停 2 天', '09-02', '09-03'])
    expect(s[1]).toMatchObject({ kind: 'break', days: 2 })
  })
  it('lines break at the gap and get one dashed bridge per break', () => {
    const s = slots(daily)
    expect(series(s, 'top3')).toEqual([13, null, 18, 15])
    expect(bridges(s, 'top3')).toEqual([[13, 15.5, 18, null]])
  })
  it('is empty when there is no data at all', () => {
    expect(slots([day('2026-09-01', null)])).toEqual([])
  })
})

describe('distCaption', () => {
  it('names the biggest bucket and mentions unranked orders apart', () => {
    const d = [{ label: '1', min: 1, max: 1, count: 0 }, { label: '11–20', min: 11, max: 20, count: 163 }, { label: '51+', min: 51, max: null, count: 13 }]
    expect(distCaption({ distribution: d, unranked: { beyond: 0, not_found: 0, unchecked: 0 } })).toBe('176 张有名次 · 最多的是 11–20 名（163 张）')
    expect(distCaption({ distribution: d, unranked: { beyond: 2, not_found: 1, unchecked: 1 } })).toBe(
      '176 张有名次 · 最多的是 11–20 名（163 张）；另有 2 张在抽样之外、1 张查询后未找到、1 张尚未检查',
    )
  })
})
