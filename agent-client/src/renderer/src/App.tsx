import { useEffect } from 'react'
import { useSettingsStore } from './stores/settingsStore'
import { useAuthStore } from './stores/authStore'
import { useConfigStore } from './stores/configStore'
import { subscribePersistence, runStartupRecovery } from './stores/persistence'
import { flushToolOutbox } from './services/toolOutbox'
import { AppLayout } from './components/layout/AppLayout'
import { NetworkConfirmDialog } from './components/NetworkConfirmDialog'
import { LoginPage } from './components/auth/LoginPage'

export default function App() {
  const status = useAuthStore((s) => s.status)
  const bootstrap = useAuthStore((s) => s.bootstrap)
  const loadSettings = useSettingsStore((s) => s.load)

  // 认证引导要用 settings.apiBaseUrl，所以得等 settings 就绪再跑
  useEffect(() => {
    loadSettings().finally(() => {
      void bootstrap()
    })
  }, [])

  // 业务侧启动必须等「认证就绪」——否则启动瞬间就会甩出一堆无 token 的请求
  useEffect(() => {
    if (status !== 'authed') return

    // M5：先装防抖持久化订阅（避免恢复灌入任务前的任何变更被吞），再做启动恢复。
    // 两函数内部均以模块级标志防重复执行。
    subscribePersistence()
    runStartupRecovery()
    // C-2：启动后补投上次断线时未回投的工具结果（服务端 duplicate 兜底）
    flushToolOutbox()

    const configStore = useConfigStore.getState()
    configStore.loadInstalledMcps().then(() => {
      configStore.connectInstalledMcps()
    })
    configStore.loadInstalledSkills()
    // 聊天下拉的模型候选。只要求登录，所以放在这一块里（早于任何权限判定）——
    // 管理员改完模型清单，下次登录/重连就带上新的了。
    configStore.loadModels()
  }, [status])

  if (status === 'loading') {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-[#e2e8f0] text-[13px] text-[#64748b]">
        正在恢复登录状态…
      </div>
    )
  }

  if (status === 'anon') return <LoginPage />

  return (
    <>
      <AppLayout />
      <NetworkConfirmDialog />
    </>
  )
}
