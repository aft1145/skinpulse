<script setup lang="ts">
// 右栏「价格走势」（spec §4.2）：全平台日线 K 线 + MA7/MA30，我的求购价画成虚线。
// 1 月 / 3 月 / 1 年在前端切片（接口一次给全 365 根和三个窗口的纵轴范围）；「全部」换成周线
// （接口和日线一起给）。切换、缩放都不会再请求。
import { computed, ref } from 'vue'
import type { Kline, Weekly, Window } from '@/api/types'
import EChart from '@/charts/EChart.vue'
import { WINDOWS, clipNote } from '@/charts/kline'
import { klineView } from '@/charts/options'
import SegmentedControl from '@/components/SegmentedControl.vue'
import { useTheme } from '@/composables/useTheme'
import { clock, money, signedPct } from '@/format'

const props = defineProps<{
  kline: Kline
  /** 周线；没有或取不到时不显示「全部」 */
  weekly?: Weekly | null
  myPrice: number | null
  tz: string
  title: string
  subtitle: string
  /** 大盘页的统计放在页面上方，图里不再重复。（布尔 prop 缺省时 Vue 当成 false，所以用「隐藏」来表达） */
  hideStats?: boolean
  height?: number
  /** 打开时的时间范围：详情页 3 月，大盘页 1 年（视觉稿） */
  defaultWin?: Window
}>()
const win = ref<Window>(props.defaultWin ?? '3m')
const { tokens } = useTheme()
const hasWeekly = computed(() => !!props.weekly?.bars.length)
const options = computed(() => WINDOWS.filter((w) => w.key !== 'all' || hasWeekly.value))
const src = computed(() =>
  win.value === 'all' && props.weekly
    ? { bars: props.weekly.bars, range: props.weekly.range, stale: props.weekly.stale, at: props.weekly.fetched_at, error: props.weekly.error }
    : { bars: props.kline.bars, range: props.kline.ranges[win.value as Exclude<Window, 'all'>], stale: props.kline.stale, at: props.kline.fetched_at, error: props.kline.error },
)
const view = computed(() =>
  klineView({ bars: src.value.bars, win: win.value, range: src.value.range, myPrice: props.myPrice, tz: props.tz, t: tokens.value }),
)
const note = computed(() => clipNote(src.value.range))
const since = computed(() => (win.value === 'all' && props.weekly?.bars.length ? props.weekly.bars[0][0].slice(0, 7) : null))
const s = computed(() => props.kline.signals)
const ch7 = computed(() => signedPct(s.value?.change_7d_pct))
const ch30 = computed(() => signedPct(s.value?.change_30d_pct))
</script>

<template>
  <article class="card chart-card">
    <div class="top">
      <div class="names">
        <h2>{{ title }}</h2>
        <span class="muted small">{{ subtitle }}</span>
      </div>
      <SegmentedControl v-model="win" :options="options" label="时间范围" />
    </div>

    <div v-if="!hideStats && s" class="stats">
      <span class="display last">{{ money(s.last_close) }}</span>
      <span class="num chg" :class="ch7.dir">7 日 {{ ch7.text }}</span>
      <span class="num chg" :class="ch30.dir">30 日 {{ ch30.text }}</span>
      <span class="muted small">MA7 {{ money(s.ma7) }} · MA30 {{ money(s.ma30) }}<template v-if="s.trend"> · {{ s.trend }}</template></span>
    </div>

    <p v-if="since" class="muted small">周线 · 自 {{ since }} 起，每根一周<template v-if="view.gaps.length">；竖虚线处 SteamDT 缺少数周数据</template></p>
    <p v-if="note" class="clip small">{{ note }}</p>
    <p v-if="src.stale" class="clip small">刷新失败，显示的是 {{ clock(src.at, tz) }} 取到的数据（{{ src.error }}）</p>

    <!-- 换时间范围时换一个新图（:key），重新播一次从左到右的入场动画。不换的话 ECharts 会把旧图
         「变形」成新图：根数不同时按下标对点，多出来的均线点被拉成横线、新 K 线直接冒出来、
         纵轴刻度叠在一起（用户 2026-09-28 录屏）。同一范围内的更新（换主题、换单的求购价）照常原地更新。 -->
    <EChart
      v-if="src.bars.length"
      :key="win"
      :option="view.option"
      :height="height ?? 340"
      :label="`${title}，${win === 'all' ? '全部周线' : `${WINDOWS.find((w) => w.key === win)?.label}日线`} K 线`"
    />
    <div v-else class="empty muted">行情暂时取不到<template v-if="src.error">：{{ src.error }}</template></div>

    <div class="legend small">
      <span><i class="sw" style="background: var(--sp-up)" />涨</span>
      <span><i class="sw" style="background: var(--sp-down)" />跌</span>
      <span><i class="ln" style="background: var(--sp-ma7)" />{{ view.maNames[0] }}</span>
      <span><i class="ln" style="background: var(--sp-ma30)" />{{ view.maNames[1] }}</span>
      <span class="grow" />
      <span class="muted">鼠标移到图上看每天的数值 · 拖动下方滑块或双指捏合缩放</span>
    </div>
  </article>
</template>

<style scoped>
.chart-card { padding: 24px 26px 20px; display: flex; flex-direction: column; gap: 16px; min-width: 0; }
.top { display: flex; align-items: center; gap: 16px; }
.names { display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 0; }
h2 { margin: 0; font-size: 18px; font-weight: 700; }
.small { font-size: 12.5px; }
.stats { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; }
.last { font-size: 30px; font-weight: 600; }
.chg { font-size: 13px; font-weight: 600; color: var(--sp-text2); }
.chg.up { color: var(--sp-up); }
.chg.down { color: var(--sp-down); }
.clip { margin: 0; color: var(--sp-warn); }
.empty { height: 340px; display: flex; align-items: center; justify-content: center; }
.legend { display: flex; align-items: center; gap: 18px; color: var(--sp-text2); }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.sw { width: 9px; height: 9px; border-radius: 2px; display: inline-block; }
.ln { width: 14px; height: 2px; display: inline-block; }
.grow { flex: 1; }
</style>
