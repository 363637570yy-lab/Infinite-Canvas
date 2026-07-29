import asyncio
import unittest

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
        self.assertEqual(body["audio"], True)
        # chre3 的字段名一个都不能出现，否则上游会静默忽略。
        for chre3_key in ("size", "image_refs", "video_refs", "audio_refs", "compliance_enabled", "compliance_mode"):
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

    def test_reference_counts_are_capped_at_the_documented_limits(self):
        payload = main.CanvasVideoRequest(
            prompt="多模态",
            images=[main.AIReference(url=f"data:image/png;base64,IMG{i}") for i in range(6)],
        )
        body = asyncio.run(main.build_cangyuan_video_request(payload, "seedance-2.0"))
        self.assertEqual(len(body["reference_image_urls"]), main.CANGYUAN_VIDEO_MAX_IMAGE_REFS)

    def test_chre3_error_wording_is_unchanged_by_the_shared_label_helper(self):
        chre3 = {"id": "chre3", "protocol": main.CHRE3_VIDEO_PROTOCOL}
        cangyuan = {"id": "cangyuan", "protocol": main.CANGYUAN_VIDEO_PROTOCOL}
        plain = {"id": "other", "protocol": "openai"}

        self.assertEqual(main.videos_contract_label(chre3, "sd2-c7"), "chre3 视频")
        self.assertEqual(main.videos_contract_label(cangyuan, "seedance-2.0"), "苍元视频")
        self.assertEqual(main.videos_contract_label(plain, "veo3-fast"), "")


if __name__ == "__main__":
    unittest.main()
