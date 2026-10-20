'use strict';
const test=require('node:test'), assert=require('node:assert/strict');
const {merge}=require('../backend/static/js/smart-agent-merge.js');
const copy=x=>JSON.parse(JSON.stringify(x));
const original=()=>({nodes:[{id:'a',title:'原图',x:1,images:[]}],connections:[],settings:{ratio:'square'},viewport:{x:0,y:0}});
test('disjoint manual and agent edits both survive',()=>{
    const base=original(),local=copy(base),remote=copy(base);local.nodes[0].x=150;remote.nodes[0].title='新标题';remote.nodes.push({id:'result',title:'成果'});
    const result=merge(base,local,remote);assert.equal(result.nodes[0].x,150);assert.equal(result.nodes[0].title,'新标题');assert.equal(result.nodes.length,2);
});
test('same field conflict does not silently overwrite draft',()=>{
    const base=original(),local=copy(base),remote=copy(base);local.nodes[0].title='用户';remote.nodes[0].title='Agent';assert.throws(()=>merge(base,local,remote),/冲突/);assert.equal(local.nodes[0].title,'用户');
});
test('tombstone cannot lose unsaved edits or resurrect unchanged node',()=>{
    const base=original(),local=copy(base),remote=copy(base);remote.nodes=[];remote.agent_deleted_nodes={a:'undo'};
    assert.equal(merge(base,local,remote).nodes.length,0);local.nodes[0].title='未保存';assert.throws(()=>merge(base,local,remote),/未保存编辑/);
});
test('deleted connections remain deleted with independently appended results',()=>{
    const base=original();base.nodes.push({id:'b'});base.connections=[{from:'a',to:'b'}];const local=copy(base),remote=copy(base);local.connections=[];remote.nodes.push({id:'c'});remote.connections.push({from:'b',to:'c'});
    assert.deepEqual(merge(base,local,remote).connections,[{from:'b',to:'c'}]);
});
