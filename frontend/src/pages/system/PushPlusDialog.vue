<script setup lang="ts">
// 「录入 PushPlus token」对话框（首次使用视觉稿：粘贴 → 发测试消息 → 成功 / 不对 / 暂时连不上）。
// 和 TokenDialog 一样：输入框不回显、不自动填充，提交后立即清空。成功只代表 PushPlus 收下了，
// 送没送到微信要用户自己看（CLAUDE.md 坑 1）。
import { NModal } from 'naive-ui'
import { computed, nextTick, ref, watch } from 'vue'
import { ApiError, updatePushPlus } from '@/api/client'

const show = defineModel<boolean>('show', { required: true })
const emit = defineEmits<{ done: [] }>()

type Phase = 'paste' | 'sending' | 'ok' | 'format' | 'rejected' | 'unverified' | 'throttled'
const phase = ref<Phase>('paste')
const token = ref('')
const message = ref('')
const input = ref<HTMLInputElement | null>(null)

watch(show, async (v) => {
  if (!v) return
  phase.value = 'paste'
  token.value = ''
  message.value = ''
  await nextTick()
  input.value?.focus()
})

const canSubmit = computed(() => phase.value !== 'sending' && !!token.value.trim())

async function submit() {
  const t = token.value
  if (!t.trim() || phase.value === 'sending') return
  token.value = ''
  phase.value = 'sending'
  try {
    await updatePushPlus(t)
    phase.value = 'ok'
    emit('done')
  } catch (e) {
    const err = e instanceof ApiError ? e : null
    message.value = typeof err?.detail === 'string' ? err.detail : (err?.message ?? String(e))
    // 400 = 格式不对（没发请求），422 = PushPlus 不收，429 = 限流，其余 = 暂时连不上
    const st = err?.status
    phase.value = st === 400 ? 'format' : st === 422 ? 'rejected' : st === 429 ? 'throttled' : 'unverified'
  }
}
</script>

<template>
  <NModal v-model:show="show" :auto-focus="false" :mask-closable="phase !== 'sending'">
    <div class="dialog card" role="dialog" aria-labelledby="pp-title">
      <h2 id="pp-title">录入 PushPlus token</h2>
      <p v-if="phase !== 'ok'" class="small text2 lead">
        在 <a href="https://www.pushplus.plus" target="_blank" rel="noopener noreferrer">pushplus.plus</a>
        用微信扫码登录，复制 token。保存前会先往你的微信发一条测试消息。
      </p>

      <form v-if="phase !== 'ok'" class="form" autocomplete="off" @submit.prevent="submit">
        <label class="field">
          <span class="small text2">token</span>
          <input
            ref="input"
            v-model="token"
            type="password"
            autocomplete="off"
            spellcheck="false"
            :class="{ bad: phase === 'rejected' || phase === 'format' }"
            :disabled="phase === 'sending'"
            placeholder="粘贴到这里"
          />
        </label>
      </form>

      <div v-if="phase === 'sending'" class="msg" role="status">
        <span class="icon neutral">…</span>
        <span><b>正在发测试消息…</b><span class="small text2">发成功才保存；原来的不受影响。</span></span>
      </div>
      <div v-else-if="phase === 'ok'" class="msg ok" role="status">
        <span class="icon">✓</span>
        <span>
          <b>已保存，测试消息已发送</b>
          <span class="small text2">请在微信中查看「SkinPulse 通道测试」，收到即表示通道正常。立即生效，无需重启。</span>
        </span>
      </div>
      <div v-else-if="phase === 'format' || phase === 'rejected'" class="msg bad" role="alert">
        <span class="icon">!</span>
        <span><b>{{ phase === 'format' ? '格式不正确' : '未保存' }}</b><span class="small text2">{{ message }}</span></span>
      </div>
      <div v-else-if="phase === 'unverified' || phase === 'throttled'" class="msg warn" role="alert">
        <span class="icon">!</span>
        <span>
          <b>{{ phase === 'throttled' ? '操作太频繁' : '暂时连不上 PushPlus' }}</b>
          <span class="small text2">{{ message || '未保存，请稍后再试。' }}</span>
        </span>
      </div>

      <div class="actions">
        <button v-if="phase !== 'ok'" type="button" class="btn press" :disabled="phase === 'sending'" @click="show = false">取消</button>
        <button v-if="phase === 'ok'" type="button" class="btn primary press" @click="show = false">完成</button>
        <button v-else type="button" class="btn primary press" :disabled="!canSubmit" @click="submit">
          {{ phase === 'sending' ? '发送中' : '发测试消息并保存' }}
        </button>
      </div>
    </div>
  </NModal>
</template>

<style scoped>
.dialog { width: 460px; padding: 24px 24px 20px; display: flex; flex-direction: column; gap: 14px; box-shadow: var(--sp-pop-shadow); }
h2 { margin: 0; font-size: 17px; font-weight: 700; }
.lead { margin: 0; line-height: 1.7; }
.lead a { color: var(--sp-accent); }
.form { display: flex; flex-direction: column; gap: 8px; }
.field { display: flex; flex-direction: column; gap: 6px; }
input {
  height: 40px; padding: 0 12px; border-radius: 10px; border: 1.5px solid var(--sp-line-strong); background: var(--sp-surface);
  color: var(--sp-text); font: 14px 'JetBrains Mono', monospace; letter-spacing: 1px;
}
input:focus { outline: none; border-color: var(--sp-accent); }
input.bad { border-color: var(--sp-up); }
.small { font-size: 12.5px; }
.text2 { color: var(--sp-text2); }
.msg { display: flex; gap: 10px; align-items: flex-start; padding: 12px 14px; border-radius: 10px; background: var(--sp-surface2); }
.msg > span:last-child { display: flex; flex-direction: column; gap: 3px; line-height: 1.55; }
.msg b { font-size: 13.5px; }
.icon { width: 20px; height: 20px; border-radius: 10px; flex-shrink: 0; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; color: var(--sp-surface); background: var(--sp-text3); }
.msg.ok { background: var(--sp-ok-soft); } .msg.ok b { color: var(--sp-ok); } .msg.ok .icon { background: var(--sp-ok); }
.msg.bad { background: color-mix(in srgb, var(--sp-up) 12%, transparent); } .msg.bad b { color: var(--sp-up); } .msg.bad .icon { background: var(--sp-up); }
.msg.warn { background: var(--sp-warn-soft); } .msg.warn b { color: var(--sp-warn); } .msg.warn .icon { background: var(--sp-warn); }
.actions { display: flex; justify-content: flex-end; gap: 10px; }
.btn { height: 36px; padding: 0 16px; border-radius: 10px; border: 1px solid var(--sp-line-strong); background: var(--sp-surface); color: var(--sp-text); font: inherit; font-size: 13px; cursor: pointer; }
.btn.primary { border: none; background: var(--sp-accent); color: var(--sp-on-accent); font-weight: 600; }
.btn:disabled { opacity: 0.5; cursor: default; }
</style>
