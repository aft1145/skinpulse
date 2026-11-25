<script setup lang="ts">
import { computed } from 'vue'
import { rankSpark } from '@/format'

const props = withDefaults(
  defineProps<{ values: (number | null)[]; width?: number; height?: number; color?: string; dot?: string; label?: string }>(),
  { width: 52, height: 22, color: 'var(--sp-text3)', dot: 'var(--sp-text2)', label: '近 7 天排名' },
)
const g = computed(() => rankSpark(props.values, props.width, props.height))
</script>

<template>
  <svg :width="width" :height="height" :viewBox="`0 0 ${width} ${height}`" role="img" :aria-label="label">
    <path :d="g.d" fill="none" :stroke="color" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
    <circle v-if="g.last" :cx="g.last[0]" :cy="g.last[1]" r="2.5" :fill="dot" />
  </svg>
</template>
