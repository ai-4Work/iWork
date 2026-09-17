### Task 8: TypeScript check and fix errors

**Files:**
- Verify: `src/renderer/src/stores/chatStore.ts`
- Verify: `src/renderer/src/stores/taskStore.ts`
- Verify: `src/renderer/src/types/index.ts`

- [ ] **Step 1: Run TypeScript check**

```bash
npx tsc --noEmit --project tsconfig.web.json 2>&1 | head -50
```

Expected: No errors related to chatStore, taskStore, or types. Fix any type errors that appear.

- [ ] **Step 2: Run the build**

```bash
npm run build
```

Expected: Build succeeds. Fix any build errors.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: resolve TypeScript errors from API integration"
```
