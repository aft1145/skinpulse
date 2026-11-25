import { describe, expect, it } from 'vitest'
import type { Order } from '@/api/types'
import { itemsFromOrders, searchItems, type ItemHit } from './palette'

const o = (template_id: number, title: string, rank: number | null, extra: Partial<Order> = {}) =>
  ({ template_id, title, rank, star: false, stattrak: false, tag: '崭新出厂', ...extra }) as Order

describe('itemsFromOrders', () => {
  it('dedupes by template and keeps the best rank', () => {
    const items = itemsFromOrders([o(1, 'AK-47 | 红线', 5), o(1, 'AK-47 | 红线', 2), o(2, 'AWP | 树蝰', null)])
    expect(items).toHaveLength(2)
    expect(items.find((i) => i.template_id === 1)!.rank).toBe(2)
  })
})

describe('searchItems', () => {
  const items: ItemHit[] = [
    { template_id: 1, title: 'CZ75 | 短趾雕', star: false, stattrak: true, tag: '崭新出厂', rank: 2 },
    { template_id: 2, title: 'CZ75 | 短趾雕', star: false, stattrak: false, tag: '崭新出厂', rank: 7 },
    { template_id: 3, title: 'M4A4 | 龙王', star: false, stattrak: false, tag: '略有磨损', rank: 70 },
    { template_id: 4, title: '暗影双匕 | 自动化', star: true, stattrak: false, tag: '略有磨损', rank: 4 },
  ]
  it('matches every word across name, StatTrak and wear, ignoring case and spaces', () => {
    expect(searchItems(items, 'cz75 短趾').map((i) => i.template_id)).toEqual([1, 2])
    expect(searchItems(items, '短趾雕 stattrak').map((i) => i.template_id)).toEqual([1])
    expect(searchItems(items, '略有磨损').map((i) => i.template_id)).toEqual([4, 3])
  })
  it('returns nothing for an empty query and respects the limit', () => {
    expect(searchItems(items, '  ')).toEqual([])
    expect(searchItems(items, 'cz75', 1)).toHaveLength(1)
  })
})
