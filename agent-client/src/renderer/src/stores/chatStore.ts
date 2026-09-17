import { create } from 'zustand'
import type { Message, MessageSegment, ServerEvent, Task, ToolCall, SideEffectItem } from '../types'
import { sendChatMessage, reconnectStream, planApi, buildApi, cancelSession, executeClientTool, presetClientToolSandboxed, submitToolResult, reprocessMessage, fetchSessionEffects, type ToolResult } from '../services/api'
import { useTaskStore } from './taskStore'
import { useQueueStore } from './queueStore'
import { useModeStore } from './modeStore'
import { useSettingsStore } from './settingsStore'
import { useMultiAgentStore } from './multiAgentStore'
import { ipcClient } from '../services/ipcClient'
import { recordExecutedTool, submitWithOutbox, answerClientReconcile, flushToolOutbox } from '../services/toolOutbox'

let planEventIdCounter = 1
/** D：按会话在途的 effects 拉取集合（防并发重复 GET）。 */
const effectsFetching = new Set<string>()

function genMsgId(): string {
  return crypto.randomUUID()
}
function genPlanEventId(): string {
  return `pe-${Date.now()}-${planEventIdCounter++}`
}

// Rebuild `content` from text-type segments only, excluding tool_call markers.
// This ensures raw tool call text that leaked through agent.text deltas is removed
// once the structured tool event fires and inserts a tool_call marker segment.
function rebuildContentFromSegments(
  m: { content: string; segments?: Array<{ type: string; content?: string }> }
): string {
  if (!m.segments || m.segments.length === 0) return m.content
  return m.segments
    .filter(s => s.type === 'text')
    .map(s => (s as { content: string }).content)
    .join('')
}

// Clean the last text segment by removing tool call raw text.
// Strategy: find any trailing JSON/XML artifact that mentions the tool name.
function cleanLastTextSegment(
  segs: Array<{ type: string; content?: string }>,
  toolName: string
) {
  for (let i = segs.length - 1; i >= 0; i--) {
    if (segs[i].type === 'text' && segs[i].content) {
      const text = segs[i].content!
      // Remove trailing JSON that contains the tool name
      const cleaned = stripTrailingToolArtifact(text, toolName)
      segs[i] = { ...segs[i], content: cleaned }
      break
    }
  }
}

// Strip trailing text that looks like a tool call JSON/XML artifact.
// Uses the known tool name for precise matching.
function stripTrailingToolArtifact(text: string, toolName: string): string {
  let result = text

  // 1. Remove complete XML tool tags (anywhere)
  result = result.replace(/<tool_call>[\s\S]*?<\/tool_call>/gi, '')
  result = result.replace(/<tool_use>[\s\S]*?<\/tool_use>/gi, '')
  result = result.replace(/<function_calls>[\s\S]*?<\/function_calls>/gi, '')
  result = result.replace(/<invoke\b[^>]*>[\s\S]*?<\/invoke>/gi, '')

  // 2. Remove trailing unclosed tool XML tags
  result = result.replace(/\s*<(?:tool_call|tool_use|function_calls|invoke)\b[\s\S]*$/gi, '')

  // 3. Find the tool name in the text (case-insensitive) and trim from the last JSON/XML structure before it.
  // This is the most reliable strategy: the raw tool text will contain the tool name.
  {
    const nameIdx = result.toLowerCase().indexOf(toolName.toLowerCase())
    if (nameIdx >= 0) {
      // Scan backwards from the tool name to find the start of the tool call artifact
      // Look for common delimiters: {, <, newline+space, etc.
      const before = result.slice(0, nameIdx)
      // Find the last "natural text boundary" before the tool name
      const boundaryMatch = before.match(/^(.*?)(?:\s*(?:\{|<(?:tool_call|tool_use|function_calls|invoke)\b))[\s\S]*$/i)
      if (boundaryMatch) {
        result = boundaryMatch[1].trimEnd()
      }
    }
  }

  // 4. Fallback: strip trailing unclosed JSON (brace counting)
  if (result === text) {
    let depth = 0
    let lastUnclosed = -1
    for (let i = result.length - 1; i >= 0; i--) {
      if (result[i] === '}') depth++
      else if (result[i] === '{') {
        if (depth === 0) { lastUnclosed = i; break }
        depth--
      }
    }
    if (lastUnclosed >= 0) {
      const tail = result.slice(lastUnclosed)
      if (/"(?:name|tool_name|tool_call|input|command|arguments)"/.test(tail)) {
        result = result.slice(0, lastUnclosed).trimEnd()
      }
    }
  }

  // 5. Fallback: strip complete trailing JSON object
  if (result === text) {
    const lastClose = result.lastIndexOf('}')
    if (lastClose >= 0) {
      let depth = 0, openPos = -1
      for (let i = lastClose; i >= 0; i--) {
        if (result[i] === '}') depth++
        else if (result[i] === '{') {
          depth--
          if (depth === 0) { openPos = i; break }
        }
      }
      if (openPos >= 0) {
        const block = result.slice(openPos, lastClose + 1)
        const after = result.slice(lastClose + 1).trim()
        if (/"(?:name|tool_name|tool_call)"\s*:/.test(block) &&
            (!after || /^(?:json|```|`)?\s*$/.test(after))) {
          result = result.slice(0, openPos).trimEnd()
        }
      }
    }
  }

  // 6. Remove markdown code fences wrapping tool JSON
  result = result.replace(/\s*```(?:json)?\s*\{[\s\S]*?\}\s*```/g, '')

  // 7. Clean excessive whitespace
  result = result.replace(/\n{3,}/g, '\n\n')

  return result.trim()
}

// ===== Stream delta batching =====
// `agent.text`/`agent.thinking` deltas arrive at high frequency. Batching them into a
// single store update per animation frame avoids a re-render (and a full markdown
// re-parse) per chunk — the dominant cause of the chat panel freezing under load.
type PendingDelta = { kind: 'text' | 'thinking'; delta: string }
// 增量按任务分桶：并发流下各任务的文本/思考必须写回各自的气泡，而非"当前选中任务"。
const pendingDeltas = new Map<string, PendingDelta[]>()
let flushRaf: number | null = null

function pushDelta(taskId: string, delta: PendingDelta) {
  const arr = pendingDeltas.get(taskId)
  if (arr) arr.push(delta)
  else pendingDeltas.set(taskId, [delta])
  scheduleFlush()
}

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
  if (pendingDeltas.size === 0) return

  const batches = new Map(pendingDeltas)
  pendingDeltas.clear()

  useTaskStore.setState((s) => {
    const tasks = s.tasks.map((t) => {
      const batch = batches.get(t.id)
      if (!batch || batch.length === 0) return t
      const msgs = [...t.messages]
      const lastIdx = msgs.length - 1
      const last = msgs[lastIdx]
      if (!last || last.role !== 'assistant') return t

      let m = last
      for (const d of batch) {
        const segs: MessageSegment[] = m.segments ? [...m.segments] : []
        const lastSeg = segs[segs.length - 1]
        if (d.kind === 'text') {
          if (lastSeg && lastSeg.type === 'text') {
            segs[segs.length - 1] = { ...lastSeg, content: lastSeg.content + d.delta }
          } else {
            segs.push({ type: 'text', content: d.delta })
          }
          m = { ...m, content: m.content + d.delta, segments: segs }
        } else {
          if (lastSeg && lastSeg.type === 'thinking') {
            segs[segs.length - 1] = { ...lastSeg, content: lastSeg.content + d.delta }
          } else {
            segs.push({ type: 'thinking', content: d.delta })
          }
          m = { ...m, thinking: (m.thinking || '') + d.delta, segments: segs }
        }
      }

      msgs[lastIdx] = m
      return { ...t, messages: msgs }
    })
    return { tasks }
  })
}

// ===== Shared event handler factory =====
// Used by both sendMessage and reconnect to avoid code duplication.

/** 处理态按任务记录：并发流下"谁在跑"必须逐任务判断，不能用一个全局布尔锁。 */
function setTaskProcessing(taskId: string, processing: boolean) {
  useChatStore.setState((s) => {
    if (processing) {
      if (s.processingTaskIds[taskId]) return {}
      return { processingTaskIds: { ...s.processingTaskIds, [taskId]: true } }
    }
    if (!s.processingTaskIds[taskId]) return {}
    const next = { ...s.processingTaskIds }
    delete next[taskId]
    return { processingTaskIds: next }
  })
}

// 事件处理器持有的 taskStore 视图：所有写入都落到"发起该流的任务"，而非用户当前
// 选中的任务。方法名/参数与 store 原 API 一致，使 switch 分支无需逐个改。
function scopedTaskStore(taskId: string) {
  const ts = () => useTaskStore.getState()
  return {
    getTask: () => ts().tasks.find((t) => t.id === taskId),
    addMessage: (m: Message) => ts().addMessage(m, taskId),
    updateLastAssistantMessage: (u: (m: Message) => Message) => ts().updateLastAssistantMessage(u, taskId),
    patchMessage: (id: string, p: Partial<Message>) => ts().patchMessage(id, p, taskId),
    removeMessage: (id: string) => ts().removeMessage(id, taskId),
    resetMessage: (id: string) => ts().resetMessage(id, taskId),
    updateTaskSeq: (seq: number) => ts().updateTaskSeq(seq, taskId)
  }
}

function createEventHandler(
  lastSeqRef: { current: number },
  taskId: string
): (event: ServerEvent) => void {
  const taskStore = scopedTaskStore(taskId)
  return (event: ServerEvent) => {
    lastSeqRef.current = event.seq

    // Commit pending text/thinking deltas before a structural event, so ordering is kept.
    if (event.type !== 'agent.text' && event.type !== 'agent.thinking') {
      flushPendingDeltas()
    }

    switch (event.type) {
      // ---- Thinking & Text ----
      case 'agent.thinking':
        pushDelta(taskId, { kind: 'thinking', delta: event.delta })
        break

      case 'agent.text':
        pushDelta(taskId, { kind: 'text', delta: event.delta })
        break

      // ---- Tool Calls (agent-initiated) ----
      case 'agent.tool_call':
        taskStore.updateLastAssistantMessage((m) => {
          const segs = m.segments || []
          cleanLastTextSegment(segs, event.tool_name)
          // Rebuild content from clean segments
          m.content = rebuildContentFromSegments(m)
          // Also clean thinking text
          if (m.thinking) m.thinking = stripTrailingToolArtifact(m.thinking, event.tool_name)
          m.segments = segs
          // Insert tool_call marker for text ordering
          m.segments.push({ type: 'tool_call' as const, toolCallId: event.tool_call_id })
          m.tools = [
            ...(m.tools || []),
            {
              id: event.tool_call_id,
              name: event.tool_name,
              status: 'running' as const,
              command: event.input?.command as string | undefined,
              detail: event.input?.command
                ? undefined
                : (typeof event.input === 'object'
                    ? JSON.stringify(event.input).slice(0, 120)
                    : undefined)
            }
          ]
          return { ...m }
        })
        break

      case 'agent.tool_result':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          tools: m.tools?.map((t) =>
            t.id === event.tool_call_id
              ? {
                  ...t,
                  status: event.result.success === false ? ('failed' as const) : ('done' as const),
                  result: event.result.output || event.result.error || 'Done'
                }
              : t
          )
        }))
        break

      // ---- Tool Requests (client-side; requires_approval=false 时自动执行) ----
      case 'client.tool_request': {
        // 幂等去重：断线重连按 since_seq 重放会重复投递同一 request_id，已存在则忽略
        const alreadyInserted = taskStore.getTask()?.messages.some((m) =>
          (m.tools || []).some((t) => t.id === event.request_id)
        )
        if (alreadyInserted) break

        const requiresApproval = event.requires_approval !== false
        taskStore.updateLastAssistantMessage((m) => {
          const segs = m.segments || []
          cleanLastTextSegment(segs, event.tool_name)
          m.content = rebuildContentFromSegments(m)
          if (m.thinking) m.thinking = stripTrailingToolArtifact(m.thinking, event.tool_name)
          m.segments = segs
          m.segments.push({ type: 'tool_call' as const, toolCallId: event.request_id })
          m.tools = [
            ...(m.tools || []),
            {
              id: event.request_id,
              name: event.tool_name,
              status: requiresApproval ? ('pending' as const) : ('running' as const),
              input: event.input,
              command: event.input?.command as string | undefined,
              approvalRequired: requiresApproval,
              // C-3：幂等档随包下发存卡（read-only/idempotent 超时安全；non-idempotent 需人工）
              idempotency: (event.idempotency ?? undefined) as ToolCall['idempotency'],
              policy: event.policy || null,
              // 角标预置按工具类型定真实值：bash 随策略、文件类恒裸机（带策略时）、MCP 等主进程回传
              sandboxed: presetClientToolSandboxed(event.tool_name, event.policy)
            }
          ]
          return { ...m }
        })
        // P2/P3：整包策略到达 → 同步给本地代理 + 沙箱（含 sandbox.required/filesystem）
        ipcClient.proxy.setPolicy(event.policy || null)
        if (!requiresApproval) {
          // 自动执行：服务端已放行，直接执行并回传结果
          runClientTool(taskId, event.request_id, event.tool_name, event.input || {}).catch(() => {})
        }
        break
      }

      case 'client.tool_timeout':
        break

      // ---- C-2 对账窗 ----
      case 'client.tool_reconcile': {
        // 服务端进对账窗：本地已执行 → 答 executed 补投真实结果；未执行/判不出 → 如实 unknown
        const curTask = taskStore.getTask()
        if (curTask?.sessionId) {
          answerClientReconcile(curTask.sessionId, event.request_id).catch((err) => {
            console.error('Reconcile answer failed:', err)
          })
        }
        break
      }

      case 'tool.reconcile_needs_confirm':
        // 写类工具判不出是否生效 → 服务端不自动重放；提示用户人工核查，避免静默双写。
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          segments: [
            ...(m.segments || []),
            {
              type: 'system_status' as const,
              message: `工具「${event.tool_name}」执行结果不确定（可能已生效）。系统已暂停自动重跑该回合以防重复执行——请先人工核查该操作是否已生效。`
            }
          ]
        }))
        break

      // ---- Plan events ----
      case 'plan.generated':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          planStatus: 'pending',
          segments: [
            ...(m.segments || []),
            {
              id: genPlanEventId(),
              timestamp: Date.now(),
              type: 'generated' as const
            }
          ]
        }))
        break

      case 'plan.question':
        taskStore.updateLastAssistantMessage((m) => ({
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
        break

      case 'plan.question_timeout':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          segments: [
            ...(m.segments || []),
            {
              id: genPlanEventId(),
              timestamp: Date.now(),
              type: 'question' as const,
              answer: '(超时)'
            }
          ]
        }))
        break

      case 'plan.confirmed':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          planStatus: 'confirmed',
          segments: [
            ...(m.segments || []),
            {
              id: genPlanEventId(),
              timestamp: Date.now(),
              type: 'confirmed' as const
            }
          ]
        }))
        break

      case 'plan.rejected':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          planStatus: 'rejected',
          processCollapsed: true,
          segments: [
            ...(m.segments || []),
            {
              id: genPlanEventId(),
              timestamp: Date.now(),
              type: 'rejected' as const
            }
          ]
        }))
        break

      case 'plan.edited':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          segments: [
            ...(m.segments || []),
            {
              id: genPlanEventId(),
              timestamp: Date.now(),
              type: 'edited' as const
            }
          ]
        }))
        break

      // ---- Build events ----
      case 'build.step_pending':
        taskStore.updateLastAssistantMessage((m) => {
          const segs = m.segments || []
          cleanLastTextSegment(segs, event.tool_name)
          m.content = rebuildContentFromSegments(m)
          if (m.thinking) m.thinking = stripTrailingToolArtifact(m.thinking, event.tool_name)
          m.segments = segs
          m.segments.push({ type: 'tool_call' as const, toolCallId: event.tool_call_id })
          m.tools = [
            ...(m.tools || []),
            {
              id: event.tool_call_id,
              name: event.tool_name,
              status: 'pending' as const,
              command: event.input?.command as string | undefined,
              detail: event.reasoning
                || (event.input?.command
                    ? undefined
                    : JSON.stringify(event.input).slice(0, 120))
            }
          ]
          return { ...m }
        })
        break

      case 'build.step_confirmed':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          tools: m.tools?.map((t) =>
            t.id === event.tool_call_id && t.status === 'pending'
              ? { ...t, status: 'running' as const }
              : t
          )
        }))
        break

      case 'build.step_skipped':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          tools: m.tools?.map((t) =>
            t.id === event.tool_call_id ? { ...t, status: 'skipped' as const } : t
          )
        }))
        break

      case 'build.aborted':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          processCollapsed: true
        }))
        setTaskProcessing(taskId, false)
        break

      // ---- Lifecycle events ----
      case 'message.complete':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          serverId: m.serverId || event.message_id,   // M4：重跑/恢复需要服务端 message_id
          serverStatus: 'completed' as const,
          processCollapsed: true,
          isStreaming: false
        }))
        taskStore.updateTaskSeq(lastSeqRef.current)
        setTaskProcessing(taskId, false)
        // D：终态后拉 /effects 重建该会话副作用分块（按 attempt 分组）。
        void useChatStore.getState().refreshEffects(taskId)
        break

      case 'message.error':
        // fatal（超时/内部错误）与 cancelled（软取消）都是终态：释放本任务处理态。
        // M1 起服务端取消不再尾随 message.complete，必须在此收口。
        if (event.fatal || event.code === 'cancelled') {
          taskStore.updateLastAssistantMessage((m) => ({
            ...m,
            serverId: m.serverId || event.message_id,   // M4：同 message.start 回填
            serverStatus: event.code === 'cancelled' ? 'cancelled' as const : 'error' as const,
            // cancelled 保留已流出的部分内容，避免用错误文案盖掉半截正文
            content: m.content || (event.code === 'cancelled' ? '**已停止**' : `**Error:** ${event.message}`),
            isStreaming: false,
            processCollapsed: true
          }))
          taskStore.updateTaskSeq(lastSeqRef.current)
          setTaskProcessing(taskId, false)
          // D：中断/异常也是终态——凡本轮有已 completed 的写工具都要如实交代。
          void useChatStore.getState().refreshEffects(taskId)
        }
        break

      case 'message.waiting_timeout':
        break

      case 'message.queued':
        break

      case 'message.start':
        // 回填服务端 message_id：供 M4 重新生成/继续按钮定位同一条 Message 行。
        taskStore.updateLastAssistantMessage((m) => (
          m.serverId ? m : { ...m, serverId: event.message_id }
        ))
        break

      case 'queue.updated':
        break

      // ---- Session events ----
      case 'session.timeout':
        break

      case 'session.recovered':
        break

      case 'system.status':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          segments: [
            ...(m.segments || []),
            { type: 'system_status' as const, message: event.message }
          ]
        }))
        break

      // ---- Multi-Agent Events ----
      case 'agent.status': {
        useMultiAgentStore.getState().updateAgentStatus(event.to, event.status)
        break
      }

      case 'session.publish': {
        const maStore = useMultiAgentStore.getState()
        if (maStore.session) {
          maStore.addMessage(event.agent_id, {
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
        }
        break
      }

      case 'session.summary': {
        const maStore2 = useMultiAgentStore.getState()
        if (maStore2.session && event.summary) {
          maStore2.addMessage(event.agent_id, {
            agentId: event.agent_id,
            role: 'agent',
            content: event.summary,
            type: 'text'
          })
          if (event.results) {
            event.results.forEach(r => {
              maStore2.updateAgentStatus(r.agent_id, 'done')
            })
          }
        }
        break
      }

      case 'heartbeat':
        break

      default:
        break
    }
  }
}

// Execute a client tool for the single-chat flow and report the result back to the
// server.
// P2/P3：提交前取走本工具执行期的域名审批并入 body（沙箱 trap 已随沙箱功能暂禁用）
async function submitWithApprovals(
  sessionId: string,
  requestId: string,
  result: ToolResult
): Promise<void> {
  const approvals = await ipcClient.proxy.takeNetworkApprovals()
  let body: ToolResult = result
  if (approvals && approvals.length) body = { ...body, network_approvals: approvals }
  // C-2：直投失败自动落 outbox 重放；成功后清理本地执行账。
  await submitWithOutbox(sessionId, requestId, body)
}

// server. Used by both auto-execute (requires_approval=false) and user confirm.
async function runClientTool(
  taskId: string,
  requestId: string,
  toolName: string,
  input: Record<string, unknown>
): Promise<void> {
  const workspace = useSettingsStore.getState().settings.workspacePath
  let result: ToolResult
  try {
    result = await executeClientTool(toolName, input, workspace)
  } catch (err: any) {
    result = { status: 'error', error: err?.message || String(err), duration_ms: 0 }
  }
  // C-2：成功执行的记入本地账，供对账（client.tool_reconcile）答 executed 补投真实结果。
  if (result.status === 'success') recordExecutedTool(requestId, result)
  useTaskStore.getState().updateLastAssistantMessage((m) => ({
    ...m,
    tools: m.tools?.map((t) =>
      t.id === requestId
        ? {
            ...t,
            // done 只表示"跑完了"；执行失败要单独标红，否则失败卡也显示绿色「完成」
            status: result.status === 'error' ? ('failed' as const) : ('done' as const),
            result: result.output || result.error || 'Done',
            // file:exec 返回的真实沙箱值优先；失败/非命令工具保留创建时的预置值
            sandboxed: result.sandboxed ?? t.sandboxed
          }
        : t
    )
  }), taskId)
  const task = useTaskStore.getState().getTask(taskId)
  if (task?.sessionId) {
    submitWithApprovals(task.sessionId, requestId, result).catch((err) => {
      console.error('Tool result submission failed:', err)
    })
  }
}

// ===== State =====

interface ChatState {
  /** 正在流式处理的任务集合（按 taskId）。并发对话下必须以任务为单位判断，UI 读当前任务的项。 */
  processingTaskIds: Record<string, true>
  currentEditingPlanMsgIdx: number | null

  sendMessage: (text: string, files?: string[], skillInvocations?: { skill_id: string; skill_name: string }[], targetTaskId?: string) => Promise<void>
  /** ack 失败（无 serverId 的 error 终态）后整轮重发，复用原 clientMessageId（幂等去重不双跑） */
  resendMessage: (msgId: string) => void
  reconnect: () => void
  /** M4：截断该 assistant 回合历史后重新生成（只替换该回合，不新增消息行） */
  regenerateMessage: (msgId: string) => void
  /** M4：从服务端已落历史原地续跑该回合（保留既有内容继续流） */
  continueMessage: (msgId: string) => void
  /** D 副作用账本：GET /effects 重建指定会话各消息的 effectsRuns（按 attempt 分块）。终态/加载/切会话时调用。 */
  refreshEffects: (taskId?: string) => Promise<void>
  /** 确认某个待审批工具；传 toolId 认准该卡，不传则认第一张（批量下发时必须传） */
  confirmTool: (toolId?: string) => void
  skipTool: (toolId?: string) => void
  stopTools: () => void
  selectPlanOption: (msgIdx: number, value: string) => void
  answerPlanQuestion: (msgIdx: number, textAnswer?: string) => void
  confirmPlan: () => void
  editPlan: (msgIdx: number) => void
  rejectPlan: () => void
  openPlanEditor: (msgIdx: number, planText: string) => void
  closePlanEditor: () => void
  savePlanFromEditor: (newText: string) => void
  cancelPlanEdit: () => void
}

// continue 的提交边界截断：assistant 文本只在"调工具前 / 回合自然结束"两处落库，
// 中断（cancelled/error/interrupted）时未落库的半截只存在于前端。这里把消息截回
// "最后一个已落库工具"处：其前文本/思考/工具卡（如 assistant1）原样保留，其后
// 的纯文本/思考（被打断的 assistant2 半截）删掉，续跑新流从这里接上，不与模型重写重叠。
function truncateToCommittedBoundary(m: Message): Partial<Message> {
  const segs = m.segments || []
  const committedToolIds = new Set(
    (m.tools || [])
      .filter((t) => t.status === 'done' || t.status === 'failed' || t.status === 'skipped')
      .map((t) => t.id)
  )
  let lastTool = -1
  for (let i = segs.length - 1; i >= 0; i--) {
    const s = segs[i]
    if (s.type === 'tool_call' && committedToolIds.has(s.toolCallId || '')) {
      lastTool = i
      break
    }
  }
  if (lastTool < 0) {
    // 全程无已落库工具：没有可保留边界 → 整段由模型从库检查点重写（B 固有语义）。
    return { content: '', thinking: '', tools: [], segments: [] }
  }
  const kept = segs.slice(0, lastTool + 1)
  const text = kept.filter((s): s is Extract<MessageSegment, { type: 'text' }> => s.type === 'text')
  const think = kept.filter((s): s is Extract<MessageSegment, { type: 'thinking' }> => s.type === 'thinking')
  const keptIds = new Set(
    kept.filter((s): s is Extract<MessageSegment, { type: 'tool_call' }> => s.type === 'tool_call')
      .map((s) => s.toolCallId || '')
  )
  return {
    content: text.map((s) => s.content).join(''),
    thinking: think.map((s) => s.content).join(''),
    segments: kept,
    tools: (m.tools || []).filter((t) => keptIds.has(t.id))
  }
}

// M4：对同一条 assistant Message 行发起 regenerate/continue。
// 事件流经 createEventHandler 走 updateLastAssistantMessage 追加到"最后一条"，
// 因此这里只接受当前任务最后一条 assistant 消息（动作条按钮也已按此门控）。
async function runReprocess(
  get: () => ChatState,
  set: (partial: Partial<ChatState>) => void,
  msgId: string,
  mode: 'regenerate' | 'continue'
): Promise<void> {
  flushPendingDeltas()
  const taskStore = useTaskStore.getState()
  const task = taskStore.getCurrentTask()
  if (!task?.sessionId) return

  const idx = task.messages.findIndex((m) => m.id === msgId)
  const msg = idx >= 0 ? task.messages[idx] : null
  if (!msg || msg.role !== 'assistant' || !msg.serverId) return
  if (get().processingTaskIds[task.id]) return
  // 只处理最后一条：流式事件（agent.text/tool/complete）都锚定最后一条 assistant。
  if (task.messages[task.messages.length - 1]?.id !== msgId) return

  if (mode === 'regenerate') {
    // 截断重生成：清空该回合旧内容，新流替换显示。
    taskStore.resetMessage(msgId, task.id)
    setTaskProcessing(task.id, true)
  } else {
    // continue：不在此刻动消息。等服务端真正接受并开始重放（收到首个事件）时，
    // 才由下方 needTruncate 包装截回提交边界；若中途被拒/失败则原样保留，可再点。
    setTaskProcessing(task.id, true)
  }

  // 配对的 user 消息本地 id 即原始 client_message_id，供服务端关联审计（可选）。
  const userMsgId = task.messages.slice(0, idx)
    .filter((m) => m.role === 'user')
    .pop()?.id

  const lastSeqRef = { current: task.lastSeq }
  const rawHandleEvent = createEventHandler(lastSeqRef, task.id)
  // continue 的惰性截断：首个事件（reprocess 会先推 message.start）到达即视为服务端
  // 已受理并开始推流，此时才截。busy / needs_confirm / 断网等无事件失败不会动半截。
  let needTruncate = mode === 'continue'
  const handleEvent: (event: ServerEvent) => void = needTruncate
    ? (event) => {
        if (needTruncate) {
          needTruncate = false
          const cur = useTaskStore.getState().getTask(task.id)
          const curMsg = cur?.messages.find((mm) => mm.id === msgId)
          if (curMsg) {
            taskStore.patchMessage(msgId, {
              ...truncateToCommittedBoundary(curMsg),
              isStreaming: true,
              processCollapsed: false,
              serverStatus: undefined
            }, task.id)
          }
        }
        rawHandleEvent(event)
      }
    : rawHandleEvent

  const stopWithNote = (note: string) => {
    flushPendingDeltas()
    const cur = useTaskStore.getState().getTask(task.id)
    const curMsg = cur?.messages.find((m) => m.id === msgId)
    const base = (curMsg?.content || '').trim()
    taskStore.patchMessage(msgId, {
      content: base || `**Error:** ${note}`,
      isStreaming: false,
      processCollapsed: true,
      serverStatus: 'error' as const
    }, task.id)
    taskStore.updateTaskSeq(lastSeqRef.current, task.id)
    setTaskProcessing(task.id, false)
  }

  reprocessMessage({
    sessionId: task.sessionId,
    messageId: msg.serverId,
    mode,
    clientMessageId: userMsgId,
    onEvent: handleEvent,
    onNeedsConfirm: () => {
      stopWithNote('该回合存在尚未确认的工具操作，已取消自动重跑。可先检查该工具是否已生效。')
    },
    onError: (err) => {
      stopWithNote(err.message)
    },
    onDone: () => {
      flushPendingDeltas()
      taskStore.updateTaskSeq(lastSeqRef.current, task.id)
      setTaskProcessing(task.id, false)
    }
  })
}

// ── 回合发送（普通发送与 ack 失败重发共用）─────────────
// userMsg：本轮的 user 消息（其 id 作为 client_message_id 幂等键）。
// assistantMsgId 传入 = 复用并重置已存在的（失败）助手占位气泡；不传 = 新建助手占位气泡。
async function startAssistantRound(
  get: () => ChatState,
  set: (partial: Partial<ChatState>) => void,
  task: Task,
  userMsg: Message,
  assistantMsgId?: string
): Promise<void> {
  const taskStore = useTaskStore.getState()
  if (!task.sessionId) return

  if (assistantMsgId) {
    // 重发：清掉旧错误内容，重置为可承接流式事件的气泡（保留 id/role/serverId）。
    taskStore.resetMessage(assistantMsgId, task.id)
  } else {
    const assistantMsg: Message = {
      id: genMsgId(),
      role: 'assistant',
      content: '',
      thinking: '',
      tools: [],
      processCollapsed: false,
      isStreaming: true,
      segments: [],
      timestamp: Date.now()
    }
    taskStore.addMessage(assistantMsg, task.id)
    assistantMsgId = assistantMsg.id
  }
  const assistantId = assistantMsgId

  setTaskProcessing(task.id, true)

  const lastSeqRef = { current: 0 }
  const handleEvent = createEventHandler(lastSeqRef, task.id)

  const settings = useSettingsStore.getState().settings
  const modeStore = useModeStore.getState()

  const drainQueue = (taskId: string) => {
    const queueStore = useQueueStore.getState()
    if ((queueStore.queues[taskId] ?? []).length > 0) {
      setTimeout(() => {
        const next = queueStore.shiftQueue(taskId)
        if (next) {
          // 排队的消息发回"入队的那个任务"，而非发送时刻用户正在看的任务。
          useChatStore.getState().sendMessage(next, undefined, undefined, taskId)
        }
      }, 300)
    }
  }

  sendChatMessage({
    sessionId: task.sessionId,
    content: userMsg.content,
    mode: modeStore.inputMode,
    sceneMode: modeStore.sceneMode,
    workspace: settings.workspacePath,
    model: settings.model,
    files: userMsg.files,
    skillInvocations: userMsg.skillInvocations && userMsg.skillInvocations.length > 0 ? userMsg.skillInvocations : undefined,
    agentId: task.agentId,
    agentType: task.agentType,
    clientMessageId: userMsg.id, // 幂等键：重发/自动重试复用同一值，服务端据此去重（摄入幂等/阶段A）
    onEvent: handleEvent,
    onDuplicate: (ack) => {
      // 服务端确认该消息已存在（可能仍在跑/已跑完）：不生成新回合，
      // 去掉助手占位气泡，保留 user 消息并回填 serverId。
      taskStore.removeMessage(assistantId, task.id)
      taskStore.patchMessage(userMsg.id, { serverId: ack.message_id }, task.id)
      setTaskProcessing(task.id, false)
    },
    onError: (err) => {
      // ack/HTTP 层失败（尚未收到任何事件）：标记 error 终态，供 UI 显示“重发”。
      flushPendingDeltas()
      taskStore.updateLastAssistantMessage((m) => ({
        ...m,
        content: `**Error:** ${err.message}`,
        isStreaming: false,
        processCollapsed: true,
        serverStatus: 'error' as const
      }), task.id)
      taskStore.updateTaskSeq(lastSeqRef.current, task.id)
      setTaskProcessing(task.id, false)
    },
    onDone: () => drainQueue(task.id)
  })
}

// ack 失败（助手气泡无 serverId、error 终态）后整轮重发：守卫对齐 runReprocess。
async function runResend(get: () => ChatState, set: (partial: Partial<ChatState>) => void, msgId: string): Promise<void> {
  flushPendingDeltas()
  const taskStore = useTaskStore.getState()
  let task = taskStore.getCurrentTask()
  if (!task) return
  if (get().processingTaskIds[task.id]) return

  const idx = task.messages.findIndex((m) => m.id === msgId)
  const assistant = idx >= 0 ? task.messages[idx] : null
  if (!assistant || assistant.role !== 'assistant') return
  // 已受理/尚在跑（有 serverId）→ 应走重新生成/继续；非 error 终态也不属于重发场景。
  if (assistant.serverId || assistant.isStreaming || assistant.serverStatus !== 'error') return
  // 只重发末位失败回合：流式事件锚定最后一条 assistant。
  if (task.messages[task.messages.length - 1]?.id !== msgId) return

  // 配对的 user 消息本地 id 即原 client_message_id。
  const userMsg = task.messages.slice(0, idx).filter((m) => m.role === 'user').pop()
  if (!userMsg) return

  // 立即给可见反馈：清空错误、置为流式占位，避免"点了没反应"。
  taskStore.patchMessage(msgId, {
    content: '',
    thinking: '',
    tools: [],
    segments: [],
    isStreaming: true,
    serverStatus: undefined,
    processCollapsed: false
  }, task.id)
  setTaskProcessing(task.id, true)

  // 会话缺失（此前连不上服务端没建成）→ 现在补建一次。
  if (!task.sessionId) {
    await taskStore.ensureSession(task.id)
    const cur = useTaskStore.getState().getTask(task.id)
    if (cur) task = cur
  }
  // 仍连不上：不假装成功，把可读错误写回气泡并复位处理态（重发按钮随后重新出现）。
  if (!task.sessionId) {
    taskStore.patchMessage(msgId, {
      content: '**Error:** 无法连接服务端，请确认服务已启动后重试。',
      isStreaming: false,
      processCollapsed: true,
      serverStatus: 'error' as const
    }, task.id)
    setTaskProcessing(task.id, false)
    return
  }

  await startAssistantRound(get, set, task, userMsg, msgId)
}

export const useChatStore = create<ChatState>((set, get) => ({
  processingTaskIds: {},
  currentEditingPlanMsgIdx: null,

  sendMessage: async (text: string, files?: string[], skillInvocations?: { skill_id: string; skill_name: string }[], targetTaskId?: string) => {
    if (!text) return

    let taskStore = useTaskStore.getState()
    // 目标 = 显式指定（队列排空要发回原任务）或当前选中任务。
    let task = targetTaskId ? taskStore.getTask(targetTaskId) : taskStore.getCurrentTask()
    const targetId = task?.id ?? targetTaskId

    // 目标任务已在处理 → 排入该任务的队列（同一会话服务端串行，不能并发两条流）。
    if (targetId && get().processingTaskIds[targetId]) {
      useQueueStore.getState().addToQueue(targetId, text)
      return
    }

    let createdJustNow = false
    // Auto-create task + session if none exists（仅当前任务路径；显式目标必已存在）
    if (!task && !targetTaskId) {
      await taskStore.create()
      // P2：新会话清空代理会话级 host 缓存（跨会话重新问）
      ipcClient.proxy.resetSession().catch(() => {})
      taskStore = useTaskStore.getState()
      task = taskStore.getCurrentTask()
      createdJustNow = true
    }
    if (!task) return

    // 已有任务却缺会话（此前连不上服务端导致建会话失败）：现在补建一次。
    // createdJustNow 时 create() 内部已 try 过一次，不再重复。
    if (!task.sessionId && !createdJustNow) {
      await taskStore.ensureSession(task.id)
      task = useTaskStore.getState().getTask(task.id)
      if (!task) return
    }

    // 先乐观展示用户问题，再谈发送——即使连不上服务端也不让用户输入凭空消失。
    const userMsg: Message = {
      id: genMsgId(),
      role: 'user',
      content: text,
      files,
      skillInvocations: skillInvocations && skillInvocations.length > 0 ? skillInvocations : undefined,
      timestamp: Date.now()
    }
    taskStore.addMessage(userMsg, task.id)

    // 仍无会话（服务端连不上）：保留问题气泡可见，并给一个可“重发”的错误回合。
    if (!task.sessionId) {
      taskStore.addMessage({
        id: genMsgId(),
        role: 'assistant',
        content: '**Error:** 无法连接服务端，会话尚未建立。可点下方"重发"重试（连上后会自动建会话再发送）。',
        isStreaming: false,
        processCollapsed: true,
        serverStatus: 'error' as const,
        segments: [],
        timestamp: Date.now()
      }, task.id)
      return
    }

    void startAssistantRound(get, set, task, userMsg)
  },

  resendMessage: (msgId) => {
    void runResend(get, set, msgId)
  },

  /** M5：断点续收。启动恢复时仅对"引擎存活且 PROCESSING 的同一在途回合"调用
   *  GET /stream（单消费者纪律，绝不与 POST 并发）。守护条件：末条为 assistant
   *  且尚无终态（serverStatus undefined）→ 有可承接流式事件的气泡且确在途。 */
  reconnect: () => {
    const taskStore = useTaskStore.getState()
    const task = taskStore.getCurrentTask()
    if (!task?.sessionId) return
    const last = task.messages[task.messages.length - 1]
    if (!last || last.role !== 'assistant') return
    if (last.serverStatus) return
    if (get().processingTaskIds[task.id]) return

    setTaskProcessing(task.id, true)

    // C-2：续收前先把断线期攒下的工具结果 outbox 补投（服务端 duplicate 兜底）
    void flushToolOutbox()

    const lastSeqRef = { current: task.lastSeq }
    const handleEvent = createEventHandler(lastSeqRef, task.id)

    // 服务端 drain 返回 seq > since_seq；传 task.lastSeq 即取 seq >= lastSeq+1 的事件。
    reconnectStream(
      task.sessionId,
      task.lastSeq,
      handleEvent,
      (err) => {
        // 续收失败：不覆盖半截正文（可能还有内容），按"中断"标记放 继续/重新生成 按钮。
        flushPendingDeltas()
        const cur = useTaskStore.getState().getTask(task.id)
        const curLast = cur?.messages[cur.messages.length - 1]
        const haveContent = !!(curLast?.content && curLast.content.trim())
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          content: haveContent ? m.content : `**Reconnect Error:** ${err.message}`,
          isStreaming: false,
          processCollapsed: true,
          serverStatus: haveContent ? ('interrupted' as const) : m.serverStatus
        }), task.id)
        taskStore.updateTaskSeq(lastSeqRef.current, task.id)
        setTaskProcessing(task.id, false)
      },
      () => {
        taskStore.updateTaskSeq(lastSeqRef.current, task.id)
      }
    )
  },

  regenerateMessage: (msgId) => {
    void runReprocess(get, set, msgId, 'regenerate')
  },

  continueMessage: (msgId) => {
    void runReprocess(get, set, msgId, 'continue')
  },

  refreshEffects: async (taskId?: string) => {
    const task = taskId
      ? useTaskStore.getState().getTask(taskId)
      : useTaskStore.getState().getCurrentTask()
    if (!task?.sessionId) return
    const key = task.sessionId
    if (effectsFetching.has(key)) return
    effectsFetching.add(key)
    try {
      const { effects } = await fetchSessionEffects(key)
      const byMsg = new Map<string, SideEffectItem[]>()
      for (const e of effects) {
        const mid = e.message_id
        if (!mid) continue
        let arr = byMsg.get(mid)
        if (!arr) { arr = []; byMsg.set(mid, arr) }
        arr.push(e)
      }
      // 服务端按 created_at 升序返回；对每条消息按 attempt 分块（NULL 旧行并作一块），
      // Map 依首见顺序保序 = 运行先后。regenerate 递增开新块、continue 并入当前块。
      for (const m of task.messages) {
        if (m.role !== 'assistant' || !m.serverId) continue
        const rows = byMsg.get(m.serverId)
        if (!rows || rows.length === 0) continue
        const buckets = new Map<number, SideEffectItem[]>()
        for (const e of rows) {
          const k = e.attempt ?? 0
          let b = buckets.get(k)
          if (!b) { b = []; buckets.set(k, b) }
          b.push(e)
        }
        useTaskStore.getState().patchMessage(m.id, { effectsRuns: [...buckets.values()] }, task.id)
      }
    } catch {
      // 网络失败静默：下个终态 / 切会话 / 加载再补
    } finally {
      effectsFetching.delete(key)
    }
  },

  confirmTool: (toolId?: string) => {
    const taskStore = useTaskStore.getState()
    const task = taskStore.getCurrentTask()
    if (!task) return

    const msg = task.messages[task.messages.length - 1]
    // 一轮可同时下发多张待审批卡：认准被点的这张，否则永远作用在第一个上
    const pendingTool = toolId
      ? msg?.tools?.find(t => t.id === toolId && t.status === 'pending')
      : msg?.tools?.find(t => t.status === 'pending')
    if (!pendingTool) return

    // Resolve which API to use: client tool request uses request_id, build step uses tool_call_id
    const isClientTool = pendingTool.input !== undefined

    taskStore.updateLastAssistantMessage((m) => ({
      ...m,
      tools: m.tools?.map((t) =>
        t.id === pendingTool.id ? { ...t, status: 'running' as const } : t
      )
    }))

    if (isClientTool) {
      runClientTool(task.id, pendingTool.id, pendingTool.name, pendingTool.input || {}).catch((err) => {
        console.error('Client tool execution failed:', err)
      })
    } else {
      buildApi.confirm(task.sessionId, pendingTool.name).catch((err) => {
        console.error('Build confirm failed:', err)
      })
    }
  },

  skipTool: (toolId?: string) => {
    const taskStore = useTaskStore.getState()
    const task = taskStore.getCurrentTask()
    if (!task) return

    const msg = task.messages[task.messages.length - 1]
    const pendingTool = toolId
      ? msg?.tools?.find(t => t.id === toolId && t.status === 'pending')
      : msg?.tools?.find(t => t.status === 'pending')
    if (!pendingTool) return
    const isClientTool = pendingTool.input !== undefined

    taskStore.updateLastAssistantMessage((m) => ({
      ...m,
      tools: m.tools?.map((t) =>
        t.id === pendingTool.id ? { ...t, status: 'skipped' as const } : t
      )
    }))

    if (isClientTool) {
      submitToolResult(task.sessionId, pendingTool.id, {
        status: 'error',
        error: 'User skipped the tool',
        duration_ms: 0,
        skipped: true
      }).catch((err) => {
        console.error('Skip tool result submission failed:', err)
      })
    } else {
      buildApi.skip(task.sessionId).catch((err) => {
        console.error('Build skip failed:', err)
      })
    }
  },

  stopTools: () => {
    const taskStore = useTaskStore.getState()
    const task = taskStore.getCurrentTask()
    if (!task) return

    const msg = task.messages[task.messages.length - 1]
    const pendingTools = msg?.tools?.filter(t => t.status === 'pending') || []

    taskStore.updateLastAssistantMessage((m) => ({
      ...m,
      tools: m.tools?.map((t) => t.status === 'pending' ? { ...t, status: 'skipped' as const } : t)
    }))

    // Submit error results for any pending client tools before cancelling
    for (const t of pendingTools) {
      if (t.input !== undefined) {
        submitToolResult(task.sessionId, t.id, {
          status: 'error',
          error: 'User cancelled',
          duration_ms: 0
        }).catch((err: any) => {
          console.error('Stop tool result submission failed:', err)
        })
      }
    }

    cancelSession(task.sessionId).catch((err) => {
      console.error('Build cancel failed:', err)
    })
  },

  selectPlanOption: (_msgIdx: number, _value: string) => {},

  answerPlanQuestion: (_msgIdx: number, textAnswer?: string) => {
    const taskStore = useTaskStore.getState()
    const task = taskStore.getCurrentTask()
    if (!task) return

    const answer = textAnswer || '已选择'
    taskStore.updateLastAssistantMessage((m) => {
      const segs = m.segments || []
      const updated = segs.map((s, i) => {
        const isLastUnanswered =
          s.type === 'question' &&
          s.answer === null &&
          !segs.slice(i + 1).some(q => q.type === 'question' && q.answer === null)
        return isLastUnanswered ? { ...s, answer } : s
      })
      return { ...m, segments: updated }
    })

    planApi.answer(task.sessionId, answer).catch((err) => {
      console.error('Plan answer failed:', err)
    })
  },

  confirmPlan: () => {
    const taskStore = useTaskStore.getState()
    const task = taskStore.getCurrentTask()
    if (!task) return

    taskStore.updateLastAssistantMessage((m) => ({
      ...m,
      planStatus: 'confirmed',
      segments: [
        ...(m.segments || []),
        {
          id: genPlanEventId(),
          timestamp: Date.now(),
          type: 'confirmed' as const
        }
      ]
    }))

    planApi.confirm(task.sessionId).catch((err) => {
      console.error('Plan confirm failed:', err)
      taskStore.updateLastAssistantMessage((m) => {
        const events = m.segments || []
        const reverted = events.filter((e, i) =>
          !(i === events.length - 1 && e.type === 'confirmed')
        )
        return { ...m, planStatus: 'pending', segments: reverted }
      })
    })
  },

  editPlan: (msgIdx: number) => {
    const taskStore = useTaskStore.getState()
    const task = taskStore.getCurrentTask()
    if (!task) return

    taskStore.updateLastAssistantMessage((m) => ({ ...m, planEditing: true }))
    set({ currentEditingPlanMsgIdx: msgIdx })
  },

  rejectPlan: () => {
    const taskStore = useTaskStore.getState()
    const task = taskStore.getCurrentTask()
    if (!task) return

    taskStore.updateLastAssistantMessage((m) => ({
      ...m,
      planStatus: 'rejected',
      processCollapsed: true,
      segments: [
        ...(m.segments || []),
        {
          id: genPlanEventId(),
          timestamp: Date.now(),
          type: 'rejected' as const
        }
      ]
    }))

    cancelSession(task.sessionId).catch((err) => {
      console.error('Plan cancel failed:', err)
    })
  },

  openPlanEditor: (msgIdx: number, _planText: string) => {
    set({ currentEditingPlanMsgIdx: msgIdx })
  },

  closePlanEditor: () => {
    const taskStore = useTaskStore.getState()
    const idx = get().currentEditingPlanMsgIdx
    if (idx != null) {
      taskStore.updateLastAssistantMessage((m) => ({ ...m, planEditing: false }))
    }
    set({ currentEditingPlanMsgIdx: null })
  },

  savePlanFromEditor: (newText: string) => {
    const idx = get().currentEditingPlanMsgIdx
    const taskStore = useTaskStore.getState()
    const task = taskStore.getCurrentTask()

    if (idx != null) {
      taskStore.updateLastAssistantMessage((m) => ({
        ...m,
        planEditing: false,
        segments: [
          ...(m.segments || []),
          {
            id: genPlanEventId(),
            timestamp: Date.now(),
            type: 'edited' as const
          }
        ]
      }))
    }
    set({ currentEditingPlanMsgIdx: null })

    if (task) {
      planApi.edit(task.sessionId, newText).catch((err) => {
        console.error('Plan edit API failed:', err)
      })
    }
  },

  cancelPlanEdit: () => {
    get().closePlanEditor()
  }
}))
