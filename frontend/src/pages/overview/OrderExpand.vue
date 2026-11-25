<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ExternalLink } from '@lucide/vue'
import type { Order } from '@/api/types'
import { money, rankSpark, refBid, relative } from '@/format'

const props = defineProps<{ order: Order; days: string[]; maxGapRatio: number; youpinUrl: string; tz: string }>()
const o = computed(() => props.order)

// 近 7 天排名：带网格线的大一号折线
const W = 280, H = 100, PAD = 10
const chart = computed(() => {
  const known = o.value.rank_7d.filter((v): v is number => v != null)
  const maxR = Math.max(5, ...known)
  const g = rankSpark(o.value.rank_7d, W, H, PAD, maxR)
  const grid = Array.from({ length: maxR }, (_, i) => PAD + (i * (H - 2 * PAD)) / (maxR - 1))
  const dots: [number, number][] = []
  o.value.rank_7d.forEach((v, i) => {
    if (v != null) dots.push([PAD + (i * (W - 2 * PAD)) / (o.value.rank_7d.length - 1), PAD + ((v - 1) * (H - 2 * PAD)) / (maxR - 1)])
  })
  return { d: g.d, grid, dots, maxR }
})
const dayLabels = computed(() => {
  const d = props.days.map((x) => x.slice(5))
  return d.length ? [d[0], d[Math.floor(d.length / 2)], d[d.length - 1]] : []
})

// 价格位置：我的价、榜首、在售最低放在同一条轴上；圆点从左边滑到位（spec §3.3）
const placed = ref(false)
onMounted(() => requestAnimationFrame(() => (placed.value = true)))
const ladder = computed(() => {
  const pts = [
    { key: 'my', label: '我的价', v: o.value.my_price, color: 'var(--sp-accent)' },
    { key: 'top', label: '榜首', v: o.value.top1_price, color: 'var(--sp-gold)' },
    { key: 'sell', label: '在售最低', v: o.value.sell_min, color: 'var(--sp-text3)' },
  ].filter((p): p is { key: string; label: string; v: number; color: string } => p.v != null)
  const hi = Math.max(...pts.map((p) => p.v), 0) * 1.08 || 1
  const pos = (v: number) => 5 + (v / hi) * 90
  const my = o.value.my_price, top = o.value.top1_price
  const span = my != null && top != null ? { left: pos(Math.min(my, top)), width: Math.abs(pos(top) - pos(my)) } : null
  return { marks: pts.map((p) => ({ ...p, left: pos(p.v) })), span }
})
const note = computed(() => {
  const r = o.value.gap_ratio
  const m = o.value.market_ratio
  // 离市价太远（难以成交 spec §5）：说明为什么升到第 1 名也不推送
  if (!o.value.gated && o.value.unreachable === 'market' && m != null) {
    return `市价是我的 ${m >= 10 ? m.toFixed(1) : m.toFixed(2)} 倍、差 ${money(o.value.market_gap ?? null)}，离市价太远：升到第 1 名也不会推送。`
  }
  if (r == null) return '暂无榜首价数据。'
  return o.value.gated
    ? `榜首是我的 ${r.toFixed(1)} 倍，超过 ${props.maxGapRatio} 倍过滤线：升到第 1 名也不会推送。`
    : `榜首是我的 ${r.toFixed(2)} 倍。`
})
const ref_ = computed(() => refBid(o.value, props.tz))
const youpin = computed(() => props.youpinUrl.replace('{template_id}', String(o.value.template_id)))
</script>

<template>
  <div class="expand">
    <section class="col">
      <span class="label">近 7 天排名</span>
      <svg :width="W" :height="H" :viewBox="`0 0 ${W} ${H}`" role="img" aria-label="近 7 天排名走势">
        <path v-for="y in chart.grid" :key="y" :d="`M0 ${y.toFixed(1)}H${W}`" stroke="var(--sp-line-soft)" stroke-width="1" />
        <path :d="chart.d" fill="none" stroke="var(--sp-accent)" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round" />
        <circle v-for="(p, i) in chart.dots" :key="i" :cx="p[0]" :cy="p[1]" r="3.5" fill="var(--sp-surface)" stroke="var(--sp-accent)" stroke-width="2" />
      </svg>
      <div class="axis num"><span v-for="d in dayLabels" :key="d">{{ d }}</span></div>
    </section>
    <section class="col">
      <span class="label">价格位置</span>
      <div class="ladder">
        <div class="rail" />
        <div v-if="ladder.span" class="span" :style="{ left: `${placed ? ladder.span.left : 5}%`, width: `${placed ? ladder.span.width : 0}%` }" />
        <div v-for="m in ladder.marks" :key="m.key" class="mark" :style="{ left: `${placed ? m.left : 5}%` }">
          <span class="mark-label">{{ m.label }}</span>
          <span class="mark-dot" :style="{ background: m.color }" />
          <span class="mark-value num">{{ money(m.v) }}</span>
        </div>
      </div>
      <span class="note">{{ note }}</span>
    </section>
    <section class="col side">
      <div class="kv"><span class="muted">别处买盘</span><span class="num" :title="ref_.title">{{ ref_.text }}</span></div>
      <div class="kv"><span class="muted">队列</span><span>{{ o.queue === '不限' ? '不限条件' : o.queue }}</span></div>
      <div class="kv"><span class="muted">检查于</span><span>{{ relative(o.checked_at) }}</span></div>
      <RouterLink class="btn primary press" :to="`/item/${o.template_id}`">查看饰品详情</RouterLink>
      <a class="btn press" :href="youpin" target="_blank" rel="noopener noreferrer">在悠悠打开<ExternalLink :size="13" /></a>
    </section>
  </div>
</template>

<style scoped>
.expand { display: grid; grid-template-columns: 280px minmax(0, 1fr) 220px; gap: 40px; align-items: start; padding: 6px 8px 14px; }
.col { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.label { font-size: 12.5px; color: var(--sp-text3); }
.axis { display: flex; justify-content: space-between; font-size: 11px; color: var(--sp-text3); }
.ladder { position: relative; height: 80px; }
.rail, .span { position: absolute; top: 34px; height: 6px; border-radius: 3px; }
.rail { left: 0; right: 0; background: var(--sp-bar); }
.span { background: var(--sp-accent-soft-strong); transition: left 320ms ease-out, width 320ms ease-out; }
.mark { position: absolute; top: 0; transform: translateX(-50%); display: flex; flex-direction: column; align-items: center; gap: 4px; transition: left 320ms cubic-bezier(0.2, 0.8, 0.2, 1); }
.mark-label { font-size: 12px; color: var(--sp-text3); white-space: nowrap; }
.mark-dot { width: 14px; height: 14px; margin-top: 3px; border-radius: 7px; border: 3px solid var(--sp-surface); box-shadow: 0 0 0 1px var(--sp-line); }
.mark-value { font-size: 13px; font-weight: 600; white-space: nowrap; }
.note { font-size: 13px; color: var(--sp-text2); line-height: 1.6; }
.side { gap: 10px; }
.kv { display: flex; justify-content: space-between; gap: 12px; font-size: 13px; }
.btn {
  height: 38px; border-radius: 10px; border: 1px solid var(--sp-line-strong); color: var(--sp-text); font-size: 13px;
  text-decoration: none; display: flex; align-items: center; justify-content: center; gap: 6px;
}
.btn:first-of-type { margin-top: 6px; }
.btn.primary { background: var(--sp-accent); border-color: var(--sp-accent); color: var(--sp-on-accent); font-weight: 600; }
.btn.primary:hover { background: var(--sp-accent-hover); }
.btn:not(.primary):hover { background: var(--sp-surface2); }
</style>
