"""画布出片提示词适配。

改词只吃导演本原文、目标 skill、时长、生成语言、图槽清单和按槽位附图。
图槽直接来自本次上传列表，不抽中间稿。本模块只做纯逻辑：图槽、消息构造、
校验；AI 调用由 main.py 的转换端点走既有文本模型通道完成。
"""

import re
from pathlib import Path

import minimax_speech_protocol as minimax_speech

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts" / "video-targets"

# 顺序即前端按钮顺序。model_hints 只用于派生工作台的默认选中（软默认，可改），
# 不做能力分类；preset 是派生工作台需要预置的勾选。
# 多图参考目标统一勾全能参考，首尾帧目标统一勾首尾帧；不按中转站分支。
VIDEO_PROMPT_TARGETS = {
    "seedance-2.0": {
        "label": "2.0提示词",
        "group": "seedance优化",
        "skill": "seedance-2.0.md",
        "family": "seedance",
        "model_hints": ["sd2.0", "seedance-2.0", "seedance2.0", "seedance_2.0", "seedance-v2"],
        "preset": {"multimodal": True},
    },
    "seedance-2.5": {
        "label": "2.5提示词",
        "group": "seedance优化",
        "skill": "seedance-2.5.md",
        "family": "seedance",
        "model_hints": ["sd2.5", "seedance-2.5", "seedance2.5", "seedance_2.5", "seedance-v2.5"],
        "preset": {"multimodal": True},
    },
    "h3-ref2va": {
        "label": "多参提示词",
        "group": "minimax优化",
        "skill": "h3-ref2va.md",
        "family": "h3",
        "model_hints": [],
        "preset": {"multimodal": True},
    },
    "h3-fl2va": {
        "label": "首尾帧提示词",
        "group": "minimax优化",
        "skill": "h3-fl2va.md",
        "family": "h3",
        "model_hints": [],
        "preset": {"frame_roles": True},
    },
}


# 转换走文字聊天通道，不走 ModelScope / 纯视频协议。
CHAT_CONVERT_BLOCKED_IDS = {"modelscope"}
CHAT_CONVERT_BLOCKED_PROTOCOLS = {"h3", "codelba"}


def is_usable_chat_provider(provider):
    if not isinstance(provider, dict):
        return False
    if provider.get("enabled", True) is False:
        return False
    provider_id = str(provider.get("id") or "").strip().lower()
    protocol = str(provider.get("protocol") or "").strip().lower()
    if provider_id in CHAT_CONVERT_BLOCKED_IDS or protocol in CHAT_CONVERT_BLOCKED_PROTOCOLS:
        return False
    if minimax_speech.is_minimax_official_protocol(protocol):
        chat_models = [
            str(model or "").strip()
            for model in (provider.get("chat_models") or [])
            if minimax_speech.is_minimax_chat_model(model)
        ]
        if not chat_models:
            return False
    return bool(provider.get("chat_models"))


def pick_chat_provider(providers, requested_id=""):
    """选一个能走 /chat/completions 的文字平台。优先用户指定，否则第一个可用。"""
    items = [item for item in (providers or []) if isinstance(item, dict)]
    requested = str(requested_id or "").strip().lower()
    if requested:
        match = next((item for item in items if str(item.get("id") or "").strip().lower() == requested and is_usable_chat_provider(item)), None)
        if match:
            return match
    keyed = next((item for item in items if is_usable_chat_provider(item) and item.get("has_key")), None)
    if keyed:
        return keyed
    return next((item for item in items if is_usable_chat_provider(item)), None)


def list_video_prompt_targets():
    items = []
    for target_id, spec in VIDEO_PROMPT_TARGETS.items():
        items.append({
            "id": target_id,
            "label": spec["label"],
            "group": spec.get("group") or spec["family"],
            "family": spec["family"],
            "model_hints": list(spec["model_hints"]),
            "preset": dict(spec["preset"]),
        })
    return items


def load_target_skill(target_id):
    spec = VIDEO_PROMPT_TARGETS.get(str(target_id or "").strip())
    if not spec:
        raise KeyError(f"未知的提示词目标：{target_id}")
    path = PROMPTS_DIR / spec["skill"]
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 图槽（画布硬事实，直接来自本次上传列表）
# ---------------------------------------------------------------------------

def normalize_output_language(value):
    return "zh" if str(value or "").strip().lower() in {"zh", "zh-cn", "cn", "chinese", "中文"} else "en"


def _normalize_image_role(value):
    role = str(value or "").strip().lower()
    if role in {"first_frame", "first"}:
        return "first_frame"
    if role in {"last_frame", "last", "end_frame"}:
        return "last_frame"
    if role in {"", "reference", "ref"}:
        return "reference"
    return role


FIRST_LAST_IMAGE_MAX = 2
FIRST_LAST_FRAME_ROLES = {"first_frame", "last_frame"}


def image_slot_count(images):
    count = 0
    for item in images or []:
        if isinstance(item, str):
            if item.strip():
                count += 1
            continue
        row = item or {}
        if str(row.get("url") or "").strip() or str(row.get("name") or "").strip():
            count += 1
    return count


def has_first_last_roles(images):
    for item in images or []:
        role = ""
        if isinstance(item, dict):
            role = item.get("role")
        else:
            role = getattr(item, "role", "")
        if _normalize_image_role(role) in FIRST_LAST_FRAME_ROLES:
            return True
    return False


def first_last_extra_images_message(count, action="生成"):
    return (
        f"首尾帧只接受最多 {FIRST_LAST_IMAGE_MAX} 张图（图1 首帧、图2 尾帧），"
        f"当前 {count} 张。请去掉多余参考图后再{action}，或改用「多参提示词」。"
    )


def reject_first_last_extra_images(images, *, target="", require_roles=False, action=""):
    """首尾帧超过 2 张图时返回提示，否则空串。不看中转站，只看项目合同。"""
    is_fl2va = str(target or "").strip() == "h3-fl2va"
    if is_fl2va:
        verb = action or "转换"
    elif require_roles:
        if not has_first_last_roles(images):
            return ""
        verb = action or "生成"
    else:
        return ""
    count = image_slot_count(images)
    if count > FIRST_LAST_IMAGE_MAX:
        return first_last_extra_images_message(count, verb)
    return ""


def build_convert_context(prompt, images, duration_s, audios=None, character_voice=False):
    """导演本原文 + 上传图槽。不抽镜头、台词、人物或风格。"""
    entries = []
    for index, item in enumerate(images or [], start=1):
        row = dict(item or {})
        entries.append({
            "index": index,
            "slot": f"图{index}",
            "name": str(row.get("name") or ""),
            "role": _normalize_image_role(row.get("role")),
            "url": str(row.get("url") or ""),
        })
    audio_entries = []
    for index, item in enumerate(audios or [], start=1):
        row = dict(item or {})
        url = str(row.get("url") or "").strip()
        if not url:
            continue
        audio_entries.append({
            "index": len(audio_entries) + 1,
            "slot": f"音{len(audio_entries) + 1}",
            "name": str(row.get("name") or ""),
            "role": "character_voice" if character_voice and not audio_entries else "reference",
            "url": url,
        })
    return {
        "source_prompt": str(prompt or ""),
        "duration_s": duration_s,
        "images": entries,
        "audios": audio_entries,
        "character_voice": bool(character_voice),
    }


def image_role_label(role, language="zh"):
    wanted = normalize_output_language(language)
    key = _normalize_image_role(role)
    if key == "first_frame":
        return "首帧" if wanted == "zh" else "first_frame"
    if key == "last_frame":
        return "尾帧" if wanted == "zh" else "last_frame"
    return "参考" if wanted == "zh" else "reference"


def convert_image_urls(images):
    """抽出可送给文字通道的参考图地址，顺序即图1、图2。不把地址写进提示词正文。"""
    urls = []
    for item in images or []:
        url = str((item or {}).get("url") or "").strip()
        if url:
            urls.append(url)
    return urls


def convert_image_attachments(ctx):
    """(url, 【图N】说明) 成对，空地址不进转换通道，避免说明贴到下一张图。"""
    pairs = []
    for item in ctx.get("images") or []:
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        name = str(item.get("name") or "").strip() or "(未命名)"
        caption = f"【图{item.get('index')}】{name} · {image_role_label(item.get('role'), 'zh')}"
        pairs.append((url, caption))
    return pairs


def convert_image_captions(ctx):
    """转换通道附图前的说明，与 convert_image_attachments 同一批图。不出片请求体。"""
    return [caption for _url, caption in convert_image_attachments(ctx)]


def image_inventory_lines(ctx, target_id="", language="en"):
    spec = VIDEO_PROMPT_TARGETS.get(str(target_id or "").strip()) or {}
    family = spec.get("family")
    lang = normalize_output_language(language)
    lines = []
    for item in ctx.get("images") or []:
        index = item.get("index") or (len(lines) + 1)
        name = str(item.get("name") or "").strip() or "(未命名)"
        if family == "h3":
            role = image_role_label(item.get("role"), "zh")
            lines.append(f"- 图{index} = <Picture {index}> = {name} （{role}）")
        elif lang == "zh":
            role = image_role_label(item.get("role"), "zh")
            lines.append(f"- 图{index} = @图片{index} = {name} （{role}）")
        else:
            role = image_role_label(item.get("role"), "en")
            lines.append(f"- Image {index} = @Image{index} = {name} ({role})")
    return lines


def audio_inventory_lines(ctx):
    lines = []
    character_voice = bool(ctx.get("character_voice"))
    for item in ctx.get("audios") or []:
        index = item.get("index") or (len(lines) + 1)
        name = str(item.get("name") or "").strip() or "(未命名)"
        if character_voice and index == 1:
            lines.append(f"- 音{index} = <Audio {index}> = {name} （角色音色，按名称匹配对应角色图 / Subject；对不上时绑正在说话的那个人）")
        else:
            lines.append(f"- 音{index} = <Audio {index}> = {name} （参考音频）")
    return lines


# ---------------------------------------------------------------------------
# 消息构造
# ---------------------------------------------------------------------------

# 只用于「当前模型多半看不到附图」的软提示，不拿来拦转换、不拿来猜视频能力。
_VISION_MODEL_HINTS = (
    "vision", "vl-", "-vl-", "internvl", "qvq", "qwen-vl", "qwen3-vl",
    "gpt-4o", "gpt-4.1", "gpt-5", "claude", "gemini", "glm-4v", "minicpm-v",
)


def chat_model_likely_sees_images(model):
    lc = str(model or "").strip().lower()
    return bool(lc) and any(key in lc for key in _VISION_MODEL_HINTS)


def convert_input_warnings(ctx, model, image_urls):
    warnings = []
    if image_urls and not chat_model_likely_sees_images(model):
        warnings.append("当前文字模型多半看不到附图，只会读文件名和导演本；要看图写词请换视觉模型")
    return warnings


def _target_family(target_id):
    spec = VIDEO_PROMPT_TARGETS.get(str(target_id or "").strip()) or {}
    return spec.get("family")


def output_language_instruction(language, target_id=""):
    family = _target_family(target_id)
    if normalize_output_language(language) == "zh":
        if family == "seedance":
            return (
                "生成语言：中文。整份描写只能用中文，禁止中英混写描写。"
                "概述、情节、结尾必须是中文句子。"
                "要写「黄昏御花园，皇帝批折」，禁止写成 “At dusk in an imperial garden”。"
                "Seedance 主标签用 @图片N，也认 @ImageN / @Image N / @image1。"
                "首尾帧声明写「@图片1 作为首帧，定义开场构图、站位、姿态和镜头方向。」"
                "不要强迫写成英文 is the first frame。"
                "不要因为 skill 里有英文样例就改回英文；英文样例只在选英文时用。"
                "台词、牌匾、画面可见原文保持原语言。"
            )
        return (
            "生成语言：中文。整份描写只能用中文，禁止中英混写描写。"
            "subject_definitions / summary / retention_analysis / detailed_description / "
            "overall_soundscape / integrated_multimodal_description "
            "必须是中文句子。"
            "要写「<Subject 1> 是……」「目标视频采用……」「皇帝坐在石桌旁」，"
            "禁止写成 “<Subject 1> is the ...” / “A cinematic ...” / “The target video is ...” / "
            "“The emperor sits ...”。"
            "不要因为 skill 里有英文样例就改回英文；英文样例只在选英文时用。"
            "段名、对齐行、标签和协议词必须保持英文，不要翻译："
            "subject_definitions / summary / [reference generation] / fully_preserved / "
            "<Picture N> / <Subject N> / [Shot N] / At MM:SS.mmm。"
            "台词、牌匾、画面可见原文保持原语言。"
        )
    if family == "seedance":
        return (
            "生成语言：英文。整份描写只能用英文，禁止中英混写描写。"
            "概述、情节、结尾必须是英文句子。"
            "要写 “The emperor sits ...”，禁止写成「皇帝坐在石桌旁」。"
            "Seedance 主标签用 @ImageN，也认 @Image N / @图片N。"
            "首尾帧声明写 “@Image1 as the first frame. It defines the opening composition, "
            "subject position, pose, and camera direction.”"
            "不要因为 skill 里有中文样例就改回中文；中文样例只在选中文时用。"
            "台词、牌匾、画面可见原文保持原语言。"
        )
    return (
        "生成语言：英文。整份描写只能用英文，禁止中英混写描写。"
        "subject_definitions / summary / retention_analysis / detailed_description / "
        "overall_soundscape / integrated_multimodal_description "
        "必须是英文句子。"
        "要写 “<Subject 1> is ...” / “The target video is ...” / “The emperor sits ...”，"
        "禁止写成「<Subject 1> 是……」「目标视频采用……」「皇帝坐在石桌旁」。"
        "不要因为 skill 里有中文样例就改回中文；中文样例只在选中文时用。"
        "段名、对齐行、标签和协议词保持 skill 规定的英文。"
        "台词、牌匾、画面可见原文保持原语言。"
    )


def build_convert_messages(target_id, ctx, source_prompt="", language="en"):
    # 语言指令放进 system，避免 skill 里的样例压过用户消息。
    system = output_language_instruction(language, target_id) + "\n\n" + load_target_skill(target_id)
    prompt_text = str(source_prompt or ctx.get("source_prompt") or "").strip()
    inventory = image_inventory_lines(ctx, target_id, language)
    inventory_text = "\n".join(inventory) if inventory else "（没有参考图）"
    audio_lines = audio_inventory_lines(ctx)
    audio_text = "\n".join(audio_lines) if audio_lines else "（没有参考音频）"
    captions = convert_image_captions(ctx)
    caption_text = "\n".join(captions) if captions else "（没有附图）"
    lang_name = "中文" if normalize_output_language(language) == "zh" else "英文"
    character_voice = bool(ctx.get("character_voice")) and str(target_id or "") == "h3-ref2va"
    voice_flag = "角色音色：开" if character_voice else "角色音色：关"
    if character_voice:
        voice_rule = (
            "角色音色已开启。音1 是人物样音，名称是角色线索，不是这一句台词的成片配音。"
            "必须在 subject_definitions 把 <Audio 1> 绑到名称或外观与该样音名称匹配的 Subject，"
            "写「<Audio 1> 是 <Subject N> 的音色参考，只借声线和语速，不复用原词」。"
            "如果样音名叫「少女音色」或「林小夏」，就绑到对应的少女 / 林小夏 Subject，不要一律绑 <Subject 1>。"
            "只有一个人物，或名称对不上任何图时，绑正在说话的那个人。"
            "summary 必须以 [reference generation + audio reference] 开头（可再拼 keyframe completion），禁止写成 audio reuse。"
            "retention_analysis 增加 <Audio 1>: reference - 用于对应角色新对白的音色。"
            "开口的人仍用 <Subject N> (S1) says: <d>[Chinese] 原文</d>，用 Audio 1 的声，不要另编一条声。"
            "不要把样音写成 BGM。"
        )
    else:
        voice_rule = (
            "角色音色未开启。不要写 <Audio N>，不要写 audio reference / audio reuse。"
        )
    user = (
        f"目标：{target_id}\n"
        f"时长（秒）：{ctx.get('duration_s')}\n"
        f"{voice_flag}\n"
        f"{voice_rule}\n"
        f"{output_language_instruction(language, target_id)}\n\n"
        "原始导演本：\n"
        f"{prompt_text}\n\n"
        "参考图槽位（与附图顺序一致；正文必须用这些槽位绑定，不要改文件名）：\n"
        f"{inventory_text}\n\n"
        "参考音频槽位：\n"
        f"{audio_text}\n\n"
        "附图已按槽位顺序附在本条消息里，每张图前有【图N】说明：\n"
        f"{caption_text}\n"
        "请看图写词。"
        "户型图、场景图、物体图也要定义成 Subject / Picture / 对应标签，禁止写 No identified subjects。\n"
        f"本轮描写语言只能是{lang_name}，不要混用另一份样例的语言。\n"
        "只输出该目标的提示词正文，不要解释，不要代码块围栏。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_repair_messages(target_id, ctx, previous_output, errors, language="en"):
    messages = build_convert_messages(target_id, ctx, language=language)
    messages.append({"role": "assistant", "content": previous_output})
    lang_name = "中文" if normalize_output_language(language) == "zh" else "英文"
    messages.append({
        "role": "user",
        "content": (
            f"上一版未通过校验，问题如下：\n- " + "\n- ".join(errors)
            + f"\n生成语言必须仍是{lang_name}，只改描写语言和结构错误，不要改成另一种语言。"
            + "\n请修正以上问题，重新只输出提示词正文。"
        ),
    })
    return messages


def strip_model_output(text):
    """去掉模型可能包的代码块围栏与首尾空白。"""
    value = str(text or "").strip()
    fence = re.match(r"^```[a-zA-Z0-9_-]*\n(.*?)\n?```$", value, re.DOTALL)
    if fence:
        value = fence.group(1).strip()
    return value


# ---------------------------------------------------------------------------
# 校验（纯规则）
# ---------------------------------------------------------------------------

_TIME_CODE = re.compile(r"\bAt\s+(\d{1,2}):(\d{2})\.(\d{3})", re.IGNORECASE)
_D_TAG = re.compile(r"<d>\s*\[Chinese\]\s*(.*?)\s*</d>", re.DOTALL | re.IGNORECASE)
_PICTURE_TAG = re.compile(r"<Picture\s+(\d{1,2})>", re.IGNORECASE)
_SUBJECT_TAG = re.compile(r"<Subject\s+(\d{1,2})>", re.IGNORECASE)
_AUDIO_TAG = re.compile(r"<Audio\s+(\d{1,2})>", re.IGNORECASE)
# 官方中文 @图片N / 兼容 @图像N / 英文 @ImageN / @Image N
_SEEDANCE_REF = re.compile(r"@(?:图片|图像|Image)\s*(\d{1,2})", re.IGNORECASE)
_CANVAS_AT_TU = re.compile(r"@图(?!片|像)\s*\d{1,2}")
_AT_IMAGE_MERGED = re.compile(
    r"@(?:Images?|图片|图像)\s*\d{1,2}\s*(?:,|and|和|与)\s*(?:@(?:Image|图片|图像)\s*)?\d{1,2}\s+(?:are|is|作为|是)",
    re.IGNORECASE,
)
_SEEDANCE_SEC_RANGE = re.compile(r"(?<![\d:])(\d{1,2})\s*[-–~到至]\s*(\d{1,2})\s*(?:s|sec|秒)\b", re.IGNORECASE)
_SEEDANCE_SEC_POINT = re.compile(r"(?:第\s*(\d{1,2})\s*(?:s|sec|秒)|(\d{1,2})\s*(?:s|sec|秒)\s*后)", re.IGNORECASE)
_SUMMARY_TASK_TYPES = (
    "reference generation",
    "keyframe completion",
    "video editing",
    "video continuation",
    "audio reuse",
    "audio reference",
)
_CJK_QUOTED = re.compile(r"[\"“「]([^\"”」]{1,200})[\"”」]")
_NO_IDENTIFIED_SUBJECTS = re.compile(r"no identified subjects", re.IGNORECASE)
_H3_REF2VA_SECTIONS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)
_RETENTION_MARKERS = (
    "fully_preserved",
    "partially_preserved",
    "attribute_transfer",
    "weak_reference",
    "fully_copy",
    "partially_copy",
)
_SUBJECT_INDEX_MAX = 20


def _norm_dialogue(text):
    return re.sub(r"\s+", "", str(text or "").strip())


def _cjk_count(text):
    return len(re.findall(r"[\u4e00-\u9fff]", str(text or "")))


def _has_cjk(text):
    return re.search(r"[\u4e00-\u9fff]", text) is not None


def _content_units(text):
    """拉丁词 + 汉字，避免中文描写被英文词数误判为过短。"""
    latin = len(re.findall(r"[A-Za-z']+", str(text or "")))
    cjk = len(re.findall(r"[\u4e00-\u9fff]", str(text or "")))
    return latin + cjk


def _check_time_codes(text, duration_s, errors):
    last = -1.0
    for match in _TIME_CODE.finditer(text):
        seconds = int(match.group(1)) * 60 + int(match.group(2)) + int(match.group(3)) / 1000.0
        if duration_s and seconds >= float(duration_s):
            errors.append(f"时间码 {match.group(0)} 超出时长 {duration_s} 秒")
        if seconds <= last:
            errors.append(f"时间码 {match.group(0)} 没有严格递增")
        last = seconds


def _source_dialogues(ctx):
    """台词只对照导演本原文引号句，不对照任何抽取结果。"""
    allowed = []
    for match in _CJK_QUOTED.finditer(str(ctx.get("source_prompt") or "")):
        quote = _norm_dialogue(match.group(1))
        if quote and _has_cjk(quote):
            allowed.append(quote)
    return list(dict.fromkeys(allowed))


def _quote_in_source_prompt(norm, ctx):
    """允许模型把未闭合长引号拆成原文里已有的短句。"""
    if _cjk_count(norm) < 4:
        return False
    return bool(norm) and norm in _norm_dialogue(ctx.get("source_prompt") or "")


def _check_output_dialogues(found, ctx, errors, warnings=None):
    """台词只准原样保留或按时长舍弃，不准改写、不准凭空新增。"""
    allowed = _source_dialogues(ctx)
    normalized_found = [_norm_dialogue(text) for text in found]
    for raw, norm in zip(found, normalized_found):
        if not norm or not allowed:
            continue
        if norm not in allowed and not _quote_in_source_prompt(norm, ctx):
            errors.append(f"台词被改写或凭空新增：{raw}")
    if warnings is not None and allowed and not normalized_found:
        warnings.append("导演本有台词，输出里全部舍弃了")


def _image_count(ctx):
    return len(ctx.get("images") or [])


def _section_body(text, name, next_names):
    match = re.search(rf"^{name}\s*:", text, re.MULTILINE | re.IGNORECASE)
    if not match:
        return ""
    rest = text[match.end():]
    if next_names:
        nxt = re.search(r"^(" + "|".join(re.escape(item) for item in next_names) + r")\s*:", rest, re.MULTILINE | re.IGNORECASE)
        if nxt:
            rest = rest[:nxt.start()]
    return rest.strip()


def fl2va_has_last_frame(ctx):
    roles = {_normalize_image_role(item.get("role")) for item in ctx.get("images") or []}
    return "last_frame" in roles


def fl2va_align_first():
    return "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."


def fl2va_align_both(ctx):
    mark = f"{float(ctx.get('duration_s') or 0):.2f}"
    return (
        "How the reference pictures align with the target video — "
        "Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; "
        f"Picture 2 (from Shot 1) aligns with the {mark}-second mark of the target video."
    )


def fl2va_expected_align(ctx):
    return fl2va_align_both(ctx) if fl2va_has_last_frame(ctx) else fl2va_align_first()


def _seedance_frame_indexes(ctx):
    first_index = last_index = None
    for item in ctx.get("images") or []:
        role = _normalize_image_role(item.get("role"))
        index = item.get("index")
        if role == "first_frame" and first_index is None:
            first_index = index
        if role == "last_frame" and last_index is None:
            last_index = index
    return first_index, last_index


def _has_seedance_frame_line(text, index, kind):
    if kind == "first":
        return bool(re.search(
            rf"@(?:图片|图像|Image)\s*{index}\s*(?:作为首帧|is the first frame|as the first frame)",
            text,
            re.IGNORECASE,
        ))
    return bool(re.search(
        rf"@(?:图片|图像|Image)\s*{index}\s*(?:作为尾帧|is the last frame|as the last frame)",
        text,
        re.IGNORECASE,
    ))


def _require_seedance_frame_lines(text, ctx, errors):
    first_index, last_index = _seedance_frame_indexes(ctx)
    if first_index and not _has_seedance_frame_line(text, first_index, "first"):
        errors.append(f"缺少首帧声明行：@图片{first_index} 作为首帧 / @Image{first_index} as the first frame")
    if last_index and not _has_seedance_frame_line(text, last_index, "last"):
        errors.append(f"缺少尾帧声明行：@图片{last_index} 作为尾帧 / @Image{last_index} as the last frame")


def _h3_has_canvas_or_seedance_at(text):
    return bool(_CANVAS_AT_TU.search(text) or _SEEDANCE_REF.search(text))


def _validate_h3_ref2va(text, ctx, errors, warnings):
    positions = []
    for section in _H3_REF2VA_SECTIONS:
        match = re.search(rf"^{section}\s*:", text, re.MULTILINE | re.IGNORECASE)
        if not match:
            errors.append(f"缺少段：{section}:")
        else:
            positions.append(match.start())
    if positions != sorted(positions):
        errors.append("六段顺序不对")
    count = _image_count(ctx)
    for match in _PICTURE_TAG.finditer(text):
        number = int(match.group(1))
        if number < 1 or number > count:
            errors.append(f"<Picture {number}> 超出挂载图数量 {count}")
    for match in _SUBJECT_TAG.finditer(text):
        number = int(match.group(1))
        if number < 1 or number > _SUBJECT_INDEX_MAX:
            errors.append(f"<Subject {number}> 超出允许范围 1–{_SUBJECT_INDEX_MAX}")
    if count and (_NO_IDENTIFIED_SUBJECTS.search(text) or (not _PICTURE_TAG.search(text) and not _SUBJECT_TAG.search(text))):
        errors.append("有参考图时 subject_definitions 必须绑定 <Picture N> 或 <Subject N>，禁止写 No identified subjects")
    if _h3_has_canvas_or_seedance_at(text):
        errors.append("残留了 @图N / @图片N / @Image N 语法，多参目标只允许 <Picture N>")
    summary = _section_body(text, "summary", ["retention_analysis"])
    if summary and not summary.lstrip().startswith("["):
        warnings.append(
            "summary 应以任务类型开头，例如 [reference generation] 或 [keyframe completion]"
        )
    elif summary:
        bracket = re.match(r"\[([^\]]+)\]", summary.lstrip())
        if bracket:
            parts = [part.strip().lower() for part in bracket.group(1).split("+")]
            if parts and not any(part in _SUMMARY_TASK_TYPES for part in parts):
                warnings.append(
                    "summary 任务类型应使用官方前缀：reference generation / keyframe completion / "
                    "video editing / video continuation / audio reuse / audio reference"
                )
    retention = _section_body(text, "retention_analysis", ["detailed_description"])
    if retention and not any(marker in retention.lower() for marker in _RETENTION_MARKERS):
        warnings.append("retention_analysis 未使用官方关系标记（fully_preserved / partially_preserved / attribute_transfer / weak_reference）")
    detail = _section_body(text, "detailed_description", ["overall_soundscape"])
    words = _content_units(detail)
    if detail and words < 200:
        warnings.append(f"detailed_description 仅 {words} 词，官方 generation 任务建议 350–500 词")
    _check_time_codes(text, ctx.get("duration_s"), errors)
    _check_output_dialogues([m.group(1).strip() for m in _D_TAG.finditer(text)], ctx, errors, warnings)
    _check_h3_character_voice(text, ctx, errors, warnings)


def _validate_h3_fl2va(text, ctx, errors, warnings):
    lines = text.splitlines()
    first_line = lines[0].strip() if lines else ""
    expected = fl2va_expected_align(ctx)
    if first_line != expected:
        errors.append(f"第一行必须是对齐行：{expected}")
    elif len(lines) > 1 and lines[1].strip():
        errors.append("对齐行之后必须空一行")
    for field in ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music"):
        if not re.search(rf"^{field}\s*:", text, re.MULTILINE | re.IGNORECASE):
            errors.append(f"缺少字段：{field}:")
    frame_max = 2 if fl2va_has_last_frame(ctx) else 1
    for match in _PICTURE_TAG.finditer(text):
        number = int(match.group(1))
        if number < 1 or number > frame_max:
            errors.append(f"<Picture {number}> 超出首尾帧图数量 {frame_max}")
    if _SUBJECT_TAG.search(text) or _h3_has_canvas_or_seedance_at(text):
        errors.append("首尾帧目标不允许出现 <Subject> / @Image / @图片 / @图；帧锚点只用 <Picture 1/2>")
    if len(re.findall(r"cuts to", text, re.IGNORECASE)) > 2:
        warnings.append("切镜超过 2 次，首尾帧目标建议单镜头")
    _check_time_codes(text, ctx.get("duration_s"), errors)
    _check_output_dialogues([m.group(1).strip() for m in _D_TAG.finditer(text)], ctx, errors, warnings)


def _seedance_integer_marks(text):
    marks = []
    for match in _SEEDANCE_SEC_RANGE.finditer(text):
        start, end = int(match.group(1)), int(match.group(2))
        marks.append((min(start, end), max(start, end), match.group(0).strip()))
    for match in _SEEDANCE_SEC_POINT.finditer(text):
        second = int(match.group(1) or match.group(2))
        marks.append((second, second, match.group(0).strip()))
    return marks


def _check_seedance_h3_markup(text, errors):
    if _PICTURE_TAG.search(text) or _SUBJECT_TAG.search(text):
        errors.append("残留了 H3 语法（<Picture>/<Subject>）")
    if _CANVAS_AT_TU.search(text):
        errors.append("残留了画布 @图N 语法；Seedance 用 @图片N 或 @ImageN")
    if _TIME_CODE.search(text):
        errors.append("残留了 H3 时间码 At MM:SS.mmm；Seedance 2.5 用整数秒，2.0 用镜头序号")


def _validate_seedance_common(text, ctx, errors, warnings, word_limit):
    count = _image_count(ctx)
    for match in _SEEDANCE_REF.finditer(text):
        number = int(match.group(1))
        if number < 1 or number > count:
            errors.append(f"@图片/@Image {number} 超出挂载图数量 {count}")
    if _AT_IMAGE_MERGED.search(text):
        errors.append("首尾帧声明必须一行一图，禁止合并声明")
    _check_seedance_h3_markup(text, errors)
    words = _content_units(text)
    if words > word_limit:
        warnings.append(f"正文 {words} 词，超出建议上限 {word_limit}")
    quoted = [m.group(1).strip() for m in _CJK_QUOTED.finditer(text) if _has_cjk(m.group(1))]
    if quoted and not _source_dialogues(ctx):
        warnings.append("输出里有中文引号台词，但导演本没有台词")
    else:
        _check_output_dialogues(quoted, ctx, errors, warnings)
    _require_seedance_frame_lines(text, ctx, errors)


def _validate_seedance_25(text, ctx, errors, warnings):
    _validate_seedance_common(text, ctx, errors, warnings, word_limit=700)
    duration = float(ctx.get("duration_s") or 0)
    last_end = -1
    for start, end, raw in _seedance_integer_marks(text):
        if duration and end > duration:
            warnings.append(f"时间轴 {raw} 超出时长 {int(duration)} 秒")
        if start < last_end:
            warnings.append(f"时间轴 {raw} 与前一段重叠或回跳")
        last_end = max(last_end, end)


def _validate_seedance_20(text, ctx, errors, warnings):
    _validate_seedance_common(text, ctx, errors, warnings, word_limit=200)
    if _seedance_integer_marks(text):
        warnings.append("Seedance 2.0 不响应时间戳，请改用镜头序号（镜头1 / 镜头2）")


_VALIDATORS = {
    "h3-ref2va": _validate_h3_ref2va,
    "h3-fl2va": _validate_h3_fl2va,
    "seedance-2.5": _validate_seedance_25,
    "seedance-2.0": _validate_seedance_20,
}


_PROTOCOL_FOR_LANGUAGE = re.compile(
    r"(?:"
    r"<Picture\s+\d+>|<Subject\s+\d+>|@(?:图片|图像|Image)\s*\d+|"
    r"\[Shot\s+\d+\]|At\s+\d{1,2}:\d{2}\.\d{3}|"
    r"fully_preserved|partially_preserved|attribute_transfer|weak_reference|"
    r"fully_copy|partially_copy|reference generation|keyframe completion|"
    r"video editing|video continuation|audio reuse|audio reference|"
    r"subject_definitions|retention_analysis|detailed_description|"
    r"overall_soundscape|non_diegetic_music|integrated_multimodal_description|"
    r"the camera cuts to|says in an off-screen voiceover|"
    r"Push In|Pull Out|Pan Left|Pan Right|Truck Left|Truck Right|"
    r"Tilt Up|Tilt Down|Pedestal Up|Pedestal Down|Zoom In|Zoom Out|"
    r"Arc Shot|Tracking Shot|Static Shot|Shake Slightly|\bPOV\b|"
    r"with small amplitude|with large amplitude|at slow speed|at fast speed|"
    r"is the first frame|is the last frame|as the first frame|as the last frame|"
    r"作为首帧|作为尾帧|is the reference|"
    r"\bN/A\b"
    r")",
    re.IGNORECASE,
)
_H3_LANGUAGE_SECTIONS = {
    "subject_definitions": ["summary"],
    "summary": ["retention_analysis"],
    "retention_analysis": ["detailed_description"],
    "detailed_description": ["overall_soundscape"],
    "overall_soundscape": ["non_diegetic_music"],
    "integrated_multimodal_description": ["overall_soundscape"],
}
_SEEDANCE_DECL_HINT = re.compile(
    r"(作为首帧|作为尾帧|is the first frame|is the last frame|as the first frame|"
    r"as the last frame|is the reference|defines)",
    re.IGNORECASE,
)


def _strip_protocol_for_language(text):
    """去掉协议词、对齐行、声明行和原样台词，只留描写供语言检查。"""
    value = _D_TAG.sub(" ", str(text or ""))
    value = re.sub(r"[\"“「][^\"”」]{1,80}[\"”」]", " ", value)
    kept = []
    for line in value.splitlines():
        stripped = line.strip()
        if stripped.startswith("For the target video, at 0.00 seconds"):
            continue
        if stripped.startswith("How the reference pictures align"):
            continue
        if _SEEDANCE_REF.search(stripped) and _SEEDANCE_DECL_HINT.search(stripped):
            continue
        kept.append(line)
    value = _PROTOCOL_FOR_LANGUAGE.sub(" ", "\n".join(kept))
    return re.sub(r"<[^>]+>", " ", value)


def _prose_counts(text):
    cleaned = _strip_protocol_for_language(text)
    return (
        len(re.findall(r"[\u4e00-\u9fff]", cleaned)),
        len(re.findall(r"[A-Za-z']+", cleaned)),
    )


def _prose_is_english(text):
    cjk, latin = _prose_counts(text)
    return latin >= 20 and latin >= cjk


def _prose_is_chinese(text):
    cjk, latin = _prose_counts(text)
    return cjk >= 20 and cjk >= latin


def _language_chunks(text, target_id):
    spec = VIDEO_PROMPT_TARGETS.get(str(target_id or "").strip()) or {}
    if spec.get("family") == "h3":
        chunks = []
        for name, nxt in _H3_LANGUAGE_SECTIONS.items():
            body = _section_body(text, name, nxt)
            if body:
                chunks.append((name, body))
        return chunks
    return [("body", str(text or ""))]


def _check_requested_language(text, target_id, language, errors):
    wanted = normalize_output_language(language)
    bad = []
    for name, body in _language_chunks(text, target_id):
        if wanted == "zh" and _prose_is_english(body):
            bad.append(name)
        elif wanted == "en" and _prose_is_chinese(body):
            bad.append(name)
    if not bad:
        return
    if wanted == "zh":
        errors.append(
            "选了中文，但这些段落仍是英文：" + " / ".join(bad)
            + "。请改成中文句子，段名和 <Subject> / [Shot] / fully_preserved 保持英文。"
        )
    else:
        errors.append(
            "选了英文，但这些段落仍是中文：" + " / ".join(bad)
            + "。请改成英文句子，台词和牌匾原文保持原语言。"
        )


def _audio_count(ctx):
    return len(ctx.get("audios") or [])


def _check_h3_character_voice(text, ctx, errors, warnings):
    character_voice = bool(ctx.get("character_voice"))
    audio_count = _audio_count(ctx)
    found = [int(match.group(1)) for match in _AUDIO_TAG.finditer(text)]
    summary = _section_body(text, "summary", ["retention_analysis"])
    summary_l = (summary or "").lower()
    if not character_voice:
        if found:
            warnings.append("未勾选角色音色，但输出写了 <Audio N>；请关掉音频绑定或勾选角色音色")
        return
    if audio_count < 1:
        errors.append("缺少音色绑定：勾选角色音色时必须挂载样音")
        return
    if 1 not in found:
        errors.append("缺少音色绑定：须写 <Audio 1> 是对应角色 Subject 的音色参考")
    for number in found:
        if number < 1 or number > audio_count:
            errors.append(f"缺少音色绑定：<Audio {number}> 超出挂载音频数量 {audio_count}")
    if "audio reuse" in summary_l and "audio reference" not in summary_l:
        errors.append("缺少音色绑定：summary 应使用 audio reference，不要只用 audio reuse")
    elif "audio reference" not in summary_l:
        errors.append("缺少音色绑定：summary 须含 audio reference")


_H3_STRUCTURAL_MARKERS = (
    "缺少段：",
    "六段顺序不对",
    "第一行必须是对齐行",
    "对齐行之后必须空一行",
    "缺少字段：",
    "缺少音色绑定",
)


def _seedance_has_draft_structure(text):
    """有 @图片 / @Image / 镜头 或一段能改的正文就过结构；太短的道歉句仍整单打回。"""
    value = str(text or "")
    if _SEEDANCE_REF.search(value) or re.search(r"镜头\s*\d+|Shot\s*\d+", value, re.I):
        return True
    return _content_units(value) >= 20


def _is_structural_error(target_id, message):
    spec = VIDEO_PROMPT_TARGETS.get(str(target_id or "").strip()) or {}
    if spec.get("family") == "h3":
        return any(marker in str(message or "") for marker in _H3_STRUCTURAL_MARKERS)
    return False


def _soften_content_issues(target_id, text, errors, warnings):
    """结构过关时，台词/语言/多余标记只提示，不废掉整份稿。"""
    spec = VIDEO_PROMPT_TARGETS.get(str(target_id or "").strip()) or {}
    items = [str(item or "").strip() for item in (errors or []) if str(item or "").strip()]
    notes = list(warnings or [])
    if spec.get("family") == "seedance" and not _seedance_has_draft_structure(text):
        if items:
            return items, notes
        return ["输出太短，不像可用的提示词正文"], notes
    hard = []
    soft = []
    for item in items:
        if _is_structural_error(target_id, item):
            hard.append(item)
        else:
            soft.append(item)
    return hard, notes + soft


def validate_target_output(target_id, text, ctx, language="en"):
    """返回 {"errors": [...], "warnings": [...]}。

    errors 只留结构问题（空稿、太短、H3 缺段/对齐行错），前端不得派生。
    台词、语言、图号、残留标记等可手改的问题进 warnings，结构过关就派生。
    """
    errors = []
    warnings = []
    value = str(text or "").strip()
    if not value:
        return {"errors": ["模型没有输出内容"], "warnings": warnings}
    validator = _VALIDATORS.get(str(target_id or "").strip())
    if validator is None:
        return {"errors": [f"未知的提示词目标：{target_id}"], "warnings": warnings}
    validator(value, ctx, errors, warnings)
    _check_requested_language(value, target_id, language, errors)
    hard, notes = _soften_content_issues(target_id, value, errors, warnings)
    return {"errors": hard, "warnings": notes}
