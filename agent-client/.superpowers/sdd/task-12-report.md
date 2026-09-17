### Task 12: Conversation List — Complete

**Status:** Done
**Commit:** `9eebaf5` — `feat: add conversation list with create/delete/select`

**File created:**
- `src/renderer/src/components/sidebar/ConversationList.tsx`

**What was done:**
- Created the `ConversationList` component consuming `useConversationStore` via Zustand selectors (`conversations`, `currentConversationId`, `create`, `select`, `delete`).
- Renders a "新对话" (New Conversation) button with a `Plus` icon (dashed border styling).
- Lists all conversations with title (truncated), relative timestamp, and a hover-reveal `Trash2` delete button.
- Active conversation is highlighted with `bg-sidebar-active`; inactive rows use `hover:bg-sidebar-hover`.
- Empty state shows "暂无对话" centered text.
- Uses `formatTime()` helper: shows HH:MM for today's conversations, "月 日" for older ones (zh-CN locale).
- Already wired into `Sidebar.tsx` which imports and renders `<ConversationList />` under the "对话" tab.
