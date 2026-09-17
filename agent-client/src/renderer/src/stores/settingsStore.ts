import { create } from 'zustand'
import type { Settings } from '../types'
import { DEFAULT_SETTINGS } from '../types'
import { ipcClient } from '../services/ipcClient'

interface SettingsState {
  settings: Settings
  isLoaded: boolean
  load: () => Promise<void>
  save: (settings: Partial<Settings>) => Promise<void>
}

export const useSettingsStore = create<SettingsState>((set, get) => ({
  settings: DEFAULT_SETTINGS,
  isLoaded: false,

  load: async () => {
    try {
      const saved = await ipcClient.settings.load()
      set({ settings: { ...DEFAULT_SETTINGS, ...saved }, isLoaded: true })
    } catch {
      set({ isLoaded: true })
    }
  },

  save: async (partial: Partial<Settings>) => {
    const updated = { ...get().settings, ...partial }
    set({ settings: updated })
    await ipcClient.settings.save(updated)
  }
}))
