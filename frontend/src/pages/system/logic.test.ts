import { describe, expect, it } from 'vitest'
import type { Channel, SettingsValues, StatusData } from '@/api/types'
import { changes, patchOf, toDraft, validate } from './fields'
import { accountState, agreement, bytes, platformGroups, restartable, serviceCmd, channelView, headline, tokenLeft, tokenRing, uptime } from './logic'

const tz = 'Asia/Shanghai'
const now = new Date('2026-09-27T04:00:00Z') // 本地 12:00

function status(over: Partial<StatusData['headline']> = {}, lastRound: StatusData['service']['last_round'] = null): StatusData {
  return {
    headline: { issue: null, paused_until: null, pushplus_error_at: null, ...over },
    service: { healthy: true, issues: [], last_round_at: '2026-09-27T03:55:00Z', last_round: lastRound, paused_until: null },
  } as unknown as StatusData
}

describe('headline', () => {
  it('says all is well with the last round summary', () => {
    const h = headline(status({}, { at: '2026-09-27T03:55:00Z', checked: 429, alerts_sent: 0, errors: 0, paused: false, gap_gated: 0, notify_failed: 0 }), tz, now)
    expect(h).toEqual({ ok: true, text: '一切正常', sub: '最近一轮 11:55 · 检查 429 张 · 0 错误 · 未暂停' })
  })
  it('names the most serious issue', () => {
    expect(headline(status({ issue: 'paused', paused_until: '2026-09-27T06:30:00Z' }), tz, now).text).toBe('悠悠风控暂停到 14:30')
    expect(headline(status({ issue: 'token_invalid' }), tz, now).text).toBe('token 已失效')
    // 从来没登录过（首次使用 spec §2.3）：不说「失效」，副标题说下一步
    expect(headline(status({ issue: 'not_logged_in' }), tz, now)).toEqual({ ok: false, text: '未登录悠悠', sub: '录入 token 后开始监控' })
    // 代码改了、服务还没重启：标题说清楚，副标题给出重启命令（2026-10-05：没重启，新功能白等了三天）
    const outdated = headline(status({ issue: 'code_outdated' }), tz, now)
    expect(outdated.text).toBe('代码已更新，服务尚未重启')
    expect(outdated.sub).toBe('新代码重启后才生效：scripts/macos/launchagent.sh restart')
    // 由 LaunchAgent 拉起的服务能在看板上重启（intent 2026-11-23）；依赖变了要先 uv sync
    const rt = (r: object) => ({ ...status({ issue: 'code_outdated' }), runtime: r }) as unknown as StatusData
    expect(headline(rt({ can_restart: true, deps_outdated: false }), tz, now).sub).toBe('新代码重启后才生效')
    expect(headline(rt({ can_restart: true, deps_outdated: true }), tz, now).sub).toBe(
      '依赖有变化：先在终端运行 uv sync，再 scripts/macos/launchagent.sh restart',
    )
    // Windows 守护进程（ADR-0021）：命令换成 skinpulse service
    expect(headline(rt({ can_restart: true, deps_outdated: true, supervisor: 'windows' }), tz, now).sub).toBe(
      '依赖有变化：先在终端运行 uv sync，再 uv run python -m skinpulse service restart',
    )
    expect(headline(status({ issue: 'pushplus_failing', pushplus_error_at: '2026-09-27T03:00:00Z' }), tz, now).text).toBe(
      'PushPlus 最近一次推送失败（11:00）',
    )
  })
})

describe('channelView', () => {
  const ch = (over: Partial<Channel>): Channel => ({
    id: 'pushplus', name: 'PushPlus 微信', configured: true, state: 'ok', last_ok_at: '2026-09-27T03:00:00Z',
    last_error_code: null, last_error_at: null, paused_until: null, ...over,
  })
  it('shows state in words and the relevant time', () => {
    expect(channelView(ch({}), tz, now)).toMatchObject({ state: '正常', key: '最近成功', value: '11:00' })
    expect(channelView(ch({ state: 'error', last_error_at: '2026-09-27T03:30:00Z', last_error_code: 'publish_failed' }), tz, now))
      .toMatchObject({ state: '出错', key: '最近出错', value: '11:30 · publish_failed' })
    expect(channelView(ch({ id: 'ntfy', configured: false, state: 'idle', last_ok_at: null }), tz, now))
      .toMatchObject({ state: '未配置', value: '—', note: '可选的备用通道，未录入 topic。' })
  })
})

describe('small formatters', () => {
  it('uptime, bytes, agreement, token ring', () => {
    expect(uptime('2026-09-24T21:00:00Z', now)).toBe('2 天 7 小时')
    expect(uptime('2026-09-27T03:57:00Z', now)).toBe('3 分钟')
    expect(uptime(null, now)).toBe('—')
    expect(bytes(84 * 1024 * 1024)).toBe('84 MB')
    expect(agreement({ checked_24h: 1140, agreed_24h: 1118, inconclusive_24h: 0, last_disagreement: null })).toEqual({ pct: 98.1, disagreed: 22 })
    expect(agreement({ checked_24h: 3, agreed_24h: 0, inconclusive_24h: 3, last_disagreement: null }).pct).toBeNull()
    expect(tokenRing(7)).toBe(0.7)
    expect(tokenRing(null)).toBe(0)
  })
})

describe('settings form', () => {
  const values = {
    polling: { round_interval_min: 5, request_interval_s: 3, hourly_request_cap: 300, fast_lane_rank_threshold: 5, night_start_hour: 0, night_end_hour: 8, night_multiplier: 2, verify_enabled: true, jitter_pct: 0.3, heartbeat_interval_min: 30, account_gap_s: 10, verify_sample_size: 5, sell_sample_size: 5, visible_limit: 20, deep_page_size: 200 },
    alerts: { enter_rank: 1, exit_rank: 4, confirm_rounds: 2, max_gap_ratio: 3, max_market_ratio: 2, min_market_gap: 20, hourly_cap: 30, group_wait_s: 60, confirm_with_live_queue: true, live_confirm_max: 10 },
    brief: { enabled: true, send_at: '00:00', snapshot_at: '23:50', top_rank: 2, quiet_days: 7 },
    buff: { round_interval_min: 15, request_gap_s: 6, queue_per_round: 10, queue_max_pages: 3, pause_hours: 2 },
  } as unknown as SettingsValues
  it('lists what changed and submits only that', () => {
    const d = toDraft(values)
    d['polling.round_interval_min'] = '6'
    d['polling.request_interval_s'] = '3.0'
    d['brief.send_at'] = '8:00'
    d['brief.enabled'] = false
    const c = changes(values, d)
    expect(c.map((x) => x.text)).toEqual(['每轮间隔 5 → 6 分钟', '每日简报 开 → 关', '发送时间 00:00 → 08:00'])
    expect(patchOf(d, c)).toEqual({ polling: { round_interval_min: 6 }, brief: { enabled: false, send_at: '08:00' } })
  })
  it('validates like the backend', () => {
    const d = toDraft(values)
    d['polling.round_interval_min'] = '0'
    d['polling.jitter_pct'] = 'abc'
    d['alerts.exit_rank'] = '1'
    d['brief.send_at'] = '25:00'
    const errs = Object.fromEntries(validate(d).map((e) => [e.field, e.message]))
    expect(errs['polling.round_interval_min']).toBe('不能小于 1')
    expect(errs['polling.jitter_pct']).toBe('要填数字')
    expect(errs['alerts.exit_rank']).toBe('要大于「升到第几名推送」（1）')
    expect(errs['brief.send_at']).toBe('格式是 HH:MM，比如 08:00')
    const d2 = toDraft(values)
    d2['alerts.enter_rank'] = '3'
    d2['alerts.exit_rank'] = '3'
    expect(validate(d2)).toEqual([{ field: 'alerts.exit_rank', message: '要大于「升到第几名推送」（3）' }])
  })
})

describe('tokenLeft', () => {
  it('says days and hours, so 6 days 23 hours does not read as a lost day', () => {
    const now = new Date('2026-09-27T08:10:00Z') // 本地 16:10
    expect(tokenLeft('2026-10-04T08:01:00Z', now)).toBe('6 天 23 小时')
    expect(tokenLeft('2026-09-28T02:10:00Z', now)).toBe('18 小时')
    expect(tokenLeft('2026-09-27T08:40:00Z', now)).toBe('不到 1 小时')
    expect(tokenLeft('2026-09-27T08:00:00Z', now)).toBe('已过期')
    expect(tokenLeft(null, now)).toBeNull()
  })
})

describe('restartable', () => {
  const d = (r: object) => ({ runtime: r }) as unknown as StatusData
  it('only for a LaunchAgent service whose dependencies are installed', () => {
    expect(restartable(d({ can_restart: true, deps_outdated: false }))).toBe(true)
    expect(restartable(d({ can_restart: false, deps_outdated: false }))).toBe(false)
    expect(restartable(d({ can_restart: true, deps_outdated: true }))).toBe(false)
    expect(restartable(d({}))).toBe(false)               // 老后端没有这两个字段
  })
})

describe('serviceCmd', () => {
  it('picks the command by supervisor, then by platform', () => {
    expect(serviceCmd({ supervisor: 'launchd', platform: 'macOS 26.1' }, 'status')).toBe('scripts/macos/launchagent.sh status')
    expect(serviceCmd({ supervisor: 'windows', platform: 'Windows 11' }, 'status')).toBe('uv run python -m skinpulse service status')
    expect(serviceCmd({ supervisor: null, platform: 'Windows 10' }, 'restart')).toBe('uv run python -m skinpulse service restart')
    expect(serviceCmd({ supervisor: null, platform: 'macOS 26.1' }, 'restart')).toBe('scripts/macos/launchagent.sh restart')
    expect(serviceCmd(undefined, 'restart')).toBe('scripts/macos/launchagent.sh restart')
  })
})

describe('accountState', () => {
  const now = new Date('2026-11-24T08:00:00Z')
  const base = {
    id: 2, label: '小号', custom_label: '小号', nickname: 'n', enabled: true, logged_in: true,
    token: { valid: true, expires_at: null, days_left: 3 }, last_sync_at: '2026-11-24T06:05:00Z', paused_until: null, orders: 14,
  }
  it('reads each state', () => {
    expect(accountState(base, 'Asia/Shanghai', now)).toEqual({ tone: 'ok', text: '已登录', note: null })
    const dead = accountState({ ...base, token: { ...base.token, valid: false } }, 'Asia/Shanghai', now)
    expect(dead.tone).toBe('bad')
    expect(dead.note).toContain('14:05')
    expect(accountState({ ...base, enabled: false }, 'Asia/Shanghai', now).tone).toBe('off')
    expect(accountState({ ...base, paused_until: '2026-11-24T09:00:00Z' }, 'Asia/Shanghai', now).text).toBe('暂停中')
    expect(accountState({ ...base, logged_in: false }, 'Asia/Shanghai', now).text).toBe('未登录')
  })
  it('reads BUFF states (BUFF spec §8)', () => {
    const b = { ...base, platform: 'buff' as const, label: 'BUFF', token: { valid: true, expires_at: null, days_left: null } }
    expect(accountState(b, 'Asia/Shanghai', now)).toEqual({ tone: 'ok', text: '已登录', note: null })
    const paused = accountState({ ...b, paused_until: '2026-11-24T09:00:00Z' }, 'Asia/Shanghai', now)
    expect(paused.text).toBe('访问受限，暂停中')
    expect(paused.note).toContain('17:00')
    expect(accountState({ ...b, token: { ...b.token, valid: false } }, 'Asia/Shanghai', now).note).toContain('悠悠照常')
    expect(accountState({ ...b, enabled: false }, 'Asia/Shanghai', now).note).toContain('session')
  })
})

describe('headline with several accounts', () => {
  it('names the account whose login is gone', () => {
    const acc = (id: number, label: string, valid: boolean) => ({
      id, label, custom_label: label, nickname: null, enabled: true, logged_in: true,
      token: { valid, expires_at: null, days_left: null }, last_sync_at: null, paused_until: null, orders: 1,
    })
    const d = {
      headline: { issue: 'token_invalid', paused_until: null, pushplus_error_at: null },
      service: { healthy: false, issues: ['token_invalid'], last_round_at: null, last_round: null, paused_until: null },
      accounts: [acc(1, '主号', true), acc(2, '小号', false)],
      runtime: null,
    } as unknown as StatusData
    expect(headline(d, 'Asia/Shanghai').text).toBe('小号登录已失效')
    expect(headline({ ...d, accounts: [acc(1, '主号', false)] } as StatusData, 'Asia/Shanghai').text).toBe('token 已失效')
  })
})


describe('platformGroups（系统页按平台分组，2026-11-25）', () => {
  const now = new Date('2026-11-24T18:00:00Z')
  const tz = 'Asia/Shanghai'
  const acc = (o: Record<string, unknown>) => ({
    id: 1, platform: 'youpin' as const, label: '川扑通', custom_label: null, nickname: '川扑通', enabled: true, logged_in: true,
    token: { valid: true, expires_at: null, days_left: 1 }, last_sync_at: '2026-11-24T17:46:00Z', paused_until: null, orders: 155, ...o,
  })
  const ch = (id: string, state: Channel['state'] = 'ok') => ({
    id, name: id, configured: true, state, last_ok_at: '2026-11-24T17:46:00Z', last_error_code: null, last_error_at: null, paused_until: null,
  }) as unknown as Channel
  const channels = [ch('youpin_anon'), ch('youpin_account'), ch('pushplus'), ch('ntfy', 'idle')]

  it('one row per platform, YouPin lines from its channels, push channels left out', () => {
    const g = platformGroups([acc({}), acc({ id: 2, platform: 'buff', label: 'BUFF', orders: 8 })], channels, tz, now)
    expect(g.map((x) => [x.id, x.name, x.mark, x.sub])).toEqual([['youpin', '悠悠', '悠', '1 个号 · 155 张求购'], ['buff', 'BUFF', 'B', '1 个号 · 8 张求购']])
    expect(g[0].lines.map((l) => [l.name, l.value, l.text])).toEqual([['匿名通道', '01:46', '正常'], ['账号通道', '01:46', '正常']])
    expect(g[1].lines).toEqual([{ key: 'buff', name: '账号通道', value: '01:46', text: '正常', state: 'ok' }])
  })
  it('several YouPin accounts: one account-channel line each, named by tag', () => {
    const accs = [acc({ tag: '悠悠 · 主号' }), acc({ id: 3, label: '小号', tag: '悠悠 · 小号', orders: 8 })]
    const g = platformGroups(accs, [ch('youpin_anon'), ch('youpin_account:1'), ch('youpin_account:3', 'error')], tz, now)
    expect(g).toHaveLength(1)
    expect(g[0].sub).toBe('2 个号 · 163 张求购')
    expect(g[0].lines.map((l) => [l.name, l.state])).toEqual([['匿名通道', 'ok'], ['账号通道 · 悠悠 · 主号', 'ok'], ['账号通道 · 悠悠 · 小号', 'error']])
  })
  it('BUFF paused, never run, disabled', () => {
    const b = (o: Record<string, unknown>) => acc({ id: 2, platform: 'buff', label: 'BUFF', orders: 8, ...o })
    const paused = platformGroups([acc({}), b({ paused_until: '2026-11-24T19:00:00Z' })], channels, tz, now)[1].lines[0]
    expect([paused.name, paused.value, paused.text, paused.state]).toEqual(['访问受限，暂停到', '03:00', '暂停中', 'warn'])
    expect(platformGroups([acc({}), b({ last_sync_at: null })], channels, tz, now)[1].lines[0].text).toBe('尚未运行')
    const off = platformGroups([acc({}), b({ enabled: false })], channels, tz, now)[1]
    expect([off.sub, off.lines[0].text]).toEqual(['1 个号 · 0 张求购', '已停用'])
  })
})
