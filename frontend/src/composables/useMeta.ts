import { createSharedComposable } from '@vueuse/core'
import { ref } from 'vue'
import { fetchMeta } from '@/api/client'
import type { Meta } from '@/api/types'
import { usePolling } from './usePolling'

/** 顶栏状态点、token 提醒、时区都来自 /api/meta；全局共享一份，60 秒刷新。 */
export const useMeta = createSharedComposable(() => {
  const meta = ref<Meta | null>(null)
  const error = ref<string | null>(null)
  const { refresh } = usePolling(async () => {
    try {
      meta.value = await fetchMeta()
      error.value = null
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }, 60_000)
  return { meta, error, refresh }
})
