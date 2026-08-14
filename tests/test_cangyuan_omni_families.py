import asyncio
import unittest

import main


# 家族与参数取自苍元中转站 /api/pricing 的 payloadBuilder + api_doc：
#   omni-fast / omni-fast-no-water      builder=omni-frame
#     params: model, prompt, aspect_ratio, reference_image_urls,
#             first_image_url, last_image_url（最多 5 张参考图）
#   omni-v2v / omni-v2v-no-water        builder=omni-v2v
#     params: model, prompt, aspect_ratio, reference_videos,
#             reference_image_urls
# 两族都没有 duration / resolution / generate_audio，aspect_ratio 只收 16:9 和 9:16。
OMNI_FORBIDDEN_KEYS = ("duration", "resolution", "audio", "generate_audio", "size", "image_refs", "video_refs", "audio_refs", "image_url")


def build(payload, model):
    return asyncio.run(main.build_cangyuan_video_request(payload, model))


class CangyuanFamilyRoutingTests(unittest.TestCase):
    def test_omni_models_resolve_to_their_own_builder_families(self):
        for model in ("omni-fast", "omni-fast-no-water", "OMNI-FAST"):
            self.assertEqual(main.cangyuan_video_family(model), main.CANGYUAN_FAMILY_OMNI_FRAME)
        for model in ("omni-v2v", "omni-v2v-no-water"):
            self.assertEqual(main.cangyuan_video_family(model), main.CANGYUAN_FAMILY_OMNI_V2V)

    def test_seedance_and_sd_prefixed_models_stay_on_the_seedance_flat_builder(self):
        for model in ("seedance-2.0", "seedance-2.0-mini-8s", "sd5-seedance-2.0", "sd7-seedance-2.0-720p", ""):
            self.assertEqual(main.cangyuan_video_family(model), main.CANGYUAN_FAMILY_SEEDANCE_FLAT)

    def test_kling_models_resolve_to_the_kling_family(self):
        for model in ("kling-3.0", "kling-3.0-omni", "KLING-3.0"):
            self.assertEqual(main.cangyuan_video_family(model), main.CANGYUAN_FAMILY_KLING)

    def test_seedance_body_still_carries_duration_resolution_and_generate_audio(self):
        payload = main.CanvasVideoRequest(prompt="回归", duration=8, resolution="480p", generate_audio=True)
        body = build(payload, "seedance-2.0")
        self.assertEqual(body["duration"], 8)
        self.assertEqual(body["resolution"], "480p")
        self.assertEqual(body["generate_audio"], True)
        self.assertNotIn("audio", body)


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

    def test_single_image_uses_reference_image_urls(self):
        data_uri = "data:image/png;base64,iVBORw0KGgo="
        payload = main.CanvasVideoRequest(prompt="动起来", images=[main.AIReference(url=data_uri)])
        body = build(payload, "omni-fast")

        self.assertEqual(body["reference_image_urls"], [data_uri])
        self.assertNotIn("image_url", body)

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
        self.assertNotIn("reference_image_urls", body)

    def test_five_untagged_images_are_accepted(self):
        payload = main.CanvasVideoRequest(
            prompt="五张图",
            images=[main.AIReference(url=f"data:image/png;base64,IMG{i}") for i in range(5)],
        )
        body = build(payload, "omni-fast")
        self.assertEqual(len(body["reference_image_urls"]), 5)

    def test_more_than_five_untagged_images_fails_loudly_instead_of_being_dropped(self):
        payload = main.CanvasVideoRequest(
            prompt="六张图",
            images=[main.AIReference(url=f"data:image/png;base64,IMG{i}") for i in range(6)],
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

    def test_reference_counts_over_limit_fail_loudly(self):
        payload = main.CanvasVideoRequest(
            prompt="多素材",
            images=[main.AIReference(url=f"data:image/png;base64,IMG{i}") for i in range(4)],
            videos=[f"https://cdn.example.com/ref{i}.mp4" for i in range(4)],
        )
        with self.assertRaises(main.HTTPException) as ctx:
            build(payload, "omni-v2v")
        self.assertEqual(ctx.exception.status_code, 400)

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
