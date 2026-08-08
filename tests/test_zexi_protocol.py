import asyncio
import unittest

import httpx
from fastapi import HTTPException

import main
import zexi_protocol as zexi


def run(coro):
    return asyncio.run(coro)


async def fake_resolve(value, kind, index):
    """单测不触网：素材归一直接回传原值，便于断言字段映射。"""
    return str(value or "").strip()


# 取自 zexitongxue.com 的 GET /v1/models 真实响应片段（只保留断言用得到的条目）。
# 站点只给 id + supported_endpoint_types，没有 type / category / capabilities。
ZEXI_MODELS_RESPONSE = {
    "data": [
        {"id": "seedance-2.5", "object": "model", "supported_endpoint_types": ["openai-video"]},
        {"id": "grok", "object": "model", "supported_endpoint_types": ["openai-video"]},
        {"id": "minimax-h3", "object": "model", "supported_endpoint_types": ["openai-video"]},
        {"id": "dolo-2", "object": "model", "supported_endpoint_types": ["openai-video"]},
        {"id": "seedance-standard-720p", "object": "model", "supported_endpoint_types": ["openai-video"]},
        {
            "id": "gpt-image-2",
            "object": "model",
            "supported_endpoint_types": ["image-generation", "image-generation-async", "image-task-query"],
        },
        {
            "id": "gemini-3-pro-image-preview",
            "object": "model",
            "supported_endpoint_types": ["image-generation", "image-generation-async", "image-task-query"],
        },
        {"id": "kimi-k2.6", "object": "model", "supported_endpoint_types": ["openai"]},
        {"id": "claude-sonnet-5", "object": "model", "supported_endpoint_types": ["anthropic", "openai"]},
        {"id": "sora2", "object": "model", "supported_endpoint_types": ["openai"]},
    ]
}

# 取自 GET /ai-api/models?type=video 真实响应片段（匿名请求才拿得到）。
ZEXI_CATALOG_RESPONSE = {
    "success": True,
    "models": [
        {
            "id": "seedance-2.5",
            "type": "video",
            "price": 0.75,
            "billing_unit": "second",
            "resolution_prices": {"480p": 0.75, "720p": 1.35},
            "can_use": True,
            "availability_label": "可用",
            "max_reference_images": 30,
            "duration_profile": {"values": list(range(4, 31)), "default": 4, "fixed": False},
            "duration_rules": {},
            "resolution_profile": {"values": ["480p", "720p"], "default": "480p", "fixed": False},
        },
        {
            "id": "grok",
            "type": "video",
            "price": 0.8,
            "billing_unit": "request",
            "can_use": True,
            "availability_label": "可用",
            "max_reference_images": 9,
            "duration_profile": {"values": [6, 10, 15], "default": 6, "fixed": False},
            "duration_rules": {"text": [6, 10, 15], "single_image": [6, 10, 15], "multi_image": [6, 10]},
            "resolution_profile": {"values": ["720p"], "default": "720p", "fixed": True},
        },
        {
            "id": "minimax-h3",
            "type": "video",
            "price": 1.5,
            "billing_unit": "request",
            "can_use": True,
            "availability_label": "可用",
            "max_reference_images": 9,
            "duration_profile": {"values": list(range(4, 16)), "default": 4, "fixed": False},
            "duration_rules": {},
            "resolution_profile": {"values": ["720p"], "default": "720p", "fixed": True},
        },
        {
            "id": "doubao-seedance-2-0-720p",
            "type": "video",
            "price": 36.8,
            "billing_unit": "token",
            "can_use": True,
            "availability_label": "可用",
            "max_reference_images": 9,
            "duration_profile": {"values": list(range(4, 16)), "default": 5, "fixed": False},
            "duration_rules": {},
            "resolution_profile": {"values": ["720p"], "default": "720p", "fixed": True},
        },
        {
            "id": "seedance-2.0-720p-pro-431",
            "type": "video",
            "price": 4.5,
            "billing_unit": "request",
            "can_use": True,
            "availability_label": "可用",
            # 站点目录明确声明这个模型不吃参考图；传了会被静默忽略并照常扣费。
            "max_reference_images": 0,
            "duration_profile": {"values": list(range(4, 16)), "default": 5, "fixed": False},
            "duration_rules": {},
            "resolution_profile": {"values": ["720p"], "default": "720p", "fixed": True},
        },
        {
            "id": "doubao-seedance-2-0-4k",
            "type": "video",
            "price": 39.1,
            "billing_unit": "token",
            "can_use": False,
            "availability_label": "维护中",
            "max_reference_images": 9,
            "duration_profile": {"values": list(range(5, 16)), "default": 5, "fixed": False},
            "duration_rules": {},
            "resolution_profile": {"values": ["4k"], "default": "4k", "fixed": True},
        },
    ],
}


def catalog():
    return zexi.parse_zexi_catalog(ZEXI_CATALOG_RESPONSE)


def video_payload(**kwargs):
    base = {"prompt": "清晨海边公路，镜头平稳推进", "model": "seedance-2.5"}
    base.update(kwargs)
    return main.CanvasVideoRequest(**base)


class ZexiRoutingTests(unittest.TestCase):
    def test_protocol_is_registered_and_routes_to_its_own_contract(self):
        provider = {"id": "zexi", "protocol": main.ZEXI_PROTOCOL}

        self.assertIn(main.ZEXI_PROTOCOL, main.SUPPORTED_PROVIDER_PROTOCOLS)
        self.assertTrue(main.is_zexi_provider(provider))
        self.assertTrue(main.is_zexi_route(provider, "seedance-2.5"))
        # 与同样使用 /v1/videos 的既有协议互不串线
        self.assertFalse(main.is_cangyuan_video_route(provider, "seedance-2.5"))
        self.assertFalse(main.is_chre3_video_route(provider, "seedance-2.5"))

    def test_endpoint_urls(self):
        provider = {"id": "zexi", "protocol": main.ZEXI_PROTOCOL}
        self.assertEqual(
            main.video_submit_url_candidates(provider, "https://zexitongxue.com", "grok"),
            ["https://zexitongxue.com/v1/videos"],
        )
        self.assertEqual(
            main.video_task_url_candidates(provider, "https://zexitongxue.com", "task_42", "", "grok"),
            ["https://zexitongxue.com/v1/videos/task_42"],
        )
        # 用户把 Base URL 填成带 /v1 的形态时不能拼成 /v1/v1
        self.assertEqual(
            main.video_submit_url_candidates(provider, "https://zexitongxue.com/v1", "grok"),
            ["https://zexitongxue.com/v1/videos"],
        )
        self.assertEqual(
            zexi.zexi_image_submit_url("https://zexitongxue.com/v1"),
            "https://zexitongxue.com/v1/images/generations/async",
        )
        self.assertEqual(
            zexi.zexi_image_task_url("https://zexitongxue.com", "aiimg_1"),
            "https://zexitongxue.com/v1/images/tasks/aiimg_1",
        )
        self.assertEqual(
            zexi.zexi_image_content_url("https://zexitongxue.com", "aiimg_1", 2),
            "https://zexitongxue.com/v1/images/tasks/aiimg_1/content?index=2",
        )
        self.assertEqual(
            zexi.zexi_video_content_url("https://zexitongxue.com", "task_42"),
            "https://zexitongxue.com/v1/videos/task_42/content",
        )

    def test_models_are_classified_from_supported_endpoint_types_not_names(self):
        grouped, ids = main.parse_upstream_models(ZEXI_MODELS_RESPONSE, main.ZEXI_PROTOCOL)

        self.assertEqual(len(ids), 10)
        # 名称兜底认不出这些视频模型（grok / dolo-2 / minimax-h3 都不含 video 关键词）
        for model in ("seedance-2.5", "grok", "minimax-h3", "dolo-2", "seedance-standard-720p"):
            self.assertIn(model, grouped["video"], model)
        self.assertIn("gpt-image-2", grouped["image"])
        self.assertIn("gemini-3-pro-image-preview", grouped["image"])
        self.assertIn("kimi-k2.6", grouped["chat"])
        self.assertIn("claude-sonnet-5", grouped["chat"])
        # sora2 在本站登记的是 openai 端点而不是 openai-video：按上游表态归 chat，不按名字猜成视频
        self.assertIn("sora2", grouped["chat"])
        self.assertNotIn("sora2", grouped["video"])

    def test_unknown_endpoint_types_fall_back_to_chat_not_video(self):
        # 上游不给能力字段时按未知处理，不猜成视频或图片
        self.assertEqual(zexi.classify_zexi_model_entry({"id": "mystery"}, "mystery"), "chat")
        self.assertEqual(zexi.classify_zexi_model_entry(None, "seedance-9.9"), "chat")

    def test_catalog_url_is_built_without_any_auth_parameter(self):
        # 该端点带 Authorization 会被站点打成 400，URL 构造里不能夹带任何凭据
        url = zexi.zexi_catalog_url("https://zexitongxue.com/v1", "video")
        self.assertEqual(url, "https://zexitongxue.com/ai-api/models?type=video")
        self.assertNotIn("key", url.lower())
        self.assertNotIn("token", url.lower())


class ZexiCatalogTests(unittest.TestCase):
    def test_capabilities_are_read_from_the_catalog(self):
        cat = catalog()
        self.assertEqual(cat.durations("grok"), [6, 10, 15])
        self.assertEqual(cat.default_duration("seedance-2.5"), 4)
        self.assertEqual(cat.resolutions("seedance-2.5"), ["480p", "720p"])
        self.assertTrue(cat.resolution_fixed("doubao-seedance-2-0-720p"))
        self.assertFalse(cat.resolution_fixed("seedance-2.5"))
        self.assertEqual(cat.max_images("seedance-2.5"), 30)
        self.assertEqual(cat.max_images("seedance-2.0-720p-pro-431"), 0)
        self.assertEqual(
            cat.duration_rules("grok"),
            {"text": [6, 10, 15], "single_image": [6, 10, 15], "multi_image": [6, 10]},
        )

    def test_empty_catalog_does_not_break_request_building(self):
        # 目录不可达时回退到家族默认值，不阻断生成
        body = run(zexi.build_zexi_video_request(
            video_payload(model="seedance-2.5", duration=6),
            "seedance-2.5",
            catalog=zexi.ZexiCatalog(),
            resolve_ref=fake_resolve,
        ))
        self.assertEqual(body["model"], "seedance-2.5")
        self.assertEqual(body["seconds"], 6)

    def test_models_marked_unusable_are_refused_before_any_paid_call(self):
        with self.assertRaises(HTTPException) as ctx:
            run(zexi.build_zexi_video_request(
                video_payload(model="doubao-seedance-2-0-4k"),
                "doubao-seedance-2-0-4k",
                catalog=catalog(),
                resolve_ref=fake_resolve,
            ))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("维护中", ctx.exception.detail)


class ZexiVideoFamilyTests(unittest.TestCase):
    def test_family_registry(self):
        self.assertEqual(zexi.zexi_video_family("seedance-2.5"), zexi.ZEXI_FAMILY_SEEDANCE_25)
        self.assertEqual(zexi.zexi_video_family("grok"), zexi.ZEXI_FAMILY_GROK)
        self.assertEqual(zexi.zexi_video_family("minimax-h3"), zexi.ZEXI_FAMILY_MINIMAX_H3)
        self.assertEqual(zexi.zexi_video_family("doubao-seedance-2-0-720p"), zexi.ZEXI_FAMILY_SEEDANCE_FLAT)
        self.assertEqual(zexi.zexi_video_family("seedance-standard-720p"), zexi.ZEXI_FAMILY_SEEDANCE_FLAT)
        self.assertEqual(zexi.zexi_video_family("dolo-2"), zexi.ZEXI_FAMILY_SEEDANCE_FLAT)
        # 未登记 id 落到通用扁平族，但仍会走上限校验
        self.assertEqual(zexi.zexi_video_family("brand-new-model"), zexi.ZEXI_FAMILY_SEEDANCE_FLAT)

    def test_seedance_25_uses_its_own_field_names(self):
        payload = video_payload(
            model="seedance-2.5",
            duration=6,
            resolution="720p",
            aspect_ratio="1:1",
            images=[{"url": "https://example.com/a.jpg"}],
            videos=["https://example.com/m.mp4"],
            audios=["https://example.com/s.mp3"],
        )
        body = run(zexi.build_zexi_video_request(payload, "seedance-2.5", catalog=catalog(), resolve_ref=fake_resolve))

        self.assertEqual(body["seconds"], 6)
        self.assertEqual(body["resolution"], "720p")
        self.assertEqual(body["aspect_ratio"], "1:1")
        self.assertEqual(body["images"], ["https://example.com/a.jpg"])
        self.assertEqual(body["videos"], ["https://example.com/m.mp4"])
        self.assertEqual(body["audios"], ["https://example.com/s.mp3"])
        # 2.5 用 seconds 而不是 duration；混用会被上游按缺省 4 秒处理
        self.assertNotIn("duration", body)
        # 与 cangyuan 的字段名必须区分开，否则参考素材会被静默忽略
        self.assertNotIn("reference_image_urls", body)
        self.assertNotIn("reference_videos", body)
        self.assertNotIn("reference_audios", body)

    def test_seedance_25_has_no_first_last_frame_mode(self):
        payload = video_payload(
            model="seedance-2.5",
            images=[
                {"url": "https://example.com/first.jpg", "role": "first_frame"},
                {"url": "https://example.com/last.jpg", "role": "last_frame"},
            ],
        )
        body = run(zexi.build_zexi_video_request(payload, "seedance-2.5", catalog=catalog(), resolve_ref=fake_resolve))

        self.assertNotIn("first_frame", body)
        self.assertNotIn("last_frame", body)
        self.assertEqual(body["images"], ["https://example.com/first.jpg", "https://example.com/last.jpg"])

    def test_seedance_flat_uses_duration_and_frame_fields(self):
        payload = video_payload(
            model="doubao-seedance-2-0-720p",
            duration=10,
            aspect_ratio="9:16",
            images=[
                {"url": "https://example.com/first.jpg", "role": "first_frame"},
                {"url": "https://example.com/last.jpg", "role": "last_frame"},
            ],
        )
        body = run(zexi.build_zexi_video_request(
            payload, "doubao-seedance-2-0-720p", catalog=catalog(), resolve_ref=fake_resolve
        ))

        self.assertEqual(body["duration"], 10)
        self.assertNotIn("seconds", body)
        # 首尾帧字段名与 cangyuan 的 first_image_url / last_image_url 不同
        self.assertEqual(body["first_frame"], "https://example.com/first.jpg")
        self.assertEqual(body["last_frame"], "https://example.com/last.jpg")
        self.assertNotIn("first_image_url", body)
        self.assertNotIn("images", body)

    def test_flat_family_omits_resolution_when_the_model_id_fixes_it(self):
        payload = video_payload(model="doubao-seedance-2-0-720p", resolution="480p")
        body = run(zexi.build_zexi_video_request(
            payload, "doubao-seedance-2-0-720p", catalog=catalog(), resolve_ref=fake_resolve
        ))
        self.assertNotIn("resolution", body)

    def test_flat_family_multi_material_fields(self):
        payload = video_payload(
            model="doubao-seedance-2-0-720p",
            images=[{"url": "https://example.com/a.jpg"}, {"url": "https://example.com/b.jpg"}],
            videos=["https://example.com/m.mp4"],
            audios=["https://example.com/s.mp3"],
        )
        body = run(zexi.build_zexi_video_request(
            payload, "doubao-seedance-2-0-720p", catalog=catalog(), resolve_ref=fake_resolve
        ))
        self.assertEqual(body["images"], ["https://example.com/a.jpg", "https://example.com/b.jpg"])
        self.assertEqual(body["reference_videos"], ["https://example.com/m.mp4"])
        self.assertEqual(body["audio_url"], "https://example.com/s.mp3")

    def test_single_reference_uses_image_url_and_multi_uses_images(self):
        """站点对单图 / 多图用的是两套字段，混用会被静默忽略。

        站点文档（grok-api「请求字段」与 video-api「素材与兼容字段」）：
            单图：image_url、image、input_reference
            多图：images、image_urls、reference_image_urls、reference_images

        线上实测教训：1 张参考图塞进 images 数组，上游照常出片、照常扣费，
        参考图零作用且不报错——本项目规则里点名最贵的失败模式。
        """
        one = video_payload(model="grok", duration=6, images=[{"url": "https://example.com/a.jpg"}])
        body = run(zexi.build_zexi_video_request(one, "grok", catalog=catalog(), resolve_ref=fake_resolve))
        self.assertEqual(body["image_url"], "https://example.com/a.jpg")
        self.assertNotIn("images", body)

        two = video_payload(
            model="grok", duration=6,
            images=[{"url": "https://example.com/a.jpg"}, {"url": "https://example.com/b.jpg"}],
        )
        body2 = run(zexi.build_zexi_video_request(two, "grok", catalog=catalog(), resolve_ref=fake_resolve))
        self.assertEqual(body2["images"], ["https://example.com/a.jpg", "https://example.com/b.jpg"])
        self.assertNotIn("image_url", body2)

    def test_seedance_25_always_uses_the_images_array(self):
        """seedance-2.5 是例外：站点文档明确要求统一用 images，单图也不例外。"""
        payload = video_payload(model="seedance-2.5", images=[{"url": "https://example.com/a.jpg"}])
        body = run(zexi.build_zexi_video_request(payload, "seedance-2.5", catalog=catalog(), resolve_ref=fake_resolve))
        self.assertEqual(body["images"], ["https://example.com/a.jpg"])
        self.assertNotIn("image_url", body)

    def test_grok_multi_image_caps_duration_at_ten_seconds(self):
        payload = video_payload(
            model="grok",
            duration=15,
            images=[{"url": "https://example.com/a.jpg"}, {"url": "https://example.com/b.jpg"}],
        )
        body = run(zexi.build_zexi_video_request(payload, "grok", catalog=catalog(), resolve_ref=fake_resolve))

        # 多图 + 15 秒会被站点直接判参数错误，本地必须先落到 10
        self.assertEqual(body["duration"], 10)
        self.assertEqual(body["ratio"], "16:9")
        self.assertEqual(body["resolution"], "720p")
        self.assertNotIn("aspect_ratio", body)
        self.assertNotIn("seconds", body)

    def test_grok_single_image_keeps_fifteen_seconds(self):
        payload = video_payload(model="grok", duration=15, images=[{"url": "https://example.com/a.jpg"}])
        body = run(zexi.build_zexi_video_request(payload, "grok", catalog=catalog(), resolve_ref=fake_resolve))
        self.assertEqual(body["duration"], 15)

    def test_grok_illegal_duration_snaps_to_an_allowed_value(self):
        payload = video_payload(model="grok", duration=8)
        body = run(zexi.build_zexi_video_request(payload, "grok", catalog=catalog(), resolve_ref=fake_resolve))
        self.assertIn(body["duration"], (6, 10, 15))
        self.assertEqual(body["duration"], 6)

    def test_minimax_h3_never_emits_a_video_field(self):
        payload = video_payload(
            model="minimax-h3",
            images=[{"url": "https://example.com/a.jpg"}],
            audios=["https://example.com/s.mp3"],
        )
        body = run(zexi.build_zexi_video_request(payload, "minimax-h3", catalog=catalog(), resolve_ref=fake_resolve))

        # H3 专属文档只背书 images 数组（"素材兼容能力以具体模型说明为准"），单图也走 images
        self.assertEqual(body["images"], ["https://example.com/a.jpg"])
        self.assertNotIn("image_url", body)
        self.assertEqual(body["audios"], ["https://example.com/s.mp3"])
        for key in ("videos", "reference_video", "reference_videos", "video_urls"):
            self.assertNotIn(key, body)


class ZexiSilentDegradationGuardTests(unittest.TestCase):
    """素材不被支持时必须显式失败——静默丢弃会照常出片并扣费。"""

    def test_video_reference_on_grok_is_rejected(self):
        payload = video_payload(model="grok", videos=["https://example.com/m.mp4"])
        with self.assertRaises(HTTPException) as ctx:
            run(zexi.build_zexi_video_request(payload, "grok", catalog=catalog(), resolve_ref=fake_resolve))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("不支持", ctx.exception.detail)

    def test_video_reference_on_minimax_h3_is_rejected(self):
        payload = video_payload(model="minimax-h3", videos=["https://example.com/m.mp4"])
        with self.assertRaises(HTTPException) as ctx:
            run(zexi.build_zexi_video_request(payload, "minimax-h3", catalog=catalog(), resolve_ref=fake_resolve))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_audio_reference_on_grok_is_rejected(self):
        payload = video_payload(model="grok", audios=["https://example.com/s.mp3"])
        with self.assertRaises(HTTPException):
            run(zexi.build_zexi_video_request(payload, "grok", catalog=catalog(), resolve_ref=fake_resolve))

    def test_reference_image_on_a_zero_ref_model_is_rejected(self):
        # 目录声明 max_reference_images=0：传图会被静默忽略并按 4.5 元/次照常扣费
        payload = video_payload(
            model="seedance-2.0-720p-pro-431",
            images=[{"url": "https://example.com/a.jpg"}],
        )
        with self.assertRaises(HTTPException) as ctx:
            run(zexi.build_zexi_video_request(
                payload, "seedance-2.0-720p-pro-431", catalog=catalog(), resolve_ref=fake_resolve
            ))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_first_last_frames_also_go_through_the_reference_limit(self):
        """首尾帧曾经绕过上限校验：flat 家族的首尾帧分支在取上限之前就 return 了。

        后果是 max_reference_images=0 的模型照样收到 first_frame / last_frame，
        上游静默忽略、照常出纯文生片、照常按 4.5 元/次扣费。
        """
        payload = video_payload(
            model="seedance-2.0-720p-pro-431",
            images=[
                {"url": "https://example.com/first.jpg", "role": "first_frame"},
                {"url": "https://example.com/last.jpg", "role": "last_frame"},
            ],
        )
        with self.assertRaises(HTTPException) as ctx:
            run(zexi.build_zexi_video_request(
                payload, "seedance-2.0-720p-pro-431", catalog=catalog(), resolve_ref=fake_resolve
            ))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_frame_mode_does_not_silently_drop_extra_reference_images(self):
        # 首尾帧与多图参考互斥，但多余的图不能悄悄不发——上游照样出片扣费，用户看不出来
        payload = video_payload(
            model="doubao-seedance-2-0-720p",
            images=[
                {"url": "https://example.com/first.jpg", "role": "first_frame"},
                {"url": "https://example.com/last.jpg", "role": "last_frame"},
                {"url": "https://example.com/extra.jpg"},
            ],
        )
        with self.assertRaises(HTTPException) as ctx:
            run(zexi.build_zexi_video_request(
                payload, "doubao-seedance-2-0-720p", catalog=catalog(), resolve_ref=fake_resolve
            ))
        self.assertIn("互斥", ctx.exception.detail)

    def test_models_absent_from_the_catalog_are_still_submitted(self):
        """能力目录是增强，不是准入门槛。

        站点的请求合同是 POST /v1/videos + 文档字段。目录按令牌分组返回、也会漏掉
        新上架模型；目录里查不到就本地拦死，会让 /v1/models 里明明可选的模型完全
        不可用——这比"参考素材可能被上游忽略"更糟。此处按家族默认上限提交，
        素材能力交给上游判定。
        """
        for cat in (zexi.ZexiCatalog(), catalog()):
            payload = video_payload(
                model="brand-new-model",
                duration=6,
                images=[{"url": "https://example.com/a.jpg"}],
                audios=["https://example.com/s.mp3"],
            )
            body = run(zexi.build_zexi_video_request(
                payload, "brand-new-model", catalog=cat, resolve_ref=fake_resolve
            ))
            self.assertEqual(body["model"], "brand-new-model")
            self.assertEqual(body["image_url"], "https://example.com/a.jpg")
            self.assertEqual(body["audio_url"], "https://example.com/s.mp3")

    def test_catalog_still_tightens_when_it_actually_states_a_limit(self):
        """目录明确表态时仍然收紧——这才是它真正的价值。"""
        payload = video_payload(
            model="seedance-2.0-720p-pro-431",
            images=[{"url": "https://example.com/a.jpg"}],
        )
        with self.assertRaises(HTTPException):
            run(zexi.build_zexi_video_request(
                payload, "seedance-2.0-720p-pro-431", catalog=catalog(), resolve_ref=fake_resolve
            ))

    def test_catalog_knows_reports_entry_presence(self):
        self.assertTrue(catalog().knows("seedance-2.5"))
        self.assertFalse(catalog().knows("brand-new-model"))
        self.assertFalse(zexi.ZexiCatalog().knows("seedance-2.5"))

    def test_too_many_reference_images_are_rejected_not_truncated(self):
        payload = video_payload(
            model="grok",
            images=[{"url": f"https://example.com/{i}.jpg"} for i in range(12)],
        )
        with self.assertRaises(HTTPException) as ctx:
            run(zexi.build_zexi_video_request(payload, "grok", catalog=catalog(), resolve_ref=fake_resolve))
        self.assertIn("9", ctx.exception.detail)


class ZexiImageRequestTests(unittest.TestCase):
    def test_gpt_image_2_uses_pixel_size_and_openai_quality(self):
        body = zexi.build_zexi_image_request(
            "a banana poster", "gpt-image-2", size="1536x1024", quality="high", n=1
        )
        self.assertEqual(body["model"], "gpt-image-2")
        self.assertEqual(body["size"], "1536x1024")
        self.assertEqual(body["quality"], "high")
        self.assertNotIn("extra_body", body)

    def test_gpt_image_2_illegal_size_falls_back_to_auto(self):
        body = zexi.build_zexi_image_request("x", "gpt-image-2", size="9999x9999", quality="auto")
        # 站点提交时零校验，非法尺寸会照常受理并计费，所以本地必须先归一
        self.assertEqual(body["size"], "auto")

    def test_gemini_image_uses_ratio_and_image_config(self):
        body = zexi.build_zexi_image_request(
            "a poster", "gemini-3-pro-image-preview", size="1920x1080", quality="4K"
        )
        self.assertEqual(body["size"], "16:9")
        self.assertEqual(body["quality"], "4K")
        self.assertEqual(body["extra_body"]["google"]["image_config"]["image_size"], "4K")
        self.assertEqual(body["extra_body"]["google"]["image_config"]["aspect_ratio"], "16:9")

    def test_gemini_auto_quality_is_not_sent(self):
        # 本地默认值不得改变上游产出：quality=auto 时交给上游默认
        body = zexi.build_zexi_image_request("a poster", "gemini-3-pro-image-preview", size="1:1", quality="auto")
        self.assertNotIn("quality", body)
        self.assertNotIn("extra_body", body)

    def test_gemini_quality_can_come_from_the_canvas_resolution_field(self):
        # 画布的 quality 走 low/medium/high/auto，2K / 4K 只出现在 resolution；
        # 不认这一路会导致 gemini 的 2K / 4K 永远发不出去而且不报错。
        body = zexi.build_zexi_image_request(
            "a poster", "gemini-3-pro-image-preview", size="16:9", quality="auto", resolution="2K"
        )
        self.assertEqual(body["quality"], "2K")
        self.assertEqual(body["extra_body"]["google"]["image_config"]["image_size"], "2K")

    def test_gpt_image_2_ignores_the_gemini_style_resolution(self):
        body = zexi.build_zexi_image_request(
            "x", "gpt-image-2", size="1024x1024", quality="high", resolution="4K"
        )
        self.assertEqual(body["quality"], "high")
        self.assertNotIn("extra_body", body)

    def test_model_aliases_resolve_to_the_real_model(self):
        body = zexi.build_zexi_image_request("x", "nano-banana-pro", size="16:9", quality="2K")
        self.assertEqual(body["model"], "gemini-3-pro-image-preview")

    def test_single_reference_image_uses_the_documented_field(self):
        body = zexi.build_zexi_image_request(
            "keep composition", "gemini-3.1-flash-image-preview",
            size="16:9", reference_urls=["https://example.com/in.jpg"],
        )
        self.assertEqual(body["image_url"], "https://example.com/in.jpg")

    def test_multiple_reference_images_are_refused_instead_of_guessed(self):
        # 站点未公开多图字段名；猜字段的表现是照常出图、照常扣费、参考图被忽略
        with self.assertRaises(HTTPException) as ctx:
            zexi.build_zexi_image_request(
                "x", "gpt-image-2", size="1024x1024",
                reference_urls=["https://example.com/a.jpg", "https://example.com/b.jpg"],
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_size_to_ratio_conversion(self):
        self.assertEqual(zexi.zexi_size_to_ratio("1024x1024"), "1:1")
        self.assertEqual(zexi.zexi_size_to_ratio("1920x1080"), "16:9")
        self.assertEqual(zexi.zexi_size_to_ratio("1080x1920"), "9:16")
        self.assertEqual(zexi.zexi_size_to_ratio("", "21:9"), "21:9")


class ZexiResponseNormalizationTests(unittest.TestCase):
    def test_three_error_envelopes_are_all_understood(self):
        new_api = {"error": {"code": "", "message": "Invalid token", "type": "new_api_error"}}
        station_video = {"code": "invalid_request", "message": "prompt is required", "data": None}
        station_image = {"error": {"message": "missing prompt"}}

        self.assertIn("Invalid token", zexi.zexi_error_text(new_api))
        self.assertIn("prompt is required", zexi.zexi_error_text(station_video))
        self.assertIn("missing prompt", zexi.zexi_error_text(station_image))

    def test_parameter_errors_are_detected_regardless_of_http_status(self):
        # 实测 grok duration=16 返回 HTTP 500 + build_request_failed，本质是参数错误
        raw = {"code": "build_request_failed", "message": "grok supports only 6, 10, or 15 seconds", "data": None}
        self.assertTrue(zexi.zexi_is_param_error(raw))

        class FakeResponse:
            status_code = 500
            text = ""

        with self.assertRaises(HTTPException) as ctx:
            zexi.zexi_raise_for_error(FakeResponse(), raw, "泽西同学视频")
        # 判成 400 而不是 502，前端才会提示改参数而不是当成上游故障重试
        self.assertEqual(ctx.exception.status_code, 400)

    def test_busy_line_rejection_is_distinguished_from_parameter_errors(self):
        """424 是站点文档里的"生成线路繁忙或任务被拒绝"，不是参数问题。

        2026-08-06 线上 minimax-h3 真实收到过 424（该模型在能力目录里被上游自己
        标注为"不稳定"）。提示必须让用户能区分"换模型重试"和"改参数"，
        否则会反复调参数而问题根本不在那里。
        """
        class FakeResponse:
            status_code = 424
            text = ""

        raw = {"error": {"message": "upstream busy", "type": "new_api_error"}}
        with self.assertRaises(HTTPException) as ctx:
            zexi.zexi_raise_for_error(FakeResponse(), raw, "泽西同学视频")
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("不是参数问题", ctx.exception.detail)
        self.assertIn("退款", ctx.exception.detail)

    def test_rate_limit_is_treated_as_line_unavailable(self):
        class FakeResponse:
            status_code = 429
            text = ""

        with self.assertRaises(HTTPException) as ctx:
            zexi.zexi_raise_for_error(FakeResponse(), {"message": "too many requests"}, "泽西同学视频")
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("线路暂时不可用", ctx.exception.detail)

    def test_upstream_server_errors_stay_502(self):
        class FakeResponse:
            status_code = 503
            text = ""

        raw = {"error": {"code": "model_not_found", "message": "temporarily unavailable", "type": "new_api_error"}}
        with self.assertRaises(HTTPException) as ctx:
            zexi.zexi_raise_for_error(FakeResponse(), raw, "泽西同学视频")
        self.assertEqual(ctx.exception.status_code, 502)

    def test_video_and_image_task_states_are_normalized(self):
        # 视频侧：扁平封套、小写状态、数字 progress
        video_done = {"task_id": "task_x", "status": "success", "progress": 100, "result_url": "https://cdn/x.mp4"}
        self.assertEqual(zexi.zexi_task_state(video_done)[0], "success")
        # 图片侧：大写状态、字符串 progress
        image_done = {"code": "success", "data": {"task_id": "aiimg_x", "status": "SUCCESS", "progress": "100%"}}
        self.assertEqual(zexi.zexi_task_state(image_done)[0], "success")
        # 图片侧实测形态：success=false + pending=false 才是失败
        image_failed = {"success": False, "pending": False, "task_id": "aiimg_x", "status": "failed", "error": "boom"}
        self.assertEqual(zexi.zexi_task_state(image_failed)[0], "failed")
        self.assertIn("boom", zexi.zexi_error_text(image_failed))
        running = {"success": True, "pending": True, "task_id": "aiimg_x", "status": "running", "progress": 15}
        self.assertEqual(zexi.zexi_task_state(running)[0], "pending")
        self.assertEqual(zexi.zexi_task_state({"status": "queued"})[0], "pending")

    def test_task_id_is_extracted_from_both_envelopes(self):
        self.assertEqual(zexi.zexi_task_id({"task_id": "task_1"}), "task_1")
        self.assertEqual(zexi.zexi_task_id({"id": "aiimg_1", "status": "queued"}), "aiimg_1")
        self.assertEqual(zexi.zexi_task_id({"data": {"task_id": "aiimg_2"}}), "aiimg_2")

    def test_upload_response_url_is_extracted(self):
        raw = {
            "success": True,
            "url": "https://zexitongxue.com/ai/reference-images/20260806/x.png",
            "image_url": "https://zexitongxue.com/ai/reference-images/20260806/x.png",
            "data": {"url": "https://zexitongxue.com/ai/reference-images/20260806/x.png"},
        }
        self.assertEqual(
            zexi.zexi_upload_result_url(raw),
            "https://zexitongxue.com/ai/reference-images/20260806/x.png",
        )

    def test_empty_httpx_errors_still_produce_a_readable_message(self):
        """httpx 的连接类异常 str() 常常是空的。

        线上真实表现：多张参考图连续上传触发 BrokenPipeError → httpx.ReadError，
        str(exc) 为空串，用户看到的弹窗是"请求泽西同学视频接口失败："后面什么都没有。
        """
        self.assertEqual(zexi.zexi_exception_text(httpx.ReadError("")), "ReadError")
        self.assertEqual(zexi.zexi_exception_text(httpx.ConnectTimeout("")), "ConnectTimeout")
        self.assertIn("boom", zexi.zexi_exception_text(httpx.ReadError("boom")))

        chained = httpx.ReadError("")
        chained.__cause__ = BrokenPipeError(32, "Broken pipe")
        text = zexi.zexi_exception_text(chained)
        self.assertIn("ReadError", text)
        self.assertIn("Broken pipe", text)

    def test_public_url_detection_rejects_local_hosts(self):
        self.assertTrue(zexi.zexi_is_public_http_url("https://example.com/a.jpg"))
        self.assertFalse(zexi.zexi_is_public_http_url("http://127.0.0.1:8000/output/a.png"))
        self.assertFalse(zexi.zexi_is_public_http_url("http://192.168.1.5/a.png"))
        self.assertFalse(zexi.zexi_is_public_http_url("/assets/a.png"))


class _StubResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class _StubClient:
    """只喂预设响应，不触网。记录每次请求的超时，便于断言未继承整体时限。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.timeouts = []

    async def get(self, url, headers=None, timeout=None, **kwargs):
        self.timeouts.append(timeout)
        return self._responses.pop(0)


class ZexiPollTests(unittest.TestCase):
    def test_task_not_found_is_reported_as_missing_not_as_failed(self):
        """404 必须先于 failed 判定。

        站点的"任务不存在"响应体里同时带 status:"failed"，若把 404 放在 failed 之后，
        那一支永远走不到，用户会把"任务号不存在"误读成"生成失败"。
        """
        not_found = {
            "error": {"message": "video task not found", "code": "task_not_found"},
            "id": "task_x", "task_id": "task_x", "status": "failed",
        }
        client = _StubClient([_StubResponse(404, not_found)])
        with self.assertRaises(HTTPException) as ctx:
            run(zexi.poll_zexi_task(client, "https://zexitongxue.com/v1/videos/task_x", "k", poll_interval=0.5, timeout=60))
        self.assertIn("任务不存在", ctx.exception.detail)

    def test_poll_requests_use_a_short_timeout_not_the_overall_deadline(self):
        """单次请求超时必须与整体等待时限分开。

        生成流程那条 client 的默认超时是按整体时限设的（1800 秒）。轮询请求若继承它，
        一次卡住的连接就能独占整个 30 分钟窗口——实测服务器到本站约 5% 连不上、
        最大延迟 14 秒，这个风险是真实的。
        """
        done = {"task_id": "task_x", "status": "success", "progress": 100}
        client = _StubClient([_StubResponse(200, done)])
        run(zexi.poll_zexi_task(client, "https://zexitongxue.com/v1/videos/task_x", "k",
                                poll_interval=0.5, timeout=1800))
        self.assertEqual(client.timeouts, [zexi.ZEXI_POLL_TIMEOUT])
        self.assertLessEqual(zexi.ZEXI_POLL_TIMEOUT.connect, 30.0)
        self.assertLessEqual(zexi.ZEXI_POLL_TIMEOUT.read, 120.0)

    def test_genuine_failure_still_reports_as_failed(self):
        failed = {"success": False, "pending": False, "task_id": "aiimg_x", "status": "failed", "error": "boom"}
        client = _StubClient([_StubResponse(200, failed)])
        with self.assertRaises(HTTPException) as ctx:
            run(zexi.poll_zexi_task(client, "https://zexitongxue.com/v1/images/tasks/aiimg_x", "k", poll_interval=0.5, timeout=60, label="泽西同学图片"))
        self.assertIn("失败", ctx.exception.detail)
        self.assertIn("boom", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
