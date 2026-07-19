import {
  createRouter,
  createWebHistory,
  type RouterHistory,
} from 'vue-router'
import { useAuthStore } from '../stores/auth'

const routes = [
  {
    path: '/login',
    component: () => import('../views/LoginView.vue'),
    meta: { public: true },
  },
  {
    path: '/',
    component: () => import('../views/KnowledgeBasesView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/knowledge-bases/:knowledgeBaseId/documents',
    component: () => import('../views/DocumentsView.vue'),
    meta: { requiresAuth: true },
  },
  { path: '/trash', component: () => import('../views/TrashView.vue'), meta: { requiresAuth: true } },
  {
    path: '/admin/users',
    component: () => import('../views/AdminUsersView.vue'),
    meta: { requiresAuth: true, admin: true },
  },
  {
    path: '/forbidden',
    component: () => import('../views/ForbiddenView.vue'),
    meta: { requiresAuth: true },
  },
]

export function createAppRouter(history: RouterHistory = createWebHistory()) {
  const router = createRouter({ history, routes })

  router.beforeEach(async (to) => {
    const auth = useAuthStore()
    await auth.initialize()
    if (to.meta.requiresAuth && !auth.user) return '/login'
    if (to.meta.public && auth.user) return '/'
    if (to.meta.admin && !auth.isAdmin) return '/forbidden'
  })

  return router
}

export default createAppRouter()
