'use strict';
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let currentStage = 'startup';
async function waitFor(check, label, timeout = 20000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const result = await check();
    if (result) return result;
    await sleep(100);
  }
  throw new Error(`Smoke stage timed out: ${label}`);
}
async function run({ win, session, root, saveScript }) {
  currentStage = 'canvas-list';
  const execute = script => win.webContents.executeJavaScript(script);
  const legacyUpdateRequests = [];
  session.webRequest.onBeforeRequest((details, callback) => {
    if (/update\.sansheng\.site|\/api\/(?:check-update|update(?:[/?-]|$)|full-install-download)/.test(details.url)) legacyUpdateRequests.push(new URL(details.url).pathname);
    callback({});
  });
  await waitFor(() => execute('Boolean(document.querySelector("#frame-canvas")?.contentDocument?.querySelector("#board"))').catch(() => false), 'canvas list iframe');
  const canvasFrame = () => win.webContents.mainFrame.framesInSubtree.find(frame => new URL(frame.url || 'about:blank').pathname === '/static/canvas-list.html');
  await waitFor(() => canvasFrame()?.executeJavaScript('document.readyState === "complete"').catch(() => false), 'canvas list loaded');
  currentStage = 'projects';
  const projects = await canvasFrame().executeJavaScript('(async()=>{const r=await fetch("/api/projects"); if(!r.ok) throw Error("projects"); const d=await r.json(); return {status:r.status, valid: Array.isArray(d.projects)};})()');
  assert.equal(projects.status, 200); assert.equal(projects.valid, true);
  currentStage = 'api-settings';
  const apiSettings = await require('./api-settings-smoke.cjs').run({win,root,waitFor});
  currentStage = 'github-updater';
  const githubUpdater = await require('./updater-smoke.cjs').run({win,root,waitFor});
  currentStage = 'iframe-prompt';
  const answer = await canvasFrame().executeJavaScript('window.prompt("Desktop smoke input", "initial")');
  assert.equal(answer, 'desktop-smoke-answer');
  const results = [];
  for (const kind of ['classic', 'smart']) {
    currentStage = `${kind}-canvas`;
    const created = await execute(`(async()=>{const r=await fetch('/api/canvases',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'Desktop smoke ${kind}',kind:'${kind}',icon:'sparkles'})}); if(!r.ok)throw Error('create'); return (await r.json()).canvas;})()`);
    assert.ok(created.id);
    const page = kind === 'smart' ? 'smart-canvas.html' : 'canvas.html';
    const route = `/static/${page}?id=${encodeURIComponent(created.id)}`;
    await execute(`document.querySelector('#frame-canvas').src=${JSON.stringify(route)}`);
    let frame = await waitFor(async () => {
      const frame = win.webContents.mainFrame.framesInSubtree.find(item => item.url.includes(`/static/${page}?id=`));
      if (!frame) return false;
      return await frame.executeJavaScript(`typeof saveCanvas === 'function' && typeof canvas !== 'undefined' && canvas?.id === ${JSON.stringify(created.id)}`).catch(() => false) ? frame : false;
    }, `${kind} canvas initialized`);
    const interaction = kind === 'classic' ? await require('./classic-canvas-smoke.cjs').run({frame}) : null;
    await frame.executeJavaScript(`canvas.title = 'Desktop smoke ${kind} saved';`);
    // Exercise the exact renderer traversal and save wait used by window close.
    await execute(saveScript);
    const saved = await execute(`fetch('/api/canvases/${encodeURIComponent(created.id)}').then(r=>r.json())`);
    assert.equal(saved.canvas.title, `Desktop smoke ${kind} saved`);
    if (interaction) {
      for (const node of interaction.created) assert.ok(saved.canvas.nodes.some(item=>item.id===node.id&&item.type===node.type),'created node persisted');
      assert.ok(saved.canvas.connections.some(link=>link.from===interaction.connection.from&&link.to===interaction.connection.to),'connection persisted');
      await execute(`document.querySelector('#frame-canvas').src=${JSON.stringify(route+'&reopen=1')}`);
      frame=await waitFor(async()=>{
        const current=win.webContents.mainFrame.framesInSubtree.find(item=>item.url.includes(`/static/${page}?id=`)&&item.url.includes('reopen=1'));
        if (!current) return false;
        return await current.executeJavaScript(`typeof canvas !== 'undefined' && canvas?.id === ${JSON.stringify(created.id)} && document.querySelectorAll('#nodes > .node').length === ${interaction.created.length}`).catch(()=>false) ? current : false;
      },'ordinary canvas nodes reopened');
    }
    results.push({ kind, saved: true, ...(interaction ? {interaction:{rightClickMenuTypes:interaction.rightClickMenuTypes,linkedNode:true,toolbarTypes:interaction.toolbarTypes,retiredTypesBlocked:interaction.retiredTypesBlocked,reopened:true}} : {}) });
  }
  currentStage = 'download';
  const downloadPath = path.join(root, 'smoke-download.txt');
  const downloaded = new Promise((resolve, reject) => {
    const timeout = setTimeout(() => { session.removeListener('will-download', listener); reject(new Error('download timeout')); }, 15000);
    function listener(_event, item) {
      if (item.getFilename() !== 'desktop-smoke.txt') return;
      session.removeListener('will-download', listener);
      item.setSavePath(downloadPath);
      item.once('done', (_event, state) => { clearTimeout(timeout); state === 'completed' ? resolve() : reject(new Error('download failed')); });
    }
    session.on('will-download', listener);
  });
  await execute(`(()=>{const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob(['InfiniteCanvas desktop smoke\\n'],{type:'text/plain'})); a.download='desktop-smoke.txt'; a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),3000);})()`);
  await downloaded;
  assert.equal(fs.readFileSync(downloadPath, 'utf8'), 'InfiniteCanvas desktop smoke\n');
  currentStage = 'screenshot';
  await execute(`document.querySelector('.sidebar').classList.add('is-pinned')`);
  await sleep(600);
  const actions = await execute(`Array.from(document.querySelectorAll('.side-actions > button:not(#update-now-btn)')).map(el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};})`);
  assert.equal(actions.length, 4);
  for (const rect of actions) {
    assert.ok(Math.abs(rect.width - actions[0].width) < 1, 'sidebar equal width');
    assert.ok(Math.abs(rect.height - actions[0].height) < 1, 'sidebar equal height');
  }
  const gap = actions[1].y - actions[0].y - actions[0].height;
  for (let index = 2; index < actions.length; index++) assert.ok(Math.abs(actions[index].y - actions[index-1].y - actions[index-1].height - gap) < 1, 'sidebar equal gaps');
  const screenshot = await win.webContents.capturePage();
  fs.writeFileSync(path.join(root, 'smoke.png'), screenshot.toPNG());
  const first = actions[0], last = actions.at(-1);
  const sidebar = await win.webContents.capturePage({ x:Math.floor(first.x-8),y:Math.floor(first.y-8),width:Math.ceil(first.width+16),height:Math.ceil(last.y+last.height-first.y+16) });
  fs.writeFileSync(path.join(root,'sidebar-actions.png'),sidebar.toPNG());
  assert.deepEqual(legacyUpdateRequests, [], 'desktop must not request the legacy web updater');
  session.webRequest.onBeforeRequest(null);
  return { projects: true, apiSettings, githubUpdater, legacyUpdateRequests:0, iframePrompt: true, canvases: results, closeSave: true, download: true, sidebarFourRows: true, screenshot: 'smoke.png' };
}
module.exports = { run, getStage: () => currentStage };
