"""MiniMax official platform protocol.

Official docs: https://platform.minimaxi.com/docs/guides/models-intro

Four current families (request bodies are not interchangeable):

- Language: OpenAI-compatible POST /v1/chat/completions, model enum MiniMax-M*.
- Video: official MiniMax-H3 POST /v2/video_generation + GET /v2/query/video_generation/{id}.
  This is not the local H3 gateway (protocol=h3, POST /v1/videos).
- Speech: POST /v1/t2a_v2 and POST /v1/get_voice. Plan A still synthesizes one sample clip.
- Image: POST /v1/image_generation (image-01 / image-01-live).

Model ids that belong to these official enums are classified from the catalog.
/v1/models rows outside the catalog still require capability fields; missing
capabilities stay unknown. Names are never regex-guessed.

Not in this module: Hailuo 02 / 2.3 (legacy POST /v1/video_generation family),
music-3.0, Anthropic /v1/messages, WebSocket T2A, voice clone.
"""

import base64
import binascii
import urllib.parse


MINIMAX_PROTOCOL = "minimax"
MINIMAX_SPEECH_PROTOCOL = "minimax-speech"
MINIMAX_OFFICIAL_PROTOCOLS = frozenset({MINIMAX_PROTOCOL, MINIMAX_SPEECH_PROTOCOL})
MINIMAX_GET_VOICE_PATH = "/v1/get_voice"
MINIMAX_T2A_PATH = "/v1/t2a_v2"
MINIMAX_CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
MINIMAX_IMAGE_PATH = "/v1/image_generation"
MINIMAX_H3_SUBMIT_PATH = "/v2/video_generation"
MINIMAX_H3_QUERY_PATH = "/v2/query/video_generation"

MINIMAX_CN_BASE_URL = "https://api.minimaxi.com"
MINIMAX_INTL_BASE_URL = "https://api.minimax.io"

# Official language catalog (platform.minimaxi.com/docs/guides/text-generation).
MINIMAX_CHAT_MODELS = (
    "MiniMax-M3",
    "MiniMax-M2.7",
    "MiniMax-M2.7-highspeed",
    "MiniMax-M2.5",
    "MiniMax-M2.5-highspeed",
    "MiniMax-M2.1",
    "MiniMax-M2.1-highspeed",
    "MiniMax-M2",
)
MINIMAX_DEFAULT_CHAT_MODEL = "MiniMax-M3"

# Official image catalog (POST /v1/image_generation).
MINIMAX_IMAGE_MODELS = (
    "image-01",
    "image-01-live",
)
MINIMAX_DEFAULT_IMAGE_MODEL = "image-01"
MINIMAX_IMAGE_PROMPT_MAX = 1500
MINIMAX_IMAGE_ASPECT_RATIOS = ("1:1", "16:9", "4:3", "3:2", "2:3", "3:4", "9:16", "21:9")
MINIMAX_IMAGE_DEFAULT_ASPECT = "1:1"
MINIMAX_IMAGE_LIVE_STYLES = ("漫画", "元气", "中世纪", "水彩")
MINIMAX_IMAGE_SUBJECT_MAX = 1

# Official MiniMax-H3 catalog (POST /v2/video_generation). Not Hailuo 02/2.3.
MINIMAX_VIDEO_MODELS = ("MiniMax-H3",)
MINIMAX_H3_MODEL = "MiniMax-H3"
MINIMAX_H3_RESOLUTIONS = ("768P", "2K")
MINIMAX_H3_DEFAULT_RESOLUTION = "768P"
MINIMAX_H3_DURATIONS = tuple(range(4, 16))
MINIMAX_H3_DEFAULT_DURATION = 5
MINIMAX_H3_T2V_RATIOS = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")
MINIMAX_H3_RATIOS = ("adaptive",) + MINIMAX_H3_T2V_RATIOS
MINIMAX_H3_PROMPT_MAX = 7000
MINIMAX_H3_REF_IMAGE_MAX = 9
MINIMAX_H3_REF_VIDEO_MAX = 3
MINIMAX_H3_REF_AUDIO_MAX = 3
MINIMAX_H3_SUCCESS_STATUSES = frozenset({"succeeded"})
MINIMAX_H3_FAILURE_STATUSES = frozenset({"failed", "cancelled"})

# Official T2A HTTP enum (platform.minimaxi.com / platform.minimax.io).
# These are T2A request parameters, not a /v1/models catalog.
MINIMAX_T2A_MODELS = (
    "speech-2.8-hd",
    "speech-2.8-turbo",
    "speech-2.6-hd",
    "speech-2.6-turbo",
    "speech-02-hd",
    "speech-02-turbo",
    "speech-01-hd",
    "speech-01-turbo",
)
MINIMAX_DEFAULT_T2A_MODEL = "speech-2.8-hd"
MINIMAX_DEFAULT_SAMPLE_TEXT = "这是用于视频角色音色参考的样音，请保持声线稳定、吐字清晰。"
MINIMAX_SAMPLE_TEXT_MAX = 200
MINIMAX_T2A_TEXT_MAX = 10000

# H3 reference audio is documented as 2–15 seconds; warn above this, still save.
MINIMAX_SAMPLE_WARN_MS = 15000


def is_minimax_official_protocol(value):
    return str(value or "").strip().lower() in MINIMAX_OFFICIAL_PROTOCOLS


def is_minimax_h3_model(model_id=""):
    return str(model_id or "").strip() == MINIMAX_H3_MODEL


def is_minimax_chat_model(model_id=""):
    return str(model_id or "").strip() in MINIMAX_CHAT_MODELS


def is_minimax_image_model(model_id=""):
    return str(model_id or "").strip() in MINIMAX_IMAGE_MODELS


def is_minimax_t2a_model(model_id=""):
    return str(model_id or "").strip() in MINIMAX_T2A_MODELS


def official_model_catalog():
    return {
        "chat": list(MINIMAX_CHAT_MODELS),
        "image": list(MINIMAX_IMAGE_MODELS),
        "video": list(MINIMAX_VIDEO_MODELS),
        "audio": list(MINIMAX_T2A_MODELS),
        "speech_models": list(MINIMAX_T2A_MODELS),
    }


def merge_official_catalog(grouped=None, ids=None):
    groups = official_model_catalog()
    incoming = grouped if isinstance(grouped, dict) else {}
    merged_ids = []
    seen = set()
    for key in ("chat", "image", "video", "audio"):
        bucket = []
        for mid in list(groups.get(key) or []) + list(incoming.get(key) or []):
            text = str(mid or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            bucket.append(text)
            merged_ids.append(text)
        groups[key] = bucket
    extra_ids = []
    for mid in list(ids or []) + list(incoming.get("unknown") or []):
        text = str(mid or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        extra_ids.append(text)
    groups["unknown"] = extra_ids
    groups["speech_models"] = list(groups["audio"])
    groups["all"] = merged_ids + extra_ids
    return groups


def minimax_api_root(base_url=""):
    value = str(base_url or "").strip().rstrip("/")
    if value.endswith("/v1") or value.endswith("/v2"):
        value = value.rsplit("/", 1)[0]
    return value


def minimax_get_voice_url(base_url=""):
    return f"{minimax_api_root(base_url)}{MINIMAX_GET_VOICE_PATH}"


def minimax_t2a_url(base_url=""):
    return f"{minimax_api_root(base_url)}{MINIMAX_T2A_PATH}"


def minimax_chat_completions_url(base_url=""):
    return f"{minimax_api_root(base_url)}{MINIMAX_CHAT_COMPLETIONS_PATH}"


def minimax_models_url(base_url=""):
    return f"{minimax_api_root(base_url)}/v1/models"


def minimax_image_url(base_url=""):
    return f"{minimax_api_root(base_url)}{MINIMAX_IMAGE_PATH}"


def minimax_h3_submit_url(base_url=""):
    return f"{minimax_api_root(base_url)}{MINIMAX_H3_SUBMIT_PATH}"


def minimax_h3_query_url(base_url, task_id):
    quoted = urllib.parse.quote(str(task_id or ""), safe="")
    return f"{minimax_api_root(base_url)}{MINIMAX_H3_QUERY_PATH}/{quoted}"


def minimax_auth_headers(api_key, json_body=True, group_id=""):
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {str(api_key or '').strip()}",
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    group = str(group_id or "").strip()
    if group:
        headers["X-Group-Id"] = group
    return headers


def minimax_url_with_group(url, group_id=""):
    group = str(group_id or "").strip()
    if not group:
        return url
    parsed = urllib.parse.urlparse(url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query["GroupId"] = group
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


def _capability_values(item):
    values = []
    if not isinstance(item, dict):
        return values
    for key in ("type", "model_type", "modelType", "task", "category", "kind"):
        value = item.get(key)
        if isinstance(value, (str, int, float)):
            values.append(str(value).strip().lower())
        elif isinstance(value, list):
            values.extend(str(part).strip().lower() for part in value)
    capabilities = item.get("capabilities")
    if isinstance(capabilities, dict):
        values.extend(str(key).strip().lower() for key, enabled in capabilities.items() if enabled)
    elif isinstance(capabilities, list):
        values.extend(str(part).strip().lower() for part in capabilities)
    types = item.get("supported_endpoint_types")
    if isinstance(types, list):
        values.extend(str(part).strip().lower() for part in types)
    return values


def classify_minimax_speech_model_entry(item, model_id=""):
    """Classify from the official MiniMax catalog, then capability fields. Never regex the id."""
    mid = str(model_id or "").strip()
    if not mid and isinstance(item, dict):
        mid = str(item.get("id") or item.get("name") or item.get("model") or "").strip()
    if is_minimax_chat_model(mid):
        return "chat"
    if is_minimax_image_model(mid):
        return "image"
    if is_minimax_h3_model(mid):
        return "video"
    if is_minimax_t2a_model(mid):
        return "audio"
    values = _capability_values(item)
    if any("video" in value or value in {"t2v", "i2v", "s2v"} for value in values):
        return "video"
    if any("image" in value for value in values):
        return "image"
    if any(
        "audio" in value or "speech" in value or "t2a" in value or value in {"tts", "voice"}
        for value in values
    ):
        return "audio"
    if any("chat" in value or value in {"llm", "text", "completion", "completions"} for value in values):
        return "chat"
    return "unknown"


def build_get_voice_request(voice_type="system"):
    wanted = str(voice_type or "system").strip().lower() or "system"
    if wanted not in {"system", "voice_cloning", "voice_generation", "all"}:
        wanted = "system"
    return {"voice_type": wanted}


def parse_voice_entries(raw, source_key):
    entries = []
    if not isinstance(raw, dict):
        return entries
    rows = raw.get(source_key)
    if not isinstance(rows, list):
        return entries
    for item in rows:
        if not isinstance(item, dict):
            continue
        voice_id = str(item.get("voice_id") or "").strip()
        if not voice_id:
            continue
        name = str(item.get("voice_name") or voice_id).strip() or voice_id
        description = item.get("description")
        if isinstance(description, list):
            description = " ".join(str(part).strip() for part in description if str(part).strip())
        else:
            description = str(description or "").strip()
        entries.append({
            "voice_id": voice_id,
            "voice_name": name,
            "description": description,
            "source": source_key,
        })
    return entries


def parse_voice_list(raw):
    voices = []
    seen = set()
    for key in ("system_voice", "voice_cloning", "voice_generation"):
        for item in parse_voice_entries(raw if isinstance(raw, dict) else {}, key):
            if item["voice_id"] in seen:
                continue
            seen.add(item["voice_id"])
            voices.append(item)
    return voices


def t2a_model(requested=""):
    value = str(requested or "").strip()
    if value in MINIMAX_T2A_MODELS:
        return value
    return MINIMAX_DEFAULT_T2A_MODEL


def t2a_text(value, default=MINIMAX_DEFAULT_SAMPLE_TEXT, limit=MINIMAX_SAMPLE_TEXT_MAX):
    text = str(value or "").strip() or str(default or "").strip()
    cap = int(limit or MINIMAX_SAMPLE_TEXT_MAX)
    if cap > 0:
        text = text[:cap]
    return text


def build_t2a_request(text, voice_id, model="", sample=True):
    """Non-streaming T2A body. Sample clips stay short; per-shot dialogue is out of scope."""
    voice = str(voice_id or "").strip()
    if not voice:
        raise ValueError("MiniMax T2A 需要 voice_id")
    limit = MINIMAX_SAMPLE_TEXT_MAX if sample else MINIMAX_T2A_TEXT_MAX
    body = {
        "model": t2a_model(model),
        "text": t2a_text(text, limit=limit),
        "stream": False,
        "voice_setting": {
            "voice_id": voice,
            "speed": 1,
            "vol": 1,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1,
        },
    }
    if not body["text"]:
        raise ValueError("MiniMax T2A 需要合成文本")
    return body


def base_resp_ok(raw):
    if not isinstance(raw, dict):
        return False
    resp = raw.get("base_resp")
    if not isinstance(resp, dict):
        return True
    try:
        return int(resp.get("status_code") or 0) == 0
    except (TypeError, ValueError):
        return False


def error_text(raw, fallback=""):
    if isinstance(raw, dict):
        resp = raw.get("base_resp")
        if isinstance(resp, dict):
            message = str(resp.get("status_msg") or "").strip()
            code = resp.get("status_code")
            if message and code not in (None, 0, "0"):
                return message
            if message and message.lower() not in {"success", "ok"}:
                return message
        for key in ("detail", "error", "message"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = str(value.get("message") or value.get("msg") or "").strip()
                if nested:
                    return nested
    text = str(fallback or "").strip()
    return text[:500] if text else "MiniMax 语音接口失败"


def _looks_like_hex(value):
    text = str(value or "").strip()
    if len(text) < 8 or len(text) % 2:
        return False
    try:
        int(text[:16], 16)
    except ValueError:
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in text[:64])


def decode_t2a_audio_bytes(raw):
    """Official T2A returns hex in data.audio. Some proxies wrap base64 as audio_file."""
    if not isinstance(raw, dict):
        raise ValueError("MiniMax T2A 返回了无法识别的响应")
    if not base_resp_ok(raw):
        raise ValueError(error_text(raw, "MiniMax T2A 合成失败"))
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    hex_audio = str(data.get("audio") or raw.get("audio") or "").strip()
    if hex_audio and _looks_like_hex(hex_audio):
        try:
            return binascii.unhexlify(hex_audio)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("MiniMax T2A 音频 hex 无法解码") from exc
    b64_audio = str(
        raw.get("audio_file")
        or data.get("audio_file")
        or (hex_audio if hex_audio and not _looks_like_hex(hex_audio) else "")
    ).strip()
    if b64_audio:
        try:
            return base64.b64decode(b64_audio)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("MiniMax T2A 音频 base64 无法解码") from exc
    raise ValueError("MiniMax T2A 没有返回音频数据")


def extra_audio_length_ms(raw):
    if not isinstance(raw, dict):
        return None
    info = raw.get("extra_info")
    if not isinstance(info, dict):
        return None
    value = info.get("audio_length")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number > 0 else None


def _payload_attr(payload, *names, default=None):
    for name in names:
        if isinstance(payload, dict) and name in payload:
            return payload.get(name)
        if hasattr(payload, name):
            return getattr(payload, name)
    return default


def _reference_url(ref):
    if isinstance(ref, str):
        return ref.strip()
    if isinstance(ref, dict):
        return str(ref.get("url") or ref.get("image_file") or "").strip()
    return str(getattr(ref, "url", "") or "").strip()


def _reference_role(ref):
    if isinstance(ref, dict):
        return str(ref.get("role") or "").strip().lower()
    return str(getattr(ref, "role", "") or "").strip().lower()


def _content_url_item(kind, url, role=""):
    if kind == "image":
        item = {"type": "image_url", "image_url": {"url": url}}
    elif kind == "video":
        item = {"type": "video_url", "video_url": {"url": url}}
    else:
        item = {"type": "audio_url", "audio_url": {"url": url}}
    if role:
        item["role"] = role
    return item


def image_aspect_ratio(value, model=""):
    raw = str(value or "").strip().replace("x", ":").replace("×", ":")
    aliases = {
        "square": "1:1",
        "portrait": "9:16",
        "landscape": "16:9",
        "wide": "16:9",
        "keep_ratio": "",
        "adaptive": "",
        "auto": "",
    }
    raw = aliases.get(raw.lower(), raw)
    if not raw:
        return MINIMAX_IMAGE_DEFAULT_ASPECT
    if raw == "21:9" and str(model or "").strip() != "image-01":
        return MINIMAX_IMAGE_DEFAULT_ASPECT
    if raw not in MINIMAX_IMAGE_ASPECT_RATIOS:
        return MINIMAX_IMAGE_DEFAULT_ASPECT
    return raw


def build_image_request(prompt, model="", aspect_ratio="", reference_images=None, style_type="", count=1):
    chosen = str(model or "").strip() or MINIMAX_DEFAULT_IMAGE_MODEL
    if chosen not in MINIMAX_IMAGE_MODELS:
        raise ValueError(f"MiniMax 图片模型只支持 {', '.join(MINIMAX_IMAGE_MODELS)}")
    text = str(prompt or "").strip()
    if not text:
        raise ValueError("MiniMax 文生图需要 prompt")
    body = {
        "model": chosen,
        "prompt": text[:MINIMAX_IMAGE_PROMPT_MAX],
        "aspect_ratio": image_aspect_ratio(aspect_ratio, chosen),
        "response_format": "url",
        "n": max(1, min(9, int(count or 1))),
        "prompt_optimizer": False,
        "aigc_watermark": False,
    }
    refs = [item for item in (reference_images or []) if _reference_url(item)]
    if len(refs) > MINIMAX_IMAGE_SUBJECT_MAX:
        raise ValueError(f"MiniMax 图生图人物参考最多 {MINIMAX_IMAGE_SUBJECT_MAX} 张，当前 {len(refs)} 张，不会静默丢图。")
    if refs:
        body["subject_reference"] = [{
            "type": "character",
            "image_file": _reference_url(refs[0]),
        }]
    style = str(style_type or "").strip()
    if style:
        if chosen != "image-01-live":
            raise ValueError("画风 style 仅 image-01-live 生效")
        if style not in MINIMAX_IMAGE_LIVE_STYLES:
            raise ValueError(f"image-01-live 画风只支持 {', '.join(MINIMAX_IMAGE_LIVE_STYLES)}")
        body["style"] = {"style_type": style, "style_weight": 0.8}
    return body


def extract_image_result(raw):
    if not isinstance(raw, dict):
        raise ValueError("MiniMax 图片接口返回了无法识别的响应")
    if not base_resp_ok(raw):
        raise ValueError(error_text(raw, "MiniMax 图片生成失败"))
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    urls = data.get("image_urls") if isinstance(data.get("image_urls"), list) else []
    b64s = data.get("image_base64") if isinstance(data.get("image_base64"), list) else []
    url = next((str(item).strip() for item in urls if str(item).strip()), "")
    b64 = next((str(item).strip() for item in b64s if str(item).strip()), "")
    if url:
        return {"type": "url", "value": url}
    if b64:
        return {"type": "b64", "value": b64}
    raise ValueError("MiniMax 图片接口没有返回图片")


def h3_duration(payload):
    raw = _payload_attr(payload, "duration", default=MINIMAX_H3_DEFAULT_DURATION)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = MINIMAX_H3_DEFAULT_DURATION
    if value not in MINIMAX_H3_DURATIONS:
        raise ValueError(f"官方 MiniMax-H3 时长只支持 4–15 秒整数，当前是 {value}")
    return value


def h3_resolution(payload):
    raw = str(_payload_attr(payload, "resolution", "size", default="") or "").strip()
    compact = raw.lower().replace("p", "")
    mapped = {
        "": MINIMAX_H3_DEFAULT_RESOLUTION,
        "480": "768P",
        "720": "768P",
        "768": "768P",
        "1080": "2K",
        "2k": "2K",
    }.get(compact, None)
    if mapped not in MINIMAX_H3_RESOLUTIONS:
        raise ValueError(f"官方 MiniMax-H3 清晰度只支持 768P / 2K，当前是 {raw or mapped}")
    return mapped


def h3_t2v_ratio(payload):
    raw = str(_payload_attr(payload, "aspect_ratio", default="") or "").strip().replace("x", ":")
    if raw.lower() in {"", "keep_ratio", "adaptive", "auto"}:
        return "16:9"
    if raw not in MINIMAX_H3_T2V_RATIOS:
        raise ValueError(f"官方 MiniMax-H3 文生视频画幅不能为 {raw}，可选 {', '.join(MINIMAX_H3_T2V_RATIOS)}")
    return raw


def classify_h3_mode(images, videos, audios, multimodal=False):
    image_roles = [_reference_role(item) or "unlabeled" for item in images if _reference_url(item)]
    has_frame = any(role in {"first_frame", "last_frame"} for role in image_roles)
    has_explicit_ref = any(role in {"reference_image", "ref_image"} for role in image_roles)
    has_media = bool(videos or audios)
    if has_frame and (has_explicit_ref or has_media or multimodal):
        raise ValueError("官方 MiniMax-H3 图生视频（首尾帧）与多模态参考互斥，不能混用。")
    if multimodal or has_media or has_explicit_ref:
        return "r2va"
    if has_frame or image_roles:
        return "i2va"
    return "t2va"


async def build_h3_official_video_request(payload, requested_model="", resolve_ref=None):
    model = str(requested_model or "").strip() or MINIMAX_H3_MODEL
    if not is_minimax_h3_model(model):
        raise ValueError(f"官方 MiniMax 视频当前只接入 MiniMax-H3，不发送 {model}（Hailuo 历史家族未实现）")
    prompt = str(_payload_attr(payload, "prompt", default="") or "").strip()
    if not prompt:
        raise ValueError("官方 MiniMax-H3 必须包含非空 text prompt")
    if len(prompt) > MINIMAX_H3_PROMPT_MAX:
        raise ValueError(f"官方 MiniMax-H3 提示词最多 {MINIMAX_H3_PROMPT_MAX} 字符")

    images = [item for item in (_payload_attr(payload, "images", default=[]) or []) if _reference_url(item)]
    videos = [item for item in (_payload_attr(payload, "videos", default=[]) or []) if _reference_url(item) or (isinstance(item, str) and item.strip())]
    audios = [item for item in (_payload_attr(payload, "audios", default=[]) or []) if _reference_url(item) or (isinstance(item, str) and item.strip())]
    multimodal = bool(_payload_attr(payload, "multimodal", default=False))
    mode = classify_h3_mode(images, videos, audios, multimodal=multimodal)

    async def resolve(url, kind, index):
        text = str(url or "").strip()
        if not text:
            return ""
        if resolve_ref:
            return await resolve_ref(text, kind, index)
        return text

    content = [{"type": "text", "text": prompt}]
    if mode == "i2va":
        first = [item for item in images if _reference_role(item) == "first_frame"]
        last = [item for item in images if _reference_role(item) == "last_frame"]
        unlabeled = [item for item in images if _reference_role(item) not in {"first_frame", "last_frame"}]
        if not first and not last and unlabeled:
            first = unlabeled[:1]
            last = unlabeled[1:2]
        if len(first) > 1 or len(last) > 1:
            raise ValueError("官方 MiniMax-H3 首帧、尾帧各最多 1 张，不会静默截断。")
        if first:
            content.append(_content_url_item("image", await resolve(_reference_url(first[0]), "image", 1), "first_frame"))
        if last:
            content.append(_content_url_item("image", await resolve(_reference_url(last[0]), "image", 2), "last_frame"))
    elif mode == "r2va":
        if len(images) > MINIMAX_H3_REF_IMAGE_MAX:
            raise ValueError(f"官方 MiniMax-H3 参考图最多 {MINIMAX_H3_REF_IMAGE_MAX} 张，当前 {len(images)} 张。")
        if len(videos) > MINIMAX_H3_REF_VIDEO_MAX:
            raise ValueError(f"官方 MiniMax-H3 参考视频最多 {MINIMAX_H3_REF_VIDEO_MAX} 条，当前 {len(videos)} 条。")
        if len(audios) > MINIMAX_H3_REF_AUDIO_MAX:
            raise ValueError(f"官方 MiniMax-H3 参考音频最多 {MINIMAX_H3_REF_AUDIO_MAX} 条，当前 {len(audios)} 条。")
        for index, item in enumerate(images, 1):
            content.append(_content_url_item("image", await resolve(_reference_url(item), "image", index), "reference_image"))
        for index, item in enumerate(videos, 1):
            content.append(_content_url_item("video", await resolve(_reference_url(item) or str(item).strip(), "video", index), "reference_video"))
        for index, item in enumerate(audios, 1):
            content.append(_content_url_item("audio", await resolve(_reference_url(item) or str(item).strip(), "audio", index), "reference_audio"))

    body = {
        "model": MINIMAX_H3_MODEL,
        "content": content,
        "resolution": h3_resolution(payload),
        "duration": h3_duration(payload),
        "aigc_watermark": bool(_payload_attr(payload, "watermark", default=False)),
    }
    if mode == "t2va":
        body["ratio"] = h3_t2v_ratio(payload)
    elif mode == "r2va":
        raw_ratio = str(_payload_attr(payload, "aspect_ratio", default="") or "").strip().replace("x", ":")
        if raw_ratio and raw_ratio.lower() not in {"keep_ratio", "auto"}:
            if raw_ratio.lower() == "adaptive" or raw_ratio in MINIMAX_H3_T2V_RATIOS:
                body["ratio"] = "adaptive" if raw_ratio.lower() == "adaptive" else raw_ratio
            else:
                body["ratio"] = "adaptive"
        else:
            body["ratio"] = "adaptive"
    return body, mode


def h3_task_id(raw):
    if not isinstance(raw, dict):
        return ""
    for key in ("task_id", "id"):
        value = raw.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    task = raw.get("task")
    if isinstance(task, dict):
        value = task.get("id") or task.get("task_id")
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def h3_task_state(raw):
    if not isinstance(raw, dict):
        return "pending", ""
    task = raw.get("task") if isinstance(raw.get("task"), dict) else raw
    status = str(task.get("status") or raw.get("status") or "").strip().lower()
    if status in MINIMAX_H3_SUCCESS_STATUSES:
        return "success", status
    if status in MINIMAX_H3_FAILURE_STATUSES:
        return "failed", status
    return "pending", status


def h3_result_url(raw):
    if not isinstance(raw, dict):
        return ""
    task = raw.get("task") if isinstance(raw.get("task"), dict) else raw
    content = task.get("content") if isinstance(task.get("content"), dict) else {}
    return str(content.get("url") or "").strip()


def h3_error_text(raw, fallback=""):
    if isinstance(raw, dict):
        task = raw.get("task") if isinstance(raw.get("task"), dict) else raw
        error = task.get("error") if isinstance(task.get("error"), dict) else raw.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("msg") or "").strip()
            if message:
                return message
        text = error_text(raw, "")
        if text:
            return text
    return str(fallback or "官方 MiniMax-H3 视频任务失败")[:500]
