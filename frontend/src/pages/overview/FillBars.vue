<script setup lang="ts">
// 近 30 天每天成交几件的小柱图（成交推送 spec §7.1）。内联 SVG：总览首页不引入 ECharts。
import { computed } from 'vue'
import { barsGeometry } from './fillsLogic'

const props = withDefaults(defineProps<{ daily: [string, number][]; width?: number; height?: number }>(), {
  width: 276,
  height: 54,
})
const g = computed(() => barsGeometry(props.daily, props.width, props.height))
const ticks = computed(() => {
  const d = props.daily
  if (!d.length) return []
  const pick = [d[0], d[Math.floor((d.length - 1) / 2)], d[d.length - 1]]
  return pick.map(([day]) => day.slice(5))
})
</script>

<template>
  <div class="bars">
    <svg :width="width" :height="height" :viewBox="`0 0 ${width} ${height}`" role="img" :aria-label="g.label">
      <line x1="0" :y1="height - 0.5" :x2="width" :y2="height - 0.5" class="base" />
      <rect v-for="s in g.stubs" :key="`s${s.x}`" :x="s.x" :y="s.y" :width="s.w" :height="s.h" rx="1" class="stub" />
      <rect v-for="b in g.bars" :key="b.day" :x="b.x" :y="b.y" :width="b.w" :height="b.h" rx="1.5" class="bar">
        <title>{{ b.day.slice(5) }} 成交 {{ b.n }} 件</title>
      </rect>
    </svg>
    <div class="ticks num"><span v-for="(t, i) in ticks" :key="i">{{ t }}</span></div>
  </div>
</template>

<style scoped>
.bars { display: flex; flex-direction: column; gap: 6px; }
.base { stroke: var(--sp-line); stroke-width: 1; }
.stub { fill: var(--sp-bar); }
.bar { fill: var(--sp-accent); }
.ticks { display: flex; justify-content: space-between; font-size: 11px; color: var(--sp-text3); }
</style>
