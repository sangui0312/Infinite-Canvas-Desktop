'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),{EventEmitter}=require('node:events'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {HybridUpdater,GitHubTransport,electronFetch}=require('../desktop/hybrid-updater.cjs');
function fixture(overrides={}){
  class Native extends EventEmitter{setFeedURL(){} async checkForUpdates(){this.emit('update-available',{version:'1.2.4',releaseNotes:'installer'});} async downloadUpdate(){this.emit('update-downloaded',{version:'1.2.4'});} quitAndInstall(){this.installed=true;}}
  const native=new Native(),calls=[],temp=fs.mkdtempSync(path.join(os.tmpdir(),'ic-hybrid-'));
  const patch={fromVersion:'1.2.3',name:'InfiniteCanvas-Files-1.2.3-to-1.2.4.zip',url:'https://github.com/sangui0312/Infinite-Canvas-Desktop/releases/download/v1.2.4/InfiniteCanvas-Files-1.2.3-to-1.2.4.zip',size:10,sha256:'a'.repeat(64)};
  const manifest={schema:1,version:'1.2.4',minimumShell:'1.0.0',mode:'files',patch};
  const transport={json:async url=>url.includes('api.github.com')?{tag_name:'v1.2.4',name:'release',assets:[{name:'hybrid-update.json',browser_download_url:'https://github.com/sangui0312/Infinite-Canvas-Desktop/releases/download/v1.2.4/hybrid-update.json'}]}:manifest,download:async(p,file,progress)=>{calls.push('download');progress(10,10);}};
  const files={inspect:async()=>calls.push('inspect')};
  const controller=new HybridUpdater({updater:native,shellVersion:'1.2.3',currentVersion:'1.2.3',enabled:true,transport,files,cacheDir:temp,applyFiles:async()=>{calls.push('apply');return true;},prepareForInstall:async()=>true,...overrides});
  return{controller,native,manifest,transport,files,calls,cleanup:()=>fs.rmSync(temp,{recursive:true,force:true})};
}
test('matching exact-base update downloads files, verifies before apply and advances functional version only',async()=>{
  const f=fixture();try{assert.equal((await f.controller.check()).route,'files');assert.equal((await f.controller.download()).phase,'downloaded');assert.deepEqual(f.calls,['download','inspect']);const result=await f.controller.install();assert.equal(result.currentVersion,'1.2.4');assert.equal(result.shellVersion,'1.2.3');assert.equal(f.native.installed,undefined);}finally{f.cleanup();}
});
test('wrong base or newer foundation routes to installer with differential enabled',async()=>{
  for(const change of [m=>m.patch.fromVersion='1.0.0',m=>m.minimumShell='9.0.0']){const f=fixture();try{change(f.manifest);assert.equal((await f.controller.check()).route,'installer');assert.equal(f.native.disableDifferentialDownload,false);await f.controller.download();await f.controller.install();assert.equal(f.native.installed,true);}finally{f.cleanup();}}
});
test('incompatible local file inventory switches to verified installer without applying the patch',async()=>{
  const f=fixture();try{f.files.inspect=async()=>{throw Error('base changed');};await f.controller.check();const result=await f.controller.download();assert.equal(result.route,'installer');assert.equal(result.phase,'available');await assert.rejects(f.controller.install());assert.ok(!f.calls.includes('apply'));}finally{f.cleanup();}
});
test('cancelled or failed file application retains the previous version',async()=>{
  for(const action of [async()=>false,async()=>{throw Error('failed');}]){const f=fixture({applyFiles:action});try{await f.controller.check();await f.controller.download();const result=await f.controller.install();assert.equal(result.currentVersion,'1.2.3');assert.equal(result.phase,'downloaded');}finally{f.cleanup();}}
});
test('manifest cannot fetch a package on another host and disabled mode never connects',async()=>{
  const f=fixture();try{f.manifest.patch.url='https://evil.example/file.zip';assert.equal((await f.controller.check()).phase,'error');}finally{f.cleanup();}
  const d=fixture({enabled:false});try{await assert.rejects(d.controller.check());}finally{d.cleanup();}
});
test('GitHub transport rejects external redirects and mismatched downloaded bytes',async()=>{
  const t=new GitHubTransport(async()=>new Response('',{status:302,headers:{location:'https://evil.example/payload'}}));await assert.rejects(t.response('https://github.com/source',true));
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'ic-download-'));try{const bytes=Buffer.from('payload');const t=new GitHubTransport(async()=>new Response(bytes));await assert.rejects(t.download({size:bytes.length,sha256:'0'.repeat(64),url:'https://github.com/source'},path.join(dir,'update.zip'),()=>{}));assert.equal(fs.existsSync(path.join(dir,'update.zip')),false);}finally{fs.rmSync(dir,{recursive:true,force:true});}
});
test('Electron request exposes redirects for validation and abort signals cannot leave a hanging check',async()=>{
  class Request extends EventEmitter{setHeader(){}end(){}abort(){this.emit('abort');this.emit('close');}}
  const controller=new AbortController(),pending=electronFetch('https://api.github.com/test',{signal:controller.signal},()=>new Request());
  controller.abort();await assert.rejects(pending,/aborted/);
  const request=new Request();request.end=()=>queueMicrotask(()=>request.emit('redirect',302,'GET','https://release-assets.githubusercontent.com/test',{}));
  const response=await electronFetch('https://github.com/asset',{},()=>request);
  assert.equal(response.status,302);assert.equal(response.headers.get('location'),'https://release-assets.githubusercontent.com/test');
});
