<script setup lang="ts">
// 系统 /system（spec §4.6，视觉稿第 3 版 + 三期补充）：「服务健康吗？」
// 每 60 秒刷新一次（只读数据库）。换 token、改设置是这页上仅有的两个写操作。
import { computed, ref, watch } from 'vue'
import { ArrowLeftRight, Check, KeyRound, Plus, TriangleAlert } from '@lucide/vue'
import { ApiError, fetchStatus } from '@/api/client'
import type { Account, NotificationItem, StatusData, TokenResult } from '@/api/types'
import RankMedal from '@/components/RankMedal.vue'
import { useMeta } from '@/composables/useMeta'
import { usePolling } from '@/composables/usePolling'
import { clock, subLine } from '@/format'
import RestartButton from './system/RestartButton.vue'
import SettingsPanel from './system/SettingsPanel.vue'
import PushPlusDialog from './system/PushPlusDialog.vue'
import AccountCard from './system/AccountCard.vue'
import TokenDialog from './system/TokenDialog.vue'
import { agreement, bytes, channelView, headline, platformGroups, restartable, serviceCmd, tokenLeft, tokenRing, uptime } from './system/logic'

const { meta, refresh: refreshMeta } = useMeta()
const tz = computed(() => meta.value?.timezone ?? 'Asia/Shanghai')
const data = ref<StatusData | null>(null)
const error = ref<string | null>(null)
const tokenOpen = ref(false)
// 多账号（多账号 spec §8）：两个以上的号（含停用的）就每号一张卡片；「添加账号」一直都有
const addOpen = ref(false)
const addPlatform = ref<'youpin' | 'buff'>('youpin')
function openAdd(p: 'youpin' | 'buff') {
  addPlatform.value = p
  addOpen.value = true
}
const tokenFor = ref<Account | null>(null)
const accountList = computed(() => data.value?.accounts ?? [])
const showCards = computed(() => accountList.value.length >= 2)
// 悠悠号至少留一个启用的；BUFF 号随时能停（BUFF spec §8）
const youpinEnabled = computed(() => accountList.value.filter((a) => a.enabled && a.platform !== 'buff').length)
const canDisable = (a: Account) => a.platform === 'buff' || youpinEnabled.value >= 2
function openToken(a: Account) {
  tokenFor.value = a
  tokenOpen.value = true
}
watch(tokenOpen, (v) => { if (!v) tokenFor.value = null })
const pushOpen = ref(false)

async function load() {
  try {
    data.value = await fetchStatus()
    error.value = null
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : String(e)
  }
}
const { refresh } = usePolling(load, 60_000)

const head = computed(() => (data.value ? headline(data.value, tz.value) : null))
const channels = computed(() => (data.value ? data.value.channels.map((ch) => ({ ...ch, v: channelView(ch, tz.value) })) : []))
// 多号时按平台分组（2026-11-25）：悠悠的通道挪进悠悠那一行，PushPlus 和 ntfy 合成「推送」卡片
const groups = computed(() => (data.value && showCards.value ? platformGroups(accountList.value, data.value.channels, tz.value) : []))
const pushChannels = computed(() => channels.value.filter((ch) => ch.id === 'pushplus' || ch.id === 'ntfy'))
const agree = computed(() => (data.value ? agreement(data.value.verification) : null))
const ring = computed(() => {
  const f = tokenRing(data.value?.account.days_left ?? null)
  const c = 2 * Math.PI * 40
  return `${(f * c).toFixed(1)} ${c.toFixed(1)}`
})
const BRIEF_MARK: Record<string, string> = { sent: '✓', late: '✓', not_sent: '–', failed: '!', gap: '·' }
const STATUS_TEXT: Record<string, string> = { sent: '已送出', failed: '失败', suppressed: '被合并或限流' }

function onToken(_r: TokenResult) {
  refresh()
  refreshMeta()
}
function onChanged() {
  refresh()
  refreshMeta()
}

// 成交推送 spec §7.3：交易中、求购成交两类用对应颜色加粗，求购成交带上成交时间
// 类名带前缀：这个页面已经有 .fill（核对进度条），撞名会把文字画成一条色块
const eventClass = (n: NotificationItem) =>
  n.kind === 'IN_TRADE' ? 'ev ev-trade' : n.kind === 'FILLED' ? 'ev ev-fill' : n.kind === 'MARKET' ? 'ev ev-market' : ''
const eventText = (n: NotificationItem) =>
  n.kind === 'FILLED' && n.filled_at ? `${n.event} · 成交于 ${clock(n.filled_at, tz.value)}` : n.event
</script>

<template>
  <main class="page">
    <div v-if="error && !data" class="card error rise">
      <b>系统状态加载失败</b>
      <span class="muted">{{ error }}</span>
      <button type="button" class="btn press" @click="refresh()">重试</button>
    </div>

    <template v-else-if="!data || !head">
      <div class="skeleton" style="width: 360px; height: 56px" />
      <div class="grid4"><div v-for="i in 4" :key="i" class="skeleton" style="height: 150px; border-radius: 16px" /></div>
      <div class="skeleton" style="height: 420px; border-radius: 18px" />
    </template>

    <template v-else>
      <section class="hero rise" style="--i: 0">
        <span class="badge" :class="head.ok ? 'ok' : 'warn'" aria-hidden="true">
          <Check v-if="head.ok" :size="28" :stroke-width="2.4" />
          <TriangleAlert v-else :size="26" :stroke-width="2.2" />
        </span>
        <div class="names">
          <h1>{{ head.text }}</h1>
          <span class="sub">{{ head.sub }}</span>
        </div>
        <RestartButton :status-cmd="serviceCmd(data.runtime, 'status')" v-if="data.headline.issue === 'code_outdated' && restartable(data)" class="hero-act" :started-at="data.runtime.started_at" primary />
      </section>

      <section v-if="!showCards" class="grid4">
        <article v-for="(ch, i) in channels" :key="ch.id" class="card channel rise" :style="{ '--i': i + 1 }">
          <div class="ch-head">
            <span class="dot" :class="ch.state" />
            <span class="ch-name">{{ ch.name }}</span>
            <span class="pill" :class="ch.state">{{ ch.v.state }}</span>
          </div>
          <div class="kv">
            <span class="muted small">{{ ch.v.key }}</span>
            <span class="num">{{ ch.v.value }}</span>
          </div>
          <span class="muted small">{{ ch.v.note }}</span>
          <button v-if="ch.id === 'pushplus' && !ch.configured && !meta?.demo" type="button" class="btn press" @click="pushOpen = true">录入 PushPlus token</button>
        </article>
      </section>

      <!-- 多号时按平台分组（2026-11-25）：每个平台一整行，左边平台和通道状态，右边这个平台的号 -->
      <section v-if="showCards" class="platforms rise" style="--i: 1" aria-labelledby="platforms-title">
        <h2 id="platforms-title" class="sec-title">平台和账号</h2>
        <article v-for="g in groups" :key="g.id" class="card platform" :class="`p-${g.id}`">
          <div class="p-id">
            <div class="p-head">
              <span class="p-mark" aria-hidden="true">{{ g.mark }}</span>
              <div class="names">
                <b class="p-name">{{ g.name }}</b>
                <span class="muted small">{{ g.sub }}</span>
              </div>
            </div>
            <div class="p-lines">
              <div v-for="l in g.lines" :key="l.key" class="p-line small">
                <span class="muted">{{ l.name }}</span>
                <span class="p-line-v"><span class="num text2">{{ l.value }}</span><span class="pill" :class="l.state">{{ l.text }}</span></span>
              </div>
            </div>
          </div>
          <div class="p-accounts">
            <AccountCard
              v-for="a in g.accounts"
              :key="a.id"
              :account="a"
              :tz="tz"
              :can-disable="canDisable(a)"
              @changed="onChanged"
              @update-token="openToken"
            />
            <button type="button" class="p-add press" @click="openAdd(g.id)"><Plus :size="14" />{{ g.id === 'buff' ? '添加 BUFF 账号' : `添加${g.name}账号` }}</button>
          </div>
        </article>
      </section>

      <section class="rows" :class="{ multi: showCards }">
        <article v-if="!showCards" class="card account rise" style="--i: 5">
          <div class="row-head">
            <h2>悠悠账号</h2>
            <span class="muted small">{{ data.account.nickname ?? '—' }}</span>
            <button type="button" class="add-btn press" @click="openAdd('youpin')"><Plus :size="14" />添加账号</button>
          </div>
          <div class="token">
            <div class="ring">
              <svg width="96" height="96" viewBox="0 0 96 96" aria-hidden="true">
                <circle cx="48" cy="48" r="40" fill="none" stroke="var(--sp-bar)" stroke-width="9" />
                <circle
                  cx="48"
                  cy="48"
                  r="40"
                  fill="none"
                  :stroke="data.account.token_valid ? 'var(--sp-accent)' : 'var(--sp-up)'"
                  stroke-width="9"
                  stroke-linecap="round"
                  :stroke-dasharray="ring"
                  transform="rotate(-90 48 48)"
                />
              </svg>
              <span class="ring-num"><b class="display">{{ data.account.days_left ?? '—' }}</b><small>天</small></span>
            </div>
            <div class="token-text">
              <b v-if="!data.account.token_valid" class="bad">token 已失效</b>
              <b v-else-if="data.account.expires_at">token 还能用 {{ tokenLeft(data.account.expires_at) }}</b>
              <b v-else>token 到期时间未知</b>
              <span class="small text2">{{ data.account.expires_at ? `${clock(data.account.expires_at, tz)} 到期` : '读不到到期时间' }}</span>
              <span class="muted tiny">到期前 2 天会在顶栏提醒</span>
            </div>
          </div>
          <button type="button" class="btn primary press" @click="tokenOpen = true"><KeyRound :size="15" />更新 token</button>
          <span class="muted tiny">在浏览器登录悠悠后，把请求头里的 authorization 粘贴进来；先校验再存进本机的钥匙串（Windows 上是凭据管理器），不会显示或回传。</span>
        </article>
        <article v-if="showCards" class="card push-card rise" style="--i: 5">
          <div class="row-head col-head">
            <h2>推送</h2>
            <span class="muted small">悠悠、BUFF 的提醒都走这里</span>
          </div>
          <div v-for="ch in pushChannels" :key="ch.id" class="push-ch">
            <span class="names">
              <b>{{ ch.name }}</b>
              <span class="muted tiny">{{ ch.id === 'ntfy' && !ch.configured ? '可选，未录入 topic' : `${ch.v.key} ${ch.v.value}` }}</span>
            </span>
            <span class="pill" :class="ch.state">{{ ch.v.state }}</span>
          </div>
          <button v-if="pushChannels.some((ch) => ch.id === 'pushplus' && !ch.configured) && !meta?.demo" type="button" class="btn press" @click="pushOpen = true">录入 PushPlus token</button>
        </article>
        <article class="card verify-card rise" style="--i: 6">
          <div class="row-head col-head">
            <h2>排名核对</h2>
            <span class="muted small">每轮抽几张，用公开队列复算，看悠悠上报的排名准不准</span>
          </div>
          <template v-if="agree?.pct != null">
            <div class="big-line">
              <span class="display big">{{ agree.pct }}%</span>
              <span class="muted small">近 24 小时一致</span>
            </div>
            <div class="track"><div class="fill" :style="{ width: `${agree.pct}%` }" /></div>
            <div class="stats small text2">
              <span>抽查 <b class="num">{{ data.verification.checked_24h }}</b> 次</span>
              <span>一致 <b class="num">{{ data.verification.agreed_24h }}</b></span>
              <span>不一致 <b class="num">{{ agree.disagreed }}</b></span>
              <span v-if="data.verification.inconclusive_24h">无法判定 <b class="num">{{ data.verification.inconclusive_24h }}</b></span>
            </div>
          </template>
          <p v-else class="muted small">近 24 小时暂无核对记录。</p>
          <span v-if="data.verification.last_disagreement" class="muted tiny">
            最近一次不一致 {{ clock(data.verification.last_disagreement.ts, tz) }}：上报第
            {{ data.verification.last_disagreement.reported_rank ?? '—' }} 名、实际第
            {{ data.verification.last_disagreement.computed_rank ?? '—' }} 名。悠悠的排名会滞后几分钟，告警前会再实时核对一次。
          </span>
        </article>
        <article class="card runtime rise" style="--i: 7">
          <div>
            <span class="muted tiny">运行方式</span>
            <b>{{ data.runtime.supervisor ? '登录后自动启动' : '手动启动' }}</b>
            <span class="muted tiny">{{ data.runtime.supervisor ? '崩溃 30 秒内拉起' : '关掉终端就停' }}</span>
          </div>
          <div>
            <span class="muted tiny">已连续运行</span>
            <b>{{ uptime(data.runtime.started_at) }}</b>
            <span class="muted tiny">{{ data.runtime.started_at ? `自 ${clock(data.runtime.started_at, tz)}` : '启动时间未知' }}</span>
          </div>
          <div>
            <span class="muted tiny">防睡眠</span>
            <b>{{ data.runtime.supervisor ? '接电源时生效' : '未开启' }}</b>
            <span class="muted tiny">{{ data.runtime.supervisor ? '合盖或用电池仍会睡' : data.runtime.platform }}</span>
          </div>
          <div>
            <span class="muted tiny">数据库</span>
            <b>{{ bytes(data.runtime.db_bytes) }}</b>
            <span class="muted tiny num">{{ data.runtime.db_path }}</span>
          </div>
          <div v-if="data.runtime.supervisor" class="runtime-foot">
            <RestartButton :status-cmd="serviceCmd(data.runtime, 'status')" v-if="restartable(data) && data.headline.issue !== 'code_outdated'" :started-at="data.runtime.started_at" />
            <span class="muted tiny">取消开机自启：在终端运行 {{ serviceCmd(data.runtime, 'uninstall') }}</span>
          </div>
        </article>
        <article class="card brief-card rise" style="--i: 8">
          <div class="row-head">
            <div class="names">
              <h2>每日简报</h2>
              <span class="muted small">{{ data.brief.enabled ? `每天 ${data.brief.send_at} 发前一天的前 ${data.brief.top_rank} 名表` : '每日简报已关闭' }}</span>
            </div>
            <RouterLink to="/brief" class="link small">查看全部</RouterLink>
          </div>
          <div class="days">
            <RouterLink v-for="b in [...data.briefs].reverse()" :key="b.date" :to="`/brief?day=${b.date}`" class="day" :class="b.kind">
              <span class="num tiny muted">{{ b.date.slice(5) }}</span>
              <span class="mark">{{ BRIEF_MARK[b.kind] }}</span>
              <span class="tiny">{{ b.kind === 'late' ? '补发' : b.kind === 'sent' ? '已发' : b.kind === 'gap' ? '无记录' : b.kind === 'failed' ? '失败' : '未发' }}</span>
            </RouterLink>
          </div>
          <p v-if="data.valuation" class="valuation muted small">
            每日估值：{{ data.valuation.enabled ? `每天 ${data.valuation.run_at}` : '已关闭' }}
            · {{ data.valuation.last ? `最近算到 ${data.valuation.last.as_of.slice(5)}，${data.valuation.last.valued} 件` : '尚未计算' }}
          </p>
        </article>
        <article class="card pushes wide rise" style="--i: 9">
          <div class="row-head">
            <div class="names">
              <h2>最近推送</h2>
              <span class="muted small">
                累计送出 {{ data.notifications.totals.sent }} · 失败 {{ data.notifications.totals.failed }} · 被合并或限流
                {{ data.notifications.totals.suppressed }}
              </span>
            </div>
            <span class="muted tiny">「已送出」仅表示 PushPlus 已接收，以微信实际收到为准</span>
          </div>
          <div v-for="n in data.notifications.recent" :key="n.id" class="push">
            <span class="num muted small">{{ clock(n.at, tz) }}</span>
            <span v-if="n.kind === 'IN_TRADE'" class="kind k-trade" role="img" aria-label="交易中"><ArrowLeftRight :size="13" :stroke-width="2.4" /></span>
            <span v-else-if="n.kind === 'FILLED'" class="kind k-fill" role="img" aria-label="求购成交"><Check :size="14" :stroke-width="2.6" /></span>
            <span v-else-if="n.kind === 'MARKET'" class="kind k-market" role="img" aria-label="接近成交价"><TriangleAlert :size="13" :stroke-width="2.4" /></span>
            <RankMedal v-else :rank="n.rank" :size="26" />
            <span class="who">
              <span class="name"><span v-if="n.star" class="star" aria-label="刀或手套">★</span>{{ n.title }}</span>
              <span class="muted tiny">{{ subLine(n) ? `${subLine(n)} · ` : '' }}<span :class="eventClass(n)">{{ eventText(n) }}</span></span>
            </span>
            <span class="st" :class="n.status">{{ STATUS_TEXT[n.status] ?? n.status }}</span>
          </div>
          <p v-if="!data.notifications.recent.length" class="muted small">暂无推送记录。</p>
        </article>
      </section>

      <SettingsPanel class="rise" style="--i: 10" :hide-buff="!accountList.some((a) => a.platform === 'buff')" :hide-dashboard="accountList.filter((a) => a.enabled).length < 2" @saved="onChanged" />
      <TokenDialog
        v-model:show="tokenOpen"
        :tz="tz"
        :account-id="tokenFor?.id"
        :account-label="showCards ? tokenFor?.label : undefined"
        :platform="tokenFor?.platform ?? 'youpin'"
        @done="onToken"
      />
      <TokenDialog v-model:show="addOpen" :tz="tz" mode="add" :platform="addPlatform" @done="onToken" />
      <PushPlusDialog v-model:show="pushOpen" @done="() => { refresh(); refreshMeta() }" />
    </template>
  </main>
</template>

<style scoped>
.page { max-width: 1440px; margin: 0 auto; padding: 36px 56px 64px; display: flex; flex-direction: column; gap: 28px; }
.small { font-size: 12.5px; }
.tiny { font-size: 12px; }
.text2 { color: var(--sp-text2); }
.hero { display: flex; align-items: center; gap: 18px; }
.badge { width: 56px; height: 56px; border-radius: 16px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.badge.ok { background: var(--sp-ok-soft); color: var(--sp-ok); }
.badge.warn { background: var(--sp-warn-soft); color: var(--sp-warn); }
.names { display: flex; flex-direction: column; gap: 4px; min-width: 0; flex: 1; }
.hero-act { margin-left: auto; }
.runtime > .runtime-foot { grid-column: 1 / -1; flex-direction: row; align-items: center; gap: 14px; flex-wrap: wrap; padding-top: 14px; border-top: 1px solid var(--sp-line-soft); }
h1 { margin: 0; font-size: 34px; line-height: 1.2; font-weight: 700; letter-spacing: -0.02em; }
h2 { margin: 0; font-size: 18px; font-weight: 700; }
.sub { font-size: 14px; color: var(--sp-text2); }
.grid4 { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }
.channel { border-radius: 16px; padding: 20px 22px; display: flex; flex-direction: column; gap: 12px; }
.ch-head { display: flex; align-items: center; gap: 8px; }
.ch-name { font-weight: 600; flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dot { width: 8px; height: 8px; border-radius: 4px; background: var(--sp-idle); box-shadow: 0 0 0 4px var(--sp-idle-soft); }
.dot.ok { background: var(--sp-ok); box-shadow: 0 0 0 4px var(--sp-ok-soft); }
.dot.warn { background: var(--sp-warn); box-shadow: 0 0 0 4px var(--sp-warn-soft); }
.dot.error { background: var(--sp-up); box-shadow: 0 0 0 4px color-mix(in srgb, var(--sp-up) 16%, transparent); }
.pill { font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 6px; background: var(--sp-idle-soft); color: var(--sp-text2); }
.pill.ok { background: var(--sp-ok-soft); color: var(--sp-ok); }
.pill.warn { background: var(--sp-warn-soft); color: var(--sp-warn); }
.pill.error { background: color-mix(in srgb, var(--sp-up) 14%, transparent); color: var(--sp-up); }
.kv { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.kv .num { font-size: 16px; font-weight: 600; }
.rows { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; align-items: stretch; }
/* 多号时按平台分组（2026-11-25）：悠悠用主色，BUFF 用总览表格里 BUFF 标签的蓝 */
.platforms { display: flex; flex-direction: column; gap: 14px; }
.sec-title { margin: 0; font-size: 15px; font-weight: 600; color: var(--sp-text2); }
.platform { --p: var(--sp-accent); --p-soft: color-mix(in srgb, var(--sp-accent) 12%, transparent);
  padding: 20px; border-radius: 16px; display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 20px; align-items: start; }
.platform.p-buff { --p: var(--sp-info); --p-soft: var(--sp-info-soft); }
.p-id { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.p-head { display: flex; align-items: center; gap: 12px; }
.p-mark {
  width: 36px; height: 36px; flex-shrink: 0; border-radius: 10px; display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 16px; color: var(--p); background: var(--p-soft);
  border: 1px solid color-mix(in srgb, var(--p) 38%, transparent);
}
.p-name { font-size: 17px; }
.p-head .names { display: flex; flex-direction: column; gap: 2px; }
.p-lines { display: flex; flex-direction: column; gap: 8px; padding-top: 12px; border-top: 1px solid var(--sp-line-soft); }
.p-line { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.p-line-v { display: flex; align-items: center; gap: 8px; white-space: nowrap; }
.p-accounts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; min-width: 0; }
.p-add {
  min-height: 44px; border-radius: 12px; border: 1px dashed var(--sp-line-strong); background: transparent; color: var(--sp-text3);
  font: inherit; font-size: 13px; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 6px;
}
.p-add:hover { color: var(--p); border-color: var(--p); }
.p-add:focus-visible { outline: 2px solid var(--sp-accent); outline-offset: 2px; }
@media (max-width: 1100px) { .platform { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 760px) { .p-accounts { grid-template-columns: minmax(0, 1fr); } }
/* 多号时下面三张并排（推送、排名核对、每日简报），运行环境和最近推送各占一整行：不会再空出一块 */
.rows.multi { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.rows.multi .push-card { order: 1; }
.rows.multi .verify-card { order: 2; }
.rows.multi .brief-card { order: 3; }
.rows.multi .runtime { order: 4; grid-column: 1 / -1; grid-template-columns: repeat(4, minmax(0, 1fr)); }
.rows.multi .pushes { order: 5; }
.push-ch { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 12px 14px; border-radius: 10px; background: var(--sp-surface2); }
.push-ch .names { display: flex; flex-direction: column; gap: 2px; }
.push-ch b { font-size: 14px; }
.add-btn {
  height: 34px; padding: 0 12px; border-radius: 10px; border: 1px dashed var(--sp-line-strong); background: transparent;
  color: var(--sp-accent); font: inherit; font-size: 13px; cursor: pointer; display: inline-flex; align-items: center; gap: 6px;
  white-space: nowrap; flex: none;
}
.add-btn:hover { border-color: var(--sp-accent); }
.add-btn:focus-visible { outline: 2px solid var(--sp-accent); outline-offset: 2px; }
.wide { grid-column: 1 / -1; }
/* 只管这页自己的卡片；子组件（参数设置）的根元素也会带上这页的 scope，不能用裸的 .card */
.rows .card { padding: 24px 26px; display: flex; flex-direction: column; gap: 16px; }
.row-head { display: flex; align-items: flex-start; gap: 12px; justify-content: space-between; }
.col-head { flex-direction: column; gap: 4px; }
.token { display: flex; align-items: center; gap: 20px; }
.ring { position: relative; width: 96px; height: 96px; flex-shrink: 0; }
.ring-num { position: absolute; inset: 0; display: flex; align-items: baseline; justify-content: center; padding-top: 30px; gap: 2px; }
.ring-num b { font-size: 26px; font-weight: 600; }
.ring-num small { font-size: 12px; color: var(--sp-text3); }
.token-text { display: flex; flex-direction: column; gap: 4px; }
.token-text b { font-size: 16px; }
.bad { color: var(--sp-up); }
.btn { height: 38px; padding: 0 16px; border-radius: 10px; border: 1px solid var(--sp-line-strong); background: var(--sp-surface); color: var(--sp-text); font: inherit; font-weight: 500; display: inline-flex; align-items: center; gap: 8px; cursor: pointer; align-self: flex-start; }
.btn.primary { border: none; background: var(--sp-accent); color: var(--sp-on-accent); font-weight: 600; }
.big-line { display: flex; align-items: baseline; gap: 12px; }
.big { font-size: 36px; font-weight: 600; }
.track { height: 8px; border-radius: 4px; background: var(--sp-bar); overflow: hidden; }
.fill { height: 8px; border-radius: 4px; background: var(--sp-ok); }
.stats { display: flex; gap: 18px; flex-wrap: wrap; }
.rows .pushes { gap: 0; padding-bottom: 12px; }
.pushes .row-head { padding-bottom: 14px; }
.push { display: grid; grid-template-columns: 96px 30px minmax(0, 1fr) 96px; gap: 14px; align-items: center; min-height: 52px; border-top: 1px solid var(--sp-line-soft); }
.who { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.name { font-size: 13.5px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.star { color: var(--sp-gold); margin-right: 4px; }
.st { font-size: 12px; font-weight: 600; text-align: right; color: var(--sp-ok); }
.kind { width: 26px; height: 26px; box-sizing: border-box; border-radius: 13px; border: 1.5px solid currentColor; display: inline-flex; align-items: center; justify-content: center; }
.kind.k-trade { color: var(--sp-accent); background: var(--sp-accent-soft); }
.kind.k-fill { color: var(--sp-ok); background: var(--sp-ok-soft); }
.kind.k-market { color: var(--sp-warn); background: var(--sp-warn-soft); }
.ev { font-weight: 600; }
.ev-trade { color: var(--sp-accent); }
.ev-market { color: var(--sp-warn); }
.ev-fill { color: var(--sp-ok); }
.st.failed { color: var(--sp-up); }
.st.suppressed { color: var(--sp-text3); }
.link { color: var(--sp-accent); text-decoration: none; font-weight: 600; white-space: nowrap; }
.days { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 8px; }
.day { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 10px 4px; border-radius: 12px; border: 1px solid var(--sp-line); text-decoration: none; color: var(--sp-text); }
.day:hover { border-color: var(--sp-line-strong); }
.mark { width: 26px; height: 26px; border-radius: 13px; display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 700; color: var(--sp-surface); background: var(--sp-ok); }
.day.late .mark { background: var(--sp-warn); }
.day.failed .mark { background: var(--sp-up); }
.day.not_sent .mark { background: var(--sp-idle); }
.day.gap { background: var(--sp-surface2); border-color: var(--sp-line-soft); color: var(--sp-text3); }
.day.gap .mark { background: var(--sp-bar); color: var(--sp-text3); }
.valuation { margin: 12px 0 0; }
/* 和「每日简报」同一行，两列两行 */
.rows .runtime { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px 20px; padding: 22px 26px; }
.runtime > div { display: flex; flex-direction: column; gap: 4px; }
.runtime b { font-size: 15px; }
.error { padding: 28px; display: flex; flex-direction: column; gap: 10px; align-items: flex-start; }
@media (max-width: 1100px) { .page { padding: 32px 24px 48px; } .grid4 { grid-template-columns: repeat(2, minmax(0, 1fr)); } .rows { grid-template-columns: 1fr; } }
@media (max-width: 1100px) { .rows.multi, .rows.multi .runtime { grid-template-columns: repeat(2, minmax(0, 1fr)); } .rows.multi .push-card { grid-column: 1 / -1; } }
@media (max-width: 760px) { .rows.multi, .rows.multi .runtime { grid-template-columns: minmax(0, 1fr); } }
</style>
