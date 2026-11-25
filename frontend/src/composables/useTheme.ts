import { createSharedComposable, usePreferredDark } from '@vueuse/core'
import { computed, ref, watchEffect } from 'vue'
import { TOKENS, type Mode } from '@/theme/tokens'
import { applyVars } from '@/theme/vars'

export type ThemePref = 'system' | 'light' | 'dark'
const KEY = 'skinpulse.theme'
const ORDER: ThemePref[] = ['system', 'light', 'dark']

// localStorage 在隐私窗口、清过站点数据时可能抛异常或拿不到值：一律当作「跟随系统」。
function readPref(): ThemePref {
  try {
    const v = localStorage.getItem(KEY)
    return v === 'light' || v === 'dark' || v === 'system' ? v : 'system'
  } catch {
    return 'system'
  }
}

function writePref(v: ThemePref): void {
  try {
    localStorage.setItem(KEY, v)
  } catch {
    /* 存不了就只在本次会话生效 */
  }
}

/** 明暗主题：跟随系统 → 浅 → 深 三态循环，选择记在 localStorage。全局共享一份。 */
export const useTheme = createSharedComposable(() => {
  const pref = ref<ThemePref>(readPref())
  const prefersDark = usePreferredDark()
  const mode = computed<Mode>(() => (pref.value === 'system' ? (prefersDark.value ? 'dark' : 'light') : pref.value))
  const tokens = computed(() => TOKENS[mode.value])
  watchEffect(() => applyVars(tokens.value, mode.value))

  function cycle(): void {
    pref.value = ORDER[(ORDER.indexOf(pref.value) + 1) % ORDER.length]
    writePref(pref.value)
  }

  return { pref, mode, tokens, cycle }
})
