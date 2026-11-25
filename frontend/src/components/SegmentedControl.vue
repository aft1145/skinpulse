<script setup lang="ts" generic="T extends string">
// 分段控件：一组互斥的按钮（role=radiogroup）。自己写而不用 Naive 的 NRadioButton：文字和高亮要
// 在容器里上下左右都居中，焦点框和悬停底色要和按钮一样是圆角（2026-09-27 用户反馈）。
// 键盘：Tab 进入当前项，←/→ 切换。
import { ref } from 'vue'

const props = defineProps<{ options: { key: T; label: string }[]; label: string }>()
const model = defineModel<T>({ required: true })
const buttons = ref<HTMLButtonElement[]>([])

function move(delta: number) {
  const i = props.options.findIndex((o) => o.key === model.value)
  const next = (i + delta + props.options.length) % props.options.length
  model.value = props.options[next].key
  buttons.value[next]?.focus()
}
</script>

<template>
  <div class="seg" role="radiogroup" :aria-label="label" @keydown.left.prevent="move(-1)" @keydown.right.prevent="move(1)">
    <button
      v-for="o in options"
      :key="o.key"
      ref="buttons"
      type="button"
      role="radio"
      class="opt press"
      :class="{ on: o.key === model }"
      :aria-checked="o.key === model"
      :tabindex="o.key === model ? 0 : -1"
      @click="model = o.key"
    >
      {{ o.label }}
    </button>
  </div>
</template>

<style scoped>
.seg {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 3px;
  border-radius: 11px;
  background: var(--sp-seg-bg);
}
.opt {
  height: 32px;
  padding: 0 14px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--sp-text2);
  font: inherit;
  font-size: 13px;
  font-weight: 500;
  line-height: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  white-space: nowrap;
  cursor: pointer;
  transition: background-color 120ms ease, color 120ms ease, box-shadow 120ms ease;
}
.opt:hover:not(.on) { background: var(--sp-surface2); color: var(--sp-text); }
.opt.on {
  background: var(--sp-surface);
  color: var(--sp-text);
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.14);
}
/* 焦点框跟着圆角走，只在键盘操作时出现 */
.opt:focus { outline: none; }
.opt:focus-visible { box-shadow: 0 0 0 2px var(--sp-accent); }
</style>
