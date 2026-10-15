'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { DesktopUpdater, installUpdateIPC, trustedUpdateSender, REPOSITORY_URL } = require('../desktop/updater.cjs');
const info = { version: '0.2.0', releaseName: 'Desktop release', releaseNotes: '更新说明' };
class FakeUpdater extends EventEmitter {
  setFeedURL(feed) { this.feed = feed; }
  async checkForUpdates() { this.emit('update-available', info); return { updateInfo: info }; }
  async downloadUpdate() { this.emit('download-progress', { percent: 50, transferred: 50, total: 100 }); this.emit('update-downloaded', info); return ['private-path']; }
  quitAndInstall(...args) { this.installArgs = args; }
}
function fixture(options = {}) {
  const updater = new FakeUpdater(), states = [];
  const controller = new DesktopUpdater({ updater, enabled: true, currentVersion: '0.1.0', onState: state => states.push(state), ...options });
  return { updater, controller, states };
}
async function downloaded(options) { const result = fixture(options); await result.controller.check(); await result.controller.download(); return result; }
test('fixed public provider and explicit full-installer settings', () => {
  const { updater, controller } = fixture();
  assert.deepEqual(updater.feed, { provider: 'github', owner: 'sangui0312', repo: 'Infinite-Canvas-Desktop', private: false, releaseType: 'release' });
  for (const key of ['autoDownload', 'autoInstallOnAppQuit', 'allowDowngrade', 'allowPrerelease']) assert.equal(updater[key], false);
  assert.equal(updater.disableDifferentialDownload, false); assert.equal(updater.logger, null);
  assert.equal(updater.requestHeaders, null); assert.equal(updater.autoRunAppAfterInstall, true);
  assert.equal(controller.getState().repositoryUrl, REPOSITORY_URL);
  const state = controller.getState(); state.phase = 'downloaded'; assert.equal(controller.getState().phase, 'idle');
});
test('only downloaded event after validation permits install; progress and metadata remain bounded', async () => {
  const { updater, controller, states } = fixture();
  updater.checkForUpdates = async () => { const value = { ...info, releaseNotes: [{ note: '文'.repeat(20000) }], releaseName: 'a'.repeat(300) }; updater.emit('update-available', value); return { updateInfo: value }; };
  assert.equal((await controller.check()).phase, 'available');
  assert.equal(controller.getState().notes.length, 15000); assert.equal(controller.getState().releaseName.length, 200);
  assert.equal(controller.getState().releaseUrl, `${REPOSITORY_URL}/releases/tag/v0.2.0`);
  assert.equal((await controller.download()).phase, 'downloaded');
  assert.ok(states.some(state => state.percent === 50)); assert.equal(controller.getState().percent, 100);
  assert.equal(JSON.stringify(controller.getState()).includes('private-path'), false);
});
test('check and download are mutually exclusive even after early available event', async () => {
  const { updater, controller } = fixture(); let finish;
  updater.checkForUpdates = () => { updater.emit('update-available', info); return new Promise(resolve => { finish = resolve; }); };
  const pending = controller.check();
  await assert.rejects(controller.check(), /尚未完成/); await assert.rejects(controller.download(), /尚未完成/);
  finish({ updateInfo: info }); await pending;
  let finishDownload;
  updater.downloadUpdate = () => new Promise(resolve => { finishDownload = resolve; });
  const pendingDownload = controller.download();
  await assert.rejects(controller.check(), /尚未完成/); await assert.rejects(controller.download(), /尚未完成/); await assert.rejects(controller.install(), /尚未完成/);
  updater.emit('update-downloaded', info); finishDownload(); await pendingDownload;
});
test('illegal actions, older releases, and prereleases are rejected', async () => {
  const { updater, controller } = fixture();
  await assert.rejects(controller.download()); await assert.rejects(controller.install());
  for (const version of ['0.0.1', '0.2.0-beta.1', 'bad']) {
    updater.checkForUpdates = async () => { updater.emit('update-available', { version }); };
    assert.equal((await controller.check()).phase, 'error');
  }
});
test('no update returns not-available', async () => {
  const { updater, controller } = fixture();
  updater.checkForUpdates = async () => { updater.emit('update-not-available', { version: '0.1.0' }); };
  assert.equal((await controller.check()).phase, 'not-available');
});
test('download checksum failure never enables install or exposes raw secrets', async () => {
  const { updater, controller } = fixture(); await controller.check();
  updater.downloadUpdate = async () => { const error = Object.assign(new Error('https://secret/token Authorization Bearer secret'), { code: 'ERR_CHECKSUM_MISMATCH' }); updater.emit('error', error); throw error; };
  const state = await controller.download();
  assert.equal(state.phase, 'error'); assert.match(state.error, /校验失败/); assert.doesNotMatch(JSON.stringify(state), /secret|Authorization|Bearer/);
  await assert.rejects(controller.install()); assert.equal(updater.installArgs, undefined);
});
test('download without validated completion, or a mismatched version, remains un-installable', async () => {
  const { updater, controller } = fixture();
  await controller.check(); updater.downloadUpdate = async () => ['installer.exe'];
  assert.equal((await controller.download()).phase, 'error'); await assert.rejects(controller.install());
  await controller.check(); updater.downloadUpdate = async () => updater.emit('update-downloaded', { version: '9.9.9' });
  assert.equal((await controller.download()).phase, 'error'); await assert.rejects(controller.install());
});
test('cancelled save/task confirmation preserves downloaded update', async () => {
  const { controller, updater } = await downloaded({ prepareForInstall: async () => false });
  assert.equal((await controller.install()).phase, 'downloaded'); assert.equal(updater.installArgs, undefined);
});
test('successful install first finishes prepare/owned backend stop, then launches installer', async () => {
  const order = [];
  const { controller, updater } = await downloaded({ prepareForInstall: async () => { order.push('save'); order.push('confirm-tasks'); await Promise.resolve(); order.push('stop-owned-backend'); return true; } });
  updater.quitAndInstall = (...args) => { order.push('install'); assert.deepEqual(args, [false, true]); };
  assert.equal((await controller.install()).phase, 'installing');
  assert.deepEqual(order, ['save', 'confirm-tasks', 'stop-owned-backend', 'install']);
  await assert.rejects(controller.install());
});
test('synchronous installer failure restores app and permits retry without a new download', async () => {
  const order = [];
  const { controller, updater } = await downloaded({ prepareForInstall: async () => { order.push('stop'); return true; }, recoverAfterInstallFailure: async () => order.push('restart') });
  updater.quitAndInstall = () => { order.push('install'); throw new Error('private path'); };
  const state = await controller.install();
  assert.equal(state.phase, 'downloaded'); assert.match(state.error, /重试安装/); assert.doesNotMatch(state.error, /private/);
  assert.deepEqual(order, ['stop', 'install', 'restart']);
  updater.quitAndInstall = () => order.push('retry'); assert.equal((await controller.install()).phase, 'installing');
});
test('native updater emitted install failure restores app once', async () => {
  let restarts = 0;
  const { controller, updater } = await downloaded({ prepareForInstall: async () => true, recoverAfterInstallFailure: async () => { restarts++; } });
  updater.quitAndInstall = () => { updater.emit('error', new Error('raw')); updater.emit('error', new Error('raw')); };
  assert.equal((await controller.install()).phase, 'downloaded'); assert.equal(restarts, 1);
});
test('development and smoke mode do not configure, contact, download, or install updates', async () => {
  const { controller, updater } = fixture({ enabled: false });
  assert.equal(updater.feed, undefined); assert.equal(controller.getState().phase, 'idle'); assert.match(controller.getState().error, /开发/);
  for (const action of ['check', 'download', 'install']) await assert.rejects(controller[action](), /不支持/);
});
test('update IPC accepts only main window same-origin top frame and zero arguments', async () => {
  const mainFrame = { url: 'http://127.0.0.1:31300/' }, handlers = new Map();
  const win = { isDestroyed: () => false, webContents: { mainFrame } };
  const local = url => { try { return new URL(url).origin === 'http://127.0.0.1:31300'; } catch { return false; } };
  const trusted = { sender: win.webContents, senderFrame: mainFrame };
  assert.equal(trustedUpdateSender(trusted, win, local), true);
  installUpdateIPC({ ipcMain: { handle: (name, fn) => handlers.set(name, fn) }, getWindow: () => win, isLocalURL: local, controller: { getState: () => ({ phase: 'idle' }), check: () => 'check', download: () => 'download', install: () => 'install' } });
  assert.equal(handlers.size, 4);
  assert.equal(handlers.get('desktop:update-check')(trusted), 'check');
  for (const handle of handlers.values()) {
    for (const bad of [{ sender: {}, senderFrame: mainFrame }, { sender: win.webContents, senderFrame: { url: mainFrame.url } }, { sender: win.webContents, senderFrame: null }]) assert.throws(() => handle(bad), /Unauthorized/);
    assert.throws(() => handle(trusted, 'https://evil/installer.exe'), /Unauthorized/);
    mainFrame.url = 'https://evil/'; assert.throws(() => handle(trusted), /Unauthorized/); mainFrame.url = 'http://127.0.0.1:31300/';
  }
  win.isDestroyed = () => true; assert.equal(trustedUpdateSender(trusted, win, local), false);
});
