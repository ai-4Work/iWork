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
            content: m.content || `**Error:** ${event.error}`,
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
            content: m.content || `**Error:** ${event.error}`,
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

