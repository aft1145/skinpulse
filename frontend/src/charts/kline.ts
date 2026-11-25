// K 线的数据转换（纯函数，vitest 覆盖）。这里不引 echarts，首页的包里就不会带上它。
//
// SteamDT 的日线顺序是 [时间, 开, 收, 高, 低]（CLAUDE.md 坑 7），ECharts 的 candlestick 要的是
// [开, 收, 低, 高]。两边都不是常见的 OHLC，转错了图照样画得出来、看着还像真的，所以集中在这里转。
import type { Bar, Window, YRange } from '@/api/types'
import type { Tokens } from '@/theme/tokens'

export const WINDOWS: { key: Window; label: string; bars: number }[] = [
  { key: '1m', label: '1 月', bars: 30 },
  { key: '3m', label: '3 月', bars: 90 },
  { key: '1y', label: '1 年', bars: 365 },
  // 「全部」换成周线，整段都画（大盘从 2023-07 起，单品从 2022-07 起）
  { key: 'all', label: '全部', bars: Infinity },
]

/** 周线上的均线：MA4 ≈ 1 个月、MA13 ≈ 1 个季度（用户 2026-09-27 选的）。日线是 MA7 / MA30。 */
export const MA_DAILY: [number, number] = [7, 30]
export const MA_WEEKLY: [number, number] = [4, 13]

/** SteamDT [ts, 开, 收, 高, 低] → ECharts [开, 收, 低, 高] */
export function toCandle(b: Bar): [number, number, number, number] {
  const [, open, close, high, low] = b
  return [open, close, low, high]
}

/** n 日均线；前 n-1 根没有足够数据，给 null（ECharts 画成空档），不拿更短的区间凑。 */
export function maSeries(closes: number[], n: number): (number | null)[] {
  const out: (number | null)[] = []
  let sum = 0
  for (let i = 0; i < closes.length; i++) {
    sum += closes[i]
    if (i >= n) sum -= closes[i - n]
    out.push(i >= n - 1 ? sum / n : null)
  }
  return out
}

export function windowBars(win: Window): number {
  return WINDOWS.find((w) => w.key === win)?.bars ?? 365
}

/** 最后 n 根。均线要先在全部数据上算、再切片，窗口开头的均线才是对的。 */
export function lastN<T>(xs: T[], n: number): T[] {
  return n >= xs.length ? xs.slice() : xs.slice(xs.length - n)
}

/** 周线里缺数据的地方：相邻两根隔了 8 天以上。返回缺口后面那根的下标和缺了几周。
 * 实测单品周线有两段半年左右的空档（2023-12 到 2024-05、2024-06 到 2024-12），横轴只排有数据的周，
 * 不标出来就会被悄悄挤掉。 */
export function weekGaps(bars: Bar[]): { index: number; weeks: number }[] {
  const out: { index: number; weeks: number }[] = []
  for (let i = 1; i < bars.length; i++) {
    const days = (Date.parse(bars[i][0]) - Date.parse(bars[i - 1][0])) / 86400_000
    if (days > 8) out.push({ index: i, weeks: Math.round(days / 7) - 1 })
  }
  return out
}

/** 截断说明：悄悄截掉等于把一个真实存在的数据点藏起来，所以必须写在图上。 */
export function clipNote(r: YRange | null): string | null {
  if (!r || r.clipped <= 0) return null
  const ext = r.extreme == null ? '' : `（极值 ${r.extreme.toFixed(2)}）`
  return `${r.clipped} 根 K 线的影线远超正常范围${ext}，已截断显示`
}

/** 涨红跌绿（spec §3.1）：ECharts 里 color 是阳线（收 ≥ 开），color0 是阴线。 */
export function candleColors(t: Pick<Tokens, 'up' | 'down'>) {
  return { color: t.up, color0: t.down, borderColor: t.up, borderColor0: t.down }
}
