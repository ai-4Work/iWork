import { ipcMain } from 'electron'
import { mcpManager, McpCallResult, McpServerConfig, McpToolDef } from './mcpManager'

export function registerMcpIpc(): void {
  ipcMain.handle('mcp:connect', async (_event, serverId: string, config: McpServerConfig): Promise<McpToolDef[]> => {
    return mcpManager.connect(serverId, config)
  })

  ipcMain.handle('mcp:disconnect', async (_event, serverId: string): Promise<void> => {
    mcpManager.disconnect(serverId)
  })

  ipcMain.handle('mcp:call-tool', async (_event, serverId: string, toolName: string, input: Record<string, unknown>, workspace?: string): Promise<McpCallResult> => {
    return mcpManager.callTool(serverId, toolName, input, workspace)
  })
}
