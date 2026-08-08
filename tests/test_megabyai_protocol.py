import asyncio
import unittest

from fastapi import HTTPException

import main
import megabyai_protocol as megabyai


def run(coro):
    return asyncio.run(coro)


MEGABYAI_MODELS_RESPONSE = {
    "object": "list",
    "data": [
        {"id": "videos-standard", "owned_by": "video-api", "supported_endpoint_types": ["openai-video"]},
        {"id": "videos-fast", "owned_by": "video-api", "supported_endpoint_types": ["openai-video"]},
        {"id": "videos-mini", "owned_by": "video-api", "supported_endpoint_types": ["openai-video"]},
        {"id": "happyhorse-1.0", "owned_by": "custom", "supported_endpoint_types": ["openai-video"]},
        {"id": "gpt-image-2", "owned_by": "openai", "supported_endpoint_types": ["image-generation", "openai"]},
        {"id": "gpt-5.5", "owned_by": "custom", "supported_endpoint_types": ["openai"]},
        {"id": "description-only-video", "owned_by": "video-api", "description": "video generation via async /v1/videos"},
    ],
}


def video_payload(**kwargs):
    base = {
        "prompt": "电影感镜头缓慢推进",
        "provider_id": "megabyai",
        "model": "videos-mini",
        "duration": 5,
        "aspect_ratio": "16:9",
        "resolution": "720p",
    }
    base.update(kwargs)
    return main.CanvasVideoRequest(**base)


async def fake_resolve(value, kind, index):
    return f"https://cdn.example/{kind}-{index}.media"


class MegabyaiRoutingTests(unittest.TestCase):
    def test_protocol_and_urls_are_registered(self):
        provider = {"id": "megabyai", "protocol": main.MEGABYAI_PROTOCOL}

        self.assertIn(main.MEGABYAI_PROTOCOL, main.SUPPORTED_PROVIDER_PROTOCOLS)
        self.assertTrue(main.is_megabyai_provider(provider))
        self.assertTrue(main.is_megabyai_route(provider, "videos-mini"))
        self.assertFalse(main.is_pidoi_route(provider, "videos-mini"))
        self.assertEqual(
            main.video_submit_url_candidates(provider, "https://newapi.megabyai.cc/v1", "videos-mini"),
            ["https://newapi.megabyai.cc/v1/videos"],
        )
        self.assertEqual(
            main.video_task_url_candidates(provider, "https://newapi.megabyai.cc", "task/42", "", "videos-mini"),
            ["https://newapi.megabyai.cc/v1/videos/task%2F42"],
        )

    def test_models_use_upstream_capabilities_not_names(self):
        grouped, ids = main.parse_upstream_models(MEGABYAI_MODELS_RESPONSE, main.MEGABYAI_PROTOCOL)

        self.assertEqual(len(ids), 7)
        for model in ("videos-standard", "videos-fast", "videos-mini", "happyhorse-1.0"):
            self.assertIn(model, grouped["video"])
        self.assertIn("gpt-image-2", grouped["image"])
        self.assertIn("gpt-5.5", grouped["chat"])
        # 文档样例用 owned_by/description 声明 /v1/videos，即使没有能力数组也应归视频。
        self.assertIn("description-only-video", grouped["video"])

    def test_unknown_capability_is_not_guessed_as_video(self):
        self.assertEqual(megabyai.classify_megabyai_model_entry({}, "video-looking-model"), "chat")
        self.assertEqual(megabyai.classify_megabyai_model_entry(None, "videos-mini"), "chat")

    def test_dynamic_video_family_does_not_use_provider_video_model_list(self):
        self.assertEqual(
            megabyai.megabyai_model_family("seedance-2.5"),
            megabyai.MEGABYAI_FAMILY_DYNAMIC_VIDEO,
        )
        self.assertEqual(megabyai.megabyai_model_family("renamed-video"), megabyai.MEGABYAI_FAMILY_DYNAMIC_VIDEO)


class MegabyaiRequestTests(unittest.TestCase):
    def test_documented_fields_and_camel_case_reference_arrays(self):
        payload = video_payload(
            model="videos-mini",
            duration=15,
            aspect_ratio="9:16",
            resolution="480p",
            images=[{"url": "https://example.com/person.jpg"}],
            videos=["https://example.com/motion.mp4"],
            audios=["https://example.com/voice.mp3"],
        )
        body = run(megabyai.build_megabyai_video_request(payload, "videos-mini", fake_resolve))

        self.assertEqual(
            body,
            {
                "model": "videos-mini",
                "prompt": "电影感镜头缓慢推进",
                "duration": 15,
                "ratio": "9:16",
                "resolution": "480p",
                "referenceImages": ["https://cdn.example/图片-1.media"],
                "referenceVideos": ["https://cdn.example/视频-1.media"],
                "referenceAudios": ["https://cdn.example/音频-1.media"],
            },
        )
        for wrong_key in ("aspect_ratio", "size", "reference_image_urls", "reference_images", "images", "videos", "audios", "audio"):
            self.assertNotIn(wrong_key, body)

    def test_empty_reference_arrays_are_omitted(self):
        body = run(megabyai.build_megabyai_video_request(video_payload(), "videos-mini", fake_resolve))
        for key in ("referenceImages", "referenceVideos", "referenceAudios"):
            self.assertNotIn(key, body)

    def test_duration_ratio_and_resolution_are_validated(self):
        for kwargs, message in (
            ({"duration": 3}, "4-15"),
            ({"duration": 16}, "4-15"),
            ({"aspect_ratio": "21:9"}, "画幅"),
            ({"resolution": "1080p"}, "清晰度"),
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(HTTPException) as ctx:
                    run(megabyai.build_megabyai_video_request(video_payload(**kwargs), "videos-mini", fake_resolve))
                self.assertIn(message, ctx.exception.detail)

    def test_dynamic_video_model_accepts_three_seconds(self):
        payload = video_payload(model="seedance-2.5", duration=3)
        body = run(
            megabyai.build_megabyai_video_request(
                payload,
                "seedance-2.5",
                fake_resolve,
            )
        )
        self.assertEqual(body["model"], "seedance-2.5")
        self.assertEqual(body["duration"], 3)

    def test_reference_limits_are_rejected_not_truncated(self):
        cases = (
            ("images", 10, "9"),
            ("videos", 4, "3"),
            ("audios", 4, "3"),
        )
        for field, count, limit in cases:
            with self.subTest(field=field):
                values = [{"url": f"https://example.com/{index}.media"} for index in range(count)] if field == "images" else [f"https://example.com/{index}.media" for index in range(count)]
                with self.assertRaises(HTTPException) as ctx:
                    run(megabyai.build_megabyai_video_request(video_payload(**{field: values}), "videos-mini", fake_resolve))
                self.assertIn(limit, ctx.exception.detail)

    def test_first_last_frames_and_generate_audio_are_rejected(self):
        cases = (
            {"images": [{"url": "https://example.com/first.jpg", "role": "first_frame"}]},
            {"generate_audio": True},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(HTTPException) as ctx:
                    run(megabyai.build_megabyai_video_request(video_payload(**kwargs), "videos-mini", fake_resolve))
                self.assertEqual(ctx.exception.status_code, 400)

    def test_model_name_is_not_a_submission_allowlist(self):
        for model in ("brand-new-video", "happyhorse-1.0", "seedance-2.5"):
            with self.subTest(model=model):
                body = run(
                    megabyai.build_megabyai_video_request(
                        video_payload(model=model, duration=3),
                        model,
                        fake_resolve,
                    )
                )
                self.assertEqual(body["model"], model)
                self.assertEqual(body["duration"], 3)

    def test_trusted_asset_flag_still_uses_public_reference_resolver(self):
        payload = video_payload(
            trusted_asset=True,
            images=[{"url": "/assets/local.png"}],
        )
        body = run(megabyai.build_megabyai_video_request(payload, "videos-mini", fake_resolve))
        self.assertEqual(body["referenceImages"], ["https://cdn.example/图片-1.media"])

    def test_non_public_reference_is_rejected(self):
        payload = video_payload(images=[{"url": "/assets/local.png"}])
        with self.assertRaises(HTTPException) as ctx:
            run(megabyai.build_megabyai_video_request(payload, "videos-mini"))
        self.assertIn("公网", ctx.exception.detail)


class MegabyaiResponseTests(unittest.TestCase):
    def test_task_id_and_state_are_normalized(self):
        self.assertEqual(megabyai.megabyai_task_id({"id": "id", "task_id": "task"}), "task")
        self.assertEqual(megabyai.megabyai_task_id({"data": {"id": "nested"}}), "nested")
        self.assertEqual(megabyai.megabyai_task_state({"status": "queued"})[0], "pending")
        self.assertEqual(megabyai.megabyai_task_state({"status": "in_progress"})[0], "pending")
        self.assertEqual(megabyai.megabyai_task_state({"status": "completed"})[0], "success")
        self.assertEqual(megabyai.megabyai_task_state({"status": "failed"})[0], "failed")

    def test_result_url_priority_matches_documentation(self):
        raw = {
            "url": "https://cdn.example/url.mp4",
            "video_url": "https://cdn.example/video.mp4",
            "metadata": {
                "content_url": "https://cdn.example/content.mp4",
                "local_url": "https://cdn.example/local.mp4",
            },
        }
        self.assertEqual(
            megabyai.megabyai_video_result_urls(raw),
            [
                "https://cdn.example/video.mp4",
                "https://cdn.example/url.mp4",
                "https://cdn.example/content.mp4",
                "https://cdn.example/local.mp4",
            ],
        )

    def test_error_code_and_message_are_preserved(self):
        self.assertEqual(
            megabyai.megabyai_error_text({"error": {"code": "unsupported_material", "message": "bad media"}}),
            "unsupported_material: bad media",
        )


if __name__ == "__main__":
    unittest.main()
