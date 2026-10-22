'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');

test('API forms and model controls contain no retired adapters or workflow requests', () => {
  for(const name of ['backend/static/api-settings.html','backend/static/js/api-settings.js','backend/static/online.html','backend/static/smart-canvas.html','backend/tools/photoshop-asset-connector/index.html','backend/tools/photoshop-asset-connector/js/generate.js']) {
    const source = read(name);
    assert.doesNotMatch(source, /api\/runninghub|value=["'](?:runninghub|volcengine)["']|rhWorkflow|volcAkInput|isRunningHubProvider|isVolcengineProvider/, name);
  }
  assert.ok(!fs.existsSync(path.join(root, 'backend/static/runninghub/api_providers.json')));
});

test('ordinary canvas retains an explicit unavailable provider instead of switching accounts', () => {
  const source = read('backend/static/js/canvas.js');
  const active = [{id:'apimart', image_models:['gpt-image-2.5-ext'], chat_models:['chat'], video_models:['minimax-h3']}];
  const context = {imageApiProviders:()=>active, chatApiProviders:()=>active, videoApiProviders:()=>active};
  for(const name of ['resolveImageProviderId','resolveChatProviderId','resolveVideoProviderId']) {
    const start=source.indexOf(`function ${name}(`), end=source.indexOf('\nfunction ',start+1);
    vm.runInNewContext(source.slice(start,end),context);
    for(const id of ['runninghub','volcengine','legacy-custom']) assert.equal(context[name](id),id);
    assert.equal(context[name]('apimart'),'apimart');
    assert.equal(context[name](''),'apimart');
  }
});

test('smart canvas blocks old engines and unavailable providers while allowing active image and video models', () => {
  const source = read('backend/static/js/smart-canvas.js');
  const context = {apiProviders:[{id:'apimart',enabled:true}]};
  const start=source.indexOf('function removedSmartPlatform('), end=source.indexOf('function renderDynamicParams(',start);
  vm.runInNewContext(source.slice(start,end),context);
  for(const old of ['runninghub','volcengine']) {
    assert.throws(()=>context.requireSmartPlatform({engine:old,provider_id:'apimart'}),/已移除/);
    assert.throws(()=>context.requireSmartPlatform({engine:'api',provider_id:old}),/已移除/);
    assert.throws(()=>context.requireSmartPlatform({engine:'api',apiKind:'video',videoProvider:old}),/已移除/);
  }
  assert.throws(()=>context.requireSmartPlatform({engine:'api',provider_id:'legacy-custom'}),/不可用/);
  context.requireSmartPlatform({engine:'api',provider_id:'apimart'});
  context.requireSmartPlatform({engine:'api',apiKind:'video',videoProvider:'apimart'});
  context.requireSmartPlatform({engine:'comfy',provider_id:'legacy-custom'});
  context.requireSmartPlatform({engine:'modelscope',provider_id:'volcengine'});
});
