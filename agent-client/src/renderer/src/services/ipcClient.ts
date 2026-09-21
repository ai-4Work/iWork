import type { Settings } from '../types'

function getAPI() {
  if (!window.electronAPI) {
    return {
      file: {
        glob: async () => [],
        read: async () => '',
        grep: async () => [],
        write: async () => {},
        edit: async () => {},
        exec: async () => ({ stdout: '', stderr: '', exit_code: 0, sandboxed: false }),
        shellEnv: async () => '',
        extractSkill: async () => '',
        extractPlugin: async () => '',
        readSkillMd: async () => '',
        getSkillDir: async () => ''
      },
      workspace: {
        select: async () => null
      },
      settings: {
        save: async () => {},
        load: async () => ({
          apiBaseUrl: '',
          apiKey: '',
          model: 'deepseek-v4-pro',
          workspacePath: '',
          fullAccess: false
        } as Settings)
      },
      auth: {
        save: async () => {},
        load: async () => null,
        clear: async () => {}
      },
      storage: {
        get: async () => null,
        set: async () => {}
      },
      mcp: {
        connect: async () => [],
        disconnect: async () => {},
        callTool: async () => { throw new Error('MCP not available') }
      },
      proxy: {
        setPolicy: async () => {},
        getPort: async () => null,
        takeNetworkApprovals: async () => [],
        resolveNetwork: async () => {},
        resetSession: async () => {},
        onNetworkAsk: () => {}
      }
    }
  }
  return window.electronAPI
}

export const ipcClient = getAPI()
