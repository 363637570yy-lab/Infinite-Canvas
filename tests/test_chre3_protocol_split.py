import asyncio
import unittest

from fastapi import HTTPException

import main


class Chre3ProtocolSplitTests(unittest.TestCase):
    def test_protocols_share_video_contract_but_only_real_protocol_forces_compliance(self):
        normal = {"id": "normal", "protocol": main.CHRE3_VIDEO_PROTOCOL}
        real = {"id": "real", "protocol": main.CHRE3_VIDEO_REAL_PROTOCOL}
        legacy = {"id": "legacy", "protocol": "openai"}

        self.assertIn(main.CHRE3_VIDEO_REAL_PROTOCOL, main.SUPPORTED_PROVIDER_PROTOCOLS)
        self.assertTrue(main.is_chre3_video_provider(normal))
        self.assertTrue(main.is_chre3_video_provider(real))
        self.assertFalse(main.is_chre3_real_video_provider(normal))
        self.assertTrue(main.is_chre3_real_video_provider(real))
        self.assertTrue(main.is_chre3_video_route(normal, "kling-v3-720p"))
        self.assertTrue(main.is_chre3_video_route(real, "sora-2-pro"))
        self.assertTrue(main.is_chre3_video_route(legacy, "sd2-c7"))

    def test_sd2_request_body_keeps_normal_and_real_modes_distinct(self):
        normal_payload = main.CanvasVideoRequest(prompt="@Image1 保持画面中的对象运动")
        normal_body = asyncio.run(main.build_sd2_video_request(normal_payload, "kling-v3-720p"))
        self.assertNotIn("compliance_enabled", normal_body)
        self.assertNotIn("compliance_mode", normal_body)

        real_payload = main.CanvasVideoRequest(prompt="@Image1 让真人自然走动")
        real_body = asyncio.run(
            main.build_sd2_video_request(real_payload, "kling-v3-720p", force_compliance=True)
        )
        self.assertEqual(real_body["compliance_enabled"], True)
        self.assertEqual(real_body["compliance_mode"], "colored-pencil")

    def test_explicit_opt_in_remains_available_for_api_callers(self):
        payload = main.CanvasVideoRequest(
            prompt="@Image1 保持动作",
            compliance_enabled=True,
            compliance_mode="watercolor",
        )
        body = asyncio.run(main.build_sd2_video_request(payload, "sd2-c7"))
        self.assertEqual(body["compliance_enabled"], True)
        self.assertEqual(body["compliance_mode"], "watercolor")

    def test_chre3_protocol_keeps_unknown_model_names_in_video_group(self):
        """上游随时新增模型名；chre3 协议下不得按名称猜类型，否则新模型会掉出视频列表。"""
        raw = {"data": [{"id": "sd2-fast"}, {"id": "sd2-c8"}, {"id": "brand-new-name-2027"}]}
        grouped, ids = main.parse_upstream_models(raw, main.CHRE3_VIDEO_PROTOCOL)
        self.assertEqual(sorted(grouped["video"]), sorted(ids))
        self.assertEqual(grouped["image"], [])
        self.assertEqual(grouped["chat"], [])

    def test_chre3_metadata_still_separates_non_video_models(self):
        """上游明确表态时以元数据为准，只有没表态才归视频。"""
        raw = {"data": [
            {"id": "with-video-type", "type": "video"},
            {"id": "with-image-type", "type": "image"},
            {"id": "with-chat-type", "type": "chat"},
            {"id": "without-metadata"},
        ]}
        grouped, _ = main.parse_upstream_models(raw, main.CHRE3_VIDEO_REAL_PROTOCOL)
        self.assertIn("with-video-type", grouped["video"])
        self.assertIn("without-metadata", grouped["video"])
        self.assertIn("with-image-type", grouped["image"])
        self.assertIn("with-chat-type", grouped["chat"])

    def test_openai_protocol_still_uses_legacy_name_fallback(self):
        """范围边界：名称兜底只在普通 openai 协议下保留，避免既有配置的分类回归。"""
        grouped, _ = main.parse_upstream_models({"data": [{"id": "sd2-c7"}, {"id": "sd2-fast"}]}, "openai")
        self.assertIn("sd2-c7", grouped["video"])
        self.assertIn("sd2-fast", grouped["chat"])

    def test_chre3_default_video_model_comes_from_provider_not_hardcoded(self):
        self.assertEqual(main.provider_first_video_model({"video_models": ["sd2-fast", "sd2-c8"]}), "sd2-fast")
        self.assertEqual(main.provider_first_video_model({"video_models": [" ", "sd2-c8"]}), "sd2-c8")
        self.assertEqual(main.provider_first_video_model({"video_models": []}), "")
        self.assertEqual(main.provider_first_video_model({}), "")

    def test_empty_model_name_fails_loudly_instead_of_falling_back(self):
        payload = main.CanvasVideoRequest(prompt="@Image1 保持动作")
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(main.build_sd2_video_request(payload, ""))
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
