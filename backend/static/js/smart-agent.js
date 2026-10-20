(function (root) {
    'use strict';
    const labels = { planning:'规划中', awaiting_approval:'待确认', waiting_approval:'待确认', waiting_user:'待处理', running:'执行中', stopping:'停止后处理中', stopped:'已停止', completed:'已完成', partial:'部分完成', failed:'失败', needs_attention:'待处理', interrupted:'已中断', pending:'待执行', succeeded:'成功', submitted:'已提交', unknown:'结果未知', blocked:'依赖未完成' };
    const actionLabels = {approve:'批准此计划', stop:'停止后续任务', continue:'继续 / 恢复查询', retry:'重试失败步骤', repair_delivery:'将已有成果放入画布', undo:'撤销本次画布操作'};
    const active = new Set(['planning','running','stopping']);
    Object.assign(labels,{needs_input:'待补充条件',submitting:'正在提交',waiting:'生成中',generated:'生成完成',delivering:'放入画布中',delivery_failed:'成果交付失败'});
    function isToggleShortcut(event) { return Boolean(!event.isComposing && !event.repeat && !event.altKey && (event.ctrlKey || event.metaKey) && event.shiftKey && String(event.key || '').toLowerCase() === 'a'); }
    function shouldPoll(run) {
        return active.has(run?.status) || (['needs_attention','interrupted','partial','stopped'].includes(run?.status) && (run.steps || []).some(s => ['waiting','generated','delivering'].includes(s.status)));
    }
    function actionsOf(run) { return (run?.actions || []).map(a => typeof a === 'string' ? a : a.action || a.id).filter(a => Object.hasOwn(actionLabels, a)); }
    function nodeIdsOf(run) { return [...new Set([...(run?.outputs || []), ...(run?.steps || [])].flatMap(o => o.node_ids || (o.node_id ? [o.node_id] : [])))]; }
    function statusOf(status) { return labels[status] || status || '尚未开始'; }
    function modelsOf(provider) { return (provider?.chat_models || []).map(m => typeof m === 'string' ? {id:m, name:m} : {id:m.id || m.name, name:m.name || m.id}).filter(m => m.id); }
    function questionsOf(run) {
        if(run?.status!=='needs_input')return [];
        const questions=(run.plan?.questions || []).filter(q=>q && typeof q.question==='string' && q.question.trim()).map((q,i)=>({id:q.id || 'q'+(i+1),question:q.question,options:(q.options || []).filter(c=>typeof c==='string')}));
        return questions.length?questions:[{id:'clarify',question:'助手尚未明确列出需要补充的具体问题。可要求它逐条列明，或直接填写补充内容。',options:['请逐条列出需要我回答的具体问题']}];
    }
    function clarificationText(questions,answers) {
        const lines=questions.filter(q=>String(answers[q.id] || '').trim()).map(q=>`问题：${q.question}\n回答：${String(answers[q.id]).trim()}`);
        return lines.length?'我补充以下内容，请据此重新规划；只追问仍然阻碍执行的必要问题，不要重复询问已回答内容。\n\n'+lines.join('\n\n'):'';
    }
    function progressOf(run) {
        const steps=run?.steps?.length?run.steps:(run?.plan?.steps || []).map(s=>({...s,status:s.blocked_reason?'blocked':'pending'}));
        const count=statuses=>steps.filter(s=>statuses.includes(s.status)).length;
        const total=steps.length,done=count(['completed','succeeded']),processing=count(['submitting','submitted','waiting','generated','delivering']),pending=count(['pending']),failed=count(['failed','delivery_failed']),blocked=count(['blocked']),unknown=count(['unknown']);
        const current=steps.filter(s=>['submitting','submitted','waiting','generated','delivering'].includes(s.status)).map(s=>s.title || s.id);
        const indeterminate=run?.status==='planning';
        return {visible:Boolean(run && (total || ['planning','needs_input','failed','interrupted','needs_attention'].includes(run.status))),total,done,processing,pending,failed,blocked,unknown,current,indeterminate,percent:total?Math.floor(done/total*100):0};
    }
    function formatDetails(settings = {}, metadata = {}) {
        const kind = metadata.kind || settings.apiKind || 'image', video = kind === 'video';
        const types = {image:'图片生成',video:'视频生成',write_prompt:'编写提示词',connect:'连接节点',group:'整理分组',arrange:'排列成果'};
        const lines = ['任务类型：' + (types[kind] || kind)];
        const present = value => value !== undefined && value !== null && value !== '';
        const pick = (...keys) => keys.map(k => settings[k]).find(present);
        const add = (name,value) => { if (present(value)) lines.push(name + '：' + value); };
        const yesNo = value => value === true ? '开启' : value === false ? '关闭' : value;
        const translated = (value, words) => words[value] || value;
        add('平台',metadata.provider || (video ? settings.videoProvider || settings.provider_id : settings.provider_id));
        add('模型',metadata.model || (video ? settings.videoModel || settings.model : settings.model));
        add('数量',metadata.count ?? settings.count);
        if (metadata.references) add('参考素材',metadata.references.length + ' 项');
        if (metadata.dependencies?.length) add('前置步骤',metadata.dependencies.length + ' 项');
        if (['image','video'].includes(kind)) {
            const mode=pick('size_mode','sizeMode');
            const ratio=video ? pick('videoAspect','ratio') : pick('ratio');
            add('画面比例',['source','adapt_input','adapt','input'].includes(mode) || ratio === 'source' ? '适配输入素材' : translated(ratio,{square:'1:1',portrait:'2:3',landscape:'3:2',portrait43:'3:4',landscape43:'4:3',story:'9:16',wide:'16:9',ultrawide:'21:9',ultratall:'9:21',auto:'自动（由模型决定）',adaptive:'随参考素材适配',custom:'自定义'}));
            if (ratio === 'custom') add('自定义比例',pick('customRatio') || (settings.customRatioWidth && settings.customRatioHeight ? settings.customRatioWidth + ':' + settings.customRatioHeight : undefined));
            add('分辨率',video ? pick('videoResolution','resolution') : pick('resolution'));
            if (settings.customWidth && settings.customHeight) add('自定义尺寸',settings.customWidth + ' × ' + settings.customHeight);
            if (settings.width && settings.height && (mode || settings.customSize)) add('像素尺寸',settings.width + ' × ' + settings.height);
            if (video) {
                const duration=pick('videoDuration','duration'); add('时长',present(duration) ? duration + ' 秒' : undefined);
                [['生成音频','videoGenerateAudio','generate_audio'],['首尾帧模式','videoUseFrameRoles','use_frame_roles'],['多模态参考','videoMultimodal','multimodal'],['返回尾帧','videoReturnLastFrame','return_last_frame'],['水印','videoWatermark','watermark'],['固定镜头','videoCameraFixed','camerafixed'],['可信素材','videoTrustedAsset','trusted_asset'],['提示词增强','videoEnhancePrompt','enhance_prompt'],['升采样','videoEnableUpsample','enable_upsample']].forEach(([name,...keys])=>add(name,yesNo(pick(...keys))));
                add('帧率',pick('fps'));
            } else {
                add('质量',translated(pick('quality'),{auto:'自动',low:'低',medium:'中',high:'高'}));
                add('背景',settings.transparent_background ? '透明' : translated(pick('background'),{auto:'自动',opaque:'不透明',transparent:'透明'}));
                if (settings.transparent_background === false) add('透明背景','关闭');
                const selectedModel=metadata.model || settings.model || '';
                add('图片版本',pick('version') || (selectedModel === 'gpt-image-2.5-ext' ? pick('imageVersion') : undefined));
                add('输出格式',pick('output_format') || (settings.outputFormat !== 'png' || selectedModel === 'gpt-image-2-official' ? pick('outputFormat') : undefined));
                add('输出压缩质量',pick('output_compression','outputCompression'));
            }
            add('随机种子',pick('seed'));
            const loop=typeof settings.loop === 'object' ? settings.loop?.mode : settings.loop;
            if (loop && loop !== 'none') add('处理方式',translated(loop,{flat:'逐张处理',group:'按组处理'}));
        }
        return lines.join('\n');
    }
    function canSend(state) { return !state.busy && !state.pending && !shouldPoll(state.run) && (state.run?.status === 'awaiting_approval' || !actionsOf(state.run).some(a => ['approve','continue','retry','repair_delivery'].includes(a))); }
    const helpers = {isToggleShortcut,actionsOf, nodeIdsOf, statusOf, modelsOf, canSend, shouldPoll, formatDetails,progressOf,questionsOf,clarificationText};
    if (typeof module === 'object' && module.exports) module.exports = helpers;
    if (!root.document) return;
    const doc = root.document;
    function el(tag, cls, text) { const n = doc.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = String(text); return n; }
    function button(text, fn, cls) { const b = el('button', cls, text); b.type = 'button'; b.addEventListener('click', fn); return b; }
    function mount() {
        if (doc.getElementById('smartAgentPanel')) return;
        const state = {open:false, busy:false, pending:null, canvas:null, session:null, run:null, messages:[], providers:[], epoch:0, failures:0, refs:[], refsKey:null};
        let pollTimer, contextTimer, unsubscribe, subscribedBridge, disposed = false;
        const requests = new Set();
        const bridge = () => root.smartAgentCanvas;
        const id = () => root.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
        const entry = button('Agent', () => setOpen(!state.open), 'smart-agent-toggle');
        entry.title='Agent 创作助手（Ctrl+Shift+A）'; entry.setAttribute('aria-keyshortcuts','Control+Shift+A Meta+Shift+A');
        entry.setAttribute('aria-controls', 'smartAgentPanel'); entry.setAttribute('aria-expanded', 'false');
        const panel = el('aside', 'smart-agent-panel'); panel.id = 'smartAgentPanel'; panel.hidden = true; panel.setAttribute('aria-label', '智能画布创作助手');
        const header = el('div', 'sa-header'); const heading = el('h2', '', 'Agent 创作助手');
        const close = button('收起', () => setOpen(false)); close.setAttribute('aria-label','收起 Agent 面板，任务继续'); header.append(heading, close);
        const controls = el('div','sa-controls');
        const provider = el('select'); provider.setAttribute('aria-label','Agent 对话平台');
        const model = el('select'); model.setAttribute('aria-label','Agent 对话模型');
        const sessions = el('select'); sessions.setAttribute('aria-label','当前画布会话');
        const newSession = button('新会话', () => guarded(createSession));
        controls.append(provider, model);
        const sessionBar=el('div','sa-session-bar');sessionBar.append(sessions,newSession);
        const agentOptions=el('div','sa-agent-options sa-controls');
        const persona=el('select'), concurrency=el('select');
        persona.setAttribute('aria-label','创作人设');concurrency.setAttribute('aria-label','图片并发数量');
        let roles=[{id:'general',name:'通用创作助手'},{id:'ecommerce',name:'电商图片大师'},{id:'video',name:'视频生成大师'}], optionsDirty=false, preferredPersona='general', roleLoadRevision=0, editingRole=null;
        roles.forEach(r=>{const o=el('option','',r.name);o.value=r.id;persona.append(o);});
        [1,2,3,4].forEach(n=>{const o=el('option','',n+' 张同时生成');o.value=n;concurrency.append(o);});concurrency.value='2';
        const roleLabel=el('label','sa-param-label','创作人设'), parallelLabel=el('label','sa-param-label','图片并发');roleLabel.append(persona);parallelLabel.append(concurrency);agentOptions.append(roleLabel,parallelLabel);
        const applyOptions=button('应用人设与并发设置',()=>guarded(async()=>{await revisePlan({persona_id:persona.value,image_concurrency:Number(concurrency.value)});closeMenu();}));applyOptions.hidden=true;
        const optionsHint=el('p','sa-muted','人设保存在个人数据目录。修改后用于新任务；已有计划需重新应用并批准。');
        const roleActions=el('div','sa-actions');
        const editRole=button('编辑当前人设',()=>guarded(async()=>{await loadRoles();openRoleEditor(roles.find(r=>r.id===persona.value));}));editRole.dataset.saPersonaEdit='';
        const addRole=button('新增人设',()=>openRoleEditor(null));addRole.dataset.saPersonaNew='';roleActions.append(editRole,addRole);
        const roleEditor=el('div','sa-persona-editor');roleEditor.hidden=true;
        const roleTitle=el('strong'), roleName=el('input'), roleDescription=el('textarea'), rolePrompt=el('textarea');
        roleName.type='text';roleName.maxLength=60;roleDescription.rows=2;roleDescription.maxLength=240;rolePrompt.rows=9;rolePrompt.maxLength=16000;
        const roleField=(name,control)=>{const label=el('label','sa-param-label',name);control.setAttribute('aria-label',name);label.append(control);return label;};
        const roleSave=button('保存人设',()=>guarded(async()=>{
            const body={name:roleName.value,description:roleDescription.value,prompt:rolePrompt.value,...(editingRole?{id:editingRole.id,expected_revision:editingRole.revision}:{})};
            const data=await api('/personas',body);await loadRoles(data.persona.id);optionsDirty=true;saveOptions();closeRoleEditor();renderRun();
            tell('人设已保存。新任务使用新提示词；已有计划请重新应用人设并批准。');
        }),'sa-primary');roleSave.dataset.saPersonaSave='';
        const roleReset=button('恢复默认',()=>guarded(async()=>{
            const data=await api('/personas/'+encodeURIComponent(editingRole.id)+'/reset',{expected_revision:editingRole.revision});
            await loadRoles(data.persona.id);optionsDirty=true;saveOptions();openRoleEditor(data.persona);renderRun();tell('已恢复默认人设，已有计划需重新应用。');
        }));roleReset.dataset.saPersonaReset='';
        const roleCancel=button('取消',()=>{closeRoleEditor();editRole.focus();});const roleEditorActions=el('div','sa-actions');roleEditorActions.append(roleSave,roleCancel,roleReset);
        roleEditor.append(roleTitle,roleField('人设名称',roleName),roleField('人设简介',roleDescription),roleField('人设提示词',rolePrompt),roleEditorActions);
        roleName.addEventListener('keydown',e=>{if(e.key==='Enter')e.preventDefault();});
        function openRoleEditor(role){
            if(role && typeof role.prompt!=='string')throw new Error('尚未读取人设提示词，请刷新后重试。');
            editingRole=role?structuredClone(role):null;roleTitle.textContent=role?'编辑人设':'新增人设';roleName.value=role?.name || '';roleDescription.value=role?.description || '';rolePrompt.value=role?.prompt || '';
            roleReset.hidden=!role?.builtin;roleReset.disabled=!role?.modified;roleEditor.hidden=false;roleActions.hidden=true;roleLabel.hidden=true;setTimeout(()=>{if(!roleEditor.hidden)roleName.focus();},0);
        }
        function closeRoleEditor(){editingRole=null;roleEditor.hidden=true;roleActions.hidden=false;roleLabel.hidden=false;}
        function roleOutdated(){const role=roles.find(r=>r.id===(state.run?.persona_id || 'general'));return Boolean(state.run && (state.run.persona_changed || (role?.revision!==undefined && role.revision!==(state.run.persona_revision ?? 0))));}
        async function loadRoles(chosen){
            const token=++roleLoadRevision,data=await api('/personas');if(disposed || token!==roleLoadRevision || !data.personas?.length)return;
            const selected=chosen || preferredPersona || persona.value;roles=data.personas;persona.replaceChildren();roles.forEach(r=>{const o=el('option','',r.name);o.value=r.id;o.title=r.description;persona.append(o);});
            if(roles.some(r=>r.id===selected))persona.value=selected;preferredPersona=persona.value;renderDisabled();
        }
        function selectedOptions(){return {persona_id:persona.value,image_concurrency:Number(concurrency.value)};}
        function restoreOptions(value){if(value?.persona_id){preferredPersona=value.persona_id;if(roles.some(r=>r.id===value.persona_id))persona.value=value.persona_id;}if([1,2,3,4].includes(Number(value?.image_concurrency)))concurrency.value=String(value.image_concurrency);optionsDirty=false;}
        function saveOptions(){try{if(state.canvas)root.localStorage.setItem('smart-agent-options:'+state.canvas,JSON.stringify(selectedOptions()));}catch{}}
        [persona,concurrency].forEach(control=>control.addEventListener('change',()=>{preferredPersona=persona.value;optionsDirty=true;saveOptions();renderDisabled();}));
        const refsHeader = el('div','sa-row'); refsHeader.append(el('strong','','引用素材'), button('引用当前选区', () => { state.refsKey = null; syncContext(true); }));
        const refs = el('div','sa-refs'); const generation = el('details','sa-generation'); generation.open=true; generation.append(el('summary','','图片 / 视频生成参数')); const generationText = el('div','sa-message-text'); generation.append(generationText);
        let generationEditor, generationCanvas;
        const applyGeneration=button('应用到待确认计划',()=>guarded(async()=>{
            const selected=generationEditor.read();
            const {apiKind,count,locks,provider_id,model,...settings}=selected;
            const steps=(state.run?.plan?.steps || []).filter(s=>s.kind===apiKind).map(s=>({id:s.id,provider_id,model,settings}));
            if(!steps.length)throw new Error('计划中没有此类型的生成步骤。');
            await revisePlan({steps});
            closeMenu();
        }));applyGeneration.hidden=true;generation.append(applyGeneration);
        const settingNames={ratio:'画面比例',resolution:'分辨率',duration:'时长（秒）',quality:'质量',version:'图片版本',background:'背景',output_format:'输出格式',output_compression:'压缩质量',generate_audio:'生成音频',use_frame_roles:'首尾帧',multimodal:'多模态参考',return_last_frame:'返回尾帧',watermark:'水印',camerafixed:'固定镜头',trusted_asset:'可信素材',enhance_prompt:'提示词增强',enable_upsample:'升采样',seed:'随机种子',fps:'帧率'};
        function parameterEditor(initial, fixedKind, onSave) {
            const box=el('div','sa-parameter-editor'), grid=el('div','sa-controls'), fields=el('div','sa-controls'), feedback=el('p','sa-muted');
            const kind=el('select'), platform=el('select'), selectedModel=el('select');
            let revision=0, ready=false, values={}, saved=structuredClone(initial || {});
            const drafts=structuredClone(initial.defaults || {});let previousKind=fixedKind || initial.apiKind || 'image';
            const label=(name,control)=>{const n=el('label','sa-param-label',name);control.setAttribute('aria-label',name);n.append(control);return n;};
            const fill=(select,options,value)=>{select.replaceChildren();options.forEach(([v,name])=>{const o=el('option','',name);o.value=String(v);select.append(o);});if([...select.options].some(o=>o.value===String(value)))select.value=String(value);};
            fill(kind,[['image','图片'],['video','视频']],fixedKind || initial.apiKind || 'image');kind.disabled=Boolean(fixedKind);
            grid.append(label('生成类型',kind),label('生成平台',platform),label('生成模型',selectedModel));box.append(grid,fields,feedback);
            const policy=el('select');fill(policy,[['default','按任务分别设置（推荐）'],['fixed','全部使用固定参数']],initial.locks?.length?'fixed':'default');if(!fixedKind)box.append(label('参数策略',policy));
            const buttonSave=onSave ? button('应用参数，重新确认',()=>guarded(async()=>{if(!ready)throw new Error('请先选择可用模型。');await onSave(read());})):null;
            if(buttonSave)box.append(buttonSave);
            function read() {
                if(!ready)throw new Error('生成参数尚未准备好，请选择支持的模型。');
                const result={apiKind:kind.value,provider_id:platform.value,model:selectedModel.value,count:Number(values.count?.value || 1)};
                Object.entries(values).forEach(([key,control])=>{if(key==='count' || control.value==='')return;const value=control._options ? control._options.find(v=>String(v)===control.value) : control.value;if(value===undefined)throw new Error('请重新选择模型支持的'+(settingNames[key] || key)+'。');result[key]=value;});
                if(result.ratio==='source'){delete result.ratio;result.size_mode='source';}else result.size_mode='fixed';
                if(!fixedKind)result.locks=policy.value==='fixed'?['provider_id','model','ratio','resolution','duration','count','size_mode'].filter(k=>result[k]!==undefined):[];
                return result;
            }
            async function capabilities() {
                const token=++revision; ready=false;fields.replaceChildren();values={};feedback.textContent='正在读取模型参数…';if(buttonSave)buttonSave.disabled=true;
                if(!platform.value || !selectedModel.value){feedback.textContent='请先在 API 设置中配置生成模型。';return;}
                try {
                    const data=await api('/capabilities?'+new URLSearchParams({provider_id:platform.value,model:selectedModel.value,kind:kind.value}));
                    if(token!==revision || !box.isConnected)return;
                    const cap=data.capabilities;if(!cap || cap.available===false)throw new Error('此模型暂未适配 Agent 参数。');
                    function choice(key,options,value){if(!options?.length)return;const control=el('select');control._options=options;const choices=options.map(v=>[v,typeof v==='boolean'?(v?'开启':'关闭'):v==='source'?'适配输入尺寸':v==='auto'?'自动':v==='opaque'?'不透明':v==='transparent'?'透明':String(v)]);if(value!==undefined && value!=='' && !options.some(v=>String(v)===String(value)))choices.unshift([value,'不支持（'+value+'），请重选']);fill(control,choices,value);values[key]=control;fields.append(label(settingNames[key] || key,control));}
                    choice('ratio',[...(cap.ratios || []),...(kind.value==='image'?['source']:[])],saved.size_mode==='source' || saved.ratio==='source'?'source':saved.ratio);
                    choice('resolution',cap.resolutions,saved.resolution?.toLowerCase());choice('duration',cap.durations,saved.duration);
                    if(!fixedKind) {const control=el('select');fill(control,Array.from({length:4},(_,i)=>[i+1,String(i+1)]),saved.count || 1);values.count=control;fields.append(label('数量',control));}
                    Object.entries(cap.settings || {}).forEach(([key,options])=>{if(Array.isArray(options) && (options.length>1 || saved[key]!==undefined))choice(key,options,saved[key]);});
                    ready=true;feedback.textContent=fixedKind?'修改后点击应用，再重新批准。':'用于新计划；待确认计划需点击应用。数量仅用于新计划。';if(buttonSave)buttonSave.disabled=false;
                    if(!fixedKind)updateToolbar();
                }catch(err){if(token===revision){feedback.textContent=err.message;ready=false;}}
            }
            function models(preferred){const p=state.providers.find(p=>p.id===platform.value);fill(selectedModel,(p?.[kind.value+'_models'] || []).map(m=>typeof m==='string'?[m,m]:[m.id || m.name,m.name || m.id]),preferred);capabilities();}
            function platforms(preferred,preferredModel){fill(platform,state.providers.filter(p=>(p[kind.value+'_models'] || []).length).map(p=>[p.id,p.name || p.id]),preferred);models(preferredModel);}
            kind.addEventListener('change',()=>{const next=kind.value;kind.value=previousKind;try{drafts[previousKind]=read();}catch{}kind.value=next;previousKind=next;saved=structuredClone(drafts[next] || {});policy.value=saved.locks?.length?'fixed':'default';platforms(saved.provider_id,saved.model);});platform.addEventListener('change',()=>{saved={};models();});selectedModel.addEventListener('change',()=>{saved={};capabilities();});
            if(fixedKind)box.addEventListener('change',()=>{box.dataset.dirty='true';feedback.textContent='参数尚未应用，请点击应用后再批准。';renderDisabled();});
            // Mount before capability requests finish so detached old editors cannot overwrite newer ones.
            return {box,identity:grid,read,all:()=>({...drafts,[kind.value]:read()}),start:()=>platforms(initial.provider_id,initial.model),dispose:()=>revision++};
        }
        const history = el('div','sa-history'); history.setAttribute('role','log'); history.setAttribute('aria-label','会话消息'); history.setAttribute('aria-live','polite');
        const runBox = el('section','sa-run'); runBox.setAttribute('aria-label','计划与任务状态');
        const notice = el('div','sa-notice'); notice.setAttribute('role','status'); notice.setAttribute('aria-live','polite');
        const form = el('form','sa-compose'); const input = el('textarea'); input.rows = 3; input.maxLength = 16000; input.placeholder = '描述想创作的内容…'; input.setAttribute('aria-label','发给 Agent 的消息');
        const send = el('button','sa-send','发送'); send.type = 'submit';send.title='发送（Ctrl+Enter）';send.setAttribute('aria-label','发送');
        const recover = button('刷新记录', () => guarded(async () => { await loadHistory(); if (state.run) await poll(); }));
        const conversation=el('div','sa-conversation');conversation.append(history,runBox,notice);
        const progress=el('section','sa-progress');progress.hidden=true;progress.setAttribute('aria-label','任务执行进度');
        const progressHead=el('div','sa-row'), progressTitle=el('strong','sa-progress-title'), progressCount=el('span','sa-progress-count');progressCount.setAttribute('aria-live','polite');progressHead.append(progressTitle,progressCount);
        const progressTrack=el('div','sa-progress-track'), progressFill=el('div','sa-progress-fill');progressTrack.setAttribute('role','progressbar');progressTrack.setAttribute('aria-label','整体任务完成进度');progressTrack.setAttribute('aria-valuemin','0');progressTrack.setAttribute('aria-valuemax','100');progressTrack.append(progressFill);
        const progressStats=el('div','sa-progress-stats'), progressCurrent=el('div','sa-progress-current'), progressActions=el('div','sa-progress-actions');
        const progressDetails=button('查看详情',()=>{closeMenu();conversation.scrollTo({top:conversation.scrollTop+runBox.getBoundingClientRect().top-conversation.getBoundingClientRect().top,behavior:'smooth'});runBox.focus({preventScroll:true});});progressDetails.dataset.saProgressDetails='';runBox.tabIndex=-1;
        const progressStop=button('停止',()=>guarded(()=>act('stop')));progressStop.dataset.saProgressStop='';progressActions.append(progressDetails,progressStop);
        progress.append(progressHead,progressTrack,progressStats,progressCurrent,progressActions);
        const questionCard=el('section','sa-questions');questionCard.hidden=true;questionCard.setAttribute('aria-label','待你回答');
        const questionHead=el('div','sa-row'),questionTitle=el('strong'),questionCount=el('span','sa-muted');questionHead.append(questionTitle,questionCount);
        const questionList=el('div','sa-question-list'),questionFoot=el('div','sa-question-foot'),questionHint=el('p','sa-muted');
        const questionSubmit=button('提交补充并继续规划',()=>guarded(async()=>{
            const run=state.run,questions=questionsOf(run),draft=questionDrafts.get(questionKey) || {},text=clarificationText(questions,draft);
            if(!text)throw new Error('请先填写至少一个问题的回答。');
            if(text.length>16000)throw new Error('补充内容过长，请缩短回答后重试。');
            closeMenu();await revisePlan({text,...selectedOptions()},run);
        }),'sa-primary');questionSubmit.dataset.saQuestionsSubmit='';
        questionFoot.append(questionHint,questionSubmit);questionCard.append(questionHead,questionList,questionFoot);
        const questionDrafts=new Map();let questionKey=null;
        const toolbar=el('div','sa-toolbar'), popovers=new Map();let openMenu=null;
        const generationModels=el('div','sa-generation-models');
        function closeMenu(focus=false){if(!openMenu)return;const menu=popovers.get(openMenu);menu.popup.hidden=true;menu.trigger.setAttribute('aria-expanded','false');openMenu=null;if(focus)menu.trigger.focus();}
        function positionMenu(){if(!openMenu)return;const popup=popovers.get(openMenu).popup;popup.style.maxHeight=Math.max(90,form.getBoundingClientRect().top-panel.getBoundingClientRect().top-22)+'px';}
        function menu(name,label,title,contents){
            const popup=el('section','sa-popover');popup.hidden=true;popup.id='sa-popup-'+name;popup.setAttribute('role','region');popup.setAttribute('aria-label',title+'面板');
            const trigger=button(label,()=>{if(openMenu===name){closeMenu(true);return;}closeMenu();openMenu=name;popup.hidden=false;trigger.setAttribute('aria-expanded','true');positionMenu();popup.querySelector('select,button')?.focus();if(name==='persona' && roleEditor.hidden)guarded(loadRoles);},'sa-tool');
            trigger.dataset.saMenu=name;trigger.title=title;trigger.setAttribute('aria-label',title);trigger.setAttribute('aria-controls',popup.id);trigger.setAttribute('aria-expanded','false');
            const top=el('div','sa-row');top.append(el('strong','',title),button('×',()=>closeMenu(true),'sa-popup-close'));popup.append(top,...contents);toolbar.append(trigger);form.append(popup);popovers.set(name,{popup,trigger});return trigger;
        }
        const addButton=menu('refs','＋','引用素材',[refsHeader,el('p','sa-muted','在画布选中图片或视频，再点击“引用当前选区”。')]);addButton.classList.add('sa-add');
        menu('models','模型','模型选择',[el('p','sa-muted','对话模型'),controls,el('p','sa-muted','图片 / 视频生成模型'),generationModels]);
        menu('params','参数','生成参数',[generation]);
        const personaButton=menu('persona','人设','创作人设',[roleLabel,roleActions,roleEditor,optionsHint,applyOptions]);
        personaButton.setAttribute('aria-label','人设设置');
        const concurrencyButton=menu('parallel','并发2','图片并发',[parallelLabel,el('p','sa-muted','独立图片同时生成，按组最多 2 路；依赖任务等待前置成果。')]);
        // Both menus operate on the same pending options; keep one apply action visible in either.
        const applyParallel=button('应用人设与并发设置',()=>applyOptions.click());popovers.get('parallel').popup.append(applyParallel);
        toolbar.append(send);form.prepend(refs,input,toolbar);
        const footerHint=el('div','sa-footer-hint','Ctrl / ⌘ + Enter 发送');
        recover.classList.add('sa-refresh');recover.title='刷新任务记录';recover.setAttribute('aria-label','刷新任务记录');sessionBar.append(recover);
        panel.append(header,sessionBar,conversation,progress,questionCard,form,footerHint);doc.body.append(entry,panel);
        function dismissMenus(event){if(openMenu && !form.contains(event.target))closeMenu();else if(openMenu && event.target===input)closeMenu();}
        doc.addEventListener('pointerdown',dismissMenus,true);
        form.addEventListener('change',()=>updateToolbar());
        function updateToolbar(){
            personaButton.textContent=persona.value==='ecommerce'?'电商':persona.value==='video'?'视频人设':persona.value==='general'?'人设':roles.find(r=>r.id===persona.value)?.name || '人设';personaButton.title=roles.find(r=>r.id===persona.value)?.name || '创作人设';
            concurrencyButton.textContent='并发'+concurrency.value;
            const modelTrigger=popovers.get('models').trigger;modelTrigger.title='对话模型：'+(model.value || '未选择')+'；生成模型：'+(generationModels.querySelector('[aria-label="生成模型"]')?.value || '未选择');
            try{const value=generationEditor?.read();const ratio=value?.size_mode==='source'?'适配':value?.ratio || '自动';const resolution=String(value?.resolution || '').toUpperCase();const trigger=popovers.get('params').trigger;trigger.textContent=ratio+(resolution?'·'+resolution:'');trigger.title='生成参数：'+ratio+' · '+resolution+(value?.duration?' · '+value.duration+'秒':'');}catch{}
        }
        function tell(text) { notice.textContent = text || ''; }
        async function api(path, body) {
            const controller = new AbortController(); requests.add(controller);
            const timeout = setTimeout(() => controller.abort(), 30000);
            try {
                const res = await root.fetch('/api/canvas-agent' + path, {method:body ? 'POST' : 'GET', credentials:'same-origin', headers:body ? {'Content-Type':'application/json'} : {}, body:body ? JSON.stringify(body) : undefined, signal:controller.signal});
                const data = await res.json().catch(() => ({}));
                if (!res.ok) { const detail = data.detail; throw new Error(typeof detail === 'string' ? detail : detail?.message || data.error?.message || `请求失败（${res.status}）`); }
                return data;
            } catch (err) { if (err.name === 'AbortError') throw new Error('请求超时或已中断，请刷新记录确认任务状态。'); throw err; }
            finally { clearTimeout(timeout); requests.delete(controller); }
        }
        async function guarded(fn) {
            if (state.busy) return; state.busy = true; renderDisabled(); tell('');
            try { await fn(); } catch (err) { tell(err.message || '操作失败，请刷新记录后重试。'); }
            finally { state.busy = false; renderDisabled(); }
        }
        function renderDisabled() {
            send.disabled = !canSend(state) && !(!state.busy && state.pending);
            send.textContent = state.pending ? '重试同一条消息' : state.run?.status==='needs_input'?'提交补充':state.run?.status==='awaiting_approval'?'修改计划':'发送';
            send.setAttribute('aria-label',send.textContent);send.title=send.textContent+'（Ctrl+Enter）';
            input.placeholder=state.run?.status==='needs_input'?'也可在这里直接回复全部问题…':'描述想创作的内容…';
            input.disabled = Boolean(state.pending); provider.disabled = model.disabled = state.busy || Boolean(state.pending) || shouldPoll(state.run);
            sessions.disabled = newSession.disabled = state.busy || Boolean(state.pending);
            recover.disabled = state.busy;
            progressStop.disabled=state.busy || Boolean(state.pending) || !actionsOf(state.run).includes('stop');
            persona.disabled=concurrency.disabled=state.busy || Boolean(state.pending) || shouldPoll(state.run);
            [editRole,addRole,roleSave,roleCancel,roleName,roleDescription,rolePrompt].forEach(control=>control.disabled=state.busy || Boolean(state.pending));roleReset.disabled=state.busy || Boolean(state.pending) || !editingRole?.modified;
            applyOptions.hidden=!['awaiting_approval','needs_input'].includes(state.run?.status);applyOptions.disabled=state.busy || Boolean(state.pending) || !(optionsDirty || roleOutdated());
            optionsHint.textContent=roleOutdated() && ['awaiting_approval','needs_input'].includes(state.run?.status)?'人设已更新，请点击应用重新规划，再批准新计划。':'人设保存在个人数据目录。修改用于新任务，正在执行的任务不受影响。';
            applyParallel.hidden=applyOptions.hidden;applyParallel.disabled=applyOptions.disabled;updateToolbar();
            applyGeneration.hidden=!['awaiting_approval','needs_input'].includes(state.run?.status);applyGeneration.disabled=state.busy || Boolean(state.pending);
            runBox.querySelectorAll('[data-sa-action]').forEach(b => { b.disabled = state.busy || Boolean(state.pending) || (b.dataset.saAction==='approve' && (optionsDirty || roleOutdated() || Boolean(runBox.querySelector('[data-dirty=true]')))); });
            renderQuestionDisabled();
        }
        function renderModels(preferred) {
            model.replaceChildren(); const found = state.providers.find(p => p.id === provider.value);
            modelsOf(found).forEach(m => { const o = el('option','',m.name); o.value = m.id; model.append(o); });
            if (preferred && [...model.options].some(o => o.value === preferred)) model.value = preferred;
            if (!model.options.length) { const o = el('option','','暂无对话模型，请先在设置中配置'); o.value=''; model.append(o); }
        }
        provider.addEventListener('change', () => renderModels());
        sessions.addEventListener('change', () => guarded(async () => { state.session = sessions.value; state.run = null; await loadHistory(); schedulePoll(0); }));
        function renderHistory() {
            history.replaceChildren();
            if (!state.messages.length) history.append(el('p','sa-muted','选中画布素材，描述创作目标。助手会先列出需要批准的制作计划。'));
            state.messages.forEach(m => { const box = el('article', 'sa-message ' + (m.role === 'user' ? 'sa-user' : '')); box.append(el('strong','',m.role === 'user' ? '你' : '助手'), el('div','sa-message-text', typeof m.content === 'string' ? m.content : JSON.stringify(m.content || ''))); history.append(box); });
        }
        function renderRun() {
            runBox.replaceChildren(); const run = state.run;
            renderQuestions(run);
            renderProgress(run);
            entry.textContent = 'Agent' + (run && !['completed','stopped'].includes(run.status) ? ' · ' + statusOf(run.status) : '');
            if (!run) { renderDisabled(); return; }
            runBox.append(el('h3','',statusOf(run.status)));
            if (run.summary) runBox.append(el('p','',run.summary));
            const plan = run.plan;
            if (plan) {
                if(!optionsDirty)restoreOptions(plan);
                runBox.append(el('p','sa-muted',`本次人设：${plan.persona_name || state.run.persona_name || roles.find(r=>r.id===(plan.persona_id || 'general'))?.name || '通用创作助手'} · 图片并发：${plan.image_concurrency || 2}（按组最多 2）`));
                const blocked=(plan.steps || []).filter(s=>s.blocked_reason).length;
                runBox.append(el('p','sa-muted',`制作清单：${(plan.steps || []).filter(s=>s.kind==='image').length} 张图片 · ${(plan.steps || []).filter(s=>s.kind==='video').length} 段视频；共 ${plan.steps?.length || 0} 项`));
                if(blocked)runBox.append(el('p','sa-error',`待调整 ${blocked} 项；批准只会执行已就绪步骤。未就绪项不会提交，也不会自动改尺寸。`));
                if(run.status==='needs_input' && !plan.steps?.length)runBox.append(el('p','sa-muted','尚未提交任何生成任务。请补充条件或在下方继续修改要求。'));
                runBox.append(el('p','',plan.summary || '制作计划'));
                const steps = el('ol','sa-steps');
                (plan.steps || []).forEach(s => {
                    const item = el('li'); item.append(el('strong','',s.title || s.kind || s.id));
                    if (s.prompt) item.append(el('p','sa-message-text',s.prompt));
                    const providerName = state.providers.find(p => p.id === s.provider_id)?.name || s.provider_id;
                    const detail = el('div','sa-message-text',formatDetails({...s.settings,loop:s.loop}, {kind:s.kind,provider:providerName,model:s.model,count:s.count,references:s.references,dependencies:s.dependencies})); item.append(detail); steps.append(item);
                    if(s.blocked_reason)item.append(el('p','sa-error',s.blocked_reason));
                    if(['awaiting_approval','needs_input','partial'].includes(run.status) && ['image','video'].includes(s.kind) && (run.steps || []).some(actual=>actual.id===s.id && ['pending','blocked'].includes(actual.status))) {
                        const disclosure=el('details','sa-edit-step');disclosure.append(el('summary','','调整此步骤参数'));
                        const editor=parameterEditor({...s.settings,provider_id:s.provider_id,model:s.model},s.kind,async selected=>{
                            const {apiKind,count,locks,provider_id,model,...settings}=selected;
                            await revisePlan({steps:[{id:s.id,provider_id,model,settings}]},run);
                        });disclosure.append(editor.box);item.append(disclosure);setTimeout(editor.start,0);
                    }
                }); runBox.append(steps, el('p','sa-muted','待确认时可调整各步骤参数，或在下方输入修改要求。修改后需重新批准；执行中的任务保持原参数。'));
            }
            (run.steps || []).forEach(s => { runBox.append(el('p','sa-step-status',`${s.title || s.id}：${statusOf(s.status)}${s.error ? ' · ' + (typeof s.error === 'string' ? s.error : s.error.message || '需要处理') : ''}`)); });
            if (run.error) runBox.append(el('p','sa-error',typeof run.error === 'string' ? run.error : run.error.message || '任务需要处理'));
            const outputs = run.outputs || [];
            outputs.forEach((o, i) => { const card = el('div','sa-output'); card.append(el('span','',`成果 ${i + 1} · ${o.kind || o.media_kind || '媒体'}`)); const ids = o.node_ids || (o.node_id ? [o.node_id] : []); if (ids.length) card.append(button('定位', () => bridge()?.locate(ids))); else card.append(el('span','sa-muted','等待交付')); runBox.append(card); });
            const ids = nodeIdsOf(run); if (ids.length) runBox.append(button('定位本次成果', () => bridge()?.locate(ids)));
            const actions = el('div','sa-actions'); actionsOf(run).forEach(action => { const label=action==='approve' && plan?.steps?.some(s=>s.blocked_reason)?'仅批准已就绪任务':actionLabels[action];const b = button(label, () => guarded(() => act(action)), action === 'approve' ? 'sa-primary' : ''); b.dataset.saAction = action; actions.append(b); }); runBox.append(actions); renderDisabled();
        }
        function renderProgress(run) {
            const p=progressOf(run);progress.hidden=!p.visible || !questionCard.hidden;if(progress.hidden)return;
            progressTitle.textContent=p.indeterminate?'正在规划任务…':statusOf(run.status);
            progressCount.textContent=p.indeterminate?'正在拆解需求':p.total?`已完成 ${p.done}/${p.total} · ${p.percent}%`:'尚未提交生成';
            progress.classList.toggle('sa-progress-indeterminate',p.indeterminate);
            progress.classList.toggle('sa-progress-warning',Boolean(p.failed || p.blocked || p.unknown || ['failed','partial','needs_attention','interrupted','stopped','needs_input'].includes(run.status)));
            progress.classList.toggle('sa-progress-complete',Boolean(p.total && p.done===p.total));
            progressFill.style.width=(p.indeterminate?35:p.percent)+'%';
            if(p.indeterminate)progressTrack.removeAttribute('aria-valuenow');else progressTrack.setAttribute('aria-valuenow',String(p.percent));
            progressTrack.setAttribute('aria-valuetext',p.total?`已完成 ${p.done} 项，共 ${p.total} 项`:'正在规划，尚无确定进度');
            progressStats.textContent=[p.processing?`进行中 ${p.processing}`:'',p.pending?`等待 ${p.pending}`:'',p.failed?`失败 ${p.failed}`:'',p.blocked?`待调整 ${p.blocked}`:'',p.unknown?`结果待确认 ${p.unknown}`:''].filter(Boolean).join(' · ');
            progressStats.hidden=p.indeterminate || !progressStats.textContent;
            progressCurrent.textContent=p.indeterminate?'正在生成制作计划，尚未提交生成':p.current.length?'当前：'+p.current.slice(0,2).join('、')+(p.current.length>2?` 等 ${p.current.length} 项`:''):run.status==='awaiting_approval'?'确认计划后开始执行':run.status==='stopping'?'正在接收已提交任务的结果，不再提交新任务':run.status==='needs_input'?'请补充条件或调整未就绪参数':p.failed || p.blocked || p.unknown?'已有成果已保留，请查看详情处理剩余任务':run.status==='completed'?'本批任务已完成':run.status==='stopped'?'已停止后续任务，现有成果已保留':'';
            progressCurrent.hidden=!progressCurrent.textContent;progressCurrent.title=progressCurrent.textContent;
            progressStop.hidden=!actionsOf(run).includes('stop') || run.status==='stopping';
        }
        function renderQuestions(run) {
            const questions=questionsOf(run);questionCard.hidden=!questions.length;
            if(!questions.length)return;
            questionTitle.textContent='待你回答';questionCount.textContent=`等待回答 ${questions.length} 个问题`;
            const key=JSON.stringify([run.id,run.plan?.revision,questions]);
            if(key===questionKey){renderQuestionDisabled();return;}
            questionKey=key;questionList.replaceChildren();
            if(!questionDrafts.has(key))questionDrafts.set(key,Object.create(null));
            const draft=questionDrafts.get(key);
            questions.forEach((q,index)=>{
                const item=el('div','sa-question'),label=el('label','sa-param-label'),text=el('strong','',`${index+1}. ${q.question}`),answer=el('textarea'),choices=el('div','sa-question-choices');
                answer.rows=2;answer.maxLength=1000;answer.placeholder='填写回答；未知内容也可写明';answer.value=draft[q.id] || '';answer.dataset.saQuestionAnswer=q.id;answer.setAttribute('aria-label','回答问题 '+(index+1));
                function changed(){draft[q.id]=answer.value;choices.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.textContent===answer.value)));renderQuestionDisabled();}
                q.options.forEach(option=>{const choice=button(option,()=>{answer.value=option;changed();answer.focus();});choice.setAttribute('aria-pressed',String(answer.value===option));choices.append(choice);});
                answer.addEventListener('input',changed);answer.addEventListener('keydown',e=>{if(e.key==='Enter' && (e.ctrlKey || e.metaKey) && !e.isComposing){e.preventDefault();questionSubmit.click();}});
                label.append(text,answer);item.append(label);if(q.options.length)item.append(choices);questionList.append(item);
            });
            renderQuestionDisabled();
        }
        function renderQuestionDisabled(){
            const questions=questionsOf(state.run),draft=questionDrafts.get(questionKey) || {},answered=questions.filter(q=>String(draft[q.id] || '').trim()).length,disabled=state.busy || Boolean(state.pending);
            questionSubmit.disabled=disabled || !answered;
            questionList.querySelectorAll('button,textarea').forEach(control=>control.disabled=disabled);
            questionHint.textContent=`已填写 ${answered}/${questions.length}。可先提交已知信息，提交后会重新规划，仍需批准才生成。`;
        }
        async function refreshCanvas() {
            if (typeof bridge()?.refresh === 'function') await bridge().refresh();
        }
        async function revisePlan(changes,run=state.run) {
            const epoch=state.epoch;
            const result=await api(`/runs/${encodeURIComponent(run.id)}/actions`,{request_id:id(),action:'revise',expected_revision:run.revision,plan_revision:run.plan.revision,...changes});
            if(epoch!==state.epoch)return;
            state.run=result.run;optionsDirty=false;saveOptions();renderRun();await loadHistory();schedulePoll(0);
            tell(changes.text?'正在根据修改要求重新规划…':'参数已更新，请检查并重新批准。');
        }
        async function act(action) {
            const run = state.run, epoch = state.epoch;
            if (!actionsOf(run).includes(action)) return;
            if (['approve','continue','retry','repair_delivery','undo'].includes(action)) { if (!bridge()?.flush) throw new Error('画布尚未准备好，请稍后再试。'); await bridge().flush(); }
            const result = await api(`/runs/${encodeURIComponent(run.id)}/${action === 'undo' ? 'undo' : 'actions'}`, action === 'undo' ? {request_id:id()} : {request_id:id(), action, expected_revision:run.revision, ...(action === 'approve' ? {plan_revision:run.plan?.revision} : {})});
            if (epoch !== state.epoch) return;
            if (result.run) state.run = result.run; else state.run = (await api(`/runs/${encodeURIComponent(run.id)}`)).run;
            await refreshCanvas(); renderRun(); schedulePoll(0);
            if (action === 'stop') tell('已请求停止后续提交；已经受理的生成任务仍可能返回成果。');
        }
        async function loadHistory() {
            if (!state.session) { state.messages = []; renderHistory(); return; }
            const epoch = state.epoch, session = state.session;
            const data = await api(`/sessions/${encodeURIComponent(session)}/messages`);
            if (epoch !== state.epoch || session !== state.session) return;
            state.messages = data.messages || []; renderHistory();
            const latest = [...state.messages].reverse().find(m => m.run_id);
            if (latest && !state.run) {
                const snapshot = await api(`/runs/${encodeURIComponent(latest.run_id)}`);
                if (epoch !== state.epoch || session !== state.session) return;
                state.run = snapshot.run;
            }
            renderRun();
        }
        async function listSessions() {
            const epoch = state.epoch, data = await api('/sessions?canvas_id=' + encodeURIComponent(state.canvas));
            if (epoch !== state.epoch) return;
            sessions.replaceChildren(); (data.sessions || []).forEach(s => { const o = el('option','',s.title || '创作会话'); o.value=s.id; sessions.append(o); });
            if (![...sessions.options].some(o => o.value === state.session)) state.session = sessions.options[0]?.value || null;
            sessions.value = state.session || ''; await loadHistory();
        }
        async function createSession() {
            if (!state.canvas) throw new Error('请先打开并保存一张画布。');
            const data = await api('/sessions',{canvas_id:state.canvas}); state.session = data.session.id; state.run = null; await listSessions();
        }
        async function submit() {
            if (!state.pending && !canSend({...state,busy:false})) return;
            if(!state.pending && ['awaiting_approval','needs_input'].includes(state.run?.status)) {
                if(!input.value.trim())return;
                await revisePlan({text:input.value.trim(),...selectedOptions()});input.value='';return;
            }
            if (!state.pending) {
                if (!input.value.trim()) return;
                if (!provider.value || !model.value) throw new Error('请先选择已配置的对话平台和模型。');
                if (!bridge()?.flush) throw new Error('画布尚未准备好，请稍后再试。');
                const selectedNodeIds = state.refs.map(n => n.id);
                const context = await bridge().flush();
                if (!context?.canvas_id || String(context.canvas_id) !== state.canvas) throw new Error('画布已切换，请刷新面板后重试。');
                if (!state.session) await createSession();
                state.pending = {session:state.session, payload:{client_request_id:id(), text:input.value.trim(), context:{canvas_revision:context.canvas_revision, selected_node_ids:selectedNodeIds, scope:'selection'}, llm:{provider_id:provider.value, model:model.value}, generation:generationEditor ? generationEditor.read() : context.generation || {},generation_defaults:generationEditor?.all() || {},...selectedOptions()}};
            }
            const pending = state.pending;
            let result;
            try { result = await api(`/sessions/${encodeURIComponent(pending.session)}/messages`,pending.payload); }
            catch (err) { throw new Error(err.message + ' 未自动重发；再次点击发送将复用同一请求编号。'); }
            state.pending = null;optionsDirty=false;input.value = ''; state.run = result.run || {id:result.run_id,status:'planning',actions:[]};
            renderRun(); schedulePoll(0); await loadHistory();
        }
        form.addEventListener('submit', e => { e.preventDefault();closeMenu();guarded(submit); });
        input.addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !e.isComposing) { e.preventDefault(); form.requestSubmit(); } });
        function renderRefs() {
            refs.replaceChildren();
            refs.hidden=!state.refs.length;
            if (!state.refs.length) refs.append(el('span','sa-muted','未引用素材，可直接讨论或引用当前选区。'));
            state.refs.forEach(n => { const card=el('div','sa-ref'); const locate=button(`${n.title || n.type || '节点'}${n.images?.length ? ' · ' + n.images.length + ' 张' : ''}`, () => bridge()?.locate([n.id])); const remove=button('×', () => { state.refs=state.refs.filter(r => r.id !== n.id); renderRefs(); }); remove.setAttribute('aria-label','移除引用 ' + (n.title || n.id)); card.append(locate,remove); refs.append(card); });
        }
        async function syncContext(force) {
            if (disposed || !state.open) return;
            const currentBridge = bridge();
            if (currentBridge !== subscribedBridge) { unsubscribe?.(); subscribedBridge=currentBridge; unsubscribe=currentBridge?.subscribe?.(() => syncContext(false)); }
            const context = currentBridge?.getContext?.();
            if (!context?.canvas_id) { tell('正在等待画布准备完成…'); return; }
            const canvas = String(context.canvas_id);
            if (canvas !== state.canvas) {
                if (state.busy || state.pending) { tell('有消息正在提交或等待确认，请先处理后切换会话。'); return; }
                state.epoch++; state.canvas=canvas; state.session=null; state.run=null; state.messages=[]; state.refsKey=null; state.failures=0; clearTimeout(pollTimer); renderHistory(); renderRun();
                restoreOptions({persona_id:'general',image_concurrency:2});try{restoreOptions(JSON.parse(root.localStorage.getItem('smart-agent-options:'+canvas)));}catch{}
                guarded(async () => { await listSessions(); schedulePoll(0); });
            }
            const ids = context.selected_node_ids || [], key=JSON.stringify(ids);
            if (force || key !== state.refsKey) { state.refsKey=key; state.refs=ids.map(i => (context.nodes || []).find(n => n.id === i) || {id:i,title:'引用节点'}); renderRefs(); }
            const generationSettings = context.generation || {};
            const generationProvider = generationSettings.apiKind === 'video' ? generationSettings.videoProvider || generationSettings.provider_id : generationSettings.provider_id;
            if(!generationEditor && state.providers.length) {
                const video=generationSettings.apiKind==='video';
                const aliases={square:'1:1',portrait:'2:3',landscape:'3:2',portrait43:'3:4',landscape43:'4:3',story:'9:16',wide:'16:9',ultrawide:'21:9'};
                const initial={...generationSettings,provider_id:generationProvider,model:video?generationSettings.videoModel:generationSettings.model,ratio:video?generationSettings.videoAspect:aliases[generationSettings.ratio] || generationSettings.ratio,resolution:video?generationSettings.videoResolution:generationSettings.resolution,duration:generationSettings.videoDuration,version:generationSettings.imageVersion};
                const aliasesSettings=video?{videoGenerateAudio:'generate_audio',videoUseFrameRoles:'use_frame_roles',videoMultimodal:'multimodal',videoReturnLastFrame:'return_last_frame',videoWatermark:'watermark',videoCameraFixed:'camerafixed',videoTrustedAsset:'trusted_asset',videoEnhancePrompt:'enhance_prompt',videoEnableUpsample:'enable_upsample'}:{outputFormat:'output_format',outputCompression:'output_compression'};
                Object.entries(aliasesSettings).forEach(([from,to])=>{if(generationSettings[from]!==undefined)initial[to]=generationSettings[from];});
                if(initial.transparent_background)initial.background='transparent';
                initial.defaults={};
                if(!video && generationSettings.videoProvider && generationSettings.videoModel)initial.defaults.video={apiKind:'video',provider_id:generationSettings.videoProvider,model:generationSettings.videoModel,ratio:generationSettings.videoAspect,resolution:generationSettings.videoResolution,duration:generationSettings.videoDuration};
                if(video && generationSettings.provider_id && generationSettings.model)initial.defaults.image={apiKind:'image',provider_id:generationSettings.provider_id,model:generationSettings.model,ratio:aliases[generationSettings.ratio] || generationSettings.ratio,resolution:generationSettings.resolution,background:generationSettings.background,version:generationSettings.imageVersion};
                generationEditor=parameterEditor(initial);generationCanvas=canvas;generationText.replaceChildren(generationEditor.box);generationModels.replaceChildren(generationEditor.identity);generationEditor.start();
            } else if(generationEditor && generationCanvas!==canvas){generationEditor.dispose();generationEditor=null;generationText.replaceChildren();await syncContext(false);}
        }
        function schedulePoll(delay) { clearTimeout(pollTimer); if (state.open && state.run && !disposed) pollTimer=setTimeout(poll,delay ?? Math.min(15000,2000 * 2 ** state.failures)); }
        async function poll() {
            if (!state.open || !state.run || disposed) return;
            if (state.busy) { schedulePoll(); return; }
            const epoch=state.epoch, runId=state.run.id, session=state.session;
            try {
                const data=await api(`/runs/${encodeURIComponent(runId)}`);
                if (disposed || epoch !== state.epoch || runId !== state.run?.id || session !== state.session) return;
                const changed=state.run.revision !== data.run.revision; state.run=data.run; state.failures=0; renderRun();
                if(!shouldPoll(state.run) && notice.textContent==='正在根据修改要求重新规划…')tell(state.run.status==='needs_input'?'请在“待你回答”中补充具体问题。':state.run.status==='awaiting_approval'?'新计划已准备好，请检查并批准。':'');
                if (changed) { await refreshCanvas(); await loadHistory(); }
            } catch (err) { state.failures=Math.min(4,state.failures+1); tell(err.message + ' 正在查询原任务，不会重新提交。'); }
            finally { if (shouldPoll(state.run) || state.failures) schedulePoll(); }
        }
        function positionEntry() {
            positionMenu();
            const minimap=doc.getElementById('minimap');
            if(!minimap) return;
            const rect=minimap.getBoundingClientRect();
            if(!rect.width || !rect.height) return;
            const arrange=doc.getElementById('smartArrangeBtn');
            const arrangeRect=arrange?.classList.contains('visible') ? arrange.getBoundingClientRect() : null;
            const top=arrangeRect?.height ? Math.min(rect.top,arrangeRect.top) : rect.top;
            entry.style.right=`${Math.max(12,root.innerWidth-rect.right)}px`;
            entry.style.bottom=`${Math.max(12,root.innerHeight-top+8)}px`;
            entry.style.maxWidth=`${rect.width}px`;
        }
        function shortcut(event) {
            if(!isToggleShortcut(event)) return;
            event.preventDefault(); event.stopImmediatePropagation();
            setOpen(!state.open);
        }
        root.addEventListener('keydown',shortcut,true);
        root.addEventListener('resize',positionEntry);
        const anchorObserver=root.ResizeObserver ? new root.ResizeObserver(positionEntry) : null;
        const arrangeObserver=root.MutationObserver ? new root.MutationObserver(positionEntry) : null;
        [doc.getElementById('minimap'),doc.getElementById('smartArrangeBtn'),form].filter(Boolean).forEach(target=>{
            anchorObserver?.observe(target);
            arrangeObserver?.observe(target,{attributes:true,attributeFilter:['class','style']});
        });
        positionEntry();
        function setOpen(open) {
            state.open=open; panel.hidden=!open; entry.setAttribute('aria-expanded',String(open)); entry.classList.toggle('active',open);
            if (open) { syncContext(true); clearInterval(contextTimer); contextTimer=setInterval(() => syncContext(false),1500); schedulePoll(0); input.focus(); }
            else {closeMenu();clearTimeout(pollTimer); clearInterval(contextTimer); entry.focus(); }
        }
        panel.addEventListener('keydown', e => { e.stopPropagation(); if(e.key==='Escape') { e.preventDefault();if(openMenu)closeMenu(true);else setOpen(false); } });
        ['pointerdown','mousedown','wheel','dblclick'].forEach(type => panel.addEventListener(type,e => e.stopPropagation()));
        function dispose() { disposed=true;doc.removeEventListener('pointerdown',dismissMenus,true); root.removeEventListener('keydown',shortcut,true); root.removeEventListener('resize',positionEntry); anchorObserver?.disconnect(); arrangeObserver?.disconnect(); clearTimeout(pollTimer); clearInterval(contextTimer); unsubscribe?.(); requests.forEach(c => c.abort()); }
        root.addEventListener('pagehide',dispose,{once:true});
        api('/providers').then(data => {
            if(disposed) return; state.providers=data.providers || []; provider.replaceChildren();
            state.providers.forEach(p => { const o=el('option','',p.name || p.id); o.value=p.id; provider.append(o); });
            const llm=bridge()?.getContext?.()?.llm;
            if (llm?.provider_id && state.providers.some(p => p.id === llm.provider_id)) provider.value=llm.provider_id;
            renderModels(llm?.model);
            if (!state.providers.length) tell('尚未配置对话平台，请在应用设置中配置后重新打开画布。');
        }).catch(err => tell(err.message));
        loadRoles().catch(err=>tell(err.message));
        renderRefs(); renderHistory(); renderDisabled();
    }
    if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded',mount,{once:true}); else mount();
})(typeof window !== 'undefined' ? window : globalThis);
