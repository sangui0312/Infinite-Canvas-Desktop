/* Version-grouped plain text; shared by the desktop UI and release validator. */
(function(root) {
    'use strict';
    function parse(value) {
        const groups=[];let group;
        for(const raw of String(value || '').split(/\r?\n/)) {
            const line=raw.trim();if(!line)continue;
            const heading=/^(?:##\s+)?v?(\d+\.\d+\.\d+)$/.exec(line);
            if(heading){group={version:heading[1],items:[]};groups.push(group);continue;}
            if(!group || !line.startsWith('- '))return null;
            const item=line.slice(2).trim();if(!item)return null;group.items.push(item);
        }
        return groups.length && groups.every(g=>g.items.length)?groups:null;
    }
    function validate(value,currentVersion) {
        const groups=parse(value);
        if(!groups || groups[0].version!==currentVersion)throw Error('更新日志须按版本分组，首组版本必须与当前版本一致。');
        const seen=new Set();let previous;
        for(const group of groups){
            const parts=group.version.split('.').map(Number);
            const delta=previous && parts.map((n,i)=>n-previous[i]).find(n=>n!==0);
            if(seen.has(group.version) || (previous && !(delta<0)))throw Error('更新日志版本须唯一，并按版本号从新到旧排列。');
            if(group.items.some(item=>/^\d{4}-\d{2}-\d{2}(?:-\d+)?[：:]/.test(item)))throw Error('更新日志使用版本号分组，不再使用日期编号。');
            previous=parts;seen.add(group.version);
        }
        return groups;
    }
    const api={parse,validate};
    if(typeof module==='object' && module.exports)module.exports=api;
    else root.DesktopReleaseNotes=api;
})(typeof window!=='undefined'?window:globalThis);
