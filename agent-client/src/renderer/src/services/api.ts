import type { ServerEvent, AppMode, SceneMode, McpHubServer, McpInstalledServer, CustomMcpServer, CreateCustomMcpRequest, HubSkill, InstalledSkill, CustomSkillDef, CreateCustomSkillRequest, McpInstallResponse, McpToolDef, SkillInstallResult, L1MemoryItem, L2SceneItem, L3PersonaItem, RuleItem, Expert, Team, NetworkApproval, PolicyPacket, McpCallResult, SideEffectItem } from '../types'
import { useSettingsStore } from '../stores/settingsStore'
import { useAuthStore } from '../stores/authStore'
import { authedFetch } from './authFetch'
import { parseNDJSONStream } from './ndjson'
import { ipcClient } from './ipcClient'

const DEFAULT_BASE_URL = '/api'

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onloadend = () => {
      const result = reader.result as string
      // strip data:application/zip;base64, prefix
      const comma = result.indexOf(',')
      resolve(comma >= 0 ? result.slice(comma + 1) : result)
    }
    reader.onerror = reject
    reader.readAsDataURL(blob)
  })
}

async function extractSkillZip(_skillId: string, skillName: string, blob: Blob): Promise<string> {
  const base64 = await blobToBase64(blob)
  // Main process resolves ~/.iwork/skills/{skillName}/, creates dirs, decodes + extracts
  return ipcClient.file.extractSkill(base64, skillName)
}

export interface ToolResult {
  status: 'success' | 'error'
  output?: string
  error?: string
  exit_code?: number
  duration_ms: number
  /** 本次执行是否真正进了 OS 写墙沙箱：bash 由 file:exec 实测，stdio MCP 由 mcp:call-tool 实测；
   *  文件类工具恒 false（主进程直写宿主），http/sse 网络 MCP 恒 false */
  sandboxed?: boolean
  /** 用户跳过时置 true，服务端据此触发 skip 反馈（append_skip_feedback） */
  skipped?: boolean
  /** 本工具执行期经本地代理判定的域名审批记录，服务端据此发 NETWORK_APPROVAL 审计 */
  network_approvals?: NetworkApproval[]
  /** P3：只留字段暂不填充——升级重试需服务端宽松策略重 push + query_loop 重试，均未实现（§8.7.5） */
  retry_reason?: string
}

export function isClientTool(toolName: string): boolean {
  return CLIENT_TOOLS.some(t => t.name === toolName)
}

// 主进程直接执行、永不进 OS 写墙沙箱的客户端工具（读/写/搜索/skill → 裸机）
const BARE_CLIENT_TOOLS = new Set(['read_file', 'write_file', 'edit_file', 'glob', 'grep', 'skill'])

/**
 * 工具卡片创建时的沙箱角标预置（与工具实际运行环境对应）：
 * - bash：策略要求沙箱则预置沙箱（file:exec 完成时用实测值覆盖）；
 * - 文件类工具：主进程直写宿主、恒为裸机 → 带策略时预置 false；无策略不标；
 * - MCP 等其它工具：不预置（stdio 受限与否、网络 MCP 恒裸机等主进程回传）。
 */
export function presetClientToolSandboxed(
  toolName: string,
  policy: PolicyPacket | null | undefined
): boolean | undefined {
  if (toolName === 'bash') return policy ? policy.sandbox?.required === true : undefined
  if (BARE_CLIENT_TOOLS.has(toolName)) return policy ? false : undefined
  return undefined
}

/**
 * Try to execute a tool as an MCP tool call.
 * Tool names from the backend follow the pattern {server_id}_{tool_name}.
 * Returns the result if matched, or null if not an MCP tool.
 */
async function tryExecuteMcpTool(
  toolName: string,
  input: Record<string, unknown>,
  workspacePath: string
): Promise<McpCallResult | null> {
  // Lazy import to avoid circular dependency with configStore
  const { useConfigStore } = await import('../stores/configStore')
  const { mcpConnectionStatuses } = useConfigStore.getState()

  console.log('Attempting to execute MCP tool:', toolName, 'with input:', input)
  console.log('Attempting to mcpConnectionStatuses:', mcpConnectionStatuses)
  for (const serverId of Object.keys(mcpConnectionStatuses)) {
    const prefix = serverId + '_'
    if (toolName.startsWith(prefix)) {
      // 接入级信任收口：只有已连接的 server 才允许执行（连接即“用户接入该 server”）
      const status = mcpConnectionStatuses[serverId]?.status
      if (status !== 'connected') {
        console.warn(`[MCP] 拒绝执行 ${toolName}：server ${serverId} 未连接（status=${status}）`)
        throw new Error(`MCP server ${serverId} 未连接（status=${status}），拒绝执行`)
      }
      const actualToolName = toolName.slice(prefix.length)
      console.log('Attempting to actualToolName:', actualToolName)
      return ipcClient.mcp.callTool(serverId, actualToolName, input, workspacePath)
    }
  }

  return null
}

export async function executeClientTool(
  toolName: string,
  input: Record<string, unknown>,
  workspacePath: string
): Promise<ToolResult> {
  const start = Date.now()
  // bash 在 file:exec 判定沙箱/裸机，完成后回填（仅命令类工具填写）
  let execSandboxed: boolean | undefined

  try {
    let output: string | undefined

    switch (toolName) {
      case 'read_file': {
        const filePath = input.path as string
        if (!filePath) throw new Error('Missing required parameter: path')
        output = await ipcClient.file.read(filePath)
        break
      }
      case 'write_file': {
        const filePath = input.path as string
        const content = input.content as string
        if (!filePath) throw new Error('Missing required parameter: path')
        if (content === undefined) throw new Error('Missing required parameter: content')
        await ipcClient.file.write(filePath, content)
        output = 'File written successfully'
        break
      }
      case 'edit_file': {
        const filePath = input.path as string
        const oldString = input.old_string as string
        const newString = input.new_string as string
        if (!filePath) throw new Error('Missing required parameter: path')
        if (oldString === undefined) throw new Error('Missing required parameter: old_string')
        if (newString === undefined) throw new Error('Missing required parameter: new_string')
        await ipcClient.file.edit(filePath, oldString, newString)
        output = 'File edited successfully'
        break
      }
      case 'glob': {
        const pattern = input.pattern as string
        if (!pattern) throw new Error('Missing required parameter: pattern')
        const files = await ipcClient.file.glob(pattern)
        output = files.join('\n') || '(no matches)'
        break
      }
      case 'grep': {
        const pattern = input.pattern as string
        if (!pattern) throw new Error('Missing required parameter: pattern')
        const dirPath = (input.path as string) || '.'
        const lines = await ipcClient.file.grep(pattern, dirPath)
        output = lines.join('\n') || '(no matches)'
        break
      }
      case 'bash': {
        const command = input.command as string
        if (!command) throw new Error('Missing required parameter: command')
        const timeoutMs = (input.timeout_ms as number) || 300000
        const result = await ipcClient.file.exec(command, timeoutMs)
        execSandboxed = result.sandboxed
        if (result.exit_code !== 0 && result.stderr) {
          return {
            status: 'error',
            error: result.stderr,
            output: result.stdout,
            exit_code: result.exit_code,
            duration_ms: Date.now() - start,
            sandboxed: result.sandboxed
          }
        }
        output = result.stdout || result.stderr || '(no output)'
        break
      }
      case 'skill': {
        const skillName = input.name as string
        if (!skillName) throw new Error('Missing required parameter: name')
        const { useConfigStore } = await import('../stores/configStore')
        let { installedSkills } = useConfigStore.getState()
        let matched = installedSkills.find(s => s.skill_name === skillName)
        // 客户端缓存只在 App 启动/手动操作时刷新，服务端安装状态可能已变化 → 实时拉取一次再查
        if (!matched) {
          await useConfigStore.getState().loadInstalledSkills()
          installedSkills = useConfigStore.getState().installedSkills
          matched = installedSkills.find(s => s.skill_name === skillName)
        }
        if (!matched) {
          const available = installedSkills.map(s => s.skill_name).join(', ') || '(无已安装 skill)'
          throw new Error(
            `Skill not found: ${skillName}。可用 skill：${available}。如需使用 ${skillName}，请先在 Skill 面板安装。`
          )
        }
        const skillMd = await ipcClient.file.readSkillMd(matched.skill_id)
        // Directory prefix is informational — never let a getSkillDir failure
        // (e.g. stale main process without the handler) block SKILL.md content.
        let skillDir = ''
        try {
          skillDir = await ipcClient.file.getSkillDir(matched.skill_id)
        } catch (err) {
          console.warn('[skill] getSkillDir failed:', err)
        }
        output = skillDir
          ? `【Skill 目录（绝对路径）: ${skillDir}\n说明：该目录下 SKILL.md 中的所有相对路径（scripts/xxx.py 等）均相对于此目录。执行脚本请先 cd到该目录，或用该绝对路径拼接出完整命令。】\n\n${skillMd}`
          : skillMd
        break
      }
      default: {
        // Route MCP tools: tool name format is {server_id}_{tool_name}
        const mcp = await tryExecuteMcpTool(toolName, input, workspacePath)
        if (mcp) {
          // 主进程回传的本次实际沙箱状态：受限 stdio → true；stdio 直连/http/sse 网络 MCP → false
          execSandboxed = mcp.sandboxed
          const raw = mcp.result
          console.log(`执行工具返回得结果：${JSON.stringify(raw)} ---------------------------------------------------------`)
          output = typeof raw === 'string' ? raw : JSON.stringify(raw)
        } else {
          throw new Error(`Unknown client tool: ${toolName}`)
        }
      }
    }

    return {
      status: 'success',
      output,
      duration_ms: Date.now() - start,
      ...(execSandboxed !== undefined ? { sandboxed: execSandboxed } : {})
    }
  } catch (err: any) {
    return {
      status: 'error',
      error: err?.message || String(err),
      duration_ms: Date.now() - start,
      ...(execSandboxed !== undefined ? { sandboxed: execSandboxed } : {})
    }
  }
}

// ===== Session management =====

export interface CreateSessionRequest {
  id: string
  scene_mode: string
  workspace: string
  model: string
  mode: string
  /** 客户端执行环境：powershell | zsh | bash（服务端据此注入 Shell: ... 系统提示词） */
  shell_env?: string
  client_tools: {
    name: string
    description: string
    input_schema: {
      type: 'object'
      properties: Record<string, unknown>
      required?: string[]
    }
  }[]
  mcp_servers?: {
    server_id: string
    server_name: string
    enabled_tools?: string[]
  }[]
  agent_id?: string
  agent_type?: 'expert' | 'team'
}

export interface CreateSessionResponse {
  id: string
  title: string
  mode: string
  scene_mode: string
  model: string
  workspace: string
  client_tools_count: number
  mcp_servers_count: number
  created_at: string
}

export const CLIENT_TOOLS = [
  {
    name: 'bash',
    description: '执行 shell 命令',
    input_schema: {
      type: 'object' as const,
      properties: {
        command: { type: 'string', description: 'The shell command to execute' },
        timeout_ms: { type: 'number', description: 'Timeout in milliseconds, default 300000 (max 300000)' }
      },
      required: ['command']
    }
  },
  {
    name: 'read_file',
    description: 'Read the contents of a file at the given path',
    input_schema: {
      type: 'object' as const,
      properties: { path: { type: 'string', description: 'Absolute path to the file' } },
      required: ['path']
    }
  },
  {
    name: 'write_file',
    description: 'Write content to a file, creating it if it does not exist',
    input_schema: {
      type: 'object' as const,
      properties: {
        path: { type: 'string', description: 'Absolute path to the file' },
        content: { type: 'string', description: 'Content to write' }
      },
      required: ['path', 'content']
    }
  },
  {
    name: 'edit_file',
    description: 'Perform exact string replacements in a file',
    input_schema: {
      type: 'object' as const,
      properties: {
        path: { type: 'string', description: 'Absolute path to the file' },
        old_string: { type: 'string', description: 'Text to replace' },
        new_string: { type: 'string', description: 'Replacement text' }
      },
      required: ['path', 'old_string', 'new_string']
    }
  },
  {
    name: 'glob',
    description: 'Find files matching a glob pattern',
    input_schema: {
      type: 'object' as const,
      properties: {
        pattern: { type: 'string', description: 'Glob pattern to match, e.g. **/*.ts' },
        path: { type: 'string', description: 'Directory to search in' }
      },
      required: ['pattern']
    }
  },
  {
    name: 'grep',
    description: 'Search file contents using regex patterns',
    input_schema: {
      type: 'object' as const,
      properties: {
        pattern: { type: 'string', description: 'Regular expression to search for' },
        path: { type: 'string', description: 'File or directory to search in' },
        glob: { type: 'string', description: 'Glob pattern to filter files' }
      },
      required: ['pattern']
    }
  }
]

export async function createSession(req: CreateSessionRequest): Promise<CreateSessionResponse> {
  const settings = useSettingsStore.getState().settings
  const baseUrl = settings.apiBaseUrl || DEFAULT_BASE_URL
  const url = `${baseUrl}/sessions`

  console.log('创建会话')

  // 客户端执行环境（powershell/zsh/bash）随建会话上报，服务端据此注入 Shell: ... 提示词
  let shellEnv = req.shell_env ?? ''
  if (!shellEnv) {
    try {
      shellEnv = await ipcClient.file.shellEnv()
    } catch (err) {
      // 打印具体失败原因（如主进程 shell:env handler 未注册），避免静默吞掉
      console.error('[createSession] shell:env 获取失败:', err)
    }
  }
  // 调试日志：打印实际发送到服务端的参数，确认 shell_env 是否成功上报
  console.log('[createSession] shell_env =', JSON.stringify(shellEnv))
  console.log('[createSession] send params:', JSON.stringify({ ...req, shell_env: shellEnv }))

  const response = await authedFetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeaders()
    },
    body: JSON.stringify({ ...req, shell_env: shellEnv })
  })

  if (!response.ok) {
    const errBody = await response.text().catch(() => '')
    let msg = `Create session error: ${response.status} ${response.statusText}`
    try {
      const parsed = JSON.parse(errBody)
      if (parsed.message) msg = parsed.message
      if (parsed.error) msg = parsed.error
    } catch { }
    throw new Error(msg)
  }

  const data: CreateSessionResponse = await response.json()
  return data
}

// ===== Main chat channel =====

export interface DuplicateAck {
  duplicate: boolean
  message_id: string
  client_message_id?: string
  status: string
}

export interface ChatStreamOptions {
  sessionId: string
  content: string
  mode: AppMode
  sceneMode: SceneMode
  workspace: string
  model: string
  files?: string[]
  skillInvocations?: { skill_id: string; skill_name: string }[]
  mcpServers?: { server_id: string; server_name: string; enabled_tools?: string[] }[]
  agentId?: string
  agentType?: 'expert' | 'team'
  clientMessageId?: string
  onEvent: (event: ServerEvent) => void
  onError: (err: Error) => void
  onDone: () => void
  onDuplicate?: (ack: DuplicateAck) => void
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

/** 仅在还没收到任何事件前发生的瞬时故障才值得带同一幂等键重发 */
function isRetryableError(err: Error): boolean {
  if (err instanceof TypeError) return true // 网络层失败（fetch）
  return /^API error: 50\d/.test(err.message) // 5xx 网关/服务错误
}

export async function sendChatMessage(opts: ChatStreamOptions): Promise<void> {
  const settings = useSettingsStore.getState().settings
  const baseUrl = settings.apiBaseUrl || DEFAULT_BASE_URL
  const url = `${baseUrl}/sessions/${opts.sessionId}/messages`

  // 有幂等键才自动重试：重发复用同一 client_message_id，服务端据此去重、不会双跑。
  const maxAttempts = opts.clientMessageId ? 3 : 1

  const headers = {
    'Content-Type': 'application/json',
    "Accept": "application/json, text/event-stream",
    ...getAuthHeaders()
  }
  const body = JSON.stringify({
    content: opts.content,
    scene_mode: opts.sceneMode,
    workspace: opts.workspace,
    model: opts.model,
    mode: opts.mode,
    files: opts.files,
    skill_invocations: opts.skillInvocations,
    mcp_servers: opts.mcpServers,
    agent_id: opts.agentId || null,
    agent_type: opts.agentType || null,
    client_message_id: opts.clientMessageId ?? null
  })

  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    let receivedAny = false
    try {
      const response = await authedFetch(url, { method: 'POST', headers, body })

      if (!response.ok) {
        const errBody = await response.text().catch(() => '')
        let msg = `API error: ${response.status} ${response.statusText}`
        try {
          const parsed = JSON.parse(errBody)
          if (parsed.message) msg = parsed.message
          if (parsed.error) msg = parsed.error
        } catch { }
        throw new Error(msg)
      }

      // 摄入幂等 duplicate ack：服务端命中 (session, client_message_id) 后返回 JSON 而非 NDJSON 流
      const contentType = response.headers.get('content-type') || ''
      if (contentType.includes('application/json')) {
        const ack = await response.json().catch(() => null) as DuplicateAck | null
        if (ack && ack.duplicate) opts.onDuplicate?.(ack)
        opts.onDone()
        return
      }

      const reader = response.body?.getReader()
      if (!reader) throw new Error('Response body is not readable')

      for await (const event of parseNDJSONStream(reader)) {
        receivedAny = true
        opts.onEvent(event)
        if (event.type === 'message.complete' || (event.type === 'message.error' && event.fatal)) {
          opts.onDone()
          return
        }
      }

      opts.onDone()
      return
    } catch (err) {
      const e = err instanceof Error ? err : new Error(String(err))
      if (attempt + 1 < maxAttempts && !receivedAny && isRetryableError(e)) {
        await sleep(500 * (attempt + 1)) // 1 次退避后再试一次，幂等键保证不双跑
        continue
      }
      opts.onError(e)
      return
    }
  }
}

// ===== Reconnection =====

export async function reconnectStream(
  sessionId: string,
  sinceSeq: number,
  onEvent: (event: ServerEvent) => void,
  onError: (err: Error) => void,
  onDone: () => void
): Promise<void> {
  const settings = useSettingsStore.getState().settings
  const baseUrl = settings.apiBaseUrl || DEFAULT_BASE_URL
  const url = `${baseUrl}/sessions/${sessionId}/stream?since_seq=${sinceSeq}`

  try {
    const response = await authedFetch(url, {
      headers: {
        ...getAuthHeaders()
      }
    })

    if (!response.ok) throw new Error(`Reconnect error: ${response.status}`)

    const reader = response.body?.getReader()
    if (!reader) throw new Error('Response body is not readable')

    for await (const event of parseNDJSONStream(reader)) {
      onEvent(event)
      if (event.type === 'message.complete' || (event.type === 'message.error' && event.fatal)) {
        onDone()
        return
      }
    }
    onDone()
  } catch (err) {
    onError(err instanceof Error ? err : new Error(String(err)))
  }
}

// ===== M5 · 恢复探测 / 历史回读 =====

/** GET /sessions/{id}/state — 引擎存活 + 在跑消息 + 疑似中断候选（B 恢复骨架②探针） */
export interface SessionStateInfo {
  session_id: string
  engine_alive: boolean
  /** 引擎 state：IDLE | PROCESSING | WAITING_SYNC | WAITING_CHILDREN */
  state: string
  current_message_id: string | null
  /** 在途子任务（§9.11.8）。state=WAITING_CHILDREN 时父正等它们回信 */
  in_flight?: Array<{
    task_id: string
    agent_path: string
    /** 成员扁平 id —— 与委派卡片的 `to` 同一个口径 */
    agent_id: string
    dispatched_at: number
  }>
  /** DB 里 status=processing 的消息 id（引擎死后残留 = 持久中断信号） */
  interrupted_candidates: string[]
}

export async function fetchSessionState(sessionId: string): Promise<SessionStateInfo> {
  const settings = useSettingsStore.getState().settings
  const baseUrl = settings.apiBaseUrl || DEFAULT_BASE_URL
  const response = await authedFetch(`${baseUrl}/sessions/${sessionId}/state`, {
    headers: { ...getAuthHeaders() }
  })
  if (!response.ok) throw new Error(`State error: ${response.status}`)
  return await response.json()
}

/** conversation_history 单行（归属到 message，M2 起落 message_id/turn；老行为 NULL） */
export interface HistoryRow {
  sequence: number | null
  role: string | null
  content: string | null
  reasoning_content: string | null
  tool_call_id: string | null
  turn: number | null
}

/** GET /sessions/{id}/messages — 服务端消息（含状态）+ 每条消息的可读 turns 行 */
export interface SessionMessageMeta {
  id: string
  client_message_id: string | null
  content: string | null
  mode: string | null
  status: string
  turn_count: number | null
  tokens_in: number | null
  tokens_out: number | null
  tool_calls_count: number | null
  error_message: string | null
  created_at: string | null
  started_at: string | null
  completed_at: string | null
  /** 'user' | 'result' …；'result' 是子 agent 回传的结果信封（content 为 JSON），非对话 */
  msg_type?: string | null
  /** 信封对回它那次派发的 task_id（委派卡片重建用） */
  cid?: string | null
  /** result 信封的发件人，扁平成员 id（服务端已剥掉 /root/ 前缀） */
  from_agent_id?: string | null
  rows: HistoryRow[]
}

export interface SessionMessagesResponse {
  session_id: string
  messages: SessionMessageMeta[]
}

export async function fetchSessionMessages(sessionId: string): Promise<SessionMessagesResponse> {
  const settings = useSettingsStore.getState().settings
  const baseUrl = settings.apiBaseUrl || DEFAULT_BASE_URL
  const response = await authedFetch(`${baseUrl}/sessions/${sessionId}/messages`, {
    headers: { ...getAuthHeaders() }
  })
  if (!response.ok) throw new Error(`History error: ${response.status}`)
  return await response.json()
}

/** GET /sessions/{id}/agents — 该会话树下已派发过的成员子会话（子列历史回读入口） */
export interface SessionAgentMeta {
  session_id: string
  /** 扁平成员 id（= 面板里那一列的 id） */
  agent_id: string
}

export interface SessionAgentsResponse {
  session_id: string
  agents: SessionAgentMeta[]
}

export async function fetchSessionAgents(sessionId: string): Promise<SessionAgentsResponse> {
  const settings = useSettingsStore.getState().settings
  const baseUrl = settings.apiBaseUrl || DEFAULT_BASE_URL
  const response = await authedFetch(`${baseUrl}/sessions/${sessionId}/agents`, {
    headers: { ...getAuthHeaders() }
  })
  if (!response.ok) throw new Error(`Agents error: ${response.status}`)
  return await response.json()
}

export interface SessionEffectsResponse {
  session_id: string
  effects: SideEffectItem[]
}

/** D 工具动作账本：拉取全会话受账工具调用（不限 state / side_effect，只读也返回，四态都带）。
 *  前端据此按 message_id 分组、按 attempt 分块重建各气泡的 effectsRuns，展示侧再按
 *  state 与 side_effect 分辨读/写与状态。 */
export async function fetchSessionEffects(sessionId: string): Promise<SessionEffectsResponse> {
  const settings = useSettingsStore.getState().settings
  const baseUrl = settings.apiBaseUrl || DEFAULT_BASE_URL
  const response = await authedFetch(`${baseUrl}/sessions/${sessionId}/effects`, {
    headers: { ...getAuthHeaders() }
  })
  if (!response.ok) throw new Error(`Effects error: ${response.status}`)
  return await response.json()
}

// ===== M4 · 显式重跑（regenerate 截断重生成 / continue 原地续跑）=====

export interface ReprocessNeedsConfirm {
  status: 'needs_confirm'
  message_id: string
  mode: 'regenerate' | 'continue'
  ambiguous_tools: { invocation_id: string; tool_name: string; idempotency: string }[]
}

export interface ReprocessMessageOptions {
  sessionId: string
  /** 服务端 message_id（Message.serverId） */
  messageId: string
  mode: 'regenerate' | 'continue'
  /** 客户端本地该条 user 消息 id，服务端据此关联审计（可选） */
  clientMessageId?: string
  onEvent: (event: ServerEvent) => void
  onError: (err: Error) => void
  onDone: () => void
  onNeedsConfirm?: (payload: ReprocessNeedsConfirm) => void
}

/**
 * 对同一条 Message 行发起重跑，输出复用 NDJSON 流（单消费者纪律）。
 * 服务端可能以 JSON 拒绝（needs_confirm：该回合尾巴有未收口的写类工具账，C-1），
 * 也可能以 404/409（not_found / busy）报错，均在此收口。
 */
export async function reprocessMessage(opts: ReprocessMessageOptions): Promise<void> {
  const settings = useSettingsStore.getState().settings
  const baseUrl = settings.apiBaseUrl || DEFAULT_BASE_URL
  const url = `${baseUrl}/sessions/${opts.sessionId}/messages/${opts.messageId}/${opts.mode}`

  try {
    const response = await authedFetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders()
      },
      body: JSON.stringify({ client_message_id: opts.clientMessageId ?? null })
    })

    if (!response.ok) {
      const errBody = await response.text().catch(() => '')
      let msg = `API error: ${response.status} ${response.statusText}`
      try {
        const parsed = JSON.parse(errBody)
        if (parsed.detail) {
          msg = typeof parsed.detail === 'string' ? parsed.detail : (parsed.detail.message || msg)
        }
        if (parsed.message) msg = parsed.message
      } catch { }
      throw new Error(msg)
    }

    const contentType = response.headers.get('content-type') || ''
    // needs_confirm：C-1 账尾拦截，服务端返回 JSON 而非 NDJSON 流
    if (contentType.includes('application/json')) {
      const payload = await response.json().catch(() => null) as ReprocessNeedsConfirm | null
      if (payload && payload.status === 'needs_confirm') opts.onNeedsConfirm?.(payload)
      opts.onDone()
      return
    }

    const reader = response.body?.getReader()
    if (!reader) throw new Error('Response body is not readable')

    for await (const event of parseNDJSONStream(reader)) {
      opts.onEvent(event)
      if (event.type === 'message.complete' || (event.type === 'message.error' && event.fatal)) {
        opts.onDone()
        return
      }
    }
    opts.onDone()
  } catch (err) {
    opts.onError(err instanceof Error ? err : new Error(String(err)))
  }
}

// ===== Plan actions =====

async function planAction(sessionId: string, action: 'confirm' | 'edit' | 'answer', body?: Record<string, unknown>): Promise<void> {
  const settings = useSettingsStore.getState().settings
  const baseUrl = settings.apiBaseUrl || DEFAULT_BASE_URL
  const url = `${baseUrl}/sessions/${sessionId}/plan/${action}`

  const response = await authedFetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeaders()
    },
    body: body ? JSON.stringify(body) : undefined
  })

  if (!response.ok) {
    const err = await response.json().catch(() => ({ message: 'Unknown error' }))
    throw new Error(err.message || `Plan ${action} failed`)
  }
}

export const planApi = {
  confirm: (sessionId: string) => planAction(sessionId, 'confirm'),
  edit: (sessionId: string, planText: string) => planAction(sessionId, 'edit', { plan_text: planText }),
  answer: (sessionId: string, answer: string) => planAction(sessionId, 'answer', { answer })
}

// ===== Build actions =====

async function buildAction(sessionId: string, action: 'confirm' | 'skip', toolName?: string): Promise<void> {
  const settings = useSettingsStore.getState().settings
  const baseUrl = settings.apiBaseUrl || DEFAULT_BASE_URL
  const url = `${baseUrl}/sessions/${sessionId}/build/${action}`

  const response = await authedFetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeaders()
    },
    body: toolName ? JSON.stringify({ tool_name: toolName }) : undefined
  })

  if (!response.ok) {
    const err = await response.json().catch(() => ({ message: 'Unknown error' }))
    throw new Error(err.message || `Build ${action} failed`)
  }
}

export const buildApi = {
  confirm: (sessionId: string, toolName?: string) => buildAction(sessionId, 'confirm', toolName),
  skip: (sessionId: string) => buildAction(sessionId, 'skip')
}

// ===== Cancel (统一 Plan 拒绝 / Build 终止) =====

export interface CancelResult {
  status: 'cancelled'
  message_id: string
}

export async function cancelSession(sessionId: string): Promise<CancelResult> {
  const settings = useSettingsStore.getState().settings
  const baseUrl = settings.apiBaseUrl || DEFAULT_BASE_URL
  const url = `${baseUrl}/sessions/${sessionId}/cancel`

  const response = await authedFetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeaders()
    }
  })

  if (!response.ok) {
    const err = await response.json().catch(() => ({ message: 'Unknown error' }))
    throw new Error(err.message || 'Cancel failed')
  }

  return response.json()
}

// ===== Tool result =====

export async function submitToolResult(
  sessionId: string,
  requestId: string,
  result: ToolResult
): Promise<void> {
  const settings = useSettingsStore.getState().settings
  const baseUrl = settings.apiBaseUrl || DEFAULT_BASE_URL
  const url = `${baseUrl}/sessions/${sessionId}/tool-result/${requestId}`

  const response = await authedFetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeaders()
    },
    body: JSON.stringify(result)
  })

  if (!response.ok) {
    const err = await response.json().catch(() => ({ message: 'Unknown error' }))
    throw new Error(err.message || 'Tool result submission failed')
  }
}

// ===== C-2 对账应答（客户端工具首段超时后服务端进对账窗，这里补投结果或声明未知）=====

export async function submitReconcileReply(
  sessionId: string,
  requestId: string,
  state: 'executed' | 'unknown',
  result?: ToolResult
): Promise<void> {
  const settings = useSettingsStore.getState().settings
  const baseUrl = settings.apiBaseUrl || DEFAULT_BASE_URL
  const url = `${baseUrl}/sessions/${sessionId}/tool-result/${requestId}`

  const response = await authedFetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeaders()
    },
    body: JSON.stringify({
      reconcile: true,
      state,
      ...(result ? { result } : {})
    })
  })

  if (!response.ok) {
    const err = await response.json().catch(() => ({ message: 'Unknown error' }))
    throw new Error(err.message || 'Reconcile reply submission failed')
  }
}

// ===== Queue =====

export async function fetchQueue(sessionId: string): Promise<{
  session_id: string
  queue: { message_id: string; content_preview: string; queue_position: number; status: string; created_at: string }[]
  current_processing: { message_id: string; content_preview: string; started_at: string } | null
}> {
  const settings = useSettingsStore.getState().settings
  const baseUrl = settings.apiBaseUrl || DEFAULT_BASE_URL
  const url = `${baseUrl}/sessions/${sessionId}/queue`

  const response = await authedFetch(url, {
    headers: {
      ...getAuthHeaders()
    }
  })

  if (!response.ok) throw new Error(`Queue fetch error: ${response.status}`)
  return response.json()
}

export async function removeFromQueue(sessionId: string, msgId: string): Promise<void> {
  const settings = useSettingsStore.getState().settings
  const baseUrl = settings.apiBaseUrl || DEFAULT_BASE_URL
  const url = `${baseUrl}/sessions/${sessionId}/queue/${msgId}`

  const response = await authedFetch(url, {
    method: 'DELETE',
    headers: {
      ...getAuthHeaders()
    }
  })

  if (!response.ok) throw new Error(`Queue remove error: ${response.status}`)
}

// ===== MCP 管理 API (section 2.9) =====

/** 业务请求唯一出头的鉴权 header；access token 由 authStore 持有（doc 18-10.3）。 */
function getAuthHeaders(): Record<string, string> {
  const token = useAuthStore.getState().accessToken
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {})
  }
}

function getMcpUrl(path: string): string {
  const settings = useSettingsStore.getState().settings
  const baseUrl = settings.apiBaseUrl || DEFAULT_BASE_URL
  return `${baseUrl}${path}`
}

// GET /mcp/hub
export async function fetchMcpHub(): Promise<{ servers: McpHubServer[] }> {
  const response = await authedFetch(getMcpUrl('/mcp/hub'), { headers: getAuthHeaders() })
  if (!response.ok) throw new Error(`MCP Hub fetch error: ${response.status}`)
  return response.json()
}

// GET /mcp/installed
export async function fetchMcpInstalled(): Promise<{ installed: McpInstalledServer[] }> {
  const response = await authedFetch(getMcpUrl('/mcp/installed'), { headers: getAuthHeaders() })
  if (!response.ok) throw new Error(`MCP Installed fetch error: ${response.status}`)
  return response.json()
}

// POST /mcp/install — 服务端登记 + 返回完整配置，客户端按 transport 建立连接
export async function installMcpApi(serverId: string): Promise<McpInstallResponse> {
  const response = await authedFetch(getMcpUrl('/mcp/install'), {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({ server_id: serverId })
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Install failed' }))
    if (response.status === 409) throw new Error('该 MCP 已安装')
    if (response.status === 404) throw new Error('server_id 不在 Hub 中')
    throw new Error(err.detail || `MCP install error: ${response.status}`)
  }
  return response.json()
}

// DELETE /mcp/uninstall/{server_id}
export async function uninstallMcpApi(serverId: string): Promise<{ uninstalled: boolean; server_id: string }> {
  const response = await authedFetch(getMcpUrl(`/mcp/uninstall/${serverId}`), {
    method: 'DELETE',
    headers: getAuthHeaders()
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Uninstall failed' }))
    throw new Error(err.detail || `MCP uninstall error: ${response.status}`)
  }
  return response.json()
}

// GET /mcp/custom
export async function fetchMcpCustom(): Promise<{ custom: CustomMcpServer[] }> {
  const response = await authedFetch(getMcpUrl('/mcp/custom'), { headers: getAuthHeaders() })
  if (!response.ok) throw new Error(`MCP Custom fetch error: ${response.status}`)
  return response.json()
}

// POST /mcp/custom
export async function createCustomMcpApi(req: CreateCustomMcpRequest): Promise<CustomMcpServer> {
  const response = await authedFetch(getMcpUrl('/mcp/custom'), {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify(req)
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Create custom MCP failed' }))
    throw new Error(err.detail || `MCP custom create error: ${response.status}`)
  }
  return response.json()
}

// DELETE /mcp/custom/{server_id}
export async function deleteCustomMcpApi(serverId: string): Promise<{ deleted: boolean; server_id: string }> {
  const response = await authedFetch(getMcpUrl(`/mcp/custom/${serverId}`), {
    method: 'DELETE',
    headers: getAuthHeaders()
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Delete custom MCP failed' }))
    throw new Error(err.detail || `MCP custom delete error: ${response.status}`)
  }
  return response.json()
}

// POST /mcp/tools — 客户端上报工具清单（身份从 token 取，不再传 user_id）
export async function reportMcpTools(
  serverId: string,
  tools: McpToolDef[]
): Promise<{ received: boolean; tool_count: number }> {
  const response = await authedFetch(getMcpUrl('/mcp/tools'), {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({ server_id: serverId, tools })
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Report tools failed' }))
    throw new Error(err.detail || `MCP tools report error: ${response.status}`)
  }
  return response.json()
}

// ===== Skill 管理 API (section 3.9) =====

function getSkillUrl(path: string): string {
  const settings = useSettingsStore.getState().settings
  const baseUrl = settings.apiBaseUrl || DEFAULT_BASE_URL
  return `${baseUrl}${path}`
}

// GET /skills/hub
export async function fetchSkillHub(): Promise<{ skills: HubSkill[] }> {
  const response = await authedFetch(getSkillUrl('/skills/hub'), { headers: getAuthHeaders() })
  if (!response.ok) throw new Error(`Skill Hub fetch error: ${response.status}`)
  return response.json()
}

// GET /skills/installed
export async function fetchInstalledSkills(): Promise<{ installed: InstalledSkill[] }> {
  const response = await authedFetch(getSkillUrl('/skills/installed'), { headers: getAuthHeaders() })
  if (!response.ok) throw new Error(`Skill Installed fetch error: ${response.status}`)
  return response.json()
}

// POST /skills/install — 服务端登记 + 返回 zip，客户端解压到 ~/.iwork/skills/{name}/
export async function installSkillApi(skillId: string): Promise<SkillInstallResult> {
  // Dedicated headers for this endpoint — the response may be JSON or binary zip,
  // so we explicitly accept both. Don't reuse getAuthHeaders() which sets
  // Content-Type: application/json as a blanket header.
  const headers: Record<string, string> = {
    'Accept': 'application/zip, application/json',
    'Content-Type': 'application/json',
    ...(useAuthStore.getState().accessToken
      ? { Authorization: `Bearer ${useAuthStore.getState().accessToken}` }
      : {})
  }

  const response = await authedFetch(getSkillUrl('/skills/install'), {
    method: 'POST',
    headers,
    body: JSON.stringify({ skill_id: skillId })
  })

  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Install failed' }))
    if (response.status === 409) throw new Error('该 Skill 已安装')
    if (response.status === 404) throw new Error('skill_id 不在 Hub 中')
    throw new Error(err.detail || `Skill install error: ${response.status}`)
  }

  // Detect zip response: check Content-Type header, fallback to Content-Disposition
  const contentType = response.headers.get('Content-Type') || ''
  const disposition = response.headers.get('Content-Disposition') || ''
  const isZip = contentType.includes('application/zip')
    || contentType.includes('application/octet-stream')
    || disposition.includes('.zip')
    || disposition.includes('attachment')

  if (isZip) {
    const skillName = response.headers.get('X-Skill-Name') || skillId
    const blob = await response.blob()
    console.log(`[installSkill] Received zip: ${blob.size} bytes, type=${contentType || '(none)'}, skill=${skillName}`)
    const extractPath = await extractSkillZip(skillId, skillName, blob)
    return { skill_id: skillId, skill_name: skillName, extract_path: extractPath }
  }

  // Fallback: JSON response (legacy backend)
  const data = await response.json()
  return { skill_id: skillId, skill_name: data.skill_name || skillId, extract_path: '' }
}

// DELETE /skills/uninstall/{skill_id}
export async function uninstallSkillApi(skillId: string): Promise<{ uninstalled: boolean; skill_id: string }> {
  const response = await authedFetch(getSkillUrl(`/skills/uninstall/${skillId}`), {
    method: 'DELETE',
    headers: getAuthHeaders()
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Uninstall failed' }))
    throw new Error(err.detail || `Skill uninstall error: ${response.status}`)
  }
  return response.json()
}

// POST /skills/enable
export async function enableSkillApi(skillId: string): Promise<{ enabled: boolean; skill_id: string }> {
  const response = await authedFetch(getSkillUrl('/skills/enable'), {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({ skill_id: skillId })
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Enable failed' }))
    throw new Error(err.detail || `Skill enable error: ${response.status}`)
  }
  return response.json()
}

// POST /skills/disable
export async function disableSkillApi(skillId: string): Promise<{ disabled: boolean; skill_id: string }> {
  const response = await authedFetch(getSkillUrl('/skills/disable'), {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({ skill_id: skillId })
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Disable failed' }))
    throw new Error(err.detail || `Skill disable error: ${response.status}`)
  }
  return response.json()
}

// GET /skills/custom
export async function fetchCustomSkillsApi(): Promise<{ custom: CustomSkillDef[] }> {
  const response = await authedFetch(getSkillUrl('/skills/custom'), { headers: getAuthHeaders() })
  if (!response.ok) throw new Error(`Custom Skills fetch error: ${response.status}`)
  return response.json()
}

// POST /skills/custom
export async function createCustomSkillApi(req: CreateCustomSkillRequest): Promise<CustomSkillDef> {
  const response = await authedFetch(getSkillUrl('/skills/custom'), {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify(req)
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Create custom skill failed' }))
    throw new Error(err.detail || `Custom skill create error: ${response.status}`)
  }
  return response.json()
}

// PUT /skills/custom/{skill_id}
export async function updateCustomSkillApi(skillId: string, req: Partial<CreateCustomSkillRequest>): Promise<{ updated: boolean; skill_id: string }> {
  const response = await authedFetch(getSkillUrl(`/skills/custom/${skillId}`), {
    method: 'PUT',
    headers: getAuthHeaders(),
    body: JSON.stringify(req)
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Update custom skill failed' }))
    throw new Error(err.detail || `Custom skill update error: ${response.status}`)
  }
  return response.json()
}

// DELETE /skills/custom/{skill_id}
export async function deleteCustomSkillApi(skillId: string): Promise<{ deleted: boolean; skill_id: string }> {
  const response = await authedFetch(getSkillUrl(`/skills/custom/${skillId}`), {
    method: 'DELETE',
    headers: getAuthHeaders()
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Delete custom skill failed' }))
    throw new Error(err.detail || `Custom skill delete error: ${response.status}`)
  }
  return response.json()
}

// ===== L1 原子记忆 API（docs/chapters/5-记忆模块）=====

function getApiUrl(path: string): string {
  const settings = useSettingsStore.getState().settings
  const baseUrl = settings.apiBaseUrl || DEFAULT_BASE_URL
  return `${baseUrl}${path}`
}

// GET /l1/memories — 只出 retrievable=true（被取代的旧版本不进列表）
export async function fetchL1Memories(
  params?: { type?: string; agentId?: string; limit?: number; offset?: number }
): Promise<{ total: number; memories: L1MemoryItem[] }> {
  const query = new URLSearchParams()
  if (params?.type) query.set('type', params.type)
  if (params?.agentId) query.set('agent_id', params.agentId)
  if (params?.limit != null) query.set('limit', String(params.limit))
  if (params?.offset != null) query.set('offset', String(params.offset))
  const suffix = query.toString() ? `?${query}` : ''
  const response = await authedFetch(getApiUrl(`/l1/memories${suffix}`), { headers: getAuthHeaders() })
  if (!response.ok) throw new Error(`L1 memories fetch error: ${response.status}`)
  return response.json()
}

// DELETE /l1/memories/{id} — 硬删（用户手删的语义是"这条不该存在"）
export async function deleteL1Memory(id: string): Promise<void> {
  const response = await authedFetch(getApiUrl(`/l1/memories/${encodeURIComponent(id)}`), {
    method: 'DELETE',
    headers: getAuthHeaders()
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({ message: 'Delete memory failed' }))
    throw new Error(err.message || err.detail || `L1 memory delete error: ${response.status}`)
  }
}

// ===== L2 场景记忆 API（docs/chapters/5-记忆模块 第二部分）=====

// GET /l2/scenes — 只出 retrievable=true（被 merge 取代的旧场景不进列表），按热度降序
export async function fetchL2Scenes(
  params?: { agentId?: string; limit?: number; offset?: number }
): Promise<{ total: number; scenes: L2SceneItem[] }> {
  const query = new URLSearchParams()
  if (params?.agentId) query.set('agent_id', params.agentId)
  if (params?.limit != null) query.set('limit', String(params.limit))
  if (params?.offset != null) query.set('offset', String(params.offset))
  const suffix = query.toString() ? `?${query}` : ''
  const response = await authedFetch(getApiUrl(`/l2/scenes${suffix}`), { headers: getAuthHeaders() })
  if (!response.ok) throw new Error(`L2 scenes fetch error: ${response.status}`)
  return response.json()
}

// DELETE /l2/scenes/{id} — 硬删（场景是自动产物，删除只作逃生口）
export async function deleteL2Scene(id: string): Promise<void> {
  const response = await authedFetch(getApiUrl(`/l2/scenes/${encodeURIComponent(id)}`), {
    method: 'DELETE',
    headers: getAuthHeaders()
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({ message: 'Delete scene failed' }))
    throw new Error(err.message || err.detail || `L2 scene delete error: ${response.status}`)
  }
}

// ===== L3 画像记忆 API（docs/chapters/5-记忆模块 第三部分）=====

// GET /l3/personas — 一个作用域一行，按最后生成时间降序
export async function fetchL3Personas(
  params?: { agentId?: string; limit?: number; offset?: number }
): Promise<{ total: number; personas: L3PersonaItem[] }> {
  const query = new URLSearchParams()
  if (params?.agentId) query.set('agent_id', params.agentId)
  if (params?.limit != null) query.set('limit', String(params.limit))
  if (params?.offset != null) query.set('offset', String(params.offset))
  const suffix = query.toString() ? `?${query}` : ''
  const response = await authedFetch(getApiUrl(`/l3/personas${suffix}`), { headers: getAuthHeaders() })
  if (!response.ok) throw new Error(`L3 personas fetch error: ${response.status}`)
  return response.json()
}

// DELETE /l3/personas?agent_id= — 硬删。画像行没有 id，主键是 (user_id, agent_id)，
// 所以作用域靠 query 参数定位（空串 = 顶层作用域）。
export async function deleteL3Persona(agentId: string): Promise<void> {
  const query = new URLSearchParams({ agent_id: agentId })
  const response = await authedFetch(getApiUrl(`/l3/personas?${query}`), {
    method: 'DELETE',
    headers: getAuthHeaders()
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({ message: 'Delete persona failed' }))
    throw new Error(err.message || err.detail || `L3 persona delete error: ${response.status}`)
  }
}

// ===== Rules 管理 API (section 5.9) =====

// GET /rules
export async function fetchRules(): Promise<{ rules: RuleItem[] }> {
  const response = await authedFetch(getApiUrl('/rules'), { headers: getAuthHeaders() })
  if (!response.ok) throw new Error(`Rules fetch error: ${response.status}`)
  return response.json()
}

// POST /rules — 新建/编辑（按 name upsert）
export async function saveRule(req: {
  name: string
  description: string
  content: string
  priority: number
}): Promise<{ id: string; name: string; created_at?: string; updated_at?: string }> {
  const response = await authedFetch(getApiUrl('/rules'), {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify(req)
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({ message: 'Save rule failed' }))
    throw new Error(err.message || err.detail || `Rule save error: ${response.status}`)
  }
  return response.json()
}

// DELETE /rules/{id}
export async function deleteRuleApi(id: string): Promise<{ status: string; id: string }> {
  const response = await authedFetch(getApiUrl(`/rules/${id}`), {
    method: 'DELETE',
    headers: getAuthHeaders()
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({ message: 'Delete rule failed' }))
    throw new Error(err.message || err.detail || `Rule delete error: ${response.status}`)
  }
  return response.json()
}

// ===== Expert & Team API (section 9.10) =====

function getExpertUrl(path: string): string {
  const settings = useSettingsStore.getState().settings
  const baseUrl = settings.apiBaseUrl || DEFAULT_BASE_URL
  return `${baseUrl}${path}`
}

// GET /experts
export async function fetchExperts(): Promise<{ experts: Expert[] }> {
  const response = await authedFetch(getExpertUrl('/experts'), { headers: getAuthHeaders() })
  if (!response.ok) throw new Error(`Experts fetch error: ${response.status}`)
  return response.json()
}

// GET /experts/{id}/download
export async function downloadExpert(id: string): Promise<string> {
  const headers: Record<string, string> = { 'Accept': 'application/zip, application/json' }

  const response = await authedFetch(getExpertUrl(`/experts/${id}/download`), { headers })

  if (!response.ok) {
    if (response.status === 404) throw new Error('专家不存在')
    throw new Error(`Expert download error: ${response.status}`)
  }

  const contentType = response.headers.get('Content-Type') || ''
  const isZip = contentType.includes('application/zip') || contentType.includes('application/octet-stream')

  if (isZip) {
    const blob = await response.blob()
    const base64 = await blobToBase64(blob)
    // Extract zip filename from Content-Disposition header, e.g. 'attachment; filename="my-expert.zip"'
    const disposition = response.headers.get('Content-Disposition') || ''
    const filenameMatch = disposition.match(/filename[^;=\n]*=["']?(([^"';\n]+\.zip))["']?/i)
    const zipName = filenameMatch ? filenameMatch[1].replace(/\.zip$/i, '') : id
    return ipcClient.file.extractPlugin(base64, zipName)
  }

  const data = await response.json()
  return data.extract_path || ''
}

// GET /teams/{id}/download
export async function downloadTeam(id: string): Promise<string> {
  const headers: Record<string, string> = { 'Accept': 'application/zip, application/json' }

  const response = await authedFetch(getExpertUrl(`/teams/${id}/download`), { headers })

  if (!response.ok) {
    if (response.status === 404) throw new Error('团队不存在')
    throw new Error(`Team download error: ${response.status}`)
  }

  const contentType = response.headers.get('Content-Type') || ''
  const isZip = contentType.includes('application/zip') || contentType.includes('application/octet-stream')

  if (isZip) {
    const blob = await response.blob()
    const base64 = await blobToBase64(blob)
    const disposition = response.headers.get('Content-Disposition') || ''
    const filenameMatch = disposition.match(/filename[^;=\n]*=["']?(([^"';\n]+\.zip))["']?/i)
    const zipName = filenameMatch ? filenameMatch[1].replace(/\.zip$/i, '') : id
    return ipcClient.file.extractPlugin(base64, zipName)
  }

  const data = await response.json()
  return data.extract_path || ''
}

// GET /teams
export async function fetchTeams(): Promise<{ teams: Team[] }> {
  const response = await authedFetch(getExpertUrl('/teams'), { headers: getAuthHeaders() })
  if (!response.ok) throw new Error(`Teams fetch error: ${response.status}`)
  return response.json()
}

// ===== RBAC 角色权限配置（docs/chapters/19-权限管理RBAC.md §4.2）=====

export interface RbacRole {
  id: number
  role_name: string
  role_key: string
  data_scope: string
  status: number
}

export interface RbacPermission {
  id: number
  parent_id: number
  permission_name: string
  permission_type: 'M' | 'C' | 'F'
  perms: string | null
  order_num: number
}

/** 服务端错误体统一 {"detail": {"error","message"}}，这里把 message 提出来当异常文案。 */
async function rbacError(response: Response, fallback: string): Promise<Error> {
  try {
    const body = await response.json()
    const detail = body?.detail
    if (detail && typeof detail === 'object' && detail.message) return new Error(detail.message)
    if (typeof detail === 'string') return new Error(detail)
  } catch {
    /* 响应体不是 JSON，用兜底文案 */
  }
  return new Error(`${fallback}（${response.status}）`)
}

// GET /system/role/list
export async function fetchRoles(): Promise<{ roles: RbacRole[] }> {
  const response = await authedFetch(getApiUrl('/system/role/list'), { headers: getAuthHeaders() })
  if (!response.ok) throw await rbacError(response, '角色列表读取失败')
  return response.json()
}

// GET /system/permission/list
export async function fetchPermissions(): Promise<{ permissions: RbacPermission[] }> {
  const response = await authedFetch(getApiUrl('/system/permission/list'), { headers: getAuthHeaders() })
  if (!response.ok) throw await rbacError(response, '权限点读取失败')
  return response.json()
}

// GET /system/role/{role_id}/permissions
export async function fetchRolePermissions(
  roleId: number
): Promise<{ role_id: number; permission_ids: number[] }> {
  const response = await authedFetch(getApiUrl(`/system/role/${roleId}/permissions`), {
    headers: getAuthHeaders()
  })
  if (!response.ok) throw await rbacError(response, '角色授权读取失败')
  return response.json()
}

// PUT /system/role/{role_id}/permissions — 整集替换
export async function grantRolePermissions(
  roleId: number,
  permissionIds: number[]
): Promise<{ updated: boolean; count: number }> {
  const response = await authedFetch(getApiUrl(`/system/role/${roleId}/permissions`), {
    method: 'PUT',
    headers: getAuthHeaders(),
    body: JSON.stringify({ permission_ids: permissionIds })
  })
  if (!response.ok) throw await rbacError(response, '保存授权失败')
  return response.json()
}

// PUT /system/role/{role_id} — 数据范围 ALL（全部）/ DEPT（本部门及下级）/ SELF（仅本人）
export async function setRoleDataScope(
  roleId: number,
  dataScope: string
): Promise<{ updated: boolean }> {
  const response = await authedFetch(getApiUrl(`/system/role/${roleId}`), {
    method: 'PUT',
    headers: getAuthHeaders(),
    body: JSON.stringify({ data_scope: dataScope })
  })
  if (!response.ok) throw await rbacError(response, '数据范围保存失败')
  return response.json()
}

// ===== 部门与用户归属（docs/chapters/19-权限管理RBAC.md §2.2）=====

export interface DeptNode {
  id: number
  parent_id: number
  dept_name: string
  /** 只有默认部门（`default`）有值；用户建的部门是 null */
  dept_key: string | null
  order_num: number
  status: number
}

export interface DeptUser {
  id: string
  username: string
  display_name: string | null
  /** null = 未分配；正常运行时种子会把所有人回填成默认部门 */
  dept_id: number | null
  status: string
  /** 自动锁定的到期时间（ISO）；null = 没锁着。用户管理页据此决定「解锁」按钮出不出来。
   *  到期会自解（doc 18-3.3），所以前端要拿它跟当前时间比，不能只看非空。 */
  locked_until: string | null
  /** 角色 id；名字拿 `fetchRoles()` 对。正常不会为空（服务端拒空集） */
  role_ids: number[]
}

// GET /system/dept/list
export async function fetchDepts(): Promise<{ depts: DeptNode[] }> {
  const response = await authedFetch(getApiUrl('/system/dept/list'), { headers: getAuthHeaders() })
  if (!response.ok) throw await rbacError(response, '部门列表读取失败')
  return response.json()
}

// GET /system/user/list
export async function fetchUsers(): Promise<{ users: DeptUser[] }> {
  const response = await authedFetch(getApiUrl('/system/user/list'), { headers: getAuthHeaders() })
  if (!response.ok) throw await rbacError(response, '用户列表读取失败')
  return response.json()
}

// POST /system/dept
export async function createDept(
  parentId: number,
  deptName: string,
  orderNum = 0
): Promise<{ id: number }> {
  const response = await authedFetch(getApiUrl('/system/dept'), {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({ parent_id: parentId, dept_name: deptName, order_num: orderNum })
  })
  if (!response.ok) throw await rbacError(response, '新建部门失败')
  return response.json()
}

// POST /system/user —— 管理员建号。部门范围由服务端判（超管任意、部门管理员限本人子树）
// roleId 传了就是"建号即定角色"（服务端按 system:role:assign 再判一次）；
// 不传就挂默认角色 —— 字段干脆不出现在 body 里，别让空数组和"没选"混成一种语义。
export async function createUser(
  username: string,
  password: string,
  deptId: number,
  roleId?: number | null
): Promise<{ id: string; username: string }> {
  const response = await authedFetch(getApiUrl('/system/user'), {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({
      username,
      password,
      dept_id: deptId,
      ...(roleId != null ? { role_ids: [roleId] } : {})
    })
  })
  if (!response.ok) throw await rbacError(response, '新建用户失败')
  return response.json()
}

// PUT /system/dept/{dept_id}
export async function updateDept(
  deptId: number,
  parentId: number,
  deptName: string,
  orderNum = 0
): Promise<{ updated: boolean }> {
  const response = await authedFetch(getApiUrl(`/system/dept/${deptId}`), {
    method: 'PUT',
    headers: getAuthHeaders(),
    body: JSON.stringify({ parent_id: parentId, dept_name: deptName, order_num: orderNum })
  })
  if (!response.ok) throw await rbacError(response, '保存部门失败')
  return response.json()
}

// DELETE /system/dept/{dept_id}
export async function deleteDept(deptId: number): Promise<{ deleted: boolean }> {
  const response = await authedFetch(getApiUrl(`/system/dept/${deptId}`), {
    method: 'DELETE',
    headers: getAuthHeaders()
  })
  if (!response.ok) throw await rbacError(response, '删除部门失败')
  return response.json()
}

// PUT /system/user/{user_id}/dept
export async function setUserDept(userId: string, deptId: number): Promise<{ updated: boolean }> {
  const response = await authedFetch(getApiUrl(`/system/user/${userId}/dept`), {
    method: 'PUT',
    headers: getAuthHeaders(),
    body: JSON.stringify({ dept_id: deptId })
  })
  if (!response.ok) throw await rbacError(response, '调整部门失败')
  return response.json()
}

// PUT /system/user/{user_id}/roles — 整集替换
export async function setUserRoles(
  userId: string,
  roleIds: number[]
): Promise<{ updated: boolean; count: number }> {
  const response = await authedFetch(getApiUrl(`/system/user/${userId}/roles`), {
    method: 'PUT',
    headers: getAuthHeaders(),
    body: JSON.stringify({ role_ids: roleIds })
  })
  if (!response.ok) throw await rbacError(response, '保存角色失败')
  return response.json()
}

// PUT /system/user/{user_id} — 改显示名。用户名不给改（是登录标识，也是 login_logs 的审计线索）
export async function updateUser(
  userId: string,
  displayName: string
): Promise<{ updated: boolean }> {
  const response = await authedFetch(getApiUrl(`/system/user/${userId}`), {
    method: 'PUT',
    headers: getAuthHeaders(),
    body: JSON.stringify({ display_name: displayName })
  })
  if (!response.ok) throw await rbacError(response, '保存显示名失败')
  return response.json()
}

// PUT /system/user/{user_id}/password — 管理员重置密码（doc 18-3.5）。
// 服务端会清锁并吊销他手上全部 refresh token，不重签 —— 新会话由他自己登录取。
export async function resetUserPassword(
  userId: string,
  password: string
): Promise<{ updated: boolean }> {
  const response = await authedFetch(getApiUrl(`/system/user/${userId}/password`), {
    method: 'PUT',
    headers: getAuthHeaders(),
    body: JSON.stringify({ password })
  })
  if (!response.ok) throw await rbacError(response, '重置密码失败')
  return response.json()
}

// PUT /system/user/{user_id}/status — active | disabled。停用即封号（没有删除档，见 user_routes）
export async function setUserStatus(
  userId: string,
  status: 'active' | 'disabled'
): Promise<{ updated: boolean }> {
  const response = await authedFetch(getApiUrl(`/system/user/${userId}/status`), {
    method: 'PUT',
    headers: getAuthHeaders(),
    body: JSON.stringify({ status })
  })
  if (!response.ok) throw await rbacError(response, '状态修改失败')
  return response.json()
}

// PUT /system/user/{user_id}/unlock — 清失败计数与锁定期（doc 18-3.3）。幂等
export async function unlockUser(userId: string): Promise<{ updated: boolean }> {
  const response = await authedFetch(getApiUrl(`/system/user/${userId}/unlock`), {
    method: 'PUT',
    headers: getAuthHeaders()
  })
  if (!response.ok) throw await rbacError(response, '解锁失败')
  return response.json()
}

// ===== LLM 模型配置（docs/chapters/19-权限管理RBAC.md 的模型配置一节）=====

/** 聊天下拉的候选项。`GET /models` 只回这三个字段 —— 端点、窗口、单价不出用户面。 */
export interface LlmModelOption {
  model_key: string
  display_name: string
  /** 页面据此标记「内网」，用户面只是提示，不做过滤 */
  deployment_type: string
}

/** 管理面视图，对应 `OrmLlmModel.to_dict()`。
 *
 *  **没有明文密钥**，也没有密文 —— 只有 `has_api_key` 与 `api_key_hint`（如 `sk-…9f2c`）。
 *  拿不到明文是设计如此：响应里出现明文就等于密钥在网络里多走一趟。
 */
export interface LlmModelAdmin {
  model_key: string
  display_name: string
  deployment_type: 'public' | 'intranet'
  protocol: 'openai_compatible' | 'anthropic'
  vendor: string
  model_api_name: string
  base_url: string
  has_api_key: boolean
  api_key_hint: string | null
  timeout_seconds: number
  max_retries: number
  extra_body: Record<string, unknown>
  context_window: number
  /** 压缩触发线（绝对 token）。null = 未设置，服务端按 context_window 折算 */
  compress_threshold_tokens: number | null
  max_output_tokens: number
  supports_tools: boolean
  supports_thinking: boolean
  thinking_budget_tokens: number
  max_concurrency: number
  price_input_per_1m: number | null
  price_output_per_1m: number | null
  enabled: boolean
  remark: string
}

/** 写接口的请求体。字段与服务端 `ModelCreateRequest` / `ModelUpdateRequest` 对齐。
 *
 *  `api_key` 的语义分两种：**新增**时给了就加密入库；**编辑**时留空或 null = 不改动
 *  库里那把 —— 编辑弹窗只显示掩码，用户不动那个框就不该把密钥抹掉。
 */
export interface LlmModelPayload {
  display_name: string
  deployment_type: 'public' | 'intranet'
  protocol: 'openai_compatible' | 'anthropic'
  vendor?: string
  model_api_name: string
  base_url?: string
  api_key?: string | null
  timeout_seconds?: number
  max_retries?: number
  extra_body?: Record<string, unknown>
  context_window?: number
  /** 传 null = 改回"按窗口折算"（与单价留空同语义）；不传 = 不改 */
  compress_threshold_tokens?: number | null
  max_output_tokens?: number
  supports_tools?: boolean
  supports_thinking?: boolean
  thinking_budget_tokens?: number
  max_concurrency?: number
  price_input_per_1m?: number | null
  price_output_per_1m?: number | null
  enabled?: boolean
  remark?: string
}

/** 连接测试的返回。失败也是一个 200，`ok=false` 带着端点原文 —— 别只看 HTTP 状态。 */
export interface ModelTestResult {
  ok: boolean
  status: number | null
  model_api_name: string
  /** ok=true 时是端点回的前若干个字 */
  sample?: string
  stop_reason?: string | null
  /** ok=false 时是报错原文（端口写错 / 密钥不对 / 端点不认 thinking 字段都在这里） */
  detail?: string
}

// GET /models —— 用户面，只要求登录。刻意不挂权限点：没有它谁都拉不到模型清单、也就发不出消息。
export async function fetchModelOptions(): Promise<{ models: LlmModelOption[] }> {
  const response = await authedFetch(getApiUrl('/models'), { headers: getAuthHeaders() })
  if (!response.ok) throw await rbacError(response, '模型清单读取失败')
  return response.json()
}

// GET /system/model/list —— 管理面，含已停用项
export async function fetchModelList(): Promise<{ models: LlmModelAdmin[] }> {
  const response = await authedFetch(getApiUrl('/system/model/list'), { headers: getAuthHeaders() })
  if (!response.ok) throw await rbacError(response, '模型列表读取失败')
  return response.json()
}

// POST /system/model
export async function createModel(
  modelKey: string,
  payload: LlmModelPayload
): Promise<{ model_key: string }> {
  const response = await authedFetch(getApiUrl('/system/model'), {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({ model_key: modelKey, ...payload })
  })
  if (!response.ok) throw await rbacError(response, '新增模型失败')
  return response.json()
}

// PUT /system/model/{model_key} —— 局部更新，只改传了的字段
export async function updateModel(
  modelKey: string,
  payload: Partial<LlmModelPayload>
): Promise<{ updated: boolean }> {
  const response = await authedFetch(getApiUrl(`/system/model/${encodeURIComponent(modelKey)}`), {
    method: 'PUT',
    headers: getAuthHeaders(),
    body: JSON.stringify(payload)
  })
  if (!response.ok) throw await rbacError(response, '保存模型失败')
  return response.json()
}

// DELETE /system/model/{model_key}
export async function deleteModel(modelKey: string): Promise<{ deleted: boolean }> {
  const response = await authedFetch(getApiUrl(`/system/model/${encodeURIComponent(modelKey)}`), {
    method: 'DELETE',
    headers: getAuthHeaders()
  })
  if (!response.ok) throw await rbacError(response, '删除模型失败')
  return response.json()
}

// POST /system/model/{model_key}/test —— 拿这一行的配置真发一次最小请求
export async function testModel(modelKey: string): Promise<ModelTestResult> {
  const response = await authedFetch(
    getApiUrl(`/system/model/${encodeURIComponent(modelKey)}/test`),
    { method: 'POST', headers: getAuthHeaders() }
  )
  // 404（模型不存在）/ 403 走这里；端点自身的报错是 200 + ok=false，不走这里
  if (!response.ok) throw await rbacError(response, '连接测试失败')
  return response.json()
}
