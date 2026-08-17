"""画布出片提示词适配（方案：画布出片提示词适配方案.md）。

导演本 → 中间稿（规则抽取）→ AI 按目标规范写正文 → 规则校验。
本模块只做纯逻辑：抽取、实名对齐、消息构造、校验；AI 调用由 main.py
的转换端点走既有文本模型通道完成。纯增量功能，不改既有生成链路。
"""

import json
import re
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts" / "video-targets"

# 顺序即前端按钮顺序。model_hints 只用于派生工作台的默认选中（软默认，可改），
# 不做能力分类；preset 是派生工作台需要预置的勾选。
VIDEO_PROMPT_TARGETS = {
    "seedance-2.0": {
        "label": "Seedance 2.0 提示词",
        "skill": "seedance-2.0.md",
        "family": "seedance",
        "model_hints": ["sd2.0", "seedance-2.0", "seedance2.0", "seedance_2.0", "seedance-v2"],
        "preset": {},
    },
    "seedance-2.5": {
        "label": "Seedance 2.5 提示词",
        "skill": "seedance-2.5.md",
        "family": "seedance",
        "model_hints": ["sd2.5", "seedance-2.5", "seedance2.5", "seedance_2.5", "seedance-v2.5"],
        "preset": {},
    },
    "h3-ref2va": {
        "label": "H3 多参提示词",
        "skill": "h3-ref2va.md",
        "family": "h3",
        "model_hints": [],
        "preset": {"multimodal": True},
    },
    "h3-fl2va": {
        "label": "H3 首尾帧提示词",
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
# 中间稿抽取（纯规则）
# ---------------------------------------------------------------------------

_SHOT_HEADER = re.compile(r"(?:^|\n)[ \t>#*\-）)（(]*(?:镜头|分镜|\[?shot)\s*0*(\d{1,2})\]?\s*[：:.、，\-—\s]", re.IGNORECASE)
_AT_SECONDS = re.compile(r"^[（(【\[]?\s*(\d{1,3}(?:\.\d+)?)\s*[sS秒]")
_DIALOGUE = re.compile(
    r"(?:台词[：:]\s*)?([\u4e00-\u9fffA-Za-z0-9_·]{1,12})\s*[：:]\s*[「“\"]([^「」“”\"]{1,200})[」”\"]"
)
_DIALOGUE_SPEAKER_STOPWORDS = {"台词", "风格", "场景", "镜头", "分镜", "运镜", "提示", "备注", "要求", "画面", "环境声", "配乐", "音效"}
_SUBJECT_AT_INDEX = re.compile(r"([\u4e00-\u9fffA-Za-z0-9_·]{1,20})\s*@图\s*(\d{1,2})")
_AT_INDEX = re.compile(r"@图\s*(\d{1,2})")
_AT_FILE = re.compile(r"@([^\s@()（），。、；「」\[\]{}<>\"']+?\.(?:png|jpe?g|webp|gif|bmp))", re.IGNORECASE)
_FILE_ROLE_HINT = re.compile(r"为角色\s*[「\"']([^「」\"']{1,20})[」\"']")
_CAMERA_WORDS = (
    "推进", "推镜", "拉远", "拉镜", "环绕", "摇镜", "横摇", "平移", "移镜", "跟随", "跟拍",
    "俯拍", "仰拍", "固定镜头", "特写", "近景", "中景", "远景", "全景", "变焦", "升格", "慢动作", "手持",
)
_STYLE_WORDS = (
    "柔光", "8k", "4k", "超高清", "高清", "画质", "电影感", "cinematic", "胶片", "写实",
    "动漫", "水墨", "赛博", "光影", "质感", "低饱和", "暖色调", "冷色调",
)
_SOUND_WORDS = ("环境声", "音效", "配乐", "bgm", "背景音乐", "soundscape")


_SUBJECT_LEADING_PARTICLES = "与和及跟同的是由让把向对给从被在"


def _clean_subject_id(value):
    text = str(value or "").strip()
    while len(text) > 1 and text[0] in _SUBJECT_LEADING_PARTICLES:
        text = text[1:]
    return text


def _norm_name(value):
    text = str(value or "").strip().replace("\\", "/")
    return text.rsplit("/", 1)[-1].lower()


def _match_image_by_name(name, images):
    """按文件名匹配上传图，返回 1 起的 index；找不到返回 0。"""
    wanted = _norm_name(name)
    if not wanted:
        return 0
    for index, item in enumerate(images, start=1):
        have = _norm_name(item.get("name"))
        if have and (have == wanted or have.endswith(wanted) or wanted.endswith(have)):
            return index
    return 0


def _fill_shot_times(shots, duration_s):
    """词里没写秒数时，按时长均分估点；第一镜保持空，给 [Shot 1] 不带时间码。"""
    if not shots:
        return
    duration = float(duration_s or 0)
    count = len(shots)
    if count <= 1:
        return
    step = duration / count if duration else 0
    for index, shot in enumerate(shots):
        if shot.get("at_s") is not None:
            continue
        shot["at_s"] = None if index == 0 else round(step * index, 3)


def _split_shots(prompt):
    """按 镜头N / Shot N 切分；没有标题就整段算一镜。标题前的铺垫并入第一镜。"""
    matches = list(_SHOT_HEADER.finditer(prompt))
    if not matches:
        return [(1, prompt.strip())]
    shots = []
    for pos, match in enumerate(matches):
        start = match.end()
        end = matches[pos + 1].start() if pos + 1 < len(matches) else len(prompt)
        try:
            index = int(match.group(1))
        except ValueError:
            index = pos + 1
        shots.append((index, prompt[start:end].strip()))
    preamble = prompt[:matches[0].start()].strip()
    if preamble and shots:
        shots[0] = (shots[0][0], preamble + "\n" + shots[0][1])
    return shots


def _extract_dialogue(text):
    dialogue = []
    for match in _DIALOGUE.finditer(text):
        speaker = match.group(1).strip()
        if speaker in _DIALOGUE_SPEAKER_STOPWORDS:
            continue
        dialogue.append({"speaker": speaker, "text": match.group(2).strip()})
    return dialogue


def _extract_camera(text):
    found = [word for word in _CAMERA_WORDS if word in text]
    return "、".join(dict.fromkeys(found))


def extract_canvas_ir(prompt, images, duration_s):
    """导演本 → 中间稿。images 是上传顺序的 [{name,url,role}]。"""
    prompt = str(prompt or "")
    images = [dict(item or {}) for item in (images or [])]
    warnings = []

    referenced = set()
    subjects = []
    seen_subjects = set()

    def _add_subject(subject_id, index, notes=""):
        key = (subject_id, index)
        if key in seen_subjects:
            return
        seen_subjects.add(key)
        subjects.append({
            "id": subject_id,
            "image": f"图{index}" if index else None,
            "notes": notes,
        })

    for match in _SUBJECT_AT_INDEX.finditer(prompt):
        subject_id, raw_index = _clean_subject_id(match.group(1)), int(match.group(2))
        if 1 <= raw_index <= len(images):
            referenced.add(raw_index)
            _add_subject(subject_id, raw_index)
        else:
            warnings.append(f"词里引用了 @图{raw_index}，但只挂载了 {len(images)} 张图")
            _add_subject(subject_id, 0)

    for match in _AT_INDEX.finditer(prompt):
        raw_index = int(match.group(1))
        if 1 <= raw_index <= len(images):
            referenced.add(raw_index)
        else:
            message = f"词里引用了 @图{raw_index}，但只挂载了 {len(images)} 张图"
            if message not in warnings:
                warnings.append(message)

    for match in _AT_FILE.finditer(prompt):
        file_name = match.group(1)
        index = _match_image_by_name(file_name, images)
        # 角色提示只认文件引用之后、下一个 @ 之前的一小段，避免同行多图时错位关联。
        window = prompt[match.end():match.end() + 50]
        next_at = window.find("@")
        if next_at >= 0:
            window = window[:next_at]
        role_match = _FILE_ROLE_HINT.search(window)
        if index:
            referenced.add(index)
            if role_match:
                _add_subject(role_match.group(1).strip(), index)
        else:
            warnings.append(f"词里引用了 @{file_name}，但 MEDIA 里没有同名图片")
            if role_match:
                _add_subject(role_match.group(1).strip(), 0)

    shots = []
    for index, body in _split_shots(prompt):
        at_match = _AT_SECONDS.match(body)
        shots.append({
            "index": index,
            "at_s": float(at_match.group(1)) if at_match else None,
            "action": body,
            "camera": _extract_camera(body),
            "dialogue": _extract_dialogue(body),
        })
    _fill_shot_times(shots, duration_s)

    style_lines = []
    sound_lines = []
    for line in prompt.splitlines():
        stripped = line.strip()
        if not stripped or len(stripped) > 60:
            continue
        lowered = stripped.lower()
        if any(word in lowered for word in _SOUND_WORDS):
            sound_lines.append(stripped)
        elif any(word in lowered for word in _STYLE_WORDS) and not _DIALOGUE.search(stripped):
            style_lines.append(stripped)

    image_entries = []
    for index, item in enumerate(images, start=1):
        image_entries.append({
            "slot": f"图{index}",
            "index": index,
            "name": str(item.get("name") or ""),
            "role": str(item.get("role") or ""),
            "referenced": index in referenced,
        })
    if referenced and len(images) > len(referenced):
        unreferenced = [entry["slot"] for entry in image_entries if not entry["referenced"]]
        warnings.append("这些图未在词中引用：" + "、".join(unreferenced))

    dialogue_total = sum(len(shot["dialogue"]) for shot in shots)
    if len(shots) == 1 and not dialogue_total and not referenced and images:
        warnings.append("导演本抽不出结构（无镜头、无台词、无图引用），图片绑定不完整")

    return {
        "source_prompt": prompt,
        "duration_s": duration_s,
        "style": " / ".join(dict.fromkeys(style_lines)),
        "shots": shots,
        "subjects": subjects,
        "images": image_entries,
        "sound": " / ".join(dict.fromkeys(sound_lines)),
        "warnings": warnings,
    }


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


def convert_image_urls(images):
    """抽出可送给文字通道的参考图地址，顺序即图1、图2。不把地址写进提示词正文。"""
    urls = []
    for item in images or []:
        url = str((item or {}).get("url") or "").strip()
        if url:
            urls.append(url)
    return urls


def image_inventory_lines(ir):
    lines = []
    for item in ir.get("images") or []:
        index = item.get("index") or (len(lines) + 1)
        slot = item.get("slot") or f"图{index}"
        name = str(item.get("name") or "").strip() or "(未命名)"
        role = str(item.get("role") or "").strip() or "reference"
        lines.append(f"- {slot} / <Picture {index}> = {name} （角色 {role}）")
    return lines


def convert_input_warnings(ir, model, image_urls):
    warnings = list(ir.get("warnings") or [])
    if image_urls and not chat_model_likely_sees_images(model):
        warnings.append("当前文字模型多半看不到附图，只会读文件名和导演本；要看图写词请换视觉模型")
    return warnings


def normalize_output_language(value):
    return "zh" if str(value or "").strip().lower() in {"zh", "zh-cn", "cn", "chinese", "中文"} else "en"


def output_language_instruction(language):
    if normalize_output_language(language) == "zh":
        return (
            "生成语言：中文。整份描写只能用中文，禁止中英混写描写。"
            "subject_definitions / summary / retention_analysis / detailed_description / "
            "overall_soundscape / integrated_multimodal_description 以及 Seedance 概述、情节、结尾"
            "必须是中文句子。"
            "要写「<Subject 1> 是……」「目标视频采用……」「皇帝坐在石桌旁」，"
            "禁止写成 “<Subject 1> is the ...” / “A cinematic ...” / “The target video is ...” / "
            "“The emperor sits ...”。"
            "不要因为 skill 里有英文样例就改回英文；英文样例只在选英文时用。"
            "段名、对齐行、标签和协议词必须保持英文，不要翻译："
            "subject_definitions / summary / [reference generation] / fully_preserved / "
            "<Picture N> / <Subject N> / [Shot N] / At MM:SS.mmm / @Image N / is the first frame。"
            "台词、牌匾、画面可见原文保持原语言。"
        )
    return (
        "生成语言：英文。整份描写只能用英文，禁止中英混写描写。"
        "subject_definitions / summary / retention_analysis / detailed_description / "
        "overall_soundscape / integrated_multimodal_description 以及 Seedance 概述、情节、结尾"
        "必须是英文句子。"
        "要写 “<Subject 1> is ...” / “The target video is ...” / “The emperor sits ...”，"
        "禁止写成「<Subject 1> 是……」「目标视频采用……」「皇帝坐在石桌旁」。"
        "不要因为 skill 里有中文样例就改回中文；中文样例只在选中文时用。"
        "段名、对齐行、标签和协议词保持 skill 规定的英文。"
        "台词、牌匾、画面可见原文保持原语言。"
    )


def build_convert_messages(target_id, ir, source_prompt="", language="en"):
    # 语言指令放进 system，避免 skill 里的英文样例压过用户消息。
    system = output_language_instruction(language) + "\n\n" + load_target_skill(target_id)
    prompt_text = str(source_prompt or ir.get("source_prompt") or "").strip()
    inventory = image_inventory_lines(ir)
    inventory_text = "\n".join(inventory) if inventory else "（没有参考图）"
    user = (
        f"目标：{target_id}\n"
        f"时长（秒）：{ir.get('duration_s')}\n"
        f"{output_language_instruction(language)}\n\n"
        "原始导演本：\n"
        f"{prompt_text}\n\n"
        "参考图槽位（与附图顺序一致；正文必须用这些槽位绑定，不要改文件名）：\n"
        f"{inventory_text}\n\n"
        "中间稿 JSON：\n"
        f"{json.dumps(ir, ensure_ascii=False, indent=2)}\n\n"
        "附图已按槽位顺序附在本条消息里。请看图写词。"
        "户型图、场景图、物体图也要定义成 Subject / Picture，禁止写 No identified subjects。\n"
        f"本轮描写语言只能是{'中文' if normalize_output_language(language) == 'zh' else '英文'}，不要混用另一份样例的语言。\n"
        "只输出该目标的提示词正文，不要解释，不要代码块围栏。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_repair_messages(target_id, ir, previous_output, errors, language="en"):
    messages = build_convert_messages(target_id, ir, language=language)
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
_AT_IMAGE = re.compile(r"@Image\s+(\d{1,2})", re.IGNORECASE)
_AT_IMAGE_MERGED = re.compile(r"@Images?\s+\d{1,2}\s*(?:,|and|和|与)\s*(?:@Image\s+)?\d{1,2}\s+(?:are|is)", re.IGNORECASE)
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


def _input_dialogues(ir):
    texts = []
    for shot in ir.get("shots") or []:
        for item in shot.get("dialogue") or []:
            text = str(item.get("text") or "").strip()
            if text:
                texts.append(text)
    return texts


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


def _check_output_dialogues(found, ir, errors, warnings=None):
    """台词只准原样保留或按时长舍弃，不准改写、不准凭空新增。"""
    inputs = [re.sub(r"\s+", "", text) for text in _input_dialogues(ir)]
    normalized_found = [re.sub(r"\s+", "", text) for text in found]
    for raw, norm in zip(found, normalized_found):
        if inputs and norm not in inputs:
            errors.append(f"台词被改写或凭空新增：{raw}")
    if warnings is not None and inputs and not normalized_found:
        warnings.append("导演本有台词，输出里全部舍弃了")


def _image_count(ir):
    return len(ir.get("images") or [])


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


def fl2va_has_last_frame(ir):
    roles = {str(item.get("role") or "").lower() for item in ir.get("images") or []}
    return bool(roles & {"last_frame", "last", "end_frame"})


def fl2va_last_shot_index(ir):
    shots = ir.get("shots") or []
    if not shots:
        return 1
    return max(int(shot.get("index") or 1) for shot in shots)


def fl2va_align_first():
    return "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."


def fl2va_align_both(ir):
    shot_n = fl2va_last_shot_index(ir)
    mark = f"{float(ir.get('duration_s') or 0):.2f}"
    return (
        "How the reference pictures align with the target video — "
        "Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; "
        f"Picture 2 (from Shot {shot_n}) aligns with the {mark}-second mark of the target video."
    )


def fl2va_expected_align(ir):
    return fl2va_align_both(ir) if fl2va_has_last_frame(ir) else fl2va_align_first()


def _seedance_frame_indexes(ir):
    first_index = last_index = None
    for item in ir.get("images") or []:
        role = str(item.get("role") or "").lower()
        index = item.get("index")
        if role in {"first_frame", "first"} and first_index is None:
            first_index = index
        if role in {"last_frame", "last"} and last_index is None:
            last_index = index
    return first_index, last_index


def _require_seedance_frame_lines(text, ir, errors):
    first_index, last_index = _seedance_frame_indexes(ir)
    if first_index and not re.search(rf"@Image\s+{first_index}\s+is\s+the\s+first\s+frame", text, re.IGNORECASE):
        errors.append(f"缺少首帧声明行：@Image {first_index} is the first frame.")
    if last_index and not re.search(rf"@Image\s+{last_index}\s+is\s+the\s+last\s+frame", text, re.IGNORECASE):
        errors.append(f"缺少尾帧声明行：@Image {last_index} is the last frame.")


def _validate_h3_ref2va(text, ir, errors, warnings):
    positions = []
    for section in _H3_REF2VA_SECTIONS:
        match = re.search(rf"^{section}\s*:", text, re.MULTILINE | re.IGNORECASE)
        if not match:
            errors.append(f"缺少段：{section}:")
        else:
            positions.append(match.start())
    if positions != sorted(positions):
        errors.append("六段顺序不对")
    count = _image_count(ir)
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
    if "@图" in text or _AT_IMAGE.search(text):
        errors.append("残留了 @图N / @Image N 语法，多参目标只允许 <Picture N>")
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
    _check_time_codes(text, ir.get("duration_s"), errors)
    _check_output_dialogues([m.group(1).strip() for m in _D_TAG.finditer(text)], ir, errors, warnings)


def _validate_h3_fl2va(text, ir, errors, warnings):
    lines = text.splitlines()
    first_line = lines[0].strip() if lines else ""
    expected = fl2va_expected_align(ir)
    if first_line != expected:
        errors.append(f"第一行必须是对齐行：{expected}")
    elif len(lines) > 1 and lines[1].strip():
        errors.append("对齐行之后必须空一行")
    for field in ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music"):
        if not re.search(rf"^{field}\s*:", text, re.MULTILINE | re.IGNORECASE):
            errors.append(f"缺少字段：{field}:")
    frame_max = 2 if fl2va_has_last_frame(ir) else 1
    for match in _PICTURE_TAG.finditer(text):
        number = int(match.group(1))
        if number < 1 or number > frame_max:
            errors.append(f"<Picture {number}> 超出首尾帧图数量 {frame_max}")
    if _SUBJECT_TAG.search(text) or _AT_IMAGE.search(text) or "@图" in text:
        errors.append("首尾帧目标不允许出现 <Subject> / @Image / @图；帧锚点只用 <Picture 1/2>")
    if len(re.findall(r"cuts to", text, re.IGNORECASE)) > 2:
        warnings.append("切镜超过 2 次，首尾帧目标建议单镜头")
    _check_time_codes(text, ir.get("duration_s"), errors)
    _check_output_dialogues([m.group(1).strip() for m in _D_TAG.finditer(text)], ir, errors, warnings)


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
    if _PICTURE_TAG.search(text) or _SUBJECT_TAG.search(text) or "@图" in text:
        errors.append("残留了 H3 语法（<Picture>/<Subject>/@图N）")
    if _TIME_CODE.search(text):
        errors.append("残留了 H3 时间码 At MM:SS.mmm；Seedance 2.5 用整数秒，2.0 用镜头序号")


def _validate_seedance_common(text, ir, errors, warnings, word_limit):
    count = _image_count(ir)
    for match in _AT_IMAGE.finditer(text):
        number = int(match.group(1))
        if number < 1 or number > count:
            errors.append(f"@Image {number} 超出挂载图数量 {count}")
    if _AT_IMAGE_MERGED.search(text):
        errors.append("首尾帧声明必须一行一图，禁止合并声明")
    _check_seedance_h3_markup(text, errors)
    words = _content_units(text)
    if words > word_limit:
        warnings.append(f"正文 {words} 词，超出建议上限 {word_limit}")
    quoted = [m.group(1).strip() for m in _CJK_QUOTED.finditer(text) if _has_cjk(m.group(1))]
    if quoted and not _input_dialogues(ir):
        warnings.append("输出里有中文引号台词，但导演本没有台词")
    else:
        _check_output_dialogues(quoted, ir, errors, warnings)
    _require_seedance_frame_lines(text, ir, errors)


def _validate_seedance_25(text, ir, errors, warnings):
    _validate_seedance_common(text, ir, errors, warnings, word_limit=700)
    duration = float(ir.get("duration_s") or 0)
    last_end = -1
    for start, end, raw in _seedance_integer_marks(text):
        if duration and end > duration:
            warnings.append(f"时间轴 {raw} 超出时长 {int(duration)} 秒")
        if start < last_end:
            warnings.append(f"时间轴 {raw} 与前一段重叠或回跳")
        last_end = max(last_end, end)


def _validate_seedance_20(text, ir, errors, warnings):
    _validate_seedance_common(text, ir, errors, warnings, word_limit=200)
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
    r"<Picture\s+\d+>|<Subject\s+\d+>|@Image\s+\d+|"
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
    r"is the first frame|is the last frame|is the reference|"
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
        if _AT_IMAGE.search(stripped) and re.search(
            r"\b(is the first frame|is the last frame|is the reference|defines)\b",
            stripped,
            re.IGNORECASE,
        ):
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
            + "。请改成中文句子，段名和 <Subject> / [Shot] / fully_preserved / @Image 声明行保持英文。"
        )
    else:
        errors.append(
            "选了英文，但这些段落仍是中文：" + " / ".join(bad)
            + "。请改成英文句子，台词和牌匾原文保持原语言。"
        )


def validate_target_output(target_id, text, ir, language="en"):
    """返回 {"errors": [...], "warnings": [...]}；errors 非空则不得发出。"""
    errors = []
    warnings = []
    value = str(text or "").strip()
    if not value:
        return {"errors": ["模型没有输出内容"], "warnings": warnings}
    validator = _VALIDATORS.get(str(target_id or "").strip())
    if validator is None:
        return {"errors": [f"未知的提示词目标：{target_id}"], "warnings": warnings}
    validator(value, ir, errors, warnings)
    _check_requested_language(value, target_id, language, errors)
    return {"errors": errors, "warnings": warnings}
