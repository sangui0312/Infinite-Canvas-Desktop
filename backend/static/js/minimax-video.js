(function(root){
    'use strict';
    const profiles = {
        'minimax-h3': {model:'MiniMax-H3', min:4, max:15, resolutions:['768p','2k'], defaultResolution:'2k'},
        'minimax-h3-max': {model:'MiniMax-H3-Max', min:5, max:15, resolutions:['480p','768p','1080p'], defaultResolution:'768p'}
    };
    const ratios = ['21:9','16:9','4:3','1:1','3:4','9:16','adaptive','keep_ratio'];
    const phases = {queued:'准备升级',verifying:'核对原任务',submitting:'提交升级',generating:'正在生成 2K',downloading:'正在保存 2K',waiting:'继续查询升级',completed:'2K 已完成',failed:'升级失败，重试',uncertain:'提交待核对'};
    const profile = model => profiles[String(model || '').trim().toLowerCase()] || null;
    const canUpgrade = item => item?.h3_request?.model === 'MiniMax-H3' && String(item.h3_request.resolution).toUpperCase() === '768P' && !!item.h3_request.task_id && !!item.h3_request.provider_id;
    const label = item => phases[item?.h3_regeneration?.status] || '升至 2K';
    function validate(model, settings, refs={}){
        const p=profile(model); if(!p) return;
        const duration=Number(settings.duration);
        if(!Number.isInteger(duration) || duration<p.min || duration>p.max) throw new Error(`${p.model} 时长须为 ${p.min}～${p.max} 秒。`);
        const resolution=String(settings.resolution || p.defaultResolution).toLowerCase();
        if(!p.resolutions.includes(resolution)) throw new Error(`${p.model} 分辨率仅支持 ${p.resolutions.map(x=>x.toUpperCase()).join('、')}。`);
        if(settings.enable_upsample) throw new Error('请先生成 768P 视频，再使用结果上的“升至 2K”。');
        if(settings.watermark && p.model==='MiniMax-H3-Max' && resolution==='1080p') throw new Error('MiniMax-H3-Max 1080P 不支持水印，请关闭水印。');
        const images=refs.images || [],videos=refs.videos || [],audios=refs.audios || [];
        if(images.length>(settings.use_frame_roles ? 2 : 9) || videos.length>3 || audios.length>3) throw new Error('首尾帧最多 2 张图片；参考模式最多 9 张图片、3 段视频、3 段音频。');
        if(settings.use_frame_roles && (!images.length || videos.length || audios.length || settings.multimodal)) throw new Error('首尾帧需要 1～2 张图片，不能混用参考视频、音频或全能参考模式。');
        if(audios.length && !images.length && !videos.length) throw new Error('参考音频须搭配图片或视频。');
        if(!settings.use_frame_roles && !ratios.includes(settings.aspect_ratio || '16:9')) throw new Error('所选比例不受 MiniMax-H3 系列支持。');
    }
    function install(adapter, options={}){
        const request=options.request || (async (url, body) => {
            const response=await root.fetch(url,body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {});
            const result=await response.json();
            if(!response.ok) throw new Error(typeof result.detail==='string' ? result.detail : '升级请求失败。');
            return result;
        });
        const confirm=options.confirm || (text=>root.confirm(text));
        const delay=options.delay || (()=>new Promise(resolve=>setTimeout(resolve,2500)));
        const active=new Map();
        const key=item=>`${item.h3_request.provider_id}\n${item.h3_request.task_id}`;
        // Smart canvas serializes media into new objects during each save.
        // Resolve the live entries for every update instead of retaining a stale object.
        const live=item=>adapter.items().filter(entry=>canUpgrade(entry) && key(entry)===key(item) && entry.url===item.url);
        const update=(item,job)=>{
            item.h3_regeneration={...job};
            for(const entry of live(item)) entry.h3_regeneration={...job};
            adapter.changed(live(item)[0] || item);
        };
        async function follow(item,job){
            while(job && !['failed','uncertain','completed','not_found'].includes(job.status)){
                update(item,job);
                await delay();
                job=await request('/api/minimax-regeneration/'+encodeURIComponent(job.id));
            }
            if(!job || job.status==='not_found') return;
            update(item,job);
            if(job.status==='completed') adapter.completed(live(item)[0] || item,job.outputs || []);
            else if(job.error) adapter.notify(job.error);
        }
        async function work(item,auto){
            let job=item.h3_regeneration;
            if(job?.status==='uncertain'){if(!auto) adapter.notify(job.error || '请到 APIMart 核对提交结果。');return;}
            if(job?.status==='completed'){if(!auto) adapter.completed(live(item)[0] || item,job.outputs || []);return;}
            const retry=job?.status==='failed';
            if(auto && retry) return;
            if(!job?.id || retry){
                if(auto) return;
                if(!confirm('将使用原任务再生成 2K，额外计费；原参考素材也可能再次计费。768P 原片会保留。继续？')) return;
                const source=item.h3_request;
                try{
                    job=await request('/api/minimax-regeneration',{provider_id:source.provider_id,source_task_id:source.task_id,source,confirmed:true,retry});
                }catch(error){
                    // The response may have been lost after acceptance. Discover
                    // the durable job instead of sending another paid request.
                    job=await request('/api/minimax-regeneration?'+new URLSearchParams({provider_id:source.provider_id,source_task_id:source.task_id}));
                    if(job.status==='not_found') throw error;
                }
            }
            await follow(item,job);
        }
        function start(item,auto=false){
            if(!canUpgrade(item)) return Promise.resolve();
            const id=key(item);
            if(active.has(id)) return active.get(id);
            const running=work(item,auto).catch(error=>adapter.notify(error.message || '升级查询失败，可点击入口继续查询。')).finally(()=>active.delete(id));
            active.set(id,running);return running;
        }
        function resume(){
            for(const item of adapter.items()) if(canUpgrade(item) && item.h3_regeneration?.id && !['completed','failed','uncertain'].includes(item.h3_regeneration.status)) start(item,true);
        }
        if(root.document && options.listen!==false){
            root.document.addEventListener('click',event=>{
                const button=event.target.closest('[data-h3-upgrade-url]');if(!button) return;
                event.preventDefault();event.stopImmediatePropagation();
                const item=adapter.items().find(item=>item.url===button.dataset.h3UpgradeUrl);if(item) start(item);
            },true);
            root.document.addEventListener('pointerdown',event=>{if(event.target.closest('[data-h3-upgrade-url]'))event.stopImmediatePropagation();},true);
            root.setInterval(resume,10000);
        }
        return {start,resume};
    }
    const api={profile,ratios,canUpgrade,label,validate,install};
    if(typeof module==='object' && module.exports) module.exports=api;
    else root.MiniMaxVideo=api;
})(typeof window==='object' ? window : globalThis);
