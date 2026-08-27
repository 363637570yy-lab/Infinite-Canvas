import asyncio
import unittest

import video_prompt_targets as vpt


XINGHUA_PROMPT = """杏花盛开的御花园，黄昏暖光。
镜头1：皇帝@图1 坐在石桌旁批阅奏折，镜头缓慢推进。
镜头2：甄嬛@图2 走入行礼。
台词：甄嬛：「臣妾参见皇上。」
镜头3：皇帝抬头微笑。
台词：皇帝：「免礼。」
镜头4：两人对坐交谈，连贯性过渡。
台词：甄嬛：「今日杏花开得正好。」
台词：皇帝：「与你同赏。」
柔光摄影 / 8K超高清
"""

TWO_IMAGES = [
    {"name": "皇帝_ref.png", "url": "http://x/1.png"},
    {"name": "甄嬛_ref.png", "url": "http://x/2.png"},
]


def xinghua_ctx(duration=15, images=TWO_IMAGES):
    return vpt.build_convert_context(XINGHUA_PROMPT, images, duration)


def all_notes(result):
    return list(result.get("errors") or []) + list(result.get("warnings") or [])


GOOD_REF2VA = """subject_definitions: <Subject 1> is a middle-aged emperor in a dark golden dragon robe from <Picture 1>. <Subject 2> is a young consort in a pale blue palace dress from <Picture 2>.
summary: In an imperial garden at dusk, the consort greets the emperor and they enjoy the apricot blossoms together.
retention_analysis: <Subject 1> must keep the same facial identity and golden robe. <Subject 2> must keep the same facial identity and pale blue dress.
detailed_description: [Shot 1] In a palace garden with apricot trees, <Subject 1> sits at a stone table reading memorials. The camera pushes in with small amplitude at slow speed. [Shot 2] At 00:04.000, the camera cuts to <Subject 2> walking in and bowing. (S1) <d>[Chinese] 臣妾参见皇上。</d> [Shot 3] At 00:08.000, <Subject 1> looks up and smiles. (S2) <d>[Chinese] 免礼。</d> [Shot 4] At 00:11.500, they sit together talking. (S1) <d>[Chinese] 今日杏花开得正好。</d> (S2) <d>[Chinese] 与你同赏。</d>
overall_soundscape: Soft garden ambience, birdsong, light breeze.
non_diegetic_music: N/A"""


class ConvertContextTests(unittest.TestCase):
    def test_slots_come_from_upload_order(self):
        ctx = xinghua_ctx()
        self.assertEqual([item["index"] for item in ctx["images"]], [1, 2])
        self.assertEqual([item["name"] for item in ctx["images"]], ["皇帝_ref.png", "甄嬛_ref.png"])
        self.assertEqual([item["role"] for item in ctx["images"]], ["reference", "reference"])
        self.assertIn("皇帝@图1", ctx["source_prompt"])
        self.assertNotIn("shots", ctx)
        self.assertNotIn("subjects", ctx)
        self.assertNotIn("style", ctx)

    def test_roles_follow_upload_list(self):
        images = [
            {"name": "first.png", "url": "u1", "role": "first_frame"},
            {"name": "last.png", "url": "u2", "role": "last_frame"},
        ]
        ctx = vpt.build_convert_context("皇帝起身", images, 8)
        self.assertEqual([item["role"] for item in ctx["images"]], ["first_frame", "last_frame"])

    def test_inventory_aliases_follow_target_and_language(self):
        ctx = xinghua_ctx()
        zh_seedance = "\n".join(vpt.image_inventory_lines(ctx, "seedance-2.5", "zh"))
        en_seedance = "\n".join(vpt.image_inventory_lines(ctx, "seedance-2.5", "en"))
        h3 = "\n".join(vpt.image_inventory_lines(ctx, "h3-ref2va", "zh"))
        self.assertIn("图1 = @图片1 = 皇帝_ref.png （参考）", zh_seedance)
        self.assertIn("Image 1 = @Image1 = 皇帝_ref.png (reference)", en_seedance)
        self.assertIn("图1 = <Picture 1> = 皇帝_ref.png （参考）", h3)

    def test_convert_image_captions(self):
        images = [
            {"name": "皇帝.png", "url": "u1", "role": "first_frame"},
            {"name": "妃子.png", "url": "u2"},
        ]
        ctx = vpt.build_convert_context("皇帝起身", images, 8)
        self.assertEqual(
            vpt.convert_image_captions(ctx),
            ["【图1】皇帝.png · 首帧", "【图2】妃子.png · 参考"],
        )

    def test_captions_keep_original_slot_when_url_missing(self):
        ctx = vpt.build_convert_context("皇帝起身", [
            {"name": "missing.png", "url": ""},
            {"name": "妃子.png", "url": "http://x/2.png", "role": "last_frame"},
        ], 8)
        self.assertEqual(vpt.convert_image_urls(ctx["images"]), ["http://x/2.png"])
        self.assertEqual(vpt.convert_image_captions(ctx), ["【图2】妃子.png · 尾帧"])
        self.assertEqual(
            vpt.convert_image_attachments(ctx),
            [("http://x/2.png", "【图2】妃子.png · 尾帧")],
        )


class FirstLastImageLimitTests(unittest.TestCase):
    def test_fl2va_convert_rejects_more_than_two_images(self):
        images = [
            {"name": "a.png", "url": "http://x/1.png", "role": "first_frame"},
            {"name": "b.png", "url": "http://x/2.png", "role": "last_frame"},
            {"name": "c.png", "url": "http://x/3.png"},
        ]
        message = vpt.reject_first_last_extra_images(images, target="h3-fl2va")
        self.assertIn("最多 2 张图", message)
        self.assertIn("当前 3 张", message)
        self.assertIn("转换", message)

    def test_fl2va_convert_allows_two_images(self):
        self.assertEqual(
            vpt.reject_first_last_extra_images(TWO_IMAGES, target="h3-fl2va"),
            "",
        )

    def test_generate_rejects_extra_images_only_when_frame_roles_present(self):
        unlabeled = [
            {"url": "http://x/1.png"},
            {"url": "http://x/2.png"},
            {"url": "http://x/3.png"},
        ]
        self.assertEqual(vpt.reject_first_last_extra_images(unlabeled, require_roles=True), "")
        labeled = [
            {"url": "http://x/1.png", "role": "first_frame"},
            {"url": "http://x/2.png", "role": "last_frame"},
            {"url": "http://x/3.png"},
        ]
        message = vpt.reject_first_last_extra_images(labeled, require_roles=True)
        self.assertIn("最多 2 张图", message)
        self.assertIn("生成", message)

    def test_other_targets_are_not_limited(self):
        images = [{"url": f"http://x/{i}.png"} for i in range(4)]
        self.assertEqual(vpt.reject_first_last_extra_images(images, target="seedance-2.5"), "")
        self.assertEqual(vpt.reject_first_last_extra_images(images, target="h3-ref2va"), "")


class AudioNameRejectTests(unittest.TestCase):
    def test_generic_name_rejected(self):
        message = vpt.reject_unmatched_audio_names(
            XINGHUA_PROMPT,
            TWO_IMAGES,
            [{"name": "音色", "url": "http://x/a.mp3"}],
            target="h3-ref2va",
        )
        self.assertIn("角色名", message)
        self.assertIn("音色", message)

    def test_voice_id_rejected(self):
        message = vpt.reject_unmatched_audio_names(
            XINGHUA_PROMPT,
            TWO_IMAGES,
            [{"name": "female-shaonv-jingpin", "url": "http://x/a.mp3"}],
            target="h3-ref2va",
        )
        self.assertIn("角色名", message)

    def test_voice_sample_file_rejected(self):
        message = vpt.reject_unmatched_audio_names(
            XINGHUA_PROMPT,
            TWO_IMAGES,
            [{"name": "voice_sample_abcdef12.mp3", "url": "http://x/a.mp3"}],
            target="h3-ref2va",
        )
        self.assertIn("角色名", message)

    def test_prompt_name_passes(self):
        self.assertEqual(
            vpt.reject_unmatched_audio_names(
                XINGHUA_PROMPT,
                TWO_IMAGES,
                [{"name": "皇帝", "url": "http://x/a.mp3"}],
                target="h3-ref2va",
            ),
            "",
        )

    def test_image_name_only_passes(self):
        self.assertEqual(
            vpt.reject_unmatched_audio_names(
                "花园里两人对话",
                TWO_IMAGES,
                [{"name": "皇帝", "url": "http://x/a.mp3"}],
                target="h3-ref2va",
            ),
            "",
        )

    def test_unmatched_name_rejected(self):
        message = vpt.reject_unmatched_audio_names(
            XINGHUA_PROMPT,
            TWO_IMAGES,
            [{"name": "婷婷", "url": "http://x/a.mp3"}],
            target="h3-ref2va",
        )
        self.assertIn("婷婷", message)
        self.assertIn("对不上", message)

    def test_no_audio_passes(self):
        self.assertEqual(
            vpt.reject_unmatched_audio_names(XINGHUA_PROMPT, TWO_IMAGES, [], target="h3-ref2va"),
            "",
        )

    def test_other_target_skips(self):
        self.assertEqual(
            vpt.reject_unmatched_audio_names(
                XINGHUA_PROMPT,
                TWO_IMAGES,
                [{"name": "音色", "url": "http://x/a.mp3"}],
                target="h3-fl2va",
            ),
            "",
        )


class MessageBuildTests(unittest.TestCase):
    def test_targets_listed_in_button_order(self):
        ids = [item["id"] for item in vpt.list_video_prompt_targets()]
        self.assertEqual(ids, ["seedance-2.0", "seedance-2.5", "h3-ref2va", "h3-fl2va"])
        presets = {item["id"]: item["preset"] for item in vpt.list_video_prompt_targets()}
        self.assertEqual(presets["seedance-2.0"], {"multimodal": True})
        self.assertEqual(presets["seedance-2.5"], {"multimodal": True})
        self.assertEqual(presets["h3-ref2va"], {"multimodal": True})
        self.assertEqual(presets["h3-fl2va"], {"frame_roles": True})
        groups = [(item["group"], item["label"]) for item in vpt.list_video_prompt_targets()]
        self.assertEqual(groups, [
            ("seedance优化", "2.0提示词"),
            ("seedance优化", "2.5提示词"),
            ("minimax优化", "多参提示词"),
            ("minimax优化", "首尾帧提示词"),
        ])

    def test_convert_messages_carry_skill_and_source_not_ir(self):
        ir = xinghua_ctx()
        messages = vpt.build_convert_messages("h3-ref2va", ir)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("subject_definitions", messages[0]["content"])
        self.assertIn("原始导演本", messages[1]["content"])
        self.assertIn("皇帝@图1", messages[1]["content"])
        self.assertIn("图1 = <Picture 1> = 皇帝_ref.png （参考）", messages[1]["content"])
        self.assertIn("【图1】皇帝_ref.png · 参考", messages[1]["content"])
        self.assertIn("臣妾参见皇上。", messages[1]["content"])
        self.assertIn("只输出该目标的提示词正文", messages[1]["content"])
        self.assertIn("生成语言：英文", messages[0]["content"])
        self.assertIn("生成语言：英文", messages[1]["content"])
        self.assertIn("本轮描写语言只能是英文", messages[1]["content"])
        self.assertIn("不要因为 skill 里有中文样例就改回中文", messages[0]["content"])
        self.assertNotIn("http://x/1.png", messages[1]["content"])
        self.assertNotIn("中间稿 JSON", messages[1]["content"])
        self.assertNotIn('"shots"', messages[1]["content"])

    def test_convert_messages_honor_chinese_output(self):
        ir = xinghua_ctx()
        messages = vpt.build_convert_messages("h3-ref2va", ir, language="zh")
        self.assertTrue(messages[0]["content"].startswith("生成语言：中文"))
        self.assertIn("不要因为 skill 里有英文样例就改回英文", messages[0]["content"])
        self.assertIn("生成语言：中文", messages[1]["content"])
        self.assertIn("本轮描写语言只能是中文", messages[1]["content"])
        self.assertIn("不要翻译", messages[1]["content"])
        self.assertNotIn("正文全英文", messages[0]["content"])
        self.assertIn("keyframe completion", messages[0]["content"])
        self.assertIn("<Subject 1> 是", messages[0]["content"])
        self.assertIn("禁止写成", messages[0]["content"])
        self.assertEqual(vpt.normalize_output_language("中文"), "zh")
        self.assertEqual(vpt.normalize_output_language(""), "en")
        self.assertGreater(vpt._content_units("镜头从门口缓缓推入客厅，保持户型图的空间顺序。" * 10), 200)

    def test_convert_image_urls_keep_slot_order(self):
        self.assertEqual(
            vpt.convert_image_urls(TWO_IMAGES + [{"name": "空", "url": "  "}]),
            ["http://x/1.png", "http://x/2.png"],
        )

    def test_non_vision_model_warns_but_still_lists_images(self):
        ir = xinghua_ctx()
        warnings = vpt.convert_input_warnings(ir, "gpt-3.5-turbo", ["http://x/1.png"])
        self.assertTrue(any("看不到附图" in item for item in warnings))
        self.assertTrue(vpt.chat_model_likely_sees_images("gpt-4o-mini"))
        self.assertFalse(vpt.chat_model_likely_sees_images("gpt-3.5-turbo"))

    def test_repair_messages_append_errors(self):
        ir = xinghua_ctx()
        messages = vpt.build_repair_messages("seedance-2.5", ir, "旧输出", ["缺少首帧声明行"])
        self.assertEqual(messages[-2]["role"], "assistant")
        self.assertIn("缺少首帧声明行", messages[-1]["content"])

    def test_unknown_target_raises(self):
        with self.assertRaises(KeyError):
            vpt.load_target_skill("h3-t2v")

    def test_strip_model_output_removes_fence(self):
        self.assertEqual(vpt.strip_model_output("```text\nabc\n```"), "abc")
        self.assertEqual(vpt.strip_model_output("  abc  "), "abc")


class ValidateRef2vaTests(unittest.TestCase):
    def test_good_output_passes(self):
        result = vpt.validate_target_output("h3-ref2va", GOOD_REF2VA, xinghua_ctx())
        self.assertEqual(result["errors"], [])

    def test_missing_section_fails(self):
        bad = GOOD_REF2VA.replace("retention_analysis:", "retention:")
        result = vpt.validate_target_output("h3-ref2va", bad, xinghua_ctx())
        self.assertTrue(any("retention_analysis" in e for e in result["errors"]))

    def test_picture_out_of_range_is_hint(self):
        bad = GOOD_REF2VA.replace("<Picture 2>", "<Picture 3>")
        result = vpt.validate_target_output("h3-ref2va", bad, xinghua_ctx())
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("<Picture 3>" in e for e in result["warnings"]))

    def test_modified_dialogue_is_hint(self):
        bad = GOOD_REF2VA.replace("臣妾参见皇上。", "臣妾拜见皇上。")
        result = vpt.validate_target_output("h3-ref2va", bad, xinghua_ctx())
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("改写" in e for e in result["warnings"]))
        self.assertFalse(any("缺失" in e for e in all_notes(result)))

    def test_dropping_dialogue_is_allowed(self):
        dropped = GOOD_REF2VA.replace(" (S1) <d>[Chinese] 今日杏花开得正好。</d> (S2) <d>[Chinese] 与你同赏。</d>", "")
        result = vpt.validate_target_output("h3-ref2va", dropped, xinghua_ctx())
        self.assertEqual(result["errors"], [])

    def test_no_identified_subjects_is_hint_when_images_exist(self):
        bad = GOOD_REF2VA.replace(
            "<Subject 1> is a middle-aged emperor in a dark golden dragon robe from <Picture 1>. <Subject 2> is a young consort in a pale blue palace dress from <Picture 2>.",
            "No identified subjects.",
        )
        result = vpt.validate_target_output("h3-ref2va", bad, xinghua_ctx())
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("No identified subjects" in e for e in result["warnings"]))

    def test_multiple_subjects_from_one_picture_allowed(self):
        ir = vpt.build_convert_context("从入户走到所有房间", [{"name": "户型.png", "url": "u1"}], 15)
        text = (
            "subject_definitions:\n"
            "<Subject 1> is the apartment layout in <Picture 1>, including the entrance and rooms.\n"
            "<Subject 2> is the corridor sequence inside <Picture 1>.\n"
            "summary:\n[reference generation] A walkthrough of <Subject 1>.\n"
            "retention_analysis:\n<Subject 1> (appears in [Shot 1]): fully_preserved - room order stays consistent.\n"
            "detailed_description:\nThe target video is a realistic interior walkthrough. [Shot 1] The camera starts at the entrance of <Subject 1>.\n"
            "overall_soundscape:\nQuiet indoor ambience.\n"
            "non_diegetic_music:\nN/A"
        )
        result = vpt.validate_target_output("h3-ref2va", text, ir)
        self.assertEqual(result["errors"], [])
        self.assertFalse(any("任务类型应使用官方前缀" in w for w in result["warnings"]))

    def test_summary_unknown_prefix_warns(self):
        text = GOOD_REF2VA.replace("summary: In an imperial garden", "summary: [custom vibe] In an imperial garden")
        result = vpt.validate_target_output("h3-ref2va", text, xinghua_ctx())
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("任务类型应使用官方前缀" in w for w in result["warnings"]))

    def test_character_voice_off_keeps_plain_output(self):
        result = vpt.validate_target_output("h3-ref2va", GOOD_REF2VA, xinghua_ctx())
        self.assertEqual(result["errors"], [])

    def test_character_voice_requires_audio_binding(self):
        ctx = vpt.build_convert_context(
            XINGHUA_PROMPT,
            TWO_IMAGES,
            15,
            audios=[{"name": "emperor.mp3", "url": "http://x/a.mp3"}],
            character_voice=True,
        )
        result = vpt.validate_target_output("h3-ref2va", GOOD_REF2VA, ctx)
        self.assertTrue(any("缺少音色绑定" in item for item in result["errors"]))

    def test_character_voice_good_output_passes(self):
        ctx = vpt.build_convert_context(
            XINGHUA_PROMPT,
            TWO_IMAGES,
            15,
            audios=[{"name": "emperor.mp3", "url": "http://x/a.mp3"}],
            character_voice=True,
        )
        text = GOOD_REF2VA.replace(
            "<Subject 1> is a middle-aged emperor in a dark golden dragon robe from <Picture 1>.",
            "<Subject 1> is a middle-aged emperor in a dark golden dragon robe from <Picture 1>. <Audio 1> is the voice timbre reference for <Subject 1>, using only timbre and pace, not the original words.",
        ).replace(
            "summary: In an imperial garden at dusk",
            "summary: [reference generation + audio reference] In an imperial garden at dusk",
        ).replace(
            "retention_analysis: <Subject 1> must keep",
            "retention_analysis: <Audio 1>: reference - used as <Subject 1>'s speaking timbre. <Subject 1> must keep",
        )
        result = vpt.validate_target_output("h3-ref2va", text, ctx)
        self.assertEqual(result["errors"], [])

    def test_character_voice_can_bind_named_audio_to_subject_two(self):
        ctx = vpt.build_convert_context(
            XINGHUA_PROMPT,
            TWO_IMAGES,
            15,
            audios=[{"name": "少女音色", "url": "http://x/a.mp3"}],
            character_voice=True,
        )
        text = GOOD_REF2VA.replace(
            "<Subject 2> is a young consort in a pale blue palace dress from <Picture 2>.",
            "<Subject 2> is a young consort in a pale blue palace dress from <Picture 2>. <Audio 1> is the voice timbre reference for <Subject 2>, using only timbre and pace, not the original words.",
        ).replace(
            "summary: In an imperial garden at dusk",
            "summary: [reference generation + audio reference] In an imperial garden at dusk",
        ).replace(
            "retention_analysis: <Subject 1> must keep",
            "retention_analysis: <Audio 1>: reference - used as <Subject 2>'s speaking timbre. <Subject 1> must keep",
        )
        result = vpt.validate_target_output("h3-ref2va", text, ctx)
        self.assertEqual(result["errors"], [])

    def test_character_voice_inventory_in_convert_messages(self):
        ctx = vpt.build_convert_context(
            "皇帝说话",
            TWO_IMAGES,
            8,
            audios=[{"name": "emperor.mp3", "url": "http://x/a.mp3"}],
            character_voice=True,
        )
        messages = vpt.build_convert_messages("h3-ref2va", ctx, language="zh")
        self.assertIn("角色音色：开", messages[1]["content"])
        self.assertIn("音1 = <Audio 1> = emperor.mp3", messages[1]["content"])
        self.assertIn("显示名必须对应原文里的角色名", messages[1]["content"])
        self.assertIn("不要一律绑 <Subject 1>", messages[1]["content"])
        self.assertNotIn("http://x/a.mp3", messages[1]["content"])
        self.assertNotIn("minimax-speech", vpt.CHAT_CONVERT_BLOCKED_PROTOCOLS)
        self.assertFalse(vpt.is_usable_chat_provider({
            "id": "minimax-speech",
            "protocol": "minimax-speech",
            "enabled": True,
            "chat_models": ["speech-2.8-hd"],
            "has_key": True,
        }))
        self.assertTrue(vpt.is_usable_chat_provider({
            "id": "minimax-official",
            "protocol": "minimax-speech",
            "enabled": True,
            "chat_models": ["MiniMax-M3"],
            "has_key": True,
        }))

    def test_ref_video_off_keeps_plain_output(self):
        messages = vpt.build_convert_messages("h3-ref2va", xinghua_ctx(), language="zh")
        self.assertIn("参考视频：关", messages[1]["content"])
        self.assertIn("（没有参考视频）", messages[1]["content"])
        result = vpt.validate_target_output("h3-ref2va", GOOD_REF2VA, xinghua_ctx())
        self.assertEqual(result["errors"], [])

    def test_ref_video_requires_binding(self):
        ctx = vpt.build_convert_context(
            XINGHUA_PROMPT,
            TWO_IMAGES,
            15,
            videos=[{"name": "walk.mp4", "url": "http://x/v.mp4"}],
        )
        result = vpt.validate_target_output("h3-ref2va", GOOD_REF2VA, ctx)
        self.assertTrue(any("缺少视频绑定" in item for item in result["errors"]))

    def test_ref_video_good_output_passes(self):
        ctx = vpt.build_convert_context(
            XINGHUA_PROMPT,
            TWO_IMAGES,
            15,
            videos=[{"name": "walk.mp4", "url": "http://x/v.mp4"}],
        )
        text = GOOD_REF2VA.replace(
            "<Subject 1> is a middle-aged emperor in a dark golden dragon robe from <Picture 1>.",
            "<Video 1> is the motion and blocking reference. <Subject 1> is a middle-aged emperor in a dark golden dragon robe from <Picture 1>.",
        ).replace(
            "retention_analysis: <Subject 1> must keep",
            "retention_analysis: <Video 1>: reference - motion and staging only, do not reuse the source dialogue. <Subject 1> must keep",
        )
        result = vpt.validate_target_output("h3-ref2va", text, ctx)
        self.assertEqual(result["errors"], [])

    def test_ref_video_inventory_in_convert_messages(self):
        ctx = vpt.build_convert_context(
            "皇帝走路",
            TWO_IMAGES,
            8,
            videos=[{"name": "walk.mp4", "url": "http://x/v.mp4"}],
        )
        messages = vpt.build_convert_messages("h3-ref2va", ctx, language="zh")
        self.assertIn("参考视频：开", messages[1]["content"])
        self.assertIn("视频1 = <Video 1> = walk.mp4", messages[1]["content"])
        self.assertNotIn("http://x/v.mp4", messages[1]["content"])

    def test_plain_ref_audio_requires_binding(self):
        ctx = vpt.build_convert_context(
            XINGHUA_PROMPT,
            TWO_IMAGES,
            15,
            audios=[{"name": "wind.mp3", "url": "http://x/a.mp3"}],
        )
        result = vpt.validate_target_output("h3-ref2va", GOOD_REF2VA, ctx)
        self.assertTrue(any("缺少音频绑定" in item for item in result["errors"]))
        self.assertFalse(any("缺少音色绑定" in item for item in result["errors"]))

    def test_plain_ref_audio_good_output_passes(self):
        ctx = vpt.build_convert_context(
            XINGHUA_PROMPT,
            TWO_IMAGES,
            15,
            audios=[{"name": "wind.mp3", "url": "http://x/a.mp3"}],
        )
        text = GOOD_REF2VA.replace(
            "<Subject 1> is a middle-aged emperor in a dark golden dragon robe from <Picture 1>.",
            "<Audio 1> is ambient garden wind, not a character voice. <Subject 1> is a middle-aged emperor in a dark golden dragon robe from <Picture 1>.",
        )
        result = vpt.validate_target_output("h3-ref2va", text, ctx)
        self.assertEqual(result["errors"], [])

    def test_english_prose_ok_when_language_en(self):
        result = vpt.validate_target_output("h3-ref2va", GOOD_REF2VA, xinghua_ctx(), language="en")
        self.assertFalse(any("仍是英文" in e for e in result["errors"]))

    def test_english_prose_is_hint_when_language_zh(self):
        result = vpt.validate_target_output("h3-ref2va", GOOD_REF2VA, xinghua_ctx(), language="zh")
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("仍是英文" in e for e in result["warnings"]))
        self.assertTrue(any("subject_definitions" in e for e in result["warnings"]))

    def test_chinese_prose_ok_when_language_zh(self):
        text = (
            "subject_definitions:\n"
            "<Subject 1> 是 <Picture 1> 里的中年皇帝，深金色龙袍，神情沉稳。\n"
            "<Subject 2> 是 <Picture 2> 里的年轻妃子，浅蓝色宫装。\n"
            "summary:\n[reference generation] 黄昏御花园里，<Subject 2> 向 <Subject 1> 行礼。\n"
            "retention_analysis:\n<Subject 1> (appears in [Shot 1]): fully_preserved - 脸和金袍保持一致。\n"
            "detailed_description:\n目标视频采用电影感宫廷风格。\n"
            "[Shot 1] <Subject 1> 坐在石桌旁批阅奏折，镜头小幅度缓慢推进。\n"
            "[Shot 2] At 00:04.000, the camera cuts to <Subject 2> 走入鞠躬。<Subject 2> (S1) says: <d>[Chinese] 臣妾参见皇上。</d>\n"
            "[Shot 3] At 00:08.000, <Subject 1> 抬头微笑。<Subject 1> (S2) says: <d>[Chinese] 免礼。</d>\n"
            "[Shot 4] At 00:11.500, 两人对坐赏花。\n"
            "overall_soundscape:\n园中轻风和远处鸟鸣。\n"
            "non_diegetic_music:\nN/A"
        )
        result = vpt.validate_target_output("h3-ref2va", text, xinghua_ctx(), language="zh")
        self.assertFalse(any("仍是英文" in e for e in all_notes(result)))
        en_result = vpt.validate_target_output("h3-ref2va", text, xinghua_ctx(), language="en")
        self.assertEqual(en_result["errors"], [])
        self.assertTrue(any("仍是中文" in e for e in en_result["warnings"]))

    def test_leftover_canvas_syntax_is_hint(self):
        bad = GOOD_REF2VA.replace("<Picture 1>", "@图1")
        result = vpt.validate_target_output("h3-ref2va", bad, xinghua_ctx())
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("@图N" in e for e in result["warnings"]))

    def test_time_code_over_duration_is_hint(self):
        bad = GOOD_REF2VA.replace("At 00:11.500", "At 00:16.000")
        result = vpt.validate_target_output("h3-ref2va", bad, xinghua_ctx(duration=15))
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("超出时长" in e for e in result["warnings"]))

    def test_time_code_not_increasing_is_hint(self):
        bad = GOOD_REF2VA.replace("At 00:08.000", "At 00:03.000")
        result = vpt.validate_target_output("h3-ref2va", bad, xinghua_ctx())
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("递增" in e for e in result["warnings"]))


class ValidateFl2vaTests(unittest.TestCase):
    def _ir(self, with_last=False):
        images = [{"name": "first.png", "url": "u1", "role": "first_frame"}]
        if with_last:
            images.append({"name": "last.png", "url": "u2", "role": "last_frame"})
        return vpt.build_convert_context("皇帝在御花园饮茶\n台词：皇帝：「春色正好。」", images, 10)

    def _good(self, align):
        return (
            align + "\n\n"
            "integrated_multimodal_description: The emperor sits at a stone table, slowly raises his head "
            "and gazes at the blossoms. The camera pushes in with small amplitude at slow speed. "
            "(S1) <d>[Chinese] 春色正好。</d>\n"
            "overall_soundscape: Quiet garden ambience with birdsong.\n"
            "non_diegetic_music: N/A"
        )

    def test_first_only_alignment_passes(self):
        ir = self._ir()
        result = vpt.validate_target_output("h3-fl2va", self._good(vpt.fl2va_align_first()), ir)
        self.assertEqual(result["errors"], [])

    def test_wrong_alignment_line_fails(self):
        result = vpt.validate_target_output(
            "h3-fl2va",
            self._good("The video starts exactly on the provided first frame."),
            self._ir(with_last=False),
        )
        self.assertTrue(any("对齐行" in e for e in result["errors"]))

    def test_both_frames_need_full_alignment(self):
        ir = self._ir(with_last=True)
        result = vpt.validate_target_output("h3-fl2va", self._good(vpt.fl2va_align_both(ir)), ir)
        self.assertEqual(result["errors"], [])

    def test_picture_anchor_allowed_in_body(self):
        ir = self._ir()
        text = self._good(vpt.fl2va_align_first()).replace(
            "The emperor sits at a stone table",
            "The emperor remains in the framing established by <Picture 1> and sits at a stone table",
        )
        result = vpt.validate_target_output("h3-fl2va", text, ir)
        self.assertEqual(result["errors"], [])

    def test_subject_syntax_is_hint(self):
        bad = self._good(vpt.fl2va_align_first()).replace("The emperor", "<Subject 1>")
        result = vpt.validate_target_output("h3-fl2va", bad, self._ir())
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("<Subject>" in e for e in result["warnings"]))

    def test_dropping_dialogue_is_allowed(self):
        text = self._good(vpt.fl2va_align_first()).replace(" (S1) <d>[Chinese] 春色正好。</d>", "")
        result = vpt.validate_target_output("h3-fl2va", text, self._ir())
        self.assertEqual(result["errors"], [])

    def test_fl2va_language_must_match_selection(self):
        ir = self._ir()
        english = self._good(vpt.fl2va_align_first())
        chinese = (
            vpt.fl2va_align_first() + "\n\n"
            "integrated_multimodal_description: [Shot 1] 皇帝坐在石桌旁，慢慢抬头看向杏花，镜头小幅度缓慢推进。"
            "(S1) <d>[Chinese] 春色正好。</d>\n"
            "overall_soundscape: 安静园中环境声和远处鸟鸣。\n"
            "non_diegetic_music: N/A"
        )
        self.assertEqual(vpt.validate_target_output("h3-fl2va", english, ir, language="en")["errors"], [])
        zh_on_en = vpt.validate_target_output("h3-fl2va", english, ir, language="zh")
        self.assertEqual(zh_on_en["errors"], [])
        self.assertTrue(any("仍是英文" in e for e in zh_on_en["warnings"]))
        self.assertFalse(any("仍是中文" in e or "仍是英文" in e for e in all_notes(vpt.validate_target_output("h3-fl2va", chinese, ir, language="zh"))))
        en_on_zh = vpt.validate_target_output("h3-fl2va", chinese, ir, language="en")
        self.assertEqual(en_on_zh["errors"], [])
        self.assertTrue(any("仍是中文" in e for e in en_on_zh["warnings"]))


class ValidateSeedanceTests(unittest.TestCase):
    GOOD_25 = (
        "@Image 1 is the reference for the emperor's appearance.\n"
        "@Image 2 is the reference for the young consort's appearance.\n"
        "The emperor (@Image 1), a middle-aged man in a dark golden dragon robe, and the young consort (@Image 2) in a pale blue dress.\n"
        "The emperor reads memorials; the consort walks in and bows; he looks up with a gentle smile.\n"
        "An imperial garden under blossoming apricot trees at dusk, warm side light.\n"
        "Medium shot slowly pushing in, then the camera cuts to a two-shot as she bows.\n"
        "Cinematic soft-light photography, warm color grade, 8K detail.\n"
        "Quiet garden ambience. The consort says: \"臣妾参见皇上。\" The emperor replies: \"免礼。\""
    )

    def test_good_seedance_25_passes(self):
        result = vpt.validate_target_output("seedance-2.5", self.GOOD_25, xinghua_ctx())
        self.assertEqual(result["errors"], [])

    def test_merged_frame_declaration_is_hint(self):
        bad = "@Images 1 and 2 are the first and last frames.\n" + self.GOOD_25
        result = vpt.validate_target_output("seedance-2.5", bad, xinghua_ctx())
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("合并声明" in e for e in result["warnings"]))

    def test_image_out_of_range_is_hint(self):
        bad = self.GOOD_25.replace("(@Image 2)", "(@Image 5)")
        result = vpt.validate_target_output("seedance-2.5", bad, xinghua_ctx())
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("@Image 5" in e for e in result["warnings"]))

    def test_h3_timecode_is_hint(self):
        bad = self.GOOD_25 + "\n[Shot 2] At 00:04.000, the camera cuts."
        result = vpt.validate_target_output("seedance-2.5", bad, xinghua_ctx())
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("At MM:SS.mmm" in e for e in result["warnings"]))

    def test_h3_picture_tag_is_hint(self):
        bad = self.GOOD_25 + "\n<Picture 1> walks in."
        result = vpt.validate_target_output("seedance-2.5", bad, xinghua_ctx())
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("<Picture>" in e for e in result["warnings"]))

    def test_seedance_25_allows_integer_seconds_and_shot_labels(self):
        text = (
            "@Image 1 is the reference for the emperor's appearance.\n"
            "@Image 2 is the reference for the young consort's appearance.\n"
            "黄昏御花园，皇帝批折，妃子入园行礼。\n"
            "镜头1（0-4s）：皇帝（@Image 1）坐在石桌旁。\n"
            "镜头2（4-8s）：妃子（@Image 2）走入鞠躬。妃子说：“臣妾参见皇上。”\n"
            "全程暖光，不要字幕。"
        )
        result = vpt.validate_target_output("seedance-2.5", text, xinghua_ctx(), language="zh")
        self.assertEqual(result["errors"], [])
        self.assertFalse(any("时间轴" in w for w in result["warnings"]))
        self.assertFalse(any("仍是英文" in e for e in all_notes(result)))
        en_result = vpt.validate_target_output("seedance-2.5", text, xinghua_ctx(), language="en")
        self.assertEqual(en_result["errors"], [])
        self.assertTrue(any("仍是中文" in e for e in en_result["warnings"]))

    def test_seedance_25_warns_when_seconds_exceed_duration(self):
        text = self.GOOD_25 + "\n镜头3（16-20s）：两人离场。"
        result = vpt.validate_target_output("seedance-2.5", text, xinghua_ctx(duration=15))
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("超出时长" in w for w in result["warnings"]))

    def test_seedance_20_allows_shot_numbers(self):
        text = (
            "镜头1：皇帝（@Image 1）坐在石桌旁批折。\n"
            "镜头2：妃子（@Image 2）走入鞠躬。妃子说：“臣妾参见皇上。”"
        )
        result = vpt.validate_target_output("seedance-2.0", text, xinghua_ctx(), language="zh")
        self.assertEqual(result["errors"], [])
        en_result = vpt.validate_target_output("seedance-2.0", text, xinghua_ctx(), language="en")
        self.assertEqual(en_result["errors"], [])
        self.assertTrue(any("仍是中文" in e for e in en_result["warnings"]))

    def test_seedance_20_warns_on_integer_timestamps(self):
        text = "0-3s：皇帝（@Image 1）坐着。3秒后妃子（@Image 2）入画。"
        result = vpt.validate_target_output("seedance-2.0", text, xinghua_ctx(), language="zh")
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("不响应时间戳" in w for w in result["warnings"]))

    def test_seedance_25_allows_shot_heading_without_h3_timecode(self):
        text = self.GOOD_25 + "\n[Shot 2] then the camera cuts to a two-shot."
        result = vpt.validate_target_output("seedance-2.5", text, xinghua_ctx())
        self.assertEqual(result["errors"], [])

    def test_seedance_25_skill_teaches_lock_and_integer_seconds(self):
        skill = vpt.load_target_skill("seedance-2.5")
        self.assertIn("有锁定", skill)
        self.assertIn("整数秒", skill)
        self.assertIn("镜头1", skill)
        self.assertIn("0-3s", skill)

    def test_all_skills_teach_both_languages(self):
        for target_id in ("h3-ref2va", "h3-fl2va", "seedance-2.0", "seedance-2.5"):
            skill = vpt.load_target_skill(target_id)
            self.assertIn("语言铁律", skill, target_id)
            self.assertIn("中文描写样例", skill, target_id)
            self.assertIn("英文描写样例", skill, target_id)
            self.assertIn("禁止中英混写", skill, target_id)

    def test_english_seedance_is_hint_when_language_zh(self):
        result = vpt.validate_target_output("seedance-2.5", self.GOOD_25, xinghua_ctx(), language="zh")
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("仍是英文" in e for e in result["warnings"]))
        result20 = vpt.validate_target_output("seedance-2.0", self.GOOD_25, xinghua_ctx(), language="zh")
        self.assertEqual(result20["errors"], [])
        self.assertTrue(any("仍是英文" in e for e in result20["warnings"]))

    def test_first_frame_declaration_is_hint_when_role_present(self):
        images = [
            {"name": "first.png", "url": "u1", "role": "first_frame"},
            {"name": "ref.png", "url": "u2"},
        ]
        ir = vpt.build_convert_context("皇帝@图1 起身，@图2 为氛围参考", images, 10)
        text = "The emperor (@Image 1) stands up slowly in a garden. Warm light. The camera holds still. Quiet ambience."
        result = vpt.validate_target_output("seedance-2.5", text, ir)
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("首帧声明" in e for e in result["warnings"]))
        result20 = vpt.validate_target_output("seedance-2.0", text, ir)
        self.assertEqual(result20["errors"], [])
        self.assertTrue(any("首帧声明" in e for e in result20["warnings"]))

    def test_seedance_20_accepts_first_frame_declaration(self):
        images = [{"name": "first.png", "url": "u1", "role": "first_frame"}]
        ir = vpt.build_convert_context("皇帝起身", images, 8)
        text = (
            "@Image 1 is the first frame. It defines the opening composition, subject position, pose, and camera direction.\n"
            "The emperor stands and walks forward. Quiet ambience."
        )
        result = vpt.validate_target_output("seedance-2.0", text, ir)
        self.assertEqual(result["errors"], [])
        zh_text = (
            "@图片1 作为首帧，定义开场构图、站位、姿态和镜头方向。\n"
            "皇帝起身向前走。安静环境声。"
        )
        zh_result = vpt.validate_target_output("seedance-2.0", zh_text, ir, language="zh")
        self.assertEqual(zh_result["errors"], [])
        self.assertFalse(any("首帧声明" in e for e in zh_result["warnings"]))

    def test_seedance_accepts_language_tags(self):
        zh_text = (
            "@图片1 是皇帝外貌的参考。\n"
            "@图片2 是妃子外貌的参考。\n"
            "黄昏御花园，皇帝批折，妃子入园行礼。\n"
            "镜头1：皇帝（@图片1）坐在石桌旁。妃子说：“臣妾参见皇上。”\n"
            "全程暖光，不要字幕。"
        )
        result = vpt.validate_target_output("seedance-2.5", zh_text, xinghua_ctx(), language="zh")
        self.assertEqual(result["errors"], [])
        self.assertFalse(any("@图N" in e or "画布 @图" in e for e in result["warnings"]))
        en_text = (
            "@Image1 is the reference for the emperor.\n"
            "@Image2 is the reference for the consort.\n"
            "At dusk in an imperial garden, the emperor reads memorials while the consort enters and bows.\n"
            "Shot 1: The emperor (@Image1) sits at a stone table. She says: \"臣妾参见皇上。\"\n"
            "Warm light throughout, no subtitles."
        )
        en_result = vpt.validate_target_output("seedance-2.5", en_text, xinghua_ctx(), language="en")
        self.assertEqual(en_result["errors"], [])

    def test_seedance_canvas_at_tu_is_hint(self):
        text = "皇帝（@图1）坐在石桌旁批折，妃子走入行礼。黄昏暖光。安静环境声。"
        result = vpt.validate_target_output("seedance-2.5", text, xinghua_ctx(), language="zh")
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("画布 @图" in e for e in result["warnings"]))

    def test_convert_messages_seedance_aliases(self):
        ctx = xinghua_ctx()
        zh = vpt.build_convert_messages("seedance-2.5", ctx, language="zh")
        en = vpt.build_convert_messages("seedance-2.5", ctx, language="en")
        self.assertIn("图1 = @图片1 = 皇帝_ref.png （参考）", zh[1]["content"])
        self.assertIn("@图片N", zh[0]["content"])
        self.assertIn("作为首帧", zh[0]["content"])
        self.assertIn("Image 1 = @Image1 = 皇帝_ref.png (reference)", en[1]["content"])
        self.assertIn("@ImageN", en[0]["content"])
        self.assertIn("as the first frame", en[0]["content"])

    def test_seedance_20_word_limit_warns(self):
        long_text = "The emperor (@Image 1) walks. " + ("very slowly and gracefully through the garden " * 40)
        result = vpt.validate_target_output("seedance-2.0", long_text, xinghua_ctx())
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("上限 200" in w for w in result["warnings"]))

    def test_source_dialogues_are_not_treated_as_invented(self):
        prompt = (
            "镜头1：皇帝@图1 独望窗外。旁白：“坐拥天下。” 又说：“却最难信人心。”\n"
            "镜头2：甄嬛@图2 入殿。甄嬛台词：“皇上深夜召见。”（停顿）“可是为了后宫之事？”\n"
            "皇帝台词：“朕曾怀疑你。”（停顿）“也曾怕你。” 甄嬛回应：“臣妾明白。”"
        )
        ir = vpt.build_convert_context(prompt, TWO_IMAGES, 15)
        text = (
            "@Image 1 is the reference for the emperor.\n"
            "@Image 2 is the reference for the consort.\n"
            "深夜养心殿，皇帝独望窗外，甄嬛入殿对峙。\n"
            "镜头1（0-5s）：皇帝（@Image 1）旁白：“坐拥天下。” 又说：“却最难信人心。”\n"
            "镜头2（5-10s）：甄嬛（@Image 2）说：“皇上深夜召见。” 又说：“可是为了后宫之事？”\n"
            "镜头3（10-15s）：皇帝说：“朕曾怀疑你。” 又说：“也曾怕你。” 甄嬛说：“臣妾明白。”\n"
            "全程烛光，不要字幕。"
        )
        result = vpt.validate_target_output("seedance-2.5", text, ir, language="zh")
        self.assertEqual(result["errors"], [])

    def test_invented_dialogue_is_hint(self):
        prompt = "镜头1：皇帝@图1 说：“免礼。”"
        ir = vpt.build_convert_context(prompt, TWO_IMAGES, 8)
        text = (
            "@Image 1 is the reference for the emperor.\n"
            "皇帝抬头。皇帝说：“今晚月色真美。”\n"
            "安静园中环境声。"
        )
        result = vpt.validate_target_output("seedance-2.5", text, ir, language="zh")
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("凭空新增" in e and "今晚月色真美" in e for e in result["warnings"]))

    def test_short_garbage_still_blocks(self):
        result = vpt.validate_target_output("seedance-2.5", "这不是合格输出", xinghua_ctx(), language="zh")
        self.assertTrue(result["errors"])
        self.assertTrue(any("太短" in e or "没有输出" in e for e in result["errors"]))


class PickChatProviderTests(unittest.TestCase):
    def test_skips_modelscope_and_video_only(self):
        pick = vpt.pick_chat_provider([
            {"id": "modelscope", "enabled": True, "chat_models": ["Qwen"], "has_key": True},
            {"id": "h3-local", "protocol": "h3", "enabled": True, "chat_models": ["minimax-h3"]},
            {"id": "minimax-speech", "protocol": "minimax-speech", "enabled": True, "chat_models": ["speech-2.8-hd"], "has_key": True},
            {"id": "comfly", "enabled": True, "chat_models": ["gpt-4o-mini"], "has_key": True},
        ], "modelscope")
        self.assertEqual(pick["id"], "comfly")

    def test_prefers_keyed_chat_provider(self):
        pick = vpt.pick_chat_provider([
            {"id": "a", "enabled": True, "chat_models": ["m1"]},
            {"id": "b", "enabled": True, "chat_models": ["m2"], "has_key": True},
        ])
        self.assertEqual(pick["id"], "b")

    def test_empty_when_only_modelscope(self):
        self.assertIsNone(vpt.pick_chat_provider([
            {"id": "modelscope", "enabled": True, "chat_models": ["Qwen"], "has_key": True},
        ]))


class ConvertEndpointTests(unittest.TestCase):
    """转换端点：进程内 mock canvas_llm，不发任何真实请求。"""

    def setUp(self):
        import main
        self.main = main
        self._chat = {"id": "comfly", "enabled": True, "chat_models": ["gpt-4o-mini"], "protocol": "openai"}
        self._orig_load = main.load_api_providers
        self._orig_public = main.public_provider
        self._orig_get = main.get_api_provider
        main.load_api_providers = lambda: [self._chat]
        main.public_provider = lambda item: {**item, "has_key": True}
        main.get_api_provider = lambda provider_id="comfly": self._chat

    def tearDown(self):
        self.main.load_api_providers = self._orig_load
        self.main.public_provider = self._orig_public
        self.main.get_api_provider = self._orig_get

    def _run_convert(self, replies, target="h3-ref2va", provider="modelscope"):
        calls = []

        async def fake_canvas_llm(payload):
            calls.append(payload)
            return {"text": replies[min(len(calls) - 1, len(replies) - 1)]}

        original = self.main.canvas_llm
        self.main.canvas_llm = fake_canvas_llm
        try:
            request = self.main.VideoPromptConvertRequest(
                target=target,
                prompt=XINGHUA_PROMPT,
                duration=15,
                provider=provider,
                model="gpt-4o-mini",
                images=[
                    {"name": "皇帝_ref.png", "url": "u1"},
                    {"name": "甄嬛_ref.png", "url": "u2"},
                ],
            )
            result = asyncio.run(self.main.video_prompt_targets_convert(request))
        finally:
            self.main.canvas_llm = original
        return result, calls

    def test_good_output_passes_first_try(self):
        result, calls = self._run_convert([GOOD_REF2VA])
        self.assertTrue(result["ok"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["prompt"], GOOD_REF2VA)
        self.assertEqual(result["errors"], [])
        self.assertIn("subject_definitions", calls[0].system_prompt)
        self.assertEqual(calls[0].provider, "comfly")
        self.assertEqual(calls[0].images, ["u1", "u2"])
        self.assertIn("原始导演本", calls[0].message)
        self.assertIn("生成语言：英文", calls[0].message)
        self.assertEqual(result.get("language"), "en")
        self.assertNotIn("ir", result)
        self.assertEqual(calls[0].image_captions, ["【图1】皇帝_ref.png · 参考", "【图2】甄嬛_ref.png · 参考"])

    def test_invalid_output_triggers_one_repair(self):
        result, calls = self._run_convert(["这不是合格输出", GOOD_REF2VA])
        self.assertTrue(result["ok"])
        self.assertEqual(len(calls), 2)
        # 修复轮必须携带上一版输出和校验错误
        self.assertEqual(calls[1].messages[-1]["role"], "assistant")
        self.assertIn("这不是合格输出", calls[1].messages[-1]["content"])
        self.assertIn("未通过校验", calls[1].message)

    def test_two_failures_reported_without_derive(self):
        result, calls = self._run_convert(["坏输出一", "坏输出二"])
        self.assertFalse(result["ok"])
        self.assertEqual(len(calls), 2)
        self.assertTrue(result["errors"])
        self.assertEqual(result["prompt"], "坏输出二")

    def test_empty_model_rejected(self):
        from fastapi import HTTPException

        request = self.main.VideoPromptConvertRequest(
            target="h3-ref2va",
            prompt=XINGHUA_PROMPT,
            duration=15,
            provider="comfly",
            model="",
        )
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(self.main.video_prompt_targets_convert(request))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("文字模型", str(ctx.exception.detail))

    def test_unknown_target_rejected(self):
        from fastapi import HTTPException

        request = self.main.VideoPromptConvertRequest(target="h3-t2v", prompt="x", duration=5)
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(self.main.video_prompt_targets_convert(request))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_character_voice_without_audio_is_rejected(self):
        from fastapi import HTTPException

        request = self.main.VideoPromptConvertRequest(
            target="h3-ref2va",
            prompt=XINGHUA_PROMPT,
            duration=15,
            provider="comfly",
            model="gpt-4o-mini",
            character_voice=True,
        )
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(self.main.video_prompt_targets_convert(request))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("样音", str(ctx.exception.detail))

    def test_unmatched_audio_name_rejected_before_llm(self):
        from fastapi import HTTPException

        request = self.main.VideoPromptConvertRequest(
            target="h3-ref2va",
            prompt=XINGHUA_PROMPT,
            duration=15,
            provider="comfly",
            model="gpt-4o-mini",
            audios=[{"name": "音色", "url": "http://x/a.mp3"}],
        )
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(self.main.video_prompt_targets_convert(request))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("角色名", str(ctx.exception.detail))

    def test_character_voice_on_fl2va_is_rejected(self):
        from fastapi import HTTPException

        request = self.main.VideoPromptConvertRequest(
            target="h3-fl2va",
            prompt=XINGHUA_PROMPT,
            duration=15,
            provider="comfly",
            model="gpt-4o-mini",
            character_voice=True,
            audios=[{"name": "a.mp3", "url": "http://x/a.mp3"}],
        )
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(self.main.video_prompt_targets_convert(request))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("多参", str(ctx.exception.detail))

    def test_ref_video_on_fl2va_is_rejected(self):
        from fastapi import HTTPException

        request = self.main.VideoPromptConvertRequest(
            target="h3-fl2va",
            prompt=XINGHUA_PROMPT,
            duration=15,
            provider="comfly",
            model="gpt-4o-mini",
            videos=[{"name": "walk.mp4", "url": "http://x/v.mp4"}],
        )
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(self.main.video_prompt_targets_convert(request))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("多参", str(ctx.exception.detail))

    def test_targets_endpoint_lists_phase_one(self):
        result = asyncio.run(self.main.video_prompt_targets_list())
        ids = [item["id"] for item in result["targets"]]
        self.assertEqual(ids, ["seedance-2.0", "seedance-2.5", "h3-ref2va", "h3-fl2va"])


if __name__ == "__main__":
    unittest.main()
