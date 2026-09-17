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

