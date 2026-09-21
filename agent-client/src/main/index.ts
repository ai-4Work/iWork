import { app, BrowserWindow, shell } from 'electron'
import { join } from 'path'
import { registerFileOps } from './fileOps'
import { registerSettings } from './settings'
import { registerAuth } from './auth'
import { registerStorage } from './storage'
import { registerMcpIpc } from './mcpIpc'
import { registerProxyIpc } from './proxy'

// 全局持有主窗口，供代理向 renderer 推送网络审批弹窗（proxy:network-ask）
let mainWindow: BrowserWindow | null = null

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    show: false,
    titleBarStyle: 'default',
    backgroundColor: '#181825',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
      nodeIntegration: false,
      contextIsolation: true
    }
  })

  // 开发环境
  mainWindow.webContents.openDevTools()

  mainWindow.on('ready-to-show', () => {
    mainWindow.show()
  })

  mainWindow.on('closed', () => {
    mainWindow = null
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  if (!app.isPackaged && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

app.whenReady().then(() => {
  app.setAppUserModelId('com.agent.electron-app')

  const { get: getSettings } = registerSettings()
  // 密钥链不可用时生产环境会在这一步拒绝启动（doc 18-10.3）
  registerAuth()
  registerStorage()
  registerFileOps(() => getSettings().workspacePath)
  registerMcpIpc()
  // 代理随 app 启动即 listen(0)，保证任何 spawn 前端口就绪（先起代理再 spawn）
  registerProxyIpc(() => mainWindow)

  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})
