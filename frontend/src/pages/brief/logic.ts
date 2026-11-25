// 简报页的文案（纯函数，vitest 覆盖）。
import type { BriefKind, BriefRow } from '@/api/types'
import { clock } from '@/format'

export const KIND_LABEL: Record<BriefKind, string> = { sent: '已发', late: '补发', not_sent: '未发送', failed: '推送失败', gap: '无记录' }

/** 时间线上每天右边那一小段：发出时间，或者没发的原因（原因在摘要里）。 */
export function sentText(r: Pick<BriefRow, 'kind' | 'sent_at'>, tz: string, now: Date = new Date()): string {
  if (r.kind === 'sent') return `发于 ${clock(r.sent_at, tz, now)}`
  if (r.kind === 'late') return `补发于 ${clock(r.sent_at, tz, now)}`
  return ''
}

/** 默认选中：网址里指定的那天；没有就选最近一条有表可看的，再没有就第一行。 */
export function pickDefault(rows: BriefRow[], day: string | null): string | null {
  if (day && rows.some((r) => r.date === day)) return day
  return (rows.find((r) => r.table || r.body) ?? rows[0])?.date ?? null
}
