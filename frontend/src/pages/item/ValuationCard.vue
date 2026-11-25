<script setup lang="ts">
// 详情页「当前价位」卡片（价位 spec §7.2，视觉稿 2026-09-27 定稿）：一年里的位置、和大盘比、
// 卖出去能拿到多少。三项差价跟着「我的求购」卡片切换的那张单重算（规则和后端一样）。
import { computed } from 'vue'
import type { ItemValuation, Order } from '@/api/types'
import EChart from '@/charts/EChart.vue'
import RangeRail from '@/components/RangeRail.vue'
import { relLineOption } from '@/charts/options'
import { useTheme } from '@/composables/useTheme'
import { money, plainPct, ratioPct, signedNum } from '@/format'
import { adviceTone, marginTone, rangeMarks, waterfall } from '../overview/valueLogic'
import { chips, marginsFor, relSeries } from './valueLogic'

const props = defineProps<{ v: ItemValuation; order: Order | null }>()
const { tokens } = useTheme()

const picked = computed(() => {
  const o = props.order ?? { my_price: null, top1_price: null, rank: null }
  return { o, v: { ...props.v, ...marginsFor(props.v, o) } }
})
const tags = computed(() => chips(props.v))
const marks = computed(() => rangeMarks(props.v))
const rangeNote = computed(() => {
  const v = props.v
  if (v.cur == null || v.hi == null || v.lo == null) return `日线只有 ${v.bars} 根，不到 60 根不算位置。`
  return `离一年高点 ${ratioPct(v.cur / v.hi - 1).text}，离一年低点 ${ratioPct(v.cur / v.lo - 1).text}。`
})
const series = computed(() => relSeries(props.v.series90))
const hasSeries = computed(() => series.value.item.some((x) => x != null))
const chart = computed(() => relLineOption(series.value, tokens.value))
const ex30 = computed(() => signedNum(props.v.ex30 == null ? null : props.v.ex30 * 100, 1))
const relTone = computed(() => (props.v.rel === '跑输' ? 'warn' : props.v.rel === '跑赢' ? 'ok' : 'plain'))
const water = computed(() => waterfall(picked.value.v, picked.value.o, props.v.fees))
// 建议跟着「我的求购」卡片选中的那张单（每行的 value 里都带着）；没有单时用卡片自己的
const advice = computed(() => props.order?.value?.advice ?? props.v.advice ?? null)
const take = computed(() => {
  const { o, v } = picked.value
  if (o.rank === 1) return { text: '已是第 1', tone: 'muted', sub: '按我的价算的差价见下面' }
  return {
    text: ratioPct(v.m_top).text,
    tone: marginTone(v.m_top, props.v.target_margin),
    sub: o.top1_price == null ? '无榜首价' : `抢到第 1（出价 ${money(o.top1_price + 0.01)}）的扣费差价`,
  }
})
</script>

<template>
  <section class="card val">
    <header class="head">
      <h2>当前价位</h2>
      <div class="chips">
        <span v-for="c in tags" :key="c.label" class="chip" :class="c.tone">{{ c.label }}</span>
      </div>
      <div class="grow" />
      <span class="meta">悠悠日线 {{ v.bars }} 根 · 估值于 {{ v.as_of.slice(5) }} · {{ v.snapshot_date ? `${v.snapshot_date.slice(5)} 价格快照` : '无价格快照' }}</span>
    </header>

    <div class="cols">
      <div class="col">
        <span class="label">一年里的位置</span>
        <div class="big-row">
          <span class="big display num">{{ plainPct(v.pct) }}</span>
          <span class="big-sub">一年分位 · 收盘价低于当前价的天数占比</span>
        </div>
        <RangeRail v-if="marks.length" :marks="marks" size="sm" />
        <p class="note">{{ rangeNote }}</p>
      </div>

      <div class="col bordered">
        <span class="label">和大盘比 · 近 90 天（起点都记作 100）</span>
        <div class="big-row">
          <span class="big display num" :class="relTone">{{ ex30 }}</span>
          <span class="big-sub">个点 · 30 日相对大盘{{ v.rel ? `（${v.rel}）` : '' }}</span>
        </div>
        <EChart v-if="hasSeries" :option="chart" :height="104" label="近 90 天走势，和大盘对比" />
        <div class="legend">
          <span><i class="solid" />它 {{ ratioPct(v.c90).text }}</span>
          <span><i class="dashed" />大盘 {{ ratioPct(v.b90).text }}</span>
        </div>
        <p class="note">30 日：它 {{ ratioPct(v.c30).text }}，大盘 {{ ratioPct(v.b30).text }}。整个市场都在跌时，「一年低位」不代表它自己便宜。</p>
      </div>

      <div class="col bordered">
        <span class="label">卖出去能拿到多少</span>
        <div class="big-row">
          <span class="big display num" :class="take.tone">{{ take.text }}</span>
          <span class="big-sub">{{ take.sub }}</span>
        </div>
        <div v-if="water.length" class="water">
          <div v-for="r in water" :key="r.label" class="wrow" :class="{ strong: r.strong }">
            <span :class="r.tone">{{ r.label }}</span>
            <span class="num" :class="r.tone">{{ r.value }}</span>
          </div>
        </div>
        <p v-else class="note">当天无价格快照，无法计算差价。</p>
      </div>
    </div>

    <div v-if="advice" class="advice" :class="adviceTone(advice.kind)">
      <span class="label">建议</span>
      <b>{{ advice.label }}</b>
      <span class="rule">{{ advice.rule }}</span>
    </div>

    <p class="foot">按过去一年悠悠日线和最新价格快照、按固定规则算出来的参考；Valve 改规则这类政策风险算不进去。7 天坏情况 = 过去一年里「7 天涨跌」最差的 10%。<template v-if="advice">建议只用回测通过的规则，回测只有一年数据、只经历过下跌行情。</template></p>
  </section>
</template>

<style scoped>
.val { padding: 24px 28px; display: flex; flex-direction: column; gap: 22px; }
.head { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
h2 { margin: 0; font-size: 18px; font-weight: 700; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { height: 24px; padding: 0 9px; border-radius: 7px; font-size: 12.5px; font-weight: 600; display: inline-flex; align-items: center; background: var(--sp-surface2); color: var(--sp-text2); }
.chip.accent { background: var(--sp-accent-soft); color: var(--sp-accent); }
.chip.warn { background: var(--sp-warn-soft); color: var(--sp-warn); }
.chip.ok { background: var(--sp-ok-soft); color: var(--sp-ok); }
.chip.down { background: color-mix(in srgb, var(--sp-down) 12%, transparent); color: var(--sp-down); }
.chip.up { background: color-mix(in srgb, var(--sp-up) 12%, transparent); color: var(--sp-up); }
.grow { flex: 1; }
.meta { font-size: 12px; color: var(--sp-text3); }
.cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1.15fr); }
.col { display: flex; flex-direction: column; gap: 14px; padding-right: 32px; min-width: 0; }
.col.bordered { padding-left: 32px; border-left: 1px solid var(--sp-line-soft); }
.col:last-child { padding-right: 0; }
.label { font-size: 12.5px; color: var(--sp-text3); }
.big-row { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.big { font-size: 40px; font-weight: 600; letter-spacing: -0.02em; line-height: 1; }
.big.accent { color: var(--sp-accent); }
.big.warn { color: var(--sp-warn); }
.big.ok { color: var(--sp-ok); }
.big.loss { color: var(--sp-loss); }
.big.muted { color: var(--sp-text3); font-size: 28px; }
.big-sub { font-size: 13px; color: var(--sp-text2); }
.note { margin: 0; font-size: 13px; color: var(--sp-text2); line-height: 1.6; }
.legend { display: flex; gap: 16px; font-size: 12px; color: var(--sp-text2); }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.legend i { width: 14px; height: 0; border-top: 2px solid var(--sp-accent); }
.legend i.dashed { border-top: 2px dashed var(--sp-text3); }
.water { display: flex; flex-direction: column; gap: 2px; margin-top: 4px; }
.wrow { display: flex; justify-content: space-between; gap: 16px; font-size: 13px; padding: 3px 0; }
.wrow.strong { font-weight: 600; border-top: 1px solid var(--sp-line-soft); }
.plain { color: var(--sp-text); }
.soft { color: var(--sp-text2); }
.loss { color: var(--sp-loss); font-weight: 600; }
.advice {
  padding: 14px 18px; border-radius: 12px; display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  border: 1px solid var(--sp-line); background: var(--sp-surface2);
}
.advice .label { font-size: 12.5px; color: var(--sp-text3); }
.advice b { font-weight: 700; color: var(--sp-text); }
.advice .rule { font-size: 13px; color: var(--sp-text2); }
.advice.warn { background: var(--sp-warn-soft); border-color: var(--sp-warn); }
.advice.warn b { color: var(--sp-warn); }
.advice.accent { background: var(--sp-accent-soft); border-color: var(--sp-accent-soft-strong); }
.advice.accent b { color: var(--sp-accent); }
.advice.muted b { color: var(--sp-text3); }
.foot { margin: 0; font-size: 12.5px; color: var(--sp-text3); }
@media (max-width: 1100px) {
  .cols { grid-template-columns: 1fr; gap: 24px; }
  .col, .col.bordered { padding: 0; border-left: none; }
}
</style>
