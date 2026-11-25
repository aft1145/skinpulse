<script setup lang="ts">
// 价位视图的三张提示卡（价位 spec §7.1）：点一下按这一项筛选，再点一下取消。
import type { ValueFilter } from './valueLogic'

defineProps<{ tiles: { key: ValueFilter; n: number; title: string; sub: string; tone: 'accent' | 'warn' | 'plain' }[] }>()
const model = defineModel<ValueFilter | null>({ required: true })
</script>

<template>
  <div class="tiles">
    <button
      v-for="t in tiles"
      :key="t.key"
      type="button"
      class="tile card press"
      :class="{ on: model === t.key }"
      :aria-pressed="model === t.key"
      @click="model = model === t.key ? null : t.key"
    >
      <span class="n display num" :class="t.tone">{{ t.n }}</span>
      <span class="body">
        <span class="title">{{ t.title }}</span>
        <span class="sub">{{ t.sub }}</span>
      </span>
    </button>
  </div>
</template>

<style scoped>
.tiles { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; }
.tile {
  display: flex; align-items: center; gap: 16px; padding: 18px 22px; border-radius: 14px; text-align: left;
  font: inherit; color: var(--sp-text); cursor: pointer; transition: border-color 120ms ease, box-shadow 120ms ease;
}
.tile:hover { border-color: var(--sp-line-strong); }
.tile.on { border-color: var(--sp-accent); box-shadow: 0 0 0 3px var(--sp-accent-soft); }
.tile:focus-visible { outline: 2px solid var(--sp-accent); outline-offset: 2px; }
.n { font-size: 30px; font-weight: 600; letter-spacing: -0.02em; min-width: 64px; }
.n.accent { color: var(--sp-accent); }
.n.warn { color: var(--sp-warn); }
.body { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.title { font-weight: 600; }
.sub { font-size: 13px; color: var(--sp-text2); }
@media (max-width: 1100px) { .tiles { grid-template-columns: 1fr; } }
</style>
