import type { Mode, Tokens } from './tokens'

const kebab = (k: string) => k.replace(/[A-Z]/g, (c) => '-' + c.toLowerCase())

/** 令牌写成 :root 上的 CSS 变量（--sp-text、--sp-accent-soft……），组件样式里直接用。 */
export function applyVars(t: Tokens, mode: Mode, root: HTMLElement = document.documentElement): void {
  for (const [k, v] of Object.entries(t)) root.style.setProperty(`--sp-${kebab(k)}`, v)
  root.dataset.theme = mode
  root.style.colorScheme = mode
}
