const test = require('node:test');
const assert = require('node:assert/strict');
if (process.versions.electron && process.argv.includes('--browser-smoke')) {
    browserSmoke().catch(error => { console.error(error); require('electron').app.exit(1); });
} else {
const {isToggleShortcut,actionsOf,nodeIdsOf,statusOf,modelsOf,canSend,shouldPoll,formatDetails,progressOf,questionsOf,clarificationText} = require('../backend/static/js/smart-agent.js');
test('clarifications are visible only when needed and replies bind to specific questions',()=>{
    const run={status:'needs_input',plan:{questions:[{id:'size',question:'尺寸说明图如何处理？',options:['提供尺寸','替换为场景图']},{id:'usage',question:'商品用途是什么？',options:[]}]}};
    const questions=questionsOf(run),text=clarificationText(questions,{size:'替换为场景图',usage:'商用烘焙'});
    assert.equal(questions.length,2);assert.ok(text.includes('问题：尺寸说明图如何处理？\n回答：替换为场景图'));assert.ok(text.includes('回答：商用烘焙'));
    assert.equal(clarificationText(questions,{}),'');
    const partial=clarificationText(questions,{size:'替换为场景图'});assert.ok(!partial.includes('问题：商品用途'));
    assert.deepEqual(questionsOf({...run,status:'planning'}),[]);
    assert.equal(questionsOf({status:'needs_input',plan:{summary:'没有明确问题'}})[0].id,'clarify');
    assert.deepEqual(questionsOf(null),[]);
});
test('batch progress counts only successful finished steps and preserves unfinished work',()=>{
    const steps=Array.from({length:21},(_,i)=>({id:String(i),title:'素材'+i,status:i<8?'completed':i<10?'waiting':'pending'}));
    const value=progressOf({status:'running',steps});
    assert.equal(value.done,8);assert.equal(value.total,21);assert.equal(value.percent,38);assert.equal(value.processing,2);assert.equal(value.pending,11);assert.equal(value.current.length,2);
    const partial=progressOf({status:'partial',steps:[{status:'completed'},{status:'failed'},{status:'blocked'},{status:'unknown'},{status:'delivery_failed'}]});
    assert.equal(partial.percent,20);assert.equal(partial.failed,2);assert.equal(partial.blocked,1);assert.equal(partial.unknown,1);
    assert.equal(progressOf({status:'planning'}).indeterminate,true);
    assert.equal(progressOf({status:'completed',steps:[]}).visible,false);
    assert.equal(progressOf(null).visible,false);
    assert.equal(progressOf({status:'completed',steps:steps.map(s=>({...s,status:'completed'}))}).percent,100);
});
test('Agent toggle shortcut is dedicated and ignores IME and repeats',()=>{
    const combo={key:'A',ctrlKey:true,shiftKey:true};
    assert.equal(isToggleShortcut(combo),true);
    for(const extra of [{ctrlKey:false},{shiftKey:false},{altKey:true},{isComposing:true},{repeat:true},{key:'Enter'}]) assert.equal(isToggleShortcut({...combo,...extra}),false);
    assert.equal(isToggleShortcut({key:'a',metaKey:true,shiftKey:true}),true);
});

test('details use readable Chinese while preserving generation choices', () => {
    const settings={ratio:'portrait43',resolution:'2k',quality:'high',background:'transparent',output_format:'png',output_compression:0,seed:0,videoDuration:5,rhConfigKey:'hidden'};
    const image=formatDetails(settings,{kind:'image',provider:'测试平台',model:'gpt-image-2',count:3,references:[{node_id:'private-id'}],dependencies:['step-private']});
    for(const line of ['任务类型：图片生成','数量：3','参考素材：1 项','前置步骤：1 项','画面比例：3:4','分辨率：2k','背景：透明','质量：高','输出压缩质量：0','随机种子：0']) assert.ok(image.includes(line),line);
    assert.ok(!image.includes('private-id') && !image.includes('hidden') && !image.includes('时长'));
    const video=formatDetails({apiKind:'video',videoProvider:'p',videoModel:'h3',videoAspect:'9:16',videoDuration:4,videoGenerateAudio:true,videoEnableUpsample:false,quality:'high',outputFormat:'png'});
    for(const line of ['任务类型：视频生成','模型：h3','时长：4 秒','生成音频：开启','升采样：关闭']) assert.ok(video.includes(line),line);
    assert.ok(!video.includes('输出格式') && !video.includes('质量：高'));
    assert.ok(formatDetails({size_mode:'source',ratio:'3:4'}).includes('适配输入素材'));
    assert.equal(settings.ratio,'portrait43');
});
test('recovery keeps polling live steps without polling terminal failures', () => {
    for (const status of ['waiting','generated','delivering']) {
        const run = {status:'needs_attention',steps:[{status}]};
        assert.equal(shouldPoll(run),true);
        assert.equal(canSend({run}),false);
    }
    for (const status of ['failed','unknown','delivery_failed']) assert.equal(shouldPoll({status:'needs_attention',steps:[{status}]}),false);
    assert.equal(shouldPoll({status:'completed',steps:[{status:'waiting'}]}),false);
});
test('only backend-advertised known actions are rendered', () => {
    assert.deepEqual(actionsOf({actions:['approve',{action:'stop'},'execute_code',{id:'repair_delivery'}]}),['approve','stop','repair_delivery']);
    assert.deepEqual(actionsOf(null),[]);
});
test('delivery references remain ordered and deduplicated', () => {
    assert.deepEqual(nodeIdsOf({outputs:[{node_ids:['n2','n1']}],steps:[{node_ids:['n1','n3']}]}),['n2','n1','n3']);
});
test('messages cannot conflict with running tasks or unresolved approval', () => {
    assert.equal(canSend({run:{status:'running'}}),false);
    assert.equal(canSend({run:{status:'awaiting_approval',actions:['approve']}}),true);
    assert.equal(canSend({run:{status:'failed',actions:['retry']}}),false);
    assert.equal(canSend({busy:true}),false);
    assert.equal(canSend({pending:{}}),false);
    assert.equal(canSend({run:{status:'completed'}}),true);
});
test('models use configured identifiers and empty states stay readable', () => {
    assert.deepEqual(modelsOf({chat_models:['chat',{id:'m',name:'模型'}]}),[{id:'chat',name:'chat'},{id:'m',name:'模型'}]);
    assert.deepEqual(modelsOf(null),[]);
    assert.equal(statusOf('awaiting_approval'),'待确认');
    assert.equal(statusOf('unknown'),'结果未知');
});
}

async function browserSmoke() {
    const {app,BrowserWindow} = require('electron');
    const fs = require('node:fs');
    const path = require('node:path');
    fs.mkdirSync(path.join(__dirname,'../.test-data'),{recursive:true});
    app.setPath('userData',fs.mkdtempSync(path.join(__dirname,'../.test-data/agent-browser-ui-')));
    await app.whenReady();
    const win = new BrowserWindow({show:false,width:1100,height:800,webPreferences:{offscreen:true,contextIsolation:true,nodeIntegration:false}});
    await win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent('<!doctype html><html lang="zh-CN"><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="minimap" style="position:fixed;right:22px;bottom:22px;width:190px;height:128px"></div><button id="smartArrangeBtn" style="position:fixed;right:22px;bottom:158px;height:32px">整理</button></body></html>'));
    await win.webContents.insertCSS(fs.readFileSync(path.join(__dirname,'../backend/static/css/smart-agent.css'),'utf8'));
    await win.webContents.executeJavaScript(`
        window.smoke={posts:0,actions:0,located:[],refresh:0};
        const context={canvas_id:'fixture',canvas_revision:1,selected_node_ids:['image1'],nodes:[{id:'image1',title:'<img src=x onerror=alert(1)>',images:['mock']}],generation:{ratio:'3:4'},llm:{provider_id:'p',model:'chat'}};
        window.smokeBridge={getContext:()=>structuredClone(context),flush:async()=>structuredClone(context),locate:ids=>smoke.located.push(ids),refresh:async()=>{smoke.refresh++}};
        let messages=[],run=null;
        window.fetch=async(url,options={})=>{
            let data={};
            if(url.endsWith('/providers')) data={providers:[{id:'p',name:'测试平台',chat_models:['chat'],image_models:['image'],video_models:['video']}]};
            else if(url.includes('/capabilities?')) data={capabilities:{available:true,ratios:['3:4','1:1','9:16'],resolutions:['1k','2k','4k'],durations:url.includes('kind=video')?[4,15]:[],settings:{background:['auto','opaque','transparent']}}};
            else if(url.includes('/sessions?')) data={sessions:[{id:'s',title:'独立测试'}]};
            else if(url.endsWith('/messages') && options.method==='POST') {smoke.posts++;smoke.payload=JSON.parse(options.body);if(smoke.failNext){smoke.failNext=false;smoke.failedId=smoke.payload.client_request_id;throw new Error('模拟断线');}run={id:'r',status:'awaiting_approval',revision:1,plan:{revision:1,summary:'<script>unsafe</script>',steps:[{id:'step1',title:'海报',settings:{ratio:'3:4'}}]},steps:[],actions:['approve','stop']};messages=[{role:'user',content:smoke.payload.text,run_id:'r'}];data={run_id:'r',run};await new Promise(r=>setTimeout(r,40));}
            else if(url.endsWith('/messages')) data={messages};
            else if(url.endsWith('/actions')) {smoke.actions++;smoke.action=JSON.parse(options.body);run={...run,revision:2,status:'completed',actions:['undo'],outputs:[{id:'o',kind:'image',node_ids:['result1']}],steps:[]};data={run};}
            else if(url.endsWith('/runs/r')) {if(smoke.recovery){smoke.recoveryReads=(smoke.recoveryReads||0)+1;run={...run,revision:2+smoke.recoveryReads,status:smoke.recoveryReads===1?'needs_attention':'completed',steps:smoke.recoveryReads===1?[{id:'step1',status:'waiting'}]:[],actions:[],outputs:smoke.recoveryReads===1?[]:[{id:'o2',kind:'image',node_ids:['recovered1']}]};}data={run};}
            return {ok:true,json:async()=>structuredClone(data)};
        }; void 0;
    `);
    await win.webContents.executeJavaScript(fs.readFileSync(path.join(__dirname,'../backend/static/js/smart-agent.js'),'utf8'));
    const waitFor = async expression => {
        for (let i=0;i<180;i++) { if(await win.webContents.executeJavaScript(expression)) return; await new Promise(r=>setTimeout(r,20)); }
        throw new Error('Browser condition not met: '+expression);
    };
    await win.webContents.executeJavaScript(`document.querySelector('.smart-agent-toggle').click()`);
    await win.webContents.executeJavaScript(`window.smartAgentCanvas=window.smokeBridge;void 0`);
    await waitFor(`document.querySelector('.sa-controls select').value==='p' && document.querySelector('[aria-label="当前画布会话"]').value==='s' && !document.querySelector('.sa-compose button[type=submit]').disabled`);
    await waitFor(`Boolean(document.querySelector('.sa-generation [aria-label="画面比例"]'))`);
    assert.equal(await win.webContents.executeJavaScript(`document.querySelectorAll('.sa-popover:not([hidden])').length`),0);
    await win.webContents.executeJavaScript(`document.querySelector('[data-sa-menu=models]').click()`);
    assert.equal(await win.webContents.executeJavaScript(`document.getElementById('sa-popup-models').hidden`),false);
    await win.webContents.executeJavaScript(`document.querySelector('[data-sa-menu=params]').click()`);
    let menuLayout=await win.webContents.executeJavaScript(`(()=>{const p=document.getElementById('sa-popup-params').getBoundingClientRect(),f=document.querySelector('.sa-compose').getBoundingClientRect(),a=document.querySelector('aside').getBoundingClientRect();return {up:p.bottom<f.top,inside:p.top>=a.top && p.right<=a.right,previousClosed:document.getElementById('sa-popup-models').hidden};})()`);
    assert.deepEqual(menuLayout,{up:true,inside:true,previousClosed:true});
    win.webContents.sendInputEvent({type:'keyDown',keyCode:'Escape'});
    await waitFor(`document.getElementById('sa-popup-params').hidden && !document.querySelector('aside').hidden && document.activeElement.dataset.saMenu==='params'`);
    await win.webContents.executeJavaScript(`(()=>{const s=document.querySelector('.sa-generation-models [aria-label="生成类型"]');s.value='video';s.dispatchEvent(new Event('change'));})()`);
    await waitFor(`Boolean(document.querySelector('.sa-generation [aria-label="时长（秒）"] option[value="15"]'))`);
    await win.webContents.executeJavaScript(`(()=>{const s=document.querySelector('.sa-generation-models [aria-label="生成类型"]');s.value='image';s.dispatchEvent(new Event('change'));})()`);
    await waitFor(`Boolean(document.querySelector('.sa-generation [aria-label="分辨率"]')) && !document.querySelector('.sa-generation [aria-label="时长（秒）"]')`);
    await win.webContents.executeJavaScript(`document.querySelector('.sa-generation [aria-label="画面比例"]').value='9:16';document.querySelector('.sa-generation [aria-label="分辨率"]').value='2k';void 0;`);
    await new Promise(r=>setTimeout(r,1700));
    assert.equal(await win.webContents.executeJavaScript(`document.querySelector('.sa-generation [aria-label="画面比例"]').value`),'9:16');
    await win.webContents.executeJavaScript(`(()=>{const s=document.querySelector('.sa-generation-models [aria-label="生成类型"]');s.value='video';s.dispatchEvent(new Event('change'));})()`);
    await waitFor(`Boolean(document.querySelector('.sa-generation [aria-label="时长（秒）"]'))`);
    await win.webContents.executeJavaScript(`document.querySelector('.sa-generation [aria-label="时长（秒）"]').value='15';(()=>{const s=document.querySelector('.sa-generation-models [aria-label="生成类型"]');s.value='image';s.dispatchEvent(new Event('change'));})()`);
    await waitFor(`Boolean(document.querySelector('.sa-generation [aria-label="画面比例"]')) && !document.querySelector('.sa-generation [aria-label="时长（秒）"]')`);
    assert.equal(await win.webContents.executeJavaScript(`document.querySelector('.sa-generation [aria-label="画面比例"]').value`),'9:16');
    await win.webContents.executeJavaScript(`document.querySelector('textarea').value='制作一张海报';document.querySelector('.sa-compose').requestSubmit();document.querySelector('.sa-compose').requestSubmit();`);
    await waitFor(`document.querySelector('[data-sa-action=approve]') && !document.querySelector('[data-sa-action=approve]').disabled`);
    let result = await win.webContents.executeJavaScript(`({posts:smoke.posts,payload:smoke.payload,safe:!document.querySelector('.sa-run script') && !document.querySelector('.sa-ref img'),sendDisabled:document.querySelector('.sa-compose button[type=submit]').disabled})`);
    assert.equal(result.posts,1);assert.equal(result.safe,true);assert.equal(result.sendDisabled,false);assert.equal(result.payload.generation.ratio,'9:16');assert.equal(result.payload.generation.resolution,'2k');assert.deepEqual(result.payload.context.selected_node_ids,['image1']);
    assert.equal(result.payload.generation_defaults.video.duration,15);assert.equal(result.payload.generation_defaults.image.ratio,'9:16');assert.deepEqual(result.payload.generation_defaults.image.locks,[]);
    await win.webContents.executeJavaScript(`document.querySelector('[data-sa-action=approve]').click();document.querySelector('[data-sa-action=approve]').click()`);
    await waitFor(`document.querySelector('[data-sa-action=undo]') && !document.querySelector('[data-sa-action=undo]').disabled`);
    result=await win.webContents.executeJavaScript(`document.querySelector('.sa-output button').click();({actions:smoke.actions,action:smoke.action,located:smoke.located,refresh:smoke.refresh})`);
    assert.equal(result.actions,1);assert.equal(result.action.plan_revision,1);assert.equal(result.action.expected_revision,1);assert.deepEqual(result.located,[['result1']]);assert.ok(result.refresh>0);
    await win.webContents.executeJavaScript(`smoke.failNext=true;document.querySelector('textarea').value='第二轮';document.querySelector('textarea').focus()`);
    win.webContents.sendInputEvent({type:'keyDown',keyCode:'Enter',modifiers:['control']});
    await waitFor(`document.querySelector('.sa-compose button[type=submit]').textContent==='重试同一条消息' && !document.querySelector('.sa-compose button[type=submit]').disabled`);
    assert.equal(await win.webContents.executeJavaScript(`document.querySelector('textarea').disabled`),true);
    await win.webContents.executeJavaScript(`document.querySelector('.sa-compose').requestSubmit();document.querySelector('.sa-compose').requestSubmit()`);
    await waitFor(`document.querySelector('[data-sa-action=approve]') && !document.querySelector('[data-sa-action=approve]').disabled`);
    result=await win.webContents.executeJavaScript(`({posts:smoke.posts,sameId:smoke.failedId===smoke.payload.client_request_id})`);
    assert.equal(result.posts,3);assert.equal(result.sameId,true);
    await win.webContents.executeJavaScript(`smoke.recovery=true;smoke.refreshBeforeRecovery=smoke.refresh;document.querySelector('.smart-agent-toggle').click();document.querySelector('.smart-agent-toggle').click()`);
    await waitFor(`smoke.recoveryReads===1 && document.querySelector('.sa-run h3').textContent==='待处理'`);
    await waitFor(`smoke.recoveryReads>=2 && document.querySelector('.sa-run h3').textContent==='已完成' && smoke.refresh>smoke.refreshBeforeRecovery`);
    await new Promise(r=>setTimeout(r,2100));
    assert.equal(await win.webContents.executeJavaScript(`smoke.recoveryReads`),2);
    result=await win.webContents.executeJavaScript(`(()=>{const e=document.querySelector('.smart-agent-toggle').getBoundingClientRect(),m=document.getElementById('minimap').getBoundingClientRect();return {button:e.toJSON(),map:m.toJSON()};})()`);
    assert.ok(result.button.bottom<=result.map.top-7,'Agent above minimap');assert.ok(Math.abs(result.button.right-result.map.right)<1,'Agent aligned with minimap');
    await win.webContents.executeJavaScript(`document.getElementById('smartArrangeBtn').classList.add('visible')`);
    await waitFor(`document.querySelector('.smart-agent-toggle').getBoundingClientRect().bottom <= document.getElementById('smartArrangeBtn').getBoundingClientRect().top-7`);
    win.setSize(390,740);
    await new Promise(r=>setTimeout(r,50));
    result=await win.webContents.executeJavaScript(`(()=>{const r=document.querySelector('aside').getBoundingClientRect();return {left:r.left,right:r.right,width:innerWidth,scroll:document.querySelector('aside').scrollWidth,client:document.querySelector('aside').clientWidth};})()`);
    assert.ok(result.left>=0 && result.right<=result.width);assert.equal(result.scroll,result.client);
    await win.webContents.executeJavaScript(`document.querySelector('[data-sa-menu=params]').click()`);
    menuLayout=await win.webContents.executeJavaScript(`(()=>{const p=document.getElementById('sa-popup-params').getBoundingClientRect(),f=document.querySelector('.sa-compose').getBoundingClientRect(),a=document.querySelector('aside').getBoundingClientRect(),t=document.querySelector('.sa-toolbar');return {up:p.bottom<f.top,inside:p.left>=a.left && p.right<=a.right && p.top>=a.top,toolbarFits:t.scrollWidth<=t.clientWidth};})()`);
    assert.deepEqual(menuLayout,{up:true,inside:true,toolbarFits:true});
    await win.webContents.executeJavaScript(`document.querySelector('[data-sa-menu=params]').click()`);
    await win.webContents.executeJavaScript(`document.querySelector('textarea').focus()`);
    win.webContents.sendInputEvent({type:'keyDown',keyCode:'Escape'});
    await waitFor(`document.querySelector('aside').hidden && document.activeElement.classList.contains('smart-agent-toggle')`);
    win.webContents.sendInputEvent({type:'keyDown',keyCode:'A',modifiers:['control','shift']});
    win.webContents.sendInputEvent({type:'keyUp',keyCode:'A',modifiers:['control','shift']});
    await waitFor(`!document.querySelector('aside').hidden`);
    win.webContents.sendInputEvent({type:'keyDown',keyCode:'A',modifiers:['control','shift']});
    win.webContents.sendInputEvent({type:'keyUp',keyCode:'A',modifiers:['control','shift']});
    await waitFor(`document.querySelector('aside').hidden`);
    console.log('BROWSER_SMOKE_OK: real Chromium DOM; late bridge; waiting recovery reaches completed and refreshes then stops polling; 390px layout; safe text; duplicate guards; network retry same request ID; plan revision; generation context; locate; Ctrl+Enter; Escape focus return; independent minimap anchor; arrange button avoidance; Ctrl+Shift+A toggle.');
    win.destroy();app.quit();
}
