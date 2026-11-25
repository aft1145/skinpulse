<script setup lang="ts">
// 左栏「我的求购」（spec §4.2）：奖牌 + 名次，最大的数字是「离榜首还差」，进度条，四项价格，近 7 天排名。
// 同一件饰品挂了多张单时可以切换。
import { computed } from 'vue'
import type { Order } from '@/api/types'
import RankMedal from '@/components/RankMedal.vue'
import SegmentedControl from '@/components/SegmentedControl.vue'
import { gapText, marketFarText, money, rankSpark, refBid, relative, sellLabel, sellSub } from '@/format'
import { orderTab, progressPct, progressText, rankRangeText, rankTitle } from './logic'

const props = defineProps<{ orders: Order[]; tz: string; days: string[] }>()
// 选中的单由页面持有：K 线上的虚线、比价里的竖线都要跟着换成这张单的价格
const pick = defineModel<string>('pick', { required: true })
const tabs = computed(() => props.orders.map((o) => ({ key: o.purchase_no, label: orderTab(o) })))
const o = computed(() => props.orders.find((x) => x.purchase_no === pick.value) ?? props.orders[0])
const first = computed(() => o.value.rank === 1)
const ref7 = computed(() => refBid(o.value, props.tz))
const spark = computed(() => rankSpark(o.value.rank_7d, 320, 64, 8, 3))
const best = computed(() => {
  const v = o.value.rank_7d.filter((x): x is number => x != null)
  return v.length ? `最好第 ${Math.min(...v)} 名` : ''
})
const d0 = computed(() => props.days[0]?.slice(5) ?? '')
const d1 = computed(() => props.days[props.days.length - 1]?.slice(5) ?? '')
</script>

<template>
  <article class="card mine">
    <SegmentedControl v-if="orders.length > 1" v-model="pick" :options="tabs" label="切换求购单" />
    <div class="head">
      <RankMedal :rank="o.rank" :size="48" />
      <div class="names">
        <span class="rank-title">{{ rankTitle(o) }}</span>
        <span class="muted small">{{ o.queue }} · 检查于 {{ relative(o.checked_at) }}</span>
      </div>
    </div>

    <div class="big">
      <span class="muted small">{{ first ? '现在' : '离榜首还差' }}</span>
      <span class="display value" :class="{ gold: first }">{{ first ? '已是第 1' : money(o.gap) }}</span>
    </div>

    <div class="progress">
      <div class="track"><div class="fill" :style="{ width: `${progressPct(o)}%` }" /></div>
      <span class="small note">
        {{ progressText(o) }}<template v-if="o.gated"> · <span class="amber">{{ gapText(o.gap_ratio, true) }}</span></template><template v-else-if="o.unreachable === 'market'"> · <span class="amber">{{ marketFarText(o.market_ratio) }}</span></template>
      </span>
    </div>

    <div class="prices">
      <div><span class="label">我的价</span><span class="num v">{{ money(o.my_price) }}</span></div>
      <div><span class="label">悠悠榜首</span><span class="num v">{{ money(o.top1_price) }}</span></div>
      <div>
        <span class="label">悠悠在售最低</span>
        <span class="num v">{{ sellLabel(o.sell_min, o.sell_depth) }} <small class="muted">{{ sellSub(o.sell_depth) }}</small></span>
      </div>
      <div :title="ref7.title">
        <span class="label">别处买盘<template v-if="o.ref_bid_platforms"> · {{ o.ref_bid_platforms }}</template></span>
        <span class="num v">{{ ref7.text }}</span>
      </div>
    </div>

    <div class="week">
      <div class="row muted small"><span>近 7 天排名</span><span>{{ best }}</span></div>
      <svg width="100%" height="64" viewBox="0 0 320 64" preserveAspectRatio="none" role="img" aria-label="近 7 天排名走势">
        <path d="M0 8H320M0 32H320M0 56H320" stroke="var(--sp-line-soft)" stroke-width="1" />
        <path :d="spark.d" fill="none" stroke="var(--sp-accent)" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke" />
      </svg>
      <div class="row num muted tiny"><span>{{ d0 }}</span><span>{{ rankRangeText(o.rank_7d) }}</span><span>{{ d1 }}</span></div>
    </div>
  </article>
</template>

<style scoped>
.mine { padding: 26px 28px; display: flex; flex-direction: column; gap: 22px; }
.head { display: flex; align-items: center; gap: 14px; }
.names { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.rank-title { font-size: 20px; font-weight: 700; }
.small { font-size: 12.5px; }
.tiny { font-size: 11px; }
.big { display: flex; flex-direction: column; gap: 4px; }
.value { font-size: 44px; font-weight: 600; line-height: 1; }
.value.gold { color: var(--sp-gold); font-size: 34px; }
.progress { display: flex; flex-direction: column; gap: 8px; }
.track { height: 8px; border-radius: 4px; background: var(--sp-bar); overflow: hidden; }
.fill { height: 8px; border-radius: 4px; background: var(--sp-accent); transition: width 600ms cubic-bezier(0.2, 0.8, 0.2, 1); }
.note { color: var(--sp-text2); }
.amber { color: var(--sp-amber); }
.prices { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px 20px; padding-top: 18px; border-top: 1px solid var(--sp-line-soft); }
.prices > div { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.label { font-size: 12px; color: var(--sp-text3); }
.v { font-size: 18px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.v small { font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; font-size: 12px; font-weight: 400; }
.week { display: flex; flex-direction: column; gap: 8px; padding-top: 18px; border-top: 1px solid var(--sp-line-soft); }
.row { display: flex; justify-content: space-between; }
</style>
