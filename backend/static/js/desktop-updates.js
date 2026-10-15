/* Desktop updates use only the isolated Electron bridge, never the web updater. */
(() => {
    'use strict';
    if (!window.desktopBridge) return;
    const repo = 'https://github.com/sangui0312/Infinite-Canvas-Desktop';
    const bridge = window.desktopBridge.updates;
    let state = { currentVersion: '0.3.4', phase: 'idle' };
    let localNotes = { version: '0.3.4', notes: '' };
    let notesPromise;
    let pending = false;
    let revision = 0;
    const previousFocus = new Map();
    const $ = id => document.getElementById(id);
    const en = () => window.StudioI18n?.lang?.() === 'en';
    const t = (zh, english) => en() ? english : zh;
    const busy = () => pending || ['checking', 'downloading', 'installing'].includes(state.phase);
    const newer = () => ['available', 'downloading', 'downloaded', 'installing'].includes(state.phase) ||
        (state.version && state.version !== state.currentVersion && state.notes);
    function text(id, value) { const el = $(id); if (el) el.textContent = value; }
    function list(id, value) {
        const el = $(id);
        if (!el) return;
        el.replaceChildren();
        if (value) {
            const li = document.createElement('li');
            li.textContent = String(value);
            li.style.whiteSpace = 'pre-wrap';
            li.style.overflowWrap = 'anywhere';
            el.append(li);
        }
    }
    function accept(next) {
        if (next && typeof next === 'object' && typeof next.phase === 'string') {
            state = { ...state, ...next };
            revision++;
        }
        render();
    }
    function render() {
        const current = state.currentVersion || '0.3.4';
        const version = newer() ? state.version : (localNotes.version || current);
        const notes = newer() ? String(state.notes || '') : String(localNotes.notes || '');
        const messages = {
            idle: t('点击检查 GitHub 上的桌面软件版本。', 'Check the desktop version on GitHub.'),
            checking: t('正在检查 GitHub Releases…', 'Checking GitHub Releases…'),
            available: t('发现新版本，请查看公告后下载。', 'A new version is available. Review the notes before downloading.'),
            'not-available': t('当前已是最新桌面版本。', 'You have the latest desktop version.'),
            downloading: state.route === 'files' ? t('正在下载功能更新文件…', 'Downloading changed program files…') : t('正在下载桌面安装更新，优先使用差分…', 'Downloading the desktop installer, preferring differential transfer…'),
            downloaded: t('下载及完整性校验完成。点击“重启并安装”继续。', 'Download and integrity verification complete. Restart and install to continue.'),
            installing: t('正在准备重启并安装…', 'Preparing to restart and install…'),
            error: String(state.error || t('更新失败，请检查网络后重试。', 'Update failed. Check your network and retry.'))
        };
        text('project-update-title', t('软件更新', 'Software update'));
        text('project-update-copy', t(`当前版本 ${current}。更新来源：GitHub Releases。${state.route === 'files' ? '本次仅下载变化的程序文件，校验后重启后台服务。' : '本次使用桌面安装包，优先差分下载，不可用时下载完整安装包。'}个人资料始终保留。`, `Current version ${current}. Source: GitHub Releases. ${state.route === 'files' ? 'Only changed program files will be downloaded.' : 'Installer updates prefer differential download and fall back to the full installer.'} Personal data is retained.`));
        text('sidebar-update-version', current);
        text('project-update-notes-title', state.releaseName || t('桌面版本公告', 'Desktop release notes'));
        text('project-update-notes-version', version || '');
        text('sidebar-notes-version', version || '');
        list('project-update-notes-list', notes);
        list('sidebar-notes-list', notes);
        if ($('project-update-notes')) $('project-update-notes').hidden = false;
        for (const id of ['project-update-notes-empty', 'sidebar-notes-empty']) {
            if ($(id)) { $(id).hidden = !!notes; $(id).textContent = t('此版本暂无公告。', 'No release notes for this version.'); }
        }
        if ($('sidebar-notes-hint')) {
            $('sidebar-notes-hint').hidden = false;
            text('sidebar-notes-hint', newer() ? t(`GitHub 新版 ${version} · 当前 ${current}`, `GitHub release ${version} · current ${current}`) : t('当前桌面版本公告', 'Current desktop release notes'));
        }
        if ($('update-source-list')) { $('update-source-list').hidden = true; $('update-source-list').style.display = 'none'; }
        if ($('project-update-rollback-btn')) { $('project-update-rollback-btn').hidden = true; $('project-update-rollback-btn').style.display = 'none'; }
        if ($('project-update-copy')) $('project-update-copy').style.overflowWrap = 'anywhere';
        text('project-update-summary-title', messages[state.phase] || messages.idle);
        text('project-update-summary-text', state.notice || t('更新前保存画布并确认运行任务；文件更新启动失败会恢复旧程序。文件包不适用时自动切换为安装包更新。', 'Save canvases and confirm running tasks before applying. Failed file updates restore the previous program. Incompatible patches switch to installer updates.'));
        const summary = $('project-update-summary');
        if (summary) { summary.className = 'update-modal-summary ' + (state.phase === 'error' ? 'fail' : state.phase === 'downloaded' ? 'ok' : 'warn'); summary.setAttribute('role', 'status'); summary.setAttribute('aria-live', 'polite'); }
        let progress = $('desktop-update-progress');
        if (!progress && summary) {
            const wrap = document.createElement('div');
            progress = document.createElement('progress');
            progress.id = 'desktop-update-progress';
            progress.max = 100;
            progress.style.width = '100%';
            progress.setAttribute('aria-label', t('更新下载进度', 'Update download progress'));
            const label = document.createElement('p');
            label.id = 'desktop-update-progress-label';
            wrap.append(progress, label);
            summary.after(wrap);
        }
        if (progress) {
            const visible = ['downloading', 'downloaded'].includes(state.phase);
            progress.parentElement.hidden = !visible;
            const percent = state.phase === 'downloaded' ? 100 : Math.min(100, Math.max(0, Number(state.percent) || 0));
            progress.value = percent;
            const mb = bytes => (Math.max(0, Number(bytes) || 0) / 1048576).toFixed(1);
            text('desktop-update-progress-label', `${percent.toFixed(1)}% · ${mb(state.transferred)} / ${mb(state.total)} MB`);
        }
        const sourceLink = $('full-install-download-btn');
        if (sourceLink) { sourceLink.href = repo + '/releases'; sourceLink.style.overflowWrap = 'anywhere'; }
        const checkButton = $('connectivity-test-btn');
        if (checkButton) checkButton.disabled = busy();
        text('connectivity-test-text', t('检查更新', 'Check for updates'));
        const confirmButton = $('project-update-confirm-btn');
        if (confirmButton) confirmButton.disabled = busy() || !['available', 'downloaded', 'error'].includes(state.phase);
        text('project-update-confirm-text', state.phase === 'downloaded' ? t('重启并安装', 'Restart and install') : state.phase === 'error' ? t('重试检查', 'Retry check') : t('下载更新', 'Download update'));
        text('project-update-cancel-text', state.phase === 'downloading' ? t('关闭（后台下载）', 'Close (download continues)') : t('取消', 'Cancel'));
        text('update-now-text', t('软件更新', 'Software update'));
        $('update-now-text')?.removeAttribute('data-i18n');
        const side = $('sidebar-server-update-btn');
        if (side) {
            side.title = t('从 GitHub 更新桌面软件', 'Update desktop software from GitHub');
            const label = side.querySelector('span');
            if (label) label.textContent = t('软件更新', 'Software update');
            side.classList.toggle('has-update', !!newer());
        }
        $('update-now-btn')?.setAttribute('title', t('软件更新', 'Software update'));
        // These fixed labels belong to the web updater's translation keys.
        $('project-update-modal')?.querySelector('[data-i18n="update.available"]')?.removeAttribute('data-i18n');
        const kicker = $('project-update-modal')?.querySelector('.studio-modal-kicker');
        if (kicker) kicker.textContent = 'GitHub Releases';
    }
    async function loadNotes() {
        if (!notesPromise) notesPromise = fetch('/static/desktop-update-notes.json', { cache: 'no-store' })
            .then(response => { if (!response.ok) throw new Error('notes'); return response.json(); })
            .then(data => { if (data && typeof data.notes === 'string') localNotes = { version: String(data.version || state.currentVersion), notes: data.notes }; })
            .catch(() => {}).finally(render);
        return notesPromise;
    }
    async function invoke(method) {
        if (busy()) return;
        pending = true;
        if (method === 'check') state.phase = 'checking';
        render();
        try {
            if (typeof bridge?.[method] !== 'function') throw new Error(t('桌面更新组件不可用，请重新启动软件或从 GitHub Releases 下载完整安装包。', 'Desktop updater unavailable. Restart the app or download the complete installer from GitHub Releases.'));
            const result = await bridge[method]();
            if (result?.phase) accept(result);
            else accept(await bridge.getState());
        } catch (error) {
            accept({ phase: 'error', error: String(error?.message || error) });
        } finally { pending = false; render(); }
    }
    function show(id) {
        const modal = $(id);
        if (!modal) return;
        if (modal.hidden) previousFocus.set(id, document.activeElement);
        render();
        modal.hidden = false;
        modal.querySelector('.studio-modal-close')?.focus();
    }
    function close(id) {
        if (state.phase === 'installing') return;
        const modal = $(id);
        if (!modal || modal.hidden) return;
        modal.hidden = true;
        previousFocus.get(id)?.focus?.();
        previousFocus.delete(id);
    }
    window.DesktopUpdates = {
        render, loadNotes, close,
        open() { show('project-update-modal'); if (['idle', 'not-available', 'error'].includes(state.phase)) return invoke('check'); },
        openNotes() { show('sidebar-notes-modal'); return loadNotes(); },
        check(manual) { if (manual) show('project-update-modal'); return invoke('check'); },
        confirm() { if (state.phase === 'downloaded') return invoke('install'); if (state.phase === 'available') return invoke('download'); if (state.phase === 'error') return invoke('check'); }
    };
    // The original startup check runs on DOMContentLoaded; this script loads before it.
    const initialRevision = revision;
    try {
        const unsubscribe = bridge?.onState?.(accept);
        window.addEventListener('beforeunload', () => unsubscribe?.(), { once: true });
        Promise.resolve(bridge?.getState?.()).then(next => { if (revision === initialRevision) accept(next); }).catch(error => accept({ phase: 'error', error: String(error?.message || error) }));
    } catch (error) { accept({ phase: 'error', error: String(error?.message || error) }); }
    window.addEventListener('DOMContentLoaded', render, { once: true });
    // Keep focus inside whichever modal is on top; Escape closes without installing.
    window.addEventListener('keydown', event => {
        const modal = ['sidebar-notes-modal', 'project-update-modal'].map($).find(el => el && !el.hidden);
        if (!modal) return;
        if (event.key === 'Escape') { event.preventDefault(); close(modal.id); }
        if (event.key !== 'Tab') return;
        const focusable = [...modal.querySelectorAll('button, a[href]')].filter(el => !el.disabled && !el.hidden && el.getClientRects().length);
        if (!focusable.length) return;
        const first = focusable[0], last = focusable[focusable.length - 1];
        if (event.shiftKey && (document.activeElement === first || !modal.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && (document.activeElement === last || !modal.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
    }, true);
    render();
})();
