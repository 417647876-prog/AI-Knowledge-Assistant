import {
  createRouter,
  createWebHistory,
  type RouterHistory,
} from 'vue-router'
import { useAuthStore } from '../stores/auth'
import AdminUsersView from '../views/AdminUsersView.vue'
import ForbiddenView from '../views/ForbiddenView.vue'
import LoginView from '../views/LoginView.vue'
import WorkspaceView from '../views/WorkspaceView.vue'

const routes = [
  { path: '/login', component: LoginView, meta: { public: true } },
  { path: '/', component: WorkspaceView, meta: { requiresAuth: true } },
  {
    path: '/admin/users',
    component: AdminUsersView,
    meta: { requiresAuth: true, admin: true },
  },
  { path: '/forbidden', component: ForbiddenView, meta: { requiresAuth: true } },
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
