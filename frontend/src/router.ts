import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  // 换页回到顶部；浏览器后退回到原来的位置；总览改筛选（只改查询参数）时不动
  scrollBehavior: (to, from, saved) => saved ?? (to.path === from.path ? false : { top: 0 }),
  routes: [
    { path: '/', name: 'overview', component: () => import('./pages/OverviewPage.vue') },
    { path: '/item/:templateId(\\d+)', name: 'item', component: () => import('./pages/ItemPage.vue') },
    { path: '/analysis', name: 'analysis', component: () => import('./pages/AnalysisPage.vue') },
    { path: '/market', name: 'market', component: () => import('./pages/MarketPage.vue') },
    { path: '/brief', name: 'brief', component: () => import('./pages/BriefPage.vue') },
    { path: '/pick', name: 'pick', component: () => import('./pages/PickPage.vue') },
    { path: '/pnl', name: 'pnl', component: () => import('./pages/PnlPage.vue') },
    { path: '/system', name: 'system', component: () => import('./pages/SystemPage.vue') },
    // 旧的系统状态页、设置页并进了 /system（三期）
    { path: '/status', redirect: '/system' },
    { path: '/settings', redirect: '/system' },
    { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('./pages/NotFoundPage.vue') },
  ],
})
