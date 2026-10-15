'use strict';
const http=require('node:http'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
async function run({win,root,waitFor}){
  let posts=0;
  const server=http.createServer((req,res)=>{
    if(req.method!=='GET'){posts++;res.writeHead(405).end();return;}
    let data;
    if(req.url==='/api/app-info')data={version:'legacy-smoke'};
    else if(req.url==='/api/projects')data={projects:[{id:'source',name:'旧 Web 测试'}]};
    else if(req.url==='/api/canvases')data={canvases:[{id:'ordinary',project:'source',updated_at:1},{id:'smart',project:'source',updated_at:1}]};
    else if(req.url.startsWith('/api/canvases/')){
      const id=req.url.split('/').at(-1);data={canvas:{id,project:'source',updated_at:1,title:'旧端 '+id,kind:id==='smart'?'smart':'classic',nodes:[{id:'image',type:id==='smart'?'smart-image':'image',x:0,y:0,items:[{url:'/assets/test.png',name:'test.png'}]}],connections:[]}};
    }else if(req.url==='/assets/test.png'){
      const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aN1cAAAAASUVORK5CYII=','base64');res.writeHead(200,{'Content-Type':'image/png','Content-Length':png.length});res.end(png);return;
    }else{res.writeHead(404).end();return;}
    res.setHeader('Content-Type','application/json');res.end(JSON.stringify(data));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const execute=script=>win.webContents.executeJavaScript(script);
  try{
    await execute(`document.querySelector('#desktop-legacy-import').click();document.querySelector('#legacy-address').value=${JSON.stringify('http://127.0.0.1:'+server.address().port)};document.querySelector('#desktop-legacy-modal [data-action="detect"]').click();`);
    await waitFor(()=>execute(`document.querySelectorAll('#legacy-projects input').length===1`),'detect legacy server');
    await execute(`document.querySelector('#desktop-legacy-modal [data-action="all"]').click();document.querySelector('#desktop-legacy-modal [data-action="start"]').click();`);
    await waitFor(()=>execute(`!document.querySelector('#desktop-legacy-modal [data-action="commit"]').hidden`),'legacy download staged');
    await execute(`document.querySelector('#desktop-legacy-modal [data-action="commit"]').click();`);
    await waitFor(()=>execute(`document.querySelector('#legacy-status').textContent.includes('已导入 1 个项目、2 个画布和 1 项素材')`),'legacy import committed');
    const info=await execute(`(async()=>{const projects=(await fetch('/api/projects').then(r=>r.json())).projects;const project=projects.find(p=>p.name.includes('旧 Web 测试'));const all=(await fetch('/api/canvases').then(r=>r.json())).canvases.filter(c=>c.project===project.id);const values=[];for(const item of all){const c=(await fetch('/api/canvases/'+item.id).then(r=>r.json())).canvas;const url=c.nodes[0].items[0].url;values.push({kind:c.kind,local:url.startsWith('/assets/imports/'),status:(await fetch(url)).status});}return values;})()`);
    assert.equal(info.length,2);assert.deepEqual(new Set(info.map(item=>item.kind)),new Set(['classic','smart']));
    assert.ok(info.every(item=>item.local&&item.status===200));assert.equal(posts,0);
    fs.writeFileSync(path.join(root,'legacy-import.png'),(await win.webContents.capturePage()).toPNG());
    await execute(`document.querySelector('#desktop-legacy-modal').close()`);
    return{detected:true,selected:true,ordinaryAndSmart:true,downloadedOnce:true,localMediaReadable:true,sourceReadOnly:true};
  }finally{await new Promise(resolve=>server.close(resolve));}
}
module.exports={run};
