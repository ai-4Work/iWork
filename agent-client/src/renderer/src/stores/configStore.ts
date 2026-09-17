import { create } from 'zustand'
import type { HubSkill, InstalledSkill, CustomSkillDef, CreateCustomSkillRequest, McpHubServer, McpInstalledServer, CustomMcpServer, CreateCustomMcpRequest, McpConnectionStatus, McpToolDef, McpInstallResponse, McpLocalOverride, MemoryItem, RuleItem } from '../types'
import {
  fetchMcpHub, fetchMcpInstalled, fetchMcpCustom,
  installMcpApi, uninstallMcpApi, createCustomMcpApi, deleteCustomMcpApi,
  fetchSkillHub, fetchInstalledSkills, fetchCustomSkillsApi,
  installSkillApi, uninstallSkillApi, enableSkillApi, disableSkillApi,
  createCustomSkillApi, deleteCustomSkillApi,
  reportMcpTools,
  fetchMemories, saveMemory, deleteMemoryApi,
  fetchRules, saveRule, deleteRuleApi
} from '../services/api'
import { ipcClient } from '../services/ipcClient'
import { useSettingsStore } from './settingsStore'

const HEFENG_MCP_AK = import.meta.env.VITE_HEFENG_API_KEY ?? ''
const HEFENG_API_BASE_URL = 'https://devapi.qweather.com/v7'
const HEFENG_API_URL = 'https://devapi.qweather.com/v7'

function isHefengMcpServer(server: {
  server_id?: string
  server_name?: string
  command?: string | null
  args?: string[]
}): boolean {
  const text = [
    server.server_id,
    server.server_name,
    server.command,
    ...(server.args || [])
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()

  return text.includes('hefeng') || text.includes('qweather')
}

function applyBuiltInMcpDefaults<T extends {
  server_id: string
  command?: string | null
  args?: string[]
  env?: Record<string, string>
  url?: string | null
  headers?: Record<string, string>
}>(server: T): T {
  if (!isHefengMcpServer(server)) return server

  return {
    ...server,
    command: 'npx',
    args: [
      '-y',
      'hefeng-mcp-server',
      `--apiKey=${HEFENG_MCP_AK}`,
      `--apiUrl=${HEFENG_API_BASE_URL}`,
      // `--geoApiUrl=${HEFENG_GEO_API_URL}`
    ],
    env: {
      ...(server.env || {}),
      HEFENG_API_KEY: HEFENG_MCP_AK,
      HEFENG_API_URL: HEFENG_API_URL,
      // HEFENG_GEO_API_URL: HEFENG_GEO_API_URL
    }
  }
}

function applyLocalMcpOverride<T extends {
  server_id: string
  command?: string | null
  args?: string[]
  env?: Record<string, string>
  url?: string | null
  headers?: Record<string, string>
}>(server: T, override?: McpLocalOverride): T {
  if (!override) return server

  const next: T = { ...server }

  if (override.command !== undefined) next.command = override.command
  if (override.args !== undefined) next.args = override.args
  if (override.url !== undefined) next.url = override.url
  if (override.env !== undefined) next.env = { ...(server.env || {}), ...override.env }
  if (override.headers !== undefined) next.headers = { ...(server.headers || {}), ...override.headers }

  return next
}

function summarizeMcpConfig(config: {
  command?: string | null
  args?: string[]
  env?: Record<string, string>
  url?: string | null
  headers?: Record<string, string>
  transport?: string
}) {
  return {
    transport: config.transport,
    command: config.command,
    args: config.args || [],
    url: config.url,
    env: config.env || {},
    headers: config.headers || {}
  }
}

interface ConfigState {
  // Skills — API-backed
  hubSkills: HubSkill[]
  hubSkillsLoading: boolean
  hubSkillsError: string | null
  installedSkills: InstalledSkill[]
  installedSkillsLoading: boolean
  installedSkillsError: string | null
  customSkills: CustomSkillDef[]
  customSkillsLoading: boolean
  customSkillsError: string | null

  // MCP — API-backed
  mcpHub: McpHubServer[]
  mcpHubLoading: boolean
  mcpHubError: string | null
  installedMcps: McpInstalledServer[]
  installedLoading: boolean
  installedError: string | null
  customMcps: CustomMcpServer[]
  customLoading: boolean
  customError: string | null

  // Installing state
  installingMcpIds: Set<string>
  installingSkillIds: Set<string>

  // MCP connection statuses per server
  mcpConnectionStatuses: Record<string, McpConnectionStatus>
  // Discovered tools per server (from tools/list after connect)
  mcpDiscoveredTools: Record<string, McpToolDef[]>

  // Skills actions
  loadSkillHub: () => Promise<void>
  loadInstalledSkills: () => Promise<void>
  loadCustomSkills: () => Promise<void>
  loadAllSkills: () => Promise<void>
  installSkill: (skillId: string) => Promise<void>
  uninstallSkill: (skillId: string) => Promise<void>
  enableSkill: (skillId: string) => Promise<void>
  disableSkill: (skillId: string) => Promise<void>
  createCustomSkill: (req: CreateCustomSkillRequest) => Promise<void>
  deleteCustomSkill: (skillId: string) => Promise<void>

  // MCP actions
  loadMcpHub: () => Promise<void>
  loadInstalledMcps: () => Promise<void>
  loadCustomMcps: () => Promise<void>
  loadAllMcps: () => Promise<void>
  connectInstalledMcps: () => Promise<void>
  /** 强制(重)连接某个已安装 server（先断开旧的再用当前 installedMcps 配置新建），改 override 后调用 */
  reconnectInstalledMcp: (serverId: string) => Promise<void>
  installMcp: (serverId: string) => Promise<void>
  uninstallMcp: (serverId: string) => Promise<void>
  createCustomMcp: (req: CreateCustomMcpRequest) => Promise<void>
  deleteCustomMcp: (serverId: string) => Promise<void>
  setMcpConnectionStatus: (serverId: string, status: Partial<McpConnectionStatus>) => void

  // MCP session helpers
  getMcpServersForSession: () => { server_id: string; server_name: string; enabled_tools?: string[] }[]
  reportMcpToolsToSession: () => Promise<void>

  // Memories & Rules — API-backed
  memories: MemoryItem[]
  memoriesLoading: boolean
  memoriesError: string | null
  rules: RuleItem[]
  rulesLoading: boolean
  rulesError: string | null

  loadMemories: () => Promise<void>
  saveMemoryAction: (req: { name: string; description: string; type: string; content: string; protected: boolean }) => Promise<void>
  deleteMemoryAction: (id: string) => Promise<void>
  loadRules: () => Promise<void>
  saveRuleAction: (req: { name: string; description: string; content: string; priority: number }) => Promise<void>
  deleteRuleAction: (id: string) => Promise<void>
}

export const useConfigStore = create<ConfigState>((set, get) => ({
  // Skills — API-backed
  hubSkills: [],
  hubSkillsLoading: false,
  hubSkillsError: null,
  installedSkills: [],
  installedSkillsLoading: false,
  installedSkillsError: null,
  customSkills: [],
  customSkillsLoading: false,
  customSkillsError: null,

  // MCP — API-backed
  mcpHub: [],
  mcpHubLoading: false,
  mcpHubError: null,
  installedMcps: [],
  installedLoading: false,
  installedError: null,
  customMcps: [],
  customLoading: false,
  customError: null,

  installingMcpIds: new Set<string>(),
  installingSkillIds: new Set<string>(),

  mcpConnectionStatuses: {},
  mcpDiscoveredTools: {},

  // Memories & Rules
  memories: [],
  memoriesLoading: false,
  memoriesError: null,
  rules: [],
  rulesLoading: false,
  rulesError: null,

  // Skills actions
  loadSkillHub: async () => {
    const s = get()
    if (s.hubSkillsLoading) return
    set({ hubSkillsLoading: true, hubSkillsError: null })
    try {
      const data = await fetchSkillHub()
      set({ hubSkills: data.skills, hubSkillsLoading: false })
    } catch (err: any) {
      set({ hubSkillsError: err.message || '加载 Skill Hub 失败', hubSkillsLoading: false })
    }
  },

  loadInstalledSkills: async () => {
    const s = get()
    if (s.installedSkillsLoading) return
    set({ installedSkillsLoading: true, installedSkillsError: null })
    try {
      const data = await fetchInstalledSkills()
      set({ installedSkills: data.installed, installedSkillsLoading: false })
    } catch (err: any) {
      set({ installedSkillsError: err.message || '加载已安装 Skill 失败', installedSkillsLoading: false })
    }
  },

  loadCustomSkills: async () => {
    const s = get()
    if (s.customSkillsLoading) return
    set({ customSkillsLoading: true, customSkillsError: null })
    try {
      const data = await fetchCustomSkillsApi()
      set({ customSkills: data.custom, customSkillsLoading: false })
    } catch (err: any) {
      set({ customSkillsError: err.message || '加载自定义 Skill 失败', customSkillsLoading: false })
    }
  },

  loadAllSkills: async () => {
    await Promise.all([get().loadSkillHub(), get().loadInstalledSkills(), get().loadCustomSkills()])
  },

  installSkill: async (skillId) => {
    const s = get()
    if (s.installingSkillIds.has(skillId)) return
    set({ installingSkillIds: new Set([...s.installingSkillIds, skillId]) })
    try {
      await installSkillApi(skillId)
      await get().loadInstalledSkills()
    } catch (err: any) {
      throw err
    } finally {
      set((st) => {
        const next = new Set(st.installingSkillIds)
        next.delete(skillId)
        return { installingSkillIds: next }
      })
    }
  },

  uninstallSkill: async (skillId) => {
    try {
      await uninstallSkillApi(skillId)
      await get().loadInstalledSkills()
    } catch (err: any) {
      throw err
    }
  },

  enableSkill: async (skillId) => {
    await enableSkillApi(skillId)
    await get().loadInstalledSkills()
  },

  disableSkill: async (skillId) => {
    await disableSkillApi(skillId)
    await get().loadInstalledSkills()
  },

  createCustomSkill: async (req) => {
    await createCustomSkillApi(req)
    await Promise.all([get().loadCustomSkills(), get().loadInstalledSkills()])
  },

  deleteCustomSkill: async (skillId) => {
    await deleteCustomSkillApi(skillId)
    await Promise.all([get().loadCustomSkills(), get().loadInstalledSkills()])
  },

  // MCP actions
  loadMcpHub: async () => {
    const s = get()
    if (s.mcpHubLoading) return
    set({ mcpHubLoading: true, mcpHubError: null })
    try {
      const data = await fetchMcpHub()
      set({ mcpHub: data.servers, mcpHubLoading: false })
    } catch (err: any) {
      set({ mcpHubError: err.message || '加载 Hub 失败', mcpHubLoading: false })
    }
  },

  loadInstalledMcps: async () => {
    const s = get()
    if (s.installedLoading) return
    set({ installedLoading: true, installedError: null })
    try {
      const data = await fetchMcpInstalled()
      const overrides = useSettingsStore.getState().settings.mcpOverrides || {}
      const installed = data.installed.map((server) =>
        applyLocalMcpOverride(applyBuiltInMcpDefaults(server), overrides[server.server_id])
      )

      console.log('[loadInstalledMcps] Installed MCP configs:', installed.map((server) => ({
        server_id: server.server_id,
        summary: summarizeMcpConfig(server)
      })))

      // 合并而非全量覆盖：仍在列表里且已是 connected/connecting 的服务器保持原状态（主进程子进程还活着），
      // 避免刷新列表把活着的连接误标成 disconnected（否则直到重启应用都不会再自动重连，工具执行会被误拒）。
      // 新出现的服务器默认 disconnected，已移除的自动丢弃。
      const prev = get().mcpConnectionStatuses
      const statuses: Record<string, McpConnectionStatus> = {}
      for (const item of installed) {
        const prior = prev[item.server_id]
        if (prior && (prior.status === 'connected' || prior.status === 'connecting')) {
          statuses[item.server_id] = prior
        } else {
          statuses[item.server_id] = { server_id: item.server_id, status: 'disconnected', tool_count: 0 }
        }
      }

      set({ installedMcps: installed, mcpConnectionStatuses: statuses, installedLoading: false })
    } catch (err: any) {
      set({ installedError: err.message || '加载已安装列表失败', installedLoading: false })
    }
  },

  connectInstalledMcps: async () => {
    const { installedMcps } = get()
    for (const server of installedMcps) {
      // 已连上/正在连的不重复启动（避免刷新后全量重启导致抖动；单个 server 需要强制重连走 reconnectInstalledMcp）
      const currentStatus = get().mcpConnectionStatuses[server.server_id]?.status
      if (currentStatus === 'connected' || currentStatus === 'connecting') continue

      set((st) => ({
        mcpConnectionStatuses: {
          ...st.mcpConnectionStatuses,
          [server.server_id]: { server_id: server.server_id, status: 'connecting', tool_count: 0 }
        }
      }))

      try {
        console.log(`[connectInstalledMcps] Connecting ${server.server_id} with config:`, JSON.stringify(summarizeMcpConfig({
          transport: server.transport,
          command: server.command,
          args: server.args,
          url: server.url,
          env: server.env,
          headers: {}
        })))
        const tools = await ipcClient.mcp.connect(server.server_id, {
          server_id: server.server_id,
          server_name: server.server_name,
          transport: server.transport as 'stdio' | 'sse' | 'streamable-http',
          command: server.command ?? undefined,
          args: server.args ?? undefined,
          url: server.url ?? undefined,
          env: server.env ?? undefined,
          headers: {}
        })

        set((st) => ({
          mcpDiscoveredTools: { ...st.mcpDiscoveredTools, [server.server_id]: tools },
          mcpConnectionStatuses: {
            ...st.mcpConnectionStatuses,
            [server.server_id]: { server_id: server.server_id, status: 'connected', tool_count: tools.length }
          }
        }))
      } catch (err: any) {
        set((st) => ({
          mcpConnectionStatuses: {
            ...st.mcpConnectionStatuses,
            [server.server_id]: { server_id: server.server_id, status: 'error', tool_count: 0, error: err.message }
          }
        }))
        console.error(`Failed to connect MCP ${server.server_id}:`, err)
      }
    }
  },

  reconnectInstalledMcp: async (serverId) => {
    const server = get().installedMcps.find((s) => s.server_id === serverId)
    if (!server) {
      console.warn(`[reconnectInstalledMcp] server ${serverId} 不在已安装列表，跳过`)
      return
    }
    // 先置 connecting；mcpManager.connect 内部会先断开旧实例再用 installedMcps 当前配置新建，因此适用于 override 改动后强制重连
    set((st) => ({
      mcpConnectionStatuses: {
        ...st.mcpConnectionStatuses,
        [serverId]: { server_id: serverId, status: 'connecting', tool_count: 0 }
      }
    }))
    try {
      console.log(`[reconnectInstalledMcp] Reconnecting ${serverId} with config:`, JSON.stringify(summarizeMcpConfig(server)))
      const tools = await ipcClient.mcp.connect(serverId, {
        server_id: server.server_id,
        server_name: server.server_name,
        transport: server.transport as 'stdio' | 'sse' | 'streamable-http',
        command: server.command ?? undefined,
        args: server.args ?? undefined,
        url: server.url ?? undefined,
        env: server.env ?? undefined,
        headers: {}
      })
      set((st) => ({
        mcpDiscoveredTools: { ...st.mcpDiscoveredTools, [serverId]: tools },
        mcpConnectionStatuses: {
          ...st.mcpConnectionStatuses,
          [serverId]: { server_id: serverId, status: 'connected', tool_count: tools.length }
        }
      }))
    } catch (err: any) {
      set((st) => ({
        mcpConnectionStatuses: {
          ...st.mcpConnectionStatuses,
          [serverId]: { server_id: serverId, status: 'error', tool_count: 0, error: err.message }
        }
      }))
      console.error(`Failed to reconnect MCP ${serverId}:`, err)
      throw err
    }
  },

  loadCustomMcps: async () => {
    const s = get()
    if (s.customLoading) return
    set({ customLoading: true, customError: null })
    try {
      const data = await fetchMcpCustom()
      set({ customMcps: data.custom, customLoading: false })
    } catch (err: any) {
      set({ customError: err.message || '加载自定义 MCP 失败', customLoading: false })
    }
  },

  loadAllMcps: async () => {
    await Promise.all([get().loadMcpHub(), get().loadInstalledMcps(), get().loadCustomMcps()])
  },

  installMcp: async (serverId) => {
    const s = get()
    if (s.installingMcpIds.has(serverId)) return
    set({ installingMcpIds: new Set([...s.installingMcpIds, serverId]) })

    try {
      // 1. Call API to register on server + get config
      const override = useSettingsStore.getState().settings.mcpOverrides?.[serverId]
      const config = applyLocalMcpOverride(
        applyBuiltInMcpDefaults(await installMcpApi(serverId) as McpInstallResponse),
        override
      )

      console.log(`[installMcp] Local override for ${serverId}:`, JSON.stringify(override || {}))
      console.log(`[installMcp] Final config for ${serverId}:`, JSON.stringify(summarizeMcpConfig(config)))

      // 2. Set connecting status
      set((st) => ({
        mcpConnectionStatuses: {
          ...st.mcpConnectionStatuses,
          [serverId]: { server_id: serverId, status: 'connecting', tool_count: 0 }
        }
      }))

      // 3. Connect client-side based on transport type, pass config as-is
      const tools = await ipcClient.mcp.connect(serverId, config)

      // 4. Store discovered tools and update status
      set((st) => ({
        mcpDiscoveredTools: { ...st.mcpDiscoveredTools, [serverId]: tools },
        mcpConnectionStatuses: {
          ...st.mcpConnectionStatuses,
          [serverId]: { server_id: serverId, status: 'connected', tool_count: tools.length }
        }
      }))

      // 5. Report tools to the backend (uses userId from settings)
      const userId = useSettingsStore.getState().settings.userId || '00000000-0000-0000-0000-000000000001'
      if (userId) {
        // console.log(`[installMcp] Reporting ${tools.length} tools for server ${serverId}, user ${userId}`)
        try {
          await reportMcpTools(userId, serverId, tools)
        } catch (err) {
          console.error(`Failed to report tools for ${serverId}:`, err)
        }
      } else {
        console.warn(`[installMcp] No userId configured, skipping tool report`)
      }

      await get().loadInstalledMcps()
    } catch (err: any) {
      set((st) => ({
        mcpConnectionStatuses: {
          ...st.mcpConnectionStatuses,
          [serverId]: { server_id: serverId, status: 'error', tool_count: 0, error: err.message }
        }
      }))
      throw err
    } finally {
      set((st) => {
        const next = new Set(st.installingMcpIds)
        next.delete(serverId)
        return { installingMcpIds: next }
      })
    }
  },

  uninstallMcp: async (serverId) => {
    try {
      // 1. Disconnect client-side only if connected (skip otherwise to avoid blocking)
      const currentStatus = get().mcpConnectionStatuses[serverId]?.status
      if (currentStatus === 'connected' || currentStatus === 'connecting') {
        try {
          await ipcClient.mcp.disconnect(serverId)
        } catch {
          // disconnect error is non-fatal
        }
      }

      // 2. Unregister from server
      await uninstallMcpApi(serverId)

      // 3. Clean up local state
      set((st) => {
        const { [serverId]: _, ...restStatuses } = st.mcpConnectionStatuses
        const { [serverId]: __, ...restTools } = st.mcpDiscoveredTools
        return { mcpConnectionStatuses: restStatuses, mcpDiscoveredTools: restTools }
      })

      await get().loadInstalledMcps()
    } catch (err: any) {
      throw err
    }
  },

  createCustomMcp: async (req) => {
    await createCustomMcpApi(req)
    await get().loadCustomMcps()
    await get().loadInstalledMcps()
    // 新建的自定义 server 刚进入安装列表且尚未连接 → 自动连上（skip 已连接者，只补连新的）
    get().connectInstalledMcps().catch(() => {})
  },

  deleteCustomMcp: async (serverId) => {
    await deleteCustomMcpApi(serverId)
    await get().loadCustomMcps()
    await get().loadInstalledMcps()
  },

  setMcpConnectionStatus: (serverId, partial) => {
    set((st) => ({
      mcpConnectionStatuses: {
        ...st.mcpConnectionStatuses,
        [serverId]: {
          server_id: serverId,
          status: 'disconnected' as const,
          tool_count: 0,
          ...st.mcpConnectionStatuses[serverId],
          ...partial
        }
      }
    }))
  },

  // Build mcp_servers payload for CreateSessionRequest
  getMcpServersForSession: () => {
    const { mcpConnectionStatuses, mcpDiscoveredTools } = get()
    const servers: { server_id: string; server_name: string; enabled_tools?: string[] }[] = []

    for (const [serverId, status] of Object.entries(mcpConnectionStatuses)) {
      if (status.status === 'connected' && status.tool_count > 0) {
        const tools = mcpDiscoveredTools[serverId] || []
        servers.push({
          server_id: serverId,
          server_name: serverId,
          enabled_tools: tools.length > 0 ? tools.map(t => t.name) : undefined
        })
      }
    }

    return servers
  },

  // Report all connected MCP tools for the current user
  reportMcpToolsToSession: async () => {
    const { mcpConnectionStatuses, mcpDiscoveredTools } = get()
    const userId = useSettingsStore.getState().settings.userId
    if (!userId) return

    for (const [serverId, status] of Object.entries(mcpConnectionStatuses)) {
      if (status.status === 'connected') {
        const tools = mcpDiscoveredTools[serverId] || []
        try {
          await reportMcpTools(userId, serverId, tools)
        } catch (err) {
          console.error(`Failed to report tools for ${serverId}:`, err)
        }
      }
    }
  },

  // Memories & Rules actions
  loadMemories: async () => {
    const s = get()
    if (s.memoriesLoading) return
    set({ memoriesLoading: true, memoriesError: null })
    try {
      const data = await fetchMemories()
      set({ memories: data.memories, memoriesLoading: false })
    } catch (err: any) {
      set({ memoriesError: err.message || '加载记忆失败', memoriesLoading: false })
    }
  },

  saveMemoryAction: async (req) => {
    await saveMemory(req)
    await get().loadMemories()
  },

  deleteMemoryAction: async (id) => {
    await deleteMemoryApi(id)
    await get().loadMemories()
  },

  loadRules: async () => {
    const s = get()
    if (s.rulesLoading) return
    set({ rulesLoading: true, rulesError: null })
    try {
      const data = await fetchRules()
      set({ rules: data.rules, rulesLoading: false })
    } catch (err: any) {
      set({ rulesError: err.message || '加载规则失败', rulesLoading: false })
    }
  },

  saveRuleAction: async (req) => {
    await saveRule(req)
    await get().loadRules()
  },

  deleteRuleAction: async (id) => {
    await deleteRuleApi(id)
    await get().loadRules()
  }
}))
