import { ipcMain } from 'electron'
import Store from 'electron-store'

interface StoredSettings {
  apiBaseUrl: string
  apiKey: string
  userId?: string
  model: string
  workspacePath: string
  fullAccess: boolean
  mcpOverrides?: Record<string, {
    command?: string
    args?: string[]
    url?: string
    env?: Record<string, string>
    headers?: Record<string, string>
  }>
}

const defaults: StoredSettings = {
  apiBaseUrl: '',
  apiKey: '',
  userId: '',
  model: 'deepseek-v4-pro',
  workspacePath: '',
  fullAccess: false,
  mcpOverrides: {}
}

export function registerSettings(): { store: Store<StoredSettings>; get: () => StoredSettings } {
  const store = new Store<StoredSettings>({ defaults })

  ipcMain.handle('settings:save', async (_event, settings: StoredSettings) => {
    store.set(settings)
  })

  ipcMain.handle('settings:load', async () => {
    return store.store
  })

  return {
    store,
    get: () => store.store
  }
}
