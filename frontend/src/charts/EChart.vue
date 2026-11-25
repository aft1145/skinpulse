<script setup lang="ts">
// 所有图表共用的外壳：按需注册（./echarts）、跟随「减少动态效果」、容器自适应宽度。
// 这个组件只被带图表的页面引用，所以 echarts 只进那些页面的包。
import './echarts'
import type { EChartsOption } from 'echarts'
import { computed } from 'vue'
import VChart from 'vue-echarts'
import { useReducedMotion } from '@/composables/useMotion'

const props = defineProps<{
  option: EChartsOption
  height: number
  label: string
  /** 系列个数会随数据变（分析页的断口虚线）时打开：新 option 里没有的系列要删掉，不能留在图上 */
  replaceSeries?: boolean
}>()
const reduced = useReducedMotion()
// spec §3.3：图表入场绘制、切换范围平滑过渡 400 ms；减少动态效果时全部关掉
const full = computed<EChartsOption>(() => ({
  animation: !reduced.value,
  animationDuration: 400,
  animationDurationUpdate: 400,
  ...props.option,
}))
</script>

<template>
  <div class="chart" role="img" :aria-label="label" :style="{ height: `${height}px` }">
    <VChart :option="full" autoresize :update-options="replaceSeries ? { replaceMerge: ['series'] } : undefined" />
  </div>
</template>

<style scoped>
.chart {
  width: 100%;
  min-width: 0;
}
</style>
