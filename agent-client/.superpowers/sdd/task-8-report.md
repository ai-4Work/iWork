## Task 8 Complete

**Commit:** `a8e0fcb` - `feat: add SSE parser and chat API service`

**Files created:**

1. `src/renderer/src/services/sse.ts` (84 lines)
   - `SSEDelta` interface — raw SSE delta from the API (content + tool_calls)
   - `SSEChunk` interface — parsed chunk with accumulated content, toolCalls map, and done flag
   - `parseSSEStream()` — async generator that reads a `ReadableStreamDefaultReader<Uint8Array>`, buffers and splits on newlines, parses `data:` line payloads as JSON, accumulates tool call fragments by index, and yields `SSEChunk` objects. Handles `[DONE]` termination and skips malformed JSON lines silently.

2. `src/renderer/src/services/api.ts` (51 lines)
   - `sendChatMessage(messages, onChunk, onError, onDone)` — reads settings from `useSettingsStore.getState()`, posts to `{apiBaseUrl}/v1/chat/completions` with Bearer auth, streams the response body through `parseSSEStream`, calling `onChunk` for each content delta and `onDone` on stream end. Catches fetch/parse errors and forwards them to `onError`.
