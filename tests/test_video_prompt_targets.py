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


def xinghua_ir(duration=15, images=TWO_IMAGES):
    return vpt.extract_canvas_ir(XINGHUA_PROMPT, images, duration)


GOOD_REF2VA = """subject_definitions: <Subject 1> is a middle-aged emperor in a dark golden dragon robe from <Picture 1>. <Subject 2> is a young consort in a pale blue palace dress from <Picture 2>.
summary: In an imperial garden at dusk, the consort greets the emperor and they enjoy the apricot blossoms together.
retention_analysis: <Subject 1> must keep the same facial identity and golden robe. <Subject 2> must keep the same facial identity and pale blue dress.
detailed_description: [Shot 1] In a palace garden with apricot trees, <Subject 1> sits at a stone table reading memorials. The camera pushes in with small amplitude at slow speed. [Shot 2] At 00:04.000, the camera cuts to <Subject 2> walking in and bowing. (S1) <d>[Chinese] 臣妾参见皇上。</d> [Shot 3] At 00:08.000, <Subject 1> looks up and smiles. (S2) <d>[Chinese] 免礼。</d> [Shot 4] At 00:11.500, they sit together talking. (S1) <d>[Chinese] 今日杏花开得正好。</d> (S2) <d>[Chinese] 与你同赏。</d>
overall_soundscape: Soft garden ambience, birdsong, light breeze.
non_diegetic_music: N/A"""


class ExtractIrTests(unittest.TestCase):
    def test_xinghua_prompt_extraction(self):
        ir = xinghua_ir()
        self.assertEqual(len(ir["shots"]), 4)
        # 开场铺垫并入第一镜
        self.assertIn("杏花盛开的御花园", ir["shots"][0]["action"])
        self.assertIn("推进", ir["shots"][0]["camera"])
        dialogues = [d["text"] for shot in ir["shots"] for d in shot["dialogue"]]
        self.assertEqual(dialogues, ["臣妾参见皇上。", "免礼。", "今日杏花开得正好。", "与你同赏。"])
        subjects = {item["id"]: item["image"] for item in ir["subjects"]}
        self.assertEqual(subjects, {"皇帝": "图1", "甄嬛": "图2"})
        self.assertTrue(all(entry["referenced"] for entry in ir["images"]))
        self.assertIn("柔光摄影 / 8K超高清", ir["style"])
        self.assertEqual(ir["warnings"], [])
        self.assertIn("皇帝@图1", ir["source_prompt"])
        self.assertIsNone(ir["shots"][0]["at_s"])
        self.assertEqual(ir["shots"][1]["at_s"], 3.75)
        self.assertEqual(ir["shots"][2]["at_s"], 7.5)
        self.assertEqual(ir["shots"][3]["at_s"], 11.25)

    def test_out_of_range_image_reference_warns(self):
        ir = vpt.extract_canvas_ir("皇帝@图1 与甄嬛@图3 对话", TWO_IMAGES, 10)
        self.assertTrue(any("@图3" in w for w in ir["warnings"]))
        subjects = {item["id"]: item["image"] for item in ir["subjects"]}
        self.assertIsNone(subjects["甄嬛"])

    def test_filename_reference_binds_subject(self):
        prompt = "（@AD-001_storyboard.png）为9宫格分镜参考图，（@阿川_ref.png）为角色「阿川」的参考形象，（@遗失.png）为角色「小夏」的参考形象"
        images = [
            {"name": "AD-001_storyboard.png", "url": "u1"},
            {"name": "阿川_ref.png", "url": "u2"},
        ]
        ir = vpt.extract_canvas_ir(prompt, images, 10)
        subjects = {item["id"]: item["image"] for item in ir["subjects"]}
        self.assertEqual(subjects["阿川"], "图2")
        self.assertIsNone(subjects["小夏"])
        self.assertTrue(any("遗失.png" in w for w in ir["warnings"]))
        self.assertTrue(ir["images"][0]["referenced"])

    def test_unreferenced_upload_warns(self):
        ir = vpt.extract_canvas_ir("皇帝@图1 独自饮茶", TWO_IMAGES, 10)
        self.assertTrue(any("图2" in w and "未在词中引用" in w for w in ir["warnings"]))

    def test_unstructured_prompt_warns_incomplete_binding(self):
        ir = vpt.extract_canvas_ir("一只猫在追蝴蝶", TWO_IMAGES, 10)
        self.assertEqual(len(ir["shots"]), 1)
        self.assertTrue(any("绑定不完整" in w for w in ir["warnings"]))


class MessageBuildTests(unittest.TestCase):
    def test_targets_listed_in_button_order(self):
        ids = [item["id"] for item in vpt.list_video_prompt_targets()]
        self.assertEqual(ids, ["seedance-2.0", "seedance-2.5", "h3-ref2va", "h3-fl2va"])
        presets = {item["id"]: item["preset"] for item in vpt.list_video_prompt_targets()}
        self.assertEqual(presets["h3-ref2va"], {"multimodal": True})
        self.assertEqual(presets["h3-fl2va"], {"frame_roles": True})

    def test_convert_messages_carry_skill_and_ir(self):
        ir = xinghua_ir()
        messages = vpt.build_convert_messages("h3-ref2va", ir)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("subject_definitions", messages[0]["content"])
        self.assertIn("原始导演本", messages[1]["content"])
        self.assertIn("皇帝@图1", messages[1]["content"])
        self.assertIn("图1 / <Picture 1> = 皇帝_ref.png", messages[1]["content"])
        self.assertIn("臣妾参见皇上。", messages[1]["content"])
        self.assertIn("只输出该目标的提示词正文", messages[1]["content"])
        self.assertIn("生成语言：英文", messages[0]["content"])
        self.assertIn("生成语言：英文", messages[1]["content"])
        self.assertNotIn("http://x/1.png", messages[1]["content"])

    def test_convert_messages_honor_chinese_output(self):
        ir = xinghua_ir()
        messages = vpt.build_convert_messages("h3-ref2va", ir, language="zh")
        self.assertTrue(messages[0]["content"].startswith("生成语言：中文"))
        self.assertIn("不要因为 skill 样例是英文就改回英文", messages[0]["content"])
        self.assertIn("生成语言：中文", messages[1]["content"])
        self.assertIn("不要翻译", messages[1]["content"])
        self.assertNotIn("正文全英文", messages[0]["content"])
        self.assertEqual(vpt.normalize_output_language("中文"), "zh")
        self.assertEqual(vpt.normalize_output_language(""), "en")
        self.assertGreater(vpt._content_units("镜头从门口缓缓推入客厅，保持户型图的空间顺序。" * 8), 200)

    def test_convert_image_urls_keep_slot_order(self):
        self.assertEqual(
            vpt.convert_image_urls(TWO_IMAGES + [{"name": "空", "url": "  "}]),
            ["http://x/1.png", "http://x/2.png"],
        )

    def test_non_vision_model_warns_but_still_lists_images(self):
        ir = xinghua_ir()
        warnings = vpt.convert_input_warnings(ir, "gpt-3.5-turbo", ["http://x/1.png"])
        self.assertTrue(any("看不到附图" in item for item in warnings))
        self.assertTrue(vpt.chat_model_likely_sees_images("gpt-4o-mini"))
        self.assertFalse(vpt.chat_model_likely_sees_images("gpt-3.5-turbo"))

    def test_repair_messages_append_errors(self):
        ir = xinghua_ir()
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
        result = vpt.validate_target_output("h3-ref2va", GOOD_REF2VA, xinghua_ir())
        self.assertEqual(result["errors"], [])

    def test_missing_section_fails(self):
        bad = GOOD_REF2VA.replace("retention_analysis:", "retention:")
        result = vpt.validate_target_output("h3-ref2va", bad, xinghua_ir())
        self.assertTrue(any("retention_analysis" in e for e in result["errors"]))

    def test_picture_out_of_range_fails(self):
        bad = GOOD_REF2VA.replace("<Picture 2>", "<Picture 3>")
        result = vpt.validate_target_output("h3-ref2va", bad, xinghua_ir())
        self.assertTrue(any("<Picture 3>" in e for e in result["errors"]))

    def test_modified_dialogue_fails(self):
        bad = GOOD_REF2VA.replace("臣妾参见皇上。", "臣妾拜见皇上。")
        result = vpt.validate_target_output("h3-ref2va", bad, xinghua_ir())
        self.assertTrue(any("改写" in e for e in result["errors"]))
        self.assertFalse(any("缺失" in e for e in result["errors"]))

    def test_dropping_dialogue_is_allowed(self):
        dropped = GOOD_REF2VA.replace(" (S1) <d>[Chinese] 今日杏花开得正好。</d> (S2) <d>[Chinese] 与你同赏。</d>", "")
        result = vpt.validate_target_output("h3-ref2va", dropped, xinghua_ir())
        self.assertEqual(result["errors"], [])

    def test_no_identified_subjects_fails_when_images_exist(self):
        bad = GOOD_REF2VA.replace(
            "<Subject 1> is a middle-aged emperor in a dark golden dragon robe from <Picture 1>. <Subject 2> is a young consort in a pale blue palace dress from <Picture 2>.",
            "No identified subjects.",
        )
        result = vpt.validate_target_output("h3-ref2va", bad, xinghua_ir())
        self.assertTrue(any("No identified subjects" in e for e in result["errors"]))

    def test_multiple_subjects_from_one_picture_allowed(self):
        ir = vpt.extract_canvas_ir("从入户走到所有房间", [{"name": "户型.png", "url": "u1"}], 15)
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

    def test_leftover_canvas_syntax_fails(self):
        bad = GOOD_REF2VA.replace("<Picture 1>", "@图1")
        result = vpt.validate_target_output("h3-ref2va", bad, xinghua_ir())
        self.assertTrue(any("@图N" in e for e in result["errors"]))

    def test_time_code_over_duration_fails(self):
        bad = GOOD_REF2VA.replace("At 00:11.500", "At 00:16.000")
        result = vpt.validate_target_output("h3-ref2va", bad, xinghua_ir(duration=15))
        self.assertTrue(any("超出时长" in e for e in result["errors"]))

    def test_time_code_not_increasing_fails(self):
        bad = GOOD_REF2VA.replace("At 00:08.000", "At 00:03.000")
        result = vpt.validate_target_output("h3-ref2va", bad, xinghua_ir())
        self.assertTrue(any("递增" in e for e in result["errors"]))


class ValidateFl2vaTests(unittest.TestCase):
    def _ir(self, with_last=False):
        images = [{"name": "first.png", "url": "u1", "role": "first_frame"}]
        if with_last:
            images.append({"name": "last.png", "url": "u2", "role": "last_frame"})
        return vpt.extract_canvas_ir("皇帝在御花园饮茶\n台词：皇帝：「春色正好。」", images, 10)

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

    def test_subject_syntax_forbidden(self):
        bad = self._good(vpt.fl2va_align_first()).replace("The emperor", "<Subject 1>")
        result = vpt.validate_target_output("h3-fl2va", bad, self._ir())
        self.assertTrue(any("<Subject>" in e for e in result["errors"]))

    def test_dropping_dialogue_is_allowed(self):
        text = self._good(vpt.fl2va_align_first()).replace(" (S1) <d>[Chinese] 春色正好。</d>", "")
        result = vpt.validate_target_output("h3-fl2va", text, self._ir())
        self.assertEqual(result["errors"], [])


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
        result = vpt.validate_target_output("seedance-2.5", self.GOOD_25, xinghua_ir())
        self.assertEqual(result["errors"], [])

    def test_merged_frame_declaration_fails(self):
        bad = "@Images 1 and 2 are the first and last frames.\n" + self.GOOD_25
        result = vpt.validate_target_output("seedance-2.5", bad, xinghua_ir())
        self.assertTrue(any("合并声明" in e for e in result["errors"]))

    def test_image_out_of_range_fails(self):
        bad = self.GOOD_25.replace("(@Image 2)", "(@Image 5)")
        result = vpt.validate_target_output("seedance-2.5", bad, xinghua_ir())
        self.assertTrue(any("@Image 5" in e for e in result["errors"]))

    def test_h3_syntax_forbidden(self):
        bad = self.GOOD_25 + "\n[Shot 2] At 00:04.000, the camera cuts."
        result = vpt.validate_target_output("seedance-2.5", bad, xinghua_ir())
        self.assertTrue(any("H3 语法" in e for e in result["errors"]))

    def test_first_frame_declaration_required_when_role_present(self):
        images = [
            {"name": "first.png", "url": "u1", "role": "first_frame"},
            {"name": "ref.png", "url": "u2"},
        ]
        ir = vpt.extract_canvas_ir("皇帝@图1 起身，@图2 为氛围参考", images, 10)
        text = "The emperor (@Image 1) stands up slowly in a garden. Warm light. The camera holds still. Quiet ambience."
        result = vpt.validate_target_output("seedance-2.5", text, ir)
        self.assertTrue(any("首帧声明" in e for e in result["errors"]))
        result20 = vpt.validate_target_output("seedance-2.0", text, ir)
        self.assertTrue(any("首帧声明" in e for e in result20["errors"]))

    def test_seedance_20_accepts_first_frame_declaration(self):
        images = [{"name": "first.png", "url": "u1", "role": "first_frame"}]
        ir = vpt.extract_canvas_ir("皇帝起身", images, 8)
        text = (
            "@Image 1 is the first frame. It defines the opening composition, subject position, pose, and camera direction.\n"
            "The emperor stands and walks forward. Quiet ambience."
        )
        result = vpt.validate_target_output("seedance-2.0", text, ir)
        self.assertEqual(result["errors"], [])

    def test_seedance_20_word_limit_warns(self):
        long_text = "The emperor (@Image 1) walks. " + ("very slowly and gracefully through the garden " * 40)
        result = vpt.validate_target_output("seedance-2.0", long_text, xinghua_ir())
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("上限 200" in w for w in result["warnings"]))


class PickChatProviderTests(unittest.TestCase):
    def test_skips_modelscope_and_video_only(self):
        pick = vpt.pick_chat_provider([
            {"id": "modelscope", "enabled": True, "chat_models": ["Qwen"], "has_key": True},
            {"id": "h3-local", "protocol": "h3", "enabled": True, "chat_models": ["minimax-h3"]},
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

    def test_targets_endpoint_lists_phase_one(self):
        result = asyncio.run(self.main.video_prompt_targets_list())
        ids = [item["id"] for item in result["targets"]]
        self.assertEqual(ids, ["seedance-2.0", "seedance-2.5", "h3-ref2va", "h3-fl2va"])


if __name__ == "__main__":
    unittest.main()
