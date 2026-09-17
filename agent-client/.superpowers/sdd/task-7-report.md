### Task 7: Command Store -- COMPLETE

**File created:** `src/renderer/src/stores/commandStore.ts`

**What was done:**
- Created a Zustand store (`useCommandStore`) with a `CommandState` interface
- Defined 4 built-in commands: `/explain`, `/fix`, `/test`, `/refactor`
- Implemented `filter(search)` method that strips the leading `/` and matches against `trigger`, `label`, and `description` fields
- The store imports the `Command` type from `../types` (interface: `id`, `trigger`, `label`, `description`)

**Verification:**
- File created at the exact path specified in the brief
- All fields in `Command` interface match the store's usage
- Committed as `feat: add command store with built-in / commands` (commit `203d23e`)
