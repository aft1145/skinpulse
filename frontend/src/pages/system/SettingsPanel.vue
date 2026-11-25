<script setup lang="ts">
// 参数设置（折叠面板，视觉稿三期）：三组表单，每项中文名、说明、单位；保存前列出改了哪几项，
// 有不合要求的就不能保存；保存后立即生效（后端重排调度）并写回 settings.toml。
import { useMessage } from 'naive-ui'
import { computed, ref } from 'vue'
import { ChevronDown, ChevronUp } from '@lucide/vue'
import { ApiError, fetchSettings, saveSettings } from '@/api/client'
import type { FieldError, SettingsValues } from '@/api/types'
import { FIELDS, GROUPS, changes, fieldId, patchOf, toDraft, validate, type Draft } from './fields'

// 没有 BUFF 号时不显示 BUFF 那一组（只有悠悠号时页面和以前一样）。布尔 prop 没传是 false，所以反着起名（CLAUDE.md 坑 11）
// 启用的号不到两个时「看板显示」也不显示：账号切换和标签那时本来就不出现，开关改了看不出变化
const props = defineProps<{ hideBuff?: boolean; hideDashboard?: boolean }>()
// 保存后通知页面刷新 meta：「看板显示」改了，顶栏和表格上的账号名要跟着变
const emit = defineEmits<{ saved: [] }>()
const message = useMessage()
const open = ref(false)
const more = ref(false)
const values = ref<SettingsValues | null>(null)
const draft = ref<Draft>({})
const serverErrors = ref<FieldError[]>([])
const saving = ref(false)
const loadError = ref<string | null>(null)

async function load() {
  try {
    values.value = await fetchSettings()
    draft.value = toDraft(values.value)
    serverErrors.value = []
    loadError.value = null
  } catch (e) {
    loadError.value = e instanceof ApiError ? e.message : String(e)
  }
}
function toggle() {
  open.value = !open.value
  if (open.value && !values.value) load()
}

const errors = computed(() => {
  const m = new Map<string, string>()
  for (const e of [...validate(draft.value), ...serverErrors.value]) if (!m.has(e.field)) m.set(e.field, e.message)
  return m
})
const changed = computed(() => (values.value ? changes(values.value, draft.value) : []))
const changedIds = computed(() => new Set(changed.value.map((c) => c.field)))
const summary = computed(() => {
  const bad = changed.value.filter((c) => errors.value.has(c.field)).length + [...errors.value.keys()].filter((f) => !changedIds.value.has(f)).length
  return { n: changed.value.length, bad }
})

const groups = computed(() =>
  GROUPS.filter((g) => !(props.hideBuff && g.section === 'buff') && !(props.hideDashboard && g.section === 'dashboard')).map((g) => {
    const all = FIELDS.filter((f) => f.section === g.section)
    return { ...g, fields: all.filter((f) => !f.more || more.value), hidden: all.filter((f) => f.more).length }
  }),
)

function discard() {
  if (values.value) draft.value = toDraft(values.value)
  serverErrors.value = []
}

async function save() {
  if (!changed.value.length || errors.value.size || saving.value) return
  saving.value = true
  try {
    const r = await saveSettings(patchOf(draft.value, changed.value))
    const texts = changed.value.map((c) => c.text)
    values.value = r.settings
    draft.value = toDraft(r.settings)
    serverErrors.value = []
    message.success(`已保存，修改了 ${r.changed.length} 项：${texts.join('、')}。已生效。`, { duration: 6000 })
    emit('saved')
  } catch (e) {
    if (e instanceof ApiError && Array.isArray(e.detail)) serverErrors.value = e.detail as FieldError[]
    else message.error(e instanceof ApiError ? e.message : String(e))
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <article class="card settings">
    <button type="button" class="head" :aria-expanded="open" aria-controls="settings-body" @click="toggle">
      <span class="names">
        <span class="title">参数设置</span>
        <span class="muted small">保存后立即生效，并写回 settings.toml</span>
      </span>
      <ChevronUp v-if="open" :size="18" class="muted" />
      <ChevronDown v-else :size="18" class="muted" />
    </button>

    <div v-if="open" id="settings-body">
      <p v-if="loadError" class="muted pad">设置读取失败：{{ loadError }}</p>
      <div v-else-if="!values" class="pad"><div class="skeleton" style="height: 320px" /></div>
      <template v-else>
        <div class="grid">
          <section v-for="g in groups" :key="g.section" class="group">
            <span class="gtitle">{{ g.title }}</span>
            <span class="muted tiny ghint">{{ g.hint }}</span>
            <div v-for="f in g.fields" :key="f.key" class="row" :class="{ bad: errors.has(fieldId(f)) }">
              <label :for="`set-${fieldId(f)}`" class="meta">
                <span class="label">
                  {{ f.label }}<span v-if="changedIds.has(fieldId(f)) && !errors.has(fieldId(f))" class="chip">已改</span>
                </span>
                <span class="desc">{{ errors.get(fieldId(f)) ?? f.desc }}</span>
              </label>
              <button
                v-if="f.kind === 'toggle'"
                :id="`set-${fieldId(f)}`"
                type="button"
                role="switch"
                class="switch press"
                :aria-checked="!!draft[fieldId(f)]"
                :class="{ on: draft[fieldId(f)] }"
                @click="draft[fieldId(f)] = !draft[fieldId(f)]"
              >
                <span class="knob" />
              </button>
              <span v-else class="input" :class="{ changed: changedIds.has(fieldId(f)), error: errors.has(fieldId(f)) }">
                <input
                  :id="`set-${fieldId(f)}`"
                  v-model="draft[fieldId(f)] as string"
                  :inputmode="f.kind === 'time' ? 'text' : 'decimal'"
                  autocomplete="off"
                  @input="serverErrors = serverErrors.filter((e) => e.field !== fieldId(f))"
                />
                <span v-if="f.unit" class="unit">{{ f.unit }}</span>
              </span>
            </div>
            <button v-if="g.hidden" type="button" class="more" @click="more = !more">
              {{ more ? '收起' : `更多 ${g.hidden} 项（通常无需修改）` }} ›
            </button>
          </section>
        </div>

        <div class="foot">
          <span class="small text2 grow">
            <template v-if="!summary.n && !summary.bad">无改动</template>
            <template v-else>
              修改了 {{ summary.n }} 项：
              <template v-for="(c, i) in changed" :key="c.field">
                <span v-if="i">、</span><b v-if="errors.has(c.field)" class="err">{{ c.text }} 不合要求</b><span v-else>{{ c.text }}</span>
              </template>
            </template>
          </span>
          <button type="button" class="btn press" :disabled="!summary.n && !serverErrors.length" @click="discard">放弃修改</button>
          <button type="button" class="btn primary press" :disabled="!summary.n || errors.size > 0 || saving" @click="save">
            {{ saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </template>
    </div>
  </article>
</template>

<style scoped>
.settings { padding: 0; }
.head { width: 100%; display: flex; align-items: center; gap: 12px; padding: 22px 28px; border: none; background: transparent; color: var(--sp-text); font: inherit; text-align: left; cursor: pointer; border-radius: 18px; }
.names { display: flex; flex-direction: column; gap: 4px; flex: 1; }
.title { font-size: 18px; font-weight: 700; }
.small { font-size: 12.5px; }
.tiny { font-size: 12px; }
.text2 { color: var(--sp-text2); }
.pad { padding: 0 28px 24px; margin: 0; }
.grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border-top: 1px solid var(--sp-line-soft); }
.group { padding: 22px 28px 24px; display: flex; flex-direction: column; border-right: 1px solid var(--sp-line-soft); }
.group:last-child { border-right: none; }
.gtitle { font-size: 14px; font-weight: 700; padding-bottom: 4px; }
.ghint { padding-bottom: 10px; }
.row { display: flex; align-items: center; gap: 14px; padding: 10px 0; border-top: 1px solid var(--sp-line-soft); }
.meta { display: flex; flex-direction: column; gap: 2px; flex: 1; min-width: 0; cursor: default; }
.label { font-size: 13.5px; font-weight: 600; }
.desc { font-size: 12px; color: var(--sp-text3); line-height: 1.5; }
.row.bad .desc { color: var(--sp-up); }
.chip { margin-left: 8px; font-size: 11px; font-weight: 600; color: var(--sp-accent); background: var(--sp-accent-soft); padding: 1px 6px; border-radius: 5px; }
.input { display: flex; align-items: center; gap: 6px; height: 34px; width: 128px; padding: 0 10px; border-radius: 9px; border: 1.5px solid var(--sp-line-strong); background: var(--sp-surface); flex-shrink: 0; }
.input.changed { border-color: var(--sp-accent); }
.input.error { border-color: var(--sp-up); }
.input:focus-within { outline: 2px solid var(--sp-accent); outline-offset: 1px; }
.input input { width: 100%; border: none; background: transparent; color: var(--sp-text); font: 13.5px 'JetBrains Mono', monospace; text-align: right; outline: none; }
.unit { font-size: 12px; color: var(--sp-text3); white-space: nowrap; }
.switch { position: relative; width: 40px; height: 22px; border-radius: 11px; border: none; background: var(--sp-bar); cursor: pointer; flex-shrink: 0; padding: 0; }
.switch.on { background: var(--sp-accent); }
.knob { position: absolute; top: 3px; left: 3px; width: 16px; height: 16px; border-radius: 8px; background: #fff; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.2); transition: left 120ms ease; }
.switch.on .knob { left: 21px; }
.more { align-self: flex-start; margin-top: 12px; padding: 0; border: none; background: none; color: var(--sp-accent); font: inherit; font-size: 12.5px; font-weight: 600; cursor: pointer; }
.foot { display: flex; align-items: center; gap: 14px; padding: 16px 28px; border-top: 1px solid var(--sp-line-soft); background: var(--sp-surface2); border-radius: 0 0 18px 18px; }
.grow { flex: 1; }
.err { color: var(--sp-up); }
.btn { height: 36px; padding: 0 14px; border-radius: 10px; border: 1px solid var(--sp-line-strong); background: var(--sp-surface); color: var(--sp-text); font: inherit; font-size: 13px; cursor: pointer; }
.btn.primary { border: none; background: var(--sp-accent); color: var(--sp-on-accent); font-weight: 600; padding: 0 16px; }
.btn:disabled { opacity: 0.45; cursor: default; }
@media (max-width: 1100px) { .grid { grid-template-columns: 1fr; } .group { border-right: none; } }
</style>
