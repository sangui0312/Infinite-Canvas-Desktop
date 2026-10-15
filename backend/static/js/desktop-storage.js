/* Desktop-only personal-data settings. Paths and file operations stay in main. */
(() => {
  'use strict';
  const bridge = window.desktopBridge?.storage;
  if (!bridge) return;
  const init = () => {
    const group = document.getElementById('settings-fold-group');
    if (!group) return;
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'side-pill'; button.id = 'desktop-storage-settings'; button.title = '数据存储';
    button.innerHTML = '<svg fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2" aria-hidden="true"><path d="M3 7h18v13H3zM3 7V4h7l2 3"/></svg><span class="side-pill-text">数据存储</span>';
    group.appendChild(button);
    const modal = document.createElement('dialog'); modal.id = 'desktop-storage-modal';
    modal.style.cssText = 'border:1px solid #d8dee8;border-radius:18px;padding:26px;width:min(560px,85vw);color:var(--text-primary,#182031);background:var(--bg-primary,#fff);box-shadow:0 18px 70px #0002';
    modal.innerHTML = '<h2 style="margin:0 0 18px;font-size:20px">数据存储</h2><p>个人资料位置</p><p id="desktop-storage-path" style="padding:14px;background:#8090a010;border-radius:10px;overflow-wrap:anywhere"></p><p style="font-size:13px;line-height:1.8">画布、素材、API 配置、工作流及界面设置保存在此处。更改位置时会重启并复制校验，原目录保留为备份。</p><p id="desktop-storage-backup" style="font-size:12px;overflow-wrap:anywhere"></p><p id="desktop-storage-status" role="status" style="font-size:13px"></p><div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:20px"><button type="button" data-action="open">打开文件夹</button><button type="button" data-action="change">更改位置</button><button type="button" data-action="reset">恢复默认位置</button><button type="button" data-action="close" style="margin-left:auto">关闭</button></div>';
    for (const item of modal.querySelectorAll('button')) item.style.cssText += ';padding:8px 13px;border-radius:9px;border:1px solid #cbd2de;background:transparent;color:inherit;cursor:pointer';
    document.body.appendChild(modal);
    const status = modal.querySelector('#desktop-storage-status');
    const refresh = async () => {
      const info = await bridge.info();
      modal.querySelector('#desktop-storage-path').textContent = info.path;
      modal.querySelector('[data-action="reset"]').disabled = info.isDefault;
      modal.querySelector('#desktop-storage-backup').textContent = info.backups?.length ? `迁移备份：${info.backups.join('；')}（卸载时不会自动删除）` : '';
      status.textContent = info.legacy ? '当前使用旧版共用目录，迁移后可独立管理资料。' : '';
    };
    button.addEventListener('click', async () => { await refresh(); modal.showModal(); });
    modal.addEventListener('click', async event => {
      const action = event.target.closest('button')?.dataset.action;
      if (!action) return;
      if (action === 'close') { modal.close(); return; }
      const buttons = [...modal.querySelectorAll('button')]; buttons.forEach(item => item.disabled = true);
      try {
        const result = await bridge[action]();
        status.textContent = result?.error || (result?.restarting ? '正在重启并迁移，请等待软件重新打开。' : '');
      } catch { status.textContent = '操作未完成，原资料保持不变。'; }
      finally { buttons.forEach(item => item.disabled = false); const info = await bridge.info(); modal.querySelector('[data-action="reset"]').disabled = info.isDefault; }
    });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
