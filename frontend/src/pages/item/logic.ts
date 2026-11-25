// 饰品详情页的「数字 → 文字 / 位置」规则（纯函数，vitest 覆盖）。
import type { Order, PriceRow } from '@/api/types'
import { DASH, money } from '@/format'

/** 近 7 天名次的一句话：一直第 N 名 / 第 a ↔ b 名来回。 */
export function rankRangeText(hist: (number | null)[]): string {
  const v = hist.filter((x): x is number => x != null)
  if (!v.length) return '近 7 天无名次记录'
  const lo = Math.min(...v)
  const hi = Math.max(...v)
  return lo === hi ? `一直第 ${lo} 名` : `第 ${lo} ↔ ${hi} 名来回`
}

/** 卡片标题：有名次写「排名第 N」；没有名次按原因说（在抽样之外 / 查过没找到 / 还没检查）。 */
export function rankTitle(o: Pick<Order, 'rank' | 'beyond' | 'checked'>): string {
  if (o.rank != null) return `我的求购排名第 ${o.rank}`
  if (!o.checked) return '我的求购尚未检查'
  if (o.beyond) return `我的求购排在第 ${o.beyond} 名以后`
  return '队列中未找到我的求购'
}

/** 我的价到了榜首价的百分之几（进度条）。已是第 1 名算 100。 */
export function progressPct(o: Pick<Order, 'rank' | 'my_price' | 'top1_price'>): number {
  if (o.rank === 1) return 100
  const { my_price: my, top1_price: top } = o
  return my != null && top ? Math.min(100, Math.round((my / top) * 100)) : 0
}

/** 进度条下面那句：只讲距离，不讲推送（用户 2026-09-27：标题和卡片里不描述推送）。 */
export function progressText(o: Pick<Order, 'rank' | 'my_price' | 'top1_price' | 'gap_ratio'>): string {
  if (o.rank === 1) return '排在第 1 名'
  if (o.my_price == null || !o.top1_price) return '暂无榜首价'
  const ratio = o.gap_ratio == null ? '' : ` · 榜首是我的 ${o.gap_ratio.toFixed(2)} 倍`
  return `已到榜首的 ${progressPct(o)}%${ratio}`
}

/** 多张单时的切换标签：名次 + 我的价。 */
export function orderTab(o: Pick<Order, 'rank' | 'my_price'>): string {
  return `${o.rank != null ? `第 ${o.rank} 名` : '未上榜'} · ${money(o.my_price)}`
}

export interface CompareRow extends PriceRow {
  youpin: boolean
  sellPct: number | null
  bidPct: number | null
  sellText: string
  sellSub: string
  bidText: string
  bidSub: string
}

/** 各平台比价：条形长度 = 在售最低价，圆点 = 最高求购价，都按同一把尺子（最大值再留 8% 余量）。 */
export function compareRows(rows: PriceRow[], my: number | null): { rows: CompareRow[]; myPct: number | null } {
  const vals = [...rows.flatMap((r) => [r.sell, r.bid]), my].filter((x): x is number => x != null && x > 0)
  const max = vals.length ? Math.max(...vals) * 1.08 : 1
  const pct = (v: number | null) => (v == null ? null : +((v / max) * 100).toFixed(2))
  return {
    myPct: pct(my),
    rows: rows.map((r) => ({
      ...r,
      youpin: r.platform === 'YOUPIN',
      sellPct: pct(r.sell),
      bidPct: pct(r.bid),
      sellText: money(r.sell),
      sellSub: r.sell_count == null ? '' : `${r.sell_count} 件`,
      bidText: r.bid == null ? DASH : money(r.bid),
      bidSub: r.bid_count == null ? '' : `${r.bid_count} 单`,
    })),
  }
}
