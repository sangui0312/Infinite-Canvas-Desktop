const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {collectReferences,localReference,createSession,request,resultMessage,canvasSourceId} = require('../backend/static/js/media-cleanup.js');

test('references include nested current/undo/pending/maps, skip binary and remote origins', () => {
  const current = {url:'/assets/a.png',nodes:[{url:'http://localhost/output/b.mp4?v=2'}]};
  current.circular = current;
  const value = [current,{undo:[{images:[{url:'/assets/undo.png'}]}]},new Map([['pending',{url:'/api/storage-files/input/c.png'}]]),new Uint8Array(1000),'https://remote.invalid/assets/a.png','data:image/png;base64,x'];
  assert.deepEqual(collectReferences(value,'http://localhost'),['/api/storage-files/input/c.png','/assets/a.png','/assets/undo.png','/output/b.mp4?v=2']);
  assert.equal(localReference('http://localhost.evil/assets/a.png','http://localhost'),'');
  assert.equal(localReference('E:\\private\\source.png'),'E:\\private\\source.png');
  assert.equal(canvasSourceId('data/canvases/canvas-123.json'),'canvas-123');
  assert.deepEqual(collectReferences('![image](/assets/unsaved.png) <img src="/assets/input.png"> https://remote.invalid/assets/remote.png','http://localhost'),['/assets/input.png','/assets/unsaved.png']);
});

test('queued heartbeat captures versions in order and ensure preserves ALL current/undo references', async () => {
  const calls = []; let unblock;
  let state = {url:'/assets/current.png',undo:[{url:'/assets/undo.png'}]};
  const session = createSession({id:'test',snapshot:()=>state,heartbeatMs:0,request:async (url,payload) => {
    calls.push({url,payload});
    if(calls.length === 1) await new Promise(resolve => {unblock=resolve;});
    return {ok:true};
  }});
  const first=session.sync(); await new Promise(setImmediate);
  state={url:'/assets/new.png',undo:state.undo}; const second=session.sync();
  unblock(); await Promise.all([first,second]);
  assert.deepEqual(calls.map(c=>c.payload.version),[1,2]);
  assert.ok(calls[0].payload.references.includes('/assets/current.png'));
  assert.ok(calls[1].payload.references.includes('/assets/new.png'));
  await session.ensure({url:'/assets/undo.png'});
  assert.deepEqual(calls.at(-1).payload.references,['/assets/new.png','/assets/undo.png']);
  assert.equal(session.available,true);session.destroy();
});

test('sync failure and uncertain coverage block undo preflight; later sync can recover', async () => {
  let fail=true, uncertain=false;const statuses=[];
  const session=createSession({snapshot:()=>({url:'/assets/a.png'}),heartbeatMs:0,onStatus:s=>statuses.push(s),request:async()=>{
    if(fail) throw new Error('offline'); return {uncertain};
  }});
  await assert.rejects(session.ensure({url:'/assets/a.png'}),/offline/);
  assert.equal(session.available,false);
  fail=false;uncertain=true;
  await assert.rejects(session.ensure({url:'/assets/a.png'}),/无法确认/);
  uncertain=false;await session.ensure({url:'/assets/a.png'});
  assert.equal(session.available,true);assert.equal(statuses.at(-1).error,'');session.destroy();
});

test('rapid render changes coalesce behind an in-flight sync without growing a queue',async()=>{
  const calls=[];let release,state={url:'/assets/first.png'};
  const session=createSession({snapshot:()=>state,heartbeatMs:0,debounceMs:1,request:async(_url,payload)=>{
    calls.push(payload);if(calls.length===1)await new Promise(resolve=>{release=resolve;});return {ok:true};
  }});
  const first=session.sync();await new Promise(setImmediate);
  for(let index=0;index<100;index++){state={url:`/assets/changed-${index}.png`};session.changed();}
  await new Promise(resolve=>setTimeout(resolve,10));assert.equal(calls.length,1);
  release();await first;await new Promise(resolve=>setTimeout(resolve,10));
  assert.equal(calls.length,2);assert.deepEqual(calls[1].references,['/assets/changed-99.png']);session.destroy();
});

test('API errors are reported and HTTP deletion method is preserved', async () => {
  let method;
  await request('/api/asset-library/items/x',undefined,{method:'DELETE',fetch:async(_url,options)=>{method=options.method;return {ok:true,json:async()=>({})};}});
  assert.equal(method,'DELETE');
  await assert.rejects(request('/api/media-cleanup/actions',{}, {fetch:async()=>({ok:false,status:409,json:async()=>({detail:'预览已变化'})})}),/预览已变化/);
  assert.match(resultMessage({results:[{status:'trashed'},{status:'failed',code:'referenced'}]}),/处理 2 项，保留或未完成 1 项/);
});

test('normal close releases only the acknowledged saved revision with keepalive; dirty/error/pending retain protection', async()=>{
  for(const scenario of ['saved','dirty','failed','pending']) {
    const calls=[];let release;
    const session=createSession({snapshot:()=>({url:'/assets/a.png'}),heartbeatMs:0,request:async(url,payload,options)=>{
      calls.push({url,payload,options});
      if(scenario==='failed' && calls.length===2) throw new Error('offline');
      if(scenario==='pending' && calls.length===2) await new Promise(resolve=>{release=resolve;});
      return {ok:true};
    }});
    await session.sync();session.markSaved();
    let pending;
    if(scenario==='dirty') session.changed();
    if(scenario==='failed') await session.sync().catch(()=>{});
    if(scenario==='pending') {pending=session.sync();await new Promise(setImmediate);}
    await session.close();
    const final=calls.at(-1);assert.equal(final.payload.closed,scenario==='saved',scenario);assert.equal(final.options.keepalive,true);
    if(pending) {release();await pending;}
  }
});

function actualUndo(name, ensure) {
  const text=fs.readFileSync(path.join(__dirname,'../backend/static/js',name),'utf8');
  const start=text.indexOf('let mediaUndoInFlight = false;');
  const end=text.indexOf(name === 'canvas.js' ? 'function cloneNode' : 'let comfyWorkflowCache',start);
  const state={nodes:[{id:'old',url:'/assets/restore.png'}],connections:[],selectedId:'old',selectedIds:['old'],selectedImage:{}};
  const messages=[];
  const context={window:{canvasMediaSession:{revision:1,ensure}},canvas:{id:'c',connections:[]},nodes:[{id:'current'}],connections:[],undoStack:[state],selected:new Set(['current']),toast:m=>messages.push(m),setStatus:m=>messages.push(m),tr:k=>k,render(){},scheduleSave(){},undoSuppressed:false,activeComposerSubject:null,lastComposerNodeId:''};
  vm.createContext(context);vm.runInContext(text.slice(start,end),context);
  return {context,state,messages,undo:()=>context.performUndo()};
}
for(const name of ['canvas.js','smart-canvas.js']) {
  test(`${name}: cleanup synchronization failure never suppresses durable canvas saving`,async()=>{
    const text=fs.readFileSync(path.join(__dirname,'../backend/static/js',name),'utf8');
    const classic=name==='canvas.js';
    const start=text.indexOf(classic?'async function saveCanvas(){':'async function persistSmartCanvas(){');
    const end=text.indexOf(classic?'async function loadConfig()':'function imageMetaFromNode',start);
    let saves=0;
    const canvas={id:'c',title:'c',connections:[],viewport:{},nodes:[]};
    const context={window:{canvasMediaSession:{sync:async()=>{throw new Error('cleanup offline');},revision:1,markSaved(){}}},canvas,canvasId:'c',applyingRemoteCanvas:false,savingCanvasNow:false,saveCanvasAgain:false,localCanvasDirty:true,lastCanvasUpdatedAt:0,nodes:[],connections:[],viewport:{},sanitizeConnections(){},serializableCanvasNodes:()=>[],setStatus(){},CLIENT_ID:'test',currentCanvasTime:null,loadCanvasList(){},setTimeout,console,savePromptDraftForCurrent(){},canvasDefaultSmartSettings:{},initialSmartSettings:{},settingsForStorage:v=>v,canvasForStorage:()=>canvas,canvasSyncInFlight:false,smartClientId:'test',toast(){},fetch:async()=>{saves++;return {ok:true,status:200,json:async()=>({})};}};
    vm.createContext(context);vm.runInContext(text.slice(start,end),context);
    await context[classic?'saveCanvas':'persistSmartCanvas']();
    assert.equal(saves,1);
    assert.equal(context.savingCanvasNow,false);
  });
  test(`${name}: actual undo leaves stack/current state untouched until restoration succeeds`, async()=>{
    let release;
    const value=actualUndo(name,()=>new Promise(resolve=>{release=resolve;}));
    const pending=value.undo();assert.equal(value.context.nodes[0].id,'current');assert.equal(value.context.undoStack.length,1);
    release({ok:true});await pending;
    assert.equal(value.context.nodes[0].id,'old');assert.equal(value.context.undoStack.length,0);
  });
  test(`${name}: failed restore and edits racing restore never overwrite the canvas`,async()=>{
    const failed=actualUndo(name,async()=>{throw new Error('restore failed');});await failed.undo();
    assert.equal(failed.context.nodes[0].id,'current');assert.equal(failed.context.undoStack.length,1);
    let release;const raced=actualUndo(name,()=>new Promise(resolve=>{release=resolve;}));
    const pending=raced.undo();raced.context.window.canvasMediaSession.revision++;raced.context.nodes=[{id:'new-edit'}];release({ok:true});await pending;
    assert.equal(raced.context.nodes[0].id,'new-edit');assert.equal(raced.context.undoStack.length,1);
  });
}
