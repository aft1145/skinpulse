// ⌘K 命令面板的匹配规则（纯函数，vitest 覆盖）。
import type { Order } from '@/api/types'

export interface ItemHit {
  template_id: number
  title: string
  star: boolean
  stattrak: boolean
  tag: string | null
  /** 这件饰品上最好的名次（多张单取最好的），给列表右边显示 */
  rank: number | null
}

/** 总览的单子 → 按饰品去重（同一件饰品可能挂了多张单）。 */
export function itemsFromOrders(orders: Order[]): ItemHit[] {
  const by = new Map<number, ItemHit>()
  for (const o of orders) {
    const cur = by.get(o.template_id)
    const rank = cur == null ? o.rank : cur.rank == null ? o.rank : o.rank == null ? cur.rank : Math.min(cur.rank, o.rank)
    by.set(o.template_id, { template_id: o.template_id, title: o.title, star: o.star, stattrak: o.stattrak, tag: o.tag, rank })
  }
  return [...by.values()]
}

const norm = (s: string) => s.toLowerCase().replace(/\s+/g, '').replace(/[|｜]/g, '')

/** 按空格切成几个词，每个词都要出现在「简称 + StatTrak + 磨损」里（不分大小写、忽略空格和竖线）。
 * 标题开头就对上的排前面，其次名次好的排前面。 */
export function searchItems(items: ItemHit[], q: string, limit = 8): ItemHit[] {
  const words = q.trim().split(/\s+/).filter(Boolean).map(norm)
  if (!words.length) return []
  const hay = (i: ItemHit) => norm(`${i.title}${i.stattrak ? 'stattrak暗金' : ''}${i.tag ?? ''}`)
  return items
    .filter((i) => words.every((w) => hay(i).includes(w)))
    .sort((a, b) => {
      const pa = norm(a.title).startsWith(words[0]) ? 0 : 1
      const pb = norm(b.title).startsWith(words[0]) ? 0 : 1
      return pa - pb || (a.rank ?? 1e9) - (b.rank ?? 1e9) || a.title.localeCompare(b.title)
    })
    .slice(0, limit)
}
