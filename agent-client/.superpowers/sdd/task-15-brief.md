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

