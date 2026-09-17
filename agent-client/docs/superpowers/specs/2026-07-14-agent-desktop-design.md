# Agent Desktop — 设计规格说明

**日期：** 2026-07-14

## 概述

一个基于 Electron + React 的桌面 Agent 应用，界面参考 WorkBuddy，支持多会话对话、本地文件操作（glob/read/grep/write/edit）、流式 SSE 响应、工具调用确认。应用为纯前端，Agent/模型逻辑由后端服务控制。

---

## 技术栈

| 类别 | 选择 | 原因 |
|------|------|------|
| 框架 | Electron + Vite + React + TypeScript | 需求指定 |
| 构建 | electron-vite | 原生支持 Electron + Vite，比 vite-plugin-electron 更成熟 |
| 状态管理 | Zustand | 轻量、TS 友好、无 boilerplate |
| UI 组件 | shadcn/ui (Radix + Tailwind CSS) | 高质量无头组件，暗色模式内置 |
| 编辑器 | @monaco-editor/react | 聊天输入框，支持 / 指令和后续 @ 引用 |
| 持久化 | electron-store | 主进程安全存储 API key |
| 打包 | electron-builder | NSIS 安装包，支持 Windows 11 |

---

## 项目结构

```
electron-app/
├── electron/                    # Electron 主进程
│   ├── main.ts                  # 入口：创建窗口、注册 IPC
│   ├── preload.ts               # contextBridge 暴露安全 API
│   └── ipc/
│       ├── fileOps.ts           # glob/read/grep/write/edit 实现
│       └── settings.ts          # API key 持久化 (electron-store)
│
├── src/                         # React 渲染进程
│   ├── main.tsx                 # React 入口
│   ├── App.tsx                  # 路由 + Layout
│   ├── components/
│   │   ├── layout/
│   │   │   └── AppLayout.tsx    # 左右分栏布局
│   │   ├── sidebar/
│   │   │   ├── Sidebar.tsx      # 左侧容器 + Tab 切换
│   │   │   ├── ConversationList.tsx
│   │   │   └── SettingsPanel.tsx
│   │   ├── chat/
│   │   │   ├── ChatPanel.tsx    # 右侧容器
│   │   │   ├── MessageList.tsx  # 消息列表（含 tool call 渲染）
│   │   │   ├── MessageItem.tsx
│   │   │   ├── ToolCallCard.tsx # 工具调用卡片（含确认按钮）
│   │   │   └── ChatInput.tsx    # Monaco 输入框 + / 指令
│   │   └── common/
│   │       └── ConfirmDialog.tsx
│   ├── stores/                  # Zustand
│   │   ├── conversationStore.ts
│   │   ├── chatStore.ts
│   │   ├── settingsStore.ts
│   │   └── commandStore.ts
│   ├── services/
│   │   ├── api.ts               # REST 请求（创建会话等）
│   │   ├── sse.ts               # SSE 流解析
│   │   └── ipcClient.ts         # 封装 preload API 调用
│   ├── hooks/
│   │   ├── useSSE.ts
│   │   └── useCommands.ts
│   ├── types/
│   │   └── index.ts             # Message, Conversation, ToolCall 等类型
│   └── styles/
│       └── globals.css
│
├── resources/                   # 打包资源
├── package.json
├── electron-builder.yml         # NSIS 打包配置
├── electron.vite.config.ts
├── tsconfig.json
└── tailwind.config.ts
```

---

## UI 布局

```
┌─────────────────────────────────────────────────────────┐
│  Title Bar (可拖拽)                           ─ × 按钮  │
├────────────┬────────────────────────────────────────────┤
│   Sidebar  │             Chat Panel                     │
│  (320px)   │                                            │
│ ┌────────┐ │ ┌────────────────────────────────────────┐ │
│ │Conversa│ │ │  User: "帮我分析这个项目结构"           │ │
│ │  tions │ │ │                                        │ │
│ │  Tab   │ │ │  Assistant: (Markdown 渲染)             │ │
│ │────────│ │ │  正在分析项目结构...                    │ │
│ │ Settings│ │ │                                        │ │
│ │  Tab   │ │ │  ┌── Tool Call ──────────────────────┐ │ │
│ └────────┘ │ │  │ glob("**/*.ts")                   │ │ │
│ ┌────────┐ │ │  │ 结果: [12 个文件...]              │ │ │
│ │ + 新会话│ │ │  └──────────────────────────────────┘ │ │
│ │────────│ │ │                                        │ │
│ │ 会话 1  │ │ │  ┌── 确认操作 ──────────────────────┐ │ │
│ │ 会话 2  │ │ │  │ 写入 src/config.ts?   [是] [否]  │ │ │
│ │ 会话 3  │ │ │  └──────────────────────────────────┘ │ │
│ └────────┘ │ └────────────────────────────────────────┘ │
│            │ ┌────────────────────────────────────────┐ │
│            │ │ 输入框 (Monaco)              [/] [@]   │ │
│            │ │ >                                        │ │
│            │ └────────────────────────────────────────┘ │
└────────────┴────────────────────────────────────────────┘
```

### 左侧 Sidebar（320px，可拖拽调整宽度）

- 顶部 Tab 切换：「对话」和「设置」
- **对话 Tab：**
  - "+" 新对话按钮
  - 会话列表：标题、时间、删除（hover 显示）
  - 选中状态高亮
- **设置 Tab：**
  - API Base URL
  - API Key（密码输入 + 可见切换）
  - Model 名称
  - 工作空间路径选择（触发原生文件夹对话框）
  - "允许完全访问权限"开关

### 右侧 Chat Panel

- 顶部：对话标题 Bar
- 中间：消息列表（scrollable，新消息自动滚底）
  - 用户气泡 / AI 气泡（Markdown 渲染）
  - AI 回复流式打字效果（SSE 实时追加内容）
  - Tool Call 卡片：工具名 + 参数 + 状态 + 结果
  - 确认卡片：操作描述 + [是]/[否] 按钮
- 底部：Monaco 输入框（单行为主，Shift+Enter 换行）
  - `/` 触发指令菜单（浮动列表 + 键盘导航）
  - 右侧发送按钮

---

## 核心类型

```typescript
interface Conversation {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: Message[];
}

interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  toolCalls?: ToolCall[];
  isStreaming?: boolean;
  timestamp: number;
}

interface ToolCall {
  id: string;
  type: 'glob' | 'read' | 'grep' | 'write' | 'edit';
  args: Record<string, string>;
  status: 'pending' | 'confirming' | 'executing' | 'done' | 'error';
  result?: string;
}

interface Settings {
  apiBaseUrl: string;
  apiKey: string;
  model: string;
  workspacePath: string;
  fullAccess: boolean;
}

interface Command {
  id: string;
  trigger: string;
  label: string;
  description: string;
}
```

---

## 数据流

### 发送消息流程

```
用户输入 (Monaco)
 → ChatStore.sendMessage()
   → 构造 Message(user) + POST /chat（OpenAI-compatible 格式）
   → sse.ts 读取 SSE stream
     → delta.content → 追加到 Message.content（流式渲染）
     → delta.tool_calls → 插入 ToolCall 卡片
       → 读操作 (glob/read/grep) → ipcClient 直接执行 → 结果显示
       → 写操作 (write/edit) → status='confirming' → 弹出确认按钮
         → 用户点"是" → ipcClient 执行 → 结果追加
         → 用户点"否" → 取消 → 继续流
     → [DONE] → Message.isStreaming = false
```

### Store 职责

| Store | 职责 | 关键操作 |
|-------|------|----------|
| conversationStore | 会话 CRUD、当前选中 | create, delete, select, rename |
| chatStore | 发送消息、流式更新、tool call 管理 | sendMessage, confirmToolCall, cancelToolCall |
| settingsStore | API 配置读写、通过 IPC 持久化 | save, load |
| commandStore | 内置指令列表、/ 过滤匹配 | filter, execute |

### IPC 通道

```
invoke (渲染 → 主):
  file:glob(pattern)        → string[]
  file:read(path)           → string
  file:grep(pattern, path)  → string[]
  file:write(path, content) → void
  file:edit(path, old, new) → void
  workspace:select()        → string | null
  settings:save(settings)   → void
  settings:load()           → Settings
```

---

## 后端 API 协议（OpenAI-compatible）

```
POST /v1/chat/completions
Content-Type: application/json
Authorization: Bearer {apiKey}

{
  "model": "{model}",
  "messages": [...],
  "stream": true
}

SSE Response:
data: {"id":"...","choices":[{"delta":{"content":"Hello"}}]}
data: {"id":"...","choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"glob","arguments":"**/*.ts"}}]}}]}
data: [DONE]
```

### 内置指令（前端预处理）

| 指令 | 标签 | 处理方式 |
|------|------|----------|
| /explain | 解释代码 | 将选中文本包装为"请解释以下代码："发送 |
| /fix | 修复问题 | 将选中文本包装为"请修复以下代码的问题："发送 |
| /test | 生成测试 | 将选中文本包装为"请为以下代码生成测试："发送 |
| /refactor | 重构代码 | 将选中文本包装为"请重构以下代码："发送 |

---

## 第一版功能范围

| 功能 | 状态 |
|------|------|
| 多会话管理（新建/切换/删除） | 实现 |
| 设置面板（API 配置 + 工作空间） | 实现 |
| 聊天消息列表 + Markdown 渲染 | 实现 |
| SSE 流式接收 | 实现 |
| / 指令菜单 | 实现 |
| 读操作 (glob/read/grep) 本地执行 | 实现 |
| 写操作 (write/edit) 确认 + 本地执行 | 实现 |
| Tool Call 卡片展示 | 实现 |
| @ 文件引用 | 后续 |
| 暗色模式 | 实现（默认暗色） |

---

## 打包配置

```yaml
# electron-builder.yml
appId: com.agent.electron-app
productName: Agent Desktop
directories:
  output: dist
  buildResources: resources
win:
  target:
    - target: nsis
      arch: [x64]
nsis:
  oneClick: false
  allowToChangeInstallationDirectory: true
  createDesktopShortcut: true
  shortcutName: Agent Desktop
```

- 开发：`npm run dev` 启动 Vite + Electron
- 构建：`npm run build` 编译 + 打包 NSIS 安装包

---

## 非功能需求

- 消息列表渲染：虚拟滚动不做（第一版消息量不大），但需做好自动滚底
- Monaco 输入框：设置为单行模式（lineHeight=1），Shift+Enter 换行
- 安全性：渲染进程通过 preload 暴露的白名单 API 访问 Node.js 能力，不直接开启 nodeIntegration
