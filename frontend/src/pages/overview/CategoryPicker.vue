<script setup lang="ts">
// 品类筛选（看板 spec §4.1，视觉稿 2026-10-09）：一个按钮 + 两栏面板。左栏大类，右栏是停在（或选中）
// 枪、刀、手套大类上时的具体武器。点大类筛整类，点右栏细到一种武器；选中后按钮显示「步枪 › AK-47 ×」。
// 键盘：↑/↓ 在当前栏里移动，→ 进右栏、← 回左栏，Esc 关面板并把焦点还给按钮。
import { onClickOutside } from '@vueuse/core'
import { ChevronDown, ChevronRight, X } from '@lucide/vue'
import { computed, nextTick, ref } from 'vue'
import type { CategoryMenuGroup } from './logic'

const props = defineProps<{ menu: CategoryMenuGroup[]; total: number; text: string }>()
const cat = defineModel<string | null>('cat', { required: true })
const sub = defineModel<string | null>('sub', { required: true })

const open = ref(false)
const root = ref<HTMLElement | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)
const groupsEl = ref<HTMLElement | null>(null)
const subsEl = ref<HTMLElement | null>(null)
// 右栏显示哪个大类的第二层：鼠标停在 / 键盘落在哪个大类上
const peek = ref<string | null>(null)
const alignRight = ref(false)
const peekGroup = computed(() => props.menu.find((g) => g.key === peek.value && g.subs.length) ?? null)

onClickOutside(root, () => (open.value = false))

async function toggle() {
  open.value = !open.value
  if (!open.value) return
  peek.value = cat.value
  // 两栏一共 400px：按钮靠右时面板改成右对齐，别伸出窗口
  const r = root.value?.getBoundingClientRect()
  alignRight.value = !!r && r.left + 420 > window.innerWidth
  await nextTick()
  const on = groupsEl.value?.querySelector<HTMLElement>('[data-on="true"]')
  ;(on ?? groupsEl.value?.querySelector<HTMLElement>('button'))?.focus()
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

function pick(c: string | null, s: string | null = null) {
  cat.value = c
  sub.value = s
  close()
}

function move(col: HTMLElement | null, delta: number) {
  const items = [...(col?.querySelectorAll<HTMLElement>('button') ?? [])]
  const i = items.indexOf(document.activeElement as HTMLElement)
  items[(i + delta + items.length) % items.length]?.focus()
}

async function intoSubs() {
  if (!peekGroup.value) return
  await nextTick()
  const on = subsEl.value?.querySelector<HTMLElement>('[data-on="true"]')
  ;(on ?? subsEl.value?.querySelector<HTMLElement>('button'))?.focus()
}

function backToGroups() {
  groupsEl.value?.querySelector<HTMLElement>(`[data-key="${peek.value}"]`)?.focus()
}
</script>

<template>
  <div ref="root" class="picker" @keydown.esc="onEsc">
    <div class="trigger-wrap" :class="{ set: cat }">
      <button
        ref="trigger"
        type="button"
        class="trigger press"
        :class="{ open }"
        aria-haspopup="true"
        :aria-expanded="open"
        :aria-label="`品类：${text}`"
        @click="toggle"
      >
        <template v-if="cat">{{ text }}</template>
        <template v-else>品类<span class="v">全部</span><ChevronDown :size="14" class="caret" /></template>
      </button>
      <button v-if="cat" type="button" class="clear press" aria-label="清除品类筛选" @click="pick(null)"><X :size="14" /></button>
    </div>

    <div v-if="open" class="pop" :class="{ right: alignRight }" role="group" aria-label="按品类筛选">
      <div ref="groupsEl" class="col groups" @keydown.down.prevent="move(groupsEl, 1)" @keydown.up.prevent="move(groupsEl, -1)" @keydown.right.prevent="intoSubs">
        <button type="button" class="it" :data-on="!cat" :class="{ on: !cat }" @click="pick(null)" @mouseenter="peek = null" @focus="peek = null">
          <span class="lb">全部品类</span><span class="n">{{ total }}</span><span class="chev" />
        </button>
        <div class="div" />
        <button
          v-for="g in menu"
          :key="g.key"
          type="button"
          class="it"
          :class="{ on: cat === g.key, peek: peek === g.key && g.subs.length }"
          :data-on="cat === g.key"
          :data-key="g.key"
          @click="pick(g.key)"
          @mouseenter="peek = g.key"
          @focus="peek = g.key"
        >
          <span class="lb">{{ g.label }}</span><span class="n">{{ g.n }}</span>
          <span class="chev"><ChevronRight v-if="g.subs.length" :size="13" /></span>
        </button>
      </div>
      <div v-if="peekGroup" ref="subsEl" class="col subs" @keydown.down.prevent="move(subsEl, 1)" @keydown.up.prevent="move(subsEl, -1)" @keydown.left.prevent="backToGroups">
        <button type="button" class="it" :class="{ on: cat === peekGroup.key && !sub }" :data-on="cat === peekGroup.key && !sub" @click="pick(peekGroup.key)">
          <span class="lb">全部{{ peekGroup.label }}</span><span class="n">{{ peekGroup.n }}</span>
        </button>
        <div class="div" />
        <button
          v-for="s in peekGroup.subs"
          :key="s.key"
          type="button"
          class="it"
          :class="{ on: cat === peekGroup.key && sub === s.key }"
          :data-on="cat === peekGroup.key && sub === s.key"
          @click="pick(peekGroup.key, s.key)"
        >
          <span class="lb">{{ s.label }}</span><span class="n">{{ s.n }}</span>
        </button>
      </div>
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
.clear {
  width: 24px; height: 24px; margin-right: 6px; border: 0; border-radius: 6px; background: transparent; color: var(--sp-accent);
  display: inline-grid; place-items: center; cursor: pointer;
}
.clear:hover { background: var(--sp-surface); }
.trigger:focus, .clear:focus, .it:focus { outline: none; }
.trigger:focus-visible, .clear:focus-visible, .it:focus-visible { box-shadow: 0 0 0 2px var(--sp-accent); }

.pop {
  position: absolute; top: calc(100% + 8px); left: 0; z-index: 20; display: flex;
  background: var(--sp-surface); border: 1px solid var(--sp-line); border-radius: 14px; box-shadow: var(--sp-pop-shadow); overflow: hidden;
}
.pop.right { left: auto; right: 0; }
.col { width: 200px; max-height: min(520px, 70vh); overflow-y: auto; display: flex; flex-direction: column; gap: 1px; padding: 6px; }
.subs { border-left: 1px solid var(--sp-line); background: var(--sp-surface2); }
.it {
  min-height: 32px; flex-shrink: 0; border: 0; border-radius: 8px; background: transparent; padding: 0 10px; font: inherit; font-size: 13px;
  color: var(--sp-text); display: flex; align-items: center; gap: 8px; text-align: left; cursor: pointer;
}
.lb { flex: 1; }
.n { color: var(--sp-text3); font-size: 12px; font-variant-numeric: tabular-nums; }
.chev { width: 13px; display: inline-flex; color: var(--sp-text3); }
.it:hover, .it.peek { background: var(--sp-surface2); }
.subs .it:hover { background: var(--sp-surface); }
.it.on { color: var(--sp-accent); font-weight: 600; }
.it.on .n { color: var(--sp-accent); }
.div { height: 1px; flex-shrink: 0; background: var(--sp-line); margin: 4px 6px; }
@media (max-width: 560px) { .col { width: 46vw; } }
</style>
