// 分析页的纯逻辑（vitest 覆盖）。
import type { Analysis, DailyClose } from '@/api/types'

/** 标题：「前 3 名 17 张，比 09-21 少 16 张」。和本周最早有数据的那天比；没得比就只说张数。
 * 拆成几段给模板：只有变化的那个数字上色（涨绿跌红，跟排名变化的配色一致）。 */
export function headline(s: Analysis['summary']): { top3: number; lead: string; n: number | null; tail: string; dir: 'up' | 'down' | 'flat' } {
  if (!s.compare) return { top3: s.top3, lead: '', n: null, tail: '', dir: 'flat' }
  const d = s.top3 - s.compare.top3
  const day = s.compare.date.slice(5)
  if (d === 0) return { top3: s.top3, lead: `，和 ${day} 一样`, n: null, tail: '', dir: 'flat' }
  return { top3: s.top3, lead: `，比 ${day} ${d > 0 ? '多' : '少'} `, n: Math.abs(d), tail: ' 张', dir: d > 0 ? 'up' : 'down' }
}

export type Metric = 'rank1' | 'top2' | 'top3'
export interface Slot {
  kind: 'day' | 'break'
  /** day：MM-DD；break：「停 N 天」 */
  label: string
  date?: string
  days?: number
  row?: DailyClose
}

/** 30 天 → 图上的格子：只排有数据的日子，中间连着没数据的几天压成一个「停 N 天」的断口。
 * 开头、结尾没数据的日子直接不画（用户 2026-09-27：服务没跑的日子不要占版面）。 */
export function slots(daily: DailyClose[]): Slot[] {
  const has = (r: DailyClose) => r.top3 != null
  const first = daily.findIndex(has)
  if (first < 0) return []
  let last = daily.length - 1
  while (!has(daily[last])) last--
  const out: Slot[] = []
  let gap = 0
  for (const r of daily.slice(first, last + 1)) {
    if (!has(r)) {
      gap++
      continue
    }
    if (gap) out.push({ kind: 'break', label: `停 ${gap} 天`, days: gap })
    gap = 0
    out.push({ kind: 'day', label: r.date.slice(5), date: r.date, row: r })
  }
  return out
}

export function series(ss: Slot[], m: Metric): (number | null)[] {
  return ss.map((s) => (s.kind === 'day' ? s.row![m] : null))
}

/** 断口两边的虚线：每个断口一条，只有断口前一天、断口、断口后一天三个点，中间取平均。 */
export function bridges(ss: Slot[], m: Metric): (number | null)[][] {
  const out: (number | null)[][] = []
  ss.forEach((s, i) => {
    if (s.kind !== 'break') return
    const a = ss[i - 1]?.row?.[m]
    const b = ss[i + 1]?.row?.[m]
    if (a == null || b == null) return
    const line: (number | null)[] = ss.map(() => null)
    line[i - 1] = a
    line[i] = (a + b) / 2
    line[i + 1] = b
    out.push(line)
  })
  return out
}

/** 分布卡片的一句说明：在册多少张、哪一档最多；没名次的单单独说。 */
export function distCaption(a: Pick<Analysis, 'distribution' | 'unranked'>): string {
  const total = a.distribution.reduce((n, b) => n + b.count, 0)
  const top = a.distribution.reduce((m, b) => (b.count > m.count ? b : m), a.distribution[0])
  const parts = [`${total} 张有名次`]
  if (top && top.count) parts.push(`最多的是 ${top.label} 名（${top.count} 张）`)
  const extra = []
  if (a.unranked.beyond) extra.push(`${a.unranked.beyond} 张在抽样之外`)
  if (a.unranked.not_found) extra.push(`${a.unranked.not_found} 张查询后未找到`)
  if (a.unranked.unchecked) extra.push(`${a.unranked.unchecked} 张尚未检查`)
  return parts.join(' · ') + (extra.length ? `；另有 ${extra.join('、')}` : '')
}
