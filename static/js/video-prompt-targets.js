// 出片提示词目标转换（画布出片提示词适配方案）。
// 本文件只做领域逻辑：目标清单、转换请求、配套模型匹配、按钮 HTML 与样式。
// 画布节点操作（派生工作台）由 smart-canvas.js 的薄接线调用本模块。
(function(){
    const state = {targets: [], loaded: false, loading: null};

    function esc(value){
        return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;', "'":'&#39;'}[ch]));
    }

    function injectStyles(){
        if(document.getElementById('vpt-style')) return;
        const style = document.createElement('style');
        style.id = 'vpt-style';
        style.textContent = [
            '.vpt-row{flex-basis:100%;display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:8px;padding-top:8px;border-top:1px dashed rgba(128,128,148,.3);}',
            '.vpt-row-label{font-size:11px;opacity:.62;margin-right:2px;user-select:none;}',
            '.vpt-btn{font-size:11px;line-height:1;padding:6px 10px;border-radius:999px;border:1px solid rgba(128,128,148,.4);background:transparent;color:inherit;cursor:pointer;white-space:nowrap;}',
            '.vpt-btn:hover{border-color:rgba(128,128,148,.85);}',
            '.vpt-btn[disabled]{opacity:.5;cursor:wait;}',
            '.vpt-meta{flex-basis:100%;display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:11px;opacity:.78;margin-top:4px;}',
            '.vpt-meta-warn{color:#c8862d;cursor:help;}'
        ].join('\n');
        document.head.appendChild(style);
    }

    async function load(){
        if(state.loaded) return state.targets;
        if(state.loading) return state.loading;
        state.loading = fetch('/api/video-prompt-targets')
            .then(res => (res.ok ? res.json() : {targets: []}))
            .then(data => {
                state.targets = Array.isArray(data?.targets) ? data.targets : [];
                state.loaded = true;
                // 清单迟到时刷新一次页面渲染，把按钮排上去：智能画布走参数面板刷新，经典画布走整体重绘。
                if(state.targets.length){
                    try {
                        if(typeof window.scheduleDynamicParamsRefresh === 'function') window.scheduleDynamicParamsRefresh(0);
                        else if(typeof window.render === 'function') window.render();
                    } catch(e) {}
                }
                return state.targets;
            })
            .catch(() => {
                state.loading = null;
                return [];
            });
        return state.loading;
    }

    function list(){
        return state.targets;
    }

    function byId(id){
        return state.targets.find(item => item.id === id) || null;
    }

    function buttonRowHtml(){
        if(!state.targets.length) return '';
        return `<div class="vpt-row">
            <span class="vpt-row-label">AI 提示词</span>
            ${state.targets.map(item => `<button type="button" class="vpt-btn" data-vpt-target="${esc(item.id)}" title="按「${esc(item.label)}」规范转换导演本并派生新工作台">${esc(item.label)}</button>`).join('')}
        </div>`;
    }

    // 派生工作台上的中间稿简表与校验警告（方案 §4 / §8）。node.videoPromptTarget 由 smart-canvas 派生时写入。
    function metaRowHtml(node){
        const meta = node?.videoPromptTarget;
        if(!meta || !meta.target) return '';
        const spec = byId(meta.target);
        const parts = [`目标 ${spec?.label || meta.target}`];
        if(meta.ir){
            if(meta.ir.shots) parts.push(`${meta.ir.shots} 镜`);
            (meta.ir.subjects || []).forEach(subject => {
                if(subject?.id) parts.push(`${subject.id}${subject.image ? '=' + subject.image : '（未绑定）'}`);
            });
        }
        const warnings = meta.warnings || [];
        const warnHtml = warnings.length
            ? `<span class="vpt-meta-warn" title="${esc(warnings.join('\n'))}">⚠ ${warnings.length} 条警告</span>`
            : '';
        return `<div class="vpt-meta">${esc(parts.join(' · '))}${warnHtml}</div>`;
    }

    async function convert(payload){
        const res = await fetch('/api/video-prompt-targets/convert', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload || {})
        });
        let data = null;
        try {
            data = await res.json();
        } catch(e) {}
        if(!res.ok) throw new Error(String(data?.detail || `转换失败（HTTP ${res.status}）`));
        return data || {};
    }

    // 派生工作台的配套模型软默认：h3 家族按 provider 协议找，seedance 按模型名提示找；找不到返回 null（保留源设置）。
    function pickVideoModelPreset(target, providers){
        const spec = typeof target === 'string' ? byId(target) : target;
        if(!spec) return null;
        const enabled = (providers || []).filter(p => p && p.enabled !== false);
        if(spec.family === 'h3'){
            const provider = enabled.find(p => String(p.protocol || '').toLowerCase() === 'h3' && (p.video_models || []).length);
            return provider ? {provider: provider.id, model: provider.video_models[0]} : null;
        }
        const hints = (spec.model_hints || []).map(hint => String(hint).toLowerCase()).filter(Boolean);
        if(!hints.length) return null;
        for(const provider of enabled){
            for(const model of provider.video_models || []){
                const lc = String(model).toLowerCase();
                if(hints.some(hint => lc.includes(hint))) return {provider: provider.id, model};
            }
        }
        return null;
    }

    window.VideoPromptTargets = {load, list, byId, buttonRowHtml, metaRowHtml, convert, pickVideoModelPreset};
    injectStyles();
    if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => { load(); });
    else load();
})();
