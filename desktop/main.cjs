'use strict';
const { app, BrowserWindow, dialog, ipcMain, shell, session, webFrameMain } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const { Backend, ORIGIN, isLocalURL } = require('./backend.cjs');
const { DesktopUpdater, installUpdateIPC, CHANNEL } = require('./updater.cjs');
const smokeMode = process.argv.includes('--desktop-smoke');
const root = process.env.IC_DESKTOP_ROOT || path.join(process.env.LOCALAPPDATA || app.getPath('appData'), 'InfiniteCanvas');
app.setPath('userData', path.join(root, 'browser-profile'));
app.setPath('sessionData', path.join(root, 'browser-profile'));
app.setAppUserModelId('site.sansheng.infinitecanvas.desktop');
let win, backend, updates, quitting = false, closing = false, restarting = false, ready = false;
const promptWindows = new Map();
const boundsFile = path.join(root, 'window.json');
function trusted(event) { return win && event.sender === win.webContents && isLocalURL(event.senderFrame?.url); }
function external(url) { try { const parsed = new URL(url); if (['https:', 'http:'].includes(parsed.protocol) && !['127.0.0.1', 'localhost', '[::1]'].includes(parsed.hostname)) shell.openExternal(parsed.href).catch(() => {}); } catch {} }
function installIPC() {
  ipcMain.on('desktop:prompt', (event, message, value) => {
    if (!trusted(event) || typeof message !== 'string' || typeof value !== 'string' || promptWindows.size) { event.returnValue = null; return; }
    const child = new BrowserWindow({ parent: win, modal: true, width: 520, height: 310, resizable: false, autoHideMenuBar: true,
      webPreferences: { preload: path.join(__dirname, 'prompt-preload.cjs'), sandbox: true, contextIsolation: true, nodeIntegration: false } });
    const childId = child.webContents.id;
    promptWindows.set(childId, { child, message: message.slice(0,4000), value: value.slice(0,20000), event, answered: false });
    child.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    child.webContents.on('will-navigate', event => event.preventDefault());
    child.on('closed', () => { const state = promptWindows.get(childId); if (state && !state.answered) state.event.returnValue = null; promptWindows.delete(childId); });
    child.loadFile(path.join(__dirname, 'prompt.html'));
    if (smokeMode) child.webContents.once('did-finish-load', () => {
      child.webContents.executeJavaScript('window.promptDialog.answer("desktop-smoke-answer")').catch(() => {});
    });
  });
  ipcMain.handle('desktop:prompt-ready', event => {
    const state = promptWindows.get(event.sender.id); if (!state || event.senderFrame !== event.sender.mainFrame) throw new Error('Unauthorized');
    return { message: state.message, value: state.value };
  });
  ipcMain.on('desktop:prompt-answer', (event, value) => {
    const state = promptWindows.get(event.sender.id);
    if (!state || event.senderFrame !== event.sender.mainFrame || (value !== null && typeof value !== 'string')) return;
    state.answered = true; state.event.returnValue = value === null ? null : value.slice(0, 20000); state.child.close();
  });
}
const saveScript = `(async () => {
  const saveFrame = async w => {
    for (let index = 0; index < w.frames.length; index++) await saveFrame(w.frames[index]);
    if (typeof w.saveCanvas !== 'function') return;
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const deadline = Date.now() + 12000;
    const busy = () => w.eval('(typeof savingCanvasNow !== "undefined" && savingCanvasNow) || (typeof saveCanvasAgain !== "undefined" && saveCanvasAgain) || (typeof canvasSyncInFlight !== "undefined" && canvasSyncInFlight)');
    while (busy() && Date.now() < deadline) await sleep(100);
    let failed = false, conflictPending = false;
    const original = w.fetch;
    w.fetch = async (...args) => { try { const response = await original.apply(w, args);
      if (String(args[0]).includes('/api/canvases/') && args[1]?.method === 'PUT') {
        if (response.status === 409) conflictPending = true;
        else if (!response.ok) failed = true;
        else conflictPending = false;
      }
      return response; } catch (e) { failed = true; throw e; } };
    try { await w.saveCanvas();
      // Both canvas implementations schedule conflict retries after saveCanvas returns.
      // Keep observing until a subsequent PUT succeeds, including the smart 300ms timer.
      while ((busy() || conflictPending) && Date.now() < deadline) await sleep(100);
      if (failed || busy() || conflictPending) throw Error('保存未完成');
    } finally { w.fetch = original; }
  }; await saveFrame(window); return true;
})()`;
async function saveBeforeClose() {
  if (!ready || !win || win.isDestroyed()) return true;
  let timeout;
  try { await Promise.race([win.webContents.executeJavaScript(saveScript), new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error('保存超时')), 15000); })]); return true; }
  catch { const result = await dialog.showMessageBox(win, { type: 'warning', message: '画布保存未完成。', detail: '可以返回重试保存，或放弃本次未保存内容并退出。', buttons: ['返回画布', '仍然退出'], defaultId: 0, cancelId: 0 }); return result.response === 1; }
  finally { clearTimeout(timeout); }
}
async function closeApp() {
  if (closing || updates?.getState().phase === 'installing') return; closing = true;
  try {
    if (!await confirmServiceStop(false)) return;
    quitting = true; ready = false; await backend?.stop(); app.quit();
  } finally { closing = false; }
}
async function confirmServiceStop(installing) {
  if (!await saveBeforeClose()) return false;
  let health; try { health = await backend.health(); } catch {
    if (installing) {
      await dialog.showMessageBox(win, { type: 'warning', message: '暂时无法确认桌面服务状态，请稍后重试安装。', buttons: ['返回画布'] });
      return false;
    }
  }
  if (health?.active_tasks > 0) {
    const choice = await dialog.showMessageBox(win, { type: 'warning', message: `仍有 ${health.active_tasks} 个任务正在执行。`, detail: '退出会停止桌面服务，远端已提交的任务可能仍会计费。', buttons: ['继续等待', installing ? '停止任务并安装' : '退出应用'], defaultId: 0, cancelId: 0 });
    if (choice.response !== 1) return false;
  }
  return true;
}
async function prepareForInstall() {
  if (closing || restarting || !ready || !await confirmServiceStop(true)) return false;
  // Set these before stopping the owned backend: crash handlers and before-quit
  // must not attempt a second close/save while the installer is taking over.
  quitting = true; ready = false;
  await backend.stop();
  return true;
}
async function recoverAfterInstallFailure() {
  if (!quitting) return;
  quitting = false; ready = false;
  try { await backend.stop(); await startWithRecovery(); await loadApp(); }
  catch { await failure('安装未能启动，桌面服务需要重新启动。'); }
}
function createWindow() {
  let bounds = {}; try { bounds = JSON.parse(fs.readFileSync(boundsFile, 'utf8')); } catch {}
  win = new BrowserWindow({ width: Math.max(800, Math.min(2400, Number(bounds.width) || 1440)), height: Math.max(600, Math.min(1600, Number(bounds.height) || 900)),
    minWidth: 800, minHeight: 600, show: false, autoHideMenuBar: true, title: 'Infinite Canvas', icon: path.join(__dirname, 'icon.ico'),
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true } });
  win.removeMenu();
  win.on('close', event => {
    try { fs.mkdirSync(root, { recursive: true }); const bounds = win.getNormalBounds(); fs.writeFileSync(boundsFile, JSON.stringify({ width: bounds.width, height: bounds.height })); } catch {}
    if (!quitting) { event.preventDefault(); void closeApp(); }
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (isLocalURL(url)) { win.loadURL(url).catch(() => {}); } else external(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (event, url) => { if (!isLocalURL(url)) { event.preventDefault(); external(url); } });
  win.webContents.on('will-frame-navigate', (event, details) => {
    const url = typeof details === 'string' ? details : details?.url;
    if (url && !isLocalURL(url) && url !== 'about:blank') event.preventDefault();
  });
  win.webContents.on('did-frame-finish-load', (_event, _main, processId, routingId) => {
    const frame = webFrameMain.fromId(processId, routingId);
    if (frame && isLocalURL(frame.url)) frame.executeJavaScript('window.prompt = (message, value = "") => window.top.desktopBridge.prompt(String(message), String(value));').catch(() => {});
  });
  win.once('ready-to-show', () => win.show());
}
async function loadApp() {
  await session.defaultSession.cookies.set({ url: ORIGIN, name: 'ic_desktop_session', value: backend.token, httpOnly: true, sameSite: 'strict', path: '/' });
  ready = true; await win.loadURL(ORIGIN); win.show();
  // Explicit local-only smoke mode uses the same startup/session/window path.
  // It never enables a debug listener, generates media, or touches another app.
  if (smokeMode) {
    try {
      const proof = await require('./smoke.cjs').run({ win, session: session.defaultSession, root, saveScript });
      console.log('IC_DESKTOP_SMOKE ' + JSON.stringify({ ok: true, ...proof, sandbox: win.webContents.getLastWebPreferences().sandbox, nodeIntegration: win.webContents.getLastWebPreferences().nodeIntegration }));
      quitting = true; ready = false; await backend.stop(); app.quit();
    } catch (error) { await smokeFailure(error); }
  }
}
async function smokeFailure(error) {
  const detail = String(error?.message || 'startup failure').slice(0, 350).replace(/(?:Bearer\s+|sk-)[\w.-]+/gi, '[redacted]');
  console.error('IC_DESKTOP_SMOKE ' + JSON.stringify({ ok: false, stage: require('./smoke.cjs').getStage(), error: detail }));
  quitting = true; ready = false; await backend?.stop(); app.exit(1);
}
async function startWithRecovery() {
  try { await backend.start(); }
  catch (error) {
    // A port conflict must not trigger rollback or touch an unknown service.
    if (String(error.message).includes('已被占用')) throw error;
    try { await backend.recover(); } catch (recoveryError) { throw new Error(`${error.message}\n${recoveryError.message}`); }
    await backend.start();
  }
}
async function failure(message) {
  if (smokeMode) { await smokeFailure(); return; }
  ready = false;
  const choice = await dialog.showMessageBox(win, { type: 'error', message, detail: '个人数据保留在独立用户目录。重试不会重新提交生成任务。', buttons: ['重新启动服务', '退出应用'], defaultId: 0, cancelId: 1 });
  if (choice.response === 0) {
    try { await backend.stop(); await startWithRecovery(); await loadApp(); }
    catch (error) { await failure(error.message); }
  } else { quitting = true; await backend?.stop(); app.quit(); }
}
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (win) { if (win.isMinimized()) win.restore(); win.show(); win.focus(); } });
  app.on('before-quit', event => { if (!quitting) { event.preventDefault(); void closeApp(); } });
  app.whenReady().then(async () => {
    if (smokeMode && !process.env.IC_DESKTOP_ROOT) throw new Error('Smoke mode requires an isolated IC_DESKTOP_ROOT.');
    installIPC(); createWindow();
    const updatesEnabled = app.isPackaged && !smokeMode;
    updates = new DesktopUpdater({ updater: updatesEnabled ? require('electron-updater').autoUpdater : null,
      currentVersion: app.getVersion(), enabled: updatesEnabled, prepareForInstall, recoverAfterInstallFailure,
      onState: state => { if (win && !win.isDestroyed() && !win.webContents.isDestroyed()) win.webContents.send(CHANNEL, state); } });
    installUpdateIPC({ ipcMain, getWindow: () => win, isLocalURL, controller: updates });
    const permissions = new Set(['clipboard-sanitized-write', 'clipboard-read', 'fullscreen']);
    session.defaultSession.setPermissionRequestHandler((contents, permission, callback, details) => callback(contents === win.webContents && isLocalURL(details.requestingUrl) && permissions.has(permission)));
    session.defaultSession.setPermissionCheckHandler((contents, permission, origin) => contents === win.webContents && isLocalURL(origin) && permissions.has(permission));
    session.defaultSession.on('will-download', (_event, item) => {
      const name = path.basename(item.getFilename()).replace(/[<>:"/\\|?*\x00-\x1f]/g, '_');
      item.setSaveDialogOptions({ title: '保存下载文件', defaultPath: path.join(app.getPath('downloads'), name) });
    });
    backend = new Backend(app.isPackaged ? path.join(process.resourcesPath, 'backend') : path.join(__dirname, '..', 'backend'), root);
    backend.on('crash', () => { if (!restarting && ready && !quitting) void failure('桌面服务意外退出。'); });
    backend.on('restart', event => {
      if (restarting || quitting || updates.getState().phase === 'installing') return; restarting = true;
      const delay = Math.max(0, Math.min(30, Number(event.delay) || 0));
      setTimeout(async () => {
        try { ready = false; await backend.stop(); await startWithRecovery(); await loadApp(); }
        catch (error) { await failure(error.message); }
        finally { restarting = false; }
      }, delay * 1000);
    });
    try { await startWithRecovery(); await loadApp(); }
    catch (error) { await failure(error.message); }
  }).catch(async error => { if (smokeMode) { await smokeFailure(); return; } await dialog.showMessageBox({ type: 'error', message: `桌面应用启动失败：${error.message}` }); quitting = true; await backend?.stop(); app.quit(); });
}
