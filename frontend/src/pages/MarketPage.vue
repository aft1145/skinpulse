<script setup lang="ts">
// 大盘 /market（spec §4.4）：「CS2 大盘今天怎么样？」
// 打开时取一次 /api/market，不轮询：大盘接口配额很紧（4005），指数最多 10 分钟取一次、日线 6 小时，
// 切换范围和缩放都在前端做，页面绝不因为用户操作额外去请求。
import { computed, onMounted, ref } from 'vue'
import { ApiError, fetchMarket } from '@/api/client'
import type { MarketData } from '@/api/types'
import { useMeta } from '@/composables/useMeta'
import { clock, money } from '@/format'
import PriceChart from './item/PriceChart.vue'
import MarketHero from './market/MarketHero.vue'

const { meta } = useMeta()
const tz = computed(() => meta.value?.timezone ?? 'Asia/Shanghai')
const data = ref<MarketData | null>(null)
const error = ref<string | null>(null)

async function load() {
  error.value = null
  try {
    data.value = await fetchMarket()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : String(e)
  }
}
onMounted(load)

const subtitle = computed(() => {
  const s = data.value?.kline?.signals
  if (!s) return 'SteamDT 全市场日线'
  const gap = s.ma_gap_pct == null ? '' : `（MA7 比 MA30 ${s.ma_gap_pct >= 0 ? '高' : '低'} ${Math.abs(s.ma_gap_pct).toFixed(1)}%）`
  return `MA7 ${money(s.ma7)} · MA30 ${money(s.ma30)}${s.trend ? ` · ${s.trend}${gap}` : ''}`
})
</script>

<template>
  <main class="page">
    <div v-if="error" class="card error rise">
      <b>大盘加载失败</b>
      <span class="muted">{{ error }}</span>
      <button type="button" class="btn press" @click="load">重试</button>
    </div>

    <template v-else-if="!data">
      <div class="skeleton" style="height: 320px; border-radius: 22px" />
      <div class="skeleton" style="height: 520px; border-radius: 18px" />
    </template>

    <div v-else-if="!data.configured" class="card error rise">
      <b>未配置 SteamDT</b>
      <span class="muted">大盘数据来自 SteamDT。请在终端运行 steamdt-key 配置 API key。</span>
    </div>

    <template v-else>
      <MarketHero v-if="data.index && data.index.value != null" :index="data.index" :kline="data.kline" :tz="tz" class="rise" style="--i: 0" />
      <div v-else class="card error rise" style="--i: 0">
        <b>大盘指数暂时取不到</b>
        <span class="muted">{{ data.index?.error }}</span>
      </div>
      <p v-if="data.index?.stale" class="warn small">指数刷新失败，显示的是 {{ clock(data.index.fetched_at, tz) }} 取到的数据（{{ data.index.error }}）</p>

      <PriceChart
        v-if="data.kline"
        :kline="data.kline"
        :weekly="data.weekly"
        :my-price="null"
        :tz="tz"
        title="日线"
        :subtitle="subtitle"
        hide-stats
        default-win="1y"
        class="rise"
        style="--i: 1"
      />

      <p class="muted small foot">
        指数最多 10 分钟取一次，日线最多 6 小时取一次（SteamDT<template v-if="data.kline?.fetched_at">，日线取于 {{ clock(data.kline.fetched_at, tz) }}</template>）。大盘接口配额很紧，切换范围和缩放都不会重新取数。
      </p>
    </template>
  </main>
</template>

<style scoped>
.page { max-width: 1440px; margin: 0 auto; padding: 36px 56px 64px; display: flex; flex-direction: column; gap: 32px; }
.small { font-size: 12.5px; }
.warn { margin: -16px 0 0; color: var(--sp-warn); }
.foot { margin: 0; }
.error { padding: 28px; display: flex; flex-direction: column; gap: 10px; align-items: flex-start; }
.btn {
  height: 40px; padding: 0 16px; border-radius: 10px; border: 1px solid var(--sp-line-strong); background: var(--sp-surface);
  color: var(--sp-text); font: inherit; font-weight: 500; cursor: pointer;
}
@media (max-width: 1100px) { .page { padding: 32px 24px 48px; } }
</style>
