"""Codelba（codelba.cn）视频协议的纯逻辑。

站点合同是 POST /openapi/v1/videos → GET /openapi/v1/videos/{task_id}
→ GET /openapi/v1/videos/{task_id}/content。当前可用网关是
https://hz.codelba.cn；https://hz.codelba.cn/ai_video_ui/ 只是网页后台。
请求字段是 size 与 image_refs / video_refs / audio_refs。
文档示例 size 为 1280x720 这种宽x高；请求体按画幅发送对应像素尺寸，
16:9 默认 1280x720。720p 只当作清晰度别名，不会写进 size。
和 chre3、苍元、MegabyAI 都不同，所以单独成协议。本模块不反向导入 main.py。
"""

import re
import urllib.parse

from fastapi import HTTPException


CODELBA_PROTOCOL = "codelba"
CODELBA_DEFAULT_BASE_URL = "https://hz.codelba.cn"
CODELBA_LEGACY_HOSTS = {"codelba.cn", "www.codelba.cn"}
CODELBA_STRIP_PATH_SUFFIXES = (
    "/ai_video_ui",
    "/ai_video_server",
    "/openapi/v1",
    "/openapi/v2",
    "/openapi",
    "/v1",
    "/v2",
)

CODELBA_FAMILY_SD_2_C5 = "sd-2-c5"
CODELBA_FAMILY_SD_2_C5_10 = "sd-2-c5-10"
CODELBA_FAMILY_SEEDANCE_2_14S = "seedance2.0-14s"

CODELBA_VIDEO_MODEL_FAMILIES = {
    "sd-2-c5": CODELBA_FAMILY_SD_2_C5,
    "sd-2-c5-10": CODELBA_FAMILY_SD_2_C5_10,
    "seedance2.0-14s": CODELBA_FAMILY_SEEDANCE_2_14S,
}

# 文档「标准尺寸」表把 1:1 写成仅 sd-2-c5，但模型能力表把 1:1 记在 sd-2-c5-10，
# 且 sd-2-c5 的支持比例没有 1:1。发送时以模型能力表为准。
CODELBA_SIZE_BY_RATIO = {
    "16:9": "1280x720",
    "9:16": "720x1280",
    "4:3": "960x720",
    "3:4": "720x960",
    "1:1": "720x720",
}

CODELBA_FAMILY_SPECS = {
    CODELBA_FAMILY_SD_2_C5: {
        "durations": frozenset({5, 8, 10, 15}),
        "sizes": frozenset({"1280x720", "720x1280", "960x720", "720x960"}),
        "ratios": frozenset({"16:9", "9:16", "4:3", "3:4"}),
        "default_size": "1280x720",
        "max_images": 9,
        "max_videos": 3,
        "max_audios": 3,
        "allow_video_refs": True,
        "allow_audio_refs": True,
        "duration_mode": "enum",
    },
    CODELBA_FAMILY_SD_2_C5_10: {
        "durations": frozenset({5, 8, 10}),
        "sizes": frozenset({"1280x720", "720x1280", "720x720"}),
        "ratios": frozenset({"16:9", "9:16", "1:1"}),
        "default_size": "1280x720",
        "max_images": 9,
        "max_videos": 3,
        "max_audios": 3,
        "allow_video_refs": True,
        "allow_audio_refs": True,
        "duration_mode": "enum",
    },
    CODELBA_FAMILY_SEEDANCE_2_14S: {
        "durations": frozenset(range(5, 16)),
        "sizes": frozenset({"1280x720", "720x1280"}),
        "ratios": frozenset({"16:9", "9:16"}),
        "default_size": "1280x720",
        "max_images": 9,
        "max_videos": 0,
        "max_audios": 0,
        "allow_video_refs": False,
        "allow_audio_refs": False,
        "duration_mode": "range",
        "min_duration": 5,
        "max_duration": 15,
    },
}

CODELBA_DEFAULT_DURATION = 5
CODELBA_PROMPT_MAX_LENGTH = 32000
CODELBA_PIXEL_SIZE_RE = re.compile(r"^(\d+)\s*[xX]\s*(\d+)$")
CODELBA_TIER_SIZE_ALIASES = frozenset({"720p", "720"})

CODELBA_TERMINAL_SUCCESS_STATUSES = {"completed"}
CODELBA_TERMINAL_FAILURE_STATUSES = {"failed"}


def codelba_api_root(base_url=""):
    value = str(base_url or CODELBA_DEFAULT_BASE_URL).strip()
    if not value:
        value = CODELBA_DEFAULT_BASE_URL
    parsed = urllib.parse.urlparse(value if "://" in value else f"https://{value}")
    path = (parsed.path or "").rstrip("/")
    changed = True
    while changed and path:
        changed = False
        lowered = path.lower()
        for suffix in CODELBA_STRIP_PATH_SUFFIXES:
            if lowered == suffix or lowered.endswith(suffix):
                path = path[: -len(suffix)].rstrip("/")
                changed = True
                break
    host = str(parsed.hostname or "").strip().lower()
    if host in CODELBA_LEGACY_HOSTS:
        host = urllib.parse.urlparse(CODELBA_DEFAULT_BASE_URL).hostname
    if not host:
        return CODELBA_DEFAULT_BASE_URL.rstrip("/")
    scheme = parsed.scheme or "https"
    netloc = host
    if parsed.port and parsed.port not in (80, 443):
        netloc = f"{host}:{parsed.port}"
    root = f"{scheme}://{netloc}"
    if path:
        root = f"{root}{path if path.startswith('/') else '/' + path}"
    return root


def codelba_models_url(base_url=""):
    return f"{codelba_api_root(base_url)}/openapi/v1/models"


def codelba_video_submit_url(base_url=""):
    return f"{codelba_api_root(base_url)}/openapi/v1/videos"


def codelba_video_task_url(base_url, task_id):
    quoted = urllib.parse.quote(str(task_id or ""), safe="")
    return f"{codelba_api_root(base_url)}/openapi/v1/videos/{quoted}"


def codelba_video_content_url(base_url, task_id):
    quoted = urllib.parse.quote(str(task_id or ""), safe="")
    return f"{codelba_api_root(base_url)}/openapi/v1/videos/{quoted}/content"


def codelba_model_family(model):
    return CODELBA_VIDEO_MODEL_FAMILIES.get(str(model or "").strip().lower(), "")


def classify_codelba_model_entry(item, model_id=""):
    """按上游能力字段分类；缺少能力字段时进 unknown，不按模型名猜测。"""
    del model_id
    values = []
    if isinstance(item, dict):
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
            elif isinstance(value, str) and value.strip():
                values.append(value.strip().lower())
        for key in ("type", "model_type", "modelType", "capability", "endpoint_type", "endpointType", "kind", "category"):
            value = item.get(key)
            if isinstance(value, dict):
                values.extend(str(name).strip().lower() for name, enabled in value.items() if enabled)
            elif isinstance(value, list):
                values.extend(str(part).strip().lower() for part in value)
            elif isinstance(value, (str, int, float)) and str(value).strip():
                values.append(str(value).strip().lower())
        owned_by = str(item.get("owned_by") or "").strip().lower()
        description = str(item.get("description") or "").strip().lower()
        if owned_by == "video-api" or "/videos" in description:
            values.append("video")
    if any("video" in value or value in {"t2v", "i2v", "s2v"} for value in values):
        return "video"
    if any("image" in value for value in values):
        return "image"
    if values:
        return "chat"
    return "unknown"


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
        raise HTTPException(status_code=400, detail="Codelba 视频提示词不能为空。")
    if len(value) > CODELBA_PROMPT_MAX_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Codelba 视频提示词最多 {CODELBA_PROMPT_MAX_LENGTH} 个字符。",
        )
    return value


def _duration(payload, spec):
    raw = getattr(payload, "duration", CODELBA_DEFAULT_DURATION)
    if raw in (None, ""):
        raw = CODELBA_DEFAULT_DURATION
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Codelba 视频时长必须是整数。")
    allowed = spec["durations"]
    if value not in allowed:
        if spec.get("duration_mode") == "range":
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Codelba 该模型时长必须是 {spec['min_duration']}-{spec['max_duration']} "
                    f"秒的整数，当前为 {value} 秒。"
                ),
            )
        choices = "、".join(str(item) for item in sorted(allowed))
        raise HTTPException(
            status_code=400,
            detail=f"Codelba 该模型只支持时长 {choices} 秒，当前为 {value} 秒；不会改成邻近值。",
        )
    return value


def _normalize_pixel_size(value):
    match = CODELBA_PIXEL_SIZE_RE.fullmatch(str(value or "").strip())
    if not match:
        return ""
    return f"{int(match.group(1))}x{int(match.group(2))}"


def _is_tier_size_alias(value):
    return str(value or "").strip().lower() in CODELBA_TIER_SIZE_ALIASES


def _size(payload, spec):
    raw_size = str(getattr(payload, "size", "") or "").strip()
    raw_ratio = str(getattr(payload, "aspect_ratio", "") or "").strip()
    pixel = _normalize_pixel_size(raw_size)
    if pixel:
        if pixel not in spec["sizes"]:
            choices = "、".join(sorted(spec["sizes"]))
            raise HTTPException(
                status_code=400,
                detail=f"Codelba 该模型不支持尺寸「{pixel}」；可选值：{choices}。",
            )
        return pixel
    ratio = raw_size if raw_size in CODELBA_SIZE_BY_RATIO else raw_ratio
    if _is_tier_size_alias(raw_size):
        ratio = raw_ratio
    if ratio in {"keep_ratio", "adaptive"}:
        raise HTTPException(
            status_code=400,
            detail="Codelba 需要明确画幅，不支持 keep_ratio / adaptive。",
        )
    if ratio:
        mapped = CODELBA_SIZE_BY_RATIO.get(ratio)
        if not mapped or mapped not in spec["sizes"] or ratio not in spec["ratios"]:
            choices = "、".join(sorted(spec["ratios"]))
            raise HTTPException(
                status_code=400,
                detail=f"Codelba 该模型不支持画幅「{ratio}」；可选值：{choices}。",
            )
        return mapped
    return spec["default_size"]


def _reject_resolution(payload):
    value = str(getattr(payload, "resolution", "") or "").strip().lower()
    if not value or value in {"720p", "720", "720P".lower()}:
        return
    raise HTTPException(
        status_code=400,
        detail="Codelba 当前模型只输出 720P，不会把 1080p/4K 等清晰度改写成 720P 后静默提交。",
    )


def _reject_unsupported_modes(payload, images, spec):
    frame_roles = {"first", "last", "first_frame", "last_frame", "start_frame", "end_frame"}
    if any(_reference_role(ref) in frame_roles for ref in images or []):
        raise HTTPException(
            status_code=400,
            detail="Codelba 文档没有首帧/尾帧字段；请移除首尾帧标记并使用普通参考图。",
        )
    if bool(getattr(payload, "generate_audio", False)):
        raise HTTPException(
            status_code=400,
            detail="Codelba 没有 generate_audio 开关；有声参考请使用 audio_refs，且必须同时带图片或视频。",
        )
    if getattr(payload, "compliance_enabled", None) is True or str(getattr(payload, "compliance_mode", "") or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Codelba 当前模型未开放 compliance_enabled / compliance_mode，请不要传 true。",
        )
    for field in ("enhance_prompt", "enable_upsample", "watermark", "camerafixed", "return_last_frame"):
        if bool(getattr(payload, field, False)):
            raise HTTPException(status_code=400, detail=f"Codelba 文档未提供 {field} 参数。")
    if getattr(payload, "seed", None) is not None:
        raise HTTPException(status_code=400, detail="Codelba 文档未提供 seed 参数。")
    if not spec["allow_video_refs"] and any(_reference_value(ref) for ref in getattr(payload, "videos", []) or []):
        raise HTTPException(
            status_code=400,
            detail="Codelba 该模型不支持参考视频；请清空视频参考后再提交。",
        )
    if not spec["allow_audio_refs"] and any(_reference_value(ref) for ref in getattr(payload, "audios", []) or []):
        raise HTTPException(
            status_code=400,
            detail="Codelba 该模型不支持参考音频；请清空音频参考后再提交。",
        )


async def _resolve_reference(resolve_ref, value, kind, index):
    resolved = await resolve_ref(value, kind, index) if resolve_ref else value
    text = str(resolved or "").strip()
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail=f"Codelba 第 {index} 个{kind}参考素材没有得到有效的公网 http(s) URL。",
        )
    return text


async def _resolve_references(refs, kind, limit, resolve_ref):
    values = [_reference_value(ref) for ref in refs or [] if _reference_value(ref)]
    if limit <= 0 and values:
        raise HTTPException(
            status_code=400,
            detail=f"Codelba 该模型不支持{kind}参考素材。",
        )
    if len(values) > limit:
        raise HTTPException(
            status_code=400,
            detail=f"Codelba {kind}参考素材最多 {limit} 个，当前为 {len(values)} 个；不会静默截断。",
        )
    return [
        await _resolve_reference(resolve_ref, value, kind, index)
        for index, value in enumerate(values, 1)
    ]


async def build_codelba_video_request(payload, requested_model, resolve_ref=None):
    model = str(requested_model or "").strip()
    family = codelba_model_family(model)
    if not family:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Codelba 模型「{model or '(empty)'}」没有对应的请求体家族文档；"
                "已实现 sd-2-c5、sd-2-c5-10、seedance2.0-14s。"
                "未登记模型不会按其它家族字段静默提交。"
            ),
        )
    spec = CODELBA_FAMILY_SPECS[family]
    images = getattr(payload, "images", []) or []
    videos = getattr(payload, "videos", []) or []
    audios = getattr(payload, "audios", []) or []
    _reject_resolution(payload)
    _reject_unsupported_modes(payload, images, spec)

    image_urls = await _resolve_references(images, "图片", spec["max_images"], resolve_ref)
    video_urls = await _resolve_references(videos, "视频", spec["max_videos"], resolve_ref)
    audio_urls = await _resolve_references(audios, "音频", spec["max_audios"], resolve_ref)
    if audio_urls and not (image_urls or video_urls):
        raise HTTPException(
            status_code=400,
            detail="Codelba 传音频时必须同时传图片或视频参考，不能只传 audio_refs。",
        )

    body = {
        "model": model,
        "prompt": _prompt(payload),
        "duration": _duration(payload, spec),
        "size": _size(payload, spec),
    }
    if image_urls:
        body["image_refs"] = image_urls
    if video_urls:
        body["video_refs"] = video_urls
    if audio_urls:
        body["audio_refs"] = audio_urls
    return body


def codelba_task_id(raw):
    if not isinstance(raw, dict):
        return ""
    nodes = [raw]
    if isinstance(raw.get("data"), dict):
        nodes.append(raw["data"])
    for node in nodes:
        # 文档要求完整保存 id（含 video- 前缀），优先读 id。
        for key in ("id", "task_id"):
            value = node.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
    return ""


def codelba_task_state(raw):
    if not isinstance(raw, dict):
        return "pending", ""
    node = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    status = str(node.get("status") or raw.get("status") or "").strip().lower()
    if status in CODELBA_TERMINAL_SUCCESS_STATUSES:
        return "success", status
    if status in CODELBA_TERMINAL_FAILURE_STATUSES:
        return "failed", status
    return "pending", status


def _string_at_path(raw, path):
    current = raw
    for key in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return current.strip() if isinstance(current, str) else ""


def codelba_video_result_urls(raw):
    if not isinstance(raw, dict):
        return []
    paths = (
        ("video_url",),
        ("url",),
        ("metadata", "content_url"),
        ("metadata", "url"),
        ("data", "video_url"),
        ("data", "url"),
        ("data", "metadata", "content_url"),
        ("data", "metadata", "url"),
    )
    urls = []
    for path in paths:
        value = _string_at_path(raw, path)
        if value and value not in urls:
            urls.append(value)
    return urls


def codelba_error_text(raw, fallback=""):
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
