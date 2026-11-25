<script setup lang="ts">
// 「更新 token」对话框（视觉稿三期：粘贴 → 校验中 → 成功 / 无效 / 暂时无法验证）。
// BUFF（BUFF spec §8）：添加时顶部能切到 BUFF，凭据换成 session cookie；更新 BUFF 号时 platform=buff。
// token 只在这个组件里短暂停留：输入框不回显（type=password）、不自动填充，提交后立即清空。
import { NModal } from 'naive-ui'
import { computed, nextTick, ref, watch } from 'vue'
import { ApiError, addAccount, addBuffAccount, updateBuffCookie, updateToken } from '@/api/client'
import type { TokenResult } from '@/api/types'
import { clock } from '@/format'

const show = defineModel<boolean>('show', { required: true })
// mode=add：添加账号（多账号 spec §8），多一个简称输入框；accountId：更新哪个号的 token（默认号 1）
const props = withDefaults(defineProps<{
  tz: string; mode?: 'update' | 'add'; accountId?: number; accountLabel?: string; platform?: 'youpin' | 'buff'
}>(), {
  mode: 'update', accountId: undefined, accountLabel: undefined, platform: 'youpin',
})
const label = ref('')
// 添加时选哪个平台；更新时跟着那个号走
const plat = ref<'youpin' | 'buff'>('youpin')
const buff = computed(() => (props.mode === 'add' ? plat.value : props.platform) === 'buff')
const emit = defineEmits<{ done: [TokenResult] }>()

type Phase = 'paste' | 'checking' | 'ok' | 'format' | 'invalid' | 'unverified' | 'throttled' | 'duplicate'
const phase = ref<Phase>('paste')
const token = ref('')
const message = ref('')
const result = ref<TokenResult | null>(null)
const input = ref<HTMLInputElement | null>(null)

// 暂时无法验证时留着给「重试」用；不是响应式数据，不进界面。成功、无效、开关对话框都清空。
let pending = ''
const pendingRetry = ref(false)

watch(show, async (v) => {
  pending = ''
  pendingRetry.value = false
  if (!v) return
  phase.value = 'paste'
  token.value = ''
  label.value = ''
  plat.value = props.mode === 'add' ? props.platform : 'youpin'   // 从「添加 BUFF 账号」打开时直接在 BUFF
  message.value = ''
  result.value = null
  await nextTick()
  input.value?.focus()
})

const canSubmit = computed(() => phase.value !== 'checking' && (!!token.value.trim() || !!pendingRetry.value))

async function submit() {
  const t = token.value.trim() ? token.value : pending
  if (!t || phase.value === 'checking') return
  token.value = '' // 立刻清掉，不在输入框里多留
  pending = t
  pendingRetry.value = false
  phase.value = 'checking'
  try {
    if (buff.value) {
      const r = props.mode === 'add' ? await addBuffAccount(t, label.value.trim())
        : await updateBuffCookie(t, props.accountId ?? 0)
      result.value = { nickname: r.nickname ?? '', user_id: 0, expires_at: null, round_started: r.round_started } as TokenResult
    } else {
      result.value = props.mode === 'add' ? await addAccount(t, label.value.trim()) : await updateToken(t, props.accountId)
    }
    pending = ''
    phase.value = 'ok'
    emit('done', result.value)
  } catch (e) {
    const err = e instanceof ApiError ? e : null
    message.value = typeof err?.detail === 'string' ? err.detail : (err?.message ?? String(e))
    // 400 = 格式都不对（没去请求悠悠），422 = 悠悠说无效，429 = 限流，409 = 这个号已经在了，其余 = 暂时无法验证
    const st = err?.status
    phase.value = st === 400 ? 'format' : st === 422 ? 'invalid' : st === 429 ? 'throttled' : st === 409 ? 'duplicate' : 'unverified'
    if (phase.value === 'invalid' || phase.value === 'format' || phase.value === 'duplicate') pending = ''
    pendingRetry.value = !!pending
  }
}

const okText = computed(() => {
  const r = result.value
  if (!r) return ''
  if (buff.value) return `已存到本机。${r.round_started ? 'BUFF 的这一轮马上开始，' : ''}之后每 15 分钟一轮。`
  const exp = r.expires_at ? `到期时间 ${clock(r.expires_at, props.tz)}。` : ''
  const sync = r.round_started ? '这一轮监控已经开始，会先同步求购单。' : '下一轮监控会同步求购单。'
  return `${exp}已存到本机，马上生效。${sync}`
})
const showInput = computed(() => phase.value !== 'ok')
const title = computed(() => {
  if (props.mode === 'add') return '添加账号'
  if (buff.value) return props.accountLabel ? `更新 cookie · ${props.accountLabel}` : '更新 BUFF cookie'
  return props.accountLabel ? `更新 token · ${props.accountLabel}` : '更新悠悠 token'
})
</script>

<template>
  <NModal v-model:show="show" :auto-focus="false" :mask-closable="phase !== 'checking'">
    <div class="dialog card" role="dialog" aria-labelledby="token-title">
      <div class="head">
        <h2 id="token-title">{{ title }}</h2>
        <div v-if="mode === 'add' && phase !== 'ok'" class="seg" role="group" aria-label="平台">
          <button type="button" :aria-pressed="!buff" :class="{ on: !buff }" :disabled="phase === 'checking'" @click="plat = 'youpin'">悠悠</button>
          <button type="button" :aria-pressed="buff" :class="{ on: buff }" :disabled="phase === 'checking'" @click="plat = 'buff'">BUFF</button>
        </div>
      </div>

      <form v-if="showInput" class="form" autocomplete="off" @submit.prevent="submit">
        <label v-if="mode === 'add'" class="field">
          <span class="small text2">简称 <span class="muted">（推送和看板里用，最多 8 个字）</span></span>
          <input v-model="label" class="plain" type="text" maxlength="8" :placeholder="buff ? 'BUFF' : '比如 小号'" :disabled="phase === 'checking'" />
        </label>
        <label class="field">
          <span class="small text2">{{ buff ? 'session' : 'authorization 请求头' }}</span>
          <input
            ref="input"
            v-model="token"
            type="password"
            autocomplete="off"
            spellcheck="false"
            :class="{ bad: phase === 'invalid' || phase === 'format' }"
            :disabled="phase === 'checking'"
            placeholder="粘贴到这里"
            aria-describedby="token-help"
          />
        </label>
        <ol v-if="phase === 'paste' && buff" id="token-help" class="tiny muted help steps">
          <li>在 Chrome 里登录 BUFF，勾上「10 天内免登录」</li>
          <li>开发者工具 → Application → Cookies → buff.163.com</li>
          <li>复制 session 那一行的 Value。先校验再保存，不会显示或回传</li>
        </ol>
        <p v-else-if="phase === 'paste'" id="token-help" class="tiny muted help">
          在浏览器登录悠悠 → 开发者工具 → 网络 → 任意一个发往 pc-api.youpin898.com 的请求 → 复制请求头里的
          authorization。带不带 Bearer 都行。先校验再保存，不会显示或回传。
        </p>
        <p v-if="buff && phase === 'paste'" class="tiny tip">
          只读：只看你的求购、队列和订单，不下单、不改价。每 15 分钟一轮，碰到验证或限流就自动停 2 小时。
        </p>
        <p v-else-if="mode === 'add' && phase === 'paste'" class="tiny tip">
          请用另一个浏览器或无痕窗口登录这个账号：在同一浏览器中切换账号会退出原账号，其 token 可能随之失效。
        </p>
      </form>

      <div v-if="phase === 'checking'" class="msg" role="status">
        <span class="icon neutral">…</span>
        <span v-if="buff"><b>正在向 BUFF 校验…</b><span class="small text2">只请求一次「我的求购」；校验通过前不会替换正在用的 session。</span></span>
        <span v-else><b>正在向悠悠校验…</b><span class="small text2">只请求一次账号信息；校验通过前不会替换正在用的 token。</span></span>
      </div>
      <div v-else-if="phase === 'ok'" class="msg ok" role="status">
        <span class="icon">✓</span>
        <span><b>{{ mode === 'add' ? '已添加' : '已更新' }} · {{ result?.nickname }}</b><span class="small text2">{{ okText }}</span></span>
      </div>
      <div v-else-if="phase === 'format'" class="msg bad" role="alert">
        <span class="icon">!</span>
        <span><b>格式不对</b><span class="small text2">{{ message }}</span></span>
      </div>
      <div v-else-if="phase === 'duplicate'" class="msg bad" role="alert">
        <span class="icon">!</span>
        <span><b>未添加</b><span class="small text2">{{ message }}</span></span>
      </div>
      <div v-else-if="phase === 'invalid'" class="msg bad" role="alert">
        <span class="icon">!</span>
        <span><b>{{ buff ? 'session 无效或已过期' : 'token 无效或已过期' }}</b><span class="small text2">未保存，原有{{ buff ? ' session' : ' token' }}不受影响。请重新复制后再试。</span></span>
      </div>
      <div v-else-if="phase === 'unverified' || phase === 'throttled'" class="msg warn" role="alert">
        <span class="icon">!</span>
        <span>
          <b>{{ phase === 'throttled' ? '操作太频繁' : buff ? 'BUFF 暂无响应' : '悠悠暂无响应' }}</b>
          <span class="small text2">{{ message || '未保存，原有 token 不受影响。请稍后再试。' }}</span>
        </span>
      </div>

      <div class="actions">
        <button v-if="phase !== 'ok'" type="button" class="btn press" :disabled="phase === 'checking'" @click="show = false">取消</button>
        <button v-if="phase === 'ok'" type="button" class="btn primary press" @click="show = false">完成</button>
        <button v-else type="button" class="btn primary press" :disabled="!canSubmit" @click="submit">
          {{ phase === 'checking' ? '校验中' : pendingRetry && !token.trim() ? '重试' : mode === 'add' ? '校验并添加' : '校验并保存' }}
        </button>
      </div>
    </div>
  </NModal>
</template>

<style scoped>
.dialog { width: 460px; padding: 24px 24px 20px; display: flex; flex-direction: column; gap: 14px; box-shadow: var(--sp-pop-shadow); }
h2 { margin: 0; font-size: 17px; font-weight: 700; }
.head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.seg { display: flex; padding: 3px; gap: 2px; border-radius: 10px; border: 1px solid var(--sp-line); background: var(--sp-surface2); }
.seg button { font: inherit; font-size: 13px; padding: 5px 14px; min-height: 30px; border: 0; border-radius: 7px; background: transparent; color: var(--sp-text2); cursor: pointer; }
.seg button.on { background: var(--sp-surface); color: var(--sp-text); font-weight: 600; box-shadow: var(--sp-shadow); }
.seg button:focus-visible { outline: 2px solid var(--sp-accent); outline-offset: 1px; }
.steps { padding-left: 18px; display: flex; flex-direction: column; gap: 2px; }
.form { display: flex; flex-direction: column; gap: 8px; }
.field { display: flex; flex-direction: column; gap: 6px; }
input {
  height: 40px; padding: 0 12px; border-radius: 10px; border: 1.5px solid var(--sp-line-strong); background: var(--sp-surface);
  color: var(--sp-text); font: 14px 'JetBrains Mono', monospace; letter-spacing: 1px;
}
input:focus { outline: none; border-color: var(--sp-accent); }
input.bad { border-color: var(--sp-up); }
input.plain { font: inherit; font-size: 14px; letter-spacing: 0; }
.tip { margin: 0; line-height: 1.6; padding: 10px 12px; border-radius: 10px; background: var(--sp-surface2); color: var(--sp-text2); }
.small { font-size: 12.5px; }
.tiny { font-size: 12px; }
.text2 { color: var(--sp-text2); }
.help { margin: 0; line-height: 1.6; }
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
