'use strict';
const assert = require('node:assert/strict');
async function run({frame}) {
  function probe() {
    const fail = message => { throw new Error(message); };
    const created = [];
    const board = document.getElementById('board');
    const rect = board.getBoundingClientRect();
    const point = {x:rect.left + Math.min(350, rect.width / 2),y:rect.top + Math.min(260,rect.height / 2)};
    const createAndCheck = (type, click) => {
      const before = new Set(nodes.map(node => node.id));
      click();
      const added = nodes.filter(node => !before.has(node.id));
      if (added.length !== 1 || added[0].type !== type) fail(`Failed to create ${type}`);
      if (!document.querySelector(`.node[data-id="${added[0].id}"]`)) fail(`Created ${type} did not render`);
      created.push({id:added[0].id,type});
      return added[0];
    };
    for (const type of ['image','prompt','loop','llm','generator','video','output','layers']) {
      board.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,button:2,clientX:point.x,clientY:point.y}));
      if (!document.getElementById('createMenu').classList.contains('open')) fail('Right-click menu did not open');
      createAndCheck(type,()=>document.querySelector(`#createMenu button[onclick="menuAdd('${type}')"]`).click());
      if (document.getElementById('createMenu').classList.contains('open')) fail('Create menu did not close');
    }
    const origin=nodes.find(node=>node.id===created.find(item=>item.type==='prompt').id);
    origin.text='Desktop ordinary canvas interaction';
    openLinkCreateMenu(origin.id,'out',point.x+220,point.y+100);
    const linked=createAndCheck('generator',()=>{
      const button=document.querySelector('#linkCreateMenu button[data-link-create="generator"]');
      if (!button) fail('Linked API node menu item missing');
      button.click();
    });
    if (!connections.some(link=>link.from===origin.id&&link.to===linked.id)) fail('Created linked node has no connection');
    document.querySelector('#quickToolbar .toolbar-toggle').click();
    createAndCheck('image',()=>document.querySelector('#quickToolbar button[onclick="addImageNode()"]')?.click());
    createAndCheck('prompt',()=>document.querySelector('#quickToolbar button[onclick="addPromptNode()"]')?.click());
    const count=nodes.length;
    for (const type of ['midjourney','msgen','minimax','rh','comfy','ltxDirector']) {
      menuAdd(type);
      if (createNodeByType(type,{x:0,y:0})!==null||nodes.length!==count) fail(`Retired type became available: ${type}`);
    }
    return {created,rightClickMenuTypes:8,linkedNode:true,toolbarTypes:2,retiredTypesBlocked:6,connection:{from:origin.id,to:linked.id}};
  }
  const result=await frame.executeJavaScript(`(()=>{try{return (${probe.toString()})()}catch(error){return {failure:String(error?.message||error)}}})()`);
  assert.equal(result.failure,undefined,result.failure);
  assert.equal(result.created.length,11);
  return result;
}
module.exports={run};
