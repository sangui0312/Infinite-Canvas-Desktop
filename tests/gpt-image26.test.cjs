'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const g=require('../backend/static/js/gpt-image26.js');

test('mixed input dimensions use a fresh supported ratio for each run',()=>{
    const settings={model:'gpt-image-2.6-ext',resolution:'1k',ratio:'source',imageVersion:'sunburst',quality:'max'};
    const sizes=['1024:768','1000:1000','941:1672','398:320'].map(customRatio=>g.params({...settings,customRatio},[{url:'fixture'}]).size);
    assert.deepEqual(sizes,['4:3','1:1','9:16','5:4']);
    assert.equal(settings.customRatio,undefined);
});
test('current resolution, references and SVG/GIF combinations are enforced',()=>{
    assert.throws(()=>g.params({resolution:'2k'}),/1K/);
    assert.throws(()=>g.params({},Array(17).fill({})),/16/);
    assert.throws(()=>g.params({outputFormat:'gif',ratio:'square'}),/自动/);
    assert.throws(()=>g.params({outputFormat:'svg'},[{url:'fixture'}]),/文生图/);
    assert.equal(g.params({outputFormat:'gif',ratio:'auto'}).size,'auto');
});
test('all declared quality levels, SVG settings and safe control text survive',()=>{
    for(const quality of g.qualities)assert.equal(g.params({quality}).quality,quality);
    const result=g.params({outputFormat:'svg',svgModel:'gpt-4.1-mini',svgEffort:'high',complexity:'low'});
    assert.deepEqual(result.svg,{model:'gpt-4.1-mini',reasoning:{effort:'high'}});
    assert.equal(result.complexity,'low');
    assert.ok(!g.controls({outputFormat:'svg',svgModel:'"><script>alert(1)</script>'}).includes('<script>'));
    assert.ok(!Object.hasOwn(g.params({background:'transparent'}),'background'));
});
