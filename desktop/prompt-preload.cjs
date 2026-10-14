'use strict';
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('promptDialog', Object.freeze({
  ready: () => ipcRenderer.invoke('desktop:prompt-ready'),
  answer: value => ipcRenderer.send('desktop:prompt-answer', value)
}));
