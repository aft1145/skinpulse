<script setup lang="ts">
// 选品结果表（价位 spec §9b.4，视觉稿 2026-10-09）：每行一个结论和触发它的那句话。点饰品进详情页。
import { RouterLink } from 'vue-router'
import type { PickRow } from '@/api/types'
import { money, plainPct, ratioPct } from '@/format'
import { VERDICTS, moveNotes } from './logic'

defineProps<{ rows: PickRow[]; reboundWindow?: number; reboundMax?: number }>()
const tone = (v: PickRow['verdict']) => VERDICTS.find((x) => x.key === v)?.tone ?? 'muted'
const posLabel = (r: PickRow) => r.pct_label?.replace('一年', '') ?? ''
</script>

<template>
  <div class="pick-table" role="table" aria-label="选品结果">
    <div class="row head" role="row">
      <span role="columnheader">饰品</span>
      <span role="columnheader" class="r">悠悠在售 / 最高求购</span>
      <span role="columnheader">一年里的位置</span>
      <span role="columnheader" class="r">7 天坏情况</span>
      <span role="columnheader" class="r">上限</span>
      <span role="columnheader" class="r">建议出价</span>
      <span role="columnheader">结论</span>
    </div>
    <div v-for="r in rows" :key="r.template_id" class="row" role="row">
      <div class="name" role="cell">
        <div class="name-line">
          <span v-if="r.star" class="star" aria-label="刀或手套">★</span>
          <RouterLink :to="`/item/${r.template_id}`" class="title">{{ r.title }}</RouterLink>
        </div>
        <span class="sub">
          <span v-if="r.mine" class="mine">已挂单</span>{{ [r.stattrak ? 'StatTrak™' : '', r.tag ?? '', r.liq ?? ''].filter(Boolean).join(' · ') || ' ' }}
        </span>
      </div>
      <div class="stack r" role="cell">
        <span class="num">{{ money(r.yp_sell) }}<small v-if="r.yp_count"> {{ r.yp_count }} 件</small></span>
        <span class="sub">求购 <span class="num">{{ money(r.yp_bid) }}</span></span>
      </div>
      <div class="pos" role="cell">
        <template v-if="r.pct != null">
          <div class="rail"><span class="dot" :style="{ left: `${r.pct * 100}%` }" /></div>
          <span class="sub"><b class="num">{{ plainPct(r.pct) }}</b> · {{ posLabel(r) }}</span>
          <span v-for="m in moveNotes(r, reboundWindow, reboundMax)" :key="m.text" class="sub move" :class="{ warn: m.warn }">{{ m.text }}</span>
        </template>
        <span v-else class="sub">日线 {{ r.bars }} 根</span>
      </div>
      <div class="stack r" role="cell">
        <span class="num" :class="{ loss: r.q10 != null && r.q10 < -0.1 }">{{ ratioPct(r.q10).text }}</span>
      </div>
      <div class="stack r" role="cell"><span class="num">{{ money(r.cap) }}</span></div>
      <div class="stack r" role="cell">
        <template v-if="r.bid != null">
          <b class="num">{{ money(r.bid) }}</b>
          <span class="sub">坏情况后 <span class="ok">{{ ratioPct(r.margin).text }}</span></span>
        </template>
        <span v-else class="sub">—</span>
      </div>
      <div class="verdict" role="cell">
        <span class="pill" :class="tone(r.verdict)">{{ r.label }}</span>
        <span class="why">{{ r.rule }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pick-table { overflow-x: auto; }
.row {
  display: grid; grid-template-columns: minmax(220px, 1.4fr) 140px 150px 100px 90px 130px minmax(260px, 1.4fr);
  gap: 16px; align-items: center; padding: 12px 14px; border-bottom: 1px solid var(--sp-line-soft); font-size: 13.5px; min-width: 1120px;
}
.row:last-child { border-bottom: 0; }
.row.head { font-size: 12px; color: var(--sp-text3); padding-block: 12px; }
.r { text-align: right; }
.stack { display: flex; flex-direction: column; gap: 3px; }
.stack.r { align-items: flex-end; }
.name { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.name-line { display: flex; align-items: center; gap: 6px; min-width: 0; }
.title { color: var(--sp-text); font-weight: 600; text-decoration: none; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.title:hover { color: var(--sp-accent); }
.star { color: var(--sp-gold); font-size: 13px; }
.mine { color: var(--sp-accent); font-weight: 600; margin-right: 8px; }
.sub { font-size: 12px; color: var(--sp-text3); }
small { font-size: 11px; color: var(--sp-text3); margin-left: 4px; }
.loss { color: var(--sp-loss); }
.ok { color: var(--sp-ok); font-weight: 600; }
.pos { display: flex; flex-direction: column; gap: 6px; padding-right: 12px; }
.rail { position: relative; height: 8px; }
.rail::before { content: ''; position: absolute; left: 0; right: 0; top: 2px; height: 4px; border-radius: 2px; background: var(--sp-bar); }
.dot { position: absolute; top: 0; width: 8px; height: 8px; margin-left: -4px; border-radius: 4px; background: var(--sp-text2); }
.verdict { display: flex; align-items: center; gap: 10px; min-width: 0; }
.why { font-size: 12.5px; color: var(--sp-text2); }
.pill { height: 24px; padding: 0 9px; border-radius: 7px; font-size: 12.5px; font-weight: 600; display: inline-flex; align-items: center; white-space: nowrap; flex-shrink: 0; border: 1px solid var(--sp-line); color: var(--sp-text3); }
.pill.accent { background: var(--sp-accent-soft); color: var(--sp-accent); border-color: transparent; }
.pill.info { background: var(--sp-info-soft); color: var(--sp-info); border-color: transparent; }
.pill.warn { background: var(--sp-warn-soft); color: var(--sp-warn); border-color: transparent; }
.move { display: block; }
.move.warn { color: var(--sp-warn); }
</style>
