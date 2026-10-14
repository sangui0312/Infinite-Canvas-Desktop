'use strict';
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const net = require('node:net');
const { spawn } = require('node:child_process');
const { randomUUID, randomBytes } = require('node:crypto');
const { EventEmitter } = require('node:events');
const PORT = 31300;
const ORIGIN = `http://127.0.0.1:${PORT}`;
function compareVersions(a, b) {
  const parse = value => { if (!/^\d+\.\d+\.\d+$/.test(value)) throw new Error('桌面壳版本标记无效。'); return value.split('.').map(Number); };
  const left = parse(a), right = parse(b);
  for (let i = 0; i < 3; i++) if (left[i] !== right[i]) return left[i] > right[i] ? 1 : -1;
  return 0;
}
function isLocalURL(value) {
  try { return new URL(value).origin === ORIGIN; } catch { return false; }
}
async function assertPortFree(port = PORT) {
  await new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once('error', () => reject(new Error(`桌面端口 ${port} 已被占用。请关闭占用程序后重试。`)));
    probe.listen(port, '127.0.0.1', () => probe.close(resolve));
  });
}
function requestJSON(route, token, method = 'GET', timeout = 2500) {
  return new Promise((resolve, reject) => {
    const req = http.request(`${ORIGIN}${route}`, { method, headers: { 'X-Desktop-Token': token }, timeout }, res => {
      let body = '';
      res.on('data', chunk => { body += chunk; if (body.length > 65536) req.destroy(new Error('响应过大')); });
      res.on('end', () => { try { if (res.statusCode !== 200) throw new Error(`服务响应 ${res.statusCode}`); resolve(JSON.parse(body)); } catch (error) { reject(error); } });
    });
    req.once('timeout', () => req.destroy(new Error('服务响应超时')));
    req.once('error', reject); req.end();
  });
}
class Backend extends EventEmitter {
  constructor(template, root) {
    super(); this.template = template; this.root = root;
    this.appDir = path.join(root, 'runtime', 'app');
    this.dataDir = path.join(root, 'userdata');
    this.child = null; this.stopping = false;
    this.shellVersion = require('../package.json').version;
    this.markerFile = path.join(root, 'runtime', 'shell-template.json');
    this.pendingShellUpgrade = null;
  }
  prepare() {
    fs.mkdirSync(this.dataDir, { recursive: true });
    if (!fs.existsSync(this.appDir)) {
      const staging = `${this.appDir}.initializing`;
      fs.mkdirSync(path.dirname(this.appDir), { recursive: true });
      fs.rmSync(staging, { recursive: true, force: true });
      fs.cpSync(this.template, staging, { recursive: true, filter: source => path.basename(source) !== '__pycache__' });
      fs.renameSync(staging, this.appDir);
    }
    let installedVersion;
    try { installedVersion = JSON.parse(fs.readFileSync(this.markerFile, 'utf8')).version; }
    catch (error) { if (error.code !== 'ENOENT') throw new Error('桌面壳版本标记损坏，请保留数据后修复安装。'); }
    if (!installedVersion) {
      fs.writeFileSync(this.markerFile, JSON.stringify({ version: this.shellVersion }));
      return;
    }
    if (compareVersions(this.shellVersion, installedVersion) <= 0 || this.pendingShellUpgrade) return;
    const staging = `${this.appDir}.shell-upgrade`;
    const backup = path.join(path.dirname(this.appDir), `shell-backup-${installedVersion}-${Date.now()}`);
    fs.rmSync(staging, { recursive: true, force: true });
    fs.cpSync(this.template, staging, { recursive: true, filter: source => path.basename(source) !== '__pycache__' });
    fs.renameSync(this.appDir, backup);
    try { fs.renameSync(staging, this.appDir); }
    catch (error) { fs.renameSync(backup, this.appDir); throw error; }
    this.pendingShellUpgrade = { backup, installedVersion };
  }
  confirmShellUpgrade() {
    if (!this.pendingShellUpgrade) return;
    fs.writeFileSync(this.markerFile, JSON.stringify({ version: this.shellVersion }));
    this.pendingShellUpgrade = null;
  }
  rollbackShellUpgrade() {
    const upgrade = this.pendingShellUpgrade; if (!upgrade) return false;
    const failed = `${this.appDir}.failed-${Date.now()}`;
    fs.renameSync(this.appDir, failed);
    try { fs.renameSync(upgrade.backup, this.appDir); }
    catch (error) { fs.renameSync(failed, this.appDir); throw error; }
    fs.writeFileSync(this.markerFile, JSON.stringify({ version: upgrade.installedVersion }));
    this.pendingShellUpgrade = null;
    return true;
  }
  environment() {
    return { ...process.env, PYTHONUTF8: '1', PYTHONUNBUFFERED: '1', IC_DESKTOP_APP_DIR: this.appDir,
      IC_DESKTOP_DATA_DIR: this.dataDir, IC_DESKTOP_PORT: String(PORT), IC_DESKTOP_TOKEN: this.token,
      IC_DESKTOP_INSTANCE: this.instance, IC_DESKTOP_PARENT_PID: String(process.pid),
      IC_DESKTOP_SHELL_VERSION: require('../package.json').version };
  }
  async start() {
    try {
      const health = await this.startProcess(); this.confirmShellUpgrade(); return health;
    } catch (error) {
      if (!this.pendingShellUpgrade) throw error;
      await this.stop(); this.rollbackShellUpgrade();
      // One fallback to the pre-install program; do not reapply the failed template.
      const health = await this.startProcess(true); this.emit('shell-rollback'); return health;
    }
  }
  async startProcess(skipPrepare = false) {
    await assertPortFree(); if (!skipPrepare) this.prepare();
    this.token = randomBytes(32).toString('hex'); this.instance = randomUUID(); this.stopping = false;
    const child = spawn(path.join(this.appDir, 'python', 'python.exe'), ['-X', 'utf8', path.join(this.template, 'desktop_entry.py')],
      { cwd: this.appDir, env: this.environment(), windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    this.child = child;
    let buffer = ''; let launchError;
    child.on('error', error => { launchError = error; });
    // Raw backend output may contain private settings: never persist or relay it.
    child.stderr.on('data', () => {});
    child.stdout.on('data', chunk => {
      buffer += chunk.toString('utf8');
      const lines = buffer.split(/\r?\n/); buffer = lines.pop().slice(-65536);
      for (const line of lines) if (line.startsWith('IC_DESKTOP_EVENT ')) {
        try { const event = JSON.parse(line.slice(17));
          if (event.instance === this.instance && event.type === 'restart') this.emit('restart', event);
        } catch {}
      }
    });
    child.on('exit', (code, signal) => {
      if (this.child === child && !this.stopping) this.emit('crash', { code, signal });
    });
    const deadline = Date.now() + 60000;
    while (Date.now() < deadline) {
      if (launchError) throw new Error(`无法启动内置 Python：${launchError.code || '启动失败'}`);
      if (child.exitCode !== null || child.signalCode) throw new Error('桌面服务启动后退出。');
      try { const health = await this.health(); if (health.ok) return health; } catch {}
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    throw new Error('桌面服务启动超时。');
  }
  async health() {
    const health = await requestJSON('/desktop/health', this.token);
    if (health.ok !== true || health.protocol !== 1 || health.instance !== this.instance || health.pid !== this.child?.pid)
      throw new Error('桌面服务身份核验失败。');
    return health;
  }
  async stop() {
    const child = this.child; if (!child) return;
    this.stopping = true;
    if (child.exitCode === null && !child.signalCode) {
      try { await requestJSON('/desktop/shutdown', this.token, 'POST'); } catch {}
      await Promise.race([new Promise(resolve => child.once('exit', resolve)), new Promise(resolve => setTimeout(resolve, 5000))]);
      if (child.exitCode === null && !child.signalCode) {
        child.kill();
        await new Promise(resolve => { if (child.exitCode !== null || child.signalCode) resolve(); else child.once('exit', resolve); });
      }
    }
    if (this.child === child) this.child = null;
  }
  async recover() {
    await this.stop();
    return new Promise((resolve, reject) => {
      const child = spawn(path.join(this.appDir, 'python', 'python.exe'), ['-X', 'utf8', path.join(this.template, 'desktop_entry.py'), '--recover-latest'],
        { cwd: this.appDir, env: this.environment(), windowsHide: true, stdio: 'ignore' });
      const timer = setTimeout(() => { child.kill(); reject(new Error('恢复超时。')); }, 30000);
      child.once('error', error => { clearTimeout(timer); reject(error); });
      child.once('exit', code => { clearTimeout(timer); code === 0 ? resolve() : reject(new Error('没有可恢复的有效程序备份。')); });
    });
  }
}
module.exports = { Backend, PORT, ORIGIN, isLocalURL, assertPortFree, requestJSON };
