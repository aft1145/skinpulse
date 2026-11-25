<script setup lang="ts">
// 大盘主卡片（视觉稿第 2 版）：左边是指数、今天的涨跌、7 天 / 30 天 / 1 年和一年区间里的位置；
// 右边是最近 25 小时的走势。
import { computed } from 'vue'
import type { MarketData } from '@/api/types'
import EChart from '@/charts/EChart.vue'
import { areaLineOption } from '@/charts/options'
import { useTheme } from '@/composables/useTheme'
import { arrowPct, clock, money, signedNum } from '@/format'
import { yearRange } from './logic'

const props = defineProps<{ index: NonNullable<MarketData['index']>; kline: MarketData['kline']; tz: string }>()
const { tokens } = useTheme()
const today = computed(() => arrowPct(props.index.diff_pct, 2))
const chips = computed(() => {
  const s = props.kline?.signals
  return [
    { label: '7 天', ...arrowPct(s?.change_7d_pct) },
    { label: '30 天', ...arrowPct(s?.change_30d_pct) },
    { label: '1 年', ...arrowPct(props.kline?.year_change_pct) },
  ]
})
const range = computed(() => (props.kline ? yearRange(props.kline.bars, props.tz) : null))
const hist = computed(() => props.index.history)
const lohi = computed(() => {
  const v = hist.value.map((h) => h[1])
  return v.length ? { lo: Math.min(...v), hi: Math.max(...v) } : null
})
const option = computed(() => areaLineOption(hist.value, props.tz, tokens.value))
</script>

<template>
  <section class="hero card">
    <div class="left">
      <div class="label small">
        <b>CS2 大盘指数</b>
        <span class="muted">SteamDT · {{ clock(index.updated_at, tz) }} 更新</span>
      </div>
      <div class="value-block">
        <h1>
          <span class="sr-only">大盘</span>
          <span class="display value">{{ money(index.value) }}</span>
          <span class="num pill" :class="today.dir">{{ today.text }}</span>
        </h1>
        <span class="small text2">比昨天 <b class="num" :class="today.dir">{{ signedNum(index.diff) }}</b></span>
      </div>
      <div class="chips">
        <span v-for="c in chips" :key="c.label" class="chip">{{ c.label }}<b class="num" :class="c.dir">{{ c.text }}</b></span>
      </div>
      <div v-if="range" class="range">
        <div class="row small muted">
          <span>一年区间里的位置</span>
          <span>离一年高点 <b class="num down">{{ signedNum(range.fromHigh, 1) }}%</b></span>
        </div>
        <div class="track" role="img" :aria-label="`现在处在一年区间的 ${range.pos}% 位置`">
          <div class="rail" />
          <div class="knob" :style="{ left: `${range.pos}%` }" />
        </div>
        <div class="row num muted tiny">
          <span>低 {{ money(range.lo) }} · {{ range.loDate }}</span>
          <span>高 {{ money(range.hi) }} · {{ range.hiDate }}</span>
        </div>
      </div>
    </div>

    <div class="right">
      <div class="row">
        <b class="h">最近 25 小时</b>
        <span v-if="lohi" class="muted tiny">最低 {{ money(lohi.lo) }} · 最高 {{ money(lohi.hi) }}</span>
      </div>
      <EChart v-if="hist.length > 1" :option="option" :height="244" label="最近 25 小时的大盘指数" />
      <div v-else class="empty muted">小时数据暂时取不到</div>
    </div>
  </section>
</template>

<style scoped>
.hero {
  display: grid; grid-template-columns: 440px minmax(0, 1fr); overflow: hidden; border-radius: 22px;
  background-image: radial-gradient(520px 260px at 0% 0%, var(--sp-accent-soft), transparent 70%);
}
.left { padding: 30px 32px 28px; display: flex; flex-direction: column; gap: 18px; border-right: 1px solid var(--sp-line-soft); }
.right { padding: 26px 28px 16px 24px; display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.label { display: flex; align-items: center; gap: 10px; }
.small { font-size: 13px; }
.tiny { font-size: 11.5px; }
.text2 { color: var(--sp-text2); }
.value-block { display: flex; flex-direction: column; gap: 10px; }
h1 { margin: 0; display: flex; align-items: center; gap: 14px; }
.value { font-size: 60px; font-weight: 600; letter-spacing: -0.03em; line-height: 1; }
.pill { font-size: 15px; font-weight: 600; padding: 5px 10px; border-radius: 8px; background: var(--sp-bar); color: var(--sp-text2); }
.pill.down { color: var(--sp-down); background: color-mix(in srgb, var(--sp-down) 12%, transparent); }
.pill.up { color: var(--sp-up); background: color-mix(in srgb, var(--sp-up) 12%, transparent); }
b.down, .chip b.down { color: var(--sp-down); }
b.up, .chip b.up { color: var(--sp-up); }
.chips { display: flex; gap: 8px; flex-wrap: wrap; }
.chip { display: inline-flex; align-items: baseline; gap: 6px; padding: 6px 10px; border-radius: 8px; background: var(--sp-seg-bg); font-size: 12.5px; color: var(--sp-text2); }
.range { margin-top: auto; display: flex; flex-direction: column; gap: 8px; padding-top: 16px; border-top: 1px solid var(--sp-line-soft); }
.row { display: flex; justify-content: space-between; align-items: baseline; }
.track { position: relative; height: 18px; }
.rail { position: absolute; left: 0; right: 0; top: 6px; height: 6px; border-radius: 3px; background: linear-gradient(90deg, var(--sp-bar), var(--sp-line-strong)); }
.knob {
  position: absolute; top: 1px; width: 16px; height: 16px; transform: translateX(-50%); border-radius: 8px;
  background: var(--sp-surface); border: 3px solid var(--sp-accent); box-shadow: 0 0 0 4px var(--sp-accent-soft);
}
.h { font-size: 14px; }
.empty { height: 244px; display: flex; align-items: center; justify-content: center; }
@media (max-width: 1100px) { .hero { grid-template-columns: 1fr; } .left { border-right: none; border-bottom: 1px solid var(--sp-line-soft); } }
</style>
