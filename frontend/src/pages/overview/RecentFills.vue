<script setup lang="ts">
// 总览「最近成交」卡片（成交推送 spec §7.1，视觉稿 2026-10-02 定稿）：左边近 30 天的件数、金额和
// 每天的小柱图，右边最近 5 笔，每笔写清楚是靠哪条推送知道的。近 30 天没有成交时父组件不渲染它。
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { ChevronDown } from '@lucide/vue'
import type { Fills } from '@/api/types'
import { money, subLine } from '@/format'
import FillBars from './FillBars.vue'
import { fillTime, howKnown } from './fillsLogic'

const props = defineProps<{ data: Fills; tz: string }>()
const SHOWN = 5
const all = ref(false)
const rows = computed(() =>
  props.data.fills.slice(0, all.value ? undefined : SHOWN).map((f) => ({
    f,
    at: fillTime(f, props.tz),
    sub: subLine(f) || (f.star ? '刀或手套' : ''),
    price: f.price == null ? '—' : `¥${money(f.price)}${f.qty > 1 ? ` × ${f.qty}` : ''}`,
    known: howKnown(f, props.tz),
  })),
)
const total = computed(() => props.data.total_30d)
const days = computed(() => props.data.daily.length)
</script>

<template>
  <section class="fills card" aria-labelledby="fills-h">
    <div class="summary">
      <h2 id="fills-h">最近成交</h2>
      <div class="big-row">
        <span class="big display">{{ total.count }}</span>
        <span class="unit">件 · 近 {{ days }} 天</span>
      </div>
      <span class="line">
        合计 <b class="num">¥{{ money(total.amount) }}</b>
        <template v-if="total.largest"> · 最大一笔 {{ total.largest.title }} ¥{{ money(total.largest.price) }}</template>
      </span>
      <FillBars class="chart" :daily="data.daily" />
    </div>

    <div class="list">
      <div class="row head">
        <span>成交时间</span><span>饰品</span><span class="right">成交价</span><span class="known">判定依据</span>
      </div>
      <div v-for="r in rows" :key="`${r.f.purchase_no}-${r.f.filled_at}`" class="row">
        <span class="num time">{{ r.at }}</span>
        <span class="who">
          <span class="name-line">
            <span v-if="r.f.star" class="star" aria-label="刀或手套">★</span>
            <RouterLink :to="`/item/${r.f.template_id}`" class="name">{{ r.f.title }}</RouterLink>
            <span v-if="r.f.likely" class="guess" title="曾处于交易中，之后悠悠「已完成」中无这张单">推测</span>
          </span>
          <span class="sub">{{ r.sub }}</span>
        </span>
        <span class="num price right">{{ r.price }}</span>
        <span class="known">
          <span class="chip" :class="r.known.tone">{{ r.known.label }}</span>
          <span v-if="r.known.note" class="note">{{ r.known.note }}</span>
        </span>
      </div>
      <button v-if="data.fills.length > SHOWN" type="button" class="more press" :aria-expanded="all" @click="all = !all">
        {{ all ? '收起' : `近 ${days} 天全部 ${data.fills.length} 笔` }}
        <ChevronDown :size="13" :class="{ flip: all }" />
      </button>
    </div>
  </section>
</template>

<style scoped>
.fills { display: grid; grid-template-columns: 340px minmax(0, 1fr); }
.summary { padding: 28px 32px; display: flex; flex-direction: column; gap: 18px; border-right: 1px solid var(--sp-line-soft); }
h2 { margin: 0; font-size: 20px; font-weight: 700; letter-spacing: -0.01em; }
.big-row { display: flex; align-items: baseline; gap: 10px; }
.big { font-size: 48px; font-weight: 600; line-height: 1; color: var(--sp-accent); }
.unit { font-size: 15px; color: var(--sp-text2); }
.line { font-size: 13px; color: var(--sp-text2); }
.line b { color: var(--sp-text); }
.chart { margin-top: auto; }
.guess {
  flex-shrink: 0; height: 18px; padding: 0 6px; border-radius: 5px; font-size: 11px; font-weight: 600;
  display: inline-flex; align-items: center; color: var(--sp-text2); border: 1px dashed var(--sp-line-strong);
}
.list { padding: 14px 28px 12px; display: flex; flex-direction: column; min-width: 0; }
.row { display: grid; grid-template-columns: 112px minmax(0, 1fr) 110px 300px; align-items: center; min-height: 56px; border-bottom: 1px solid var(--sp-line-soft); }
.row.head { min-height: 40px; font-size: 12.5px; font-weight: 500; color: var(--sp-text3); }
.right { text-align: right; }
.time { font-size: 13px; color: var(--sp-text2); }
.who { display: flex; flex-direction: column; gap: 3px; min-width: 0; padding-right: 16px; }
.name-line { display: flex; align-items: center; gap: 6px; min-width: 0; }
.name { color: var(--sp-text); font-weight: 600; text-decoration: none; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.name:hover { color: var(--sp-accent); }
.star { color: var(--sp-gold); font-size: 13px; }
.sub { font-size: 12px; color: var(--sp-text3); }
.price { font-weight: 600; }
.known { padding-left: 28px; display: flex; align-items: center; gap: 8px; min-width: 0; }
.chip { height: 24px; padding: 0 9px; border-radius: 7px; display: inline-flex; align-items: center; font-size: 12.5px; font-weight: 600; white-space: nowrap; }
.chip.accent { background: var(--sp-accent-soft); color: var(--sp-accent); }
.chip.ok { background: var(--sp-ok-soft); color: var(--sp-ok); }
.chip.warn { background: var(--sp-warn-soft); color: var(--sp-warn); }
.chip.muted { background: var(--sp-surface2); color: var(--sp-text3); }
.note { font-size: 12.5px; color: var(--sp-text3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.more {
  align-self: center; margin-top: 10px; height: 34px; padding: 0 14px; border-radius: 9px; border: none; background: transparent;
  color: var(--sp-accent); font: inherit; font-size: 13px; font-weight: 600; cursor: pointer; display: inline-flex; align-items: center; gap: 6px;
}
.more:hover { background: var(--sp-accent-soft); }
.more svg { transition: transform 200ms ease; }
.more .flip { transform: rotate(180deg); }
@media (max-width: 1100px) {
  .fills { grid-template-columns: 1fr; }
  .summary { border-right: none; border-bottom: 1px solid var(--sp-line-soft); padding: 24px; }
  .list { padding: 8px 16px 12px; }
  .row { grid-template-columns: 96px minmax(0, 1fr) 90px; }
  .known { grid-column: 2 / -1; padding: 0 0 10px; }
  .row.head .known { display: none; }
}
</style>
