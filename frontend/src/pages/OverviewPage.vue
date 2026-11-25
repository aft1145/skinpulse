<script setup lang="ts">
import { refDebounced } from '@vueuse/core'
import { NCheckbox, NInput, useMessage } from 'naive-ui'
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowUpDown, BellOff, RefreshCw, Search, TrendingDown } from '@lucide/vue'
import { ApiError, fetchFills, fetchOverview, syncNow } from '@/api/client'
import type { Fills, Order, Overview } from '@/api/types'
import { useAccount } from '@/composables/useAccount'
import { useMeta } from '@/composables/useMeta'
import { useCountUp } from '@/composables/useMotion'
import { usePolling } from '@/composables/usePolling'
import SegmentedControl from '@/components/SegmentedControl.vue'
import { countSpark, headlineText } from '@/format'
import CategoryPicker from './overview/CategoryPicker.vue'
import FocusCard from './overview/FocusCard.vue'
import GapPicker from './overview/GapPicker.vue'
import InsightCard from './overview/InsightCard.vue'
import OrdersTable from './overview/OrdersTable.vue'
import PausedCard from './overview/PausedCard.vue'
import RecentFills from './overview/RecentFills.vue'
import SetupCard from './overview/SetupCard.vue'
import ValueTable from './overview/ValueTable.vue'
import ValueTiles from './overview/ValueTiles.vue'
import {
  SEGMENTS,
  accountTag,
  categoryMenu,
  categoryText,
  filterOrders,
  filtersFromQuery,
  filtersToQuery,
  gapCounts,
  unreachableInsight,
  unreachableOf,
  movesInsight,
  rankFlashes,
  setupView,
  top3Insight,
} from './overview/logic'
import {
  ADVICE,
  VALUE_FILTERS,
  VIEWS,
  filterByAdvice,
  feePct,
  filterByValue,
  sortByTakeMargin,
  valueHeadline,
  valueStateFromQuery,
  valueStateToQuery,
} from './overview/valueLogic'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const { meta } = useMeta()
// 顶栏选的号（多账号 spec §8）：标题、焦点卡片、表格、最近成交都只算这个号；「全部」时 null
const { effective: account } = useAccount()
const accountParam = computed(() => (account.value === 'all' ? null : account.value))
// 「开始使用」（首次使用 spec §2）：三步必需的没做完才有；第一轮还没跑完时大标题没意义，先藏起来
const setup = computed(() => setupView(meta.value, data.value ? data.value.orders.length : null))
const showHero = computed(() => !setup.value || meta.value?.setup?.first_round === true)

const data = ref<Overview | null>(null)
const error = ref<string | null>(null)
const flashes = ref(new Map<string, 'up' | 'down'>())
let prevOrders: Order[] | null = null
let flashTimer: ReturnType<typeof setTimeout> | undefined

// 最近成交（成交推送 spec §7.1）：单独取，取不到只是卡片不显示，不影响主页
const fills = ref<Fills | null>(null)
async function loadFills() {
  try {
    fills.value = await fetchFills(30, accountParam.value)
  } catch {
    fills.value = null
  }
}

async function load() {
  void loadFills()
  try {
    const next = await fetchOverview(accountParam.value)
    flashes.value = rankFlashes(prevOrders, next.orders)
    clearTimeout(flashTimer)
    if (flashes.value.size) flashTimer = setTimeout(() => (flashes.value = new Map()), 1600)
    prevOrders = next.orders
    data.value = next
    error.value = null
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : String(e)
  }
}
const { refresh } = usePolling(load, 60_000)
// 换了号：重新取，不和上一个号的名次比（否则整页都在闪）
watch(accountParam, () => {
  prevOrders = null
  void refresh()
})
const tagOf = (o: Order) => accountTag(o, data.value?.accounts, account.value)

// 筛选条件、视图（排名 / 价位）和 URL 同步：刷新页面、分享链接后保留
const filters = reactive(filtersFromQuery(route.query))
const vs = reactive(valueStateFromQuery(route.query))
const q = ref(filters.q)
const qDebounced = refDebounced(q, 150)
watch(qDebounced, (v) => (filters.q = v))
const toQuery = () => ({ ...filtersToQuery(filters), ...valueStateToQuery(vs) })
watch([filters, vs], () => router.replace({ query: toQuery() }), { deep: true })
watch(() => vs.view, (v) => { if (v !== 'value') Object.assign(vs, { vf: null, adv: null }) })
// 反过来也要同步：浏览器前进 / 后退、点了带参数的链接
watch(
  () => route.query,
  (query) => {
    const next = filtersFromQuery(query)
    const nextVs = valueStateFromQuery(query)
    if (JSON.stringify({ ...filtersToQuery(next), ...valueStateToQuery(nextVs) }) === JSON.stringify(toQuery())) return
    Object.assign(filters, next)
    Object.assign(vs, nextVs)
    q.value = next.q
  },
)

// 价位视图（价位 spec §7.1）：全部单，只按搜索和价位筛选，按「抢到第 1」从高到低
const valuation = computed(() => data.value?.valuation ?? null)
const valueShown = computed(() => {
  if (!data.value) return []
  const searched = filterOrders(data.value.orders, { seg: 'all', hideUnreachable: false, q: filters.q, cat: null, sub: null, gap: null })
  // 建议关掉（或后端还没重启）时没有建议列，网址里的 adv 也不起作用
  const adv = valuation.value?.counts.advice ? vs.adv : null
  return sortByTakeMargin(filterByAdvice(filterByValue(searched, vs.vf), adv))
})
const vHead = computed(() => (valuation.value && data.value ? valueHeadline(valuation.value, data.value.orders) : null))
const vTiles = computed(() => {
  const c = valuation.value?.counts
  if (!c) return []
  return [
    { key: 'low' as const, n: c.low, title: '处在一年低位', sub: '收盘价在一年里最低的 20%', tone: 'accent' as const },
    { key: 'fall' as const, n: c.fall, title: '在跌且跑输大盘', sub: '低价求购最容易在这时成交', tone: 'warn' as const },
    { key: 'hard' as const, n: c.hard, title: '难出手', sub: '在售少，或买卖价差超过 20%', tone: 'plain' as const },
  ]
})
const vfLabel = computed(() => VALUE_FILTERS.find((f) => f.key === vs.vf)?.label ?? '')
const advLabel = computed(() => ADVICE.find((a) => a.key === vs.adv)?.label ?? '')
const adviceCounts = computed(() => valuation.value?.counts.advice ?? null)
const feeNote = computed(() => {
  const f = valuation.value?.fees
  if (!f) return ''
  return `差价已扣手续费（悠悠 ${feePct(f.YOUPIN)} / BUFF ${feePct(f.BUFF)}）、提现 ${feePct(f.withdraw)}、让价 ${feePct(f.undercut)}`
})


// 品类（看板 spec §4.1）：张数按品类以外的条件算；后端还是旧版本（没有 category_groups）时不显示按钮、也不按品类筛
const catGroups = computed(() => data.value?.category_groups ?? [])
const catMenu = computed(() => {
  if (!data.value || !catGroups.value.length) return []
  return categoryMenu(catBase.value, catGroups.value, filters)
})
const catLabel = computed(() => categoryText(catMenu.value, catGroups.value, filters.cat, filters.sub))
const catBase = computed(() => (data.value ? filterOrders(data.value.orders, { ...filters, cat: null, sub: null }) : []))
// 「离第 1」档位（intent 2026-11-20）：张数按档位以外的条件算
const gapBase = computed(() => {
  if (!data.value) return []
  const f = data.value.category_groups ? filters : { ...filters, cat: null, sub: null }
  return filterOrders(data.value.orders, { ...f, gap: null })
})
const gapN = computed(() => gapCounts(gapBase.value))
// 难以成交（难以成交 spec §5）：按勾选以外的条件算有几张，复选框后面和列表标题旁都用这个数
const unreachableN = computed(() => {
  if (!data.value) return 0
  const f = data.value.category_groups ? filters : { ...filters, cat: null, sub: null }
  return filterOrders(data.value.orders, { ...f, hideUnreachable: false }).filter((o) => unreachableOf(o)).length
})
const shown = computed(() => filterOrders(gapBase.value, { seg: 'all', hideUnreachable: false, q: '', cat: null, sub: null, gap: filters.gap }))
// 网址里不认识的大类、第二层当作没选（数据到了才知道认不认识）。第二层按全部在册单认，
// 不按当前筛选：分段一切换就把选择丢掉不对
watch([catGroups, () => filters.cat, () => filters.sub], ([g]) => {
  if (!g.length || !filters.cat || !data.value) return
  if (!g.some((x) => x.key === filters.cat)) Object.assign(filters, { cat: null, sub: null })
  else if (filters.sub && !data.value.orders.some((o) => o.category === filters.cat && o.subcategory === filters.sub)) filters.sub = null
})
const head = computed(() => (data.value ? headlineText(data.value.headline) : null))
const firstCount = computed(() => head.value?.parts[0]?.count ?? 0)
const animated = useCountUp(firstCount)
const maxGap = computed(() => meta.value?.thresholds.max_gap_ratio ?? 3)
const youpinUrl = computed(() => meta.value?.links.youpin_item ?? '')
const ins = computed(() => {
  const i = data.value?.insights
  if (!i) return null
  return { top3: top3Insight(i), moves: movesInsight(i), unreachable: unreachableInsight(i), spark: countSpark(i.top3_series, 72, 30) }
})

const syncing = ref(false)
async function onSync() {
  syncing.value = true
  try {
    const r = await syncNow()
    message.success(`已同步 ${r.synced} 张求购`)
    await refresh()
  } catch (e) {
    message.error(e instanceof ApiError ? e.message : '同步失败')
  } finally {
    syncing.value = false
  }
}
</script>

<template>
  <main class="page">
    <div v-if="error && !data" class="card error rise">
      <b>总览加载失败</b>
      <span class="muted">{{ error }}</span>
      <button type="button" class="btn press" @click="refresh()">重试</button>
    </div>

    <template v-else-if="!data">
      <div class="skeleton" style="width: 420px; height: 42px" />
      <div class="grid3"><div v-for="i in 3" :key="i" class="skeleton" style="height: 210px; border-radius: 18px" /></div>
      <div class="skeleton" style="height: 480px; border-radius: 18px" />
    </template>

    <template v-else>
      <SetupCard v-if="setup" :view="setup" :tz="meta?.timezone ?? 'Asia/Shanghai'" class="rise" style="--i: 0" @changed="refresh()" />
      <!-- 充值前检查暂停单（暂停单 spec §5）：平时在「分析」页（2026-11-25 用户：不常用，不占总览最上面）。
           只有余额花光、单子全被暂停（求购中 0 张，「开始使用」写着「还没有求购单」）时才留在这里 -->
      <PausedCard v-if="setup?.noOrders" :tz="meta?.timezone ?? 'Asia/Shanghai'" :youpin-url="youpinUrl" class="rise" style="--i: 0" />
      <section v-if="showHero" class="hero rise" style="--i: 0">
        <div v-if="vs.view === 'value'" class="headline">
          <template v-if="vHead">
            <h1 class="title"><span class="accent num-inline">{{ vHead.n }} 张</span>抢到第 1 仍有 {{ vHead.target }} 差价</h1>
            <p class="sub">{{ vHead.sub }}</p>
          </template>
          <template v-else>
            <h1 class="title">暂无估值</h1>
            <p class="sub">每天 00:30 计算一次；想马上看，在终端运行 <code>uv run python -m skinpulse valuation</code>（约 12 分钟）。</p>
          </template>
        </div>
        <div v-else class="headline">
          <h1 v-if="head?.parts.length" class="title">
            <template v-for="(p, i) in head.parts" :key="p.rank">
              <span v-if="i" class="sep">、</span>
              <span class="accent num-inline">{{ i === 0 ? Math.round(animated) : p.count }} 张</span>求购排名第 {{ p.rank }}
            </template>
          </h1>
          <h1 v-else class="title">{{ head?.empty }}</h1>
          <p v-if="head?.sub" class="sub">{{ head.sub }}</p>
        </div>
        <SegmentedControl v-model="vs.view" :options="VIEWS" label="视图" />
        <button v-if="vs.view === 'rank'" type="button" class="btn press" :disabled="syncing" @click="onSync">
          <RefreshCw :size="15" :class="{ spin: syncing }" />{{ syncing ? '同步中…' : '立即同步' }}
        </button>
      </section>

      <template v-if="vs.view === 'value'">
        <ValueTiles v-if="vTiles.length" v-model="vs.vf" :tiles="vTiles" class="rise" style="--i: 1" />
        <section v-if="valuation" class="list rise" style="--i: 2">
          <div class="toolbar">
            <h2>全部求购</h2>
            <span class="num muted">{{ valueShown.length }}</span>
            <button v-if="vs.vf" type="button" class="chip-btn press" :aria-label="`取消筛选：${vfLabel}`" @click="vs.vf = null">{{ vfLabel }} ×</button>
            <button v-if="vs.adv && adviceCounts" type="button" class="chip-btn press" :aria-label="`取消筛选：${advLabel}`" @click="vs.adv = null">建议：{{ advLabel }} ×</button>
            <div class="grow" />
            <span class="fee muted">{{ feeNote }}</span>
            <NInput v-model:value="q" placeholder="搜索饰品" clearable class="search" aria-label="搜索饰品">
              <template #prefix><Search :size="15" /></template>
            </NInput>
          </div>
          <div class="card table-card">
            <ValueTable :orders="valueShown" :target="valuation.target_margin" :fees="valuation.fees" :as-of="valuation.as_of" :advice-counts="adviceCounts" v-model:adv="vs.adv" />
            <div v-if="!valueShown.length" class="empty muted">无符合条件的求购单</div>
            <div class="foot muted">显示 {{ valueShown.length }} / {{ data.orders.length }} 张 · 估值于 {{ valuation.as_of.slice(5) }}<template v-if="error"> · 刷新失败，显示的是上一次的数据</template></div>
          </div>
          <p class="rule muted">依据过去一年悠悠日线和价格快照、按固定规则计算的参考值，不含 Valve 改规则等政策风险。「—」表示无法计算（日线不足 60 根或无在售价），不是 0。<template v-if="adviceCounts">建议只用回测通过的规则：按「7 天坏情况」倒推求购价上限；回测只有一年数据、只经历过下跌行情。</template></p>
        </section>
      </template>

      <template v-else>
      <section v-if="data.focus.length" class="grid3">
        <FocusCard v-for="(o, i) in data.focus" :key="o.purchase_no" :order="o" class="rise" :style="{ '--i': i + 1 }" />
      </section>

      <section v-if="ins" class="grid3">
        <InsightCard v-bind="ins.top3" :spark="ins.spark" class="rise" style="--i: 4"><template #icon><TrendingDown :size="18" /></template></InsightCard>
        <InsightCard v-bind="ins.moves" class="rise" style="--i: 5"><template #icon><ArrowUpDown :size="18" /></template></InsightCard>
        <InsightCard v-bind="ins.unreachable" class="rise" style="--i: 6"><template #icon><BellOff :size="18" /></template></InsightCard>
      </section>

      <RecentFills v-if="fills && fills.total_30d.count" :data="fills" :tz="meta?.timezone ?? 'Asia/Shanghai'" class="rise" style="--i: 7" />

      <section class="list rise" style="--i: 8">
        <div class="toolbar">
          <h2>全部求购</h2>
          <span class="num muted">{{ data.orders.length }}</span>
          <span v-if="filters.hideUnreachable && unreachableN" class="muted hidden-note">已隐藏 {{ unreachableN }} 张难以成交的单</span>
          <div class="grow" />
          <CategoryPicker v-if="catGroups.length" v-model:cat="filters.cat" v-model:sub="filters.sub" :menu="catMenu" :total="catBase.length" :text="catLabel" />
          <GapPicker v-model="filters.gap" :counts="gapN" :total="gapBase.length" />
          <SegmentedControl v-model="filters.seg" :options="SEGMENTS" label="按排名筛选" />
          <NCheckbox v-model:checked="filters.hideUnreachable">隐藏难以成交的<span v-if="unreachableN" class="num muted check-n">{{ unreachableN }}</span></NCheckbox>
          <NInput v-model:value="q" placeholder="搜索饰品" clearable class="search" aria-label="搜索饰品">
            <template #prefix><Search :size="15" /></template>
          </NInput>
        </div>
        <div class="card table-card">
          <OrdersTable :orders="shown" :tag-of="tagOf" :flashes="flashes" :days="data.insights.days" :max-gap-ratio="maxGap" :youpin-url="youpinUrl" :tz="meta?.timezone ?? 'Asia/Shanghai'" />
          <div v-if="!shown.length" class="empty muted">无符合条件的求购单</div>
          <div class="foot muted">显示 {{ shown.length }} / {{ data.orders.length }} 张<template v-if="error"> · 刷新失败，显示的是上一次的数据</template></div>
        </div>
      </section>
      </template>
    </template>
  </main>
</template>

<style scoped>
.page { max-width: 1440px; margin: 0 auto; padding: 48px 56px 64px; display: flex; flex-direction: column; gap: 40px; }
.grid3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; }
.hero { display: flex; align-items: flex-end; gap: 24px; }
.headline { flex: 1; display: flex; flex-direction: column; gap: 10px; }
.title { margin: 0; font-size: 34px; line-height: 1.2; font-weight: 700; letter-spacing: -0.02em; }
.accent { color: var(--sp-accent); }
.sep { color: var(--sp-text3); margin: 0 2px; }
.sub { margin: 0; font-size: 14px; color: var(--sp-text2); }
.btn {
  height: 40px; padding: 0 16px; border-radius: 10px; border: 1px solid var(--sp-line-strong); background: var(--sp-surface);
  color: var(--sp-text); font: inherit; font-weight: 500; display: inline-flex; align-items: center; gap: 8px; cursor: pointer;
}
.btn:hover { background: var(--sp-surface2); }
.btn:disabled { opacity: 0.6; cursor: default; }
.spin { animation: spin 0.9s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.list { display: flex; flex-direction: column; gap: 16px; }
.toolbar { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.toolbar h2 { margin: 0; font-size: 20px; font-weight: 700; letter-spacing: -0.01em; }
.hidden-note { font-size: 13px; white-space: nowrap; }
.check-n { margin-left: 6px; font-size: 12px; }
.grow { flex: 1; }
.search { width: 220px; }
.table-card { overflow: hidden; padding: 4px 8px 0; }
.empty { padding: 48px; text-align: center; }
.foot { height: 52px; display: flex; align-items: center; justify-content: center; font-size: 13px; }
.chip-btn {
  height: 28px; padding: 0 10px; border-radius: 8px; border: 1px solid var(--sp-line-strong); background: var(--sp-surface);
  color: var(--sp-text2); font: inherit; font-size: 12.5px; cursor: pointer;
}
.chip-btn:hover { color: var(--sp-text); }
.fee { font-size: 12.5px; }
.rule { margin: 0; font-size: 12.5px; line-height: 1.7; }
code { font-family: 'JetBrains Mono', monospace; font-size: 12.5px; }
.error { padding: 28px; display: flex; flex-direction: column; gap: 10px; align-items: flex-start; }
@media (max-width: 1100px) { .page { padding: 32px 24px 48px; } .grid3 { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 760px) {
  .page { padding: 24px 16px 40px; gap: 28px; }
  .hero { flex-wrap: wrap; align-items: center; gap: 16px; }
  .headline { flex: 1 0 100%; }
  .title { font-size: 26px; }
}
</style>
