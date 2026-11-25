<script setup lang="ts">
// 「开始使用」卡片（首次使用 spec §2，视觉稿已确认）：三步必需的没做完时放在总览最上面，做完就消失。
// 顺序固定；完成的一步原地变成一行打勾的灰字，当前要做的那一步按钮是主色。
import { Check } from '@lucide/vue'
import { computed, ref } from 'vue'
import { useMeta } from '@/composables/useMeta'
import PushPlusDialog from '@/pages/system/PushPlusDialog.vue'
import TokenDialog from '@/pages/system/TokenDialog.vue'
import type { SetupKey, SetupView } from './logic'

const props = defineProps<{ view: SetupView; tz: string }>()
const emit = defineEmits<{ changed: [] }>()
const { refresh } = useMeta()

const tokenOpen = ref(false)
const pushOpen = ref(false)
const copied = ref(false)
const MARKET_CMD = 'uv run python -m skinpulse steamdt-key'

const TITLE: Record<SetupKey, string> = { youpin: '登录悠悠', push: '微信推送', first_round: '第一轮监控', market: '行情数据' }
const steps = computed(() => {
  let n = 0
  return props.view.steps.map((s) => ({ ...s, title: TITLE[s.key], n: s.key === 'market' ? null : ++n }))
})

function done() {
  void refresh()
  emit('changed')
}

async function copy() {
  try {
    await navigator.clipboard.writeText(MARKET_CMD)
    copied.value = true
    setTimeout(() => (copied.value = false), 1600)
  } catch {
    /* 剪贴板不可用（非 https、被拒）：命令就在旁边，自己选中复制 */
  }
}
</script>

<template>
  <section class="setup card" aria-labelledby="setup-title">
    <div v-if="view.noOrders" class="head">
      <h1 id="setup-title" class="display">监控已运行</h1>
      <span class="left">你在悠悠上暂无求购单：挂单后，下一轮会显示在这里。</span>
    </div>
    <div v-else class="head">
      <h1 id="setup-title" class="display">开始使用</h1>
      <span class="left">还差 <b class="num">{{ view.left }}</b> 步</span>
    </div>
    <ol v-if="!view.noOrders">
      <li v-for="s in steps" :key="s.key" :class="s.state">
        <span class="mark" aria-hidden="true">
          <Check v-if="s.state === 'done'" :size="14" :stroke-width="2.4" />
          <template v-else-if="s.n">{{ s.n }}</template>
          <template v-else>＋</template>
        </span>
        <div class="text">
          <b>{{ s.title }}<span v-if="s.key === 'market' && s.state !== 'done'" class="opt">可选</span></b>
          <span v-if="s.state === 'done'" class="desc">{{ s.doneText }}</span>
          <span v-else-if="s.key === 'youpin'" class="desc">
            在浏览器里登录悠悠，复制请求头里的 authorization。
            <a href="https://github.com/aft1145/skinpulse/blob/main/docs/guide.md#4-登录悠悠账号同步求购单" target="_blank" rel="noopener noreferrer">复制方法</a>
          </span>
          <span v-else-if="s.key === 'push'" class="desc">升到第 1 名、成交这些提醒要靠它送到微信。在 pushplus.plus 用微信扫码登录，复制 token。</span>
          <span v-else-if="s.key === 'first_round'" class="desc">登录后自动开始，几分钟内完成。</span>
          <span v-else class="desc">大盘、价位、选品要用 SteamDT key，在终端里录：</span>
        </div>
        <div class="act">
          <template v-if="s.state !== 'done'">
            <button v-if="s.key === 'youpin'" type="button" class="btn press" :class="{ primary: s.state === 'current' }" @click="tokenOpen = true">录入 token</button>
            <button v-else-if="s.key === 'push'" type="button" class="btn press" :class="{ primary: s.state === 'current' }" @click="pushOpen = true">录入 PushPlus token</button>
            <span v-else-if="s.key === 'first_round'" class="muted small">{{ s.state === 'waiting' ? '等登录' : '进行中' }}</span>
            <span v-else class="cmd">
              <code class="num">{{ MARKET_CMD }}</code>
              <button type="button" class="copy press" @click="copy">{{ copied ? '已复制' : '复制' }}</button>
            </span>
          </template>
        </div>
      </li>
    </ol>
    <TokenDialog v-model:show="tokenOpen" :tz="tz" @done="done" />
    <PushPlusDialog v-model:show="pushOpen" @done="done" />
  </section>
</template>

<style scoped>
.setup:has(.head:only-child) { padding-bottom: 30px; }
.setup { padding: 30px 34px 14px; display: flex; flex-direction: column; gap: 18px; }
.head { display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; }
h1 { margin: 0; font-size: 30px; font-weight: 700; letter-spacing: -0.02em; }
.left { color: var(--sp-text3); font-size: 14px; }
.left b { color: var(--sp-accent); }
ol { list-style: none; margin: 0; padding: 0; }
li { display: grid; grid-template-columns: 40px minmax(0, 1fr) auto; gap: 16px; align-items: center; padding: 18px 0; border-top: 1px solid var(--sp-line-soft); }
.mark {
  width: 30px; height: 30px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
  font: 13px 'JetBrains Mono', monospace; color: var(--sp-text3); border: 1px solid var(--sp-line-strong);
}
li.current .mark { color: var(--sp-accent); border-color: var(--sp-accent); background: var(--sp-accent-soft); }
li.done .mark { color: var(--sp-ok); border-color: transparent; background: var(--sp-ok-soft); }
li.optional .mark { border-style: dashed; font-family: inherit; }
.text { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.text b { font-size: 16px; }
li.done .text b, li.optional .text b { color: var(--sp-text2); font-size: 15px; }
.opt { font-weight: 400; font-size: 12px; color: var(--sp-text3); margin-left: 8px; }
.desc { color: var(--sp-text3); font-size: 13px; line-height: 1.6; }
li.current .desc { color: var(--sp-text2); }
.desc a { color: var(--sp-accent); }
.act { display: flex; align-items: center; }
.btn {
  min-height: 40px; padding: 0 18px; border-radius: 10px; border: 1px solid var(--sp-line-strong); background: transparent;
  color: var(--sp-text); font: inherit; font-size: 14px; cursor: pointer; white-space: nowrap;
}
.btn:hover { background: var(--sp-surface2); }
.btn.primary { border: none; background: var(--sp-accent); color: var(--sp-on-accent); font-weight: 600; }
.btn.primary:hover { filter: brightness(1.06); }
.btn:focus-visible, .copy:focus-visible { outline: 2px solid var(--sp-accent); outline-offset: 2px; }
.cmd { display: flex; align-items: center; gap: 8px; background: var(--sp-surface2); border: 1px solid var(--sp-line); border-radius: 8px; padding: 5px 5px 5px 12px; }
.cmd code { font-size: 12px; color: var(--sp-text2); white-space: nowrap; }
.copy { font: inherit; font-size: 12px; padding: 5px 10px; border-radius: 6px; border: 1px solid var(--sp-line-strong); background: var(--sp-surface); color: var(--sp-text2); cursor: pointer; }
.small { font-size: 13px; }
.muted { color: var(--sp-text3); }
@media (max-width: 720px) {
  .setup { padding: 22px 18px 8px; }
  li { grid-template-columns: 34px minmax(0, 1fr); }
  .act { grid-column: 2; }
  .cmd { max-width: 100%; overflow-x: auto; }
}
</style>
