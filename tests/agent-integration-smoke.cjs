// Actual page + bridge + FastAPI/Agent/host/ops; only upstream inference is fake.
const {app,BrowserWindow} = require('electron');
const {spawn,spawnSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root=path.resolve(__dirname,'..');
fs.mkdirSync(path.join(root,'.test-data'),{recursive:true});
app.setPath('userData',fs.mkdtempSync(path.join(root,'.test-data/agent-integration-ui-')));
const mixed=process.argv.includes('--mixed'), placeholders=process.argv.includes('--placeholders'), expected=mixed?21:placeholders?8:3;
const packaged=process.argv.includes('--packaged');
const personaManagement=process.argv.includes('--personas');
const questionMode=process.argv.includes('--questions');
app.on('window-all-closed',()=>{});
let child,win,base,exited=false;
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function run() {
    const env={PYTHONIOENCODING:'utf-8',PYTHONUTF8:'1',PYTHONDONTWRITEBYTECODE:'1'};
    for(const key of ['SYSTEMROOT','WINDIR','TEMP','TMP','PATH']) if(process.env[key]) env[key]=process.env[key];
    const program=packaged?path.join(root,'dist/win-unpacked/resources/backend'):path.join(root,'backend');
    child=spawn(path.join(program,'python/python.exe'),['-X','utf8',path.join(__dirname,'agent_integration_server.py'),...(packaged?['--packaged']:[]),...(mixed?['--mixed']:[]),...(placeholders?['--placeholders']:[]),...(process.argv.includes('--progress')?['--progress']:[]),...(questionMode?['--questions']:[])],{cwd:root,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    let output='';
    child.stdout.on('data',chunk=>{output=(output+chunk.toString()).slice(-14000);const match=output.match(/AGENT_SMOKE_READY (\d+)/);if(match)base='http://127.0.0.1:'+match[1];});
    child.stderr.on('data',chunk=>{output=(output+chunk.toString()).slice(-14000);});
    child.on('exit',()=>{exited=true;});
    for(let i=0;i<300;i++){if(exited)throw new Error('Fixture server exited: '+output);if(base){try{if((await fetch(base+'/__agent_smoke/status')).ok)break;}catch{}}await delay(100);if(i===299)throw new Error('Fixture server timeout: '+output);}
    await app.whenReady();
    win=new BrowserWindow({show:false,width:1450,height:950,webPreferences:{offscreen:true,nodeIntegration:false,contextIsolation:true}});
    win.webContents.session.webRequest.onBeforeRequest((details,callback)=>callback({cancel:!details.url.startsWith(base+'/') && !details.url.startsWith('data:') && !details.url.startsWith('blob:')}));
    const errors=[];win.webContents.on('console-message',(_event,...args)=>{const msg=args.find(x=>typeof x==='string');if(msg?.includes('Uncaught'))errors.push(msg);});
    await win.loadURL(base+'/static/smart-canvas.html?id=smoke');
    const js=async code=>{
        const value=await win.webContents.executeJavaScript(`(async()=>{try{return await eval(${JSON.stringify(code)});}catch(error){return {__smokeError:error.stack};}})()`);
        if(value?.__smokeError)throw new Error(value.__smokeError+'\nFixture script: '+code.slice(0,240));
        return value;
    };
    async function waitFor(condition,label){for(let i=0;i<200;i++){if(await js(condition))return;if(await js(`document.querySelector('.sa-run h3')?.textContent==='失败'`))break;await delay(100);}throw new Error(label+' timed out: '+await js(`document.querySelector('.sa-notice')?.textContent+' / '+document.querySelector('.sa-run')?.textContent+' / fixture generation: '+JSON.stringify(window.smartAgentCanvas?.getContext()?.generation)`));}
    await waitFor(`window.smartAgentCanvas?.getContext()?.canvas_id==='smoke' && document.querySelector('.smart-agent-toggle')`,'actual canvas bridge');
    await js(`document.querySelector('.smart-agent-toggle').click()`);
    await waitFor(`document.querySelector('.sa-controls select')?.value==='apimart' && document.querySelectorAll('.sa-controls select')[1]?.value==='testllm' && !document.querySelector('.sa-compose button[type=submit]')?.disabled`,'provider controls');
    await waitFor(`Boolean(document.querySelector('.sa-generation [aria-label="分辨率"]'))`,'generation parameter options');
    if(personaManagement){
        await js(`document.querySelector('[data-sa-menu=persona]').click()`);
        await waitFor(`!document.querySelector('[data-sa-persona-new]').disabled`,'persona library loaded');
        await js(`document.querySelector('[data-sa-persona-new]').click();document.querySelector('[aria-label="人设名称"]').value='套图策划师';document.querySelector('[aria-label="人设简介"]').value='每张图有不同的信息重点';document.querySelector('[aria-label="人设提示词"]').value='先规划整套图的信息分工，再为每张图编写完整提示词。保持商品一致，各图主题、卖点和文案不重复。';document.querySelector('[data-sa-persona-save]').click()`);
        await waitFor(`document.querySelector('[aria-label="创作人设"]').selectedOptions[0]?.textContent==='套图策划师' && document.querySelector('.sa-persona-editor').hidden && !document.querySelector('[data-sa-persona-edit]').disabled`,'new persona saved and selected');
        await js(`document.querySelector('[data-sa-persona-edit]').click()`);
        await waitFor(`!document.querySelector('.sa-persona-editor').hidden && !document.querySelector('[data-sa-persona-save]').disabled`,'custom persona editor opened');
        await js(`document.querySelector('[aria-label="人设提示词"]').value+=' 每套图先检查主题差异。'`);
        await delay(150);fs.writeFileSync(path.join(root,'docs/screenshots/smart-agent-personas.png'),(await win.webContents.capturePage()).toPNG());
        await js(`document.querySelector('[data-sa-persona-save]').click()`);
        await waitFor(`document.querySelector('.sa-persona-editor').hidden && !document.querySelector('[data-sa-persona-edit]').disabled`,'custom persona edited');
        const catalog=await(await fetch(base+'/api/canvas-agent/personas')).json();
        const custom=catalog.personas.find(p=>p.name==='套图策划师');assert.equal(custom.revision,2);assert.ok(custom.prompt.includes('检查主题差异'));
        await win.reload();
        await waitFor(`window.smartAgentCanvas?.getContext()?.canvas_id==='smoke' && document.querySelector('.smart-agent-toggle')`,'canvas reloaded');
        await js(`document.querySelector('.smart-agent-toggle').click()`);
        await waitFor(`document.querySelector('[aria-label="创作人设"]').value===${JSON.stringify(custom.id)} && !document.querySelector('.sa-compose button[type=submit]').disabled && Boolean(document.querySelector('.sa-generation [aria-label="分辨率"]'))`,'custom role restored after page restart');
        await js(`(()=>{const s=document.querySelector('[aria-label="创作人设"]');s.value='general';s.dispatchEvent(new Event('change'));})()`);
    }
    await js(`document.querySelector('.sa-generation [aria-label="分辨率"]').value='4k';document.querySelector('.sa-generation [aria-label="数量"]').value='${mixed?1:expected}';document.querySelector('.sa-compose textarea').value='${mixed?'制作8张轮播图，12张详情图，1条15秒视频':placeholders?'制作8张海报':'做三张测试海报'}';document.querySelector('.sa-compose').requestSubmit()`);
    if(questionMode){
        await waitFor(`!document.querySelector('.sa-questions').hidden && document.querySelectorAll('[data-sa-question-answer]').length===2 && !document.querySelector('.sa-compose button[type=submit]').disabled`,'explicit two-question card');
        assert.equal(await js(`document.querySelector('[data-sa-questions-submit]').disabled`),true);
        assert.equal(await js(`Boolean(document.querySelector('[data-sa-action=approve]'))`),false);
        await js(`[...document.querySelectorAll('.sa-question-choices button')].find(b=>b.textContent==='取消尺寸图，替换为使用场景图').click()`);
        await js(`document.querySelector('.sa-refresh').click()`);
        await waitFor(`!document.querySelector('.sa-refresh').disabled`,'refresh pending questions');
        assert.equal(await js(`document.querySelector('[data-sa-question-answer=dimensions]').value`),'取消尺寸图，替换为使用场景图');
        const fixed=await js(`(()=>{const q=document.querySelector('.sa-questions').getBoundingClientRect(),f=document.querySelector('.sa-compose').getBoundingClientRect();document.querySelector('.sa-conversation').scrollTop=99999;return {top:q.top,bottom:q.bottom,inputTop:f.top};})()`);
        assert.ok(fixed.bottom<=fixed.inputTop);
        assert.equal(await js(`document.querySelector('.sa-questions').getBoundingClientRect().top`),fixed.top);
        await delay(150);fs.writeFileSync(path.join(root,'docs/screenshots/smart-agent-questions.png'),(await win.webContents.capturePage()).toPNG());
        win.setSize(390,740);await delay(150);
        assert.deepEqual(await js(`(()=>{const c=document.querySelector('.sa-questions').getBoundingClientRect(),b=document.querySelector('[data-sa-questions-submit]').getBoundingClientRect(),p=document.querySelector('aside');return {inside:c.left>=0 && c.right<=innerWidth && b.bottom<=innerHeight,fits:p.scrollWidth<=p.clientWidth};})()`),{inside:true,fits:true});
        win.setSize(1450,950);await delay(100);
        assert.equal((await(await fetch(base+'/__agent_smoke/status')).json()).calls.generation,0);
        await js(`document.querySelector('[data-sa-questions-submit]').click()`);
        await waitFor(`!document.querySelector('.sa-questions').hidden && document.querySelectorAll('[data-sa-question-answer]').length===1 && document.querySelector('[data-sa-question-answer]').dataset.saQuestionAnswer==='usage' && !document.querySelector('.sa-compose button[type=submit]').disabled`,'partial answer preserves remaining specific question');
        await js(`(()=>{const a=document.querySelector('[data-sa-question-answer=usage]');a.value='商用烘焙工作台';a.dispatchEvent(new Event('input'));a.focus();})()`);
        win.webContents.sendInputEvent({type:'keyDown',keyCode:'Enter',modifiers:['control']});
        win.webContents.sendInputEvent({type:'keyUp',keyCode:'Enter',modifiers:['control']});
    }
    await waitFor(`document.querySelector('[data-sa-action=approve]') && !document.querySelector('[data-sa-action=approve]').disabled`,'actual approved plan');
    if(personaManagement){
        await js(`document.querySelector('[data-sa-menu=persona]').click()`);
        await waitFor(`!document.querySelector('[data-sa-persona-edit]').disabled`,'persona menu ready');
        await js(`document.querySelector('[data-sa-persona-edit]').click()`);
        await waitFor(`!document.querySelector('.sa-persona-editor').hidden && !document.querySelector('[data-sa-persona-save]').disabled`,'builtin persona editor opened');
        await js(`document.querySelector('[aria-label="人设提示词"]').value+=' 本套图片不得重复同一主题。';document.querySelector('[data-sa-persona-save]').click()`);
        await waitFor(`document.querySelector('.sa-persona-editor').hidden && !document.querySelector('[data-sa-persona-edit]').disabled`,'builtin override saved');
        assert.equal(await js(`document.querySelector('[data-sa-action=approve]').disabled`),true);
        await js(`[...document.querySelectorAll('button')].find(b=>b.textContent==='应用人设与并发设置').click()`);
        await waitFor(`document.querySelector('[data-sa-action=approve]') && !document.querySelector('[data-sa-action=approve]').disabled && document.querySelector('.sa-notice').textContent.includes('参数已更新')`,'same persona replan requires approval');
        const savedGeneral=(await(await fetch(base+'/api/canvas-agent/personas')).json()).personas.find(p=>p.id==='general');assert.equal(savedGeneral.revision,1);
    }
    await js(`(()=>{const s=document.querySelector('[aria-label="创作人设"]');s.value='ecommerce';s.dispatchEvent(new Event('change'));})()`);
    if(placeholders) await js(`(()=>{const s=document.querySelector('[aria-label="图片并发数量"]');s.value='4';s.dispatchEvent(new Event('change'));})()`);
    assert.equal(await js(`document.querySelector('[data-sa-action=approve]').disabled`),true);
    await js(`[...document.querySelectorAll('button')].find(b=>b.textContent==='应用人设与并发设置').click()`);
    await waitFor(`document.querySelector('.sa-run').textContent.includes('本次人设：电商图片大师') && !document.querySelector('[data-sa-action=approve]').disabled`,'persona replan requires approval');
    if(!mixed){
        await js(`document.querySelector('.sa-generation [aria-label="画面比例"]').value='9:16';document.querySelector('.sa-generation [aria-label="分辨率"]').value='2k';[...document.querySelectorAll('.sa-generation button')].find(b=>b.textContent==='应用到待确认计划').click()`);
        await waitFor(`document.querySelector('.sa-run').textContent.includes('画面比例：9:16') && document.querySelector('.sa-run').textContent.includes('分辨率：2k') && !document.querySelector('[data-sa-action=approve]').disabled`,'revised 9:16 2K plan');
    }
    assert.equal((await(await fetch(base+'/__agent_smoke/status')).json()).calls.generation,0);
    fs.writeFileSync(path.join(__dirname,'.runtime-agent-parameters.png'),(await win.webContents.capturePage()).toPNG());
    fs.writeFileSync(path.join(root,'docs/screenshots/smart-agent-composer.png'),(await win.webContents.capturePage()).toPNG());
    await js(`document.querySelector('[data-sa-menu=params]').click()`);
    await delay(150);
    fs.writeFileSync(path.join(__dirname,'.runtime-agent-composer-menu.png'),(await win.webContents.capturePage()).toPNG());
    await js(`document.querySelector('[data-sa-menu=params]').click()`);
    await js(`document.querySelector('[data-sa-action=approve]').click()`);
    if(placeholders){
        await waitFor(`nodes.filter(n=>n.agentRunId).length===8 && document.querySelectorAll('[data-agent-state=waiting]').length===4 && document.querySelectorAll('[data-agent-state=pending]').length===4`,'eight reserved nodes with four active and four queued');
        const reserved=await js(`nodes.filter(n=>n.agentRunId).map(n=>n.id).sort()`);
        await waitFor(`[...document.querySelectorAll('[data-run-timer]')].some(timer=>timer.textContent!=='0s')`,'active node elapsed timer advances');
        const first=reserved[0];
        await js(`window.smartAgentCanvas.locate([${JSON.stringify(first)}]);void 0`);
        await delay(150);
        fs.writeFileSync(path.join(root,'.test-data/agent-placeholders-generating.png'),(await win.webContents.capturePage()).toPNG());
        await win.loadURL(base+'/static/smart-canvas.html?id=smoke');
        await waitFor(`nodes.filter(n=>n.agentRunId).length===8 && document.querySelectorAll('[data-agent-state=waiting]').length===4 && document.querySelectorAll('[data-agent-state=pending]').length===4`,'reserved generation state survives page reload');
        assert.deepEqual(await js(`nodes.filter(n=>n.agentRunId).map(n=>n.id).sort()`),reserved);
        await js(`document.querySelector('[data-agent-task]').click()`);
        await waitFor(`!document.querySelector('#smartAgentPanel').hidden && document.querySelector('.sa-run h3')?.textContent==='执行中'`,'placeholder task button restores correct run');
        assert.equal((await(await fetch(base+'/__agent_smoke/status')).json()).calls.generation,4);
        await fetch(base+'/__agent_smoke/release',{method:'POST'});
    }
    // Unsaved manual position change while the server generates another node.
    await delay(150);
    await js(`nodes.find(n=>n.id==='manual').x=777;render();void 0`);
    if(process.argv.includes('--progress')){
        await waitFor(`Number(document.querySelector('.sa-progress [role=progressbar]').getAttribute('aria-valuenow'))>0 && Number(document.querySelector('.sa-progress [role=progressbar]').getAttribute('aria-valuenow'))<100`,'visible live progress before task completion');
        const fixed=await js(`(()=>{const p=document.querySelector('.sa-progress').getBoundingClientRect(),f=document.querySelector('.sa-compose').getBoundingClientRect(),t=document.querySelector('.sa-progress-count').textContent;document.querySelector('.sa-conversation').scrollTop=99999;return {top:p.top,bottom:p.bottom,inputTop:f.top,text:t};})()`);
        assert.ok(fixed.bottom<=fixed.inputTop);assert.ok(fixed.text.includes('/21'));
        const after=await js(`document.querySelector('.sa-progress').getBoundingClientRect().top`);assert.equal(after,fixed.top);
        await delay(100);fs.writeFileSync(path.join(root,'docs/screenshots/smart-agent-progress.png'),(await win.webContents.capturePage()).toPNG());
        await js(`document.querySelector('[data-sa-progress-details]').click()`);
        assert.equal(await js(`document.activeElement.classList.contains('sa-run')`),true);
    }
    await waitFor(`window.smartAgentCanvas.getContext().nodes.filter(n=>n.id!=='manual').length===${expected} && document.querySelector('.sa-run h3')?.textContent==='已完成' && document.querySelector('[data-sa-action=undo]') && !document.querySelector('[data-sa-action=undo]').disabled`,'parallel results delivered through actual bridge');
    let data=await js(`({manualX:nodes.find(n=>n.id==='manual').x,ids:window.smartAgentCanvas.getContext().nodes.map(n=>n.id),status:document.querySelector('.sa-run h3').textContent})`);
    assert.equal(data.manualX,777);assert.equal(data.ids.length,expected+1);assert.equal(data.status,'已完成');
    assert.equal(await js(`document.querySelector('.sa-progress [role=progressbar]').getAttribute('aria-valuenow')`),'100');
    if(placeholders){
        const output=await js(`nodes.find(n=>n.agentRunId).images[0]`);
        assert.equal(output.name,'测试海报.png');
        assert.match(output.original_name,/^integration-result-\d+\.png$/);
        assert.equal(output.agent_name,output.name);
        assert.ok(await js(`document.querySelector('.sa-output').textContent.includes('测试海报.png')`));
        assert.ok(await js(`[...document.querySelectorAll('[data-image-name]')].some(label=>label.textContent==='测试海报.png')`));
        assert.equal(await js(`(()=>{const card=document.querySelector('.sa-output'),label=card.querySelector('span'),text=label.textContent;label.textContent='Agent_very_long_filename_'.repeat(12)+'.png';const fits=card.querySelector('button').getBoundingClientRect().right<=card.getBoundingClientRect().right;label.textContent=text;return fits;})()`),true);
        await js(`window.__downloadCapture=[];window.__originalAnchorClick=HTMLAnchorElement.prototype.click;HTMLAnchorElement.prototype.click=function(){window.__downloadCapture.push({href:this.href,name:this.download});};void 0`);
        for(const mode of ['agent','original']){
            await js(`window.__downloadTest=downloadSmartImageWithChoice(nodes.find(n=>n.agentRunId).images[0]);void 0`);
            await waitFor(`Boolean(document.querySelector('[data-smart-download-mode=agent]'))`,'Agent filename download chooser');
            assert.deepEqual(await js(`[...document.querySelectorAll('[data-smart-download-mode]')].map(button=>button.dataset.smartDownloadMode)`),['agent','original','rename','cancel']);
            if(mode==='agent'){
                await js(`document.documentElement.classList.add('theme-dark','studio-theme-dark');void 0`);
                await delay(200);
                fs.writeFileSync(path.join(root,'.test-data/agent-name-download-choice.png'),(await win.webContents.capturePage()).toPNG());
                await js(`document.documentElement.classList.remove('theme-dark','studio-theme-dark');void 0`);
            }
            await js(`document.querySelector('[data-smart-download-mode=${mode}]').click();window.__downloadTest.then(()=>null)`);
            const captured=await js(`window.__downloadCapture.at(-1)`);
            assert.equal(captured.name,mode==='agent'?output.name:output.original_name);
            const downloaded=await fetch(captured.href);
            assert.equal(downloaded.status,200);
            assert.ok(downloaded.headers.get('content-disposition').includes(encodeURIComponent(captured.name)));
            assert.deepEqual([...new Uint8Array(await downloaded.arrayBuffer()).slice(0,4)],[137,80,78,71]);
        }
        await js(`window.__nativeDownloadFetch=window.fetch;window.fetch=async(...args)=>{const response=await window.__nativeDownloadFetch(...args);if(String(args[0]).includes('/api/canvas-assets/download'))window.__downloadZip=[...new Uint8Array(await response.clone().arrayBuffer())];return response;};window.__downloadTest=zipDownloadImageItems('Agent 套图',nodes.filter(n=>n.agentRunId).slice(0,2).map(n=>n.images[0]));void 0`);
        await waitFor(`Boolean(document.querySelector('[data-smart-download-mode=agent]'))`,'batch filename download chooser');
        await js(`document.querySelector('[data-smart-download-mode=agent]').click();window.__downloadTest.then(()=>null)`);
        const zip=Buffer.from(await js(`window.__downloadZip`));
        const names=spawnSync(path.join(root,'backend/python/python.exe'),['-B','-X','utf8','-c','import io,json,sys,zipfile\nprint(json.dumps(zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())).namelist()))'],{input:zip,encoding:'utf8',windowsHide:true});
        assert.equal(names.status,0,names.stderr);
        assert.deepEqual(JSON.parse(names.stdout),['测试海报.png','测试海报_2.png']);
        await js(`window.__downloadCapture=[];window.__downloadTest=downloadRawImagesSequentially(nodes.filter(n=>n.agentRunId).slice(0,2).map(n=>n.images[0]));void 0`);
        await waitFor(`Boolean(document.querySelector('[data-smart-download-mode=original]'))`,'sequential original filename chooser');
        await js(`document.querySelector('[data-smart-download-mode=original]').click();window.__downloadTest.then(()=>null)`);
        assert.deepEqual(await js(`window.__downloadCapture.map(item=>item.name)`),await js(`nodes.filter(n=>n.agentRunId).slice(0,2).map(n=>n.images[0].original_name)`));
        await js(`HTMLAnchorElement.prototype.click=window.__originalAnchorClick;window.fetch=window.__nativeDownloadFetch;void 0`);
    }
    await js(`document.querySelector('.sa-output button').click()`);
    assert.equal(await js(`window.smartAgentCanvas.getContext().selected_node_ids.some(id=>id!=='manual')`),true);
    await waitFor(`[...document.images].some(image=>(image.currentSrc || image.src).includes('integration-result-') && image.complete && image.naturalWidth>0)`,'actual generated media image loaded');
    await delay(250);
    const screenshot=path.join(__dirname,'.runtime-agent-smoke.png');fs.writeFileSync(screenshot,(await win.webContents.capturePage()).toPNG());
    const delivered=(await(await fetch(base+'/__agent_smoke/status')).json()).canvas.nodes.find(n=>n.id!=='manual');
    const local=await js(`JSON.parse(JSON.stringify(nodes.find(n=>n.id!=='manual')))`);
    console.log('NODE_DELIVERY_DIFF '+JSON.stringify(Object.fromEntries([...new Set([...Object.keys(delivered),...Object.keys(local)])].filter(k=>JSON.stringify(delivered[k])!==JSON.stringify(local[k])).map(k=>[k,{server:delivered[k],local:local[k]}]))));
    await js(`window.smartAgentCanvas.flush().then(()=>undefined)`);
    const beforeUndo=(await(await fetch(base+'/__agent_smoke/status')).json()).canvas;
    const current=beforeUndo.nodes.find(n=>n.id!=='manual');
    const receipt=Object.values(beforeUndo.agent_operation_receipts).find(r=>r.after?.[current.id]);
    const expectedReceipt=receipt.after[current.id];
    console.log('NODE_UNDO_RECEIPT_DIFF '+JSON.stringify(Object.fromEntries([...new Set([...Object.keys(expectedReceipt),...Object.keys(current)])].filter(k=>JSON.stringify(expectedReceipt[k])!==JSON.stringify(current[k])).map(k=>[k,{receipt:expectedReceipt[k],current:current[k]}]))));
    await js(`document.querySelector('[data-sa-action=undo]').click()`);
    await waitFor(`window.smartAgentCanvas.getContext().nodes.filter(n=>n.id!=='manual').length===0`,'actual undo refresh');
    const status=await(await fetch(base+'/__agent_smoke/status')).json();
    assert.equal(status.calls.llm,2+(personaManagement?1:0)+(questionMode?2:0));assert.deepEqual(status.calls.personas,[...Array(1+(personaManagement?1:0)+(questionMode?2:0)).fill('general'),'ecommerce']);assert.equal(status.calls.peak,placeholders?4:2);assert.equal(status.calls.generation,expected);if(mixed)assert.equal(status.calls.video,1);assert.equal(status.canvas.nodes.length,1);assert.equal(status.canvas.nodes[0].x,777);assert.equal(errors.length,0,errors.join('\n'));
    win.webContents.sendInputEvent({type:'keyDown',keyCode:'A',modifiers:['control','shift']});
    win.webContents.sendInputEvent({type:'keyUp',keyCode:'A',modifiers:['control','shift']});
    await waitFor(`document.querySelector('#smartAgentPanel').hidden`,'Agent shortcut closes panel');
    await waitFor(`document.querySelector('.smart-agent-toggle').getBoundingClientRect().bottom<=document.querySelector('#minimap').getBoundingClientRect().top-7`,'independent minimap anchor');
    const anchor=await js(`(()=>{const b=document.querySelector('.smart-agent-toggle'),r=b.getBoundingClientRect(),m=document.querySelector('#minimap').getBoundingClientRect();return {rightAligned:Math.abs(r.right-m.right)<1,shortcut:b.getAttribute('aria-keyshortcuts')};})()`);
    assert.ok(anchor.rightAligned);assert.ok(anchor.shortcut.includes('Control+Shift+A'));
    await js(`document.documentElement.classList.add('theme-dark','studio-theme-dark')`);await delay(200);
    fs.writeFileSync(path.join(__dirname,'.runtime-agent-entry.png'),(await win.webContents.capturePage()).toPNG());
    win.webContents.sendInputEvent({type:'keyDown',keyCode:'A',modifiers:['control','shift']});win.webContents.sendInputEvent({type:'keyUp',keyCode:'A',modifiers:['control','shift']});
    await waitFor(`!document.querySelector('#smartAgentPanel').hidden`,'composer dark preview');
    await delay(200);fs.writeFileSync(path.join(__dirname,'.runtime-agent-composer-dark.png'),(await win.webContents.capturePage()).toPNG());
    console.log('INTEGRATION_SMOKE_OK '+JSON.stringify({origin:base,packaged,actualPage:true,actualBridge:true,actualAgentHostOps:true,upstream:'fake only',generated:expected,video:status.calls.video || 0,peak:status.calls.peak,personas:status.calls.personas,undoRemainingGenerated:0,manualDraftX:777,screenshot}));
}
async function cleanup(){if(win&&!win.isDestroyed())win.destroy();if(base&&!exited){try{await fetch(base+'/__agent_smoke/shutdown',{method:'POST'});}catch{}}for(let i=0;i<70&&!exited;i++)await delay(100);if(child&&!exited){child.kill();for(let i=0;i<20&&!exited;i++)await delay(100);}}
run().then(async()=>{await cleanup();app.quit();}).catch(async error=>{console.error(error.stack||error);await cleanup();app.exit(1);});
