'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict'),{execFile}=require('node:child_process');
const {GitHubTransport}=require('./hybrid-updater.cjs');
const allowed=rel=>['main.py','legacy_import.py','desktop-app-version.json','VERSION'].includes(rel)||rel.startsWith('static/');
const sha=value=>crypto.createHash('sha256').update(value).digest('hex');
async function run({updates,backend,root}){
  const current=JSON.parse(fs.readFileSync(path.join(backend.appDir,'desktop-app-version.json'),'utf8')).version;
  const parts=current.split('.').map(Number),version=`${parts[0]}.${parts[1]}.${parts[2]+1}`;
  const work=path.join(root,'hybrid-proof');fs.mkdirSync(work,{recursive:true});
  const removed=path.join(backend.appDir,'static/hybrid-remove-proof.js');fs.writeFileSync(removed,'old program file');
  const base={};
  function walk(dir,rel=''){for(const item of fs.readdirSync(dir,{withFileTypes:true})){const name=path.posix.join(rel,item.name),file=path.join(dir,item.name);if(item.isDirectory())walk(file,name);else if(allowed(name))base[name]=sha(fs.readFileSync(file));}}
  walk(backend.appDir);
  const personal=path.join(backend.dataDir,'data/hybrid-personal-proof.json');fs.writeFileSync(personal,'{"preserved":true}');
  const before=sha(fs.readFileSync(personal));
  async function packagePatch(broken){
    const contents={'desktop-app-version.json':JSON.stringify({version,minimumShell:current,protocol:1}),'static/hybrid-added-proof.txt':'new program file'};
    if(broken)contents['main.py']='raise RuntimeError("synthetic startup failure")';
    const files=Object.fromEntries(Object.entries(contents).map(([rel,value])=>[rel,{size:Buffer.byteLength(value),sha256:sha(value)}]));
    const manifest={schema:1,fromVersion:current,version,baseFiles:base,files,remove:['static/hybrid-remove-proof.js']};
    const spec=path.join(work,'spec.json'),zip=path.join(work,broken?'broken.zip':'valid.zip');
    fs.writeFileSync(spec,JSON.stringify({manifest,contents}));
    const script='import json,zipfile,sys;v=json.load(open(sys.argv[1],encoding="utf-8"));z=zipfile.ZipFile(sys.argv[2],"w",zipfile.ZIP_DEFLATED);z.writestr("patch.json",json.dumps(v["manifest"]));[z.writestr("files/"+k,s.encode()) for k,s in v["contents"].items()];z.close()';
    await new Promise((resolve,reject)=>execFile(path.join(backend.appDir,'python/python.exe'),['-I','-X','utf8','-c',script,spec,zip],{windowsHide:true},error=>error?reject(error):resolve()));
    return zip;
  }
  const original={enabled:updates.enabled,transport:updates.transport};
  try{
    for(const broken of [true,false]){
      const zip=await packagePatch(broken),bytes=fs.readFileSync(zip),name=`InfiniteCanvas-Files-${current}-to-${version}.zip`;
      const manifest={schema:1,version,minimumShell:current,mode:'files',notes:'Synthetic update proof',patch:{fromVersion:current,name,url:`https://github.com/sangui0312/Infinite-Canvas-Desktop/releases/download/v${version}/${name}`,size:bytes.length,sha256:sha(bytes)}};
      const transfer=new GitHubTransport(async()=>new Response(bytes));
      updates.enabled=true;updates.set({phase:'idle',currentVersion:current,error:''});
      updates.transport={json:async url=>url.includes('api.github.com')?{tag_name:'v'+version,name:'Synthetic update',assets:[{name:'hybrid-update.json',browser_download_url:`https://github.com/sangui0312/Infinite-Canvas-Desktop/releases/download/v${version}/hybrid-update.json`}]}:manifest,download:(record,file,progress)=>transfer.download(record,file,progress)};
      assert.equal((await updates.check()).route,'files');assert.equal((await updates.download()).phase,'downloaded');
      const result=await updates.install();
      if(broken){assert.equal(result.currentVersion,current);assert.equal(result.phase,'downloaded');assert.ok(fs.existsSync(removed));}
      else{assert.equal(result.currentVersion,version);assert.equal(result.phase,'not-available');assert.equal(fs.existsSync(removed),false);assert.equal(fs.readFileSync(path.join(backend.appDir,'static/hybrid-added-proof.txt'),'utf8'),'new program file');}
      assert.equal(sha(fs.readFileSync(personal)),before);
    }
    return{fileDownloadVerified:true,unhealthyBackendRolledBack:true,newFilesApplied:true,obsoleteFilesRemoved:true,functionalVersionAdvanced:true,personalDataPreserved:true};
  }finally{updates.enabled=original.enabled;updates.transport=original.transport;}
}
module.exports={run};
