<script setup lang="ts">
import { darkTheme, dateZhCN, NConfigProvider, NMessageProvider, zhCN } from 'naive-ui'
import { computed } from 'vue'
import AppShell from '@/components/AppShell.vue'
import { useTheme } from '@/composables/useTheme'
import { themeOverrides } from '@/theme/naive'

const { mode, tokens } = useTheme()
const overrides = computed(() => themeOverrides(tokens.value))
</script>

<template>
  <NConfigProvider :theme="mode === 'dark' ? darkTheme : null" :theme-overrides="overrides" :locale="zhCN" :date-locale="dateZhCN">
    <NMessageProvider placement="top">
      <AppShell>
        <RouterView />
      </AppShell>
    </NMessageProvider>
  </NConfigProvider>
</template>
