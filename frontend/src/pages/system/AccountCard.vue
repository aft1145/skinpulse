<script setup lang="ts">
// 系统页的一个悠悠号（多账号 spec §8，视觉稿「系统页 · 每号一张卡片」）：状态、到期、最近同步，
// 更新 token、立即同步、停用 / 启用、改简称。写操作都走看板的同源 JSON 接口。
// 状态类名带 is- 前缀：系统页自己有 .bad（红字），父组件的 scoped 样式会作用到子组件根元素（CLAUDE.md 坑 11）。
import { useMessage } from 'naive-ui'
import { computed, nextTick, ref } from 'vue'
import { Pencil } from '@lucide/vue'
import { ApiError, buffSync, patchAccount, syncNow } from '@/api/client'
import type { Account } from '@/api/types'
import { clock } from '@/format'
import { accountState } from './logic'

const props = defineProps<{ account: Account; tz: string; canDisable: boolean }>()
const emit = defineEmits<{ changed: []; updateToken: [Account] }>()
const message = useMessage()

const state = computed(() => accountState(props.account, props.tz))
// BUFF 号（BUFF spec §8）：凭据是 session cookie，没有到期时间；「立即同步」= 让 BUFF 的下一轮马上开始
const buff = computed(() => props.account.platform === 'buff')
const busy = ref(false)
const editing = ref(false)
const draft = ref('')
const input = ref<HTMLInputElement | null>(null)

async function run(fn: () => Promise<unknown>, ok: string) {
  if (busy.value) return
  busy.value = true
  try {
    await fn()
    message.success(ok)
    emit('changed')
  } catch (e) {
    message.error(e instanceof ApiError ? e.message : String(e))
  } finally {
    busy.value = false
  }
}

const sync = () => buff.value
  ? run(async () => {
      const r = await buffSync()
      if (!r.round_started) throw new Error('BUFF 监控当前未排队（服务刚启动或该账号已停用），请稍后再试')
    }, '已让 BUFF 的下一轮马上开始，一两分钟后刷新')
  : run(() => syncNow(props.account.id), '同步完成')
const toggle = () =>
  run(() => patchAccount(props.account.id, { enabled: !props.account.enabled }), props.account.enabled ? '已停用' : '已启用，下一轮会同步它的单')

async function edit() {
  draft.value = props.account.custom_label ?? ''
  editing.value = true
  await nextTick()
  input.value?.focus()
}
function save() {
  if (!editing.value) return
  editing.value = false
  if (draft.value.trim() === (props.account.custom_label ?? '')) return
  void run(() => patchAccount(props.account.id, { label: draft.value.trim() }), '简称已修改')
}
</script>

<template>
  <article class="card acct" :class="`is-${state.tone}`">
    <div class="head">
      <input
        v-if="editing"
        ref="input"
        v-model="draft"
        class="label-input"
        maxlength="8"
        aria-label="简称"
        :placeholder="account.nickname ?? '简称'"
        @keydown.enter.prevent="save"
        @keydown.esc.prevent="editing = false"
        @blur="save"
      />
      <template v-else>
        <b class="label">{{ account.label }}</b>
        <span class="plat" :class="{ buff }">{{ buff ? 'BUFF' : '悠悠' }}</span>
        <button type="button" class="icon-btn press" aria-label="改简称" @click="edit"><Pencil :size="14" /></button>
      </template>
      <span class="pill" :class="`is-${state.tone}`"><span class="dot" />{{ state.text }}</span>
    </div>
    <dl>
      <template v-if="buff && account.enabled && account.token.valid">
        <dt>cookie 到期</dt>
        <dd class="text2">未知（BUFF 未提供），失效时会提醒</dd>
      </template>
      <template v-else-if="account.enabled && account.token.valid">
        <dt>token 到期</dt>
        <dd>
          {{ account.token.expires_at ? clock(account.token.expires_at, tz) : '读不到' }}
          <span v-if="account.token.days_left != null" :class="{ warn: account.token.days_left <= 2 }">（还有 {{ account.token.days_left }} 天）</span>
        </dd>
      </template>
      <dt>最近同步</dt>
      <dd>{{ account.last_sync_at ? clock(account.last_sync_at, tz) : '暂无' }} · <span class="num">{{ account.orders }}</span> 张求购<template v-if="buff"> · 每 15 分钟一轮</template></dd>
      <template v-if="state.note">
        <dt>现在</dt>
        <dd class="text2">{{ state.note }}</dd>
      </template>
    </dl>
    <div class="actions">
      <button
        v-if="account.enabled"
        type="button"
        class="btn press"
        :class="{ primary: state.tone === 'bad' }"
        :disabled="busy"
        @click="emit('updateToken', account)"
      >{{ buff ? '更新 cookie' : '更新 token' }}</button>
      <button v-if="account.enabled && account.token.valid" type="button" class="btn press" :disabled="busy" @click="sync">立即同步</button>
      <button
        v-if="!account.enabled || canDisable"
        type="button"
        class="btn ghost press"
        :disabled="busy"
        @click="toggle"
      >{{ account.enabled ? '停用' : '启用' }}</button>
    </div>
  </article>
</template>

<style scoped>
.acct { padding: 20px 22px; display: flex; flex-direction: column; gap: 14px; }
.acct.is-bad { border-color: color-mix(in srgb, var(--sp-warn) 55%, var(--sp-line)); }
.acct.is-off { opacity: 0.75; }
.head { display: flex; align-items: center; gap: 8px; min-height: 32px; }
.label { font-size: 17px; }
.label-input {
  height: 32px; width: 140px; padding: 0 10px; border-radius: 8px; border: 1.5px solid var(--sp-accent);
  background: var(--sp-surface); color: var(--sp-text); font: inherit; font-size: 15px;
}
.label-input:focus { outline: none; }
.icon-btn {
  width: 32px; height: 32px; border-radius: 8px; border: 0; background: transparent; color: var(--sp-text2);
  display: inline-flex; align-items: center; justify-content: center; cursor: pointer;
}
.icon-btn:hover { color: var(--sp-text); background: var(--sp-surface2); }
.icon-btn:focus-visible { outline: 2px solid var(--sp-accent); outline-offset: 1px; }
.plat { font-size: 11px; padding: 1px 6px; border-radius: 6px; border: 1px solid var(--sp-line); color: var(--sp-text2); }
.plat.buff { color: var(--sp-info); background: var(--sp-info-soft); border-color: color-mix(in srgb, var(--sp-info) 35%, transparent); }
.pill { margin-left: auto; display: inline-flex; align-items: center; gap: 6px; font-size: 12px; padding: 4px 10px; border-radius: 999px; }
.pill .dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.pill.is-ok { color: var(--sp-ok); background: var(--sp-ok-soft); }
.pill.is-bad { color: var(--sp-warn); background: var(--sp-warn-soft); }
.pill.is-off { color: var(--sp-text2); background: var(--sp-surface2); }
dl { margin: 0; display: grid; grid-template-columns: 76px minmax(0, 1fr); row-gap: 8px; font-size: 13px; }
dt { color: var(--sp-text2); }
dd { margin: 0; }
.warn { color: var(--sp-warn); }
.text2 { color: var(--sp-text2); }
.actions { display: flex; gap: 10px; flex-wrap: wrap; }
.btn { height: 36px; padding: 0 14px; border-radius: 10px; border: 1px solid var(--sp-line-strong); background: var(--sp-surface); color: var(--sp-text); font: inherit; font-size: 13px; cursor: pointer; }
.btn.primary { border: none; background: var(--sp-accent); color: var(--sp-on-accent); font-weight: 600; }
.btn.ghost { border-color: transparent; background: transparent; color: var(--sp-text2); margin-left: auto; }
.btn.ghost:hover { color: var(--sp-text); }
.btn:disabled { opacity: 0.5; cursor: default; }
</style>
