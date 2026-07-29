import asyncio
import unittest

import main


# 家族与参数取自苍元中转站 /api/pricing 的 payloadBuilder 元数据：
#   omni-fast / omni-fast-no-water      builder=omni-frame
#     params: model, prompt, aspect_ratio, image_url, input_reference,
#             first_image_url, last_image_url
#   omni-v2v / omni-v2v-no-water        builder=omni-v2v
#     params: model, prompt, aspect_ratio, reference_videos,
#             reference_image_urls, input_video / input_video2
# 两族都没有 duration / resolution / audio，aspect_ratio 只收 16:9 和 9:16。
OMNI_FORBIDDEN_KEYS = ("duration", "resolution", "audio", "size", "image_refs", "video_refs", "audio_refs")


def build(payload, model):
    return asyncio.run(main.build_cangyuan_video_request(payload, model))


class CangyuanFamilyRoutingTests(unittest.TestCase):
    def test_omni_models_resolve_to_their_own_builder_families(self):
        for model in ("omni-fast", "omni-fast-no-water", "OMNI-FAST"):
            self.assertEqual(main.cangyuan_video_family(model), main.CANGYUAN_FAMILY_OMNI_FRAME)
        for model in ("omni-v2v", "omni-v2v-no-water"):
            self.assertEqual(main.cangyuan_video_family(model), main.CANGYUAN_FAMILY_OMNI_V2V)

    def test_seedance_and_unknown_models_stay_on_the_seedance_flat_builder(self):
        for model in ("seedance-2.0", "seedance-2.0-mini-8s", "sd5-seedance-2.0", "veo-clean", "sora-2", ""):
            self.assertEqual(main.cangyuan_video_family(model), main.CANGYUAN_FAMILY_SEEDANCE_FLAT)

    def test_seedance_body_still_carries_duration_resolution_and_audio(self):
        payload = main.CanvasVideoRequest(prompt="回归", duration=8, resolution="480p", generate_audio=True)
        body = build(payload, "seedance-2.0")
        self.assertEqual(body["duration"], 8)
        self.assertEqual(body["resolution"], "480p")
        self.assertEqual(body["audio"], True)


class CangyuanOmniFrameTests(unittest.TestCase):
    def test_body_drops_duration_resolution_and_audio(self):
        payload = main.CanvasVideoRequest(
            prompt="让这张图动起来",
            duration=15,
            resolution="720p",
            generate_audio=True,
        )
        body = build(payload, "omni-fast-no-water")

        self.assertEqual(body["model"], "omni-fast-no-water")
        self.assertEqual(sorted(body), ["aspect_ratio", "model", "prompt"])
        for key in OMNI_FORBIDDEN_KEYS:
            self.assertNotIn(key, body)

    def test_single_image_uses_the_singular_image_url_field(self):
        data_uri = "data:image/png;base64,iVBORw0KGgo="
        payload = main.CanvasVideoRequest(prompt="动起来", images=[main.AIReference(url=data_uri)])
        body = build(payload, "omni-fast")

        self.assertEqual(body["image_url"], data_uri)
        # 复数字段是 seedance 家族的，omni-frame 用了会被上游静默忽略。
        self.assertNotIn("reference_image_urls", body)

    def test_paired_frame_roles_switch_to_first_last_frame_mode(self):
        payload = main.CanvasVideoRequest(
            prompt="过渡",
            images=[
                main.AIReference(url="data:image/png;base64,AAAA", role="first_frame"),
                main.AIReference(url="data:image/png;base64,BBBB", role="last_frame"),
            ],
        )
        body = build(payload, "omni-fast")

        self.assertEqual(body["first_image_url"], "data:image/png;base64,AAAA")
        self.assertEqual(body["last_image_url"], "data:image/png;base64,BBBB")
        self.assertNotIn("image_url", body)

    def test_more_than_one_untagged_image_fails_loudly_instead_of_being_dropped(self):
        payload = main.CanvasVideoRequest(
            prompt="三张图",
            images=[main.AIReference(url=f"data:image/png;base64,IMG{i}") for i in range(3)],
        )
        with self.assertRaises(main.HTTPException) as ctx:
            build(payload, "omni-fast")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_reference_video_or_audio_is_rejected_with_a_pointer_to_v2v(self):
        for kwargs in ({"videos": ["https://cdn.example.com/ref.mp4"]}, {"audios": ["https://cdn.example.com/ref.mp3"]}):
            payload = main.CanvasVideoRequest(prompt="图生视频", **kwargs)
            with self.assertRaises(main.HTTPException) as ctx:
                build(payload, "omni-fast")
            self.assertEqual(ctx.exception.status_code, 400)


class CangyuanOmniV2VTests(unittest.TestCase):
    def test_body_drops_duration_resolution_and_audio(self):
        payload = main.CanvasVideoRequest(prompt="换风格", duration=15, resolution="720p", generate_audio=True)
        body = build(payload, "omni-v2v-no-water")

        self.assertEqual(sorted(body), ["aspect_ratio", "model", "prompt"])
        for key in OMNI_FORBIDDEN_KEYS:
            self.assertNotIn(key, body)

    def test_reference_video_alone_is_valid_because_v2v_is_video_first(self):
        payload = main.CanvasVideoRequest(prompt="换风格", videos=["https://cdn.example.com/ref.mp4"])
        body = build(payload, "omni-v2v")

        self.assertEqual(body["reference_videos"], ["https://cdn.example.com/ref.mp4"])
        self.assertNotIn("reference_image_urls", body)

    def test_reference_counts_are_capped_at_two(self):
        payload = main.CanvasVideoRequest(
            prompt="多素材",
            images=[main.AIReference(url=f"data:image/png;base64,IMG{i}") for i in range(4)],
            videos=[f"https://cdn.example.com/ref{i}.mp4" for i in range(4)],
        )
        body = build(payload, "omni-v2v")

        self.assertEqual(len(body["reference_image_urls"]), main.CANGYUAN_OMNI_V2V_MAX_IMAGE_REFS)
        self.assertEqual(len(body["reference_videos"]), main.CANGYUAN_OMNI_V2V_MAX_VIDEO_REFS)

    def test_reference_audio_is_rejected(self):
        payload = main.CanvasVideoRequest(prompt="配乐", audios=["https://cdn.example.com/ref.mp3"])
        with self.assertRaises(main.HTTPException) as ctx:
            build(payload, "omni-v2v")
        self.assertEqual(ctx.exception.status_code, 400)


class CangyuanOmniAspectRatioTests(unittest.TestCase):
    def test_only_the_two_documented_ratios_survive(self):
        cases = (("16:9", "16:9"), ("9:16", "9:16"), ("3:4", "9:16"), ("1:1", "16:9"), ("21:9", "16:9"), ("4:3", "16:9"), ("", "16:9"))
        for requested, expected in cases:
            for model in ("omni-fast", "omni-v2v"):
                body = build(main.CanvasVideoRequest(prompt="比例", aspect_ratio=requested), model)
                self.assertEqual(body["aspect_ratio"], expected, f"{model} / {requested}")

    def test_seedance_ratios_are_untouched_by_the_omni_narrowing(self):
        body = build(main.CanvasVideoRequest(prompt="比例", aspect_ratio="1:1"), "seedance-2.0")
        self.assertEqual(body["aspect_ratio"], "1:1")


if __name__ == "__main__":
    unittest.main()
