// 大盘页的纯逻辑（vitest 覆盖）。
import type { Bar } from '@/api/types'
import { localDate } from '@/format'

export interface YearRange {
  lo: number
  loDate: string
  hi: number
  hiDate: string
  /** 现在的收盘价在一年区间里的位置，0–100 */
  pos: number
  /** 离一年高点差多少（%），≤ 0 */
  fromHigh: number
}

/** 一年区间：按收盘价取最高、最低（和信号一样只看收盘，离谱的影线不算数）。 */
export function yearRange(bars: Bar[], tz: string): YearRange | null {
  if (!bars.length) return null
  let lo = 0
  let hi = 0
  bars.forEach((b, i) => {
    if (b[2] < bars[lo][2]) lo = i
    if (b[2] > bars[hi][2]) hi = i
  })
  const now = bars[bars.length - 1][2]
  const L = bars[lo][2]
  const H = bars[hi][2]
  return {
    lo: L,
    loDate: localDate(bars[lo][0], tz).slice(2),
    hi: H,
    hiDate: localDate(bars[hi][0], tz).slice(2),
    pos: H === L ? 50 : +(((now - L) / (H - L)) * 100).toFixed(1),
    fromHigh: H ? +((now / H - 1) * 100).toFixed(1) : 0,
  }
}
