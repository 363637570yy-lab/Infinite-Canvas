import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

import grok2api_protocol as grok2api
import main


def run(coro):
    return asyncio.run(coro)


def video_payload(**kwargs):
    base = {
        "prompt": "电影感镜头缓慢推进",
        "provider_id": "grok2api",
        "model": "grok-imagine-video",
        "duration": 8,
        "aspect_ratio": "16:9",
        "resolution": "720p",
    }
    base.update(kwargs)
    return main.CanvasVideoRequest(**base)


async def fake_resolve(value, kind, index):
    return f"https://cdn.example/{kind}-{index}.png"


class Grok2ApiRoutingTests(unittest.TestCase):
    def test_loopback_media_urls_are_rewritten_to_provider_base(self):
        loopback = "http://127.0.0.1:8000/v1/media/images/img_abc"
        rewritten = grok2api.rewrite_grok2api_media_url(loopback, "https://gateway.example/v1")
        self.assertEqual(rewritten, "https://gateway.example/v1/media/images/img_abc")
        # 已是公网主机时不改写。
        public = "https://cdn.example/v1/media/images/img_abc"
        self.assertEqual(
            grok2api.rewrite_grok2api_media_url(public, "https://gateway.example"),
            public,
        )
        # 非媒体路径不改写。
        self.assertEqual(
            grok2api.rewrite_grok2api_media_url("http://127.0.0.1:8000/healthz", "https://gateway.example"),
            "http://127.0.0.1:8000/healthz",
        )
        item = main.normalize_grok2api_image_item(
            {"type": "url", "value": loopback},
            {"base_url": "https://gateway.example"},
        )
        self.assertEqual(item["value"], "https://gateway.example/v1/media/images/img_abc")

    def test_protocol_and_urls_are_registered(self):
        provider = {"id": "grok2api", "protocol": main.GROK2API_PROTOCOL}

        self.assertIn(main.GROK2API_PROTOCOL, main.SUPPORTED_PROVIDER_PROTOCOLS)
        self.assertTrue(main.is_grok2api_provider(provider))
        self.assertTrue(main.is_grok2api_route(provider, "grok-imagine-video"))
        self.assertFalse(main.is_grok_provider(provider, "grok-imagine-video"))
        self.assertEqual(
            main.video_submit_url_candidates(provider, "https://gateway.example/v1", "grok-imagine-video"),
            ["https://gateway.example/v1/videos/generations"],
        )
        self.assertEqual(
            main.video_task_url_candidates(
                provider,
                "https://gateway.example",
                "request/42",
                "",
                "grok-imagine-video",
            ),
            ["https://gateway.example/v1/videos/request%2F42"],
        )
        self.assertEqual(
            grok2api.grok2api_video_content_url("https://gateway.example/v1", "request/42"),
            "https://gateway.example/v1/videos/request%2F42/content",
        )
        self.assertEqual(
            grok2api.grok2api_chat_completions_url("https://gateway.example/v1"),
            "https://gateway.example/v1/chat/completions",
        )
        self.assertEqual(
            grok2api.grok2api_image_generation_url("https://gateway.example"),
            "https://gateway.example/v1/images/generations",
        )
        self.assertEqual(
            grok2api.grok2api_image_edit_url("https://gateway.example/v1"),
            "https://gateway.example/v1/images/edits",
        )

    def test_models_prefer_upstream_capability_then_official_catalog(self):
        raw = {
            "data": [
                {"id": "grok-imagine-video", "supported_endpoint_types": ["openai-video"]},
                {"id": "gpt-vision", "supported_endpoint_types": ["openai-image"]},
                {"id": "grok-imagine-video-1.5"},
                {"id": "grok-chat-fast"},
                {"id": "build-dynamic-chat-xyz"},
            ]
        }
        grouped, ids = main.parse_upstream_models(raw, main.GROK2API_PROTOCOL)

        self.assertEqual(
            ids,
            [
                "build-dynamic-chat-xyz",
                "gpt-vision",
                "grok-chat-fast",
                "grok-imagine-video",
                "grok-imagine-video-1.5",
            ],
        )
        self.assertEqual(grouped["video"], ["grok-imagine-video", "grok-imagine-video-1.5"])
        self.assertEqual(grouped["image"], ["gpt-vision"])
        self.assertEqual(grouped["chat"], ["grok-chat-fast"])
        self.assertEqual(grouped["unknown"], ["build-dynamic-chat-xyz"])
        # 无能力字段时，官方固定 ID 走目录，不按子串猜。
        self.assertEqual(
            grok2api.classify_grok2api_model_entry({}, "grok-imagine-video"),
            "video",
        )
        self.assertEqual(
            grok2api.classify_grok2api_model_entry({}, "custom-video-model"),
            "unknown",
        )
        self.assertTrue(grok2api.grok2api_client_surface_supported("chat_completions"))
        self.assertFalse(grok2api.grok2api_client_surface_supported("responses"))
        self.assertIn("responses", grok2api.GROK2API_UNSUPPORTED_CLIENT_SURFACES)
        self.assertIn("messages", grok2api.GROK2API_UNSUPPORTED_CLIENT_SURFACES)
        self.assertIn("images_stream", grok2api.GROK2API_UNSUPPORTED_CLIENT_SURFACES)


class Grok2ApiRequestTests(unittest.TestCase):
    def test_documented_json_fields_and_single_image_mapping(self):
        payload = video_payload(
            duration=15,
            aspect_ratio="9:16",
            resolution="1080p",
            images=[{"url": "asset://local-image"}],
        )
        body = run(
            grok2api.build_grok2api_video_request(payload, "grok-imagine-video", fake_resolve)
        )

        self.assertEqual(
            body,
            {
                "model": "grok-imagine-video",
                "prompt": "电影感镜头缓慢推进",
                "duration": 15,
                "aspect_ratio": "9:16",
                "resolution": "1080p",
                "image": {"url": "https://cdn.example/图片-1.png"},
            },
        )
        for wrong_key in ("seconds", "size", "quality", "input_reference", "image_url"):
            self.assertNotIn(wrong_key, body)

    def test_multiple_images_use_reference_images_without_dropping_the_first(self):
        payload = video_payload(
            images=[
                {"url": "https://example.com/first.png"},
                {"url": "https://example.com/second.png"},
                {"url": "https://example.com/third.png"},
            ]
        )
        body = run(
            grok2api.build_grok2api_video_request(payload, "grok-imagine-video", fake_resolve)
        )

        self.assertEqual(body["image"], {"url": "https://cdn.example/图片-1.png"})
        self.assertEqual(
            body["reference_images"],
            [
                {"url": "https://cdn.example/图片-2.png"},
                {"url": "https://cdn.example/图片-3.png"},
            ],
        )

    def test_file_id_is_forwarded_without_being_silently_dropped(self):
        payload = video_payload(
            images=[
                {"file_id": "input_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"},
                {"url": "https://example.com/second.png"},
            ]
        )
        body = run(
            grok2api.build_grok2api_video_request(payload, "grok-imagine-video", fake_resolve)
        )

        self.assertEqual(body["image"], {"file_id": "input_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"})
        self.assertEqual(body["reference_images"], [{"url": "https://cdn.example/图片-2.png"}])

    def test_invalid_file_id_is_rejected_before_submission(self):
        payload = video_payload(images=[{"file_id": "input_upstream_asset_1"}])
        with self.assertRaises(HTTPException) as ctx:
            run(grok2api.build_grok2api_video_request(payload, "grok-imagine-video", fake_resolve))
        self.assertIn("input_*", ctx.exception.detail)

    def test_reference_cannot_contain_both_url_and_file_id(self):
        payload = video_payload(images=[{"url": "https://example.com/ref.png", "file_id": "input_asset"}])
        with self.assertRaises(HTTPException) as ctx:
            run(grok2api.build_grok2api_video_request(payload, "grok-imagine-video", fake_resolve))
        self.assertIn("url 或 file_id", ctx.exception.detail)

    def test_defaults_and_limits_match_grok2api(self):
        payload = SimpleNamespace(
            prompt="text",
            duration=None,
            aspect_ratio="",
            size="",
            resolution="",
            images=[],
            videos=[],
            audios=[],
        )
        body = run(grok2api.build_grok2api_video_request(payload, "grok-imagine-video"))

        self.assertEqual(body["duration"], 8)
        self.assertEqual(body["aspect_ratio"], "16:9")
        self.assertEqual(body["resolution"], "720p")

        for field, value, text in (
            ("duration", 0, "1-15"),
            ("duration", 16, "1-15"),
            ("aspect_ratio", "21:9", "画幅"),
            ("resolution", "2k", "清晰度"),
        ):
            invalid = video_payload(**{field: value})
            with self.subTest(field=field, value=value):
                with self.assertRaises(HTTPException) as ctx:
                    run(grok2api.build_grok2api_video_request(invalid, "grok-imagine-video"))
                self.assertIn(text, ctx.exception.detail)

    def test_canvas_default_remains_explicit_five_seconds(self):
        payload = main.CanvasVideoRequest(
            prompt="电影感镜头缓慢推进",
            provider_id="grok2api",
            model="grok-imagine-video",
        )
        body = run(grok2api.build_grok2api_video_request(payload, "grok-imagine-video"))

        self.assertEqual(payload.duration, 5)
        self.assertEqual(body["duration"], 5)

    def test_canvas_allows_grok2api_image_only_video(self):
        payload = main.CanvasVideoRequest(
            provider_id="grok2api",
            model="grok-imagine-video",
            images=[{"url": "https://example.com/reference.png"}],
        )
        body = run(grok2api.build_grok2api_video_request(payload, "grok-imagine-video"))

        self.assertEqual(body["prompt"], "")
        self.assertEqual(body["image"], {"url": "https://example.com/reference.png"})

    def test_unsupported_video_audio_frames_and_switches_are_rejected(self):
        cases = (
            {"videos": ["https://example.com/ref.mp4"]},
            {"audios": ["https://example.com/ref.mp3"]},
            {"images": [{"url": "https://example.com/first.png", "role": "first_frame"}]},
            {"generate_audio": True},
            {"seed": 7},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(HTTPException) as ctx:
                    run(grok2api.build_grok2api_video_request(video_payload(**kwargs), "grok-imagine-video"))
                self.assertEqual(ctx.exception.status_code, 400)

    def test_reference_limit_is_rejected_not_truncated(self):
        payload = video_payload(
            images=[{"url": f"https://example.com/{index}.png"} for index in range(9)]
        )
        with self.assertRaises(HTTPException) as ctx:
            run(grok2api.build_grok2api_video_request(payload, "grok-imagine-video"))
        self.assertIn("最多 8", ctx.exception.detail)

    def test_image_generation_request_uses_documented_json_fields(self):
        body = run(
            grok2api.build_grok2api_image_request(
                "一只戴红围巾的猫",
                "grok-imagine-image-quality",
                size="1024x1024",
                aspect_ratio="1:1",
                resolution="2k",
                quality="auto",
            )
        )

        self.assertEqual(
            body,
            {
                "model": "grok-imagine-image-quality",
                "prompt": "一只戴红围巾的猫",
                "n": 1,
                "response_format": "url",
                "size": "1024x1024",
                "aspect_ratio": "1:1",
                "resolution": "2k",
            },
        )
        self.assertNotIn("quality", body)

    def test_image_edit_request_uses_url_image_fields_and_preserves_references(self):
        body = run(
            grok2api.build_grok2api_image_request(
                "把背景改成夜景",
                "grok-imagine-image-edit",
                size="auto",
                aspect_ratio="16:9",
                resolution="1k",
                reference_images=[
                    {"url": "https://example.com/first.png"},
                    {"url": "https://example.com/second.png"},
                ],
            )
        )

        self.assertNotIn("image", body)
        self.assertEqual(
            body["images"],
            [
                {"url": "https://example.com/first.png"},
                {"url": "https://example.com/second.png"},
            ],
        )
        self.assertEqual(body["resolution"], "1k")

    def test_image_edit_rejects_file_id_and_preserves_upstream_limits(self):
        valid_file_id = "input_" + "A" * 32
        with self.assertRaises(HTTPException) as file_ctx:
            run(
                grok2api.build_grok2api_image_request(
                    "编辑",
                    "grok-imagine-image-edit",
                    reference_images=[{"file_id": valid_file_id}],
                )
            )
        self.assertIn("image.url", file_ctx.exception.detail)

        body = run(
            grok2api.build_grok2api_image_request(
                "编辑",
                "grok-imagine-image-edit",
                reference_images=[{"url": "https://example.com/ref.png"}],
                count=2,
                resolution="2k",
            )
        )
        self.assertEqual(body["n"], 2)
        self.assertEqual(body["resolution"], "2k")


class Grok2ApiResponseTests(unittest.TestCase):
    def test_task_and_result_are_normalized(self):
        raw = {
            "request_id": "req_42",
            "status": "done",
            "progress": 100,
            "video": {"url": "https://cdn.example/video.mp4"},
        }
        self.assertEqual(grok2api.grok2api_task_id(raw), "req_42")
        self.assertEqual(grok2api.grok2api_task_state(raw)[0], "success")
        self.assertEqual(
            grok2api.grok2api_video_result_urls(raw),
            ["https://cdn.example/video.mp4"],
        )
        self.assertEqual(
            grok2api.grok2api_task_state({"status": "failed"})[0],
            "failed",
        )

    def test_public_status_contract_only_accepts_done_and_failed(self):
        # 公开查询 JSON 只出 done/failed/pending；内部 completed 等不得当成功。
        for status in ("completed", "succeeded", "success", "ready", "queued", "processing", "pending", ""):
            with self.subTest(status=status):
                self.assertEqual(grok2api.grok2api_task_state({"status": status})[0], "pending")
        self.assertEqual(grok2api.grok2api_task_state({"status": "done"})[0], "success")
        self.assertEqual(grok2api.grok2api_task_state({"status": "failed"})[0], "failed")
        for status in ("failure", "expired", "cancelled", "error", "timeout"):
            with self.subTest(legacy_failure=status):
                self.assertEqual(grok2api.grok2api_task_state({"status": status})[0], "pending")

    def test_error_code_and_message_are_preserved(self):
        self.assertEqual(
            grok2api.grok2api_error_text({"error": {"code": "quota", "message": "out of quota"}}),
            "quota: out of quota",
        )


class Grok2ApiCanvasTaskTests(unittest.TestCase):
    def test_empty_video_request_returns_422(self):
        provider = {
            "id": "grok2api",
            "name": "Grok2API",
            "protocol": "grok2api",
        }
        with patch.object(main, "get_api_provider", return_value=provider):
            with self.assertRaises(HTTPException) as raised:
                run(main.canvas_video(main.CanvasVideoRequest(provider_id="grok2api")))

        self.assertEqual(raised.exception.status_code, 422)

    def test_canvas_route_returns_a_short_lived_pending_response(self):
        provider = {
            "id": "grok2api",
            "name": "Grok2API",
            "protocol": "grok2api",
            "base_url": "https://gateway.example",
            "video_models": ["grok-imagine-video"],
        }
        created_coroutines = []

        def capture_task(coro):
            created_coroutines.append(coro)
            coro.close()
            return object()

        with patch.object(main, "get_api_provider", return_value=provider), patch.object(
            main, "provider_env_key_value", return_value="test-token"
        ), patch.object(main.asyncio, "create_task", side_effect=capture_task):
            result = run(main.canvas_video(video_payload()))

        task_id = result["task_id"]
        try:
            self.assertTrue(result["video_pending"])
            self.assertTrue(result["grok2api_pending"])
            self.assertEqual(result["status"], "queued")
            self.assertTrue(task_id.startswith("canvas_grok2api_"))
            self.assertEqual(len(created_coroutines), 1)
            self.assertEqual(main.CANVAS_TASKS[task_id]["status"], "queued")
        finally:
            main.CANVAS_TASKS.pop(task_id, None)
            main.CANVAS_VIDEO_TASK_HANDLES.pop(task_id, None)

    def test_background_task_has_upstream_aligned_timeout_and_terminal_success(self):
        self.assertGreaterEqual(main.GROK2API_VIDEO_POLL_TIMEOUT, 2 * 60 * 60)
        task_id = "canvas_grok2api_test_success"
        main.CANVAS_TASKS[task_id] = {
            "id": task_id,
            "type": "grok2api-video",
            "status": "queued",
            "result": None,
            "error": "",
        }
        result = {"videos": ["/output/grok2api_video_test.mp4"], "task_id": "req_42"}
        try:
            with patch.object(main, "generate_grok2api_video", new=AsyncMock(return_value=result)) as generate:
                run(
                    main.run_grok2api_canvas_video_task(
                        task_id,
                        video_payload(),
                        {"id": "grok2api", "name": "Grok2API", "protocol": "grok2api"},
                        "https://gateway.example",
                        "grok-imagine-video",
                    )
                )
            self.assertEqual(main.CANVAS_TASKS[task_id]["status"], "succeeded")
            self.assertEqual(main.CANVAS_TASKS[task_id]["result"], result)
            generate.assert_awaited_once()
        finally:
            main.CANVAS_TASKS.pop(task_id, None)

    def test_background_task_exposes_failure_instead_of_leaking_an_exception(self):
        task_id = "canvas_grok2api_test_failure"
        main.CANVAS_TASKS[task_id] = {
            "id": task_id,
            "type": "grok2api-video",
            "status": "queued",
            "result": None,
            "error": "",
        }
        try:
            with patch.object(
                main,
                "generate_grok2api_video",
                new=AsyncMock(side_effect=HTTPException(status_code=502, detail="上游任务失败")),
            ):
                run(
                    main.run_grok2api_canvas_video_task(
                        task_id,
                        video_payload(),
                        {"id": "grok2api", "name": "Grok2API", "protocol": "grok2api"},
                        "https://gateway.example",
                        "grok-imagine-video",
                    )
                )
            self.assertEqual(main.CANVAS_TASKS[task_id]["status"], "failed")
            self.assertEqual(main.CANVAS_TASKS[task_id]["status_code"], 502)
            self.assertIn("上游任务失败", main.CANVAS_TASKS[task_id]["error"])
        finally:
            main.CANVAS_TASKS.pop(task_id, None)


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeVideoClient:
    def __init__(self, response):
        self.response = response
        self.post_calls = []

    async def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.response


class _FakePollingVideoClient:
    def __init__(self, post_response, get_responses):
        self.post_response = post_response
        self.get_responses = list(get_responses)
        self.post_calls = []
        self.get_calls = []

    async def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.post_response

    async def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.get_responses.pop(0)


class _FakeChatClient:
    def __init__(self, response):
        self.response = response
        self.post_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.response


class Grok2ApiHttpTests(unittest.TestCase):
    def test_chat_uses_grok2api_chat_completions_route(self):
        client = _FakeChatClient(
            _FakeResponse(
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": "你好"}}
                    ],
                    "usage": {"total_tokens": 3},
                }
            )
        )
        provider = {
            "id": "grok2api",
            "name": "Grok2API",
            "protocol": "grok2api",
            "base_url": "https://gateway.example",
            "chat_models": ["grok-chat-model"],
        }
        payload = main.ChatRequest(provider="grok2api", model="grok-chat-model", message="你好")
        conversation = {"messages": [{"role": "user", "content": "你好"}]}

        with patch.object(main, "get_api_provider", return_value=provider), patch.object(
            main, "provider_env_key_value", return_value="test-token"
        ), patch.object(main.httpx, "AsyncClient", return_value=client):
            result = run(main.build_chat_text_reply(payload, conversation))

        self.assertEqual(result["content"], "你好")
        self.assertEqual(client.post_calls[0][0], "https://gateway.example/v1/chat/completions")
        self.assertEqual(client.post_calls[0][1]["json"]["model"], "grok-chat-model")
        self.assertEqual(client.post_calls[0][1]["headers"]["Authorization"], "Bearer test-token")

    def test_image_generation_and_edit_use_separate_documented_routes(self):
        client = _FakeVideoClient(
            _FakeResponse(
                {"data": [{"url": "https://gateway.example/v1/media/images/result.png"}]}
            )
        )
        provider = {
            "id": "grok2api",
            "name": "Grok2API",
            "protocol": "grok2api",
            "base_url": "https://gateway.example/v1",
        }

        with patch.object(main, "provider_env_key_value", return_value="test-token"):
            generated, _ = run(
                main.generate_grok2api_image(
                    "一只猫",
                    "1024x1024",
                    "auto",
                    "grok-imagine-image-quality",
                    [],
                    provider,
                    resolution="2k",
                    client=client,
                )
            )
            edited, _ = run(
                main.generate_grok2api_image(
                    "改成夜景",
                    "auto",
                    "auto",
                    "grok-imagine-image-edit",
                    [{"url": "https://example.com/ref.png"}],
                    provider,
                    resolution="1k",
                    client=client,
                )
            )

        self.assertEqual(generated["type"], "url")
        self.assertEqual(edited["type"], "url")
        self.assertEqual(client.post_calls[0][0], "https://gateway.example/v1/images/generations")
        self.assertEqual(client.post_calls[1][0], "https://gateway.example/v1/images/edits")
        self.assertEqual(client.post_calls[1][1]["json"]["image"], {"url": "https://example.com/ref.png"})
        self.assertEqual(client.post_calls[0][1]["headers"]["Content-Type"], "application/json")
        # 产品路径不做流式出图 / Responses / Messages。
        for _, request in client.post_calls:
            self.assertNotIn("stream", request["json"])
            self.assertNotIn("partial_images", request["json"])

    def test_submit_uses_documented_url_json_and_bearer_header(self):
        client = _FakeVideoClient(
            _FakeResponse(
                {
                    "request_id": "req_42",
                    "status": "done",
                    "video": {"url": "https://gateway.example/v1/videos/req_42.mp4"},
                }
            )
        )
        payload = video_payload(duration=12, aspect_ratio="9:16", resolution="1080p")
        provider = {"id": "grok2api", "name": "Grok2API", "protocol": "grok2api"}
        save_result = AsyncMock(return_value="/output/grok2api_video_req_42.mp4")

        with patch.object(main, "provider_env_key_value", return_value="test-token"), patch.object(
            main, "save_remote_video_to_output", save_result
        ):
            result = run(
                main.generate_grok2api_video(
                    client,
                    payload,
                    provider,
                    "https://gateway.example/v1",
                    "grok-imagine-video",
                )
            )

        self.assertEqual(len(client.post_calls), 1)
        submit_url, request = client.post_calls[0]
        self.assertEqual(submit_url, "https://gateway.example/v1/videos/generations")
        self.assertEqual(
            request["json"],
            {
                "model": "grok-imagine-video",
                "prompt": "电影感镜头缓慢推进",
                "duration": 12,
                "aspect_ratio": "9:16",
                "resolution": "1080p",
            },
        )
        self.assertEqual(request["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(request["headers"]["Content-Type"], "application/json")
        self.assertEqual(result["videos"], ["/output/grok2api_video_req_42.mp4"])

    def test_pending_video_url_is_not_downloaded_before_terminal_status(self):
        client = _FakePollingVideoClient(
            _FakeResponse({"request_id": "req_42", "status": "pending"}),
            [
                _FakeResponse(
                    {
                        "request_id": "req_42",
                        "status": "pending",
                        "progress": 40,
                        "video": {"url": "https://cdn.example/not-ready.mp4"},
                    }
                ),
                _FakeResponse(
                    {
                        "request_id": "req_42",
                        "status": "done",
                        "progress": 100,
                        "video": {"url": "https://cdn.example/ready.mp4"},
                    }
                ),
            ],
        )
        provider = {"id": "grok2api", "name": "Grok2API", "protocol": "grok2api"}
        save_result = AsyncMock(return_value="/output/grok2api_video_req_42.mp4")
        payload = video_payload()

        with patch.object(main, "provider_env_key_value", return_value="test-token"), patch.object(
            main, "save_remote_video_to_output", save_result
        ), patch.object(main.asyncio, "sleep", new=AsyncMock()):
            result = run(
                main.generate_grok2api_video(
                    client,
                    payload,
                    provider,
                    "https://gateway.example",
                    "grok-imagine-video",
                )
            )

        self.assertEqual(len(client.get_calls), 2)
        self.assertEqual(client.get_calls[0][0], "https://gateway.example/v1/videos/req_42")
        self.assertEqual(save_result.await_count, 1)
        self.assertEqual(result["videos"], ["/output/grok2api_video_req_42.mp4"])

    def test_local_reference_is_converted_to_public_url_not_file_id(self):
        """客户端主路径是公网 URL；不会把本地素材伪造成 input_* file_id。"""
        payload = video_payload(images=[{"url": "asset://local-image"}])
        resolve_calls = []

        async def resolve(value, kind, index):
            resolve_calls.append((value, kind, index))
            return f"https://cdn.example/public-{index}.png"

        body = run(
            grok2api.build_grok2api_video_request(payload, "grok-imagine-video", resolve)
        )
        self.assertEqual(resolve_calls, [("asset://local-image", "图片", 1)])
        self.assertEqual(body["image"], {"url": "https://cdn.example/public-1.png"})
        self.assertNotIn("file_id", body["image"])


if __name__ == "__main__":
    unittest.main()
