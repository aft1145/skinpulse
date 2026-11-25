<script setup lang="ts">
import { computed } from 'vue'
import type { Order } from '@/api/types'
import RankMedal from '@/components/RankMedal.vue'
import RankSpark from '@/components/RankSpark.vue'
import { bestRank, money, subLine } from '@/format'

const props = defineProps<{ order: Order }>()
const o = computed(() => props.order)
const first = computed(() => o.value.rank === 1)
const pct = computed(() => {
  const { my_price: my, top1_price: top } = o.value
  if (first.value) return 100
  return my != null && top ? Math.min(100, Math.round((my / top) * 100)) : 0
})
const sub = computed(() => {
  const best = bestRank(o.value.rank_7d)
  return [subLine(o.value) || null, best != null ? `7 天最好第 ${best} 名` : null].filter(Boolean).join(' · ')
})
</script>

<template>
  <RouterLink class="focus card press" :to="`/item/${o.template_id}`">
    <div class="head">
      <RankMedal :rank="o.rank" :size="32" />
      <div class="names">
        <span class="title"><span v-if="o.star" class="star" aria-label="刀或手套">★</span>{{ o.title }}</span>
        <span class="muted small">{{ sub }}</span>
      </div>
      <RankSpark :values="o.rank_7d" :width="64" :height="28" />
    </div>
    <div class="numbers">
      <div class="big">
        <span class="muted small">{{ first ? '现在' : '离榜首还差' }}</span>
        <span class="display value" :class="{ gold: first }">{{ first ? '已是第 1' : money(o.gap) }}</span>
      </div>
      <div class="pair small">
        <span class="muted">我的价 <b class="num">{{ money(o.my_price) }}</b></span>
        <span class="muted">榜首 <b class="num">{{ money(o.top1_price) }}</b></span>
      </div>
    </div>
    <div class="progress">
      <div class="track"><div class="fill" :style="{ width: `${pct}%` }" /></div>
      <span class="small note">{{ first ? '排在第 1 名' : `已到榜首的 ${pct}%` }}</span>
    </div>
  </RouterLink>
</template>

<style scoped>
.focus { display: flex; flex-direction: column; gap: 20px; padding: 24px 26px; text-decoration: none; color: inherit; }
.focus:hover { border-color: var(--sp-line-strong); }
.head { display: flex; align-items: center; gap: 12px; }
.names { display: flex; flex-direction: column; gap: 3px; min-width: 0; flex: 1; }
.title { font-size: 16px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.star { color: var(--sp-gold); margin-right: 6px; }
.small { font-size: 12.5px; }
.numbers { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; }
.big { display: flex; flex-direction: column; gap: 4px; }
.value { font-size: 34px; font-weight: 600; line-height: 1; }
.value.gold { color: var(--sp-gold); font-size: 28px; }
.pair { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; }
.pair b { color: var(--sp-text); font-weight: 600; }
.progress { display: flex; flex-direction: column; gap: 8px; }
.track { height: 8px; border-radius: 4px; background: var(--sp-bar); overflow: hidden; }
.fill { height: 8px; border-radius: 4px; background: var(--sp-accent); transition: width 600ms cubic-bezier(0.2, 0.8, 0.2, 1); }
.note { color: var(--sp-text2); }
</style>
