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

