import asyncio
import unittest

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


if __name__ == "__main__":
    unittest.main()
