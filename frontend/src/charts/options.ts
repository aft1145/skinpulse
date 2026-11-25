// 图表的 option 组装（纯函数，vitest 覆盖）。只 import echarts 的类型，不带运行时代码。
import type { EChartsOption } from 'echarts'
import type { Bar, Window, YRange } from '@/api/types'
import { localDate, money, parts } from '@/format'
import type { Tokens } from '@/theme/tokens'
import { MA_DAILY, MA_WEEKLY, candleColors, lastN, maSeries, toCandle, weekGaps, windowBars } from './kline'
import { categoryAxis, crossPointer, tooltipStyle, valueAxis, zoomSlider } from './theme'

/** 纵轴范围：后端按实体算好的范围（含截断），再把我的求购价也框进来，虚线才看得见。 */
export function yBounds(range: YRange | null, my: number | null): { min: number; max: number } | null {
  if (!range) return null
  const lo = my == null ? range.lo : Math.min(range.lo, my)
  const hi = my == null ? range.hi : Math.max(range.hi, my)
  const pad = (hi - lo) * 0.04 || 1
  return { min: +(lo - pad).toFixed(2), max: +(hi + pad).toFixed(2) }
}

export interface KlineInput {
  bars: Bar[]
  win: Window
  range: YRange | null
  /** 我的求购价：画成 accent 色虚线；大盘页没有，传 null */
  myPrice: number | null
  tz: string
  t: Tokens
}

export interface KlineView {
  dates: string[]
  candles: [number, number, number, number][]
  /** 短、长两条均线（日线 MA7 / MA30，周线 MA4 / MA13） */
  ma7: (number | null)[]
  ma30: (number | null)[]
  maNames: [string, string]
  /** 周线里缺数据的地方（只有「全部」才算） */
  gaps: { index: number; weeks: number }[]
  option: EChartsOption
}

const row = (k: string, v: string, color?: string) =>
  `<div style="display:flex;justify-content:space-between;gap:16px"><span style="opacity:.7">${k}</span>` +
  `<span style="font-family:'JetBrains Mono',monospace${color ? `;color:${color}` : ''}">${v}</span></div>`

export function klineView({ bars, win, range, myPrice, tz, t }: KlineInput): KlineView {
  const n = windowBars(win)
  const weekly = win === 'all'
  const [a, b] = weekly ? MA_WEEKLY : MA_DAILY
  const maNames: [string, string] = weekly ? [`MA${a} 周`, `MA${b} 周`] : [`MA${a}`, `MA${b}`]
  // 均线先在全部数据上算、再切窗口：窗口开头的 MA30 才是真正的 30 日均价
  const closes = bars.map((x) => x[2])
  const ma7 = lastN(maSeries(closes, a), n)
  const ma30 = lastN(maSeries(closes, b), n)
  const shown = lastN(bars, n)
  const dates = shown.map((x) => localDate(x[0], tz))
  const candles = shown.map(toCandle)
  const yb = yBounds(range, myPrice)
  const short = win === '1y' || weekly ? (d: string) => d.slice(2) : (d: string) => d.slice(5)
  // 周线缺数据的地方画一条竖虚线，标「缺 N 周」
  const gaps = weekly ? weekGaps(shown) : []

  const option: EChartsOption = {
    grid: { left: 8, right: 60, top: 16, bottom: 64 },
    xAxis: { ...categoryAxis(t, dates), axisLabel: { ...categoryAxis(t, dates).axisLabel, formatter: short } },
    // 范围的两端不标刻度：后端算出的 lo/hi 是 16.73 这类零碎数，标出来反而难读
    yAxis: { ...valueAxis(t), scale: true, ...(yb ?? {}), axisLabel: { ...valueAxis(t).axisLabel, showMinLabel: false, showMaxLabel: false } },
    // 缩放只改横轴，纵轴范围保持后端给的那一份（filterMode none）。滚轮留给页面滚动：
    // 拖动平移、触控板双指捏合（浏览器里是 ctrl+滚轮）缩放，或者拖下面的滑块。
    dataZoom: [
      { type: 'inside', start: 0, end: 100, filterMode: 'none', zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: false, moveOnMouseMove: true },
      zoomSlider(t),
    ],
    tooltip: {
      trigger: 'axis',
      axisPointer: crossPointer(t),
      ...tooltipStyle(t),
      formatter: (params: unknown) => {
        const i = (Array.isArray(params) ? params[0] : params)?.dataIndex as number | undefined
        if (i == null || !shown[i]) return ''
        const [, o, c, h, l] = shown[i]
        return (
          `<div style="display:flex;flex-direction:column;gap:5px;min-width:168px">` +
          `<b style="font-family:'JetBrains Mono',monospace">${dates[i]}${weekly ? ' 那周' : ''}</b>` +
          row('开 / 收', `${money(o)} / ${money(c)}`, c >= o ? t.up : t.down) +
          row('高 / 低', `${money(h)} / ${money(l)}`) +
          row(`${maNames[0]} / ${maNames[1]}`, `${money(ma7[i])} / ${money(ma30[i])}`) +
          `</div>`
        )
      },
    },
    series: [
      {
        type: 'candlestick',
        name: '日线',
        data: candles,
        itemStyle: candleColors(t),
        barMaxWidth: 12,
        clip: true,
        markLine:
          myPrice == null
            ? undefined
            : {
                symbol: 'none',
                silent: true,
                animation: false,
                lineStyle: { color: t.accent, type: [6, 5], width: 1.5 },
                label: {
                  position: 'insideStartTop',
                  formatter: `我的求购 ${money(myPrice)}`,
                  color: t.onAccent,
                  backgroundColor: t.accent,
                  padding: [2, 8],
                  borderRadius: 6,
                  fontWeight: 600,
                },
                data: [{ yAxis: myPrice }],
              },
      },
      {
        type: 'line',
        name: maNames[0],
        data: ma7,
        showSymbol: false,
        connectNulls: false,
        lineStyle: { width: 1.6, color: t.ma7 },
        itemStyle: { color: t.ma7 },
        markLine: gaps.length
          ? {
              symbol: 'none',
              silent: true,
              animation: false,
              lineStyle: { color: t.lineStrong, type: [3, 3], width: 1 },
              label: { position: 'insideEndTop', formatter: '{b}', color: t.text3, fontSize: 11 },
              data: gaps.map((g) => ({ xAxis: g.index, name: `缺 ${g.weeks} 周` })),
            }
          : undefined,
      },
      { type: 'line', name: maNames[1], data: ma30, showSymbol: false, connectNulls: false, lineStyle: { width: 1.6, color: t.ma30 }, itemStyle: { color: t.ma30 } },
    ],
  }
  return { dates, candles, ma7, ma30, maNames, gaps, option }
}

/** 折线 + 渐变填充（大盘最近 25 小时）。颜色看首尾：比开头高就用涨色（红），低就用跌色（绿）。 */
export function areaLineOption(points: [string, number][], tz: string, t: Tokens): EChartsOption {
  const labels = points.map((p) => clockLabel(p[0], tz))
  const vals = points.map((p) => p[1])
  const color = vals.length > 1 && vals[vals.length - 1] < vals[0] ? t.down : t.up
  return {
    grid: { left: 4, right: 52, top: 14, bottom: 24 },
    xAxis: { ...categoryAxis(t, labels), boundaryGap: false },
    yAxis: { ...valueAxis(t), scale: true, splitNumber: 3 },
    tooltip: {
      trigger: 'axis',
      ...tooltipStyle(t),
      axisPointer: { type: 'line', lineStyle: { color: t.lineStrong, type: 'dashed' } },
      formatter: (params: unknown) => {
        const p = (Array.isArray(params) ? params[0] : params) as { dataIndex: number }
        return `<span style="font-family:'JetBrains Mono',monospace;opacity:.7">${labels[p.dataIndex]}</span>&nbsp;&nbsp;<b style="font-family:'JetBrains Mono',monospace">${money(vals[p.dataIndex])}</b>`
      },
    },
    series: [
      {
        type: 'line',
        data: vals,
        showSymbol: false,
        smooth: 0.25,
        lineStyle: { width: 2.4, color },
        itemStyle: { color },
        areaStyle: {
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [{ offset: 0, color: `${color}47` }, { offset: 1, color: `${color}00` }],
          },
        },
      },
    ],
  }
}

/** 详情页「和大盘比」：近 90 天单品（实线、accent）和大盘（虚线），起点都记作 100；缺的日子断开，不补。 */
export function relLineOption(s: { dates: string[]; item: (number | null)[]; broad: (number | null)[] }, t: Tokens): EChartsOption {
  const fmt = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(1))
  return {
    grid: { left: 4, right: 40, top: 10, bottom: 22 },
    xAxis: { ...categoryAxis(t, s.dates), boundaryGap: false },
    yAxis: { ...valueAxis(t), scale: true, splitNumber: 3 },
    tooltip: {
      trigger: 'axis',
      ...tooltipStyle(t),
      axisPointer: { type: 'line', lineStyle: { color: t.lineStrong, type: 'dashed' } },
      formatter: (params: unknown) => {
        const i = ((Array.isArray(params) ? params[0] : params) as { dataIndex: number }).dataIndex
        return `<b style="font-family:'JetBrains Mono',monospace">${s.dates[i]}</b>` + row('它', fmt(s.item[i]), t.accent) + row('大盘', fmt(s.broad[i]))
      },
    },
    series: [
      { name: '它', type: 'line', data: s.item, showSymbol: false, lineStyle: { width: 2, color: t.accent }, itemStyle: { color: t.accent } },
      { name: '大盘', type: 'line', data: s.broad, showSymbol: false, lineStyle: { width: 1.6, color: t.text3, type: 'dashed' }, itemStyle: { color: t.text3 } },
    ],
  }
}

/** 小时轴的标签：跨日的那个整点带上日期。 */
function clockLabel(iso: string, tz: string): string {
  const p = parts(new Date(iso), tz)
  return p.hour === '00' ? `${p.month}-${p.day} 00:00` : `${p.hour}:${p.minute}`
}
