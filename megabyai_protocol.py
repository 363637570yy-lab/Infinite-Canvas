"""MegabyAI 视频协议的纯逻辑。

MegabyAI 的视频接口使用 POST /v1/videos + GET /v1/videos/{task_id}，
但请求字段与项目已有视频协议不同。本模块只负责协议字段、能力分类和
任务响应归一化，不反向导入 main.py，便于在不触网、不产生费用的情况下测试。
"""

import urllib.parse

from fastapi import HTTPException


MEGABYAI_PROTOCOL = "megabyai"
MEGABYAI_DEFAULT_BASE_URL = "https://newapi.megabyai.cc"

# 文档明确登记的模型属于同一个请求体家族。模型列表只是平台目录，不能作为
# 发送时的模型白名单；站点改名或新增模型时仍沿用 MegabyAI 的视频请求合同。
# 已登记模型保留文档中的 4 秒下限，未登记/改名模型使用站点新增模型的 3 秒下限。
MEGABYAI_FAMILY_VIDEOS = "videos"
MEGABYAI_FAMILY_DYNAMIC_VIDEO = "dynamic-video"
MEGABYAI_VIDEO_MODEL_FAMILIES = {
    "videos-standard": MEGABYAI_FAMILY_VIDEOS,
    "videos-fast": MEGABYAI_FAMILY_VIDEOS,
    "videos-mini": MEGABYAI_FAMILY_VIDEOS,
}
MEGABYAI_DYNAMIC_MIN_DURATION = 3
MEGABYAI_DYNAMIC_MAX_DURATION = 15

MEGABYAI_ASPECT_RATIOS = {"16:9", "9:16", "1:1"}
MEGABYAI_RESOLUTIONS = {"720p", "480p"}
MEGABYAI_DEFAULT_ASPECT_RATIO = "16:9"
MEGABYAI_DEFAULT_RESOLUTION = "720p"
MEGABYAI_MIN_DURATION = 4
MEGABYAI_MAX_DURATION = 15
MEGABYAI_MAX_IMAGE_REFS = 9
MEGABYAI_MAX_VIDEO_REFS = 3
MEGABYAI_MAX_AUDIO_REFS = 3

MEGABYAI_TERMINAL_SUCCESS_STATUSES = {"completed"}
MEGABYAI_TERMINAL_FAILURE_STATUSES = {"failed"}


def megabyai_api_root(base_url=""):
    value = str(base_url or MEGABYAI_DEFAULT_BASE_URL).strip().rstrip("/")
    if not value:
        value = MEGABYAI_DEFAULT_BASE_URL
    if value.endswith("/v1") or value.endswith("/v2"):
        value = value.rsplit("/", 1)[0]
    return value


def megabyai_video_submit_url(base_url=""):
    return f"{megabyai_api_root(base_url)}/v1/videos"


def megabyai_video_task_url(base_url, task_id):
    quoted = urllib.parse.quote(str(task_id or ""), safe="")
    return f"{megabyai_api_root(base_url)}/v1/videos/{quoted}"


def megabyai_video_content_url(base_url, task_id):
    quoted = urllib.parse.quote(str(task_id or ""), safe="")
    return f"{megabyai_api_root(base_url)}/v1/videos/{quoted}/content"


def megabyai_model_family(model):
    """按已知请求合同选择参数边界，不把 API 模型目录当发送白名单。"""
    normalized = str(model or "").strip().lower()
    family = MEGABYAI_VIDEO_MODEL_FAMILIES.get(normalized)
    if family:
        return family
    return MEGABYAI_FAMILY_DYNAMIC_VIDEO if normalized else ""


def classify_megabyai_model_entry(item, model_id=""):
    """按上游明确的端点/描述元数据分类，不按模型名称猜能力。

    MegabyAI 档案的模型示例使用 ``owned_by=video-api`` 和
    ``description=.../v1/videos``，而不一定提供 supported_endpoint_types；
    这些字段明确说明了视频合同，可以据此归类。
    """
    if isinstance(item, dict):
        endpoint_types = item.get("supported_endpoint_types")
        values = [
            str(value).strip().lower()
            for value in endpoint_types
        ] if isinstance(endpoint_types, list) else []
        if any("video" in value for value in values):
            return "video"
        if any("image" in value for value in values):
            return "image"
        if values:
            return "chat"
        owned_by = str(item.get("owned_by") or "").strip().lower()
        description = str(item.get("description") or "").strip().lower()
        if owned_by == "video-api" or "/v1/videos" in description:
            return "video"
    return "chat"


def _reference_value(ref):
    if isinstance(ref, str):
        return ref.strip()
    if isinstance(ref, dict):
        return str(ref.get("url") or "").strip()
    return str(getattr(ref, "url", "") or "").strip()


def _reference_role(ref):
    if isinstance(ref, dict):
        return str(ref.get("role") or "").strip().lower()
    return str(getattr(ref, "role", "") or "").strip().lower()


def _prompt(payload):
    value = str(getattr(payload, "prompt", "") or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="MegabyAI 视频提示词不能为空。")
    return value


def _duration(payload, family=MEGABYAI_FAMILY_VIDEOS):
    try:
        value = int(getattr(payload, "duration", 5))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="MegabyAI 视频时长必须是整数。")
    minimum = (
        MEGABYAI_DYNAMIC_MIN_DURATION
        if family == MEGABYAI_FAMILY_DYNAMIC_VIDEO
        else MEGABYAI_MIN_DURATION
    )
    maximum = (
        MEGABYAI_DYNAMIC_MAX_DURATION
        if family == MEGABYAI_FAMILY_DYNAMIC_VIDEO
        else MEGABYAI_MAX_DURATION
    )
    if not minimum <= value <= maximum:
        raise HTTPException(
            status_code=400,
            detail=f"MegabyAI 视频时长必须在 {minimum}-{maximum} 秒之间。",
        )
    return value


def _aspect_ratio(payload):
    value = str(getattr(payload, "aspect_ratio", "") or "").strip()
    if not value:
        value = str(getattr(payload, "size", "") or "").strip()
    value = value or MEGABYAI_DEFAULT_ASPECT_RATIO
    if value not in MEGABYAI_ASPECT_RATIOS:
        choices = ", ".join(sorted(MEGABYAI_ASPECT_RATIOS))
        raise HTTPException(
            status_code=400,
            detail=f"MegabyAI 不支持画幅「{value}」；可选值：{choices}。",
        )
    return value


def _resolution(payload):
    value = str(getattr(payload, "resolution", "") or "").strip().lower()
    value = value or MEGABYAI_DEFAULT_RESOLUTION
    if value not in MEGABYAI_RESOLUTIONS:
        choices = ", ".join(sorted(MEGABYAI_RESOLUTIONS))
        raise HTTPException(
            status_code=400,
            detail=f"MegabyAI 不支持清晰度「{value}」；可选值：{choices}。",
        )
    return value


def _reject_unsupported_modes(payload, images):
    frame_roles = {"first", "last", "first_frame", "last_frame", "start_frame", "end_frame"}
    if any(_reference_role(ref) in frame_roles for ref in images or []):
        raise HTTPException(
            status_code=400,
            detail="MegabyAI 文档不支持首帧/尾帧字段；请移除首尾帧标记并使用普通参考图。",
        )
    if bool(getattr(payload, "generate_audio", False)):
        raise HTTPException(
            status_code=400,
            detail="MegabyAI 文档不支持 generate_audio 生成音频开关；只能传入 referenceAudios 参考音频。",
        )
    for field in ("enhance_prompt", "enable_upsample", "watermark", "camerafixed", "return_last_frame"):
        if bool(getattr(payload, field, False)):
            raise HTTPException(status_code=400, detail=f"MegabyAI 文档未提供 {field} 参数。")
    if getattr(payload, "seed", None) is not None:
        raise HTTPException(status_code=400, detail="MegabyAI 文档未提供 seed 参数。")
    if getattr(payload, "compliance_enabled", None) is True or str(getattr(payload, "compliance_mode", "") or "").strip():
        raise HTTPException(status_code=400, detail="MegabyAI 文档未提供真人合规参数。")


async def _resolve_reference(resolve_ref, value, kind, index):
    resolved = await resolve_ref(value, kind, index) if resolve_ref else value
    text = str(resolved or "").strip()
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail=f"MegabyAI 第 {index} 个{kind}参考素材没有得到有效的公网 http(s) URL。",
        )
    return text


async def _resolve_references(refs, kind, limit, resolve_ref):
    values = [_reference_value(ref) for ref in refs or [] if _reference_value(ref)]
    if len(values) > limit:
        raise HTTPException(
            status_code=400,
            detail=f"MegabyAI {kind}参考素材最多 {limit} 个，当前为 {len(values)} 个；不会静默截断。",
        )
    return [
        await _resolve_reference(resolve_ref, value, kind, index)
        for index, value in enumerate(values, 1)
    ]


async def build_megabyai_video_request(
    payload,
    requested_model,
    resolve_ref=None,
):
    model = str(requested_model or "").strip()
    family = megabyai_model_family(model)
    if not family:
        raise HTTPException(
            status_code=400,
            detail=(
                f"MegabyAI 模型「{model or '(empty)'}」不能为空；"
                "模型名称不由项目白名单限制，必须填写实际要发送给上游的模型名称。"
            ),
        )

    images = getattr(payload, "images", []) or []
    videos = getattr(payload, "videos", []) or []
    audios = getattr(payload, "audios", []) or []
    _reject_unsupported_modes(payload, images)
    body = {
        "model": model,
        "prompt": _prompt(payload),
        "duration": _duration(payload, family),
        "ratio": _aspect_ratio(payload),
        "resolution": _resolution(payload),
    }
    image_urls = await _resolve_references(images, "图片", MEGABYAI_MAX_IMAGE_REFS, resolve_ref)
    video_urls = await _resolve_references(videos, "视频", MEGABYAI_MAX_VIDEO_REFS, resolve_ref)
    audio_urls = await _resolve_references(audios, "音频", MEGABYAI_MAX_AUDIO_REFS, resolve_ref)
    if image_urls:
        body["referenceImages"] = image_urls
    if video_urls:
        body["referenceVideos"] = video_urls
    if audio_urls:
        body["referenceAudios"] = audio_urls
    return body


def megabyai_task_id(raw):
    if not isinstance(raw, dict):
        return ""
    nodes = [raw]
    if isinstance(raw.get("data"), dict):
        nodes.append(raw["data"])
    for node in nodes:
        for key in ("task_id", "id"):
            value = node.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
    return ""


def megabyai_task_state(raw):
    if not isinstance(raw, dict):
        return "pending", ""
    node = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    status = str(node.get("status") or raw.get("status") or "").strip().lower()
    if status in MEGABYAI_TERMINAL_SUCCESS_STATUSES:
        return "success", status
    if status in MEGABYAI_TERMINAL_FAILURE_STATUSES:
        return "failed", status
    return "pending", status


def _string_at_path(raw, path):
    current = raw
    for key in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return current.strip() if isinstance(current, str) else ""


def megabyai_video_result_urls(raw):
    if not isinstance(raw, dict):
        return []
    paths = (
        ("video_url",),
        ("url",),
        ("metadata", "content_url"),
        ("metadata", "local_url"),
        ("metadata", "url"),
        ("data", "video_url"),
        ("data", "url"),
        ("data", "metadata", "content_url"),
        ("data", "metadata", "local_url"),
    )
    urls = []
    for path in paths:
        value = _string_at_path(raw, path)
        if value and value not in urls:
            urls.append(value)
    return urls


def megabyai_error_text(raw, fallback=""):
    if isinstance(raw, dict):
        error = raw.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "").strip()
            message = str(error.get("message") or error.get("detail") or "").strip()
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
