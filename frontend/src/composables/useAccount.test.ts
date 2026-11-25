import { describe, expect, it } from 'vitest'
import type { Account, Meta } from '@/api/types'
import { effectiveAccount, enabledAccounts } from './useAccount'

const acc = (id: number, enabled = true): Account => ({
  id, label: `号${id}`, custom_label: null, nickname: null, enabled, logged_in: true,
  token: { valid: true, expires_at: null, days_left: null }, last_sync_at: null, paused_until: null, orders: 1,
})
const meta = (accounts?: Account[]) => ({ accounts }) as unknown as Meta

describe('account selection', () => {
  it('is always all with fewer than two enabled accounts', () => {
    expect(effectiveAccount(meta([acc(1), acc(2, false)]), 2)).toBe('all')
    expect(effectiveAccount(meta(undefined), 1)).toBe('all')
    expect(effectiveAccount(null, 1)).toBe('all')
  })
  it('falls back to all when the chosen account is gone', () => {
    expect(effectiveAccount(meta([acc(1), acc(2)]), 2)).toBe(2)
    expect(effectiveAccount(meta([acc(1), acc(2)]), 5)).toBe('all')
    expect(enabledAccounts(meta([acc(1), acc(2, false)])).map((a) => a.id)).toEqual([1])
  })
})
