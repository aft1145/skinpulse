import { describe, expect, it } from 'vitest'
import type { PickOptions, PickRow } from '@/api/types'
import { VERDICTS, defaultFilters, estimateQuery, filterRows, filtersFromQuery, filtersSummary, filtersToQuery, moveNotes, facetsFor, fitFilters, progressRatio, progressText, sortRows, toggle } from './logic'

const O: PickOptions = {
  groups: [{ key: 'rifle', label: '步枪', default: true }, { key: 'knife', label: '刀', default: true }, { key: 'sticker', label: '印花', default: false }],
  subs: {}, wears: [{ key: 'ft', label: '久经沙场' }], versions: [{ key: 'normal', label: '普通' }, { key: 'stattrak', label: 'StatTrak™' }],
  max: 500, night: '23:40～00:50',
}
const row = (t: string, verdict: PickRow['verdict'], extra: Partial<PickRow> = {}) =>
  ({ title: t, verdict, margin: null, cap: null, yp_bid: null, ...extra }) as PickRow

describe('条件', () => {
  it('默认：默认大类、只看普通版本', () => {
    expect(defaultFilters(O)).toEqual({ groups: ['rifle', 'knife'], subs: [], wears: [], versions: ['normal'], price_min: null, price_max: null })
  })
  it('网址往返；版本可以清空', () => {
    const f = { groups: ['rifle'], subs: ['AK-47'], wears: ['ft'], versions: [], price_min: 1, price_max: 300 }
    expect(filtersFromQuery(filtersToQuery(f), O)).toEqual(f)
    expect(filtersFromQuery({ pmin: '-3', pmax: 'x' }, O)).toMatchObject({ price_min: null, price_max: null, groups: ['rifle', 'knife'] })
  })
  it('预估查询串和摘要', () => {
    const f = { ...defaultFilters(O), wears: ['ft'], price_min: 1, price_max: null }
    expect(estimateQuery(f)).toBe('groups=rifle%2Cknife&subs=&wears=ft&versions=normal&price_min=1')
    expect(filtersSummary(f, O)).toBe('步枪 · 刀，久经沙场，普通，¥1～不限')
    expect(toggle(['a', 'b'], 'a')).toEqual(['b'])
    expect(toggle(['a'], 'b')).toEqual(['a', 'b'])
  })
})

describe('进度', () => {
  it('文案和比例', () => {
    expect(progressText({ phase: 'prices', done: 2, total: 4, pool: 357, priced: 0 })).toBe('取价格 2 / 4 批（池子 357 个）')
    expect(progressText({ phase: 'klines', done: 120, total: 300, pool: 357, priced: 300 })).toBe('取日线、估值 120 / 300（价格区间筛完剩 300 个）')
    expect(progressRatio({ phase: 'prices', done: 2, total: 4 })).toBeCloseTo(0.15)
    expect(progressRatio({ phase: 'klines', done: 0, total: 0 })).toBeCloseTo(0.3)
    expect(progressRatio({ phase: 'done', done: 0, total: 0 })).toBe(1)
  })
})

describe('结果', () => {
  it('排序：可以挂按差价、观望按接近程度，其余在后', () => {
    const rows = [row('n', 'none'), row('w1', 'wait', { cap: 50, yp_bid: 100 }), row('g1', 'go', { margin: 0.2 }),
      row('x', 'no'), row('g2', 'go', { margin: 0.5 }), row('w2', 'wait', { cap: 80, yp_bid: 100 }), row('w3', 'wait')]
    expect(sortRows(rows).map((r) => r.title)).toEqual(['g2', 'g1', 'w2', 'w1', 'w3', 'x', 'n'])
    expect(filterRows(rows, 'go').map((r) => r.title)).toEqual(['g1', 'g2'])
    expect(VERDICTS.map((v) => v.label)).toEqual(['可以挂', '观望', '不建议', '不评价'])
  })
})

describe('moveNotes', () => {
  it('rebound from the recent low warns at the threshold; hype is only a note', () => {
    expect(moveNotes({ rebound: 0.712, amp: 17.4 }, 60, 0.3)).toEqual([
      { text: '近 60 天低点 +71%', warn: true },
      { text: '炒过：一年高低差 17.4 倍', warn: false },
    ])
    expect(moveNotes({ rebound: 0.3, amp: 1.2 }, 60, 0.3)).toEqual([{ text: '近 60 天低点 +30%', warn: true }])
    expect(moveNotes({ rebound: 0.05, amp: null }, 60, 0.3)).toEqual([{ text: '近 60 天低点 +5%', warn: false }])
    expect(moveNotes({ rebound: null, amp: null }, 60, 0.3)).toEqual([])
    expect(moveNotes({ rebound: 0.5 }, undefined, undefined)).toEqual([])          // 老后端：不写
  })
})

describe('磨损、版本跟着大类走（intent 2026-11-23）', () => {
  const F: PickOptions = {
    ...O,
    wears: [{ key: 'fn', label: '崭新出厂' }, { key: 'ft', label: '久经沙场' }, { key: 'none', label: '无磨损' }],
    versions: [{ key: 'normal', label: '普通' }, { key: 'stattrak', label: 'StatTrak™' }, { key: 'souvenir', label: '纪念品' }],
    group_facets: {
      rifle: { wears: ['fn', 'ft'], versions: ['normal', 'stattrak', 'souvenir'] },
      knife: { wears: ['fn', 'ft', 'none'], versions: ['normal', 'stattrak'] },
      agent: { wears: ['none'], versions: ['normal'] },
      gloves: { wears: ['fn', 'ft'], versions: ['normal'] },
    },
  }
  const keys = (x: { key: string }[]) => x.map((o) => o.key)

  it('只列选中大类里有的；只剩「无磨损」或「普通」时整行不显示', () => {
    const agent = facetsFor(F, ['agent'])
    expect([agent.showWears, agent.showVersions]).toEqual([false, false])
    const knife = facetsFor(F, ['knife'])
    expect([keys(knife.wears), keys(knife.versions), knife.showVersions]).toEqual([['fn', 'ft', 'none'], ['normal', 'stattrak'], true])
    const gloves = facetsFor(F, ['gloves'])
    expect([gloves.showWears, gloves.showVersions]).toEqual([true, false])
    expect(keys(facetsFor(F, ['agent', 'rifle']).wears)).toEqual(['fn', 'ft', 'none'])      // 并集，按原顺序
  })

  it('没选大类、老后端没有 group_facets：全部照旧', () => {
    expect(keys(facetsFor(F, []).wears)).toEqual(['fn', 'ft', 'none'])
    const old = facetsFor(O, ['agent'])
    expect([keys(old.wears), old.showWears, old.showVersions]).toEqual([['ft'], true, true])
  })

  it('不适用的已选值去掉；版本那一行重新出现时回到默认「普通」', () => {
    const f = { groups: ['agent'], subs: [], wears: ['fn'], versions: ['normal'], price_min: 20, price_max: 100 }
    const agent = fitFilters(f, facetsFor(F, ['agent']), true)
    expect([agent.wears, agent.versions]).toEqual([[], []])
    const back = fitFilters({ ...agent, groups: ['rifle'] }, facetsFor(F, ['rifle']), false)
    expect([back.wears, back.versions]).toEqual([[], ['normal']])
    const knife = fitFilters({ ...f, groups: ['knife'], wears: ['fn', 'none'], versions: ['stattrak', 'souvenir'] }, facetsFor(F, ['knife']), true)
    expect([knife.wears, knife.versions]).toEqual([['fn', 'none'], ['stattrak']])
    expect(fitFilters(f, facetsFor(F, ['agent']), true)).not.toBe(f)                        // 不改原对象
  })
})
