'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const M=require('../backend/static/js/smart-director-model.js');

test('legacy director data normalizes without mutation or losing output metadata',()=>{
    const old=M.defaults();old.props[0].name=undefined;old.exports=[{url:'/output/old.png',kind:'image',custom:'retained'}];
    const original=JSON.stringify(old),d=M.normalize(old);
    assert.equal(JSON.stringify(old),original);assert.equal(d.props[0].name,'白块 1');assert.equal(d.exports[0].custom,'retained');
    assert.deepEqual(M.normalize({...d,pieces:[],props:[]}).pieces,[]);
    assert.equal(M.normalize({duration:Infinity,ratio:'bad',camera:{keyframes:[]}}).duration,10);
});
test('normalization deduplicates frame times and object IDs and validates dimensions',()=>{
    const d=M.normalize({pieces:[{id:'a',keyframes:[{t:0,x:1},{t:0,x:2}]}],props:[{id:'a',w:-2,h:Infinity}]});
    assert.notEqual(d.pieces[0].id,d.props[0].id);assert.equal(d.pieces[0].keyframes.length,1);assert.equal(d.pieces[0].keyframes[0].x,2);assert.equal(d.props[0].w,.1);assert.equal(d.props[0].h,1);
});
test('motion interpolates position and rotates through the short path',()=>{
    const frames=[{t:0,x:0,z:0,yaw:350},{t:10,x:10,z:2,yaw:10}];
    assert.deepEqual(M.keyframeAt(frames,5),{t:5,x:5,z:1,yaw:360});
    assert.deepEqual(M.keyframeAt(frames,-1),frames[0]);
});
test('keyframe move is bounded and refuses collisions without changing data',()=>{
    const frames=[{t:0,x:1},{t:10,x:2}];
    assert.equal(M.moveKeyframe(frames,1,-3,10),false);assert.equal(frames[1].t,10);
    assert.equal(M.moveKeyframe(frames,0,5,10),true);assert.equal(frames[0].t,5);
    M.setKeyframe(frames,{t:5.001,x:9});assert.equal(frames.length,2);assert.equal(frames[0].x,9);
});
test('changing duration proportionally retimes all actors and camera',()=>{
    const d=M.defaults();d.playhead=5;M.resizeDuration(d,4);
    assert.equal(d.duration,4);assert.equal(d.playhead,2);assert.equal(d.pieces[0].keyframes[1].t,4);assert.equal(d.camera.keyframes[1].t,4);
});
test('orbit zoom changes actual position and preserves look target',()=>{
    const f={x:7,y:6,z:8,targetX:1,targetY:0,targetZ:2,fov:45},o=M.orbitFromFrame(f);
    const initial=M.orbitFrame(o);for(const k of ['x','y','z'])assert.ok(Math.abs(initial[k]-f[k])<1e-10);
    o.radius*=.5;const zoom=M.orbitFrame(o);assert.equal(zoom.targetX,1);
    assert.ok(Math.abs(Math.hypot(zoom.x-1,zoom.y,zoom.z-2)-o.radius)<1e-10);
});
test('undo and redo preserve exported files and invalidate redo on a new edit',()=>{
    const h=new M.History(),d=M.defaults(),before=M.clone(d);d.props[0].w=3;h.record(before,d);
    d.exports.push({url:'/output/kept.webm'});const undo=h.restore(d,'undo');assert.equal(undo.props[0].w,2);assert.equal(undo.exports.length,1);
    const redo=h.restore(undo,'redo');assert.equal(redo.props[0].w,3);const original=M.clone(redo);redo.props[0].w=4;h.record(original,redo);assert.equal(h.redo.length,0);
});
