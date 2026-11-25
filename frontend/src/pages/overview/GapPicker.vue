<script setup lang="ts">
// 「离第 1」档位筛选（intent 2026-11-20，做法 A）：和品类按钮同一套外观，一个按钮 + 一栏面板。
// 档位和表格里的标签一一对应；已是第 1、交易中、算不出差距的单不参与。
// 键盘：↑/↓ 在面板里移动，Esc 关面板并把焦点还给按钮。
import { onClickOutside } from '@vueuse/core'
import { ChevronDown, X } from '@lucide/vue'
import { computed, nextTick, ref } from 'vue'
import { GAP_FILTERS, GAP_MID, GAP_NEAR, type GapFilter } from './logic'

const props = defineProps<{ counts: Record<GapFilter, number>; total: number }>()
const gap = defineModel<GapFilter | null>({ required: true })

const pct = (v: number) => `${Math.round(v * 100)}%`
const HINT: Record<GapFilter, string> = {
  near: `≤ ${pct(GAP_NEAR)}`,
  mid: `${pct(GAP_NEAR)}～${pct(GAP_MID)}`,
  far: `> ${pct(GAP_MID)}`,
  gated: '差价过大',
}
const label = computed(() => GAP_FILTERS.find((g) => g.key === gap.value)?.label ?? '全部')

const open = ref(false)
const root = ref<HTMLElement | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)
const list = ref<HTMLElement | null>(null)
const alignRight = ref(false)

onClickOutside(root, () => (open.value = false))

async function toggle() {
  open.value = !open.value
  if (!open.value) return
  const r = root.value?.getBoundingClientRect()
  alignRight.value = !!r && r.left + 240 > window.innerWidth
  await nextTick()
  const on = list.value?.querySelector<HTMLElement>('[data-on="true"]')
  ;(on ?? list.value?.querySelector<HTMLElement>('button'))?.focus()
}

function close() {
  open.value = false
  trigger.value?.focus()
}

function onEsc(e: KeyboardEvent) {
  if (!open.value) return
  e.stopPropagation()
  close()
}

function pick(g: GapFilter | null) {
  gap.value = g
  close()
}

function move(delta: number) {
  const items = [...(list.value?.querySelectorAll<HTMLElement>('button') ?? [])]
  const i = items.indexOf(document.activeElement as HTMLElement)
  items[(i + delta + items.length) % items.length]?.focus()
}
</script>

<template>
  <div ref="root" class="picker" @keydown.esc="onEsc">
    <div class="trigger-wrap" :class="{ set: gap }">
      <button
        ref="trigger"
        type="button"
        class="trigger press"
        :class="{ open }"
        aria-haspopup="true"
        :aria-expanded="open"
        :aria-label="`离第 1：${label}`"
        @click="toggle"
      >
        离第 1<span class="v">{{ label }}</span><ChevronDown v-if="!gap" :size="14" class="caret" />
      </button>
      <button v-if="gap" type="button" class="clear press" aria-label="清除离第 1 筛选" @click="pick(null)"><X :size="14" /></button>
    </div>

    <div v-if="open" ref="list" class="pop" :class="{ right: alignRight }" role="group" aria-label="按离第 1 的差距筛选" @keydown.down.prevent="move(1)" @keydown.up.prevent="move(-1)">
      <button type="button" class="it" :class="{ on: !gap }" :data-on="!gap" @click="pick(null)">
        <span class="dot" /><span class="lb">全部</span><span class="n">{{ props.total }}</span>
      </button>
      <div class="div" />
      <button
        v-for="g in GAP_FILTERS"
        :key="g.key"
        type="button"
        class="it"
        :class="{ on: gap === g.key }"
        :data-on="gap === g.key"
        @click="pick(g.key)"
      >
        <span class="dot" :class="g.key" /><span class="lb">{{ g.label }}<small>{{ HINT[g.key] }}</small></span><span class="n">{{ props.counts[g.key] }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.picker { position: relative; }
.trigger-wrap {
  height: 38px; display: inline-flex; align-items: center; border-radius: 10px;
  border: 1px solid var(--sp-line-strong); background: var(--sp-surface);
}
.trigger-wrap.set { background: var(--sp-accent-soft); border-color: transparent; }
.trigger {
  height: 100%; padding: 0 12px; border: 0; border-radius: 10px; background: transparent; font: inherit;
  font-size: 13px; font-weight: 500; color: var(--sp-text2); display: inline-flex; align-items: center; gap: 8px; cursor: pointer;
  white-space: nowrap;
}
.trigger .v { color: var(--sp-text); }
.trigger .caret { color: var(--sp-text3); }
.trigger-wrap:not(.set):hover { background: var(--sp-surface2); }
.trigger-wrap:has(.trigger.open) { border-color: var(--sp-accent); box-shadow: 0 0 0 3px var(--sp-accent-soft); }
.set .trigger { color: var(--sp-accent); font-weight: 600; padding-right: 4px; }
.set .trigger .v { color: var(--sp-accent); }
.clear {
  width: 24px; height: 24px; margin-right: 6px; border: 0; border-radius: 6px; background: transparent; color: var(--sp-accent);
  display: inline-grid; place-items: center; cursor: pointer;
}
.clear:hover { background: var(--sp-surface); }
.trigger:focus, .clear:focus, .it:focus { outline: none; }
.trigger:focus-visible, .clear:focus-visible, .it:focus-visible { box-shadow: 0 0 0 2px var(--sp-accent); }

.pop {
  position: absolute; top: calc(100% + 8px); left: 0; z-index: 20; width: 220px; display: flex; flex-direction: column; gap: 1px; padding: 6px;
  background: var(--sp-surface); border: 1px solid var(--sp-line); border-radius: 14px; box-shadow: var(--sp-pop-shadow);
}
.pop.right { left: auto; right: 0; }
.it {
  min-height: 32px; flex-shrink: 0; border: 0; border-radius: 8px; background: transparent; padding: 0 10px; font: inherit; font-size: 13px;
  color: var(--sp-text); display: flex; align-items: center; gap: 8px; text-align: left; cursor: pointer;
}
.lb { flex: 1; display: inline-flex; align-items: baseline; gap: 6px; }
.lb small { font-size: 11.5px; color: var(--sp-text3); font-weight: 400; }
.n { color: var(--sp-text3); font-size: 12px; font-variant-numeric: tabular-nums; }
.dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; background: transparent; }
.dot.near { background: var(--sp-accent); }
.dot.mid { background: var(--sp-info); }
.dot.far { background: var(--sp-warn); }
.dot.gated { background: repeating-linear-gradient(45deg, var(--sp-text3) 0 2px, transparent 2px 3.5px); box-shadow: inset 0 0 0 1px var(--sp-text3); }
.it:hover { background: var(--sp-surface2); }
.it.on { color: var(--sp-accent); font-weight: 600; }
.it.on .n { color: var(--sp-accent); }
.div { height: 1px; flex-shrink: 0; background: var(--sp-line); margin: 4px 6px; }
</style>
