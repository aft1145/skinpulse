<script setup lang="ts">
// 选品页（价位 spec §9b.4，视觉稿 2026-10-09 定稿）：上面圈池子，中间是进度，下面是结果。
// 开始选品是这一页唯一的写请求：只请求 SteamDT，不碰悠悠；夜间窗口和超过 500 个由后端拒绝。
import { refDebounced } from '@vueuse/core'
import { NInputNumber, NSelect, useMessage } from 'naive-ui'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError, estimatePick, fetchPickOptions, fetchPickRun, fetchPickRuns, startPick } from '@/api/client'
import type { PickEstimate, PickFilters, PickOptions, PickRunBrief, PickRunDetail, PickVerdict } from '@/api/types'
import { usePolling } from '@/composables/usePolling'
import { clock } from '@/format'
import { useMeta } from '@/composables/useMeta'
import PickTable from './pick/PickTable.vue'
import {
  VERDICTS, estimateQuery, facetsFor, filterRows, filtersFromQuery, filtersSummary, filtersToQuery, fitFilters, progressRatio, progressText,
  sortRows, toggle,
} from './pick/logic'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const { meta } = useMeta()
const tz = computed(() => meta.value?.timezone ?? 'Asia/Shanghai')

const options = ref<PickOptions | null>(null)
const filters = reactive<PickFilters>({ groups: [], subs: [], wears: [], versions: [], price_min: null, price_max: null })
const est = ref<PickEstimate | null>(null)
const estError = ref<string | null>(null)
const runs = ref<PickRunBrief[]>([])
const run = ref<PickRunDetail | null>(null)
const loadError = ref<string | null>(null)
const starting = ref(false)
// 结论筛选：网址里有就用；没有时默认看「可以挂」，这一档是空的就看全部（loadRuns 里定）
const verdict = ref<PickVerdict | null>(VERDICTS.some((v) => v.key === route.query.v) ? (route.query.v as PickVerdict) : null)
const verdictFromUrl = VERDICTS.some((v) => v.key === route.query.v)
const verdictTouched = ref(false)

onMounted(async () => {
  try {
    options.value = await fetchPickOptions()
    Object.assign(filters, filtersFromQuery(route.query, options.value))
    await loadRuns(Number(route.query.run) || null)
  } catch (e) {
    loadError.value = e instanceof ApiError ? e.message : String(e)
  }
})

async function loadRuns(pick: number | null = run.value?.id ?? null) {
  const r = await fetchPickRuns()
  runs.value = r.runs
  const id = pick ?? r.runs[0]?.id ?? null
  run.value = id ? await fetchPickRun(id) : null
  // 网址里没指定档位、用户也还没点过：有「可以挂」就看它，没有就看全部
  if (!verdictFromUrl && !verdictTouched.value && run.value?.status === 'done') verdict.value = run.value.counts.go ? 'go' : null
}

// 正在跑时每 5 秒刷新进度；没在跑就不发请求
usePolling(async () => {
  if (run.value?.status !== 'running') return
  try {
    await loadRuns()
  } catch {
    /* 下一次再试 */
  }
}, 5000)

// 条件、当前查看的那次、结论筛选 → 网址
watch([filters, () => run.value?.id, verdict], () => {
  if (!options.value) return
  const q: Record<string, string> = { ...filtersToQuery(filters) }
  if (run.value) q.run = String(run.value.id)
  if (verdict.value) q.v = verdict.value
  router.replace({ query: q })
}, { deep: true })

// 条件一变就预估张数（只查目录）
const estKey = computed(() => (options.value ? estimateQuery(filters) : ''))
const estKeyDebounced = refDebounced(estKey, 250)
watch(estKeyDebounced, async (q) => {
  if (!q) return
  try {
    est.value = await estimatePick(q)
    estError.value = null
  } catch (e) {
    est.value = null
    estError.value = e instanceof ApiError ? e.message : String(e)
  }
})

const subOptions = computed(() => {
  if (!options.value) return []
  return filters.groups.flatMap((g) => (options.value!.subs[g] ?? []).map((s) => ({ label: `${s.label}（${s.n}）`, value: s.key })))
})
// 磨损、版本跟着大类走（intent 2026-11-23）：只列选中大类里有的，只剩「无磨损」/「普通」时整行不显示
const facets = computed(() => (options.value ? facetsFor(options.value, filters.groups) : null))
// 换了大类，不属于这些大类的武器、磨损、版本从条件里去掉
watch(() => filters.groups, (_g, before) => {
  const ok = new Set(subOptions.value.map((o) => o.value))
  if (filters.subs.some((s) => !ok.has(s))) filters.subs = filters.subs.filter((s) => ok.has(s))
  if (!options.value || !facets.value) return
  const was = before ? facetsFor(options.value, before).showVersions : true
  const next = fitFilters(filters, facets.value, was)
  if (next.wears.join() !== filters.wears.join()) filters.wears = next.wears
  if (next.versions.join() !== filters.versions.join()) filters.versions = next.versions
})

const running = computed(() => run.value?.status === 'running' || runs.value.some((r) => r.status === 'running'))
const canStart = computed(() => !!est.value && !est.value.over && est.value.count > 0 && !running.value && !starting.value && !!filters.groups.length)
const estText = computed(() => {
  if (estError.value) return estError.value
  if (!est.value) return '正在估算…'
  if (est.value.count === 0) return '按这些条件一个模板也圈不到'
  if (est.value.over) return `约 ${est.value.count.toLocaleString()} 个模板 · 超过 ${est.value.max}，请缩小范围（加选武器、磨损或版本）`
  return `约 ${est.value.count.toLocaleString()} 个模板 · 预计 ${est.value.minutes} 分钟（只请求 SteamDT）`
})

async function onStart() {
  starting.value = true
  try {
    const { id } = await startPick({ ...filters })
    await loadRuns(id)
    message.success('开始选品')
  } catch (e) {
    message.error(e instanceof ApiError ? e.message.replace(/^请求失败（\d+）：/, '') : '开始失败')
  } finally {
    starting.value = false
  }
}

const rows = computed(() => (run.value ? filterRows(sortRows(run.value.results), verdict.value) : []))
const runOptions = computed(() =>
  runs.value.map((r) => ({
    value: r.id,
    label: `${clock(r.created_at, tz.value)} · ${STATUS[r.status]}${r.status === 'done' ? ` · 可以挂 ${r.counts.go} / ${r.priced}` : ''}`,
  })),
)
const STATUS: Record<PickRunBrief['status'], string> = { running: '进行中', done: '完成', failed: '失败', interrupted: '中断' }
async function pickRun(id: number) {
  run.value = await fetchPickRun(id)
}
const minutes = (a: string, b: string | null) => Math.max(1, Math.round(((b ? Date.parse(b) : Date.now()) - Date.parse(a)) / 60000))
</script>

<template>
  <main class="page">
    <div v-if="loadError" class="card error rise"><b>选品页加载失败</b><span class="muted">{{ loadError }}</span></div>

    <template v-else-if="options">
      <section class="hero rise" style="--i: 0">
        <div class="headline">
          <h1 v-if="run && run.status === 'done' && run.counts.go" class="title"><span class="accent num-inline">{{ run.counts.go }} 个</span>可以挂</h1>
          <h1 v-else-if="run && run.status === 'done'" class="title">本次无可挂饰品</h1>
          <h1 v-else-if="run && run.status === 'running'" class="title">正在选品</h1>
          <h1 v-else-if="run" class="title">上一次选品{{ STATUS[run.status] }}了</h1>
          <h1 v-else class="title">尚未选品</h1>
          <p class="sub">
            <template v-if="run">
              按「{{ filtersSummary(run.filters, options) }}」圈出 {{ run.pool }} 个 · {{ clock(run.created_at, tz) }}
              <template v-if="run.status === 'done'">已完成，用时 {{ minutes(run.created_at, run.finished_at) }} 分钟</template>
              <template v-if="run.error"> · {{ run.error }}</template>
            </template>
            <template v-else>在下方圈定范围（最多 {{ options.max }} 个模板），筛选值得挂求购的饰品。</template>
          </p>
        </div>
        <NSelect v-if="runs.length > 1" :value="run?.id ?? null" :options="runOptions" class="runs" aria-label="最近几次选品" @update:value="pickRun" />
      </section>

      <section class="card pool rise" style="--i: 1" aria-label="圈池子">
        <div class="pool-row">
          <span class="lbl">大类</span>
          <div class="chks">
            <button v-for="g in options.groups" :key="g.key" type="button" class="chk press" :class="{ on: filters.groups.includes(g.key) }"
              :aria-pressed="filters.groups.includes(g.key)" @click="filters.groups = toggle(filters.groups, g.key)">{{ g.label }}</button>
          </div>
        </div>
        <div v-if="facets?.showWears !== false" class="pool-row">
          <span class="lbl">磨损</span>
          <div class="chks">
            <button v-for="w in facets?.wears ?? options.wears" :key="w.key" type="button" class="chk press" :class="{ on: filters.wears.includes(w.key) }"
              :aria-pressed="filters.wears.includes(w.key)" @click="filters.wears = toggle(filters.wears, w.key)">{{ w.label }}</button>
            <span class="hint">不选 = 全部</span>
          </div>
        </div>
        <div class="pool-row">
          <template v-if="facets?.showVersions !== false">
          <span class="lbl">版本</span>
          <div class="chks grow-0">
            <button v-for="v in facets?.versions ?? options.versions" :key="v.key" type="button" class="chk press" :class="{ on: filters.versions.includes(v.key) }"
              :aria-pressed="filters.versions.includes(v.key)" @click="filters.versions = toggle(filters.versions, v.key)">{{ v.label }}</button>
          </div>
          </template>
          <span class="lbl" :class="{ wide: facets?.showVersions !== false }">在售价</span>
          <NInputNumber v-model:value="filters.price_min" :min="0" clearable placeholder="不限" class="price" aria-label="在售价下限"><template #prefix>¥</template></NInputNumber>
          <span class="muted">～</span>
          <NInputNumber v-model:value="filters.price_max" :min="0" clearable placeholder="不限" class="price" aria-label="在售价上限"><template #prefix>¥</template></NInputNumber>
          <span class="lbl wide">武器</span>
          <NSelect v-model:value="filters.subs" multiple filterable clearable :options="subOptions" placeholder="全部" class="subs" aria-label="具体武器" max-tag-count="responsive" />
        </div>
        <div class="pool-foot">
          <span :class="{ warn: est?.over || estError || est?.count === 0 }">{{ estText }}</span>
          <button type="button" class="btn primary press" :disabled="!canStart" @click="onStart">{{ running ? '进行中…' : '开始选品' }}</button>
        </div>
        <p class="hint">价格区间按悠悠在售最低价筛选，需先取得价格，因此按名称圈定的不能超过 {{ options.max }} 个。{{ options.night }} 为价格快照和估值时段，此时段内不开始新任务，进行中的任务会暂停。</p>
      </section>

      <section v-if="run && run.status === 'running'" class="card progress rise" style="--i: 2" aria-live="polite">
        <div class="ptop"><b>{{ progressText(run) }}</b><span class="muted">已用 {{ minutes(run.created_at, null) }} 分钟</span></div>
        <div class="bar" role="progressbar" :aria-valuenow="Math.round(progressRatio(run) * 100)" aria-valuemin="0" aria-valuemax="100">
          <span :style="{ width: `${progressRatio(run) * 100}%` }" />
        </div>
      </section>

      <template v-if="run && run.results.length">
        <section class="tiles rise" style="--i: 3">
          <button v-for="v in VERDICTS" :key="v.key" type="button" class="tile press" :class="[v.tone, { on: verdict === v.key }]"
            :aria-pressed="verdict === v.key" @click="verdictTouched = true; verdict = verdict === v.key ? null : v.key">
            <b class="num">{{ run.counts[v.key] }}</b><span>{{ v.label }}</span>
          </button>
        </section>
        <section class="card table-card rise" style="--i: 4">
          <PickTable :rows="rows" :rebound-window="run?.rebound_window" :rebound-max="run?.rebound_max" />
          <div v-if="!rows.length" class="empty muted">此档位无饰品</div>
          <div class="foot muted">显示 {{ rows.length }} / {{ run.results.length }} 个<template v-if="run.price_failed"> · {{ run.price_failed }} 个未取到价格</template><template v-if="run.failed"> · {{ run.failed }} 个日线没取到，用的是库里已有的</template></div>
        </section>
        <p class="rule muted">
          按过去一年悠悠日线、SteamDT 当天价格、按固定规则算的参考：上限 = 碰上 7 天坏情况后的到手价，扣掉目标差价和每件最低利润。
          上限达到悠悠最高求购的 90% 即为「可以挂」（成交多在第 2～6 名）；悠悠最高求购取自 SteamDT，为所有子队列中的最高价，结果偏保守。
          只用回测通过的规则；政策风险算不进去。不下单、不改价。
        </p>
      </template>
    </template>

    <template v-else>
      <div class="skeleton" style="width: 360px; height: 42px" />
      <div class="skeleton" style="height: 220px; border-radius: 18px" />
    </template>
  </main>
</template>

<style scoped>
.page { max-width: 1440px; margin: 0 auto; padding: 48px 56px 64px; display: flex; flex-direction: column; gap: 24px; }
.hero { display: flex; align-items: flex-end; gap: 24px; }
.headline { flex: 1; display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.title { margin: 0; font-size: 34px; line-height: 1.2; font-weight: 700; letter-spacing: -0.02em; }
.accent { color: var(--sp-accent); }
.sub { margin: 0; font-size: 14px; color: var(--sp-text2); line-height: 1.6; }
.runs { width: 300px; }
.pool { padding: 18px 22px; display: flex; flex-direction: column; gap: 12px; }
.pool-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.lbl { font-size: 12.5px; color: var(--sp-text3); width: 44px; flex-shrink: 0; }
.lbl.wide { width: auto; margin-left: 16px; }
.chks { display: flex; gap: 6px; flex-wrap: wrap; flex: 1; align-items: center; }
.chks.grow-0 { flex: 0 0 auto; }
.chk {
  height: 30px; padding: 0 11px; border-radius: 8px; border: 1px solid var(--sp-line); background: var(--sp-surface);
  font: inherit; font-size: 13px; color: var(--sp-text2); cursor: pointer;
}
.chk:hover { border-color: var(--sp-line-strong); color: var(--sp-text); }
.chk.on { border-color: var(--sp-accent); background: var(--sp-accent-soft); color: var(--sp-text); font-weight: 600; }
.chk:focus { outline: none; }
.chk:focus-visible { box-shadow: 0 0 0 2px var(--sp-accent); }
.price { width: 120px; }
.subs { min-width: 200px; flex: 1; max-width: 360px; }
.hint { margin: 0; font-size: 12px; color: var(--sp-text3); line-height: 1.6; }
.pool-foot { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding-top: 12px; border-top: 1px solid var(--sp-line-soft); font-size: 13px; color: var(--sp-text2); }
.pool-foot .warn { color: var(--sp-warn); }
.btn {
  height: 38px; padding: 0 18px; border-radius: 10px; border: 1px solid var(--sp-line-strong); background: var(--sp-surface);
  color: var(--sp-text); font: inherit; font-weight: 600; cursor: pointer; white-space: nowrap;
}
.btn.primary { background: var(--sp-accent); border-color: var(--sp-accent); color: var(--sp-on-accent); }
.btn.primary:hover:not(:disabled) { background: var(--sp-accent-hover); }
.btn:disabled { opacity: 0.45; cursor: default; }
.progress { padding: 16px 22px; display: flex; flex-direction: column; gap: 10px; }
.ptop { display: flex; gap: 12px; align-items: baseline; }
.bar { height: 8px; border-radius: 4px; background: var(--sp-bar); overflow: hidden; }
.bar span { display: block; height: 100%; background: var(--sp-accent); border-radius: 4px; transition: width 400ms ease; }
.tiles { display: flex; gap: 12px; flex-wrap: wrap; }
.tile {
  min-width: 130px; padding: 12px 16px; border-radius: 14px; border: 1px solid var(--sp-line); background: var(--sp-surface);
  display: flex; align-items: baseline; gap: 10px; font: inherit; color: var(--sp-text2); cursor: pointer;
}
.tile b { font-size: 24px; color: var(--sp-text); }
.tile.accent b { color: var(--sp-accent); }
.tile.on { border-color: var(--sp-accent); box-shadow: 0 0 0 3px var(--sp-accent-soft); }
.tile:focus { outline: none; }
.tile:focus-visible { box-shadow: 0 0 0 2px var(--sp-accent); }
.table-card { overflow: hidden; padding: 4px 8px 0; }
.empty { padding: 48px; text-align: center; }
.foot { height: 52px; display: flex; align-items: center; justify-content: center; font-size: 13px; }
.rule { margin: 0; font-size: 12.5px; line-height: 1.7; }
.muted { color: var(--sp-text3); }
.error { padding: 28px; display: flex; flex-direction: column; gap: 10px; }
@media (max-width: 1100px) { .page { padding: 32px 24px 48px; } .hero { flex-direction: column; align-items: flex-start; } }
</style>
