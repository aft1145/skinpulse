<script setup lang="ts">
import { onKeyStroke } from '@vueuse/core'
import { NTooltip } from 'naive-ui'
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Monitor, Moon, Search, Sun } from '@lucide/vue'
import type { Issue } from '@/api/types'
import { useAccount } from '@/composables/useAccount'
import { useMeta } from '@/composables/useMeta'
import { useTheme } from '@/composables/useTheme'
import { clock } from '@/format'
import CommandPalette from './CommandPalette.vue'
import SegmentedControl from './SegmentedControl.vue'
import StatusDot from './StatusDot.vue'

const { meta, error } = useMeta()
const { selected, effective, accounts } = useAccount()
const { pref, cycle } = useTheme()
const palette = ref(false)
const route = useRoute()

onKeyStroke('k', (e) => {
  if (e.metaKey || e.ctrlKey) {
    e.preventDefault()
    palette.value = !palette.value
  }
})

// `match` 是算作「在这一页」的路由名：饰品详情是从总览点进去的，导航上仍高亮总览（视觉稿第 3 版）。
const nav = [
  { label: '总览', to: '/', spa: true, match: ['overview', 'item'] },
  { label: '分析', to: '/analysis', spa: true, match: ['analysis'] },
  { label: '大盘', to: '/market', spa: true, match: ['market'] },
  { label: '简报', to: '/brief', spa: true, match: ['brief'] },
  { label: '选品', to: '/pick', spa: true, match: ['pick'] },
  { label: '盈亏', to: '/pnl', spa: true, match: ['pnl'] },
  { label: '系统', to: '/system', spa: true, match: ['system'] },
]

const ISSUE_TEXT: Record<Issue, string> = {
  not_logged_in: '未登录',
  token_invalid: 'token 已失效',
  paused: '风控暂停中',
  stale: '监控未运行',
  errors: '上一轮有错误',
  sync_slow: '单轮接近超时',
  buff_paused: 'BUFF 暂停中',
}

const status = computed(() => {
  if (error.value) return { tone: 'warn' as const, text: '连不上服务', time: '' }
  const m = meta.value
  if (!m) return { tone: 'idle' as const, text: '读取中', time: '' }
  const time = clock(m.service.last_round_at, m.timezone)
  if (m.service.healthy) return { tone: 'ok' as const, text: '监控正常', time }
  // 从没登录过：灰点，不是警告色——没做完不等于出错（首次使用 spec §2.3）；其它问题都是它的后果，不再列
  if (m.service.issues.includes('not_logged_in')) return { tone: 'idle' as const, text: ISSUE_TEXT.not_logged_in, time: '' }
  const worst = (['token_invalid', 'paused', 'stale', 'errors', 'sync_slow', 'buff_paused'] as Issue[]).find((i) => m.service.issues.includes(i))
  // 多号时说清是哪个号失效了（多账号 spec §8）
  const dead = worst === 'token_invalid' && accounts.value.length >= 2
    ? accounts.value.find((a) => a.logged_in && !a.token.valid) : undefined
  if (dead) return { tone: 'warn' as const, text: `${dead.label}登录已失效`, time }
  return { tone: 'warn' as const, text: worst ? ISSUE_TEXT[worst] : '需要留意', time }
})

const tokenAlert = computed(() => {
  const d = meta.value?.token.days_left
  return d != null && d <= 2 ? `token 还剩 ${d} 天` : null
})

// 账号切换（多账号 spec §8）：启用的号 ≥ 2 时才有，只在总览和盈亏页上显示（盈亏 spec §7.1）。
// 放在内容区右上角、不在顶栏里（2026-11-25：放顶栏时这两页的导航被挤到第二行，换页时导航跳来跳去）。
// 总览上带每个号的求购张数；盈亏页上张数没有意义，只写名字。名字跟 [dashboard] platform_names 走（tag）。
const ACCOUNT_PAGES = ['overview', 'pnl']
const accountOptions = computed(() => {
  const accs = accounts.value
  if (accs.length < 2) return []
  const name = (a: (typeof accs)[number]) => a.tag ?? a.label
  if (route.name === 'pnl') return [{ key: 'all', label: '全部账户' }, ...accs.map((a) => ({ key: String(a.id), label: name(a) }))]
  const total = accs.reduce((n, a) => n + a.orders, 0)
  return [{ key: 'all', label: `全部 ${total}` }, ...accs.map((a) => ({ key: String(a.id), label: `${name(a)} ${a.orders}` }))]
})
const accountKey = computed({
  get: () => String(effective.value),
  set: (k: string) => (selected.value = k === 'all' ? 'all' : Number(k)),
})

const showAccount = computed(() => accountOptions.value.length > 0 && ACCOUNT_PAGES.includes(String(route.name)))

const themeLabel = computed(() => ({ system: '主题：跟随系统', light: '主题：浅色', dark: '主题：深色' })[pref.value])
</script>

<template>
  <div class="shell">
    <header class="bar">
      <a href="/" class="brand" aria-label="SkinPulse 首页">
        <span class="logo sp-logo" aria-hidden="true">
          <!-- 图标 A · 登顶（公开仓库装修 spec §1）：脉搏线最高的尖峰上一个金点，金色同第 1 名徽章 -->
          <svg width="22" height="22" viewBox="2 2 28 28" fill="none">
            <path d="M4 20h6l3 4 5-15 3.5 8H28" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" />
            <circle class="sp-logo-dot" cx="18" cy="4.9" r="2.1" />
          </svg>
        </span>
        <span class="display name">SkinPulse</span>
      </a>
      <NTooltip v-if="meta?.demo">
        <template #trigger>
          <span class="demo-tag" tabindex="0">演示数据</span>
        </template>
        单子和行情都是假的；写操作已关闭
      </NTooltip>
      <nav class="nav" aria-label="页面">
        <template v-for="n in nav" :key="n.to">
          <RouterLink
            v-if="n.spa"
            :to="n.to"
            class="link"
            :class="{ on: n.match?.includes(String(route.name)) }"
            active-class=""
            exact-active-class=""
            :aria-current="n.match?.includes(String(route.name)) ? 'page' : undefined"
          >{{ n.label }}</RouterLink>
          <a v-else :href="n.to" class="link">{{ n.label }}</a>
        </template>
      </nav>
      <div class="spacer" />
      <RouterLink v-if="tokenAlert" to="/system" class="token-alert press">{{ tokenAlert }}</RouterLink>
      <RouterLink to="/system" class="status" :title="meta?.service.issues.join(', ') || undefined">
        <StatusDot :tone="status.tone" />
        <span>{{ status.text }}</span>
        <template v-if="status.time"><span class="muted when">·</span><span class="num when">{{ status.time }}</span></template>
      </RouterLink>
      <NTooltip>
        <template #trigger>
          <button type="button" class="icon press" aria-label="搜索（⌘K）" @click="palette = true"><Search :size="17" /></button>
        </template>
        搜索 · ⌘K
      </NTooltip>
      <NTooltip>
        <template #trigger>
          <button type="button" class="icon press" :aria-label="themeLabel" @click="cycle">
            <Monitor v-if="pref === 'system'" :size="17" />
            <Sun v-else-if="pref === 'light'" :size="17" />
            <Moon v-else :size="17" />
          </button>
        </template>
        {{ themeLabel }}（点击切换）
      </NTooltip>
    </header>
    <div v-if="showAccount" class="scope">
      <span class="scope-label" aria-hidden="true">账号</span>
      <SegmentedControl v-model="accountKey" :options="accountOptions" label="账号" class="acct" />
    </div>
    <slot />
    <CommandPalette v-model:show="palette" />
  </div>
</template>

<style scoped>
.bar {
  position: sticky; top: 0; z-index: 10;
  height: 64px; padding: 0 56px; display: flex; align-items: center; gap: 36px;
  background: var(--sp-header-bg); border-bottom: 1px solid var(--sp-line);
  transition: background-color 200ms ease, border-color 200ms ease;
}
.brand { display: flex; align-items: center; gap: 10px; text-decoration: none; }
.demo-tag {
  margin-left: -6px; padding: 2px 9px; border-radius: 999px; font-size: 11.5px; white-space: nowrap; cursor: default;
  color: var(--sp-gold); border: 1px solid color-mix(in srgb, var(--sp-gold) 45%, transparent);
  background: color-mix(in srgb, var(--sp-gold) 9%, transparent);
}
.demo-tag:focus-visible { outline: 2px solid var(--sp-accent); outline-offset: 2px; }
.logo {
  width: 30px; height: 30px; border-radius: 9px; background: #0A6B69; color: #fff;
  display: flex; align-items: center; justify-content: center;
}
/* 图标 A 的固定配色（公开仓库装修 spec §1），和 favicon、README 的 logo 一致，不跟主题强调色走 */
.logo :deep(.sp-logo-dot) { fill: #F2C14E; }
:global([data-theme='dark'] .sp-logo.sp-logo) { background: #14928F; }
:global([data-theme='dark'] .sp-logo .sp-logo-dot.sp-logo-dot) { fill: #F7D06B; }
.name { font-size: 17px; font-weight: 700; }
.nav { display: flex; gap: 28px; height: 64px; }
.link {
  display: flex; align-items: center; height: 64px; color: var(--sp-text2); text-decoration: none;
  border-bottom: 2px solid transparent; transition: color 120ms ease, border-color 120ms ease;
}
.link:hover { color: var(--sp-text); }
.link.on { color: var(--sp-text); font-weight: 600; border-bottom-color: var(--sp-text); }
/* 放不下时不许把文字压成两行（2026-11-25：导航、状态、token 提醒的汉字曾被挤成竖排） */
.nav, .link, .status, .token-alert, .brand { flex-shrink: 0; white-space: nowrap; }
.spacer { flex: 1; }
.status { display: flex; align-items: center; gap: 8px; color: var(--sp-text2); font-size: 13px; text-decoration: none; }
.token-alert {
  font-size: 13px; font-weight: 600; color: var(--sp-warn); background: var(--sp-warn-soft);
  padding: 6px 12px; border-radius: 999px; text-decoration: none;
}
.icon {
  width: 38px; height: 38px; border-radius: 10px; border: 1px solid var(--sp-line); background: var(--sp-surface);
  color: var(--sp-text2); display: flex; align-items: center; justify-content: center; cursor: pointer; padding: 0;
}
.icon:hover { color: var(--sp-text); border-color: var(--sp-line-strong); }
/* 窄屏逐级收起（2026-11-24 手机宽度横向溢出）：先收间距；再把导航挪到第二行、可横向滑动；
   账号切换单独一行铺满；手机上顶栏不再吸顶（三行太占屏），品牌只留图标、状态只留文字 */
@media (max-width: 1360px) { .bar { padding: 0 24px; gap: 20px; } .nav { gap: 18px; } }
@media (max-width: 1180px) {
  .bar { height: auto; min-height: 64px; flex-wrap: wrap; row-gap: 0; padding-top: 12px; }
  .nav {
    order: 10; flex: 1 0 100%; height: 44px; margin: 0 -24px; padding: 0 24px;
    overflow-x: auto; scrollbar-width: none;
  }
  .nav::-webkit-scrollbar { display: none; }
  .link { height: 44px; flex-shrink: 0; white-space: nowrap; }
  .token-alert, .status { white-space: nowrap; }
}
/* 账号切换：内容区右上角，和页面内容同宽对齐（页面 .page 是 1440 宽、左右 56） */
.scope {
  max-width: 1440px; margin: 0 auto; padding: 20px 56px 0; box-sizing: border-box;
  display: flex; align-items: center; justify-content: flex-end; gap: 10px;
}
.scope-label { font-size: 12.5px; color: var(--sp-text3); white-space: nowrap; }
@media (max-width: 1100px) { .scope { padding: 16px 24px 0; } }
@media (max-width: 760px) {
  .scope { padding: 16px 16px 0; }
  .acct { display: flex; min-width: 0; overflow-x: auto; scrollbar-width: none; }
  .acct :deep(.opt) { flex: 1 0 auto; }
}
@media (max-width: 760px) {
  .bar { position: static; padding: 10px 16px 0; gap: 10px 12px; }
  .nav { margin: 0 -16px; padding: 0 16px; gap: 20px; }
  .name { display: none; }
}
@media (max-width: 480px) { .when { display: none; } }
</style>
