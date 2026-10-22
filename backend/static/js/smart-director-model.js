(function(root, factory){
    const model = factory();
    if(typeof module === 'object' && module.exports) module.exports = model;
    else root.SmartDirectorModel = model;
})(typeof window === 'object' ? window : globalThis, function(){
    'use strict';
    const ratioSizes = {'16:9':[1280,720], '9:16':[720,1280], '1:1':[720,720], '4:3':[960,720], '3:4':[540,720]};
    const clamp = (v,min,max) => Math.max(min,Math.min(max,v));
    const number = (v,f=0) => v !== '' && v != null && Number.isFinite(Number(v)) ? Number(v) : f;
    const clone = value => JSON.parse(JSON.stringify(value));
    const uid = prefix => `${prefix}_${globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2,12)}`;
    function defaults(){
        return {duration:10, ratio:'16:9', playhead:0, selectedPieceId:'piece_red', followPieceId:'', exports:[],
            pieces:[{id:'piece_red',name:'红色棋子',color:'#ef4444',keyframes:[{t:0,x:-2,z:0,yaw:0},{t:10,x:2,z:0,yaw:0}]}],
            props:[{id:'block_1',name:'白块 1',x:0,z:-2,w:2,d:1,h:1}],
            camera:{keyframes:[{t:0,x:7,y:6,z:8,targetX:0,targetY:0,targetZ:0,fov:45},{t:10,x:7,y:6,z:8,targetX:0,targetY:0,targetZ:0,fov:45}]}};
    }
    function setKeyframe(frames, frame){
        const i = frames.findIndex(f => Math.abs(f.t-frame.t)<.02);
        if(i<0) frames.push({...frame}); else frames[i] = {...frame};
        frames.sort((a,b)=>a.t-b.t);
    }
    function normalize(value){
        const base=defaults(), source=value && typeof value==='object' ? value : {};
        const d={...base,...clone(source)};
        d.duration=clamp(number(d.duration,10),1,30);
        d.ratio=ratioSizes[d.ratio] ? d.ratio : '16:9';
        d.playhead=clamp(number(d.playhead),0,d.duration);
        const used=new Set();
        function id(value,prefix){ const text=String(value||''); const next=text && !used.has(text) ? text : uid(prefix); used.add(next); return next; }
        function frames(values, fallback, camera=false){
            const result=[];
            for(const raw of Array.isArray(values) && values.length ? values : fallback){
                if(!raw || typeof raw!=='object') continue;
                const f={t:clamp(number(raw.t),0,30),x:number(raw.x),z:number(raw.z)};
                if(camera) Object.assign(f,{y:number(raw.y,6),targetX:number(raw.targetX),targetY:number(raw.targetY),targetZ:number(raw.targetZ),fov:clamp(number(raw.fov,45),20,80)});
                else f.yaw=number(raw.yaw);
                setKeyframe(result,f);
            }
            return result.length ? result : clone(fallback);
        }
        d.pieces=(Array.isArray(source.pieces) ? source.pieces : base.pieces).filter(p=>p && typeof p==='object').map((p,i)=>({
            ...p,id:id(p.id,'piece'),name:String(p.name||`棋子 ${i+1}`),color:/^#[\da-f]{6}$/i.test(p.color) ? p.color : '#3b82f6',
            keyframes:frames(p.keyframes,[{t:0,x:i*1.5,z:0,yaw:0}])}));
        d.props=(Array.isArray(source.props) ? source.props : base.props).filter(p=>p && typeof p==='object').map((p,i)=>({
            ...p,id:id(p.id,'block'),name:String(p.name||`白块 ${i+1}`),x:number(p.x),z:number(p.z),
            w:clamp(number(p.w,1),.1,100),h:clamp(number(p.h,1),.1,100),d:clamp(number(p.d,1),.1,100)}));
        d.camera={...(source.camera||{}),keyframes:frames(source.camera?.keyframes,base.camera.keyframes,true)};
        d.exports=Array.isArray(source.exports) ? clone(source.exports.filter(e=>e?.url)) : [];
        if(!d.pieces.some(p=>p.id===d.selectedPieceId)) d.selectedPieceId=d.pieces[0]?.id||'';
        if(!d.pieces.some(p=>p.id===d.followPieceId)) d.followPieceId='';
        return d;
    }
    function keyframeAt(frames,time){
        const sorted=[...frames].sort((a,b)=>a.t-b.t);
        if(!sorted.length) return {};
        if(time<=sorted[0].t) return {...sorted[0]};
        if(time>=sorted.at(-1).t) return {...sorted.at(-1)};
        const i=sorted.findIndex(f=>f.t>=time),a=sorted[i-1],b=sorted[i],mix=(time-a.t)/(b.t-a.t),out={t:time};
        for(const key of new Set([...Object.keys(a),...Object.keys(b)])){
            if(key==='t') continue;
            const x=number(a[key]),y=number(b[key],x);
            const delta=key==='yaw' ? ((y-x+180)%360+360)%360-180 : y-x;
            out[key]=x+delta*mix;
        }
        return out;
    }
    function moveKeyframe(frames,index,time,duration){
        if(!frames[index]) return false;
        const t=Math.round(clamp(time,0,duration)*100)/100;
        if(frames.some((f,i)=>i!==index && Math.abs(f.t-t)<.02)) return false;
        frames[index]={...frames[index],t}; frames.sort((a,b)=>a.t-b.t); return true;
    }
    function resizeDuration(data,duration){
        const next=clamp(number(duration,data.duration),1,30),scale=next/data.duration;
        for(const frames of [...data.pieces.map(p=>p.keyframes),data.camera.keyframes]){
            const result=[];
            for(const f of frames) setKeyframe(result,{...f,t:Math.round(clamp(f.t*scale,0,next)*100)/100});
            frames.splice(0,frames.length,...result);
        }
        data.playhead=clamp(data.playhead*scale,0,next); data.duration=next;
    }
    function orbitFromFrame(frame){
        const x=frame.x-frame.targetX,y=frame.y-frame.targetY,z=frame.z-frame.targetZ,radius=Math.max(.1,Math.hypot(x,y,z));
        return {targetX:frame.targetX,targetY:frame.targetY,targetZ:frame.targetZ,fov:frame.fov,radius,theta:Math.atan2(x,z),phi:Math.acos(clamp(y/radius,-1,1))};
    }
    function orbitFrame(o){
        return {x:o.targetX+o.radius*Math.sin(o.phi)*Math.sin(o.theta),y:o.targetY+o.radius*Math.cos(o.phi),z:o.targetZ+o.radius*Math.sin(o.phi)*Math.cos(o.theta),targetX:o.targetX,targetY:o.targetY,targetZ:o.targetZ,fov:o.fov||45};
    }
    function editableSnapshot(data){ const result=clone(data); delete result.exports; return result; }
    class History {
        constructor(){ this.undo=[]; this.redo=[]; }
        record(before,after){
            const previous=editableSnapshot(before);
            if(JSON.stringify(previous)===JSON.stringify(editableSnapshot(after))) return false;
            this.undo.push(previous); if(this.undo.length>80) this.undo.shift(); this.redo=[]; return true;
        }
        restore(data,direction){
            const from=direction==='undo'?this.undo:this.redo,to=direction==='undo'?this.redo:this.undo;
            if(!from.length) return null;
            to.push(editableSnapshot(data));
            return normalize({...from.pop(),exports:clone(data.exports)});
        }
    }
    return {ratioSizes,clamp,number,clone,uid,defaults,normalize,keyframeAt,setKeyframe,moveKeyframe,resizeDuration,orbitFromFrame,orbitFrame,History};
});
