/**
 * Electron API compatibility shim for Windows.
 *
 * On Windows, require('electron') resolves to node_modules/electron/index.js
 * which returns the exe path (a string). This file creates a virtual node_modules
 * module that provides the real Electron API.
 *
 * This is applied as a Rollup banner in the main process build output.
 *
 * Approach: Intercept Module._resolveFilename when it tries to find 'electron'.
 * Redirect it to a virtual module file we create that returns the real API.
 */

(function () {
  if (process.platform !== 'win32') return

  var Module = require('module')
  var fs = require('fs')
  var path = require('path')

  var electronShimPath = path.join(__dirname, '..', 'src', 'main', '_electron-shim.cjs')
  var electronShimDir = path.dirname(electronShimPath)

  // Check if the shim file already exists (written by the bootstrap)
  if (fs.existsSync(electronShimPath)) {
    // Patch resolution: redirect 'electron' to our shim
    var _resolveFilename = Module._resolveFilename
    Module._resolveFilename = function (request: string, parent: unknown) {
      if (request === 'electron' || request === 'electron/main') {
        // Check if we're inside the Electron runtime
        if (process.versions && process.versions.electron) {
          return electronShimPath
        }
      }
      return _resolveFilename.apply(this, arguments)
    }
  }
})()
