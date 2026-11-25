// 总览最上面的「暂停中的求购单」卡片（暂停单 spec §5，视觉稿已确认）：纯函数，vitest 覆盖。
// 缺失写「—」绝不写 0；在售「无人在售」和「还没查」分开（走 sellLabel）。
import type { PausedItem, PausedPayload, PausedRun, PausedTier } from '@/api/types'
import { DASH, clock, relative, sellLabel } from '@/format'

export type CardState = 'hidden' | 'never' | 'running' | 'risky' | 'safe'
export type RiskTier = Exclude<PausedTier, 'other'>

export const RISK_TIERS: RiskTier[] = ['inverted', 'top1', 'near', 'near_sell']

export const TIER_LABEL: Record<RiskTier, string> = {
  inverted: '倒挂 · 我的价 ≥ 在售最低',
  top1: '直接第 1',
  near: '离第 1 差 5% 内',
  near_sell: '贴近在售 · ≥ 90%',
}

const HEADLINE: Record<RiskTier, string> = {
  inverted: '恢复后会倒挂',
  top1: '恢复后直接第 1',
  near: '恢复后离第 1 差不到 5%',
  near_sell: '恢复后贴近在售价',
}

function riskCount(p: PausedPayload): number {
  const t = p.summary?.tiers
  return t ? RISK_TIERS.reduce((n, k) => n + t[k].count, 0) : 0
}

export function cardState(p: PausedPayload | null): CardState {
  if (!p) return 'hidden'
  if (p.run?.status === 'running') return 'running'
  if (!p.result || !p.summary) return p.known_paused > 0 ? 'never' : 'hidden'
  if (riskCount(p) > 0) return 'risky'
  // 查过、没有危险的单：之后又有单被暂停，回到「查一下」的样子
  if (p.paused_since > 0) return 'never'
  return p.summary.total > 0 ? 'safe' : 'hidden'
}

/** 有危险时的大标题：最严重的那一档。 */
export function riskHeadline(p: PausedPayload): { n: number; text: string; tier: RiskTier } | null {
  const t = p.summary?.tiers
  if (!t) return null
  const tier = RISK_TIERS.find((k) => t[k].count > 0)
  return tier ? { n: t[tier].count, text: HEADLINE[tier], tier } : null
}

/** 「查一下」状态的标题。从没查过时不写张数：库里的暂停事件包括暂停期间被撤掉的单，数会偏大
 * （11-24 库副本：库里 498 张，实际暂停中、库里有记录的 416 张）。查过之后的「又被暂停」是准的。 */
export function neverTitle(p: PausedPayload): { n: number | null; text: string } {
  if (p.result && p.paused_since > 0) return { n: p.paused_since, text: '检查后被暂停，充值前请重新检查' }
  return { n: null, text: '有求购单被暂停，充值前请检查' }
}

/** 最近一次检查没完成（失败、中途停了），而卡片显示的是更早的结果或者没有结果：要说出来，别让人拿旧结果去充值。 */
export function lastFailure(p: PausedPayload): string | null {
  const r = p.run
  if (!r || (r.status !== 'failed' && r.status !== 'interrupted')) return null
  if (p.result && p.result.id === r.id) return null
  return `上次检查未完成：${r.error ?? '服务已重启'}`
}

function hoursSince(iso: string, now: Date): number {
  return (now.getTime() - new Date(iso).getTime()) / 3_600_000
}

/** 副标题：前一句是要留意的（warn 色），后一句是检查信息。 */
export function subline(p: PausedPayload, tz: string, now: Date = new Date()): { warn: string | null; info: string } {
  const r = p.result
  if (!r || !p.summary) return { warn: null, info: '' }
  const warns: string[] = []
  const failed = lastFailure(p)
  if (failed) warns.push(failed)
  const at = r.finished_at ?? r.started_at
  if (hoursSince(at, now) > p.stale_h) warns.push(`结果是 ${relative(at, now)}的`)
  if (p.paused_since > 0) warns.push(`检查之后又有 ${p.paused_since} 张被暂停`)
  const info = [`检查于 ${clock(at, tz, now)}`, `暂停中 ${p.summary.total} 张`]
  if (p.summary.old > 0) info.push(`${p.summary.old} 张暂停超过 ${p.old_days} 天`)
  if (r.incomplete) info.push('仅查询了前几十页，结果不完整')
  if (r.skipped.length) info.push(`${r.skipped.map((id) => p.accounts[String(id)] ?? `账号 ${id}`).join('、')} 查询失败`)
  return { warn: warns.length ? warns.join('，') : null, info: info.join(' · ') }
}

export function needsRecheck(p: PausedPayload, now: Date = new Date()): boolean {
  const r = p.result
  return !!r && (p.paused_since > 0 || lastFailure(p) != null || hoursSince(r.finished_at ?? r.started_at, now) > p.stale_h)
}

export interface TierBlock {
  key: RiskTier
  label: string
  count: number
  amount: number
}

export function tierBlocks(p: PausedPayload): TierBlock[] {
  const t = p.summary?.tiers
  if (!t) return []
  return RISK_TIERS.map((key) => ({ key, label: TIER_LABEL[key], count: t[key].count, amount: t[key].amount }))
}

/** 「恢复后」一格：第 1 的写高出多少，不是第 1 的写差多少。 */
export function afterText(it: PausedItem): { first: boolean; text: string } {
  if (it.top1_kind === 'alone') return { first: true, text: '无人求购' }
  if (it.top1_kind === 'tie') return { first: true, text: '并列' }
  if (it.top1_kind === 'ahead' && it.over != null) return { first: true, text: `高出 ¥${num(it.over)}` }
  if (it.gap_ratio != null) return { first: false, text: `差 ${(it.gap_ratio * 100).toFixed(1)}%` }
  return { first: false, text: DASH }
}

/** 去掉多余的 0：650 → 650、13.4 → 13.4、402.64 → 402.64 */
export function num(v: number | null | undefined): string {
  if (v == null) return DASH
  return String(Number(v.toFixed(2)))
}

export function sellText(it: PausedItem, now: Date = new Date()): { main: string; note: string } {
  const main = it.sell_src == null && it.sell_depth == null ? DASH : sellLabel(it.sell_min, it.sell_depth)
  let note = ''
  if (it.sell_src === 'youpin') note = it.sell_depth ? `实时 · ${it.sell_depth} 件` : '实时'
  else if (it.sell_src === 'steamdt') note = it.sell_at ? `SteamDT · ${relative(it.sell_at, now)}` : 'SteamDT'
  return { main: main === '无人在售' || main === DASH ? main : num(it.sell_min), note }
}

/** 「我的价 / 在售」的小横条：0～200% 画满，100% 处是竖线。 */
export function ratioBar(ratio: number | null): { width: number; pct: string; over: boolean } {
  if (ratio == null) return { width: 0, pct: DASH, over: false }
  return { width: Math.min(100, Math.max(0, ratio * 50)), pct: `${Math.round(ratio * 100)}%`, over: ratio >= 1 }
}

const PHASE_LABEL: Record<NonNullable<PausedRun['phase']>, string> = { pages: '翻暂停单', prices: '查在售价', verify: '核对在售' }
const STEPS: NonNullable<PausedRun['phase']>[] = ['pages', 'prices', 'verify']

/** 检查中：当前一步、计数、整体进度（翻页 0～15%，SteamDT 15～80%，核对 80～100%）、大概还要几分钟。 */
export function progress(run: PausedRun): {
  label: string
  count: string
  pct: number
  minutes: number | null
  steps: { key: string; label: string; state: 'done' | 'current' | 'todo' }[]
} {
  const phase = run.phase ?? 'pages'
  const frac = run.total > 0 ? Math.min(1, run.done / run.total) : 0
  let pct = 0
  let count = ''
  let minutes: number | null = null
  if (phase === 'pages') {
    pct = Math.min(14, run.done)
    count = run.done ? `第 ${run.done} 页` : ''
    minutes = 8
  } else if (phase === 'prices') {
    pct = 15 + 65 * frac
    count = `${run.done} / ${run.total} 批`
    minutes = Math.max(1, Math.ceil(((run.total - run.done) * 65 + 100) / 60))
  } else {
    pct = 80 + 20 * frac
    count = `${run.done} / ${run.total}`
    minutes = Math.max(1, Math.ceil(((run.total - run.done) * 4) / 60))
  }
  const at = STEPS.indexOf(phase)
  return {
    label: PHASE_LABEL[phase],
    count,
    pct: Math.round(pct),
    minutes,
    steps: STEPS.map((k, i) => ({ key: k, label: PHASE_LABEL[k], state: i < at ? 'done' : i === at ? 'current' : 'todo' })),
  }
}

/** 明细：选中的档，先给 limit 行。 */
export function visibleItems(items: PausedItem[], tier: RiskTier | null, limit: number | null): PausedItem[] {
  const rows = tier ? items.filter((i) => i.tier === tier) : []
  return limit == null ? rows : rows.slice(0, limit)
}

/** 多号时商品名后的账号简称：第一个号不带（同总览表格，多账号 spec §8）。 */
export function pausedAccountTag(it: PausedItem, accounts: Record<string, string>): string | null {
  const ids = Object.keys(accounts).map(Number).sort((a, b) => a - b)
  if (ids.length < 2 || it.account_id === ids[0]) return null
  return accounts[String(it.account_id)] ?? null
}
