/* Shared APIMart GPT Image 2.6 Ext contract for existing creation surfaces. */
(function(root, factory){
    const api = factory();
    if(typeof module === 'object' && module.exports) module.exports = api;
    if(root) root.GptImage26 = api;
})(typeof window === 'undefined' ? null : window, function(){
    'use strict';
    const ratios = ['1:1','16:9','9:16','4:3','3:4','3:2','2:3','5:4','4:5','21:9'];
    const qualities = ['auto','low','medium','high','xhigh','max'];
    const formats = ['png','jpeg','webp','svg','gif'];
    const aliases = {square:'1:1',wide:'16:9',story:'9:16',landscape43:'4:3',portrait43:'3:4',landscape:'3:2',portrait:'2:3',ultrawide:'21:9'};
    const isModel = model => String(model || '').trim().toLowerCase() === 'gpt-image-2.6-ext';
    const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    function closestRatio(width, height){
        if(!(width > 0 && height > 0)) throw Error('无法读取适配图片尺寸，请重新选择参考图。');
        const error = ratio => { const [w,h] = ratio.split(':').map(Number); return Math.abs((width / height) / (w / h) - 1); };
        return ratios.reduce((best, ratio) => error(ratio) < error(best) ? ratio : best);
    }
    function params(source={}, refs=[]){
        if(refs.length > 16) throw Error('GPT-Image-2.6 Ext 最多支持 16 张参考图。');
        const resolution = String(source.resolution || '1k').toLowerCase();
        if(!['1k','auto','custom'].includes(resolution)) throw Error('GPT-Image-2.6 Ext 当前仅开放 1K，请重新选择分辨率。');
        const version = String(source.imageVersion || source.version || 'flare').trim().toLowerCase();
        if(!['flare','sunburst'].includes(version)) throw Error('Ext 版本须为 Flare 或 Sunburst。');
        const format = String(source.outputFormat || source.output_format || 'png').toLowerCase().replace(/^jpg$/, 'jpeg');
        if(!formats.includes(format)) throw Error('请选择支持的输出格式。');
        let size = aliases[source.ratio] || source.ratio || 'auto';
        if(source.ratio === 'source' || source.ratio === 'custom'){
            const value = source.customRatio || `${source.customRatioWidth || ''}:${source.customRatioHeight || ''}`;
            const [w,h] = value.split(':').map(Number);
            size = closestRatio(w,h);
        }
        if(resolution === 'custom'){
            const match = String(source.customSize || `${source.customWidth}x${source.customHeight}`).match(/^(\d+)\s*[xX*]\s*(\d+)$/);
            if(!match) throw Error('历史尺寸无效，请选择比例和 1K。');
            size = closestRatio(Number(match[1]),Number(match[2]));
        }
        if(!['auto',...ratios].includes(size)) throw Error('请选择自动或支持的 10 种比例。');
        if(['svg','gif'].includes(format) && refs.length) throw Error('SVG / GIF 仅支持文生图，请移除参考图或改选静态图片格式。');
        if(format === 'gif' && size !== 'auto') throw Error('GIF 仅支持自动尺寸，请选择自动比例。');
        const result = {version,size,resolution:'1k',output_format:format};
        const quality = String(source.quality || 'auto').toLowerCase();
        if(!qualities.includes(quality)) throw Error('请选择支持的图片质量。');
        result.quality = quality;
        if(source.complexity){
            if(!['low','medium','high'].includes(source.complexity)) throw Error('请选择支持的复杂度。');
            result.complexity = source.complexity;
        }
        if(format === 'svg'){
            if(source.svgModel){
                result.svg = {model:String(source.svgModel).trim()};
                if(source.svgEffort) result.svg.reasoning = {effort:source.svgEffort};
            } else if(source.svg) result.svg = source.svg;
            else if(source.svgEffort) throw Error('指定推理档位前请填写 SVG 编写模型。');
        }
        return result;
    }
    function controls(settings={}, className=''){
        const select = (key,label,options,value) => `<select class="${escape(className)}" data-param="${key}" aria-label="${label}" title="${label}">${options.map(([v,l]) => `<option value="${v}" ${v === value ? 'selected' : ''}>${l}</option>`).join('')}</select>`;
        let html = select('imageVersion','Ext 版本',[['flare','Flare'],['sunburst','Sunburst']],settings.imageVersion || 'flare');
        html += select('outputFormat','输出格式',formats.map(v=>[v,v.toUpperCase()]),settings.outputFormat || 'png');
        html += select('complexity','复杂度',[['','复杂度自动'],['low','简洁'],['medium','中等复杂度'],['high','丰富细节']],settings.complexity || '');
        if(settings.outputFormat === 'svg'){
            html += `<input class="${escape(className)}" data-param="svgModel" aria-label="SVG 编写模型" title="SVG 编写模型，留空由上游选择" placeholder="SVG 编写模型（可选）" maxlength="160" value="${escape(settings.svgModel || '')}">`;
            html += select('svgEffort','SVG 推理档位',[['','推理自动'],...['none','minimal','low','medium','high','xhigh','max'].map(v=>[v,v])],settings.svgEffort || '');
        }
        return html;
    }
    function bind(container, settings, changed){
        for(const element of container.querySelectorAll('[data-param]')){
            element.onmousedown = element.onclick = event => event.stopPropagation();
            element.onchange = event => {
                event.stopPropagation();
                settings[element.dataset.param] = element.value;
                if(element.dataset.param === 'outputFormat' && element.value === 'gif'){
                    settings.ratio = 'auto'; settings.resolution = '1k';
                }
                changed();
            };
        }
    }
    return {isModel,ratios,qualities,formats,closestRatio,params,controls,bind};
});
