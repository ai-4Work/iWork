// Use official MCP TypeScript SDK for reliable protocol handling across transports.
// This manager stays provider-agnostic and talks to MCP servers only through the SDK.

import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { SSEClientTransport } from '@modelcontextprotocol/sdk/client/sse.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js'
import { ProxyAgent } from 'undici'
import { getCurrentPolicy, getProxyEnv, getProxyUrl } from './proxy'
import { buildSandboxedSpawnParams, doctor } from './sandbox'
import { applyNodeProxyShim } from './nodeProxyShim'

export interface McpServerConfig {
  server_id: string
  transport: 'stdio' | 'sse' | 'streamable-http'
  command?: string
  args?: string[]
  env?: Record<string, string>
  url?: string
  headers?: Record<string, string>
}

export interface McpToolDef {
  name: string
  description: string
  input_schema: Record<string, unknown>
}

/** MCP 工具执行结果 + 本次实际是否在 OS 写墙沙箱中运行（受限 stdio → true；stdio 直连/http/sse → false） */
export interface McpCallResult {
  result: unknown
  sandboxed: boolean
}

function summarizeConfig(config: McpServerConfig) {
  return {
    server_id: config.server_id,
    transport: config.transport,
    command: config.command,
    args: config.args || [],
    url: config.url,
    env: config.env || {},
    headers: config.headers || {}
  }
}

function resolveEnv(value: string): string {
  return value.replace(/\$\{(\w+)\}/g, (_, name) => process.env[name] || '')
}

function resolveEnvVars(env: Record<string, string> | undefined): Record<string, string> | undefined {
  if (!env) return undefined

  const resolved: Record<string, string> = {}
  for (const [key, value] of Object.entries(env)) {
    resolved[key] = resolveEnv(value)
  }
  return resolved
}

function buildHeaders(headers: Record<string, string> | undefined): Record<string, string> {
  const merged: Record<string, string> = {
    Accept: 'application/json, text/event-stream',
    'User-Agent': 'agent-electron-app/1.0.0'
  }

  if (!headers) return merged

  for (const [key, value] of Object.entries(headers)) {
    merged[key] = resolveEnv(value)
  }

  return merged
}

function buildProcessEnv(overrides: Record<string, string> | undefined): Record<string, string> {
  const mergedEntries = [
    ...Object.entries(process.env),
    ...Object.entries(getProxyEnv()),
    ...Object.entries(overrides || {})
  ].filter((entry): entry is [string, string] => typeof entry[1] === 'string')

  return Object.fromEntries(mergedEntries)
}

function requireField(value: string | undefined, fieldName: string, serverId: string): string {
  const resolved = value?.trim()
  if (!resolved) {
    throw new Error(`MCP server ${serverId} is missing required field: ${fieldName}`)
  }
  return resolved
}

// ===== M2 网络墙：主进程内 http/sse 直连会绕过本地代理 → 注入带 dispatcher 的 walled fetch =====
// 未命中白名单时的 Ask 判定在 proxy.ts decideHost（随包下发的网络策略）。undici ProxyAgent 对
// http/https 目标统一走 CONNECT 隧道 → 每个 MCP 目标域都经本地代理 CONNECT 判定。代理未就绪
// 时回退直连并告警（网络墙失效，仅启动竞态场景）。
let mcpProxyAgent: ProxyAgent | null = null

function walledFetch(url: string | URL, init?: RequestInit): Promise<Response> {
  const proxyUrl = getProxyUrl()
  if (!proxyUrl) {
    console.warn('[MCP http/sse] 本地代理未就绪 → 直连 fetch（本连接不经网络墙）')
    return fetch(url, init)
  }
  if (!mcpProxyAgent) mcpProxyAgent = new ProxyAgent(proxyUrl)
  return fetch(url, { ...(init || {}), dispatcher: mcpProxyAgent } as RequestInit)
}

/** stdio 实例的写墙模式：restricted=true 时经 dsh runner 受限令牌包裹，写墙根 = workspace */
interface StdioExecMode {
  workspace: string
  restricted: boolean
}

/** 是否为"联网下载型"启动器（npx 等）：这些启动器本身是 .cmd 垫片，且首启要把包写进
 * npm 缓存（在工作区外），与 Windows WRITE_RESTRICTED 写墙沙箱实测不兼容 → 无法沙箱化。 */
function isBootstrapLauncher(command: string | undefined): boolean {
  if (!command) return false
  const base = command.trim().toLowerCase().replace(/\\/g, '/').split('/').pop() || ''
  return base === 'npx' || base === 'npx.cmd' || base === 'npx.exe'
}

/** 判定 stdio MCP 本次该不该套写墙：策略要求沙箱 + 非 npx 类启动器 + 非空工作区 + 沙箱探活通过 → 受限实例；
 * 否则回退直连并告警（fail-open 仅限 :full/画像禁用/无工作区/沙箱不可用/npx 引导器场景，见权限文档）。 */
async function resolveStdioExec(
  command: string | undefined,
  workspace?: string
): Promise<StdioExecMode> {
  const required = getCurrentPolicy()?.sandbox?.required === true
  const ws = (workspace || '').trim()
  if (!required) return { workspace: ws, restricted: false }
  if (isBootstrapLauncher(command)) {
    console.warn(
      `[MCP stdio] 策略要求沙箱，但启动器 ${command} 需联网下载包且为 .cmd 垫片，与 Windows 写墙沙箱不兼容 → 裸机直连（不受写墙保护）`
    )
    return { workspace: ws, restricted: false }
  }
  if (!ws) {
    console.warn('[MCP stdio] 策略要求沙箱但未提供工作区 → 直连回退（无写墙根）')
    return { workspace: '', restricted: false }
  }
  let ok = false
  try {
    ok = await doctor()
  } catch {
    ok = false
  }
  if (!ok) {
    console.warn('[MCP stdio] 沙箱不可用（doctor 探活失败）→ 直连回退')
    return { workspace: ws, restricted: false }
  }
  return { workspace: ws, restricted: true }
}

async function createTransport(
  config: McpServerConfig,
  exec?: StdioExecMode | null
): Promise<StdioClientTransport | SSEClientTransport | StreamableHTTPClientTransport> {
  console.log(`[MCP] Resolved config for ${config.server_id}:`, JSON.stringify(summarizeConfig(config)))

  switch (config.transport) {
    case 'stdio': {
      const command = requireField(config.command, 'command', config.server_id)
      const env = buildProcessEnv(resolveEnvVars(config.env))
      // Node 内置 fetch 不读 proxy env → 注入 shim 强制其出网走本地审批代理（见 nodeProxyShim.ts）
      await applyNodeProxyShim(env)
      const args = config.args || []

      console.log(`[MCP stdio] Creating transport: ${command} ${args.join(' ')}${exec?.restricted ? ' (sandboxed)' : ''}`)
      console.log(`[MCP stdio] Effective env for ${config.server_id}:`, JSON.stringify(resolveEnvVars(config.env) || {}))

      if (exec?.restricted) {
        // 受限执行实例：SDK transport 只认 cross_spawn 参数，把真实 command 换成
        // electron(RUN_AS_NODE) + console-shim + runner，argv 前置 --workspace/--temp/--mode。
        const sandboxed = await buildSandboxedSpawnParams(command, args, exec.workspace, env)
        return new StdioClientTransport({
          command: sandboxed.command,
          args: sandboxed.args,
          env: sandboxed.env,
          cwd: sandboxed.cwd,
          stderr: 'pipe'
        })
      }
      return new StdioClientTransport({ command, args, env, stderr: 'pipe' })
    }

    case 'sse': {
      const url = requireField(resolveEnv(config.url || ''), 'url', config.server_id)
      const headers = buildHeaders(config.headers)

      console.log(`[MCP SSE] Creating transport: ${url}`)
      return new SSEClientTransport(new URL(url), {
        requestInit: { headers },
        fetch: walledFetch
      })
    }

    case 'streamable-http': {
      const url = requireField(resolveEnv(config.url || ''), 'url', config.server_id)
      const headers = buildHeaders(config.headers)

      console.log(`[MCP streamable-http] Creating transport: ${url}`)
      return new StreamableHTTPClientTransport(new URL(url), {
        requestInit: { headers },
        fetch: walledFetch
      })
    }

    default:
      throw new Error(`Unsupported transport: ${config.transport}`)
  }
}

class McpConnection {
  private client: Client | null = null
  /** 当前 stdio 实例的写墙模式（http/sse 恒 null）；workspace 或 restricted 变化 → 关旧建新 */
  private execMode: StdioExecMode | null = null
  /** 重建串行化：并发 callTool 不做双换，后到者排在重建链之后读同一 client */
  private reconnectChain: Promise<void> = Promise.resolve()

  constructor(private config: McpServerConfig) {}

  private async spawnClient(exec: StdioExecMode | null): Promise<Client> {
    const transport =
      this.config.transport === 'stdio'
        ? await createTransport(this.config, exec)
        : await createTransport(this.config)
    const client = new Client(
      { name: 'agent-electron-app', version: '1.0.0' },
      { capabilities: {} }
    )
    await client.connect(transport)
    return client
  }

  async connect(): Promise<McpToolDef[]> {
    // 发现：此刻无当前工具的 sandbox 策略，起一次实例读 schema 即可（listTools 是固定服务端
    // 代码、无模型输入）。首个受控 callTool 会按 (workspace, restricted) 惰性重建受限实例，
    // 所以发现实例不保留给执行用——避免同一 server 常驻"不受限+受限"双进程。
    const exec = this.config.transport === 'stdio' ? await resolveStdioExec(this.config.command) : null
    this.execMode = exec

    console.log(`[MCP] Connecting ${this.config.server_id} (${this.config.transport})...`)

    try {
      this.client = await this.spawnClient(exec)
    } catch (err: any) {
      if (err?.message === 'Not connected' && this.config.transport === 'stdio') {
        console.log(`[MCP] ${this.config.server_id} handshake retry after "Not connected"...`)
        this.client = await this.spawnClient(exec)
      } else {
        throw err
      }
    }

    console.log(`[MCP] ${this.config.server_id} connected, listing tools...`)
    const result = await this.client.listTools({}, { timeout: 120000 })

    const tools: McpToolDef[] = result.tools.map((tool: any) => ({
      name: tool.name,
      description: tool.description || '',
      input_schema: tool.inputSchema || {}
    }))

    console.log(`[MCP] ${this.config.server_id} found ${tools.length} tools`)
    return tools
  }

  async callTool(
    name: string,
    input: Record<string, unknown>,
    workspace?: string
  ): Promise<McpCallResult> {
    if (this.config.transport === 'stdio') {
      await this.ensureExecMode(workspace)
    }
    if (!this.client) {
      throw new Error(`MCP server ${this.config.server_id} is not connected`)
    }

    // 实际受限状态：仅 stdio 可能存在 OS 写墙受限实例（execMode.restricted）；http/sse 无子进程沙箱 → 裸机。
    const sandboxed = this.config.transport === 'stdio' ? (this.execMode?.restricted ?? false) : false
    const result = await this.client.callTool({ name, arguments: input }, undefined, { timeout: 300000 })
    return { result, sandboxed }
  }

  /** 惰性执行实例：期望模式与当前不一致时关旧建新。直连实例不受 workspace 影响，不因切工作区重建 */
  private ensureExecMode(workspace?: string): Promise<void> {
    const job = this.reconnectChain.then(async () => {
      const desired = await resolveStdioExec(this.config.command, workspace)
      const current = this.execMode
      const sameMode =
        current &&
        current.restricted === desired.restricted &&
        (!desired.restricted || current.workspace === desired.workspace)
      if (sameMode) {
        return
      }
      if (this.client) {
        try {
          await this.client.close()
        } catch (err) {
          console.warn(`[MCP] Error closing ${this.config.server_id}:`, err)
        }
        this.client = null
      }
      this.execMode = desired
      try {
        this.client = await this.spawnClient(desired)
      } catch (err) {
        // 重建失败（如受限 spawn 起不来）：清空模式，下次调用重试而非永久"未连接"
        this.execMode = null
        throw err
      }
      console.log(
        `[MCP] ${this.config.server_id} exec instance -> workspace=${desired.workspace || '(none)'} restricted=${desired.restricted}`
      )
    })
    this.reconnectChain = job.catch((err) =>
      console.warn(`[MCP] ${this.config.server_id} exec instance rebuild failed:`, err)
    )
    return job
  }

  async disconnect(): Promise<void> {
    if (!this.client) return

    try {
      await this.client.close()
    } catch (err) {
      console.warn(`[MCP] Error closing ${this.config.server_id}:`, err)
    }

    this.client = null
    this.execMode = null
  }
}

class McpManager {
  private connections = new Map<string, McpConnection>()

  async connect(serverId: string, config: McpServerConfig): Promise<McpToolDef[]> {
    await this.disconnect(serverId)

    const connection = new McpConnection(config)
    this.connections.set(serverId, connection)
    return connection.connect()
  }

  async callTool(
    serverId: string,
    toolName: string,
    input: Record<string, unknown>,
    workspace?: string
  ): Promise<McpCallResult> {
    const connection = this.connections.get(serverId)
    if (!connection) {
      throw new Error(`MCP server ${serverId} is not connected`)
    }

    return connection.callTool(toolName, input, workspace)
  }

  async disconnect(serverId: string): Promise<void> {
    const connection = this.connections.get(serverId)
    if (!connection) return

    await connection.disconnect()
    this.connections.delete(serverId)
  }

  async disconnectAll(): Promise<void> {
    for (const [serverId] of this.connections) {
      await this.disconnect(serverId)
    }
  }
}

export const mcpManager = new McpManager()
