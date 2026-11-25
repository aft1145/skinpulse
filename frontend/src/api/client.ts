import type {
  Analysis, BriefData, Fills, ItemDetail, MarketData, Meta, Overview, PausedPayload, PickEstimate, PickFilters, PickOptions,
  PickRunBrief, PickRunDetail, PnlBasis, PnlItems, PnlSells, PnlSummary, SettingsSave, SettingsValues, StatusData, TokenResult,
} from './types'

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    /** 服务端返回的 detail 原样保留：设置表单的 422 是逐项的 [{field, message}] */
    public detail: unknown = null,
  ) {
    super(message)
  }
}

// 写请求一律带 JSON 的 Content-Type：服务端的跨站防护只收 JSON（spec §6）。
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { accept: 'application/json' }
  if (init.body !== undefined) headers['content-type'] = 'application/json'
  let r: Response
  try {
    r = await fetch(path, { ...init, headers })
  } catch {
    throw new ApiError(0, '无法连接看板服务：服务可能未运行')
  }
  if (!r.ok) {
    let text = ''
    let raw: unknown = null
    try {
      const j = await r.json()
      raw = j?.detail ?? null
      if (typeof raw === 'string') text = `：${raw}`
    } catch {
      /* 不是 JSON 就只报状态码 */
    }
    throw new ApiError(r.status, `请求失败（${r.status}）${text}`, raw)
  }
  return (await r.json()) as T
}

export const fetchMeta = () => request<Meta>('/api/meta')
/** account：只要这个号的单（总览顶栏选了某个号，多账号 spec §8）；不给就是全部 */
export const fetchOverview = (account?: number | null) =>
  request<Overview>(account != null ? `/api/overview?account=${account}` : '/api/overview')
/** 不给 accountId 就按号依次同步全部启用的号 */
export const syncNow = (accountId?: number) =>
  request<{ synced: number }>('/api/youpin/sync', { method: 'POST', body: JSON.stringify(accountId != null ? { account_id: accountId } : {}) })
export const fetchItem = (templateId: number) => request<ItemDetail>(`/api/item/${templateId}`)
export const fetchMarket = () => request<MarketData>('/api/market')
export const fetchAnalysis = () => request<Analysis>('/api/analysis')
export const fetchStatus = () => request<StatusData>('/api/status')
export const restartService = () => request<{ restarting: boolean }>('/api/system/restart', { method: 'POST', body: '{}' })
export const fetchBrief = () => request<BriefData>('/api/brief')
export const fetchFills = (days = 30, account?: number | null) =>
  request<Fills>(`/api/fills?days=${days}${account != null ? `&account=${account}` : ''}`)
export const fetchSettings = () => request<SettingsValues>('/api/settings')
export const saveSettings = (patch: Partial<Record<'polling' | 'alerts' | 'brief', Record<string, unknown>>>) =>
  request<SettingsSave>('/api/settings', { method: 'POST', body: JSON.stringify(patch) })
/** token 只放在请求体里，不进网址、不进日志 */
export const updateToken = (token: string, accountId?: number) =>
  request<TokenResult>('/api/youpin/token', {
    method: 'POST', body: JSON.stringify(accountId != null && accountId !== 1 ? { token, account_id: accountId } : { token }),
  })
/** 添加一个悠悠号（多账号 spec §7）：用新的设备身份校验，重号 409 */
export const addAccount = (token: string, label: string) =>
  request<TokenResult & { id: number }>('/api/accounts', { method: 'POST', body: JSON.stringify({ token, label }) })
/** 添加一个 BUFF 号（BUFF spec §8）：session cookie 只放在请求体里 */
export const addBuffAccount = (cookie: string, label: string) =>
  request<{ id: number; nickname: string | null; round_started: boolean }>('/api/accounts', {
    method: 'POST', body: JSON.stringify({ platform: 'buff', cookie, label }),
  })
/** 换一个 BUFF 号的 session */
export const updateBuffCookie = (cookie: string, accountId: number) =>
  request<{ id: number; nickname: string | null; round_started: boolean }>('/api/buff/cookie', {
    method: 'POST', body: JSON.stringify({ cookie, account_id: accountId }),
  })
/** 让 BUFF 的下一轮马上开始（不在接口里同步） */
export const buffSync = () => request<{ round_started: boolean }>('/api/buff/sync', { method: 'POST', body: '{}' })
/** 改简称、停用 / 启用 */
export const patchAccount = (id: number, patch: { label?: string; enabled?: boolean }) =>
  request<{ ok: boolean }>(`/api/accounts/${id}`, { method: 'PATCH', body: JSON.stringify(patch) })
/** PushPlus token：发一条测试消息成功才保存，同样只放在请求体里 */
export const updatePushPlus = (token: string) =>
  request<{ sent: boolean }>('/api/pushplus/token', { method: 'POST', body: JSON.stringify({ token }) })

// 选品（价位 spec §9b）：开始选品是唯一的写请求，只请求 SteamDT、不碰悠悠
export const fetchPickOptions = () => request<PickOptions>('/api/pick/options')
export const estimatePick = (query: string) => request<PickEstimate>(`/api/pick/estimate?${query}`)
export const fetchPickRuns = () => request<{ runs: PickRunBrief[]; busy: boolean }>('/api/pick/runs')
export const fetchPickRun = (id: number) => request<PickRunDetail>(`/api/pick/runs/${id}`)
export const startPick = (f: PickFilters) => request<{ id: number }>('/api/pick/runs', { method: 'POST', body: JSON.stringify(f) })

// 盈亏（盈亏 spec §6）：只读库。account 不给就是全部号相加
const acc = (account?: number | null) => (account != null ? `account=${account}` : '')
export const fetchPnl = (account?: number | null) => request<PnlSummary>(`/api/pnl?${acc(account)}`)
export const fetchPnlItems = (p: { account?: number | null; sort: 'worst' | 'best'; basis: PnlBasis; q?: string;
  category?: string | null; offset?: number; limit?: number }) => {
  const q = new URLSearchParams({ sort: p.sort, basis: p.basis, offset: String(p.offset ?? 0), limit: String(p.limit ?? 50) })
  if (p.account != null) q.set('account', String(p.account))
  if (p.q) q.set('q', p.q)
  if (p.category) q.set('category', p.category)
  return request<PnlItems>(`/api/pnl/items?${q}`)
}
export const fetchPnlSells = (account?: number | null, limit = 12) =>
  request<PnlSells>(`/api/pnl/sells?limit=${limit}${account != null ? `&account=${account}` : ''}`)

// 充值前检查暂停单（暂停单 spec §5.3）：「检查一次」交给服务在后台跑（约 8 分钟），页面只拉进度和结果
export const fetchPaused = () => request<PausedPayload>('/api/paused')
export const startPausedCheck = () => request<{ run_id: number }>('/api/paused/check', { method: 'POST', body: '{}' })
