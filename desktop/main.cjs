'use strict';
const { app, BrowserWindow, dialog, ipcMain, shell, session, webFrameMain } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const { Backend, ORIGIN, isLocalURL, assertPortFree } = require('./backend.cjs');
const { installUpdateIPC, CHANNEL } = require('./updater.cjs');
const { HybridUpdater, GitHubTransport, electronFetch } = require('./hybrid-updater.cjs');
const { FileUpdates, businessVersion } = require('./file-updates.cjs');
const { Storage, writeIni } = require('./storage.cjs');
const smokeMode = process.argv.includes('--desktop-smoke');
const installDir = app.isPackaged ? path.dirname(app.getPath('exe')) : path.resolve(__dirname, '..');
const storage = new Storage({ installDir, localAppData: process.env.LOCALAPPDATA || app.getPath('appData'), isolatedRoot: process.env.IC_DESKTOP_ROOT });
const storageHelper = process.argv.includes('--storage-info') || process.argv.includes('--storage-uninstall');
let storageFailure;
let root = storage.root;
const runtimeRoot = storage.controlRoot;
// Acquire one stable lock per installation before migrating or opening a profile.
app.setPath('userData', path.join(runtimeRoot, 'lock-profile'));
app.setPath('sessionData', path.join(root, 'browser-profile'));
app.setAppUserModelId('site.sansheng.infinitecanvas.desktop');
let win, backend, updates, fileUpdates, smokeRunning = false, quitting = false, closing = false, restarting = false, ready = false;
const promptWindows = new Map();
let boundsFile = path.join(root, 'window.json');
function trusted(event) { return win && event.sender === win.webContents && isLocalURL(event.senderFrame?.url); }
function external(url) { try { const parsed = new URL(url); if (['https:', 'http:'].includes(parsed.protocol) && !['127.0.0.1', 'localhost', '[::1]'].includes(parsed.hostname)) shell.openExternal(parsed.href).catch(() => {}); } catch {} }
function installIPC() {
  ipcMain.handle('desktop:storage-info', event => {
    if (!trusted(event)) throw Error('Unauthorized');
    return storage.info();
  });
  ipcMain.handle('desktop:storage-open', async event => {
    if (!trusted(event)) throw Error('Unauthorized');
    return { error: await shell.openPath(storage.root) };
  });
  ipcMain.handle('desktop:storage-change', async (event, reset) => {
    if (!trusted(event)) throw Error('Unauthorized');
    if (closing || restarting || !ready || ['checking', 'downloading', 'installing'].includes(updates?.getState().phase)) return { error: '请等待当前操作完成后再更改位置。' };
    let target = storage.defaultRoot;
    if (!reset) {
      const selected = await dialog.showOpenDialog(win, { title: '选择个人数据的存放位置', properties: ['openDirectory', 'createDirectory'] });
      if (selected.canceled) return { canceled: true };
      target = storage.target(selected.filePaths[0]);
    }
    try { storage.validateTarget(target); } catch (error) { return { error: error.message }; }
    const confirmation = await dialog.showMessageBox(win, { type: 'question', message: '迁移个人资料并重新启动？', detail: `新位置：${target}\n软件重启后复制并校验画布、素材和设置。资料较多时请耐心等待；原目录会保留为备份。目标中已有的本软件迁移备份会另存保留。`, buttons: ['取消', '迁移并重启'], defaultId: 0, cancelId: 0 });
    if (confirmation.response !== 1) return { canceled: true };
    restarting = true;
    try {
      // Migration never discards a failed save or interrupts a running generation.
      await win.webContents.executeJavaScript(saveScript);
      const health = await backend.health();
      if (health.active_tasks > 0) throw Error('还有生成任务正在执行，请完成后再迁移。');
      await session.defaultSession.cookies.flushStore(); session.defaultSession.flushStorageData();
      storage.schedule(target);
      ready = false; await backend.stop(); quitting = true;
      app.relaunch(); setTimeout(() => app.exit(0), 150);
      return { restarting: true };
    } catch (error) { restarting = false; return { error: error.message === '还有生成任务正在执行，请完成后再迁移。' ? error.message : '暂时无法完成保存或迁移准备，原资料保持不变。' }; }
  });
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
async function applyFiles(archive, version) {
  if (closing || restarting || !ready || !await confirmServiceStop(true)) return false;
  restarting = true; ready = false;
  try {
    await backend.stop(); await fileUpdates.apply(archive, version);
    const health = await backend.start();
    if (health.desktop_version !== version) throw Error('更新后的功能版本不匹配。');
    await fileUpdates.confirm(); await loadApp(); return true;
  } catch (error) {
    await backend.stop(); await fileUpdates.rollback();
    await backend.start(); await loadApp(); throw error;
  } finally { restarting = false; }
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
  if (updates && backend) {
    const currentVersion = businessVersion(backend.appDir, app.getVersion());
    updates.native.state.currentVersion = currentVersion;
    updates.set({ currentVersion });
  }
  await session.defaultSession.cookies.set({ url: ORIGIN, name: 'ic_desktop_session', value: backend.token, httpOnly: true, sameSite: 'strict', path: '/' });
  ready = true; await win.loadURL(ORIGIN); win.show();
  // Explicit local-only smoke mode uses the same startup/session/window path.
  // It never enables a debug listener, generates media, or touches another app.
  if (smokeMode && !smokeRunning) {
    smokeRunning = true;
    try {
      const proof = await require('./smoke.cjs').run({ win, session: session.defaultSession, root, saveScript, updates, backend });
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
if (storageHelper) {
  // No personal profile/session or backend is opened by the uninstaller helper.
  const temporaryProfile = path.join(app.getPath('temp'), `ic-storage-helper-${process.pid}`);
  app.setPath('userData', temporaryProfile); app.setPath('sessionData', temporaryProfile);
  app.whenReady().then(() => {
    try {
      if (process.argv.includes('--storage-info')) {
        const output = process.argv[process.argv.indexOf('--storage-info') + 1];
        const tempRoot = fs.realpathSync(app.getPath('temp'));
        const parent = fs.realpathSync(path.dirname(output));
        const rel = path.relative(tempRoot, parent);
        if (rel.startsWith('..') || path.isAbsolute(rel) || path.basename(output) !== 'storage.ini') throw Error('Invalid helper output');
        writeIni(output, { path: storage.root, deletable: storage.owned(storage.root) ? '1' : '0' });
      } else storage.uninstall(process.argv.includes('--remove-personal-data'));
      app.exit(0);
    } catch { app.exit(2); }
  });
}
else if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (win) { if (win.isMinimized()) win.restore(); win.show(); win.focus(); } });
  app.on('before-quit', event => { if (!quitting) { event.preventDefault(); void closeApp(); } });
  app.whenReady().then(async () => {
    if (smokeMode && !process.env.IC_DESKTOP_ROOT) throw new Error('Smoke mode requires an isolated IC_DESKTOP_ROOT.');
    // No data copying while another desktop service (including older shells) runs.
    await assertPortFree();
    try { storage.bootstrap(); } catch (error) { storageFailure = error; }
    root = storage.root; boundsFile = path.join(root, 'window.json');
    app.setPath('userData', path.join(root, 'browser-profile'));
    app.setPath('sessionData', path.join(root, 'browser-profile'));
    if (storageFailure) {
      const choice = await dialog.showMessageBox({ type: 'error', message: storageFailure.message, detail: '请检查原数据磁盘或目录权限。首次安装可选择其他位置存放个人资料。', buttons: storage.config.path ? ['退出'] : ['退出', '选择其他位置'], defaultId: 0 });
      if (choice.response === 1 && !storage.config.path) {
        const selected = await dialog.showOpenDialog({ properties: ['openDirectory', 'createDirectory'], title: '选择个人数据位置' });
        if (!selected.canceled) {
          const target = storage.target(selected.filePaths[0]); storage.validateTarget(target);
          storage.writable(target); storage.mark(target); storage.commit({ path: target });
          app.relaunch(); app.exit(0); return;
        }
      }
      app.exit(1); return;
    }
    if (storage.migrationError) await dialog.showMessageBox({ type: 'warning', message: '迁移未完成，已继续使用原数据目录。', detail: storage.migrationError, buttons: ['确定'] });
    if (!smokeMode && storage.config.legacy === '1' && !storage.config.prompted) {
      const choice = await dialog.showMessageBox({ type: 'question', message: '将旧版个人资料迁移到软件文件夹？', detail: `旧位置：${storage.root}\n新位置：${storage.defaultRoot}\n复制校验后保留旧目录备份。不同安装版本将使用独立资料。`, buttons: ['暂时使用旧位置', '迁移并重启', '选择其他位置'], defaultId: 1, cancelId: 0 });
      let target = storage.defaultRoot;
      if (choice.response === 2) {
        const selected = await dialog.showOpenDialog({ properties: ['openDirectory', 'createDirectory'] });
        target = selected.canceled ? null : storage.target(selected.filePaths[0]);
      }
      if (choice.response && target) {
        try { storage.schedule(target); app.relaunch(); app.exit(0); return; }
        catch (error) { await dialog.showMessageBox({ type: 'error', message: error.message, buttons: ['继续使用原位置'] }); }
      }
      storage.commit({ ...storage.config, prompted: '1' });
    }
    installIPC(); createWindow();
    backend = new Backend(app.isPackaged ? path.join(process.resourcesPath, 'backend') : path.join(__dirname, '..', 'backend'), runtimeRoot, root);
    fileUpdates = new FileUpdates(backend);
    await fileUpdates.recover();
    const updatesEnabled = app.isPackaged && !smokeMode;
    updates = new HybridUpdater({ updater: updatesEnabled ? require('electron-updater').autoUpdater : null,
      shellVersion: app.getVersion(), currentVersion: businessVersion(backend.appDir, app.getVersion()), enabled: updatesEnabled, prepareForInstall, recoverAfterInstallFailure,
      transport: new GitHubTransport(electronFetch),
      files: fileUpdates, applyFiles, cacheDir: path.join(runtimeRoot, 'file-updates/downloads'),
      onState: state => { if (win && !win.isDestroyed() && !win.webContents.isDestroyed()) win.webContents.send(CHANNEL, state); } });
    installUpdateIPC({ ipcMain, getWindow: () => win, isLocalURL, controller: updates });
    const permissions = new Set(['clipboard-sanitized-write', 'clipboard-read', 'fullscreen']);
    session.defaultSession.setPermissionRequestHandler((contents, permission, callback, details) => callback(contents === win.webContents && isLocalURL(details.requestingUrl) && permissions.has(permission)));
    session.defaultSession.setPermissionCheckHandler((contents, permission, origin) => contents === win.webContents && isLocalURL(origin) && permissions.has(permission));
    session.defaultSession.on('will-download', (_event, item) => {
      const name = path.basename(item.getFilename()).replace(/[<>:"/\\|?*\x00-\x1f]/g, '_');
      item.setSaveDialogOptions({ title: '保存下载文件', defaultPath: path.join(app.getPath('downloads'), name) });
    });
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
