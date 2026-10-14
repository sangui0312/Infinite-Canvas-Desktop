'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

async function run({win,root,waitFor}) {
  const execute = script => win.webContents.executeJavaScript(script);
  await execute(`switchUI(null, 'api-settings')`);
  const frame = await waitFor(async()=>{
    const candidate = win.webContents.mainFrame.framesInSubtree.find(item=>item.url.includes('/static/api-settings.html'));
    return candidate && await candidate.executeJavaScript(`typeof loadProviders === 'function' && document.readyState === 'complete'`).catch(()=>false) ? candidate : false;
  },'API settings page');
  const state = ()=>frame.executeJavaScript(`({visible:visibleProviders().map(p=>p.id),selected:provider()?.id||'',title:editorTitle.textContent,settings:getComputedStyle(settingsContent).display,recommend:getComputedStyle(recommendContent).display,onboarding:getComputedStyle(providerOnboardingCard).display,list:Array.from(providerList.querySelectorAll('[onclick]')).map(el=>el.getAttribute('onclick'))})`);
  await frame.executeJavaScript('loadProviders()');
  let view=await state();
  assert.equal(view.visible.length,0);
  assert.equal(view.selected,'');
  assert.equal(view.settings,'none');
  assert.notEqual(view.recommend,'none');
  assert.equal(view.onboarding,'none');
  assert.ok(!view.title.includes('ModelScope'));
  await frame.executeJavaScript(`closeRecommendApi(); renderEditor(); selectProvider('modelscope');`);
  view=await state();
  assert.equal(view.settings,'none');
  assert.equal(view.selected,'');
  assert.notEqual(view.recommend,'none');
  await frame.executeJavaScript('addProvider()');
  view=await state();
  assert.equal(view.visible.length,1);
  const customId=view.selected;
  assert.ok(customId.startsWith('custom-api'));
  assert.notEqual(view.settings,'none');
  assert.equal(view.recommend,'none');
  assert.equal(await frame.executeJavaScript('saveProviders()'),true);
  await frame.executeJavaScript(`selectProvider('modelscope'); selectProvider('runninghub'); selectProvider('volcengine');`);
  assert.equal((await state()).selected,customId);
  await frame.executeJavaScript('loadProviders()');
  view=await state();
  assert.equal(view.selected,customId);
  assert.notEqual(view.settings,'none');
  assert.equal(view.recommend,'none');
  assert.ok(view.list.every(value=>!['modelscope','runninghub','volcengine'].some(id=>value.includes("'"+id+"'"))));
  await frame.executeJavaScript('deleteProvider()');
  await waitFor(async()=>{
    const view=await state();
    return view.visible.length===0 && view.settings==='none' && view.recommend!=='none';
  },'empty API page after delete');
  await waitFor(()=>frame.executeJavaScript(`fetch('/api/providers').then(r=>r.json()).then(d=>!d.providers.some(p=>p.id===${JSON.stringify(customId)}))`),'deleted API persistence');
  await frame.executeJavaScript(`window.dispatchEvent(new MessageEvent('message',{data:{type:'studio-lang',lang:'en'}}));`);
  view=await state();
  assert.equal(view.settings,'none');
  assert.equal(view.selected,'');
  await frame.executeJavaScript(`window.dispatchEvent(new MessageEvent('message',{data:{type:'studio-lang',lang:'zh'}}));`);
  await new Promise(resolve=>setTimeout(resolve,250));
  fs.writeFileSync(path.join(root,'api-settings-empty.png'),(await win.webContents.capturePage()).toPNG());
  await execute(`switchUI(null,'canvas')`);
  return {hiddenPlatformsExcluded:true,emptyRecommendation:true,customDefault:true,deleteLastVisible:true,languageRefresh:true};
}
module.exports={run};
