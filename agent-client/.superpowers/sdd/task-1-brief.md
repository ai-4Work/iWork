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

