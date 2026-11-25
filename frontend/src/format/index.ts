// 看板上所有「数字 → 文字」的规则都在这里，纯函数、vitest 覆盖。
// 项目的几条铁律落在这一层：缺失写「—」绝不写 0；「无人在售」和「还没查」是两回事；
// 时间一律按服务端给的时区显示。
import type { Headline, Order } from '@/api/types'

export const DASH = '—'

export function money(v: number | null | undefined): string {
  return v == null ? DASH : v.toFixed(2)
}

/** 悠悠在售：在售数为 0 是查过的结论「无人在售」；在售数为 null 是还没查到，写「—」。 */
export function sellLabel(min: number | null, depth: number | null): string {
  if (depth === 0) return '无人在售'
  return money(min)
}

export function sellSub(depth: number | null): string {
  return depth != null && depth > 0 ? `在售 ${depth} 件` : ''
}

/** 差价倍数：离第 1 太远的写明「难成交」（难以成交 spec §5）。 */
export function gapText(ratio: number | null, gated: boolean): string {
  if (ratio == null) return DASH
  return gated ? `×${ratio.toFixed(1)} 难成交` : `×${ratio.toFixed(2)}`
}

/** 离市价太远（难以成交 spec §5）：「离市价 N 倍 难成交」，倍数缺失时只写「离市价太远 难成交」。 */
export function marketFarText(ratio: number | null | undefined): string {
  return ratio == null ? '离市价太远 难成交' : `离市价 ${ratio >= 10 ? ratio.toFixed(1) : ratio.toFixed(2)} 倍 难成交`
}

/** 别处买盘：别的平台最高求购价的中位数，和悠悠榜首比高多少（百分比由后端算，和告警消息同一个算法）。
 * 空格子要解释为什么空——这一列天生稀疏。 */
export function refBid(
  o: Pick<Order, 'ref_bid' | 'ref_bid_platforms' | 'ref_bid_pct' | 'ref_bid_at'>,
  tz?: string,
  now: Date = new Date(),
): { text: string; title: string } {
  if (o.ref_bid == null) {
    return { text: DASH, title: '仅进入告警范围的单会查询别处买盘，多数单为空' }
  }
  const parts = [`${o.ref_bid_platforms || '参照平台'} 最高求购价的中位数`]
  let text = money(o.ref_bid)
  if (o.ref_bid_pct != null) {
    const pct = Math.round(o.ref_bid_pct)
    text += `（${pct > 0 ? '+' : ''}${pct}%）`
    parts.push(pct === 0 ? '和悠悠榜首持平' : `比悠悠榜首${pct > 0 ? '高' : '低'} ${Math.abs(pct)}%`)
  }
  if (o.ref_bid_at && tz) parts.push(`查于 ${clock(o.ref_bid_at, tz, now)}`)
  return { text, title: parts.join('，') }
}

/** 没有排名时的说法：排在可见页之外（>N）、查过没找到、还没检查过。 */
export function unrankedLabel(o: Pick<Order, 'rank' | 'beyond' | 'checked'>): string | null {
  if (o.rank != null) return null
  if (o.beyond) return `>${o.beyond}`
  return o.checked ? '未找到' : '未检查'
}

/** 副标题：StatTrak™ · 磨损（或颜色）。 */
export function subLine(o: Pick<Order, 'stattrak' | 'tag'>): string {
  return [o.stattrak ? 'StatTrak™' : null, o.tag].filter(Boolean).join(' · ')
}

/** 今天比昨天：名次数字变小 = 升了。 */
export function rankChange(change: number | null): { text: string; dir: 'up' | 'down' } | null {
  if (change == null || change === 0) return null
  return change < 0 ? { text: `↑${-change}`, dir: 'up' } : { text: `↓${change}`, dir: 'down' }
}

export function bestRank(hist: (number | null)[]): number | null {
  const v = hist.filter((x): x is number => x != null)
  return v.length ? Math.min(...v) : null
}

export interface HeadlineText {
  parts: { count: number; rank: number }[]
  empty: string | null
  sub: string
}

/** 总览标题（spec §4.1）：「N 张求购排名第 1、M 张求购排名第 2」，数量为 0 的名次省略。 */
export function headlineText(h: Headline): HeadlineText {
  const parts = Array.from({ length: h.top_rank }, (_, i) => ({ rank: i + 1, count: h.counts[String(i + 1)] ?? 0 })).filter(
    (p) => p.count > 0,
  )
  if (parts.length) {
    const hasFirst = (h.counts['1'] ?? 0) > 0
    return {
      parts,
      empty: null,
      sub: hasFirst ? '' : '暂无第 1 名',
    }
  }
  return {
    parts: [],
    empty: `暂无求购进入前 ${h.top_rank}`,
    sub: h.best ? `最好名次：第 ${h.best.rank} 名（${h.best.count} 张）` : '暂无排名数据',
  }
}

// ---- 时间：按服务端给的时区（Meta.timezone）显示 ----

/** 按时区拆出年、月、日、时、分（都是两位数的字符串）。 */
export function parts(d: Date, tz: string): Record<string, string> {
  const fmt = new Intl.DateTimeFormat('en-CA', {
    timeZone: tz,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  })
  return Object.fromEntries(fmt.formatToParts(d).map((p) => [p.type, p.value]))
}

/** 当天只写 HH:mm；别的日子写 MM-DD HH:mm。 */
export function clock(iso: string | null, tz: string, now: Date = new Date()): string {
  if (!iso) return DASH
  const p = parts(new Date(iso), tz)
  const n = parts(now, tz)
  const hm = `${p.hour}:${p.minute}`
  return p.year === n.year && p.month === n.month && p.day === n.day ? hm : `${p.month}-${p.day} ${hm}`
}

/** 本地日期 YYYY-MM-DD（K 线的横轴、悬停浮层）。 */
export function localDate(iso: string, tz: string): string {
  const p = parts(new Date(iso), tz)
  return `${p.year}-${p.month}-${p.day}`
}

/** 涨跌幅：带正负号（负号用 −），方向给颜色和箭头用；颜色从不单独表达含义（spec §3.1）。 */
export function signedPct(v: number | null | undefined, digits = 1): { text: string; dir: 'up' | 'down' | 'flat' } {
  if (v == null) return { text: DASH, dir: 'flat' }
  const r = Number(v.toFixed(digits))
  if (r === 0) return { text: `${(0).toFixed(digits)}%`, dir: 'flat' }
  return r > 0 ? { text: `+${r.toFixed(digits)}%`, dir: 'up' } : { text: `−${(-r).toFixed(digits)}%`, dir: 'down' }
}

/** 带箭头的涨跌幅（大盘页）：↑0.45% / ↓0.05%，方向给颜色用。 */
export function arrowPct(v: number | null | undefined, digits = 1): { text: string; dir: 'up' | 'down' | 'flat' } {
  const p = signedPct(v, digits)
  if (p.dir === 'flat') return p
  return { text: (p.dir === 'up' ? '↑' : '↓') + p.text.slice(1), dir: p.dir }
}

/** 带正负号的数（负号用 −）：+3.55 / −0.43。 */
export function signedNum(v: number | null | undefined, digits = 2): string {
  if (v == null) return DASH
  const r = Number(v.toFixed(digits))
  if (r === 0) return (0).toFixed(digits)
  return r > 0 ? `+${r.toFixed(digits)}` : `−${(-r).toFixed(digits)}`
}

export function relative(iso: string | null, now: Date = new Date()): string {
  if (!iso) return DASH
  const s = Math.max(0, Math.round((now.getTime() - new Date(iso).getTime()) / 1000))
  if (s < 60) return '刚刚'
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`
  return `${Math.floor(s / 86400)} 天前`
}

// ---- 迷你线：排名走势（名次越小越靠上），缺的日子断开 ----

export function rankSpark(
  values: (number | null)[],
  w: number,
  h: number,
  pad = 3,
  minMax = 5,
): { d: string; last: [number, number] | null } {
  const known = values.filter((v): v is number => v != null)
  const maxR = Math.max(minMax, ...known)
  const n = values.length
  let d = ''
  let pen = false
  let last: [number, number] | null = null
  values.forEach((v, i) => {
    if (v == null) {
      pen = false
      return
    }
    const x = n === 1 ? w / 2 : pad + (i * (w - 2 * pad)) / (n - 1)
    const y = pad + ((v - 1) * (h - 2 * pad)) / (maxR - 1)
    d += `${pen ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`
    pen = true
    last = [x, y]
  })
  return { d, last }
}

/** 数量走势（数值越大越靠上），给提示行的小趋势线用。 */
export function countSpark(values: number[], w: number, h: number, pad = 3): string {
  if (!values.length) return ''
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const span = hi - lo || 1
  return values
    .map((v, i) => {
      const x = values.length === 1 ? w / 2 : pad + (i * (w - 2 * pad)) / (values.length - 1)
      const y = pad + (1 - (v - lo) / span) * (h - 2 * pad)
      return `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`
    })
    .join('')
}

/** 比率（0.153 = 15.3%）→ 带正负号的百分比：+15.3% / −2.0%。价位视图的差价、涨跌都用它。 */
export function ratioPct(v: number | null | undefined, digits = 1): { text: string; dir: 'up' | 'down' | 'flat' } {
  // 100% 以上不带小数：冷门饰品的纸面差价能到几万个百分点，小数只是噪声
  return signedPct(v == null ? v : v * 100, v != null && Math.abs(v) >= 1 ? 0 : digits)
}

/** 分位这类 0～1 的比率 → 不带正负号的整数百分比：0.123 → 12%。 */
export function plainPct(v: number | null | undefined): string {
  return v == null ? DASH : `${Math.round(v * 100)}%`
}
