"""参考生图：有参考图就必须准备好再发出，不能静默降级成纯文生。

对照 qnvideo-gzt 生图工作台的同类修复：
1. data URL / 画布落盘地址不能被 is_image_reference 丢掉；
2. /images/edits 必须带上压缩后的图片字节，禁止空 files 仍去 POST；
3. 一张都读不到就 400，不要当文生图继续扣费。
"""
import base64
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
PNG_DATA_URL = "data:image/png;base64," + base64.b64encode(PNG_1PX).decode("ascii")


def openai_provider():
    return {
        "id": "test-openai",
        "name": "Test OpenAI",
        "base_url": "https://api.example.com/v1",
        "protocol": "openai",
        "image_request_mode": "openai",
        "api_key": "sk-test",
    }


def openai_responses_provider():
    item = openai_provider()
    item["id"] = "test-openai-rs"
    item["name"] = "Test OpenAI RS"
    item["image_request_mode"] = "openai-responses"
    return item


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"data": [{"b64_json": base64.b64encode(PNG_1PX).decode("ascii")}]}
        self.text = text or "{}"

    def raise_for_status(self):
        if self.status_code >= 400:
            request = main.httpx.Request("POST", "https://api.example.com/v1/images/edits")
            response = main.httpx.Response(self.status_code, request=request, text=self.text)
            raise main.httpx.HTTPStatusError("error", request=request, response=response)

    def json(self):
        return self._payload


class FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self.posts = []
        self.response = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        self.posts.append({"url": url, **kwargs})
        return self.response if self.response is not None else FakeResponse()


class ImageReferenceRecognitionTests(unittest.TestCase):
    def test_data_url_is_an_image_even_without_kind(self):
        self.assertTrue(main.is_image_reference({"url": PNG_DATA_URL}))

    def test_canvas_output_path_without_extension_is_kept(self):
        self.assertTrue(main.is_image_reference({"url": "/output/online_abc123"}))
        self.assertTrue(main.is_image_reference({"url": "/assets/refs/foo"}))

    def test_avif_and_heic_are_images(self):
        self.assertTrue(main.is_image_reference({"url": "/output/shot.avif"}))
        self.assertTrue(main.is_image_reference({"url": "/assets/phone.heic"}))

    def test_videos_are_still_rejected(self):
        self.assertFalse(main.is_image_reference({"url": "/output/clip.mp4"}))
        self.assertFalse(main.is_image_reference({"kind": "video", "url": "/output/photo.png"}))

    def test_kind_image_still_wins(self):
        self.assertTrue(main.is_image_reference({"kind": "image", "url": "https://cdn.example/no-ext"}))


class PreparedImageReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_local_png_becomes_multipart_bytes(self):
        path = os.path.join(self.tmp, "ref.png")
        with open(path, "wb") as fh:
            fh.write(PNG_1PX)
        with patch.object(main, "output_file_from_url", return_value=path):
            part = main.prepared_image_reference_part({"url": "/output/ref.png", "name": "ref.png"})
        self.assertIsNotNone(part)
        name, raw, mime = part
        self.assertTrue(name.endswith(".png"))
        self.assertGreater(len(raw), 0)
        self.assertIn("image/", mime)

    def test_data_url_is_prepared_instead_of_skipped(self):
        part = main.prepared_image_reference_part({"url": PNG_DATA_URL, "kind": "image"})
        self.assertIsNotNone(part)
        self.assertGreater(len(part[1]), 0)
        self.assertTrue(part[2].startswith("image/"))

    def test_unreadable_http_url_returns_none(self):
        self.assertIsNone(main.prepared_image_reference_part({"url": "https://cdn.example/missing.png"}))


class OpenAiEditReferenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_edits_posts_prepared_data_url_instead_of_empty_files(self):
        client = FakeAsyncClient()
        with patch.object(main, "get_api_provider", return_value=openai_provider()), \
             patch.object(main, "api_headers", return_value={"Authorization": "Bearer x"}), \
             patch.object(main, "extract_image", return_value={"type": "b64", "value": "AA"}), \
             patch.object(main.httpx, "AsyncClient", return_value=client):
            image, _raw = await main.generate_ai_image(
                "edit this",
                "1024x1024",
                "auto",
                "test-image",
                [{"url": PNG_DATA_URL, "kind": "image"}],
                "test-openai",
            )
        self.assertEqual(image["type"], "b64")
        self.assertEqual(len(client.posts), 1)
        self.assertTrue(client.posts[0]["url"].endswith("/images/edits"))
        files = client.posts[0]["files"]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0][0], "image")
        self.assertGreater(len(files[0][1][1]), 0)
        self.assertTrue(str(files[0][1][2]).startswith("image/"))

    async def test_unreadable_references_fail_before_empty_edits_post(self):
        client = FakeAsyncClient()
        with patch.object(main, "get_api_provider", return_value=openai_provider()), \
             patch.object(main, "api_headers", return_value={"Authorization": "Bearer x"}), \
             patch.object(main.httpx, "AsyncClient", return_value=client):
            with self.assertRaises(HTTPException) as ctx:
                await main.generate_ai_image(
                    "edit this",
                    "1024x1024",
                    "auto",
                    "test-image",
                    [{"url": "https://cdn.example/missing.png", "kind": "image"}],
                    "test-openai",
                )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("空请求", ctx.exception.detail)
        self.assertEqual(client.posts, [])

    async def test_filtered_out_references_fail_in_build_online_image_result(self):
        payload = main.OnlineImageRequest(
            prompt="keep the character",
            provider_id="test-openai",
            model="test-image",
            reference_images=[main.AIReference(url="/output/clip.mp4", kind="video")],
        )
        with patch.object(main, "get_api_provider", return_value=openai_provider()):
            with self.assertRaises(HTTPException) as ctx:
                await main.build_online_image_result(payload)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("没有一张被识别为图片", ctx.exception.detail)


SAFETY_REJECT_TEXT = (
    '{"error":{"message":"Your request was rejected by the safety system. '
    'safety_violations=[sexual].","type":"image_generation_user_error","param":null}}'
)


class ResponsesImageEditRoutingTests(unittest.TestCase):
    def test_default_openai_mode_never_forces_responses(self):
        self.assertFalse(main.should_use_responses_for_image_refs(
            "openai", "gpt-image-2", [{"url": PNG_DATA_URL}], []
        ))
        self.assertFalse(main.should_use_responses_for_image_refs(
            "openai", "test-image", [{"url": PNG_DATA_URL}], []
        ))

    def test_openai_responses_mode_uses_responses(self):
        self.assertTrue(main.should_use_responses_for_image_refs(
            "openai-responses", "any-model", [], []
        ))

    def test_safety_text_is_not_a_background_param_reject(self):
        self.assertTrue(main.responses_is_content_reject(SAFETY_REJECT_TEXT))
        self.assertFalse(main.responses_is_content_reject('{"error":{"message":"Unknown parameter: background"}}'))


class GptImage2ImagesApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_gpt_image_2_reference_posts_edits_not_responses(self):
        client = FakeAsyncClient()
        with patch.object(main, "get_api_provider", return_value=openai_provider()), \
             patch.object(main, "api_headers", return_value={"Authorization": "Bearer x"}), \
             patch.object(main, "extract_image", return_value={"type": "b64", "value": "AA"}), \
             patch.object(main.httpx, "AsyncClient", return_value=client):
            image, _raw = await main.generate_ai_image(
                "keep the character",
                "1024x1024",
                "auto",
                "gpt-image-2",
                [{"url": PNG_DATA_URL, "kind": "image"}],
                "test-openai",
            )
        self.assertEqual(image["type"], "b64")
        self.assertEqual(len(client.posts), 1)
        self.assertTrue(client.posts[0]["url"].endswith("/images/edits"))
        files = client.posts[0]["files"]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0][0], "image")
        self.assertGreater(len(files[0][1][1]), 0)

    async def test_openai_responses_mode_still_posts_responses_edit(self):
        client = FakeAsyncClient()
        with patch.object(main, "get_api_provider", return_value=openai_responses_provider()), \
             patch.object(main, "api_headers", return_value={"Authorization": "Bearer x"}), \
             patch.object(main, "extract_image", return_value={"type": "b64", "value": "AA"}), \
             patch.object(main.httpx, "AsyncClient", return_value=client):
            image, _raw = await main.generate_ai_image(
                "keep the character",
                "1024x1024",
                "auto",
                "gpt-image-2",
                [{"url": PNG_DATA_URL, "kind": "image"}],
                "test-openai-rs",
            )
        self.assertEqual(image["type"], "b64")
        self.assertEqual(len(client.posts), 1)
        self.assertTrue(client.posts[0]["url"].endswith("/responses"))
        body = client.posts[0]["json"]
        self.assertEqual(body["tools"][0]["action"], "edit")
        content = body["input"][0]["content"]
        self.assertEqual(content[1]["type"], "input_image")
        self.assertTrue(str(content[1]["image_url"]).startswith("data:image/"))

    async def test_unreadable_gpt_image_2_refs_fail_before_empty_edits_post(self):
        client = FakeAsyncClient()
        with patch.object(main, "get_api_provider", return_value=openai_provider()), \
             patch.object(main, "api_headers", return_value={"Authorization": "Bearer x"}), \
             patch.object(main.httpx, "AsyncClient", return_value=client):
            with self.assertRaises(HTTPException) as ctx:
                await main.generate_ai_image(
                    "keep the character",
                    "1024x1024",
                    "auto",
                    "gpt-image-2",
                    [{"url": "https://cdn.example/missing.png", "kind": "image"}],
                    "test-openai",
                )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("空请求", ctx.exception.detail)
        self.assertEqual(client.posts, [])

    async def test_safety_reject_does_not_retry_responses(self):
        client = FakeAsyncClient()
        client.response = FakeResponse(status_code=400, text=SAFETY_REJECT_TEXT)

        async def boom_stream(*_args, **_kwargs):
            raise AssertionError("safety 400 must not fall back to stream")

        with patch.object(main, "post_openai_responses_stream", side_effect=boom_stream):
            resp = await main.post_openai_responses(
                client,
                "https://api.example.com/v1/responses",
                {"Authorization": "Bearer x"},
                {"model": "gpt-image-2"},
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(len(client.posts), 1)


class FriendlySafetyErrorTests(unittest.IsolatedAsyncioTestCase):
    def test_safety_violations_become_chinese(self):
        detail = main.friendly_image_error_detail(SAFETY_REJECT_TEXT, "1024x1024", "gpt-image-2")
        self.assertIn("内容安全", detail)
        self.assertNotIn("safety_violations", detail)

    async def test_build_online_image_result_rewrites_http_exception(self):
        payload = main.OnlineImageRequest(
            prompt="keep the character",
            provider_id="test-openai",
            model="gpt-image-2",
            reference_images=[main.AIReference(url=PNG_DATA_URL, kind="image")],
        )

        async def boom(*_args, **_kwargs):
            raise HTTPException(status_code=400, detail=SAFETY_REJECT_TEXT)

        with patch.object(main, "get_api_provider", return_value=openai_provider()), \
             patch.object(main, "generate_ai_image", side_effect=boom):
            with self.assertRaises(HTTPException) as ctx:
                await main.build_online_image_result(payload)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("内容安全", ctx.exception.detail)
        self.assertNotIn("safety_violations", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
