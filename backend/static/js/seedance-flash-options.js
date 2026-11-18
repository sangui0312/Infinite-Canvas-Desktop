window.SeedanceFlashOptions = {
    controls(s) {
        const seed = Number.isSafeInteger(s.videoFlashSeed) ? s.videoFlashSeed : '';
        return `<label>输出格式 <select data-flash-field="videoFlashFormat"><option>mp4</option><option ${s.videoFlashFormat==='mov'?'selected':''}>mov</option></select></label>
        <label><input type="checkbox" data-flash-field="videoFlashCheck" ${s.videoFlashCheck?'checked':''}>内容审核</label>
        <label><input type="checkbox" data-flash-field="videoFlashSearch" ${s.videoFlashSearch?'checked':''}>联网搜索</label>
        <label>随机种子 <input type="number" step="1" placeholder="随机" style="width:7em" data-flash-field="videoFlashSeed" value="${seed}"></label>`;
    },
    bind(root,s,save) {
        root.querySelectorAll('[data-flash-field]').forEach(input => {
            input.onchange = () => {
                if (!input.reportValidity()) return;
                s[input.dataset.flashField] = input.type==='checkbox' ? input.checked :
                    input.type==='number' ? (input.value==='' ? null : input.valueAsNumber) : input.value;
                save();
            };
        });
    },
    payload(s) {
        return {output_format:s.videoFlashFormat || 'mp4', nsfw_check:!!s.videoFlashCheck,
            web_search:!!s.videoFlashSearch, seed:s.videoFlashSeed ?? null};
    }
};
