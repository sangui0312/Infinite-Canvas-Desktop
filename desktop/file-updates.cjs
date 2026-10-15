'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
function businessVersion(appDir, fallback) {
  try { const value=JSON.parse(fs.readFileSync(path.join(appDir,'desktop-app-version.json'),'utf8')).version;
    if (/^\d+\.\d+\.\d+$/.test(value)) return value; } catch {}
  return fallback;
}
class FileUpdates {
  constructor(backend) { this.backend=backend; this.work=path.join(backend.root,'file-updates'); }
  run(command, archive='', version='') {
    fs.mkdirSync(this.work,{recursive:true});
    const script=fs.readFileSync(path.join(__dirname,'apply_update.py'),'utf8');
    return new Promise((resolve,reject)=>{
      const child=spawn(path.join(this.backend.appDir,'python/python.exe'),['-I','-X','utf8','-c',script,command,this.backend.appDir,this.work,archive,version],{windowsHide:true,stdio:'ignore'});
      const timer=setTimeout(()=>{child.kill();reject(Error('文件更新操作超时，请重启软件恢复。'));},120000);
      child.once('error',()=>{clearTimeout(timer);reject(Error('无法启动更新校验工具。'));});
      child.once('exit',code=>{clearTimeout(timer);code===0?resolve():reject(Error('文件包或本机版本校验失败，已保留恢复记录；请使用安装包更新。'));});
    });
  }
  async recover() { if(fs.existsSync(path.join(this.work,'transaction.json'))) await this.run('rollback'); }
  inspect(file,version) { return this.run('inspect',file,version); }
  apply(file,version) { return this.run('apply',file,version); }
  confirm() { return this.run('confirm'); }
  rollback() { return this.run('rollback'); }
}
module.exports={FileUpdates,businessVersion};
