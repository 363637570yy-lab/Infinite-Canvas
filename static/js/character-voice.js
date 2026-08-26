// Plan A 角色音色：MiniMax 只合成一条样音，H3 用这条声说新词。
(function(){
    const PROTOCOL = 'minimax-speech';
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
            '.character-voice-sample audio{height:28px;max-width:220px;}'
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

    function mergeAudios(existing, sampleUrl, sampleName){
        const list = (existing || []).filter(item => item && (item.url || typeof item === 'string'));
        const url = String(sampleUrl || '').trim();
        if(!url) return list;
        if(list.some(item => String(item.url || item) === url)) return list;
        return [{url, name: sampleName || '角色样音', kind: 'audio'}, ...list];
    }

    function convertAudios(node, connected){
        const sampleUrl = node?.voiceSampleUrl || node?.runSettings?.videoVoiceSampleUrl || '';
        const sampleName = node?.voiceSampleName || node?.runSettings?.videoVoiceSampleName || '角色样音';
        return mergeAudios(connected, sampleUrl, sampleName).map((item, i) => ({
            name: item.name || `音${i + 1}`,
            url: item.url || item,
            role: i === 0 ? 'character_voice' : ''
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

    function panelHtml(state){
        injectStyles();
        const providers = speechProviders(state.providers);
        const providerId = state.providerId || providers[0]?.id || '';
        const cached = voiceCache[providerId] || {};
        const models = state.models || cached.models || [];
        const voices = state.voices || cached.voices || [];
        const model = state.model || models[0] || '';
        const voiceId = state.voiceId || voices[0]?.voice_id || '';
        const text = state.text || DEFAULT_TEXT;
        const sampleUrl = state.sampleUrl || '';
        if(!providers.length){
            return `<div class="character-voice-panel"><span class="vpt-row-label">请先在 API 设置接入 MiniMax 语音协议并保存 Key</span></div>`;
        }
        return `<div class="character-voice-panel" data-character-voice-panel="1">
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
            sampleUrl: src.voiceSampleUrl || src.videoVoiceSampleUrl || '',
            sampleName: src.voiceSampleName || src.videoVoiceSampleName || ''
        };
    }

    function applySampleToNode(node, sample, fields){
        if(!node) return;
        node.voiceSampleUrl = sample.url;
        node.voiceSampleName = sample.name || '角色样音';
        node.voiceId = sample.voice_id || fields.voiceId || node.voiceId;
        node.speechModel = sample.model || fields.model || node.speechModel;
        node.speechProviderId = fields.providerId || node.speechProviderId;
        node.characterVoice = true;
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

    window.CharacterVoice = {
        PROTOCOL,
        DEFAULT_TEXT,
        speechProviders,
        mergeAudios,
        convertAudios,
        fetchVoices,
        generateSample,
        voiceCache,
        panelHtml,
        readStateFrom,
        applySampleToNode,
        applySampleToSettings
    };
})();
