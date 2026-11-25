import { describe, expect, it } from 'vitest'
import type { BriefRow } from '@/api/types'
import { pickDefault, sentText } from './logic'

const row = (date: string, over: Partial<BriefRow> = {}): BriefRow => ({
  date, decision: 'sent', kind: 'sent', label: '已发送', summary: '', delivery: null, title: null,
  sent_at: null, table: null, note: null, body: null, ...over,
})

describe('brief page', () => {
  const now = new Date('2026-09-27T07:00:00Z')
  it('says when a brief went out, and when it was a catch-up', () => {
    expect(sentText({ kind: 'sent', sent_at: '2026-09-26T16:00:00Z' }, 'Asia/Shanghai', now)).toBe('发于 00:00')
    expect(sentText({ kind: 'late', sent_at: '2026-09-24T04:16:00Z' }, 'Asia/Shanghai', now)).toBe('补发于 09-24 12:16')
    expect(sentText({ kind: 'gap', sent_at: null }, 'Asia/Shanghai', now)).toBe('')
  })
  it('selects the day from the URL, else the newest one with something to show', () => {
    const rows = [row('2026-09-27', { kind: 'gap' }), row('2026-09-26', { body: 'x' }), row('2026-09-25', { table: [] })]
    expect(pickDefault(rows, '2026-09-25')).toBe('2026-09-25')
    expect(pickDefault(rows, '2026-01-01')).toBe('2026-09-26')
    expect(pickDefault([], null)).toBeNull()
  })
})
