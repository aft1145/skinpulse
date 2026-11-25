// 图表的公共样式：颜色一律取自设计令牌（useTheme().tokens），明暗切换时跟着变。
// 只放纯对象，不 import echarts 的运行时代码。
import type { Tokens } from '@/theme/tokens'
import { FONT_MONO, FONT_SANS } from '@/theme/naive'

export function textStyle(t: Tokens) {
  return { fontFamily: FONT_SANS, color: t.text2 }
}

/** 数值轴：刻度用等宽数字、注释色，分隔线用最淡的线。 */
export function valueAxis(t: Tokens) {
  return {
    type: 'value' as const,
    position: 'right' as const,
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: t.text3, fontFamily: FONT_MONO, fontSize: 11 },
    splitLine: { lineStyle: { color: t.lineSoft } },
  }
}

export function categoryAxis(t: Tokens, data: string[]) {
  return {
    type: 'category' as const,
    data,
    boundaryGap: true,
    axisLine: { lineStyle: { color: t.line } },
    axisTick: { show: false },
    axisLabel: { color: t.text3, fontFamily: FONT_MONO, fontSize: 11, hideOverlap: true },
  }
}

export function tooltipStyle(t: Tokens) {
  return {
    backgroundColor: t.tipBg,
    borderColor: t.line,
    borderWidth: 1,
    padding: [10, 12],
    textStyle: { color: t.text, fontFamily: FONT_SANS, fontSize: 13 },
    extraCssText: `border-radius: 12px; box-shadow: ${t.popShadow};`,
  }
}

export function crossPointer(t: Tokens) {
  return {
    type: 'cross' as const,
    lineStyle: { color: t.lineStrong, type: 'dashed' as const },
    crossStyle: { color: t.lineStrong },
    label: { backgroundColor: t.text2, color: t.surface, fontFamily: FONT_MONO },
  }
}

/** 图下方的缩放滑块：背景是整段收盘价的迷你线，选中的窗口用主色框出来。 */
export function zoomSlider(t: Tokens) {
  return {
    type: 'slider' as const,
    start: 0,
    end: 100,
    filterMode: 'none' as const,
    height: 22,
    bottom: 8,
    left: 8,
    right: 60,
    borderColor: 'transparent',
    backgroundColor: t.segBg,
    fillerColor: t.accentSoft,
    dataBackground: { lineStyle: { color: t.text3, width: 1 }, areaStyle: { color: 'transparent' } },
    selectedDataBackground: { lineStyle: { color: t.accent, width: 1 }, areaStyle: { color: 'transparent' } },
    handleStyle: { color: t.surface, borderColor: t.accent },
    moveHandleStyle: { color: t.accent, opacity: 0.5 },
    textStyle: { color: t.text3, fontSize: 11 },
    brushSelect: false,
  }
}
