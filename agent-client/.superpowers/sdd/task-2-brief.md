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

