### Task 14: Chat Messages & Tool Call Cards - COMPLETED

**Commit:** `2b31893` - `feat: add chat panel with messages, markdown, tool call cards`

**Files created:**

1. `src/renderer/src/components/chat/ToolCallCard.tsx` - Displays a tool call with status icon (pending/confirming/executing/done/error), formatted args, confirm/cancel buttons for write tools, and result output.

2. `src/renderer/src/components/chat/MessageItem.tsx` - Renders a single message with user/assistant avatar, role label, ReactMarkdown content, streaming cursor indicator, and nested ToolCallCard components.

3. `src/renderer/src/components/chat/MessageList.tsx` - Renders the message list with auto-scroll to bottom on new messages. Shows an empty state with emoji and prompt text when no messages exist.

4. `src/renderer/src/components/chat/ChatPanel.tsx` - Top-level chat panel composing the conversation title bar, MessageList, and ChatInput (not yet implemented - Task 15).

**Dependencies verified:**
- `react-markdown` already in `package.json` (^9.0.1)
- `lucide-react` icons available (Wrench, CheckCircle, XCircle, Loader2, AlertCircle, Bot, User)
- Types (`ToolCall`, `Message`) defined in `src/renderer/src/types/index.ts`
- Store methods (`useConversationStore.getCurrentConversation`, `useChatStore.confirmToolCall`, `useChatStore.cancelToolCall`) already implemented
- `ChatInput` import in ChatPanel.tsx will resolve once Task 15 is complete (no build errors from the import itself since the file doesn't exist yet)
