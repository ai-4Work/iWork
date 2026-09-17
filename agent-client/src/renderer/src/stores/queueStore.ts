import { create } from 'zustand'

// 队列按任务分桶：任务处理中时输入进去排到本任务队列，任务终态排空时发回本任务。
// 全局单队列会导致 B 任务排队的消息被发到"当时的当前任务"，多任务并发下会串。
interface QueueState {
  queues: Record<string, string[]>
  addToQueue: (taskId: string, text: string) => void
  removeFromQueue: (taskId: string, index: number) => void
  shiftQueue: (taskId: string) => string | undefined
  clearQueue: (taskId?: string) => void
}

export const useQueueStore = create<QueueState>((set, get) => ({
  queues: {},

  addToQueue: (taskId, text) =>
    set((s) => ({
      queues: { ...s.queues, [taskId]: [...(s.queues[taskId] ?? []), text] }
    })),

  removeFromQueue: (taskId, index) =>
    set((s) => ({
      queues: {
        ...s.queues,
        [taskId]: (s.queues[taskId] ?? []).filter((_, i) => i !== index)
      }
    })),

  shiftQueue: (taskId) => {
    const q = get().queues[taskId] ?? []
    if (q.length === 0) return undefined
    const first = q[0]
    set((s) => ({ queues: { ...s.queues, [taskId]: q.slice(1) } }))
    return first
  },

  clearQueue: (taskId) =>
    set((s) => {
      if (!taskId) return { queues: {} }
      const next = { ...s.queues }
      delete next[taskId]
      return { queues: next }
    })
}))
