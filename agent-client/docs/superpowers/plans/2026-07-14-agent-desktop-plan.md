# Agent Desktop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an Electron + React + TypeScript desktop agent app with multi-session chat, Monaco editor input, local file tools, SSE streaming, and NSIS packaging.

**Architecture:** electron-vite scaffold with `src/main/` (Electron main process + IPC), `src/preload/` (contextBridge), `src/renderer/` (React app). Zustand stores manage state; services layer handles SSE and IPC; shadcn/ui + Tailwind provide the UI.

**Tech Stack:** electron-vite, React 18, TypeScript 5, Zustand 4, Tailwind CSS 3, shadcn/ui (Radix primitives), Monaco Editor, electron-store, electron-builder

## Global Constraints

- Electron 28+, Windows 11 target
- Renderer must NOT use nodeIntegration; all Node.js access via preload contextBridge
- Default dark theme
- Monaco Editor: single-line mode, Shift+Enter for newline
- SSE parsing: OpenAI-compatible `data:` format with `[DONE]` sentinel
- File tools: read ops auto-execute, write ops require user confirmation
- @ file references NOT in scope for v1

---

### Task 1: Project Scaffolding

**Files:**
- Create: `package.json`
- Create: `electron.vite.config.ts`
- Create: `tsconfig.json`
- Create: `tsconfig.node.json`
- Create: `tsconfig.web.json`
- Create: `tailwind.config.js`
- Create: `postcss.config.js`
- Create: `src/renderer/index.html`

- [ ] **Step 1: Initialize package.json**

```bash
cd d:/MyProject/project_money/agent/agent-electron-app
```

Create `package.json`:

```json
{
  "name": "agent-desktop",
  "version": "1.0.0",
  "description": "Agent Desktop - AI-powered coding assistant",
  "main": "./out/main/index.js",
  "scripts": {
    "dev": "electron-vite dev",
    "build": "electron-vite build",
    "preview": "electron-vite preview",
    "package": "electron-vite build && electron-builder --win"
  },
  "dependencies": {
    "electron-store": "^8.1.0",
    "zustand": "^4.5.0",
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-markdown": "^9.0.1",
    "@monaco-editor/react": "^4.6.0",
    "lucide-react": "^0.312.0",
    "clsx": "^2.1.0",
    "tailwind-merge": "^2.2.0",
    "class-variance-authority": "^0.7.0",
    "@radix-ui/react-dialog": "^1.0.5",
    "@radix-ui/react-switch": "^1.0.3",
    "@radix-ui/react-tooltip": "^1.0.7",
    "@radix-ui/react-scroll-area": "^1.0.5",
    "@radix-ui/react-tabs": "^1.0.4"
  },
  "devDependencies": {
    "@types/react": "^18.2.48",
    "@types/react-dom": "^18.2.18",
    "@vitejs/plugin-react": "^4.2.1",
    "autoprefixer": "^10.4.17",
    "electron": "^28.2.0",
    "electron-builder": "^24.9.1",
    "electron-vite": "^2.0.0",
    "postcss": "^8.4.33",
    "tailwindcss": "^3.4.1",
    "typescript": "^5.3.3"
  }
}
```

- [ ] **Step 2: Install dependencies**

```bash
npm install
```

- [ ] **Step 3: Create electron.vite.config.ts**

```typescript
import { resolve } from 'path'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()]
  },
  preload: {
    plugins: [externalizeDepsPlugin()]
  },
  renderer: {
    resolve: {
      alias: {
        '@': resolve('src/renderer/src')
      }
    },
    plugins: [react()]
  }
})
```

- [ ] **Step 4: Create tsconfig files**

`tsconfig.json`:

```json
{
  "files": [],
  "references": [
    { "path": "./tsconfig.node.json" },
    { "path": "./tsconfig.web.json" }
  ]
}
```

`tsconfig.node.json`:

```json
{
  "compilerOptions": {
    "composite": true,
    "module": "ESNext",
    "moduleResolution": "Node",
    "allowSyntheticDefaultImports": true,
    "esModuleInterop": true,
    "strict": true,
    "skipLibCheck": true,
    "outDir": "./out",
    "sourceMap": true,
    "target": "ESNext",
    "lib": ["ESNext"]
  },
  "include": ["src/main/**/*", "src/preload/**/*", "electron.vite.config.ts"]
}
```

`tsconfig.web.json`:

```json
{
  "compilerOptions": {
    "composite": true,
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "allowSyntheticDefaultImports": true,
    "esModuleInterop": true,
    "jsx": "react-jsx",
    "strict": true,
    "skipLibCheck": true,
    "outDir": "./out",
    "sourceMap": true,
    "target": "ESNext",
    "lib": ["ESNext", "DOM", "DOM.Iterable"],
    "baseUrl": ".",
    "paths": {
      "@/*": ["src/renderer/src/*"]
    }
  },
  "include": ["src/renderer/src/**/*"]
}
```

- [ ] **Step 5: Create Tailwind + PostCSS config**

`tailwind.config.js`:

```js
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./src/renderer/src/**/*.{ts,tsx}', './src/renderer/index.html'],
  theme: {
    extend: {
      colors: {
        sidebar: {
          bg: '#1e1e2e',
          hover: '#2a2a3e',
          active: '#363650',
          border: '#2e2e42'
        },
        chat: {
          bg: '#181825',
          bubble: {
            user: '#3b3b5c',
            assistant: '#1e1e2e'
          }
        },
        accent: {
          DEFAULT: '#7c7cf8',
          hover: '#6a6ae8'
        }
      }
    }
  },
  plugins: []
}
```

`postcss.config.js`:

```js
module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {}
  }
}
```

- [ ] **Step 6: Create renderer index.html**

`src/renderer/index.html`:

```html
<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Agent Desktop</title>
  </head>
  <body class="bg-chat-bg text-gray-100 overflow-hidden">
    <div id="root"></div>
    <script type="module" src="./src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: scaffold electron-vite project with Tailwind and dependencies"
```

---

### Task 2: Type Definitions

**Files:**
- Create: `src/renderer/src/types/index.ts`

**Produces:**
- `Conversation`, `Message`, `ToolCall`, `Settings`, `Command` types
- `ElectronAPI` interface for window.electronAPI

- [ ] **Step 1: Create types file**

`src/renderer/src/types/index.ts`:

```typescript
export interface Conversation {
  id: string
  title: string
  createdAt: number
  updatedAt: number
  messages: Message[]
}

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  toolCalls?: ToolCall[]
  isStreaming?: boolean
  timestamp: number
}

export type ToolType = 'glob' | 'read' | 'grep' | 'write' | 'edit'

export interface ToolCall {
  id: string
  type: ToolType
  args: Record<string, string>
  status: 'pending' | 'confirming' | 'executing' | 'done' | 'error'
  result?: string
  name?: string
}

export interface Settings {
  apiBaseUrl: string
  apiKey: string
  model: string
  workspacePath: string
  fullAccess: boolean
}

export const DEFAULT_SETTINGS: Settings = {
  apiBaseUrl: 'http://localhost:8080',
  apiKey: '',
  model: 'gpt-4',
  workspacePath: '',
  fullAccess: false
}

export interface Command {
  id: string
  trigger: string
  label: string
  description: string
}

export interface ElectronAPI {
  file: {
    glob: (pattern: string) => Promise<string[]>
    read: (path: string) => Promise<string>
    grep: (pattern: string, path: string) => Promise<string[]>
    write: (path: string, content: string) => Promise<void>
    edit: (path: string, oldStr: string, newStr: string) => Promise<void>
  }
  workspace: {
    select: () => Promise<string | null>
  }
  settings: {
    save: (settings: Settings) => Promise<void>
    load: () => Promise<Settings>
  }
}

declare global {
  interface Window {
    electronAPI: ElectronAPI
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add src/renderer/src/types/index.ts
git commit -m "feat: add core type definitions"
```

---

### Task 3: Electron Main Process

**Files:**
- Create: `src/main/index.ts`
- Create: `src/main/fileOps.ts`
- Create: `src/main/settings.ts`

**Interfaces:**
- Produces: Main process registers IPC handlers for `file:*`, `workspace:*`, `settings:*` channels
- Depends on: Types from Task 2 (Settings interface shape)

- [ ] **Step 1: Create file operations handler**

`src/main/fileOps.ts`:

```typescript
import { ipcMain, dialog } from 'electron'
import { readFile, writeFile, readdir, stat } from 'fs/promises'
import { join, resolve, dirname } from 'path'

function globToRegex(pattern: string): RegExp {
  const escaped = pattern
    .replace(/[.+^${}()|[\]\\]/g, '\\$&')
    .replace(/\*\*\//g, '<<<DOUBLESTAR>>>')
    .replace(/\*/g, '[^/\\\\]*')
    .replace(/<<<DOUBLESTAR>>>/g, '(.*/)?')
  return new RegExp(`^${escaped}$`)
}

async function globFiles(basePath: string, pattern: string): Promise<string[]> {
  const results: string[] = []
  const regex = globToRegex(pattern)

  async function walk(dir: string) {
    try {
      const entries = await readdir(dir, { withFileTypes: true })
      for (const entry of entries) {
        const fullPath = join(dir, entry.name)
        const relativePath = fullPath.replace(basePath, '').replace(/^[/\\]/, '')
        if (entry.isDirectory()) {
          // skip node_modules, .git
          if (entry.name === 'node_modules' || entry.name === '.git') continue
          await walk(fullPath)
        } else if (entry.isFile()) {
          if (regex.test(relativePath)) {
            results.push(relativePath)
          }
        }
      }
    } catch {
      // skip inaccessible directories
    }
  }

  await walk(basePath)
  return results
}

export function registerFileOps(workspacePath: () => string): void {
  ipcMain.handle('file:glob', async (_event, pattern: string) => {
    const base = workspacePath()
    if (!base) throw new Error('No workspace selected')
    return globFiles(base, pattern)
  })

  ipcMain.handle('file:read', async (_event, filePath: string) => {
    const base = workspacePath()
    if (!base) throw new Error('No workspace selected')
    const fullPath = resolve(base, filePath)
    // security: ensure path is within workspace
    if (!fullPath.startsWith(resolve(base))) throw new Error('Path traversal denied')
    return readFile(fullPath, 'utf-8')
  })

  ipcMain.handle('file:grep', async (_event, pattern: string, dirPath: string) => {
    const base = workspacePath()
    if (!base) throw new Error('No workspace selected')
    const searchDir = resolve(base, dirPath || '.')
    if (!searchDir.startsWith(resolve(base))) throw new Error('Path traversal denied')

    const results: string[] = []
    const regex = new RegExp(pattern, 'g')

    async function search(dir: string) {
      const entries = await readdir(dir, { withFileTypes: true })
      for (const entry of entries) {
        const fullPath = join(dir, entry.name)
        if (entry.isDirectory()) {
          if (entry.name === 'node_modules' || entry.name === '.git') continue
          await search(fullPath)
        } else if (entry.isFile()) {
          try {
            const content = await readFile(fullPath, 'utf-8')
            const lines = content.split('\n')
            const relativePath = fullPath.replace(base, '').replace(/^[/\\]/, '')
            lines.forEach((line, i) => {
              if (regex.test(line)) {
                results.push(`${relativePath}:${i + 1}: ${line.trim()}`)
              }
            })
          } catch {
            // skip binary files
          }
        }
      }
    }

    await search(searchDir)
    return results
  })

  ipcMain.handle('file:write', async (_event, filePath: string, content: string) => {
    const base = workspacePath()
    if (!base) throw new Error('No workspace selected')
    const fullPath = resolve(base, filePath)
    if (!fullPath.startsWith(resolve(base))) throw new Error('Path traversal denied')

    // ensure parent directory exists
    await (await import('fs/promises')).mkdir(dirname(fullPath), { recursive: true })
    return writeFile(fullPath, content, 'utf-8')
  })

  ipcMain.handle('file:edit', async (_event, filePath: string, oldStr: string, newStr: string) => {
    const base = workspacePath()
    if (!base) throw new Error('No workspace selected')
    const fullPath = resolve(base, filePath)
    if (!fullPath.startsWith(resolve(base))) throw new Error('Path traversal denied')

    const content = await readFile(fullPath, 'utf-8')
    if (!content.includes(oldStr)) throw new Error('old_string not found in file')
    const newContent = content.replace(oldStr, newStr)
    return writeFile(fullPath, newContent, 'utf-8')
  })

  ipcMain.handle('workspace:select', async () => {
    const result = await dialog.showOpenDialog({
      properties: ['openDirectory']
    })
    if (result.canceled || result.filePaths.length === 0) return null
    return result.filePaths[0]
  })
}
```

- [ ] **Step 2: Create settings handler**

`src/main/settings.ts`:

```typescript
import { ipcMain } from 'electron'
import Store from 'electron-store'

interface StoredSettings {
  apiBaseUrl: string
  apiKey: string
  model: string
  workspacePath: string
  fullAccess: boolean
}

const defaults: StoredSettings = {
  apiBaseUrl: 'http://localhost:8080',
  apiKey: '',
  model: 'gpt-4',
  workspacePath: '',
  fullAccess: false
}

export function registerSettings(): { store: Store<StoredSettings>; get: () => StoredSettings } {
  const store = new Store<StoredSettings>({ defaults })

  ipcMain.handle('settings:save', async (_event, settings: StoredSettings) => {
    store.set(settings)
  })

  ipcMain.handle('settings:load', async () => {
    return store.store
  })

  return {
    store,
    get: () => store.store
  }
}
```

- [ ] **Step 3: Create main process entry**

`src/main/index.ts`:

```typescript
import { app, BrowserWindow, shell } from 'electron'
import { join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import { registerFileOps } from './fileOps'
import { registerSettings } from './settings'

function createWindow(): void {
  const mainWindow = new BrowserWindow({
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

  mainWindow.on('ready-to-show', () => {
    mainWindow.show()
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

app.whenReady().then(() => {
  electronApp.setAppUserModelId('com.agent.electron-app')

  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  const { get: getSettings } = registerSettings()
  registerFileOps(() => getSettings().workspacePath)

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
```

- [ ] **Step 4: Commit**

```bash
git add src/main/
git commit -m "feat: add Electron main process with IPC handlers"
```

---

### Task 4: Preload Script

**Files:**
- Create: `src/preload/index.ts`

**Interfaces:**
- Produces: `window.electronAPI` with `file`, `workspace`, `settings` namespaces

- [ ] **Step 1: Create preload script**

`src/preload/index.ts`:

```typescript
import { contextBridge, ipcRenderer } from 'electron'

const api = {
  file: {
    glob: (pattern: string) => ipcRenderer.invoke('file:glob', pattern),
    read: (path: string) => ipcRenderer.invoke('file:read', path),
    grep: (pattern: string, dirPath: string) => ipcRenderer.invoke('file:grep', pattern, dirPath),
    write: (path: string, content: string) => ipcRenderer.invoke('file:write', path, content),
    edit: (path: string, oldStr: string, newStr: string) =>
      ipcRenderer.invoke('file:edit', path, oldStr, newStr)
  },
  workspace: {
    select: () => ipcRenderer.invoke('workspace:select')
  },
  settings: {
    save: (settings: unknown) => ipcRenderer.invoke('settings:save', settings),
    load: () => ipcRenderer.invoke('settings:load')
  }
}

contextBridge.exposeInMainWorld('electronAPI', api)
```

- [ ] **Step 2: Commit**

```bash
git add src/preload/index.ts
git commit -m "feat: add preload script with contextBridge API"
```

---

### Task 5: IPC Client & Settings Store

**Files:**
- Create: `src/renderer/src/services/ipcClient.ts`
- Create: `src/renderer/src/stores/settingsStore.ts`

**Interfaces:**
- Consumes: `ElectronAPI` from Task 2, preload from Task 4
- Produces: `ipcClient` service, `useSettingsStore` Zustand store

- [ ] **Step 1: Create IPC client service**

`src/renderer/src/services/ipcClient.ts`:

```typescript
import type { Settings } from '../types'

function getAPI() {
  if (!window.electronAPI) {
    // Return mock for dev in browser (non-Electron context)
    return {
      file: {
        glob: async () => [],
        read: async () => '',
        grep: async () => [],
        write: async () => {},
        edit: async () => {}
      },
      workspace: {
        select: async () => null
      },
      settings: {
        save: async () => {},
        load: async () => ({
          apiBaseUrl: 'http://localhost:8080',
          apiKey: '',
          model: 'gpt-4',
          workspacePath: '',
          fullAccess: false
        })
      }
    }
  }
  return window.electronAPI
}

export const ipcClient = getAPI()
```

- [ ] **Step 2: Create settings store**

`src/renderer/src/stores/settingsStore.ts`:

```typescript
import { create } from 'zustand'
import type { Settings } from '../types'
import { DEFAULT_SETTINGS } from '../types'
import { ipcClient } from '../services/ipcClient'

interface SettingsState {
  settings: Settings
  isLoaded: boolean
  load: () => Promise<void>
  save: (settings: Partial<Settings>) => Promise<void>
}

export const useSettingsStore = create<SettingsState>((set, get) => ({
  settings: DEFAULT_SETTINGS,
  isLoaded: false,

  load: async () => {
    try {
      const saved = await ipcClient.settings.load()
      set({ settings: { ...DEFAULT_SETTINGS, ...saved }, isLoaded: true })
    } catch {
      set({ isLoaded: true })
    }
  },

  save: async (partial: Partial<Settings>) => {
    const updated = { ...get().settings, ...partial }
    set({ settings: updated })
    await ipcClient.settings.save(updated)
  }
}))
```

- [ ] **Step 3: Commit**

```bash
git add src/renderer/src/services/ipcClient.ts src/renderer/src/stores/settingsStore.ts
git commit -m "feat: add IPC client and settings store"
```

---

### Task 6: Conversation Store

**Files:**
- Create: `src/renderer/src/stores/conversationStore.ts`

**Interfaces:**
- Produces: `useConversationStore` with `create`, `delete`, `select`, `updateTitle`, `addMessage`, `getCurrentConversation`

- [ ] **Step 1: Create conversation store**

`src/renderer/src/stores/conversationStore.ts`:

```typescript
import { create } from 'zustand'
import type { Conversation, Message } from '../types'

let nextId = 1
function genId(): string {
  return `conv_${Date.now()}_${nextId++}`
}
function msgGenId(): string {
  return `msg_${Date.now()}_${nextId++}`
}

interface ConversationState {
  conversations: Conversation[]
  currentConversationId: string | null

  create: () => string
  delete: (id: string) => void
  select: (id: string) => void
  updateTitle: (id: string, title: string) => void
  addMessage: (message: Message) => void
  updateLastAssistantMessage: (updater: (msg: Message) => Message) => void
  getCurrentConversation: () => Conversation | undefined
}

export const useConversationStore = create<ConversationState>((set, get) => ({
  conversations: [],
  currentConversationId: null,

  create: () => {
    const id = genId()
    const conv: Conversation = {
      id,
      title: '新对话',
      createdAt: Date.now(),
      updatedAt: Date.now(),
      messages: []
    }
    set((s) => ({
      conversations: [conv, ...s.conversations],
      currentConversationId: id
    }))
    return id
  },

  delete: (id: string) => {
    set((s) => {
      const filtered = s.conversations.filter((c) => c.id !== id)
      const currentId =
        s.currentConversationId === id
          ? filtered[0]?.id ?? null
          : s.currentConversationId
      return { conversations: filtered, currentConversationId: currentId }
    })
  },

  select: (id: string) => set({ currentConversationId: id }),

  updateTitle: (id: string, title: string) => {
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === id ? { ...c, title } : c
      )
    }))
  },

  addMessage: (message: Message) => {
    set((s) => ({
      conversations: s.conversations.map((c) => {
        if (c.id === s.currentConversationId) {
          // Auto-title from first user message
          const title =
            c.title === '新对话' && message.role === 'user'
              ? message.content.slice(0, 40)
              : c.title
          return {
            ...c,
            title,
            updatedAt: Date.now(),
            messages: [...c.messages, message]
          }
        }
        return c
      })
    }))
  },

  updateLastAssistantMessage: (updater: (msg: Message) => Message) => {
    set((s) => ({
      conversations: s.conversations.map((c) => {
        if (c.id !== s.currentConversationId) return c
        const messages = [...c.messages]
        const lastIdx = messages.length - 1
        if (lastIdx >= 0 && messages[lastIdx].role === 'assistant') {
          messages[lastIdx] = updater(messages[lastIdx])
        }
        return { ...c, messages, updatedAt: Date.now() }
      })
    }))
  },

  getCurrentConversation: () => {
    const state = get()
    return state.conversations.find((c) => c.id === state.currentConversationId)
  }
}))
```

- [ ] **Step 2: Commit**

```bash
git add src/renderer/src/stores/conversationStore.ts
git commit -m "feat: add conversation store with multi-session support"
```

---

### Task 7: Command Store

**Files:**
- Create: `src/renderer/src/stores/commandStore.ts`

**Interfaces:**
- Produces: `useCommandStore` with `commands` list and `filter` method

- [ ] **Step 1: Create command store**

`src/renderer/src/stores/commandStore.ts`:

```typescript
import { create } from 'zustand'
import type { Command } from '../types'

const BUILTIN_COMMANDS: Command[] = [
  { id: 'explain', trigger: '/explain', label: '解释代码', description: '解释选中的代码' },
  { id: 'fix', trigger: '/fix', label: '修复问题', description: '修复代码中的问题' },
  { id: 'test', trigger: '/test', label: '生成测试', description: '为选中的代码生成测试' },
  { id: 'refactor', trigger: '/refactor', label: '重构代码', description: '重构选中的代码' }
]

interface CommandState {
  commands: Command[]
  filter: (search: string) => Command[]
}

export const useCommandStore = create<CommandState>(() => ({
  commands: BUILTIN_COMMANDS,

  filter: (search: string) => {
    const q = search.toLowerCase().replace(/^\//, '')
    if (!q) return BUILTIN_COMMANDS
    return BUILTIN_COMMANDS.filter(
      (c) =>
        c.trigger.toLowerCase().includes(q) ||
        c.label.toLowerCase().includes(q) ||
        c.description.toLowerCase().includes(q)
    )
  }
}))
```

- [ ] **Step 2: Commit**

```bash
git add src/renderer/src/stores/commandStore.ts
git commit -m "feat: add command store with built-in / commands"
```

---

### Task 8: SSE & API Services

**Files:**
- Create: `src/renderer/src/services/sse.ts`
- Create: `src/renderer/src/services/api.ts`

**Interfaces:**
- Consumes: Settings from Task 5
- Produces: `parseSSEStream` generator, `sendChatMessage` function

- [ ] **Step 1: Create SSE parser**

`src/renderer/src/services/sse.ts`:

```typescript
export interface SSEDelta {
  content?: string
  tool_calls?: Array<{
    index: number
    id?: string
    function?: {
      name?: string
      arguments?: string
    }
  }>
}

export interface SSEChunk {
  content: string
  toolCalls: Map<number, {
    id: string
    name: string
    arguments: string
    complete: boolean
  }>
  done: boolean
}

export async function* parseSSEStream(
  reader: ReadableStreamDefaultReader<Uint8Array>
): AsyncGenerator<SSEChunk> {
  const decoder = new TextDecoder()
  let buffer = ''
  const accumulatingToolCalls = new Map<number, {
    id: string
    name: string
    arguments: string
    complete: boolean
  }>()

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed || !trimmed.startsWith('data: ')) continue

      const data = trimmed.slice(6)
      if (data === '[DONE]') {
        yield { content: '', toolCalls: accumulatingToolCalls, done: true }
        return
      }

      try {
        const parsed = JSON.parse(data)
        const choice = parsed.choices?.[0]
        if (!choice) continue

        const delta: SSEDelta = choice.delta || {}
        let content = ''

        if (delta.content) {
          content = delta.content
        }

        if (delta.tool_calls) {
          for (const tc of delta.tool_calls) {
            const existing = accumulatingToolCalls.get(tc.index) || {
              id: tc.id || '',
              name: '',
              arguments: '',
              complete: false
            }
            if (tc.id) existing.id = tc.id
            if (tc.function?.name) existing.name = tc.function.name
            if (tc.function?.arguments) existing.arguments += tc.function.arguments
            accumulatingToolCalls.set(tc.index, existing)
          }
        }

        yield { content, toolCalls: accumulatingToolCalls, done: false }
      } catch {
        // skip malformed JSON lines
      }
    }
  }
}
```

- [ ] **Step 2: Create API service**

`src/renderer/src/services/api.ts`:

```typescript
import type { Message } from '../types'
import { useSettingsStore } from '../stores/settingsStore'
import { parseSSEStream } from './sse'
import type { SSEChunk } from './sse'

export async function sendChatMessage(
  messages: Message[],
  onChunk: (chunk: SSEChunk) => void,
  onError: (err: Error) => void,
  onDone: () => void
): Promise<void> {
  const settings = useSettingsStore.getState().settings
  const url = `${settings.apiBaseUrl}/v1/chat/completions`

  const apiMessages = messages.map((m) => ({
    role: m.role,
    content: m.content
  }))

  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${settings.apiKey}`
      },
      body: JSON.stringify({
        model: settings.model,
        messages: apiMessages,
        stream: true
      })
    })

    if (!response.ok) {
      throw new Error(`API error: ${response.status} ${response.statusText}`)
    }

    const reader = response.body?.getReader()
    if (!reader) throw new Error('Response body is not readable')

    for await (const chunk of parseSSEStream(reader)) {
      if (chunk.done) {
        onDone()
        return
      }
      onChunk(chunk)
    }

    onDone()
  } catch (err) {
    onError(err instanceof Error ? err : new Error(String(err)))
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add src/renderer/src/services/sse.ts src/renderer/src/services/api.ts
git commit -m "feat: add SSE parser and chat API service"
```

---

### Task 9: Chat Store

**Files:**
- Create: `src/renderer/src/stores/chatStore.ts`

**Interfaces:**
- Consumes: conversationStore (Task 6), api (Task 8), types (Task 2)
- Produces: `useChatStore` with `sendMessage`, `confirmToolCall`, `cancelToolCall`, `isLoading`

- [ ] **Step 1: Create chat store**

`src/renderer/src/stores/chatStore.ts`:

```typescript
import { create } from 'zustand'
import type { Message, ToolCall, ToolType } from '../types'
import { useConversationStore } from './conversationStore'
import { sendChatMessage } from '../services/api'
import { ipcClient } from '../services/ipcClient'
import type { SSEChunk } from '../services/sse'

let msgId = 1
function genMsgId(): string {
  return `msg_${Date.now()}_${msgId++}`
}

function genToolId(): string {
  return `tool_${Date.now()}_${msgId++}`
}

const TOOL_TYPE_MAP: Record<string, ToolType> = {
  glob: 'glob',
  read: 'read',
  grep: 'grep',
  write: 'write',
  edit: 'edit'
}

const READ_TOOLS: Set<ToolType> = new Set(['glob', 'read', 'grep'])
const WRITE_TOOLS: Set<ToolType> = new Set(['write', 'edit'])

async function executeToolCall(tc: ToolCall, workspacePath: string): Promise<string> {
  const { type, args } = tc
  try {
    switch (type) {
      case 'glob':
        return JSON.stringify(await ipcClient.file.glob(args.pattern || '**/*'))
      case 'read':
        return await ipcClient.file.read(args.path || args.filePath || '')
      case 'grep':
        return JSON.stringify(
          await ipcClient.file.grep(args.pattern || '', args.path || '.')
        )
      case 'write':
        await ipcClient.file.write(args.path || args.filePath || '', args.content || '')
        return `File written: ${args.path || args.filePath}`
      case 'edit':
        await ipcClient.file.edit(
          args.path || args.filePath || '',
          args.oldStr || args.old_string || '',
          args.newStr || args.new_string || ''
        )
        return `File edited: ${args.path || args.filePath}`
    }
  } catch (err) {
    throw new Error(err instanceof Error ? err.message : String(err))
  }
}

interface ChatState {
  isLoading: boolean
  sendMessage: (content: string) => Promise<void>
  confirmToolCall: (tcId: string) => Promise<void>
  cancelToolCall: (tcId: string) => void
}

export const useChatStore = create<ChatState>(() => ({
  isLoading: false,

  sendMessage: async (content: string) => {
    const convStore = useConversationStore.getState()
    const conv = convStore.getCurrentConversation()

    // Create conversation if none exists
    let convId = conv?.id
    if (!convId) {
      convId = convStore.create()
    }

    // Add user message
    const userMsg: Message = {
      id: genMsgId(),
      role: 'user',
      content,
      timestamp: Date.now()
    }
    convStore.addMessage(userMsg)

    // Create assistant placeholder
    const assistantMsg: Message = {
      id: genMsgId(),
      role: 'assistant',
      content: '',
      isStreaming: true,
      toolCalls: [],
      timestamp: Date.now()
    }
    convStore.addMessage(assistantMsg)

    const currentConv = convStore.getCurrentConversation()
    if (!currentConv) return

    useChatStore.setState({ isLoading: true })

    await sendChatMessage(
      currentConv.messages.filter((m) => !m.isStreaming),
      (chunk: SSEChunk) => {
        // Handle content stream
        if (chunk.content) {
          convStore.updateLastAssistantMessage((msg) => ({
            ...msg,
            content: msg.content + chunk.content
          }))
        }

        // Handle tool calls
        if (chunk.toolCalls.size > 0) {
          const existingToolCalls =
            convStore.getCurrentConversation()?.messages.find(
              (m) => m.id === assistantMsg.id
            )?.toolCalls || []

          chunk.toolCalls.forEach((tc, idx) => {
            const toolType = TOOL_TYPE_MAP[tc.name] || 'read'
            // Check if this tool call already exists
            const existing = existingToolCalls.find(
              (et) => et.id === tc.id || et.id.startsWith(`tool_${idx}`)
            )

            if (existing) {
              // Update arguments
              existing.args = { ...existing.args, _raw: tc.arguments }
            } else if (tc.name) {
              let args: Record<string, string> = { _raw: tc.arguments }
              try {
                const parsed = JSON.parse(tc.arguments)
                args = { ...parsed, _raw: tc.arguments }
              } catch {
                // arguments may be incomplete (streaming), use raw string
              }

              const newTc: ToolCall = {
                id: tc.id || `tool_${idx}`,
                type: toolType,
                name: tc.name,
                args,
                status: WRITE_TOOLS.has(toolType) ? 'confirming' : 'pending'
              }

              convStore.updateLastAssistantMessage((msg) => ({
                ...msg,
                toolCalls: [...(msg.toolCalls || []), newTc]
              }))
            }
          })

          // Auto-execute read tools
          setTimeout(async () => {
            const updatedConv = convStore.getCurrentConversation()
            const updatedMsg = updatedConv?.messages.find(
              (m) => m.id === assistantMsg.id
            )
            if (!updatedMsg?.toolCalls) return

            for (const tc of updatedMsg.toolCalls) {
              if (tc.status === 'pending' && READ_TOOLS.has(tc.type)) {
                // Mark executing
                convStore.updateLastAssistantMessage((msg) => ({
                  ...msg,
                  toolCalls: msg.toolCalls?.map((t) =>
                    t.id === tc.id ? { ...t, status: 'executing' as const } : t
                  )
                }))

                try {
                  const result = await executeToolCall(tc, '')
                  convStore.updateLastAssistantMessage((msg) => ({
                    ...msg,
                    toolCalls: msg.toolCalls?.map((t) =>
                      t.id === tc.id
                        ? { ...t, status: 'done' as const, result }
                        : t
                    )
                  }))
                } catch (err) {
                  convStore.updateLastAssistantMessage((msg) => ({
                    ...msg,
                    toolCalls: msg.toolCalls?.map((t) =>
                      t.id === tc.id
                        ? {
                            ...t,
                            status: 'error' as const,
                            result: err instanceof Error ? err.message : String(err)
                          }
                        : t
                    )
                  }))
                }
              }
            }
          }, 0)
        }
      },
      (err: Error) => {
        convStore.updateLastAssistantMessage((msg) => ({
          ...msg,
          content: msg.content + `\n\n**错误:** ${err.message}`,
          isStreaming: false
        }))
        useChatStore.setState({ isLoading: false })
      },
      () => {
        convStore.updateLastAssistantMessage((msg) => ({
          ...msg,
          isStreaming: false
        }))
        useChatStore.setState({ isLoading: false })
      }
    )
  },

  confirmToolCall: async (tcId: string) => {
    const convStore = useConversationStore.getState()

    // Mark executing
    convStore.updateLastAssistantMessage((msg) => ({
      ...msg,
      toolCalls: msg.toolCalls?.map((t) =>
        t.id === tcId ? { ...t, status: 'executing' as const } : t
      )
    }))

    const conv = convStore.getCurrentConversation()
    const lastMsg = conv?.messages[conv.messages.length - 1]
    const tc = lastMsg?.toolCalls?.find((t) => t.id === tcId)

    if (tc) {
      try {
        const result = await executeToolCall(tc, '')
        convStore.updateLastAssistantMessage((msg) => ({
          ...msg,
          toolCalls: msg.toolCalls?.map((t) =>
            t.id === tcId ? { ...t, status: 'done' as const, result } : t
          )
        }))
      } catch (err) {
        convStore.updateLastAssistantMessage((msg) => ({
          ...msg,
          toolCalls: msg.toolCalls?.map((t) =>
            t.id === tcId
              ? {
                  ...t,
                  status: 'error' as const,
                  result: err instanceof Error ? err.message : String(err)
                }
              : t
          )
        }))
      }
    }
  },

  cancelToolCall: (tcId: string) => {
    const convStore = useConversationStore.getState()
    convStore.updateLastAssistantMessage((msg) => ({
      ...msg,
      toolCalls: msg.toolCalls?.map((t) =>
        t.id === tcId
          ? { ...t, status: 'error' as const, result: 'User cancelled' }
          : t
      )
    }))
  }
}))
```

- [ ] **Step 2: Commit**

```bash
git add src/renderer/src/stores/chatStore.ts
git commit -m "feat: add chat store with SSE streaming and tool call handling"
```

---

### Task 10: Global Styles

**Files:**
- Create: `src/renderer/src/styles/globals.css`

- [ ] **Step 1: Create global styles**

`src/renderer/src/styles/globals.css`:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html, body, #root {
  height: 100%;
  overflow: hidden;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  background-color: #181825;
  color: #e0e0e0;
}

::-webkit-scrollbar {
  width: 6px;
}

::-webkit-scrollbar-track {
  background: transparent;
}

::-webkit-scrollbar-thumb {
  background: #3b3b5c;
  border-radius: 3px;
}

::-webkit-scrollbar-thumb:hover {
  background: #4a4a6a;
}
```

- [ ] **Step 2: Commit**

```bash
git add src/renderer/src/styles/globals.css
git commit -m "feat: add global styles with dark theme"
```

---

### Task 11: App Layout & Sidebar Shell

**Files:**
- Create: `src/renderer/src/App.tsx`
- Create: `src/renderer/src/main.tsx`
- Create: `src/renderer/src/components/layout/AppLayout.tsx`
- Create: `src/renderer/src/components/sidebar/Sidebar.tsx`

**Interfaces:**
- Consumes: stores from Tasks 5-7
- Produces: App shell with left sidebar + right content area

- [ ] **Step 1: Create main.tsx entry**

`src/renderer/src/main.tsx`:

```tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './styles/globals.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
```

- [ ] **Step 2: Create App.tsx**

`src/renderer/src/App.tsx`:

```tsx
import { useEffect } from 'react'
import { useSettingsStore } from './stores/settingsStore'
import { useConversationStore } from './stores/conversationStore'
import { AppLayout } from './components/layout/AppLayout'

export default function App() {
  const load = useSettingsStore((s) => s.load)
  const create = useConversationStore((s) => s.create)

  useEffect(() => {
    load()
    // Create initial conversation if none
    create()
  }, [])

  return <AppLayout />
}
```

- [ ] **Step 3: Create AppLayout**

`src/renderer/src/components/layout/AppLayout.tsx`:

```tsx
import { useState, useCallback } from 'react'
import { Sidebar } from '../sidebar/Sidebar'
import { ChatPanel } from '../chat/ChatPanel'

export function AppLayout() {
  const [sidebarWidth, setSidebarWidth] = useState(320)
  const [dragging, setDragging] = useState(false)

  const handleMouseDown = useCallback(() => {
    setDragging(true)
  }, [])

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!dragging) return
      const newWidth = Math.max(240, Math.min(500, e.clientX))
      setSidebarWidth(newWidth)
    },
    [dragging]
  )

  const handleMouseUp = useCallback(() => {
    setDragging(false)
  }, [])

  return (
    <div
      className="flex h-screen select-none"
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
    >
      <div style={{ width: sidebarWidth }} className="flex-shrink-0">
        <Sidebar />
      </div>
      <div
        className="w-1 cursor-col-resize hover:bg-accent bg-transparent transition-colors flex-shrink-0"
        onMouseDown={handleMouseDown}
      />
      <div className="flex-1 min-w-0">
        <ChatPanel />
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Create Sidebar shell**

`src/renderer/src/components/sidebar/Sidebar.tsx`:

```tsx
import { useState } from 'react'
import { MessageSquare, Settings } from 'lucide-react'
import { ConversationList } from './ConversationList'
import { SettingsPanel } from './SettingsPanel'

type Tab = 'conversations' | 'settings'

export function Sidebar() {
  const [tab, setTab] = useState<Tab>('conversations')

  return (
    <div className="h-full bg-sidebar-bg border-r border-sidebar-border flex flex-col">
      <div className="flex border-b border-sidebar-border">
        <button
          onClick={() => setTab('conversations')}
          className={`flex-1 flex items-center justify-center gap-2 py-3 text-sm transition-colors ${
            tab === 'conversations'
              ? 'text-accent border-b-2 border-accent'
              : 'text-gray-400 hover:text-gray-200'
          }`}
        >
          <MessageSquare size={16} />
          对话
        </button>
        <button
          onClick={() => setTab('settings')}
          className={`flex-1 flex items-center justify-center gap-2 py-3 text-sm transition-colors ${
            tab === 'settings'
              ? 'text-accent border-b-2 border-accent'
              : 'text-gray-400 hover:text-gray-200'
          }`}
        >
          <Settings size={16} />
          设置
        </button>
      </div>
      <div className="flex-1 overflow-hidden">
        {tab === 'conversations' ? <ConversationList /> : <SettingsPanel />}
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Commit**

```bash
git add src/renderer/src/main.tsx src/renderer/src/App.tsx src/renderer/src/components/
git commit -m "feat: add app layout with resizable sidebar"
```

---

### Task 12: Conversation List

**Files:**
- Create: `src/renderer/src/components/sidebar/ConversationList.tsx`

**Interfaces:**
- Consumes: `useConversationStore`

- [ ] **Step 1: Create conversation list**

`src/renderer/src/components/sidebar/ConversationList.tsx`:

```tsx
import { Plus, Trash2 } from 'lucide-react'
import { useConversationStore } from '../../stores/conversationStore'

export function ConversationList() {
  const conversations = useConversationStore((s) => s.conversations)
  const currentId = useConversationStore((s) => s.currentConversationId)
  const create = useConversationStore((s) => s.create)
  const select = useConversationStore((s) => s.select)
  const del = useConversationStore((s) => s.delete)

  return (
    <div className="flex flex-col h-full">
      <div className="p-3">
        <button
          onClick={() => create()}
          className="w-full flex items-center justify-center gap-2 py-2 rounded-lg border border-dashed border-gray-500 text-gray-400 hover:text-gray-200 hover:border-gray-400 transition-colors text-sm"
        >
          <Plus size={16} />
          新对话
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-2">
        {conversations.map((conv) => (
          <div
            key={conv.id}
            onClick={() => select(conv.id)}
            className={`group flex items-center justify-between px-3 py-2.5 rounded-lg cursor-pointer mb-1 transition-colors ${
              conv.id === currentId
                ? 'bg-sidebar-active text-gray-100'
                : 'text-gray-400 hover:bg-sidebar-hover hover:text-gray-200'
            }`}
          >
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate">{conv.title}</div>
              <div className="text-xs text-gray-500 mt-0.5">
                {formatTime(conv.updatedAt)}
              </div>
            </div>
            <button
              onClick={(e) => {
                e.stopPropagation()
                del(conv.id)
              }}
              className="opacity-0 group-hover:opacity-100 p-1 hover:text-red-400 transition-all"
            >
              <Trash2 size={14} />
            </button>
          </div>
        ))}
        {conversations.length === 0 && (
          <div className="text-center text-gray-500 text-sm mt-8">
            暂无对话
          </div>
        )}
      </div>
    </div>
  )
}

function formatTime(ts: number): string {
  const d = new Date(ts)
  const now = new Date()
  const isToday = d.toDateString() === now.toDateString()
  if (isToday) {
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  }
  return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}
```

- [ ] **Step 2: Commit**

```bash
git add src/renderer/src/components/sidebar/ConversationList.tsx
git commit -m "feat: add conversation list with create/delete/select"
```

---

### Task 13: Settings Panel

**Files:**
- Create: `src/renderer/src/components/sidebar/SettingsPanel.tsx`

**Interfaces:**
- Consumes: `useSettingsStore`

- [ ] **Step 1: Create settings panel**

`src/renderer/src/components/sidebar/SettingsPanel.tsx`:

```tsx
import { useState } from 'react'
import { FolderOpen, Eye, EyeOff } from 'lucide-react'
import { useSettingsStore } from '../../stores/settingsStore'
import { ipcClient } from '../../services/ipcClient'

export function SettingsPanel() {
  const settings = useSettingsStore((s) => s.settings)
  const save = useSettingsStore((s) => s.save)
  const [showKey, setShowKey] = useState(false)

  return (
    <div className="p-4 space-y-5 overflow-y-auto h-full">
      <h3 className="text-sm font-medium text-gray-300">API 配置</h3>

      <div>
        <label className="text-xs text-gray-500 mb-1.5 block">Base URL</label>
        <input
          type="text"
          value={settings.apiBaseUrl}
          onChange={(e) => save({ apiBaseUrl: e.target.value })}
          placeholder="http://localhost:8080"
          className="w-full px-3 py-2 rounded-lg bg-chat-bg border border-sidebar-border text-gray-200 text-sm focus:outline-none focus:border-accent transition-colors"
        />
      </div>

      <div>
        <label className="text-xs text-gray-500 mb-1.5 block">API Key</label>
        <div className="relative">
          <input
            type={showKey ? 'text' : 'password'}
            value={settings.apiKey}
            onChange={(e) => save({ apiKey: e.target.value })}
            placeholder="sk-..."
            className="w-full px-3 py-2 pr-10 rounded-lg bg-chat-bg border border-sidebar-border text-gray-200 text-sm focus:outline-none focus:border-accent transition-colors"
          />
          <button
            onClick={() => setShowKey(!showKey)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
          >
            {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
        </div>
      </div>

      <div>
        <label className="text-xs text-gray-500 mb-1.5 block">Model</label>
        <input
          type="text"
          value={settings.model}
          onChange={(e) => save({ model: e.target.value })}
          placeholder="gpt-4"
          className="w-full px-3 py-2 rounded-lg bg-chat-bg border border-sidebar-border text-gray-200 text-sm focus:outline-none focus:border-accent transition-colors"
        />
      </div>

      <hr className="border-sidebar-border" />

      <h3 className="text-sm font-medium text-gray-300">工作空间</h3>

      <div>
        <label className="text-xs text-gray-500 mb-1.5 block">文件夹路径</label>
        <div className="flex gap-2">
          <input
            type="text"
            value={settings.workspacePath}
            readOnly
            placeholder="未选择"
            className="flex-1 px-3 py-2 rounded-lg bg-chat-bg border border-sidebar-border text-gray-200 text-sm focus:outline-none cursor-default"
          />
          <button
            onClick={async () => {
              const path = await ipcClient.workspace.select()
              if (path) save({ workspacePath: path })
            }}
            className="px-3 py-2 rounded-lg bg-sidebar-hover text-gray-300 hover:text-gray-100 hover:bg-sidebar-active transition-colors"
          >
            <FolderOpen size={16} />
          </button>
        </div>
      </div>

      <div className="flex items-center justify-between">
        <div>
          <label className="text-sm text-gray-300">完全访问权限</label>
          <p className="text-xs text-gray-500 mt-0.5">允许 Agent 读写工作空间文件</p>
        </div>
        <button
          onClick={() => save({ fullAccess: !settings.fullAccess })}
          className={`relative w-10 h-5 rounded-full transition-colors ${
            settings.fullAccess ? 'bg-accent' : 'bg-gray-600'
          }`}
        >
          <div
            className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
              settings.fullAccess ? 'translate-x-5' : 'translate-x-0.5'
            }`}
          />
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add src/renderer/src/components/sidebar/SettingsPanel.tsx
git commit -m "feat: add settings panel with API config and workspace"
```

---

### Task 14: Chat Messages & Tool Call Cards

**Files:**
- Create: `src/renderer/src/components/chat/ChatPanel.tsx`
- Create: `src/renderer/src/components/chat/MessageList.tsx`
- Create: `src/renderer/src/components/chat/MessageItem.tsx`
- Create: `src/renderer/src/components/chat/ToolCallCard.tsx`

**Interfaces:**
- Consumes: conversationStore, chatStore

- [ ] **Step 1: Create ToolCallCard**

`src/renderer/src/components/chat/ToolCallCard.tsx`:

```tsx
import { Wrench, CheckCircle, XCircle, Loader2, AlertCircle } from 'lucide-react'
import type { ToolCall } from '../../types'
import { useChatStore } from '../../stores/chatStore'

const STATUS_ICON: Record<string, React.ReactNode> = {
  pending: <Loader2 size={14} className="animate-spin text-gray-400" />,
  confirming: <AlertCircle size={14} className="text-yellow-400" />,
  executing: <Loader2 size={14} className="animate-spin text-blue-400" />,
  done: <CheckCircle size={14} className="text-green-400" />,
  error: <XCircle size={14} className="text-red-400" />
}

const STATUS_LABEL: Record<string, string> = {
  pending: '执行中...',
  confirming: '等待确认',
  executing: '执行中...',
  done: '完成',
  error: '失败'
}

interface Props {
  toolCall: ToolCall
}

export function ToolCallCard({ toolCall }: Props) {
  const confirm = useChatStore((s) => s.confirmToolCall)
  const cancel = useChatStore((s) => s.cancelToolCall)

  return (
    <div className="my-2 p-3 rounded-lg bg-sidebar-bg border border-sidebar-border">
      <div className="flex items-center gap-2 mb-2">
        <Wrench size={14} className="text-accent" />
        <span className="text-sm font-medium text-gray-300">
          {toolCall.name || toolCall.type}
        </span>
        <span className="flex items-center gap-1 text-xs">
          {STATUS_ICON[toolCall.status]}
          <span className="text-gray-500">{STATUS_LABEL[toolCall.status]}</span>
        </span>
      </div>

      <div className="text-xs text-gray-500 font-mono bg-chat-bg rounded p-2 mb-2 overflow-x-auto">
        {formatArgs(toolCall.args)}
      </div>

      {toolCall.status === 'confirming' && (
        <div className="flex gap-2">
          <button
            onClick={() => confirm(toolCall.id)}
            className="px-3 py-1 text-xs rounded bg-green-600 hover:bg-green-700 text-white transition-colors"
          >
            是，执行
          </button>
          <button
            onClick={() => cancel(toolCall.id)}
            className="px-3 py-1 text-xs rounded bg-red-600 hover:bg-red-700 text-white transition-colors"
          >
            否，取消
          </button>
        </div>
      )}

      {toolCall.result && (
        <div className="text-xs text-gray-400 font-mono bg-chat-bg rounded p-2 mt-2 max-h-32 overflow-y-auto whitespace-pre-wrap">
          {toolCall.result}
        </div>
      )}
    </div>
  )
}

function formatArgs(args: Record<string, string>): string {
  const { _raw, ...rest } = args
  if (_raw) return _raw
  return JSON.stringify(rest, null, 0)
}
```

- [ ] **Step 2: Create MessageItem**

`src/renderer/src/components/chat/MessageItem.tsx`:

```tsx
import { Bot, User } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import type { Message } from '../../types'
import { ToolCallCard } from './ToolCallCard'

interface Props {
  message: Message
}

export function MessageItem({ message }: Props) {
  const isUser = message.role === 'user'

  return (
    <div className={`flex gap-3 px-4 py-4 ${isUser ? '' : 'bg-chat-bg/50'}`}>
      <div
        className={`w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 ${
          isUser ? 'bg-accent' : 'bg-sidebar-active'
        }`}
      >
        {isUser ? <User size={14} /> : <Bot size={14} />}
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-xs text-gray-500 mb-1">
          {isUser ? '你' : 'Assistant'}
        </div>
        <div className="prose prose-invert prose-sm max-w-none text-gray-200">
          {message.content ? (
            <ReactMarkdown>{message.content}</ReactMarkdown>
          ) : message.isStreaming ? (
            <span className="inline-block w-2 h-4 bg-accent animate-pulse" />
          ) : null}
        </div>
        {message.toolCalls?.map((tc) => (
          <ToolCallCard key={tc.id} toolCall={tc} />
        ))}
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Create MessageList**

`src/renderer/src/components/chat/MessageList.tsx`:

```tsx
import { useEffect, useRef } from 'react'
import { useConversationStore } from '../../stores/conversationStore'
import { MessageItem } from './MessageItem'

export function MessageList() {
  const conv = useConversationStore((s) => s.getCurrentConversation())
  const messages = conv?.messages || []
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, messages[messages.length - 1]?.content])

  if (messages.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-500">
        <div className="text-center">
          <div className="text-4xl mb-3">🤖</div>
          <div className="text-sm">开始一段新对话</div>
          <div className="text-xs mt-1 text-gray-600">
            输入 / 使用指令，或直接提问
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto">
      {messages.map((msg) => (
        <MessageItem key={msg.id} message={msg} />
      ))}
      <div ref={bottomRef} />
    </div>
  )
}
```

- [ ] **Step 4: Create ChatPanel**

`src/renderer/src/components/chat/ChatPanel.tsx`:

```tsx
import { useConversationStore } from '../../stores/conversationStore'
import { MessageList } from './MessageList'
import { ChatInput } from './ChatInput'

export function ChatPanel() {
  const conv = useConversationStore((s) => s.getCurrentConversation())

  return (
    <div className="h-full flex flex-col">
      <div className="flex-shrink-0 px-4 py-3 border-b border-sidebar-border">
        <h2 className="text-sm font-medium text-gray-300 truncate">
          {conv?.title || 'Agent Desktop'}
        </h2>
      </div>
      <MessageList />
      <ChatInput />
    </div>
  )
}
```

- [ ] **Step 5: Commit**

```bash
git add src/renderer/src/components/chat/
git commit -m "feat: add chat panel with messages, markdown, tool call cards"
```

---

### Task 15: Monaco Chat Input with / Commands

**Files:**
- Create: `src/renderer/src/components/chat/ChatInput.tsx`

**Interfaces:**
- Consumes: chatStore, commandStore, conversationStore

- [ ] **Step 1: Create ChatInput**

`src/renderer/src/components/chat/ChatInput.tsx`:

```tsx
import { useRef, useState, useCallback, useEffect, KeyboardEvent } from 'react'
import Editor, { type OnMount } from '@monaco-editor/react'
import type { editor } from 'monaco-editor'
import { Send, CornerDownLeft } from 'lucide-react'
import { useChatStore } from '../../stores/chatStore'
import { useCommandStore } from '../../stores/commandStore'
import type { Command } from '../../types'

export function ChatInput() {
  const sendMessage = useChatStore((s) => s.sendMessage)
  const isLoading = useChatStore((s) => s.isLoading)
  const filterCommands = useCommandStore((s) => s.filter)

  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null)
  const [showCommands, setShowCommands] = useState(false)
  const [commandList, setCommandList] = useState<Command[]>([])
  const [selectedCmdIdx, setSelectedCmdIdx] = useState(0)

  const handleMount: OnMount = (editor) => {
    editorRef.current = editor
    editor.focus()
  }

  const getLineText = useCallback((): string => {
    if (!editorRef.current) return ''
    const model = editorRef.current.getModel()
    if (!model) return ''
    const position = editorRef.current.getPosition()
    if (!position) return ''
    return model.getLineContent(position.lineNumber)
  }, [])

  const insertCommand = useCallback(
    (cmd: Command) => {
      if (!editorRef.current) return
      const model = editorRef.current.getModel()
      if (!model) return
      const position = editorRef.current.getPosition()
      if (!position) return

      const lineContent = model.getLineContent(position.lineNumber)
      const beforeCursor = lineContent.slice(0, position.column)
      const slashIdx = beforeCursor.lastIndexOf('/')
      if (slashIdx === -1) return

      const before = lineContent.slice(0, slashIdx)
      const after = lineContent.slice(position.column)
      model.setValue(
        model
          .getValue()
          .split('\n')
          .map((l, i) =>
            i === position.lineNumber - 1 ? before + cmd.trigger + ' ' + after : l
          )
          .join('\n')
      )
      editorRef.current.setPosition({
        lineNumber: position.lineNumber,
        column: before.length + cmd.trigger.length + 2
      })
      setShowCommands(false)
      editorRef.current.focus()
    },
    []
  )

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (showCommands) {
        if (e.key === 'ArrowDown') {
          e.preventDefault()
          setSelectedCmdIdx((i) => Math.min(i + 1, commandList.length - 1))
          return
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault()
          setSelectedCmdIdx((i) => Math.max(i - 1, 0))
          return
        }
        if (e.key === 'Enter' && commandList[selectedCmdIdx]) {
          e.preventDefault()
          insertCommand(commandList[selectedCmdIdx])
          return
        }
        if (e.key === 'Escape') {
          setShowCommands(false)
          return
        }
      }

      // Submit on Enter (without Shift)
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        handleSend()
      }
    },
    [showCommands, commandList, selectedCmdIdx]
  )

  const handleSend = useCallback(() => {
    if (!editorRef.current || isLoading) return
    const text = editorRef.current.getValue().trim()
    if (!text) return
    sendMessage(text)
    editorRef.current.setValue('')
    setShowCommands(false)
  }, [sendMessage, isLoading])

  // Watch for / commands
  useEffect(() => {
    if (!editorRef.current) return
    const disposable = editorRef.current.onDidChangeCursorPosition(() => {
      const lineText = getLineText()
      const model = editorRef.current.getModel()
      if (!model) return
      const pos = editorRef.current!.getPosition()
      if (!pos) return

      const beforeCursor = lineText.slice(0, pos.column)
      const slashIdx = beforeCursor.lastIndexOf('/')
      const spaceAfterSlash = beforeCursor.indexOf(' ', slashIdx)

      if (slashIdx !== -1 && (spaceAfterSlash === -1 || spaceAfterSlash > pos.column)) {
        const query = beforeCursor.slice(slashIdx, pos.column)
        const results = filterCommands(query)
        if (results.length > 0) {
          setCommandList(results)
          setShowCommands(true)
          setSelectedCmdIdx(0)
        } else {
          setShowCommands(false)
        }
      } else {
        setShowCommands(false)
      }
    })

    return () => disposable.dispose()
  }, [getLineText, filterCommands])

  return (
    <div className="flex-shrink-0 border-t border-sidebar-border p-3 relative">
      {showCommands && (
        <div className="absolute bottom-full left-3 right-3 mb-1 bg-sidebar-bg border border-sidebar-border rounded-lg shadow-lg max-h-48 overflow-y-auto z-50">
          {commandList.map((cmd, idx) => (
            <button
              key={cmd.id}
              onClick={() => insertCommand(cmd)}
              className={`w-full text-left px-3 py-2 text-sm transition-colors flex items-center justify-between ${
                idx === selectedCmdIdx
                  ? 'bg-sidebar-active text-gray-100'
                  : 'text-gray-400 hover:bg-sidebar-hover'
              }`}
            >
              <span>
                <span className="text-accent font-medium">{cmd.trigger}</span>
                <span className="mx-2">—</span>
                {cmd.label}
              </span>
              <span className="text-xs text-gray-600">{cmd.description}</span>
            </button>
          ))}
        </div>
      )}

      <div className="flex items-end gap-2 bg-chat-bg rounded-xl border border-sidebar-border px-4 py-2 focus-within:border-accent transition-colors">
        <div className="flex-1 min-h-[40px] max-h-[200px] overflow-y-auto">
          <Editor
            height="40px"
            defaultLanguage="plaintext"
            theme="vs-dark"
            onMount={handleMount}
            loading={<div className="text-gray-500 text-sm px-1">加载编辑器...</div>}
            options={{
              minimap: { enabled: false },
              lineNumbers: 'off',
              glyphMargin: false,
              folding: false,
              lineDecorationsWidth: 0,
              lineNumbersMinChars: 0,
              scrollBeyondLastLine: false,
              wordWrap: 'on',
              renderLineHighlight: 'none',
              overviewRulerLanes: 0,
              hideCursorInOverviewRuler: true,
              overviewRulerBorder: false,
              scrollbar: { vertical: 'hidden', horizontal: 'hidden' },
              fontSize: 14,
              fontFamily: "'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace",
              padding: { top: 0, bottom: 0 },
              suggest: { showWords: false, showSnippets: false }
            }}
            wrapperProps={{ onKeyDown: handleKeyDown }}
          />
        </div>
        <div className="flex items-center gap-1 text-xs text-gray-500 pb-1 flex-shrink-0">
          <button
            onClick={handleSend}
            disabled={isLoading}
            className="p-1.5 rounded-lg hover:bg-sidebar-hover text-gray-400 hover:text-gray-200 transition-colors disabled:opacity-50"
            title="发送 (Enter)"
          >
            {isLoading ? (
              <div className="w-4 h-4 border-2 border-gray-400 border-t-transparent rounded-full animate-spin" />
            ) : (
              <Send size={16} />
            )}
          </button>
        </div>
      </div>
      <div className="flex items-center gap-3 px-1 mt-2 text-xs text-gray-600">
        <span className="flex items-center gap-1">
          <CornerDownLeft size={12} /> 发送
        </span>
        <span>Shift+Enter 换行</span>
        <span>/ 调用指令</span>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add src/renderer/src/components/chat/ChatInput.tsx
git commit -m "feat: add Monaco chat input with / command menu"
```

---

### Task 16: Packaging Configuration

**Files:**
- Create: `electron-builder.yml`

- [ ] **Step 1: Create electron-builder.yml**

`electron-builder.yml`:

```yaml
appId: com.agent.electron-app
productName: Agent Desktop
directories:
  output: dist
  buildResources: resources
files:
  - out/**/*
  - '!out/renderer/src/**'
win:
  target:
    - target: nsis
      arch: [x64]
  icon: resources/icon.png
nsis:
  oneClick: false
  allowToChangeInstallationDirectory: true
  createDesktopShortcut: true
  shortcutName: Agent Desktop
  installerIcon: resources/icon.ico
  uninstallerIcon: resources/icon.ico
  installerHeaderIcon: resources/icon.ico
npmRebuild: false
```

- [ ] **Step 2: Verify build**

```bash
npm run build
```

Expected: Build completes without errors, output in `out/` directory.

- [ ] **Step 3: Verify package (optional, requires Windows)**

```bash
npm run package
```

Expected: NSIS installer created in `dist/` directory.

- [ ] **Step 4: Commit**

```bash
git add electron-builder.yml
git commit -m "feat: add electron-builder NSIS packaging config"
```

---

### Task 17: Integration Verification

**Steps:**

- [ ] **Step 1: Run dev server**

```bash
npm run dev
```

Expected: Electron window opens with the Agent Desktop app.

- [ ] **Step 2: Manual smoke test checklist**

- Left sidebar shows "对话" tab active by default
- Click "+" to create a new conversation
- Switch between conversations
- Switch to "设置" tab, verify all fields render
- Click folder button to test workspace selection dialog
- Toggle "完全访问权限" switch
- Type in Monaco input box, verify `/explain` triggers command menu
- Arrow-key navigate command menu, press Enter to insert
- Send a message (will fail without backend, but should show user message and error)
- Delete a conversation

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: integration fixes from smoke testing"
```
