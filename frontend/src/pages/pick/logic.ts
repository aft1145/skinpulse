// 选品页的纯逻辑（价位 spec §9b.4）：条件 ⇄ 网址、预估查询串、进度文案、结论排序。vitest 覆盖（logic.test.ts）。
import type { PickFilters, PickOption, PickOptions, PickRow, PickRunBrief, PickVerdict } from '@/api/types'

export const VERDICTS: { key: PickVerdict; label: string; tone: 'accent' | 'info' | 'warn' | 'muted' }[] = [
  { key: 'go', label: '可以挂', tone: 'accent' },
  { key: 'wait', label: '观望', tone: 'info' },
  { key: 'no', label: '不建议', tone: 'warn' },
  { key: 'none', label: '不评价', tone: 'muted' },
]

export function defaultFilters(o: Pick<PickOptions, 'groups'>): PickFilters {
  return { groups: o.groups.filter((g) => g.default).map((g) => g.key), subs: [], wears: [], versions: ['normal'], price_min: null, price_max: null }
}

const list = (v: unknown) => (typeof v === 'string' && v ? v.split(',').filter(Boolean) : null)
const num = (v: unknown) => {
  if (typeof v !== 'string' || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) && n >= 0 ? n : null
}

/** 网址 ⇄ 条件。没写的项用默认值；不认识的值留给后端 422（页面会显示原因）。 */
export function filtersFromQuery(q: Record<string, unknown>, o: Pick<PickOptions, 'groups'>): PickFilters {
  const d = defaultFilters(o)
  return {
    groups: list(q.groups) ?? d.groups,
    subs: list(q.subs) ?? [],
    wears: list(q.wears) ?? [],
    versions: q.versions === '' ? [] : list(q.versions) ?? d.versions,
    price_min: num(q.pmin),
    price_max: num(q.pmax),
  }
}

export function filtersToQuery(f: PickFilters): Record<string, string> {
  const out: Record<string, string> = { groups: f.groups.join(','), versions: f.versions.join(',') }
  if (f.subs.length) out.subs = f.subs.join(',')
  if (f.wears.length) out.wears = f.wears.join(',')
  if (f.price_min != null) out.pmin = String(f.price_min)
  if (f.price_max != null) out.pmax = String(f.price_max)
  return out
}

/** /api/pick/estimate 的查询串（和 POST 的条件同一套）。 */
export function estimateQuery(f: PickFilters): string {
  const p = new URLSearchParams({ groups: f.groups.join(','), subs: f.subs.join(','), wears: f.wears.join(','), versions: f.versions.join(',') })
  if (f.price_min != null) p.set('price_min', String(f.price_min))
  if (f.price_max != null) p.set('price_max', String(f.price_max))
  return p.toString()
}

/** 只切换一个值（多选按钮用），不改原数组。 */
export const toggle = (xs: string[], x: string) => (xs.includes(x) ? xs.filter((y) => y !== x) : [...xs, x])

const PHASE: Record<string, string> = { pool: '圈名单', prices: '取价格', klines: '取日线、估值', done: '完成' }

/** 进度条下面那句：「取价格 2 / 4 批」「取日线、估值 120 / 357」。 */
export function progressText(r: Pick<PickRunBrief, 'phase' | 'done' | 'total' | 'pool' | 'priced'>): string {
  if (r.phase === 'prices') return `${PHASE.prices} ${r.done} / ${r.total} 批（池子 ${r.pool} 个）`
  if (r.phase === 'klines') return `${PHASE.klines} ${r.done} / ${r.total}（价格区间筛完剩 ${r.priced} 个）`
  return PHASE[r.phase] ?? r.phase
}

/** 整体进度 0～1：取价算前 30%，取日线算后 70%（日线那段更长）。 */
export function progressRatio(r: Pick<PickRunBrief, 'phase' | 'done' | 'total'>): number {
  const part = r.total > 0 ? Math.min(r.done / r.total, 1) : 0
  if (r.phase === 'prices') return 0.3 * part
  if (r.phase === 'klines') return 0.3 + 0.7 * part
  return r.phase === 'done' ? 1 : 0
}

/** 默认顺序：可以挂（按差价从高到低）→ 观望（上限 / 最高求购 从高到低，越接近越前）→ 不建议 → 不评价。 */
export function sortRows(rows: PickRow[]): PickRow[] {
  const rank: Record<PickVerdict, number> = { go: 0, wait: 1, no: 2, none: 3 }
  const key = (r: PickRow) =>
    r.verdict === 'go' ? (r.margin ?? -Infinity) : r.verdict === 'wait' && r.cap != null && r.yp_bid ? r.cap / r.yp_bid : -Infinity
  return [...rows].sort((a, b) => rank[a.verdict] - rank[b.verdict] || key(b) - key(a) || a.title.localeCompare(b.title, 'zh'))
}

export function filterRows(rows: PickRow[], v: PickVerdict | null): PickRow[] {
  return v ? rows.filter((r) => r.verdict === v) : rows
}

/** 结果页标题下那行：按什么条件圈的。 */
export function filtersSummary(f: PickFilters, o: PickOptions): string {
  const name = (xs: { key: string; label: string }[], keys: string[]) => keys.map((k) => xs.find((x) => x.key === k)?.label ?? k)
  const parts = [name(o.groups, f.groups).join(' · ')]
  if (f.subs.length) parts.push(f.subs.join(' · '))
  if (f.wears.length) parts.push(name(o.wears, f.wears).join(' · '))
  parts.push(f.versions.length ? name(o.versions, f.versions).join(' · ') : '全部版本')
  if (f.price_min != null || f.price_max != null) parts.push(`¥${f.price_min ?? 0}～${f.price_max == null ? '不限' : `¥${f.price_max}`}`)
  return parts.join('，')
}

/** 炒过：一年最高是最低的这么多倍以上（只作描述，回测两段都不过，不进结论） */
export const HYPED_AMP = 3

/** 「一年里的位置」下面的补充说明（intent 2026-11-23）：近期从低点反弹多少（到门槛标 warn）、有没有炒过。 */
export function moveNotes(r: Pick<PickRow, 'rebound' | 'amp'>, window?: number, max?: number): { text: string; warn: boolean }[] {
  const out: { text: string; warn: boolean }[] = []
  if (r.rebound != null && window) {
    out.push({ text: `近 ${window} 天低点 +${Math.round(r.rebound * 100)}%`, warn: max != null && r.rebound >= max })
  }
  if (r.amp != null && r.amp >= HYPED_AMP) out.push({ text: `炒过：一年高低差 ${r.amp.toFixed(1)} 倍`, warn: false })
  return out
}

export interface Facets {
  wears: PickOption[]
  versions: PickOption[]
  /** 只剩「无磨损」时不显示磨损那一行（等于不筛） */
  showWears: boolean
  /** 只剩「普通」时不显示版本那一行 */
  showVersions: boolean
}

/** 选中的大类里实际有的磨损、版本（intent 2026-11-23：探员没有磨损和版本）。没选大类、老后端没给时全部列出。 */
export function facetsFor(o: Pick<PickOptions, 'wears' | 'versions' | 'group_facets'>, groups: string[]): Facets {
  const gf = o.group_facets
  if (!gf || !groups.length || groups.some((g) => !gf[g])) {
    return { wears: o.wears, versions: o.versions, showWears: true, showVersions: true }
  }
  const w = new Set(groups.flatMap((g) => gf[g].wears))
  const v = new Set(groups.flatMap((g) => gf[g].versions))
  const wears = o.wears.filter((x) => w.has(x.key))
  const versions = o.versions.filter((x) => v.has(x.key))
  return {
    wears, versions,
    showWears: !(wears.length <= 1 && (wears[0]?.key ?? 'none') === 'none'),
    showVersions: !(versions.length <= 1 && (versions[0]?.key ?? 'normal') === 'normal'),
  }
}

/** 换了大类后把条件对上：不适用的磨损、版本去掉，行不显示就清空（不筛）；版本那一行刚重新出现、又是空的，
 * 回到默认「普通」。`wasShowingVersions` 是换大类之前版本那一行显不显示。返回新对象。 */
export function fitFilters(f: PickFilters, fc: Facets, wasShowingVersions: boolean): PickFilters {
  const w = new Set(fc.wears.map((x) => x.key))
  const v = new Set(fc.versions.map((x) => x.key))
  const wears = fc.showWears ? f.wears.filter((k) => w.has(k)) : []
  let versions = fc.showVersions ? f.versions.filter((k) => v.has(k)) : []
  if (fc.showVersions && !wasShowingVersions && !versions.length && v.has('normal')) versions = ['normal']
  return { ...f, wears, versions }
}
