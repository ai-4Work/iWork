import { useAuthStore } from '../stores/authStore'

/** 这两个码意味着 refresh token 也没了，只能回登录页（doc 18-8.5） */
const RELOGIN_CODES = new Set(['TOKEN_INVALID', 'REFRESH_TOKEN_INVALID'])

async function errorCode(res: Response): Promise<string> {
  try {
    const body = await res.clone().json()
    const detail = body?.detail
    return (detail && typeof detail === 'object' ? detail.error : '') || ''
  } catch {
    return ''
  }
}

/**
 * 带认证的业务请求：自动附 access token，401 时按错误码分流。
 *
 * - `TOKEN_EXPIRED`：刷新一次后重放原请求。刷新走单飞（见 authStore）——
 *   并发刷新会触发服务端重放检测，把用户整族 token 吊销掉。
 * - `TOKEN_INVALID` / `REFRESH_TOKEN_INVALID`：清登录态回登录页。
 *
 * 这个包装是「被动刷新」的落点：只在收到 401 后刷，不设定时器。
 */
export async function authedFetch(
  url: string,
  init: RequestInit = {},
  retry = true
): Promise<Response> {
  const token = useAuthStore.getState().accessToken
  const headers: Record<string, string> = { ...(init.headers as Record<string, string> | undefined) }
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(url, { ...init, headers })
  if (res.status !== 401) return res

  const code = await errorCode(res)

  if (code === 'TOKEN_EXPIRED' && retry) {
    try {
      await useAuthStore.getState().refresh()
    } catch {
      return res // 刷新失败时 authStore 已清登录态并给出提示
    }
    return authedFetch(url, init, false)
  }

  if (RELOGIN_CODES.has(code)) {
    await useAuthStore.getState().clearSession('登录已过期，请重新登录')
  }
  return res
}
