"""Grok2API 视频协议的纯逻辑。

Grok2API 使用严格的 JSON 视频合同：POST /v1/videos/generations 创建任务，
GET /v1/videos/{request_id} 查询，GET /v1/videos/{request_id}/content 取片。
本模块只负责字段映射、能力分类、URL 构造和响应归一化，不反向导入 main.py。
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


def classify_grok2api_model_entry(item, model_id=""):
    """只按上游能力字段分类；没有能力字段时保持未知/聊天，不按名称猜视频。"""
    if not isinstance(item, dict):
        return "chat"

    values = []
    for key in (
        "supported_endpoint_types",
        "capabilities",
        "supported_modalities",
        "modalities",
    ):
        value = item.get(key)
        if isinstance(value, dict):
            values.extend(str(name).strip().lower() for name, enabled in value.items() if enabled)
        elif isinstance(value, list):
            values.extend(str(part).strip().lower() for part in value)
        elif isinstance(value, str):
            values.append(value.strip().lower())

    for key in ("type", "model_type", "modelType", "capability"):
        value = item.get(key)
        if isinstance(value, (str, int, float)):
            values.append(str(value).strip().lower())

    if any("video" in value or value in {"t2v", "i2v", "s2v"} for value in values):
        return "video"
    if any("image" in value for value in values):
        return "image"
    return "chat"


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
