import { create } from 'zustand'
import type { MultiAgentSession, MultiAgentMessage, AgentColumn, Expert, Team, TeamMember, ServerEvent, ToolCall } from '../types'
import { createSession, executeClientTool, presetClientToolSandboxed, submitToolResult, planApi, buildApi, cancelSession, fetchTeams, fetchExperts, fetchSessionMessages, fetchSessionAgents, type ToolResult, type SessionAgentMeta, type SessionMessageMeta } from '../services/api'
import { useSettingsStore } from './settingsStore'
import { ipcClient } from '../services/ipcClient'
import { recordExecutedTool, answerClientReconcile } from '../services/toolOutbox'

function genMsgId(): string {
  return `ma-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function genPlanEventId(): string {
  return `ma-plan-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

// Resolve display name from either API format (camelCase displayName) or legacy (snake_case display_name / name object)
function getExpertDisplayName(e: Expert): string {
  return e.displayName || e.display_name || e.id
}

function getTeamDisplayName(t: Team): string {
  return t.displayName || t.display_name || t.id
}

function getTeamLeadDisplayName(t: Team): string {
  return t.leadDisplayName || t.lead_display_name || ''
}

function getTeamLeadProfession(t: Team): string {
  return t.leadProfession || t.lead_profession || ''
}

function getMemberName(m: TeamMember): string {
  if (m.displayName) return m.displayName
  if (m.name) return m.name.zh || m.name.en || m.id
  return m.id
}

function getMemberProfession(m: TeamMember): string {
  if (typeof m.profession === 'string') return m.profession
  return (m.profession as any)?.zh || (m.profession as any)?.en || ''
}

function getMemberAvatar(m: TeamMember, fallback: string): string {
  if (!m.avatar) return fallback
  if (m.avatar.startsWith('avatars/')) return m.avatar.replace(/^avatars\//, '').charAt(0)
  return m.avatar.charAt(0)
}

const COLUMN_COLORS = ['#10b981', '#3b82f6', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4', '#f97316']

/** 由团队元数据建会话（列 = 成员，lead 排头）。openSession 与 restoreTeamSession 同源，
 *  避免两处各写一遍成员映射。team.members 缺失（拉不到）时返回 null。 */
function buildSession(team: Team, experts: Expert[], taskId?: string): MultiAgentSession | null {
  const memberList = team.members || []
  const leadMember = memberList.find(m => m.role === 'lead') || memberList[0]
  if (!leadMember) return null

  const agents: AgentColumn[] = memberList.map((m, i) => {
    const expert = experts.find(e => e.id === m.id)
    const displayName = getMemberName(m)
    return {
      id: m.id,
      displayName,
      profession: getMemberProfession(m),
      avatar: getMemberAvatar(m, displayName.charAt(0)),
      role: m.role,
      color: expert?.color || COLUMN_COLORS[i % COLUMN_COLORS.length],
      status: 'idle' as const,
      messages: [],
      config: expert?.config || { max_turn: 15, max_tokens: 50000, timeout_seconds: 180 }
    }
  })

  agents.sort((a, b) => (a.role === 'lead' ? -1 : b.role === 'lead' ? 1 : 0))

  return {
    teamId: team.id,
    taskId,
    teamName: getTeamDisplayName(team),
    teamIcon: team.icon,
    agents,
    leadAgentId: leadMember.id,
    sessionId: undefined,
    isProcessing: false
  }
}

/** 一次派发的信息，只有子会话那条派发行上有：卡片的任务描述 + 派发时刻。
 *  派发时刻用来把卡片插回 lead 列的正确位置——result 行的时刻是**子完成**的时刻，
 *  按它排会把同一会话里的卡片全挤到末尾（实测根会话 5aec4b80：3 张卡片全落在第二轮
 *  提问之后）；实时那张卡片是在派发时出现的，派发时刻才是它该在的位置。 */
interface DispatchInfo {
  prompt: string
  dispatchedAt: number
}

/** 服务端一个 messages 行 = 该列的一个回合：`content` → user 气泡（用户提问 / 派给子的
 *  任务），`rows` 里 role=assistant 的拼接 → agent 气泡（空的中间行是纯工具回合，不产出
 *  气泡）。`msg_type='result'` 的 envelope 不是对话，只有 lead 列把它变成委派卡片 ——
 *  所以传了 `dispatches`（= lead 列）才渲染卡片，子会话里不会有这种行。 */
function rebuildColumnMessages(
  msgs: SessionMessageMeta[],
  agentId: string,
  dispatches?: Map<string, DispatchInfo>
): MultiAgentMessage[] {
  const out: MultiAgentMessage[] = []
  for (const m of msgs) {
    const ts = m.created_at ? Date.parse(m.created_at) : Date.now()
    if (m.msg_type === 'result') {
      const info = dispatches?.get(m.cid || '')
      if (dispatches) {
        out.push(rebuildDelegationCard(m, agentId, info?.prompt || '', info?.dispatchedAt ?? ts))
      }
      continue
    }
    const assistText = (m.rows || [])
      .filter((r) => r.role === 'assistant' && r.content)
      .map((r) => r.content as string)
      .join('')
    if (m.content && m.content.trim()) {
      out.push({ id: `hist-${m.id}-u`, agentId, role: 'user', content: m.content, type: 'text', timestamp: ts })
    }
    if (assistText.trim()) {
      out.push({ id: `hist-${m.id}-a`, agentId, role: 'agent', content: assistText, type: 'text', timestamp: ts })
    }
  }
  // 卡片按派发时刻插回气泡之间。sort 是稳定的（同一轮的气泡共享 created_at），
  // 所以气泡之间不会被重排。
  if (dispatches) out.sort((a, b) => a.timestamp - b.timestamp)
  return out
}

/** result envelope（`content` 是 `{status, output, error}` JSON）→ lead 列的一张委派卡片。
 *  envelope 只带产出、不带任务描述，任务描述由调用方从子会话那条 cid 相同的派发行取；
 *  取不到（子会话被 regenerate 截断过）就留空 prompt，其余字段照常。 */
function rebuildDelegationCard(
  m: SessionMessageMeta,
  agentId: string,
  prompt: string,
  ts: number
): MultiAgentMessage {
  let payload: { status?: string; output?: string; error?: string } = {}
  try {
    payload = JSON.parse(m.content || '{}')
  } catch {
    // 非 JSON 的空/畸形 envelope：按"无产出"处理，不让一条脏行毁掉整列回填
  }
  const full = payload.output || payload.error || ''
  return {
    id: `hist-${m.id}-d`,
    agentId,
    role: 'agent',
    type: 'delegation',
    content: '',
    delegation: {
      to: m.from_agent_id || '',
      taskType: 'delegate',
      prompt,
      status: payload.status === 'completed' ? 'done' : 'failed',
      // 预览与全文用同一个回落：失败时两者都是错误串，否则卡片会"预览有、展开空"
      outputPreview: full.slice(0, 200),
      outputFull: full
    },
    timestamp: ts
  }
}

/** 把回读到的历史落到某一列，返回新 session；不该落就返回 null（不改）。三道门：
 *  面板身份没变（取数途中切了任务就不能写串台）、列存在、列还没有非空内容
 *  （正在跑的列由中继 delta 填着，不能抢）。 */
function applyRebuiltColumn(
  cur: MultiAgentSession,
  expect: { teamId: string; taskId?: string },
  agentId: string,
  messages: MultiAgentMessage[],
  sessionId: string,
  status?: AgentColumn['status']
): MultiAgentSession | null {
  if (cur.teamId !== expect.teamId || cur.taskId !== expect.taskId) return null
  const col = cur.agents.find((a) => a.id === agentId)
  if (!col || col.messages.some((m) => m.content)) return null
  return {
    ...cur,
    // 面板级会话只在 lead 列绑：它同时是"这个任务已经连上服务端"的标记
    sessionId: agentId === cur.leadAgentId ? cur.sessionId || sessionId : cur.sessionId,
    agents: cur.agents.map((a) =>
      a.id === agentId ? { ...a, messages, sessionId, status: status || a.status } : a
    )
  }
}

// Append a text delta to a message, merging consecutive text segments.
function appendTextDelta(msg: MultiAgentMessage, delta: string): MultiAgentMessage {
  const segs = [...(msg.segments || [])]
  const last = segs[segs.length - 1]
  if (last && last.type === 'text') {
    segs[segs.length - 1] = { ...last, content: last.content + delta }
  } else {
    segs.push({ type: 'text', content: delta })
  }
  return { ...msg, content: msg.content + delta, segments: segs }
}

// Append a thinking delta to a message, merging consecutive thinking segments.
function appendThinkingDelta(msg: MultiAgentMessage, delta: string): MultiAgentMessage {
  const segs = [...(msg.segments || [])]
  const last = segs[segs.length - 1]
  if (last && last.type === 'thinking') {
    segs[segs.length - 1] = { ...last, content: last.content + delta }
  } else {
    segs.push({ type: 'thinking', content: delta })
  }
  return { ...msg, thinking: (msg.thinking || '') + delta, segments: segs }
}

// ---- Stream delta batching ----
// `agent.text`/`agent.thinking` deltas arrive at high frequency. Batching them into a
// single store update per animation frame avoids one re-render (and one full markdown
// re-parse) per chunk — the dominant cause of the multi-agent panel freezing under load.
type PendingDelta = { agentId: string; kind: 'text' | 'thinking'; delta: string }
let pendingDeltas: PendingDelta[] = []
let flushRaf: number | null = null

function scheduleFlush() {
  if (flushRaf !== null) return
  flushRaf = requestAnimationFrame(() => {
    flushRaf = null
    flushPendingDeltas()
  })
}

function flushPendingDeltas() {
  if (flushRaf !== null) {
    cancelAnimationFrame(flushRaf)
    flushRaf = null
  }
  if (pendingDeltas.length === 0) return

  const batch = pendingDeltas
  pendingDeltas = []

  const byAgent = new Map<string, PendingDelta[]>()
  for (const d of batch) {
    const list = byAgent.get(d.agentId)
    if (list) list.push(d)
    else byAgent.set(d.agentId, [d])
  }

  useMultiAgentStore.setState((s) => {
    if (!s.session) return s
    return {
      session: {
        ...s.session,
        agents: s.session.agents.map((a) => {
          const deltas = byAgent.get(a.id)
          if (!deltas) return a

          const msgs = [...a.messages]
          const last = msgs[msgs.length - 1]
          let msg: MultiAgentMessage =
            last && last.role === 'agent' && last.type !== 'delegation'
              ? { ...last }
              : { id: genMsgId(), agentId: a.id, role: 'agent', content: '', type: 'text', timestamp: Date.now() }

          for (const d of deltas) {
            msg = d.kind === 'text' ? appendTextDelta(msg, d.delta) : appendThinkingDelta(msg, d.delta)
          }
          msg = { ...msg, isStreaming: true }

          if (last && last.role === 'agent' && last.type !== 'delegation') {
            msgs[msgs.length - 1] = msg
          } else {
            msgs.push(msg)
          }
          return { ...a, messages: msgs }
        })
      }
    }
  })
}

interface MultiAgentState {
  session: MultiAgentSession | null
  isOpen: boolean
  teamDataCache: Record<string, { team: Team; experts: Expert[] }>

  openSession: (team: Team, experts: Expert[], taskId?: string) => void
  /** 切到某个团队任务：会话能重建就重建（冷缓存时回 Hub 拉元数据），失败返回 false。 */
  restoreTeamSession: (teamId: string, taskId?: string) => Promise<boolean>
  /** 回读团队任务的历史：子会话 → 各子列、lead 会话 → lead 列气泡、其中的 result
   *  信封 → 委派卡片（团队对话只在服务端，本地 taskStore 恒为空）。 */
  loadTeamHistory: (sessionId: string) => Promise<void>
  closeSession: () => void
  updateAgentStatus: (agentId: string, status: AgentColumn['status']) => void
  addMessage: (agentId: string, msg: Omit<MultiAgentMessage, 'id' | 'timestamp'>) => void
  appendOrAddMessage: (agentId: string, msg: Omit<MultiAgentMessage, 'id' | 'timestamp'>) => void
  /** Update the last agent message of a column, or create a fresh one if the last isn't a streamable agent message. */
  updateAgentMessage: (agentId: string, updater: (msg: MultiAgentMessage) => MultiAgentMessage) => void
  toggleColumn: (agentId: string) => void
  showAllColumns: () => void
  setProcessing: (val: boolean) => void
  setSessionId: (sessionId: string) => void
  /** 中继事件自报的会话 id，记回对应列（子 agent 的工具回投认它） */
  setAgentSessionId: (agentId: string, sessionId: string) => void
  /** Process a ServerEvent from the NDJSON stream. Returns true if the stream should end. */
  handleStreamEvent: (event: ServerEvent) => boolean

  // Plan / build / tool interactions — scoped to a specific agent column
  confirmPlan: (agentId: string) => void
  rejectPlan: (agentId: string) => void
  answerPlanQuestion: (agentId: string, textAnswer?: string) => void
  /** 确认某个待审批工具；传 toolId 认准该卡，不传则认第一张（批量下发时必须传） */
  confirmTool: (agentId: string, toolId?: string) => void
  skipTool: (agentId: string, toolId?: string) => void
  stopTools: (agentId: string) => void
}

// 工具回投的落点：优先该列自己中的会话（中继 chunk 自报），缺失才回落 lead。
// 用 lead 会话回投子 agent 的请求服务端匹配不到 invocation，结果会被当重复吞掉，
// 子干等满 330s 才报"执行结果不确定"。
function resolveAgentSession(s: MultiAgentState, agentId: string): string | undefined {
  return s.session?.agents.find((a) => a.id === agentId)?.sessionId || s.session?.sessionId
}

// Execute a client tool for the multi-agent flow and report the result back to the
// server. Used by both auto-execute (requires_approval=false) and user confirm.
// P2/P3：提交前取走本工具执行期的域名审批并入 body（沙箱 trap 已随沙箱功能暂禁用）
async function submitWithApprovals(sessionId: string, requestId: string, result: ToolResult): Promise<void> {
  const approvals = await ipcClient.proxy.takeNetworkApprovals()
  let body: ToolResult = result
  if (approvals && approvals.length) body = { ...body, network_approvals: approvals }
  await submitToolResult(sessionId, requestId, body)
}

async function runAgentTool(
  get: () => MultiAgentState,
  agentId: string,
  requestId: string,
  toolName: string,
  input: Record<string, unknown>
): Promise<void> {
  const sessionId = resolveAgentSession(get(), agentId)
  if (!sessionId) return
  const workspace = useSettingsStore.getState().settings.workspacePath
  let result: ToolResult
  try {
    result = await executeClientTool(toolName, input, workspace)
  } catch (err: any) {
    result = { status: 'error', error: err?.message || String(err), duration_ms: 0 }
  }
  // 记账：对账窗（client.tool_reconcile）到达时靠它答"已执行"，否则只能答 unknown，
  // 服务端会把写操作判成失败让模型重试 —— 而命令其实已经跑过了。
  if (result.status === 'success') recordExecutedTool(requestId, result)
  get().updateAgentMessage(agentId, (m) => ({
    ...m,
    tools: m.tools?.map((t) =>
      t.id === requestId
        ? {
            ...t,
            status: 'done' as const,
            result: result.output || result.error || 'Done',
            sandboxed: result.sandboxed ?? t.sandboxed
          }
        : t
    )
  }))
  submitWithApprovals(sessionId, requestId, result).catch((err) => {
    console.error('Tool result submission failed:', err)
  })
}

export const useMultiAgentStore = create<MultiAgentState>((set, get) => ({
  session: null,
  isOpen: false,
  teamDataCache: {},

  openSession: (team, experts, taskId) => {
    set((s) => ({
      teamDataCache: { ...s.teamDataCache, [team.id]: { team, experts } }
    }))
    const session = buildSession(team, experts, taskId)
    if (!session) return
    set({ isOpen: true, session })
  },

  closeSession: () => {
    set({ isOpen: false, session: null })
  },

  // 切换到一个团队任务：会话能当场重建就必须重建。旧实现只看内存里的
  // teamDataCache（只有本进程点过「使用」才写），重启后必然落空 → 返回 false
  // 被调用方丢弃 → 界面回落单 agent 面板，看起来"点了没反应"。现在冷缓存时
  // 回 Hub 拉元数据补建；拉不到才算失败（调用方据此提示用户）。
  // 认的是**任务**不是团队：同一个团队常被开成多个任务，只比 teamId 会把上一个
  // 任务的对话留在界面上。
  restoreTeamSession: async (teamId, taskId) => {
    const cur = get().session
    if (cur && cur.teamId === teamId && cur.taskId === taskId) return true

    const cached = get().teamDataCache[teamId]
    if (cached) {
      const session = buildSession(cached.team, cached.experts, taskId)
      if (!session) return false
      set({ isOpen: true, session })
      return true
    }

    let team: Team | undefined
    let experts: Expert[] = []
    try {
      const [teamsResp, expertsResp] = await Promise.all([
        fetchTeams(),
        fetchExperts().catch(() => ({ experts: [] as Expert[] }))
      ])
      team = teamsResp.teams.find((t) => t.id === teamId)
      experts = expertsResp.experts
    } catch {
      return false
    }
    if (!team) return false

    set((s) => ({ teamDataCache: { ...s.teamDataCache, [team.id]: { team, experts } } }))
    const session = buildSession(team, experts, taskId)
    if (!session) return false
    set({ isOpen: true, session })
    return true
  },

  // 团队任务的历史只在服务端（从不写 taskStore），切任务/重启时回读。三条数据流：
  // 子会话 → 子列、lead 会话 → lead 列气泡、lead 会话里的 result 信封 → 委派卡片。
  // 子 agent 的对话不在父会话里（父只收一行 result 信封），而在**子会话自己**的
  // conversation_history 里，所以得先列子会话再逐个回读。
  loadTeamHistory: async (sessionId) => {
    const start = get().session
    if (!start || start.sessionId) return
    const expect = { teamId: start.teamId, taskId: start.taskId }
    const leadId = start.leadAgentId

    const write = (
      agentId: string,
      messages: MultiAgentMessage[],
      colSessionId: string,
      status?: AgentColumn['status']
    ) => {
      set((s) => {
        if (!s.session) return s
        const next = applyRebuiltColumn(s.session, expect, agentId, messages, colSessionId, status)
        return next ? { session: next } : s
      })
    }

    // 1. 子会话 → 子列。顺带收下各自的派发信息（prompt + 派发时刻）：委派卡片的任务
    //    描述只有子会话里有——parent 的 result 信封只有产出，没有当初派了什么活。
    const dispatches = new Map<string, DispatchInfo>()
    let children: SessionAgentMeta[] = []
    try {
      children = (await fetchSessionAgents(sessionId)).agents
    } catch {
      children = [] // 列不到子会话不该连累 lead 列，下面照常回读
    }
    await Promise.all(
      children.map(async (child) => {
        let msgs: SessionMessageMeta[]
        try {
          msgs = (await fetchSessionMessages(child.session_id)).messages
        } catch {
          return
        }
        for (const m of msgs) {
          if (m.cid && m.content) {
            dispatches.set(m.cid, {
              prompt: m.content,
              dispatchedAt: m.created_at ? Date.parse(m.created_at) : Date.now()
            })
          }
        }
        const rebuilt = rebuildColumnMessages(msgs, child.agent_id)
        if (rebuilt.length === 0) return
        const last = msgs[msgs.length - 1]
        const status: AgentColumn['status'] =
          last?.status === 'completed' ? 'done' : last?.status === 'error' ? 'failed' : 'idle'
        write(child.agent_id, rebuilt, child.session_id, status)
      })
    )

    // 2. lead 列：气泡 + 委派卡片（卡片按派发时刻插回气泡之间，与实时观感同序）
    let leadMsgs: SessionMessageMeta[]
    try {
      leadMsgs = (await fetchSessionMessages(sessionId)).messages
    } catch {
      return
    }
    const rebuilt = rebuildColumnMessages(leadMsgs, leadId, dispatches)
    if (rebuilt.length === 0) return
    write(leadId, rebuilt, sessionId)
  },

  updateAgentStatus: (agentId, status) => {
    set((s) => {
      if (!s.session) return s
      const agent = s.session.agents.find(a => a.id === agentId)
      if (!agent || agent.status === status) return s
      return {
        session: {
          ...s.session,
          agents: s.session.agents.map(a =>
            a.id === agentId ? { ...a, status } : a
          )
        }
      }
    })
  },

  addMessage: (agentId, msg) => {
    set((s) => {
      if (!s.session) return s
      return {
        session: {
          ...s.session,
          agents: s.session.agents.map(a =>
            a.id === agentId
              ? {
                  ...a,
                  messages: [
                    ...a.messages,
                    { ...msg, id: genMsgId(), timestamp: Date.now() }
                  ]
                }
              : a
          )
        }
      }
    })
  },

  // For streaming text: appends content to the last message of the same role+type, or creates a new one
  appendOrAddMessage: (agentId, msg) => {
    set((s) => {
      if (!s.session) return s
      return {
        session: {
          ...s.session,
          agents: s.session.agents.map(a => {
            if (a.id !== agentId) return a
            const lastMsg = a.messages[a.messages.length - 1]
            if (
              lastMsg &&
              lastMsg.role === msg.role &&
              lastMsg.type === msg.type &&
              msg.type === 'text'
            ) {
              return {
                ...a,
                messages: [
                  ...a.messages.slice(0, -1),
                  { ...lastMsg, content: lastMsg.content + msg.content }
                ]
              }
            }
            return {
              ...a,
              messages: [
                ...a.messages,
                { ...msg, id: genMsgId(), timestamp: Date.now() }
              ]
            }
          })
        }
      }
    })
  },

  updateAgentMessage: (agentId, updater) => {
    set((s) => {
      if (!s.session) return s
      return {
        session: {
          ...s.session,
          agents: s.session.agents.map(a => {
            if (a.id !== agentId) return a
            const msgs = [...a.messages]
            const last = msgs[msgs.length - 1]
            if (last && last.role === 'agent' && last.type !== 'delegation') {
              msgs[msgs.length - 1] = updater(last)
            } else {
              const fresh: MultiAgentMessage = {
                id: genMsgId(),
                agentId,
                role: 'agent',
                content: '',
                type: 'text',
                timestamp: Date.now()
              }
              msgs.push(updater(fresh))
            }
            return { ...a, messages: msgs }
          })
        }
      }
    })
  },

  toggleColumn: (agentId) => {
    set((s) => {
      if (!s.session) return s
      return {
        session: {
          ...s.session,
          agents: s.session.agents.map(a =>
            a.id === agentId ? { ...a, _hidden: !a._hidden } : a
          )
        }
      }
    })
  },

  showAllColumns: () => {
    set((s) => {
      if (!s.session) return s
      return {
        session: {
          ...s.session,
          agents: s.session.agents.map(a => ({ ...a, _hidden: false }))
        }
      }
    })
  },

  setProcessing: (val) => {
    set((s) => {
      if (!s.session) return s
      return { session: { ...s.session, isProcessing: val } }
    })
  },

  setSessionId: (sessionId) => {
    set((s) => {
      if (!s.session) return s
      return { session: { ...s.session, sessionId } }
    })
  },

  setAgentSessionId: (agentId, sessionId) => {
    set((s) => {
      if (!s.session) return s
      if (!s.session.agents.some((a) => a.id === agentId && a.sessionId !== sessionId)) return s
      return {
        session: {
          ...s.session,
          agents: s.session.agents.map((a) => (a.id === agentId ? { ...a, sessionId } : a))
        }
      }
    })
  },

  handleStreamEvent: (event: ServerEvent): boolean => {
    const s = get().session
    if (!s) return true

    const agentId = event.agent_id || s.leadAgentId

    // 中继出父流的事件会自报发起会话（子 agent 的 tool_request / reconcile 要回到
    // 子会话去）；父自己的事件没有该字段，回落 lead。
    const eventSessionId = (event as { session_id?: string }).session_id
    if (eventSessionId && agentId !== s.leadAgentId) {
      get().setAgentSessionId(agentId, eventSessionId)
    }

    // Commit any pending text/thinking deltas before processing a structural event,
    // so segment ordering is preserved.
    if (event.type !== 'agent.text' && event.type !== 'agent.thinking') {
      flushPendingDeltas()
    }

    switch (event.type) {
      // ---- Thinking & Text ----
      case 'agent.text': {
        const agent = s.agents.find(a => a.id === agentId)
        if (agent && agent.status !== 'running') {
          get().updateAgentStatus(agentId, 'running')
        }
        pendingDeltas.push({ agentId, kind: 'text', delta: event.delta })
        scheduleFlush()
        return false
      }

      case 'agent.thinking': {
        get().updateAgentStatus(agentId, 'thinking')
        pendingDeltas.push({ agentId, kind: 'thinking', delta: event.delta })
        scheduleFlush()
        return false
      }

      // ---- Tool calls (agent-initiated) ----
      case 'agent.tool_call': {
        get().updateAgentMessage(agentId, (m) => {
          const segs = [...(m.segments || [])]
          segs.push({ type: 'tool_call', toolCallId: event.tool_call_id })
          const tool: ToolCall = {
            id: event.tool_call_id,
            name: event.tool_name,
            status: 'running',
            input: event.input,
            command: event.input?.command as string | undefined,
            detail: event.input?.command
              ? undefined
              : (typeof event.input === 'object' ? JSON.stringify(event.input).slice(0, 120) : undefined)
          }
          return { ...m, segments: segs, tools: [...(m.tools || []), tool] }
        })
        get().updateAgentStatus(agentId, 'running')
        return false
      }

      case 'agent.tool_result': {
        get().updateAgentMessage(agentId, (m) => ({
          ...m,
          tools: m.tools?.map((t) =>
            t.id === event.tool_call_id
              ? { ...t, status: event.result.success === false ? ('failed' as const) : ('done' as const), result: event.result.output || event.result.error || 'Done' }
              : t
          )
        }))
        return false
      }

      // ---- Client tool requests (requires_approval=false 时自动执行) ----
      case 'client.tool_request': {
        // 幂等去重：断线重连重放同一 request_id 时忽略
        const alreadyInserted = get().session?.agents.some((a) =>
          a.messages.some((m) => (m.tools || []).some((t) => t.id === event.request_id))
        )
        if (alreadyInserted) return false

        const requiresApproval = event.requires_approval !== false
        get().updateAgentMessage(agentId, (m) => {
          const segs = [...(m.segments || [])]
          segs.push({ type: 'tool_call', toolCallId: event.request_id })
          const tool: ToolCall = {
            id: event.request_id,
            name: event.tool_name,
            status: requiresApproval ? 'pending' : 'running',
            input: event.input,
            command: event.input?.command as string | undefined,
            approvalRequired: requiresApproval,
            policy: event.policy || null,
            // 角标预置按工具类型定真实值：bash 随策略、文件类恒裸机（带策略时）、MCP 等主进程回传
            sandboxed: presetClientToolSandboxed(event.tool_name, event.policy)
          }
          return { ...m, segments: segs, tools: [...(m.tools || []), tool] }
        })
        // P2/P3：整包策略到达 → 同步给本地代理 + 沙箱（含 sandbox.required/filesystem）
        ipcClient.proxy.setPolicy(event.policy || null)
        if (!requiresApproval) {
          runAgentTool(get, agentId, event.request_id, event.tool_name, event.input || {}).catch(() => {})
        }
        return false
      }

      case 'client.tool_timeout': {
        return false
      }

      // C-2 对账窗：服务端首段等待超时后来问"这次到底跑没跑"。本地账里有就补投真实
      // 结果，没有就如实答 unknown（不瞎编、不自动重执行）。回投必须打到发起请求的
      // 那个会话，否则服务端找不到 invocation。
      case 'client.tool_reconcile': {
        const reconcileSession = event.session_id || resolveAgentSession(get(), agentId)
        if (reconcileSession) {
          answerClientReconcile(reconcileSession, event.request_id).catch(() => {})
        }
        return false
      }

      // C-2 需确认：写类工具判不出是否生效 → 服务端不自动重放。在**发起该工具的那一列**
      // 提示人工核查，避免用户不知道"这次写可能已经生效了"而重复操作。
      case 'tool.reconcile_needs_confirm': {
        get().updateAgentMessage(agentId, (m) => ({
          ...m,
          segments: [
            ...(m.segments || []),
            {
              type: 'system_status' as const,
              message: `工具「${event.tool_name}」执行结果不确定（可能已生效）。系统已暂停自动重跑该回合以防重复执行——请先人工核查该操作是否已生效。`
            }
          ]
        }))
        return false
      }

      // ---- Plan events ----
      case 'plan.generated': {
        get().updateAgentMessage(agentId, (m) => ({
          ...m,
          planStatus: 'pending',
          segments: [
            ...(m.segments || []),
            { id: genPlanEventId(), timestamp: Date.now(), type: 'generated' as const }
          ]
        }))
        return false
      }

      case 'plan.question': {
        get().updateAgentMessage(agentId, (m) => ({
          ...m,
          segments: [
            ...(m.segments || []),
            {
              id: genPlanEventId(),
              timestamp: Date.now(),
              type: 'question' as const,
              question: event.question,
              options: event.options,
              input_type: event.input_type,
              answer: null
            }
          ]
        }))
        return false
      }

      case 'plan.question_timeout': {
        get().updateAgentMessage(agentId, (m) => ({
          ...m,
          segments: [
            ...(m.segments || []),
            { id: genPlanEventId(), timestamp: Date.now(), type: 'question' as const, answer: '(超时)' }
          ]
        }))
        return false
      }

      case 'plan.confirmed': {
        get().updateAgentMessage(agentId, (m) => ({
          ...m,
          planStatus: 'confirmed',
          segments: [
            ...(m.segments || []),
            { id: genPlanEventId(), timestamp: Date.now(), type: 'confirmed' as const }
          ]
        }))
        return false
      }

      case 'plan.rejected': {
        get().updateAgentMessage(agentId, (m) => ({
          ...m,
          planStatus: 'rejected',
          segments: [
            ...(m.segments || []),
            { id: genPlanEventId(), timestamp: Date.now(), type: 'rejected' as const }
          ]
        }))
        return false
      }

      case 'plan.edited': {
        get().updateAgentMessage(agentId, (m) => ({
          ...m,
          segments: [
            ...(m.segments || []),
            { id: genPlanEventId(), timestamp: Date.now(), type: 'edited' as const }
          ]
        }))
        return false
      }

      // ---- Build events ----
      case 'build.step_pending': {
        get().updateAgentMessage(agentId, (m) => {
          const segs = [...(m.segments || [])]
          segs.push({ type: 'tool_call', toolCallId: event.tool_call_id })
          const tool: ToolCall = {
            id: event.tool_call_id,
            name: event.tool_name,
            status: 'pending',
            command: event.input?.command as string | undefined,
            detail: event.reasoning
              || (event.input?.command
                  ? undefined
                  : JSON.stringify(event.input).slice(0, 120))
          }
          return { ...m, segments: segs, tools: [...(m.tools || []), tool] }
        })
        return false
      }

      case 'build.step_confirmed': {
        get().updateAgentMessage(agentId, (m) => ({
          ...m,
          tools: m.tools?.map((t) => t.id === event.tool_call_id ? { ...t, status: 'running' as const } : t)
        }))
        return false
      }

      case 'build.step_skipped': {
        get().updateAgentMessage(agentId, (m) => ({
          ...m,
          tools: m.tools?.map((t) => t.id === event.tool_call_id ? { ...t, status: 'skipped' as const } : t)
        }))
        return false
      }

      case 'build.aborted': {
        return false
      }

      // ---- System status ----
      case 'system.status': {
        get().updateAgentMessage(agentId, (m) => ({
          ...m,
          segments: [
            ...(m.segments || []),
            { type: 'system_status', message: event.message }
          ]
        }))
        return false
      }

      // ---- Multi-agent delegation / status ----
      case 'session.publish': {
        get().addMessage(event.agent_id, {
          agentId: event.agent_id,
          role: 'agent',
          content: '',
          type: 'delegation',
          delegation: {
            to: event.to,
            taskType: event.task_type,
            prompt: event.prompt,
            status: 'running'
          }
        })
        return false
      }

      case 'agent.status': {
        get().updateAgentStatus(event.to, event.status)

        // done 与 failed 都是**终态**，卡片都要收尾；只认 done 的话子任务报错后
        // 卡片会永远停在"执行中"。waiting 也算未收尾（见 task.waiting 分支）。
        if (event.status === 'done' || event.status === 'failed') {
          const sess = get().session
          if (sess) {
            set({
              session: {
                ...sess,
                agents: sess.agents.map(a => {
                  if (a.id !== event.agent_id) return a
                  return {
                    ...a,
                    messages: a.messages.map(m => {
                      if (
                        m.type === 'delegation' &&
                        m.delegation &&
                        m.delegation.to === event.to &&
                        (m.delegation.status === 'running' ||
                          m.delegation.status === 'waiting')
                      ) {
                        return {
                          ...m,
                          delegation: {
                            ...m.delegation,
                            status: event.status,
                            outputPreview: event.output_preview
                              ? `${event.output_preview}`
                              : '',
                            outputFull: event.output ?? event.output_preview ?? ''
                          }
                        }
                      }
                      return m
                    })
                  }
                })
              }
            })
            // 这里**不再**把 output_preview 追加成子那一列的一条消息：子列的内容归
            // 中继过来的流式文本（agent.text，逐 delta、完整），而 output_preview 是
            // 服务端结果提取（`_last_assistant_text` 取末条）后再砍到 200 字的片段，
            // 是**另一条路**的产物。追加进去会在子列末尾多出一条断在句中的短气泡。
            // 全文走卡片：outputFull + 展开态。
          }
        }
        return false
      }

      case 'task.waiting': {
        // 父说完这一轮、进 WAITING_CHILDREN 等子回信（§9.11.8）。这段时间**没有**
        // message.complete、流也不关，可能挂几分钟——所以要把在途的那几张委派卡片
        // 显式改成"等待中"，否则界面一直显示"执行中..."，看起来像卡住了。
        const inbound = new Set(event.in_flight.map(i => i.agent_id))
        get().updateAgentStatus(event.agent_id, 'waiting')
        const sess = get().session
        if (sess) {
          set({
            session: {
              ...sess,
              agents: sess.agents.map(a => {
                if (a.id !== event.agent_id) return a
                return {
                  ...a,
                  messages: a.messages.map(m =>
                    m.type === 'delegation' &&
                    m.delegation &&
                    m.delegation.status === 'running' &&
                    inbound.has(m.delegation.to)
                      ? { ...m, delegation: { ...m.delegation, status: 'waiting' } }
                      : m
                  )
                }
              })
            }
          })
        }
        return false
      }

      case 'session.summary': {
        get().addMessage(event.agent_id, {
          agentId: event.agent_id,
          role: 'agent',
          content: event.summary,
          type: 'text'
        })
        get().updateAgentStatus(event.agent_id, 'done')
        return false
      }

      case 'message.complete': {
        const sess = get().session
        if (sess) {
          set({
            session: {
              ...sess,
              agents: sess.agents.map(a => ({
                ...a,
                status: (a.id === sess.leadAgentId || a.status === 'running' ||
                  a.status === 'thinking' || a.status === 'waiting')
                  ? 'done' as const
                  : a.status,
                messages: a.messages.map(m => (m.isStreaming ? { ...m, isStreaming: false } : m))
              }))
            }
          })
        }
        get().setProcessing(false)
        return true
      }

      case 'message.error': {
        const sess = get().session
        if (sess) {
          get().addMessage(sess.leadAgentId, {
            agentId: sess.leadAgentId,
            role: 'agent',
            content: `**错误:** ${(event as any).error || '未知错误'}`,
            type: 'text'
          })
          if (event.fatal === true) {
            const cur = get().session
            if (cur) {
              set({
                session: {
                  ...cur,
                  agents: cur.agents.map(a => ({
                    ...a,
                    messages: a.messages.map(m => (m.isStreaming ? { ...m, isStreaming: false } : m))
                  }))
                }
              })
            }
          }
        }
        get().setProcessing(false)
        return event.fatal === true
      }

      default:
        return false
    }
  },

  // ---- Plan / build / tool interactions ----

  confirmPlan: (agentId) => {
    const sessionId = get().session?.sessionId
    if (!sessionId) return

    get().updateAgentMessage(agentId, (m) => ({
      ...m,
      planStatus: 'confirmed',
      segments: [
        ...(m.segments || []),
        { id: genPlanEventId(), timestamp: Date.now(), type: 'confirmed' as const }
      ]
    }))

    planApi.confirm(sessionId).catch((err) => {
      console.error('Plan confirm failed:', err)
    })
  },

  rejectPlan: (agentId) => {
    const sessionId = get().session?.sessionId
    if (!sessionId) return

    get().updateAgentMessage(agentId, (m) => ({
      ...m,
      planStatus: 'rejected',
      segments: [
        ...(m.segments || []),
        { id: genPlanEventId(), timestamp: Date.now(), type: 'rejected' as const }
      ]
    }))

    cancelSession(sessionId).catch((err) => {
      console.error('Plan cancel failed:', err)
    })
  },

  answerPlanQuestion: (agentId, textAnswer) => {
    const sessionId = get().session?.sessionId
    if (!sessionId) return

    const answer = textAnswer || '已选择'
    get().updateAgentMessage(agentId, (m) => {
      const segs = (m.segments || []).map((s, i, arr) => {
        const isLastUnanswered =
          s.type === 'question' &&
          s.answer === null &&
          !arr.slice(i + 1).some(q => q.type === 'question' && q.answer === null)
        return isLastUnanswered ? { ...s, answer } : s
      })
      return { ...m, segments: segs }
    })

    planApi.answer(sessionId, answer).catch((err) => {
      console.error('Plan answer failed:', err)
    })
  },

  confirmTool: (agentId, toolId) => {
    const sessionId = resolveAgentSession(get(), agentId)
    if (!sessionId) return

    let pendingTool: ToolCall | undefined
    get().updateAgentMessage(agentId, (m) => {
      // 一轮可同时下发多张待审批卡：认准被点的那张，否则永远作用在第一个上
      pendingTool = toolId
        ? m.tools?.find(t => t.id === toolId && t.status === 'pending')
        : m.tools?.find(t => t.status === 'pending')
      if (!pendingTool) return m
      return {
        ...m,
        tools: m.tools?.map((t) => t.id === pendingTool!.id ? { ...t, status: 'running' as const } : t)
      }
    })

    if (!pendingTool) return
    const isClientTool = pendingTool.input !== undefined

    if (isClientTool) {
      const workspace = useSettingsStore.getState().settings.workspacePath
      executeClientTool(pendingTool.name, pendingTool.input || {}, workspace).then((result) => {
        if (result.status === 'success') recordExecutedTool(pendingTool!.id, result)
        get().updateAgentMessage(agentId, (m) => ({
          ...m,
          tools: m.tools?.map((t) =>
            t.id === pendingTool!.id
              ? { ...t, status: result.status === 'error' ? ('failed' as const) : ('done' as const), result: result.output || result.error || 'Done', sandboxed: result.sandboxed ?? t.sandboxed }
              : t
          )
        }))
        submitWithApprovals(sessionId, pendingTool!.id, result).catch((err) => {
          console.error('Tool result submission failed:', err)
        })
      }).catch((err) => {
        console.error('Client tool execution failed:', err)
        get().updateAgentMessage(agentId, (m) => ({
          ...m,
          tools: m.tools?.map((t) =>
            t.id === pendingTool!.id ? { ...t, status: 'failed' as const, result: err.message || 'Tool execution failed' } : t
          )
        }))
        submitToolResult(sessionId, pendingTool!.id, {
          status: 'error',
          error: err.message || 'Tool execution failed',
          duration_ms: 0
        }).catch(() => {})
      })
    } else {
      buildApi.confirm(sessionId, pendingTool.name).catch((err) => {
        console.error('Build confirm failed:', err)
      })
    }
  },

  skipTool: (agentId, toolId) => {
    const sessionId = resolveAgentSession(get(), agentId)
    if (!sessionId) return

    let pendingTool: ToolCall | undefined
    get().updateAgentMessage(agentId, (m) => {
      pendingTool = toolId
        ? m.tools?.find(t => t.id === toolId && t.status === 'pending')
        : m.tools?.find(t => t.status === 'pending')
      if (!pendingTool) return m
      return {
        ...m,
        tools: m.tools?.map((t) => t.id === pendingTool!.id ? { ...t, status: 'skipped' as const } : t)
      }
    })

    if (!pendingTool) return
    const isClientTool = pendingTool.input !== undefined

    if (isClientTool) {
      submitToolResult(sessionId, pendingTool.id, {
        status: 'error',
        error: 'User skipped the tool',
        duration_ms: 0,
        skipped: true
      }).catch((err) => {
        console.error('Skip tool result submission failed:', err)
      })
    } else {
      buildApi.skip(sessionId).catch((err) => {
        console.error('Build skip failed:', err)
      })
    }
  },

  stopTools: (agentId) => {
    const leadSessionId = get().session?.sessionId
    if (!leadSessionId) return
    // 工具结果要回投到**发起请求的会话**（可能是子列），而 cancel 打的是 lead
    const toolSessionId = resolveAgentSession(get(), agentId) || leadSessionId

    let pendingTools: ToolCall[] = []
    get().updateAgentMessage(agentId, (m) => {
      pendingTools = m.tools?.filter(t => t.status === 'pending') || []
      return {
        ...m,
        tools: m.tools?.map((t) => t.status === 'pending' ? { ...t, status: 'skipped' as const } : t)
      }
    })

    for (const t of pendingTools) {
      if (t.input !== undefined) {
        submitToolResult(toolSessionId, t.id, {
          status: 'error',
          error: 'User cancelled',
          duration_ms: 0
        }).catch((err) => {
          console.error('Stop tool result submission failed:', err)
        })
      }
    }

    cancelSession(leadSessionId).catch((err) => {
      console.error('Build cancel failed:', err)
    })
  }
}))
