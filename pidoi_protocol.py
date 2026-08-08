"""Pidoi 视频协议的纯逻辑。

本模块不导入 main.py：请求体家族、能力分类、任务响应归一化和 URL 构造
都可以在不触网、不产生费用的情况下单独测试。Pidoi 同一站点的模型并不
共用请求体，未被文档登记的模型必须显式拒绝，不能按模型名猜字段。
"""

import re
import urllib.parse

from fastapi import HTTPException


PIDOI_PROTOCOL = "pidoi"
PIDOI_DEFAULT_BASE_URL = "https://pidoi.com"

PIDOI_FAMILY_OMNI_FLASH_720P = "omni-flash-720p"
PIDOI_FAMILY_SORA_V3_933_PRO = "sora-v3-933-pro"
PIDOI_FAMILY_TEJIASD = "tejiasd"

PIDOI_VIDEO_MODEL_FAMILIES = {
    "omni-flash-720p": PIDOI_FAMILY_OMNI_FLASH_720P,
    "sora-v3-933-pro": PIDOI_FAMILY_SORA_V3_933_PRO,
    "tejiasd": PIDOI_FAMILY_TEJIASD,
}

PIDOI_OMNI_ASPECT_RATIOS = {"1:1", "16:9", "9:16", "4:3", "3:4"}
# The 933 document explicitly includes 21:9; it is not valid for the other
# two documented families.
PIDOI_SORA_ASPECT_RATIOS = PIDOI_OMNI_ASPECT_RATIOS | {"21:9"}
PIDOI_TEJIASD_ASPECT_RATIOS = set(PIDOI_OMNI_ASPECT_RATIOS)
PIDOI_ASPECT_RATIOS = PIDOI_SORA_ASPECT_RATIOS
PIDOI_OMNI_DURATIONS = {5, 10}
# The parameter table lists 15, while the document's image+audio example
# explicitly sends 5. Accept both values until the upstream documentation is
# made consistent; reject all values outside the two documented examples.
PIDOI_SORA_DURATIONS = {5, 15}
PIDOI_OMNI_MAX_IMAGES = 4
PIDOI_OMNI_MAX_VIDEOS = 3
PIDOI_OMNI_MAX_AUDIOS = 3
PIDOI_SORA_MAX_IMAGES = 9
PIDOI_SORA_MAX_VIDEOS = 3
PIDOI_SORA_MAX_AUDIOS = 3
PIDOI_TEJIASD_MAX_IMAGES = 9
PIDOI_TEJIASD_MAX_VIDEOS = 3
PIDOI_TEJIASD_MAX_AUDIOS = 3

PIDOI_TERMINAL_SUCCESS_STATUSES = {
    "COMPLETED",
    "COMPLETE",
    "SUCCESS",
    "SUCCEEDED",
    "DONE",
    "FINISHED",
}
PIDOI_TERMINAL_FAILURE_STATUSES = {
    "FAILED",
    "FAILURE",
    "ERROR",
    "CANCELLED",
    "CANCELED",
    "TIMEOUT",
    "EXPIRED",
    "REJECTED",
}


def pidoi_api_root(base_url=""):
    value = str(base_url or PIDOI_DEFAULT_BASE_URL).strip().rstrip("/")
    if not value:
        value = PIDOI_DEFAULT_BASE_URL
    if value.endswith("/v1") or value.endswith("/v2"):
        value = value.rsplit("/", 1)[0]
    return value


def pidoi_video_submit_url(base_url=""):
    return f"{pidoi_api_root(base_url)}/v1/videos"


def pidoi_video_task_url(base_url, task_id):
    quoted = urllib.parse.quote(str(task_id or ""), safe="")
    return f"{pidoi_api_root(base_url)}/v1/videos/{quoted}"


def pidoi_video_content_url(base_url, task_id):
    quoted = urllib.parse.quote(str(task_id or ""), safe="")
    return f"{pidoi_api_root(base_url)}/v1/videos/{quoted}/content"


def pidoi_model_family(model):
    return PIDOI_VIDEO_MODEL_FAMILIES.get(str(model or "").strip().lower(), "")


def classify_pidoi_model_entry(item, model_id=""):
    """按 /v1/models 的能力字段分类；缺少能力字段时按未知处理。"""
    if isinstance(item, dict):
        endpoint_types = item.get("supported_endpoint_types")
        values = [str(value).strip().lower() for value in endpoint_types] if isinstance(endpoint_types, list) else []
        if any("video" in value for value in values):
            return "video"
        if any("image" in value for value in values):
            return "image"
        if values:
            return "chat"
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


def _reference_values(refs):
    return [_reference_value(ref) for ref in refs or [] if _reference_value(ref)]


def _prompt(payload, max_length=0):
    value = str(getattr(payload, "prompt", "") or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="Pidoi 视频提示词不能为空。")
    if max_length and len(value) > max_length:
        raise HTTPException(status_code=400, detail=f"Pidoi 视频提示词最多 {max_length} 个字符。")
    return value


def _aspect_ratio(payload, allowed, required=True):
    value = str(getattr(payload, "aspect_ratio", "") or "").strip()
    if not value and not required:
        return ""
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise HTTPException(status_code=400, detail=f"Pidoi 视频比例无效：{value or '(empty)'}；可选值：{choices}")
    return value


def _duration(payload):
    try:
        value = int(getattr(payload, "duration", 0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Pidoi 视频时长必须是正整数。")
    if value <= 0:
        raise HTTPException(status_code=400, detail="Pidoi 视频时长必须是正整数。")
    return value


def _fixed_resolution(payload, expected):
    value = str(getattr(payload, "resolution", "") or "").strip()
    if value and value.lower() != expected.lower():
        raise HTTPException(
            status_code=400,
            detail=f"Pidoi 当前文档只支持 {expected} 清晰度，收到：{value}。",
        )


def _pixel_size(payload):
    value = str(getattr(payload, "size", "") or "").strip()
    if not value:
        return ""
    match = re.fullmatch(r"(\d+)\s*[xX]\s*(\d+)", value)
    if not match or int(match.group(1)) <= 0 or int(match.group(2)) <= 0:
        raise HTTPException(
            status_code=400,
            detail="Pidoi tejiasd 的 size 必须是正整数像素尺寸，例如 1280x720。",
        )
    return f"{int(match.group(1))}x{int(match.group(2))}"


def _seed(payload):
    value = getattr(payload, "seed", None)
    if value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Pidoi tejiasd 的 seed 必须是非负整数。")
    if value < 0:
        raise HTTPException(status_code=400, detail="Pidoi tejiasd 的 seed 必须是非负整数。")
    return value


def _reject_unsupported_canvas_modes(payload, images, allow_size=False, allow_seed=False):
    frame_roles = {"first", "last", "first_frame", "last_frame", "start_frame", "end_frame"}
    if any(_reference_role(ref) in frame_roles for ref in images or []):
        raise HTTPException(
            status_code=400,
            detail="Pidoi 文档未提供首帧/尾帧字段，带首尾帧标记的素材不能按普通参考图发送。",
        )
    if bool(getattr(payload, "return_last_frame", False)):
        raise HTTPException(status_code=400, detail="Pidoi 文档未提供 return_last_frame 参数。")
    if bool(getattr(payload, "generate_audio", False)):
        raise HTTPException(
            status_code=400,
            detail="Pidoi 文档未提供 generate_audio 参数；请关闭本地音频开关，或改用参考音频数组。",
        )
    for field in ("enhance_prompt", "enable_upsample", "watermark", "camerafixed"):
        if bool(getattr(payload, field, False)):
            raise HTTPException(status_code=400, detail=f"Pidoi 文档未提供 {field} 参数。")
    if bool(getattr(payload, "trusted_asset", False)):
        raise HTTPException(status_code=400, detail="Pidoi 文档未提供 trusted_asset 参数。")
    if getattr(payload, "compliance_enabled", None) is True or str(getattr(payload, "compliance_mode", "") or "").strip():
        raise HTTPException(status_code=400, detail="Pidoi 文档未提供真人合规参数。")
    if not allow_size and str(getattr(payload, "size", "") or "").strip():
        raise HTTPException(status_code=400, detail="Pidoi 当前模型文档不支持 size 参数。")
    if not allow_seed and getattr(payload, "seed", None) is not None:
        raise HTTPException(status_code=400, detail="Pidoi 当前模型文档不支持 seed 参数。")


async def _resolve_reference(resolve_ref, value, kind, index):
    resolved = await resolve_ref(value, kind, index) if resolve_ref else value
    text = str(resolved or "").strip()
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail=f"Pidoi 第 {index} 个{kind}参考素材没有得到有效的公网 http(s) URL。",
        )
    return text


async def _resolve_references(refs, kind, limit, resolve_ref):
    values = _reference_values(refs)
    if len(values) > limit:
        raise HTTPException(
            status_code=400,
            detail=f"Pidoi {kind}参考素材最多 {limit} 个，当前为 {len(values)} 个；不会静默截断。",
        )
    return [
        await _resolve_reference(resolve_ref, value, kind, index)
        for index, value in enumerate(values, 1)
    ]


async def _build_omni_flash_request(payload, model, resolve_ref):
    duration = _duration(payload)
    if duration not in PIDOI_OMNI_DURATIONS:
        raise HTTPException(status_code=400, detail="omni-flash-720p 只支持 5 秒或 10 秒。")
    images = getattr(payload, "images", []) or []
    videos = getattr(payload, "videos", []) or []
    audios = getattr(payload, "audios", []) or []
    _reject_unsupported_canvas_modes(payload, images)
    _fixed_resolution(payload, "720P")
    image_urls = await _resolve_references(images, "图片", PIDOI_OMNI_MAX_IMAGES, resolve_ref)
    video_urls = await _resolve_references(videos, "视频", PIDOI_OMNI_MAX_VIDEOS, resolve_ref)
    audio_urls = await _resolve_references(audios, "音频", PIDOI_OMNI_MAX_AUDIOS, resolve_ref)
    body = {
        "model": model,
        "prompt": _prompt(payload),
        "duration": duration,
        "resolution": "720P",
        "metadata": {"aspect_ratio": _aspect_ratio(payload, PIDOI_OMNI_ASPECT_RATIOS)},
    }
    if image_urls:
        body["images"] = image_urls
    if video_urls:
        body["videos"] = video_urls
    if audio_urls:
        body["audios"] = audio_urls
    return body


async def _build_sora_933_request(payload, model, resolve_ref):
    duration = _duration(payload)
    if duration not in PIDOI_SORA_DURATIONS:
        raise HTTPException(status_code=400, detail="sora-v3-933-pro 文档示例/参数表只确认支持 5 秒或 15 秒。")
    images = getattr(payload, "images", []) or []
    videos = getattr(payload, "videos", []) or []
    audios = getattr(payload, "audios", []) or []
    _reject_unsupported_canvas_modes(payload, images)
    _fixed_resolution(payload, "720p")
    image_urls = await _resolve_references(images, "图片", PIDOI_SORA_MAX_IMAGES, resolve_ref)
    video_urls = await _resolve_references(videos, "视频", PIDOI_SORA_MAX_VIDEOS, resolve_ref)
    audio_urls = await _resolve_references(audios, "音频", PIDOI_SORA_MAX_AUDIOS, resolve_ref)
    if len(image_urls) + len(video_urls) + len(audio_urls) > 12:
        raise HTTPException(status_code=400, detail="sora-v3-933-pro 单次素材总数最多 12 个；不会静默截断。")
    body = {
        "model": model,
        "prompt": _prompt(payload),
        "aspect_ratio": _aspect_ratio(payload, PIDOI_SORA_ASPECT_RATIOS),
        "resolution": "720p",
        "seconds": str(duration),
    }
    if image_urls:
        body["image_url"] = image_urls[0]
        if len(image_urls) > 1:
            body["reference_image_urls"] = image_urls[1:]
    if video_urls:
        body["reference_video" if len(video_urls) == 1 else "reference_videos"] = (
            video_urls[0] if len(video_urls) == 1 else video_urls
        )
    if audio_urls:
        body["audio_url" if len(audio_urls) == 1 else "audio_urls"] = (
            audio_urls[0] if len(audio_urls) == 1 else audio_urls
        )
    return body


async def _build_tejiasd_request(payload, model, resolve_ref):
    duration = _duration(payload)
    images = getattr(payload, "images", []) or []
    videos = getattr(payload, "videos", []) or []
    audios = getattr(payload, "audios", []) or []
    _reject_unsupported_canvas_modes(payload, images, allow_size=True, allow_seed=True)
    image_urls = await _resolve_references(images, "图片", PIDOI_TEJIASD_MAX_IMAGES, resolve_ref)
    video_urls = await _resolve_references(videos, "视频", PIDOI_TEJIASD_MAX_VIDEOS, resolve_ref)
    audio_urls = await _resolve_references(audios, "音频", PIDOI_TEJIASD_MAX_AUDIOS, resolve_ref)
    size = _pixel_size(payload)
    requested_resolution = str(getattr(payload, "resolution", "") or "").strip()
    if size and requested_resolution:
        raise HTTPException(status_code=400, detail="Pidoi tejiasd 的 size 与 resolution 不能同时发送。")
    if not size:
        _fixed_resolution(payload, "720P")
    seed = _seed(payload)
    body = {
        "model": model,
        "prompt": _prompt(payload, 2500),
        "duration": duration,
        "n": 1,
    }
    if size:
        body["size"] = size
    else:
        body["resolution"] = "720P"
        aspect_ratio = _aspect_ratio(payload, PIDOI_TEJIASD_ASPECT_RATIOS, required=False)
        if aspect_ratio:
            body["metadata"] = {"aspect_ratio": aspect_ratio}
    if seed is not None:
        body["seed"] = seed
    if image_urls:
        body["images"] = image_urls
    if video_urls:
        body["videos"] = video_urls
    if audio_urls:
        body["audios"] = audio_urls
    return body


async def build_pidoi_video_request(payload, requested_model, resolve_ref=None):
    model = str(requested_model or "").strip()
    family = pidoi_model_family(model)
    if not family:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Pidoi 模型「{model or '(empty)'}」已被上游声明为视频，但项目没有对应的请求体家族文档；"
                "为避免参考素材被静默忽略，暂不提交。"
            ),
        )
    if family == PIDOI_FAMILY_OMNI_FLASH_720P:
        return await _build_omni_flash_request(payload, model, resolve_ref)
    if family == PIDOI_FAMILY_SORA_V3_933_PRO:
        return await _build_sora_933_request(payload, model, resolve_ref)
    if family == PIDOI_FAMILY_TEJIASD:
        return await _build_tejiasd_request(payload, model, resolve_ref)
    raise HTTPException(status_code=400, detail=f"Pidoi 未实现请求体家族：{family}")


def pidoi_task_id(raw):
    if not isinstance(raw, dict):
        return ""
    for node in (
        raw,
        raw.get("data") if isinstance(raw.get("data"), dict) else None,
    ):
        if not isinstance(node, dict):
            continue
        for key in ("task_id", "id"):
            value = node.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
    return ""


def pidoi_task_state(raw):
    if not isinstance(raw, dict):
        return "pending", ""
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    status = str(raw.get("status") or data.get("status") or "").strip()
    upper = status.upper()
    if upper in PIDOI_TERMINAL_SUCCESS_STATUSES:
        return "success", status
    if upper in PIDOI_TERMINAL_FAILURE_STATUSES:
        return "failed", status
    return "pending", status


def _nested_value(raw, path):
    current = raw
    for key in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return current if isinstance(current, str) else ""


def pidoi_video_result_urls(raw, family=""):
    """按对应下游文档的优先级提取成片地址。"""
    if not isinstance(raw, dict):
        return []
    family = str(family or "").strip().lower()
    if family == PIDOI_FAMILY_TEJIASD:
        paths = [
            ("metadata", "url"),
            ("video_url",),
            ("url",),
            ("result_url",),
            ("data", "metadata", "url"),
            ("data", "video_url"),
            ("data", "url"),
            ("data", "result_url"),
            ("content",),
            ("data", "content"),
        ]
    elif family == PIDOI_FAMILY_SORA_V3_933_PRO:
        paths = [
            ("video_url",),
            ("data", "video_url"),
            ("url",),
            ("result_url",),
            ("data", "result_url"),
            ("data", "url"),
            ("content",),
            ("data", "content"),
        ]
    else:
        paths = [
            ("result_url",),
            ("data", "metadata", "url"),
            ("video_url",),
            ("data", "video_url"),
            ("data", "result_url"),
            ("data", "url"),
            ("url",),
            ("metadata", "url"),
            ("outputs", 0, "url"),
            ("outputs", 0, "download_url"),
            ("data", "outputs", 0, "url"),
            ("data", "outputs", 0, "download_url"),
            ("content",),
            ("data", "content"),
        ]
    urls = []
    for path in paths:
        current = raw
        for key in path:
            if isinstance(key, int):
                if not isinstance(current, list) or key >= len(current):
                    current = ""
                    break
                current = current[key]
            elif isinstance(current, dict):
                current = current.get(key)
            else:
                current = ""
                break
        if isinstance(current, str) and current.strip() and current.strip() not in urls:
            urls.append(current.strip())
    return urls


def pidoi_error_text(raw, fallback=""):
    if isinstance(raw, dict):
        error = raw.get("error")
        if isinstance(error, dict):
            if error.get("message"):
                return str(error["message"]).strip()[:500]
        for key in ("fail_reason", "message", "detail"):
            if raw.get(key):
                return str(raw[key]).strip()[:500]
        if isinstance(error, dict):
            for key in ("detail", "code"):
                if error.get(key):
                    return str(error[key]).strip()[:500]
        elif isinstance(error, str) and error.strip():
            return error.strip()[:500]
    return str(fallback or raw or "").strip()[:500]
