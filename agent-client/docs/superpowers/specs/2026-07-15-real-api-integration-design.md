# Real API Integration — Design Spec

**Date:** 2026-07-15
**Status:** draft

## Overview

Replace all demo/mock response logic in `chatStore.ts` with real API calls via `sendChatMessage` / `reconnectStream`. All three modes (Ask, Plan, Build) go through the NDJSON streaming API. Plan/Build confirmation actions call their respective REST endpoints.

## What changes

### 1. taskStore.ts — Deduplicate empty "新建任务"

`create()` checks if a task with `title === '新建任务' && messages.length === 0` already exists. If so, `select()` it instead of creating a new one.

### 2. chatStore.ts — Full rewrite (remove demo simulation)

**Remove:**
- `getDemoResponse()` (~100 lines)
- `simulateAssistant()` with all its internal helpers (typeThinking, startTools, runNextTool, autoExecuteTools, startPlanQuestions, showPlanGenerated, finish) (~200 lines)
- `simTimer` variable
- `planPending` state (user confirmations no longer held as in-memory Promises)

**`sendMessage(text)` new flow:**
1. Add user message to task
2. Create assistant placeholder (`isStreaming: true`, `processCollapsed: false`)
3. Set `isProcessing = true`
4. Call `api.sendChatMessage()` with sessionId = task.id, passing all mode/scene/workspace/model params
5. Each `onEvent` updates the assistant message in place via `updateLastAssistantMessage`
6. `onDone` sets `isProcessing = false`, collapses process, processes next queued message
7. `onError` sets error text on the assistant message, stops processing

**ServerEvent → UI state mapping:**

| Event | Action |
|-------|--------|
| `agent.thinking` | Append `delta` to `thinking` |
| `agent.text` | Append `delta` to `content` |
| `agent.tool_call` | Push new ToolCall with status `running` |
| `agent.tool_result` | Set matching ToolCall result + status `done` |
| `client.tool_request` | Push ToolCall with status `pending`, store `request_id` for later `submitToolResult` |
| `plan.generated` | Set `planGenerated` text, `planStatus = 'pending'` |
| `plan.confirmed` | Set `planStatus = 'confirmed'` |
| `plan.rejected` | Set `planStatus = 'rejected'` |
| `plan.edited` | Update `planGenerated` from server |
| `build.step_pending` | Push ToolCall with status `pending`, store `tool_call_id`/`step` |
| `build.step_confirmed` | Mark matching ToolCall as running → done |
| `build.step_skipped` | Remove matching ToolCall |
| `build.aborted` | Collapse process, stop execution |
| `message.complete` | Set `processCollapsed = true`, `isProcessing = false` |
| `message.error` (fatal) | Set error text, stop processing |
| `message.queued` | Update queue display via `queueStore` |
| `queue.updated` | Sync queue state from server |
| `session.timeout` | Show timeout warning |
| `session.recovered` | Trigger reconnect flow |

### 3. Plan mode — Real API calls

`confirmPlan()`, `editPlan()`, `rejectPlan()` all call their corresponding `planApi.*` methods with the session id (`task.id`). The `planPending` Promise pattern is removed — confirmation flows are driven by server events.

### 4. Build mode — Real API calls

`confirmTool()`, `skipTool()`, `stopTools()` call `buildApi.*` methods. Store `tool_call_id` and `step` from `build.step_pending` events so the confirm/skip calls target the right step.

### 5. Reconnection

- Track `lastSeq` (highest seen `seq` from server events) per task
- On reconnection (app restart, network recovery, switching back to a task): call `reconnectStream(sessionId, sinceSeq=lastSeq+1)`
- Replay missed events to rebuild current UI state

### 6. Queue handling

When `isProcessing` is true and user sends another message:
- Add to local `queueStore`
- Also show if server returns `message.queued` / `queue.updated` events

## What stays the same

- Task id generation (`task-{timestamp}-{counter}`) in taskStore
- Message id generation in chatStore
- All UI components (MessageList, MessageItem, ChatInput, etc.) — they already read from stores reactively
- `ndjson.ts` stream parser
- `api.ts` all API functions (no changes needed, already implemented correctly)
- `ChatStreamOptions` interface
- PlanEditorPanel, Sidebar, AppLayout

## ServerEvent field notes for implementation

- `agent.thinking` / `agent.text`: use `message_id` to ensure we're updating the correct assistant message
- `agent.tool_call`: `tool_call_id` is the key for matching with `agent.tool_result` and `build.step_pending`
- `client.tool_request`: needs `request_id` for `submitToolResult()` call
- `plan.generated`: `plan_text` is the full generated plan
- `message.complete`: `summary` contains token/duration stats (can display optionally)
- `message.error`: `fatal: true` means stream is done; `fatal: false` means a recoverable warning

## Files changed

| File | Change |
|------|--------|
| `src/renderer/src/stores/taskStore.ts` | `create()` dedup logic |
| `src/renderer/src/stores/chatStore.ts` | Remove demo, wire up real API, add reconnect |
| `src/renderer/src/types/index.ts` | Add `lastSeq` to Task, remove unused fields if any |
