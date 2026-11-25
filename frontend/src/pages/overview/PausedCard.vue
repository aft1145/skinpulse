<script setup lang="ts">
// 「暂停中的求购单」卡片（暂停单 spec §5，视觉稿已确认）：总览最上面，充值前点一下「检查一次」。
// 自己取数：平时跟着总览 60 秒刷新，检查中每 3 秒拉一次进度。看板只读库，检查由服务在后台跑。
import { Check, CirclePause, Clock, RefreshCw, Search } from '@lucide/vue'
import { useIntervalFn } from '@vueuse/core'
import { useMessage } from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { ApiError, fetchPaused, startPausedCheck } from '@/api/client'
import type { PausedPayload } from '@/api/types'
import { usePolling } from '@/composables/usePolling'
import PausedTable from './PausedTable.vue'
import {
  cardState, lastFailure, needsRecheck, neverTitle, progress, riskHeadline, subline, tierBlocks, type RiskTier,
} from './pausedLogic'

const props = defineProps<{ tz: string; youpinUrl: string }>()
const message = useMessage()

const data = ref<PausedPayload | null>(null)
async function load() {
  try {
    data.value = await fetchPaused()
  } catch {
    /* 取不到就保持上一次的样子；后端还是旧版本时整张卡片不显示 */
  }
}
const { refresh } = usePolling(load, 60_000)

const state = computed(() => cardState(data.value))
const running = computed(() => state.value === 'running')
const fast = useIntervalFn(() => void load(), 3_000, { immediate: false })
watch(running, (r, was) => {
  if (r) fast.resume()
  else {
    fast.pause()
    // 刚查完：失败的话说一声（结果卡片还是上一次的）
    const run = data.value?.run
    if (was && run && run.status !== 'done') message.error(`检查未完成：${run.error ?? '服务已重启'}`)
  }
})

const head = computed(() => (data.value ? riskHeadline(data.value) : null))
const never = computed(() => (data.value ? neverTitle(data.value) : null))
const sub = computed(() => (data.value ? subline(data.value, props.tz) : null))
const recheck = computed(() => (data.value ? needsRecheck(data.value) : false))
const failure = computed(() => (data.value ? lastFailure(data.value) : null))
const blocks = computed(() => (data.value ? tierBlocks(data.value) : []))
const otherCount = computed(() => data.value?.summary?.tiers.other.count ?? 0)
const prog = computed(() => (data.value?.run && running.value ? progress(data.value.run) : null))

const open = ref<RiskTier | null>(null)
const openCount = computed(() => blocks.value.find((b) => b.key === open.value)?.count ?? 0)
function toggle(k: RiskTier) {
  open.value = open.value === k ? null : k
}

const starting = ref(false)
async function check() {
  starting.value = true
  try {
    await startPausedCheck()
    open.value = null
    await refresh()
  } catch (e) {
    message.error(e instanceof ApiError ? e.message : '无法开始检查')
  } finally {
    starting.value = false
  }
}

const yuan = (v: number) => `¥${Math.round(v).toLocaleString('en-US')}`
</script>

<template>
  <section v-if="state !== 'hidden'" class="card paused" :class="state" aria-labelledby="paused-title">
    <!-- 检查中 -->
    <template v-if="state === 'running' && prog">
      <span class="eyebrow"><CirclePause :size="15" />暂停中的求购单</span>
      <h2 id="paused-title" class="display title sm">正在检查…</h2>
      <div class="prog">
        <div class="prog-row"><span>{{ prog.label }}</span><span class="num muted2">{{ prog.count }}</span></div>
        <div class="bar" role="progressbar" aria-label="检查进度" :aria-valuenow="prog.pct" aria-valuemin="0" aria-valuemax="100">
          <div :style="{ width: `${prog.pct}%` }" />
        </div>
        <div class="steps">
          <template v-for="(s, i) in prog.steps" :key="s.key">
            <span v-if="i" class="dot">·</span>
            <span :class="s.state"><Check v-if="s.state === 'done'" :size="12" :stroke-width="2.6" />{{ s.label }}</span>
          </template>
        </div>
      </div>
      <p class="note">还要约 {{ prog.minutes }} 分钟。可以离开这个页面，查完结果会留着。</p>
    </template>

    <!-- 查过、没有危险的单：一行 -->
    <div v-else-if="state === 'safe' && data?.summary" class="safe-row">
      <span class="ok-mark" aria-hidden="true"><Check :size="17" :stroke-width="2.2" /></span>
      <div class="safe-text">
        <b id="paused-title">暂停中的 <span class="num">{{ data.summary.total }}</span> 张恢复后都不靠前</b>
        <span class="muted">{{ sub?.info }} · 可以放心充值</span>
      </div>
      <button type="button" class="btn press" :disabled="starting" @click="check">重新检查</button>
    </div>

    <!-- 从没查过 / 查过之后又有新暂停（结果里没有危险的单） -->
    <template v-else-if="state === 'never' && never">
      <span class="eyebrow"><CirclePause :size="15" />暂停中的求购单</span>
      <h2 id="paused-title" class="display title sm">
        <template v-if="never.n != null"><span class="hot num-inline">{{ never.n }} 张</span></template>{{ never.text }}
      </h2>
      <p v-if="failure" class="sub"><span class="warn"><Clock :size="14" />{{ failure }}</span></p>
      <p class="note">余额不够时悠悠会自动暂停，充值后自动恢复。查一次约 8 分钟，看恢复后会不会倒挂或直接第 1。</p>
      <button type="button" class="btn primary press start" :disabled="starting" @click="check">
        <Search :size="15" />检查一次
      </button>
    </template>

    <!-- 有危险的单 -->
    <template v-else-if="state === 'risky' && head && data">
      <div class="top">
        <div class="headline">
          <span class="eyebrow"><CirclePause :size="15" />暂停中的求购单 · 充值后会自动恢复</span>
          <h2 id="paused-title" class="display title"><span class="hot num-inline">{{ head.n }} 张</span>{{ head.text }}</h2>
          <p class="sub">
            <span v-if="sub?.warn" class="warn"><Clock :size="14" />{{ sub.warn }}</span>
            <span>{{ sub?.info }}</span>
          </p>
        </div>
        <button type="button" class="btn press" :class="{ primary: recheck }" :disabled="starting" @click="check">
          <RefreshCw :size="15" />重新检查
        </button>
      </div>
      <div class="tiers" role="group" aria-label="按档位查看">
        <button
          v-for="b in blocks"
          :key="b.key"
          type="button"
          class="tier press"
          :class="[b.key, { on: open === b.key }]"
          :aria-pressed="open === b.key"
          :disabled="!b.count"
          @click="toggle(b.key)"
        >
          <span class="t-label"><i aria-hidden="true" />{{ b.label }}</span>
          <span class="t-n display">{{ b.count }}</span>
          <span class="t-amt">占用 <span class="num">{{ yuan(b.amount) }}</span></span>
        </button>
      </div>
      <PausedTable v-if="open" :items="data.items" :tier="open" :total="openCount" :accounts="data.accounts" :youpin-url="youpinUrl" />
      <p class="note">
        <template v-if="!open">点一档看明细 · </template>其余 <span class="num">{{ otherCount }}</span> 张恢复后不靠前<template v-if="open">，不列出。只读检查，不会改价或撤单</template>
      </p>
    </template>
  </section>
</template>

<style scoped>
.paused { padding: 24px 28px 20px; display: flex; flex-direction: column; gap: 18px; }
.paused.risky, .paused.never {
  border-color: color-mix(in srgb, var(--sp-warn) 42%, transparent);
  box-shadow: var(--sp-shadow), 0 0 0 4px color-mix(in srgb, var(--sp-warn) 5%, transparent);
}
.paused.safe { padding: 16px 24px; }
.eyebrow { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--sp-text2); }
.title { margin: 0; font-size: 30px; font-weight: 700; line-height: 1.2; }
.title.sm { font-size: 22px; line-height: 1.3; }
.hot { color: var(--sp-warn); }
.top { display: flex; align-items: flex-start; gap: 20px; flex-wrap: wrap; }
.headline { display: flex; flex-direction: column; gap: 8px; flex: 1 1 520px; min-width: 0; }
.sub { margin: 0; font-size: 13px; color: var(--sp-text3); display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: center; }
.warn { display: inline-flex; align-items: center; gap: 6px; color: var(--sp-warn); font-weight: 500; }
.note { margin: 0; font-size: 12.5px; color: var(--sp-text3); line-height: 1.6; }
.muted { color: var(--sp-text3); }
.muted2 { color: var(--sp-text2); }
.btn {
  display: inline-flex; align-items: center; gap: 8px; min-height: 40px; padding: 0 16px; border-radius: 10px;
  border: 1px solid var(--sp-line-strong); background: var(--sp-surface2); color: var(--sp-text); font: inherit; font-size: 13.5px;
  cursor: pointer; white-space: nowrap;
}
.btn:hover:not(:disabled) { border-color: var(--sp-text3); }
.btn.primary { border-color: var(--sp-accent); background: var(--sp-accent); color: var(--sp-on-accent); font-weight: 600; }
.btn.primary:hover:not(:disabled) { filter: brightness(1.06); }
.btn:disabled { opacity: 0.6; cursor: default; }
.btn:focus-visible, .tier:focus-visible { outline: 2px solid var(--sp-accent); outline-offset: 2px; }
.start { align-self: flex-start; }

.tiers { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.tier {
  font: inherit; text-align: left; display: flex; flex-direction: column; gap: 6px; padding: 14px 16px; border-radius: 14px;
  border: 1px solid var(--sp-line); background: var(--sp-surface2); color: var(--sp-text); cursor: pointer;
}
.tier:disabled { opacity: 0.45; cursor: default; }
.t-label { display: flex; align-items: center; gap: 6px; font-size: 12.5px; }
.t-label i { width: 7px; height: 7px; border-radius: 2px; background: currentColor; }
.t-n { font-size: 26px; font-weight: 700; line-height: 1; }
.t-amt { font-size: 12px; color: var(--sp-text3); }
.tier.inverted .t-label { color: var(--sp-warn); }
.tier.top1 .t-label { color: var(--sp-gold); }
.tier.near .t-label { color: var(--sp-accent); }
.tier.near_sell .t-label { color: var(--sp-text2); }
.tier.inverted:not(:disabled):not(.on) { border-color: color-mix(in srgb, var(--sp-warn) 35%, transparent); }
.tier.on.inverted { border-color: var(--sp-warn); background: var(--sp-warn-soft); }
.tier.on.top1 { border-color: var(--sp-gold); background: var(--sp-gold-soft); }
.tier.on.near { border-color: var(--sp-accent); background: var(--sp-accent-soft); }
.tier.on.near_sell { border-color: var(--sp-text3); background: var(--sp-surface); }

.prog { display: flex; flex-direction: column; gap: 8px; max-width: 520px; }
.prog-row { display: flex; justify-content: space-between; font-size: 13px; }
.bar { height: 6px; border-radius: 3px; background: var(--sp-line-soft); overflow: hidden; }
.bar div { height: 6px; border-radius: 3px; background: var(--sp-accent); transition: width 400ms ease; }
.steps { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--sp-text3); }
.steps .done { color: var(--sp-ok); display: inline-flex; align-items: center; gap: 3px; }
.steps .current { color: var(--sp-text); }

.safe-row { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.ok-mark { display: flex; align-items: center; justify-content: center; width: 34px; height: 34px; border-radius: 10px; background: var(--sp-ok-soft); color: var(--sp-ok); }
.safe-text { display: flex; flex-direction: column; gap: 3px; flex: 1 1 260px; min-width: 0; }
.safe-text b { font-size: 15px; }
.safe-text .muted { font-size: 12.5px; }

@media (max-width: 720px) {
  .paused { padding: 20px 18px 16px; }
  .title { font-size: 24px; }
  .tiers { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (prefers-reduced-motion: reduce) {
  .bar div { transition: none; }
}
</style>
