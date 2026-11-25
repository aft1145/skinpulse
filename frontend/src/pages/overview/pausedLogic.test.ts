import { describe, expect, it } from 'vitest'
import type { PausedItem, PausedPayload, PausedRun } from '@/api/types'
import {
  afterText, cardState, lastFailure, needsRecheck, neverTitle, pausedAccountTag, progress, ratioBar, riskHeadline, sellText, subline,
  tierBlocks, visibleItems,
} from './pausedLogic'

const NOW = new Date('2026-11-24T06:00:00Z')
const TZ = 'Asia/Shanghai'

const run = (over: Partial<PausedRun> = {}): PausedRun => ({
  id: 1, status: 'done', source: 'dashboard', started_at: '2026-11-24T05:52:00Z', finished_at: '2026-11-24T06:00:00Z',
  phase: null, done: 0, total: 0, incomplete: false, steamdt: true, skipped: [], error: null, ...over,
})

const tiers = (inv = 0, top1 = 0, near = 0, ns = 0, other = 0) => ({
  inverted: { count: inv, amount: inv * 100 }, top1: { count: top1, amount: top1 * 10 }, near: { count: near, amount: 5 },
  near_sell: { count: ns, amount: 2 }, other: { count: other, amount: 1 },
})

const payload = (over: Partial<PausedPayload> = {}): PausedPayload => ({
  run: run(), result: run(), summary: { total: 518, amount: 77615.9, old: 102, tiers: tiers(36, 52, 32, 3, 395) },
  items: [], paused_since: 0, known_paused: 124, old_days: 60, stale_h: 2, accounts: {}, ...over,
})

const item = (over: Partial<PausedItem> = {}): PausedItem => ({
  purchase_no: 'A', account_id: 1, template_id: 1, title: '专业手套 | 狩鹿', star: true, stattrak: false, tag: '久经沙场',
  price: 650, remaining_qty: 1, paused_at: null, paused_days: 121, old: true, top_bid: 190, top_empty: false,
  top1_kind: 'ahead', gap_ratio: null, over: 460, sell_min: 205, sell_depth: 1, sell_src: 'youpin',
  sell_at: '2026-11-24T05:59:00Z', sell_ratio: 3.17, tier: 'inverted', ...over,
})

describe('cardState', () => {
  it('五种状态', () => {
    expect(cardState(null)).toBe('hidden')
    expect(cardState(payload())).toBe('risky')
    expect(cardState(payload({ run: run({ id: 2, status: 'running', phase: 'prices' }) }))).toBe('running')
    expect(cardState(payload({ run: null, result: null, summary: null }))).toBe('never')
    expect(cardState(payload({ run: null, result: null, summary: null, known_paused: 0 }))).toBe('hidden')
    const safe = { total: 41, amount: 10, old: 0, tiers: tiers(0, 0, 0, 0, 41) }
    expect(cardState(payload({ summary: safe }))).toBe('safe')
    expect(cardState(payload({ summary: safe, paused_since: 3 }))).toBe('never')
    expect(cardState(payload({ summary: { ...safe, total: 0, tiers: tiers() } }))).toBe('hidden')
    // 上次检查失败、之前也没查成过：照样是「查一下」
    expect(cardState(payload({ run: run({ status: 'failed' }), result: null, summary: null }))).toBe('never')
  })
})

describe('标题', () => {
  it('取最严重的一档', () => {
    expect(riskHeadline(payload())).toEqual({ n: 36, text: '恢复后会倒挂', tier: 'inverted' })
    const s = payload({ summary: { total: 9, amount: 1, old: 0, tiers: tiers(0, 0, 4, 1, 4) } })
    expect(riskHeadline(s)).toEqual({ n: 4, text: '恢复后离第 1 差不到 5%', tier: 'near' })
  })
  it('查一下', () => {
    expect(neverTitle(payload({ result: null, summary: null }))).toEqual({ n: null, text: '有求购单被暂停，充值前请检查' })
    expect(neverTitle(payload({ paused_since: 3 }))).toEqual({ n: 3, text: '检查后被暂停，充值前请重新检查' })
    expect(neverTitle(payload({ result: null, summary: null, known_paused: 0 })).n).toBeNull()
  })
})

describe('subline', () => {
  it('新鲜的结果只有检查信息', () => {
    expect(subline(payload(), TZ, NOW)).toEqual({ warn: null, info: '检查于 14:00 · 暂停中 518 张 · 102 张暂停超过 60 天' })
    expect(needsRecheck(payload(), NOW)).toBe(false)
  })
  it('过期、检查后新暂停、不完整、没查成的号', () => {
    const p = payload({
      result: run({ finished_at: '2026-11-24T01:00:00Z', incomplete: true, skipped: [2] }), paused_since: 3,
      accounts: { 1: '主号', 2: '小号' },
    })
    const s = subline(p, TZ, NOW)
    expect(s.warn).toBe('结果是 5 小时前的，检查之后又有 3 张被暂停')
    expect(s.info).toBe('检查于 09:00 · 暂停中 518 张 · 102 张暂停超过 60 天 · 仅查询了前几十页，结果不完整 · 小号 查询失败')
    expect(needsRecheck(p, NOW)).toBe(true)
  })
})

describe('lastFailure', () => {
  it('最近一次没完成、卡片显示的是更早的结果', () => {
    const p = payload({ run: run({ id: 2, status: 'failed', error: '悠悠风控，所有号的请求都暂停了，过一阵再查' }) })
    expect(lastFailure(p)).toBe('上次检查未完成：悠悠风控，所有号的请求都暂停了，过一阵再查')
    expect(subline(p, TZ, NOW).warn).toBe('上次检查未完成：悠悠风控，所有号的请求都暂停了，过一阵再查')
    expect(needsRecheck(p, NOW)).toBe(true)
    expect(lastFailure(payload({ run: run({ id: 2, status: 'interrupted', error: null }), result: null, summary: null })))
      .toBe('上次检查未完成：服务已重启')
    expect(lastFailure(payload())).toBeNull()
  })
})

describe('档位块和明细', () => {
  it('四档', () => {
    expect(tierBlocks(payload()).map((b) => [b.key, b.count])).toEqual([['inverted', 36], ['top1', 52], ['near', 32], ['near_sell', 3]])
  })
  it('选中一档、先给 8 行', () => {
    const items = [item(), item({ purchase_no: 'B', tier: 'top1' }), ...Array.from({ length: 9 }, (_, i) => item({ purchase_no: `I${i}` }))]
    expect(visibleItems(items, null, 8)).toEqual([])
    expect(visibleItems(items, 'inverted', 8)).toHaveLength(8)
    expect(visibleItems(items, 'inverted', null)).toHaveLength(10)
    expect(visibleItems(items, 'top1', 8).map((i) => i.purchase_no)).toEqual(['B'])
  })
})

describe('一行', () => {
  it('恢复后', () => {
    expect(afterText(item())).toEqual({ first: true, text: '高出 ¥460' })
    expect(afterText(item({ top1_kind: 'alone', over: null, top_bid: null }))).toEqual({ first: true, text: '无人求购' })
    expect(afterText(item({ top1_kind: 'tie', over: 0 }))).toEqual({ first: true, text: '并列' })
    expect(afterText(item({ top1_kind: null, over: null, gap_ratio: 0.0325 }))).toEqual({ first: false, text: '差 3.3%' })
    expect(afterText(item({ top1_kind: null, over: null, gap_ratio: null }))).toEqual({ first: false, text: '—' })
  })
  it('在售：实时、SteamDT、无人在售、没查', () => {
    expect(sellText(item(), NOW)).toEqual({ main: '205', note: '实时 · 1 件' })
    expect(sellText(item({ sell_src: 'steamdt', sell_min: 402.64, sell_depth: 9, sell_at: '2026-11-24T03:00:00Z' }), NOW))
      .toEqual({ main: '402.64', note: 'SteamDT · 3 小时前' })
    expect(sellText(item({ sell_min: null, sell_depth: 0 }), NOW)).toEqual({ main: '无人在售', note: '实时' })
    expect(sellText(item({ sell_min: null, sell_depth: null, sell_src: null }), NOW)).toEqual({ main: '—', note: '' })
  })
  it('比值横条', () => {
    expect(ratioBar(3.17)).toEqual({ width: 100, pct: '317%', over: true })
    expect(ratioBar(0.9)).toEqual({ width: 45, pct: '90%', over: false })
    expect(ratioBar(null)).toEqual({ width: 0, pct: '—', over: false })
  })
  it('账号简称：一个号或第一个号不带', () => {
    expect(pausedAccountTag(item({ account_id: 2 }), {})).toBeNull()
    expect(pausedAccountTag(item({ account_id: 1 }), { 1: '主号', 2: '小号' })).toBeNull()
    expect(pausedAccountTag(item({ account_id: 2 }), { 1: '主号', 2: '小号' })).toBe('小号')
  })
})

describe('progress', () => {
  it('三步', () => {
    const p = progress(run({ status: 'running', phase: 'prices', done: 2, total: 6 }))
    expect(p.label).toBe('查在售价')
    expect(p.count).toBe('2 / 6 批')
    expect(p.pct).toBe(37)
    expect(p.minutes).toBe(6)
    expect(p.steps.map((s) => s.state)).toEqual(['done', 'current', 'todo'])
    expect(progress(run({ status: 'running', phase: 'pages', done: 5 })).count).toBe('第 5 页')
    expect(progress(run({ status: 'running', phase: 'verify', done: 30, total: 30 })).pct).toBe(100)
  })
})
