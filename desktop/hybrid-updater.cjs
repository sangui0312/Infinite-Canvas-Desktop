'use strict';
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const {once}=require('node:events');
const {Readable}=require('node:stream');
const {DesktopUpdater,REPOSITORY_URL}=require('./updater.cjs');
const API='https://api.github.com/repos/sangui0312/Infinite-Canvas-Desktop/releases/latest';
const stable=v=>typeof v==='string'&&/^\d+\.\d+\.\d+$/.test(v);
const compare=(a,b)=>{const x=a.split('.').map(Number),y=b.split('.').map(Number);for(let i=0;i<3;i++)if(x[i]!==y[i])return x[i]>y[i]?1:-1;return 0;};
function electronFetch(url,options={},requestFactory) {
  // net.fetch cancels manual redirects on this Electron release. ClientRequest
  // exposes the redirect before following it, so the transport can validate it.
  return new Promise((resolve,reject)=>{
    const settings={url,method:'GET',redirect:'manual',credentials:'omit',useSessionCookies:false};
    const request=requestFactory?requestFactory(settings):require('electron').net.request(settings);
    let resolved=false;
    const abort=()=>request.abort();
    if(options.signal?.aborted){request.abort();reject(Error('Download aborted'));return;}
    options.signal?.addEventListener('abort',abort,{once:true});
    request.once('close',()=>options.signal?.removeEventListener('abort',abort));
    request.on('error',error=>{if(!resolved)reject(error);});
    request.once('abort',()=>{if(!resolved)reject(Error('Download aborted'));});
    for(const [key,value] of Object.entries(options.headers||{}))request.setHeader(key,value);
    request.on('redirect',(status,_method,location,headers)=>{
      resolved=true;
      const normalized=Object.fromEntries(Object.entries(headers||{}).map(([key,value])=>[key,Array.isArray(value)?value.join(', '):String(value)]));
      normalized.location=location;
      resolve(new Response(null,{status,headers:normalized}));request.abort();
    });
    request.on('response',message=>{
      resolved=true;
      const headers=Object.fromEntries(Object.entries(message.headers).map(([key,value])=>[key,Array.isArray(value)?value.join(', '):String(value)]));
      message.once('aborted',()=>message.destroy(Error('Download aborted')));
      resolve(new Response([204,205,304].includes(message.statusCode)?null:Readable.toWeb(message),{status:message.statusCode,headers}));
    });
    request.end();
  });
}
function releaseAsset(url,version,name) {
  const expected=`${REPOSITORY_URL}/releases/download/v${version}/${name}`;
  if(url!==expected)throw Error('更新文件地址不属于当前 GitHub 发布。');
  return url;
}
class GitHubTransport {
  constructor(fetcher){this.fetcher=fetcher;this.context=new WeakMap();}
  touch(response){const state=this.context.get(response);if(state){clearTimeout(state.timer);state.timer=setTimeout(()=>state.controller.abort(),60000);}}
  finish(response){const state=this.context.get(response);if(state){clearTimeout(state.timer);state.controller.abort();this.context.delete(response);}}
  async response(url,asset=false){
    let current=url;
    for(let count=0;count<5;count++){
      const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),30000);
      let response;
      try{response=await this.fetcher(current,{credentials:'omit',redirect:'manual',cache:'no-store',headers:{Accept:'application/json','User-Agent':'Infinite-Canvas-Desktop'},signal:controller.signal});}
      finally{clearTimeout(timer);}
      if([301,302,303,307,308].includes(response.status)){
        if(!asset)throw Error('更新清单发生非预期跳转。');
        const next=new URL(response.headers.get('location'),current);
        if(next.protocol!=='https:'||next.username||next.password||!['github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'].includes(next.hostname))throw Error('更新下载发生未授权跳转。');
        current=next.href;continue;
      }
      if(!response.ok)throw Error('GitHub 更新服务暂时不可用。');
      this.context.set(response,{controller,timer:setTimeout(()=>controller.abort(),60000)});return response;
    }
    throw Error('更新下载跳转过多。');
  }
  async json(url,asset=false){
    const response=await this.response(url,asset);let size=0;const buffers=[];
    try{for await(const chunk of response.body){this.touch(response);size+=chunk.length;if(size>8*1024*1024)throw Error('更新清单过大。');buffers.push(Buffer.from(chunk));}
      return JSON.parse(Buffer.concat(buffers).toString('utf8'));
    }finally{this.finish(response);}
  }
  async download(record,file,onProgress){
    const response=await this.response(record.url,true);const hash=crypto.createHash('sha256');let size=0;
    const output=fs.createWriteStream(file+'.part');let writeError;
    output.on('error',error=>{writeError=error;});
    try{
      for await(const chunk of response.body){
        this.touch(response);
        size+=chunk.length;if(size>record.size)throw Error('更新文件大小不符。');
        hash.update(chunk);if(writeError)throw writeError;
        if(!output.write(chunk))await once(output,'drain');
        onProgress(size,record.size);
      }
      output.end();await once(output,'close');if(writeError)throw writeError;
      if(size!==record.size||hash.digest('hex')!==record.sha256)throw Error('更新文件完整性校验失败。');
      fs.renameSync(file+'.part',file);
    }catch(error){output.destroy();throw error;}finally{this.finish(response);}
  }
}
class HybridUpdater {
  constructor({updater,shellVersion,currentVersion,enabled,onState=()=>{},prepareForInstall,recoverAfterInstallFailure,transport,files,applyFiles,cacheDir}){
    this.enabled=enabled;this.shellVersion=shellVersion;this.transport=transport;this.files=files;this.applyFiles=applyFiles;this.cacheDir=cacheDir;this.onState=onState;this.busy=false;
    this.state={phase:'idle',currentVersion,shellVersion,version:'',notes:'',releaseName:'',route:'installer',percent:0,transferred:0,total:0,error:enabled?'':'开发或测试模式不连接更新服务。',repositoryUrl:REPOSITORY_URL};
    this.native=new DesktopUpdater({updater,currentVersion,enabled,prepareForInstall,recoverAfterInstallFailure,onState:state=>{if(this.state.route==='installer')this.set({...state,shellVersion});}});
  }
  getState(){return {...this.state};}
  set(patch){this.state={...this.state,...patch};this.onState(this.getState());}
  guard(phases){if(!this.enabled||this.busy||!phases.includes(this.state.phase))throw Error('当前状态不允许此更新操作。');}
  async check(){
    this.guard(['idle','available','not-available','error']);this.busy=true;this.set({phase:'checking',error:'',version:'',route:'files'});
    try{
      const release=await this.transport.json(API);const version=String(release.tag_name||'').replace(/^v/,'');
      if(!stable(version)||release.draft||release.prerelease)throw Error('发布版本无效。');
      if(compare(version,this.state.currentVersion)<=0){this.set({phase:'not-available',version:'',route:'installer'});return this.getState();}
      const asset=Array.isArray(release.assets)?release.assets.find(a=>a.name==='hybrid-update.json'):null;
      let manifest=null;
      if(asset){
        releaseAsset(asset.browser_download_url,version,'hybrid-update.json');
        manifest=await this.transport.json(asset.browser_download_url,true);
        if(manifest.schema!==1||manifest.version!==version||!stable(manifest.minimumShell)||!['files','installer'].includes(manifest.mode))throw Error('混合更新清单无效。');
      }
      const candidates=Array.isArray(manifest?.patches)&&manifest.patches.length?manifest.patches:(manifest?.patch?[manifest.patch]:[]);
      const majorOf=value=>String(value||'').split('.')[0];
      const patch=manifest?.mode==='files'&&compare(this.shellVersion,manifest.minimumShell)>=0&&majorOf(version)===majorOf(this.state.currentVersion)
        ?candidates.find(item=>item&&item.fromVersion===this.state.currentVersion)
        :null;
      if(patch){
        if(!/^[a-f0-9]{64}$/.test(patch.sha256)||!Number.isSafeInteger(patch.size)||patch.size<=0||patch.size>512*1024*1024||!/^InfiniteCanvas-Files-[0-9.]+-to-[0-9.]+\.zip$/.test(patch.name))throw Error('文件更新包信息无效。');
        releaseAsset(patch.url,version,patch.name);this.patch={...patch,version};
        this.set({phase:'available',route:'files',version,notes:String(manifest.notes||release.body||'').slice(0,15000),releaseName:String(release.name||'').slice(0,200),total:patch.size});
      }else{
        this.set({route:'installer'});this.native.state.currentVersion=this.state.currentVersion;
        await this.native.check();
      }
    }catch{this.set({phase:'error',error:'检查更新失败，请检查网络或稍后重试。'});}
    finally{this.busy=false;}
    return this.getState();
  }
  async download(){
    this.guard(['available']);
    if(this.state.route==='installer')return this.native.download();
    this.busy=true;this.set({phase:'downloading',error:'',percent:0,transferred:0});
    try{
      fs.mkdirSync(this.cacheDir,{recursive:true});this.downloaded=path.join(this.cacheDir,this.patch.name);
      await this.transport.download(this.patch,this.downloaded,(transferred,total)=>this.set({transferred,total,percent:100*transferred/total}));
      try { await this.files.inspect(this.downloaded,this.patch.version); }
      catch {
        this.set({route:'installer',notice:'功能文件包不适用于当前运行文件，已切换为安装包更新。'});
        this.native.state.currentVersion=this.state.currentVersion;
        await this.native.check(); return this.getState();
      }
      this.set({phase:'downloaded',percent:100});
    }catch{this.set({phase:'error',error:'文件更新未通过下载或本机版本校验，请重新检查或下载完整安装包。'});}
    finally{this.busy=false;}
    return this.getState();
  }
  async install(){
    this.guard(['downloaded']);
    if(this.state.route==='installer')return this.native.install();
    this.busy=true;this.set({phase:'installing',error:''});
    try{
      if(await this.applyFiles(this.downloaded,this.patch.version))this.set({phase:'not-available',currentVersion:this.patch.version,version:'',error:''});
      else this.set({phase:'downloaded'});
    }catch{this.set({phase:'downloaded',error:'文件更新未成功，已尝试恢复旧程序；请检查运行状态或使用完整安装包。'});}
    finally{this.busy=false;}
    return this.getState();
  }
}
module.exports={HybridUpdater,GitHubTransport,releaseAsset,compare,electronFetch};
