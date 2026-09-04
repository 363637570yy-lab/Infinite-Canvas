import asyncio
import unittest
from unittest import mock

from fastapi import HTTPException

import main


def run(coro):
    return asyncio.run(coro)


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

    def test_sd2_c8_body_matches_async_docs(self):
        payload = main.CanvasVideoRequest(
            prompt="@Image1 是张三",
            duration=15,
            aspect_ratio="16:9",
            images=[{"url": "https://example.com/image1.png"}],
        )
        body = run(main.build_sd2_video_request(payload, "sd2-c8"))
        self.assertEqual(body["model"], "sd2-c8")
        self.assertEqual(body["duration"], 15)
        self.assertEqual(body["size"], "16:9")
        self.assertEqual(body["image_refs"], ["https://example.com/image1.png"])
        self.assertNotIn("generate_audio", body)
        self.assertNotIn("compliance_enabled", body)

    def test_uuid_and_task_prefix_ids_are_both_accepted(self):
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        self.assertEqual(main.chre3_task_id({"id": uuid, "task_id": uuid, "status": "processing"}), uuid)
        self.assertEqual(main.chre3_task_id({"id": uuid, "status": "processing"}), uuid)
        self.assertEqual(main.chre3_task_id({"id": "task_" + uuid}), "task_" + uuid)

    def test_processing_is_not_a_terminal_status(self):
        self.assertNotIn("PROCESSING", main.VIDEO_TASK_SUCCESS_STATUSES)
        self.assertNotIn("PROCESSING", main.VIDEO_TASK_FAILURE_STATUSES)

    def test_submit_processing_then_polls_get_until_completed(self):
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        provider = {"id": "custom-api-8", "protocol": main.CHRE3_VIDEO_PROTOCOL, "base_url": "https://llm.chre3.com"}
        payload = main.CanvasVideoRequest(prompt="@Image1 面向镜头", duration=15, aspect_ratio="16:9")
        post = mock.Mock(status_code=200, text="{}")
        post.json.return_value = {
            "id": uuid,
            "task_id": uuid,
            "object": "video",
            "model": "sd2-c8",
            "status": "processing",
            "progress": 0,
        }
        get = mock.Mock(status_code=200, text="{}")
        get.json.return_value = {
            "id": uuid,
            "status": "completed",
            "progress": 100,
            "video_url": "https://llm.chre3.com/outputs/xxx.mp4",
            "url": "https://llm.chre3.com/outputs/xxx.mp4",
        }
        get.raise_for_status = mock.Mock()
        client = mock.AsyncMock()
        client.post.return_value = post
        client.get.return_value = get
        with mock.patch.object(main, "api_headers", return_value={"Authorization": "Bearer test"}), mock.patch.object(
            main.asyncio, "sleep", new=mock.AsyncMock()
        ), mock.patch.object(
            main, "save_remote_video_to_output", new=mock.AsyncMock(return_value="/output/xxx.mp4")
        ):
            result = run(main.generate_sd2_video(client, payload, provider, "https://llm.chre3.com", "sd2-c8"))
        self.assertEqual(client.post.await_count, 1)
        self.assertEqual(client.post.await_args.args[0], "https://llm.chre3.com/v1/videos")
        self.assertGreaterEqual(client.get.await_count, 1)
        self.assertEqual(client.get.await_args.args[0], f"https://llm.chre3.com/v1/videos/{uuid}")
        self.assertEqual(result["task_id"], uuid)
        self.assertEqual(result["videos"], ["/output/xxx.mp4"])

    def test_cloudflare_submit_timeout_explains_lost_task_id(self):
        provider = {"id": "custom-api-8", "protocol": main.CHRE3_VIDEO_PROTOCOL, "base_url": "https://llm.chre3.com"}
        payload = main.CanvasVideoRequest(prompt="海边", duration=8)
        response = mock.Mock(
            status_code=524,
            text="The origin web server did not return a complete response within the 120-second Proxy Read Timeout window.",
        )
        response.json.side_effect = ValueError("not json")
        client = mock.AsyncMock()
        client.post.return_value = response
        with mock.patch.object(main, "api_headers", return_value={"Authorization": "Bearer test"}):
            with self.assertRaises(HTTPException) as ctx:
                run(main.generate_sd2_video(client, payload, provider, "https://llm.chre3.com", "sd2-c8"))
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("异步", ctx.exception.detail)
        self.assertIn("没有拿到任务号", ctx.exception.detail)
        client.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
