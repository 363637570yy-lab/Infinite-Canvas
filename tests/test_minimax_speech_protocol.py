import asyncio
import unittest
from unittest import mock

import minimax_speech_protocol as speech
import main


class MiniMaxSpeechRoutingTests(unittest.TestCase):
    def test_protocol_is_registered(self):
        self.assertEqual(speech.MINIMAX_SPEECH_PROTOCOL, "minimax-speech")
        self.assertIn(speech.MINIMAX_SPEECH_PROTOCOL, main.SUPPORTED_PROVIDER_PROTOCOLS)
        self.assertIn(speech.MINIMAX_PROTOCOL, main.SUPPORTED_PROVIDER_PROTOCOLS)
        self.assertTrue(main.is_minimax_speech_protocol("minimax-speech"))
        self.assertTrue(main.is_minimax_speech_protocol("minimax"))
        self.assertFalse(main.is_h3_protocol("minimax-speech"))
        self.assertFalse(main.is_minimax_speech_protocol("h3"))
        self.assertFalse(main.is_h3_route({"protocol": "minimax-speech"}, "MiniMax-H3"))
        self.assertTrue(main.is_minimax_official_h3_route({"protocol": "minimax-speech"}, "MiniMax-H3"))
        self.assertEqual(
            main.video_submit_url_candidates({"protocol": "minimax-speech"}, "https://api.minimaxi.com", "MiniMax-H3"),
            ["https://api.minimaxi.com/v2/video_generation"],
        )
        self.assertEqual(
            speech.minimax_h3_query_url("https://api.minimaxi.com/v1", "4240"),
            "https://api.minimaxi.com/v2/query/video_generation/4240",
        )
        self.assertEqual(main.canvas_video_task_type({"protocol": "minimax-speech"}, "MiniMax-H3"), "minimax-h3-video")
        self.assertIn("minimax-h3-video", main.CANVAS_VIDEO_TASK_TYPES)

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
    def test_classifies_from_official_catalog_then_capabilities(self):
        self.assertEqual(
            speech.classify_minimax_speech_model_entry(
                {"id": "speech-2.8-hd", "supported_endpoint_types": ["audio"]},
                "speech-2.8-hd",
            ),
            "audio",
        )
        self.assertEqual(
            speech.classify_minimax_speech_model_entry({"id": "speech-2.8-hd"}, "speech-2.8-hd"),
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
            speech.classify_minimax_speech_model_entry({"id": "MiniMax-M2.7"}, "MiniMax-M2.7"),
            "chat",
        )
        self.assertEqual(
            speech.classify_minimax_speech_model_entry({"id": "image-01"}, "image-01"),
            "image",
        )
        self.assertEqual(
            speech.classify_minimax_speech_model_entry({"id": "MiniMax-H3"}, "MiniMax-H3"),
            "video",
        )
        self.assertEqual(
            speech.classify_minimax_speech_model_entry({"id": "not-a-real-model"}, "not-a-real-model"),
            "unknown",
        )
        grouped, ids = main.parse_upstream_models(
            {
                "data": [
                    {"id": "speech-2.8-hd"},
                    {"id": "MiniMax-M2.7"},
                    {"id": "custom-foo"},
                ]
            },
            speech.MINIMAX_SPEECH_PROTOCOL,
        )
        self.assertEqual(ids, ["MiniMax-M2.7", "custom-foo", "speech-2.8-hd"])
        self.assertEqual(grouped["audio"], ["speech-2.8-hd"])
        self.assertEqual(grouped["chat"], ["MiniMax-M2.7"])
        self.assertEqual(grouped["unknown"], ["custom-foo"])
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


def run(coro):
    return asyncio.run(coro)


class MiniMaxOfficialImageTests(unittest.TestCase):
    def test_text_to_image_body(self):
        body = speech.build_image_request("海边的女孩", "image-01", "16:9")
        self.assertEqual(body["model"], "image-01")
        self.assertEqual(body["aspect_ratio"], "16:9")
        self.assertFalse(body["prompt_optimizer"])
        self.assertNotIn("subject_reference", body)

    def test_character_reference_is_explicit(self):
        body = speech.build_image_request(
            "同一人",
            "image-01",
            reference_images=[{"url": "https://cdn.example/a.png", "role": "character"}],
        )
        self.assertEqual(body["subject_reference"][0]["type"], "character")
        self.assertEqual(body["subject_reference"][0]["image_file"], "https://cdn.example/a.png")

    def test_extra_reference_images_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "最多 1 张"):
            speech.build_image_request(
                "同一人",
                "image-01",
                reference_images=[{"url": "https://a"}, {"url": "https://b"}],
            )


class MiniMaxOfficialH3VideoTests(unittest.TestCase):
    def test_text_to_video_requires_ratio(self):
        payload = main.CanvasVideoRequest(
            prompt="女舰长站在观景窗前",
            provider_id="minimax",
            model="MiniMax-H3",
            duration=5,
            aspect_ratio="16:9",
            resolution="768P",
        )
        body, mode = run(speech.build_h3_official_video_request(payload, "MiniMax-H3"))
        self.assertEqual(mode, "t2va")
        self.assertEqual(body["model"], "MiniMax-H3")
        self.assertEqual(body["resolution"], "768P")
        self.assertEqual(body["duration"], 5)
        self.assertEqual(body["ratio"], "16:9")
        self.assertEqual(body["content"][0]["type"], "text")
        self.assertNotIn("generate_audio", body)

    def test_first_last_frames_are_i2va(self):
        payload = main.CanvasVideoRequest(
            prompt="小女孩长大",
            provider_id="minimax",
            model="MiniMax-H3",
            duration=6,
            resolution="2K",
            images=[
                main.AIReference(url="https://cdn.example/first.png", role="first_frame"),
                main.AIReference(url="https://cdn.example/last.png", role="last_frame"),
            ],
        )
        body, mode = run(speech.build_h3_official_video_request(payload, "MiniMax-H3"))
        self.assertEqual(mode, "i2va")
        self.assertNotIn("ratio", body)
        roles = [item.get("role") for item in body["content"] if item.get("type") == "image_url"]
        self.assertEqual(roles, ["first_frame", "last_frame"])

    def test_reference_audio_uses_r2va(self):
        payload = main.CanvasVideoRequest(
            prompt="人物说话，音色参考音频1",
            provider_id="minimax",
            model="MiniMax-H3",
            duration=5,
            aspect_ratio="adaptive",
            resolution="",
            images=[main.AIReference(url="https://cdn.example/ref.png", role="reference_image")],
            audios=["https://cdn.example/voice.mp3"],
        )
        body, mode = run(speech.build_h3_official_video_request(payload, "MiniMax-H3"))
        self.assertEqual(mode, "r2va")
        self.assertEqual(body["ratio"], "adaptive")
        self.assertEqual(body["resolution"], "768P")
        kinds = [item["type"] for item in body["content"]]
        self.assertEqual(kinds, ["text", "image_url", "audio_url"])
        self.assertEqual(body["content"][2]["role"], "reference_audio")

    def test_frame_and_reference_roles_are_mutex(self):
        payload = main.CanvasVideoRequest(
            prompt="冲突",
            provider_id="minimax",
            model="MiniMax-H3",
            images=[
                main.AIReference(url="https://cdn.example/first.png", role="first_frame"),
                main.AIReference(url="https://cdn.example/ref.png", role="reference_image"),
            ],
        )
        with self.assertRaisesRegex(ValueError, "互斥"):
            run(speech.build_h3_official_video_request(payload, "MiniMax-H3"))

    def test_hailuo_legacy_model_is_rejected(self):
        payload = main.CanvasVideoRequest(prompt="x", provider_id="minimax", model="MiniMax-Hailuo-2.3")
        with self.assertRaisesRegex(ValueError, "Hailuo"):
            run(speech.build_h3_official_video_request(payload, "MiniMax-Hailuo-2.3"))

    def test_empty_resolution_maps_to_768p_not_2k(self):
        payload = main.CanvasVideoRequest(prompt="x", provider_id="minimax", model="MiniMax-H3", resolution="720p")
        body, _mode = run(speech.build_h3_official_video_request(payload, "MiniMax-H3"))
        self.assertEqual(body["resolution"], "768P")

    def test_task_query_shape(self):
        raw = {
            "task": {
                "id": "424010985738629",
                "status": "succeeded",
                "content": {"url": "https://cdn.example/out.mp4"},
            }
        }
        self.assertEqual(speech.h3_task_id({"task_id": "424010985738629"}), "424010985738629")
        self.assertEqual(speech.h3_task_state(raw), ("success", "succeeded"))
        self.assertEqual(speech.h3_result_url(raw), "https://cdn.example/out.mp4")


class MiniMaxFetchModelsTests(unittest.TestCase):
    def test_fetch_models_includes_official_speech_catalog(self):
        catalog = speech.merge_official_catalog()

        async def fake_probe(client, base_url, api_key, group_id=""):
            return {
                "ok": True,
                "status": 200,
                "image_models": catalog["image"],
                "chat_models": catalog["chat"],
                "video_models": catalog["video"],
                "audio_models": catalog["audio"],
                "speech_models": catalog["speech_models"],
                "unknown_models": [],
                "all": catalog["all"],
                "voices": [],
                "voice_count": 0,
                "model_count": len(catalog["all"]),
                "message": "ok",
                "raw": {},
            }

        async def run_fetch():
            with mock.patch("main.probe_minimax_speech_endpoint", fake_probe):
                return await main.fetch_models_from_upstream(
                    "https://api.minimaxi.com",
                    "test-key",
                    "minimax-speech",
                )

        data = run(run_fetch())
        self.assertIn("speech-2.8-hd", data["audio_models"])
        self.assertIn("speech-2.8-hd", data["all"])
        self.assertIn("MiniMax-H3", data["video_models"])
        self.assertIn("image-01", data["image_models"])
        self.assertGreaterEqual(data["total"], 11)
