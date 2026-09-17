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

