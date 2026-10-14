'use strict';
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('desktopBridge', Object.freeze({
  prompt: (message, value = '') => ipcRenderer.sendSync('desktop:prompt', String(message).slice(0, 4000), String(value).slice(0, 20000)),
  updates: Object.freeze({
    getState: () => ipcRenderer.invoke('desktop:update-get-state'),
    check: () => ipcRenderer.invoke('desktop:update-check'),
    download: () => ipcRenderer.invoke('desktop:update-download'),
    install: () => ipcRenderer.invoke('desktop:update-install'),
    onState: listener => {
      if (typeof listener !== 'function') throw new TypeError('更新监听器必须是函数。');
      const handler = (_event, state) => listener(state);
      ipcRenderer.on('desktop:update-state', handler);
      return () => ipcRenderer.removeListener('desktop:update-state', handler);
    }
  })
}));
