<script setup lang="ts">
// 一年收盘区间条（价位 spec §7）：现价标在条上方，一年低 / 高标在条下方、分别贴左右两端——
// 现价贴近一端时标签也不会叠在一起。总览展开行（md）和详情页卡片（sm）共用。
import { onMounted, ref } from 'vue'

defineProps<{ marks: { key: 'lo' | 'cur' | 'hi'; label: string; value: string; left: number }[]; size?: 'sm' | 'md' }>()
// 现价的点从左端滑到位（spec §3.3）
const placed = ref(false)
onMounted(() => requestAnimationFrame(() => (placed.value = true)))
</script>

<template>
  <div class="ladder" :class="size ?? 'md'">
    <div class="rail" />
    <div v-for="m in marks" :key="m.key" class="mark" :class="m.key" :style="{ left: `${placed || m.key !== 'cur' ? m.left : 3}%` }">
      <template v-if="m.key === 'cur'">
        <span class="mark-label">{{ m.label }} <b class="num">{{ m.value }}</b></span>
        <span class="mark-dot" />
      </template>
      <template v-else>
        <span class="mark-dot" />
        <span class="mark-label">{{ m.label }} <b class="num">{{ m.value }}</b></span>
      </template>
    </div>
  </div>
</template>

<style scoped>
.ladder { position: relative; --dot: 14px; --rail: 37px; height: 80px; }
.ladder.sm { --dot: 12px; --rail: 33px; height: 64px; }
.rail { position: absolute; left: 3%; right: 3%; top: calc(var(--rail) - 3px); height: 6px; border-radius: 3px; background: var(--sp-bar); }
.mark { position: absolute; display: flex; flex-direction: column; align-items: center; gap: 3px; transition: left 320ms cubic-bezier(0.2, 0.8, 0.2, 1); }
.mark-label { font-size: 12px; color: var(--sp-text3); white-space: nowrap; line-height: 16px; }
.sm .mark-label { font-size: 11.5px; }
.mark-label b { color: var(--sp-text); font-weight: 600; margin-left: 2px; }
.mark-dot {
  width: var(--dot); height: var(--dot); box-sizing: border-box; border-radius: 50%; border: 2.5px solid var(--sp-surface);
  box-shadow: 0 0 0 1px var(--sp-line); background: var(--sp-text3);
}
/* 现价：标签 16px + 间距 3px 之后是点，点的中心对准条 */
.mark.cur { top: calc(var(--rail) - 19px - var(--dot) / 2); transform: translateX(-50%); }
.mark.cur .mark-dot { background: var(--sp-accent); }
.mark.lo, .mark.hi { top: calc(var(--rail) - var(--dot) / 2); }
.mark.lo { transform: translateX(calc(var(--dot) / -2)); align-items: flex-start; }
.mark.hi { transform: translateX(calc(-100% + var(--dot) / 2)); align-items: flex-end; }
</style>
