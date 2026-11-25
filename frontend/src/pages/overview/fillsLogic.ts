// 总览「最近成交」卡片的纯逻辑（成交推送 spec §7.1）：每笔成交是怎么知道的、时间怎么写、小柱图的几何。
// vitest 覆盖（fillsLogic.test.ts）。时间一律按服务端的时区（Meta.timezone）显示。
import type { Fill } from '@/api/types'
import { parts } from '@/format'

/** 发现比成交晚这么久以上（比如 Mac 睡着了）算「补推」。和推送正文的说明行同一个门槛（core/messages.py）。 */
export const LATE_MINUTES = 20

export type KnownTone = 'accent' | 'ok' | 'warn' | 'muted'

const minutesBetween = (a: string, b: string) => Math.round((Date.parse(b) - Date.parse(a)) / 60_000)

function span(minutes: number): string {
  if (minutes < 60) return `${minutes} 分钟`
  const h = Math.floor(minutes / 60), m = minutes % 60
  return m ? `${h} 小时 ${m} 分钟` : `${h} 小时`
}

/** MM-DD HH:MM */
export function monthDayTime(iso: string, tz: string): string {
  const p = parts(new Date(iso), tz)
  return `${p.month}-${p.day} ${p.hour}:${p.minute}`
}

/** 和成交同一天只写 HH:MM，跨天写 MM-DD HH:MM */
function near(iso: string, ref: string, tz: string): string {
  const p = parts(new Date(iso), tz), r = parts(new Date(ref), tz)
  const hm = `${p.hour}:${p.minute}`
  return p.year === r.year && p.month === r.month && p.day === r.day ? hm : `${p.month}-${p.day} ${hm}`
}

export function fillTime(f: Fill, tz: string): string {
  const t = monthDayTime(f.filled_at, tz)
  return f.exact ? t : `约 ${t}`
}

/** 「怎么知道的」那一列：交易中时推送 / 成交后推送 / 成交后补推（晚 20 分钟以上） / 没有推送。 */
export function howKnown(f: Fill, tz: string): { label: string; tone: KnownTone; note: string } {
  if (f.notice_kind === 'IN_TRADE' && f.notice_at) {
    const ahead = minutesBetween(f.notice_at, f.filled_at)
    const note = near(f.notice_at, f.filled_at, tz) + (f.exact && ahead > 0 ? `，提前 ${span(ahead)}` : '')
    return { label: '交易中时推送', tone: 'accent', note }
  }
  if (f.notice_kind === 'FILLED' && f.notice_at) {
    // 晚多少量到推送这一刻：平时和发现消失在同一轮，但推送失败重试、或者服务没重启时会差很多
    const late = f.exact ? minutesBetween(f.filled_at, f.notice_at) : 0
    if (late >= LATE_MINUTES) {
      return { label: '成交后补推', tone: 'warn', note: `${near(f.notice_at, f.filled_at, tz)} 推送，延迟 ${span(late)}` }
    }
    return { label: '成交后推送', tone: 'ok', note: near(f.notice_at, f.filled_at, tz) }
  }
  return { label: '未推送', tone: 'muted', note: '' }
}

export interface Bar { x: number; y: number; w: number; h: number; day: string; n: number }
export interface Stub { x: number; y: number; w: number; h: number }

const BAR_W = 6
const STUB_H = 3
const TOP_PAD = 6

/** 每天一根柱子，柱高按最多的那天归一；没有成交的日子画一道浅色短线，免得看起来像缺数据。 */
export function barsGeometry(daily: [string, number][], width: number, height: number): {
  bars: Bar[]; stubs: Stub[]; label: string
} {
  const n = daily.length
  const gap = n > 1 ? (width - n * BAR_W) / (n - 1) : 0
  const max = Math.max(0, ...daily.map(([, c]) => c))
  const bars: Bar[] = []
  const stubs: Stub[] = []
  daily.forEach(([day, c], i) => {
    const x = Math.round(i * (BAR_W + gap) * 10) / 10
    if (c > 0 && max > 0) {
      const h = Math.round((c / max) * (height - TOP_PAD) * 10) / 10
      bars.push({ x, y: height - h, w: BAR_W, h, day, n: c })
    } else {
      stubs.push({ x, y: height - STUB_H, w: BAR_W, h: STUB_H })
    }
  })
  const total = daily.reduce((a, [, c]) => a + c, 0)
  return { bars, stubs, label: `近 ${n} 天成交 ${total} 件，最多一天 ${max} 件` }
}
