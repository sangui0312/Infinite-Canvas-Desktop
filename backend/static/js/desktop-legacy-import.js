(() => {
  'use strict';
  if (!window.desktopBridge) return;
  const init = () => {
    const group=document.querySelector('#settings-fold-group');if(!group)return;
    const entry=document.createElement('button');entry.className='side-pill';entry.type='button';entry.id='desktop-legacy-import';entry.title='从旧 Web 导入';
    entry.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4"/></svg><span class="side-pill-text">从旧 Web 导入</span>';group.appendChild(entry);
    const modal=document.createElement('dialog');modal.id='desktop-legacy-modal';
    modal.style.cssText='border:1px solid #d8dee8;border-radius:18px;padding:26px;width:min(640px,87vw);max-height:85vh;color:var(--text-primary,#182031);background:var(--bg-primary,#fff)';
    modal.innerHTML='<h2 style="margin:0 0 16px;font-size:20px">从旧 Web 导入</h2><p style="font-size:13px;line-height:1.8">旧 Web 需保持运行。选择项目后下载画布及素材，完成后新建项目；请暂停编辑待迁移项目。API 密钥和登录状态不导入。</p><div style="display:flex;gap:8px"><input id="legacy-address" aria-label="旧 Web 地址" placeholder="http://192.168.124.30:3000" style="flex:1;min-width:0;padding:10px;border:1px solid #cbd2de;border-radius:8px;background:transparent;color:inherit"><button data-action="detect">检测连接</button></div><p id="legacy-source"></p><div id="legacy-projects" style="max-height:180px;overflow:auto"></div><progress id="legacy-progress" style="width:100%" max="100" value="0" hidden></progress><p id="legacy-status" role="status" style="font-size:13px;white-space:pre-wrap"></p><details id="legacy-missing" hidden><summary>缺失素材明细</summary><div style="max-height:150px;overflow:auto;font-size:12px;overflow-wrap:anywhere"></div></details><div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:18px"><button data-action="all" hidden>全选</button><button data-action="start" disabled>开始下载</button><button data-action="retry" hidden>重试下载</button><button data-action="commit" hidden>完成导入</button><button data-action="close" style="margin-left:auto">关闭</button></div>';
    for(const b of modal.querySelectorAll('button')){b.type='button';b.style.cssText+=';padding:8px 12px;border:1px solid #cbd2de;border-radius:8px;background:transparent;color:inherit;cursor:pointer';}
    document.body.appendChild(modal);
    const $=sel=>modal.querySelector(sel),status=$('#legacy-status');let source=null,job=null,timer=null,working=false;
    async function request(route,body){const response=await fetch('/desktop/legacy/'+route,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'操作未完成，请重试。');return data;}
    function render(){
      const phase=job?.phase;const running=['collecting','downloading','committing'].includes(phase);
      $('[data-action="detect"]').disabled=working||running;
      $('[data-action="start"]').disabled=working||running||!source||!$('#legacy-projects input:checked');
      $('[data-action="retry"]').hidden=!['error','ready'].includes(phase);
      $('[data-action="commit"]').hidden=phase!=='ready';
      $('[data-action="commit"]').textContent=job?.missing?.length?`导入已有内容（缺失 ${job.missing.length} 项）`:'完成导入';
      const progress=$('#legacy-progress');progress.hidden=!job;progress.value=job?.total?100*job.done/job.total:0;
      if(job){
        const labels={collecting:'正在读取项目与画布…',downloading:`正在下载素材 ${job.done}/${job.total}，已取得 ${(job.bytes/1048576).toFixed(1)} MB`,ready:`下载完成：${job.total-job.missing.length} 项素材可用，${job.missing.length} 项缺失。${job.missing.length?'可先重试下载，或选择导入已有内容。':'点击“完成导入”创建本地项目。'}`,committing:'正在创建本地项目…',error:job.error,completed:`已导入 ${job.result?.projects||0} 个项目、${job.result?.canvases||0} 个画布和 ${job.result?.resources||0} 项素材。${job.result?.missing?`另有 ${job.result.missing} 项缺失，请查看明细。`:'素材已保存在本地，可关闭旧 Web 使用。'}`};status.textContent=job.error||labels[phase]||'';
        const detail=$('#legacy-missing');detail.hidden=!job.missing?.length;detail.querySelector('div').replaceChildren();
        for(const missing of (job.missing||[]).slice(0,100)){const line=document.createElement('p');line.textContent=missing.url+'：'+missing.reason;detail.querySelector('div').append(line);}
      }
    }
    async function poll(){if(!job)return;try{job=await request('jobs/'+job.id);render();if(['collecting','downloading','committing'].includes(job.phase))timer=setTimeout(poll,1000);}catch(error){status.textContent=error.message;}}
    entry.addEventListener('click',()=>{modal.showModal();if(job&&!timer)poll();});
    $('#legacy-projects').addEventListener('change',render);
    modal.addEventListener('click',async event=>{
      const action=event.target.closest('button')?.dataset.action;if(!action)return;
      if(action==='close'){modal.close();return;}
      if(action==='all'){for(const checkbox of modal.querySelectorAll('#legacy-projects input'))checkbox.checked=true;render();return;}
      if(working)return;working=true;render();
      try{
        if(action==='detect'){
          source=null;job=null;$('#legacy-projects').replaceChildren();
          source=await request('detect',{address:$('#legacy-address').value.trim()});job=null;clearTimeout(timer);timer=null;
          $('#legacy-source').textContent=`已识别画布程序 v${source.version}，${source.projects.length} 个项目`;
          $('#legacy-projects').replaceChildren();
          for(const project of source.projects){const label=document.createElement('label');label.style.cssText='display:flex;gap:8px;padding:8px';const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.value=project.id;const text=document.createElement('span');text.textContent=`${project.name}（${project.canvas_count} 个画布）`;label.append(checkbox,text);$('#legacy-projects').append(label);}
          $('[data-action="all"]').hidden=false;status.textContent='请选择要导入的项目。';
        }else if(action==='start'){
          job=await request('start',{source:source.source,projects:[...modal.querySelectorAll('#legacy-projects input:checked')].map(input=>input.value)});
          localStorage.setItem('desktop-legacy-job',job.id);poll();
        }else if(action==='retry'){job=await request('jobs/'+job.id+'/retry',{});clearTimeout(timer);poll();}
        else if(action==='commit'){
          const missing=Boolean(job.missing.length);job={...job,phase:'committing'};render();
          job=await request('jobs/'+job.id+'/commit',{allow_missing:missing});
          const frame=document.querySelector('#frame-canvas');
          if(frame?.contentWindow?.location.pathname==='/static/canvas-list.html')frame.contentWindow.location.reload();
        }
      }catch(error){status.textContent=error.message;if(job){job.error=error.message;if(job.phase==='committing')job.phase='ready';}}
      finally{working=false;render();}
    });
    const previous=localStorage.getItem('desktop-legacy-job');if(previous&&/^[a-f0-9]{32}$/.test(previous)){job={id:previous};request('jobs/'+previous).then(value=>{job=value;render();if(['collecting','downloading','committing'].includes(job.phase))poll();}).catch(()=>{job=null;localStorage.removeItem('desktop-legacy-job');});}
  };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
