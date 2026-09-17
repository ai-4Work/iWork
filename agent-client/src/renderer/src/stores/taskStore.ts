import { create } from 'zustand'
import type { Task, Message } from '../types'
import { createSession, CLIENT_TOOLS } from '../services/api'
import { useModeStore } from './modeStore'
import { useSettingsStore } from './settingsStore'
import { useConfigStore } from './configStore'

function genUUID(): string {
  return crypto.randomUUID()
}

function formatTime(ts: number): string {
  const d = new Date()
  const h = d.getHours().toString().padStart(2, '0')
  const m = d.getMinutes().toString().padStart(2, '0')
  return `${h}:${m}`
}

interface TaskState {
  tasks: Task[]
  currentTaskId: string | null

  create: (agentInfo?: { agentId?: string; agentType?: 'expert' | 'team'; agentName?: string }) => Promise<string>
  /** 为指定任务（默认当前任务）确保存在服务端会话（无则建一次）；成功返回 true，失败不抛。 */
  ensureSession: (taskId?: string) => Promise<boolean>
  delete: (id: string) => void
  select: (id: string) => void
  rename: (id: string, title: string) => void
  duplicate: (id: string) => void
  // 变更方法均接受可选 taskId：缺省作用于当前选中任务；流式事件/后台任务显式传目标任务，
  // 使"发起请求的任务"与"用户当前正在看的任务"解耦（多任务并发对话）。
  addMessage: (message: Message, taskId?: string) => void
  updateLastAssistantMessage: (updater: (msg: Message) => Message, taskId?: string) => void
  removeMessage: (messageId: string, taskId?: string) => void
  patchMessage: (messageId: string, patch: Partial<Message>, taskId?: string) => void
  /** M4 regenerate：清空该条 assistant 消息的回合内容并置为流中（保留 id/serverId）。 */
  resetMessage: (messageId: string, taskId?: string) => void
  /** M5：启动恢复时以本地持久化快照重建 tasks 列表（不触碰服务端会话）。 */
  hydrate: (tasks: Task[], currentTaskId: string | null) => void
  /** 服务端历史回读：整表替换该任务的 messages（回读是"以服务端为准重写"，不是追加）。 */
  replaceMessages: (taskId: string, messages: Message[]) => void
  updateTaskSeq: (seq: number, taskId?: string) => void
  getCurrentTask: () => Task | undefined
  getTask: (taskId: string) => Task | undefined
}

export const useTaskStore = create<TaskState>((set, get) => ({
  tasks: [],
  currentTaskId: null,

  create: async (agentInfo) => {
    const state = get()
    // Dedup: if an empty task already exists with a valid sessionId, just select it
    // Skip dedup for agent tasks since they always have a specific title
    if (!agentInfo) {
      const existing = state.tasks.find(
        t => t.title === '新建任务' && t.messages.length === 0 && t.sessionId
      )
      if (existing) {
        set((s) => ({
          currentTaskId: existing.id,
          tasks: s.tasks.map((t) => ({ ...t, active: t.id === existing.id }))
        }))
        return existing.id
      }
    }

    const id = genUUID()
    const title = agentInfo?.agentName ? `${agentInfo.agentName} — 新任务` : '新建任务'
    // Create local task placeholder first
    set((s) => ({
      tasks: [
        {
          id, sessionId: '', title, time: '刚才', active: false, messages: [], lastSeq: 0,
          agentId: agentInfo?.agentId,
          agentType: agentInfo?.agentType,
          agentName: agentInfo?.agentName
        },
        ...s.tasks.map((t) => ({ ...t, active: false }))
      ],
      currentTaskId: id
    }))

    // 尝试建立服务端会话（失败不抛；sessionId 为空时由 sendMessage / 重发的 ensureSession 再补）
    await get().ensureSession()

    return id
  },

  ensureSession: async (taskId?: string) => {
    const t = get().tasks.find((x) => x.id === (taskId ?? get().currentTaskId))
    if (!t) return false
    if (t.sessionId) return true

    const modeStore = useModeStore.getState()
    const settings = useSettingsStore.getState().settings
    try {
      const data = await createSession({
        id: t.id,
        scene_mode: modeStore.sceneMode,
        workspace: settings.workspacePath,
        model: settings.model,
        mode: modeStore.inputMode,
        client_tools: CLIENT_TOOLS,
        mcp_servers: useConfigStore.getState().getMcpServersForSession(),
        agent_id: t.agentId,
        agent_type: t.agentType
      })
      set((s) => ({
        tasks: s.tasks.map((x) =>
          x.id === t.id ? { ...x, sessionId: data.id } : x
        )
      }))
      return true
    } catch (err) {
      console.error('Failed to ensure session:', err)
      return false
    }
  },

  delete: (id: string) => {
    set((s) => {
      const filtered = s.tasks.filter((t) => t.id !== id)
      let currentId = s.currentTaskId
      if (s.currentTaskId === id) {
        currentId = filtered.length > 0 ? filtered[0].id : null
        if (currentId) {
          filtered[0] = { ...filtered[0], active: true }
        }
      }
      return { tasks: filtered, currentTaskId: currentId }
    })
  },

  select: (id: string) => {
    set((s) => ({
      currentTaskId: id,
      tasks: s.tasks.map((t) => ({ ...t, active: t.id === id }))
    }))
  },

  rename: (id: string, title: string) => {
    set((s) => ({
      tasks: s.tasks.map((t) => (t.id === id ? { ...t, title } : t))
    }))
  },

  duplicate: (id: string) => {
    const task = get().tasks.find((t) => t.id === id)
    if (!task) return
    const newId = genUUID()
    set((s) => ({
      tasks: [
        {
          id: newId,
          sessionId: '',
          title: task.title + ' (副本)',
          time: '刚才',
          active: false,
          lastSeq: 0,
          messages: task.messages ? JSON.parse(JSON.stringify(task.messages)) : [],
          agentId: task.agentId,
          agentType: task.agentType,
          agentName: task.agentName
        },
        ...s.tasks
      ]
    }))
  },

  addMessage: (message: Message, taskId?: string) => {
    set((s) => ({
      tasks: s.tasks.map((t) => {
        if (t.id !== (taskId ?? s.currentTaskId)) return t
        const title =
          t.title === '新建任务' && message.role === 'user'
            ? message.content.slice(0, 40)
            : t.title
        return {
          ...t,
          title,
          time: formatTime(Date.now()),
          messages: [...t.messages, message]
        }
      })
    }))
  },

  updateLastAssistantMessage: (updater: (msg: Message) => Message, taskId?: string) => {
    set((s) => ({
      tasks: s.tasks.map((t) => {
        if (t.id !== (taskId ?? s.currentTaskId)) return t
        const messages = [...t.messages]
        const lastIdx = messages.length - 1
        if (lastIdx >= 0 && messages[lastIdx].role === 'assistant') {
          messages[lastIdx] = updater(messages[lastIdx])
        }
        return { ...t, messages, time: formatTime(Date.now()) }
      })
    }))
  },

  removeMessage: (messageId: string, taskId?: string) => {
    set((s) => ({
      tasks: s.tasks.map((t) => {
        if (t.id !== (taskId ?? s.currentTaskId)) return t
        return { ...t, messages: t.messages.filter((m) => m.id !== messageId) }
      })
    }))
  },

  patchMessage: (messageId: string, patch: Partial<Message>, taskId?: string) => {
    set((s) => ({
      tasks: s.tasks.map((t) => {
        if (t.id !== (taskId ?? s.currentTaskId)) return t
        return {
          ...t,
          messages: t.messages.map((m) =>
            m.id === messageId ? { ...m, ...patch } : m
          )
        }
      })
    }))
  },

  resetMessage: (messageId: string, taskId?: string) => {
    set((s) => ({
      tasks: s.tasks.map((t) => {
        if (t.id !== (taskId ?? s.currentTaskId)) return t
        return {
          ...t,
          messages: t.messages.map((m) => {
            if (m.id !== messageId) return m
            return {
              id: m.id,
              role: m.role,
              serverId: m.serverId,
              content: '',
              thinking: '',
              tools: [],
              segments: [],
              effectsRuns: m.effectsRuns, // M4 regenerate：副作用账本跨运行保留，不随旧回合清空
              planStatus: undefined,
              planEditing: false,
              serverStatus: undefined,
              isStreaming: true,
              processCollapsed: false,
              timestamp: Date.now()
            }
          })
        }
      })
    }))
  },

  updateTaskSeq: (seq: number, taskId?: string) => {
    set((s) => ({
      tasks: s.tasks.map((t) =>
        t.id === (taskId ?? s.currentTaskId) ? { ...t, lastSeq: seq } : t
      )
    }))
  },

  getCurrentTask: () => {
    const state = get()
    return state.tasks.find((t) => t.id === state.currentTaskId)
  },

  getTask: (taskId: string) => get().tasks.find((t) => t.id === taskId),

  hydrate: (tasks: Task[], currentTaskId: string | null) => {
    const cleaned = tasks.map((t) => ({ ...t, messages: t.messages || [] }))
    const targetId =
      currentTaskId && cleaned.some((t) => t.id === currentTaskId)
        ? currentTaskId
        : (cleaned[0]?.id ?? null)
    set((s) => ({
      tasks: cleaned.map((t) => ({ ...t, active: t.id === targetId })),
      currentTaskId: targetId
    }))
  },

  replaceMessages: (taskId, messages) => {
    set((s) => ({
      tasks: s.tasks.map((t) => (t.id === taskId ? { ...t, messages } : t))
    }))
  }
}))
