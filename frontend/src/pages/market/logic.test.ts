import { describe, expect, it } from 'vitest'
import type { Bar } from '@/api/types'
import { yearRange } from './logic'

const bar = (iso: string, c: number): Bar => [iso, c, c, c + 5, c - 5]

describe('yearRange', () => {
  it('uses closes for the high and low, and places today in between', () => {
    const r = yearRange(
      [bar('2025-10-17T05:00:00Z', 1672.05), bar('2025-10-24T05:00:00Z', 651.66), bar('2026-09-27T05:00:00Z', 796.48)],
      'Asia/Shanghai',
    )!
    expect(r).toMatchObject({ lo: 651.66, loDate: '25-10-24', hi: 1672.05, hiDate: '25-10-17' })
    expect(r.pos).toBeCloseTo(14.2, 1)
    expect(r.fromHigh).toBe(-52.4)
  })
  it('is null without bars and centred when flat', () => {
    expect(yearRange([], 'Asia/Shanghai')).toBeNull()
    expect(yearRange([bar('2026-09-27T05:00:00Z', 5)], 'Asia/Shanghai')!.pos).toBe(50)
  })
})
