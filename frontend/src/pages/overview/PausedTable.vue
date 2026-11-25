<script setup lang="ts">
// 暂停单卡片里的明细（暂停单 spec §5.2）：选中一档后展开，先给 8 行。
import { ExternalLink } from '@lucide/vue'
import { computed, ref, watch } from 'vue'
import type { PausedItem } from '@/api/types'
import { afterText, num, pausedAccountTag, ratioBar, sellText, visibleItems, type RiskTier } from './pausedLogic'

const props = defineProps<{ items: PausedItem[]; tier: RiskTier; total: number; accounts: Record<string, string>; youpinUrl: string }>()

const LIMIT = 8
const all = ref(false)
watch(() => props.tier, () => (all.value = false))
const rows = computed(() =>
  visibleItems(props.items, props.tier, all.value ? null : LIMIT).map((it) => ({
    it,
    after: afterText(it),
    sell: sellText(it),
    bar: ratioBar(it.sell_ratio),
    tag: pausedAccountTag(it, props.accounts),
    url: props.youpinUrl ? props.youpinUrl.replace('{template_id}', String(it.template_id)) : '',
  })),
)
</script>

<template>
  <div class="box">
    <div class="scroll">
      <table>
        <thead>
          <tr>
            <th>饰品</th>
            <th class="r w-mine">我的价</th>
            <th class="w-after">恢复后</th>
            <th class="r w-top">第 1 名</th>
            <th class="r w-sell">在售最低</th>
            <th class="w-ratio">我的价 / 在售</th>
            <th class="w-link"><span class="sr">去悠悠</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.it.purchase_no">
            <td>
              <div class="name">
                <span><span v-if="r.it.star" class="star" aria-label="★">★</span>{{ r.it.title }}<span v-if="r.tag" class="acct">{{ r.tag }}</span></span>
                <span class="sub">
                  <span v-if="r.it.stattrak" class="st">StatTrak™</span>
                  <span v-if="r.it.tag">{{ r.it.tag }}</span>
                  <span v-if="r.it.paused_days != null" :class="r.it.old ? 'age old' : 'age'">暂停 {{ r.it.paused_days }} 天</span>
                </span>
              </div>
            </td>
            <td class="r num">{{ num(r.it.price) }}<span v-if="r.it.remaining_qty > 1" class="qty"> × {{ r.it.remaining_qty }}</span></td>
            <td>
              <span v-if="r.after.first" class="first">第 1<span class="after">{{ r.after.text }}</span></span>
              <span v-else class="num">{{ r.after.text }}</span>
            </td>
            <td class="r num muted">{{ num(r.it.top_bid) }}</td>
            <td class="r">
              <div class="sell">
                <span :class="r.sell.main === '无人在售' || r.sell.main === '—' ? '' : 'num'">{{ r.sell.main }}</span>
                <span v-if="r.sell.note" class="note">{{ r.sell.note }}</span>
              </div>
            </td>
            <td>
              <div class="ratio">
                <span class="num pct" :class="{ over: r.bar.over }">{{ r.bar.pct }}</span>
                <span class="track" aria-hidden="true">
                  <span class="fill" :class="{ over: r.bar.over }" :style="{ width: `${r.bar.width}%` }" />
                  <span class="tick" />
                </span>
              </div>
            </td>
            <td class="c">
              <a v-if="r.url" :href="r.url" target="_blank" rel="noopener noreferrer" class="link" :aria-label="`在悠悠打开 ${r.it.title}`">
                <ExternalLink :size="14" />
              </a>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="foot">
      <span>显示 <span class="num">{{ rows.length }}</span> / <span class="num">{{ total }}</span> 张</span>
      <button v-if="!all && total > LIMIT" type="button" class="more" @click="all = true">显示全部</button>
      <span class="legend">竖线 = 在售最低；「实时」是悠悠在售接口查的，「SteamDT」带它的更新时间</span>
    </div>
  </div>
</template>

<style scoped>
.box { border: 1px solid var(--sp-line); border-radius: 14px; background: var(--sp-surface2); overflow: hidden; }
.scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; min-width: 900px; }
th { padding: 11px 8px; font-weight: 500; font-size: 12px; color: var(--sp-text3); text-align: left; border-bottom: 1px solid var(--sp-line); }
th:first-child, td:first-child { padding-left: 16px; }
td { padding: 11px 8px; border-bottom: 1px solid var(--sp-line-soft); vertical-align: middle; }
.r { text-align: right; }
.c { text-align: center; }
.w-mine, .w-top { width: 86px; }
.w-after { width: 160px; padding-left: 12px; }
.w-sell { width: 130px; }
.w-ratio { width: 170px; padding-left: 16px; }
.w-link { width: 44px; }
td:nth-child(3) { padding-left: 12px; }
td:nth-child(6) { padding-left: 16px; }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.name { display: flex; flex-direction: column; gap: 3px; }
.star { color: var(--sp-gold); margin-right: 4px; }
.acct { margin-left: 6px; font-size: 11px; padding: 1px 7px; border-radius: 6px; background: var(--sp-surface); border: 1px solid var(--sp-line); color: var(--sp-text2); }
.sub { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--sp-text3); }
.st { color: var(--sp-stattrak); }
.age.old { font-size: 11px; padding: 1px 7px; border-radius: 6px; background: var(--sp-amber-soft); color: var(--sp-amber); border: 1px solid color-mix(in srgb, var(--sp-amber) 32%, transparent); }
.qty { color: var(--sp-text3); font-size: 12px; }
.first { display: inline-flex; align-items: center; gap: 6px; color: var(--sp-gold); font-weight: 500; }
.after { color: var(--sp-text3); font-weight: 400; font-size: 12px; }
.muted { color: var(--sp-text3); }
.sell { display: flex; flex-direction: column; gap: 2px; align-items: flex-end; }
.note { font-size: 11.5px; color: var(--sp-text3); white-space: nowrap; }
.ratio { display: flex; align-items: center; gap: 10px; }
.pct { width: 46px; text-align: right; color: var(--sp-text2); }
.pct.over { color: var(--sp-warn); font-weight: 600; }
.track { position: relative; flex: 1; height: 6px; border-radius: 3px; background: var(--sp-line-soft); }
.fill { position: absolute; left: 0; top: 0; height: 6px; border-radius: 3px; background: var(--sp-text3); }
.fill.over { background: var(--sp-warn); }
.tick { position: absolute; left: 50%; top: -3px; width: 1.5px; height: 12px; background: var(--sp-text2); }
.link { display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px; border-radius: 8px; color: var(--sp-text2); }
.link:hover { background: var(--sp-surface); color: var(--sp-accent); }
.link:focus-visible, .more:focus-visible { outline: 2px solid var(--sp-accent); outline-offset: 2px; }
.foot { display: flex; align-items: center; gap: 12px; padding: 12px 16px; font-size: 12.5px; color: var(--sp-text3); flex-wrap: wrap; }
.more { font: inherit; font-size: 12.5px; border: none; background: none; padding: 0; color: var(--sp-accent); cursor: pointer; }
.legend { margin-left: auto; }
</style>
