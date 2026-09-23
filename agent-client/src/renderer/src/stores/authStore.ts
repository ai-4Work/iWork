import { create } from 'zustand'
import type { StoredTokens } from '../types'
import { ipcClient } from '../services/ipcClient'
import { useSettingsStore } from './settingsStore'

export interface AuthUser {
  id: string
  username: string
  display_name: string
  status: string
}

/**
 * loading：启动引导中，业务请求一律不发（否则启动瞬间会甩一堆 401）
 * authed：可发业务请求
 * anon：显示登录页
 */
export type AuthStatus = 'loading' | 'authed' | 'anon'

interface AuthState {
  status: AuthStatus
  user: AuthUser | null
  /** 后端下发的权限点（doc 19-6.4）。**不进 token**：每次开客户端由 /auth/me 重拉，
   *  这样管理员改完权限，用户下次启动就生效，不必等 token 过期。 */
  permissions: string[]
  accessToken: string | null
  refreshToken: string | null
  expiresAt: number
  /** 登录页展示的提示（密码错、被锁定、登录已过期…） */
  notice: string | null
  bootstrap: () => Promise<void>
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  /** 单飞刷新：并发调用共用同一次 refresh（并发刷新会触发服务端重放检测） */
  refresh: () => Promise<void>
  /** 重拉当前用户与权限点（登录后、改完授权后调） */
  refreshPermissions: () => Promise<void>
  clearSession: (notice?: string) => Promise<void>
}

interface TokenResponse {
  access_token: string
  refresh_token: string
  expires_in: number
  user?: AuthUser
}

function apiUrl(path: string): string {
  const base = useSettingsStore.getState().settings.apiBaseUrl || '/api'
  return `${base}${path}`
}

/** 服务端错误体统一为 {"detail": {"error": "<码>", "message": "<人话>"}}（doc 18-11.4） */
async function readError(res: Response): Promise<{ code: string; message: string }> {
  try {
    const body = await res.json()
    const detail = body?.detail
    if (detail && typeof detail === 'object') {
      return { code: detail.error || '', message: detail.message || '' }
    }
    return { code: '', message: typeof detail === 'string' ? detail : '' }
  } catch {
    return { code: '', message: '' }
  }
}

function persist(tokens: StoredTokens): void {
  // 落盘失败不能影响当前会话，只是下次启动要重新登录
  ipcClient.auth.save(tokens).catch((err) => console.error('[auth] 凭证落盘失败:', err))
}

function applyTokenResponse(data: TokenResponse): void {
  const expiresAt = Date.now() + data.expires_in * 1000
  useAuthStore.setState({
    accessToken: data.access_token,
    refreshToken: data.refresh_token,
    expiresAt,
    user: data.user ?? useAuthStore.getState().user
  })
  persist({
    accessToken: data.access_token,
    refreshToken: data.refresh_token,
    expiresAt
  })
}

let refreshInFlight: Promise<void> | null = null

async function doRefresh(): Promise<void> {
  const rt = useAuthStore.getState().refreshToken
  if (!rt) throw new Error('REFRESH_TOKEN_INVALID')

  const res = await fetch(apiUrl('/auth/refresh'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: rt })
  })

  if (!res.ok) {
    const { code, message } = await readError(res)
    // 5xx / 网关故障是暂时的，别把人踢下线（doc 18-10.3：只有 401 才清登录态）
    if (res.status !== 401) throw new Error(message || `刷新失败（${res.status}）`)
    await useAuthStore.getState().clearSession(message || '登录已过期，请重新登录')
    throw new Error(code || 'REFRESH_TOKEN_INVALID')
  }

  applyTokenResponse(await res.json())
}

async function refreshTokens(): Promise<void> {
  if (refreshInFlight) return refreshInFlight
  refreshInFlight = doRefresh().finally(() => {
    refreshInFlight = null
  })
  return refreshInFlight
}

async function fetchMe(): Promise<void> {
  const token = useAuthStore.getState().accessToken
  if (!token) return
  try {
    const res = await fetch(apiUrl('/auth/me'), {
      headers: { Authorization: `Bearer ${token}` }
    })
    if (res.ok) {
      const body = await res.json()
      // permissions 与 user 同一个响应回来（doc 19-5.4），少一次往返
      useAuthStore.setState({
        user: body.user,
        permissions: Array.isArray(body.permissions) ? body.permissions : []
      })
    } else if (res.status === 401) {
      await useAuthStore.getState().clearSession('登录已过期，请重新登录')
    }
  } catch {
    // 网络不通保持已登录，身份稍后由业务请求重试
  }
}

export const useAuthStore = create<AuthState>((set, get) => ({
  status: 'loading',
  user: null,
  permissions: [],
  accessToken: null,
  refreshToken: null,
  expiresAt: 0,
  notice: null,

  bootstrap: async () => {
    const stored = await ipcClient.auth.load()
    if (!stored?.refreshToken) {
      set({ status: 'anon' })
      return
    }
    set({
      accessToken: stored.accessToken,
      refreshToken: stored.refreshToken,
      expiresAt: stored.expiresAt
    })

    // 本地 access token 已过期（或即将过期）→ 先静默刷新，刷新失败才显示登录页
    if (stored.expiresAt <= Date.now() + 5000) {
      try {
        await refreshTokens()
      } catch {
        // 刷新被判定失效时 clearSession 已经给过提示，别覆盖它
        set({
          status: 'anon',
          user: null,
          accessToken: null,
          refreshToken: null,
          notice: get().notice ?? '无法连接服务端，请检查网络或服务是否已启动'
        })
        return
      }
    }

    // token 可用即算认证就绪：先放业务请求，身份（user）随后补上
    set({ status: 'authed', notice: null })
    void fetchMe()
  },

  login: async (username, password) => {
    set({ notice: null })
    const res = await fetch(apiUrl('/auth/login'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    })
    if (!res.ok) {
      const { message } = await readError(res)
      set({ notice: message || `登录失败（${res.status}）` })
      throw new Error('LOGIN_FAILED')
    }
    applyTokenResponse(await res.json())
    set({ status: 'authed', notice: null })
    // 登录响应不带 permissions，补拉一次；不 await，避免拖慢进主界面
    void fetchMe()
  },

  logout: async () => {
    const rt = get().refreshToken
    // 先清本地：登出按钮必须一定生效，服务端吊销失败不能把用户卡在已登录态
    await get().clearSession()
    if (!rt) return
    try {
      await fetch(apiUrl('/auth/logout'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: rt })
      })
    } catch (err) {
      console.warn('[auth] 服务端登出失败，本地已清:', err)
    }
  },

  refresh: refreshTokens,

  refreshPermissions: fetchMe,

  clearSession: async (notice) => {
    await ipcClient.auth.clear().catch(() => {})
    set({
      status: 'anon',
      user: null,
      permissions: [],
      accessToken: null,
      refreshToken: null,
      expiresAt: 0,
      notice: notice ?? null
    })
  }
}))

/**
 * 权限判断。`perms` 传数组表示**任一满足**，与后端 `require_permission` 同义。
 * 写成 hook（订阅 permissions）而不是普通函数：管理员改完授权后 `refreshPermissions()`
 * 一跑，侧边栏就能立刻收回去。
 */
export function usePermi(perms: string | string[]): boolean {
  return hasPermi(useAuthStore((s) => s.permissions), perms)
}

/** 纯函数版：需要「先过滤再渲染」的地方用它 —— 在 `.filter()` 回调里调 `usePermi`
 *  等于 hooks in loop，eslint（react-hooks/rules-of-hooks）会拦。
 *  判断语义只有这一处，`usePermi` 也转调它。 */
export function hasPermi(owned: readonly string[], perms: string | string[]): boolean {
  const wanted = Array.isArray(perms) ? perms : [perms]
  return wanted.some((p) => owned.includes(p))
}
