import { contextBridge, ipcRenderer } from 'electron'

// 单一订阅者：contextBridge 包装后的回调引用不好做 removeListener，改用最新回调转发
let networkAskCb: ((d: { host: string; protocol: string }) => void) | null = null
ipcRenderer.on('proxy:network-ask', (_event, d: { host: string; protocol: string }) => {
  networkAskCb?.(d)
})

const api = {
  file: {
    glob: (pattern: string) => ipcRenderer.invoke('file:glob', pattern),
    read: (path: string) => ipcRenderer.invoke('file:read', path),
    grep: (pattern: string, dirPath: string) => ipcRenderer.invoke('file:grep', pattern, dirPath),
    write: (path: string, content: string) => ipcRenderer.invoke('file:write', path, content),
    edit: (path: string, oldStr: string, newStr: string) =>
      ipcRenderer.invoke('file:edit', path, oldStr, newStr),
    exec: (command: string, timeoutMs?: number) =>
      ipcRenderer.invoke('file:exec', command, timeoutMs),
    shellEnv: () => ipcRenderer.invoke('shell:env'),
    extractSkill: (base64: string, skillName: string) =>
      ipcRenderer.invoke('file:extractSkill', base64, skillName),
    extractPlugin: (base64: string, zipName: string) =>
      ipcRenderer.invoke('file:extractPlugin', base64, zipName),
    readSkillMd: (skillId: string) =>
      ipcRenderer.invoke('file:readSkillMd', skillId),
    getSkillDir: (skillId: string) =>
      ipcRenderer.invoke('file:getSkillDir', skillId)
  },
  workspace: {
    select: () => ipcRenderer.invoke('workspace:select')
  },
  settings: {
    save: (settings: unknown) => ipcRenderer.invoke('settings:save', settings),
    load: () => ipcRenderer.invoke('settings:load')
  },
  storage: {
    get: (ns: string, key: string) => ipcRenderer.invoke('storage:get', ns, key),
    set: (ns: string, key: string, value: unknown) => ipcRenderer.invoke('storage:set', ns, key, value)
  },
  mcp: {
    connect: (serverId: string, config: unknown) => ipcRenderer.invoke('mcp:connect', serverId, config),
    disconnect: (serverId: string) => ipcRenderer.invoke('mcp:disconnect', serverId),
    callTool: (serverId: string, toolName: string, input: unknown, workspace?: string) =>
      ipcRenderer.invoke('mcp:call-tool', serverId, toolName, input, workspace)
  },
  proxy: {
    setPolicy: (packet: unknown) => ipcRenderer.invoke('proxy:set-policy', packet),
    getPort: () => ipcRenderer.invoke('proxy:get-port'),
    takeNetworkApprovals: () => ipcRenderer.invoke('proxy:take-network-approvals'),
    resolveNetwork: (host: string, protocol: string, approved: boolean) =>
      ipcRenderer.invoke('proxy:resolve-network', host, protocol, approved),
    resetSession: () => ipcRenderer.invoke('proxy:reset-session'),
    onNetworkAsk: (cb: (d: { host: string; protocol: string }) => void) => {
      networkAskCb = cb
    }
  }
}

contextBridge.exposeInMainWorld('electronAPI', api)
