import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      redirect: '/uebersicht'
    },
    {
      path: '/uebersicht',
      name: 'uebersicht',
      component: () => import('../views/UebersichtView.vue'),
      meta: { title: 'Übersicht' }
    },
    {
      path: '/dashboard',
      redirect: '/uebersicht'
    },
    {
      path: '/items',
      name: 'items',
      component: () => import('../views/NachrichtenView.vue'),
      meta: { title: 'Nachrichten' }
    },
    {
      path: '/items/:id',
      name: 'item-detail',
      component: () => import('../views/NachrichtenView.vue'),
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
    },
    {
      path: '/stats',
      name: 'stats',
      component: () => import('../views/StatsView.vue'),
      meta: { title: 'System Status' }
    }
  ]
})

router.beforeEach((to, _from, next) => {
  document.title = `${to.meta.title} - Liga Hessen News`
  next()
})

export default router
