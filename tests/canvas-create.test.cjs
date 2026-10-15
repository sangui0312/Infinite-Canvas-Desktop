'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../backend/static/js/canvas.js'), 'utf8');
function fixture() {
  const calls = [];
  const context = {menuPoint:{x:40,y:50},closeCreateMenu:()=>calls.push({action:'close'})};
  const creators = {image:'addImageNode',prompt:'addPromptNode',loop:'addLoopNode',group:'addGroupNode',llm:'addLLMNode',generator:'addGeneratorNode',video:'addVideoNode',output:'addOutputNode',layers:'addLayersNode'};
  for (const [type, name] of Object.entries(creators)) context[name] = point => {const node={type,point};calls.push({action:'create',node});return node;};
  const declarations = source.slice(source.indexOf('const RETIRED_CANVAS_NODE_NAMES'), source.indexOf('function retiredCanvasNodeLabel'));
  const functions = source.slice(source.indexOf('function createNodeByType('), source.indexOf('function mediaKindForUpload('));
  vm.runInNewContext(declarations + functions, context);
  return {context,calls};
}
test('every visible ordinary-canvas menu item dispatches to its creator at the clicked position',()=>{
  for (const type of ['image','prompt','loop','llm','generator','video','output','layers']) {
    const {context,calls}=fixture();
    context.menuAdd(type);
    assert.equal(calls.length,2,type);
    assert.equal(calls[0].action,'close');
    assert.equal(calls[1].node.type,type);
    assert.equal(calls[1].node.point,context.menuPoint);
  }
});
test('linked-node creation dispatches every supported type at the requested position',()=>{
  for (const type of ['image','prompt','loop','group','llm','generator','video','output','layers']) {
    const {context,calls}=fixture(), point={x:100,y:200};
    const node=context.createNodeByType(type,point);
    assert.equal(node.type,type);assert.equal(node.point,point);assert.equal(calls.length,1);
  }
});
test('retired node types stay unavailable through menu and linked-node creation',()=>{
  for (const type of ['midjourney','msgen','minimax','rh','comfy','ltxDirector']) {
    const {context,calls}=fixture();
    context.menuAdd(type);
    assert.equal(context.createNodeByType(type,{x:0,y:0}),null);
    assert.equal(calls.length,1);assert.equal(calls[0].action,'close');
  }
});
