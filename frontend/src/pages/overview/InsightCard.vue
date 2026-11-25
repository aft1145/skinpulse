<script setup lang="ts">
defineProps<{ title: string; text: string; tone: 'warn' | 'neutral'; spark?: string }>()
</script>

<template>
  <div class="insight card">
    <div class="icon" :class="tone"><slot name="icon" /></div>
    <div class="body">
      <span class="title">{{ title }}</span>
      <span class="text">{{ text }}</span>
    </div>
    <svg v-if="spark" width="72" height="30" viewBox="0 0 72 30" aria-hidden="true">
      <path :d="spark" fill="none" :stroke="tone === 'warn' ? 'var(--sp-warn)' : 'var(--sp-text3)'" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
  </div>
</template>

<style scoped>
.insight { display: flex; align-items: center; gap: 16px; padding: 18px 22px; border-radius: 14px; }
.icon { width: 40px; height: 40px; flex-shrink: 0; border-radius: 12px; display: flex; align-items: center; justify-content: center; background: var(--sp-surface2); color: var(--sp-text2); }
.icon.warn { background: var(--sp-warn-soft); color: var(--sp-warn); }
.body { display: flex; flex-direction: column; gap: 3px; flex: 1; min-width: 0; }
.title { font-weight: 600; }
.text { font-size: 13px; color: var(--sp-text2); }
</style>
