var Module = require('module');

console.log('[preload] Running inside Electron', process.versions.electron);

var virtualElectron = {
  app: {
    whenReady: function() {
      console.log('[virtual] app.whenReady called!');
      return Promise.resolve();
    },
    on: function() { console.log('[virtual] app.on:', arguments[0]); },
    quit: function() { console.log('[virtual] app.quit'); },
    getName: function() { return 'Agent Desktop'; },
    isPackaged: false,
    setAppUserModelId: function() {},
  },
  BrowserWindow: function() {
    console.log('[virtual] BrowserWindow created');
    return {
      on: function() {},
      show: function() {},
      loadURL: function() {},
      loadFile: function() {},
      webContents: { setWindowOpenHandler: function() {} },
      getAllWindows: function() { return []; }
    };
  },
  shell: { openExternal: function() {} },
};

var electronPath = Module._resolveFilename('electron', module, false);
require.cache[electronPath] = {
  id: electronPath,
  filename: electronPath,
  loaded: true,
  exports: virtualElectron
};

console.log('[preload] Virtual electron module injected');
