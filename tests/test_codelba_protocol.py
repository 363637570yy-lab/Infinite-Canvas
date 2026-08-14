import asyncio
import unittest

from fastapi import HTTPException

import main
import codelba_protocol as codelba


def run(coro):
    return asyncio.run(coro)


CODELBA_MODELS_RESPONSE = {
    "object": "list",
    "data": [
        {"id": "sd-2-c5", "supported_endpoint_types": ["openai-video"]},
        {"id": "sd-2-c5-10", "supported_endpoint_types": ["video"]},
        {"id": "seedance2.0-14s", "owned_by": "video-api", "description": "async /openapi/v1/videos"},
        {"id": "gpt-image-2", "supported_endpoint_types": ["image-generation"]},
        {"id": "gpt-5.5", "supported_endpoint_types": ["openai"]},
        {"id": "mystery-model"},
        {"id": "name-looks-like-video"},
    ],
}


def video_payload(**kwargs):
    base = {
        "prompt": "人物站在海边，海风吹动衣角，镜头缓慢向前推进，电影质感。",
        "provider_id": "codelba",
        "model": "sd-2-c5",
        "duration": 10,
        "aspect_ratio": "16:9",
    }
    base.update(kwargs)
    return main.CanvasVideoRequest(**base)


async def fake_resolve(value, kind, index):
    return f"https://cdn.example/{kind}-{index}.media"


class CodelbaRoutingTests(unittest.TestCase):
    def test_protocol_and_openapi_urls_are_registered(self):
        provider = {"id": "codelba", "protocol": main.CODELBA_PROTOCOL}

        self.assertIn(main.CODELBA_PROTOCOL, main.SUPPORTED_PROVIDER_PROTOCOLS)
        self.assertTrue(main.is_codelba_provider(provider))
        self.assertTrue(main.is_codelba_route(provider, "sd-2-c5"))
        self.assertFalse(main.is_chre3_video_route(provider, "sd-2-c5"))
        self.assertFalse(main.is_cangyuan_video_route(provider, "sd-2-c5"))
        self.assertFalse(main.is_megabyai_route(provider, "sd-2-c5"))
        self.assertEqual(
            main.video_submit_url_candidates(provider, "https://codelba.cn/v1", "sd-2-c5"),
            ["https://codelba.cn/openapi/v1/videos"],
        )
        self.assertEqual(
            main.video_submit_url_candidates(provider, "https://codelba.cn/openapi/v1", "sd-2-c5"),
            ["https://codelba.cn/openapi/v1/videos"],
        )
        self.assertEqual(
            main.video_task_url_candidates(provider, "https://codelba.cn", "video-168", "", "sd-2-c5"),
            ["https://codelba.cn/openapi/v1/videos/video-168"],
        )
        self.assertEqual(
            codelba.codelba_video_content_url("https://codelba.cn/v1", "video-168"),
            "https://codelba.cn/openapi/v1/videos/video-168/content",
        )
        self.assertEqual(
            main.upstream_models_url("https://codelba.cn", main.CODELBA_PROTOCOL),
            "https://codelba.cn/openapi/v1/models",
        )

    def test_models_use_upstream_capabilities_not_names(self):
        grouped, ids = main.parse_upstream_models(CODELBA_MODELS_RESPONSE, main.CODELBA_PROTOCOL)

        self.assertEqual(len(ids), 7)
        for model in ("sd-2-c5", "sd-2-c5-10", "seedance2.0-14s"):
            self.assertIn(model, grouped["video"])
        self.assertIn("gpt-image-2", grouped["image"])
        self.assertIn("gpt-5.5", grouped["chat"])
        self.assertIn("mystery-model", grouped["unknown"])
        self.assertIn("name-looks-like-video", grouped["unknown"])
        self.assertNotIn("name-looks-like-video", grouped["video"])

    def test_unknown_capability_is_not_guessed_as_video(self):
        self.assertEqual(codelba.classify_codelba_model_entry({}, "sd-2-c5"), "unknown")
        self.assertEqual(codelba.classify_codelba_model_entry(None, "seedance2.0-14s"), "unknown")
        self.assertEqual(codelba.classify_codelba_model_entry({"id": "video-looking-model"}, "video-looking-model"), "unknown")


class CodelbaFamilyTests(unittest.TestCase):
    def test_family_registry_rejects_undocumented_models(self):
        self.assertEqual(codelba.codelba_model_family("sd-2-c5"), codelba.CODELBA_FAMILY_SD_2_C5)
        self.assertEqual(codelba.codelba_model_family("sd-2-c5-10"), codelba.CODELBA_FAMILY_SD_2_C5_10)
        self.assertEqual(codelba.codelba_model_family("seedance2.0-14s"), codelba.CODELBA_FAMILY_SEEDANCE_2_14S)
        for model in ("sd2-c5", "sd-2-c7", "seedance-2.0", "seedance2.0"):
            self.assertEqual(codelba.codelba_model_family(model), "")
            with self.assertRaises(HTTPException) as ctx:
                run(codelba.build_codelba_video_request(video_payload(model=model), model, fake_resolve))
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("请求体家族", ctx.exception.detail)


class CodelbaRequestTests(unittest.TestCase):
    def test_sd2_c5_uses_pixel_size_and_snake_case_refs(self):
        payload = video_payload(
            model="sd-2-c5",
            duration=15,
            aspect_ratio="9:16",
            images=[{"url": "https://example.com/reference-1.jpg"}],
            videos=["https://example.com/motion.mp4"],
            audios=["https://example.com/voice.mp3"],
        )
        body = run(codelba.build_codelba_video_request(payload, "sd-2-c5", fake_resolve))

        self.assertEqual(
            body,
            {
                "model": "sd-2-c5",
                "prompt": "人物站在海边，海风吹动衣角，镜头缓慢向前推进，电影质感。",
                "duration": 15,
                "size": "720x1280",
                "image_refs": ["https://cdn.example/图片-1.media"],
                "video_refs": ["https://cdn.example/视频-1.media"],
                "audio_refs": ["https://cdn.example/音频-1.media"],
            },
        )
        for wrong_key in (
            "aspect_ratio",
            "ratio",
            "resolution",
            "reference_image_urls",
            "referenceImages",
            "images",
            "videos",
            "audios",
            "generate_audio",
            "compliance_enabled",
        ):
            self.assertNotIn(wrong_key, body)

    def test_empty_reference_arrays_are_omitted(self):
        body = run(codelba.build_codelba_video_request(video_payload(), "sd-2-c5", fake_resolve))
        for key in ("image_refs", "video_refs", "audio_refs"):
            self.assertNotIn(key, body)
        self.assertEqual(body["size"], "1280x720")
        self.assertEqual(body["duration"], 10)

    def test_sd2_c5_duration_and_ratio_are_exact_enums(self):
        for duration in (5, 8, 10, 15):
            body = run(codelba.build_codelba_video_request(video_payload(duration=duration), "sd-2-c5", fake_resolve))
            self.assertEqual(body["duration"], duration)
        for kwargs, message in (
            ({"duration": 6}, "只支持时长"),
            ({"aspect_ratio": "1:1"}, "画幅"),
            ({"aspect_ratio": "21:9"}, "画幅"),
            ({"resolution": "1080p"}, "720P"),
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(HTTPException) as ctx:
                    run(codelba.build_codelba_video_request(video_payload(**kwargs), "sd-2-c5", fake_resolve))
                self.assertIn(message, ctx.exception.detail)

    def test_sd2_c5_maps_43_and_34_to_documented_pixel_sizes(self):
        body_43 = run(codelba.build_codelba_video_request(video_payload(aspect_ratio="4:3"), "sd-2-c5", fake_resolve))
        body_34 = run(codelba.build_codelba_video_request(video_payload(aspect_ratio="3:4"), "sd-2-c5", fake_resolve))
        self.assertEqual(body_43["size"], "960x720")
        self.assertEqual(body_34["size"], "720x960")

    def test_sd2_c5_10_supports_11_and_rejects_15s_and_43(self):
        body = run(
            codelba.build_codelba_video_request(
                video_payload(model="sd-2-c5-10", duration=8, aspect_ratio="1:1"),
                "sd-2-c5-10",
                fake_resolve,
            )
        )
        self.assertEqual(body["model"], "sd-2-c5-10")
        self.assertEqual(body["duration"], 8)
        self.assertEqual(body["size"], "720x720")
        with self.assertRaises(HTTPException) as ctx:
            run(codelba.build_codelba_video_request(video_payload(model="sd-2-c5-10", duration=15), "sd-2-c5-10", fake_resolve))
        self.assertIn("只支持时长", ctx.exception.detail)
        with self.assertRaises(HTTPException) as ctx:
            run(codelba.build_codelba_video_request(video_payload(model="sd-2-c5-10", aspect_ratio="4:3"), "sd-2-c5-10", fake_resolve))
        self.assertIn("画幅", ctx.exception.detail)

    def test_seedance_14s_rejects_video_and_audio_refs(self):
        body = run(
            codelba.build_codelba_video_request(
                video_payload(
                    model="seedance2.0-14s",
                    duration=15,
                    aspect_ratio="9:16",
                    images=[{"url": "https://example.com/reference-1.jpg"}],
                ),
                "seedance2.0-14s",
                fake_resolve,
            )
        )
        self.assertEqual(
            body,
            {
                "model": "seedance2.0-14s",
                "prompt": "人物站在海边，海风吹动衣角，镜头缓慢向前推进，电影质感。",
                "duration": 15,
                "size": "720x1280",
                "image_refs": ["https://cdn.example/图片-1.media"],
            },
        )
        self.assertNotIn("video_refs", body)
        self.assertNotIn("audio_refs", body)
        with self.assertRaises(HTTPException) as ctx:
            run(
                codelba.build_codelba_video_request(
                    video_payload(model="seedance2.0-14s", videos=["https://example.com/a.mp4"]),
                    "seedance2.0-14s",
                    fake_resolve,
                )
            )
        self.assertIn("参考视频", ctx.exception.detail)
        with self.assertRaises(HTTPException) as ctx:
            run(
                codelba.build_codelba_video_request(
                    video_payload(model="seedance2.0-14s", audios=["https://example.com/a.mp3"]),
                    "seedance2.0-14s",
                    fake_resolve,
                )
            )
        self.assertIn("参考音频", ctx.exception.detail)

    def test_seedance_14s_accepts_duration_range_and_rejects_outside(self):
        body = run(
            codelba.build_codelba_video_request(
                video_payload(model="seedance2.0-14s", duration=6),
                "seedance2.0-14s",
                fake_resolve,
            )
        )
        self.assertEqual(body["duration"], 6)
        with self.assertRaises(HTTPException) as ctx:
            run(codelba.build_codelba_video_request(video_payload(model="seedance2.0-14s", duration=4), "seedance2.0-14s", fake_resolve))
        self.assertIn("5-15", ctx.exception.detail)

    def test_pixel_size_passthrough_and_audio_requires_image_or_video(self):
        body = run(
            codelba.build_codelba_video_request(
                video_payload(size="1280x720", aspect_ratio="9:16"),
                "sd-2-c5",
                fake_resolve,
            )
        )
        self.assertEqual(body["size"], "1280x720")
        with self.assertRaises(HTTPException) as ctx:
            run(
                codelba.build_codelba_video_request(
                    video_payload(audios=["https://example.com/a.mp3"]),
                    "sd-2-c5",
                    fake_resolve,
                )
            )
        self.assertIn("必须同时传图片或视频", ctx.exception.detail)

    def test_reference_limits_are_rejected_not_truncated(self):
        cases = (
            ("images", 10, "9"),
            ("videos", 4, "3"),
            ("audios", 4, "3"),
        )
        for field, count, limit in cases:
            with self.subTest(field=field):
                values = (
                    [{"url": f"https://example.com/{index}.media"} for index in range(count)]
                    if field == "images"
                    else [f"https://example.com/{index}.media" for index in range(count)]
                )
                kwargs = {field: values}
                if field == "audios":
                    kwargs["images"] = [{"url": "https://example.com/cover.jpg"}]
                with self.assertRaises(HTTPException) as ctx:
                    run(codelba.build_codelba_video_request(video_payload(**kwargs), "sd-2-c5", fake_resolve))
                self.assertIn(limit, ctx.exception.detail)

    def test_first_last_frames_generate_audio_and_compliance_are_rejected(self):
        cases = (
            {"images": [{"url": "https://example.com/first.jpg", "role": "first_frame"}]},
            {"generate_audio": True},
            {"compliance_enabled": True},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(HTTPException) as ctx:
                    run(codelba.build_codelba_video_request(video_payload(**kwargs), "sd-2-c5", fake_resolve))
                self.assertEqual(ctx.exception.status_code, 400)

    def test_non_public_reference_is_rejected(self):
        payload = video_payload(images=[{"url": "/assets/local.png"}])
        with self.assertRaises(HTTPException) as ctx:
            run(codelba.build_codelba_video_request(payload, "sd-2-c5"))
        self.assertIn("公网", ctx.exception.detail)


class CodelbaResponseTests(unittest.TestCase):
    def test_task_id_prefers_documented_id_field(self):
        self.assertEqual(codelba.codelba_task_id({"id": "video-168", "task_id": "other"}), "video-168")
        self.assertEqual(codelba.codelba_task_id({"data": {"id": "video-nested"}}), "video-nested")
        self.assertEqual(codelba.codelba_task_state({"status": "queued"})[0], "pending")
        self.assertEqual(codelba.codelba_task_state({"status": "in_progress"})[0], "pending")
        self.assertEqual(codelba.codelba_task_state({"status": "completed"})[0], "success")
        self.assertEqual(codelba.codelba_task_state({"status": "failed"})[0], "failed")

    def test_error_code_and_message_are_preserved(self):
        self.assertEqual(
            codelba.codelba_error_text({"error": {"code": "generation_failed", "message": "参考素材包含真人脸部"}}),
            "generation_failed: 参考素材包含真人脸部",
        )


if __name__ == "__main__":
    unittest.main()
