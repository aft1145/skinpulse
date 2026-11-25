import { useDocumentVisibility, useIntervalFn } from '@vueuse/core'
import { onMounted, watch } from 'vue'

/** 每隔 ms 调一次 fn；页面在后台时暂停，切回来立刻补一次。挂载时先跑一次。 */
export function usePolling(fn: () => unknown, ms: number) {
  const visibility = useDocumentVisibility()
  const { pause, resume } = useIntervalFn(() => void fn(), ms, { immediate: false })
  onMounted(() => {
    void fn()
    if (visibility.value === 'visible') resume()
  })
  watch(visibility, (v) => {
    if (v === 'visible') {
      void fn()
      resume()
    } else {
      pause()
    }
  })
  return { refresh: () => fn() }
}
