import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def collect_video_audios(node, connected):
    """与 static/js/character-voice.js 的 collectVideoAudios 对齐的规格。"""
    def url_of(item):
        if not item:
            return ""
        if isinstance(item, str):
            return item.strip()
        return str(item.get("url") or "").strip()

    def normalize(item):
        if isinstance(item, str):
            text = item.strip()
            return {"url": text, "name": "音频", "kind": "audio"} if text else None
        url = url_of(item)
        if not url:
            return None
        return {
            "url": url,
            "name": item.get("name") or "音频",
            "kind": item.get("kind") or "audio",
            "role": item.get("role") or "",
            "sourceType": item.get("sourceType") or "",
        }

    def is_voice(item):
        if not item:
            return False
        return item.get("sourceType") in ("voice", "legacy") or item.get("role") == "character_voice"

    leftover_url = str((node or {}).get("voiceSampleUrl") or "").strip()
    leftover = None
    if leftover_url:
        leftover = {
            "url": leftover_url,
            "name": (node or {}).get("voiceSampleName") or "角色样音",
            "kind": "audio",
            "role": "character_voice",
            "sourceType": "legacy",
        }
    merged = [item for item in (normalize(x) for x in (connected or [])) if item]
    if leftover and leftover["url"] not in {item["url"] for item in merged}:
        merged = [leftover, *merged]
    voice = [item for item in merged if is_voice(item)]
    rest = [item for item in merged if item not in voice]
    return [*voice, *rest]


def uses_character_voice(node, connected):
    if (node or {}).get("characterVoice"):
        return True
    return any(
        item.get("sourceType") in ("voice", "legacy") or item.get("role") == "character_voice"
        for item in collect_video_audios(node, connected)
    )


class CharacterVoiceNodeContractTests(unittest.TestCase):
    def test_classic_canvas_exposes_voice_node(self):
        html = _read("static/canvas.html")
        self.assertIn('onclick="addVoiceNode()"', html)
        self.assertIn("menuAdd('voice')", html)
        js = _read("static/js/canvas.js")
        self.assertIn("function addVoiceNode(", js)
        self.assertIn("type === 'voice'", js)
        self.assertIn("from.type === 'voice'", js)
        self.assertIn("CharacterVoice.collectVideoAudios", js)
        self.assertIn("CharacterVoice.usesCharacterVoice", js)
        self.assertIn("点框里的音频可选为当前音色", js)
        self.assertIn("function attachCanvasVoiceSampleNode(", js)
        self.assertIn("function voiceOutputNodes(", js)
        self.assertIn("from: voiceNode.id, to: out.id", js)
        self.assertIn("generatedOutputs", js)
        self.assertIn("to.type === 'output'", js)
        self.assertIn("video-input-audio", js)
        self.assertIn("<audio src=", js)
        self.assertIn("function beginCanvasAudioNodeRename(", js)
        self.assertIn("kind === 'audio' || kind === 'video'", js)
        self.assertIn("n.voiceSampleNodeId === id", js)
        self.assertIn("n.voiceSampleOutputId === id", js)
        self.assertIn("onSample(sample){ attachCanvasVoiceSampleNode(node, sample); }", js)
        self.assertIn("voice-title-input", js)
        self.assertIn("mediaKindForNode(to) === 'audio'", js)
        self.assertIn("kind !== 'image' && kind !== 'audio'", js)
        self.assertIn("is-audio", js)
        self.assertIn("is-selected", js)
        self.assertNotIn("audioNode.url = url", js)
        self.assertNotIn("from: audioNode.id, to: dest.id", js)
        self.assertNotIn('data-video-toggle="characterVoice"', js)
        self.assertNotIn('data-video-toggle="enhancePrompt"', js)
        self.assertNotIn('data-video-toggle="enableUpsample"', js)
        self.assertNotIn('data-video-toggle="watermark"', js)
        self.assertNotIn('data-video-toggle="cameraFixed"', js)
        self.assertNotIn("bindCanvasCharacterVoicePanel", js)
        self.assertNotIn("function videoNodeShowsCharacterVoice", js)
        css = _read("static/css/canvas.css")
        self.assertIn(".video-input-item.is-audio", css)
        self.assertIn(".video-input-audio audio", css)
        self.assertIn(".output-audio-wrap.is-selected", css)

    def test_character_voice_module_owns_node_helpers(self):
        js = _read("static/js/character-voice.js")
        for token in (
            "NODE_TYPE = 'voice'",
            "function createNodeData",
            "function collectVideoAudios",
            "function usesCharacterVoice",
            "function renderBody",
            "function bindPanel",
            "function mediaRef",
            "function generatorSource",
            "function displaySampleName",
            "function looksLikeVoiceId",
            'data-cv="name"',
            "state.standalone",
            "读取音色",
            "sourceType: 'legacy'",
            "sampleUrl: ''",
            "node.url = ''",
            "generatedOutputs: []",
            "voiceSampleOutputId: ''",
            "const models = providerSpeechModels(provider);",
        ):
            self.assertIn(token, js)
        self.assertIn("微风拂过柔软的草地", js)
        self.assertIn("LEGACY_SAMPLE_TEXTS", js)
        self.assertNotIn("node.url = url", js)

    def test_i18n_has_voice_keys(self):
        i18n = _read("static/js/i18n/canvas.js")
        self.assertIn('"canvas.voiceNode"', i18n)
        self.assertIn('"canvas.voiceHint"', i18n)
        self.assertIn("点框里的音频可选为当前音色", i18n)
        version = _read("static/js/i18n.js")
        self.assertIn("2026.08.30.voice-output.1", version)

    def test_default_sample_text_matches_protocol(self):
        import minimax_speech_protocol as speech
        js = _read("static/js/character-voice.js")
        self.assertIn(speech.MINIMAX_DEFAULT_SAMPLE_TEXT, js)

    def test_dead_video_toggles_are_not_sent(self):
        classic = _read("static/js/canvas.js")
        self.assertIn("enhance_prompt:false", classic)
        self.assertIn("enable_upsample:false", classic)
        self.assertIn("watermark:false", classic)
        self.assertIn("camerafixed:false", classic)


class CharacterVoiceAudioOrderTests(unittest.TestCase):
    def test_connected_voice_is_audio_one_before_bgm(self):
        audios = collect_video_audios({}, [
            {"url": "/output/bgm.mp3", "name": "bgm", "kind": "audio"},
            {"url": "/output/sample.mp3", "name": "角色样音", "kind": "audio", "role": "character_voice", "sourceType": "voice"},
        ])
        self.assertEqual(audios[0]["url"], "/output/sample.mp3")
        self.assertEqual(audios[1]["url"], "/output/bgm.mp3")
        self.assertTrue(uses_character_voice({}, audios))

    def test_leftover_video_sample_still_counts(self):
        node = {"voiceSampleUrl": "/output/old.mp3", "voiceSampleName": "旧样音"}
        audios = collect_video_audios(node, [])
        self.assertEqual(audios[0]["url"], "/output/old.mp3")
        self.assertEqual(audios[0]["sourceType"], "legacy")
        self.assertTrue(uses_character_voice(node, []))

    def test_generic_audio_is_not_character_voice(self):
        connected = [{"url": "/output/bgm.mp3", "name": "bgm", "kind": "audio"}]
        self.assertFalse(uses_character_voice({}, connected))
        self.assertEqual(collect_video_audios({}, connected)[0]["url"], "/output/bgm.mp3")

    def test_legacy_checkbox_still_flags_character_voice(self):
        self.assertTrue(uses_character_voice({"characterVoice": True}, []))

    def test_duplicate_leftover_url_is_not_prepended_twice(self):
        node = {"voiceSampleUrl": "/output/sample.mp3"}
        audios = collect_video_audios(node, [
            {"url": "/output/sample.mp3", "name": "角色样音", "kind": "audio", "sourceType": "voice", "role": "character_voice"},
        ])
        self.assertEqual([item["url"] for item in audios], ["/output/sample.mp3"])


if __name__ == "__main__":
    unittest.main()
