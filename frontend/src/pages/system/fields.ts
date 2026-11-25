// 系统页「参数设置」的字段表：中文名、说明、单位、范围（和 config.py 的校验一致，后端还会再校一遍）。
import type { FieldError, SettingsSection, SettingsValues } from '@/api/types'

export interface Field {
  section: SettingsSection
  key: string
  label: string
  desc: string
  kind: 'int' | 'float' | 'toggle' | 'time'
  unit?: string
  min?: number
  max?: number
  /** 收在「更多」里，一般不用改 */
  more?: boolean
}

export const GROUPS: { section: SettingsSection; title: string; hint: string }[] = [
  { section: 'polling', title: '监控节奏', hint: '[polling] · 查询越频繁，对悠悠的请求越多' },
  { section: 'alerts', title: '推送规则', hint: '[alerts] · 什么时候推一条「升到第 1 名」；难以成交的单不推送、不进简报' },
  { section: 'brief', title: '每日简报', hint: '[brief] · 每天一条前 N 名表' },
  { section: 'buff', title: 'BUFF', hint: '[buff] · 只读、慢：每次请求至少隔 5 秒' },
  { section: 'dashboard', title: '看板显示', hint: '[dashboard] · 只影响看板上的写法，推送和简报照旧' },
]

export const FIELDS: Field[] = [
  { section: 'polling', key: 'round_interval_min', label: '每轮间隔', desc: '多久把全部求购查一轮', kind: 'int', unit: '分钟', min: 1 },
  { section: 'polling', key: 'request_interval_s', label: '请求间隔', desc: '两次请求之间至少等多久', kind: 'float', unit: '秒', min: 0.5 },
  { section: 'polling', key: 'hourly_request_cap', label: '每小时请求上限', desc: '超过就等到下个小时', kind: 'int', unit: '次', min: 10 },
  { section: 'polling', key: 'fast_lane_rank_threshold', label: '快车道', desc: '排在前几名的单每轮都查', kind: 'int', unit: '名', min: 1 },
  { section: 'polling', key: 'night_start_hour', label: '夜间开始', desc: '这之后放慢，本地时间', kind: 'int', unit: '点', min: 0, max: 23 },
  { section: 'polling', key: 'night_end_hour', label: '夜间结束', desc: '到这个点恢复', kind: 'int', unit: '点', min: 0, max: 24 },
  { section: 'polling', key: 'night_multiplier', label: '夜间放慢', desc: '夜里的每轮间隔乘以这个倍数', kind: 'float', unit: '倍', min: 1 },
  { section: 'polling', key: 'verify_enabled', label: '排名核对', desc: '每轮抽几张用公开队列复算', kind: 'toggle' },
  { section: 'polling', key: 'jitter_pct', label: '随机抖动', desc: '每次间隔随机上下浮动的比例，0–1', kind: 'float', min: 0, max: 1, more: true },
  { section: 'polling', key: 'heartbeat_interval_min', label: '心跳间隔', desc: '多久确认一次账号还在线', kind: 'int', unit: '分钟', min: 5, more: true },
  { section: 'polling', key: 'account_gap_s', label: '账号间隔', desc: '有几个号时，一个号同步完等多久再同步下一个', kind: 'float', unit: '秒', min: 0, more: true },
  { section: 'polling', key: 'verify_sample_size', label: '每轮抽查', desc: '排名核对每轮抽几张', kind: 'int', unit: '张', min: 0, more: true },
  { section: 'polling', key: 'sell_sample_size', label: '在售价抽样', desc: '每轮刷新几张单的在售价', kind: 'int', unit: '张', min: 0, more: true },
  { section: 'polling', key: 'visible_limit', label: '公开队列可见行数', desc: '公开队列一页能看到几行', kind: 'int', unit: '行', min: 1, more: true },
  { section: 'polling', key: 'deep_page_size', label: '深查页大小', desc: '排在页外时用账号接口多看几行', kind: 'int', unit: '行', min: 0, more: true },
  { section: 'alerts', key: 'enter_rank', label: '升到第几名推送', desc: '升到这个名次以内就推一条', kind: 'int', unit: '名', min: 1 },
  // 下限（≥ 2）由「要大于升到第几名推送」隐含，只报那一条，和视觉稿一致
  { section: 'alerts', key: 'exit_rank', label: '掉到第几名以后解除', desc: '要大于「升到第几名推送」', kind: 'int', unit: '名' },
  { section: 'alerts', key: 'confirm_rounds', label: '连续确认几轮', desc: '连着几轮都在才算数，防抖', kind: 'int', unit: '轮', min: 1 },
  { section: 'alerts', key: 'max_gap_ratio', label: '离第 1 倍数', desc: '榜首价达到我的价的这个倍数时算难以成交。0 = 不按这条判断', kind: 'float', unit: '倍', min: 0 },
  { section: 'alerts', key: 'max_market_ratio', label: '离市价倍数', desc: '市价达到我的价的这个倍数，且差价不低于下一项时算难以成交。0 = 不按这条判断', kind: 'float', unit: '倍', min: 0 },
  { section: 'alerts', key: 'min_market_gap', label: '离市价差价', desc: '差价低于这个数的不算，例如 7 元求购、在售 9.46', kind: 'float', unit: '元', min: 0 },
  { section: 'alerts', key: 'hourly_cap', label: '每小时推送上限', desc: '一小时最多推几条', kind: 'int', unit: '条', min: 1 },
  { section: 'alerts', key: 'group_wait_s', label: '合并等待', desc: '同一轮里的几条合成一条再发', kind: 'int', unit: '秒', min: 0 },
  { section: 'alerts', key: 'confirm_with_live_queue', label: '推送前实时核对', desc: '悠悠的名次滞后几分钟，推送前用公开队列再次核对', kind: 'toggle' },
  { section: 'alerts', key: 'live_confirm_max', label: '每轮最多实时核对', desc: '一轮里最多核对几张', kind: 'int', unit: '张', min: 0 },
  { section: 'brief', key: 'enabled', label: '每日简报', desc: '关掉就不发', kind: 'toggle' },
  { section: 'brief', key: 'send_at', label: '发送时间', desc: '本地时间，发前一天的表', kind: 'time' },
  { section: 'brief', key: 'snapshot_at', label: '价格快照时间', desc: '表里「在售」取这个时刻的悠悠价', kind: 'time' },
  { section: 'brief', key: 'top_rank', label: '表里列前几名', desc: '标题和表都跟着变', kind: 'int', unit: '名', min: 1 },
  { section: 'buff', key: 'round_interval_min', label: '每轮间隔', desc: '多久查一轮 BUFF 的求购', kind: 'int', unit: '分钟', min: 5, max: 240 },
  { section: 'buff', key: 'request_gap_s', label: '请求间隔', desc: '两次请求之间至少等多久', kind: 'float', unit: '秒', min: 5 },
  { section: 'buff', key: 'queue_per_round', label: '每轮查队列', desc: '一轮最多给几张单数名次', kind: 'int', unit: '次', min: 0, max: 50 },
  { section: 'buff', key: 'pause_hours', label: '受限后暂停', desc: '撞到限流或验证码后停多久', kind: 'float', unit: '小时', min: 0.1, max: 48, more: true },
  { section: 'buff', key: 'queue_max_pages', label: '队列最多翻几页', desc: '每页 100 条', kind: 'int', unit: '页', min: 1, max: 10, more: true },
  { section: 'dashboard', key: 'platform_names', label: '账号写成平台名', desc: '账号切换和表格标签写「悠悠」「BUFF」，不写昵称；同一平台有几个号时带上简称', kind: 'toggle' },
  { section: 'brief', key: 'quiet_days', label: '无变动时', desc: '前 N 名无变化时，每隔几天发送一次', kind: 'int', unit: '天', min: 1 },
]

export type Draft = Record<string, string | boolean>
const id = (f: Field) => `${f.section}.${f.key}`

/** 设置值 → 表单草稿（输入框里都是字符串，开关是布尔）。 */
export function toDraft(v: SettingsValues): Draft {
  const d: Draft = {}
  for (const f of FIELDS) {
    const x = v[f.section]?.[f.key]
    d[id(f)] = f.kind === 'toggle' ? Boolean(x) : x == null ? '' : String(x)
  }
  return d
}

function parse(f: Field, raw: string | boolean): number | string | boolean | null {
  if (f.kind === 'toggle') return Boolean(raw)
  const s = String(raw).trim()
  if (f.kind === 'time') {
    const m = /^(\d{1,2}):(\d{2})$/.exec(s)
    if (!m || +m[1] > 23 || +m[2] > 59) return null
    return `${m[1].padStart(2, '0')}:${m[2]}`
  }
  if (!/^-?\d+(\.\d+)?$/.test(s)) return null
  const n = Number(s)
  return f.kind === 'int' && !Number.isInteger(n) ? null : n
}

/** 前端先校一遍，给出和后端一样的中文说明；后端还会再校一遍。 */
export function validate(d: Draft): FieldError[] {
  const errs: FieldError[] = []
  for (const f of FIELDS) {
    const v = parse(f, d[id(f)])
    if (v === null) {
      errs.push({ field: id(f), message: f.kind === 'time' ? '格式是 HH:MM，比如 08:00' : f.kind === 'int' ? '要填整数' : '要填数字' })
    } else if (typeof v === 'number') {
      if (f.min != null && v < f.min) errs.push({ field: id(f), message: `不能小于 ${f.min}` })
      else if (f.max != null && v > f.max) errs.push({ field: id(f), message: `不能大于 ${f.max}` })
    }
  }
  const enter = Number(d['alerts.enter_rank'])
  const exit = Number(d['alerts.exit_rank'])
  if (!errs.some((e) => e.field === 'alerts.exit_rank') && Number.isFinite(enter) && Number.isFinite(exit) && exit <= enter) {
    errs.push({ field: 'alerts.exit_rank', message: `要大于「升到第几名推送」（${enter}）` })
  }
  return errs
}

export interface Change {
  field: string
  text: string
}

/** 改了哪几项：「每轮间隔 5 → 6 分钟」。只比能解析的值，比如 3 和 3.0 不算改。 */
export function changes(orig: SettingsValues, d: Draft): Change[] {
  const out: Change[] = []
  for (const f of FIELDS) {
    const before = orig[f.section]?.[f.key]
    if (before === undefined) continue      // 后端没有这一项（旧后端没有 [dashboard]）：不算改动
    const after = parse(f, d[id(f)])
    if (after === null || after === before) continue
    const show = (x: unknown) => (f.kind === 'toggle' ? (x ? '开' : '关') : String(x))
    out.push({ field: id(f), text: `${f.label} ${show(before)} → ${show(after)}${f.unit ? ` ${f.unit}` : ''}` })
  }
  return out
}

/** 只提交改了的项。 */
export function patchOf(d: Draft, changed: Change[]): Partial<Record<SettingsSection, Record<string, unknown>>> {
  const out: Partial<Record<SettingsSection, Record<string, unknown>>> = {}
  for (const c of changed) {
    const f = FIELDS.find((x) => id(x) === c.field)!
    ;(out[f.section] ??= {})[f.key] = parse(f, d[c.field])
  }
  return out
}

export const fieldId = id
