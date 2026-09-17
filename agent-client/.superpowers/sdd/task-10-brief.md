### Task 10: Global Styles

**Files:**
- Create: `src/renderer/src/styles/globals.css`

- [ ] **Step 1: Create global styles**

`src/renderer/src/styles/globals.css`:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html, body, #root {
  height: 100%;
  overflow: hidden;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  background-color: #181825;
  color: #e0e0e0;
}

::-webkit-scrollbar {
  width: 6px;
}

::-webkit-scrollbar-track {
  background: transparent;
}

::-webkit-scrollbar-thumb {
  background: #3b3b5c;
  border-radius: 3px;
}

::-webkit-scrollbar-thumb:hover {
  background: #4a4a6a;
}
```

- [ ] **Step 2: Commit**

```bash
git add src/renderer/src/styles/globals.css
git commit -m "feat: add global styles with dark theme"
```

---

