'use strict';
const REPOSITORY_URL = 'https://github.com/sangui0312/Infinite-Canvas-Desktop';
const CHANNEL = 'desktop:update-state';
const FEED = Object.freeze({ provider: 'github', owner: 'sangui0312', repo: 'Infinite-Canvas-Desktop', private: false, releaseType: 'release' });
function stableVersion(value) { return typeof value === 'string' && /^\d+\.\d+\.\d+$/.test(value) && value.length <= 60; }
function newer(value, current) {
  if (!stableVersion(value) || !stableVersion(current)) return false;
  const a = value.split('.').map(Number), b = current.split('.').map(Number);
  for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i] > b[i];
  return false;
}
function notesText(value) {
  if (Array.isArray(value)) return value.map(item => typeof item?.note === 'string' ? item.note.slice(0, 15000) : '').join('\n').slice(0, 15000);
  return typeof value === 'string' ? value.slice(0, 15000) : '';
}
function safeError(error, fallback) {
  // Do not expose URLs, headers, stack traces, tokens, or raw provider messages.
  const code = typeof error?.code === 'string' ? error.code : '';
  if (/CHECKSUM|SHA512|SIGNATURE|INVALID_SIGNATURE/.test(code)) return '更新文件校验失败，请重新检查并下载。';
  return fallback;
}
class DesktopUpdater {
  constructor({ updater, currentVersion, enabled, onState = () => {}, prepareForInstall = async () => false, recoverAfterInstallFailure = async () => {} }) {
    this.updater = updater; this.enabled = enabled; this.onState = onState;
    this.prepareForInstall = prepareForInstall; this.recoverAfterInstallFailure = recoverAfterInstallFailure;
    this.busy = false; this.recovering = null;
    this.state = { currentVersion, phase: 'idle', version: '', releaseName: '', notes: '', percent: 0, transferred: 0, total: 0,
      error: enabled ? '' : '开发或冒烟测试模式不支持桌面更新，请使用已安装的正式版。', repositoryUrl: REPOSITORY_URL, releaseUrl: `${REPOSITORY_URL}/releases` };
    if (!enabled || !updater) return;
    updater.logger = null;
    updater.requestHeaders = null;
    updater.autoDownload = false; updater.autoInstallOnAppQuit = false;
    updater.autoRunAppAfterInstall = true;
    updater.allowDowngrade = false; updater.allowPrerelease = false; updater.disableDifferentialDownload = false;
    updater.disableWebInstaller = true;
    updater.setFeedURL({ ...FEED });
    updater.on('update-available', info => { if (this.state.phase === 'checking') this.available(info); });
    updater.on('update-not-available', () => { if (this.state.phase === 'checking') this.set({ phase: 'not-available', error: '' }); });
    updater.on('download-progress', progress => {
      if (this.state.phase !== 'downloading') return;
      const number = value => Number.isFinite(value) && value >= 0 ? value : 0;
      this.set({ percent: Math.min(100, number(progress.percent)), transferred: number(progress.transferred), total: number(progress.total) });
    });
    // Native updater emits this only after its integrity/signature validation.
    updater.on('update-downloaded', info => {
      if (this.state.phase !== 'downloading') return;
      if (info?.version !== this.state.version) { this.fail(null, '更新版本不匹配，请重新检查更新。'); return; }
      this.set({ phase: 'downloaded', percent: 100, error: '' });
    });
    updater.on('error', error => {
      if (this.state.phase === 'installing') { void this.recoverInstall(); return; }
      if (['checking', 'downloading'].includes(this.state.phase)) this.fail(error, '更新失败，请检查网络后重试。');
    });
  }
  getState() { return { ...this.state }; }
  set(patch) { this.state = { ...this.state, ...patch }; this.onState(this.getState()); }
  fail(error, fallback) { this.set({ phase: 'error', error: safeError(error, fallback) }); }
  available(info) {
    if (!newer(info?.version, this.state.currentVersion)) { this.fail(null, '更新版本无效，请稍后重试。'); return; }
    this.set({ phase: 'available', version: info.version, releaseName: typeof info.releaseName === 'string' ? info.releaseName.slice(0, 200) : '',
      notes: notesText(info.releaseNotes), releaseUrl: `${REPOSITORY_URL}/releases/tag/v${info.version}`, error: '' });
  }
  assertAction(phases) {
    if (!this.enabled) throw new Error('开发或冒烟测试模式不支持桌面更新。');
    if (this.busy || !phases.includes(this.state.phase)) throw new Error('当前更新操作尚未完成，或状态不允许此操作。');
  }
  async check() {
    this.assertAction(['idle', 'not-available', 'available', 'error']); this.busy = true;
    this.set({ phase: 'checking', version: '', releaseName: '', notes: '', percent: 0, transferred: 0, total: 0, error: '', releaseUrl: `${REPOSITORY_URL}/releases` });
    try {
      const result = await this.updater.checkForUpdates();
      if (this.state.phase === 'checking') {
        if (newer(result?.updateInfo?.version, this.state.currentVersion)) this.available(result.updateInfo);
        else this.set({ phase: 'not-available' });
      }
    } catch (error) { this.fail(error, '检查更新失败，请检查网络后重试。'); }
    finally { this.busy = false; }
    return this.getState();
  }
  async download() {
    this.assertAction(['available']); this.busy = true;
    this.set({ phase: 'downloading', percent: 0, transferred: 0, total: 0, error: '' });
    try {
      await this.updater.downloadUpdate();
      if (this.state.phase === 'downloading') this.fail(null, '下载未通过完整校验，请重新检查更新。');
    } catch (error) { this.fail(error, '下载或校验失败，请重新检查更新。'); }
    finally { this.busy = false; }
    return this.getState();
  }
  async recoverInstall() {
    if (this.recovering) return this.recovering;
    this.recovering = (async () => {
      try { await this.recoverAfterInstallFailure(); }
      catch { /* Main process presents its own recovery dialog. */ }
      this.set({ phase: 'downloaded', error: '未能启动安装，已保留下载文件；请重试安装，或关闭应用后运行完整安装包。' });
    })();
    try { await this.recovering; } finally { this.recovering = null; }
  }
  async install() {
    this.assertAction(['downloaded']); this.busy = true;
    this.set({ phase: 'installing', error: '' });
    try {
      if (!await this.prepareForInstall()) { this.set({ phase: 'downloaded' }); return this.getState(); }
      try {
        const result = this.updater.quitAndInstall(false, true);
        if (result === false) await this.recoverInstall();
        else if (this.recovering) await this.recovering;
      } catch { await this.recoverInstall(); }
    } catch { await this.recoverInstall(); }
    finally { this.busy = false; }
    return this.getState();
  }
}
function trustedUpdateSender(event, win, isLocalURL) {
  return Boolean(win && !win.isDestroyed() && event.sender === win.webContents && event.senderFrame === win.webContents.mainFrame && isLocalURL(event.senderFrame?.url));
}
function installUpdateIPC({ ipcMain, getWindow, isLocalURL, controller }) {
  for (const [name, method] of Object.entries({ 'get-state': 'getState', check: 'check', download: 'download', install: 'install' })) {
    ipcMain.handle(`desktop:update-${name}`, (event, ...args) => {
      if (!trustedUpdateSender(event, getWindow(), isLocalURL) || args.length) throw new Error('Unauthorized desktop update request.');
      return controller[method]();
    });
  }
}
module.exports = { DesktopUpdater, installUpdateIPC, trustedUpdateSender, REPOSITORY_URL, CHANNEL };
