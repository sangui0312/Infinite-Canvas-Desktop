(function(root){
    'use strict';
    const clone = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
    const stable = value => JSON.stringify(value && typeof value === 'object' ? (Array.isArray(value) ? value.map(v => JSON.parse(stable(v) || 'null')) : Object.fromEntries(Object.keys(value).sort().map(k => [k, JSON.parse(stable(value[k]) || 'null')]))) : value);
    const equal = (a,b) => stable(a) === stable(b);
    function mergeFields(base,local,remote,label){
        const result={...clone(remote)};
        for(const key of new Set([...Object.keys(base||{}),...Object.keys(local||{})])){
            if(equal(local?.[key],base?.[key])) continue;
            if(!equal(remote?.[key],base?.[key]) && !equal(local?.[key],remote?.[key])) throw new Error(`画布冲突：${label}的 ${key} 已在其他任务中修改。本地草稿已保留，请先处理冲突。`);
            if(local && Object.hasOwn(local,key)) result[key]=clone(local[key]); else delete result[key];
        }
        return result;
    }
    function merge(base,local,remote){
        if(!base) return clone(remote);
        const result=clone(remote), b=new Map((base.nodes||[]).map(n=>[n.id,n])), l=new Map((local.nodes||[]).map(n=>[n.id,n])), r=new Map((remote.nodes||[]).map(n=>[n.id,n]));
        result.nodes=[];
        for(const id of new Set([...r.keys(),...l.keys()])){
            const old=b.get(id), mine=l.get(id), theirs=r.get(id);
            if(remote.agent_deleted_nodes?.[id]){
                if(mine && !equal(old,mine)) throw new Error('已撤销的成果存在未保存编辑，本地草稿已保留。');
                continue;
            }
            if(!old){ if(mine&&theirs&&!equal(mine,theirs)) throw new Error('画布节点编号冲突，本地草稿已保留。'); result.nodes.push(clone(mine||theirs)); continue; }
            if(!mine){ if(theirs&&!equal(old,theirs)) throw new Error('已删除的节点被其他任务编辑，本地草稿已保留。'); continue; }
            if(!theirs){ if(!equal(old,mine)) throw new Error('正在编辑的节点已被删除，本地草稿已保留。'); continue; }
            result.nodes.push(mergeFields(old,mine,theirs,mine.title||id));
        }
        const edgeKey=e=>`${e.from}\u0000${e.to}\u0000${e.kind||'flow'}`, oldEdges=new Map((base.connections||[]).map(e=>[edgeKey(e),e])), mineEdges=new Map((local.connections||[]).map(e=>[edgeKey(e),e])), theirEdges=new Map((remote.connections||[]).map(e=>[edgeKey(e),e]));
        const ids=new Set(result.nodes.map(n=>n.id));
        result.connections=[...new Set([...theirEdges.keys(),...mineEdges.keys()])].filter(k=>!oldEdges.has(k)||(mineEdges.has(k)&&theirEdges.has(k))).map(k=>clone(theirEdges.get(k)||mineEdges.get(k))).filter(e=>ids.has(e.from)&&ids.has(e.to));
        for(const field of ['title','icon','viewport','settings']){
            if(field==='settings'||field==='viewport') result[field]=mergeFields(base[field]||{},local[field]||{},remote[field]||{},field);
            else if(!equal(local[field],base[field])){
                if(!equal(remote[field],base[field])&&!equal(local[field],remote[field])) throw new Error(`画布 ${field} 存在冲突，本地草稿已保留。`);
                result[field]=clone(local[field]);
            }
        }
        // Append independently recorded logs without resurrecting removed nodes.
        result.logs=[...(remote.logs||[])];
        for(const log of local.logs||[]) if(!result.logs.some(e=>equal(e,log))) result.logs.push(clone(log));
        result.logs=result.logs.slice(-500);
        return result;
    }
    const api={merge,mergeFields,equal};
    if(typeof module==='object'&&module.exports) module.exports=api;
    else root.SmartAgentMerge=api;
})(typeof window==='undefined'?globalThis:window);
