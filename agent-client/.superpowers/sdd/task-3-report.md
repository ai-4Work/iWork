# Task 3 Report: Rewrite chatStore

## Status: DONE

## What was done

1. **Removed all demo code**: Deleted `getDemoResponse()` (~65 lines), `simulateAssistant()` with all inner functions (~240 lines), `simTimer` variable, `pendingTool` state, `planPending` state, and `genToolId()`.

2. **Simplified ChatState interface**: Removed `pendingTool` and `planPending` from the interface. All plan/build methods are stubs for Tasks 4+5.

3. **Implemented `sendMessage()` with real API**: Calls `sendChatMessage()` from `../services/api` with a ServerEvent handler that updates the assistant message via `taskStore.updateLastAssistantMessage()`.

4. **Added `reconnect()` method**: Uses `reconnectStream()` with the same event handler pattern.

5. **Extracted `createEventHandler()` helper**: A shared function that takes `taskStore`, `set`, and a mutable `lastSeqRef` object, returning a `(event: ServerEvent) => void` handler. Used by both `sendMessage` and `reconnect` to avoid code duplication.

6. **Handles all ServerEvent types**: Every event type in the `ServerEvent` union is handled in the switch statement, satisfying TypeScript exhaustiveness. No-op handlers have comments explaining why they're no-ops.

7. **Kept `genMsgId()`**, removed `genToolId()`. Renamed `msgId` counter to `msgIdCounter`.

8. **`updateTaskSeq` already existed** in taskStore.ts -- no changes needed there.

## TypeScript verification

`npx tsc --noEmit --project tsconfig.web.json` -- **0 errors, clean build**

## Commits

- `973626c` feat: replace demo simulation with real NDJSON API streaming

## Concerns

- **`build.step_confirmed` and `build.step_skipped` lack `tool_call_id`** in the ServerEvent type definition. The current implementation matches by `tool_name` + `status: 'pending'` as a workaround, which could match the wrong tool if there are multiple pending tools with the same name. Consider adding `tool_call_id` to these event types.
- **`planApi` and `buildApi` imports were intentionally omitted** since the plan/build methods are stubs. They should be re-imported when Tasks 4 and 5 implement those methods.
- **Workspace and model are hardcoded to empty strings** -- Task 7 will wire settings.
