<script setup lang="ts">
// 「重启服务」（intent 2026-11-23-restart-button）：先确认，再请服务自己退出，由 LaunchAgent 拉起。
// 之后每 2 秒探一次 /api/status，启动时间变了就刷新页面；60 秒还没回来就提示去终端看。
import { onBeforeUnmount, ref } from 'vue'
import { RotateCw } from '@lucide/vue'
import { ApiError, fetchStatus, restartService } from '@/api/client'

const props = defineProps<{ startedAt: string | null; primary?: boolean; statusCmd: string }>()

type Phase = 'idle' | 'confirm' | 'restarting' | 'timeout' | 'error'
const phase = ref<Phase>('idle')
const message = ref('')
const POLL_MS = 2000
const GIVE_UP_MS = 60_000
let timer: ReturnType<typeof setTimeout> | undefined

onBeforeUnmount(() => clearTimeout(timer))

async function go() {
  phase.value = 'restarting'
  try {
    await restartService()
  } catch (e) {
    phase.value = 'error'
    message.value = e instanceof ApiError ? e.message : '重启失败'
    return
  }
  const deadline = Date.now() + GIVE_UP_MS
  const poll = async () => {
    try {
      const s = await fetchStatus()
      if (s.runtime.started_at && s.runtime.started_at !== props.startedAt) {
        window.location.reload()
        return
      }
    } catch {
      // 服务正在重启，连不上是正常的
    }
    if (Date.now() > deadline) {
      phase.value = 'timeout'
      return
    }
    timer = setTimeout(poll, POLL_MS)
  }
  timer = setTimeout(poll, POLL_MS)
}
</script>

<template>
  <div class="restart">
    <template v-if="phase === 'idle' || phase === 'error'">
      <button type="button" class="btn press" :class="{ primary }" @click="phase = 'confirm'">
        <RotateCw :size="15" aria-hidden="true" />重启服务
      </button>
      <span v-if="phase === 'error'" class="note warn" role="alert">{{ message }}</span>
    </template>
    <template v-else-if="phase === 'confirm'">
      <span class="note">重启要 10～30 秒，这期间看板打不开、不监控。</span>
      <button type="button" class="btn primary press" @click="go">确定重启</button>
      <button type="button" class="btn press" @click="phase = 'idle'">取消</button>
    </template>
    <span v-else-if="phase === 'restarting'" class="note" role="status"><RotateCw :size="15" class="spin" aria-hidden="true" />正在重启…</span>
    <span v-else class="note warn" role="alert">60 秒内未恢复：请在终端运行 {{ statusCmd }} 查看</span>
  </div>
</template>

<style scoped>
.restart { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.btn {
  height: 38px; padding: 0 16px; border-radius: 10px; border: 1px solid var(--sp-line-strong); background: var(--sp-surface);
  color: var(--sp-text); font: inherit; font-weight: 500; display: inline-flex; align-items: center; gap: 8px; cursor: pointer;
}
.btn:hover { background: var(--sp-surface2); }
.btn.primary { border: none; background: var(--sp-accent); color: var(--sp-on-accent); font-weight: 600; }
.btn.primary:hover { filter: brightness(1.05); }
.note { font-size: 13px; color: var(--sp-text2); display: inline-flex; align-items: center; gap: 6px; }
.note.warn { color: var(--sp-warn); }
.spin { animation: spin 0.9s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
