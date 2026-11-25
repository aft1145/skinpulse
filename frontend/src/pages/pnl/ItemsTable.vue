<script setup lang="ts">
// 单品表（盈亏 spec §7.4）：亏得最多 / 赚得最多、搜索、品类筛选；先列 8 个，点「再显示 50 个」往下加。
// 不用滚动自动加载：表下面还有「最近卖出」，一滚就加载会让人永远翻不到它（2026-11-24 测试实例验收）。
import { refDebounced } from '@vueuse/core'
import { NTooltip } from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { fetchPnlItems } from '@/api/client'
import type { PnlBasis, PnlItem } from '@/api/types'
import SegmentedControl from '@/components/SegmentedControl.vue'
import { flagText, itemSub, signedPercent, signedYuan, tone, whereText, yuan } from './logic'

const props = defineProps<{ account: number | null; basis: PnlBasis; category: string | null; tz: string }>()
const emit = defineEmits<{ clearCategory: [] }>()

const sort = ref<'worst' | 'best'>('worst')
const q = ref('')
const qd = refDebounced(q, 250)
const items = ref<PnlItem[]>([])
const total = ref(0)
const shown = ref(8)
const loading = ref(false)
const PAGE = 50
const sortOptions = [{ key: 'worst' as const, label: '亏损最多' }, { key: 'best' as const, label: '盈利最多' }]

// 条件一变就重新请求；还在路上的旧请求回来时丢掉（按序号认最新的），不能拿旧条件的结果填表（评审 2026-11-24）
let seq = 0
async function load(reset: boolean) {
  if (!reset && loading.value) return
  const my = ++seq
  loading.value = true
  try {
    const r = await fetchPnlItems({ account: props.account, sort: sort.value, basis: props.basis, q: qd.value,
      category: props.category, offset: reset ? 0 : items.value.length, limit: PAGE })
    if (my !== seq) return
    items.value = reset ? r.items : [...items.value, ...r.items]
    total.value = r.total
    if (reset) shown.value = 8
  } finally {
    if (my === seq) loading.value = false
  }
}
load(true)
watch([() => props.account, () => props.basis, () => props.category, sort, qd], () => load(true))

function more() {
  shown.value += PAGE
  if (shown.value > items.value.length && items.value.length < total.value) load(false)
}

const rows = computed(() => items.value.slice(0, shown.value).map((r) => {
  const value = props.basis === 'net' ? r.value_net : r.value_mkt
  const w = whereText(r.where)
  return { ...r, value, w, flag: flagText(r.price_flag), t: tone(r.float),
           pct: r.float != null && r.cost > 0 ? signedPercent(r.float / r.cost) : '—' }
}))
const valueLabel = computed(() => (props.basis === 'net' ? '到手' : '市值'))
const categoryLabel = computed(() => items.value[0]?.category_label ?? '')
</script>

<template>
  <section class="wrap" aria-labelledby="items-h">
    <div class="head">
      <h2 id="items-h">单品</h2>
      <span class="num muted count">{{ total }}</span>
      <button v-if="category" type="button" class="chip press" @click="emit('clearCategory')">
        {{ categoryLabel }} <span aria-hidden="true">×</span><span class="sr-only">清除品类筛选</span>
      </button>
      <div class="grow" />
      <SegmentedControl v-model="sort" :options="sortOptions" label="排序" />
      <label class="search">
        <span class="sr-only">搜索饰品</span>
        <input v-model="q" type="search" placeholder="搜索饰品" />
      </label>
    </div>
    <div class="card table">
      <div class="tr th">
        <span>饰品</span><span>在哪</span><span class="r">持有</span><span class="r">成本</span>
        <span class="r">{{ valueLabel }}</span><span class="r">浮动</span><span class="r">涨跌</span>
      </div>
      <div v-for="r in rows" :key="r.key" class="tr">
        <div class="name">
          <RouterLink v-if="r.template_id != null" :to="`/item/${r.template_id}`" class="title">{{ r.name }}</RouterLink>
          <span v-else class="title plain">{{ r.name }}</span>
          <span class="tiny muted">{{ itemSub(r.sub, r.category_label, r.platforms) }}</span>
        </div>
        <span><span class="where" :class="r.w.key">{{ r.w.text }}</span></span>
        <span class="num r">{{ r.qty }}</span>
        <span class="num r text2">{{ yuan(r.cost) }}</span>
        <span class="num r text2">
          {{ yuan(r.value) }}
          <NTooltip v-if="r.flag">
            <template #trigger><span class="flag" tabindex="0" :aria-label="r.flag" /></template>
            {{ r.flag }}
          </NTooltip>
        </span>
        <span class="num r strong" :class="r.t">{{ signedYuan(r.float) }}</span>
        <span class="num r" :class="r.t">{{ r.pct }}</span>
      </div>
      <div v-if="!rows.length && !loading" class="foot muted">无符合条件的单品</div>
      <div v-if="rows.length" class="foot muted">
        显示 {{ rows.length }} / {{ total }} 个
        <button v-if="rows.length < total" type="button" class="more press" :disabled="loading" @click="more">再显示 {{ Math.min(PAGE, total - rows.length) }} 个</button>
        <button v-else-if="rows.length > 8" type="button" class="more press" @click="shown = 8">收起</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.wrap { display: flex; flex-direction: column; gap: 16px; scroll-margin-top: 80px; }
.head { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
h2 { margin: 0; font-size: 20px; font-weight: 700; letter-spacing: -0.01em; }
.count { font-size: 13px; }
.grow { flex: 1; }
.chip { height: 28px; padding: 0 10px; border-radius: 14px; border: 1px solid var(--sp-line-strong); background: var(--sp-accent-soft); color: var(--sp-accent); font: inherit; font-size: 12.5px; font-weight: 600; cursor: pointer; }
.search input { width: 220px; height: 38px; padding: 0 12px; border-radius: 10px; border: 1px solid var(--sp-line-strong); background: var(--sp-surface); color: var(--sp-text); font: inherit; font-size: 13px; }
.table { overflow-x: auto; }
.tr { display: grid; grid-template-columns: minmax(220px, 3fr) 130px 64px 116px 128px 128px 92px; align-items: center; min-height: 64px; padding: 0 24px; border-bottom: 1px solid var(--sp-line-soft); gap: 8px; }
.th { min-height: 44px; color: var(--sp-text3); font-size: 12.5px; font-weight: 500; border-bottom-color: var(--sp-line); }
.r { text-align: right; }
.name { display: flex; flex-direction: column; gap: 4px; min-width: 0; padding-right: 16px; }
.title { color: var(--sp-text); font-weight: 600; font-size: 14.5px; text-decoration: none; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.title:hover { color: var(--sp-accent); }
.title.plain:hover { color: var(--sp-text); }
.tiny { font-size: 12.5px; }
.text2 { color: var(--sp-text2); }
.strong { font-weight: 600; }
.gain { color: var(--sp-accent); }
.loss { color: var(--sp-loss); }
.flat { color: var(--sp-text2); }
.where { display: inline-flex; align-items: center; height: 24px; padding: 0 9px; border-radius: 7px; font-size: 12.5px; font-weight: 600; white-space: nowrap; }
.where.inv { background: var(--sp-accent-soft); color: var(--sp-accent); }
.where.lease { background: var(--sp-info-soft); color: var(--sp-info); }
.where.unknown { background: var(--sp-warn-soft); color: var(--sp-warn); }
.where.mixed { background: var(--sp-surface2); color: var(--sp-text2); }
.flag { display: inline-block; width: 7px; height: 7px; margin-left: 6px; border-radius: 4px; background: var(--sp-warn); vertical-align: middle; cursor: help; }
.foot { min-height: 52px; display: flex; align-items: center; justify-content: center; gap: 12px; font-size: 13px; }
.more { height: 32px; padding: 0 12px; border-radius: 9px; border: none; background: transparent; color: var(--sp-accent); font: inherit; font-size: 13px; font-weight: 600; cursor: pointer; }
</style>
