import { useEffect } from 'react'
import { useSettingsStore } from './stores/settingsStore'
import { useConfigStore } from './stores/configStore'
import { subscribePersistence, runStartupRecovery } from './stores/persistence'
import { flushToolOutbox } from './services/toolOutbox'
import { AppLayout } from './components/layout/AppLayout'
import { NetworkConfirmDialog } from './components/NetworkConfirmDialog'

export default function App() {
  const load = useSettingsStore((s) => s.load)

  useEffect(() => {
    // M5：先装防抖持久化订阅（避免恢复灌入任务前的任何变更被吞），再等待 settings
    // 就绪后做启动恢复。两函数内部均以模块级标志防 StrictMode 双执行。
    subscribePersistence()
    load().finally(() => {
      runStartupRecovery()
      // C-2：启动后补投上次断线时未回投的工具结果（服务端 duplicate 兜底）
      flushToolOutbox()
    })
    const configStore = useConfigStore.getState()
    configStore.loadInstalledMcps().then(() => {
      configStore.connectInstalledMcps()
    })
    configStore.loadInstalledSkills()
  }, [])

  return (
    <>
      <AppLayout />
      <NetworkConfirmDialog />
    </>
  )
}
