<script setup lang="ts">
// 价位视图的展开行（价位 spec §7.1）：一年收盘区间、卖出去能拿到多少、几项原始数字。
import { computed } from 'vue'
import type { Order, OrderValue, ValuationSummary } from '@/api/types'
import RangeRail from '@/components/RangeRail.vue'
import { ratioPct } from '@/format'
import { adviceTone, rangeMarks, waterfall } from './valueLogic'

const props = defineProps<{ order: Order; value: OrderValue; fees: ValuationSummary['fees'] }>()
const marks = computed(() => rangeMarks(props.value))
const rows = computed(() => waterfall(props.value, props.order, props.fees))
const note = computed(() => {
  const v = props.value
  if (v.pct == null || v.cur == null || v.hi == null || v.lo == null) return `日线只有 ${v.bars} 根，不到 60 根不算位置。`
  return `现价在一年收盘里排 ${Math.round(v.pct * 100)}%，离一年高点 ${ratioPct(v.cur / v.hi - 1).text}，离一年低点 ${ratioPct(v.cur / v.lo - 1).text}。`
})
</script>

<template>
  <div v-if="value.advice" class="advice" :class="adviceTone(value.advice.kind)">
    <span class="label">建议</span>
    <b>{{ value.advice.label }}</b>
    <span class="rule">{{ value.advice.rule }}</span>
  </div>
  <div class="expand">
    <section class="col">
      <span class="label">一年收盘区间（悠悠日线）</span>
      <RangeRail v-if="marks.length" :marks="marks" />
      <span class="note">{{ note }}</span>
    </section>
    <section class="col">
      <span class="label">卖出去能拿到多少</span>
      <div v-if="rows.length" class="water">
        <div v-for="r in rows" :key="r.label" class="wrow" :class="{ strong: r.strong }">
          <span :class="r.tone">{{ r.label }}</span>
          <span class="num" :class="r.tone">{{ r.value }}</span>
        </div>
      </div>
      <span v-else class="note">当天无价格快照，无法计算差价。</span>
    </section>
    <section class="col side">
      <div class="kv"><span class="muted">日线</span><span class="num">{{ value.bars }} 根</span></div>
      <div class="kv"><span class="muted">7 天波动</span><span class="num">{{ value.vol7 == null ? '—' : `±${(value.vol7 * 100).toFixed(1)}%` }}</span></div>
      <div class="kv"><span class="muted">在售（悠悠 + BUFF）</span><span class="num">{{ value.listed == null ? '—' : `${value.listed} 件` }}</span></div>
      <div v-if="value.conditional" class="kv"><span class="muted">条件单</span><span>K 线按整个磨损档算</span></div>
      <RouterLink class="btn primary press" :to="`/item/${order.template_id}`">查看饰品详情</RouterLink>
    </section>
  </div>
</template>

<style scoped>
.advice {
  margin: 4px 8px 16px; padding: 12px 16px; border-radius: 12px; display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  border: 1px solid var(--sp-line); background: var(--sp-surface);
}
.advice b { font-weight: 700; color: var(--sp-text); }
.advice .rule { font-size: 13px; color: var(--sp-text2); }
.advice.warn { background: var(--sp-warn-soft); border-color: var(--sp-warn); }
.advice.warn b { color: var(--sp-warn); }
.advice.accent { background: var(--sp-accent-soft); border-color: var(--sp-accent-soft-strong); }
.advice.accent b { color: var(--sp-accent); }
.advice.muted b { color: var(--sp-text3); }
.expand { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr) 220px; gap: 40px; align-items: start; padding: 6px 8px 14px; }
.col { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.label { font-size: 12.5px; color: var(--sp-text3); }
.note { font-size: 13px; color: var(--sp-text2); line-height: 1.6; }
.water { display: flex; flex-direction: column; }
.wrow { display: flex; justify-content: space-between; gap: 16px; font-size: 13px; padding: 4px 0; }
.wrow.strong { font-weight: 600; border-top: 1px solid var(--sp-line-soft); border-bottom: 1px solid var(--sp-line-soft); }
.plain { color: var(--sp-text); }
.soft { color: var(--sp-text2); }
.loss { color: var(--sp-loss); font-weight: 600; }
.side { gap: 10px; }
.kv { display: flex; justify-content: space-between; gap: 12px; font-size: 13px; }
.btn {
  height: 38px; margin-top: 6px; border-radius: 10px; border: 1px solid var(--sp-accent); background: var(--sp-accent);
  color: var(--sp-on-accent); font-size: 13px; font-weight: 600; text-decoration: none; display: flex; align-items: center; justify-content: center;
}
.btn:hover { background: var(--sp-accent-hover); }
</style>
