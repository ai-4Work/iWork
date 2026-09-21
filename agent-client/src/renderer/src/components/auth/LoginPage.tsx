import { useState } from 'react'
import { useAuthStore } from '../../stores/authStore'

/**
 * 登录 / 注册页。注册成功后由 authStore 内部紧接一次登录
 * （/auth/register 不返回 token，见 doc 18-11.1）。
 */
export function LoginPage() {
  const login = useAuthStore((s) => s.login)
  const register = useAuthStore((s) => s.register)
  const notice = useAuthStore((s) => s.notice)

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState<'login' | 'register' | null>(null)
  const [transportError, setTransportError] = useState<string | null>(null)

  const canSubmit = username.trim().length > 0 && password.length > 0 && busy === null

  async function submit(kind: 'login' | 'register') {
    if (!canSubmit) return
    setTransportError(null)
    setBusy(kind)
    try {
      const name = username.trim()
      if (kind === 'login') await login(name, password)
      else await register(name, password)
    } catch (err) {
      // 服务端错误已由 store 写进 notice；这里只兜住网络层异常
      if (!useAuthStore.getState().notice) {
        setTransportError(err instanceof Error ? err.message : '网络异常，请稍后重试')
      }
    } finally {
      setBusy(null)
    }
  }

  const message = transportError || notice

  return (
    <div className="flex h-screen w-screen items-center justify-center bg-[#e2e8f0]">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void submit('login')
        }}
        className="w-[360px] bg-white rounded-xl shadow-lg border border-[#e2e8f0] px-7 py-8"
      >
        <h1 className="text-[19px] font-semibold text-[#0f172a] tracking-[-0.2px]">登录 iWork</h1>
        <p className="mt-1 text-xs text-[#64748b]">请使用账号密码登录</p>

        <label className="block mt-6 text-xs text-[#475569]">
          用户名
          <input
            autoFocus
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            spellCheck={false}
            placeholder="3–32 位小写字母、数字、_ 或 -"
            className="mt-1.5 w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] text-[#0f172a] outline-none focus:border-[#a7f3d0] placeholder:text-[#cbd5e1]"
          />
        </label>

        <label className="block mt-4 text-xs text-[#475569]">
          密码
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            placeholder="至少 8 位"
            className="mt-1.5 w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] text-[#0f172a] outline-none focus:border-[#a7f3d0] placeholder:text-[#cbd5e1]"
          />
        </label>

        {message && (
          <div className="mt-4 px-2.5 py-2 rounded-md bg-[#fef2f2] border border-[#fecaca] text-xs text-[#b91c1c]">
            {message}
          </div>
        )}

        <button
          type="submit"
          disabled={!canSubmit}
          className="mt-6 w-full py-2 rounded-md border-none font-sans text-[13px] font-medium text-white bg-[#047857] cursor-pointer transition-colors hover:bg-[#065f46] disabled:bg-[#94a3b8] disabled:cursor-not-allowed"
        >
          {busy === 'login' ? '登录中…' : '登录'}
        </button>

        <button
          type="button"
          disabled={!canSubmit}
          onClick={() => void submit('register')}
          className="mt-2.5 w-full py-2 rounded-md border border-[#e2e8f0] font-sans text-[13px] text-[#475569] bg-white cursor-pointer transition-colors hover:bg-[#f1f5f9] disabled:text-[#94a3b8] disabled:cursor-not-allowed"
        >
          {busy === 'register' ? '注册中…' : '注册新账号'}
        </button>
      </form>
    </div>
  )
}
