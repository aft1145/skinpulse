<script setup lang="ts">
withDefaults(defineProps<{ tone: 'ok' | 'warn' | 'idle' }>(), {})
</script>

<template>
  <span class="dot" :class="tone" aria-hidden="true" />
</template>

<style scoped>
.dot { width: 8px; height: 8px; border-radius: 4px; flex-shrink: 0; }
.ok { background: var(--sp-ok); box-shadow: 0 0 0 4px var(--sp-ok-soft); }
.warn { background: var(--sp-warn); box-shadow: 0 0 0 4px var(--sp-warn-soft); animation: pulse 1.8s ease-in-out infinite; }
.idle { background: var(--sp-idle); box-shadow: 0 0 0 4px var(--sp-idle-soft); }
@keyframes pulse { 50% { box-shadow: 0 0 0 7px var(--sp-warn-soft); } }
</style>
