import type { Message, Task } from '../types'
import { ipcClient } from '../services/ipcClient'
import {
  fetchSessionMessages,
  fetchSessionState,
  type SessionMessageMeta
} from '../services/api'
import { useTaskStore } from './taskStore'
import { useChatStore } from './chatStore'
import { useMultiAgentStore } from './multiAgentStore'
import { showToast } from '../utils/toast'

// ═══════════════════════════════════════════════════════════════
// M5 · 本地持久化 + 启动恢复
// 持久化只是"骨架"：让 lastSeq/serverId/终态标记在重启后仍可用以决定续收或回读。
// 真相源始终是服务端（conversation_history / GET messages），本地快照不伪造内容。
// 范围：单 agent 主对话（taskStore/chatStore），多 agent 不动。
// ═══════════════════════════════════════════════════════════════

export interface StoredSession {
  currentTaskId: string | null
  tasks: Task[]
}

const NS = 'tasks'
const KEY = 'v1'

/** 是否在途 assistant：流中且无终态。重启后此类消息要么被续收（整回合重流），
 *  要么被 /messages 回读补全 —— 因此快照里只需存占位骨架，避免与重流内容重复。 */
function isInFlight(m: Message): boolean {
  return m.role === 'assistant' && m.isStreaming === true && !m.serverStatus
}

/**
 * 持久化前的可序列化裁剪：去掉 undefined，保证纯数据可写 electron-store。
 * 在途 assistant 一律压成空骨架（内容/工具/段由续收或回读重建，见文件头注释）。
 */
function storable(m: Message): Message {
  if (isInFlight(m)) {
    return {
      id: m.id,
      role: 'assistant',
      serverId: m.serverId,
      content: '',
      isStreaming: true,
      timestamp: m.timestamp
    }
  }
  const clean = {
    id: m.id,
    role: m.role,
    serverId: m.serverId,
    content: m.content,
    thinking: m.thinking,
    files: m.files,
    skillInvocations: m.skillInvocations,
    tools: m.tools,
    segments: m.segments,
    processCollapsed: m.processCollapsed,
    planStatus: m.planStatus,
    planEditing: m.planEditing,
    isStreaming: m.isStreaming,
    serverStatus: m.serverStatus,
    timestamp: m.timestamp
  }
  return JSON.parse(JSON.stringify(clean)) as Message
}

function storableTask(t: Task): Task {
  return { ...t, messages: (t.messages || []).map(storable) }
}

function snapshot(): StoredSession {
  const s = useTaskStore.getState()
  return { currentTaskId: s.currentTaskId, tasks: s.tasks.map(storableTask) }
}

// ---- 防抖自动保存 ----
let saveTimer: ReturnType<typeof setTimeout> | null = null

export function schedulePersistenceSave(): void {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => { void flushPersistence() }, 600)
}

async function flushPersistence(): Promise<void> {
  saveTimer = null
  try {
    await ipcClient.storage.set(NS, KEY, snapshot())
  } catch (err) {
    console.error('Persistence save failed:', err)
  }
}

let subscribed = false

/** 渲染层启动时调用一次：此后任何任务/消息变更都会防抖落盘。 */
export function subscribePersistence(): void {
  if (subscribed) return
  subscribed = true
  useTaskStore.subscribe(() => schedulePersistenceSave())
  // D：切换当前任务（选另一历史会话）→ 重建其副作用分块（本地快照不存 effectsRuns）。
  useTaskStore.subscribe((state, prev) => {
    if (state.currentTaskId === prev.currentTaskId) return
    const t = state.tasks.find((x) => x.id === state.currentTaskId)
    if (t?.sessionId) void useChatStore.getState().refreshEffects()
  })
}

// ---- 终态回填（服务端历史为准，只校正；不新增消息行）----

function applyTerminal(
  taskId: string,
  local: Message,
  server: SessionMessageMeta
): void {
  const textRows = (server.rows || [])
    .filter((r) => r.role === 'assistant' && r.content)
    .map((r) => r.content)
    .join('')
  const body = server.content || textRows
  const mappedStatus: Message['serverStatus'] =
    server.status === 'error' ? 'error'
    : server.status === 'cancelled' ? 'cancelled'
    : 'completed'
  // 以服务端为准（content 列或 text 行组装），服务端为空才退回本地已有正文。
  const content =
    body && body.trim()
      ? body
      : (local.content && local.content.trim()
          ? local.content
          : (server.error_message ? `**Error:** ${server.error_message}` : (local.content || '')))
  useTaskStore.getState().patchMessage(local.id, {
    serverId: local.serverId || server.id,
    serverStatus: mappedStatus,
    isStreaming: false,
    processCollapsed: true,
    content
  }, taskId)
}

function markInterrupted(taskId: string, local: Message, attachServerId?: string): void {
  useTaskStore.getState().patchMessage(local.id, {
    serverId: local.serverId || attachServerId,
    serverStatus: 'interrupted',
    isStreaming: false,
    processCollapsed: true
  }, taskId)
}

/**
 * 用 GET /messages 回读结果校正本任务消息的终态/内容。
 * - 有 serverId 且服务端已终态 → 回填 serverStatus + 空内容兜底；
 * - 尾巴 in-flight（isStreaming 无终态）引擎又不在跑 → 按中断标记（继续/重新生成可救）；
 * - 服务端不可达 → 尾巴标 interrupted，避免永久转圈。
 */
async function reconcileFromServer(taskId: string): Promise<void> {
  const taskStore = useTaskStore.getState()
  const task = taskStore.tasks.find((t) => t.id === taskId)
  if (!task?.sessionId) return

  let serverMsgs: SessionMessageMeta[]
  try {
    const resp = await fetchSessionMessages(task.sessionId)
    serverMsgs = resp.messages
  } catch {
    const tail = lastInFlight(task)
    if (tail) markInterrupted(taskId, tail.local)
    return
  }

  const byId = new Map(serverMsgs.map((m) => [m.id, m]))
  const terminal = (s: SessionMessageMeta | undefined) =>
    s && (s.status === 'completed' || s.status === 'error' || s.status === 'cancelled')
  for (const local of task.messages) {
    if (local.role !== 'assistant' || !local.serverId) continue
    const server = byId.get(local.serverId)
    if (!terminal(server)) continue
    if (local.serverStatus && local.serverStatus !== 'interrupted') continue // 已定终态不回退
    applyTerminal(taskId, local, server!)
  }

  // 尾巴在途且未被上面校正：引擎死/服务端残留 → 中断或补成真终态。
  const tail = lastInFlight(task)
  if (!tail) return
  const tailServer = tail.local.serverId ? byId.get(tail.local.serverId) : undefined
  if (terminal(tailServer)) {
    applyTerminal(taskId, tail.local, tailServer!)
    return
  }
  // crash 早于 message.start（尾巴无 serverId）：取"最新一条本地未占用"的服务端行归属，
  // 它可能是真终态（应用补回）或 processing 残留（中断）。
  const usedIds = new Set(task.messages.map((m) => m.serverId).filter(Boolean))
  const candidate = [...serverMsgs].reverse().find((m) => !usedIds.has(m.id))
  if (candidate && terminal(candidate)) {
    applyTerminal(taskId, tail.local, candidate)
  } else {
    markInterrupted(taskId, tail.local, candidate?.id)
  }
}

function lastInFlight(task: Task): { local: Message; idx: number } | null {
  for (let i = task.messages.length - 1; i >= 0; i--) {
    const m = task.messages[i]
    if (m.role !== 'assistant') continue
    if (m.isStreaming === true && !m.serverStatus) return { local: m, idx: i }
    break // 最近一条 assistant 已收尾 → 其后不可能再有在途
  }
  return null
}

// ---- 切换任务时回读该会话的对话 ----

const historyLoading = new Set<string>()

/** 服务端一条 messages 行 = 一个回合（user 的 content + assistant 的 rows）。
 *  msg_type='result' 是子 agent 结果信封（content 为 JSON），不是对话，跳过。
 *  status=processing 说明引擎死在半路 → 映射成客户端专用的 interrupted，
 *  给"继续/重新生成"放行（与 applyTerminal 同一套终态映射）。 */
function rebuildMessages(msgs: SessionMessageMeta[]): Message[] {
  const out: Message[] = []
  for (const m of msgs) {
    if (m.msg_type === 'result') continue
    const ts = m.created_at ? Date.parse(m.created_at) : Date.now()
    const assistText = (m.rows || [])
      .filter((r) => r.role === 'assistant' && r.content)
      .map((r) => r.content as string)
      .join('')
    if (m.content && m.content.trim()) {
      out.push({
        id: `hist-${m.id}-u`,
        role: 'user',
        serverId: m.id,
        content: m.content,
        timestamp: ts
      })
    }
    if (!assistText.trim()) continue
    const serverStatus: Message['serverStatus'] =
      m.status === 'error' ? 'error'
      : m.status === 'cancelled' ? 'cancelled'
      : m.status === 'processing' ? 'interrupted'
      : 'completed'
    out.push({
      id: `hist-${m.id}-a`,
      role: 'assistant',
      serverId: m.id,
      content: assistText,
      serverStatus,
      isStreaming: false,
      processCollapsed: true,
      timestamp: ts
    })
  }
  return out
}

/**
 * 本地没有这个任务的对话时，从服务端回读补上。
 * 只在"本地空 + 有会话 + 不在跑"时动手：本地已有历史不重拉（避免闪一下、
 * 也避免把本地尚未落库的回合冲掉）；失败静默，绝不让点击卡住。
 */
export async function loadTaskHistory(taskId: string): Promise<void> {
  const task = useTaskStore.getState().getTask(taskId)
  if (!task?.sessionId) return
  if (task.messages.length > 0) return
  if (useChatStore.getState().processingTaskIds[taskId]) return
  if (historyLoading.has(task.sessionId)) return
  historyLoading.add(task.sessionId)
  try {
    const { messages } = await fetchSessionMessages(task.sessionId)
    const rebuilt = rebuildMessages(messages)
    if (rebuilt.length === 0) return
    // 拉取期间任务可能已被写入（例如用户直接发了消息）→ 让位给本地
    const cur = useTaskStore.getState().getTask(taskId)
    if (!cur || cur.messages.length > 0) return
    useTaskStore.getState().replaceMessages(taskId, rebuilt)
    useChatStore.getState().refreshEffects(taskId).catch(() => {})
  } catch {
    // 服务端不可达：保持空界面，发送时 ensureSession 会再试
  } finally {
    historyLoading.delete(task.sessionId)
  }
}

/**
 * 「打开一个任务」的唯一入口（任务列表点击 / 启动恢复都走这里）：
 * 按任务类型把它的历史搬回界面 —— 单 agent 读 taskStore，团队读 multiAgentStore。
 * 团队对话从来不写 taskStore，只回读 lead 列（子列的中继 delta 没落库，无从重放）。
 */
export async function activateTask(taskId: string): Promise<void> {
  const task = useTaskStore.getState().getTask(taskId)
  if (!task) return
  if (task.agentType === 'team') {
    if (!task.agentId) return
    const ok = await useMultiAgentStore.getState().restoreTeamSession(task.agentId, task.id)
    if (!ok) {
      showToast('该专家团资料加载失败，请从「专家和专家团」重新进入')
      return
    }
    if (task.sessionId) await useMultiAgentStore.getState().loadTeamHistory(task.sessionId)
    return
  }
  await loadTaskHistory(taskId)
}

// ---- 启动恢复 ----
let recoveryRan = false

/**
 * App 启动时（settings 加载完成后）调用：本地快照重建任务列表，
 * 再对当前会话探测：引擎存活且 PROCESSING 同一在途回合 → GET /stream 续收；
 * 否则用服务端历史回读校正终态/中断。
 */
export async function runStartupRecovery(): Promise<void> {
  if (recoveryRan) return
  recoveryRan = true
  try {
    const snap = (await ipcClient.storage.get(NS, KEY)) as StoredSession | null
    if (!snap || !Array.isArray(snap.tasks) || snap.tasks.length === 0) return
    useTaskStore.getState().hydrate(snap.tasks, snap.currentTaskId)

    const task = useTaskStore.getState().getCurrentTask()
    if (!task) return

    // 团队任务：对话只在 multiAgentStore（本地 taskStore 恒为空），走统一入口把
    // 面板和 lead 列历史搬回来。下面那套 tail 续收逻辑对团队任务本就不适用。
    if (task.agentType === 'team') {
      await activateTask(task.id)
      return
    }

    // 本地一条都没有（快照被裁剪/解析失败等）→ 回读服务端历史，别留个空界面。
    if (task.sessionId && task.messages.length === 0) {
      await loadTaskHistory(task.id)
      return
    }

    if (!task.sessionId) return

    const tail = lastInFlight(task)
    let needReconcile = false
    if (tail) {
      try {
        const state = await fetchSessionState(task.sessionId)
        // 引擎在跑且正是本尾巴在途回合（知道 serverId 时要求精确匹配）→ 续收。
        const matchesCurrent =
          !tail.local.serverId ||
          !state.current_message_id ||
          state.current_message_id === tail.local.serverId
        // WAITING_CHILDREN 也必须续收：父在等子 agent 回信，同一个 message_id 还没结束，
        // 流仍然开着（也不会有 message.complete）。漏掉它就会把正在跑的尾巴判成
        // interrupted，而子其实还在干。
        const engineBusy =
          state.state === 'PROCESSING' || state.state === 'WAITING_CHILDREN'
        if (state.engine_alive && engineBusy && matchesCurrent) {
          // 续收走 GET /stream（不 POST、不并发 GET，单消费者纪律）
          useChatStore.getState().reconnect()
          return
        }
      } catch {
        // 探测不可达：落到 reconcile，其内部处理引擎不可达（尾巴标 interrupted）
      }
      needReconcile = true
    }
    // 上次曾标"中断"（但当时可能只是服务端暂不可达）→ 这次回读校正成真终态。
    if (!needReconcile && task.messages.some((m) => m.serverStatus === 'interrupted')) {
      needReconcile = true
    }
    if (needReconcile) await reconcileFromServer(task.id)
    // D：reconcile 可能给中断尾消息补 serverId → 再拉一次 /effects 让其余块也挂回。
    useChatStore.getState().refreshEffects().catch(() => {})
  } catch (err) {
    console.error('Startup recovery failed:', err)
  }
}
