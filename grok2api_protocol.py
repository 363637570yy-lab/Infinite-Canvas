"""Grok2API 的纯协议逻辑。

Grok2API 的视频、图片和聊天接口都使用独立的 JSON 合同。本模块只负责
字段映射、能力分类、URL 构造和响应归一化，不反向导入 main.py。
"""

import base64
import urllib.parse

from fastapi import HTTPException


GROK2API_PROTOCOL = "grok2api"
GROK2API_DEFAULT_BASE_URL = "http://127.0.0.1:8000"

GROK2API_ASPECT_RATIOS = {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"}
GROK2API_RESOLUTIONS = {"480p", "720p", "1080p"}
GROK2API_DEFAULT_ASPECT_RATIO = "16:9"
GROK2API_DEFAULT_RESOLUTION = "720p"
GROK2API_DEFAULT_DURATION = 8
GROK2API_MIN_DURATION = 1
GROK2API_MAX_DURATION = 15
GROK2API_MAX_IMAGE_REFS = 8
GROK2API_IMAGE_ASPECT_RATIOS = {
    "auto",
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "2:1",
    "1:2",
    "19.5:9",
    "9:19.5",
    "20:9",
    "9:20",
}
GROK2API_IMAGE_EDIT_SIZES = {"auto", "1024x1024", "1024x1536", "1536x1024"}
GROK2API_IMAGE_RESOLUTIONS = {"1k", "2k"}
GROK2API_IMAGE_EDIT_RESOLUTIONS = {"1k", "2k"}
GROK2API_IMAGE_RESPONSE_FORMATS = {"url", "b64_json"}
GROK2API_INPUT_ASSET_PREFIX = "input_"
GROK2API_INPUT_ASSET_BYTES = 24

GROK2API_TERMINAL_SUCCESS_STATUSES = {"done", "completed", "succeeded", "success", "ready"}
GROK2API_TERMINAL_FAILURE_STATUSES = {
    "failed",
    "failure",
    "expired",
    "cancelled",
    "canceled",
    "error",
    "rejected",
    "timeout",
    "timed_out",
}


def grok2api_api_root(base_url=""):
    value = str(base_url or GROK2API_DEFAULT_BASE_URL).strip().rstrip("/")
    if not value:
        value = GROK2API_DEFAULT_BASE_URL
    if value.endswith("/v1") or value.endswith("/v2"):
        value = value.rsplit("/", 1)[0]
    return value


def grok2api_video_submit_url(base_url=""):
    return f"{grok2api_api_root(base_url)}/v1/videos/generations"


def grok2api_video_task_url(base_url, request_id):
    quoted = urllib.parse.quote(str(request_id or ""), safe="")
    return f"{grok2api_api_root(base_url)}/v1/videos/{quoted}"


def grok2api_video_content_url(base_url, request_id):
    quoted = urllib.parse.quote(str(request_id or ""), safe="")
    return f"{grok2api_api_root(base_url)}/v1/videos/{quoted}/content"


def grok2api_chat_completions_url(base_url=""):
    return f"{grok2api_api_root(base_url)}/v1/chat/completions"


def grok2api_image_generation_url(base_url=""):
    return f"{grok2api_api_root(base_url)}/v1/images/generations"


def grok2api_image_edit_url(base_url=""):
    return f"{grok2api_api_root(base_url)}/v1/images/edits"


def classify_grok2api_model_entry(item, model_id=""):
    """只按上游明确的能力字段分类；缺失能力字段时返回 unknown。

    Grok2API 当前公开的 /v1/models 通常只有模型 ID，不能据模型名推断
    图片、视频或聊天能力。保留 unknown 能让调用方显式处理，而不是静默
    把模型放进错误的请求链路。
    """
    if not isinstance(item, dict):
        return "unknown"

    values = []
    for key in (
        "supported_endpoint_types",
        "capabilities",
        "supported_modalities",
        "modalities",
        "output_modalities",
        "supported_operations",
        "operations",
    ):
        value = item.get(key)
        if isinstance(value, dict):
            values.extend(str(name).strip().lower() for name, enabled in value.items() if enabled)
        elif isinstance(value, list):
            values.extend(str(part).strip().lower() for part in value)
        elif isinstance(value, str):
            values.append(value.strip().lower())

    for key in ("type", "model_type", "modelType", "capability", "endpoint_type", "endpointType"):
        value = item.get(key)
        if isinstance(value, dict):
            values.extend(str(name).strip().lower() for name, enabled in value.items() if enabled)
        elif isinstance(value, (str, int, float)):
            values.append(str(value).strip().lower())
        elif isinstance(value, list):
            values.extend(str(part).strip().lower() for part in value)

    if any("video" in value or value in {"t2v", "i2v", "s2v"} for value in values):
        return "video"
    if any("image" in value or value in {"t2i", "iti", "edit_image", "image_edit"} for value in values):
        return "image"
    if any(
        "chat" in value
        or value in {"llm", "text", "completion", "completions", "response", "responses", "text_generation"}
        for value in values
    ):
        return "chat"
    return "unknown"


def _reference_locator(ref):
    if isinstance(ref, str):
        return "url", ref.strip()
    if isinstance(ref, dict):
        url = str(ref.get("url") or "").strip()
        file_id = str(ref.get("file_id") or "").strip()
    else:
        url = str(getattr(ref, "url", "") or "").strip()
        file_id = str(getattr(ref, "file_id", "") or "").strip()
    if url and file_id:
        raise HTTPException(status_code=400, detail="Grok2API 每个图片参考素材只能提供 url 或 file_id 其中一个。")
    if file_id:
        encoded = file_id[len(GROK2API_INPUT_ASSET_PREFIX):] if file_id.startswith(GROK2API_INPUT_ASSET_PREFIX) else ""
        valid_alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        if not encoded or len(encoded) != 32 or any(char not in valid_alphabet for char in encoded):
            raise HTTPException(
                status_code=400,
                detail="Grok2API file_id 必须是该站签发的 input_* 临时素材 ID。",
            )
        try:
            decoded = base64.urlsafe_b64decode(encoded + "=")
        except (ValueError, TypeError):
            decoded = b""
        if len(decoded) != GROK2API_INPUT_ASSET_BYTES:
            raise HTTPException(
                status_code=400,
                detail="Grok2API file_id 必须是该站签发的 input_* 临时素材 ID。",
            )
        return "file_id", file_id
    if url:
        return "url", url
    raise HTTPException(status_code=400, detail="Grok2API 图片参考素材缺少 url 或 file_id。")


def _reference_role(ref):
    if isinstance(ref, dict):
        return str(ref.get("role") or "").strip().lower()
    return str(getattr(ref, "role", "") or "").strip().lower()


def _prompt(payload):
    return str(getattr(payload, "prompt", "") or "").strip()


def _duration(payload):
    raw = getattr(payload, "duration", GROK2API_DEFAULT_DURATION)
    if raw in (None, ""):
        return GROK2API_DEFAULT_DURATION
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Grok2API 视频时长必须是整数。")
    if not GROK2API_MIN_DURATION <= value <= GROK2API_MAX_DURATION:
        raise HTTPException(
            status_code=400,
            detail=f"Grok2API 视频时长必须在 {GROK2API_MIN_DURATION}-{GROK2API_MAX_DURATION} 秒之间。",
        )
    return value


def _aspect_ratio(payload):
    value = str(getattr(payload, "aspect_ratio", "") or "").strip().lower()
    if not value:
        value = str(getattr(payload, "size", "") or "").strip().lower()
    value = value or GROK2API_DEFAULT_ASPECT_RATIO
    if value not in GROK2API_ASPECT_RATIOS:
        choices = ", ".join(sorted(GROK2API_ASPECT_RATIOS))
        raise HTTPException(
            status_code=400,
            detail=f"Grok2API 不支持画幅「{value}」；可选值：{choices}。",
        )
    return value


def _resolution(payload):
    value = str(getattr(payload, "resolution", "") or "").strip().lower()
    value = value or GROK2API_DEFAULT_RESOLUTION
    if value not in GROK2API_RESOLUTIONS:
        choices = ", ".join(sorted(GROK2API_RESOLUTIONS))
        raise HTTPException(
            status_code=400,
            detail=f"Grok2API 不支持清晰度「{value}」；可选值：{choices}。",
        )
    return value


def _reject_unsupported_modes(payload, images, videos, audios):
    frame_roles = {"first", "last", "first_frame", "last_frame", "start_frame", "end_frame"}
    if any(_reference_role(ref) in frame_roles for ref in images or []):
        raise HTTPException(
            status_code=400,
            detail="Grok2API 文档不支持首帧/尾帧字段，请移除首尾帧标记并使用普通参考图。",
        )
    if videos:
        raise HTTPException(status_code=400, detail="Grok2API 视频接口不支持参考视频字段。")
    if audios:
        raise HTTPException(status_code=400, detail="Grok2API 视频接口不支持参考音频字段。")
    unsupported_flags = (
        "generate_audio",
        "enhance_prompt",
        "enable_upsample",
        "watermark",
        "camerafixed",
        "return_last_frame",
        "multimodal",
    )
    for field in unsupported_flags:
        if bool(getattr(payload, field, False)):
            raise HTTPException(status_code=400, detail=f"Grok2API 文档未提供 {field} 参数。")
    if getattr(payload, "seed", None) is not None:
        raise HTTPException(status_code=400, detail="Grok2API 文档未提供 seed 参数。")
    if getattr(payload, "compliance_enabled", None) is True or str(getattr(payload, "compliance_mode", "") or "").strip():
        raise HTTPException(status_code=400, detail="Grok2API 文档未提供真人合规参数。")


async def _resolve_reference(resolve_ref, value, index):
    resolved = await resolve_ref(value, "图片", index) if resolve_ref else value
    text = str(resolved or "").strip()
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail=f"Grok2API 第 {index} 个图片参考素材没有得到有效的公网 http(s) URL。",
        )
    return text


async def build_grok2api_video_request(payload, requested_model, resolve_ref=None):
    model = str(requested_model or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="Grok2API 视频模型名称不能为空。")

    images = getattr(payload, "images", []) or []
    videos = getattr(payload, "videos", []) or []
    audios = getattr(payload, "audios", []) or []
    _reject_unsupported_modes(payload, images, videos, audios)

    locators = []
    for ref in images:
        if isinstance(ref, str) and not ref.strip():
            continue
        kind, value = _reference_locator(ref)
        locators.append((kind, value))
    if len(locators) > GROK2API_MAX_IMAGE_REFS:
        raise HTTPException(
            status_code=400,
            detail=f"Grok2API 图片参考素材最多 {GROK2API_MAX_IMAGE_REFS} 张，当前为 {len(locators)} 张；不会静默截断。",
        )
    image_inputs = []
    for index, (kind, value) in enumerate(locators, 1):
        if kind == "file_id":
            image_inputs.append({"file_id": value})
        else:
            image_inputs.append({"url": await _resolve_reference(resolve_ref, value, index)})
    prompt = _prompt(payload)
    if not prompt and not image_inputs:
        raise HTTPException(status_code=400, detail="Grok2API 文生视频必须提供 prompt。")

    body = {
        "model": model,
        "prompt": prompt,
        "duration": _duration(payload),
        "aspect_ratio": _aspect_ratio(payload),
        "resolution": _resolution(payload),
    }
    if image_inputs:
        body["image"] = image_inputs[0]
        if len(image_inputs) > 1:
            body["reference_images"] = image_inputs[1:]
    return body


def _image_count(value):
    if value in (None, ""):
        return 1
    try:
        count = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Grok2API 图片数量 n 必须是整数。")
    if not 1 <= count <= 10:
        raise HTTPException(status_code=400, detail="Grok2API 图片数量 n 必须在 1-10 之间。")
    return count


def _image_response_format(value):
    result = str(value or "url").strip().lower() or "url"
    if result not in GROK2API_IMAGE_RESPONSE_FORMATS:
        choices = ", ".join(sorted(GROK2API_IMAGE_RESPONSE_FORMATS))
        raise HTTPException(status_code=400, detail=f"Grok2API 图片 response_format 不支持「{result}」；可选值：{choices}。")
    return result


def _image_aspect_ratio(value, size=""):
    result = str(value or "").strip().lower()
    aliases = {
        "square": "1:1",
        "portrait": "2:3",
        "landscape": "3:2",
        "portrait43": "3:4",
        "landscape43": "4:3",
        "story": "9:16",
        "wide": "16:9",
    }
    result = aliases.get(result, result)
    if not result and str(size or "").strip():
        size_aliases = {
            "auto": "auto",
            "1024x1024": "1:1",
            "1280x720": "16:9",
            "720x1280": "9:16",
            "1536x1024": "3:2",
            "1792x1024": "3:2",
            "1024x1536": "2:3",
            "1024x1792": "2:3",
        }
        result = size_aliases.get(str(size).strip().lower(), "")
        if not result:
            raise HTTPException(
                status_code=400,
                detail="Grok2API 图片 size 不能单独使用该尺寸；请同时提供受支持的 aspect_ratio。",
            )
    if result and result not in GROK2API_IMAGE_ASPECT_RATIOS:
        choices = ", ".join(sorted(GROK2API_IMAGE_ASPECT_RATIOS))
        raise HTTPException(status_code=400, detail=f"Grok2API 图片不支持画幅「{result}」；可选值：{choices}。")
    return result


def _image_resolution(value, editing=False):
    result = str(value or "1k").strip().lower() or "1k"
    choices_set = GROK2API_IMAGE_EDIT_RESOLUTIONS if editing else GROK2API_IMAGE_RESOLUTIONS
    if result not in choices_set:
        choices = ", ".join(sorted(choices_set))
        raise HTTPException(status_code=400, detail=f"Grok2API 图片不支持清晰度「{result}」；可选值：{choices}。")
    return result


def _image_size(value, editing):
    result = str(value or "").strip().lower().replace("*", "x").replace("×", "x")
    if editing and result and result not in GROK2API_IMAGE_EDIT_SIZES:
        choices = ", ".join(sorted(GROK2API_IMAGE_EDIT_SIZES))
        raise HTTPException(status_code=400, detail=f"Grok2API 图片编辑 size 不支持「{result}」；可选值：{choices}。")
    return result


def _reject_unsupported_image_options(quality):
    value = str(quality or "").strip().lower()
    if value and value != "auto":
        raise HTTPException(
            status_code=400,
            detail="Grok2API 图片合同未提供 quality 参数；请将质量设为自动，避免静默改变上游默认值。",
        )


async def _resolve_image_reference(resolve_ref, value, index):
    resolved = await resolve_ref(value, "图片", index) if resolve_ref else value
    text = str(resolved or "").strip()
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail=f"Grok2API 第 {index} 个图片参考素材没有得到有效的公网 http(s) URL。",
        )
    return text


async def build_grok2api_image_request(
    prompt,
    requested_model,
    size="",
    aspect_ratio="",
    resolution="",
    reference_images=None,
    quality="",
    count=1,
    response_format="url",
    resolve_ref=None,
):
    """构造 Grok2API /images/generations 或 /images/edits 的 JSON 请求体。"""
    model = str(requested_model or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="Grok2API 图片模型名称不能为空。")
    text_prompt = str(prompt or "").strip()
    if not text_prompt:
        raise HTTPException(status_code=400, detail="Grok2API 图片 prompt 不能为空。")

    _reject_unsupported_image_options(quality)
    refs = [ref for ref in (reference_images or []) if ref is not None]
    if len(refs) > GROK2API_MAX_IMAGE_REFS:
        raise HTTPException(
            status_code=400,
            detail=f"Grok2API 图片参考素材最多 {GROK2API_MAX_IMAGE_REFS} 张，当前为 {len(refs)} 张；不会静默截断。",
        )
    image_inputs = []
    for index, ref in enumerate(refs, 1):
        kind, value = _reference_locator(ref)
        if kind != "url":
            raise HTTPException(
                status_code=400,
                detail="Grok2API 图片编辑当前只接受 image.url，不接受 file_id；请使用公网图片 URL。",
            )
        image_inputs.append({"url": await _resolve_image_reference(resolve_ref, value, index)})

    editing = bool(image_inputs)
    normalized_count = _image_count(count)
    body = {
        "model": model,
        "prompt": text_prompt,
        "n": normalized_count,
        "response_format": _image_response_format(response_format),
    }
    normalized_size = _image_size(size, editing)
    normalized_aspect = _image_aspect_ratio(aspect_ratio, size)
    normalized_resolution = _image_resolution(resolution, editing)
    if normalized_size:
        body["size"] = normalized_size
    if normalized_aspect:
        body["aspect_ratio"] = normalized_aspect
    if normalized_resolution:
        body["resolution"] = normalized_resolution
    if editing:
        if len(image_inputs) == 1:
            body["image"] = image_inputs[0]
        else:
            body["images"] = image_inputs
    return body


def grok2api_task_id(raw):
    if not isinstance(raw, dict):
        return ""
    nodes = [raw]
    if isinstance(raw.get("data"), dict):
        nodes.append(raw["data"])
    for node in nodes:
        for key in ("request_id", "id", "task_id"):
            value = node.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
    return ""


def grok2api_task_state(raw):
    if not isinstance(raw, dict):
        return "pending", ""
    node = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    status = str(node.get("status") or raw.get("status") or "").strip().lower()
    if status in GROK2API_TERMINAL_SUCCESS_STATUSES:
        return "success", status
    if status in GROK2API_TERMINAL_FAILURE_STATUSES:
        return "failed", status
    return "pending", status


def _video_url_from_node(node):
    if not isinstance(node, dict):
        return ""
    video = node.get("video")
    if isinstance(video, dict):
        value = video.get("url")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def grok2api_video_result_urls(raw):
    if not isinstance(raw, dict):
        return []
    urls = []
    for node in (raw, raw.get("data")):
        value = _video_url_from_node(node)
        if value and value not in urls:
            urls.append(value)
    return urls


def grok2api_error_text(raw, fallback=""):
    if isinstance(raw, dict):
        error = raw.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "").strip()
            message = str(error.get("message") or error.get("detail") or error.get("type") or "").strip()
            if code and message:
                return f"{code}: {message}"
            if message:
                return message
            if code:
                return code
        elif isinstance(error, str) and error.strip():
            return error.strip()
        for key in ("message", "detail"):
            value = raw.get(key)
            if value:
                return str(value).strip()
    return str(fallback or "").strip()[:500] or str(raw)[:500]
