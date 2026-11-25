<script setup lang="ts">
// 价位视图的表格（价位 spec §7.1）：列照定稿的视觉稿，按「抢到第 1」从高到低排好再传进来。
// 不引 ECharts（首页产物里不能有它，见 tests/test_frontend_build.py），全用 HTML。
import { NDataTable, type DataTableColumns } from 'naive-ui'
import { computed, h, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { ChevronDown } from '@lucide/vue'
import type { AdviceKind, Order, ValuationSummary } from '@/api/types'
import { money, plainPct, ratioPct, subLine } from '@/format'
import ValueExpand from './ValueExpand.vue'
import { ADVICE, adviceTone, marginTone } from './valueLogic'

const props = defineProps<{
  orders: Order[]
  target: number
  fees: ValuationSummary['fees']
  asOf: string
  /** 各建议的张数（价位 spec §9.6）；null 表示建议关掉了，不显示「建议」列 */
  adviceCounts?: Record<AdviceKind, number> | null
  adv?: AdviceKind | null
}>()
const emit = defineEmits<{ 'update:adv': [AdviceKind | null] }>()
const expanded = ref<string[]>([])

const dash = () => h('span', { class: 'num soft' }, '—')
const stack = (top: ReturnType<typeof h>, bottom: string, right = true) =>
  h('div', { class: ['stack', right ? 'right' : ''] }, [top, h('span', { class: 'sub' }, bottom || ' ')])
const chip = (label: string, tone: string) => h('span', { class: `chip ${tone}` }, label)

// 二期（视觉稿「二期」预览）：多一列「建议」，其余列让出一点宽度
const narrow = computed(() => props.adviceCounts != null)
const columns = computed<DataTableColumns<Order>>(() => [
  {
    title: '饰品',
    key: 'title',
    minWidth: 240,
    render: (o) =>
      h('div', { class: 'name-cell' }, [
        h('div', { class: 'name-line' }, [
          o.star ? h('span', { class: 'star', 'aria-label': '刀或手套' }, '★') : null,
          h(RouterLink, { to: `/item/${o.template_id}`, class: 'name' }, () => o.title),
        ]),
        h('span', { class: 'sub' }, [
          subLine(o),
          o.value?.conditional ? '条件单' : '',
          // 这件的估值比别的旧（那几天 K 线没取到、或刚挂的单）：标出日期，别让它冒充最新的
          o.value && o.value.as_of !== props.asOf ? `估值于 ${o.value.as_of.slice(5)}` : '',
        ].filter(Boolean).join(' · ') || ' '),
      ]),
  },
  {
    title: '一年里的位置',
    key: 'pct',
    width: narrow.value ? 170 : 190,
    render: (o) => {
      const v = o.value
      if (!v || v.pct == null) return stack(dash(), v ? '日线不足' : '无估值', false)
      const tone = v.pct_label === '一年低位' ? 'accent' : v.pct_label === '一年高位' ? 'warn' : 'plain'
      return h('div', { class: 'pos' }, [
        h('div', { class: 'rail' }, [h('span', { class: `dot ${tone}`, style: { left: `${v.pct * 100}%` } })]),
        h('span', { class: 'sub' }, [h('b', { class: 'num strong-soft' }, plainPct(v.pct)), ` · ${v.pct_label?.replace('一年', '') ?? ''}`]),
      ])
    },
  },
  {
    title: '走势',
    key: 'trend',
    width: narrow.value ? 190 : 230,
    render: (o) => {
      const v = o.value
      if (!v || !v.trend) return stack(dash(), '', false)
      const t = v.trend === '下降' ? 'down' : v.trend === '上升' ? 'up' : 'neutral'
      const r = v.rel === '跑输' ? 'warn' : v.rel === '跑赢' ? 'ok' : 'neutral'
      return h('div', { class: 'stack' }, [
        h('span', { class: 'chips' }, [chip(v.trend, t), v.rel ? chip(`${v.rel}大盘`, r) : null]),
        h('span', { class: 'sub' }, v.c30 == null ? ' ' : `30 日 ${ratioPct(v.c30).text} · 大盘 ${ratioPct(v.b30).text}`),
      ])
    },
  },
  {
    title: '7 天坏情况',
    key: 'q10',
    width: narrow.value ? 110 : 120,
    align: 'right',
    render: (o) => {
      const v = o.value
      const bad = v?.q10 != null && v.q10 < -0.1
      return stack(h('span', { class: ['num', 'strong', bad ? 'loss' : ''] }, ratioPct(v?.q10).text), v?.liq ?? '')
    },
  },
  {
    title: '按我的价成交',
    key: 'm_my',
    width: narrow.value ? 130 : 140,
    align: 'right',
    render: (o) =>
      stack(h('span', { class: `num strong ${marginTone(o.value?.m_my, Number.POSITIVE_INFINITY)}` }, ratioPct(o.value?.m_my).text), `我的价 ${money(o.my_price)}`),
  },
  {
    title: () => h('span', { class: 'sorted' }, ['抢到第 1', h(ChevronDown, { size: 12, 'aria-label': '从高到低' })]),
    key: 'm_top',
    width: narrow.value ? 150 : 160,
    align: 'right',
    render: (o) => {
      const v = o.value
      if (v && o.rank === 1) return stack(h('span', { class: 'num soft' }, '已是第 1'), `我的价 ${money(o.my_price)}`)
      const tone = marginTone(v?.m_top, props.target)
      return stack(
        h('span', { class: `num big ${tone}` }, ratioPct(v?.m_top).text),
        o.top1_price == null ? '' : `第 ${o.rank ?? '?'} 名 → 出价 ${money(o.top1_price + 0.01)}`,
      )
    },
  },
  ...(props.adviceCounts == null ? [] : [{
    title: '建议',
    key: 'advice',
    width: 130,
    // 表头筛选：张数来自后端汇总；真正的筛选在页面上做（要进网址），这里只负责显示和发出选择
    filterMultiple: false,
    filterOptions: ADVICE.map((a) => ({ label: `${a.label}  ${props.adviceCounts![a.key] ?? 0}`, value: a.key })),
    filterOptionValue: props.adv ?? null,
    filter: () => true,
    render: (o: Order) => {
      const a = o.value?.advice
      if (!a) return dash()
      return h('span', { class: `adv ${adviceTone(a.kind)}`, title: a.rule }, a.label)
    },
  }]),
  {
    type: 'expand',
    width: 56,
    expandable: (o) => o.value != null,
    renderExpand: (o) => h(ValueExpand, { order: o, value: o.value!, fees: props.fees }),
  },
])

function onFilters(f: Record<string, unknown>) {
  const v = f.advice
  emit('update:adv', typeof v === 'string' ? (v as AdviceKind) : null)
}

const expandIcon = ({ expanded, rowData }: { expanded: boolean; rowData: object }) =>
  h(
    'button',
    {
      type: 'button',
      class: ['toggle', expanded ? 'open' : ''],
      'aria-expanded': String(expanded),
      'aria-label': `${expanded ? '收起' : '展开'} ${(rowData as Order).title}`,
    },
    h(ChevronDown, { size: 15 }),
  )
</script>

<template>
  <NDataTable
    v-model:expanded-row-keys="expanded"
    class="values"
    :columns="columns"
    table-layout="fixed"
    @update:filters="onFilters"
    :data="orders"
    :row-key="(o: Order) => o.purchase_no"
    :render-expand-icon="expandIcon"
    :bordered="false"
    :bottom-bordered="false"
  />
</template>

<style scoped>
.values :deep(.n-data-table-td) { vertical-align: middle; }
.values :deep(.name-cell) { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.values :deep(.name-line) { display: flex; align-items: center; gap: 6px; min-width: 0; }
.values :deep(.name) { color: var(--sp-text); font-weight: 600; font-size: 14.5px; text-decoration: none; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.values :deep(.name:hover) { color: var(--sp-accent); }
.values :deep(.star) { color: var(--sp-gold); font-size: 13px; }
.values :deep(.sub) { font-size: 12.5px; color: var(--sp-text3); }
.values :deep(.stack) { display: flex; flex-direction: column; gap: 4px; }
.values :deep(.stack.right) { align-items: flex-end; }
.values :deep(.strong) { font-weight: 600; }
.values :deep(.strong-soft) { color: var(--sp-text); }
.values :deep(.big) { font-weight: 700; font-size: 15px; }
.values :deep(.soft), .values :deep(.muted) { color: var(--sp-text3); }
.values :deep(.accent) { color: var(--sp-accent); }
.values :deep(.loss) { color: var(--sp-loss); }
.values :deep(.pos) { display: flex; flex-direction: column; gap: 6px; padding-right: 24px; }
.values :deep(.rail) { position: relative; height: 8px; }
.values :deep(.rail)::before { content: ''; position: absolute; left: 0; right: 0; top: 2px; height: 4px; border-radius: 2px; background: var(--sp-bar); }
.values :deep(.dot) { position: absolute; top: 0; width: 8px; height: 8px; transform: translateX(-50%); border-radius: 4px; border: 1.5px solid var(--sp-surface); box-sizing: border-box; background: var(--sp-text2); }
.values :deep(.dot.accent) { background: var(--sp-accent); }
.values :deep(.dot.warn) { background: var(--sp-warn); }
.values :deep(.chips) { display: flex; gap: 6px; }
.values :deep(.stack .sub) { white-space: nowrap; }
.values :deep(.chip) { height: 22px; padding: 0 8px; border-radius: 6px; font-size: 12.5px; font-weight: 600; display: inline-flex; align-items: center; background: var(--sp-surface2); color: var(--sp-text2); }
.values :deep(.chip.down) { background: color-mix(in srgb, var(--sp-down) 12%, transparent); color: var(--sp-down); }
.values :deep(.chip.up) { background: color-mix(in srgb, var(--sp-up) 12%, transparent); color: var(--sp-up); }
.values :deep(.chip.warn) { background: var(--sp-warn-soft); color: var(--sp-warn); font-weight: 500; }
.values :deep(.chip.ok) { background: var(--sp-ok-soft); color: var(--sp-ok); font-weight: 500; }
.values :deep(.adv) {
  height: 26px; padding: 0 10px; border-radius: 7px; font-size: 12.5px; font-weight: 600; white-space: nowrap;
  display: inline-flex; align-items: center; border: 1px solid var(--sp-line); background: var(--sp-surface2); color: var(--sp-text);
}
.values :deep(.adv.warn) { background: var(--sp-warn-soft); color: var(--sp-warn); border-color: var(--sp-warn); }
.values :deep(.adv.accent) { background: var(--sp-accent-soft); color: var(--sp-accent); border-color: var(--sp-accent-soft-strong); }
.values :deep(.adv.muted) { color: var(--sp-text3); font-weight: 500; }
.values :deep(.sorted) { display: inline-flex; align-items: center; gap: 4px; color: var(--sp-text); }
.values :deep(.n-data-table-expand-trigger) { width: auto; height: auto; margin: 0; }
.values :deep(.toggle) {
  width: 32px; height: 32px; padding: 0; border-radius: 9px; border: 1px solid var(--sp-line); background: transparent;
  display: inline-flex; align-items: center; justify-content: center; color: var(--sp-text2); cursor: pointer;
  transition: border-color 120ms ease, color 120ms ease, background-color 120ms ease;
}
.values :deep(.toggle svg) { transition: transform 200ms ease; }
.values :deep(.toggle:hover) { border-color: var(--sp-line-strong); color: var(--sp-text); }
.values :deep(.toggle.open) { background: var(--sp-accent-soft); border-color: var(--sp-accent); color: var(--sp-accent); }
.values :deep(.toggle.open svg) { transform: rotate(180deg); }
.values :deep(.n-data-table-tr--expanded .n-data-table-td) { background: var(--sp-surface2); }
</style>
