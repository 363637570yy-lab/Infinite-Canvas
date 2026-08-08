import asyncio
import unittest

from fastapi import HTTPException

import main
import pidoi_protocol as pidoi


def run(coro):
    return asyncio.run(coro)


PIDOI_MODELS_RESPONSE = {
    "object": "list",
    "success": True,
    "data": [
        {"id": "omni-flash-720p", "supported_endpoint_types": ["openai-video"]},
        {"id": "sora-v3-933-pro", "supported_endpoint_types": ["openai-video"]},
        {"id": "minimax-h3", "supported_endpoint_types": ["openai-video"]},
        {"id": "grok-imagine-1.0-video", "supported_endpoint_types": ["openai-video"]},
        {"id": "tejiasd", "supported_endpoint_types": ["openai-video"]},
        {"id": "tejiasd2", "supported_endpoint_types": ["openai-video"]},
        {"id": "mystery-model"},
    ],
}


def video_payload(**kwargs):
    base = {
        "prompt": "保持参考人物身份一致，电影感镜头",
        "provider_id": "pidoi",
        "model": "omni-flash-720p",
        "duration": 10,
        "aspect_ratio": "16:9",
    }
    base.update(kwargs)
    return main.CanvasVideoRequest(**base)


async def fake_resolve(value, kind, index):
    return f"https://cdn.example/{kind}-{index}.media"


class PidoiRoutingTests(unittest.TestCase):
    def test_models_are_classified_from_endpoint_capabilities(self):
        grouped, ids = main.parse_upstream_models(PIDOI_MODELS_RESPONSE, main.PIDOI_PROTOCOL)
        self.assertEqual(ids, sorted({item["id"] for item in PIDOI_MODELS_RESPONSE["data"]}))
        for model in (
            "omni-flash-720p",
            "sora-v3-933-pro",
            "minimax-h3",
            "grok-imagine-1.0-video",
            "tejiasd",
            "tejiasd2",
        ):
            self.assertIn(model, grouped["video"])
        self.assertIn("mystery-model", grouped["chat"])

    def test_unknown_capability_is_not_guessed_as_video(self):
        self.assertEqual(pidoi.classify_pidoi_model_entry({}, "video-looking-model"), "chat")
        self.assertEqual(pidoi.classify_pidoi_model_entry(None, "omni-flash-720p"), "chat")

    def test_protocol_routes_to_its_own_contract(self):
        provider = {"id": "pidoi-omni", "protocol": main.PIDOI_PROTOCOL}
        self.assertIn(main.PIDOI_PROTOCOL, main.SUPPORTED_PROVIDER_PROTOCOLS)
        self.assertTrue(main.is_pidoi_provider(provider))
        self.assertTrue(main.is_pidoi_route(provider, "omni-flash-720p"))
        self.assertFalse(main.is_cangyuan_video_route(provider, "omni-flash-720p"))
        self.assertFalse(main.is_chre3_video_route(provider, "omni-flash-720p"))

    def test_video_urls_use_pidoi_videos_contract(self):
        provider = {"id": "pidoi-omni", "protocol": main.PIDOI_PROTOCOL}
        self.assertEqual(
            main.video_submit_url_candidates(provider, "https://pidoi.com/v1", "omni-flash-720p"),
            ["https://pidoi.com/v1/videos"],
        )
        self.assertEqual(
            main.video_task_url_candidates(
                provider, "https://pidoi.com", "task/42", "", "omni-flash-720p"
            ),
            ["https://pidoi.com/v1/videos/task%2F42"],
        )


class PidoiFamilyTests(unittest.TestCase):
    def test_family_registry_does_not_guess_undocumented_models(self):
        self.assertEqual(pidoi.pidoi_model_family("omni-flash-720p"), pidoi.PIDOI_FAMILY_OMNI_FLASH_720P)
        self.assertEqual(pidoi.pidoi_model_family("sora-v3-933-pro"), pidoi.PIDOI_FAMILY_SORA_V3_933_PRO)
        self.assertEqual(pidoi.pidoi_model_family("tejiasd"), pidoi.PIDOI_FAMILY_TEJIASD)
        for model in ("minimax-h3", "grok-imagine-1.0-video", "tejiasd2"):
            self.assertEqual(pidoi.pidoi_model_family(model), "")
        with self.assertRaises(HTTPException) as ctx:
            run(pidoi.build_pidoi_video_request(video_payload(model="tejiasd2"), "tejiasd2", fake_resolve))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("请求体家族", ctx.exception.detail)

    def test_omni_flash_request_uses_documented_nested_metadata_fields(self):
        payload = video_payload(
            model="omni-flash-720p",
            duration=5,
            aspect_ratio="9:16",
            images=[{"url": "https://example.com/character.jpg"}],
            videos=["https://example.com/motion.mp4"],
            audios=["https://example.com/rhythm.mp3"],
        )
        body = run(pidoi.build_pidoi_video_request(payload, "omni-flash-720p", fake_resolve))
        self.assertEqual(body["model"], "omni-flash-720p")
        self.assertEqual(body["duration"], 5)
        self.assertEqual(body["resolution"], "720P")
        self.assertEqual(body["metadata"], {"aspect_ratio": "9:16"})
        self.assertEqual(body["images"], ["https://cdn.example/图片-1.media"])
        self.assertEqual(body["videos"], ["https://cdn.example/视频-1.media"])
        self.assertEqual(body["audios"], ["https://cdn.example/音频-1.media"])
        for key in ("seconds", "size", "aspect_ratio", "n", "audio"):
            self.assertNotIn(key, body)

    def test_omni_text_to_video_omits_empty_arrays(self):
        body = run(pidoi.build_pidoi_video_request(video_payload(duration=10), "omni-flash-720p", fake_resolve))
        for key in ("images", "videos", "audios"):
            self.assertNotIn(key, body)

    def test_omni_duration_and_reference_limits_are_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            run(pidoi.build_pidoi_video_request(video_payload(duration=15), "omni-flash-720p", fake_resolve))
        self.assertEqual(ctx.exception.status_code, 400)
        too_many = video_payload(
            images=[{"url": f"https://example.com/{index}.jpg"} for index in range(5)]
        )
        with self.assertRaises(HTTPException) as ctx:
            run(pidoi.build_pidoi_video_request(too_many, "omni-flash-720p", fake_resolve))
        self.assertIn("最多 4", ctx.exception.detail)

    def test_sora_933_request_uses_top_level_reference_fields(self):
        payload = video_payload(
            model="sora-v3-933-pro",
            duration=15,
            aspect_ratio="16:9",
            images=[
                {"url": "https://example.com/main.jpg"},
                {"url": "https://example.com/ref.jpg"},
            ],
            videos=["https://example.com/camera.mp4"],
            audios=["https://example.com/audio.mp3"],
        )
        body = run(pidoi.build_pidoi_video_request(payload, "sora-v3-933-pro", fake_resolve))
        self.assertEqual(body["aspect_ratio"], "16:9")
        self.assertEqual(body["resolution"], "720p")
        self.assertEqual(body["seconds"], "15")
        self.assertEqual(body["image_url"], "https://cdn.example/图片-1.media")
        self.assertEqual(body["reference_image_urls"], ["https://cdn.example/图片-2.media"])
        self.assertEqual(body["reference_video"], "https://cdn.example/视频-1.media")
        self.assertEqual(body["audio_url"], "https://cdn.example/音频-1.media")
        for key in ("duration", "metadata", "images", "videos", "audios", "n"):
            self.assertNotIn(key, body)

    def test_sora_933_only_accepts_documented_duration_and_rejects_tail_frames(self):
        with self.assertRaises(HTTPException) as ctx:
            run(pidoi.build_pidoi_video_request(video_payload(duration=10), "sora-v3-933-pro", fake_resolve))
        self.assertIn("5 秒或 15 秒", ctx.exception.detail)
        five_second_body = run(
            pidoi.build_pidoi_video_request(
                video_payload(model="sora-v3-933-pro", duration=5),
                "sora-v3-933-pro",
                fake_resolve,
            )
        )
        self.assertEqual(five_second_body["seconds"], "5")
        payload = video_payload(
            model="sora-v3-933-pro",
            duration=15,
            images=[
                {"url": "https://example.com/first.jpg", "role": "first_frame"},
                {"url": "https://example.com/last.jpg", "role": "last_frame"},
            ],
        )
        with self.assertRaises(HTTPException) as ctx:
            run(pidoi.build_pidoi_video_request(payload, "sora-v3-933-pro", fake_resolve))
        self.assertIn("首帧/尾帧", ctx.exception.detail)

    def test_aspect_ratio_is_checked_per_documented_family(self):
        sora_body = run(
            pidoi.build_pidoi_video_request(
                video_payload(model="sora-v3-933-pro", duration=15, aspect_ratio="21:9"),
                "sora-v3-933-pro",
                fake_resolve,
            )
        )
        self.assertEqual(sora_body["aspect_ratio"], "21:9")
        for model in ("omni-flash-720p", "tejiasd"):
            payload = video_payload(model=model, aspect_ratio="21:9")
            with self.assertRaises(HTTPException) as ctx:
                run(pidoi.build_pidoi_video_request(payload, model, fake_resolve))
            self.assertIn("比例无效", ctx.exception.detail)

    def test_tejiasd_request_uses_common_arrays_and_n_one(self):
        payload = video_payload(
            model="tejiasd",
            duration=15,
            aspect_ratio="4:3",
            images=[{"url": "https://example.com/character.jpg"}],
        )
        body = run(pidoi.build_pidoi_video_request(payload, "tejiasd", fake_resolve))
        self.assertEqual(body["model"], "tejiasd")
        self.assertEqual(body["duration"], 15)
        self.assertEqual(body["resolution"], "720P")
        self.assertEqual(body["n"], 1)
        self.assertEqual(body["metadata"], {"aspect_ratio": "4:3"})
        self.assertEqual(body["images"], ["https://cdn.example/图片-1.media"])
        for key in ("seconds", "size", "image_url", "reference_image_urls", "audio"):
            self.assertNotIn(key, body)

    def test_tejiasd_maps_documented_size_and_seed_without_mixing_resolution(self):
        payload = video_payload(
            model="tejiasd",
            size="1280x720",
            resolution="",
            seed=42,
        )
        body = run(pidoi.build_pidoi_video_request(payload, "tejiasd", fake_resolve))
        self.assertEqual(body["size"], "1280x720")
        self.assertEqual(body["seed"], 42)
        self.assertNotIn("resolution", body)
        self.assertNotIn("metadata", body)

    def test_tejiasd_rejects_invalid_size_and_negative_seed(self):
        with self.assertRaises(HTTPException) as ctx:
            run(
                pidoi.build_pidoi_video_request(
                    video_payload(model="tejiasd", size="720P"),
                    "tejiasd",
                    fake_resolve,
                )
            )
        self.assertIn("像素尺寸", ctx.exception.detail)
        with self.assertRaises(HTTPException) as ctx:
            run(
                pidoi.build_pidoi_video_request(
                    video_payload(model="tejiasd", seed=-1),
                    "tejiasd",
                    fake_resolve,
                )
            )
        self.assertIn("非负整数", ctx.exception.detail)

    def test_tejiasd_prompt_limit_is_rejected_before_submission(self):
        payload = video_payload(model="tejiasd", prompt="x" * 2501)
        with self.assertRaises(HTTPException) as ctx:
            run(pidoi.build_pidoi_video_request(payload, "tejiasd", fake_resolve))
        self.assertIn("2500", ctx.exception.detail)

    def test_explicit_audio_switch_is_rejected_instead_of_silently_ignored(self):
        payload = video_payload(generate_audio=True)
        with self.assertRaises(HTTPException) as ctx:
            run(pidoi.build_pidoi_video_request(payload, "omni-flash-720p", fake_resolve))
        self.assertIn("generate_audio", ctx.exception.detail)


class PidoiResponseTests(unittest.TestCase):
    def test_task_id_and_status_are_normalized(self):
        self.assertEqual(
            pidoi.pidoi_task_id({"id": "id-1", "task_id": "task-1"}),
            "task-1",
        )
        self.assertEqual(
            pidoi.pidoi_task_id({"data": {"id": "nested-id"}}),
            "nested-id",
        )
        self.assertEqual(pidoi.pidoi_task_state({"status": "SUCCESS"})[0], "success")
        self.assertEqual(pidoi.pidoi_task_state({"data": {"status": "processing"}})[0], "pending")
        self.assertEqual(
            pidoi.pidoi_task_state(
                {"status": "processing", "video_url": "https://cdn.example/premature.mp4"}
            )[0],
            "pending",
        )
        self.assertEqual(pidoi.pidoi_task_state({"status": "failed"})[0], "failed")

    def test_video_url_priority_matches_documentation(self):
        raw = {
            "result_url": "https://cdn.example/result.mp4",
            "video_url": "https://cdn.example/video.mp4",
            "url": "https://cdn.example/url.mp4",
            "metadata": {"url": "https://cdn.example/metadata.mp4"},
            "data": {"metadata": {"url": "https://cdn.example/nested.mp4"}},
            "outputs": [{"download_url": "https://cdn.example/output.mp4"}],
        }
        self.assertEqual(
            pidoi.pidoi_video_result_urls(raw, pidoi.PIDOI_FAMILY_OMNI_FLASH_720P),
            [
                "https://cdn.example/result.mp4",
                "https://cdn.example/nested.mp4",
                "https://cdn.example/video.mp4",
                "https://cdn.example/url.mp4",
                "https://cdn.example/metadata.mp4",
                "https://cdn.example/output.mp4",
            ],
        )
        self.assertEqual(
            pidoi.pidoi_video_result_urls(raw, pidoi.PIDOI_FAMILY_TEJIASD),
            [
                "https://cdn.example/metadata.mp4",
                "https://cdn.example/video.mp4",
                "https://cdn.example/url.mp4",
                "https://cdn.example/result.mp4",
                "https://cdn.example/nested.mp4",
            ],
        )
        self.assertEqual(
            pidoi.pidoi_video_result_urls(raw, pidoi.PIDOI_FAMILY_SORA_V3_933_PRO),
            [
                "https://cdn.example/video.mp4",
                "https://cdn.example/url.mp4",
                "https://cdn.example/result.mp4",
            ],
        )

    def test_error_priority_reads_documented_envelopes(self):
        self.assertEqual(
            pidoi.pidoi_error_text({"error": {"message": "bad key"}, "message": "fallback"}),
            "bad key",
        )
        self.assertEqual(pidoi.pidoi_error_text({"fail_reason": "quota"}), "quota")
        self.assertEqual(
            pidoi.pidoi_error_text({"error": {"code": "submit_failed"}, "fail_reason": "quota"}),
            "quota",
        )


if __name__ == "__main__":
    unittest.main()
