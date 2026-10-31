/* Shared desktop media lifecycle UI and canvas protection. No filesystem paths are deletion targets. */
(function (root, factory) {
  'use strict';
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.DesktopMediaCleanup = api;
})(typeof window === 'undefined' ? null : window, function (root) {
  'use strict';
  const id = () => root?.crypto?.randomUUID?.() || `media-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const labels = {candidate:'可清理', eligible:'可清理', referenced:'仍被引用', retained:'主动保留', kept:'主动保留', protected:'暂时保护', leased:'暂时保护', uncertain:'无法确认', unknown:'无法确认',observing:'观察期内'};
  const messages = {asset_protected:'素材仍被引用或临时保护',referenced:'素材仍被引用',protections_unavailable:'使用保护尚未准备完成',active_tasks_or_reads:'存在正在执行的任务或文件读取',stale_scan:'预览已变化，请重新扫描',incomplete_references:'引用检查不完整，文件已保留',unresolved_session:'有未确认的编辑会话，文件已保留',another_process:'其他程序正在使用此资料目录',file_changed:'文件已变化，请重新扫描',observation_period:'素材仍在观察期内',confirmation_required:'永久删除需要再次确认',unknown_asset:'素材尚未登记',asset_unavailable:'素材当前不可用',missing_file:'素材文件缺失',source_unreadable:'记录无法读取',source_directory_unreadable:'记录目录无法读取',unresolved_media:'部分素材地址无法确认',root_offline:'素材目录离线',root_unreadable:'素材目录无法读取',cancelled:'扫描已取消',failed:'操作未完成',blocked:'受到使用保护',retained:'文件已保留',skipped:'文件已跳过',file_restored_entry_pending:'文件已恢复，关联条目尚待恢复核对'};
  const messageFor = code => messages[code] || (typeof code === 'string' && !/^[a-z0-9_]+$/.test(code) ? code : '操作未完成，文件已保留');
  function canvasSourceId(value) { return String(value || '').replace(/\\/g,'/').split('/').pop().replace(/\.json$/i,''); }
  function localReference(value, origin = root?.location?.origin || 'http://localhost') {
    if (typeof value !== 'string' || value.length > 16384) return '';
    const text = value.trim();
    if (/^[a-z]:[\\/]/i.test(text)) return text;
    try {
      const url = new URL(text, origin);
      if (url.origin !== origin || !/^\/(?:assets\/|output\/|api\/storage-files\/)/i.test(url.pathname)) return '';
      return url.pathname + url.search;
    } catch { return ''; }
  }
  function collectReferences(value, origin) {
    const references = new Set(), seen = new WeakSet(), todo = [value];
    while (todo.length) {
      const current = todo.pop();
      if (typeof current === 'string') {
        const ref = localReference(current, origin); if (ref) references.add(ref);
        // Unsaved message/HTML fields can contain local media inside Markdown or attributes.
        for(const match of current.matchAll(/(?:https?:\/\/[^\s"<>]+)?\/(?:assets|output|api\/storage-files)\/[^\s"<>\]\)]+/g)) {
          const embedded = localReference(match[0], origin); if(embedded) references.add(embedded);
        }
      }
      else if (current && typeof current === 'object' && !seen.has(current)) {
        seen.add(current);
        if (ArrayBuffer.isView(current) || current instanceof ArrayBuffer) continue;
        if (current instanceof Map || current instanceof Set) todo.push(...current.values());
        else todo.push(...Object.values(current));
      }
    }
    return [...references].sort();
  }
  async function request(path, data, options = {}) {
    const response = await (options.fetch || root.fetch.bind(root))(path, {
      method: options.method || (data === undefined ? 'GET' : 'POST'),
      credentials:'same-origin', cache:'no-store', signal:options.signal, keepalive:options.keepalive,
      ...(data === undefined ? {} : {headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)})
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || result.ok === false || result.success === false) {
      const detail = result.detail || result.error;
      throw new Error(typeof detail === 'string' ? messageFor(detail) : detail?.message || result.message || `操作未完成 (${response.status})`);
    }
    return result;
  }
  function createSession(options) {
    const sessionId = options.id || id();
    const send = options.request || request;
    let revision = 0, version = 0, timer = null, destroyed = false, available = false, savedRevision = -1;
    let chain = Promise.resolve(), lastError = '', pending = 0;
    const endpoint = `/api/media-sessions/${encodeURIComponent(sessionId)}`;
    function notify(error) {
      available = !error; lastError = error ? String(error.message || error) : '';
      options.onStatus?.({available,error:lastError});
    }
    function refs(extra) { return collectReferences([options.snapshot(), extra], options.origin); }
    function enqueue(task) {
      pending++;
      const next = chain.catch(() => {}).then(async () => { try { return await task(); } finally { pending--; } });
      chain = next;
      return next;
    }
    function sync(extra, syncOptions = {}) {
      // Capture before queuing so an edit cannot rewrite an earlier version's protection set.
      const references = refs(extra), nextVersion = ++version;
      return enqueue(async () => {
        try {
          const result = await send(`${endpoint}/references`, {operation_id:id(), version:nextVersion,references,closed:false,...syncOptions}, {method:'PUT'});
          if(result.uncertain) throw new Error('部分素材使用关系无法确认，已暂停清理资格');
          notify(null); return result;
        } catch (error) { notify(error); throw error; }
      });
    }
    function changed() {
      revision++;
      if (destroyed || timer !== null) return;
      const flush = () => {
        if(destroyed) return;
        if(pending) { timer = setTimeout(flush, options.debounceMs ?? 100); return; }
        timer = null; sync().catch(() => {});
      };
      timer = setTimeout(flush, options.debounceMs ?? 100);
    }
    async function ensure(snapshot) {
      if (destroyed) throw new Error('素材保护会话已结束');
      await sync(snapshot);
      if(destroyed) throw new Error('素材保护会话已结束');
      const references = refs(snapshot), nextVersion = ++version;
      return enqueue(async () => {
        try {
          const result = await send(`${endpoint}/ensure`, {operation_id:id(),version:nextVersion,references});
          if (result.missing?.length || result.failed?.length || result.complete === false) throw new Error('素材缺失或恢复失败，当前画布已保留');
          notify(null); return result;
        } catch (error) { notify(error); throw error; }
      });
    }
    const heartbeat = options.heartbeatMs === 0 ? null : setInterval(() => { if (!destroyed && !pending) sync().catch(() => {}); }, options.heartbeatMs || 15000);
    // Retain the final protection set if the page disappears during a save. The backend handles lost sessions conservatively.
    function destroy() { destroyed = true; clearTimeout(timer); clearInterval(heartbeat); }
    function markSaved(acknowledgedRevision = revision) { if(acknowledgedRevision === revision) savedRevision = acknowledgedRevision; }
    async function close() {
      // Closing cannot certify unsaved input, failed saves or a pending edit. Lost sessions remain protected.
      const closed = available && pending === 0 && savedRevision === revision;
      const payload = {operation_id:id(),version:++version,references:refs(),closed};
      destroy();
      return send(`${endpoint}/references`, payload, {method:'PUT',keepalive:true});
    }
    return {changed,sync,ensure,destroy,markSaved,close,get revision(){return revision;},get available(){return available;},get error(){return lastError;},id:sessionId};
  }
  function formatBytes(value) {
    let size = Math.max(0, Number(value) || 0), unit = 0;
    const units = ['B','KB','MB','GB','TB'];
    while(size >= 1024 && unit < units.length - 1) { size /= 1024; unit++; }
    return `${size.toFixed(unit ? 1 : 0)} ${units[unit]}`;
  }
  function resultMessage(result) {
    const rows = result.results || result.items || [];
    if (!rows.length) return result.message || '操作已完成，请重新扫描确认当前状态。';
    const failed = rows.filter(row => row.ok === false || row.success === false || ['failed','blocked','protected','referenced','error','retained','skipped','file_restored_entry_pending'].includes(row.status));
    return `处理 ${rows.length} 项，保留或未完成 ${failed.length} 项。${failed.map(row => messageFor(row.reason || row.error || row.code || row.status)).slice(0,3).join('；')}${rows.some(row => row.conflict) ? '同名文件已保留，素材恢复到新位置。' : ''}`;
  }
  function mount(container) {
    if (!root || !container || container.dataset.mediaCleanupMounted) return null;
    container.dataset.mediaCleanupMounted = 'true';
    const doc = container.ownerDocument;
    const section = doc.createElement('section'); section.className = 'media-cleanup';
    section.innerHTML = '<h3>素材清理</h3><label><input type="checkbox" data-mc="enabled"> 自动清理未引用素材</label><div class="media-cleanup-fields"><label>未引用保留 <select data-mc="days"><option value="7">7 天</option><option value="30" selected>30 天</option><option value="60">60 天</option><option value="90">90 天</option></select></label><label>素材回收站保留 <select data-mc="trash-days"><option value="7" selected>7 天</option><option value="14">14 天</option><option value="30">30 天</option><option value="60">60 天</option><option value="90">90 天</option></select></label><button type="button" data-mc="save">保存设置</button></div><p class="media-cleanup-note">首次启用从扫描确认后开始观察。移入同盘回收站不会立即释放空间；永久删除后才会释放空间。</p><p data-mc="status" role="status" aria-live="polite">正在读取清理设置…</p><div class="media-cleanup-toolbar"><button type="button" data-mc="scan">扫描预览</button><button type="button" data-mc="cancel" disabled>取消扫描</button><button type="button" data-mc="trash">素材回收站</button></div><div class="media-cleanup-toolbar"><select data-mc="filter" aria-label="素材状态"><option value="">全部状态</option><option value="eligible">可清理</option><option value="referenced">仍被引用</option><option value="kept">主动保留</option><option value="protected">暂时保护</option><option value="unknown">无法确认</option><option value="observing">观察期内</option></select><button type="button" data-mc="previous" disabled>上一页</button><span data-mc="page"></span><button type="button" data-mc="next" disabled>下一页</button></div><div data-mc="rows" class="media-cleanup-rows"></div><div class="media-cleanup-toolbar"><button type="button" data-mc="action" disabled>将本页所选移入素材回收站</button><button type="button" data-mc="restore" hidden>恢复所选素材</button><button type="button" data-mc="purge" hidden>永久删除所选素材</button></div>';
    container.appendChild(section);
    const el = name => section.querySelector(`[data-mc="${name}"]`);
    let generation = 0, pageRequest = 0, pollTimer = null, scanId = '', scan = null, offset = 0, mode = 'scan', rows = [], total = 0, busy = false, dirty = false;
    const limit = 40;
    const selected = new Set();
    const status = text => { el('status').textContent = text; };
    const safe = task => Promise.resolve().then(task).catch(error => status(error.message || '操作未完成，请重试。'));
    function selectionButtons() {
      const has = selected.size > 0;
      el('action').disabled = busy || !has || !scan?.complete;
      el('restore').disabled = busy || !has; el('purge').disabled = busy || !has;
    }
    function draw() {
      selected.clear(); el('rows').replaceChildren();
      for (const row of rows) {
        const line = doc.createElement('div'); line.className = 'media-cleanup-row';
        const key = mode === 'trash' ? row.operation_id || row.id : row.asset_id || row.id;
        const checkbox = doc.createElement('input'); checkbox.type = 'checkbox'; checkbox.setAttribute('aria-label','选择素材');
        const category = row.category || row.classification || row.status;
        checkbox.disabled = !key || (mode === 'scan' && !['candidate','eligible','observing'].includes(category));
        checkbox.addEventListener('change', () => { checkbox.checked ? selected.add(key) : selected.delete(key); selectionButtons(); });
        const description = doc.createElement('span');
        description.textContent = `${row.name || row.relative_path || row.path || key || '无法确认的素材'} · ${formatBytes(row.size || row.size_bytes)} · ${mode === 'trash' ? '素材回收站' : labels[category] || '无法确认'}${row.reason ? ` · ${row.reason}` : ''}`;
        line.append(checkbox, description);
        if(mode === 'trash') {
          const metadata = doc.createElement('small');
          const date = value => value ? new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString() : '未知';
          metadata.textContent = `原位置：${row.original_path || row.rel || row.relative_path || '未知'}；原因：${row.reason || '手动清理'}；移入时间：${date(row.created || row.trashed_at || row.created_at)}；到期时间：${date(row.expires || row.expires_at)}`;
          description.appendChild(metadata);
        } else if (key) {
          const referenceButton = doc.createElement('button'); referenceButton.type = 'button'; referenceButton.textContent = '使用位置';
          referenceButton.addEventListener('click', () => safe(async () => {
            const result = await request(`/api/media-assets/${encodeURIComponent(key)}/references`);
            let detail = line.querySelector('.media-cleanup-references');
            if (!detail) { detail = doc.createElement('div'); detail.className = 'media-cleanup-references'; line.appendChild(detail); }
            detail.replaceChildren();
            const sources = Array.isArray(result.references) ? result.references : result.references?.references || result.items || [];
            if (!sources.length) detail.textContent = result.complete === false ? '引用检查不完整，不能认定为无引用。' : '没有持久引用；临时保护和最终检查仍可能阻止清理。';
            for(const source of sources) {
              const kind = source.kind || source.source_type || source.type || '';
              const sourceLabels = {canvas:'画布',smart_canvas:'智能画布',conversation:'聊天记录',history:'生成历史',workflow:'工作流',asset_library:'资产库',agent:'Agent 记录',import:'导入任务',session:'编辑会话'};
              const item = doc.createElement('p'); item.textContent = `${sourceLabels[kind] || '来源'}：${source.title || source.source_id || source.id || ''}${source.reason ? `（${messageFor(source.reason)}）` : ''}`;
              if (/canvas/.test(kind) && source.source_id && !/trash/.test(kind)) {
                const link = doc.createElement('a'); link.textContent = '打开画布';
                link.href = `/static/${/smart/.test(kind) ? 'smart-canvas' : 'canvas'}.html?id=${encodeURIComponent(canvasSourceId(source.source_id))}`;
                link.target = '_blank'; link.rel = 'noopener'; item.append(' ',link);
              }
              detail.appendChild(item);
            }
          })); line.appendChild(referenceButton);
          const keepButton = doc.createElement('button'); keepButton.type = 'button';
          const updateKeepLabel = () => { keepButton.textContent = row.keep ? '取消保留' : '始终保留'; };
          updateKeepLabel();
          keepButton.addEventListener('click', () => safe(async () => {
            keepButton.disabled = true;
            try {
              await request(`/api/media-assets/${encodeURIComponent(key)}/keep`,{operation_id:id(),keep:!row.keep},{method:'PUT'});
              row.keep = row.keep ? 0 : 1; updateKeepLabel();
              scan = {...scan,complete:false}; selected.clear(); selectionButtons();
              status(row.keep ? '已设为始终保留。请重新扫描以刷新候选。' : '已取消主动保留。请重新扫描，未引用观察期将重新判断。');
            } finally { keepButton.disabled = false; }
          }));
          line.appendChild(keepButton);
        }
        el('rows').appendChild(line);
      }
      if (!rows.length) { const empty = doc.createElement('p'); empty.textContent = '当前页没有素材。'; el('rows').appendChild(empty); }
      el('page').textContent = `${offset + (rows.length ? 1 : 0)}–${offset + rows.length} / ${total}`;
      el('previous').disabled = busy || offset === 0; el('next').disabled = busy || offset + rows.length >= total;
      el('action').hidden = mode !== 'scan'; el('restore').hidden = mode !== 'trash'; el('purge').hidden = mode !== 'trash';
      el('filter').disabled = mode === 'trash'; selectionButtons();
    }
    function readPage(result) {
      rows = result.assets || result.items || result.operations || [];
      total = Number(result.total ?? rows.length);
      // Also accept an unpaged core response while keeping DOM work bounded.
      if (rows.length > limit) { total = rows.length; rows = rows.slice(offset, offset + limit); }
    }
    async function loadPage(token = generation) {
      const pageToken = ++pageRequest;
      clearTimeout(pollTimer);
      const result = mode === 'trash' ? await request(`/api/media-trash?offset=${offset}&limit=${limit}`) : await request(`/api/media-cleanup/scans/${encodeURIComponent(scanId)}?offset=${offset}&limit=${limit}&filter=${encodeURIComponent(el('filter').value)}`);
      if(token !== generation || pageToken !== pageRequest) return;
      if(mode === 'scan') {
        scan = result;
        const summary = result.summary || {};
        status(`${result.complete ? '扫描完成' : result.status === 'cancelled' ? '扫描已取消' : result.status === 'completed' ? '扫描结束，覆盖不完整' : '正在扫描'}；预计移入回收站 ${formatBytes(summary.trash_bytes || summary.candidate_bytes || summary.eligible_bytes)}，不会立即释放空间。${result.errors?.length ? `覆盖不完整：${result.errors.map(error => messageFor(typeof error === 'string' ? error : error.reason || error.message || error.code)).slice(0,5).join('；')}` : ''}`);
        const running = !result.complete && !['cancelled','failed','completed','incomplete'].includes(result.status);
        el('cancel').disabled = !running;
        if(running) pollTimer = setTimeout(() => safe(() => loadPage(token)), 750);
      } else status('素材回收站：恢复会重新建立素材可用性；永久删除不可撤销。');
      readPage(result); draw();
    }
    async function refreshSettings() {
      const result = await request('/api/media-cleanup/settings'), settings = result.settings || result;
      if(!dirty) { el('enabled').checked = !!settings.enabled; el('days').value = String(settings.retention_days || 30); el('trash-days').value = String(settings.trash_retention_days || 7); }
      el('enabled').disabled = settings.available === false || result.automatic_available === false || settings.automatic_available === false || result.ready === false;
      const scanned = result.last_successful_scan || settings.last_successful_scan;
      status(`自动清理${settings.enabled ? '已开启' : '已关闭'}。上次成功扫描：${scanned ? new Date(typeof scanned === 'number' ? scanned * 1000 : scanned).toLocaleString() : '尚未扫描'}。${el('enabled').disabled ? '保护接入尚未完成，暂不可开启自动清理。' : ''}`);
    }
    section.addEventListener('change', event => { if(['enabled','days','trash-days'].includes(event.target.dataset.mc)) dirty = true; });
    el('save').onclick = () => safe(async () => {
      const result = await request('/api/media-cleanup/settings',{operation_id:id(),enabled:el('enabled').checked,retention_days:Number(el('days').value),trash_retention_days:Number(el('trash-days').value)},{method:'PUT'});
      dirty = false; await refreshSettings(); if(result.message) status(result.message);
    });
    el('scan').onclick = () => safe(async () => {
      const token = ++generation; clearTimeout(pollTimer); mode = 'scan'; offset = 0; selected.clear();
      el('scan').disabled = true;
      try { const result = await request('/api/media-cleanup/scans',{operation_id:id()}); if(token !== generation) return; scanId = result.id || result.scan_id; if(!scanId) throw new Error('扫描未返回任务标识'); await loadPage(token); }
      finally { el('scan').disabled = false; }
    });
    el('cancel').onclick = () => safe(async () => {
      if(!scanId) return; ++generation; clearTimeout(pollTimer);
      await request(`/api/media-cleanup/scans/${encodeURIComponent(scanId)}/cancel`,{operation_id:id()});
      el('cancel').disabled = true; await loadPage();
    });
    el('trash').onclick = () => safe(async () => { ++generation; clearTimeout(pollTimer); mode = 'trash'; offset = 0; el('cancel').disabled = true; await loadPage(); });
    el('previous').onclick = () => safe(async () => { offset = Math.max(0,offset-limit); await loadPage(); });
    el('next').onclick = () => safe(async () => { offset += limit; await loadPage(); });
    el('filter').onchange = () => safe(async () => { offset = 0; if(scanId) await loadPage(); });
    async function action(kind) {
      if(busy || !selected.size) return;
      const ids = [...selected];
      const prompt = kind === 'purge' ? `永久删除所选 ${ids.length} 项素材？文件无法通过撤销恢复。仍被引用的素材将保留。` : kind === 'restore' ? `恢复所选 ${ids.length} 项素材？同名文件不会被覆盖。` : `将所选 ${ids.length} 项移入素材回收站？移入回收站不会立即释放磁盘空间。`;
      if(!root.confirm(prompt)) return;
      busy = true; selectionButtons();
      try {
        const result = kind === 'action' ? await request('/api/media-cleanup/actions',{operation_id:id(),scan_id:scanId,asset_ids:ids}) : await request(`/api/media-trash/${kind}`,{operation_id:id(),operation_ids:ids,...(kind === 'purge' ? {confirmed:true} : {})});
        const message = resultMessage(result);
        if(kind === 'action') { mode = 'trash'; offset = 0; }
        await loadPage(); status(message);
      } finally { busy = false; selectionButtons(); }
    }
    for(const kind of ['action','restore','purge']) el(kind).onclick = () => safe(() => action(kind));
    safe(refreshSettings);
    return {refresh:() => safe(refreshSettings),stop:() => { generation++;clearTimeout(pollTimer); }};
  }
  function bindCanvas(snapshot) {
    if(!root) return null;
    let warning = null;
    const session = createSession({
      snapshot:() => [snapshot(), [...root.document.querySelectorAll('img[src],video[src],source[src],audio[src]')].map(element => element.getAttribute('src'))],
      onStatus:state => {
        if(state.available) { warning?.remove(); warning = null; return; }
        if(!warning) { warning = root.document.createElement('div'); warning.className = 'media-cleanup-session-status'; warning.setAttribute('role','status'); root.document.body.appendChild(warning); }
        warning.textContent = '素材使用保护未同步，自动清理应暂停。撤销将在保护和素材恢复成功后继续。';
        root.dispatchEvent(new root.CustomEvent('media-protection-status',{detail:state}));
      }
    });
    root.addEventListener('pagehide', event => { if(event.persisted) session.sync().catch(() => {}); else session.close().catch(() => {}); });
    root.addEventListener('pageshow', event => { if(event.persisted) session.sync().catch(() => {}); });
    root.document.addEventListener('input', session.changed, true);
    root.document.addEventListener('change', session.changed, true);
    return session;
  }
  async function removalResponse(response) {
    const result = await response.json();
    if(!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : result.error || '移除操作未完成');
    if(result.results?.length) root?.dispatchEvent(new root.CustomEvent('media-removal-result',{detail:{message:resultMessage(result)}}));
    return result;
  }
  return {collectReferences,localReference,createSession,bindCanvas,formatBytes,resultMessage,mount,request,removalResponse,canvasSourceId};
});
