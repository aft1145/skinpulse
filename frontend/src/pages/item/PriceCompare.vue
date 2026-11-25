<script setup lang="ts">
// 各平台比价（spec §4.2）：条形 = 在售最低价，gold 圆点 = 最高求购价，竖线 = 我的求购价。
// 用 HTML 画而不是 ECharts：每行旁边就是精确数字，读屏能按行读出来（偏离记录见 plan）。
import { computed } from 'vue'
import type { ItemDetail } from '@/api/types'
import { clock, money } from '@/format'
import { compareRows } from './logic'

const props = defineProps<{ prices: NonNullable<ItemDetail['prices']>; myPrice: number | null; tz: string }>()
const view = computed(() => compareRows(props.prices.rows, props.myPrice))
</script>

<template>
  <section class="card compare">
    <div class="top">
      <div class="names">
        <h2>各平台比价</h2>
        <span class="muted small">
          条形是在售最低价，圆点是最高求购价<template v-if="myPrice != null">，竖线是我的求购价 {{ money(myPrice) }}</template>
        </span>
      </div>
      <span class="muted tiny">SteamDT · 取于 {{ clock(prices.fetched_at, tz) }}</span>
    </div>
    <p v-if="prices.stale" class="warn small">刷新失败，显示的是 {{ clock(prices.fetched_at, tz) }} 取到的数据（{{ prices.error }}）</p>
    <p v-if="!prices.rows.length" class="muted empty">
      比价暂时取不到<template v-if="prices.error">：{{ prices.error }}</template>
    </p>

    <div v-else class="table" role="table" aria-label="各平台比价">
      <div class="row head" role="row">
        <span role="columnheader"><span class="sr-only">平台</span></span>
        <span role="columnheader"><span class="sr-only">价格位置</span></span>
        <span role="columnheader" class="muted tiny right">在售最低 · 件数</span>
        <span role="columnheader" class="muted tiny right">最高求购 · 单数</span>
      </div>
      <div v-for="r in view.rows" :key="r.platform" class="row" role="row">
        <span role="cell" :class="{ bold: r.youpin }">{{ r.name }}</span>
        <div class="axis" role="cell" aria-hidden="true">
          <div v-if="r.sellPct != null" class="bar" :class="{ mine: r.youpin }" :style="{ width: `${r.sellPct}%` }" />
          <div v-if="r.bidPct != null" class="dot" :style="{ left: `${r.bidPct}%` }" />
          <div v-if="view.myPct != null" class="me" :style="{ left: `${view.myPct}%` }" />
        </div>
        <span role="cell" class="right pair"><b class="num">{{ r.sellText }}</b><small class="muted">{{ r.sellSub }}</small></span>
        <span role="cell" class="right pair"><b class="num" :class="{ none: r.bid == null }">{{ r.bidText }}</b><small class="muted">{{ r.bidSub }}</small></span>
      </div>
    </div>
    <p class="muted small foot">Steam 价含 15% 手续费，余额无法提现，与国内平台不是同一市场，不宜直接比较。「—」表示该平台无此项数据，不是 0 元。</p>
  </section>
</template>

<style scoped>
.compare { padding: 24px 28px 26px; display: flex; flex-direction: column; gap: 20px; }
.top { display: flex; align-items: flex-end; gap: 16px; }
.names { display: flex; flex-direction: column; gap: 4px; flex: 1; }
h2 { margin: 0; font-size: 18px; font-weight: 700; }
.small { font-size: 12.5px; }
.tiny { font-size: 12px; }
.warn { margin: 0; color: var(--sp-warn); }
.empty { margin: 0; padding: 24px 0; }
.table { display: flex; flex-direction: column; }
.row { display: grid; grid-template-columns: 110px minmax(0, 1fr) 200px 200px; gap: 24px; align-items: center; height: 50px; border-top: 1px solid var(--sp-line-soft); }
.row.head { height: auto; padding-bottom: 8px; border-top: none; }
.bold { font-weight: 700; }
.right { text-align: right; }
.axis { position: relative; height: 50px; }
.bar { position: absolute; left: 0; top: 19px; height: 12px; border-radius: 0 6px 6px 0; background: var(--sp-bar); }
.bar.mine { background: var(--sp-accent-soft-strong); }
.dot { position: absolute; top: 18px; width: 14px; height: 14px; transform: translateX(-50%); border-radius: 7px; background: var(--sp-surface); border: 3px solid var(--sp-gold); }
.me { position: absolute; top: 8px; bottom: 8px; width: 2px; border-radius: 1px; background: var(--sp-accent); }
.pair { display: flex; align-items: center; justify-content: flex-end; gap: 8px; }
.pair b { font-weight: 600; }
.pair b.none { color: var(--sp-text3); }
.pair small { font-size: 12px; width: 44px; text-align: right; }
.foot { margin: 0; }
@media (max-width: 1200px) { .row { grid-template-columns: 90px minmax(0, 1fr) 150px 150px; gap: 16px; } }
</style>
