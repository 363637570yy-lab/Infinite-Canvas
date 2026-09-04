"""Codelba（codelba.cn）视频协议的纯逻辑。

站点合同是 POST /openapi/v1/videos → GET /openapi/v1/videos/{task_id}
→ GET /openapi/v1/videos/{task_id}/content。当前可用网关是
https://hz.codelba.cn；https://hz.codelba.cn/ai_video_ui/ 只是网页后台。
全部视频模型共用同一套 OpenAPI v1 请求字段：resolution、
aspect_ratio 与 image_refs / video_refs / audio_refs。模型改名不是新家族。
时长、清晰度、画幅、参考上限以 GET /openapi/v1/models 的能力字段为准；
本地家族表只给目录不可用时的旧 ID 兜底。没有能力字段的模型
不会按其它版本参数提交。平台把公共 resolution / aspect_ratio 转成
厂商参数；调用方不要发 1280x720 这类内部宽高，也不要同时发 size。
size 只作为入站旧字段，用来还原画幅或清晰度。本模块不反向导入 main.py。
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

# size 只用于把旧调用的宽高还原成公共 aspect_ratio，不再写入请求体。
CODELBA_SIZE_BY_RATIO = {
    "16:9": "1280x720",
    "9:16": "720x1280",
    "4:3": "960x720",
    "3:4": "720x960",
    "1:1": "720x720",
    "21:9": "1680x720",
}
CODELBA_RATIO_BY_SIZE = {size: ratio for ratio, size in CODELBA_SIZE_BY_RATIO.items()}

CODELBA_FAMILY_SPECS = {
    CODELBA_FAMILY_SD_2_C5: {
        "durations": frozenset({5, 8, 10, 15}),
        "duration_order": (5, 8, 10, 15),
        "sizes": frozenset({"1280x720", "720x1280", "960x720", "720x960"}),
        "ratios": frozenset({"16:9", "9:16", "4:3", "3:4"}),
        "ratio_order": ("16:9", "9:16", "4:3", "3:4"),
        "default_ratio": "16:9",
        "default_resolution": "720p",
        "resolution_originals": ("720p",),
        "resolutions": frozenset({"720p", "720"}),
        "max_images": 9,
        "max_videos": 3,
        "max_audios": 3,
        "allow_video_refs": True,
        "allow_audio_refs": True,
        "duration_mode": "enum",
        "compliance_supported": False,
    },
    CODELBA_FAMILY_SD_2_C5_10: {
        "durations": frozenset({5, 8, 10}),
        "duration_order": (5, 8, 10),
        "sizes": frozenset({"1280x720", "720x1280", "720x720"}),
        "ratios": frozenset({"16:9", "9:16", "1:1"}),
        "ratio_order": ("16:9", "9:16", "1:1"),
        "default_ratio": "16:9",
        "default_resolution": "720p",
        "resolution_originals": ("720p",),
        "resolutions": frozenset({"720p", "720"}),
        "max_images": 9,
        "max_videos": 3,
        "max_audios": 3,
        "allow_video_refs": True,
        "allow_audio_refs": True,
        "duration_mode": "enum",
        "compliance_supported": False,
    },
    CODELBA_FAMILY_SEEDANCE_2_14S: {
        "durations": frozenset(range(5, 16)),
        "duration_order": tuple(range(5, 16)),
        "sizes": frozenset({"1280x720", "720x1280"}),
        "ratios": frozenset({"16:9", "9:16"}),
        "ratio_order": ("16:9", "9:16"),
        "default_ratio": "16:9",
        "default_resolution": "720p",
        "resolution_originals": ("720p",),
        "resolutions": frozenset({"720p", "720"}),
        "max_images": 9,
        "max_videos": 0,
        "max_audios": 0,
        "allow_video_refs": False,
        "allow_audio_refs": False,
        "duration_mode": "range",
        "min_duration": 5,
        "max_duration": 15,
        "compliance_supported": False,
    },
}

CODELBA_DEFAULT_DURATION = 5
CODELBA_PROMPT_MAX_LENGTH = 32000
CODELBA_PIXEL_SIZE_RE = re.compile(r"^(\d+)\s*[xX]\s*(\d+)$")
CODELBA_DEFAULT_RESOLUTIONS = frozenset({"720p", "720"})

CODELBA_TERMINAL_SUCCESS_STATUSES = {"completed"}
CODELBA_TERMINAL_FAILURE_STATUSES = {"failed"}


def _int_list(value):
    values = []
    if not isinstance(value, list):
        return values
    for item in value:
        try:
            values.append(int(item))
        except (TypeError, ValueError):
            continue
    return values


def _str_list(value):
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _optional_nonneg_int(entry, key):
    if not isinstance(entry, dict) or key not in entry:
        return None
    try:
        return max(0, int(entry.get(key)))
    except (TypeError, ValueError):
        return None


def _normalize_resolution_token(value):
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text in {"4k", "2160", "2160p"}:
        return "4k"
    if text.endswith("p") and text[:-1].isdigit():
        return text
    if text.isdigit():
        return f"{text}p"
    return text


def _catalog_resolution_originals(entry):
    values = []
    seen = set()
    for item in _str_list((entry or {}).get("resolutions")):
        original = str(item).strip()
        token = _normalize_resolution_token(original)
        if not token or token in seen:
            continue
        seen.add(token)
        values.append(original)
    return tuple(values)


def _catalog_resolutions(entry):
    return frozenset(_normalize_resolution_token(item) for item in _catalog_resolution_originals(entry))


def has_codelba_video_capability_schema(item):
    """OpenAPI v1 模型条目用时长/画幅/清晰度/参考上限声明视频能力，不看模型名。"""
    if not isinstance(item, dict):
        return False
    durations = _int_list(item.get("durations"))
    ratios = _str_list(item.get("aspect_ratios"))
    resolutions = _catalog_resolution_originals(item)
    has_ref_caps = any(key in item for key in ("max_image_refs", "max_video_refs", "max_audio_refs"))
    has_compliance = "compliance_supported" in item
    return bool(durations) and (bool(ratios) or bool(resolutions) or has_ref_caps or has_compliance)


def _legacy_family_spec(model):
    family = CODELBA_VIDEO_MODEL_FAMILIES.get(str(model or "").strip().lower(), "")
    if not family:
        return None
    return dict(CODELBA_FAMILY_SPECS[family])


class CodelbaCatalog:
    """GET /openapi/v1/models 的解析结果。

    目录只提供每个模型的时长、画幅、清晰度和参考上限。
    请求体字段本身是全站同一套 OpenAPI v1，不随模型改名变化。
    """

    def __init__(self, models=None):
        self._by_id = {}
        for item in models or []:
            if isinstance(item, dict) and item.get("id"):
                self._by_id[str(item["id"]).strip().lower()] = item

    def __len__(self):
        return len(self._by_id)

    def entry(self, model):
        return self._by_id.get(str(model or "").strip().lower()) or {}

    def knows(self, model):
        return bool(self.entry(model))

    def spec_for(self, model):
        entry = self.entry(model)
        if not entry:
            return None
        durations = _int_list(entry.get("durations"))
        ratios = _str_list(entry.get("aspect_ratios"))
        resolution_originals = _catalog_resolution_originals(entry)
        sizes = set()
        for item in _str_list(entry.get("sizes")):
            pixel = _normalize_pixel_size(item)
            if pixel:
                sizes.add(pixel)
        for ratio in ratios:
            mapped = CODELBA_SIZE_BY_RATIO.get(ratio)
            if mapped:
                sizes.add(mapped)
        if not durations:
            return None
        if not ratios and not resolution_originals and not sizes:
            return None
        if not ratios:
            ratios = [
                ratio for ratio, size in CODELBA_SIZE_BY_RATIO.items() if size in sizes
            ]
        max_images = _optional_nonneg_int(entry, "max_image_refs")
        max_videos = _optional_nonneg_int(entry, "max_video_refs")
        max_audios = _optional_nonneg_int(entry, "max_audio_refs")
        duration_set = frozenset(durations)
        low, high = min(durations), max(durations)
        is_range = duration_set == frozenset(range(low, high + 1)) and (high - low + 1) >= 4
        if not resolution_originals:
            resolution_originals = ("720p",)
        default_resolution = resolution_originals[0]
        for original in resolution_originals:
            if _normalize_resolution_token(original) in {"720p", "720"}:
                default_resolution = original
                break
        return {
            "durations": duration_set,
            "duration_order": tuple(durations),
            "sizes": frozenset(sizes),
            "ratios": frozenset(ratios),
            "ratio_order": tuple(ratios),
            "default_ratio": ratios[0] if ratios else "",
            "default_resolution": default_resolution,
            "resolution_originals": resolution_originals,
            "max_images": 0 if max_images is None else max_images,
            "max_videos": 0 if max_videos is None else max_videos,
            "max_audios": 0 if max_audios is None else max_audios,
            "allow_video_refs": (max_videos or 0) > 0,
            "allow_audio_refs": (max_audios or 0) > 0,
            "duration_mode": "range" if is_range else "enum",
            "min_duration": low,
            "max_duration": high,
            "resolutions": frozenset(
                _normalize_resolution_token(item) for item in resolution_originals
            ) or CODELBA_DEFAULT_RESOLUTIONS,
            "compliance_supported": bool(entry.get("compliance_supported")),
        }


def parse_codelba_catalog(raw):
    items = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(items, list) and isinstance(raw, dict):
        items = raw.get("models")
    if not isinstance(items, list):
        items = []
    return CodelbaCatalog(items)


def resolve_codelba_spec(model, catalog=None):
    if catalog is not None:
        spec = catalog.spec_for(model)
        if spec:
            return spec
    return _legacy_family_spec(model)


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
    if has_codelba_video_capability_schema(item):
        return "video"
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
    raw = getattr(payload, "duration", None)
    if raw in (None, ""):
        order = spec.get("duration_order") or tuple(sorted(spec.get("durations") or []))
        if order:
            return int(order[0])
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


def _raw_aspect_ratio(payload):
    for key in ("aspect_ratio", "aspectRatio", "ratio"):
        value = str(getattr(payload, key, "") or "").strip()
        if value:
            return value
    return ""


def _resolution_choices(spec):
    originals = spec.get("resolution_originals") or ()
    if originals:
        return "、".join(originals)
    allowed = spec.get("resolutions") or CODELBA_DEFAULT_RESOLUTIONS
    return "、".join(sorted(item for item in allowed if item)) or "720p"


def _match_resolution(value, spec):
    token = _normalize_resolution_token(value)
    if not token:
        return ""
    for original in spec.get("resolution_originals") or ():
        if _normalize_resolution_token(original) == token:
            return original
    allowed = spec.get("resolutions") or CODELBA_DEFAULT_RESOLUTIONS
    if token in allowed or str(value or "").strip() in allowed:
        if token.isdigit():
            return f"{token}p"
        return token
    return ""


def _size_as_resolution(raw_size, spec):
    if not raw_size or _normalize_pixel_size(raw_size):
        return ""
    if raw_size in spec.get("ratios", frozenset()) or raw_size in CODELBA_SIZE_BY_RATIO:
        return ""
    if _match_resolution(raw_size, spec) or _normalize_resolution_token(raw_size):
        return raw_size
    return ""


def _aspect_ratio(payload, spec):
    raw_ratio = _raw_aspect_ratio(payload)
    raw_size = str(getattr(payload, "size", "") or "").strip()
    pixel = _normalize_pixel_size(raw_size)
    ratio_from_pixel = CODELBA_RATIO_BY_SIZE.get(pixel, "")
    size_as_ratio = raw_size if raw_size in spec.get("ratios", frozenset()) or raw_size in CODELBA_SIZE_BY_RATIO else ""
    if pixel and raw_ratio and ratio_from_pixel and raw_ratio != ratio_from_pixel:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Codelba 同时传 size「{pixel}」和 aspect_ratio「{raw_ratio}」时必须一致，"
                f"该尺寸对应 {ratio_from_pixel}。"
            ),
        )
    ratio = raw_ratio or size_as_ratio or ratio_from_pixel
    if ratio in {"keep_ratio", "adaptive"}:
        raise HTTPException(
            status_code=400,
            detail="Codelba 需要明确画幅，不支持 keep_ratio / adaptive。",
        )
    allowed = spec.get("ratios") or frozenset()
    if ratio:
        if ratio not in allowed:
            choices = "、".join(spec.get("ratio_order") or sorted(allowed))
            raise HTTPException(
                status_code=400,
                detail=f"Codelba 该模型不支持画幅「{ratio}」；可选值：{choices}。",
            )
        return ratio
    default = spec.get("default_ratio") or ""
    if default:
        return default
    order = spec.get("ratio_order") or tuple(sorted(allowed))
    if order:
        return order[0]
    raise HTTPException(status_code=400, detail="Codelba 该模型没有可用的 aspect_ratio。")


def _resolution(payload, spec):
    raw_res = str(getattr(payload, "resolution", "") or "").strip()
    raw_size = str(getattr(payload, "size", "") or "").strip()
    pixel = _normalize_pixel_size(raw_size)
    size_as_res = _size_as_resolution(raw_size, spec)
    if pixel and raw_res:
        implied = "720p" if pixel in CODELBA_RATIO_BY_SIZE else ""
        if implied and _normalize_resolution_token(raw_res) not in {"720p", "720"}:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Codelba 同时传 size「{pixel}」和 resolution「{raw_res}」时必须一致，"
                    f"该尺寸对应 {implied}。"
                ),
            )
    if raw_res and size_as_res:
        if _normalize_resolution_token(raw_res) != _normalize_resolution_token(size_as_res):
            raise HTTPException(
                status_code=400,
                detail="Codelba 同时传 size 和 resolution 时必须一致。",
            )
    chosen = raw_res or size_as_res
    if not chosen:
        return spec.get("default_resolution") or ""
    matched = _match_resolution(chosen, spec)
    if not matched:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Codelba 该模型不支持清晰度「{chosen}」；"
                f"可选值：{_resolution_choices(spec)}。不会改写成邻近值。"
            ),
        )
    return matched


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
            detail="Codelba 没有 generate_audio 开关；有声参考请使用 audio_refs。",
        )
    if getattr(payload, "compliance_enabled", None) is True or str(getattr(payload, "compliance_mode", "") or "").strip():
        if not spec.get("compliance_supported"):
            raise HTTPException(
                status_code=400,
                detail="Codelba 该模型未开放 compliance_enabled / compliance_mode，请不要传 true。",
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


async def build_codelba_video_request(payload, requested_model, resolve_ref=None, catalog=None):
    model = str(requested_model or "").strip()
    spec = resolve_codelba_spec(model, catalog)
    if not spec:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Codelba 模型「{model or '(empty)'}」没有可用的能力字段，也没有本地家族文档。"
                "请先同步 /openapi/v1/models；未返回时长/画幅/参考上限的模型"
                "不会按其它版本参数提交。"
            ),
        )
    images = getattr(payload, "images", []) or []
    videos = getattr(payload, "videos", []) or []
    audios = getattr(payload, "audios", []) or []
    _reject_unsupported_modes(payload, images, spec)

    image_urls = await _resolve_references(images, "图片", spec["max_images"], resolve_ref)
    video_urls = await _resolve_references(videos, "视频", spec["max_videos"], resolve_ref)
    audio_urls = await _resolve_references(audios, "音频", spec["max_audios"], resolve_ref)

    body = {
        "model": model,
        "prompt": _prompt(payload),
        "duration": _duration(payload, spec),
    }
    resolution = _resolution(payload, spec)
    aspect_ratio = _aspect_ratio(payload, spec)
    if resolution:
        body["resolution"] = resolution
    if aspect_ratio:
        body["aspect_ratio"] = aspect_ratio
    if image_urls:
        body["image_refs"] = image_urls
    if video_urls:
        body["video_refs"] = video_urls
    if audio_urls:
        body["audio_refs"] = audio_urls
    if spec.get("compliance_supported") and getattr(payload, "compliance_enabled", None) is True:
        body["compliance_enabled"] = True
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


def codelba_error_code(raw):
    if not isinstance(raw, dict):
        return ""
    error = raw.get("error")
    if isinstance(error, dict):
        return str(error.get("code") or "").strip()
    return ""


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
