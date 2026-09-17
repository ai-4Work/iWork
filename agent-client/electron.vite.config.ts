import { resolve } from 'path'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'
import react from '@vitejs/plugin-react'

// Shim prepended to the main process output.
// On Windows, require('electron') resolves to node_modules/electron/index.js
// which returns the exe path (a string) instead of the Electron API object.
// This shim intercepts module resolution and redirects to the built-in module.
const ELECTRON_SHIM = `
(function () {
  var Module = require('module');
  var _resolveFilename = Module._resolveFilename;
  Module._resolveFilename = function (request, parent) {
    if (request === 'electron') {
      try {
        var res = _resolveFilename.call(this, request, parent);
        var cached = require.cache[res];
        if (cached && typeof cached.exports === 'string') {
          // npm package is shadowing — remove cache entry
          delete require.cache[res];
        }
        var loaded = require(res);
        if (typeof loaded === 'string') {
          // Still got the npm package string — electron API missing
          // Fallback: use internal browser init which exports the API
          try { return _resolveFilename.call(this, 'electron/js2c/browser_init', parent); } catch(e2) {}
        }
        return res;
      } catch (e) {
        // MODULE_NOT_FOUND — electron npm package not installed (unlikely)
        try { return _resolveFilename.call(this, 'electron/js2c/browser_init', parent); } catch(e2) {}
        return _resolveFilename.call(this, request, parent);
      }
    }
    return _resolveFilename.call(this, request, parent);
  };
})();
`

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        output: {
          banner: ELECTRON_SHIM,
          format: 'cjs'
        }
      }
    }
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        output: {
          format: 'cjs'
        }
      }
    }
  },
  renderer: {
    // 默认取渲染进程 root（src/renderer），显式指回项目根，让 agent-client/.env 生效
    envDir: resolve('.'),
    resolve: {
      alias: {
        '@': resolve('src/renderer/src')
      }
    },
    plugins: [react()],
    server: {
      proxy: {
        '/api': {
          // target: 'http://127.0.0.1:8000',
          target: 'http://10.0.58.165:8000',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, '')
        }
      }
    }
  }
})
