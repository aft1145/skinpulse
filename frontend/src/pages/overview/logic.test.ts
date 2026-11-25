import { describe, expect, it } from 'vitest'
import type { Insights, Meta, Order } from '@/api/types'
import { GAP_FILTERS, GAP_TIER_LABEL, SEGMENTS, accountTag, condAheadText, isBuff, ownAheadText, gapCounts, nullsLast, rankCompare, marketTags, categoryMenu, categoryText, filterOrders, gapPct, gapSortKey, gapTier, ladderMarks, filtersFromQuery, filtersToQuery, marketCellText, marketSortKey, unreachableInsight, unreachableOf, movesInsight, rankFlashes, rankSortKey, setupView, top3Insight } from './logic'

const o = (no: string, rank: number | null, extra: Partial<Order> = {}): Order => ({
  purchase_no: no, template_id: 1, title: `AK | ${no}`, star: false, stattrak: false, tag: null, queue: '不限',
  rank, beyond: null, checked: true, rank_7d: [], rank_change: null, my_price: 1, top1_price: 2, gap: 1, gap_ratio: 2, gated: false,
  sell_min: null, sell_depth: null, ref_bid: null, ref_bid_platforms: null, ref_bid_pct: null, ref_bid_at: null, remaining_qty: 1, checked_at: null,
  sell_checked_at: null, ...extra,
})

describe('filterOrders', () => {
  const orders = [o('a', 1, { unreachable: 'market' }), o('b', 2), o('c', 3, { gated: true }), o('d', 7), o('e', null, { tag: '崭新出厂' })]
  const f = (x: Partial<Parameters<typeof filterOrders>[1]>) => filterOrders(orders, { seg: 'all', hideUnreachable: false, q: '', cat: null, sub: null, gap: null, ...x })
  it('排名分段', () => {
    expect(f({ seg: 'top2' }).map((x) => x.purchase_no)).toEqual(['a', 'b'])
    expect(f({ seg: 'top3' }).map((x) => x.purchase_no)).toEqual(['a', 'b', 'c'])
    expect(f({ seg: 'rest' }).map((x) => x.purchase_no)).toEqual(['d', 'e'])
  })
  it('隐藏难以成交的（离第 1、离市价；旧后端只有 gated）、搜索（含磨损）', () => {
    expect(f({ hideUnreachable: true }).map((x) => x.purchase_no)).toEqual(['b', 'd', 'e'])
    expect(f({ q: ' ak | D ' }).map((x) => x.purchase_no)).toEqual(['d'])
    expect(f({ q: '崭新' }).map((x) => x.purchase_no)).toEqual(['e'])
  })
})

describe('rankFlashes', () => {
  it('首次加载不闪；升降分开', () => {
    expect(rankFlashes(null, [o('a', 1)]).size).toBe(0)
    const m = rankFlashes([o('a', 3), o('b', 2), o('c', 5), o('d', null)], [o('a', 2), o('b', 4), o('c', 5), o('d', 3), o('e', 1)])
    expect([...m]).toEqual([['a', 'up'], ['b', 'down']])
  })
})

describe('排名分段', () => {
  it('「前 2 名」排最前、「全部」排最后', () => {
    expect(SEGMENTS.map((s) => s.key)).toEqual(['top2', 'top3', 'rest', 'all'])
  })
})

describe('URL 查询参数', () => {
  it('默认只看前 2 名；往返一致，非法值回到默认', () => {
    const f = { seg: 'top3' as const, hideUnreachable: true, q: 'AWP', cat: null, sub: null, gap: null }
    expect(filtersFromQuery(filtersToQuery(f))).toEqual(f)
    expect(filtersFromQuery({})).toEqual({ seg: 'top2', hideUnreachable: false, q: '', cat: null, sub: null, gap: null })
    expect(filtersToQuery({ seg: 'top2', hideUnreachable: false, q: '  ', cat: null, sub: null, gap: null })).toEqual({})
    expect(filtersToQuery({ seg: 'all', hideUnreachable: false, q: '', cat: null, sub: null, gap: null })).toEqual({ rank: 'all' })
    expect(filtersFromQuery(filtersToQuery({ seg: 'all', hideUnreachable: false, q: '', cat: null, sub: null, gap: null })).seg).toBe('all')
    expect(filtersFromQuery({ rank: 'bogus', hide: 'yes' })).toEqual({ seg: 'top2', hideUnreachable: false, q: '', cat: null, sub: null, gap: null })
  })
})

describe('提示行', () => {
  const ins = (x: Partial<Insights>): Insights => ({ total: 429, unreachable_count: 153, unreachable_top: 98, unreachable_market: 55, moved_up: 33, moved_down: 34, top3_series: [33, 25, 16], days: [], ...x })
  it('前 3 名走势', () => {
    expect(top3Insight(ins({}))).toEqual({ title: '前 3 名在变少', text: '7 天里从 33 张到 16 张', tone: 'warn' })
    expect(top3Insight(ins({ top3_series: [10, 12] })).title).toBe('前 3 名在变多')
    expect(top3Insight(ins({ top3_series: [5, 5] })).tone).toBe('neutral')
  })
  it('升降和过滤', () => {
    expect(movesInsight(ins({})).text).toBe('比昨天升 33 张、降 34 张')
    expect(movesInsight(ins({ moved_up: 0, moved_down: 0 })).title).toBe('今日名次无变化')
    expect(unreachableInsight(ins({}))).toEqual({ title: '153 张难以成交', text: '离第 1 太远 98 张、离市价太远 55 张', tone: 'neutral' })
  })
})

describe('交易中的行（成交推送 spec §7.2）', () => {
  const t = o('t', null, { in_trade_since: '2026-10-01T09:50:00Z' })
  const orders = [o('a', 1), o('b', 2), o('d', 7), t]
  it('默认「前 2 名」「前 3 名」里也看得到，「第 4 名以后」里没有', () => {
    const f = (seg: 'top2' | 'top3' | 'rest') => filterOrders(orders, { seg, hideUnreachable: false, q: '', cat: null, sub: null, gap: null }).map((x) => x.purchase_no)
    expect(f('top2')).toEqual(['a', 'b', 't'])
    expect(f('top3')).toEqual(['a', 'b', 't'])
    expect(f('rest')).toEqual(['d'])
  })
  it('按排名排序时排在第 1 名之前', () => {
    expect(rankSortKey(t)).toBeLessThan(rankSortKey(o('a', 1))!)
    expect(rankSortKey(o('x', null))).toBeNull()
    expect(rankSortKey(o('b', 2))).toBe(2)
  })
})

describe('品类', () => {
  const groups = [{ key: 'rifle', label: '步枪' }, { key: 'pistol', label: '手枪' }, { key: 'sticker', label: '印花' }, { key: 'other', label: '其他' }]
  const orders = [
    o('a', 1, { category: 'rifle', subcategory: 'AK-47', subcategory_label: 'AK-47' }),
    o('b', 2, { category: 'rifle', subcategory: 'M4A1-S', subcategory_label: 'M4A1消音版' }),
    o('c', 5, { category: 'rifle', subcategory: 'AK-47', subcategory_label: 'AK-47' }),
    o('d', 3, { category: 'sticker', subcategory: null, subcategory_label: null }),
    o('e', 4), // 后端还是旧版本：没有品类字段，算「其他」
  ]
  const f = (x: Partial<Parameters<typeof filterOrders>[1]>) =>
    filterOrders(orders, { seg: 'all', hideUnreachable: false, q: '', cat: null, sub: null, gap: null, ...x }).map((y) => y.purchase_no)

  it('按大类、第二层筛，和排名分段叠加', () => {
    expect(f({ cat: 'rifle' })).toEqual(['a', 'b', 'c'])
    expect(f({ cat: 'rifle', sub: 'AK-47' })).toEqual(['a', 'c'])
    expect(f({ cat: 'rifle', sub: 'AK-47', seg: 'top2' })).toEqual(['a'])
    expect(f({ cat: 'other' })).toEqual(['e'])
    // 没选大类时第二层不起作用
    expect(f({ sub: 'AK-47' })).toHaveLength(5)
  })

  it('URL 往返；没有大类时丢掉第二层', () => {
    const x = { seg: 'top2' as const, hideUnreachable: false, q: '', cat: 'rifle', sub: 'AK-47', gap: null }
    expect(filtersToQuery(x)).toEqual({ cat: 'rifle', sub: 'AK-47' })
    expect(filtersFromQuery(filtersToQuery(x))).toEqual(x)
    expect(filtersFromQuery({ sub: 'AK-47' })).toMatchObject({ cat: null, sub: null, gap: null })
    expect(filtersToQuery({ ...x, cat: null })).toEqual({})
  })

  it('面板：两层张数、0 张的不出现、第二层按张数排', () => {
    const m = categoryMenu(orders, groups, { cat: null, sub: null })
    expect(m.map((g) => [g.key, g.n])).toEqual([['rifle', 3], ['sticker', 1], ['other', 1]])
    expect(m[0].subs).toEqual([{ key: 'AK-47', label: 'AK-47', n: 2 }, { key: 'M4A1-S', label: 'M4A1消音版', n: 1 }])
    expect(m[1].subs).toEqual([])
  })

  it('面板：当前选中的即使 0 张也留着', () => {
    const m = categoryMenu(orders.slice(3), groups, { cat: 'rifle', sub: 'AK-47' })
    expect(m[0]).toEqual({ key: 'rifle', label: '步枪', n: 0, subs: [{ key: 'AK-47', label: 'AK-47', n: 0 }] })
  })

  it('按钮文字', () => {
    const m = categoryMenu(orders, groups, { cat: null, sub: null })
    expect(categoryText(m, groups, null, null)).toBe('全部')
    expect(categoryText(m, groups, 'rifle', null)).toBe('步枪')
    expect(categoryText(m, groups, 'rifle', 'M4A1-S')).toBe('步枪 › M4A1消音版')
    expect(categoryText([], groups, 'pistol', 'USP-S')).toBe('手枪 › USP-S')
  })
})

describe('价格梯', () => {
  it('差价比例和分档', () => {
    expect(gapPct({ my_price: 97, top1_price: 98 })).toBeCloseTo(1 / 98)
    expect(gapPct({ my_price: 9, top1_price: 10.2 })).toBeCloseTo(1.2 / 10.2)
    expect(gapPct({ my_price: 10, top1_price: 9 })).toBe(0)               // 快照滞后：不出负数
    expect(gapPct({ my_price: 10, top1_price: null })).toBeNull()
    expect(gapPct({ my_price: 0, top1_price: 5 })).toBeNull()
    const t = (my: number, top: number | null, extra: Partial<Order> = {}) => gapTier(o('x', 2, { my_price: my, top1_price: top, ...extra }))
    expect(t(97, 98)).toBe('near')
    expect(t(95, 100)).toBe('near')            // 正好 5%
    expect(t(88, 100)).toBe('mid')
    expect(t(85, 100)).toBe('mid')             // 正好 15%
    expect(t(84, 100)).toBe('far')
    expect(t(18, 300, { gated: true })).toBe('gated')
    expect(t(10, null)).toBe('none')
    expect(gapTier(o('x', 1, { my_price: 97, top1_price: 97 }))).toBe('first')
    expect(gapTier(o('x', null, { in_trade_since: '2026-10-09T01:00:00Z' }))).toBe('trade')
    expect(GAP_TIER_LABEL.far).toBe('较远')
    // 优先级：交易中 > 已是第 1 > 没有榜首价 > 难成交（离第 1） > 按比例
    expect(gapTier(o('x', 1, { my_price: 1, top1_price: 5, gated: true }))).toBe('first')
    expect(gapTier(o('x', null, { top1_price: null, gated: true }))).toBe('none')
    expect(gapTier(o('x', 1, { in_trade_since: '2026-10-09T01:00:00Z' }))).toBe('trade')
  })

  it('排序键：第 1 和交易中最前，算不出的为 null', () => {
    expect(gapSortKey(o('a', 1))).toBe(-1)
    expect(gapSortKey(o('b', 2, { my_price: 1, top1_price: 2 }))).toBe(0.5)
    expect(gapSortKey(o('c', 2, { top1_price: null }))).toBeNull()
  })

  it('轴上的位置：从 0 到三者最高，缺的为 null', () => {
    expect(ladderMarks({ my_price: 9, top1_price: 10, sell_min: 20 })).toEqual({ my: 45, top: 50, sell: 100 })
    expect(ladderMarks({ my_price: 10, top1_price: 13.5, sell_min: 13.5 })).toEqual({ my: 10 / 13.5 * 100, top: 100, sell: 100 })
    expect(ladderMarks({ my_price: 5, top1_price: 10, sell_min: null })).toEqual({ my: 50, top: 100, sell: null })
    expect(ladderMarks({ my_price: 5, top1_price: null, sell_min: 0 })).toEqual({ my: 100, top: null, sell: null })
    expect(ladderMarks({ my_price: null, top1_price: null, sell_min: null })).toBeNull()
    expect(ladderMarks({ my_price: 5, top1_price: 10, sell_min: 20, sell_depth: 0 })).toEqual({ my: 50, top: 100, sell: null })
  })
})

describe('市价提醒标签', () => {
  it('倒挂 warn、贴近市价 amber，两种都有就都显示', () => {
    expect(marketTags(null)).toEqual([])
    expect(marketTags(undefined)).toEqual([])
    expect(marketTags('inverted')).toEqual([{ label: '榜首高于在售', tone: 'warn' }])
    expect(marketTags('near')).toEqual([{ label: '贴近市价', tone: 'amber' }])
    expect(marketTags('both').map((t) => t.label)).toEqual(['榜首高于在售', '贴近市价'])
  })
})

describe('离第 1：同名次排序、按档位筛选（intent 2026-11-20）', () => {
  const p = (no: string, rank: number | null, my: number | null, top: number | null, extra: Partial<Order> = {}) =>
    o(no, rank, { my_price: my, top1_price: top, ...extra })
  const orders = [
    p('far4', 4, 2, 100),           // 98%
    p('near4', 4, 94, 100),         // 6%
    p('none4', 4, 10, null),        // 算不出
    p('mid4', 4, 90, 100),          // 10%
    p('r3', 3, 1, 100),
    p('r5', 5, 99, 100),
    p('nr', null, 1, 2),
    p('t', null, 1, 2, { in_trade_since: '2026-10-01T09:50:00Z' }),
    p('first', 1, 5, 5),
  ]
  // Naive 降序时把比较结果乘 −1，这里模拟它
  const sorted = (desc: boolean) =>
    [...orders].sort((a, b) => (desc ? -1 : 1) * rankCompare(a, b, desc)).map((x) => x.purchase_no)

  it('升序：名次从小到大，同名次差距小的在前，算不出的在同名次最后，没有排名的最后', () => {
    expect(sorted(false)).toEqual(['t', 'first', 'r3', 'near4', 'mid4', 'far4', 'none4', 'r5', 'nr'])
  })
  it('降序：只反转名次，同名次里仍然差距小的在前；没有排名的仍在最后', () => {
    expect(sorted(true)).toEqual(['r5', 'near4', 'mid4', 'far4', 'none4', 'r3', 'first', 't', 'nr'])
  })

  const f = (gap: string | null) =>
    filterOrders(orders, { seg: 'all', hideUnreachable: false, q: '', cat: null, sub: null, gap: gap as never }).map((x) => x.purchase_no)
  it('按档位筛：第 1、交易中、算不出的不参与', () => {
    expect(f(null)).toHaveLength(orders.length)
    expect(f('far')).toEqual(['far4', 'r3', 'nr'])
    expect(f('mid')).toEqual(['near4', 'mid4'])
    expect(f('near')).toEqual(['r5'])
    expect(filterOrders([p('g', 6, 1, 10, { gated: true }), p('x', 6, 9, 10)], { seg: 'all', hideUnreachable: false, q: '', cat: null, sub: null, gap: 'gated' }).map((x) => x.purchase_no)).toEqual(['g'])
  })
  it('档位按钮和表格里的标签一致，张数按档位数', () => {
    expect(GAP_FILTERS.map((g) => [g.key, g.label])).toEqual([['near', '很近'], ['mid', '一般'], ['far', '较远'], ['gated', '离第 1 太远']])
    expect(gapCounts(orders)).toEqual({ near: 1, mid: 2, far: 3, gated: 0 })
  })
  it('网址：gap=档位，往返一致；不认识的当作没选', () => {
    const x = { seg: 'top2' as const, hideUnreachable: false, q: '', cat: null, sub: null, gap: 'near' as const }
    expect(filtersToQuery(x)).toEqual({ gap: 'near' })
    expect(filtersFromQuery(filtersToQuery(x))).toEqual(x)
    expect(filtersFromQuery({ gap: 'first' }).gap).toBeNull()
    expect(filtersFromQuery({ gap: 'bogus' }).gap).toBeNull()
  })
})

describe('可排序列：空值始终在最后', () => {
  const by = nullsLast<{ v: number | null }>((x) => x.v)
  const xs = [{ v: 2 }, { v: null }, { v: 1 }, { v: 3 }]
  const sorted = (desc: boolean) => [...xs].sort((a, b) => (desc ? -1 : 1) * by(a, b, desc)).map((x) => x.v)
  it('升序、降序', () => {
    expect(sorted(false)).toEqual([1, 2, 3, null])
    expect(sorted(true)).toEqual([3, 2, 1, null])
  })
})

describe('setupView', () => {
  const meta = (setup: Meta['setup'], extra: Partial<Meta> = {}) =>
    ({ token: { valid: false, expires_at: null, days_left: 6 }, setup, ...extra }) as unknown as Meta
  const none = { youpin: false, push: false, first_round: false, market: false }

  it('starts with login as the current step and the first round waiting on it', () => {
    const v = setupView(meta(none))!
    expect(v.left).toBe(3)
    expect(v.steps.map((s) => [s.key, s.state])).toEqual([
      ['youpin', 'current'], ['push', 'todo'], ['first_round', 'waiting'], ['market', 'optional'],
    ])
  })
  it('moves on to push once logged in and the first round has run', () => {
    const v = setupView(meta({ ...none, youpin: true, first_round: true }), 12)!
    expect(v.left).toBe(1)
    expect(v.steps.map((s) => s.state)).toEqual(['done', 'current', 'done', 'optional'])
    expect(v.steps[0].doneText).toBe('已登录 · token 还有 6 天到期')
    expect(v.steps[2].doneText).toBe('已完成 · 同步了 12 张求购单')
  })
  it('first round becomes current after login when push is done', () => {
    const v = setupView(meta({ ...none, youpin: true, push: true }))!
    expect(v.steps.map((s) => s.state)).toEqual(['done', 'done', 'current', 'optional'])
  })
  it('disappears once the three required steps are done, market optional', () => {
    expect(setupView(meta({ youpin: true, push: true, first_round: true, market: false }))).toBeNull()
    expect(setupView(meta({ youpin: true, push: true, first_round: true, market: false }), 3)).toBeNull()
  })
  it('says there are no buy orders yet once set up with zero orders', () => {
    expect(setupView(meta({ youpin: true, push: true, first_round: true, market: true }), 0)).toEqual({ steps: [], left: 0, noOrders: true })
  })
  it('never shows for demo or an older backend', () => {
    expect(setupView(meta(none, { demo: true }))).toBeNull()
    expect(setupView(meta(undefined))).toBeNull()
    expect(setupView(null)).toBeNull()
  })
})

describe('multi-account', () => {
  const accounts = { '1': '主号', '2': '小号', '3': '三号' }
  it('tags every account but the first, only in the all view with two or more accounts', () => {
    expect(accountTag({ account_id: 2 }, accounts, 'all')).toBe('小号')
    expect(accountTag({ account_id: 1 }, accounts, 'all')).toBeNull()
    expect(accountTag({ account_id: 2 }, accounts, 2)).toBeNull()
    expect(accountTag({ account_id: 2 }, { '2': '小号' }, 'all')).toBeNull()
    expect(accountTag({ account_id: 2 }, {}, 'all')).toBeNull()
    expect(accountTag({}, accounts, 'all')).toBeNull()
  })
  it('says which of my accounts is ahead', () => {
    expect(ownAheadText({ own_ahead: { label: '小号', price: 19.6 } })).toBe('前面是我的小号（¥19.60）')
    expect(ownAheadText({ own_ahead: { label: '小号', price: null } })).toBe('前面是我的小号')
    expect(ownAheadText({ own_ahead: null })).toBeNull()
    expect(ownAheadText({})).toBeNull()
  })
})

describe('BUFF rows (BUFF spec §8)', () => {
  it('says how many conditional orders sit ahead, only for BUFF rows', () => {
    expect(condAheadText({ platform: 'buff', cond_ahead: 7 })).toBe('前面还有 7 张条件单')
    expect(condAheadText({ platform: 'buff', cond_ahead: 0 })).toBeNull()
    expect(condAheadText({ platform: 'youpin', cond_ahead: 3 })).toBeNull()
    expect(isBuff({ platform: 'buff' })).toBe(true)
    expect(isBuff({})).toBe(false)
  })
  it('tags BUFF rows like any other non-first account', () => {
    const accounts = { '1': '主号', '2': 'BUFF' }
    expect(accountTag({ account_id: 2 }, accounts, 'all')).toBe('BUFF')
    expect(accountTag({ account_id: 1 }, accounts, 'all')).toBeNull()
  })
})

describe('难以成交（难以成交 spec §5）', () => {
  it('原因：新字段优先，旧后端退回 gated', () => {
    expect(unreachableOf(o('a', 1, { unreachable: 'market' }))).toBe('market')
    expect(unreachableOf(o('a', 1, { unreachable: null, gated: true }))).toBeNull()
    expect(unreachableOf(o('a', 1, { gated: true }))).toBe('top')
    expect(unreachableOf(o('a', 1))).toBeNull()
  })
  it('离市价单元格：差价、倍数、别处买盘、未知', () => {
    const far = o('a', 1, { market: 4871, market_src: 'sell', market_gap: 4861, market_ratio: 487.1, unreachable: 'market' })
    expect(marketCellText(far)).toEqual({ main: '差 4861.00', sub: '487.1 倍', hard: true })
    const near = o('b', 1, { market: 49, market_src: 'sell', market_gap: 24, market_ratio: 1.96, unreachable: null })
    expect(marketCellText(near)).toEqual({ main: '差 24.00', sub: '1.96 倍', hard: false })
    const bid = o('c', 1, { market: 1500, market_src: 'bid', market_gap: 1490, market_ratio: 150, unreachable: 'market', sell_depth: 0 })
    expect(marketCellText(bid).sub).toBe('别处买盘 · 150.0 倍')
    expect(marketCellText(o('d', 2, { market: null, sell_depth: 0 }))).toEqual({ main: '—', sub: '无在售、无别处买盘', hard: false })
    expect(marketCellText(o('e', 2))).toEqual({ main: '—', sub: '', hard: false })
  })
  it('离市价排序键：倍数，未知为 null', () => {
    expect(marketSortKey(o('a', 1, { market_ratio: 2.5 }))).toBe(2.5)
    expect(marketSortKey(o('a', 1))).toBeNull()
  })
})
