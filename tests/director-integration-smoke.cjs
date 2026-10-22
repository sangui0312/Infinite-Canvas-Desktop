// Real Chromium + WebGL/MediaRecorder + local uploads. Uses a disposable backend.
'use strict';
const {app,BrowserWindow}=require('electron');
const {spawn}=require('node:child_process');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),out=path.join(root,'.test-data/director-smoke');
fs.mkdirSync(out,{recursive:true});app.setPath('userData',fs.mkdtempSync(path.join(out,'profile-')));
app.commandLine.appendSwitch('use-angle','swiftshader');app.commandLine.appendSwitch('enable-unsafe-swiftshader');
app.on('window-all-closed',()=>{});
let child,win,base,logs='';const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function main(){
    const env={PYTHONIOENCODING:'utf-8',PYTHONUTF8:'1',PYTHONDONTWRITEBYTECODE:'1'};
    for(const key of ['SYSTEMROOT','WINDIR','TEMP','TMP','PATH'])if(process.env[key])env[key]=process.env[key];
    child=spawn(path.join(root,'backend/python/python.exe'),['-B','-X','utf8',path.join(root,'tests/agent_integration_server.py')],{cwd:root,windowsHide:true,env,stdio:['ignore','pipe','pipe']});
    child.stdout.on('data',b=>{logs+=b.toString();const m=logs.match(/AGENT_SMOKE_READY (\d+)/);if(m)base='http://127.0.0.1:'+m[1];});child.stderr.on('data',b=>logs+=b.toString());
    for(let i=0;i<200;i++){if(base){try{if((await fetch(base+'/__agent_smoke/status')).ok)break;}catch{}}await delay(100);}
    assert.ok(base,logs);await app.whenReady();
    win=new BrowserWindow({show:false,width:1450,height:1000,webPreferences:{contextIsolation:true,nodeIntegration:false,backgroundThrottling:false}});
    const errors=[];win.webContents.on('console-message',(...args)=>{if(args[0].level==='error'||args[1]===3)errors.push(args[0].message||args[2]);});
    const js=code=>win.webContents.executeJavaScript(code);
    async function wait(expr,label){for(let i=0;i<150;i++){if(await js(expr))return;await delay(100);}throw Error(`${label}: ${await js(`document.querySelector('[data-director-status]')?.textContent`)} / ${errors.join('\n')}`);}
    const click=action=>js(`document.querySelector('[data-director-action="${action}"]').click()`);
    async function snapshot(name){await win.webContents.capturePage();await delay(120);fs.writeFileSync(path.join(out,name),(await win.webContents.capturePage()).toPNG());}
    const input=(selector,value,event='input')=>js(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.focus();e.value=${JSON.stringify(value)};e.dispatchEvent(new Event('${event}',{bubbles:true}));e.blur();})()`);
    await win.loadURL(base+'/static/smart-canvas.html?id=smoke');
    await wait(`typeof createSmartDirectorNode==='function' && canvas?.id==='smoke'`,'canvas ready');
    await js(`(async()=>{
        window.directorErrors=[];addEventListener('error',e=>directorErrors.push(e.message));addEventListener('unhandledrejection',e=>directorErrors.push(String(e.reason)));
        const T=await import('/static/vendor/js/three-0.160.0.module.js');window.sizeCalls=0;window.disposedGeometries=0;
        const size=T.WebGLRenderer.prototype.setSize;T.WebGLRenderer.prototype.setSize=function(...args){sizeCalls++;return size.apply(this,args);};
        const dispose=T.BufferGeometry.prototype.dispose;T.BufferGeometry.prototype.dispose=function(){disposedGeometries++;return dispose.call(this);};
        window.recordedTracks=[];const capture=HTMLCanvasElement.prototype.captureStream;HTMLCanvasElement.prototype.captureStream=function(...args){const stream=capture.apply(this,args);recordedTracks.push(...stream.getTracks());return stream;};
        window.fixtureDirector=createSmartDirectorNode(120,180);openSmartDirectorNode(fixtureDirector);
    })()`);
    await wait(`document.querySelector('[data-director-canvas] canvas') && !document.querySelector('[data-director-action=png]').disabled`,'WebGL editor ready');
    assert.equal(await js(`document.querySelectorAll('[data-key-index]').length`),4);
    // Zoom must change the image, without reallocating the drawing buffer each frame.
    const zoom=await js(`(()=>{const c=document.querySelector('[data-director-canvas] canvas'),before=c.toDataURL(),calls=sizeCalls;c.dispatchEvent(new WheelEvent('wheel',{deltaY:-120,bubbles:true,cancelable:true}));return {changed:before!==c.toDataURL(),resizes:sizeCalls-calls};})()`);
    assert.deepEqual(zoom,{changed:true,resizes:0});
    await click('add-piece');assert.equal(await js(`fixtureDirector.director.pieces.length`),2);
    await input('[data-field="piece.name"]','棋子 <img src=x onerror=alert(1)>');
    assert.equal(await js(`Boolean(document.querySelector('.director-object img'))`),false);
    assert.ok((await js(`document.querySelector('[data-director-object-list]').textContent`)).includes('<img'));
    await input('[data-field="piece.name"]','绿色棋子');
    await click('duplicate');assert.equal(await js(`fixtureDirector.director.pieces.length`),3);
    await click('delete-object');assert.equal(await js(`fixtureDirector.director.pieces.length`),2);
    await click('undo');assert.equal(await js(`fixtureDirector.director.pieces.length`),3);
    await click('redo');assert.equal(await js(`fixtureDirector.director.pieces.length`),2);
    await click('add-block');await input('[data-field="prop.w"]','3.5');assert.equal(await js(`fixtureDirector.director.props.at(-1).w`),3.5);
    await click('undo');assert.equal(await js(`fixtureDirector.director.props.at(-1).w`),1.4);
    await click('redo');assert.equal(await js(`fixtureDirector.director.props.at(-1).w`),3.5);
    // Real pointer input for timeline retiming; one undo entry per complete drag.
    const key=await js(`(()=>{const e=document.querySelector('[data-key-track=piece_red][data-key-index="1"]'),r=e.getBoundingClientRect(),t=e.closest('[data-track]').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2,target:t.x+t.width*.65};})()`);
    win.webContents.sendInputEvent({type:'mouseMove',x:Math.round(key.x),y:Math.round(key.y)});
    win.webContents.sendInputEvent({type:'mouseDown',x:Math.round(key.x),y:Math.round(key.y),button:'left',clickCount:1});
    win.webContents.sendInputEvent({type:'mouseMove',x:Math.round(key.target),y:Math.round(key.y)});
    win.webContents.sendInputEvent({type:'mouseUp',x:Math.round(key.target),y:Math.round(key.y),button:'left',clickCount:1});
    await delay(80);assert.ok(Math.abs(await js(`fixtureDirector.director.pieces[0].keyframes[1].t`)-6.5)<.1);
    await click('delete-keyframe');assert.equal(await js(`fixtureDirector.director.pieces[0].keyframes.length`),1);await click('undo');
    await click('play');const a=await js(`Number(document.querySelector('[data-director-playhead]').value)`);await wait(`Number(document.querySelector('[data-director-playhead]').value)!==${a}`,'playhead advances');await click('play');
    // Typing does not trigger director or underlying canvas shortcuts.
    await js(`document.querySelector('[data-director-duration]').focus()`);
    win.webContents.sendInputEvent({type:'keyDown',keyCode:'Space'});win.webContents.sendInputEvent({type:'keyUp',keyCode:'Space'});
    assert.equal(await js(`document.querySelector('[data-director-action=play]').textContent`),'播放');
    await input('[data-director-duration]','2','change');await input('[data-director-ratio]','9:16','change');
    await click('mode-director');await delay(350);
    await snapshot('director-light.png');
    await js(`document.documentElement.classList.add('studio-theme-dark')`);await delay(350);
    await snapshot('director-dark.png');
    win.setSize(650,760);await delay(100);
    assert.equal(await js(`(()=>{const e=document.querySelector('.director-shell'),r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.bottom<=innerHeight+1&&e.scrollWidth<=e.clientWidth;})()`),true);
    await snapshot('director-small.png');
    win.setSize(390,760);await delay(150);
    assert.equal(await js(`(()=>{const e=document.querySelector('.director-shell');return e.scrollWidth<=e.clientWidth && document.querySelector('.director-transport').scrollWidth<=e.clientWidth;})()`),true);
    win.setSize(1450,1000);await delay(80);
    await click('png');await wait(`fixtureDirector.director.exports.length===1 && document.querySelector('[data-director-export-progress]').hidden`,'PNG saved');
    const png=await js(`(async()=>{const url=fixtureDirector.director.exports[0].url,img=new Image();img.src=url;await img.decode();return {w:img.naturalWidth,h:img.naturalHeight};})()`);assert.deepEqual(png,{w:720,h:1280});
    const before=await js(`fixtureDirector.director.playhead`);await click('webm');await delay(180);
    assert.equal(await js(`document.querySelector('[data-director-playhead]').disabled`),true);await click('cancel-export');await delay(150);
    assert.equal(await js(`fixtureDirector.director.exports.length`),1);assert.equal(await js(`fixtureDirector.director.playhead`),before);
    assert.equal(await js(`recordedTracks.every(t=>t.readyState==='ended')`),true);
    await click('webm');await wait(`fixtureDirector.director.exports.length===2 && document.querySelector('[data-director-export-progress]').hidden`,'WebM saved');
    const video=await js(`(async()=>{const v=document.createElement('video');v.preload='metadata';const done=new Promise((resolve,reject)=>{v.onloadedmetadata=()=>resolve({w:v.videoWidth,h:v.videoHeight});v.onerror=()=>reject(Error('Invalid exported video'));setTimeout(()=>reject(Error('metadata timeout')),5000);});v.src=fixtureDirector.director.exports[1].url;const r=await done;v.removeAttribute('src');v.load();return r;})()`);assert.deepEqual(video,{w:720,h:1280});
    const draft=await js(`fixtureDirector.director.playhead`);await click('webm');await delay(120);await click('close');await delay(180);
    assert.equal(await js(`Boolean(document.querySelector('.director-modal'))`),false);assert.equal(await js(`recordedTracks.every(t=>t.readyState==='ended')`),true);assert.equal(await js(`fixtureDirector.director.playhead`),draft);
    assert.equal(await js(`fixtureDirector.director.exports.length`),2);assert.ok(await js(`disposedGeometries>4`));
    await js(`openSmartDirectorNode(fixtureDirector)`);await wait(`document.querySelector('[data-director-canvas] canvas')`,'reopen');await click('close');
    // Unsupported encoders and constructor errors must leave the editor usable.
    await js(`openSmartDirectorNode(fixtureDirector)`);await wait(`!document.querySelector('[data-director-action=webm]').disabled`,'reopen renderer');
    await js(`window.originalRecorder=MediaRecorder;window.MediaRecorder=class{static isTypeSupported(){return false;}};void 0`);await click('webm');
    assert.equal(await js(`document.querySelector('[data-director-export-progress]').hidden`),true);
    assert.ok((await js(`document.querySelector('[data-director-status]').textContent`)).includes('PNG'));
    await js(`window.MediaRecorder=class{static isTypeSupported(){return true;}constructor(){throw Error('fixture encoder failure');}};void 0`);await click('webm');
    assert.equal(await js(`recordedTracks.every(t=>t.readyState==='ended')`),true);
    await js(`window.MediaRecorder=originalRecorder;void 0`);await click('close');
    // A late upload callback from a closed dialog cannot change the new session.
    await js(`window.lateOutputs=0;SmartDirector.open({director:fixtureDirector.director},{upload:()=>new Promise(r=>window.finishLateUpload=r),createOutput:()=>{lateOutputs++;return {};},save:()=>{},toast:()=>{}})`);
    await wait(`!document.querySelector('[data-director-action=png]').disabled`,'late-upload renderer');await click('png');
    await wait(`typeof finishLateUpload==='function'`,'delayed upload started');await click('close');
    await js(`openSmartDirectorNode(fixtureDirector);finishLateUpload([{url:'/fixture-delayed.png'}])`);await delay(180);
    assert.equal(await js(`lateOutputs`),0);assert.equal(await js(`Boolean(document.querySelector('.director-modal'))`),true);await click('close');
    assert.deepEqual(await js(`directorErrors`),[]);assert.deepEqual(errors.filter(x=>!String(x).includes('CONTEXT_LOST_WEBGL')),[]);
    console.log('DIRECTOR_SMOKE_OK',JSON.stringify({actualCanvas:true,actualWebGL:true,zoom,objectUndoRedo:true,keyframeDragDelete:true,playheadSync:true,smallAndDarkLayout:true,png,video,recordingCancelAndClose:true,outputNodes:2,folder:out}));
    win.destroy();await fetch(base+'/__agent_smoke/shutdown',{method:'POST'});app.quit();
}
main().catch(error=>{console.error(error.stack||error,logs.slice(-2500));if(win)win.destroy();if(child)child.kill();app.exit(1);});
