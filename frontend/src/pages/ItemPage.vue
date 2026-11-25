<script setup lang="ts">
// 饰品详情 /item/:templateId（spec §4.2）：「我在这件饰品上的求购排第几、还差多少、价格处在什么位置？」
// 打开时取一次 /api/item，不轮询：行情走服务端缓存（价格 5 分钟、K 线 60 分钟），轮询也拿不到新东西。
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ChevronRight, ExternalLink } from '@lucide/vue'
import { ApiError, fetchItem } from '@/api/client'
import type { ItemDetail } from '@/api/types'
import { useMeta } from '@/composables/useMeta'
import MyOrderCard from './item/MyOrderCard.vue'
import PriceChart from './item/PriceChart.vue'
import PriceCompare from './item/PriceCompare.vue'
import ValuationCard from './item/ValuationCard.vue'

const route = useRoute()
const { meta } = useMeta()
const tz = computed(() => meta.value?.timezone ?? 'Asia/Shanghai')
const data = ref<ItemDetail | null>(null)
const error = ref<{ status: number; text: string } | null>(null)
const loading = ref(true)

async function load(id: number) {
  loading.value = true
  error.value = null
  try {
    data.value = await fetchItem(id)
  } catch (e) {
    data.value = null
    error.value = e instanceof ApiError ? { status: e.status, text: e.message } : { status: 0, text: String(e) }
  } finally {
    loading.value = false
  }
}
const id = computed(() => Number(route.params.templateId))
watch(id, (v) => (Number.isFinite(v) ? load(v) : (error.value = { status: 404, text: '' })), { immediate: true })

// 多张单时选中的那张；换饰品或数据刷新后选中的单不在了，就回到排名最好的那张
const pick = ref('')
watch(data, (d) => {
  if (d && !d.orders.some((o) => o.purchase_no === pick.value)) pick.value = d.orders[0]?.purchase_no ?? ''
})
const picked = computed(() => data.value?.orders.find((o) => o.purchase_no === pick.value) ?? null)
const myPrice = computed(() => picked.value?.my_price ?? null)
const marketNote = computed(() => {
  const m = data.value?.market
  if (!m) return null
  if (!m.configured) return '未配置 SteamDT API key，仅显示自己的求购。请在终端运行 steamdt-key 配置。'
  if (!m.mapped) return '这件饰品不在 SteamDT 饰品目录中（或目录尚未同步：请在终端运行 steamdt-items），无法查询多平台行情。'
  return null
})
</script>

<template>
  <main class="page">
    <template v-if="loading && !data">
      <div class="skeleton" style="width: 360px; height: 42px" />
      <div class="grid2">
        <div class="skeleton" style="height: 560px; border-radius: 18px" />
        <div class="skeleton" style="height: 560px; border-radius: 18px" />
      </div>
    </template>

    <div v-else-if="error" class="card error rise">
      <b>{{ error.status === 404 ? '未找到这件饰品' : '详情加载失败' }}</b>
      <span class="muted">{{ error.status === 404 ? '库中无它的求购单，也无 SteamDT 映射。' : error.text }}</span>
      <RouterLink to="/" class="btn press">回到求购总览</RouterLink>
    </div>

    <template v-else-if="data">
      <section class="hero rise" style="--i: 0">
        <nav class="crumbs small" aria-label="面包屑">
          <RouterLink to="/">求购总览</RouterLink>
          <ChevronRight :size="12" aria-hidden="true" />
          <span aria-current="page">饰品详情</span>
        </nav>
        <div class="title-row">
          <div class="names">
            <h1><span v-if="data.star" class="star" aria-label="刀或手套">★</span>{{ data.title }}</h1>
            <div class="sub small">
              <template v-if="data.stattrak"><span class="stattrak">StatTrak™</span><span class="muted">·</span></template>
              <template v-if="data.tag"><span>{{ data.tag }}</span><span v-if="data.market_hash_name" class="muted">·</span></template>
              <span v-if="data.market_hash_name" class="num muted en">{{ data.market_hash_name }}</span>
            </div>
          </div>
          <a class="btn press" :href="data.youpin_url" target="_blank" rel="noopener noreferrer">在悠悠打开<ExternalLink :size="14" /></a>
        </div>
      </section>

      <section class="grid2">
        <MyOrderCard v-if="data.orders.length" v-model:pick="pick" :orders="data.orders" :tz="tz" :days="data.days" class="rise" style="--i: 1" />
        <article v-else class="card empty-mine rise" style="--i: 1">
          <h2>我的求购</h2>
          <p class="muted">这件饰品当前无求购单。</p>
        </article>

        <PriceChart
          v-if="data.kline"
          :kline="data.kline"
          :weekly="data.weekly"
          :my-price="myPrice"
          :tz="tz"
          title="价格走势"
          subtitle="全平台日线 · SteamDT · 虚线是我的求购价"
          class="rise"
          style="--i: 2"
        />
        <article v-else class="card note-card rise" style="--i: 2">
          <h2>价格走势</h2>
          <p class="muted">{{ marketNote }}</p>
        </article>
      </section>

      <ValuationCard v-if="data.valuation" :v="data.valuation" :order="picked" class="rise" style="--i: 3" />
      <!-- 还没估值时也占一行说明，别让人以为这块没上线（用户 2026-09-28：重启后等补算那几分钟找不到卡片） -->
      <article v-else-if="data.market.mapped" class="card note-card rise" style="--i: 3">
        <h2>当前价位</h2>
        <p class="muted">暂无这件饰品的估值：每天 00:30 计算一次，服务启动时会先补算（约 12 分钟），完成后刷新本页即可。</p>
      </article>

      <PriceCompare v-if="data.prices" :prices="data.prices" :my-price="myPrice" :tz="tz" class="rise" style="--i: 4" />
    </template>
  </main>
</template>

<style scoped>
.page { max-width: 1440px; margin: 0 auto; padding: 36px 56px 64px; display: flex; flex-direction: column; gap: 32px; }
.small { font-size: 13px; }
.hero { display: flex; flex-direction: column; gap: 14px; }
.crumbs { display: flex; align-items: center; gap: 8px; color: var(--sp-text3); }
.crumbs a { color: var(--sp-text2); text-decoration: none; }
.crumbs a:hover { color: var(--sp-text); }
.title-row { display: flex; align-items: flex-end; gap: 20px; }
.names { display: flex; flex-direction: column; gap: 10px; flex: 1; min-width: 0; }
h1 { margin: 0; font-size: 34px; line-height: 1.2; font-weight: 700; letter-spacing: -0.02em; }
.star { color: var(--sp-gold); margin-right: 8px; }
.sub { display: flex; align-items: center; gap: 10px; color: var(--sp-text2); flex-wrap: wrap; }
.stattrak { color: var(--sp-stattrak); font-weight: 600; }
.en { font-size: 12.5px; }
.grid2 { display: grid; grid-template-columns: 380px minmax(0, 1fr); gap: 20px; align-items: stretch; }
.btn {
  height: 40px; padding: 0 16px; border-radius: 10px; border: 1px solid var(--sp-line-strong); background: var(--sp-surface);
  color: var(--sp-text); font: inherit; font-weight: 500; display: inline-flex; align-items: center; gap: 8px; cursor: pointer; text-decoration: none; white-space: nowrap;
}
.btn:hover { background: var(--sp-surface2); }
.error { padding: 28px; display: flex; flex-direction: column; gap: 10px; align-items: flex-start; }
.empty-mine, .note-card { padding: 26px 28px; display: flex; flex-direction: column; gap: 12px; }
.empty-mine h2, .note-card h2 { margin: 0; font-size: 18px; font-weight: 700; }
.empty-mine p, .note-card p { margin: 0; }
@media (max-width: 1100px) { .page { padding: 32px 24px 48px; } .grid2 { grid-template-columns: 1fr; } }
</style>
