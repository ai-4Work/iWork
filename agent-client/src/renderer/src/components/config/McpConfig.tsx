import { useEffect, useState } from 'react'
import { useConfigStore } from '../../stores/configStore'
import { useSettingsStore } from '../../stores/settingsStore'
import { useCardTooltip } from './useCardTooltip'
import type { CreateCustomMcpRequest, McpConnectionStatus, McpLocalOverride } from '../../types'

function Spinner() {
  return (
    <svg className="w-4 h-4 animate-spin text-[#94a3b8]" viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeDasharray="28" strokeDashoffset="8" />
    </svg>
  )
}

const connectionStatusColors: Record<McpConnectionStatus['status'], string> = {
  disconnected: 'bg-[#f1f5f9] text-[#94a3b8]',
  connecting: 'bg-[#fffbeb] text-[#b45309]',
  connected: 'bg-[#f0fdf4] text-[#047857]',
  error: 'bg-[#fef2f2] text-[#dc2626]'
}

const connectionStatusLabel: Record<McpConnectionStatus['status'], string> = {
  disconnected: 'Disconnected',
  connecting: 'Connecting',
  connected: 'Connected',
  error: 'Error'
}

function parseLinesToRecord(text: string): Record<string, string> {
  const entries = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const index = line.indexOf('=')
      if (index < 0) return null
      const key = line.slice(0, index).trim()
      const value = line.slice(index + 1).trim()
      return key ? [key, value] as const : null
    })
    .filter((entry): entry is readonly [string, string] => entry !== null)

  return Object.fromEntries(entries)
}

function recordToLines(record?: Record<string, string>): string {
  if (!record) return ''
  return Object.entries(record)
    .map(([key, value]) => `${key}=${value}`)
    .join('\n')
}

export function McpConfig() {
  const [tab, setTab] = useState<'hub' | 'installed' | 'custom'>('hub')
  const [search, setSearch] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [editingOverride, setEditingOverride] = useState<{ serverId: string; serverName: string } | null>(null)
  const { hoverProps, tooltip } = useCardTooltip()

  const {
    mcpHub, mcpHubLoading, mcpHubError,
    installedMcps, installedLoading, installedError,
    customMcps, customLoading, customError,
    installingMcpIds, mcpConnectionStatuses,
    loadAllMcps,
    installMcp, uninstallMcp, createCustomMcp, deleteCustomMcp
  } = useConfigStore()
  const { settings, save } = useSettingsStore()

  useEffect(() => {
    loadAllMcps()
  }, [])

  const installedIds = new Set(installedMcps.map((server) => server.server_id))
  const filteredHub = mcpHub.filter((server) =>
    !search
    || server.server_name.toLowerCase().includes(search.toLowerCase())
    || server.description.toLowerCase().includes(search.toLowerCase())
    || server.category.toLowerCase().includes(search.toLowerCase())
  )

  const mcpOverrides = settings.mcpOverrides || {}

  const handleInstall = async (serverId: string) => {
    setActionError(null)
    try {
      await installMcp(serverId)
    } catch (err: any) {
      setActionError(err.message || 'Install failed')
    }
  }

  const handleUninstall = async (serverId: string) => {
    setActionError(null)
    try {
      await uninstallMcp(serverId)
    } catch (err: any) {
      setActionError(err.message || 'Uninstall failed')
    }
  }

  const handleDeleteCustom = async (serverId: string) => {
    setActionError(null)
    try {
      await deleteCustomMcp(serverId)
    } catch (err: any) {
      setActionError(err.message || 'Delete failed')
    }
  }

  const handleSaveOverride = async (serverId: string, override: McpLocalOverride) => {
    const nextOverrides = { ...mcpOverrides }
    const isEmpty = !override.command && !override.url
      && (!override.args || override.args.length === 0)
      && (!override.env || Object.keys(override.env).length === 0)
      && (!override.headers || Object.keys(override.headers).length === 0)

    if (isEmpty) {
      delete nextOverrides[serverId]
    } else {
      nextOverrides[serverId] = override
    }

    await save({ mcpOverrides: nextOverrides })
    await loadAllMcps()
    // override 改动只影响下次连接 → 强制重连该 server，让新配置立即生效（loadAllMcps 会保留已连接状态，
    // 若不主动重连，仍会以旧配置的常驻子进程运行）
    try {
      await useConfigStore.getState().reconnectInstalledMcp(serverId)
    } catch (err: any) {
      setActionError(err?.message || '重连 MCP 失败')
    }
    setEditingOverride(null)
  }

  return (
    <div>
      <div className="flex items-center gap-2 mb-5">
        <div className="flex gap-0.5 bg-[#f1f5f9] rounded-md p-0.5">
          {(['hub', 'installed', 'custom'] as const).map((currentTab) => (
            <button
              key={currentTab}
              onClick={() => setTab(currentTab)}
              className={`px-[18px] py-[7px] rounded-[5px] text-[13px] font-medium transition-colors ${
                tab === currentTab ? 'bg-white text-[#0f172a] shadow-sm' : 'text-[#64748b] hover:text-[#0f172a]'
              }`}
            >
              {currentTab === 'hub' ? 'Hub' : currentTab === 'installed' ? 'Installed' : 'Custom'}
            </button>
          ))}
        </div>

        {tab === 'hub' && (
          <div className="relative ml-auto">
            <svg className="w-4 h-4 absolute left-2.5 top-1/2 -translate-y-1/2 text-[#94a3b8]" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <circle cx="7" cy="7" r="4.5" />
              <line x1="10.5" y1="10.5" x2="14" y2="14" />
            </svg>
            <input
              className="w-[220px] py-[6px] px-2.5 pl-[30px] border border-[#e2e8f0] rounded-md text-[13px] bg-white outline-none focus:border-[#a7f3d0]"
              placeholder="Search MCP..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        )}
      </div>

      {actionError && (
        <div className="mb-3 px-3 py-2 bg-[#fef2f2] border border-[#fecaca] rounded-md text-[13px] text-[#dc2626] flex items-center gap-2">
          <span>{actionError}</span>
          <button onClick={() => setActionError(null)} className="ml-auto text-[#94a3b8] hover:text-[#0f172a] cursor-pointer">&times;</button>
        </div>
      )}

      {tab === 'hub' && (
        <>
          {mcpHubLoading && <div className="flex items-center gap-2 text-[#94a3b8] text-[13px] py-4"><Spinner /> Loading MCP hub...</div>}
          {mcpHubError && <div className="text-[#dc2626] text-[13px] py-3">{mcpHubError}</div>}
          {!mcpHubLoading && !mcpHubError && (
            <div className="grid grid-cols-4 gap-2.5">
              {filteredHub.map((server) => {
                const installed = installedIds.has(server.server_id)
                const installing = installingMcpIds.has(server.server_id)

                return (
                  <div key={server.server_id} {...hoverProps(server.server_name, server.description)} className="bg-white border border-[#e2e8f0] rounded-[10px] p-[12px] flex gap-2.5 hover:border-[#cbd5e1] hover:shadow-sm transition-all">
                    <div className="w-[32px] h-[32px] rounded-md bg-[#f1f5f9] flex items-center justify-center flex-shrink-0 text-base">{server.icon}</div>
                    <div className="flex-1 min-w-0">
                      <div data-tip-name className="font-semibold text-[13px] text-[#0f172a] truncate">{server.server_name}</div>
                      <div data-tip-desc className="text-xs text-[#94a3b8] mt-0.5 line-clamp-2">{server.description}</div>
                      <div className="flex gap-1.5 mt-1.5 flex-wrap">
                        <span className="inline-block text-[10px] font-medium px-1.5 py-0.5 rounded whitespace-nowrap bg-[#fffbeb] text-[#b45309]">{server.category}</span>
                        <span className="inline-block text-[10px] font-medium px-1.5 py-0.5 rounded whitespace-nowrap bg-[#f1f5f9] text-[#64748b]">{server.transport}</span>
                      </div>
                    </div>
                    <div className="flex-shrink-0 self-center flex flex-col items-stretch">
                      {installing ? (
                        <span className="inline-flex items-center gap-1 px-3.5 py-1.5 rounded-md text-xs font-medium border border-[#e2e8f0] text-[#94a3b8] bg-[#f1f5f9]">
                          <Spinner /> 安装中
                        </span>
                      ) : (
                        <button
                          onClick={() => installed ? handleUninstall(server.server_id) : handleInstall(server.server_id)}
                          className={`px-3.5 py-1.5 rounded-md text-xs font-medium transition-colors border cursor-pointer ${
                            installed
                              ? 'border-[#e2e8f0] text-[#94a3b8] bg-[#f1f5f9]'
                              : 'border-[#a7f3d0] text-[#047857] bg-[#f0fdf4] hover:bg-[#a7f3d0]'
                          }`}
                        >
                          {installed ? '已安装' : '安装'}
                        </button>
                      )}
                      <button
                        onClick={() => setEditingOverride({ serverId: server.server_id, serverName: server.server_name })}
                        className="mt-2 px-3.5 py-1.5 rounded-md text-xs font-medium border border-[#e2e8f0] text-[#64748b] bg-white hover:border-[#a7f3d0] hover:text-[#047857] cursor-pointer"
                      >
                        Local config
                      </button>
                    </div>
                  </div>
                )
              })}
              {filteredHub.length === 0 && <div className="text-[#94a3b8] text-[13px] py-3">No matching MCP servers</div>}
            </div>
          )}
        </>
      )}

      {tab === 'installed' && (
        <>
          {installedLoading && <div className="flex items-center gap-2 text-[#94a3b8] text-[13px] py-4"><Spinner /> Loading installed MCPs...</div>}
          {installedError && <div className="text-[#dc2626] text-[13px] py-3">{installedError}</div>}
          {!installedLoading && !installedError && (
            <div className="grid grid-cols-4 gap-2.5">
              {installedMcps.map((server) => {
                const connStatus = mcpConnectionStatuses[server.server_id]
                const hasOverride = Boolean(mcpOverrides[server.server_id])

                return (
                  <div key={server.server_id} {...hoverProps(server.server_name, server.description)} className="bg-white border border-l-[3px] border-l-[#a7f3d0] border-[#e2e8f0] rounded-[10px] p-[12px] flex gap-2.5">
                    <div className="w-[32px] h-[32px] rounded-md bg-[#f1f5f9] flex items-center justify-center flex-shrink-0 text-base">{server.icon}</div>
                    <div className="flex-1 min-w-0">
                      <div data-tip-name className="font-semibold text-[13px] text-[#0f172a] truncate">{server.server_name}</div>
                      <div data-tip-desc className="text-xs text-[#94a3b8] mt-0.5 line-clamp-2">{server.description}</div>
                      <div className="flex gap-1.5 mt-1.5 flex-wrap items-center">
                        <span className="inline-block text-[10px] font-medium px-1.5 py-0.5 rounded whitespace-nowrap bg-[#fffbeb] text-[#b45309]">{server.category}</span>
                        <span className="inline-block text-[10px] font-medium px-1.5 py-0.5 rounded whitespace-nowrap bg-[#f1f5f9] text-[#64748b]">{server.transport}</span>
                        {connStatus && (
                          <span className={`inline-block text-[10px] font-medium px-1.5 py-0.5 rounded whitespace-nowrap ${connectionStatusColors[connStatus.status]}`}>
                            {connectionStatusLabel[connStatus.status]}
                            {connStatus.tool_count > 0 && ` · ${connStatus.tool_count} tools`}
                          </span>
                        )}
                        {hasOverride && (
                          <span className="inline-block text-[10px] font-medium px-1.5 py-0.5 rounded whitespace-nowrap bg-[#eff6ff] text-[#1d4ed8]">
                            local override
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="flex-shrink-0 self-center flex flex-col items-stretch">
                      <button onClick={() => handleUninstall(server.server_id)} className="px-3.5 py-1.5 rounded-md text-xs font-medium border border-[#e2e8f0] text-[#94a3b8] bg-[#f1f5f9] cursor-pointer">Uninstall</button>
                      <button
                        onClick={() => setEditingOverride({ serverId: server.server_id, serverName: server.server_name })}
                        className="mt-2 px-3.5 py-1.5 rounded-md text-xs font-medium border border-[#e2e8f0] text-[#64748b] bg-white hover:border-[#a7f3d0] hover:text-[#047857] cursor-pointer"
                      >
                        配置
                      </button>
                    </div>
                  </div>
                )
              })}
              {installedMcps.length === 0 && <div className="text-[#94a3b8] text-[13px] py-3">No installed MCPs yet</div>}
            </div>
          )}
        </>
      )}

      {tab === 'custom' && (
        <>
          <div className="flex gap-2 mb-5">
            <button
              onClick={() => setShowCreate(true)}
              className="flex items-center gap-1.5 px-4 py-2 border border-dashed border-[#e2e8f0] rounded-md text-[13px] text-[#64748b] hover:border-[#a7f3d0] hover:text-[#047857] hover:bg-[#f0fdf4] transition-colors cursor-pointer bg-white"
            >
              <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M8 3v10M3 8h10" /></svg>
              Add custom MCP
            </button>
          </div>

          {customLoading && <div className="flex items-center gap-2 text-[#94a3b8] text-[13px] py-4"><Spinner /> Loading custom MCPs...</div>}
          {customError && <div className="text-[#dc2626] text-[13px] py-3">{customError}</div>}
          {!customLoading && !customError && (
            <div className="grid grid-cols-4 gap-2.5">
              {customMcps.map((server) => {
                const hasOverride = Boolean(mcpOverrides[server.server_id])

                return (
                  <div key={server.server_id} {...hoverProps(server.server_name, server.description)} className="bg-white border border-l-[3px] border-l-[#a7f3d0] border-[#e2e8f0] rounded-[10px] p-[12px] flex gap-2.5">
                    <div className="w-[32px] h-[32px] rounded-md bg-[#f1f5f9] flex items-center justify-center flex-shrink-0 text-base">{server.icon}</div>
                    <div className="flex-1 min-w-0">
                      <div data-tip-name className="font-semibold text-[13px] text-[#0f172a] truncate">{server.server_name}</div>
                      <div data-tip-desc className="text-xs text-[#94a3b8] mt-0.5 line-clamp-2">{server.description}</div>
                      <div className="flex gap-1.5 mt-1.5 flex-wrap">
                        <span className="inline-block text-[10px] font-medium px-1.5 py-0.5 rounded whitespace-nowrap bg-[#f0fdf4] text-[#047857]">custom</span>
                        <span className="inline-block text-[10px] font-medium px-1.5 py-0.5 rounded whitespace-nowrap bg-[#f1f5f9] text-[#64748b]">{server.transport}</span>
                        {hasOverride && (
                          <span className="inline-block text-[10px] font-medium px-1.5 py-0.5 rounded whitespace-nowrap bg-[#eff6ff] text-[#1d4ed8]">
                            local override
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="flex-shrink-0 self-center flex flex-col items-stretch">
                      <button onClick={() => handleDeleteCustom(server.server_id)} className="px-3.5 py-1.5 rounded-md text-xs font-medium border border-[#e2e8f0] text-[#94a3b8] bg-[#f1f5f9] cursor-pointer">Delete</button>
                      <button
                        onClick={() => setEditingOverride({ serverId: server.server_id, serverName: server.server_name })}
                        className="mt-2 px-3.5 py-1.5 rounded-md text-xs font-medium border border-[#e2e8f0] text-[#64748b] bg-white hover:border-[#a7f3d0] hover:text-[#047857] cursor-pointer"
                      >
                        配置
                      </button>
                    </div>
                  </div>
                )
              })}
              {customMcps.length === 0 && <div className="text-[#94a3b8] text-[13px] py-3">No custom MCPs yet</div>}
            </div>
          )}

          {showCreate && (
            <CreateMcpModal
              onClose={() => setShowCreate(false)}
              onSubmit={async (req) => {
                setActionError(null)
                try {
                  await createCustomMcp(req)
                  setShowCreate(false)
                } catch (err: any) {
                  setActionError(err.message || 'Create failed')
                }
              }}
            />
          )}
        </>
      )}

      {editingOverride && (
        <McpOverrideModal
          serverId={editingOverride.serverId}
          serverName={editingOverride.serverName}
          initialValue={mcpOverrides[editingOverride.serverId]}
          onClose={() => setEditingOverride(null)}
          onSubmit={handleSaveOverride}
        />
      )}

      {tooltip}
    </div>
  )
}

function McpOverrideModal({
  serverId,
  serverName,
  initialValue,
  onClose,
  onSubmit
}: {
  serverId: string
  serverName: string
  initialValue?: McpLocalOverride
  onClose: () => void
  onSubmit: (serverId: string, override: McpLocalOverride) => Promise<void>
}) {
  const [command, setCommand] = useState(initialValue?.command || '')
  const [args, setArgs] = useState(initialValue?.args?.join(', ') || '')
  const [url, setUrl] = useState(initialValue?.url || '')
  const [envText, setEnvText] = useState(recordToLines(initialValue?.env))
  const [headersText, setHeadersText] = useState(recordToLines(initialValue?.headers))
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async () => {
    setSubmitting(true)
    try {
      await onSubmit(serverId, {
        command: command.trim() || undefined,
        args: args.trim() ? args.split(',').map((item) => item.trim()).filter(Boolean) : undefined,
        url: url.trim() || undefined,
        env: envText.trim() ? parseLinesToRecord(envText) : undefined,
        headers: headersText.trim() ? parseLinesToRecord(headersText) : undefined
      })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-lg p-6 w-[520px] max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="text-[15px] font-semibold text-[#0f172a]">Local MCP config</h3>
            <div className="text-[12px] text-[#94a3b8] mt-1">{serverName} · {serverId}</div>
          </div>
          <button onClick={onClose} className="text-[#94a3b8] hover:text-[#0f172a] cursor-pointer">
            <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 4l8 8M12 4l-8 8" /></svg>
          </button>
        </div>

        <div className="mb-4 rounded-md border border-[#e2e8f0] bg-[#f8fafc] px-3 py-2 text-[12px] text-[#64748b]">
          This override is stored locally and merged into the MCP config before install/connect. Use it when the backend does not provide a private host, API key, env vars, or request headers.
        </div>

        <div className="space-y-3.5">
          <div>
            <label className="block text-[12px] font-medium text-[#64748b] mb-1">Command</label>
            <input className="w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0]" placeholder="npx" value={command} onChange={(e) => setCommand(e.target.value)} />
          </div>
          <div>
            <label className="block text-[12px] font-medium text-[#64748b] mb-1">Args (comma separated)</label>
            <input className="w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0]" placeholder="-y, hefeng-mcp-server, --apiKey=xxx" value={args} onChange={(e) => setArgs(e.target.value)} />
          </div>
          <div>
            <label className="block text-[12px] font-medium text-[#64748b] mb-1">URL</label>
            <input className="w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0]" placeholder="https://mcp.example.com/mcp" value={url} onChange={(e) => setUrl(e.target.value)} />
          </div>
          <div>
            <label className="block text-[12px] font-medium text-[#64748b] mb-1">Env (one per line: KEY=VALUE)</label>
            <textarea className="w-full min-h-[100px] px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0] resize-y" placeholder={'HEFENG_API_KEY=xxx\nHEFENG_API_URL=https://your-host/v7'} value={envText} onChange={(e) => setEnvText(e.target.value)} />
          </div>
          <div>
            <label className="block text-[12px] font-medium text-[#64748b] mb-1">Headers (one per line: KEY=VALUE)</label>
            <textarea className="w-full min-h-[88px] px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0] resize-y" placeholder={'Authorization=Bearer xxx\nX-Api-Key=yyy'} value={headersText} onChange={(e) => setHeadersText(e.target.value)} />
          </div>
        </div>

        <div className="flex justify-end gap-2 mt-5">
          <button onClick={onClose} className="px-4 py-1.5 rounded-md text-[13px] border border-[#e2e8f0] text-[#64748b] cursor-pointer">Close</button>
          <button onClick={handleSubmit} disabled={submitting} className={`px-4 py-1.5 rounded-md text-[13px] font-medium cursor-pointer border ${
            !submitting
              ? 'border-[#a7f3d0] text-[#047857] bg-[#f0fdf4] hover:bg-[#a7f3d0]'
              : 'border-[#e2e8f0] text-[#cbd5e1] bg-[#f8fafc] cursor-not-allowed'
          }`}>
            {submitting ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}

function CreateMcpModal({ onClose, onSubmit }: {
  onClose: () => void
  onSubmit: (req: CreateCustomMcpRequest) => Promise<void>
}) {
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [transport, setTransport] = useState<string>('stdio')
  const [command, setCommand] = useState('')
  const [args, setArgs] = useState('')
  const [url, setUrl] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async () => {
    if (!name.trim()) return
    setSubmitting(true)
    try {
      const req: CreateCustomMcpRequest = {
        server_name: name.trim(),
        description: desc.trim() || undefined,
        transport: transport || undefined,
        command: transport === 'stdio' ? (command.trim() || null) : null,
        args: transport === 'stdio' && args.trim() ? args.split(',').map((item) => item.trim()).filter(Boolean) : [],
        url: transport !== 'stdio' ? (url.trim() || null) : null
      }
      await onSubmit(req)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-lg p-6 w-[420px] max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-[15px] font-semibold text-[#0f172a]">Add custom MCP</h3>
          <button onClick={onClose} className="text-[#94a3b8] hover:text-[#0f172a] cursor-pointer">
            <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 4l8 8M12 4l-8 8" /></svg>
          </button>
        </div>

        <div className="space-y-3.5">
          <div>
            <label className="block text-[12px] font-medium text-[#64748b] mb-1">Name *</label>
            <input className="w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0]" placeholder="My MCP" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div>
            <label className="block text-[12px] font-medium text-[#64748b] mb-1">Description</label>
            <input className="w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0]" placeholder="What this MCP is for" value={desc} onChange={(e) => setDesc(e.target.value)} />
          </div>
          <div>
            <label className="block text-[12px] font-medium text-[#64748b] mb-1">Transport</label>
            <select className="w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0] bg-white" value={transport} onChange={(e) => setTransport(e.target.value)}>
              <option value="stdio">stdio</option>
              <option value="sse">SSE</option>
              <option value="streamable-http">Streamable HTTP</option>
            </select>
          </div>

          {transport === 'stdio' && (
            <>
              <div>
                <label className="block text-[12px] font-medium text-[#64748b] mb-1">Command</label>
                <input className="w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0]" placeholder="npx" value={command} onChange={(e) => setCommand(e.target.value)} />
              </div>
              <div>
                <label className="block text-[12px] font-medium text-[#64748b] mb-1">Args (comma separated)</label>
                <input className="w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0]" placeholder="-y, @company/mcp-server" value={args} onChange={(e) => setArgs(e.target.value)} />
              </div>
            </>
          )}

          {transport !== 'stdio' && (
            <div>
              <label className="block text-[12px] font-medium text-[#64748b] mb-1">URL</label>
              <input className="w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0]" placeholder="https://mcp.example.com/mcp" value={url} onChange={(e) => setUrl(e.target.value)} />
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 mt-5">
          <button onClick={onClose} className="px-4 py-1.5 rounded-md text-[13px] border border-[#e2e8f0] text-[#64748b] cursor-pointer">Cancel</button>
          <button onClick={handleSubmit} disabled={!name.trim() || submitting} className={`px-4 py-1.5 rounded-md text-[13px] font-medium cursor-pointer border ${
            name.trim() && !submitting
              ? 'border-[#a7f3d0] text-[#047857] bg-[#f0fdf4] hover:bg-[#a7f3d0]'
              : 'border-[#e2e8f0] text-[#cbd5e1] bg-[#f8fafc] cursor-not-allowed'
          }`}>
            {submitting ? 'Creating...' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  )
}
