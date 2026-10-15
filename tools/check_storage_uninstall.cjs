'use strict';
// NSIS uses the actual packaged Electron storage helper, with synthetic data only.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { Storage, readIni, writeIni, digest } = require('../desktop/storage.cjs');
const project = path.resolve(__dirname, '..');
const root = path.join(project, '.test-data', `uninstall-storage-${Date.now()}`);
fs.mkdirSync(root, { recursive: true });
const env = { ...process.env, LOCALAPPDATA: path.join(root, 'local') };
delete env.ELECTRON_RUN_AS_NODE; delete env.IC_DESKTOP_ROOT;
function run(executable, args, timeout = 60000) {
  return new Promise((resolve, reject) => {
    const child = spawn(executable, args, { env, windowsHide: true, windowsVerbatimArguments: args.some(arg=>arg.startsWith('_?=')), stdio: 'ignore' });
    const timer = setTimeout(() => { child.kill(); reject(Error('Storage uninstall helper timed out')); }, timeout);
    child.once('error', e => { clearTimeout(timer); reject(e); });
    child.once('exit', code => { clearTimeout(timer); code === 0 ? resolve() : reject(Error(`${path.basename(executable)} ${args[0] || ''} exited ${code}`)); });
  });
}
async function main() {
  const results = [];
  for (const mode of ['keep', 'delete', 'update', 'silent']) {
    const installDir = path.join(root, `安装-${mode}`);
    fs.cpSync(path.join(project, 'dist/win-unpacked'), installDir, { recursive: true, filter: source => path.relative(path.join(project, 'dist/win-unpacked'), source).replaceAll('\\','/') !== 'resources/backend' });
    const storage = new Storage({ installDir, localAppData: env.LOCALAPPDATA }); storage.bootstrap();
    fs.mkdirSync(path.join(storage.root, 'userdata/assets'), { recursive: true });
    const media = path.join(storage.root, 'userdata/assets/素材.bin'); fs.writeFileSync(media, 'synthetic-personal-data');
    // Exercise a custom location too; the parent and unrelated files must survive.
    if (mode === 'delete') { storage.schedule(storage.target(path.join(root, '自定义资料'))); storage.finishMigration(); }
    const actualMedia = path.join(storage.root, 'userdata/assets/素材.bin'), before = digest(actualMedia);
    fs.mkdirSync(path.join(storage.controlRoot, 'runtime/app'), { recursive: true }); fs.writeFileSync(path.join(storage.controlRoot, 'runtime/app/main.py'), 'synthetic program');
    const unrelated = path.join(path.dirname(storage.root), '其他文件.txt'); fs.writeFileSync(unrelated, 'preserve');
    const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'ic-uninstall-info-'));
    try {
      await run(path.join(installDir, 'Infinite Canvas.exe'), ['--storage-info', path.join(temp, 'storage.ini')]);
      assert.equal(readIni(path.join(temp, 'storage.ini')).path, storage.root);
      assert.equal(readIni(path.join(temp, 'storage.ini')).deletable, '1');
    } finally { fs.rmSync(temp, { recursive: true, force: true }); }
    writeIni(path.join(project, '.test-data/storage-uninstall-fixture.ini'), { path: installDir });
    await run(path.join(project, '.test-data/storage-uninstall-check.exe'), []);
    const args = mode === 'delete' ? ['/TEST-DELETE'] : mode === 'update' ? ['/TEST-DELETE','/TEST-UPDATE'] : ['/S'];
    // _?= makes NSIS waitable and keeps the fixture install directory explicit.
    await run(path.join(installDir, 'Uninstall Infinite Canvas.exe'), [...args, `_?=${installDir}`]);
    assert.equal(fs.existsSync(path.join(installDir, 'Infinite Canvas.exe')), false);
    assert.equal(fs.readFileSync(unrelated, 'utf8'), 'preserve');
    if (mode === 'delete') {
      assert.equal(fs.existsSync(storage.root), false); assert.equal(fs.existsSync(storage.configFile), false);
      assert.equal(digest(media), before); // Migration backup remains.
    } else {
      assert.equal(digest(actualMedia), before); assert.equal(fs.existsSync(storage.configFile), true);
      assert.equal(fs.existsSync(path.join(storage.controlRoot, 'runtime')), mode === 'update');
    }
    results.push({ mode, programRemoved:true, personalDataDeleted:mode==='delete', unrelatedPreserved:true, unicodePath:true });
  }
  fs.writeFileSync(path.join(root,'verification.json'), JSON.stringify(results,null,2));
  console.log(JSON.stringify({ passed:true, results, fixture:root }));
}
main().catch(e=>{ console.error(e.stack); process.exitCode=1; });
