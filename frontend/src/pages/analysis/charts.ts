// 分析页两张图的 option（纯函数）。只 import echarts 的类型。
import type { EChartsOption } from 'echarts'
import type { Analysis } from '@/api/types'
import { categoryAxis, tooltipStyle, valueAxis } from '@/charts/theme'
import type { Tokens } from '@/theme/tokens'
import { FONT_MONO } from '@/theme/naive'
import { bridges, series, type Metric, type Slot } from './logic'

const METRICS: { key: Metric; name: string; color: (t: Tokens) => string }[] = [
  { key: 'top3', name: '前 3 名', color: (t) => t.bronze },
  { key: 'top2', name: '前 2 名', color: (t) => t.silver },
  { key: 'rank1', name: '第 1 名', color: (t) => t.gold },
]

export function dailyOption(ss: Slot[], t: Tokens): EChartsOption {
  const labels = ss.map((s) => s.label)
  const breakIdx = ss.map((s, i) => (s.kind === 'break' ? i : -1)).filter((i) => i >= 0)
  const solid = METRICS.map((m) => ({
    id: m.key,
    type: 'line' as const,
    name: m.name,
    data: series(ss, m.key),
    connectNulls: false,
    symbol: 'circle',
    symbolSize: 7,
    lineStyle: { width: 2.4, color: m.color(t) },
    itemStyle: { color: t.surface, borderColor: m.color(t), borderWidth: 2 },
    // 断口：浅底色标出这一格是「服务没跑」，横轴标签写停了几天
    markArea: m.key === 'top3' && breakIdx.length
      ? { silent: true, itemStyle: { color: t.surface2 }, data: breakIdx.map((i) => [{ xAxis: i }, { xAxis: i }]) as never }
      : undefined,
  }))
  const dashed = METRICS.flatMap((m) =>
    bridges(ss, m.key).map((line, i) => ({
      id: `bridge-${m.key}-${i}`,
      type: 'line' as const,
      data: line,
      connectNulls: false,
      showSymbol: false,
      silent: true,
      tooltip: { show: false },
      lineStyle: { width: 1.4, color: m.color(t), type: [2, 4] as number[], opacity: 0.55 },
    })),
  )
  return {
    grid: { left: 8, right: 44, top: 16, bottom: 30 },
    xAxis: {
      ...categoryAxis(t, labels),
      boundaryGap: true,
      axisLabel: {
        ...categoryAxis(t, labels).axisLabel,
        interval: 0,
        formatter: (v: string) => (v.startsWith('停') ? `{gap|${v}}` : v),
        rich: { gap: { color: t.text3, fontSize: 11, backgroundColor: t.surface, borderColor: t.line, borderWidth: 1, borderRadius: 6, padding: [2, 6] } },
      },
    },
    yAxis: { ...valueAxis(t), minInterval: 1, min: 0 },
    tooltip: {
      trigger: 'axis',
      ...tooltipStyle(t),
      axisPointer: { type: 'line', lineStyle: { color: t.lineStrong, type: 'dashed' } },
      formatter: (params: unknown) => {
        const i = ((Array.isArray(params) ? params[0] : params) as { dataIndex: number }).dataIndex
        const s = ss[i]
        if (!s) return ''
        if (s.kind === 'break') return `服务停机 ${s.days} 天，期间无数据`
        const r = s.row!
        const line = (k: string, v: number | null) =>
          `<div style="display:flex;justify-content:space-between;gap:18px"><span style="opacity:.7">${k}</span><b style="font-family:${FONT_MONO}">${v ?? '—'} 张</b></div>`
        return `<div style="font-family:${FONT_MONO};opacity:.7;margin-bottom:4px">${s.date}</div>` +
          line('前 3 名', r.top3) + line('前 2 名', r.top2) + line('第 1 名', r.rank1) +
          `<div style="opacity:.6;margin-top:4px;font-size:12px">当天检查 ${r.total} 张</div>`
      },
    },
    series: [...solid, ...dashed],
  }
}

export function distOption(dist: Analysis['distribution'], t: Tokens): EChartsOption {
  const medal = [t.gold, t.silver, t.bronze]
  return {
    grid: { left: 8, right: 8, top: 28, bottom: 28 },
    xAxis: { ...categoryAxis(t, dist.map((b) => b.label)), axisLabel: { ...categoryAxis(t, []).axisLabel, interval: 0 } },
    yAxis: { type: 'value', show: false },
    tooltip: { trigger: 'item', ...tooltipStyle(t), formatter: (p: unknown) => {
      const x = p as { name: string; value: number }
      return `第 ${x.name} 名：<b style="font-family:${FONT_MONO}">${x.value}</b> 张`
    } },
    series: [{
      type: 'bar',
      barCategoryGap: '18%',
      data: dist.map((b, i) => ({
        value: b.count,
        itemStyle: { color: medal[i] ?? (i < 10 ? t.lineStrong : t.bar), borderRadius: [6, 6, 2, 2] },
        label: { show: true, position: 'top', color: medal[i] ?? t.text2, fontFamily: FONT_MONO, fontSize: 12, fontWeight: 600 },
      })),
    }],
  }
}
