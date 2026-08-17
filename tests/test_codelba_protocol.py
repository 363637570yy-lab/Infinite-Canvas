import asyncio
import os
import unittest

from fastapi import HTTPException

import main
import codelba_protocol as codelba


def run(coro):
    return asyncio.run(coro)


CODELBA_OPENAPI_SD25 = {
    "id": "sd2.5",
    "object": "model",
    "name": "SD2.5",
    "durations": [5, 8, 10, 15],
    "resolutions": ["720"],
    "aspect_ratios": ["16:9", "4:3", "9:16", "1:1"],
    "max_image_refs": 9,
    "max_video_refs": 3,
    "max_audio_refs": 3,
    "compliance_supported": True,
}

CODELBA_OPENAPI_SEEDANCE_FAST = {
    "id": "seedance-2.0-fast-720p",
    "object": "model",
    "name": "SD2.0 Fast 720p",
    "durations": [5, 8, 10, 15],
    "resolutions": ["720"],
    "aspect_ratios": ["16:9", "4:3", "9:16", "1:1"],
    "max_image_refs": 9,
    "max_video_refs": 3,
    "max_audio_refs": 3,
    "compliance_supported": True,
}

CODELBA_MODELS_RESPONSE = {
    "object": "list",
    "data": [
        {"id": "sd-2-c5", "supported_endpoint_types": ["openai-video"]},
        {"id": "sd-2-c5-10", "supported_endpoint_types": ["video"]},
        {"id": "seedance2.0-14s", "owned_by": "video-api", "description": "async /openapi/v1/videos"},
        CODELBA_OPENAPI_SD25,
        CODELBA_OPENAPI_SEEDANCE_FAST,
        {"id": "gpt-image-2", "supported_endpoint_types": ["image-generation"]},
        {"id": "gpt-5.5", "supported_endpoint_types": ["openai"]},
        {"id": "mystery-model"},
        {"id": "name-looks-like-video"},
    ],
}


def video_payload(**kwargs):
    base = {
        "prompt": "人物站在海边，海风吹动衣角，镜头缓慢向前推进，电影质感。",
        "provider_id": "codelba",
        "model": "sd-2-c5",
        "duration": 10,
        "aspect_ratio": "16:9",
    }
    base.update(kwargs)
    return main.CanvasVideoRequest(**base)


async def fake_resolve(value, kind, index):
    return f"https://cdn.example/{kind}-{index}.media"


class CodelbaRoutingTests(unittest.TestCase):
    def test_protocol_and_openapi_urls_are_registered(self):
        provider = {"id": "codelba", "protocol": main.CODELBA_PROTOCOL}

        self.assertIn(main.CODELBA_PROTOCOL, main.SUPPORTED_PROVIDER_PROTOCOLS)
        self.assertTrue(main.is_codelba_provider(provider))
        self.assertTrue(main.is_codelba_route(provider, "sd-2-c5"))
        self.assertFalse(main.is_chre3_video_route(provider, "sd-2-c5"))
        self.assertFalse(main.is_cangyuan_video_route(provider, "sd-2-c5"))
        self.assertFalse(main.is_megabyai_route(provider, "sd-2-c5"))
        self.assertEqual(
            main.video_submit_url_candidates(provider, "https://codelba.cn/v1", "sd-2-c5"),
            ["https://hz.codelba.cn/openapi/v1/videos"],
        )
        self.assertEqual(
            main.video_submit_url_candidates(provider, "https://codelba.cn/openapi/v1", "sd-2-c5"),
            ["https://hz.codelba.cn/openapi/v1/videos"],
        )
        self.assertEqual(
            main.video_task_url_candidates(provider, "https://codelba.cn", "video-168", "", "sd-2-c5"),
            ["https://hz.codelba.cn/openapi/v1/videos/video-168"],
        )
        self.assertEqual(
            codelba.codelba_video_content_url("https://codelba.cn/v1", "video-168"),
            "https://hz.codelba.cn/openapi/v1/videos/video-168/content",
        )
        self.assertEqual(
            main.upstream_models_url("https://codelba.cn", main.CODELBA_PROTOCOL),
            "https://hz.codelba.cn/openapi/v1/models",
        )
        self.assertEqual(
            main.upstream_models_url("https://codelba.cn/openapi/v1", main.CODELBA_PROTOCOL),
            "https://hz.codelba.cn/openapi/v1/models",
        )
        self.assertEqual(
            main.upstream_models_url("https://codelba.cn/v1", main.CODELBA_PROTOCOL),
            "https://hz.codelba.cn/openapi/v1/models",
        )
        self.assertEqual(
            main.upstream_models_url("https://hz.codelba.cn/ai_video_ui/", main.CODELBA_PROTOCOL),
            "https://hz.codelba.cn/openapi/v1/models",
        )
        self.assertEqual(codelba.codelba_api_root(""), "https://hz.codelba.cn")
        self.assertEqual(codelba.codelba_api_root("https://hz.codelba.cn/ai_video_ui/"), "https://hz.codelba.cn")
        self.assertEqual(
            codelba.codelba_api_root("https://hz.codelba.cn/ai_video_ui/openapi/v1"),
            "https://hz.codelba.cn",
        )

    def test_models_use_upstream_capabilities_not_names(self):
        grouped, ids = main.parse_upstream_models(CODELBA_MODELS_RESPONSE, main.CODELBA_PROTOCOL)

        self.assertEqual(len(ids), 9)
        for model in ("sd-2-c5", "sd-2-c5-10", "seedance2.0-14s", "sd2.5", "seedance-2.0-fast-720p"):
            self.assertIn(model, grouped["video"])
        self.assertIn("gpt-image-2", grouped["image"])
        self.assertIn("gpt-5.5", grouped["chat"])
        self.assertIn("mystery-model", grouped["unknown"])
        self.assertIn("name-looks-like-video", grouped["unknown"])
        self.assertNotIn("name-looks-like-video", grouped["video"])

    def test_unknown_capability_is_not_guessed_as_video(self):
        self.assertEqual(codelba.classify_codelba_model_entry({}, "sd-2-c5"), "unknown")
        self.assertEqual(codelba.classify_codelba_model_entry(None, "seedance2.0-14s"), "unknown")
        self.assertEqual(codelba.classify_codelba_model_entry({"id": "video-looking-model"}, "video-looking-model"), "unknown")
        self.assertEqual(codelba.classify_codelba_model_entry({"id": "sd2.5", "name": "SD2.5"}, "sd2.5"), "unknown")

    def test_openapi_capability_schema_is_video_without_name_guessing(self):
        self.assertEqual(codelba.classify_codelba_model_entry(CODELBA_OPENAPI_SD25, "sd2.5"), "video")
        self.assertEqual(codelba.classify_codelba_model_entry(CODELBA_OPENAPI_SEEDANCE_FAST, "seedance-2.0-fast-720p"), "video")


class CodelbaFamilyTests(unittest.TestCase):
    def test_family_registry_rejects_undocumented_models_without_catalog(self):
        self.assertEqual(codelba.codelba_model_family("sd-2-c5"), codelba.CODELBA_FAMILY_SD_2_C5)
        self.assertEqual(codelba.codelba_model_family("sd-2-c5-10"), codelba.CODELBA_FAMILY_SD_2_C5_10)
        self.assertEqual(codelba.codelba_model_family("seedance2.0-14s"), codelba.CODELBA_FAMILY_SEEDANCE_2_14S)
        for model in ("sd2.5", "sd2-c5", "sd-2-c7", "seedance-2.0", "seedance-2.0-fast-720p"):
            self.assertEqual(codelba.codelba_model_family(model), "")
            with self.assertRaises(HTTPException) as ctx:
                run(codelba.build_codelba_video_request(video_payload(model=model), model, fake_resolve))
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("能力字段", ctx.exception.detail)


class CodelbaCatalogTests(unittest.TestCase):
    def test_catalog_allows_renamed_models_with_capability_fields(self):
        catalog = codelba.parse_codelba_catalog({"object": "list", "data": [CODELBA_OPENAPI_SD25, CODELBA_OPENAPI_SEEDANCE_FAST]})
        self.assertTrue(catalog.knows("sd2.5"))
        self.assertTrue(catalog.knows("seedance-2.0-fast-720p"))
        self.assertFalse(catalog.knows("brand-new-video"))

        body = run(
            codelba.build_codelba_video_request(
                video_payload(
                    model="sd2.5",
                    duration=10,
                    aspect_ratio="16:9",
                    images=[{"url": "https://example.com/reference-1.jpg"}],
                    videos=["https://example.com/motion.mp4"],
                    audios=["https://example.com/voice.mp3"],
                ),
                "sd2.5",
                fake_resolve,
                catalog=catalog,
            )
        )
        self.assertEqual(body["model"], "sd2.5")
        self.assertEqual(body["duration"], 10)
        self.assertEqual(body["size"], "1280x720")
        self.assertEqual(body["image_refs"], ["https://cdn.example/图片-1.media"])
        self.assertEqual(body["video_refs"], ["https://cdn.example/视频-1.media"])
        self.assertEqual(body["audio_refs"], ["https://cdn.example/音频-1.media"])
        self.assertNotIn("compliance_enabled", body)
        self.assertNotIn("aspect_ratio", body)
        self.assertNotIn("resolution", body)

        fast = run(
            codelba.build_codelba_video_request(
                video_payload(model="seedance-2.0-fast-720p", duration=5, aspect_ratio="1:1"),
                "seedance-2.0-fast-720p",
                fake_resolve,
                catalog=catalog,
            )
        )
        self.assertEqual(fast["model"], "seedance-2.0-fast-720p")
        self.assertEqual(fast["size"], "720x720")

    def test_catalog_incomplete_entry_does_not_guess_another_family(self):
        catalog = codelba.parse_codelba_catalog({"data": [{"id": "sd2.5", "name": "SD2.5"}]})
        self.assertTrue(catalog.knows("sd2.5"))
        self.assertIsNone(catalog.spec_for("sd2.5"))
        with self.assertRaises(HTTPException) as ctx:
            run(codelba.build_codelba_video_request(video_payload(model="sd2.5"), "sd2.5", fake_resolve, catalog=catalog))
        self.assertIn("能力字段", ctx.exception.detail)

    def test_catalog_missing_ref_limits_rejects_references(self):
        catalog = codelba.parse_codelba_catalog({
            "data": [{
                "id": "sd2.5",
                "durations": [5, 10],
                "aspect_ratios": ["16:9"],
            }]
        })
        body = run(codelba.build_codelba_video_request(video_payload(model="sd2.5", duration=5), "sd2.5", fake_resolve, catalog=catalog))
        self.assertEqual(body["model"], "sd2.5")
        self.assertNotIn("image_refs", body)
        with self.assertRaises(HTTPException) as ctx:
            run(
                codelba.build_codelba_video_request(
                    video_payload(model="sd2.5", images=[{"url": "https://example.com/a.jpg"}]),
                    "sd2.5",
                    fake_resolve,
                    catalog=catalog,
                )
            )
        self.assertIn("不支持图片参考", ctx.exception.detail)

    def test_catalog_compliance_is_omitted_unless_enabled(self):
        catalog = codelba.parse_codelba_catalog({"data": [CODELBA_OPENAPI_SD25]})
        body = run(codelba.build_codelba_video_request(video_payload(model="sd2.5"), "sd2.5", fake_resolve, catalog=catalog))
        self.assertNotIn("compliance_enabled", body)
        enabled = run(
            codelba.build_codelba_video_request(
                video_payload(model="sd2.5", compliance_enabled=True),
                "sd2.5",
                fake_resolve,
                catalog=catalog,
            )
        )
        self.assertEqual(enabled["compliance_enabled"], True)
        self.assertEqual(enabled["compliance_mode"], "fishnet")

    def test_catalog_overrides_legacy_family_when_both_exist(self):
        catalog = codelba.parse_codelba_catalog({
            "data": [{
                "id": "sd-2-c5",
                "durations": [5],
                "aspect_ratios": ["16:9"],
                "max_image_refs": 1,
                "max_video_refs": 0,
                "max_audio_refs": 0,
            }]
        })
        with self.assertRaises(HTTPException) as ctx:
            run(codelba.build_codelba_video_request(video_payload(model="sd-2-c5", duration=10), "sd-2-c5", fake_resolve, catalog=catalog))
        self.assertIn("只支持时长", ctx.exception.detail)


class CodelbaRequestTests(unittest.TestCase):
    def test_sd2_c5_uses_pixel_size_and_snake_case_refs(self):
        payload = video_payload(
            model="sd-2-c5",
            duration=15,
            aspect_ratio="9:16",
            images=[{"url": "https://example.com/reference-1.jpg"}],
            videos=["https://example.com/motion.mp4"],
            audios=["https://example.com/voice.mp3"],
        )
        body = run(codelba.build_codelba_video_request(payload, "sd-2-c5", fake_resolve))

        self.assertEqual(
            body,
            {
                "model": "sd-2-c5",
                "prompt": "人物站在海边，海风吹动衣角，镜头缓慢向前推进，电影质感。",
                "duration": 15,
                "size": "720x1280",
                "image_refs": ["https://cdn.example/图片-1.media"],
                "video_refs": ["https://cdn.example/视频-1.media"],
                "audio_refs": ["https://cdn.example/音频-1.media"],
            },
        )
        for wrong_key in (
            "aspect_ratio",
            "ratio",
            "resolution",
            "reference_image_urls",
            "referenceImages",
            "images",
            "videos",
            "audios",
            "generate_audio",
            "compliance_enabled",
        ):
            self.assertNotIn(wrong_key, body)

    def test_empty_reference_arrays_are_omitted(self):
        body = run(codelba.build_codelba_video_request(video_payload(), "sd-2-c5", fake_resolve))
        for key in ("image_refs", "video_refs", "audio_refs"):
            self.assertNotIn(key, body)
        self.assertEqual(body["size"], "1280x720")
        self.assertEqual(body["duration"], 10)

    def test_sd2_c5_duration_and_ratio_are_exact_enums(self):
        for duration in (5, 8, 10, 15):
            body = run(codelba.build_codelba_video_request(video_payload(duration=duration), "sd-2-c5", fake_resolve))
            self.assertEqual(body["duration"], duration)
        for kwargs, message in (
            ({"duration": 6}, "只支持时长"),
            ({"aspect_ratio": "1:1"}, "画幅"),
            ({"aspect_ratio": "21:9"}, "画幅"),
            ({"resolution": "1080p"}, "清晰度"),
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(HTTPException) as ctx:
                    run(codelba.build_codelba_video_request(video_payload(**kwargs), "sd-2-c5", fake_resolve))
                self.assertIn(message, ctx.exception.detail)

    def test_sd2_c5_maps_43_and_34_to_pixel_sizes(self):
        body_43 = run(codelba.build_codelba_video_request(video_payload(aspect_ratio="4:3"), "sd-2-c5", fake_resolve))
        body_34 = run(codelba.build_codelba_video_request(video_payload(aspect_ratio="3:4"), "sd-2-c5", fake_resolve))
        self.assertEqual(body_43["size"], "960x720")
        self.assertEqual(body_34["size"], "720x960")

    def test_sd2_c5_10_supports_11_and_rejects_15s_and_43(self):
        body = run(
            codelba.build_codelba_video_request(
                video_payload(model="sd-2-c5-10", duration=8, aspect_ratio="1:1"),
                "sd-2-c5-10",
                fake_resolve,
            )
        )
        self.assertEqual(body["model"], "sd-2-c5-10")
        self.assertEqual(body["duration"], 8)
        self.assertEqual(body["size"], "720x720")
        with self.assertRaises(HTTPException) as ctx:
            run(codelba.build_codelba_video_request(video_payload(model="sd-2-c5-10", duration=15), "sd-2-c5-10", fake_resolve))
        self.assertIn("只支持时长", ctx.exception.detail)
        with self.assertRaises(HTTPException) as ctx:
            run(codelba.build_codelba_video_request(video_payload(model="sd-2-c5-10", aspect_ratio="4:3"), "sd-2-c5-10", fake_resolve))
        self.assertIn("画幅", ctx.exception.detail)

    def test_seedance_14s_rejects_video_and_audio_refs(self):
        body = run(
            codelba.build_codelba_video_request(
                video_payload(
                    model="seedance2.0-14s",
                    duration=15,
                    aspect_ratio="9:16",
                    images=[{"url": "https://example.com/reference-1.jpg"}],
                ),
                "seedance2.0-14s",
                fake_resolve,
            )
        )
        self.assertEqual(
            body,
            {
                "model": "seedance2.0-14s",
                "prompt": "人物站在海边，海风吹动衣角，镜头缓慢向前推进，电影质感。",
                "duration": 15,
                "size": "720x1280",
                "image_refs": ["https://cdn.example/图片-1.media"],
            },
        )
        self.assertNotIn("video_refs", body)
        self.assertNotIn("audio_refs", body)
        with self.assertRaises(HTTPException) as ctx:
            run(
                codelba.build_codelba_video_request(
                    video_payload(model="seedance2.0-14s", videos=["https://example.com/a.mp4"]),
                    "seedance2.0-14s",
                    fake_resolve,
                )
            )
        self.assertIn("参考视频", ctx.exception.detail)
        with self.assertRaises(HTTPException) as ctx:
            run(
                codelba.build_codelba_video_request(
                    video_payload(model="seedance2.0-14s", audios=["https://example.com/a.mp3"]),
                    "seedance2.0-14s",
                    fake_resolve,
                )
            )
        self.assertIn("参考音频", ctx.exception.detail)

    def test_seedance_14s_accepts_duration_range_and_rejects_outside(self):
        body = run(
            codelba.build_codelba_video_request(
                video_payload(model="seedance2.0-14s", duration=6),
                "seedance2.0-14s",
                fake_resolve,
            )
        )
        self.assertEqual(body["duration"], 6)
        with self.assertRaises(HTTPException) as ctx:
            run(codelba.build_codelba_video_request(video_payload(model="seedance2.0-14s", duration=4), "seedance2.0-14s", fake_resolve))
        self.assertIn("5-15", ctx.exception.detail)

    def test_pixel_size_passthrough_and_audio_requires_image_or_video(self):
        body = run(
            codelba.build_codelba_video_request(
                video_payload(size="1280x720", aspect_ratio="9:16"),
                "sd-2-c5",
                fake_resolve,
            )
        )
        self.assertEqual(body["size"], "1280x720")
        with self.assertRaises(HTTPException) as ctx:
            run(
                codelba.build_codelba_video_request(
                    video_payload(audios=["https://example.com/a.mp3"]),
                    "sd-2-c5",
                    fake_resolve,
                )
            )
        self.assertIn("必须同时传图片或视频", ctx.exception.detail)

    def test_legacy_720p_size_alias_maps_16_9_to_1280x720(self):
        body = run(
            codelba.build_codelba_video_request(
                video_payload(size="720p", aspect_ratio="16:9"),
                "sd-2-c5",
                fake_resolve,
            )
        )
        self.assertEqual(body["size"], "1280x720")

    def test_reference_limits_are_rejected_not_truncated(self):
        cases = (
            ("images", 10, "9"),
            ("videos", 4, "3"),
            ("audios", 4, "3"),
        )
        for field, count, limit in cases:
            with self.subTest(field=field):
                values = (
                    [{"url": f"https://example.com/{index}.media"} for index in range(count)]
                    if field == "images"
                    else [f"https://example.com/{index}.media" for index in range(count)]
                )
                kwargs = {field: values}
                if field == "audios":
                    kwargs["images"] = [{"url": "https://example.com/cover.jpg"}]
                with self.assertRaises(HTTPException) as ctx:
                    run(codelba.build_codelba_video_request(video_payload(**kwargs), "sd-2-c5", fake_resolve))
                self.assertIn(limit, ctx.exception.detail)

    def test_first_last_frames_generate_audio_and_compliance_are_rejected(self):
        cases = (
            {"images": [{"url": "https://example.com/first.jpg", "role": "first_frame"}]},
            {"generate_audio": True},
            {"compliance_enabled": True},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(HTTPException) as ctx:
                    run(codelba.build_codelba_video_request(video_payload(**kwargs), "sd-2-c5", fake_resolve))
                self.assertEqual(ctx.exception.status_code, 400)

    def test_non_public_reference_is_rejected(self):
        payload = video_payload(images=[{"url": "/assets/local.png"}])
        with self.assertRaises(HTTPException) as ctx:
            run(codelba.build_codelba_video_request(payload, "sd-2-c5"))
        self.assertIn("公网", ctx.exception.detail)


class CodelbaResponseTests(unittest.TestCase):
    def test_task_id_prefers_documented_id_field(self):
        self.assertEqual(codelba.codelba_task_id({"id": "video-168", "task_id": "other"}), "video-168")
        self.assertEqual(codelba.codelba_task_id({"data": {"id": "video-nested"}}), "video-nested")
        self.assertEqual(codelba.codelba_task_state({"status": "queued"})[0], "pending")
        self.assertEqual(codelba.codelba_task_state({"status": "in_progress"})[0], "pending")
        self.assertEqual(codelba.codelba_task_state({"status": "completed"})[0], "success")
        self.assertEqual(codelba.codelba_task_state({"status": "failed"})[0], "failed")

    def test_error_code_and_message_are_preserved(self):
        self.assertEqual(
            codelba.codelba_error_text({"error": {"code": "generation_failed", "message": "参考素材包含真人脸部"}}),
            "generation_failed: 参考素材包含真人脸部",
        )


class _FakeResponse:
    def __init__(self, status_code, text, payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _FakeClient:
    def __init__(self, response):
        self.response = response
        self.urls = []

    async def get(self, url, headers=None, timeout=None):
        del timeout
        self.urls.append(url)
        return self.response


class CodelbaProbeTests(unittest.TestCase):
    def test_gateway_502_html_is_not_reported_as_wrong_base_url(self):
        client = _FakeClient(_FakeResponse(
            502,
            "<html>\n<head><title>502 Bad Gateway</title></head>\n<body><h1>502 Bad Gateway</h1></body></html>",
        ))
        result = run(main.probe_codelba_endpoint(client, "https://hz.codelba.cn/ai_video_ui/", "sk-test"))

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 502)
        self.assertEqual(client.urls, ["https://hz.codelba.cn/openapi/v1/models"])
        self.assertIn("网关不可用", result["message"])
        self.assertIn("/openapi/v1", result["message"])
        self.assertIn("不是请求地址填错", result["message"])
        self.assertNotIn("返回网页 HTML", result["message"])
        self.assertNotIn("/v1/models", result["message"])

    def test_load_catalog_parses_openapi_capability_fields(self):
        main._CODELBA_CATALOG_CACHE.clear()
        previous = os.environ.get("API_PROVIDER_CODELBA_KEY")
        os.environ["API_PROVIDER_CODELBA_KEY"] = "sk-test-catalog"
        client = _FakeClient(_FakeResponse(200, "{}", {"object": "list", "data": [CODELBA_OPENAPI_SD25]}))
        try:
            catalog = run(main.load_codelba_catalog(client, {"id": "codelba"}, "https://hz.codelba.cn"))
        finally:
            if previous is None:
                os.environ.pop("API_PROVIDER_CODELBA_KEY", None)
            else:
                os.environ["API_PROVIDER_CODELBA_KEY"] = previous
        self.assertTrue(catalog.knows("sd2.5"))
        self.assertEqual(catalog.spec_for("sd2.5")["max_images"], 9)
        self.assertEqual(client.urls, ["https://hz.codelba.cn/openapi/v1/models"])


if __name__ == "__main__":
    unittest.main()
