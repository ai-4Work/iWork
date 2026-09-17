### Task 4 Report: Plan mode API integration

**Status:** DONE

**File modified:** `src/renderer/src/stores/chatStore.ts`

**Changes:**
- Added `planApi` to the import line from `../services/api`
- Replaced 4 stub methods with full implementations:
  1. `confirmPlan()` — optimistically sets planStatus to 'confirmed', calls `planApi.confirm(task.id)`, reverts to 'pending' on error
  2. `rejectPlan()` — sets planStatus to 'rejected', collapses process, calls `planApi.reject(task.id)`
  3. `editPlan(msgIdx)` — validates message has `planGenerated`, sets planEditing to true, sets `currentEditingPlanMsgIdx`
  4. `savePlanFromEditor(newText)` — updates planGenerated locally, clears editing state, calls `planApi.edit(task.id, newText)`

**Verification:**
- `npx tsc --noEmit --project tsconfig.web.json` passed with zero errors
- Commit: `6f32362` — "feat: integrate Plan mode with real planApi endpoints"

**Concerns:** None
