import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/strategies' },
    { path: '/strategies', component: () => import('@/pages/Strategies.vue') },
    { path: '/backtest', component: () => import('@/pages/Backtest.vue') },
    { path: '/live', component: () => import('@/pages/Live.vue') },
    { path: '/evolve', component: () => import('@/pages/Evolve.vue') },
    { path: '/data-download', component: () => import('@/pages/DataDownload.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/strategies' },
  ],
})

export default router
