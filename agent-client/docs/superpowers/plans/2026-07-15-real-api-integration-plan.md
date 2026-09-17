# Real API Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all demo/mock response logic in chatStore.ts with real NDJSON streaming API calls, integrate Plan/Build confirmation endpoints, and support reconnection.

**Architecture:** `sendMessage()` opens a long-lived NDJSON stream via `sendChatMessage()`. All ServerEvents (thinking, text, tool calls, plan, build) update the assistant message reactively through `updateLastAssistantMessage`. Plan/Build confirmations are out-of-band POST requests (`planApi.*`, `buildApi.*`) that signal the server; the stream continues with the server's response. A `lastSeq` counter per task enables reconnection via `reconnectStream()`.

**Tech Stack:** TypeScript, Zustand, Fetch API, NDJSON streaming

## Global Constraints

- API base URL defaults to `http://localhost:8080`
- Frontend-generated task ID (`task-{timestamp}-{counter}`) serves as session ID
- All three modes (ask/plan/build) use the same `sendChatMessage` stream
- Plan/Build confirmations are separate REST calls; stream stays open
- `lastSeq` tracked per-task for reconnection

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/renderer/src/types/index.ts` | Add `lastSeq` to Task, `sessionId` to chatStore state |
| `src/renderer/src/stores/taskStore.ts` | Dedup empty tasks, manage `lastSeq` per task |
| `src/renderer/src/stores/chatStore.ts` | Core: sendMessage via API, ServerEvent→UI mapping, plan/build API calls, reconnect |

---

### Task 1: Add `lastSeq` to Task type

**Files:**
- Modify: `src/renderer/src/types/index.ts`

**Interfaces:**
- Produces: `Task.lastSeq: number` — highest seen event sequence number, used by reconnect

- [ ] **Step 1: Add `lastSeq` field to Task interface**

```ts
// In src/renderer/src/types/index.ts, modify the Task interface:

export interface Task {
  id: string
  title: string
  time: string
  active: boolean
  messages: Message[]
  lastSeq: number  // <-- add this line
}
```

- [ ] **Step 2: Commit**

```bash
git add src/renderer/src/types/index.ts
git commit -m "feat: add lastSeq field to Task type for reconnection support"
```

---

### Task 2: Dedup empty tasks in taskStore + init lastSeq

**Files:**
- Modify: `src/renderer/src/stores/taskStore.ts`

**Interfaces:**
- Consumes: `Task.lastSeq` from Task 1
- Produces: `create()` skips creation if empty "新建任务" exists; new tasks init `lastSeq: 0`

- [ ] **Step 1: Update `create()` with dedup logic**

In `src/renderer/src/stores/taskStore.ts`, replace the `create` method:

```ts
create: () => {
  const state = get()
  // Dedup: if an empty "新建任务" already exists, just select it
  const existing = state.tasks.find(
    t => t.title === '新建任务' && t.messages.length === 0
  )
  if (existing) {
    set((s) => ({
      currentTaskId: existing.id,
      tasks: s.tasks.map((t) => ({ ...t, active: t.id === existing.id }))
    }))
    return existing.id
  }

  const id = genId()
  set((s) => ({
    tasks: [
      { id, title: '新建任务', time: '刚才', active: false, messages: [], lastSeq: 0 },
      ...s.tasks.map((t) => ({ ...t, active: false }))
    ],
    currentTaskId: id
  }))
  return id
},
```

- [ ] **Step 2: Update `addMessage` to init `lastSeq` if missing**

The `addMessage` method creates tasks — ensure `lastSeq` is set. Since `addMessage` only modifies existing tasks (it doesn't create new task entries), and `create()` already sets `lastSeq: 0`, we need to add a migration for existing demo tasks. Update the `tasks` initial state to include `lastSeq: 0` on all demo tasks.

In the `DEMO_TASKS` array, add `lastSeq: 0` to each task:

```ts
const DEMO_TASKS: Task[] = [
  {
    id: 'task-1',
    title: '新建任务',
    time: '刚才',
    active: true,
    messages: [],
    lastSeq: 0     // <-- add
  },
  // ... same for task-2, task-3, task-4
]
```

- [ ] **Step 3: Update `duplicate` to include `lastSeq`**

```ts
duplicate: (id: string) => {
  const task = get().tasks.find((t) => t.id === id)
  if (!task) return
  const newId = genId()
  set((s) => ({
    tasks: [
      {
        id: newId,
        title: task.title + ' (副本)',
        time: '刚才',
        active: false,
        messages: task.messages ? JSON.parse(JSON.stringify(task.messages)) : [],
        lastSeq: 0    // <-- add
      },
      ...s.tasks
    ]
  }))
},
```

- [ ] **Step 4: Commit**

```bash
git add src/renderer/src/stores/taskStore.ts
git commit -m "feat: dedup empty tasks on create, add lastSeq field"
```

---

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
            content: m.content || `**Error:** ${event.message}`,
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

### Task 4: Plan mode API integration

**Files:**
- Modify: `src/renderer/src/stores/chatStore.ts`

**Interfaces:**
- Consumes: `planApi` from `../services/api`
- Produces: `confirmPlan()`, `rejectPlan()`, `editPlan()`, `savePlanFromEditor()` call real API

- [ ] **Step 1: Implement `confirmPlan` — call planApi.confirm**

```ts
confirmPlan: () => {
  const taskStore = useTaskStore.getState()
  const task = taskStore.getCurrentTask()
  if (!task) return

  // Optimistically update local state
  taskStore.updateLastAssistantMessage((m) => ({
    ...m,
    planStatus: 'confirmed'
  }))

  // Call API (fire-and-forget — stream handles the rest)
  planApi.confirm(task.id).catch((err) => {
    console.error('Plan confirm failed:', err)
    // Revert on failure
    taskStore.updateLastAssistantMessage((m) => ({
      ...m,
      planStatus: 'pending'
    }))
  })
},
```

- [ ] **Step 2: Implement `rejectPlan` — call planApi.reject**

```ts
rejectPlan: () => {
  const taskStore = useTaskStore.getState()
  const task = taskStore.getCurrentTask()
  if (!task) return

  taskStore.updateLastAssistantMessage((m) => ({
    ...m,
    planStatus: 'rejected',
    processCollapsed: true
  }))

  planApi.reject(task.id).catch((err) => {
    console.error('Plan reject failed:', err)
  })
},
```

- [ ] **Step 3: Implement `editPlan` — same as before but no planPending**

```ts
editPlan: (msgIdx: number) => {
  const taskStore = useTaskStore.getState()
  const task = taskStore.getCurrentTask()
  if (!task) return
  const msg = task.messages[msgIdx]
  if (!msg?.planGenerated) return

  taskStore.updateLastAssistantMessage((m) => ({ ...m, planEditing: true }))
  set({ currentEditingPlanMsgIdx: msgIdx })
},
```

- [ ] **Step 4: Implement `savePlanFromEditor` — updates local + calls planApi.edit**

```ts
savePlanFromEditor: (newText: string) => {
  const idx = get().currentEditingPlanMsgIdx
  const taskStore = useTaskStore.getState()
  const task = taskStore.getCurrentTask()

  if (idx != null) {
    taskStore.updateLastAssistantMessage((m) => ({
      ...m,
      planGenerated: newText,
      planEditing: false
    }))
  }
  set({ currentEditingPlanMsgIdx: null })

  // Call API
  if (task) {
    planApi.edit(task.id, newText).catch((err) => {
      console.error('Plan edit API failed:', err)
    })
  }
},
```

- [ ] **Step 5: Commit**

```bash
git add src/renderer/src/stores/chatStore.ts
git commit -m "feat: integrate Plan mode with real planApi endpoints"
```

---

### Task 5: Build mode API integration

**Files:**
- Modify: `src/renderer/src/stores/chatStore.ts`

**Interfaces:**
- Consumes: `buildApi` from `../services/api`
- Produces: `confirmTool()`, `skipTool()`, `stopTools()` call real API

- [ ] **Step 1: Implement Build mode methods**

```ts
confirmTool: () => {
  const taskStore = useTaskStore.getState()
  const task = taskStore.getCurrentTask()
  if (!task) return

  buildApi.confirm(task.id).catch((err) => {
    console.error('Build confirm failed:', err)
  })
},

skipTool: () => {
  const taskStore = useTaskStore.getState()
  const task = taskStore.getCurrentTask()
  if (!task) return

  buildApi.skip(task.id).catch((err) => {
    console.error('Build skip failed:', err)
  })
},

stopTools: () => {
  const taskStore = useTaskStore.getState()
  const task = taskStore.getCurrentTask()
  if (!task) return

  buildApi.abort(task.id).catch((err) => {
    console.error('Build abort failed:', err)
  })
},
```

- [ ] **Step 2: Commit**

```bash
git add src/renderer/src/stores/chatStore.ts
git commit -m "feat: integrate Build mode with real buildApi endpoints"
```

---

### Task 6: Reconnection support

**Files:**
- Modify: `src/renderer/src/stores/chatStore.ts`

**Interfaces:**
- Consumes: `reconnectStream` from `../services/api`, `Task.lastSeq`
- Produces: `reconnect()` method that replays missed events

- [ ] **Step 1: Add `reconnect` method to ChatState interface and implementation**

```ts
// In ChatState interface, add:
reconnect: () => void

// In create(), add:
reconnect: () => {
  const taskStore = useTaskStore.getState()
  const task = taskStore.getCurrentTask()
  if (!task) return

  const sinceSeq = task.lastSeq + 1

  const handleEvent = (event: ServerEvent) => {
    // Same event handler pattern as sendMessage
    // (extract to a shared helper — see Step 2)

    taskStore.updateTaskSeq(event.seq)

    switch (event.type) {
      case 'agent.thinking':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m, thinking: (m.thinking || '') + event.delta
        }))
        break
      case 'agent.text':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m, content: m.content + event.delta
        }))
        break
      case 'agent.tool_call':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          tools: [...(m.tools || []), {
            id: event.tool_call_id,
            name: event.tool_name,
            status: 'running' as const,
            detail: typeof event.input === 'object'
              ? JSON.stringify(event.input).slice(0, 120)
              : undefined
          }]
        }))
        break
      case 'agent.tool_result':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          tools: m.tools?.map((t) =>
            t.id === event.tool_call_id
              ? { ...t, status: 'done' as const, result: event.result.output || event.result.error || 'Done' }
              : t
          )
        }))
        break
      case 'message.complete':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m, processCollapsed: true, isStreaming: false
        }))
        set({ isProcessing: false })
        break
      case 'message.error':
        if (event.fatal) {
          taskStore.updateLastAssistantMessage((m) => ({
            ...m,
            content: m.content || `**Error:** ${event.message}`,
            isStreaming: false,
            processCollapsed: true
          }))
          set({ isProcessing: false })
        }
        break
      default:
        break
    }
  }

  reconnectStream(
    task.id,
    sinceSeq,
    handleEvent,
    (err) => console.error('Reconnect error:', err),
    () => set({ isProcessing: false })
  )
},
```

- [ ] **Step 2: Extract shared `createEventHandler` helper**

To avoid duplicating the event handler between `sendMessage` and `reconnect`, extract a helper function. Add this above the `create()` call:

```ts
function createEventHandler(
  taskStore: ReturnType<typeof useTaskStore.getState>,
  set: (partial: Partial<ChatState>) => void,
  lastSeqRef: { current: number }
) {
  return (event: ServerEvent) => {
    lastSeqRef.current = event.seq

    switch (event.type) {
      case 'agent.thinking':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m, thinking: (m.thinking || '') + event.delta
        }))
        break
      case 'agent.text':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m, content: m.content + event.delta
        }))
        break
      case 'agent.tool_call':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          tools: [...(m.tools || []), {
            id: event.tool_call_id,
            name: event.tool_name,
            status: 'running' as const,
            detail: typeof event.input === 'object'
              ? JSON.stringify(event.input).slice(0, 120)
              : undefined
          }]
        }))
        break
      case 'agent.tool_result':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          tools: m.tools?.map((t) =>
            t.id === event.tool_call_id
              ? { ...t, status: 'done' as const, result: event.result.output || event.result.error || 'Done' }
              : t
          )
        }))
        break
      case 'client.tool_request':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          tools: [...(m.tools || []), {
            id: event.request_id,
            name: event.tool_name,
            status: 'pending' as const,
            detail: typeof event.input === 'object'
              ? JSON.stringify(event.input).slice(0, 120)
              : undefined
          }]
        }))
        break
      case 'plan.generated':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m, planGenerated: event.plan_text, planStatus: 'pending'
        }))
        break
      case 'plan.confirmed':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m, planStatus: 'confirmed'
        }))
        break
      case 'plan.rejected':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m, planStatus: 'rejected', processCollapsed: true
        }))
        break
      case 'plan.edited':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m, planGenerated: event.new_plan_text
        }))
        break
      case 'build.step_pending':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m,
          tools: [...(m.tools || []), {
            id: event.tool_call_id,
            name: event.tool_name,
            status: 'pending' as const,
            detail: event.reasoning || JSON.stringify(event.input).slice(0, 120)
          }]
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
          ...m, processCollapsed: true
        }))
        set({ isProcessing: false })
        break
      case 'message.complete':
        taskStore.updateLastAssistantMessage((m) => ({
          ...m, processCollapsed: true, isStreaming: false
        }))
        taskStore.updateTaskSeq(lastSeqRef.current)
        set({ isProcessing: false })
        break
      case 'message.error':
        if (event.fatal) {
          taskStore.updateLastAssistantMessage((m) => ({
            ...m,
            content: m.content || `**Error:** ${event.message}`,
            isStreaming: false,
            processCollapsed: true
          }))
          taskStore.updateTaskSeq(lastSeqRef.current)
          set({ isProcessing: false })
        }
        break
      default:
        break
    }
  }
}
```

Then update `sendMessage` to use `createEventHandler`:

```ts
sendMessage: (text: string) => {
  // ... initial setup (add messages, set isProcessing) ...

  const lastSeqRef = { current: 0 }
  const taskStore = useTaskStore.getState()
  const handleEvent = createEventHandler(taskStore, set, lastSeqRef)

  sendChatMessage({
    // ... options ...
    onEvent: handleEvent,
    onError: (err) => {
      taskStore.updateLastAssistantMessage((m) => ({
        ...m,
        content: `**Error:** ${err.message}`,
        isStreaming: false,
        processCollapsed: true
      }))
      taskStore.updateTaskSeq(lastSeqRef.current)
      set({ isProcessing: false })
    },
    onDone: () => {
      const queueStore = useQueueStore.getState()
      if (queueStore.queue.length > 0) {
        setTimeout(() => {
          const next = queueStore.shiftQueue()
          if (next) useChatStore.getState().sendMessage(next)
        }, 300)
      }
    }
  })
},
```

And update `reconnect`:

```ts
reconnect: () => {
  const taskStore = useTaskStore.getState()
  const task = taskStore.getCurrentTask()
  if (!task || !task.lastSeq) return

  const lastSeqRef = { current: task.lastSeq }
  const handleEvent = createEventHandler(taskStore, set, lastSeqRef)

  reconnectStream(
    task.id,
    task.lastSeq + 1,
    handleEvent,
    (err) => console.error('Reconnect error:', err),
    () => set({ isProcessing: false })
  )
},
```

- [ ] **Step 3: Commit**

```bash
git add src/renderer/src/stores/chatStore.ts
git commit -m "feat: add reconnection support with shared event handler"
```

---

### Task 7: Final integration — pass settings to sendChatMessage

**Files:**
- Modify: `src/renderer/src/stores/chatStore.ts`

**Interfaces:**
- Consumes: `useSettingsStore` from `./settingsStore`

Currently `sendChatMessage` is called with empty `workspace` and `model`. Wire up the settings store.

- [ ] **Step 1: Import settingsStore and read settings in sendMessage**

Add import:
```ts
import { useSettingsStore } from './settingsStore'
```

In `sendMessage`, read settings before calling the API:

```ts
const settings = useSettingsStore.getState().settings

sendChatMessage({
  sessionId: task.id,
  content: text,
  mode: inputMode,
  sceneMode: sceneMode,
  workspace: settings.workspacePath,
  model: settings.model,
  onEvent: handleEvent,
  onError: (err) => { /* ... */ },
  onDone: () => { /* ... */ }
})
```

- [ ] **Step 2: Commit**

```bash
git add src/renderer/src/stores/chatStore.ts
git commit -m "feat: wire settings workspace/model into sendChatMessage"
```

---

### Task 8: TypeScript check and fix errors

**Files:**
- Verify: `src/renderer/src/stores/chatStore.ts`
- Verify: `src/renderer/src/stores/taskStore.ts`
- Verify: `src/renderer/src/types/index.ts`

- [ ] **Step 1: Run TypeScript check**

```bash
npx tsc --noEmit --project tsconfig.web.json 2>&1 | head -50
```

Expected: No errors related to chatStore, taskStore, or types. Fix any type errors that appear.

- [ ] **Step 2: Run the build**

```bash
npm run build
```

Expected: Build succeeds. Fix any build errors.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: resolve TypeScript errors from API integration"
```
