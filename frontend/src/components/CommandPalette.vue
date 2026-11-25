<script setup lang="ts">
import { NInput, NModal } from 'naive-ui'
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { CornerDownLeft, Search } from '@lucide/vue'
import { fetchOverview } from '@/api/client'
import { subLine } from '@/format'
import { itemsFromOrders, searchItems, type ItemHit } from './palette'

// ⌘K 命令面板（spec §4）：跳转页面，或按饰品名搜索直达详情。
// 饰品来自 /api/overview（我挂了单的那些），打开面板时取，5 分钟内不重复取。
const show = defineModel<boolean>('show', { required: true })
const router = useRouter()
const q = ref('')
const active = ref(0)
const input = ref<InstanceType<typeof NInput> | null>(null)

interface Cmd { key: string; label: string; hint: string; kind: 'page' | 'item'; star?: boolean; go: () => void }
const pages: Cmd[] = [
  { key: 'p:/', label: '求购总览', hint: '/', kind: 'page', go: () => router.push('/') },
  { key: 'p:/analysis', label: '分析', hint: '/analysis', kind: 'page', go: () => router.push('/analysis') },
  { key: 'p:/market', label: '大盘', hint: '/market', kind: 'page', go: () => router.push('/market') },
  { key: 'p:/brief', label: '简报', hint: '/brief', kind: 'page', go: () => router.push('/brief') },
  { key: 'p:/system', label: '系统状态', hint: '/system', kind: 'page', go: () => router.push('/system') },
  { key: 'p:/system#settings', label: '设置', hint: '/system', kind: 'page', go: () => router.push('/system') },
  { key: 'p:/system#token', label: '更新 token', hint: '/system', kind: 'page', go: () => router.push('/system') },
]

const items = ref<ItemHit[] | null>(null)
const loadError = ref(false)
let loadedAt = 0
// 看板常常一开一整天：列表超过 5 分钟就在打开面板时重取一次，新同步进来的单也搜得到
async function loadItems() {
  if (items.value && Date.now() - loadedAt < 5 * 60_000) return
  try {
    items.value = itemsFromOrders((await fetchOverview()).orders)
    loadedAt = Date.now()
    loadError.value = false
  } catch {
    loadError.value = true
  }
}

const results = computed<Cmd[]>(() => {
  const t = q.value.trim()
  const pageHits = pages.filter((c) => !t || c.label.includes(t) || c.hint.includes(t))
  const itemHits = searchItems(items.value ?? [], t).map((i) => ({
    key: `i:${i.template_id}`,
    label: i.title,
    hint: [subLine(i), i.rank != null ? `第 ${i.rank} 名` : null].filter(Boolean).join(' · '),
    kind: 'item' as const,
    star: i.star,
    go: () => router.push(`/item/${i.template_id}`),
  }))
  return [...itemHits, ...pageHits]
})

watch(show, async (v) => {
  if (!v) return
  q.value = ''
  active.value = 0
  loadItems()
  await nextTick()
  input.value?.focus()
})
watch(q, () => (active.value = 0))

function run(c: Cmd | undefined) {
  if (!c) return
  show.value = false
  c.go()
}
function onKey(e: KeyboardEvent) {
  if (e.key === 'ArrowDown') { active.value = Math.min(active.value + 1, results.value.length - 1); e.preventDefault() }
  else if (e.key === 'ArrowUp') { active.value = Math.max(active.value - 1, 0); e.preventDefault() }
  else if (e.key === 'Enter') run(results.value[active.value])
}
</script>

<template>
  <NModal v-model:show="show" :auto-focus="false" transform-origin="center">
    <div class="palette card" role="dialog" aria-label="命令面板">
      <NInput ref="input" v-model:value="q" placeholder="搜饰品，或跳转到页面…" size="large" clearable @keydown="onKey">
        <template #prefix><Search :size="16" /></template>
      </NInput>
      <ul class="list" role="listbox">
        <li v-for="(c, i) in results" :key="c.key" role="option" :aria-selected="i === active">
          <button type="button" class="item" :class="{ on: i === active }" @mouseenter="active = i" @click="run(c)">
            <span class="kind muted">{{ c.kind === 'item' ? '饰品' : '页面' }}</span>
            <span class="label"><span v-if="c.star" class="star" aria-label="刀或手套">★</span>{{ c.label }}</span>
            <span class="hint muted" :class="{ num: c.kind === 'page' }">{{ c.hint }}</span>
            <CornerDownLeft v-if="i === active" :size="14" class="enter" />
          </button>
        </li>
        <li v-if="!results.length" class="muted empty">
          无匹配的饰品或页面<template v-if="loadError">（饰品列表获取失败）</template>
        </li>
      </ul>
    </div>
  </NModal>
</template>

<style scoped>
.palette { width: 560px; padding: 14px; box-shadow: var(--sp-pop-shadow); }
.list { list-style: none; margin: 10px 0 0; padding: 0; max-height: 420px; overflow-y: auto; }
.item {
  width: 100%; display: flex; align-items: center; gap: 12px; padding: 10px 12px; border: 0; border-radius: 10px;
  background: transparent; color: var(--sp-text); font: inherit; cursor: pointer; text-align: left;
}
.kind { font-size: 11.5px; width: 28px; flex-shrink: 0; }
.label { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.star { color: var(--sp-gold); margin-right: 4px; }
.hint { font-size: 12.5px; white-space: nowrap; }
.item.on { background: var(--sp-accent-soft); }
.enter { color: var(--sp-text3); }
.empty { padding: 12px; }
</style>
