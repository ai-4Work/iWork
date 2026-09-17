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

