(function(){
    'use strict';
    const M=window.SmartDirectorModel;
    const {clamp,number,clone,uid,keyframeAt,setKeyframe,orbitFrame,orbitFromFrame}=M;
    const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const time=value=>`${number(value).toFixed(2)}s`;
    let active=null,threePromise=null;
    const field=(label,key,type='number',extra='')=>`<label>${label}<input data-field="${key}" type="${type}" ${type==='number'?'step="0.1"':''} ${extra}></label>`;
    const button=(action,label,extra='')=>`<button type="button" data-director-action="${action}" ${extra}>${label}</button>`;
    function shell(){
        return `<div class="director-modal"><section class="director-shell" role="dialog" aria-modal="true" aria-labelledby="director-title" tabindex="-1">
            <header class="director-head"><div><b id="director-title">3D 导演台</b><span>白模预演 · 走位与运镜</span></div><div class="director-head-actions">${button('undo','撤销','title="Ctrl+Z"')}${button('redo','重做','title="Ctrl+Shift+Z"')}${button('close','关闭','aria-label="关闭 3D 导演台"')}</div></header>
            <main class="director-main"><aside class="director-objects"><div class="director-section-head"><b>场景对象</b><span data-director-count></span></div>
                <div class="director-add">${button('add-piece','＋ 棋子','data-edit-control')}${button('add-block','＋ 白块','data-edit-control')}</div><div data-director-object-list></div>
                <div class="director-object-actions">${button('duplicate','复制','data-edit-control')}${button('delete-object','删除','data-edit-control')}</div>
                <p class="director-help">拖动对象调整走位。<br>右键旋转 · Shift 拖动平移<br>滚轮缩放 · 空格播放</p></aside>
            <section class="director-stage"><div class="director-stage-tools">${button('mode-edit','编辑视角','class="is-active" data-edit-control')}${button('mode-director','导演视角','data-edit-control')}${button('reset-view','重置视角','data-edit-control')}</div>
                <div class="director-canvas-wrap" data-director-canvas><span class="director-loading">正在准备 3D 场景…</span></div>
                <div class="director-stage-badge" data-director-time></div><p class="director-stage-hint" data-director-mode-hint></p></section>
            <aside class="director-panel"><div class="director-section-head"><b>预演设置</b><span>本地渲染</span></div>
                <div class="director-grid2"><label>画面比例<select data-director-ratio data-edit-control>${Object.keys(M.ratioSizes).map(r=>`<option>${r}</option>`).join('')}</select></label>
                <label>时长（秒）<input data-director-duration data-edit-control type="number" min="1" max="30" step="1" title="调整时长会等比例缩放关键帧"></label></div>
                <div data-director-inspector></div><section class="director-section"><div class="director-section-head"><b>镜头跟随</b></div><select data-director-follow data-edit-control aria-label="镜头跟随对象"></select><p class="director-help">跟随对象的位置；镜头位置仍由关键帧控制。</p></section>
            </aside></main>
            <section class="director-timeline"><div class="director-transport">${button('start','回到起点','data-edit-control')}${button('play','播放','data-edit-control')}<span data-director-time></span><input type="range" data-director-playhead data-edit-control min="0" step="0.01" aria-label="播放位置"><span class="director-spacer"></span>${button('delete-keyframe','删除关键帧','data-edit-control')}</div>
                <div class="director-tracks" data-director-tracks></div></section>
            <footer class="director-footer"><span class="director-status" role="status" aria-live="polite" data-director-status>修改自动保存到画布</span><div class="director-export-progress" hidden data-director-export-progress><progress max="100" value="0" aria-label="导演台导出进度"></progress>${button('cancel-export','取消导出')}</div><span class="director-spacer"></span>${button('png','导出 PNG','data-export-control')}${button('webm','导出 WebM','class="director-primary" data-export-control')}</footer>
        </section></div>`;
    }
    class DirectorSession {
        constructor(node,api){
            this.node=node;this.api=api;this.data=node.director=M.normalize(node.director);this.history=new M.History();
            this.selection=this.data.selectedPieceId ? {kind:'piece',id:this.data.selectedPieceId} : {kind:'camera',id:'camera'};
            this.viewMode='edit';this.cameraDraft=null;this.playing=false;this.closed=false;this.job=null;this.listeners=new AbortController();this.returnFocus=document.activeElement;
            this.editOrbit=orbitFromFrame({x:10,y:10,z:10,targetX:0,targetY:0,targetZ:0,fov:45});
            this.root=document.createElement('div');this.root.innerHTML=shell();document.body.appendChild(this.root);
            this.root.querySelector('.director-shell').focus({preventScroll:true});this.bind();this.refresh();this.init();
        }
        $(selector){return this.root.querySelector(selector);}
        on(target,name,fn,options={}){target.addEventListener(name,fn,{...options,signal:this.listeners.signal});}
        persist(){if(!this.closed){this.node.director=this.data;this.api.save(this.node);}}
        status(text){if(!this.closed)this.$('[data-director-status]').textContent=text;}
        notify(text){if(!this.closed){this.status(text);this.api.toast?.(text);}}
        piece(){return this.data.pieces.find(p=>p.id===this.selection.id);}
        prop(){return this.data.props.find(p=>p.id===this.selection.id);}
        frames(track){return track==='camera' ? this.data.camera.keyframes : this.data.pieces.find(p=>p.id===track)?.keyframes;}
        finishInput(){
            if(this.inputBefore){this.history.record(this.inputBefore,this.data);this.inputBefore=null;this.updateButtons();}
        }
        mutate(fn){
            if(this.job || this.closed)return;
            this.finishInput();this.pause();const before=clone(this.data);fn();this.history.record(before,this.data);this.persist();this.refresh();
        }
        undo(direction){
            if(this.job)return;
            this.finishInput();this.pause();const restored=this.history.restore(this.data,direction);if(!restored)return;
            this.data=restored;this.cameraDraft=null;this.selectedKey=null;this.ensureSelection();this.persist();this.refresh();
        }
        ensureSelection(){
            const valid=this.selection.kind==='camera'||(this.selection.kind==='piece'?this.piece():this.prop());
            if(!valid)this.selection=this.data.pieces[0] ? {kind:'piece',id:this.data.pieces[0].id} : {kind:'camera',id:'camera'};
            if(this.selection.kind==='piece')this.data.selectedPieceId=this.selection.id;
        }
        select(kind,id){this.finishInput();this.selection={kind,id};this.ensureSelection();this.selectedKey=null;this.refresh();}
        cameraFrame(draft=true){
            const f={...keyframeAt(this.data.camera.keyframes,this.data.playhead),t:this.data.playhead};
            const follow=this.data.pieces.find(p=>p.id===this.data.followPieceId);
            if(follow){const p=keyframeAt(follow.keyframes,this.data.playhead);f.targetX=p.x;f.targetZ=p.z;}
            return draft && this.cameraDraft ? {...this.cameraDraft,t:this.data.playhead} : f;
        }
        refresh(){
            if(this.closed)return;
            this.ensureSelection();this.renderObjects();this.renderInspector();this.renderTimeline();
            this.$('[data-director-ratio]').value=this.data.ratio;
            this.$('[data-director-duration]').value=this.data.duration;
            this.$('[data-director-follow]').innerHTML='<option value="">不跟随</option>'+this.data.pieces.map(p=>`<option value="${escape(p.id)}">${escape(p.name)}</option>`).join('');
            this.$('[data-director-follow]').value=this.data.followPieceId;
            this.syncMeshes();this.render();this.updateButtons();
        }
        renderObjects(){
            const row=(kind,id,name,color)=>`<button type="button" class="director-object ${this.selection.kind===kind&&this.selection.id===id?'is-active':''}" data-object-kind="${kind}" data-object-id="${escape(id)}" data-edit-control><span class="director-object-dot" style="background:${color}"></span><span>${escape(name)}</span><small>${kind==='piece'?'角色':kind==='prop'?'白块':'镜头'}</small></button>`;
            this.$('[data-director-object-list]').innerHTML=this.data.pieces.map(p=>row('piece',p.id,p.name,p.color)).join('')+this.data.props.map(p=>row('prop',p.id,p.name,'#cbd5e1')).join('')+row('camera','camera','导演镜头','#a78bfa');
            this.$('[data-director-count]').textContent=this.data.pieces.length+this.data.props.length;
        }
        renderInspector(){
            const kind=this.selection.kind,object=kind==='piece'?this.piece():this.prop();
            let html='';
            if(kind==='camera'){
                html=`<div class="director-section-head"><b>导演镜头</b><span data-director-inspector-time></span></div><div class="director-grid3">${field('位置 X','camera.x')}${field('位置 Y','camera.y')}${field('位置 Z','camera.z')}</div><div class="director-grid3">${field('目标 X','camera.targetX')}${field('目标 Y','camera.targetY')}${field('目标 Z','camera.targetZ')}</div>${field('视场角（°）','camera.fov','number','min="20" max="80"')}<p class="director-help">导演视角中拖动镜头为临时预览，点击下方按钮写入当前时间。</p>${button('camera-keyframe','＋ 镜头关键帧','class="director-primary" data-edit-control')}`;
            }else if(object){
                html=`<div class="director-section-head"><b>${kind==='piece'?'棋子属性':'白块属性'}</b><span data-director-inspector-time></span></div>${field('名称',`${kind}.name`,'text','maxlength="80"')}`;
                if(kind==='piece')html+=`${field('颜色','piece.color','color')}<div class="director-grid3">${field('位置 X','piece.x')}${field('位置 Z','piece.z')}${field('朝向（°）','piece.yaw')}</div>${button('piece-keyframe','＋ 走位关键帧','class="director-primary" data-edit-control')}<p class="director-help">移动棋子或修改位置会写入当前时间的关键帧。</p>`;
                else html+=`<div class="director-grid2">${field('位置 X','prop.x')}${field('位置 Z','prop.z')}</div><div class="director-grid3">${field('宽度','prop.w','number','min="0.1" max="100"')}${field('高度','prop.h','number','min="0.1" max="100"')}${field('深度','prop.d','number','min="0.1" max="100"')}</div><p class="director-help">白块为静态布景，尺寸与位置对整个预演生效。</p>`;
            }
            this.$('[data-director-inspector]').innerHTML=`<section class="director-section">${html}</section>`;
            this.syncInputs();
        }
        renderTimeline(){
            const tracks=[...this.data.pieces.map(p=>({id:p.id,name:p.name})),{id:'camera',name:'导演镜头'}],duration=this.data.duration;
            const ticks=Array.from({length:6},(_,i)=>`<span style="left:${i*20}%">${(duration*i/5).toFixed(1)}s</span>`).join('');
            this.$('[data-director-tracks]').innerHTML=`<div class="director-track-row director-ruler-row"><span>关键帧轨道</span><div class="director-ruler">${ticks}</div></div>`+tracks.map(track=>`<div class="director-track-row"><button type="button" class="director-track-label" data-track-select="${escape(track.id)}" data-edit-control>${escape(track.name)}</button><div class="director-track" data-track="${escape(track.id)}" aria-label="${escape(track.name)}关键帧轨道"><span class="director-track-head" data-track-head></span>${this.frames(track.id).map((f,i)=>`<button type="button" class="director-key ${this.selectedKey?.track===track.id&&Math.abs(this.selectedKey.t-f.t)<.02?'is-active':''}" style="left:${clamp(f.t/duration,0,1)*100}%" data-key-track="${escape(track.id)}" data-key-index="${i}" data-edit-control aria-label="${escape(track.name)} ${time(f.t)} 关键帧${f.t>duration?'（超出时长）':''}" title="${time(f.t)} · 拖动调整时间">◆</button>`).join('')}</div></div>`).join('');
            this.syncTime();
        }
        syncInputs(){
            if(this.closed)return;
            const p=this.piece(),prop=this.prop(),frame=p?keyframeAt(p.keyframes,this.data.playhead):{},camera=this.cameraFrame();
            this.root.querySelectorAll('[data-field]').forEach(el=>{
                if(document.activeElement===el)return;
                const [kind,key]=el.dataset.field.split('.');
                el.value=kind==='camera'?number(camera[key]):kind==='prop'?(prop?.[key]??''):key==='name'||key==='color'?(p?.[key]??''):number(frame[key]);
            });
        }
        syncTime(){
            if(this.closed)return;
            this.root.querySelectorAll('[data-director-time]').forEach(el=>el.textContent=`${time(this.data.playhead)} / ${time(this.data.duration)}`);
            const inspectorTime=this.$('[data-director-inspector-time]');if(inspectorTime)inspectorTime.textContent=`当前时间 ${time(this.data.playhead)}`;
            const slider=this.$('[data-director-playhead]');slider.max=this.data.duration;slider.value=this.data.playhead;
            this.root.querySelectorAll('[data-track-head]').forEach(el=>el.style.left=`${this.data.playhead/this.data.duration*100}%`);
        }
        updateButtons(){
            if(this.closed)return;
            const busy=Boolean(this.job),ready=Boolean(this.three);
            this.root.querySelectorAll('[data-edit-control],[data-field]').forEach(el=>el.disabled=busy);
            this.root.querySelectorAll('[data-export-control]').forEach(el=>el.disabled=busy||!ready);
            this.$('[data-director-action="undo"]').disabled=busy||!this.history.undo.length;
            this.$('[data-director-action="redo"]').disabled=busy||!this.history.redo.length;
            for(const action of ['delete-object','duplicate'])this.$(`[data-director-action="${action}"]`).disabled=busy||this.selection.kind==='camera';
            this.$('[data-director-action="delete-keyframe"]').disabled=busy||!this.selectedKey||((this.frames(this.selectedKey?.track)||[]).length<2);
            this.$('[data-director-action="play"]').textContent=this.playing?'暂停':'播放';
            this.$('[data-director-export-progress]').hidden=!busy;
            for(const mode of ['edit','director'])this.$(`[data-director-action="mode-${mode}"]`).classList.toggle('is-active',this.viewMode===mode);
            this.$('[data-director-mode-hint]').textContent=this.viewMode==='edit'?'左键移动对象 · 右键旋转 · Shift 拖动平移 · 滚轮缩放 · 紫色框为导演镜头':'当前为导出镜头 · 拖动旋转，滚轮推进 · 点击“镜头关键帧”保存运镜';
        }
        pause(){this.playing=false;cancelAnimationFrame(this.playRaf);if(!this.closed)this.updateButtons();}
        play(){
            if(this.job||!this.three)return;
            if(this.playing){this.pause();return;}
            this.finishInput();this.playing=true;this.cameraDraft=null;let last=performance.now();
            const tick=now=>{
                if(this.closed||!this.playing)return;
                this.data.playhead=(this.data.playhead+Math.min(.1,(now-last)/1000))%this.data.duration;last=now;
                this.render();this.playRaf=requestAnimationFrame(tick);
            };
            this.updateButtons();this.playRaf=requestAnimationFrame(tick);
        }
        seek(t){if(this.job)return;this.pause();this.data.playhead=clamp(t,0,this.data.duration);this.cameraDraft=null;this.directorOrbit=null;this.render();}
        action(action){
            if(action==='close'){this.close();return;}
            if(action==='cancel-export'){this.cancelExport();return;}
            if(this.job)return;
            if(action==='undo'||action==='redo')return this.undo(action);
            if(action==='mode-edit'||action==='mode-director'){
                this.viewMode=action==='mode-edit'?'edit':'director';this.cameraDraft=null;this.directorOrbit=orbitFromFrame(this.cameraFrame(false));this.render();this.updateButtons();return;
            }
            if(action==='reset-view'){
                if(this.viewMode==='edit')this.editOrbit=orbitFromFrame({x:10,y:10,z:10,targetX:0,targetY:0,targetZ:0,fov:45});
                else{this.cameraDraft=null;this.directorOrbit=orbitFromFrame(this.cameraFrame(false));}
                this.render();return;
            }
            if(action==='play')return this.play();
            if(action==='start')return this.seek(0);
            if(action==='png')return this.exportPng();
            if(action==='webm')return this.exportWebm();
            this.mutate(()=>{
                if(action==='add-piece'){
                    const piece={id:uid('piece'),name:`棋子 ${this.data.pieces.length+1}`,color:['#3b82f6','#22c55e','#f59e0b','#a855f7'][this.data.pieces.length%4],keyframes:[{t:this.data.playhead,x:0,z:0,yaw:0}]};
                    this.data.pieces.push(piece);this.selection={kind:'piece',id:piece.id};
                }else if(action==='add-block'){
                    const prop={id:uid('block'),name:`白块 ${this.data.props.length+1}`,x:0,z:0,w:1.4,d:1,h:1};this.data.props.push(prop);this.selection={kind:'prop',id:prop.id};
                }else if(action==='duplicate'){
                    const value=this.selection.kind==='piece'?this.piece():this.prop();if(!value)return;
                    const copy=clone(value);copy.id=uid(this.selection.kind);copy.name+=' 副本';
                    if(this.selection.kind==='piece'){copy.keyframes.forEach(f=>f.x+=1);this.data.pieces.push(copy);}else{copy.x+=1;this.data.props.push(copy);}
                    this.selection.id=copy.id;
                }else if(action==='delete-object'){
                    const key=this.selection.kind==='piece'?'pieces':'props';if(this.selection.kind==='camera')return;
                    this.data[key]=this.data[key].filter(p=>p.id!==this.selection.id);
                    if(this.data.followPieceId===this.selection.id)this.data.followPieceId='';this.selectedKey=null;this.ensureSelection();
                }else if(action==='piece-keyframe'){
                    const p=this.piece();if(p)setKeyframe(p.keyframes,{...keyframeAt(p.keyframes,this.data.playhead),t:this.data.playhead});
                }else if(action==='camera-keyframe'){setKeyframe(this.data.camera.keyframes,{...this.cameraFrame(),t:this.data.playhead});this.cameraDraft=null;}
                else if(action==='delete-keyframe'&&this.selectedKey){
                    const frames=this.frames(this.selectedKey.track);
                    if(frames?.length>1){const i=frames.findIndex(f=>Math.abs(f.t-this.selectedKey.t)<.02);if(i>=0)frames.splice(i,1);}
                    this.selectedKey=null;
                }
            });
        }
        editField(el){
            if(this.job)return;
            if(!this.inputBefore)this.inputBefore=clone(this.data);
            const [kind,key]=el.dataset.field.split('.'),value=el.type==='number'?number(el.value):el.value;
            if(kind==='piece'){
                const p=this.piece();if(!p)return;
                if(key==='name'||key==='color')p[key]=value;
                else setKeyframe(p.keyframes,{...keyframeAt(p.keyframes,this.data.playhead),t:this.data.playhead,[key]:value});
            }else if(kind==='prop'){
                const p=this.prop();if(!p)return;p[key]=['w','h','d'].includes(key)?clamp(value,.1,100):value;
            }else if(kind==='camera'){
                setKeyframe(this.data.camera.keyframes,{...this.cameraFrame(),t:this.data.playhead,[key]:key==='fov'?clamp(value,20,80):value});this.cameraDraft=null;this.directorOrbit=null;
            }
            if(key==='name'||key==='color'){
                this.root.querySelectorAll('[data-object-id]').forEach(row=>{
                    if(row.dataset.objectId!==this.selection.id)return;
                    if(key==='name')row.children[1].textContent=value;
                    else row.children[0].style.background=value;
                });
            }
            this.renderTimeline();
            this.syncMeshes();this.render();this.persist();
        }
        bind(){
            this.on(this.root,'click',e=>{
                const action=e.target.closest('[data-director-action]')?.dataset.directorAction;
                if(action){e.preventDefault();this.action(action);return;}
                const object=e.target.closest('[data-object-kind]');if(object&&!this.job)this.select(object.dataset.objectKind,object.dataset.objectId);
                const track=e.target.closest('[data-track-select]');if(track&&!this.job)this.select(track.dataset.trackSelect==='camera'?'camera':'piece',track.dataset.trackSelect);
            });
            this.on(this.root,'focusin',e=>{if(e.target.matches('[data-field]')&&!this.inputBefore)this.inputBefore=clone(this.data);});
            this.on(this.root,'focusout',e=>{if(e.target.matches('[data-field]')){this.finishInput();this.syncInputs();}});
            this.on(this.root,'input',e=>{
                const el=e.target;
                if(el.matches('[data-director-playhead]'))this.seek(number(el.value));
                else if(el.matches('[data-field]'))this.editField(el);
            });
            this.on(this.root,'change',e=>{
                const el=e.target;if(this.job)return;
                if(el.matches('[data-director-ratio]'))this.mutate(()=>this.data.ratio=el.value);
                else if(el.matches('[data-director-duration]'))this.mutate(()=>{M.resizeDuration(this.data,number(el.value,this.data.duration));this.cameraDraft=null;this.selectedKey=null;});
                else if(el.matches('[data-director-follow]'))this.mutate(()=>{this.data.followPieceId=el.value;this.cameraDraft=null;});
            });
            this.on(document,'keydown',e=>this.keydown(e),{capture:true});
            this.on(window,'pagehide',()=>this.close());
            this.bindTimeline();
        }
        keydown(e){
            if(this.closed||e.isComposing)return;
            const input=e.target.closest('input,textarea,select,[contenteditable="true"]');
            if(e.key==='Tab'){
                const items=[...this.root.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled)')].filter(el=>el.getClientRects().length);
                if(!items.includes(document.activeElement)){e.preventDefault();(e.shiftKey?items.at(-1):items[0])?.focus();}
                else if(e.shiftKey&&document.activeElement===items[0]){e.preventDefault();items.at(-1)?.focus();}
                else if(!e.shiftKey&&document.activeElement===items.at(-1)){e.preventDefault();items[0]?.focus();}
            }
            e.stopImmediatePropagation();
            if(input)return;
            if(e.repeat)return;
            const key=e.key.toLowerCase();
            if(key==='escape'){e.preventDefault();if(this.job)this.cancelExport();else this.close();}
            else if(key===' '){e.preventDefault();this.play();}
            else if((e.ctrlKey||e.metaKey)&&key==='z'){e.preventDefault();this.undo(e.shiftKey?'redo':'undo');}
            else if((e.ctrlKey||e.metaKey)&&key==='y'){e.preventDefault();this.undo('redo');}
            else if((key==='delete'||key==='backspace')&&!this.job){e.preventDefault();this.action(this.selectedKey?'delete-keyframe':'delete-object');}
        }
        bindTimeline(){
            this.on(this.root,'pointerdown',e=>{
                const track=e.target.closest('[data-track]');if(!track||this.job||e.button!==0)return;
                this.finishInput();this.pause();const key=e.target.closest('[data-key-index]'),rect=track.getBoundingClientRect();
                const trackId=track.dataset.track,frames=this.frames(trackId),index=key?Number(key.dataset.keyIndex):-1;
                this.timelineDrag={pointerId:e.pointerId,track:trackId,index,keyElement:key,before:clone(this.data),rect,changed:false};
                track.setPointerCapture(e.pointerId);
                if(key){this.root.querySelectorAll('.director-key.is-active').forEach(el=>el.classList.remove('is-active'));key.classList.add('is-active');this.selectedKey={track:trackId,t:frames[index].t};this.data.playhead=clamp(frames[index].t,0,this.data.duration);this.cameraDraft=null;}
                else{this.selectedKey=null;this.data.playhead=clamp((e.clientX-rect.left)/rect.width*this.data.duration,0,this.data.duration);this.cameraDraft=null;}
                this.render();this.updateButtons();e.preventDefault();
            });
            this.on(this.root,'pointermove',e=>{
                const drag=this.timelineDrag;if(!drag||drag.pointerId!==e.pointerId)return;
                const t=clamp((e.clientX-drag.rect.left)/drag.rect.width*this.data.duration,0,this.data.duration);
                if(drag.index>=0){
                    const frames=this.frames(drag.track),current=frames.findIndex(f=>Math.abs(f.t-this.selectedKey.t)<.02);
                    if(M.moveKeyframe(frames,current,t,this.data.duration)){this.selectedKey.t=Math.round(t*100)/100;drag.changed=true;}
                    drag.keyElement.style.left=`${this.selectedKey.t/this.data.duration*100}%`;
                }
                this.data.playhead=t;this.cameraDraft=null;this.directorOrbit=null;this.render();
            });
            const finish=e=>{
                const drag=this.timelineDrag;if(!drag||drag.pointerId!==e.pointerId)return;
                this.timelineDrag=null;
                if(e.type==='pointercancel'){this.data=drag.before;this.node.director=this.data;this.selectedKey=null;}
                else if(drag.changed){this.history.record(drag.before,this.data);this.persist();}
                this.renderTimeline();this.render();this.updateButtons();
            };
            this.on(this.root,'pointerup',finish);this.on(this.root,'pointercancel',finish);
        }
        async init(){
            try{
                if(!threePromise)threePromise=import('/static/vendor/js/three-0.160.0.module.js').catch(err=>{threePromise=null;throw err;});
                const THREE=await threePromise;if(this.closed)return;
                const scene=new THREE.Scene();scene.background=new THREE.Color('#eef1f6');
                const camera=new THREE.PerspectiveCamera(45,16/9,.1,400),editCamera=new THREE.PerspectiveCamera(45,16/9,.1,400);
                const renderer=new THREE.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});renderer.setPixelRatio(1);renderer.shadowMap.enabled=true;
                this.three={THREE,scene,camera,editCamera,renderer,meshes:new Map(),propsMeshes:new Map(),raycaster:new THREE.Raycaster(),pointer:new THREE.Vector2(),size:''};
                const host=this.$('[data-director-canvas]');host.replaceChildren(renderer.domElement);
                const ground=new THREE.Mesh(new THREE.PlaneGeometry(80,80),new THREE.MeshStandardMaterial({color:'#f8fafc',roughness:1}));ground.rotation.x=-Math.PI/2;ground.receiveShadow=true;scene.add(ground);
                const grid=new THREE.GridHelper(40,40,0x94a3b8,0xd5dce7);grid.position.y=.005;scene.add(grid);
                scene.add(new THREE.HemisphereLight(0xffffff,0x94a3b8,2));const sun=new THREE.DirectionalLight(0xffffff,2);sun.position.set(5,8,6);sun.castShadow=true;scene.add(sun);
                const helperCamera=camera.clone(),helper=new THREE.CameraHelper(helperCamera),guideColor=new THREE.Color('#a78bfa');helper.setColors(guideColor,guideColor,guideColor,guideColor,guideColor);scene.add(helper);Object.assign(this.three,{ground,helper,helperCamera,grid});
                this.syncMeshes();this.bindCanvas();this.resizeObserver=new ResizeObserver(()=>this.render());this.resizeObserver.observe(host);this.render();this.updateButtons();
            }catch(error){
                if(!this.closed){this.$('[data-director-canvas]').textContent=`3D 场景加载失败：${error.message||error}`;this.notify('3D 场景无法加载，可关闭后重试。');}
                this.disposeScene();
            }
        }
        syncMeshes(){
            if(!this.three)return;
            const {THREE,scene,meshes,propsMeshes}=this.three;
            const sync=(items,map,make)=>{
                for(const [id,mesh]of map)if(!items.some(p=>p.id===id)){scene.remove(mesh);mesh.geometry.dispose();mesh.material.dispose();map.delete(id);}
                for(const item of items){
                    let mesh=map.get(item.id);
                    if(!mesh){mesh=make(item);mesh.castShadow=true;mesh.receiveShadow=true;scene.add(mesh);map.set(item.id,mesh);}
                    if(item.color)mesh.material.color.set(item.color);
                    mesh.material.emissive.set(this.selection.id===item.id?'#182238':'#000000');
                    if(!item.keyframes)mesh.scale.set(item.w,item.h,item.d);
                }
            };
            sync(this.data.pieces,meshes,p=>{const mesh=new THREE.Mesh(new THREE.CylinderGeometry(.35,.55,1.2,28),new THREE.MeshStandardMaterial({color:p.color,roughness:.72}));mesh.userData={kind:'piece',id:p.id};return mesh;});
            sync(this.data.props,propsMeshes,p=>{const mesh=new THREE.Mesh(new THREE.BoxGeometry(1,1,1),new THREE.MeshStandardMaterial({color:'#ffffff',roughness:.9}));mesh.userData={kind:'prop',id:p.id};return mesh;});
        }
        applyCamera(camera,f,aspect){
            camera.position.set(f.x,f.y,f.z);camera.lookAt(f.targetX,f.targetY,f.targetZ);
            if(camera.fov!==f.fov||camera.aspect!==aspect){camera.fov=f.fov;camera.aspect=aspect;camera.updateProjectionMatrix();}
            camera.updateMatrixWorld();
        }
        render(exportFrame=false){
            if(this.closed)return;
            exportFrame=exportFrame||Boolean(this.job);
            if(this.three){
                const {THREE,camera,editCamera,renderer,scene,meshes,propsMeshes,helper,helperCamera,grid}=this.three,[w,h]=M.ratioSizes[this.data.ratio],size=`${w}:${h}`;
                if(size!==this.three.size){renderer.setSize(w,h,false);this.three.size=size;renderer.domElement.style.aspectRatio=`${w}/${h}`;}
                const host=this.$('[data-director-canvas]'),width=Math.max(1,Math.min(host.clientWidth,host.clientHeight*w/h));
                renderer.domElement.style.width=`${width}px`;renderer.domElement.style.height=`${width*h/w}px`;
                this.data.pieces.forEach(p=>{const mesh=meshes.get(p.id),f=keyframeAt(p.keyframes,this.data.playhead);if(mesh){mesh.position.set(f.x,.6,f.z);mesh.rotation.y=THREE.MathUtils.degToRad(f.yaw);mesh.material.emissive.set(!exportFrame&&this.selection.id===p.id?'#182238':'#000000');}});
                this.data.props.forEach(p=>{const mesh=propsMeshes.get(p.id);if(mesh){mesh.position.set(p.x,p.h/2,p.z);mesh.material.emissive.set(!exportFrame&&this.selection.id===p.id?'#182238':'#000000');}});
                this.applyCamera(camera,this.cameraFrame(!exportFrame),w/h);this.applyCamera(editCamera,orbitFrame(this.editOrbit),w/h);
                const f=this.cameraFrame(!exportFrame);helperCamera.copy(camera);helperCamera.far=clamp(Math.hypot(f.x-f.targetX,f.y-f.targetY,f.z-f.targetZ)+2,3,100);helperCamera.updateProjectionMatrix();
                helper.visible=!exportFrame&&this.viewMode==='edit';grid.visible=!exportFrame&&this.viewMode==='edit';helper.update();
                renderer.render(scene,exportFrame||this.viewMode==='director'?camera:editCamera);
            }
            this.syncTime();this.syncInputs();
        }
        groundPoint(e){
            const {THREE,raycaster,pointer,renderer,editCamera}=this.three,rect=renderer.domElement.getBoundingClientRect();
            pointer.set((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1);raycaster.setFromCamera(pointer,editCamera);
            return raycaster.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0,1,0),0),new THREE.Vector3());
        }
        bindCanvas(){
            const canvas=this.three.renderer.domElement;
            this.on(canvas,'contextmenu',e=>e.preventDefault());
            this.on(canvas,'pointerdown',e=>{
                if(this.job||e.button>2)return;
                this.finishInput();this.pause();let mode=e.shiftKey||e.button===1?'pan':'orbit',item=null,offset=null;
                if(e.button===0&&!e.shiftKey&&this.viewMode==='edit'){
                    const {raycaster,pointer,editCamera,meshes,propsMeshes}=this.three,rect=canvas.getBoundingClientRect();
                    pointer.set((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1);raycaster.setFromCamera(pointer,editCamera);
                    const hit=raycaster.intersectObjects([...meshes.values(),...propsMeshes.values()])[0];
                    if(hit){const {kind,id}=hit.object.userData;this.select(kind,id);item=kind==='piece'?this.piece():this.prop();mode=kind;
                        const p=this.groundPoint(e),f=kind==='piece'?keyframeAt(item.keyframes,this.data.playhead):item;if(p)offset={x:f.x-p.x,z:f.z-p.z};}
                }
                if(this.viewMode==='director'&&!this.directorOrbit)this.directorOrbit=orbitFromFrame(this.cameraFrame());
                this.pointerDrag={pointerId:e.pointerId,mode,x:e.clientX,y:e.clientY,id:item?.id,offset,before:clone(this.data),draft:this.cameraDraft?clone(this.cameraDraft):null,changed:false};
                canvas.setPointerCapture(e.pointerId);e.preventDefault();
            });
            this.on(canvas,'pointermove',e=>{
                const drag=this.pointerDrag;if(!drag||drag.pointerId!==e.pointerId)return;
                const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(!dx&&!dy)return;
                if(drag.mode==='orbit'||drag.mode==='pan'){
                    const o=this.viewMode==='director'?this.directorOrbit:this.editOrbit;
                    if(drag.mode==='orbit'){o.theta-=dx*.009;o.phi=clamp(o.phi+dy*.009,.08,Math.PI-.08);}
                    else{const scale=o.radius*.0015;o.targetX+=(-dx*Math.cos(o.theta)-dy*Math.sin(o.theta))*scale;o.targetZ+=(dx*Math.sin(o.theta)-dy*Math.cos(o.theta))*scale;}
                    if(this.viewMode==='director')this.cameraDraft={...this.cameraFrame(false),...orbitFrame(o)};
                }else{
                    const p=this.groundPoint(e);if(!p)return;const x=Number((p.x+(drag.offset?.x||0)).toFixed(2)),z=Number((p.z+(drag.offset?.z||0)).toFixed(2));
                    if(drag.mode==='piece'){const item=this.data.pieces.find(p=>p.id===drag.id);setKeyframe(item.keyframes,{...keyframeAt(item.keyframes,this.data.playhead),t:this.data.playhead,x,z});}
                    else{const item=this.data.props.find(p=>p.id===drag.id);item.x=x;item.z=z;}
                }
                drag.x=e.clientX;drag.y=e.clientY;drag.changed=true;this.render();
            });
            const finish=e=>{
                const drag=this.pointerDrag;if(!drag||drag.pointerId!==e.pointerId)return;this.pointerDrag=null;
                if(e.type==='pointercancel'){this.data=drag.before;this.node.director=this.data;this.cameraDraft=drag.draft;}
                else if(drag.changed&&['piece','prop'].includes(drag.mode)){this.history.record(drag.before,this.data);this.persist();}
                this.renderTimeline();this.render();this.updateButtons();
            };
            this.on(canvas,'pointerup',finish);this.on(canvas,'pointercancel',finish);
            this.on(canvas,'wheel',e=>{
                if(this.job)return;e.preventDefault();this.pause();
                if(this.viewMode==='director'&&!this.directorOrbit)this.directorOrbit=orbitFromFrame(this.cameraFrame());
                const o=this.viewMode==='director'?this.directorOrbit:this.editOrbit;o.radius=clamp(o.radius*Math.exp(clamp(e.deltaY,-120,120)*.0015),1,100);
                if(this.viewMode==='director')this.cameraDraft={...this.cameraFrame(false),...orbitFrame(o)};this.render();
            },{passive:false});
        }
        beginJob(kind){
            if(this.job||!this.three||this.closed)return null;
            this.finishInput();const wasPlaying=this.playing;this.pause();
            const job={kind,cancelled:false,original:this.data.playhead,draft:this.cameraDraft?clone(this.cameraDraft):null,wasPlaying,raf:0,stream:null,recorder:null};
            this.job=job;this.updateButtons();this.progress(0,kind==='png'?'正在导出当前镜头…':'正在录制预演…');return job;
        }
        progress(value,label){if(this.closed)return;this.$('[data-director-export-progress] progress').value=value;this.status(`${label} ${Math.round(value)}%`);}
        validJob(job){return !this.closed&&this.job===job&&!job.cancelled;}
        stopTracks(job){job.stream?.getTracks().forEach(track=>track.stop());}
        finishJob(job){
            cancelAnimationFrame(job.raf);this.stopTracks(job);
            if(this.job!==job)return;
            this.job=null;if(this.closed)return;
            this.data.playhead=job.original;this.cameraDraft=job.draft;this.render();this.updateButtons();
            if(job.wasPlaying)this.play();
        }
        async saveBlob(job,blob){
            if(!this.validJob(job))return;
            const name=`director-${Date.now()}.${job.kind==='png'?'png':'webm'}`,kind=job.kind==='png'?'image':'video';
            this.progress(98,'正在保存到画布…');
            const files=await this.api.upload([new File([blob],name,{type:blob.type})]);
            if(!this.validJob(job))return;
            const item=files?.[0];if(!item?.url)throw new Error('输出保存失败，请重试');
            const output={...item,kind,name:item.name||name,generatedResult:true};
            if(!this.api.createOutput?.(output))throw new Error('未能在画布创建导出节点');
            this.data.exports.push({url:output.url,name:output.name,kind,createdAt:Date.now()});this.persist();this.notify(`${job.kind.toUpperCase()} 已在导演台右侧创建输出节点`);
        }
        async exportPng(){
            const job=this.beginJob('png');if(!job)return;
            try{
                this.render(true);const blob=await new Promise(resolve=>this.three.renderer.domElement.toBlob(resolve,'image/png'));
                if(this.validJob(job)){if(!blob)throw new Error('PNG 导出失败');await this.saveBlob(job,blob);}
            }catch(error){if(this.validJob(job))this.notify(error.message||'PNG 保存失败');}
            finally{this.finishJob(job);}
        }
        exportWebm(){
            const job=this.beginJob('webm');if(!job)return;
            try{
                if(typeof MediaRecorder==='undefined'||!this.three.renderer.domElement.captureStream)throw new Error('当前环境不支持 WebM，请使用 PNG 导出。');
                const mime=['video/webm;codecs=vp9','video/webm;codecs=vp8','video/webm'].find(type=>MediaRecorder.isTypeSupported(type));
                if(!mime)throw new Error('当前环境没有可用的 WebM 编码器，请使用 PNG 导出。');
                this.data.playhead=0;this.render(true);job.stream=this.three.renderer.domElement.captureStream(24);
                const recorder=job.recorder=new MediaRecorder(job.stream,{mimeType:mime,videoBitsPerSecond:4000000}),chunks=[];
                recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
                recorder.onstop=async()=>{
                    this.stopTracks(job);
                    try{if(this.validJob(job)){if(!chunks.length)throw new Error('录制没有生成视频数据');await this.saveBlob(job,new Blob(chunks,{type:mime}));}}
                    catch(error){if(this.validJob(job))this.notify(error.message||'WebM 保存失败');}
                    finally{this.finishJob(job);}
                };
                recorder.onerror=()=>{if(!this.validJob(job))return;this.notify('视频录制失败，请重试或导出 PNG。');this.cancelExport();};
                recorder.start(250);const start=performance.now(),duration=this.data.duration;
                const tick=now=>{
                    if(!this.validJob(job))return;
                    const elapsed=(now-start)/1000;this.data.playhead=clamp(elapsed,0,duration);this.render(true);this.progress(Math.min(95,elapsed/duration*95),'正在录制预演…');
                    if(elapsed>=duration){if(recorder.state!=='inactive')recorder.stop();}else job.raf=requestAnimationFrame(tick);
                };
                job.raf=requestAnimationFrame(tick);
            }catch(error){this.notify(error.message||'WebM 导出失败');job.cancelled=true;this.finishJob(job);}
        }
        cancelExport(){
            const job=this.job;if(!job)return;job.cancelled=true;cancelAnimationFrame(job.raf);
            if(job.recorder?.state!=='inactive'&&job.recorder){try{job.recorder.stop();}catch{}}
            this.stopTracks(job);this.finishJob(job);this.status('已取消导出，预演内容已保留');
        }
        disposeScene(){
            const scene=this.three?.scene;if(!scene)return;
            const geometries=new Set(),materials=new Set();scene.traverse(object=>{if(object.geometry)geometries.add(object.geometry);if(object.material)for(const material of Array.isArray(object.material)?object.material:[object.material])materials.add(material);object.shadow?.map?.dispose?.();});
            geometries.forEach(g=>g.dispose());materials.forEach(m=>m.dispose());this.three.renderer.dispose();this.three.renderer.forceContextLoss();this.three=null;
        }
        close(){
            if(this.closed)return;this.finishInput();this.pause();this.cancelExport();this.pause();this.persist();this.closed=true;
            this.resizeObserver?.disconnect();this.listeners.abort();this.disposeScene();this.root.remove();
            if(active===this)active=null;if(this.returnFocus?.isConnected)this.returnFocus.focus({preventScroll:true});
        }
    }
    window.SmartDirector={open(node,api){active?.close();active=new DirectorSession(node,api);},close(){active?.close();},normalize:M.normalize,ratioSizes:M.ratioSizes};
})();
