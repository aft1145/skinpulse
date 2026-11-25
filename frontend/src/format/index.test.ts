import { describe, expect, it } from 'vitest'
import {
  bestRank,
  clock,
  localDate,
  signedPct,
  arrowPct,
  signedNum,
  countSpark,
  gapText,
  marketFarText,
  headlineText,
  money,
  rankChange,
  rankSpark,
  refBid,
  relative,
  sellLabel,
  sellSub,
  subLine,
  unrankedLabel,
} from './index'

describe('money / sell', () => {
  it('缺失写「—」，绝不写 0', () => {
    expect(money(null)).toBe('—')
    expect(money(0.3)).toBe('0.30')
    expect(money(12.39)).toBe('12.39')
  })
  it('「无人在售」和「还没查」是两回事', () => {
    expect(sellLabel(null, 0)).toBe('无人在售')
    expect(sellLabel(null, null)).toBe('—')
    expect(sellLabel(64.49, 3)).toBe('64.49')
    expect(sellSub(3)).toBe('在售 3 件')
    expect(sellSub(0)).toBe('')
    expect(sellSub(null)).toBe('')
  })
})

describe('gap / ref bid / sub line', () => {
  it('差价倍数，离第 1 太远的写明难成交', () => {
    expect(gapText(1.4714, false)).toBe('×1.47')
    expect(gapText(10.7, true)).toBe('×10.7 难成交')
    expect(marketFarText(487.1)).toBe('离市价 487.1 倍 难成交')
    expect(marketFarText(2.63)).toBe('离市价 2.63 倍 难成交')
    expect(marketFarText(null)).toBe('离市价太远 难成交')
    expect(gapText(null, false)).toBe('—')
  })
  it('别处买盘：中位数、和悠悠榜首比（后端算的百分比）、查于何时；空格子解释原因', () => {
    const now = new Date('2026-09-27T04:00:00Z')
    expect(
      refBid({ ref_bid: 121, ref_bid_platforms: 'BUFF/C5', ref_bid_pct: 10, ref_bid_at: '2026-09-27T01:09:00Z' }, 'Asia/Shanghai', now),
    ).toEqual({ text: '121.00（+10%）', title: 'BUFF/C5 最高求购价的中位数，比悠悠榜首高 10%，查于 09:09' })
    expect(refBid({ ref_bid: 8.95, ref_bid_platforms: 'BUFF/C5', ref_bid_pct: -13.1, ref_bid_at: null }).text).toBe('8.95（-13%）')
    expect(refBid({ ref_bid: 8.95, ref_bid_platforms: null, ref_bid_pct: null, ref_bid_at: null }).title).toBe('参照平台 最高求购价的中位数')
    const empty = refBid({ ref_bid: null, ref_bid_platforms: null, ref_bid_pct: null, ref_bid_at: null })
    expect(empty.text).toBe('—')
    expect(empty.title).toContain('告警范围')
  })
  it('没有排名时分得清三种情况', () => {
    expect(unrankedLabel({ rank: null, beyond: 20, checked: true })).toBe('>20')
    expect(unrankedLabel({ rank: null, beyond: null, checked: true })).toBe('未找到')
    expect(unrankedLabel({ rank: null, beyond: null, checked: false })).toBe('未检查')
    expect(unrankedLabel({ rank: 3, beyond: null, checked: true })).toBeNull()
  })
  it('副标题', () => {
    expect(subLine({ stattrak: true, tag: '崭新出厂' })).toBe('StatTrak™ · 崭新出厂')
    expect(subLine({ stattrak: false, tag: '藕色' })).toBe('藕色')
    expect(subLine({ stattrak: true, tag: null })).toBe('StatTrak™')
    expect(subLine({ stattrak: false, tag: null })).toBe('')
  })
})

describe('rank', () => {
  it('名次数字变小是升', () => {
    expect(rankChange(-1)).toEqual({ text: '↑1', dir: 'up' })
    expect(rankChange(2)).toEqual({ text: '↓2', dir: 'down' })
    expect(rankChange(0)).toBeNull()
    expect(rankChange(null)).toBeNull()
  })
  it('7 天最好名次', () => {
    expect(bestRank([null, 3, 2, null, 4])).toBe(2)
    expect(bestRank([null, null])).toBeNull()
  })
})

describe('headline（spec §4.1）', () => {
  it('列出数量不为 0 的名次', () => {
    const h = headlineText({ counts: { '1': 0, '2': 3 }, top_rank: 2, best: { rank: 2, count: 3 } })
    expect(h.parts).toEqual([{ rank: 2, count: 3 }])
    expect(h.sub).toBe('暂无第 1 名')
  })
  it('有第 1 名时两个都列', () => {
    const h = headlineText({ counts: { '1': 1, '2': 3 }, top_rank: 2, best: { rank: 1, count: 1 } })
    expect(h.parts).toEqual([
      { rank: 1, count: 1 },
      { rank: 2, count: 3 },
    ])
    expect(h.sub).toBe('')  // 不描述推送的事
  })
  it('前 N 名一张都没有', () => {
    const h = headlineText({ counts: { '1': 0, '2': 0 }, top_rank: 2, best: { rank: 5, count: 2 } })
    expect(h.parts).toEqual([])
    expect(h.empty).toBe('暂无求购进入前 2')
    expect(h.sub).toBe('最好名次：第 5 名（2 张）')
    expect(headlineText({ counts: { '1': 0, '2': 0 }, top_rank: 2, best: null }).sub).toBe('暂无排名数据')
  })
})

describe('time（按服务端时区）', () => {
  const tz = 'Asia/Shanghai'
  const now = new Date('2026-09-27T04:00:00Z') // 本地 09-27 12:00
  it('当天只写时分，别的日子带日期', () => {
    expect(clock('2026-09-27T01:09:00Z', tz, now)).toBe('09:09')
    expect(clock('2026-09-26T15:59:00Z', tz, now)).toBe('09-26 23:59')
    expect(clock('2026-09-26T16:01:00Z', tz, now)).toBe('00:01')
    expect(clock(null, tz, now)).toBe('—')
  })
  it('相对时间', () => {
    expect(relative('2026-09-27T03:59:30Z', now)).toBe('刚刚')
    expect(relative('2026-09-27T03:57:00Z', now)).toBe('3 分钟前')
    expect(relative('2026-09-27T01:00:00Z', now)).toBe('3 小时前')
    expect(relative('2026-09-25T04:00:00Z', now)).toBe('2 天前')
  })
})

describe('sparklines', () => {
  it('名次越小越靠上，缺的日子断开', () => {
    const { d, last } = rankSpark([2, null, 1, 3], 30, 20, 0, 3)
    expect(d).toBe('M0.0 10.0M20.0 0.0L30.0 20.0')
    expect(last).toEqual([30, 20])
  })
  it('全空时没有线', () => {
    expect(rankSpark([null, null], 30, 20)).toEqual({ d: '', last: null })
  })
  it('数量越大越靠上', () => {
    expect(countSpark([33, 16], 10, 10, 0)).toBe('M0.0 0.0L10.0 10.0')
  })
})

describe('二期：日期和涨跌幅', () => {
  it('K 线日期按服务端时区取本地日（UTC 16:00 已是北京时间次日）', () => {
    expect(localDate('2026-09-26T16:30:00Z', 'Asia/Shanghai')).toBe('2026-09-27')
    expect(localDate('2026-09-26T15:30:00Z', 'Asia/Shanghai')).toBe('2026-09-26')
  })
  it('涨跌幅带符号和方向，缺失写 —，不写 0', () => {
    expect(signedPct(7.26)).toEqual({ text: '+7.3%', dir: 'up' })
    expect(signedPct(-5.84)).toEqual({ text: '−5.8%', dir: 'down' })
    expect(signedPct(0.4482, 2)).toEqual({ text: '+0.45%', dir: 'up' })
    expect(signedPct(0)).toEqual({ text: '0.0%', dir: 'flat' })
    expect(signedPct(null)).toEqual({ text: '—', dir: 'flat' })
  })
})

describe('大盘页的涨跌写法', () => {
  it('箭头代替正负号', () => {
    expect(arrowPct(-0.0539, 2)).toEqual({ text: '↓0.05%', dir: 'down' })
    expect(arrowPct(0.4482, 2)).toEqual({ text: '↑0.45%', dir: 'up' })
    expect(arrowPct(null)).toEqual({ text: '—', dir: 'flat' })
  })
  it('带符号的差值', () => {
    expect(signedNum(-0.43)).toBe('−0.43')
    expect(signedNum(3.55)).toBe('+3.55')
    expect(signedNum(null)).toBe('—')
  })
})

describe('ratio percentages (valuation)', () => {
  it('signs ratios and keeps null as a dash', async () => {
    const { plainPct, ratioPct } = await import('./index')
    expect(ratioPct(0.153).text).toBe('+15.3%')
    expect(ratioPct(-0.02)).toEqual({ text: '−2.0%', dir: 'down' })
    expect(ratioPct(0).text).toBe('0.0%')
    expect(ratioPct(null).text).toBe('—')
    expect(ratioPct(8.703).text).toBe('+870%')
    expect(ratioPct(1454.448).text).toBe('+145445%')
    expect(ratioPct(-0.999).text).toBe('−99.9%')
    expect(plainPct(0.123)).toBe('12%')
    expect(plainPct(null)).toBe('—')
  })
})
