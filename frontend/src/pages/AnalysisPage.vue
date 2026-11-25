<script setup lang="ts">
// 分析 /analysis（spec §4.3）：「整体排名在变好还是变差？」视觉稿第 2 版。
// 打开时取一次 /api/analysis，之后每 5 分钟刷新一次（一轮监控大约这个频率；只读数据库，不碰悠悠）。
import { computed, ref } from 'vue'
import { ApiError, fetchAnalysis } from '@/api/client'
import type { Analysis } from '@/api/types'
import EChart from '@/charts/EChart.vue'
import { useMeta } from '@/composables/useMeta'
import PausedCard from './overview/PausedCard.vue'
import { usePolling } from '@/composables/usePolling'
import { useTheme } from '@/composables/useTheme'
import { subLine } from '@/format'
import { dailyOption, distOption } from './analysis/charts'
import { distCaption, headline, slots } from './analysis/logic'

const { tokens } = useTheme()
const data = ref<Analysis | null>(null)
const error = ref<string | null>(null)

async function load() {
  try {
    data.value = await fetchAnalysis()
    error.value = null
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : String(e)
  }
}
const { refresh } = usePolling(load, 300_000)

const head = computed(() => (data.value ? headline(data.value.summary) : null))
const ss = computed(() => (data.value ? slots(data.value.daily) : []))
const runDays = computed(() => ss.value.filter((s) => s.kind === 'day').length)
const daily = computed(() => dailyOption(ss.value, tokens.value))
const dist = computed(() => (data.value ? distOption(data.value.distribution, tokens.value) : {}))
const { meta } = useMeta()
</script>

<template>
  <main class="page">
    <div v-if="error && !data" class="card error rise">
      <b>分析加载失败</b>
      <span class="muted">{{ error }}</span>
      <button type="button" class="btn press" @click="refresh()">重试</button>
    </div>

    <template v-else-if="!data">
      <div class="skeleton" style="width: 460px; height: 42px" />
      <div class="skeleton" style="height: 380px; border-radius: 18px" />
      <div class="grid2"><div v-for="i in 2" :key="i" class="skeleton" style="height: 520px; border-radius: 18px" /></div>
    </template>

    <template v-else-if="head">
      <section class="hero rise" style="--i: 0">
        <h1>前 3 名 <span class="display">{{ head.top3 }}</span> 张{{ head.lead }}<span v-if="head.n != null" class="display" :class="head.dir">{{ head.n }}</span>{{ head.tail }}</h1>
        <p class="sub">
          今日上升 <b class="num ok">{{ data.summary.moved_up }}</b> 张、下降 <b class="num up">{{ data.summary.moved_down }}</b> 张<span class="muted"> · 每天按最后一次检查计</span>
        </p>
      </section>

      <article class="card chart-card rise" style="--i: 1">
        <div class="top">
          <div class="names">
            <h2>近 30 天排进前几名的张数</h2>
            <span class="muted small">每日收盘名次 · 仅含服务运行的 {{ runDays }} 天，断开处标注停机天数</span>
          </div>
          <div class="legend small">
            <span><i style="background: var(--sp-bronze)" />前 3 名</span>
            <span><i style="background: var(--sp-silver)" />前 2 名</span>
            <span><i style="background: var(--sp-gold)" />第 1 名</span>
          </div>
        </div>
        <EChart v-if="ss.length" :option="daily" :height="300" replace-series label="近 30 天每天收盘时前 3 名、前 2 名、第 1 名的张数" />
        <div v-else class="empty muted">近 30 天暂无排名记录</div>
      </article>

      <section class="grid2">
        <article class="card rise" style="--i: 2">
          <div class="names">
            <h2>现在的排名分布</h2>
            <span class="muted small">{{ distCaption(data) }}</span>
          </div>
          <EChart :option="dist" :height="380" label="现在每个名次档位的求购张数" />
          <span class="muted small">横轴为名次；第 1、2、3 名分别为金、银、铜色。</span>
        </article>

        <article class="card rise" style="--i: 3">
          <div class="names">
            <h2>今天变化最大的</h2>
            <span class="muted small">和昨天收盘比 · 点一行进饰品详情</span>
          </div>
          <div class="movers">
            <div v-for="side in (['up', 'down'] as const)" :key="side" class="col">
              <span class="col-title" :class="side === 'up' ? 'ok' : 'up'">{{ side === 'up' ? '升幅最大' : '降幅最大' }}</span>
              <RouterLink v-for="m in data.movers[side]" :key="m.purchase_no" :to="`/item/${m.template_id}`" class="mover">
                <span class="num delta" :class="side === 'up' ? 'ok' : 'up'">{{ side === 'up' ? '↑' : '↓' }}{{ Math.abs(m.rank_change) }}</span>
                <span class="who">
                  <span class="name"><span v-if="m.star" class="star" aria-label="刀或手套">★</span>{{ m.title }}</span>
                  <span v-if="subLine(m)" class="muted tiny">{{ subLine(m) }}</span>
                </span>
                <span class="num muted from">{{ m.prev_rank }} → {{ m.rank }}</span>
              </RouterLink>
              <span v-if="!data.movers[side].length" class="muted small none">今日无</span>
            </div>
          </div>
        </article>
      </section>

      <!-- 充值前检查暂停单（暂停单 spec §5，2026-11-25 从总览挪来）：不常用，放在分析页最后；没有暂停单时不显示 -->
      <PausedCard :tz="meta?.timezone ?? 'Asia/Shanghai'" :youpin-url="meta?.links.youpin_item ?? ''" class="rise" style="--i: 4" />
    </template>
  </main>
</template>

<style scoped>
.page { max-width: 1440px; margin: 0 auto; padding: 36px 56px 64px; display: flex; flex-direction: column; gap: 32px; }
.hero { display: flex; flex-direction: column; gap: 10px; }
h1 { margin: 0; font-size: 34px; line-height: 1.2; font-weight: 700; letter-spacing: -0.02em; }
h1 .down { color: var(--sp-up); }
h1 .up { color: var(--sp-ok); }
.sub { margin: 0; font-size: 14px; color: var(--sp-text2); }
.ok { color: var(--sp-ok); }
.up { color: var(--sp-up); }
h2 { margin: 0; font-size: 18px; font-weight: 700; }
.small { font-size: 12.5px; }
.tiny { font-size: 11.5px; }
.card { padding: 24px 28px; display: flex; flex-direction: column; gap: 16px; }
.chart-card { padding-bottom: 16px; }
.top { display: flex; align-items: center; gap: 24px; }
.names { display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 0; }
.legend { display: flex; gap: 18px; color: var(--sp-text2); }
.legend span { display: inline-flex; align-items: center; gap: 7px; }
.legend i { width: 14px; height: 3px; border-radius: 2px; display: inline-block; }
.empty { height: 300px; display: flex; align-items: center; justify-content: center; }
.grid2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; align-items: stretch; }
.movers { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; }
.col { display: flex; flex-direction: column; min-width: 0; }
.col-title { font-size: 13px; font-weight: 600; padding-bottom: 8px; border-bottom: 1px solid var(--sp-line-soft); }
.mover { display: flex; align-items: center; gap: 10px; padding: 8px 4px; border-bottom: 1px solid var(--sp-line-soft); text-decoration: none; color: inherit; border-radius: 6px; }
.mover:hover { background: var(--sp-surface2); }
.delta { width: 40px; flex-shrink: 0; font-size: 13px; font-weight: 600; }
.who { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
.name { font-size: 13.5px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.star { color: var(--sp-gold); margin-right: 4px; }
.from { flex-shrink: 0; font-size: 12px; }
.none { padding: 12px 0; }
.error { padding: 28px; display: flex; flex-direction: column; gap: 10px; align-items: flex-start; }
.btn { height: 40px; padding: 0 16px; border-radius: 10px; border: 1px solid var(--sp-line-strong); background: var(--sp-surface); color: var(--sp-text); font: inherit; cursor: pointer; }
@media (max-width: 1100px) { .page { padding: 32px 24px 48px; } .grid2 { grid-template-columns: 1fr; } }
</style>
