// Plan A 角色音色：MiniMax 只合成一条样音，H3 用这条声说新词。
// 经典画布用独立 type=voice 节点生成样音，再连到视频节点当作音频1 / 官方 H3 reference_audio。
(function(){
    const PROTOCOL = 'minimax-speech';
    const NODE_TYPE = 'voice';
    const DEFAULT_TEXT = '这是用于视频角色音色参考的样音，请保持声线稳定、吐字清晰。';
    const voiceCache = {};

    function esc(value){
        return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;', "'":'&#39;'}[ch]));
    }

    function injectStyles(){
        if(document.getElementById('character-voice-style')) return;
        const style = document.createElement('style');
        style.id = 'character-voice-style';
        style.textContent = [
            '.character-voice-panel{flex-basis:100%;display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:6px;}',
            '.character-voice-panel select,.character-voice-panel input{max-width:220px;font-size:11px;padding:4px 6px;border-radius:8px;border:1px solid rgba(128,128,148,.4);background:transparent;color:inherit;}',
            '.character-voice-panel .cv-text{flex:1 1 180px;min-width:140px;max-width:none;}',
            '.character-voice-panel .cv-generate{font-size:11px;line-height:1;padding:6px 10px;border-radius:999px;border:1px solid rgba(128,128,148,.4);background:transparent;color:inherit;cursor:pointer;}',
            '.character-voice-panel .cv-generate:disabled{opacity:.5;cursor:wait;}',
            '.character-voice-sample{flex-basis:100%;display:flex;gap:8px;align-items:center;font-size:11px;opacity:.86;}',
            '.character-voice-sample audio{height:28px;max-width:220px;}',
            '.character-voice-panel.standalone{flex-direction:column;align-items:stretch;margin-top:0;}',
            '.character-voice-panel.standalone select,.character-voice-panel.standalone input,.character-voice-panel.standalone .cv-text{max-width:none;width:100%;}',
            '.character-voice-panel.standalone .cv-generate{align-self:flex-start;}',
            '.character-voice-panel.standalone .character-voice-sample audio{max-width:100%;width:100%;}',
            '.voice-node-body{display:flex;flex-direction:column;gap:8px;padding:2px 0;}',
            '.voice-node-hint{font-size:11px;line-height:1.45;opacity:.78;}'
        ].join('\n');
        document.head.appendChild(style);
    }

    function speechProviders(providers){
        return (providers || []).filter(p => {
            if(!p || p.enabled === false) return false;
            const protocol = String(p.protocol || '').toLowerCase();
            return (protocol === PROTOCOL || protocol === 'minimax') && p.has_key;
        });
    }

    function itemUrl(item){
        if(!item) return '';
        if(typeof item === 'string') return String(item).trim();
        return String(item.url || '').trim();
    }

    function normalizeAudio(item, fallbackName){
        if(typeof item === 'string'){
            const url = String(item).trim();
            return url ? {url, name: fallbackName || '音频', kind: 'audio'} : null;
        }
        const url = itemUrl(item);
        if(!url) return null;
        return {
            url,
            name: item.name || fallbackName || '音频',
            kind: item.kind || 'audio',
            role: item.role || '',
            sourceType: item.sourceType || ''
        };
    }

    function isVoiceAudio(item){
        if(!item) return false;
        if(item.sourceType === 'voice' || item.sourceType === 'legacy') return true;
        if(item.role === 'character_voice') return true;
        if(item.type === NODE_TYPE) return true;
        return false;
    }

    function leftoverSample(node){
        const url = String(node?.voiceSampleUrl || node?.runSettings?.videoVoiceSampleUrl || '').trim();
        if(!url) return null;
        return {
            url,
            name: node?.voiceSampleName || node?.runSettings?.videoVoiceSampleName || '角色样音',
            kind: 'audio',
            role: 'character_voice',
            sourceType: 'legacy'
        };
    }

    function mergeAudios(existing, sampleUrl, sampleName){
        const list = (existing || []).map(item => normalizeAudio(item)).filter(Boolean);
        const url = String(sampleUrl || '').trim();
        if(!url) return list;
        if(list.some(item => itemUrl(item) === url)) return list;
        return [{url, name: sampleName || '角色样音', kind: 'audio'}, ...list];
    }

    function collectVideoAudios(node, connected){
        const list = (connected || []).map(item => normalizeAudio(item)).filter(Boolean);
        const leftover = leftoverSample(node);
        const merged = leftover && !list.some(item => itemUrl(item) === leftover.url)
            ? [leftover, ...list]
            : list;
        const voice = [];
        const rest = [];
        merged.forEach(item => {
            if(isVoiceAudio(item)) voice.push(item);
            else rest.push(item);
        });
        return [...voice, ...rest];
    }

    function usesCharacterVoice(node, connected){
        if(node?.characterVoice || node?.runSettings?.videoCharacterVoice) return true;
        return collectVideoAudios(node, connected).some(isVoiceAudio);
    }

    function convertAudios(node, connected){
        const cv = usesCharacterVoice(node, connected);
        return collectVideoAudios(node, connected).map((item, i) => ({
            name: item.name || `音${i + 1}`,
            url: item.url || item,
            role: cv && i === 0 ? 'character_voice' : ''
        }));
    }

    async function fetchVoices(providerId){
        const key = String(providerId || '');
        if(voiceCache[key]) return voiceCache[key];
        const res = await fetch(`/api/minimax-speech/voices?provider_id=${encodeURIComponent(key)}`);
        let data = null;
        try { data = await res.json(); } catch(e) {}
        if(!res.ok) throw new Error(String(data?.detail || `读取音色失败（HTTP ${res.status}）`));
        voiceCache[key] = data || {};
        return voiceCache[key];
    }

    async function generateSample(payload){
        const res = await fetch('/api/minimax-speech/sample', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload || {})
        });
        let data = null;
        try { data = await res.json(); } catch(e) {}
        if(!res.ok) throw new Error(String(data?.detail || `样音合成失败（HTTP ${res.status}）`));
        return data || {};
    }

    function providerSpeechModels(provider){
        const listed = (provider && provider.audio_models) || [];
        const models = listed.map(item => String(item || '').trim()).filter(Boolean);
        return models.length ? models : ['speech-2.8-hd'];
    }

    function panelHtml(state){
        injectStyles();
        const providers = speechProviders(state.providers);
        const providerId = state.providerId || providers[0]?.id || '';
        const provider = providers.find(item => item.id === providerId) || providers[0] || null;
        const cached = voiceCache[providerId] || {};
        const models = state.models || cached.models || providerSpeechModels(provider);
        const voices = state.voices || cached.voices || [];
        const model = state.model || models[0] || '';
        const voiceId = state.voiceId || voices[0]?.voice_id || '';
        const text = state.text || DEFAULT_TEXT;
        const sampleUrl = state.sampleUrl || '';
        const extraClass = state.standalone ? ' standalone' : '';
        if(!providers.length){
            return `<div class="character-voice-panel${extraClass}"><span class="vpt-row-label">请先在 API 设置接入 MiniMax 语音协议并保存 Key</span></div>`;
        }
        return `<div class="character-voice-panel${extraClass}" data-character-voice-panel="1">
            <select class="cv-provider" data-cv="provider">${providers.map(p => `<option value="${esc(p.id)}" ${p.id === providerId ? 'selected' : ''}>${esc(p.name || p.id)}</option>`).join('')}</select>
            <select class="cv-model" data-cv="model">${(models.length ? models : ['speech-2.8-hd']).map(m => `<option value="${esc(m)}" ${m === model ? 'selected' : ''}>${esc(m)}</option>`).join('')}</select>
            <select class="cv-voice" data-cv="voice">${voices.length ? voices.map(v => `<option value="${esc(v.voice_id)}" ${v.voice_id === voiceId ? 'selected' : ''}>${esc(v.voice_name || v.voice_id)}</option>`).join('') : '<option value="">读取音色…</option>'}</select>
            <input class="cv-text" data-cv="text" type="text" maxlength="200" value="${esc(text)}" title="样音试听文本，不要写成整段台词">
            <button type="button" class="cv-generate" data-cv="generate">生成样音</button>
            ${sampleUrl ? `<div class="character-voice-sample"><audio src="${esc(sampleUrl)}" controls preload="metadata"></audio><span>${esc(state.sampleName || '角色样音')}</span></div>` : ''}
        </div>`;
    }

    function readStateFrom(node, settings){
        const src = settings || node || {};
        return {
            providerId: src.speechProviderId || src.videoSpeechProvider || '',
            model: src.speechModel || src.videoSpeechModel || '',
            voiceId: src.voiceId || src.videoVoiceId || '',
            text: src.voiceSampleText || src.videoVoiceSampleText || DEFAULT_TEXT,
            sampleUrl: src.voiceSampleUrl || src.videoVoiceSampleUrl || src.url || '',
            sampleName: src.voiceSampleName || src.videoVoiceSampleName || src.name || ''
        };
    }

    function applySampleToNode(node, sample, fields){
        if(!node) return;
        const url = sample.url;
        const name = sample.name || '角色样音';
        node.voiceSampleUrl = url;
        node.voiceSampleName = name;
        node.voiceId = sample.voice_id || fields.voiceId || node.voiceId;
        node.speechModel = sample.model || fields.model || node.speechModel;
        node.speechProviderId = fields.providerId || node.speechProviderId;
        if(node.type === NODE_TYPE){
            node.url = url;
            node.name = name;
            node.mediaKind = 'audio';
        } else {
            node.characterVoice = true;
        }
    }

    function applySampleToSettings(settings, sample, fields){
        if(!settings) return;
        settings.videoVoiceSampleUrl = sample.url;
        settings.videoVoiceSampleName = sample.name || '角色样音';
        settings.videoVoiceId = sample.voice_id || fields.voiceId || settings.videoVoiceId;
        settings.videoSpeechModel = sample.model || fields.model || settings.videoSpeechModel;
        settings.videoSpeechProvider = fields.providerId || settings.videoSpeechProvider;
        settings.videoCharacterVoice = true;
    }

    function createNodeData(point, id){
        const p = point || {x: 0, y: 0};
        return {
            id: id || (`voice-${Date.now()}`),
            type: NODE_TYPE,
            x: Number(p.x) || 0,
            y: Number(p.y) || 0,
            speechProviderId: '',
            speechModel: '',
            voiceId: '',
            voiceSampleText: DEFAULT_TEXT,
            voiceSampleUrl: '',
            voiceSampleName: '',
            url: '',
            name: '音色',
            mediaKind: 'audio'
        };
    }

    function mediaRef(node){
        const url = String(node?.url || node?.voiceSampleUrl || '').trim();
        if(!url) return null;
        return {
            url,
            name: node.name || node.voiceSampleName || '角色样音',
            role: 'character_voice',
            kind: 'audio',
            sourceType: NODE_TYPE
        };
    }

    function generatorSource(node){
        const ref = mediaRef(node);
        if(!node || !ref) return null;
        return {
            id: node.id,
            type: NODE_TYPE,
            label: ref.name,
            preview: ref.url,
            refs: [ref],
            prompt: ''
        };
    }

    function bindPanel(wrap, node, hooks){
        const panel = wrap.querySelector('[data-character-voice-panel]');
        if(!panel || !node) return;
        const showError = hooks?.showError || ((msg, title) => { alert(msg); });
        const providerSel = panel.querySelector('[data-cv="provider"]');
        const modelSel = panel.querySelector('[data-cv="model"]');
        const voiceSel = panel.querySelector('[data-cv="voice"]');
        const textInput = panel.querySelector('[data-cv="text"]');
        const genBtn = panel.querySelector('[data-cv="generate"]');
        panel.onmousedown = e => e.stopPropagation();
        const persist = () => {
            if(providerSel) node.speechProviderId = providerSel.value;
            if(modelSel) node.speechModel = modelSel.value;
            if(voiceSel) node.voiceId = voiceSel.value;
            if(textInput) node.voiceSampleText = textInput.value;
            hooks?.onChange?.();
        };
        [providerSel, modelSel, voiceSel, textInput].forEach(el => {
            if(!el) return;
            el.onmousedown = e => e.stopPropagation();
            el.onclick = e => e.stopPropagation();
            el.onchange = persist;
            el.oninput = persist;
        });
        if(providerSel && !voiceCache[providerSel.value]){
            fetchVoices(providerSel.value).then(data => {
                if(!node.speechModel) node.speechModel = data.default_model || node.speechModel;
                if(!node.voiceId && data.voices?.[0]) node.voiceId = data.voices[0].voice_id;
                persist();
                hooks?.onRefresh?.();
            }).catch(err => showError(err.message || '读取音色失败', '音色'));
        }
        if(genBtn){
            genBtn.onmousedown = e => e.stopPropagation();
            genBtn.onclick = async e => {
                e.stopPropagation();
                persist();
                if(!node.speechProviderId || !node.voiceId){
                    showError('请先选择 MiniMax 平台和音色', '音色');
                    return;
                }
                const old = genBtn.textContent;
                genBtn.disabled = true;
                genBtn.textContent = '合成中…';
                try {
                    const sample = await generateSample({
                        provider_id: node.speechProviderId,
                        model: node.speechModel || '',
                        voice_id: node.voiceId,
                        text: node.voiceSampleText || DEFAULT_TEXT
                    });
                    applySampleToNode(node, sample, {
                        providerId: node.speechProviderId,
                        model: node.speechModel,
                        voiceId: node.voiceId
                    });
                    hooks?.onSample?.(sample);
                    if(sample.warning) showError(sample.warning, '音色');
                    hooks?.onRefresh?.();
                } catch(err){
                    showError(err.message || '样音合成失败', '音色');
                }
                genBtn.disabled = false;
                genBtn.textContent = old;
            };
        }
    }

    function renderBody(node, hooks){
        injectStyles();
        const wrap = document.createElement('div');
        wrap.className = 'voice-node-body generator-body';
        const hint = hooks?.hint || '生成一条人物样音，连到视频节点当作音频1 / 官方 H3 reference_audio。不是按台词配音。';
        wrap.innerHTML = `<div class="voice-node-hint">${esc(hint)}</div>` + panelHtml({
            ...readStateFrom(node),
            providers: hooks?.providers || [],
            standalone: true
        });
        bindPanel(wrap, node, hooks);
        return wrap;
    }

    window.CharacterVoice = {
        PROTOCOL,
        NODE_TYPE,
        DEFAULT_TEXT,
        speechProviders,
        mergeAudios,
        collectVideoAudios,
        usesCharacterVoice,
        convertAudios,
        fetchVoices,
        generateSample,
        voiceCache,
        panelHtml,
        readStateFrom,
        applySampleToNode,
        applySampleToSettings,
        createNodeData,
        mediaRef,
        generatorSource,
        bindPanel,
        renderBody
    };
})();
