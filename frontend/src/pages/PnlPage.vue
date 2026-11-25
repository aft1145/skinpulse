<script setup lang="ts">
// 盈亏页（盈亏 spec §7，视觉稿 2026-11-24 定稿）：焦点是「总盈亏」一个数。只读库，不请求任何外部服务。
import { NTooltip } from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { ApiError, fetchPnl, fetchPnlSells } from '@/api/client'
import type { PnlBasis, PnlSells, PnlSummary } from '@/api/types'
import SegmentedControl from '@/components/SegmentedControl.vue'
import { useAccount } from '@/composables/useAccount'
import { useMeta } from '@/composables/useMeta'
import { clock } from '@/format'
import ItemsTable from './pnl/ItemsTable.vue'
import SellsTable from './pnl/SellsTable.vue'
import { categoryRows, monthBars, readBasis, signedPercent, signedYuan, summaryView, tone, writeBasis, yuan } from './pnl/logic'

const { meta } = useMeta()
const { effective } = useAccount()
const tz = computed(() => meta.value?.timezone ?? 'Asia/Shanghai')
const account = computed(() => (effective.value === 'all' ? null : effective.value))

const summary = ref<PnlSummary | null>(null)
const sells = ref<PnlSells | null>(null)
const error = ref<string | null>(null)
const basis = ref<PnlBasis>(readBasis('market'))
const basisTouched = ref(false)
const chart = ref<'months' | 'curve'>('months')
const category = ref<string | null>(null)
const sellsAll = ref(false)

async function load() {
  try {
    const acc = account.value
    const [s, sl] = await Promise.all([fetchPnl(acc), fetchPnlSells(acc, sellsAll.value ? 0 : 6)])
    summary.value = s
    sells.value = sl
    if (!basisTouched.value) basis.value = readBasis(s.basis_default)
    error.value = null
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : String(e)
  }
}
load()
watch([account, sellsAll], load)
watch(basis, (b) => writeBasis(b))
const basisModel = computed({
  get: () => basis.value,
  set: (b: PnlBasis) => { basisTouched.value = true; basis.value = b },
})

const view = computed(() => (summary.value ? summaryView(summary.value, basis.value) : null))
const bars = computed(() => monthBars(summary.value?.months ?? []))
const cats = computed(() => categoryRows(summary.value?.categories ?? [], basis.value))
const monthsFrom = computed(() => summary.value?.months[0]?.[0] ?? null)
const updated = computed(() => clock(summary.value?.computed_at ?? null, tz.value))
const where = computed(() => summary.value?.where ?? null)
const curveFrom = computed(() => summary.value?.as_of?.slice(5) ?? '')
const footText = computed(() => {
  const n = summary.value?.notes
  if (!n) return ''
  const rent = n.lease_rent_day != null ? `，日租金合计 ${yuan(n.lease_rent_day)}` : ''
  const parts = [`租金（租出中 ${n.lease_qty} 件${rent}）`, '提现费']
  if (n.unbooked_inv_qty) parts.push(`库存中另有 ${n.unbooked_inv_qty} 件无买入记录，市价约 ${yuan(n.unbooked_inv_value)}`)
  return `不算的：${parts.join(' · ')}。`
})
const basisOptions = [{ key: 'market' as PnlBasis, label: '按市价' }, { key: 'net' as PnlBasis, label: '按到手价' }]
const chartOptions = [{ key: 'months' as const, label: '每月已实现' }, { key: 'curve' as const, label: '持仓走势' }]

function pickCategory(key: string) {
  category.value = category.value === key ? null : key
  document.getElementById('pnl-items')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
</script>

<template>
  <main class="page">
    <div class="hero rise" style="--i: 0">
      <div class="names">
        <h1>盈亏</h1>
        <p v-if="summary?.as_of && view" class="sub">
          持有 <span class="num">{{ summary.holding?.qty }}</span> 件 · 卖出 <span class="num">{{ summary.realized?.sells }}</span> 笔 · {{ updated }} 更新
        </p>
      </div>
      <SegmentedControl v-if="view" v-model="basisModel" :options="basisOptions" label="浮动按什么算" />
    </div>

    <p v-if="error" class="card empty">{{ error }}</p>
    <section v-else-if="summary && !summary.as_of" class="card empty rise" style="--i: 1">
      <h2>暂无盈亏数据</h2>
      <p class="muted">请在终端运行 <code>uv run python -m skinpulse pnl import</code> 导入悠悠的买卖历史，之后每天 00:45 自动更新。</p>
    </section>
    <template v-else-if="summary && view">
      <!-- 焦点卡片 -->
      <section class="focus card rise" style="--i: 1" aria-label="总盈亏">
        <div class="focus-main" :class="tone(view.total)">
          <span class="label">总盈亏 · {{ view.basisLabel }}</span>
          <span class="big display" :class="tone(view.total)">{{ signedYuan(view.total) }}</span>
          <div class="stats">
            <div class="stat">
              <span class="label">已实现</span>
              <span class="num stat-v" :class="tone(view.realized)">{{ signedYuan(view.realized) }}</span>
              <span class="tiny muted">{{ summary.realized?.matched }} 笔已匹配买入</span>
            </div>
            <div class="sep" />
            <div class="stat">
              <span class="label">浮动</span>
              <span class="num stat-v" :class="tone(view.float)">{{ signedYuan(view.float) }}</span>
              <span class="tiny muted">{{ signedPercent(view.floatPct) }} · 持有 {{ summary.holding?.qty }} 件</span>
            </div>
          </div>
        </div>
        <div class="focus-side">
          <div class="block">
            <span class="block-h">持仓</span>
            <div class="bars">
              <span class="tiny muted">成本</span>
              <div class="bar"><div class="fill cost" style="width: 100%" /></div>
              <span class="num strong right">{{ yuan(view.cost) }}</span>
              <span class="tiny muted">{{ view.valueLabel }}</span>
              <div class="bar"><div class="fill value" :style="{ width: `${(view.valueRatio * 100).toFixed(1)}%` }" /></div>
              <span class="num strong right">{{ yuan(view.value) }}</span>
            </div>
          </div>
          <div v-if="where" class="block">
            <span class="block-h">这 {{ summary.holding?.qty }} 件在哪</span>
            <div class="stack" role="img" :aria-label="`库存 ${where.inv} 件，租出 ${where.lease} 件，不在库存 ${where.unknown} 件`">
              <div class="part inv" :style="{ flexGrow: where.inv }" />
              <div class="part lease" :style="{ flexGrow: where.lease }" />
              <div class="part unknown" :style="{ flexGrow: where.unknown }" />
            </div>
            <div class="legend">
              <span><i class="sw inv" />库存 <b class="num">{{ where.inv }}</b></span>
              <span><i class="sw lease" />租出 <b class="num">{{ where.lease }}</b></span>
              <NTooltip>
                <template #trigger>
                  <span tabindex="0"><i class="sw unknown" />不在库存 <b class="num">{{ where.unknown }}</b></span>
                </template>
                成本合计 {{ yuan(where.unknown_cost) }}
              </NTooltip>
            </div>
            <span class="tiny muted">不在库存：库存和悠悠租出中均无，也未在悠悠或 BUFF 售出，多为在 BUFF App 中出租（网页无法获取出租列表）</span>
          </div>
          <div class="compare">
            <template v-if="summary.youpin">
              <span>悠悠自己算的浮动（库存里有买入价的 {{ summary.youpin.n }} 件，按市价）</span>
              <span class="num strong2">{{ signedYuan(summary.youpin.pnl) }}</span>
            </template>
            <span v-if="summary.holding?.unpriced_qty" class="full">{{ summary.holding.unpriced_qty }} 件无市价，未计入市值</span>
          </div>
        </div>
      </section>

      <!-- 图表 + 品类 -->
      <div class="row2 rise" style="--i: 2">
        <section class="card chart" aria-labelledby="chart-h">
          <div class="card-head">
            <h2 id="chart-h">{{ chart === 'months' ? '每月已实现' : '持仓走势' }}</h2>
            <div class="grow" />
            <SegmentedControl v-model="chart" :options="chartOptions" label="图表" />
          </div>
          <template v-if="chart === 'months'">
            <div v-if="bars.length" class="cols">
              <div v-for="b in bars" :key="b.key" class="col" :title="b.title">
                <span class="col-v num">{{ b.label }}</span>
                <div class="col-bar" :class="[b.tone, { off: !b.on }]" :style="{ height: `${b.height}px` }" />
              </div>
            </div>
            <div v-if="bars.length" class="axis num">
              <span v-for="b in bars" :key="b.key">{{ b.axis }}</span>
            </div>
            <p v-else class="muted chart-empty">暂无已匹配买入的卖出</p>
            <div class="chart-foot tiny muted">
              <span v-if="monthsFrom">{{ monthsFrom }} 以来合计 <b class="num" :class="tone(view.realized)">{{ signedYuan(view.realized) }}</b></span>
              <span v-if="summary.realized?.unknown">另有 {{ summary.realized.unknown }} 笔卖出无买入记录（到手 {{ yuan(summary.realized.unknown_net) }}），未计入</span>
            </div>
          </template>
          <div v-else class="curve-empty">
            <span class="strong">从 {{ curveFrom }} 开始每天记一笔</span>
            <span class="muted">攒够两周后画出持仓市值和浮动盈亏的走势</span>
          </div>
        </section>

        <section class="card cats" aria-labelledby="cats-h">
          <div class="card-head">
            <h2 id="cats-h">按品类</h2>
            <span class="tiny muted">浮动 · 按亏损从大到小</span>
          </div>
          <div class="cat-list">
            <button
              v-for="c in cats" :key="c.key" type="button" class="cat press" :class="{ on: category === c.key }"
              :aria-pressed="category === c.key" @click="pickCategory(c.key)"
            >
              <span class="cat-name">{{ c.label }}</span>
              <span class="num tiny muted right">{{ c.qty }} 件</span>
              <span class="cat-bar"><span class="cat-fill" :class="c.tone" :style="{ width: c.width }" /></span>
              <span class="num right strong" :class="c.tone">{{ c.text }}</span>
            </button>
          </div>
        </section>
      </div>

      <ItemsTable id="pnl-items" class="rise" style="--i: 3" :account="account" :basis="basis" :category="category"
                  :tz="tz" @clear-category="category = null" />
      <SellsTable v-if="sells" class="rise" style="--i: 4" :data="sells" :tz="tz" :all="sellsAll" @toggle="sellsAll = !sellsAll" />

      <p v-if="summary.notes" class="foot tiny muted">
        {{ footText }}
      </p>
    </template>
    <div v-else class="skeleton" style="height: 320px" />
  </main>
</template>

<style scoped>
.page { max-width: 1440px; margin: 0 auto; padding: 48px 56px 64px; display: flex; flex-direction: column; gap: 32px; }
.hero { display: flex; align-items: flex-end; gap: 16px; }
.names { display: flex; flex-direction: column; gap: 10px; flex: 1; }
h1 { margin: 0; font-size: 34px; line-height: 1.2; font-weight: 700; letter-spacing: -0.02em; }
h2 { margin: 0; font-size: 18px; font-weight: 700; }
.sub { margin: 0; font-size: 14px; color: var(--sp-text2); }
.empty { padding: 32px; display: flex; flex-direction: column; gap: 10px; }
.empty p { margin: 0; }
code { font-family: 'JetBrains Mono', monospace; font-size: 12.5px; background: var(--sp-surface2); padding: 2px 6px; border-radius: 6px; }
.tiny { font-size: 12px; }
.label { font-size: 12.5px; color: var(--sp-text3); }
.strong { font-weight: 600; }
.right { text-align: right; }
.grow { flex: 1; }
.gain { color: var(--sp-accent); }
.loss { color: var(--sp-loss); }
.flat { color: var(--sp-text2); }

.focus { display: grid; grid-template-columns: minmax(0, 1fr) 560px; overflow: hidden; border-radius: 20px; }
.focus-main { padding: 34px 40px 32px; display: flex; flex-direction: column; gap: 22px; color: var(--sp-text); }
.focus-main.loss { background-image: linear-gradient(120deg, color-mix(in srgb, var(--sp-loss) 7%, transparent), transparent 60%); }
.focus-main.gain { background-image: linear-gradient(120deg, color-mix(in srgb, var(--sp-accent) 8%, transparent), transparent 60%); }
.focus-main .label { font-size: 14px; font-weight: 500; color: var(--sp-text2); }
.big { font-size: 76px; font-weight: 600; letter-spacing: -0.035em; line-height: 0.95; }
.stats { display: flex; gap: 36px; margin-top: 6px; }
.stat { display: flex; flex-direction: column; gap: 6px; }
.stat-v { font-size: 20px; font-weight: 600; }
.sep { width: 1px; background: var(--sp-line-soft); }
.focus-side { padding: 30px 36px 28px; border-left: 1px solid var(--sp-line-soft); display: flex; flex-direction: column; gap: 22px; }
.block { display: flex; flex-direction: column; gap: 10px; }
.block-h { font-size: 13px; font-weight: 600; color: var(--sp-text2); }
.bars { display: grid; grid-template-columns: 40px minmax(0, 1fr) 110px; align-items: center; gap: 12px; }
.bar { height: 12px; border-radius: 6px; background: var(--sp-bar); overflow: hidden; }
.fill { height: 12px; border-radius: 6px; }
.fill.cost { background: var(--sp-line-strong); }
.fill.value { background: var(--sp-accent); }
.stack { display: flex; height: 10px; border-radius: 5px; overflow: hidden; gap: 2px; }
.part { flex-basis: 0; min-width: 0; }
.part.inv, .sw.inv { background: var(--sp-accent); }
.part.lease, .sw.lease { background: var(--sp-info); }
.part.unknown, .sw.unknown { background: var(--sp-warn); }
.legend { display: flex; gap: 18px; font-size: 12.5px; color: var(--sp-text2); }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.legend b { color: var(--sp-text); }
.sw { width: 8px; height: 8px; border-radius: 2px; display: inline-block; }
.compare { margin-top: auto; padding-top: 14px; border-top: 1px dashed var(--sp-line); display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; font-size: 12.5px; color: var(--sp-text3); }
.compare .strong2 { font-weight: 600; color: var(--sp-text2); }
.compare .full { flex-basis: 100%; }

.row2 { display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(0, 1fr); gap: 24px; align-items: stretch; }
.card-head { display: flex; align-items: center; gap: 12px; }
.chart { padding: 26px 30px 24px; display: flex; flex-direction: column; gap: 14px; }
.cols { display: flex; align-items: flex-end; gap: 6px; height: 214px; border-bottom: 1px solid var(--sp-line); }
.col { flex: 1 1 0; min-width: 0; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; gap: 6px; height: 100%; }
.col-v { font-size: 11px; color: var(--sp-text2); white-space: nowrap; }
.col-bar { width: 100%; max-width: 26px; border-radius: 5px 5px 0 0; background: var(--sp-accent); }
.col-bar.loss { background: var(--sp-loss); }
.col-bar.off { background: var(--sp-bar); border-radius: 2px; }
.axis { display: flex; gap: 6px; font-size: 10.5px; color: var(--sp-text3); }
.axis span { flex: 1 1 0; min-width: 0; text-align: center; white-space: nowrap; }
.chart-foot { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.chart-empty { margin: 60px 0; text-align: center; }
.curve-empty { height: 248px; border: 1px dashed var(--sp-line-strong); border-radius: 14px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; color: var(--sp-text2); }
.cats { padding: 26px 26px 18px; display: flex; flex-direction: column; gap: 10px; }
.cats .card-head { align-items: baseline; padding: 0 4px; }
.cat-list { display: flex; flex-direction: column; }
.cat {
  display: grid; grid-template-columns: 92px 44px minmax(0, 1fr) 100px; align-items: center; gap: 10px;
  height: 32px; padding: 0 4px; border: none; border-radius: 8px; background: transparent; color: var(--sp-text); font: inherit; cursor: pointer; text-align: left;
}
.cat:hover { background: var(--sp-surface2); }
.cat.on { background: var(--sp-accent-soft); }
.cat-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-weight: 500; }
.cat-bar { height: 8px; border-radius: 4px; background: var(--sp-bar); overflow: hidden; }
.cat-fill { display: block; height: 8px; border-radius: 4px; }
.cat-fill.loss { background: var(--sp-loss); }
.cat-fill.gain { background: var(--sp-accent); }
.cat-fill.flat { background: var(--sp-line-strong); }
.foot { margin: 0; line-height: 1.7; }

@media (max-width: 1100px) {
  .page { padding: 32px 24px 48px; }
  .hero { flex-wrap: wrap; }
  .focus, .row2 { grid-template-columns: minmax(0, 1fr); }
  .focus-side { border-left: none; border-top: 1px solid var(--sp-line-soft); }
  .big { font-size: 56px; }
}
</style>
