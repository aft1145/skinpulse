<script setup lang="ts">
import { NDataTable, type DataTableColumns, type DataTableSortState } from 'naive-ui'
import { h, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { ArrowLeftRight, BellOff, ChevronDown } from '@lucide/vue'
import type { Order } from '@/api/types'
import RankMedal from '@/components/RankMedal.vue'
import RankSpark from '@/components/RankSpark.vue'
import { DASH, clock, money, rankChange, sellLabel, subLine, unrankedLabel } from '@/format'
import { GAP_TIER_LABEL, condAheadText, gapPct, gapSortKey, gapTier, inTrade, isBuff, ladderMarks, marketCellText, marketSortKey, marketTags, nullsLast, ownAheadText, rankCompare, unreachableOf } from './logic'
import OrderExpand from './OrderExpand.vue'

const props = defineProps<{
  orders: Order[]
  flashes: Map<string, 'up' | 'down'>
  days: string[]
  maxGapRatio: number
  youpinUrl: string
  tz: string
  /** 商品名后面的账号标签（多账号 spec §8）；不传就没有 */
  tagOf?: (o: Order) => string | null
}>()
const expanded = ref<string[]>([])
// 比较函数要知道当前方向（Naive 降序时把结果乘 −1）：空值始终在最后；排名列降序时同名次仍按差距从小到大
// （intent 2026-11-20）。同一时间只有一列在排序。
const desc = ref(false)
function onSorter(s: DataTableSortState | DataTableSortState[] | null) {
  const one = Array.isArray(s) ? s[0] : s
  desc.value = one?.order === 'descend'
}
const byPrice = nullsLast<Order>((o) => o.my_price)
const byGap = nullsLast<Order>(gapSortKey)
const byMarket = nullsLast<Order>(marketSortKey)

const numCell = (v: string, cls = '') => h('span', { class: `num ${cls}` }, v)

// 「离第 1」单元格：已是第 1 / 交易中写标签，否则写差价比例、金额和档位
function gapCell(o: Order) {
  const tier = gapTier(o)
  if (tier === 'first' || tier === 'trade') return h('div', { class: 'gap' }, [h('span', { class: `pill ${tier}` }, GAP_TIER_LABEL[tier])])
  const g = gapPct(o)
  if (tier === 'none' || g == null) return numCell(DASH, 'soft')
  const sub = `差 ${money(Math.max(0, (o.top1_price ?? 0) - (o.my_price ?? 0)))}${tier === 'gated' && o.gap_ratio != null ? ` · ×${o.gap_ratio.toFixed(1)}` : ''}`
  return h('div', { class: 'gap' }, [
    h('b', { class: `pct ${tier}` }, `${Math.round(g * 100)}%`),
    h('span', { class: 'sub' }, sub),
    h('span', { class: `pill ${tier}` }, [tier === 'gated' ? h(BellOff, { size: 11, 'aria-hidden': 'true' }) : null, GAP_TIER_LABEL[tier]]),
  ])
}

const columns: DataTableColumns<Order> = [
  {
    title: '饰品',
    key: 'title',
    minWidth: 280,
    render: (o) =>
      h('div', { class: 'name-cell' }, [
        h('div', { class: 'name-line' }, [
          o.star ? h('span', { class: 'star', 'aria-label': '刀或手套' }, '★') : null,
          // 对不上悠悠模板的 BUFF 单（template_id < 0）没有详情页（BUFF spec §4）
          o.template_id > 0
            ? h(RouterLink, { to: `/item/${o.template_id}`, class: 'name' }, () => o.title)
            : h('span', { class: 'name' }, o.title),
          props.tagOf?.(o) ? h('span', { class: isBuff(o) ? 'acct buff' : 'acct' }, props.tagOf(o) ?? '') : null,
        ]),
        h('span', { class: 'sub' }, subLine(o) || ' '),
      ]),
  },
  {
    title: '排名',
    key: 'rank',
    width: 190,
    // 名次从小到大，同名次按「离第 1」从小到大（intent 2026-11-20）
    sorter: (a, b) => rankCompare(a, b, desc.value),
    defaultSortOrder: 'ascend',
    render: (o) => {
      // 交易中（成交推送 spec §7.2）：不画名次，画一个「交易中」胶囊和开始时间
      if (inTrade(o)) {
        return h('div', { class: 'rank-cell' }, [
          h('span', { class: 'trade-pill' }, [h(ArrowLeftRight, { size: 13, 'aria-hidden': 'true' }), '交易中']),
          h('span', { class: 'sub' }, `${clock(o.in_trade_since ?? null, props.tz)} 起`),
        ])
      }
      const ch = rankChange(o.rank_change)
      return h('div', { class: 'rank-cell' }, [
        h(RankMedal, { rank: o.rank }),
        h(RankSpark, { values: o.rank_7d }),
        ch ? h('span', { class: `num change ${ch.dir}` }, ch.text) : null,
        unrankedLabel(o) ? h('span', { class: 'sub' }, unrankedLabel(o) ?? '') : null,
      ])
    },
  },
  // 价格梯（看板 spec §4.1，视觉稿 2026-10-09）：三个价一行数字 + 一条从 0 起的横轴，
  // 我的价到榜首价那段按「离第 1」的档位上色，一眼看出哪些接近、哪些还差得远
  {
    title: '价格（我的 · 榜首 · 在售）',
    key: 'ladder',
    minWidth: 380,
    // 四列合成一列后，按我的价排序还要能用（原来「我的价」列可排）
    sorter: (a, b) => byPrice(a, b, desc.value),
    render: (o) => {
      const m = ladderMarks(o)
      const tier = gapTier(o)
      const nums = h('div', { class: 'ladder-nums' }, [
        h('span', [h('i', '我的'), h('b', { class: 'num' }, money(o.my_price))]),
        h('span', [h('i', '榜首'), h('span', { class: 'num' }, money(o.top1_price))]),
        h('span', { class: 'sell-num' }, [
          h('i', '在售'),
          h('span', { class: o.sell_depth === 0 ? '' : 'num' }, sellLabel(o.sell_min, o.sell_depth)),
          o.sell_depth != null && o.sell_depth > 0 ? h('small', ` ${o.sell_depth} 件`) : null,
        ]),
      ])
      if (!m) return h('div', { class: 'ladder' }, [nums])
      const seg = m.my != null && m.top != null && tier !== 'first' && tier !== 'trade'
        ? h('span', { class: `seg ${tier}`, style: { left: `${m.my}%`, width: `${Math.max(m.top - m.my, 0.8)}%` } })
        : null
      return h('div', { class: 'ladder' }, [
        nums,
        h('div', { class: 'track', role: 'img', 'aria-label': `我的价 ${money(o.my_price)}，榜首价 ${money(o.top1_price)}，悠悠在售 ${sellLabel(o.sell_min, o.sell_depth)}` }, [
          m.my != null ? h('span', { class: 'mine', style: { width: `${m.my}%` } }) : null,
          seg,
          m.sell != null ? h('span', { class: 'sell', style: { left: `${m.sell}%` } }) : null,
          m.top != null ? h('span', { class: 'top', style: { left: `${m.top}%` } }) : null,
          m.my != null ? h('span', { class: 'dot', style: { left: `${m.my}%` } }) : null,
        ]),
      ])
    },
  },
  {
    title: '离第 1',
    key: 'gap',
    width: 150,
    align: 'right',
    sorter: (a, b) => byGap(a, b, desc.value),
    render: (o) => {
      const cell = gapCell(o)
      const tags = marketTags(o.market_flag)
      const ahead = ownAheadText(o)
      const cond = condAheadText(o)
      if (!tags.length && !ahead && !cond) return cell
      return h('div', { class: 'gap' }, [
        cell,
        ...tags.map((t) => h('span', { class: `mtag ${t.tone}` }, t.label)),
        ahead ? h('span', { class: 'own-ahead' }, ahead) : null,
        cond ? h('span', { class: 'cond-ahead' }, cond) : null,
      ])
    },
  },
  // 离市价（难以成交 spec §5）：差价、倍数；离市价太远时带「难成交」
  {
    title: '离市价',
    key: 'market',
    width: 150,
    align: 'right',
    sorter: (a, b) => byMarket(a, b, desc.value),
    render: (o) => {
      const t = marketCellText(o)
      return h('div', { class: 'gap' }, [
        h('span', { class: t.main === DASH ? 'num soft' : 'num strong' }, t.main),
        t.sub ? h('span', { class: 'sub' }, t.sub) : null,
        t.hard ? h('span', { class: 'pill gated' }, [h(BellOff, { size: 11, 'aria-hidden': 'true' }), GAP_TIER_LABEL.gated]) : null,
      ])
    },
  },
  {
    type: 'expand',
    width: 56,
    renderExpand: (o) =>
      h(OrderExpand, { order: o, days: props.days, maxGapRatio: props.maxGapRatio, youpinUrl: props.youpinUrl, tz: props.tz }),
  },
]

const rowClass = (o: Order) => {
  const f = props.flashes.get(o.purchase_no)
  return [f ? `flash-${f}` : '', inTrade(o) ? 'in-trade' : '', unreachableOf(o) ? 'unreach' : ''].filter(Boolean).join(' ')
}
// Naive 自带的展开触发器是个 <div>：键盘点不到、读屏读不出。里面放一个真的 <button>，
// 它的点击（包括回车、空格）冒泡给 Naive 的处理器去展开。
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
    class="orders"
    :columns="columns"
    :data="orders"
    :row-key="(o: Order) => o.purchase_no"
    :row-class-name="rowClass"
    :render-expand-icon="expandIcon"
    @update:sorter="onSorter"
    table-layout="fixed"
    :bordered="false"
    :bottom-bordered="false"
  />
</template>

<style scoped>
.orders :deep(.n-data-table-td) { vertical-align: middle; }
.orders :deep(tr.n-data-table-tr) { transition: background-color 160ms ease; }
.orders :deep(.name-cell) { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.orders :deep(.name-line) { display: flex; align-items: center; gap: 6px; min-width: 0; }
.orders :deep(.name) { color: var(--sp-text); font-weight: 600; font-size: 14.5px; text-decoration: none; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.orders :deep(.name:hover) { color: var(--sp-accent); }
.orders :deep(.star) { color: var(--sp-gold); font-size: 13px; }
.orders :deep(.sub) { font-size: 12.5px; color: var(--sp-text3); }
.orders :deep(.rank-cell) { display: flex; align-items: center; gap: 12px; }
.orders :deep(.change) { font-size: 12px; font-weight: 600; }
.orders :deep(.change.up) { color: var(--sp-ok); }
.orders :deep(.change.down) { color: var(--sp-up); }
.orders :deep(.strong) { font-weight: 600; font-size: 14.5px; }
.orders :deep(.soft) { color: var(--sp-text2); }
/* 价格梯 */
.orders :deep(.ladder) { display: flex; flex-direction: column; gap: 8px; min-width: 0; padding-right: 8px; }
.orders :deep(.ladder-nums) { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px 18px; font-size: 13px; color: var(--sp-text2); }
.orders :deep(.ladder-nums > span) { white-space: nowrap; }
.orders :deep(.ladder-nums i) { font-style: normal; font-size: 11.5px; color: var(--sp-text3); margin-right: 5px; }
.orders :deep(.ladder-nums b) { color: var(--sp-text); font-weight: 600; }
.orders :deep(.ladder-nums small) { color: var(--sp-text3); font-size: 11px; }
.orders :deep(.sell-num) { margin-left: auto; }
.orders :deep(.track) { position: relative; height: 10px; border-radius: 5px; background: var(--sp-bar); }
.orders :deep(.track .mine) { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 5px 0 0 5px; background: color-mix(in srgb, var(--sp-text3) 22%, transparent); }
.orders :deep(.track .seg) { position: absolute; top: 0; bottom: 0; border-radius: 3px; }
.orders :deep(.seg.near) { background: var(--sp-accent); }
.orders :deep(.seg.mid) { background: var(--sp-info); }
.orders :deep(.seg.far) { background: var(--sp-warn); }
.orders :deep(.seg.gated) { background: repeating-linear-gradient(45deg, var(--sp-text3) 0 4px, transparent 4px 7px); }
.orders :deep(.track .dot) { position: absolute; top: 50%; width: 12px; height: 12px; margin: -6px 0 0 -6px; border-radius: 50%; background: var(--sp-text); border: 2px solid var(--sp-surface); box-sizing: border-box; }
.orders :deep(.track .top) { position: absolute; top: 50%; width: 10px; height: 10px; margin: -5px 0 0 -5px; transform: rotate(45deg); background: var(--sp-surface); border: 2px solid var(--sp-text); box-sizing: border-box; }
.orders :deep(.track .sell) { position: absolute; top: -3px; bottom: -3px; width: 2px; margin-left: -1px; background: var(--sp-text3); border-radius: 1px; }
.orders :deep(.gap) { display: flex; flex-direction: column; align-items: flex-end; gap: 3px; }
.orders :deep(.gap .sub) { white-space: nowrap; }
.orders :deep(.pct) { font-size: 18px; font-weight: 700; font-variant-numeric: tabular-nums; }
.orders :deep(.pct.near) { color: var(--sp-accent); }
.orders :deep(.pct.mid) { color: var(--sp-info); }
.orders :deep(.pct.far) { color: var(--sp-warn); }
.orders :deep(.pct.gated) { color: var(--sp-text2); }
.orders :deep(.pill) { height: 20px; padding: 0 7px; border-radius: 6px; font-size: 11.5px; font-weight: 600; display: inline-flex; align-items: center; gap: 3px; }
.orders :deep(.pill.near) { background: var(--sp-accent-soft); color: var(--sp-accent); }
.orders :deep(.pill.mid) { background: var(--sp-info-soft); color: var(--sp-info); }
.orders :deep(.pill.far) { background: var(--sp-warn-soft); color: var(--sp-warn); }
.orders :deep(.pill.gated) { background: transparent; color: var(--sp-text2); border: 1px dashed var(--sp-line-strong); }
/* 难以成交（难以成交 spec §5）：没勾选隐藏时整行退到次要色，原因写在触发它的那一列 */
.orders :deep(tr.unreach .name), .orders :deep(tr.unreach .ladder-nums b), .orders :deep(tr.unreach .strong) { color: var(--sp-text2); }
.orders :deep(tr.unreach .rank-cell), .orders :deep(tr.unreach .track) { opacity: 0.55; }
.orders :deep(.pill.first) { height: 24px; background: var(--sp-gold-soft); color: var(--sp-gold); }
.orders :deep(.mtag) { height: 20px; padding: 0 7px; border-radius: 6px; font-size: 11.5px; font-weight: 600; display: inline-flex; align-items: center; white-space: nowrap; }
.orders :deep(.mtag.warn) { background: var(--sp-warn-soft); color: var(--sp-warn); }
.orders :deep(.mtag.amber) { background: var(--sp-amber-soft); color: var(--sp-amber); }
.orders :deep(.acct) {
  height: 20px; padding: 0 7px; border-radius: 6px; font-size: 11.5px; font-weight: 600; white-space: nowrap;
  display: inline-flex; align-items: center; flex: none;
  color: var(--sp-amber); background: var(--sp-amber-soft); border: 1px solid color-mix(in srgb, var(--sp-amber) 35%, transparent);
}
.orders :deep(.own-ahead) { font-size: 12px; color: var(--sp-amber); white-space: nowrap; }
.orders :deep(.acct.buff) { color: var(--sp-info); background: var(--sp-info-soft); border-color: color-mix(in srgb, var(--sp-info) 38%, transparent); }
.orders :deep(.cond-ahead) { font-size: 12px; color: var(--sp-info); white-space: nowrap; }
.orders :deep(.pill.trade) { height: 24px; background: var(--sp-surface); color: var(--sp-accent); border: 1px solid var(--sp-accent); }
.orders :deep(.n-data-table-expand-trigger) { width: auto; height: auto; margin: 0; }
.orders :deep(.toggle) {
  width: 32px; height: 32px; padding: 0; border-radius: 9px; border: 1px solid var(--sp-line); background: transparent;
  display: inline-flex; align-items: center; justify-content: center; color: var(--sp-text2); cursor: pointer;
  transition: border-color 120ms ease, color 120ms ease, background-color 120ms ease;
}
.orders :deep(.toggle svg) { transition: transform 200ms ease; }
.orders :deep(.toggle:hover) { border-color: var(--sp-line-strong); color: var(--sp-text); }
.orders :deep(.toggle.open) { background: var(--sp-accent-soft); border-color: var(--sp-accent); color: var(--sp-accent); }
.orders :deep(.toggle.open svg) { transform: rotate(180deg); }
.orders :deep(.n-data-table-tr--expanded .n-data-table-td) { background: var(--sp-surface2); }
/* 交易中：浅强调色底、描边胶囊（成交推送 spec §7.2） */
.orders :deep(tr.in-trade .n-data-table-td) { background: var(--sp-accent-soft); }
.orders :deep(.trade-pill) {
  height: 28px; padding: 0 10px 0 8px; box-sizing: border-box; border-radius: 14px; display: inline-flex; align-items: center; gap: 6px;
  font-size: 12.5px; font-weight: 600; color: var(--sp-accent); border: 1.5px solid var(--sp-accent); background: var(--sp-surface);
}
/* 自动刷新后名次变了：底色闪一下，1.6 秒淡掉（spec §3.3） */
.orders :deep(tr.flash-up .n-data-table-td) { animation: flash-up 1.6s ease-out; }
.orders :deep(tr.flash-down .n-data-table-td) { animation: flash-down 1.6s ease-out; }
@keyframes flash-up { from { background-color: var(--sp-flash-up); } }
@keyframes flash-down { from { background-color: var(--sp-flash-down); } }
</style>
