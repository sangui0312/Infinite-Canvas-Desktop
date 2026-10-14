'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const { Backend, PORT, isLocalURL, assertPortFree } = require('../desktop/backend.cjs');
test('only exact loopback desktop origin is trusted', () => {
  assert.equal(isLocalURL('http://127.0.0.1:31300/static/index.html'), true);
  for (const value of ['http://localhost:31300', 'http://127.0.0.1:3000', 'https://127.0.0.1:31300', 'http://127.0.0.1.evil:31300', 'file:///tmp/x', 'invalid']) assert.equal(isLocalURL(value), false);
});
test('first install creates isolated runtime and preserves subsequent edits and userdata', () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'ic-shell-'));
  try {
    const template = path.join(temp, 'template'); fs.mkdirSync(template); fs.writeFileSync(path.join(template, 'main.py'), 'template');
    const backend = new Backend(template, path.join(temp, 'profile')); backend.prepare();
    fs.writeFileSync(path.join(backend.appDir, 'main.py'), 'updated'); fs.writeFileSync(path.join(backend.dataDir, 'canvas.json'), 'personal'); backend.prepare();
    assert.equal(fs.readFileSync(path.join(backend.appDir, 'main.py'), 'utf8'), 'updated');
    assert.equal(fs.readFileSync(path.join(backend.dataDir, 'canvas.json'), 'utf8'), 'personal');
    assert.equal(backend.environment().IC_DESKTOP_PORT, String(PORT));
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
});
test('port conflict refuses connection and leaves unrelated listener running', async () => {
  const server = net.createServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  try { await assert.rejects(assertPortFree(server.address().port), /已被占用/); assert.equal(server.listening, true); }
  finally { await new Promise(resolve => server.close(resolve)); }
});
function upgradeFixture() {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'ic-shell-upgrade-'));
  const template = path.join(temp, 'template'); fs.mkdirSync(template); fs.writeFileSync(path.join(template, 'main.py'), 'old');
  const backend = new Backend(template, path.join(temp, 'profile')); backend.shellVersion = '0.1.0'; backend.prepare();
  fs.writeFileSync(path.join(backend.dataDir, 'canvas.json'), 'personal');
  fs.writeFileSync(path.join(template, 'main.py'), 'new'); backend.shellVersion = '0.2.0';
  return { temp, backend };
}
test('higher shell upgrades runtime only; equal version preserves later business update', () => {
  const { temp, backend } = upgradeFixture();
  try {
    backend.prepare(); assert.equal(fs.readFileSync(path.join(backend.appDir, 'main.py'), 'utf8'), 'new');
    assert.equal(fs.readFileSync(path.join(backend.pendingShellUpgrade.backup, 'main.py'), 'utf8'), 'old');
    backend.confirmShellUpgrade();
    fs.writeFileSync(path.join(backend.appDir, 'main.py'), 'business-update'); backend.prepare();
    assert.equal(fs.readFileSync(path.join(backend.appDir, 'main.py'), 'utf8'), 'business-update');
    assert.equal(fs.readFileSync(path.join(backend.dataDir, 'canvas.json'), 'utf8'), 'personal');
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
});
test('new shell startup failure restores old runtime and retains userdata', async () => {
  const { temp, backend } = upgradeFixture();
  try {
    let starts = 0;
    backend.startProcess = async skipPrepare => {
      starts++;
      if (!skipPrepare) { backend.prepare(); throw new Error('synthetic new runtime failure'); }
      assert.equal(fs.readFileSync(path.join(backend.appDir, 'main.py'), 'utf8'), 'old'); return { ok: true };
    };
    assert.equal((await backend.start()).ok, true); assert.equal(starts, 2);
    assert.equal(JSON.parse(fs.readFileSync(backend.markerFile, 'utf8')).version, '0.1.0');
    assert.equal(fs.readFileSync(path.join(backend.dataDir, 'canvas.json'), 'utf8'), 'personal');
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
});
