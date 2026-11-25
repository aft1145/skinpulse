// 系统页的「数字 → 文字」（纯函数，vitest 覆盖）。
import type { Account, Channel, StatusData } from '@/api/types'
import { DASH, clock } from '@/format'

/** 开机自启的管理命令：Windows 上是 `skinpulse service`（ADR-0021），macOS 上是 launchagent.sh。 */
export function serviceCmd(r: { supervisor?: string | null; platform?: string } | undefined | null, action: string): string {
  const win = r?.supervisor === 'windows' || (r?.supervisor == null && (r?.platform ?? '').startsWith('Windows'))
  return win ? `uv run python -m skinpulse service ${action}` : `scripts/macos/launchagent.sh ${action}`
}

/** 标题只说最要紧的一件事（顺序由后端排好）；副标题是最近一轮的摘要。 */
export function headline(d: StatusData, tz: string, now: Date = new Date()): { ok: boolean; text: string; sub: string } {
  const h = d.headline
  // 多号时说清是哪个号失效了（多账号 spec §8）
  const live = (d.accounts ?? []).filter((a) => a.enabled)
  const dead = live.length >= 2 ? live.find((a) => a.logged_in && !a.token.valid) : undefined
  const text =
    h.issue === null
      ? '一切正常'
      : h.issue === 'not_logged_in'
        ? '未登录悠悠'
        : h.issue === 'token_invalid'
        ? dead ? `${dead.label}登录已失效` : 'token 已失效'
        : h.issue === 'paused'
          ? `悠悠风控暂停到 ${clock(h.paused_until, tz, now)}`
          : h.issue === 'stale'
            ? '监控未运行'
            : h.issue === 'code_outdated'
              ? '代码已更新，服务尚未重启'
              : h.issue === 'pushplus_failing'
              ? `PushPlus 最近一次推送失败（${clock(h.pushplus_error_at, tz, now)}）`
              : h.issue === 'sync_slow'
              ? '账号或求购单过多，单轮接近超时'
              : h.issue === 'buff_paused'
              ? 'BUFF 访问受限，暂停中'
              : '上一轮有错误'
  // 服务跑的还是启动时加载的旧代码（skinpulse/codeversion.py）：副标题直接给出要做的事
  if (h.issue === 'code_outdated') {
    const r = d.runtime
    const restart = serviceCmd(r, 'restart')
    const sub = r?.deps_outdated
      ? `依赖有变化：先在终端运行 uv sync，再 ${restart}`
      : restartable(d)
        ? '新代码重启后才生效'
        : `新代码重启后才生效：${restart}`
    return { ok: false, text, sub }
  }
  if (h.issue === 'not_logged_in') return { ok: false, text, sub: '录入 token 后开始监控' }
  const lr = d.service.last_round
  const at = clock(lr?.at ?? d.service.last_round_at, tz, now)
  const sub = lr
    ? `最近一轮 ${at} · 检查 ${lr.checked} 张 · ${lr.errors} 错误 · ${lr.paused ? '暂停中' : '未暂停'}`
    : d.service.last_round_at
      ? `最近一轮 ${at}`
      : '尚未运行过一轮'
  return { ok: h.issue === null, text, sub }
}

const NOTES: Record<Channel['id'], string> = {
  youpin_anon: '公开队列核对、在售价刷新。',
  youpin_account: '每轮同步你的求购和排名。',
  pushplus: '主推送通道。',
  ntfy: '可选的备用通道。',
}

/** 通道卡片：状态字、一个关键时间、一句说明。颜色从不单独表达含义（spec §3.1）。 */
export function channelView(ch: Channel, tz: string, now: Date = new Date()) {
  const state =
    ch.state === 'ok' ? '正常' : ch.state === 'warn' ? '暂停中' : ch.state === 'error' ? '出错' : ch.configured ? '尚未使用' : '未配置'
  let key = '最近成功'
  let value = clock(ch.last_ok_at, tz, now)
  if (ch.state === 'warn' && ch.paused_until) {
    key = '暂停到'
    value = clock(ch.paused_until, tz, now)
  } else if (ch.state === 'error') {
    key = '最近出错'
    value = `${clock(ch.last_error_at, tz, now)}${ch.last_error_code ? ` · ${ch.last_error_code}` : ''}`
  }
  const note = ch.id === 'ntfy' && !ch.configured ? '可选的备用通道，未录入 topic。' : NOTES[ch.id]
  return { state, key, value, note }
}

/** 已连续运行多久：「2 天 7 小时」「5 小时 3 分」「3 分钟」。 */
export function uptime(startedAt: string | null, now: Date = new Date()): string {
  if (!startedAt) return DASH
  const m = Math.max(0, Math.floor((now.getTime() - new Date(startedAt).getTime()) / 60000))
  const d = Math.floor(m / 1440)
  const h = Math.floor((m % 1440) / 60)
  if (d) return `${d} 天 ${h} 小时`
  if (h) return `${h} 小时 ${m % 60} 分`
  return `${m} 分钟`
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 ** 2) return `${Math.round(n / 1024)} KB`
  if (n < 1024 ** 3) return `${Math.round(n / 1024 ** 2)} MB`
  return `${(n / 1024 ** 3).toFixed(1)} GB`
}

/** 抽样核对的一致率：无法判定的不算进分母（那是我们的盲区，不是悠悠错了）。 */
export function agreement(v: StatusData['verification']): { pct: number | null; disagreed: number } {
  const judged = v.checked_24h - v.inconclusive_24h
  return { pct: judged > 0 ? Math.round((v.agreed_24h / judged) * 1000) / 10 : null, disagreed: judged - v.agreed_24h }
}

/** token 剩余天数环：按「10 天免登录」算满格。 */
export function tokenRing(daysLeft: number | null): number {
  if (daysLeft == null) return 0
  return Math.max(0, Math.min(1, daysLeft / 10))
}

/** token 还剩多久：「6 天 23 小时」。只写整天的话，6 天 23 小时会显示成 6 天，看着像少了一天。 */
export function tokenLeft(expiresAt: string | null, now: Date = new Date()): string | null {
  if (!expiresAt) return null
  const h = Math.floor((new Date(expiresAt).getTime() - now.getTime()) / 3_600_000)
  if (h < 0) return '已过期'
  if (h < 1) return '不到 1 小时'
  const d = Math.floor(h / 24)
  return d ? `${d} 天 ${h % 24} 小时` : `${h} 小时`
}

/** 能不能在看板上重启（intent 2026-11-23）：服务由 LaunchAgent 拉起、依赖不用先 uv sync。老后端没有这两个字段时不给按钮。 */
export function restartable(d: StatusData): boolean {
  return d.runtime?.can_restart === true && d.runtime?.deps_outdated === false
}

/** 系统页账号卡片的状态（多账号 spec §8）：tone 决定描边和胶囊颜色，note 是「现在」那一行。 */
export function accountState(a: Account, tz: string, now: Date = new Date()): { tone: 'ok' | 'bad' | 'off'; text: string; note: string | null } {
  if (a.platform === 'buff') return buffState(a, tz, now)
  if (!a.enabled) return { tone: 'off', text: '已停用', note: '不同步、不推送，它的单不算进总览；钥匙串里的 token 还留着' }
  if (!a.logged_in) return { tone: 'bad', text: '未登录', note: null }
  if (!a.token.valid) {
    const at = a.last_sync_at ? clock(a.last_sync_at, tz, now) : DASH
    return { tone: 'bad', text: '登录已失效', note: `该账号的单停留在 ${at} 的数据，不判定名次、不推送；其他账号正常` }
  }
  if (a.paused_until && new Date(a.paused_until) > now) {
    return { tone: 'bad', text: '暂停中', note: `悠悠限流或风控，${clock(a.paused_until, tz, now)} 后再试` }
  }
  return { tone: 'ok', text: '已登录', note: null }
}

/** BUFF 号的卡片状态（BUFF spec §8）：没有 token 到期；限流、验证码时暂停一阵自动恢复 */
function buffState(a: Account, tz: string, now: Date): { tone: 'ok' | 'bad' | 'off'; text: string; note: string | null } {
  if (!a.enabled) return { tone: 'off', text: '已停用', note: '不请求 BUFF、不推送，它的单不算进总览；钥匙串里的 session 还留着' }
  if (!a.token.valid) {
    const at = a.last_sync_at ? clock(a.last_sync_at, tz, now) : DASH
    return { tone: 'bad', text: '登录已失效', note: `BUFF 的单停在 ${at} 的数据，不判名次、不推送；悠悠照常` }
  }
  if (a.paused_until && new Date(a.paused_until) > now) {
    return { tone: 'bad', text: '访问受限，暂停中', note: `BUFF 要求验证或限流，${clock(a.paused_until, tz, now)} 自动恢复；这期间不请求、不推送，悠悠照常` }
  }
  return { tone: 'ok', text: '已登录', note: null }
}

/** 系统页「平台和账号」一行里左边那几条状态（2026-11-25 按平台分组）。 */
export interface PlatformLine {
  key: string
  name: string
  value: string
  text: string
  state: 'ok' | 'warn' | 'error' | 'idle'
}

export interface PlatformGroup {
  id: 'youpin' | 'buff'
  name: string
  mark: string
  sub: string
  accounts: Account[]
  lines: PlatformLine[]
}

const plat = (a: Account) => a.platform ?? 'youpin'

/** 多号时系统页按平台分组：每个平台一行，左边是平台和它的通道状态，右边是这个平台的号。
 *  悠悠的两条来自 channels（匿名通道、账号通道；多个悠悠号时账号通道一号一条）；BUFF 没有通道记录，
 *  用号的最近同步和「访问受限，暂停到」拼出来。没有号的平台不出现。 */
export function platformGroups(accounts: Account[], channels: Channel[], tz: string, now: Date = new Date()): PlatformGroup[] {
  const out: PlatformGroup[] = []
  for (const id of ['youpin', 'buff'] as const) {
    const accs = accounts.filter((a) => plat(a) === id)
    if (!accs.length) continue
    const live = accs.filter((a) => a.enabled)
    const orders = live.reduce((n, a) => n + a.orders, 0)
    const name = id === 'youpin' ? '悠悠' : 'BUFF'
    let lines: PlatformLine[]
    if (id === 'youpin') {
      lines = channels
        .filter((c) => c.id === 'youpin_anon' || String(c.id).startsWith('youpin_account'))
        .map((c) => {
          const v = channelView(c, tz, now)
          const n = Number(String(c.id).split(':')[1])
          const who = Number.isFinite(n) ? accs.find((a) => a.id === n) : undefined
          const label = c.id === 'youpin_anon' ? '匿名通道' : who ? `账号通道 · ${who.tag ?? who.label}` : '账号通道'
          return { key: String(c.id), name: label, value: v.value, text: v.state, state: (c.state ?? 'idle') as PlatformLine['state'] }
        })
    } else if (!live.length) {
      lines = [{ key: 'buff', name: '账号通道', value: DASH, text: '已停用', state: 'idle' }]
    } else {
      const last = live.map((a) => a.last_sync_at).filter((t): t is string => !!t).sort().at(-1) ?? null
      const paused = live.map((a) => a.paused_until).filter((t): t is string => !!t && new Date(t) > now).sort().at(-1) ?? null
      lines = paused
        ? [{ key: 'buff', name: '访问受限，暂停到', value: clock(paused, tz, now), text: '暂停中', state: 'warn' }]
        : [{ key: 'buff', name: '账号通道', value: clock(last, tz, now), text: last ? '正常' : '尚未运行', state: last ? 'ok' : 'idle' }]
    }
    out.push({ id, name, mark: id === 'youpin' ? '悠' : 'B', sub: `${accs.length} 个号 · ${orders} 张求购`, accounts: accs, lines })
  }
  return out
}
