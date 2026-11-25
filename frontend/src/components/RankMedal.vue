<script setup lang="ts">
import { computed } from 'vue'
import { useTheme } from '@/composables/useTheme'
import { medalColors } from '@/theme/tokens'

const props = withDefaults(defineProps<{ rank: number | null; size?: number }>(), { size: 28 })
const { tokens } = useTheme()
const c = computed(() => medalColors(tokens.value, props.rank))
const label = computed(() => (props.rank == null ? '—' : String(props.rank)))
</script>

<template>
  <span
    class="medal display"
    :style="{
      width: `${size}px`,
      height: `${size}px`,
      fontSize: `${Math.round(size * 0.46)}px`,
      background: c.bg,
      color: c.fg,
      borderColor: c.line,
    }"
    :aria-label="rank == null ? '排名未知' : `第 ${rank} 名`"
    >{{ label }}</span
  >
</template>

<style scoped>
.medal {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  border-radius: 999px;
  border: 1.5px solid;
  font-weight: 700;
  line-height: 1;
}
</style>
