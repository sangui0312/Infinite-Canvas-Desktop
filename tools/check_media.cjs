'use strict';
// Synthetic local files only: no model, upload gateway, or production requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { Backend, ORIGIN, assertPortFree } = require('../desktop/backend.cjs');
const project = path.resolve(__dirname, '..');
async function main() {
  const root = path.join(project, '.test-data', `media-${Date.now()}`);
  const backend = new Backend(path.join(project,'dist/win-unpacked/resources/backend'), root);
  try {
    await backend.start();
    const call = async (route, options={}) => {
      const response = await fetch(ORIGIN+route,{...options,headers:{'X-Desktop-Token':backend.token,...options.headers}});
      assert.ok(response.ok, `local check ${route}: HTTP ${response.status}`);
      return response;
    };
    const post = (route, data) => call(route, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    assert.equal((await fetch(ORIGIN+'/static/index.html')).status,403);
    const png = path.join(root,'sample.png'), video = path.join(root,'sample.mp4');
    const python = path.join(backend.appDir,'python/python.exe');
    execFileSync(python,['-B','-c','from PIL import Image;import sys;Image.new("RGBA",(32,32),(50,120,200,255)).save(sys.argv[1])',png],{windowsHide:true});
    execFileSync(path.join(backend.appDir,'tools/ffmpeg/ffmpeg.exe'),['-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=blue:s=32x32:r=10','-t','0.2','-c:v','libx264','-pix_fmt','yuv420p','-y',video],{windowsHide:true});
    const form = new FormData();
    form.append('files',new Blob([fs.readFileSync(png)],{type:'image/png'}),'sample.png');
    form.append('files',new Blob([fs.readFileSync(video)],{type:'video/mp4'}),'sample.mp4');
    const uploaded = await (await call('/api/ai/upload',{method:'POST',body:form})).json();
    const image = uploaded.files.find(item=>item.kind==='image');
    const movie = uploaded.files.find(item=>item.kind==='video');
    assert.ok(image && movie);
    assert.deepEqual(Buffer.from(await (await call(image.url)).arrayBuffer()),fs.readFileSync(png));
    const range = await call(movie.url,{headers:{Range:'bytes=0-31'}});
    assert.equal(range.status,206); assert.equal((await range.arrayBuffer()).byteLength,32);
    const zipPath = path.join(root,'media.zip');
    const archive = await post('/api/canvas-assets/download',{items:[{url:image.url,name:'sample.png'},{url:movie.url,name:'sample.mp4'}]});
    fs.writeFileSync(zipPath,Buffer.from(await archive.arrayBuffer()));
    execFileSync(python,['-B','-c','import zipfile,sys;z=zipfile.ZipFile(sys.argv[1]);assert set(z.namelist())=={"sample.png","sample.mp4"};assert z.testzip() is None',zipPath],{windowsHide:true});
    const psd = await (await post('/api/canvas/layers-psd',{filename:'smoke.psd',items:[{url:image.url,role:'base',z_index:0,name:'base'}]})).json();
    assert.equal(psd.ok,true);
    const psdBytes = Buffer.from(await (await call(psd.psd_url)).arrayBuffer());
    assert.equal(psdBytes.subarray(0,4).toString(),'8BPS');
    const imported = new FormData();
    imported.append('file',new Blob([JSON.stringify({title:'Synthetic imported project',kind:'smart',nodes:[],connections:[]})],{type:'application/json'}),'canvas.json');
    const canvas = await (await call('/api/canvases/import',{method:'POST',body:imported})).json();
    assert.equal(canvas.canvas.kind,'smart');
    const projectExport = await call('/api/projects/default/export');
    const projectBytes = Buffer.from(await projectExport.arrayBuffer());
    assert.equal(projectBytes.subarray(0,2).toString(),'PK');
    const result = {packagedBackend:true,sessionRequired:true,imageUpload:true,videoUpload:true,videoRange206:true,mixedMediaZip:true,psdExport:true,canvasImport:true,projectExport:true};
    fs.writeFileSync(path.join(project,'docs/media-verification.json'),JSON.stringify(result,null,2)+'\n');
    console.log(JSON.stringify(result));
  } finally { await backend.stop(); }
  await assertPortFree();
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
