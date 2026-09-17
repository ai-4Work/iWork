#!/usr/bin/env node
/**
 * Minimal Electron main process to test require('electron') on Windows.
 * This is loaded by electron.exe directly, outside of electron-vite.
 */

// Step 1: Try require('electron')
var e = require('electron');
console.log('Step 1: require("electron") returns:', typeof e);
if (typeof e === 'string') {
  console.log('  -> BUG CONFIRMED: electron is a string (exe path)');
} else if (e && e.app) {
  console.log('  -> OK: electron API is available');
}

// Step 2: Try to access linked bindings
console.log('\nStep 2: Testing _linkedBinding...');
var bindings = [
  'electron_browser_app',
  'electron_browser_window',
  'electron_browser_shell',
  'electron_browser_ipc',
  'electron_browser_dialog',
  'electron_common_shell',
  'electron_common_ipc',
];
bindings.forEach(function(name) {
  try {
    var b = process._linkedBinding(name);
    console.log('  ' + name + ': OK (' + Object.keys(b).slice(0, 6).join(',') + ')');
  } catch(err) {
    console.log('  ' + name + ': FAILED - ' + err.message.substring(0, 50));
  }
});

// Step 3: If electron is a string, can we construct the API?
console.log('\nStep 3: Building electron API from bindings...');
try {
  var appBinding = process._linkedBinding('electron_browser_app');
  if (appBinding.app) {
    console.log('  app.whenReady: ' + typeof appBinding.app.whenReady);
    // Test if app.whenReady works
    appBinding.app.whenReady().then(function() {
      console.log('  SUCCESS: app.whenReady() callback fired!');
      // Create a BrowserWindow
      try {
        var bwBinding = process._linkedBinding('electron_browser_window');
        console.log('  BrowserWindow binding: ' + Object.keys(bwBinding).slice(0, 5).join(','));
      } catch(e2) {
        console.log('  BrowserWindow: ' + e2.message.substring(0, 50));
      }
      appBinding.app.quit();
    });
  }
} catch(e3) {
  console.log('  ERROR: ' + e3.message.substring(0, 100));
}
