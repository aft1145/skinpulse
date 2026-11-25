import { usePreferredReducedMotion } from '@vueuse/core'
import { computed, onBeforeUnmount, ref, watch, type Ref } from 'vue'

/** 系统开了「减少动态效果」时为 true：所有 JS 动画直接跳到终点。 */
export function useReducedMotion() {
  const pref = usePreferredReducedMotion()
  return computed(() => pref.value === 'reduce')
}

/** 数字滚动：从旧值平滑滚到新值（spec §3.3，600 ms，ease-out-cubic）。 */
export function useCountUp(target: Ref<number>, duration = 600) {
  const reduced = useReducedMotion()
  const shown = ref(0)
  let raf = 0
  watch(
    target,
    (to) => {
      cancelAnimationFrame(raf)
      if (reduced.value) {
        shown.value = to
        return
      }
      const from = shown.value
      const start = performance.now()
      const step = (t: number) => {
        const k = Math.min(1, (t - start) / duration)
        shown.value = from + (to - from) * (1 - Math.pow(1 - k, 3))
        if (k < 1) raf = requestAnimationFrame(step)
      }
      raf = requestAnimationFrame(step)
    },
    { immediate: true },
  )
  onBeforeUnmount(() => cancelAnimationFrame(raf))
  return shown
}
