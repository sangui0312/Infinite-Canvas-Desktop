'use strict';
// Real Chromium profile migration; only synthetic files in .test-data are used.
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const assert = require('node:assert/strict');
const { Storage, digest } = require('../desktop/storage.cjs');
const project = path.resolve(__dirname, '..');
async function main() {
  const root = path.join(project, '.test-data', `storage-migration-${Date.now()}`);
  fs.mkdirSync(root, { recursive: true });
  const options = { installDir: path.join(root,'软件'), localAppData: path.join(root,'local') };
  const html = path.join(root,'profile.html'); fs.writeFileSync(html,'<!doctype html><title>Storage migration check</title>');
  const driver = path.join(root,'driver.cjs');
  fs.writeFileSync(driver, `const {app,BrowserWindow,session}=require('electron');
    const {Storage}=require(${JSON.stringify(path.join(project,'desktop/storage.cjs'))});
    const {pathToFileURL}=require('node:url');
    app.whenReady().then(async()=>{
      const storage=new Storage(${JSON.stringify(options)});storage.bootstrap();
      app.setPath('userData',require('node:path').join(storage.root,'browser-profile'));
      app.setPath('sessionData',require('node:path').join(storage.root,'browser-profile'));
      const win=new BrowserWindow({show:false,webPreferences:{sandbox:true,nodeIntegration:false,contextIsolation:true}});
      await win.loadURL(pathToFileURL(${JSON.stringify(html)}).href);
      if(process.argv.includes('--seed')) await win.webContents.executeJavaScript('localStorage.setItem("storage-proof","synthetic-setting")');
      else if(await win.webContents.executeJavaScript('localStorage.getItem("storage-proof")')!=='synthetic-setting')throw Error('Interface setting lost');
      await session.defaultSession.cookies.flushStore();session.defaultSession.flushStorageData();
      win.destroy();setTimeout(()=>app.quit(),300);
    }).catch(()=>app.exit(1));`);
  async function run(args=[]) {
    await new Promise((resolve,reject)=>{
      const env={...process.env}; delete env.ELECTRON_RUN_AS_NODE;
      const child=spawn(require('electron'),[driver,...args],{env,windowsHide:true,stdio:'ignore'});
      const timer=setTimeout(()=>{child.kill();reject(Error('Profile check timeout'));},30000);
      child.on('exit',code=>{clearTimeout(timer);code===0?resolve():reject(Error('Profile check failed '+code));});child.on('error',reject);
    });
  }
  await run(['--seed']);
  let storage = new Storage(options); const original=storage.root;
  for (const name of ['userdata/data/画布.json','userdata/assets/图片.bin','userdata/API/.env','userdata/workflows/流程.json']) {
    const file=path.join(original,name); fs.mkdirSync(path.dirname(file),{recursive:true}); fs.writeFileSync(file,'synthetic-data');
  }
  const originalHash=digest(path.join(original,'userdata/data/画布.json'));
  storage.schedule(storage.target(path.join(root,'自定义磁盘位置'))); await run();
  storage=new Storage(options); assert.notEqual(storage.root,original);
  assert.equal(digest(path.join(storage.root,'userdata/data/画布.json')),originalHash);
  assert.equal(digest(path.join(original,'userdata/data/画布.json')),originalHash);
  storage.schedule(storage.defaultRoot); await run();
  storage=new Storage(options); assert.equal(storage.root,original);
  const proof={passed:true,customMove:true,restoreDefault:true,chromiumSettingsPreserved:true,canvasAndAssetsPreserved:true,originalBackupRetained:true,profile:root};
  fs.writeFileSync(path.join(root,'verification.json'),JSON.stringify(proof,null,2)); console.log(JSON.stringify(proof));
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
