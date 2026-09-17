import { create } from 'zustand'
import type { AppMode, SceneMode } from '../types'

interface ModeState {
  inputMode: AppMode
  sceneMode: SceneMode
  setInputMode: (mode: AppMode) => void
  setSceneMode: (mode: SceneMode) => void
}

export const useModeStore = create<ModeState>((set) => ({
  inputMode: 'build',
  sceneMode: 'office',
  setInputMode: (mode) => set({ inputMode: mode }),
  setSceneMode: (mode) => set({ sceneMode: mode })
}))
