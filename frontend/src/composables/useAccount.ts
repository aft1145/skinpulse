import { createSharedComposable } from '@vueuse/core'
import { computed, ref, watch } from 'vue'
import type { Account, Meta } from '@/api/types'
import { useMeta } from './useMeta'

/** 总览顶栏选的号：'all' 或号的 id（多账号 spec §8）。记在浏览器里，读写都包 try/catch。 */
export type AccountSel = 'all' | number
const KEY = 'skinpulse.account'

function read(): AccountSel {
  try {
    const v = localStorage.getItem(KEY)
    const n = v == null ? NaN : Number(v)
    return Number.isInteger(n) && n > 0 ? n : 'all'
  } catch {
    return 'all'
  }
}

function write(v: AccountSel) {
  try {
    localStorage.setItem(KEY, String(v))
  } catch {
    /* 隐私模式等存不了：只是下次不记得 */
  }
}

/** 启用的号；少于 2 个时不显示任何账号相关的东西 */
export function enabledAccounts(meta: Meta | null | undefined): Account[] {
  return (meta?.accounts ?? []).filter((a) => a.enabled)
}

/** 实际生效的选择：只有一个号、或选的号已经停用时都当「全部」 */
export function effectiveAccount(meta: Meta | null | undefined, sel: AccountSel): AccountSel {
  const accs = enabledAccounts(meta)
  if (accs.length < 2 || sel === 'all') return 'all'
  return accs.some((a) => a.id === sel) ? sel : 'all'
}

export const useAccount = createSharedComposable(() => {
  const { meta } = useMeta()
  const selected = ref<AccountSel>(read())
  watch(selected, write)
  const effective = computed(() => effectiveAccount(meta.value, selected.value))
  const accounts = computed(() => enabledAccounts(meta.value))
  return { selected, effective, accounts }
})
