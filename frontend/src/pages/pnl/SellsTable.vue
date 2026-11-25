<script setup lang="ts">
// 最近卖出（盈亏 spec §7.5）：到手、配到的成本、盈亏、配到的买入；成本未知的单列标签。
import { computed } from 'vue'
import type { PnlSells } from '@/api/types'
import { localDate } from '@/format'
import { lotText, platformLabel, sellSub, signedYuan, tone, yuan } from './logic'

const props = defineProps<{ data: PnlSells; tz: string; all: boolean }>()
const emit = defineEmits<{ toggle: [] }>()

const rows = computed(() => props.data.sells.map((s) => ({
  ...s, day: localDate(s.at, props.tz), subLine: sellSub(s.sub, s.qty), t: tone(s.pnl), lot: lotText(s.lots, props.tz),
})))
</script>

<template>
  <section class="wrap" aria-labelledby="sells-h">
    <div class="head">
      <h2 id="sells-h">最近卖出</h2>
      <span class="tiny muted">到手价是平台扣完手续费给的，未扣提现费 · 先进先出，悠悠买、BUFF 卖也配对</span>
    </div>
    <div class="card table">
      <div class="tr th">
        <span>卖出</span><span>饰品</span><span class="r">到手</span><span class="r">成本</span><span class="r">盈亏</span>
        <span class="pl">配到的买入</span>
      </div>
      <div v-for="s in rows" :key="s.order_no" class="tr">
        <span class="date-col"><span class="num text2 date">{{ s.day }}</span><span class="tiny muted">{{ platformLabel(s.platform) }}卖出</span></span>
        <div class="name">
          <RouterLink v-if="s.template_id != null" :to="`/item/${s.template_id}`" class="title">{{ s.name }}</RouterLink>
          <span v-else class="title">{{ s.name }}</span>
          <span class="tiny muted">{{ s.subLine }}</span>
        </div>
        <span class="num r strong">{{ yuan(s.net) }}</span>
        <span class="num r text2">{{ yuan(s.cost) }}</span>
        <span class="num r strong" :class="s.t">{{ signedYuan(s.pnl) }}</span>
        <span class="pl lot">
          <span v-if="s.cost != null" class="num text2">{{ s.lot }}</span>
          <template v-else>
            <span class="unknown">成本未知</span>
            <span class="tiny muted">无买入记录，未计入盈亏</span>
          </template>
        </span>
      </div>
      <div v-if="!rows.length" class="foot muted">暂无卖出</div>
      <div v-else-if="data.total > 6" class="foot">
        <button type="button" class="more press" :aria-expanded="all" @click="emit('toggle')">
          {{ all ? '收起' : `全部 ${data.total} 笔` }}
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.wrap { display: flex; flex-direction: column; gap: 16px; }
.head { display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; }
h2 { margin: 0; font-size: 20px; font-weight: 700; letter-spacing: -0.01em; }
.tiny { font-size: 12.5px; }
.table { overflow-x: auto; }
.tr { display: grid; grid-template-columns: 112px minmax(200px, 2.4fr) 110px 110px 110px minmax(220px, 1.4fr); align-items: center; min-height: 58px; padding: 0 24px; border-bottom: 1px solid var(--sp-line-soft); gap: 8px; }
.th { min-height: 44px; color: var(--sp-text3); font-size: 12.5px; font-weight: 500; border-bottom-color: var(--sp-line); }
.r { text-align: right; }
.pl { padding-left: 24px; }
.date { font-size: 13px; }
.date-col { display: flex; flex-direction: column; gap: 2px; }
.name { display: flex; flex-direction: column; gap: 3px; min-width: 0; padding-right: 16px; }
.title { color: var(--sp-text); font-weight: 600; text-decoration: none; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.title:hover { color: var(--sp-accent); }
.text2 { color: var(--sp-text2); }
.strong { font-weight: 600; }
.gain { color: var(--sp-accent); }
.loss { color: var(--sp-loss); }
.flat { color: var(--sp-text3); }
.lot { display: flex; align-items: center; gap: 8px; min-width: 0; white-space: nowrap; font-size: 12.5px; }
.unknown { height: 24px; padding: 0 9px; border-radius: 7px; display: inline-flex; align-items: center; font-weight: 600; background: var(--sp-surface2); color: var(--sp-text3); }
.foot { min-height: 52px; display: flex; align-items: center; justify-content: center; font-size: 13px; }
.more { height: 34px; padding: 0 14px; border-radius: 9px; border: none; background: transparent; color: var(--sp-accent); font: inherit; font-size: 13px; font-weight: 600; cursor: pointer; }
</style>
