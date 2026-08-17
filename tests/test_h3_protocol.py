import asyncio
import unittest
from unittest import mock

import h3_protocol as h3
import main


def run(coro):
    return asyncio.run(coro)


H3_MODELS_RESPONSE = {
    "object": "list",
    "data": [
        {
            "id": "minimax-h3",
            "object": "model",
            "owned_by": "video-api",
            "supported_endpoint_types": ["openai-video"],
            "description": "async /v1/videos",
        }
    ],
}


def video_payload(**kwargs):
    base = {
        "prompt": "电影感镜头缓慢推进",
        "provider_id": "h3-local",
        "model": "minimax-h3",
        "duration": 5,
        "aspect_ratio": "16:9",
        "resolution": "480p",
    }
    base.update(kwargs)
    return main.CanvasVideoRequest(**base)


class H3RoutingTests(unittest.TestCase):
    def test_protocol_and_urls_are_registered(self):
        provider = {"id": "h3-local", "protocol": main.H3_PROTOCOL}

        self.assertIn(main.H3_PROTOCOL, main.SUPPORTED_PROVIDER_PROTOCOLS)
        self.assertTrue(main.is_h3_provider(provider))
        self.assertTrue(main.is_h3_route(provider, "minimax-h3"))
        self.assertFalse(main.is_megabyai_route(provider, "minimax-h3"))
        self.assertEqual(
            main.video_submit_url_candidates(provider, "http://127.0.0.1:8088", "minimax-h3"),
            ["http://127.0.0.1:8088/v1/videos"],
        )
        self.assertEqual(
            main.video_task_url_candidates(provider, "http://127.0.0.1:8088/v1", "video_abc", "", "minimax-h3"),
            ["http://127.0.0.1:8088/v1/videos/video_abc"],
        )
        self.assertEqual(
            h3.h3_video_cancel_url("http://127.0.0.1:8088", "video_abc"),
            "http://127.0.0.1:8088/v1/videos/video_abc",
        )
        self.assertEqual(main.canvas_video_task_type(provider, "minimax-h3"), "h3-video")
        self.assertIn("h3-video", main.CANVAS_VIDEO_TASK_TYPES)

    def test_submitted_h3_task_is_queryable(self):
        task_id = "canvas_h3_queryable_fixture"
        main.CANVAS_TASKS[task_id] = {
            "id": task_id,
            "type": "h3-video",
            "status": "queued",
        }
        try:
            task = run(main.get_canvas_video_task(task_id))
            self.assertEqual(task["id"], task_id)
            self.assertEqual(task["type"], "h3-video")
            self.assertEqual(task["status"], "queued")
        finally:
            main.CANVAS_TASKS.pop(task_id, None)

    def test_models_use_upstream_capabilities_not_names(self):
        grouped, ids = main.parse_upstream_models(H3_MODELS_RESPONSE, main.H3_PROTOCOL)
        self.assertEqual(ids, ["minimax-h3"])
        self.assertIn("minimax-h3", grouped["video"])
        self.assertEqual(h3.classify_h3_model_entry({}, "minimax-h3"), "chat")


class H3RequestTests(unittest.TestCase):
    def test_canvas_fields_only(self):
        body, media = h3.build_h3_video_request(video_payload(), "minimax-h3")
        self.assertEqual(
            body,
            {
                "model": "minimax-h3",
                "prompt": "电影感镜头缓慢推进",
                "seconds": 5,
                "size": "480p",
                "aspect_ratio": "16:9",
            },
        )
        self.assertEqual(media["images"], [])
        self.assertEqual(media["videos"], [])
        self.assertEqual(media["audios"], [])
        for wrong in ("steps", "ycnodes", "duration", "resolution"):
            self.assertNotIn(wrong, body)

    def test_duration_and_size_are_translated(self):
        body, _ = h3.build_h3_video_request(video_payload(duration=8, resolution="720p"), "minimax-h3")
        self.assertEqual(body["seconds"], 8)
        self.assertEqual(body["size"], "720p")
        body, _ = h3.build_h3_video_request(video_payload(duration=4, aspect_ratio="9:16"), "minimax-h3")
        self.assertEqual(body["seconds"], 4)
        self.assertEqual(body["aspect_ratio"], "9:16")

    def test_forwards_all_media_to_gateway_fields(self):
        body, media = h3.build_h3_video_request(
            video_payload(
                images=[
                    {"url": "a.png", "role": "first_frame"},
                    {"url": "b.png", "role": "last_frame"},
                    {"url": "c.png"},
                ],
                videos=["http://example/a.mp4"],
            ),
            "minimax-h3",
        )
        self.assertEqual(
            media["images"],
            [("first_frame", "a.png"), ("last_frame", "b.png"), ("ref_image_0", "c.png")],
        )
        self.assertEqual(media["videos"], ["http://example/a.mp4"])
        self.assertNotIn("first_frame", body)

    def test_unlabeled_first_image_still_promoted_without_multimodal(self):
        # 现状保留：不勾全能参考时，第一张无角色图仍进首帧槽。
        _, media = h3.build_h3_video_request(
            video_payload(images=[{"url": "a.png"}, {"url": "b.png"}]),
            "minimax-h3",
        )
        self.assertEqual(media["images"], [("first_frame", "a.png"), ("ref_image_0", "b.png")])

    def test_multimodal_sends_all_unlabeled_images_as_refs(self):
        # 全能参考：全部无角色图进 ref_image_N，不再自动占用首帧槽。
        _, media = h3.build_h3_video_request(
            video_payload(multimodal=True, images=[{"url": "a.png"}, {"url": "b.png"}]),
            "minimax-h3",
        )
        self.assertEqual(media["images"], [("ref_image_0", "a.png"), ("ref_image_1", "b.png")])

    def test_multimodal_keeps_explicit_frame_roles(self):
        _, media = h3.build_h3_video_request(
            video_payload(
                multimodal=True,
                images=[
                    {"url": "a.png", "role": "first_frame"},
                    {"url": "b.png", "role": "last_frame"},
                    {"url": "c.png"},
                ],
            ),
            "minimax-h3",
        )
        self.assertEqual(
            media["images"],
            [("first_frame", "a.png"), ("last_frame", "b.png"), ("ref_image_0", "c.png")],
        )

    def test_gateway_error_text_is_used_as_is(self):
        self.assertEqual(h3.h3_error_text({"detail": "只允许上传 1 张首帧"}), "只允许上传 1 张首帧")
        self.assertEqual(h3.h3_error_text({"error": "不支持 seconds=4，只接受 5 到 15 之间的整数"}), "不支持 seconds=4，只接受 5 到 15 之间的整数")


class H3CancelTests(unittest.TestCase):
    def tearDown(self):
        main.CANVAS_TASKS.clear()
        main.CANVAS_VIDEO_TASK_HANDLES.clear()

    def test_cancel_url_matches_task_url(self):
        self.assertEqual(
            h3.h3_video_cancel_url("http://127.0.0.1:8088", "video_abc"),
            "http://127.0.0.1:8088/v1/videos/video_abc",
        )

    def test_cancel_queued_video_does_not_call_upstream(self):
        task_id = "canvas_h3_cancel_queued"
        main.CANVAS_TASKS[task_id] = {
            "id": task_id,
            "type": "h3-video",
            "status": "queued",
            "provider_id": "h3-local",
            "model": "minimax-h3",
            "upstream_task_id": "",
        }
        with mock.patch.object(main, "cancel_h3_upstream_video", new=mock.AsyncMock()) as delete_upstream:
            result = run(main.cancel_canvas_video_task(task_id))
        self.assertTrue(result["cancelled"])
        self.assertEqual(main.CANVAS_TASKS[task_id]["status"], "cancelled")
        delete_upstream.assert_not_awaited()

    def test_cancel_h3_running_task_deletes_upstream(self):
        task_id = "canvas_h3_cancel_running"
        main.CANVAS_TASKS[task_id] = {
            "id": task_id,
            "type": "h3-video",
            "status": "running",
            "provider_id": "h3-local",
            "model": "minimax-h3",
            "upstream_task_id": "video_abc",
        }
        handle = mock.Mock()
        main.CANVAS_VIDEO_TASK_HANDLES[task_id] = handle
        provider = {"id": "h3-local", "protocol": main.H3_PROTOCOL, "base_url": "http://127.0.0.1:8088"}
        with mock.patch.object(main, "get_api_provider", return_value=provider), mock.patch.object(
            main, "video_api_root", return_value="http://127.0.0.1:8088"
        ), mock.patch.object(main, "cancel_h3_upstream_video", new=mock.AsyncMock(return_value=True)) as delete_upstream:
            result = run(main.cancel_canvas_video_task(task_id))
        self.assertTrue(result["cancelled"])
        delete_upstream.assert_awaited_once()
        self.assertEqual(delete_upstream.await_args.args[2], "video_abc")
        handle.cancel.assert_called_once()

    def test_cancel_other_protocol_does_not_delete_h3(self):
        task_id = "canvas_cangyuan_cancel"
        main.CANVAS_TASKS[task_id] = {
            "id": task_id,
            "type": "cangyuan-video",
            "status": "running",
            "provider_id": "cangyuan",
            "upstream_task_id": "task_1",
        }
        handle = mock.Mock()
        main.CANVAS_VIDEO_TASK_HANDLES[task_id] = handle
        with mock.patch.object(main, "cancel_h3_upstream_video", new=mock.AsyncMock()) as delete_upstream:
            result = run(main.cancel_canvas_video_task(task_id))
        self.assertTrue(result["cancelled"])
        delete_upstream.assert_not_awaited()
        handle.cancel.assert_called_once()

    def test_generate_h3_deletes_upstream_when_cancelled_after_submit(self):
        provider = {"id": "h3-local", "protocol": main.H3_PROTOCOL}
        payload = video_payload()
        response = mock.Mock()
        response.status_code = 200
        response.json.return_value = {"id": "video_abc", "status": "queued"}
        client = mock.AsyncMock()
        client.post.return_value = response
        seen = []

        def on_task_id(task_id):
            seen.append(task_id)

        with mock.patch.object(main, "h3_api_key", return_value="test-key"), mock.patch.object(
            main, "api_headers", return_value={"Authorization": "Bearer test-key"}
        ), mock.patch.object(
            main, "cancel_h3_upstream_video", new=mock.AsyncMock(return_value=True)
        ) as delete_upstream:
            with self.assertRaises(main.CanvasTaskCancelled):
                run(main.generate_h3_video(
                    client,
                    payload,
                    provider,
                    "http://127.0.0.1:8088",
                    "minimax-h3",
                    on_task_id=on_task_id,
                    should_cancel=lambda: True,
                ))
        self.assertEqual(seen, ["video_abc"])
        delete_upstream.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
