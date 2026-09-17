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

