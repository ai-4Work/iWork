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

