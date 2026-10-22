const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {parse,validate}=require('../backend/static/js/desktop-release-notes.js');
if(!process.versions.electron){
test('published notes group all historical changes under descending versions',()=>{
    const notes=require('../backend/static/desktop-update-notes.json');
    const groups=validate(notes.notes,notes.version);
    assert.ok(groups.length>=14);assert.equal(groups[0].version,require('../package.json').version);
    assert.ok(groups.reduce((n,g)=>n+g.items.length,0)>=25);
    assert.ok(groups.some(g=>g.version==='0.3.7' && g.items.some(s=>s.includes('Litterbox'))));
    assert.ok(groups.some(g=>g.version==='0.2.1' && g.items.some(s=>s.includes('右键菜单'))));
    assert.ok(groups.some(g=>g.version==='1.0.0' && g.items.some(s=>s.includes('待补充条件'))));
});
test('release validation compares versions numerically and rejects dates, duplicates and orphan text',()=>{
    assert.equal(validate('1.10.0\n- A\n\n1.9.0\n- B','1.10.0').length,2);
    for(const text of ['1.0.0\n- 2026-10-20-23：A','1.0.0\n- A\n1.0.0\n- B','1.9.0\n- A\n1.10.0\n- B','1.0.0\n','历史更新\n1.0.0\n- A'])assert.throws(()=>validate(text,text.startsWith('1.9')?'1.9.0':'1.0.0'));
    assert.throws(()=>validate('1.0.0\n- A','1.0.1'));
    assert.equal(parse('legacy <img src=x onerror=alert(1)>'),null);
    assert.equal(parse('v1.0.0\r\n- <img src=x>')[0].items[0],'<img src=x>');
});
}
if(process.versions.electron && process.argv.includes('--browser-smoke'))browserSmoke().catch(e=>{console.error(e);require('electron').app.exit(1);});
async function browserSmoke(){
    const {app,BrowserWindow}=require('electron');
    const root=path.resolve(__dirname,'..');
    app.setPath('userData',fs.mkdtempSync(path.join(root,'.test-data/version-notes-ui-')));await app.whenReady();
    const win=new BrowserWindow({show:false,width:1426,height:893,webPreferences:{offscreen:true,nodeIntegration:false,contextIsolation:true}});
    const html=fs.readFileSync(path.join(root,'backend/static/index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<iframe\b[^>]*>[\s\S]*?<\/iframe>/gi,'').replace(/<link\b[^>]*>/gi,'');
    win.webContents.session.webRequest.onBeforeRequest((d,done)=>done({cancel:!d.url.startsWith('data:')}));
    await win.loadURL('data:text/html;charset=utf-8,'+encodeURIComponent(html));
    const notes=JSON.parse(fs.readFileSync(path.join(root,'backend/static/desktop-update-notes.json'),'utf8'));
    const expectedGroups=validate(notes.notes,notes.version).length;
    await win.webContents.executeJavaScript(`window.desktopBridge={updates:{getState:async()=>({currentVersion:'1.0.0',phase:'not-available'}),check:async()=>({currentVersion:'1.0.0',phase:'not-available'}),onState:fn=>{window.testUpdateState=fn;return ()=>{};}}};window.fetch=async()=>({ok:true,json:async()=>(${JSON.stringify(notes)})});void 0;`);
    for(const name of ['desktop-release-notes.js','desktop-updates.js'])await win.webContents.executeJavaScript(fs.readFileSync(path.join(root,'backend/static/js',name),'utf8'));
    await win.webContents.executeJavaScript('window.DesktopUpdates.loadNotes().then(()=>window.DesktopUpdates.open())');
    await new Promise(r=>setTimeout(r,200));
    assert.equal(await win.webContents.executeJavaScript(`document.querySelectorAll('#project-update-notes-list>.desktop-note-group').length || document.querySelectorAll('#project-update-notes-list>li').length`),expectedGroups);
    assert.equal(await win.webContents.executeJavaScript(`document.querySelector('#project-update-notes-list .desktop-note-version').textContent`),'v'+notes.version);
    await win.webContents.executeJavaScript(`document.documentElement.classList.add('theme-dark');void 0`);await new Promise(r=>setTimeout(r,150));
    fs.writeFileSync(path.join(root,'docs/screenshots/version-release-notes.png'),(await win.webContents.capturePage()).toPNG());
    await win.webContents.executeJavaScript('window.DesktopUpdates.close("project-update-modal");window.DesktopUpdates.openNotes()');
    assert.equal(await win.webContents.executeJavaScript(`document.querySelectorAll('#sidebar-notes-list>.desktop-note-group').length || document.querySelectorAll('#sidebar-notes-list>li').length`),expectedGroups);
    const remote={currentVersion:'1.0.0',phase:'available',version:'1.0.1',notes:'1.0.1\n- <img src=x onerror="window.unsafeNote=true">\n\n1.0.0\n- Old'};
    await win.webContents.executeJavaScript(`window.testUpdateState(${JSON.stringify(remote)});void 0`);
    assert.equal(await win.webContents.executeJavaScript(`document.querySelector('#project-update-notes-list .desktop-note-version').textContent`),'v1.0.1');
    assert.equal(await win.webContents.executeJavaScript(`document.querySelectorAll('#project-update-notes-list img').length===0 && !window.unsafeNote`),true);
    win.setSize(390,740);await win.webContents.executeJavaScript('window.DesktopUpdates.close("sidebar-notes-modal");window.DesktopUpdates.open()');await new Promise(r=>setTimeout(r,100));
    assert.equal(await win.webContents.executeJavaScript(`(()=>{const r=document.querySelector('#project-update-modal .studio-modal-panel').getBoundingClientRect();return r.left>=0 && r.right<=innerWidth;})()`),true);
    console.log('VERSION_NOTES_UI_OK: local and remote version groups; sidebar; safe text; dark mode; 390px.');
    win.destroy();app.quit();
}
