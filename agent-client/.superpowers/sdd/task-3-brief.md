### Task 3: Rewrite chatStore — remove demo code, add real API sendMessage

**Files:**
- Modify: `src/renderer/src/stores/chatStore.ts`

**Interfaces:**
- Consumes: `sendChatMessage` from `../services/api`, `Task.lastSeq` from Task 1
- Produces: `sendMessage(text)` that calls real API and maps ServerEvents to UI state

- [ ] **Step 1: Replace imports and remove demo helpers**

Remove these imports:
```ts
// REMOVE:
import type { Message, ToolCall, AppMode, SceneMode } from '../types'
// ADD:
import type { Message, ToolCall, AppMode, SceneMode, ServerEvent } from '../types'
import { sendChatMessage, reconnectStream, planApi, buildApi } from '../services/api'
```

Keep: `import { create } from 'zustand'`, `import { useTaskStore } from './taskStore'`, `import { useQueueStore } from './queueStore'`, `import { useModeStore } from './modeStore'`

- [ ] **Step 2: Remove msgId/toolId counters and gen functions**

Remove these lines:
```ts
// REMOVE:
let msgId = 100
let toolIdCounter = 200
function genMsgId(): string {
  return `msg-${Date.now()}-${msgId++}`
}
function genToolId(): string {
  return `tool-${Date.now()}-${toolIdCounter++}`
}
```

Replace with:
```ts
let msgIdCounter = 100
function genMsgId(): string {
  return `msg-${Date.now()}-${msgIdCounter++}`
}
```

- [ ] **Step 3: Simplify ChatState interface**

Replace the interface:

```ts
interface ChatState {
  isProcessing: boolean
  currentEditingPlanMsgIdx: number | null

  sendMessage: (text: string) => void
  // Build mode
  confirmTool: () => void
  skipTool: () => void
  stopTools: () => void
  // Plan mode
  selectPlanOption: (msgIdx: number, value: string) => void
  answerPlanQuestion: (msgIdx: number, textAnswer?: string) => void
  confirmPlan: () => void
  editPlan: (msgIdx: number) => void
  rejectPlan: () => void
  // Plan editor
  openPlanEditor: (msgIdx: number, planText: string) => void
  closePlanEditor: () => void
  savePlanFromEditor: (newText: string) => void
  cancelPlanEdit: () => void
}
```

Removed from old interface: `pendingTool`, `planPending`, `genMsgId`, `genToolId` (were in module scope).

- [ ] **Step 4: Write the initial store creation with empty method stubs**

```ts
export const useChatStore = create<ChatState>((set, get) => ({
  isProcessing: false,
  currentEditingPlanMsgIdx: null,

  // ===== SEND =====
  sendMessage: (text: string) => {
    // Implemented in Step 5
  },

  // ===== BUILD MODE =====
  confirmTool: () => {
    // Implemented in Task 5
  },

  skipTool: () => {
    // Implemented in Task 5
  },

  stopTools: () => {
    // Implemented in Task 5
  },

  // ===== PLAN MODE =====
  selectPlanOption: (msgIdx: number, value: string) => {
    // UI-side only — no-op for now
  },

  answerPlanQuestion: (msgIdx: number, textAnswer?: string) => {
    // Will be handled by plan question events from server
  },

  confirmPlan: () => {
    // Implemented in Task 4
  },

  editPlan: (msgIdx: number) => {
    // Implemented in Task 4
  },

  rejectPlan: () => {
    // Implemented in Task 4
  },

  // ===== PLAN EDITOR =====
  openPlanEditor: (msgIdx: number, _planText: string) => {
    set({ currentEditingPlanMsgIdx: msgIdx })
  },

  closePlanEditor: () => {
    const taskStore = useTaskStore.getState()
    const idx = get().currentEditingPlanMsgIdx
    if (idx != null) {
      taskStore.updateLastAssistantMessage((m) => ({ ...m, planEditing: false }))
    }
    set({ currentEditingPlanMsgIdx: null })
  },

  savePlanFromEditor: (newText: string) => {
    // Implemented in Task 4
  },

  cancelPlanEdit: () => {
    get().closePlanEditor()
  }
}))
```

- [ ] **Step 5: Write `sendMessage` with real API + ServerEvent handler**

```ts
sendMessage: (text: string) => {
  if (!text) return

  // If processing, queue the message
  if (get().isProcessing) {
    useQueueStore.getState().addToQueue(text)
    return
  }

  const taskStore = useTaskStore.getState()
  const task = taskStore.getCurrentTask()
  if (!task) return

  const modeStore = useModeStore.getState()
  const inputMode = modeStore.inputMode
  const sceneMode = modeStore.sceneMode

  // Add user message
  const userMsg: Message = {
    id: genMsgId(),
    role: 'user',
    content: text,
    timestamp: Date.now()
  }
  taskStore.addMessage(userMsg)

  set({ isProcessing: true })

  // Create assistant placeholder
  const assistantMsg: Message = {
    id: genMsgId(),
    role: 'assistant',
    content: '',
    thinking: '',
    tools: [],
    processCollapsed: false,
    isStreaming: true,
    timestamp: Date.now()
  }
  taskStore.addMessage(assistantMsg)

  // Track lastSeq for this stream
  let lastSeq = 0

  // ServerEvent → UI handler
  const handleEvent = (event: ServerEvent) => {
    lastSeq = event.seq

    switch (event.type) {
      case 'agent.thinking':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          thinking: (m.thinking || '') + event.delta
        }))
        break

      case 'agent.text':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          content: m.content + event.delta
        }))
        break

      case 'agent.tool_call':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          tools: [
            ...(m.tools || []),
            {
              id: event.tool_call_id,
              name: event.tool_name,
              status: 'running' as const,
              detail: typeof event.input === 'object'
                ? JSON.stringify(event.input).slice(0, 120)
                : undefined
            }
          ]
        }))
        break

      case 'agent.tool_result':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          tools: m.tools?.map((t) =>
            t.id === event.tool_call_id
              ? {
                  ...t,
                  status: 'done' as const,
                  result: event.result.output || event.result.error || 'Done'
                }
              : t
          )
        }))
        break

      case 'client.tool_request':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          tools: [
            ...(m.tools || []),
            {
              id: event.request_id,
              name: event.tool_name,
              status: 'pending' as const,
              detail: typeof event.input === 'object'
                ? JSON.stringify(event.input).slice(0, 120)
                : undefined
            }
          ]
        }))
        break

      case 'plan.generated':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          planGenerated: event.plan_text,
          planStatus: 'pending'
        }))
        break

      case 'plan.confirmed':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          planStatus: 'confirmed'
        }))
        break

      case 'plan.rejected':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          planStatus: 'rejected',
          processCollapsed: true
        }))
        break

      case 'plan.edited':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          planGenerated: event.new_plan_text
        }))
        break

      case 'build.step_pending':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          tools: [
            ...(m.tools || []),
            {
              id: event.tool_call_id,
              name: event.tool_name,
              status: 'pending' as const,
              detail: event.reasoning || JSON.stringify(event.input).slice(0, 120)
            }
          ]
        }))
        break

      case 'build.step_confirmed':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          tools: m.tools?.map((t) =>
            t.id === event.tool_call_id
              ? { ...t, status: 'running' as const }
              : t
          )
        }))
        break

      case 'build.step_skipped':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          tools: m.tools?.filter((t) => t.id !== event.tool_call_id)
        }))
        break

      case 'build.aborted':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          processCollapsed: true
        }))
        set({ isProcessing: false })
        break

      case 'message.complete':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          processCollapsed: true,
          isStreaming: false
        }))
        // Save lastSeq to task for reconnection
        taskStore.updateTaskSeq(lastSeq)
        set({ isProcessing: false })
        break

      case 'message.error':
        if (event.fatal) {
          taskStore.updateLastAssistantMessage((m) => ({
            ...m,
            content: m.content || `**Error:** ${event.error}`,
            isStreaming: false,
            processCollapsed: true
          }))
          taskStore.updateTaskSeq(lastSeq)
          set({ isProcessing: false })
        }
        break

      case 'message.queued':
        // Server confirmed the message is queued
        break

      case 'queue.updated':
        // Server sent updated queue state
        break

      case 'message.start':
        // Message processing started on server
        break

      case 'session.timeout':
        // Session idle timeout warning — could show toast
        break

      case 'session.recovered':
        // Session recovered after reconnect
        break

      case 'heartbeat':
        // Keep-alive heartbeat
        break

      default:
        break
    }
  }

  // Fire the API call (async, runs in background)
  sendChatMessage({
    sessionId: task.id,
    content: text,
    mode: inputMode,
    sceneMode: sceneMode,
    workspace: '',  // Will be filled from settings if needed
    model: '',      // Will be filled from settings if needed
    onEvent: handleEvent,
    onError: (err) => {
      taskStore.updateLastAssistantMessage((m) => ({
        ...m,
        content: `**Error:** ${err.message}`,
        isStreaming: false,
        processCollapsed: true
      }))
      taskStore.updateTaskSeq(lastSeq)
      set({ isProcessing: false })
    },
    onDone: () => {
      // Process next queued message
      const queueStore = useQueueStore.getState()
      if (queueStore.queue.length > 0) {
        setTimeout(() => {
          const next = queueStore.shiftQueue()
          if (next) {
            useChatStore.getState().sendMessage(next)
          }
        }, 300)
      }
    }
  })
},
```

- [ ] **Step 6: Add `updateTaskSeq` to taskStore**

In `src/renderer/src/stores/taskStore.ts`, add the method to the interface and implementation:

```ts
// In TaskState interface, add:
updateTaskSeq: (seq: number) => void

// In create() call, add:
updateTaskSeq: (seq: number) => {
  set((s) => ({
    tasks: s.tasks.map((t) =>
      t.id === s.currentTaskId ? { ...t, lastSeq: seq } : t
    )
  }))
},
```

- [ ] **Step 7: Remove the entire demo simulation section**

Delete everything from `// ===== DEMO SIMULATION =====` to the end of the file. This includes:
- `simTimer` variable
- `simulateAssistant()` function and all inner functions (`typeThinking`, `startPlanQuestions`, `showPlanGenerated`, `autoExecuteTools`, `startTools`, `runNextTool`, `finish`, `getCurrentMsg`)
- `getDemoResponse()` function

- [ ] **Step 8: Commit**

```bash
git add src/renderer/src/stores/chatStore.ts src/renderer/src/stores/taskStore.ts
git commit -m "feat: replace demo simulation with real NDJSON API streaming"
```

---

