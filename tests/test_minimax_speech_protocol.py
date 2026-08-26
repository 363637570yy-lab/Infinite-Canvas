import unittest

import minimax_speech_protocol as speech
import main


class MiniMaxSpeechRoutingTests(unittest.TestCase):
    def test_protocol_is_registered(self):
        self.assertEqual(speech.MINIMAX_SPEECH_PROTOCOL, "minimax-speech")
        self.assertIn(speech.MINIMAX_SPEECH_PROTOCOL, main.SUPPORTED_PROVIDER_PROTOCOLS)
        self.assertTrue(main.is_minimax_speech_protocol("minimax-speech"))
        self.assertFalse(main.is_h3_protocol("minimax-speech"))
        self.assertFalse(main.is_minimax_speech_protocol("h3"))

    def test_urls_strip_version_suffix(self):
        self.assertEqual(
            speech.minimax_t2a_url("https://api.minimaxi.com/v1"),
            "https://api.minimaxi.com/v1/t2a_v2",
        )
        self.assertEqual(
            speech.minimax_get_voice_url("https://api.minimax.io/"),
            "https://api.minimax.io/v1/get_voice",
        )
        self.assertIn(
            "GroupId=g123",
            speech.minimax_url_with_group("https://api.minimaxi.com/v1/t2a_v2", "g123"),
        )


class MiniMaxSpeechClassifyTests(unittest.TestCase):
    def test_classifies_from_capability_fields_not_names(self):
        self.assertEqual(
            speech.classify_minimax_speech_model_entry(
                {"id": "speech-2.8-hd", "supported_endpoint_types": ["audio"]},
                "speech-2.8-hd",
            ),
            "audio",
        )
        self.assertEqual(
            speech.classify_minimax_speech_model_entry(
                {"id": "MiniMax-M2.7", "capabilities": {"chat": True}},
                "MiniMax-M2.7",
            ),
            "chat",
        )
        self.assertEqual(
            speech.classify_minimax_speech_model_entry({"id": "speech-2.8-hd"}, "speech-2.8-hd"),
            "unknown",
        )
        grouped, ids = main.parse_upstream_models(
            {
                "data": [
                    {"id": "speech-2.8-hd"},
                    {"id": "MiniMax-M2.7", "supported_endpoint_types": ["openai-chat"]},
                ]
            },
            speech.MINIMAX_SPEECH_PROTOCOL,
        )
        self.assertEqual(ids, ["MiniMax-M2.7", "speech-2.8-hd"])
        self.assertEqual(grouped["audio"], [])
        self.assertEqual(grouped["unknown"], ["speech-2.8-hd"])
        self.assertEqual(grouped["chat"], ["MiniMax-M2.7"])
        self.assertEqual(grouped["video"], [])


class MiniMaxT2ARequestTests(unittest.TestCase):
    def test_sample_body_matches_official_t2a_fields(self):
        body = speech.build_t2a_request("春色正好。", "male-qn-qingse", "speech-2.8-hd")
        self.assertEqual(body["model"], "speech-2.8-hd")
        self.assertEqual(body["text"], "春色正好。")
        self.assertFalse(body["stream"])
        self.assertEqual(body["voice_setting"]["voice_id"], "male-qn-qingse")
        self.assertEqual(body["audio_setting"]["format"], "mp3")
        self.assertNotIn("generate_audio", body)
        self.assertNotIn("images", body)
        self.assertNotIn("prompt", body)

    def test_unknown_t2a_model_falls_back_to_default_enum(self):
        body = speech.build_t2a_request("你好", "v1", "not-a-real-model")
        self.assertEqual(body["model"], speech.MINIMAX_DEFAULT_T2A_MODEL)

    def test_sample_text_is_capped(self):
        body = speech.build_t2a_request("哈" * 500, "v1")
        self.assertEqual(len(body["text"]), speech.MINIMAX_SAMPLE_TEXT_MAX)

    def test_missing_voice_id_is_rejected(self):
        with self.assertRaises(ValueError):
            speech.build_t2a_request("你好", "")

    def test_decode_official_hex_audio(self):
        payload = {
            "data": {"audio": "4d5033aa", "status": 2},
            "extra_info": {"audio_length": 9900},
            "base_resp": {"status_code": 0, "status_msg": "success"},
        }
        self.assertEqual(speech.decode_t2a_audio_bytes(payload), bytes.fromhex("4d5033aa"))
        self.assertEqual(speech.extra_audio_length_ms(payload), 9900)

    def test_decode_base64_audio_file(self):
        payload = {
            "audio_file": "TVAz",
            "base_resp": {"status_code": 0, "status_msg": "success"},
        }
        self.assertTrue(speech.decode_t2a_audio_bytes(payload))

    def test_nonzero_base_resp_is_an_error(self):
        payload = {"base_resp": {"status_code": 1004, "status_msg": "login check failed"}}
        with self.assertRaisesRegex(ValueError, "login check failed"):
            speech.decode_t2a_audio_bytes(payload)

    def test_parse_system_voices(self):
        raw = {
            "system_voice": [
                {"voice_id": "male-qn-qingse", "voice_name": "青涩", "description": ["青年男声"]},
            ],
            "voice_cloning": [],
        }
        voices = speech.parse_voice_list(raw)
        self.assertEqual(voices[0]["voice_id"], "male-qn-qingse")
        self.assertEqual(voices[0]["source"], "system_voice")
        self.assertEqual(speech.build_get_voice_request("nope"), {"voice_type": "system"})
