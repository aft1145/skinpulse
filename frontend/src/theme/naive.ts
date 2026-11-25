import type { GlobalThemeOverrides } from 'naive-ui'
import type { Tokens } from './tokens'

export const FONT_SANS = "'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', system-ui, -apple-system, sans-serif"
export const FONT_MONO = "'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, Consolas, monospace"

/** 由设计令牌生成 Naive UI 的主题覆盖：组件库的配色、圆角、字体全部跟着令牌走。 */
export function themeOverrides(t: Tokens): GlobalThemeOverrides {
  return {
    common: {
      primaryColor: t.accent,
      primaryColorHover: t.accentHover,
      primaryColorPressed: t.accentPressed,
      primaryColorSuppl: t.accent,
      fontFamily: FONT_SANS,
      fontFamilyMono: FONT_MONO,
      borderRadius: '10px',
      borderRadiusSmall: '8px',
      bodyColor: t.bg,
      cardColor: t.surface,
      modalColor: t.surface,
      popoverColor: t.tipBg,
      tableColor: t.surface,
      inputColor: t.surface,
      textColorBase: t.text,
      textColor1: t.text,
      textColor2: t.text2,
      textColor3: t.text3,
      placeholderColor: t.text3,
      borderColor: t.lineStrong,
      dividerColor: t.lineSoft,
      hoverColor: t.surface2,
      successColor: t.ok,
      warningColor: t.warn,
      errorColor: t.up,
    },
    DataTable: {
      thColor: t.surface,
      tdColor: t.surface,
      tdColorHover: t.surface2,
      // 排序列不加底色：视觉稿里各列一样平
      tdColorSorting: t.surface,
      thColorSorting: t.surface,
      thColorHover: t.surface2,
      borderColor: t.lineSoft,
      thTextColor: t.text3,
      thFontWeight: '500',
      tdTextColor: t.text,
      thPaddingMedium: '12px 16px',
      tdPaddingMedium: '12px 16px',
      borderRadius: '18px',
    },
    Input: { borderRadius: '10px', heightMedium: '38px' },
    Button: { borderRadiusMedium: '10px', heightMedium: '40px', fontWeight: '500' },
    Card: { borderRadius: '18px', borderColor: t.line },
    Tooltip: { color: t.tipBg, textColor: t.text, boxShadow: t.popShadow },
  }
}
