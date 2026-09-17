import { create } from 'zustand'

export interface NetworkAskPending {
  host: string
  protocol: string
}

interface NetworkApprovalState {
  pending: NetworkAskPending | null
  setPending: (p: NetworkAskPending | null) => void
}

export const useNetworkApprovalStore = create<NetworkApprovalState>((set) => ({
  pending: null,
  setPending: (p) => set({ pending: p })
}))
