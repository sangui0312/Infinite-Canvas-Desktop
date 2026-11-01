'use strict';
const {app,BrowserWindow}=require('electron');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
app.setPath('userData',fs.mkdtempSync(path.join(root,'.test-data/minimax-browser-')));
app.on('window-all-closed',()=>{});
let child,win,base,log='';
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function main(){
    const backendDir=process.argv.includes('--packaged') ? path.join(root,'dist/win-unpacked/resources/backend') : path.join(root,'backend');
    child=spawn(path.join(backendDir,'python/python.exe'),['-B','-X','utf8',path.join(__dirname,'minimax_integration_server.py')],{cwd:root,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',PYTHONUTF8:'1',MINIMAX_UI_BACKEND:backendDir},windowsHide:true,stdio:['ignore','pipe','pipe']});
    child.stdout.on('data',b=>{log+=b;const match=log.match(/MINIMAX_UI_READY (\d+)/);if(match)base='http://127.0.0.1:'+match[1];});
    child.stderr.on('data',b=>log+=b);
    for(let i=0;i<200;i++){
        if(base){try{if((await fetch(base+'/__minimax_fixture')).ok)break;}catch{}}
        if(child.exitCode!==null)throw Error(log.slice(-2500));
        await delay(100);
    }
    assert.ok(base,log.slice(-2500));await app.whenReady();
    win=new BrowserWindow({show:false,width:1280,height:900,webPreferences:{contextIsolation:true,nodeIntegration:false,backgroundThrottling:false}});
    win.webContents.session.webRequest.onBeforeRequest((d,cb)=>cb({cancel:!d.url.startsWith(base)&&!d.url.startsWith('data:')&&!d.url.startsWith('blob:')}));
    const errors=[];win.webContents.on('render-process-gone',(_,details)=>errors.push(details.reason));
    const js=code=>win.webContents.executeJavaScript(code);
    async function wait(code,label){for(let i=0;i<150;i++){try{if(await js(code))return;}catch{}await delay(100);}throw Error('Timed out: '+label+' '+await js(`JSON.stringify({items:typeof smartH3Items==='function' ? smartH3Items() : [],text:document.body.innerText.slice(-1600)})`));}
    await win.loadURL(base+'/static/canvas.html?id=minimax-classic');
    await wait(`typeof canvasH3Items==='function' && canvasH3Items().some(item=>item.h3_request)`,'classic load');
    assert.equal(await js(`Boolean(document.querySelector('[data-h3-upgrade-url]'))`),true);
    await js(`window.confirm=()=>true;document.querySelector('[data-h3-upgrade-url]').click()`);
    await wait(`canvasH3Items().some(item=>item.h3_regeneration?.status==='generating')`,'classic progress');
    assert.equal(await js(`canvasH3Items().some(item=>item.url==='/output/fixture.mp4')`),true);
    await wait(`canvasH3Items().some(item=>item.h3_request?.model==='MiniMax-H3-Regeneration')`,'classic 2k result');
    await delay(300);
    await js(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))))`);
    fs.writeFileSync(path.join(root,'.test-data/minimax-classic-ui.png'),(await win.webContents.capturePage()).toPNG());
    await js(`saveCanvas(true)`);
    await win.loadURL(base+'/static/canvas.html?id=minimax-classic');
    await wait(`typeof canvasH3Items==='function' && canvasH3Items().some(item=>item.h3_request?.model==='MiniMax-H3-Regeneration')`,'classic durable result');
    await win.loadURL(base+'/static/smart-canvas.html?id=minimax-smart');
    await wait(`typeof smartH3Items==='function' && smartH3Items().some(item=>item.h3_request)`,'smart load');
    await js(`selectedId='preview';selectedImage={nodeId:'preview',index:0};render();window.confirm=()=>true;void 0;`);
    await wait(`Boolean(document.querySelector('[data-smart-node-action="h3Upgrade"]'))`,'smart toolbar');
    await js(`document.querySelector('[data-smart-node-action="h3Upgrade"]').click()`);
    await wait(`smartH3Items().some(item=>item.h3_regeneration?.status==='generating')`,'smart progress');
    await wait(`smartH3Items().some(item=>item.h3_request?.model==='MiniMax-H3-Regeneration')`,'smart result');
    await wait(`document.querySelector('[data-smart-node-action="h3Upgrade"]')?.textContent.includes('2K 已完成')`,'smart completed button');
    assert.equal(await js(`smartH3Items().some(item=>item.url==='/output/fixture.mp4')`),true);
    await js(`settings.videoProvider='apimart';settings.videoModel='MiniMax-H3-Max';settings.videoResolution='1080p';settings.apiKind='video';renderApiVideoParams();`);
    assert.equal(await js(`Boolean(document.querySelector('[data-smart-param="videoResolution"][data-smart-value="2k"]'))`),false);
    assert.equal(await js(`Boolean(document.querySelector('[data-toggle-param="videoEnableUpsample"]'))`),false);
    assert.equal(await js(`Boolean(document.querySelector('[data-toggle-param="videoWatermark"]'))`),false);
    await js(`settings.videoUseFrameRoles=true;renderApiVideoParams();`);
    assert.equal(await js(`document.querySelector('.aspect-control button').disabled`),true);
    await delay(300);
    await js(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))))`);
    const screen=await js(`JSON.stringify({button:document.querySelector('[data-smart-node-action="h3Upgrade"]')?.textContent,nodes:nodes.map(n=>({id:n.id,x:n.x,y:n.y,model:n.images?.[0]?.h3_request?.model,rect:(()=>{const r=document.querySelector('.image-node[data-id="'+n.id+'"]')?.getBoundingClientRect();return r ? {x:r.x,y:r.y,width:r.width,height:r.height}:null;})()}))})`);
    fs.writeFileSync(path.join(root,'.test-data/minimax-smart-ui.json'),screen);
    fs.writeFileSync(path.join(root,'.test-data/minimax-smart-ui.png'),(await win.webContents.capturePage()).toPNG());
    const report=await(await fetch(base+'/__minimax_fixture')).json();
    assert.equal(report.posts.length,2);assert.equal(errors.length,0);
    console.log('MINIMAX_UI_OK: both real canvas windows, preview retained, explicit upgrade/progress, independent 2K result, save/reopen, Max controls and frame aspect; exactly two mocked submissions.');
    win.destroy();child.kill();app.quit();
}
main().catch(error=>{console.error(error.stack,log.slice(-2400));if(win)win.destroy();if(child)child.kill();app.exit(1);});
