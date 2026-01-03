import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'dashboard',
      component: () => import('../views/DashboardView.vue'),
      meta: { title: 'Dashboard' }
    },
    {
      path: '/items',
      name: 'items',
      component: () => import('../views/ItemsView.vue'),
      meta: { title: 'Nachrichten' }
    },
    {
      path: '/items/:id',
      name: 'item-detail',
      component: () => import('../views/ItemDetailView.vue'),
      meta: { title: 'Nachricht' }
    },
    {
      path: '/sources',
      name: 'sources',
      component: () => import('../views/SourcesView.vue'),
      meta: { title: 'Quellen' }
    },
    {
      path: '/sources/:id',
      name: 'source-detail',
      component: () => import('../views/SourceDetailView.vue'),
      meta: { title: 'Quelle' }
    },
    {
      path: '/rules',
      name: 'rules',
      component: () => import('../views/RulesView.vue'),
      meta: { title: 'Regeln' }
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('../views/SettingsView.vue'),
      meta: { title: 'Einstellungen' }
    }
  ]
})

router.beforeEach((to, _from, next) => {
  document.title = `${to.meta.title} - Liga Hessen News`
  next()
})

export default router
