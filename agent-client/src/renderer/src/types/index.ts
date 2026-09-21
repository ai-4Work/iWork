// ===== Modes =====
export type AppMode = 'ask' | 'plan' | 'build'
export type SceneMode = 'office' | 'code'

// ===== Tool Call =====
export interface ToolCall {
  id: string
  name: string
  command?: string
  detail?: string
  result?: string
  _result?: string
  input?: Record<string, unknown>
  /** failed = 工具已执行完但结果是失败（done 只表示"跑完了"，成败另看此处） */
  status: 'pending' | 'running' | 'done' | 'failed' | 'skipped'
  /** false = 服务端判定无需确认，客户端自动执行 */
  approvalRequired?: boolean
  /** C-3 幂等档（随 client.tool_request 下发）：read-only/idempotent 超时可安全重试；non-idempotent 需人工 */
  idempotency?: 'read-only' | 'idempotent' | 'non-idempotent'
  /** 随 client.tool_request 下发的策略包（阶段1），客户端据此预检 */
  policy?: PolicyPacket | null
  /**
   * 本次执行是否真正进了 OS 写墙沙箱。bash/stdio MCP 由主进程实测回填；
   * 文件类工具（write/edit/read/glob/grep/skill）与网络型 MCP 恒为 false（裸机）。
   * undefined = 未知/不适用（如无策略的文件类、未回传前的 MCP）。
   */
  sandboxed?: boolean
}

// ===== Plan =====
export interface PlanEvent {
  id: string
  timestamp: number
  type: 'generated' | 'question' | 'confirmed' | 'rejected' | 'edited'
  question?: string
  options?: string[]
  input_type?: 'select' | 'text' | 'confirm'
  answer?: string | null
}

// ===== Message =====
/** D 工具动作账本：一条受账工具调用（GET /effects 下发）。 */
export interface SideEffectItem {
  invocation_id: string
  /** 归属消息 id：前端按消息分组、把 effects 挂回对应 assistant 气泡 */
  message_id?: string | null
  /** 运行序号：regenerate 开新块（递增），continue 并入当前块；NULL = 迁移前旧块 */
  attempt?: number | null
  tool_name: string
  location?: string
  /** 账本状态：completed 跑了 / skipped 没跑 / issued 结果未回 / superseded 作废待重试 */
  state?: 'issued' | 'completed' | 'skipped' | 'superseded'
  /** 是否写类：true = 构成副作用；false = 只读过程记录 */
  side_effect?: boolean
  idempotency?: string
  input?: Record<string, unknown>
  result?: Record<string, unknown> | null
  error?: string | null
  created_at?: string | null
  completed_at?: string | null
}

export type MessageSegment =
  | { type: 'text'; content: string }
  | { type: 'thinking'; content: string }
  | { type: 'tool_call'; toolCallId?: string }
  | { type: 'system_status'; message: string }
  | { type: 'effects'; effects: SideEffectItem[] }
  | PlanEvent

export interface Message {
  id: string
  role: 'user' | 'assistant'
  serverId?: string
  content: string
  thinking?: string
  files?: string[]
  skillInvocations?: { skill_id: string; skill_name: string }[]
  tools?: ToolCall[]
  processCollapsed?: boolean
  segments?: MessageSegment[]
  /** D 副作用分运行账本：终态 / 会话加载时由 GET /effects 按 attempt 分组重建。
   *  每块 = 一次运行（regenerate 递增开新块、continue 并入当前块），跨重跑追加、不回滚。 */
  effectsRuns?: SideEffectItem[][]
  planStatus?: 'pending' | 'confirmed' | 'rejected'
  planEditing?: boolean
  isStreaming?: boolean
  /** 服务端回合终态（M1/M4 起回填）。undefined = 尚在流式 / 升级前旧消息。
   *  interrupted 为客户端自标（M5 恢复：引擎不在跑/探测不可达时把 in-flight 置为此态），
   *  服务端不产生该值，仅用于前端给"继续/重新生成"按钮放行。
   *  用于 assistant 气泡外沿的"重新生成/继续"按钮门控。 */
  serverStatus?: 'completed' | 'error' | 'cancelled' | 'interrupted'
  timestamp: number
}

// ===== Task (replaces Conversation) =====
export interface Task {
  id: string
  sessionId: string
  title: string
  time: string
  active: boolean
  messages: Message[]
  lastSeq: number
  agentId?: string
  agentType?: 'expert' | 'team'
  agentName?: string
}

// ===== Settings =====
export interface Settings {
  apiBaseUrl: string
  apiKey: string
  userId: string
  model: string
  workspacePath: string
  fullAccess: boolean
  mcpOverrides: Record<string, McpLocalOverride>
}

export const DEFAULT_SETTINGS: Settings = {
  apiBaseUrl: '',
  apiKey: '',
  userId: '',
  model: '/projects/data-report',
  workspacePath: '',
  fullAccess: false,
  mcpOverrides: {}
}

// ===== Skills (matches section 3.9 API responses) =====

/** Hub 中的 Skill 条目 (GET /skills/hub) */
export interface HubSkill {
  skill_id: string
  skill_name: string
  description: string
  version: string
  category: string
  icon: string
  author: string
  tags: string[]
}

/** 已安装的 Skill (GET /skills/installed) */
export interface InstalledSkill {
  skill_id: string
  skill_name: string
  description: string
  icon: string
  category: string
  source: 'hub' | 'custom' | 'builtin'
  enabled: boolean
  prompt: string
}

/** 自定义 Skill (GET /skills/custom) */
export interface CustomSkillDef {
  skill_id: string
  skill_name: string
  description: string
  icon: string
  category: string
  prompt: string
  created_at: string
  updated_at: string
}

/** 创建自定义 Skill 的请求体 (POST /skills/custom) */
export interface CreateCustomSkillRequest {
  skill_name: string
  description?: string
  icon?: string
  prompt: string
  category?: string
}

// Keep legacy CustomSkill for now if needed, but mark as deprecated
/** @deprecated Use CustomSkillDef instead */
export interface CustomSkill {
  id: string
  name: string
  desc: string
  icon: string
  source: 'create' | 'upload'
  fileName?: string
  time: string
}

// ===== MCP (matches section 2.9 API responses) =====

/** Hub 中的 MCP 服务条目 (GET /mcp/hub) */
export interface McpHubServer {
  server_id: string
  server_name: string
  description: string
  icon: string
  category: string
  transport: 'stdio' | 'sse' | 'streamable-http'
  command: string | null
  args: string[]
  url: string | null
  env: Record<string, string>
}

/** 已安装的 MCP 服务 (GET /mcp/installed) */
export interface McpInstalledServer {
  server_id: string
  server_name: string
  description: string
  icon: string
  category: string
  transport: string
  command: string | null
  args: string[]
  url: string | null
  env: Record<string, string>
}

/** 自定义 MCP 服务 (GET /mcp/custom) */
export interface CustomMcpServer {
  server_id: string
  server_name: string
  description: string
  icon: string
  category: string
  transport: string
  command: string | null
  args: string[]
  url: string | null
  env: Record<string, string>
}

/** 创建自定义 MCP 的请求体 (POST /mcp/custom) */
export interface CreateCustomMcpRequest {
  server_name: string
  description?: string
  icon?: string
  category?: string
  transport?: string
  command?: string | null
  args?: string[]
  url?: string | null
  env?: Record<string, string>
}

/** 安装 MCP 请求体 (POST /mcp/install) */
export interface InstallMcpRequest {
  server_id: string
}

/** MCP 安装响应 (POST /mcp/install — 新架构返回配置而非仅登记) */
export interface McpInstallResponse {
  server_id: string
  server_name: string
  transport: 'stdio' | 'sse' | 'streamable-http'
  // stdio
  command?: string
  args?: string[]
  env?: Record<string, string>
  // streamable-http / sse
  url?: string
  headers?: Record<string, string>
}

export interface McpLocalOverride {
  command?: string
  args?: string[]
  url?: string
  env?: Record<string, string>
  headers?: Record<string, string>
}

/** 单个 MCP 工具定义 */
export interface McpToolDef {
  name: string
  description: string
  input_schema: Record<string, unknown>
}

/** MCP 连接状态（客户端维护） */
export interface McpConnectionStatus {
  server_id: string
  status: 'disconnected' | 'connecting' | 'connected' | 'error'
  tool_count: number
  error?: string
}

/** Skill 安装结果（客户端本地安装后返回） */
export interface SkillInstallResult {
  skill_id: string
  skill_name: string
  extract_path: string
}

// L1 原子记忆：服务端从对话里自动抽取的结构化事实碎片（只读，仅可删）
export interface L1MemoryItem {
  id: string
  content: string
  type: 'persona' | 'episodic' | 'instruction'
  priority: number
  scene_name: string
  agent_id: string
  activity_start_time?: string | null
  activity_end_time?: string | null
  version: number
  created_at: string
  updated_at: string
}

// L2 场景记忆：由 L1 记忆整合出的跨会话叙事（只读，仅可删）
export interface L2SceneItem {
  id: string
  name: string
  summary: string
  content: string
  heat: number
  version: number
  agent_id: string
  source_memory_ids: string[]
  created_at: string
  updated_at: string
}

// L3 画像记忆：由 L2 场景叙事综合出的身份文档，一条用户消息前整份注入
// system 提示词（只读，仅可删）。一个作用域一行，所以没有 id —— agent_id 即主键。
export interface L3PersonaItem {
  agent_id: string
  content: string
  version: number
  memory_count_at_generation: number
  created_at: string
  updated_at: string
}

export interface RuleItem {
  id: string
  name: string
  description: string
  content: string
  priority: number
  created_at: string
  updated_at: string
}

// ===== Expert & Team (section 9.10) =====
export interface Expert {
  id: string
  // support both API camelCase and legacy snake_case
  displayName?: string
  display_name?: string
  profession: string
  icon: string
  color: string
  desc: string
  tags: string[]
  version: string
  config: {
    max_turn: number
    max_tokens: number
    timeout_seconds: number
  }
  system_prompt?: string
}

export interface TeamMember {
  id: string
  displayName?: string
  name?: { en: string; zh: string }  // legacy object format
  profession: string
  avatar?: string
  role: 'lead' | 'member'
}

export interface Team {
  id: string
  displayName?: string
  display_name?: string
  icon: string
  desc: string
  leadDisplayName?: string
  lead_display_name?: string
  leadProfession?: string
  lead_profession?: string
  skills: string[]
  mcp: string[]
  members: TeamMember[]
}

// ===== Multi-Agent Session (section 9) =====
export interface AgentColumn {
  id: string
  displayName: string
  profession: string
  avatar: string
  role: 'lead' | 'member'
  color: string
  // waiting：子任务已派发出去、本列在等它回信（服务端 WAITING_CHILDREN）
  // failed：子任务异常收尾
  status: 'idle' | 'thinking' | 'running' | 'done' | 'waiting' | 'failed'
  messages: MultiAgentMessage[]
  config: { max_turn: number; max_tokens: number; timeout_seconds: number }
  model?: string
  skills?: string[]
  _hidden?: boolean
  // 该列背后服务端会话的 id。子 agent 的工具请求必须回投到**子会话**（服务端按
  // 子 session 匹配 invocation），只有 lead 会话 id 时子等不到结果。中继 chunk 自报
  // 家门，这里记下来。
  sessionId?: string
}

export interface MultiAgentMessage {
  id: string
  agentId: string
  role: 'user' | 'agent'
  content: string
  type?: 'text' | 'delegation' | 'tool' | 'status'
  delegation?: {
    to: string; taskType: string; prompt: string; status: string
    outputPreview?: string
    outputFull?: string
  }
  // Rich content — mirrors the regular Message shape so the multi-agent panel can
  // render plan/build/tool interactions the same way MessageItem does.
  thinking?: string
  tools?: ToolCall[]
  segments?: MessageSegment[]
  planStatus?: 'pending' | 'confirmed' | 'rejected'
  planEditing?: boolean
  isStreaming?: boolean
  timestamp: number
}

export interface MultiAgentSession {
  teamId: string
  /** 本面板所属的任务 id：同一个团队可以有多个任务，切换任务必须重建面板，
   *  只比对 teamId 会把上一个任务的对话留在界面上。 */
  taskId?: string
  teamName: string
  teamIcon: string
  agents: AgentColumn[]
  leadAgentId: string
  sessionId?: string
  isProcessing: boolean
}

// ===== Commands =====
export interface Command {
  id: string
  trigger: string
  label: string
  description: string
}

// ===== Electron API =====
/** MCP 工具执行结果 + 本次实际是否在 OS 写墙沙箱中运行（受限 stdio → true；stdio 直连/http/sse → false） */
export interface McpCallResult {
  result: unknown
  sandboxed: boolean
}

export interface ElectronAPI {
  file: {
    glob: (pattern: string) => Promise<string[]>
    read: (path: string) => Promise<string>
    grep: (pattern: string, path: string) => Promise<string[]>
    write: (path: string, content: string) => Promise<void>
    edit: (path: string, oldStr: string, newStr: string) => Promise<void>
    exec: (command: string, timeoutMs?: number) => Promise<{ stdout: string; stderr: string; exit_code: number; sandboxed: boolean }>
    shellEnv: () => Promise<string>
    extractSkill: (base64: string, skillName: string) => Promise<string>
    extractPlugin: (base64: string, zipName: string) => Promise<string>
    readSkillMd: (skillId: string) => Promise<string>
    getSkillDir: (skillId: string) => Promise<string>
  }
  workspace: {
    select: () => Promise<string | null>
  }
  settings: {
    save: (settings: Settings) => Promise<void>
    load: () => Promise<Settings>
  }
  /** M5：渲染层通用本地持久化（消息骨架/outbox）。ns 为命名空间，key 为该项键。 */
  storage: {
    get: (ns: string, key: string) => Promise<unknown>
    set: (ns: string, key: string, value: unknown) => Promise<void>
  }
  mcp: {
    connect: (serverId: string, config: McpInstallResponse) => Promise<McpToolDef[]>
    disconnect: (serverId: string) => Promise<void>
    callTool: (serverId: string, toolName: string, input: Record<string, unknown>, workspace?: string) => Promise<McpCallResult>
  }
  proxy: {
    setPolicy: (packet: PolicyPacket | null) => Promise<void>
    getPort: () => Promise<number | null>
    takeNetworkApprovals: () => Promise<NetworkApproval[]>
    resolveNetwork: (host: string, protocol: string, approved: boolean) => Promise<void>
    resetSession: () => Promise<void>
    onNetworkAsk: (cb: (d: { host: string; protocol: string }) => void) => void
  }
}

declare global {
  interface Window {
    electronAPI: ElectronAPI
  }
}

// ===== Permission Policy (from server build_policy, §8) =====
// 随 client.tool_request 下发的策略包（服务端 permission.py build_policy 产出）。
export interface NetworkRule {
  host: string
  protocol: string
  decision: 'allow' | 'prompt' | 'forbidden'
}

export interface PolicyPacket {
  filesystem?: {
    profile?: string
    allow_write?: string[]
    deny_write?: string[]
    deny_read?: string[]
  }
  network?: {
    enabled?: boolean
    allow_domains?: string[]
    deny_domains?: string[]
    unknown_domain?: string
    approval_policy?: string | Record<string, unknown>
    network_rules?: NetworkRule[]
  }
  sandbox?: {
    required?: boolean
  }
}

/** 单次工具执行期经本地代理判定的域名审批记录，随 /tool-result 回传服务端（NETWORK_APPROVAL 审计） */
export interface NetworkApproval {
  host: string
  protocol: string
  decision: 'allow' | 'deny'
  approved: boolean
}

// ===== NDJSON Server Events (from spec section 1.9.2) =====
// All events carry an optional agent_id for multi-agent sessions (section 9.10.3)
export type ServerEvent =
  // Thinking & Text
  | {
    type: 'agent.thinking'
    seq: number
    delta: string
    turn: number
    message_id: string
    agent_id?: string
  }
  | {
    type: 'agent.text'
    seq: number
    delta: string
    turn: number
    message_id: string
    agent_id?: string
  }
  // Tool calls
  | {
    type: 'agent.tool_call'
    seq: number
    tool_name: string
    tool_call_id: string
    input: Record<string, unknown>
    turn: number
    message_id: string
    agent_id?: string
  }
  | {
    type: 'agent.tool_result'
    seq: number
    tool_call_id: string
    tool_name: string
    result: { success: boolean; output?: string; error?: string; duration_ms: number }
    turn: number
    message_id: string
    agent_id?: string
  }
  | {
    type: 'client.tool_request'
    seq: number
    request_id: string
    tool_name: string
    input: Record<string, unknown>
    message_id: string
    agent_id?: string
    // §8 权限阶段1：服务端三态判定翻译出的审批标记 + 策略包
    requires_approval?: boolean
    tool_call_id?: string
    step?: number
    reasoning?: string | null
    policy?: PolicyPacket | null
    /** C-3 幂等档：read-only / idempotent / non-idempotent（超时/断线能否自动重试的 hint） */
    idempotency?: string
    /** 发起该请求的会话 id；子 agent 的工具请求要回投到这里，缺失则回落 lead */
    session_id?: string
  }
  | {
    type: 'client.tool_timeout'
    seq: number
    request_id: string
    message: string
    agent_id?: string
  }
  // C-2 对账窗：客户端工具首段等待超时，服务端向客户端询问该次执行是否已生效/结果
  | {
    type: 'client.tool_reconcile'
    seq: number
    request_id: string
    invocation_id: string
    tool_name: string
    message_id: string
    /** C-3 幂等档：read-only / idempotent / non-idempotent —— 供客户端决定能否补执行 */
    idempotency?: string
    agent_id?: string
    /** 同 client.tool_request：应答要送回发起请求的那个会话 */
    session_id?: string
  }
  // C-2 需确认：写类工具判不出是否生效 → 服务端不自动重放，提示用户人工确认
  | {
    type: 'tool.reconcile_needs_confirm'
    seq: number
    request_id: string
    invocation_id: string
    tool_name: string
    message_id: string
    message?: string
    agent_id?: string
  }
  // Plan mode
  | {
    type: 'plan.generated'
    seq: number
    message_id: string
    agent_id?: string
  }
  | {
    type: 'plan.question'
    seq: number
    message_id: string
    question: string
    options?: string[]
    input_type: 'select' | 'text' | 'confirm'
    context?: string
    agent_id?: string
  }
  | { type: 'plan.question_timeout'; seq: number; message_id: string; agent_id?: string }
  | { type: 'plan.confirmed'; seq: number; message_id: string; agent_id?: string }
  | { type: 'plan.rejected'; seq: number; message_id: string; agent_id?: string }
  | { type: 'plan.edited'; seq: number; message_id: string; new_plan_text: string; agent_id?: string }
  // Build mode
  | {
    type: 'build.step_pending'
    seq: number
    message_id: string
    tool_name: string
    tool_call_id: string
    input: Record<string, unknown>
    step: number
    reasoning?: string
    agent_id?: string
  }
  | { type: 'build.step_confirmed'; seq: number; step: number; tool_name: string; tool_call_id: string; agent_id?: string }
  | { type: 'build.step_skipped'; seq: number; step: number; tool_name: string; tool_call_id: string; agent_id?: string }
  | { type: 'build.aborted'; seq: number; message_id: string; agent_id?: string }
  // Queue & Lifecycle
  | {
    type: 'queue.updated'
    seq: number
    session_id: string
    queue: { message_id: string; content_preview: string; queue_position: number; status: 'pending' }[]
    current_processing_id: string | null
    agent_id?: string
  }
  | {
    type: 'message.queued'
    seq: number
    message_id: string
    queue_position: number
    queue_size: number
    agent_id?: string
  }
  | {
    type: 'message.start'
    seq: number
    message_id: string
    mode: AppMode
    scene_mode: SceneMode
    workspace: string
    agent_id?: string
  }
  | {
    type: 'message.complete'
    seq: number
    message_id: string
    summary: {
      turns: number
      tokens_in: number
      tokens_out: number
      duration_ms: number
      tool_calls_count: number
    }
    agent_id?: string
  }
  | {
    type: 'message.error'
    seq: number
    message_id: string
    message: string
    code: string
    fatal: boolean
    turn?: number
    agent_id?: string
  }
  | {
    type: 'message.waiting_timeout'
    seq: number
    message_id: string
    reason: 'plan_confirm_timeout' | 'build_confirm_timeout' | 'tool_result_timeout'
    agent_id?: string
  }
  | { type: 'session.timeout'; seq: number; idle_minutes: number; archive_at: string; agent_id?: string }
  | { type: 'heartbeat'; seq: number; timestamp: number; agent_id?: string }
  | {
    type: 'session.recovered'
    seq: number
    current_message_id: string | null
    queue_size: number
    agent_id?: string
  }
  | {
    type: 'system.status'
    seq: number
    message: string
    agent_id?: string
  }
  // ===== Multi-Agent Events (section 9.10.3) =====
  | {
    type: 'agent.status'
    seq: number
    agent_id: string   // lead agent 的扁平成员 id —— 它的那一列
    to: string          // member agent 的扁平成员 id —— 列里那张委派卡片
    status: 'idle' | 'thinking' | 'running' | 'done' | 'waiting' | 'failed'
    output_preview?: string | null
    output?: string | null
  }
  | {
    type: 'session.publish'
    seq: number
    agent_id: string
    to: string
    task_type: string
    prompt: string
  }
  | {
    // 父本轮说完、进 WAITING_CHILDREN 等子回信（§9.11.8）。此时**不会**发
    // message.complete，流也不关，可能挂几分钟——所以这个事件是"还没完，在等谁"
    // 的唯一信号。顶层 agent_id 是父那一列，in_flight 里每项是等待中的成员。
    type: 'task.waiting'
    seq: number
    message_id: string
    agent_id: string
    turn: number
    in_flight: Array<{ task_id: string; agent_path: string; agent_id: string }>
  }
  | {
    type: 'session.summary'
    seq: number
    agent_id: string
    summary: string
    results: Array<{ agent_id: string; status: string; output_preview: string }> | null
  }
