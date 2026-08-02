"""APIMart 代理 Gemini 图像模型的三层适配。

APIMart 不是原样转发 Gemini：
1. 原生 Gemini API 挂在站点根的 /v1beta 下，而平台 base_url 通常配到 /v1，
   直接拼会得到 /v1/v1beta 的双前缀；
2. 响应外面包一层 {code, data}，candidates 被埋在下面；
3. 图片可能不走 inlineData，而是塞在 text part 的 Markdown data URL 里。
三层任意一层没适配，表现都是出图报错或拿不到图，所以逐层锁死。
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
from fastapi import HTTPException


PNG_B64 = "iVBORw0KGgoAAAANSUhEUg"
JPEG_B64 = "/9j/4AAQSkZJRgABAQAAAQ"


def apimart_provider(**extra):
    item = {
        "id": "apimart",
        "name": "APIMART",
        "base_url": "https://api.apimart.ai/v1",
        "protocol": "apimart",
    }
    item.update(extra)
    return item


def gemini_response(parts):
    return {"candidates": [{"content": {"parts": parts}}]}


class GeminiEndpointTests(unittest.TestCase):
    """双前缀是本次报错的根因，端点拼接单独锁。"""

    def test_apimart_endpoint_has_no_double_prefix(self):
        url = main.gemini_endpoint_url(apimart_provider(), "gemini-3-pro-image-preview")
        self.assertNotIn("/v1/v1beta", url)
        self.assertEqual(
            url,
            "https://api.apimart.ai/v1beta/models/gemini-3-pro-image-preview:generateContent",
        )

    def test_apimart_endpoint_keeps_configured_host(self):
        """host 从 base_url 推导而不是写死，换镜像域名仍然可用。"""
        provider = apimart_provider(base_url="https://mirror.apimart.ai/v1")
        url = main.gemini_endpoint_url(provider, "gemini-3-pro-image-preview")
        self.assertTrue(url.startswith("https://mirror.apimart.ai/v1beta/models/"))

    def test_apimart_endpoint_without_version_suffix(self):
        provider = apimart_provider(base_url="https://api.apimart.ai")
        url = main.gemini_endpoint_url(provider, "gemini-3-pro-image-preview")
        self.assertEqual(
            url,
            "https://api.apimart.ai/v1beta/models/gemini-3-pro-image-preview:generateContent",
        )

    def test_manual_override_still_wins(self):
        provider = apimart_provider(image_generation_endpoint="https://custom.example.com/gen")
        url = main.gemini_endpoint_url(provider, "gemini-3-pro-image-preview")
        self.assertEqual(url, "https://custom.example.com/gen")

    def test_non_apimart_provider_unchanged(self):
        """非 APIMart 平台必须继续走 provider_endpoint_url，行为不能被这次改动波及。"""
        provider = {"id": "google", "base_url": "https://generativelanguage.googleapis.com", "protocol": "gemini"}
        expected = main.provider_endpoint_url(
            provider,
            "image_generation_endpoint",
            "/v1beta/models/gemini-3-pro-image-preview:generateContent",
        )
        self.assertEqual(main.gemini_endpoint_url(provider, "gemini-3-pro-image-preview"), expected)

    def test_model_name_is_url_quoted(self):
        url = main.gemini_endpoint_url(apimart_provider(), "models/gemini-3-pro-image-preview")
        self.assertIn("/models/gemini-3-pro-image-preview:generateContent", url)


class ChatBaseTests(unittest.TestCase):
    """聊天侧走的是另一条拼接分支，同因，一起锁。"""

    def _resolve(self, provider):
        with patch.object(main, "get_api_provider", return_value=provider), \
             patch.object(main, "preferred_chat_model", return_value="gemini-3-pro"), \
             patch.object(main, "effective_protocol", return_value="gemini"), \
             patch.object(main, "api_headers", return_value={}):
            base, _headers, _model = main.resolve_chat_provider("apimart", "gemini-3-pro", "")
        return base

    def test_apimart_chat_base_has_no_double_prefix(self):
        base = self._resolve(apimart_provider())
        self.assertNotIn("/v1/v1beta", base)
        self.assertEqual(base, "https://api.apimart.ai/v1beta")

    def test_non_apimart_chat_base_unchanged(self):
        provider = {
            "id": "google",
            "name": "Google",
            "base_url": "https://generativelanguage.googleapis.com",
            "protocol": "gemini",
        }
        self.assertEqual(self._resolve(provider), "https://generativelanguage.googleapis.com/v1beta")


class MarkdownDataUrlTests(unittest.TestCase):
    def test_markdown_wrapped_data_url_extracted(self):
        payload = main.image_payload_from_string(f"![image](data:image/jpeg;base64,{JPEG_B64})")
        self.assertIsNotNone(payload)
        self.assertEqual(payload["type"], "b64")
        self.assertEqual(payload["mime_type"], "image/jpeg")
        self.assertEqual(payload["value"], JPEG_B64)

    def test_data_url_with_leading_prose(self):
        text = f"这是你要的图：\n\n![result](data:image/png;base64,{PNG_B64})\n"
        payload = main.image_payload_from_string(text)
        self.assertEqual(payload["mime_type"], "image/png")
        self.assertEqual(payload["value"], PNG_B64)

    def test_plain_data_url_unchanged(self):
        """原有的纯 data URL 路径不能被新正则改写。"""
        payload = main.image_payload_from_string(f"data:image/png;base64,{PNG_B64}")
        self.assertEqual(payload["type"], "b64")
        self.assertEqual(payload["mime_type"], "image/png")
        self.assertEqual(payload["value"], PNG_B64)

    def test_plain_text_still_returns_none(self):
        self.assertIsNone(main.image_payload_from_string("生成失败，请重试"))
        self.assertIsNone(main.image_payload_from_string(""))
        self.assertIsNone(main.image_payload_from_string(None))


class GeminiPartExtractionTests(unittest.TestCase):
    def test_inline_data_takes_priority(self):
        raw = gemini_response([
            {"inlineData": {"mimeType": "image/png", "data": PNG_B64}, "text": f"![i](data:image/jpeg;base64,{JPEG_B64})"},
        ])
        payload = main.extract_image(raw)
        self.assertEqual(payload["value"], PNG_B64)
        self.assertEqual(payload["mime_type"], "image/png")

    def test_falls_back_to_markdown_text(self):
        raw = gemini_response([{"text": f"![i](data:image/jpeg;base64,{JPEG_B64})"}])
        payload = main.extract_image(raw)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["value"], JPEG_B64)
        self.assertEqual(payload["mime_type"], "image/jpeg")

    def test_text_only_part_without_image_ignored(self):
        """纯文字回复不能被误判成图片；取不到图时沿用既有的 502，不是静默出空图。"""
        raw = gemini_response([{"text": "模型拒绝了这个提示词"}])
        with self.assertRaises(HTTPException) as ctx:
            main.extract_images(raw)
        self.assertEqual(ctx.exception.status_code, 502)
        with self.assertRaises(HTTPException):
            main.extract_image(raw)

    def test_extract_images_dedups_inline_and_text(self):
        """同一张图既走 inlineData 又出现在 Markdown 里时只能收一次。"""
        raw = gemini_response([
            {"inlineData": {"mimeType": "image/png", "data": PNG_B64}, "text": f"![i](data:image/png;base64,{PNG_B64})"},
        ])
        images = main.extract_images(raw)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["value"], PNG_B64)

    def test_extract_images_collects_distinct_parts(self):
        raw = gemini_response([
            {"inlineData": {"mimeType": "image/png", "data": PNG_B64}},
            {"text": f"![i](data:image/jpeg;base64,{JPEG_B64})"},
        ])
        values = [item["value"] for item in main.extract_images(raw)]
        self.assertIn(PNG_B64, values)
        self.assertIn(JPEG_B64, values)


class ApimartEnvelopeTests(unittest.TestCase):
    def test_unwrap_exposes_candidates(self):
        wrapped = {"code": 200, "data": gemini_response([{"inlineData": {"mimeType": "image/png", "data": PNG_B64}}])}
        unwrapped = main.unwrap_apimart_response(wrapped)
        self.assertIn("candidates", unwrapped)
        self.assertEqual(main.extract_image(unwrapped)["value"], PNG_B64)

    def test_unwrapped_body_passes_through(self):
        raw = gemini_response([{"inlineData": {"mimeType": "image/png", "data": PNG_B64}}])
        self.assertIs(main.unwrap_apimart_response(raw), raw)

    def test_wrapped_body_without_unwrap_yields_nothing(self):
        """回归护栏：不解包就取不到图，正是这次报错的第二层原因。"""
        wrapped = {"code": 200, "data": gemini_response([{"inlineData": {"mimeType": "image/png", "data": PNG_B64}}])}
        with self.assertRaises(HTTPException):
            main.extract_images(wrapped)
        with self.assertRaises(HTTPException):
            main.extract_image(wrapped)


if __name__ == "__main__":
    unittest.main()
