'use strict';
const {app, BrowserWindow} = require('electron');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
fs.mkdirSync(path.join(root, '.test-data'), {recursive:true});
app.setPath('userData', fs.mkdtempSync(path.join(root, '.test-data/grid-browser-')));
app.commandLine.appendSwitch('force-device-scale-factor', process.env.GRID_TEST_DPR || '1.25');
app.on('window-all-closed', () => {});
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let server, win, base, log = '';

async function main(){
    const backendDir = path.join(root, process.argv.includes('--packaged') ? 'dist/win-unpacked/resources/backend' : 'backend');
    server = spawn(path.join(backendDir, 'python/python.exe'), ['-B', '-X', 'utf8', path.join(__dirname, 'gpt26_integration_server.py')], {
        cwd:root, windowsHide:true, env:{...process.env, PYTHONDONTWRITEBYTECODE:'1', GPT26_UI_BACKEND:backendDir}, stdio:['ignore', 'pipe', 'pipe']
    });
    server.stdout.on('data', bytes => {
        log += bytes;
        const match = log.match(/GPT26_UI_READY (\d+)/);
        if(match) base = 'http://127.0.0.1:' + match[1];
    });
    server.stderr.on('data', bytes => { log += bytes; });
    for(let i = 0; i < 200 && !base; i++){
        if(server.exitCode !== null) throw Error(log.slice(-2000));
        await delay(100);
    }
    assert.ok(base, log.slice(-2000));
    await app.whenReady();
    win = new BrowserWindow({show:false, width:1440, height:1000, webPreferences:{backgroundThrottling:false}});
    win.webContents.session.webRequest.onBeforeRequest((details, callback) => callback({cancel:!['data:', 'blob:', base].some(prefix => details.url.startsWith(prefix))}));
    const js = async code => {
        try { return await win.webContents.executeJavaScript(code); }
        catch(error){ throw new Error(error.message + '\n' + code, {cause:error}); }
    };
    async function wait(code){
        for(let i = 0; i < 150; i++){
            if(await js(`Boolean(${code})`).catch(() => false)) return;
            await delay(100);
        }
        throw Error('Timed out ' + code);
    }
    async function checkGuides(horizontal, vertical, gap=0){
        const errors = await js(`(() => {
            const image=document.getElementById('cropImage');
            const rect=image.getBoundingClientRect();
            const expected=[];
            const offsets=${gap} > 0 ? [-0.5,0.5] : [0];
            for(const position of ${JSON.stringify(horizontal)}) for(const offset of offsets) expected.push({vertical:false,fraction:position+offset*${gap}/image.naturalHeight});
            for(const position of ${JSON.stringify(vertical)}) for(const offset of offsets) expected.push({vertical:true,fraction:position+offset*${gap}/image.naturalWidth});
            const lines=[...document.querySelectorAll('.grid-guide-line')];
            if(lines.length!==expected.length) throw Error('Missing guide lines');
            return lines.map((line,index)=>{
                const guide=line.getBoundingClientRect(),target=expected[index];
                return target.vertical ? guide.x+guide.width/2-(rect.x+rect.width*target.fraction) : guide.y+guide.height/2-(rect.y+rect.height*target.fraction);
            });
        })()`);
        assert.ok(errors.every(error => Math.abs(error) < 1), 'Guide boundary mismatch ' + errors);
    }
    async function mouse(type, point){
        win.webContents.sendInputEvent({type, x:Math.round(point.x), y:Math.round(point.y), button:'left', clickCount:1});
        await delay(30);
    }
    for(const kind of ['classic', 'smart']){
        await win.loadURL(base + '/static/' + (kind === 'classic' ? 'canvas' : 'smart-canvas') + '.html?id=gpt26-' + kind);
        await wait(`typeof nodes !== 'undefined' && nodes.length > 0`);
        await js(`nodes.push({id:'grid-test',type:${JSON.stringify(kind === 'classic' ? 'image' : 'smart-image')},url:'/api/storage-files/generated/square.png',name:'square.png',x:100,y:100,images:[{url:'/api/storage-files/generated/square.png',name:'square.png',kind:'image'}]});openImageEditor('grid-test');void 0`);
        await wait(`imageEditBaseW > 0 && document.getElementById('cropImage').naturalWidth === 300`);
        await js(`setImageEditMode('grid',true);void 0`);
        const scales = process.argv.includes('--custom-scales') ? await js(`StudioScale.options.filter(value=>value!=='auto')`) : ['100', '80', '125'];
        const zooms = process.argv.includes('--custom-scales') ? [0.15, 0.673, 0.89, 1.37, 2.35, 6] : [0.75, 1, 3];
        for(const scale of scales){
            await js(`StudioScale.set('${scale}');void 0`);
            await delay(100);
            for(const zoom of zooms){
                await js(`imageEditZoom=${zoom};applyImageEditZoom();gridCustomMode=true;gridCustomLines=[{type:'v',pos:1/3},{type:'h',pos:2/3}];document.getElementById('gridGapSize').value='0';refreshGridSplitPreview();void 0`);
                const report = await js(`(() => {
                    const image=document.getElementById('cropImage').getBoundingClientRect();
                    const overlay=document.getElementById('gridGuideOverlay');
                    return {dpr:devicePixelRatio,lines:[...document.querySelectorAll('.grid-guide-line')].map(line=>{
                        const rect=line.getBoundingClientRect(),vertical=line.classList.contains('vertical');
                        return {error:vertical ? rect.x+rect.width/2-(image.x+image.width/3) : rect.y+rect.height/2-(image.y+image.height*2/3),lengthError:vertical?rect.height-image.height:rect.width-image.width};
                    }),visible:Boolean(overlay?.classList.contains('active')),rects:gridSplitRects(300,300)};
                })()`);
                assert.equal(report.lines.length, 2, 'Both canvases need visible guides');
                assert.ok(report.visible);
                for(const line of report.lines){
                    assert.ok(Math.abs(line.error) < 1, 'Guide is offset ' + JSON.stringify(line));
                    assert.ok(Math.abs(line.lengthError) < 1, 'Guide extends beyond image');
                }
                assert.deepEqual(report.rects.map(rect => [rect.x, rect.y, rect.w, rect.h]), [[0,0,100,200],[100,0,200,200],[0,200,100,100],[100,200,200,100]]);
            }
        }
        await js(`StudioScale.set('80');imageEditBaseW=600;imageEditBaseH=600;imageEditZoom=1;applyImageEditZoom();gridCustomMode=false;toggleGridCustomMode();setGridCustomOrientation('v');void 0`);
        const points = await js(`(() => {
            const rect=document.getElementById('cropImage').getBoundingClientRect();
            const stage=document.getElementById('imageEditStage').getBoundingClientRect();
            const visibleTop=Math.max(rect.top,stage.top),visibleBottom=Math.min(rect.bottom,stage.bottom);
            const y=(visibleTop+visibleBottom)/2;
            return {start:{x:rect.x+rect.width*0.4,y},end:{x:rect.x+rect.width*0.6,y}};
        })()`);
        await mouse('mouseDown', points.start);
        await mouse('mouseUp', points.start);
        assert.ok(Math.abs(await js('gridCustomLines[0]?.pos') - 0.4) < 0.01, 'Click should place a guide under pointer ' + JSON.stringify(await js(`({lines:gridCustomLines,width:innerWidth,height:innerHeight,hit:document.elementFromPoint(${points.start.x},${points.start.y})?.id,point:${JSON.stringify(points.start)}})`)));
        await mouse('mouseDown', points.start);
        await mouse('mouseMove', points.end);
        await mouse('mouseUp', points.end);
        const dragged = await js('gridCustomLines[0].pos');
        assert.ok(Math.abs(dragged - 0.6) < 0.01, 'Drag should follow pointer');
        await checkGuides([], [dragged]);
        await js(`applyGridPreset(2,3);document.getElementById('gridGapSize').value='12';refreshGridSplitPreview();void 0`);
        await checkGuides([0.5], [1/3, 2/3], 12);
        assert.deepEqual(await js('gridSplitRects(300,300).map(rect=>[rect.w,rect.h])'), [[94,144],[88,144],[94,144],[94,144],[88,144],[94,144]]);
        await js(`imageEditZoom=3;applyImageEditZoom();document.getElementById('imageEditStage').scrollTo(190,150);void 0`);
        await checkGuides([0.5], [1/3, 2/3], 12);
        win.setSize(1200, 850);
        await delay(150);
        await checkGuides([0.5], [1/3, 2/3], 12);
        win.setSize(1440, 1000);
        await js(`imageEditZoom=1;applyImageEditZoom();document.getElementById('imageEditStage').scrollTo(0,0);void 0`);
        fs.writeFileSync(path.join(root, '.test-data/grid-guides-' + kind + '-' + (process.env.GRID_TEST_DPR || '1.25') + '.png'), (await win.webContents.capturePage()).toPNG());
        await js(`setImageEditMode('crop',true);void 0`);
        assert.equal(await js(`document.querySelectorAll('.grid-guide-line').length`), 0, 'Crop mode clears guides');
        await js(`setImageEditMode('grid',true);void 0`);
        if(kind === 'smart'){
            await js(`nodes.find(node=>node.id==='grid-test').images.push({url:'/api/storage-files/generated/square.png',name:'square-2.png',kind:'image'});setGridOperationMode('join');void 0`);
            assert.equal(await js(`document.getElementById('gridGuideOverlay').classList.contains('active')`), false, 'Join mode clears split guides');
            await js(`setGridOperationMode('split');void 0`);
        }
        await js(`(async()=>{applyGridPreset(2,3);document.getElementById('gridGapSize').value='12';window.gridBeforeIds=nodes.map(node=>node.id);await applyImageEdit();})()`);
        const slices = await js(`(async()=>{
            const images=nodes.filter(node=>!gridBeforeIds.includes(node.id)).flatMap(node=>node.images||[]);
            return Promise.all(images.map(async item=>{
                const image=new Image();image.src=typeof item==='string'?item:item.url;await image.decode();
                const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;
                const context=canvas.getContext('2d');context.drawImage(image,0,0);
                return {size:[image.naturalWidth,image.naturalHeight],pixel:[...context.getImageData(0,0,1,1).data]};
            }));
        })()`);
        assert.deepEqual(slices.map(slice=>slice.size), [[94,144],[88,144],[94,144],[94,144],[88,144],[94,144]]);
        assert.ok(slices.every(slice=>JSON.stringify(slice.pixel)==='[0,0,128,255]'), 'Guides must not be burned into exported slices');
        for(const [name, width, height] of [['landscape',400,300], ['portrait',300,533]]){
            await js(`(() => {const node=nodes.find(node=>node.id==='grid-test');node.url='/api/storage-files/generated/${name}.png';node.images[0].url=node.url;openImageEditor('grid-test');})()`);
            await wait(`imageEditBaseW > 0 && document.getElementById('cropImage').naturalWidth === ${width} && document.getElementById('cropImage').naturalHeight === ${height}`);
            await js(`setImageEditMode('grid',true);applyGridPreset(2,3);document.getElementById('gridGapSize').value='12';imageEditZoom=1.5;applyImageEditZoom();void 0`);
            await checkGuides([0.5], [1/3, 2/3], 12);
        }
        console.log(kind + ' passed display/UI/editor scales, mouse placement/drag, preset/gap, scrolling, resizing, square/landscape/portrait, mode changes and six exported slices');
    }
    console.log('GRID_GUIDES_OK DPR=' + (process.env.GRID_TEST_DPR || '1.25'));
    win.destroy(); server.kill(); app.quit();
}
main().catch(error => {console.error(error.stack, log.slice(-2000)); win?.destroy(); server?.kill(); app.exit(1);});
