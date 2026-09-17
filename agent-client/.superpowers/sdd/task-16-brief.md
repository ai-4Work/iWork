### Task 16: Packaging Configuration

**Files:**
- Create: `electron-builder.yml`

- [ ] **Step 1: Create electron-builder.yml**

`electron-builder.yml`:

```yaml
appId: com.agent.electron-app
productName: Agent Desktop
directories:
  output: dist
  buildResources: resources
files:
  - out/**/*
  - '!out/renderer/src/**'
win:
  target:
    - target: nsis
      arch: [x64]
  icon: resources/icon.png
nsis:
  oneClick: false
  allowToChangeInstallationDirectory: true
  createDesktopShortcut: true
  shortcutName: Agent Desktop
  installerIcon: resources/icon.ico
  uninstallerIcon: resources/icon.ico
  installerHeaderIcon: resources/icon.ico
npmRebuild: false
```

- [ ] **Step 2: Verify build**

```bash
npm run build
```

Expected: Build completes without errors, output in `out/` directory.

- [ ] **Step 3: Verify package (optional, requires Windows)**

```bash
npm run package
```

Expected: NSIS installer created in `dist/` directory.

- [ ] **Step 4: Commit**

```bash
git add electron-builder.yml
git commit -m "feat: add electron-builder NSIS packaging config"
```

---

