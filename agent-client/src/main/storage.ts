import { ipcMain } from 'electron'
import Store from 'electron-store'

/**
 * 通用渲染层持久化（M5：消息/lastSeq 骨架、M6：outbox）。
 * 独立 electron-store 文件（name: iwork-data），与 settings.ts 的 config 分离。
 * 按 namespace 组织：storage:set('tasks','v1',...) → store.set('tasks',{...})，
 * 避免把用户 key 直接当 dot-path 写入（key 可含 : / .）。
 */
export function registerStorage(): {
  get: (ns: string, key: string) => unknown
  set: (ns: string, key: string, value: unknown) => void
} {
  const store = new Store<Record<string, Record<string, unknown>>>({ name: 'iwork-data' })

  const read = (ns: string, key: string): unknown =>
    (store.get(ns) as Record<string, unknown> | undefined)?.[key] ?? null
  const write = (ns: string, key: string, value: unknown): void => {
    const nsObj = (store.get(ns) as Record<string, unknown> | undefined) ?? {}
    store.set(ns, { ...nsObj, [key]: value })
  }

  ipcMain.handle('storage:get', async (_event, ns: string, key: string) => read(ns, key))
  ipcMain.handle('storage:set', async (_event, ns: string, key: string, value: unknown) => {
    write(ns, key, value)
  })

  return { get: read, set: write }
}
