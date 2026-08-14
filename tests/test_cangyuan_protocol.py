import asyncio
import unittest
from unittest import mock

import main


# 取自 ai.cangyuansuanli.cn 的 GET /v1/models 真实响应：只有 id 和
# supported_endpoint_types，没有 type / category / capabilities 字段。
CANGYUAN_MODELS_RESPONSE = {
    "data": [
        {"id": "gpt-image-2-4k", "object": "model", "supported_endpoint_types": ["openai"]},
        {"id": "nano-banana-pro-2k", "object": "model", "supported_endpoint_types": ["openai"]},
        {"id": "seedance-2.0", "object": "model", "supported_endpoint_types": ["openai-video"]},
        {"id": "seedance-2.0-mini-8s", "object": "model", "supported_endpoint_types": ["openai-video"]},
        {"id": "omni-v2v", "object": "model", "supported_endpoint_types": ["openai-video"]},
        {"id": "veo-clean", "object": "model", "supported_endpoint_types": ["openai-video"]},
    ]
}


class CangyuanProtocolTests(unittest.TestCase):
    def test_protocol_is_registered_and_routes_to_the_videos_contract(self):
        provider = {"id": "cangyuan", "protocol": main.CANGYUAN_VIDEO_PROTOCOL}

        self.assertIn(main.CANGYUAN_VIDEO_PROTOCOL, main.SUPPORTED_PROVIDER_PROTOCOLS)
        self.assertTrue(main.is_cangyuan_video_provider(provider))
        self.assertTrue(main.is_cangyuan_video_route(provider, "seedance-2.0"))
        self.assertFalse(main.is_chre3_video_route(provider, "seedance-2.0"))
        self.assertEqual(
            main.video_submit_url_candidates(provider, "https://ai.cangyuansuanli.cn", "seedance-2.0"),
            ["https://ai.cangyuansuanli.cn/v1/videos"],
        )
        self.assertEqual(
            main.video_task_url_candidates(provider, "https://ai.cangyuansuanli.cn", "video_42", "", "seedance-2.0"),
            ["https://ai.cangyuansuanli.cn/v1/videos/video_42"],
        )

    def test_base_url_with_trailing_v1_is_not_doubled(self):
        provider = {"id": "cangyuan", "protocol": main.CANGYUAN_VIDEO_PROTOCOL}
        self.assertEqual(
            main.video_submit_url_candidates(provider, "https://ai.cangyuansuanli.cn/v1", "seedance-2.0"),
            ["https://ai.cangyuansuanli.cn/v1/videos"],
        )

    def test_models_are_classified_from_supported_endpoint_types(self):
        grouped, ids = main.parse_upstream_models(CANGYUAN_MODELS_RESPONSE, main.CANGYUAN_VIDEO_PROTOCOL)

        self.assertEqual(len(ids), 6)
        # 名称兜底认不出这些视频模型（不含 doubao-seedance / video 等关键词），
        # 必须靠 supported_endpoint_types 才能归到 video 组。
        self.assertIn("seedance-2.0", grouped["video"])
        self.assertIn("omni-v2v", grouped["video"])
        self.assertIn("veo-clean", grouped["video"])
        self.assertIn("gpt-image-2-4k", grouped["image"])
        self.assertIn("nano-banana-pro-2k", grouped["image"])
        self.assertEqual(grouped["chat"], [])

    def test_seedance_body_uses_cangyuan_field_names_not_chre3_ones(self):
        payload = main.CanvasVideoRequest(
            prompt="雨夜霓虹街道，镜头缓慢推进",
            duration=8,
            aspect_ratio="9:16",
            resolution="480p",
            generate_audio=True,
        )
        body = asyncio.run(main.build_cangyuan_video_request(payload, "seedance-2.0"))

        self.assertEqual(body["model"], "seedance-2.0")
        self.assertEqual(body["aspect_ratio"], "9:16")
        self.assertEqual(body["duration"], 8)
        self.assertEqual(body["resolution"], "480p")
        self.assertEqual(body["generate_audio"], True)
        # chre3 的字段名一个都不能出现，否则上游会静默忽略。
        for chre3_key in ("size", "image_refs", "video_refs", "audio_refs", "compliance_enabled", "compliance_mode", "audio"):
            self.assertNotIn(chre3_key, body)

    def test_duration_allows_four_seconds_and_clamps_out_of_range(self):
        for requested, expected in ((4, 4), (15, 15), (3, 4), (30, 15)):
            payload = main.CanvasVideoRequest(prompt="测试", duration=requested)
            body = asyncio.run(main.build_cangyuan_video_request(payload, "seedance-2.0"))
            self.assertEqual(body["duration"], expected)

    def test_unsupported_aspect_ratio_and_resolution_fall_back_to_defaults(self):
        payload = main.CanvasVideoRequest(prompt="测试", aspect_ratio="7:5", resolution="1080p")
        body = asyncio.run(main.build_cangyuan_video_request(payload, "seedance-2.0"))
        self.assertEqual(body["aspect_ratio"], "16:9")
        self.assertEqual(body["resolution"], "720p")

    def test_base64_reference_images_skip_the_public_url_conversion(self):
        data_uri = "data:image/png;base64,iVBORw0KGgo="
        payload = main.CanvasVideoRequest(
            prompt="让画面动起来",
            images=[main.AIReference(url=data_uri)],
        )
        body = asyncio.run(main.build_cangyuan_video_request(payload, "seedance-2.0"))
        self.assertEqual(body["reference_image_urls"], [data_uri])

    def test_paired_frame_roles_switch_to_first_last_frame_mode(self):
        payload = main.CanvasVideoRequest(
            prompt="平滑电影感过渡",
            images=[
                main.AIReference(url="data:image/png;base64,AAAA", role="first_frame"),
                main.AIReference(url="data:image/png;base64,BBBB", role="last_frame"),
            ],
        )
        body = asyncio.run(main.build_cangyuan_video_request(payload, "seedance-2.0"))

        self.assertEqual(body["first_image_url"], "data:image/png;base64,AAAA")
        self.assertEqual(body["last_image_url"], "data:image/png;base64,BBBB")
        # 首尾帧与多模态互斥，不能同时带参考图数组。
        self.assertNotIn("reference_image_urls", body)

    def test_unpaired_frame_role_stays_in_multimodal_mode(self):
        payload = main.CanvasVideoRequest(
            prompt="只有首帧",
            images=[main.AIReference(url="data:image/png;base64,AAAA", role="first_frame")],
        )
        body = asyncio.run(main.build_cangyuan_video_request(payload, "seedance-2.0"))

        self.assertNotIn("first_image_url", body)
        self.assertEqual(body["reference_image_urls"], ["data:image/png;base64,AAAA"])

    def test_reference_video_without_any_image_is_rejected(self):
        payload = main.CanvasVideoRequest(
            prompt="只有参考视频",
            videos=["https://cdn.example.com/ref.mp4"],
        )
        with self.assertRaises(main.HTTPException) as ctx:
            asyncio.run(main.build_cangyuan_video_request(payload, "seedance-2.0"))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_generate_audio_false_is_sent_explicitly(self):
        payload = main.CanvasVideoRequest(prompt="无声", generate_audio=False)
        body = asyncio.run(main.build_cangyuan_video_request(payload, "seedance-2.0"))
        self.assertEqual(body["generate_audio"], False)
        self.assertNotIn("audio", body)

    def test_over_limit_reference_images_fail_loudly_instead_of_being_dropped(self):
        payload = main.CanvasVideoRequest(
            prompt="多模态",
            images=[main.AIReference(url=f"data:image/png;base64,IMG{i}") for i in range(6)],
        )
        with self.assertRaises(main.HTTPException) as ctx:
            asyncio.run(main.build_cangyuan_video_request(payload, "seedance-2.0"))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("最多接受 4", ctx.exception.detail)

    def test_sd5_allows_five_reference_images_and_keeps_generate_audio(self):
        payload = main.CanvasVideoRequest(
            prompt="全能参考",
            generate_audio=False,
            images=[main.AIReference(url=f"data:image/png;base64,IMG{i}") for i in range(5)],
        )
        body = asyncio.run(main.build_cangyuan_video_request(payload, "sd5-seedance-2.0-fast"))
        self.assertEqual(len(body["reference_image_urls"]), 5)
        self.assertEqual(body["generate_audio"], False)
        self.assertNotIn("audio", body)

    def test_sd5_rejects_shared_video_audio_quota(self):
        payload = main.CanvasVideoRequest(
            prompt="超额源素材",
            images=[main.AIReference(url="data:image/png;base64,IMG")],
            videos=["https://cdn.example.com/a.mp4", "https://cdn.example.com/b.mp4"],
            audios=["https://cdn.example.com/a.mp3", "https://cdn.example.com/b.mp3"],
        )
        with self.assertRaises(main.HTTPException) as ctx:
            asyncio.run(main.build_cangyuan_video_request(payload, "sd5-seedance-2.0"))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("合计最多 3", ctx.exception.detail)

    def test_sd5_sends_seed_only_when_explicit(self):
        with_seed = asyncio.run(main.build_cangyuan_video_request(
            main.CanvasVideoRequest(prompt="带种子", seed=0),
            "sd5-seedance-2.0",
        ))
        without_seed = asyncio.run(main.build_cangyuan_video_request(
            main.CanvasVideoRequest(prompt="无种子"),
            "sd5-seedance-2.0",
        ))
        self.assertEqual(with_seed["seed"], 0)
        self.assertNotIn("seed", without_seed)

    def test_kling_family_sends_documented_fields_only(self):
        payload = main.CanvasVideoRequest(
            prompt="可灵",
            duration=3,
            aspect_ratio="1:1",
            resolution="1080p",
            generate_audio=False,
            images=[main.AIReference(url="data:image/png;base64,IMG")],
        )
        body = asyncio.run(main.build_cangyuan_video_request(payload, "kling-3.0"))
        self.assertEqual(main.cangyuan_video_family("kling-3.0"), main.CANGYUAN_FAMILY_KLING)
        self.assertEqual(body["duration"], 3)
        self.assertEqual(body["aspect_ratio"], "16:9")
        self.assertEqual(body["resolution"], "1080p")
        self.assertEqual(body["generate_audio"], False)
        self.assertEqual(body["reference_image_urls"], ["data:image/png;base64,IMG"])
        for extra in ("audio", "reference_videos", "reference_audios", "image_url"):
            self.assertNotIn(extra, body)

    def test_kling_rejects_video_or_audio_refs(self):
        payload = main.CanvasVideoRequest(
            prompt="可灵",
            videos=["https://cdn.example.com/ref.mp4"],
        )
        with self.assertRaises(main.HTTPException) as ctx:
            asyncio.run(main.build_cangyuan_video_request(payload, "kling-3.0"))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_kling_omni_allows_three_images(self):
        payload = main.CanvasVideoRequest(
            prompt="可灵 omni",
            images=[main.AIReference(url=f"data:image/png;base64,IMG{i}") for i in range(3)],
        )
        body = asyncio.run(main.build_cangyuan_video_request(payload, "kling-3.0-omni"))
        self.assertEqual(len(body["reference_image_urls"]), 3)

    def test_unimplemented_families_are_rejected_instead_of_seedance_fallback(self):
        for model in ("veo-3.1", "grok-video", "happyhouse-1.0", "minimax-h3-2k", "sora-2", "gemini-omni-flash"):
            self.assertEqual(main.cangyuan_video_family(model), main.CANGYUAN_FAMILY_UNSUPPORTED)
            with self.assertRaises(main.HTTPException) as ctx:
                asyncio.run(main.build_cangyuan_video_request(main.CanvasVideoRequest(prompt="拦截"), model))
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("尚未实现", ctx.exception.detail)

    def test_cangyuan_poll_timeout_is_two_hours_and_does_not_change_global(self):
        self.assertEqual(main.CANGYUAN_VIDEO_POLL_TIMEOUT, 7200)
        self.assertEqual(main.VIDEO_POLL_TIMEOUT, 1800)
        cangyuan = {"id": "cangyuan", "protocol": main.CANGYUAN_VIDEO_PROTOCOL}
        other = {"id": "chre3", "protocol": main.CHRE3_VIDEO_PROTOCOL}
        self.assertEqual(main.video_poll_timeout_for(cangyuan), 7200)
        self.assertEqual(main.video_poll_timeout_for(other), 1800)

    def test_chre3_error_wording_is_unchanged_by_the_shared_label_helper(self):
        chre3 = {"id": "chre3", "protocol": main.CHRE3_VIDEO_PROTOCOL}
        cangyuan = {"id": "cangyuan", "protocol": main.CANGYUAN_VIDEO_PROTOCOL}
        plain = {"id": "other", "protocol": "openai"}

        self.assertEqual(main.videos_contract_label(chre3, "sd2-c7"), "chre3 视频")
        self.assertEqual(main.videos_contract_label(cangyuan, "seedance-2.0"), "苍元视频")
        self.assertEqual(main.videos_contract_label(plain, "veo3-fast"), "")

    def test_canvas_route_returns_pending_response_for_cangyuan(self):
        provider = {
            "id": "custom-api-9",
            "name": "苍元算力",
            "protocol": main.CANGYUAN_VIDEO_PROTOCOL,
            "base_url": "https://ai.cangyuansuanli.cn",
            "video_models": ["seedance-2.0"],
        }
        created_coroutines = []

        def capture_task(coro):
            created_coroutines.append(coro)
            coro.close()
            return object()

        payload = main.CanvasVideoRequest(
            prompt="测试后台任务",
            provider_id="custom-api-9",
            model="seedance-2.0",
        )
        with mock.patch.object(main, "get_api_provider", return_value=provider), mock.patch.object(
            main, "provider_env_key_value", return_value="test-token"
        ), mock.patch.object(main.asyncio, "create_task", side_effect=capture_task):
            result = asyncio.run(main.canvas_video(payload))

        task_id = result["task_id"]
        try:
            self.assertTrue(result["video_pending"])
            self.assertFalse(result["grok2api_pending"])
            self.assertEqual(result["status"], "queued")
            self.assertTrue(task_id.startswith("canvas_cangyuan_"))
            self.assertEqual(main.CANVAS_TASKS[task_id]["type"], "cangyuan-video")
            self.assertEqual(len(created_coroutines), 1)
        finally:
            main.CANVAS_TASKS.pop(task_id, None)
            main.CANVAS_VIDEO_TASK_HANDLES.pop(task_id, None)

    def test_public_reference_url_prefers_configured_public_media_base(self):
        with mock.patch.object(main, "output_file_from_url", return_value="/tmp/ref.png"), mock.patch.object(
            main, "upstream_safe_image_ref", side_effect=lambda value: value
        ), mock.patch.object(main, "local_asset_public_url", return_value="https://hb.qnzn.top:8443/assets/input/ref.png"), mock.patch.object(
            main, "upload_local_video_to_cloud", new=mock.AsyncMock()
        ) as upload:
            url = asyncio.run(main.openai_video_proxy_public_reference_url({"url": "/assets/input/ref.png"}))

        self.assertEqual(url, "https://hb.qnzn.top:8443/assets/input/ref.png")
        upload.assert_not_awaited()

    def test_wait_for_video_task_retries_transient_poll_errors(self):
        provider = {"id": "cangyuan", "protocol": main.CANGYUAN_VIDEO_PROTOCOL, "base_url": "https://ai.cangyuansuanli.cn"}
        client = mock.Mock()
        success = mock.Mock()
        success.raise_for_status.return_value = None
        success.json.return_value = {"status": "completed", "data": {"video_url": "https://cdn.example.com/out.mp4"}}
        client.get = mock.AsyncMock(side_effect=[main.httpx.ConnectError("reset"), success])

        with mock.patch.object(main, "video_task_url_candidates", return_value=["https://ai.cangyuansuanli.cn/v1/videos/task_1"]), mock.patch.object(
            main, "video_output_urls", return_value=["https://cdn.example.com/out.mp4"]
        ), mock.patch.object(main, "provider_env_key_value", return_value="test-token"), mock.patch.object(
            main, "IMAGE_POLL_INTERVAL", 0.01
        ), mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            raw = asyncio.run(main.wait_for_video_task(client, provider, "task_1", "", "seedance-2.0"))

        self.assertEqual(raw["status"], "completed")
        self.assertEqual(client.get.await_count, 2)


if __name__ == "__main__":
    unittest.main()
