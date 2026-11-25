import { describe, expect, it } from 'vitest'
import type { Fill } from '@/api/types'
import { barsGeometry, fillTime, howKnown } from './fillsLogic'

const TZ = 'Asia/Shanghai'
const fill = (extra: Partial<Fill> = {}): Fill => ({
  purchase_no: 'P', template_id: 1, title: 'SSG 08 | 渐变强酸', tag: '崭新出厂', star: false, stattrak: false,
  price: 3.9, qty: 1, filled_at: '2026-10-02T01:55:14Z', exact: true, detected_at: '2026-10-02T02:00:00Z',
  notice: 'sent', notice_kind: 'FILLED', notice_at: '2026-10-02T02:00:05Z', ...extra,
})

describe('howKnown：这次成交是怎么知道的', () => {
  it('交易中时推送，说明提前多久', () => {
    expect(howKnown(fill({ notice_kind: 'IN_TRADE', notice_at: '2026-10-01T09:50:08Z', filled_at: '2026-10-01T10:16:06Z' }), TZ))
      .toEqual({ label: '交易中时推送', tone: 'accent', note: '17:50，提前 26 分钟' })
  })
  it('成交后推送', () => {
    expect(howKnown(fill(), TZ)).toEqual({ label: '成交后推送', tone: 'ok', note: '10:00' })
  })
  it('晚 20 分钟以上算补推', () => {
    expect(howKnown(fill({ detected_at: '2026-10-02T02:20:14Z', notice_at: '2026-10-02T02:20:16Z' }), TZ))
      .toEqual({ label: '成交后补推', tone: 'warn', note: '10:20 推送，延迟 25 分钟' })
  })
  it('未推送', () => {
    expect(howKnown(fill({ notice: null, notice_kind: null, notice_at: null }), TZ))
      .toEqual({ label: '未推送', tone: 'muted', note: '' })
  })
  it('晚多少量到推送：发现得早、推得晚也算补推', () => {
    expect(howKnown(fill({ detected_at: '2026-10-02T02:00:00Z', notice_at: '2026-10-02T13:36:00Z' }), TZ).label).toBe('成交后补推')
  })
  it('跨天的推送时间带日期', () => {
    expect(howKnown(fill({ notice_at: '2026-10-02T17:00:00Z', detected_at: '2026-10-02T17:00:00Z' }), TZ).note)
      .toBe('10-03 01:00 推送，延迟 15 小时 5 分钟')
  })
})

describe('fillTime', () => {
  it('精确时间 MM-DD HH:MM；部分成交加「约」', () => {
    expect(fillTime(fill(), TZ)).toBe('10-02 09:55')
    expect(fillTime(fill({ exact: false, filled_at: '2026-10-02T02:20:00Z' }), TZ)).toBe('约 10-02 10:20')
  })
})

describe('barsGeometry：近 30 天每天成交几件的小柱图', () => {
  const daily: [string, number][] = [['2026-10-01', 0], ['2026-10-02', 2], ['2026-10-03', 1]]
  it('有成交画柱子，没有成交画一道短线，柱高按最大值归一', () => {
    const g = barsGeometry(daily, 30, 54)
    expect(g.bars).toHaveLength(2)
    expect(g.bars[0]).toEqual({ x: 12, y: 6, w: 6, h: 48, day: '2026-10-02', n: 2 })
    expect(g.bars[1].h).toBe(24)
    expect(g.stubs).toEqual([{ x: 0, y: 51, w: 6, h: 3 }])
    expect(g.label).toBe('近 3 天成交 3 件，最多一天 2 件')
  })
  it('全是 0 也不除以 0', () => {
    expect(barsGeometry([['2026-10-01', 0]], 30, 54).bars).toEqual([])
  })
})
