### Task 2 Report: Dedup empty tasks in taskStore

**Status:** DONE

**Date:** 2026-07-15

**Commit:** `27f43f3` — feat: dedup empty tasks on create, add updateTaskSeq method

**Changes made to `src/renderer/src/stores/taskStore.ts`:**

1. **Dedup logic in `create()` (Step 1):** Before creating a new task, the method now checks if an empty "新建任务" (messages.length === 0) already exists. If one is found, it selects/activates that task and returns its ID instead of creating a new one. This prevents accumulating stale empty tasks when the user clicks "new task" multiple times.

2. **`updateTaskSeq` method:** Added to both the `TaskState` interface and the store implementation. Updates `lastSeq` on the current task only, needed by later tasks for reconnection support.

3. **Removed redundant state mutation:** The old `create()` had a `get().tasks.find(...); current.active = true` side-effect after `set()` which was both unnecessary (the set already handles active state) and a Zustand anti-pattern (direct state mutation outside set).

**TypeScript check:** `npx tsc --noEmit --project tsconfig.web.json` passed with zero errors.

**Self-review notes:**
- Steps 2 and 3 from the task brief (adding `lastSeq: 0` to DEMO_TASKS and `duplicate`) were already completed by the previous task (commit `15a33af`).
- Edge case: if multiple empty "新建任务" entries exist, `find()` returns the first one. Acceptable since the dedup logic prevents creating duplicates going forward.
- `updateTaskSeq` only updates the current task, matching the reconnection scenario where seq comes from the active streaming connection.

**Concerns:** None.
