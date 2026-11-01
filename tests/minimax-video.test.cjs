'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const api=require('../backend/static/js/minimax-video.js');
const original=()=>({url:'/output/768.mp4',h3_request:{provider_id:'apimart',model:'MiniMax-H3',resolution:'768P',task_id:'source'}});

test('both canvases apply different H3/Max capabilities without changing other models',()=>{
    for(const [file,fn] of [['canvas.js','isCanvasH3Video'],['smart-canvas.js','isSmartH3Video']]){
        const source=fs.readFileSync('backend/static/js/'+file,'utf8');
        const context={MiniMaxVideo:api,isApimartProviderId:id=>id==='apimart',tr:x=>x};
        for(const name of [fn,'videoResolutionOptions']){
            const start=source.indexOf(`function ${name}(`),end=source.indexOf('\nfunction ',start+1);
            vm.runInNewContext(source.slice(start,end),context);
        }
        for(const model of ['MiniMax-H3','MiniMax-H3-Max']) assert.equal(context[fn]({model,apiProvider:'apimart',videoModel:model,videoProvider:'apimart'}),true);
        assert.deepEqual(Array.from(context.videoResolutionOptions('apimart','MiniMax-H3-Max'),x=>x[0]),['','480p','768p','1080p']);
        assert.deepEqual(Array.from(context.videoResolutionOptions('apimart','MiniMax-H3'),x=>x[0]),['','768p','2k']);
        assert.ok(context.videoResolutionOptions('custom','MiniMax-H3').some(x=>x[0]==='4k'));
    }
});

test('Max invalid duration, resolution, watermark and mixed-frame references stop before submission',()=>{
    for(const settings of [{duration:4},{duration:5,resolution:'2k'},{duration:5,resolution:'1080p',watermark:true},{duration:5,enable_upsample:true}]) assert.throws(()=>api.validate('MiniMax-H3-Max',settings));
    api.validate('MiniMax-H3-Max',{duration:15,resolution:'1080p'},{images:[{}],videos:[{}]});
    assert.throws(()=>api.validate('MiniMax-H3-Max',{duration:5,use_frame_roles:true},{images:[{}],videos:[{}]}));
    assert.throws(()=>api.validate('MiniMax-H3',{duration:4},{images:Array(10).fill({})}));
    assert.throws(()=>api.validate('MiniMax-H3',{duration:4},{audios:[{}]}));
    api.validate('MiniMax-H3',{duration:4,resolution:'2k'});
});

test('upgrade eligibility excludes Max, external videos and already-2K outputs',()=>{
    assert.equal(api.canUpgrade(original()),true);
    for(const model of ['MiniMax-H3-Max','MiniMax-H3-Regeneration']) assert.equal(api.canUpgrade({...original(),h3_request:{...original().h3_request,model}}),false);
    assert.equal(api.canUpgrade({url:'/output/external.mp4'}),false);
    assert.equal(api.canUpgrade({...original(),h3_request:{...original().h3_request,resolution:'2K'}}),false);
});

function fixture(request,confirm=()=>true){
    const item=original(),updates=[],completed=[],messages=[];
    const controller=api.install({items:()=>[item],changed:i=>updates.push(i.h3_regeneration.status),completed:(i,outputs)=>completed.push(outputs),notify:message=>messages.push(message)}, {request,confirm,delay:async()=>{},listen:false});
    return {item,updates,completed,messages,controller};
}

test('simultaneous clicks create one job; a lost response is recovered by lookup, never POST retry',async()=>{
    let posts=0,lookups=0;
    const f=fixture(async(url,body)=>{
        if(body){posts++;throw Error('response lost');}
        if(url.includes('?')){lookups++;return {id:'job',status:'generating'};}
        return {id:'job',status:'completed',outputs:[{url:'/output/2k.mp4'}]};
    });
    await Promise.all([f.controller.start(f.item),f.controller.start(f.item)]);
    assert.equal(posts,1);assert.equal(lookups,1);
    assert.equal(f.completed.length,1);assert.equal(f.item.url,'/output/768.mp4');
    assert.ok(f.updates.includes('generating'));assert.equal(api.label(f.item),'2K 已完成');
});

test('confirmation cancellation and unknown submissions never create another paid request',async()=>{
    let calls=0;
    const f=fixture(async()=>{calls++;},()=>false);
    await f.controller.start(f.item);assert.equal(calls,0);
    f.item.h3_regeneration={id:'job',status:'uncertain',error:'核对任务'};
    await f.controller.start(f.item);assert.equal(calls,0);assert.equal(f.messages.at(-1),'核对任务');
});

test('persisted upgrade resumes queries and download failures leave the original video intact',async()=>{
    const f=fixture(async(url,body)=>{
        assert.equal(body,undefined);
        return {id:'job',status:'completed',outputs:[{url:'/output/2k.mp4'}]};
    });
    f.item.h3_regeneration={id:'job',status:'waiting'};
    await f.controller.start(f.item,true);
    assert.equal(f.item.url,'/output/768.mp4');assert.equal(f.completed.length,1);
});

test('API helpers are loaded before both canvas scripts',()=>{
    for(const name of ['canvas','smart-canvas']){
        const source=fs.readFileSync('backend/static/'+name+'.html','utf8');
        assert.ok(source.indexOf('/static/js/minimax-video.js')<source.indexOf('/static/js/'+name+'.js?'));
    }
});

test('smart save object replacement cannot strand progress or lose completed outputs',async()=>{
    let item=original(),completed;
    const first=item;
    const controller=api.install({items:()=>[item],changed(){item=JSON.parse(JSON.stringify(item));},completed:source=>completed=source,notify:message=>{throw Error(message);}}, {
        listen:false,confirm:()=>true,delay:async()=>{},
        request:async(url,body)=>body ? {id:'job',status:'generating'} : {id:'job',status:'completed',outputs:[{url:'/output/2k.mp4'}]}
    });
    await controller.start(first);
    assert.equal(item.h3_regeneration.status,'completed');
    assert.notEqual(item,first);assert.equal(completed,item);
});
