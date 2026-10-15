'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { Storage, readIni, filesIn } = require('../desktop/storage.cjs');
function fixture() {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'ic-storage-'));
  const options = { installDir: path.join(temp, 'app'), localAppData: path.join(temp, 'local') };
  fs.mkdirSync(options.installDir); fs.mkdirSync(options.localAppData);
  return { temp, options, make: () => new Storage(options), cleanup: () => fs.rmSync(temp, { recursive: true, force: true }) };
}
function put(root, name, content = 'personal') { const file = path.join(root, name); fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, content); }
test('new installation defaults beside executable; separate installations never share runtime or personal data', () => {
  const f = fixture(); try {
    const a = f.make(); a.bootstrap(); const b = new Storage({ ...f.options, installDir: path.join(f.temp, 'other-app') }); b.bootstrap();
    assert.equal(a.root, path.join(f.options.installDir, 'InfiniteCanvasData')); assert.notEqual(a.root, b.root); assert.notEqual(a.controlRoot, b.controlRoot);
    assert.ok(a.owned(a.root)); assert.equal(f.make().root, a.root);
  } finally { f.cleanup(); }
});
test('legacy data prompts without modifying the old files; migration verifies personal data and excludes old runtime/cache', () => {
  const f = fixture(); try {
    const old = path.join(f.options.localAppData, 'InfiniteCanvas');
    for (const name of ['userdata/data/canvases/a.json', 'userdata/assets/image.png', 'userdata/API/.env', 'browser-profile/Local Storage/a', 'window.json']) put(old, name);
    put(old, 'runtime/app/main.py', 'old code'); put(old, 'browser-profile/Cache/cache', 'cache');
    const a = f.make(); a.bootstrap(); assert.equal(a.info().legacy, true); assert.equal(fs.existsSync(path.join(old, 'owner.ini')), false);
    a.schedule(a.defaultRoot); const b = f.make(); b.bootstrap();
    assert.equal(b.root, b.defaultRoot); assert.equal(b.info().lastBackup, old); assert.equal(b.info().legacy, false);
    assert.equal(fs.readFileSync(path.join(b.root, 'userdata/assets/image.png'), 'utf8'), 'personal');
    assert.equal(fs.existsSync(path.join(b.root, 'runtime')), false); assert.equal(fs.existsSync(path.join(b.root, 'browser-profile/Cache')), false);
    assert.equal(fs.readFileSync(path.join(old, 'runtime/app/main.py'), 'utf8'), 'old code');
  } finally { f.cleanup(); }
});
test('custom storage relocation preserves source; keep uninstall retains data and reinstall resolves custom path', () => {
  const f = fixture(); try {
    const a = f.make(); a.bootstrap(); const original = a.root; put(original, 'userdata/data/canvas.json');
    const target = a.target(path.join(f.temp, 'chosen')); a.schedule(target);
    const b = f.make(); b.bootstrap(); assert.equal(b.root, target); assert.ok(b.owned(target));
    put(b.controlRoot, 'runtime/app/main.py', 'program'); b.uninstall(false);
    assert.equal(fs.existsSync(path.join(b.controlRoot, 'runtime')), false); assert.equal(fs.existsSync(path.join(target, 'userdata/data/canvas.json')), true);
    const c = f.make(); c.bootstrap(); assert.equal(c.root, target); assert.equal(fs.existsSync(path.join(original, 'userdata/data/canvas.json')), true);
  } finally { f.cleanup(); }
});
test('explicit uninstall deletes only current owned folder, preserving old migration backup and parent siblings', () => {
  const f = fixture(); try {
    const a = f.make(); a.bootstrap(); put(a.root, 'userdata/canvas.json'); const old = a.root;
    const parent = path.join(f.temp, 'custom'); a.schedule(a.target(parent)); const b = f.make(); b.bootstrap(); put(parent, 'unrelated.txt', 'keep');
    b.uninstall(true);
    assert.equal(fs.existsSync(b.root), false); assert.equal(fs.readFileSync(path.join(parent, 'unrelated.txt'), 'utf8'), 'keep');
    assert.equal(fs.existsSync(old), true); assert.equal(fs.existsSync(b.configFile), false);
  } finally { f.cleanup(); }
});
test('nonempty, nested, program-resource and other installations targets are rejected without overwriting files', () => {
  const f = fixture(); try {
    const a = f.make(); a.bootstrap(); const target = a.target(path.join(f.temp, 'existing')); put(target, 'mine.txt');
    assert.throws(() => a.schedule(target), /已有资料/); assert.throws(() => a.schedule(a.target(a.root)), /重叠/);
    assert.throws(() => a.schedule(a.target(path.join(a.installDir, 'resources'))), /资源/);
    const b = new Storage({ ...f.options, installDir: path.join(f.temp, 'other') }); b.bootstrap(); assert.throws(() => a.schedule(b.root), /已有资料/);
    assert.equal(fs.readFileSync(path.join(target, 'mine.txt'), 'utf8'), 'personal');
  } finally { f.cleanup(); }
});
test('failed migration and missing custom drive preserve the active source instead of opening empty data', () => {
  const f = fixture(); try {
    const a = f.make(); a.bootstrap(); put(a.root, 'userdata/canvas.json'); const original = a.root;
    const target = a.target(path.join(f.temp, 'target')); a.schedule(target); put(target, 'unexpected.json');
    const b = f.make(); b.bootstrap(); assert.equal(b.root, original); assert.match(b.migrationError, /已有资料/); assert.equal(readIni(b.configFile).pending, undefined);
    b.commit({ path: path.join(f.temp, 'disconnected', 'InfiniteCanvasData') }); assert.throws(() => f.make().bootstrap(), /不可用/);
    assert.equal(fs.existsSync(path.join(f.temp, 'disconnected')), false);
  } finally { f.cleanup(); }
});
test('source junction and foreign ownership prevent recursive deletion', t => {
  const f = fixture(); try {
    const a = f.make(); a.bootstrap(); const foreign = path.join(f.temp, 'foreign'); put(foreign, 'keep.txt');
    fs.symlinkSync(foreign, path.join(a.root, 'linked'), process.platform === 'win32' ? 'junction' : 'dir');
    assert.throws(() => filesIn(a.root), /链接/); assert.throws(() => a.uninstall(true), /链接/);
    assert.equal(fs.existsSync(path.join(foreign, 'keep.txt')), true);
    fs.unlinkSync(path.join(a.root, 'linked'));
    fs.writeFileSync(path.join(a.root, 'owner.ini'), '[InfiniteCanvas]\nowner=someone-else\n');
    assert.throws(() => a.uninstall(true), /归属/);
  } finally { f.cleanup(); }
});
test('resume after copy commit interruption selects the fully verified target', () => {
  const f = fixture(); try {
    const a = f.make(); a.bootstrap(); put(a.root, 'userdata/hello'); a.schedule(a.target(path.join(f.temp, 'dest')));
    const pendingConfig = { ...a.config }; a.finishMigration(); const target = a.root;
    a.commit(pendingConfig); const b = f.make(); b.bootstrap(); assert.equal(b.root, target); assert.equal(b.info().lastBackup, pendingConfig.path);
  } finally { f.cleanup(); }
});
test('restoring default archives its previous backup instead of blocking or overwriting it', () => {
  const f = fixture(); try {
    const a = f.make(); a.bootstrap(); put(a.root, 'userdata/canvas.json', 'original');
    a.schedule(a.target(path.join(f.temp, '自定义位置'))); a.finishMigration();
    put(a.root, 'userdata/canvas.json', 'newer');
    a.schedule(a.defaultRoot); const b = f.make(); b.bootstrap();
    assert.equal(b.root, b.defaultRoot); assert.equal(fs.readFileSync(path.join(b.root, 'userdata/canvas.json'), 'utf8'), 'newer');
    const archived = b.backups().find(item=>item.includes('.backup-'));
    assert.equal(fs.readFileSync(path.join(archived, 'userdata/canvas.json'), 'utf8'), 'original');
    b.uninstall(true); assert.equal(fs.existsSync(archived), true);
  } finally { f.cleanup(); }
});
