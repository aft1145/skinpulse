<script setup lang="ts">
// 每日简报 /brief（spec §4.5，视觉稿三期）：「这几天的简报都发了吗？」
// 左边时间线，右边选中那天的前 N 名表；老记录解析不成表的，原样显示正文。选中的日子记在网址里。
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError, fetchBrief } from '@/api/client'
import type { BriefData } from '@/api/types'
import RankMedal from '@/components/RankMedal.vue'
import { useMeta } from '@/composables/useMeta'
import { usePolling } from '@/composables/usePolling'
import { DASH, clock, money, subLine } from '@/format'
import { KIND_LABEL, pickDefault, sentText } from './brief/logic'

const route = useRoute()
const router = useRouter()
const { meta } = useMeta()
const tz = computed(() => meta.value?.timezone ?? 'Asia/Shanghai')
const data = ref<BriefData | null>(null)
const error = ref<string | null>(null)

async function load() {
  try {
    data.value = await fetchBrief()
    error.value = null
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : String(e)
  }
}
const { refresh } = usePolling(load, 300_000)

const selected = computed(() => {
  const rows = data.value?.rows ?? []
  const day = pickDefault(rows, typeof route.query.day === 'string' ? route.query.day : null)
  return rows.find((r) => r.date === day) ?? null
})
function pick(date: string) {
  router.replace({ query: { ...route.query, day: date } })
}
// 数据刷新后，网址里的那天没了（不会发生，但别让右边空着）
watch(data, (d) => {
  if (d && typeof route.query.day === 'string' && !d.rows.some((r) => r.date === route.query.day)) router.replace({ query: {} })
})
const topN = computed(() => data.value?.top_rank ?? 2)
</script>

<template>
  <main class="page">
    <div v-if="error && !data" class="card error rise">
      <b>简报记录加载失败</b>
      <span class="muted">{{ error }}</span>
      <button type="button" class="btn press" @click="refresh()">重试</button>
    </div>

    <template v-else-if="!data">
      <div class="skeleton" style="width: 380px; height: 42px" />
      <div class="grid"><div class="skeleton" style="height: 480px; border-radius: 18px" /><div class="skeleton" style="height: 480px; border-radius: 18px" /></div>
    </template>

    <template v-else>
      <section class="hero rise" style="--i: 0">
        <div class="names">
          <h1>近 30 天发送 <span class="display">{{ data.summary.sent_30d }}</span> 条</h1>
          <p class="sub">
            <template v-if="data.enabled">每天 {{ data.send_at }} 发前一天的前 {{ topN }} 名表<span v-if="data.next_at" class="muted"> · 下一条 {{ clock(data.next_at, tz) }}</span></template>
            <template v-else>每日简报已关闭，可以在「系统」页的参数设置里打开</template>
          </p>
        </div>
        <div class="legend small">
          <span><i class="lg sent" />已发</span>
          <span><i class="lg late" />补发</span>
          <span><i class="lg not_sent" />未发送（附原因）</span>
          <span><i class="lg gap" />无记录</span>
        </div>
      </section>

      <section v-if="data.rows.length" class="grid">
        <nav class="card timeline rise" style="--i: 1" aria-label="按天查看">
          <button
            v-for="(r, i) in data.rows"
            :key="r.date"
            type="button"
            class="day"
            :class="{ on: selected?.date === r.date }"
            :aria-pressed="selected?.date === r.date"
            @click="pick(r.date)"
          >
            <span class="rail-col">
              <span class="dot" :class="r.kind" />
              <span v-if="i < data.rows.length - 1" class="rail" />
            </span>
            <span class="day-body">
              <span class="day-top">
                <b class="num">{{ r.date.slice(5) }}</b>
                <span class="kind" :class="r.kind">{{ KIND_LABEL[r.kind] }}</span>
                <span class="grow" />
                <span class="num muted tiny">{{ sentText(r, tz) }}</span>
              </span>
              <span class="summary small">{{ r.kind === 'not_sent' || r.kind === 'failed' ? r.label : r.summary }}</span>
            </span>
          </button>
        </nav>

        <article v-if="selected" class="card detail rise" style="--i: 2">
          <div class="d-head">
            <h2>{{ selected.date.slice(5) }} 的前 {{ topN }} 名表</h2>
            <span class="small text2">
              <b class="kind" :class="selected.kind">{{ KIND_LABEL[selected.kind] }}</b>
              <template v-if="sentText(selected, tz)"> · {{ sentText(selected, tz) }}</template>
              <template v-if="selected.summary"> · {{ selected.summary }}</template>
            </span>
            <span v-if="selected.delivery" class="small bad">{{ selected.delivery }}</span>
          </div>

          <div v-if="selected.table" class="table" role="table" aria-label="前 N 名表">
            <div class="tr th" role="row">
              <span role="columnheader">名次</span><span role="columnheader">饰品</span>
              <span role="columnheader" class="r">我的求购</span><span role="columnheader" class="r">榜首</span>
              <span role="columnheader" class="r">悠悠在售</span><span role="columnheader">变动</span>
            </div>
            <div v-for="(t, i) in selected.table" :key="i" class="tr" role="row">
              <span role="cell"><RankMedal :rank="t.rank" :size="28" /></span>
              <span role="cell" class="who">
                <span class="name"><span v-if="t.star" class="star" aria-label="刀或手套">★</span>{{ t.title }}</span>
                <span class="muted tiny">{{ subLine(t) || DASH }}</span>
              </span>
              <span role="cell" class="r num strong">{{ money(t.my_price) }}</span>
              <span role="cell" class="r num" :class="{ me: t.is_me }">{{ t.is_me ? '是我' : money(t.top1_price) }}</span>
              <span role="cell" class="r num" :class="{ muted: t.sell_price == null }">{{ money(t.sell_price) }}</span>
              <span role="cell" class="chips">
                <span v-for="c in t.changes" :key="c" class="chip" :class="{ new: c === '新进' }">{{ c }}</span>
              </span>
            </div>
            <span class="muted tiny foot">{{ selected.note ? `${selected.note} · ` : '' }}「—」表示当天无在售价，不是 0 元</span>
          </div>
          <div v-else-if="selected.body" class="raw">
            <span class="muted small">这一天的格式和现在的表不一样，原样显示：</span>
            <pre>{{ selected.body }}</pre>
          </div>
          <p v-else class="muted small">{{ selected.kind === 'gap' ? selected.summary : '当天未发送表格。' }}</p>
        </article>
      </section>
      <p v-else class="card empty muted rise">暂无简报记录。</p>
    </template>
  </main>
</template>

<style scoped>
.page { max-width: 1440px; margin: 0 auto; padding: 36px 56px 64px; display: flex; flex-direction: column; gap: 28px; }
.small { font-size: 12.5px; }
.tiny { font-size: 12px; }
.text2 { color: var(--sp-text2); }
.grow { flex: 1; }
.hero { display: flex; align-items: flex-end; gap: 24px; }
.names { display: flex; flex-direction: column; gap: 10px; flex: 1; }
h1 { margin: 0; font-size: 34px; line-height: 1.2; font-weight: 700; letter-spacing: -0.02em; }
h2 { margin: 0; font-size: 20px; font-weight: 700; }
.sub { margin: 0; font-size: 14px; color: var(--sp-text2); }
.legend { display: flex; gap: 16px; color: var(--sp-text2); padding-bottom: 6px; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.lg { width: 10px; height: 10px; border-radius: 5px; display: inline-block; }
.lg.sent, .dot.sent { background: var(--sp-ok); }
.lg.late, .dot.late { background: var(--sp-warn); }
.lg.not_sent, .dot.not_sent { background: transparent; border: 2px solid var(--sp-idle); }
.dot.failed { background: var(--sp-up); }
.lg.gap, .dot.gap { background: transparent; border: 2px dashed var(--sp-line-strong); }
.grid { display: grid; grid-template-columns: 380px minmax(0, 1fr); gap: 20px; align-items: start; }
.timeline { padding: 14px 12px; display: flex; flex-direction: column; }
.day { display: flex; gap: 14px; text-align: left; padding: 12px 14px 14px 12px; border: none; border-radius: 12px; background: transparent; color: var(--sp-text); font: inherit; cursor: pointer; }
.day:hover { background: var(--sp-surface2); }
.day.on { background: var(--sp-accent-soft); }
.rail-col { display: flex; flex-direction: column; align-items: center; padding-top: 3px; align-self: stretch; }
.dot { width: 12px; height: 12px; border-radius: 6px; flex-shrink: 0; }
.rail { width: 2px; flex: 1; margin-top: 8px; margin-bottom: -18px; background: var(--sp-line-soft); }
.day-body { display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 0; }
.day-top { display: flex; align-items: baseline; gap: 10px; }
.kind { font-size: 12px; font-weight: 600; color: var(--sp-text2); }
.kind.sent { color: var(--sp-ok); } .kind.late { color: var(--sp-warn); } .kind.failed { color: var(--sp-up); } .kind.gap { color: var(--sp-text3); }
.summary { color: var(--sp-text2); line-height: 1.5; }
.detail { padding: 24px 28px; display: flex; flex-direction: column; gap: 18px; min-width: 0; }
.d-head { display: flex; flex-direction: column; gap: 6px; }
.bad { color: var(--sp-up); }
.table { display: flex; flex-direction: column; }
.tr { display: grid; grid-template-columns: 56px minmax(0, 1fr) 96px 96px 96px 220px; gap: 16px; align-items: center; padding: 12px 4px; border-bottom: 1px solid var(--sp-line-soft); }
.tr.th { padding: 0 4px 10px; font-size: 12px; color: var(--sp-text3); border-bottom: 1px solid var(--sp-line); }
.r { text-align: right; }
.strong { font-weight: 600; }
.me { color: var(--sp-gold); font-weight: 700; }
.who { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.name { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.star { color: var(--sp-gold); margin-right: 4px; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { font-size: 12px; padding: 2px 8px; border-radius: 6px; background: var(--sp-surface2); color: var(--sp-text2); white-space: nowrap; }
.chip.new { background: var(--sp-accent-soft); color: var(--sp-accent); }
.foot { padding-top: 12px; }
.raw { display: flex; flex-direction: column; gap: 10px; }
pre { margin: 0; padding: 16px 18px; border-radius: 12px; background: var(--sp-surface2); border: 1px solid var(--sp-line-soft); font: 12.5px/1.7 'JetBrains Mono', 'PingFang SC', monospace; color: var(--sp-text2); white-space: pre-wrap; max-height: 560px; overflow: auto; }
.empty { padding: 28px; margin: 0; }
.error { padding: 28px; display: flex; flex-direction: column; gap: 10px; align-items: flex-start; }
.btn { height: 40px; padding: 0 16px; border-radius: 10px; border: 1px solid var(--sp-line-strong); background: var(--sp-surface); color: var(--sp-text); font: inherit; cursor: pointer; }
@media (max-width: 1100px) { .page { padding: 32px 24px 48px; } .grid { grid-template-columns: 1fr; } .tr { grid-template-columns: 44px minmax(0, 1fr) 72px 72px 72px; } .tr > :last-child { grid-column: 2 / -1; } }
</style>
