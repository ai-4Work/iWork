### Task 7: Final integration — pass settings to sendChatMessage

**Files:**
- Modify: `src/renderer/src/stores/chatStore.ts`

**Interfaces:**
- Consumes: `useSettingsStore` from `./settingsStore`

Currently `sendChatMessage` is called with empty `workspace` and `model`. Wire up the settings store.

- [ ] **Step 1: Import settingsStore and read settings in sendMessage**

Add import:
```ts
import { useSettingsStore } from './settingsStore'
```

In `sendMessage`, read settings before calling the API:

```ts
const settings = useSettingsStore.getState().settings

sendChatMessage({
  sessionId: task.id,
  content: text,
  mode: inputMode,
  sceneMode: sceneMode,
  workspace: settings.workspacePath,
  model: settings.model,
  onEvent: handleEvent,
  onError: (err) => { /* ... */ },
  onDone: () => { /* ... */ }
})
```

- [ ] **Step 2: Commit**

```bash
git add src/renderer/src/stores/chatStore.ts
git commit -m "feat: wire settings workspace/model into sendChatMessage"
```

---

