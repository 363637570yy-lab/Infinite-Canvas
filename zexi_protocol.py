# -*- coding: utf-8 -*-
"""泽西同学（zexitongxue.com）中转站协议。

本模块只放协议纯逻辑：能力目录解析、家族判定、请求体构造、响应归一化。
提交 / 轮询 / 路由留在 main.py，模块本身不 import main.py，便于单测直接加载。

站点合同（均由只读探测确认，见 tests/test_zexi_protocol.py）：

  提交视频  POST {root}/v1/videos                       Bearer
  查询视频  GET  {root}/v1/videos/{task_id}             Bearer
  下载视频  GET  {root}/v1/videos/{task_id}/content     Bearer
  提交图片  POST {root}/v1/images/generations/async     Bearer
  查询图片  GET  {root}/v1/images/tasks/{task_id}       Bearer
  下载图片  GET  {root}/v1/images/tasks/{id}/content?index=N   Bearer
  上传素材  POST {root}/v1/images/upload  (multipart)   Bearer
  能力目录  GET  {root}/ai-api/models?type=video|image  **禁止带 Authorization**

最后一条是本站最反直觉的一点：能力目录带 Bearer 会被打成 HTTP 400
"Client credential header is forbidden: authorization"，只有匿名请求才返回
can_use / duration_profile / resolution_profile / max_reference_images。
所以 catalog 通道绝对不能复用 main.py 的 api_headers()。
"""

import asyncio
import math
import re
import time
import urllib.parse

from fastapi import HTTPException

ZEXI_PROTOCOL = "zexi"
ZEXI_DEFAULT_BASE_URL = "https://zexitongxue.com"

# 站点自己的校验层用 {code, message, data} 封套，且**参数错误也可能返回 HTTP 500**
# （实测 grok duration=16 → HTTP 500 + code=build_request_failed）。因此终态判定
# 一律读 code / status 字段，不按 HTTP 状态码分支。
ZEXI_PARAM_ERROR_CODES = {
    "invalid_request",
    "build_request_failed",
    "invalid_parameter",
    "param_error",
}

ZEXI_TERMINAL_FAILURE_STATUSES = {"FAILED", "FAILURE", "FAIL", "ERROR", "CANCELED", "CANCELLED", "TIMEOUT", "EXPIRED", "REJECTED"}
ZEXI_TERMINAL_SUCCESS_STATUSES = {"SUCCESS", "SUCCEEDED", "COMPLETED", "COMPLETE", "DONE", "FINISHED"}
ZEXI_PENDING_STATUSES = {"QUEUED", "PENDING", "RUNNING", "PROCESSING", "IN_PROGRESS", "SUBMITTED"}

# ---------------------------------------------------------------- 请求体家族

# 同站不同模型分属不同请求体家族，字段互不通用。家族无法从能力目录读出——目录只给
# 上限（时长/分辨率/参考图数），不给字段名——所以家族按模型 id 显式登记。
# 这不是"按名称兜底猜测"：登记表里的每个 id 都对应站点文档里一份独立的字段说明。
ZEXI_FAMILY_SEEDANCE_25 = "seedance-2.5"
ZEXI_FAMILY_GROK = "grok"
ZEXI_FAMILY_MINIMAX_H3 = "minimax-h3"
ZEXI_FAMILY_SEEDANCE_FLAT = "seedance-flat"

ZEXI_VIDEO_MODEL_FAMILIES = {
    "seedance-2.5": ZEXI_FAMILY_SEEDANCE_25,
    "grok": ZEXI_FAMILY_GROK,
    "minimax-h3": ZEXI_FAMILY_MINIMAX_H3,
    # 以下均走站点通用 seedance 扁平字段（/docs/video-api.html 的兼容字段表）
    "seedance-standard-720p": ZEXI_FAMILY_SEEDANCE_FLAT,
    "seedance-2.0-480p-pro": ZEXI_FAMILY_SEEDANCE_FLAT,
    "seedance-2.0-480p-pro2": ZEXI_FAMILY_SEEDANCE_FLAT,
    "seedance-2.0-720p-pro": ZEXI_FAMILY_SEEDANCE_FLAT,
    "seedance-2.0-720p-pro-431": ZEXI_FAMILY_SEEDANCE_FLAT,
    "seedance-2.0-720-pro-enhance": ZEXI_FAMILY_SEEDANCE_FLAT,
    "seedance-fast-2.0-480p-pro": ZEXI_FAMILY_SEEDANCE_FLAT,
    "seedance-fast-2.0-720p-pro": ZEXI_FAMILY_SEEDANCE_FLAT,
    "seedance-fast-2.0-720p-pro-431": ZEXI_FAMILY_SEEDANCE_FLAT,
    "doubao-seedance-2-0-480p": ZEXI_FAMILY_SEEDANCE_FLAT,
    "doubao-seedance-2-0-720p": ZEXI_FAMILY_SEEDANCE_FLAT,
    "doubao-seedance-2-0-1080p": ZEXI_FAMILY_SEEDANCE_FLAT,
    "dolo": ZEXI_FAMILY_SEEDANCE_FLAT,
    "dolo-2": ZEXI_FAMILY_SEEDANCE_FLAT,
}

# 家族的素材能力。image 上限会被能力目录的 max_reference_images 覆盖；
# video / audio 上限目录不返回，只能取站点文档值，属于未经真实出片验证的边界。
ZEXI_FAMILY_LIMITS = {
    ZEXI_FAMILY_SEEDANCE_25: {"images": 30, "videos": 10, "audios": 10, "total": 50, "frames": False},
    ZEXI_FAMILY_GROK: {"images": 9, "videos": 0, "audios": 0, "total": 0, "frames": False},
    ZEXI_FAMILY_MINIMAX_H3: {"images": 9, "videos": 0, "audios": 3, "total": 0, "frames": False},
    ZEXI_FAMILY_SEEDANCE_FLAT: {"images": 9, "videos": 3, "audios": 5, "total": 0, "frames": True},
}

ZEXI_FAMILY_LABELS = {
    ZEXI_FAMILY_SEEDANCE_25: "Seedance 2.5",
    ZEXI_FAMILY_GROK: "Grok 视频",
    ZEXI_FAMILY_MINIMAX_H3: "MiniMax H3",
    ZEXI_FAMILY_SEEDANCE_FLAT: "Seedance 通用",
}

ZEXI_VIDEO_ASPECT_RATIOS = {"16:9", "9:16", "1:1"}
# seedance 扁平家族的站点文档示例里出现过 4:3 / 3:4 / 21:9，2.5 与 grok 明确只有三种。
ZEXI_FLAT_ASPECT_RATIOS = {"16:9", "9:16", "1:1", "4:3", "3:4", "21:9"}

# ---------------------------------------------------------------- 图片家族

ZEXI_IMAGE_STYLE_PIXEL = "pixel"      # size 传 1024x1024 这类像素串
ZEXI_IMAGE_STYLE_RATIO = "ratio"      # size 传 16:9 这类比例串

ZEXI_IMAGE_MODEL_STYLES = {
    "gpt-image-2": ZEXI_IMAGE_STYLE_PIXEL,
    "gemini-3-pro-image-preview": ZEXI_IMAGE_STYLE_RATIO,
    "gemini-3.1-flash-image-preview": ZEXI_IMAGE_STYLE_RATIO,
    "grok-imagine-image": ZEXI_IMAGE_STYLE_RATIO,
    "grok-imagine-image-pro": ZEXI_IMAGE_STYLE_RATIO,
    "grok-imagine-image-lite": ZEXI_IMAGE_STYLE_RATIO,
    "grok-imagine-image-edit": ZEXI_IMAGE_STYLE_RATIO,
    "grok-imagine-image-quality": ZEXI_IMAGE_STYLE_RATIO,
}

# gemini 系用 image_config 走 1K/2K/4K；gpt-image-2 用 OpenAI 的 low/medium/high/auto。
ZEXI_GEMINI_IMAGE_MODELS = {"gemini-3-pro-image-preview", "gemini-3.1-flash-image-preview"}
ZEXI_GEMINI_QUALITIES = {"1K", "2K", "4K"}
ZEXI_GPT_IMAGE_QUALITIES = {"low", "medium", "high", "auto"}
ZEXI_GPT_IMAGE_SIZES = {
    "1024x1024", "1536x1024", "1024x1536",
    "2048x1152", "3840x2160", "2160x3840", "auto",
}
ZEXI_IMAGE_RATIOS = {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9"}

# 站点公开的模型别名（客户传别名 → 实际模型），登记后前端选到别名也能走对家族。
ZEXI_IMAGE_MODEL_ALIASES = {
    "dall-e": "gpt-image-2",
    "dall-e-2": "gpt-image-2",
    "dall-e-3": "grok-imagine-image-pro",
    "nano-banana": "gemini-3.1-flash-image-preview",
    "nano-banana2": "gemini-3.1-flash-image-preview",
    "nano-banana-2": "gemini-3.1-flash-image-preview",
    "nano-banana-pro": "gemini-3-pro-image-preview",
    "grok-imagine-image-quality": "grok-imagine-image-pro",
}


def zexi_api_root(base_url=""):
    """把用户填的 Base URL 归一成站点根，末尾不带 /v1。"""
    value = str(base_url or ZEXI_DEFAULT_BASE_URL).strip().rstrip("/")
    if not value:
        value = ZEXI_DEFAULT_BASE_URL
    if value.endswith("/v1") or value.endswith("/v2"):
        value = value.rsplit("/", 1)[0]
    return value


def zexi_resolve_image_model(model):
    mid = str(model or "").strip()
    return ZEXI_IMAGE_MODEL_ALIASES.get(mid.lower(), mid)


def zexi_video_family(model):
    """按模型 id 判定请求体家族。

    未登记的 id 落到 seedance 扁平家族——那是站点 /docs/video-api.html 声明的
    通用兼容字段集，也是站点新增模型最可能落入的形态。返回值同时用于上限校验，
    所以未登记模型如果附了它不支持的素材，仍会在下面显式报错而不是静默丢弃。
    """
    mid = str(model or "").strip().lower()
    if not mid:
        return ZEXI_FAMILY_SEEDANCE_FLAT
    return ZEXI_VIDEO_MODEL_FAMILIES.get(mid, ZEXI_FAMILY_SEEDANCE_FLAT)


# ---------------------------------------------------------------- 能力目录


class ZexiCatalog:
    """`GET /ai-api/models?type=video|image` 的解析结果。

    目录不可达时构造空目录：所有查询返回 None / 空，调用方回退到家族声明的默认值，
    不因为目录拿不到就阻断生成。
    """

    def __init__(self, models=None, fetched_at=0.0):
        self._by_id = {}
        for item in models or []:
            if isinstance(item, dict) and item.get("id"):
                self._by_id[str(item["id"]).strip().lower()] = item
        self.fetched_at = fetched_at or 0.0

    def __len__(self):
        return len(self._by_id)

    def entry(self, model):
        return self._by_id.get(str(model or "").strip().lower()) or {}

    def knows(self, model):
        """目录里是否有这个模型的能力条目。

        目录拿不到时不能按家族默认上限放行——那等于用猜测替代上游表态。
        调用方据此在"附了参考素材但能力未知"时收紧拦截。
        """
        return bool(self.entry(model))

    def can_use(self, model):
        entry = self.entry(model)
        if "can_use" not in entry:
            return None
        return bool(entry.get("can_use"))

    def availability_label(self, model):
        return str(self.entry(model).get("availability_label") or "").strip()

    def durations(self, model):
        profile = self.entry(model).get("duration_profile")
        if not isinstance(profile, dict):
            return []
        values = profile.get("values")
        if not isinstance(values, list):
            return []
        out = []
        for value in values:
            try:
                out.append(int(value))
            except Exception:
                continue
        return sorted(set(out))

    def default_duration(self, model):
        profile = self.entry(model).get("duration_profile")
        if not isinstance(profile, dict):
            return None
        try:
            return int(profile.get("default"))
        except Exception:
            return None

    def duration_rules(self, model):
        rules = self.entry(model).get("duration_rules")
        return rules if isinstance(rules, dict) and rules else {}

    def resolutions(self, model):
        profile = self.entry(model).get("resolution_profile")
        if not isinstance(profile, dict):
            return []
        values = profile.get("values")
        if not isinstance(values, list):
            return []
        return [str(v).strip().lower() for v in values if str(v or "").strip()]

    def resolution_fixed(self, model):
        profile = self.entry(model).get("resolution_profile")
        if not isinstance(profile, dict):
            return None
        return bool(profile.get("fixed"))

    def default_resolution(self, model):
        profile = self.entry(model).get("resolution_profile")
        if not isinstance(profile, dict):
            return ""
        return str(profile.get("default") or "").strip().lower()

    def max_images(self, model):
        entry = self.entry(model)
        if "max_reference_images" not in entry:
            return None
        try:
            return max(0, int(entry.get("max_reference_images")))
        except Exception:
            return None


def parse_zexi_catalog(raw, fetched_at=0.0):
    models = raw.get("models") if isinstance(raw, dict) else None
    if not isinstance(models, list):
        models = []
    return ZexiCatalog(models, fetched_at=fetched_at)


def zexi_catalog_url(base_url="", kind="video"):
    root = zexi_api_root(base_url)
    kind = "image" if str(kind or "").strip().lower() == "image" else "video"
    return f"{root}/ai-api/models?type={kind}"


# ---------------------------------------------------------------- 素材归一


def zexi_reference_value(ref):
    if isinstance(ref, str):
        return ref.strip()
    if isinstance(ref, dict):
        return str(ref.get("url") or "").strip()
    return str(getattr(ref, "url", "") or "").strip()


def zexi_reference_role(ref):
    if isinstance(ref, dict):
        return str(ref.get("role") or "").strip().lower()
    return str(getattr(ref, "role", "") or "").strip().lower()


def zexi_reference_values(refs):
    out = []
    for ref in refs or []:
        value = zexi_reference_value(ref)
        if value:
            out.append(value)
    return out


def zexi_frame_pair(images):
    """首尾帧必须成对标注，只有两端都在才按首尾帧提交。"""
    first = next((ref for ref in (images or []) if zexi_reference_role(ref) in {"first_frame", "first"}), None)
    last = next((ref for ref in (images or []) if zexi_reference_role(ref) in {"last_frame", "last"}), None)
    return (first, last) if first is not None and last is not None else (None, None)


def zexi_normalize_prompt(prompt):
    """Seedance 2.5 支持 @图片1 / @视频1 / @音频1 引用素材数组下标，原样保留。"""
    return str(prompt or "").strip()


# ---------------------------------------------------------------- 参数归一


def zexi_aspect_ratio(family, aspect_ratio="", size=""):
    value = str(aspect_ratio or "").strip() or str(size or "").strip()
    allowed = ZEXI_FLAT_ASPECT_RATIOS if family == ZEXI_FAMILY_SEEDANCE_FLAT else ZEXI_VIDEO_ASPECT_RATIOS
    if value in allowed:
        return value
    # 画布上的其它比例按取向就近落到竖屏 / 方屏 / 横屏，不静默变成默认横屏。
    match = re.match(r"^\s*(\d+)\s*[:x×]\s*(\d+)\s*$", value)
    if match:
        try:
            width = int(match.group(1))
            height = int(match.group(2))
        except Exception:
            width = height = 0
        if width > 0 and height > 0:
            if abs(width - height) / max(width, height) < 0.05:
                return "1:1" if "1:1" in allowed else "16:9"
            return "9:16" if height > width else "16:9"
    return "16:9"


def zexi_clamp_duration(duration, allowed, fallback):
    """把画布时长落到目录允许值。允许值列表为空时只做正整数兜底。"""
    try:
        value = int(round(float(duration)))
    except Exception:
        value = None
    if not allowed:
        if value is None or value <= 0:
            return int(fallback or 5)
        return value
    if value is None or value <= 0:
        if fallback and int(fallback) in allowed:
            return int(fallback)
        return allowed[0]
    if value in allowed:
        return value
    # 不在允许集合时取最近的合法值，偏大优先向下取，避免越级涨价。
    lower = [item for item in allowed if item <= value]
    if lower:
        return max(lower)
    return min(allowed)


def zexi_grok_duration_mode(image_count):
    if image_count >= 2:
        return "multi_image"
    if image_count == 1:
        return "single_image"
    return "text"


def zexi_grok_allowed_durations(catalog, model, image_count):
    """grok 是目录里唯一带 duration_rules 的模型：多图参考最高只能 10 秒。"""
    rules = catalog.duration_rules(model) if catalog else {}
    mode = zexi_grok_duration_mode(image_count)
    values = rules.get(mode) if isinstance(rules, dict) else None
    out = []
    if isinstance(values, list):
        for value in values:
            try:
                out.append(int(value))
            except Exception:
                continue
    if out:
        return sorted(set(out))
    catalog_values = catalog.durations(model) if catalog else []
    if catalog_values:
        return [v for v in catalog_values if not (mode == "multi_image" and v > 10)] or catalog_values
    return [6, 10] if mode == "multi_image" else [6, 10, 15]


def zexi_resolution(catalog, model, requested, family):
    """分辨率只在模型真的给选择时才发。

    目录里 resolution_profile.fixed=true 的模型（2.0 全系、doubao 全系）由模型 id
    决定分辨率，站点文档的请求示例也从不带 resolution；多发一个固定值没有收益，
    还可能因为大小写 / 写法不一致被判参数错误。
    """
    values = catalog.resolutions(model) if catalog else []
    fixed = catalog.resolution_fixed(model) if catalog else None
    want = str(requested or "").strip().lower()
    if family == ZEXI_FAMILY_SEEDANCE_FLAT and fixed is not False:
        return ""
    if family in {ZEXI_FAMILY_GROK, ZEXI_FAMILY_MINIMAX_H3}:
        # 两族文档都明确要求带 720p，且非 720p 直接判参数错误。
        return values[0] if values else "720p"
    if not values:
        return want or ""
    if want in values:
        return want
    default = (catalog.default_resolution(model) if catalog else "") or values[0]
    return default


def _limit(catalog, model, family, kind):
    limits = ZEXI_FAMILY_LIMITS.get(family) or ZEXI_FAMILY_LIMITS[ZEXI_FAMILY_SEEDANCE_FLAT]
    declared = int(limits.get(kind) or 0)
    if kind == "images" and catalog is not None:
        from_catalog = catalog.max_images(model)
        if from_catalog is not None:
            return from_catalog
    return declared


def _reject_unsupported(kind_label, family, count, limit, model):
    """素材超限或家族不支持时必须显式报错。

    静默截断是本项目最贵的失败模式：上游照常出片、照常扣费，用户以为参考素材生效了。
    """
    label = ZEXI_FAMILY_LABELS.get(family, family)
    if limit <= 0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"模型 {model}（{label} 家族）不支持{kind_label}参考素材，当前附了 {count} 个。"
                f"上游会忽略这些素材并照常扣费出片，因此这里直接拦下。请移除{kind_label}素材，或换一个支持的模型。"
            ),
        )
    raise HTTPException(
        status_code=400,
        detail=(
            f"模型 {model}（{label} 家族）最多支持 {limit} 个{kind_label}参考素材，当前附了 {count} 个。"
            f"请减少到 {limit} 个以内。"
        ),
    )


# ---------------------------------------------------------------- 视频请求体


async def build_zexi_video_request(payload, requested_model, catalog=None, resolve_ref=None):
    """构造 POST /v1/videos 的请求体。

    resolve_ref(value, kind, index) -> str：把画布素材转成上游可取的公网 URL。
    调用方注入，便于单测不触网。kind ∈ {"图片", "视频", "音频"}。
    """
    model = str(requested_model or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="泽西同学视频需要指定模型名称。")
    family = zexi_video_family(model)
    catalog = catalog if catalog is not None else ZexiCatalog()

    if catalog.can_use(model) is False:
        label = catalog.availability_label(model) or "维护中"
        raise HTTPException(
            status_code=400,
            detail=f"泽西同学的模型 {model} 当前不可提交（状态：{label}）。站点不会自动切换线路，请改选其它模型。",
        )

    async def _noop(value, kind, index):
        return str(value or "").strip()

    resolve = resolve_ref or _noop

    image_refs = list(payload.images or [])
    image_values = zexi_reference_values(image_refs)
    video_values = zexi_reference_values(getattr(payload, "videos", None))
    audio_values = zexi_reference_values(getattr(payload, "audios", None))

    # 素材上限必须在家族分派**之前**统一校验一次。放到各家族内部会漏掉提前 return 的
    # 分支——首尾帧就是这样绕过检查的：flat 家族的首尾帧分支在取上限之前就返回了，
    # max_reference_images=0 的模型照样收到 first_frame / last_frame，上游静默忽略并照常扣费。
    if image_values or video_values or audio_values:
        # 能力目录只是增强，不是准入门槛。站点的请求合同是 POST /v1/videos + 文档字段，
        # 目录里没有这个模型（新上架、按令牌分组不同、或本就只在 /v1/models 里）时
        # 按文档正常提交，由上游自己判定——本地拦下会让合法模型完全不可用。
        # 只有目录**明确说了**上限时才据此收紧。
        if not catalog.knows(model):
            print(f"[zexi] 能力目录没有 {model} 的条目，按家族默认上限提交，素材能力以上游判定为准")
        max_images = _limit(catalog, model, family, "images")
        max_videos = _limit(catalog, model, family, "videos")
        max_audios = _limit(catalog, model, family, "audios")
        if len(image_values) > max_images:
            _reject_unsupported("图片", family, len(image_values), max_images, model)
        if len(video_values) > max_videos:
            _reject_unsupported("视频", family, len(video_values), max_videos, model)
        if len(audio_values) > max_audios:
            _reject_unsupported("音频", family, len(audio_values), max_audios, model)

    if family == ZEXI_FAMILY_SEEDANCE_25:
        return await _build_seedance_25(payload, model, catalog, resolve, image_refs, video_values, audio_values)
    if family == ZEXI_FAMILY_GROK:
        return await _build_grok(payload, model, catalog, resolve, image_refs)
    if family == ZEXI_FAMILY_MINIMAX_H3:
        return await _build_minimax_h3(payload, model, catalog, resolve, image_refs, audio_values)
    return await _build_seedance_flat(payload, model, catalog, resolve, image_refs, video_values, audio_values)


async def _resolve_images(resolve, refs, limit, family, model, catalog):
    values = zexi_reference_values(refs)
    if len(values) > limit:
        _reject_unsupported("图片", family, len(values), limit, model)
    return [await resolve(value, "图片", index) for index, value in enumerate(values, 1)]


def _apply_reference_images(body, images):
    """按站点约定放置参考图字段。

    站点对单图和多图用的是**不同字段**（/docs/video-api.html 与 grok 文档均如此）：
        单图：image_url、imageUrl、image、input_reference
        多图：images、image_urls、reference_image_urls、reference_images

    线上实测教训：1 张参考图时塞进 images 数组，上游会静默忽略——照常出片、照常
    扣费、参考图零作用、不报任何错。所以单图必须走 image_url。

    seedance-2.5 是例外（站点文档明确要求统一用 images），由该家族自行构造，不走这里。
    """
    if not images:
        return body
    if len(images) == 1:
        body["image_url"] = images[0]
    else:
        body["images"] = images
    return body


async def _build_seedance_25(payload, model, catalog, resolve, image_refs, video_values, audio_values):
    """Seedance 2.5：images / videos / audios + seconds + resolution + aspect_ratio。

    本族**没有首尾帧模式**，画布上标注的首尾帧会被当成普通参考图按顺序提交。
    """
    family = ZEXI_FAMILY_SEEDANCE_25
    max_images = _limit(catalog, model, family, "images")
    images = await _resolve_images(resolve, image_refs, max_images, family, model, catalog)
    total_cap = ZEXI_FAMILY_LIMITS[family]["total"]
    total = len(images) + len(video_values) + len(audio_values)
    if total_cap and total > total_cap:
        raise HTTPException(
            status_code=400,
            detail=f"Seedance 2.5 的图片、视频、音频合计最多 {total_cap} 个，当前 {total} 个。",
        )
    allowed = catalog.durations(model) or list(range(4, 31))
    body = {
        "model": model,
        "prompt": zexi_normalize_prompt(payload.prompt),
        "seconds": zexi_clamp_duration(payload.duration, allowed, catalog.default_duration(model) or 4),
        "aspect_ratio": zexi_aspect_ratio(family, payload.aspect_ratio, payload.size),
    }
    resolution = zexi_resolution(catalog, model, payload.resolution, family)
    if resolution:
        body["resolution"] = resolution
    if images:
        body["images"] = images
    if video_values:
        body["videos"] = [await resolve(value, "视频", index) for index, value in enumerate(video_values, 1)]
    if audio_values:
        body["audios"] = [await resolve(value, "音频", index) for index, value in enumerate(audio_values, 1)]
    return body


async def _build_grok(payload, model, catalog, resolve, image_refs):
    """Grok：duration / ratio / resolution(720p) / images，无视频与音频参考。"""
    family = ZEXI_FAMILY_GROK
    max_images = _limit(catalog, model, family, "images")
    images = await _resolve_images(resolve, image_refs, max_images, family, model, catalog)
    allowed = zexi_grok_allowed_durations(catalog, model, len(images))
    duration = zexi_clamp_duration(payload.duration, allowed, catalog.default_duration(model) or 6)
    body = {
        "model": model,
        "prompt": zexi_normalize_prompt(payload.prompt),
        "duration": duration,
        "ratio": zexi_aspect_ratio(family, payload.aspect_ratio, payload.size),
        "resolution": zexi_resolution(catalog, model, payload.resolution, family),
    }
    _apply_reference_images(body, images)
    return body


async def _build_minimax_h3(payload, model, catalog, resolve, image_refs, audio_values):
    """MiniMax H3：images(≤9) + audios(≤3) + seconds + resolution。

    站点文档明确写"不要提交 videos / reference_video / reference_videos"，
    本族因此不构造任何视频字段；上游附了视频素材会在上面被拦下。
    """
    family = ZEXI_FAMILY_MINIMAX_H3
    max_images = _limit(catalog, model, family, "images")
    images = await _resolve_images(resolve, image_refs, max_images, family, model, catalog)
    allowed = catalog.durations(model) or list(range(4, 16))
    body = {
        "model": model,
        "prompt": zexi_normalize_prompt(payload.prompt),
        "seconds": zexi_clamp_duration(payload.duration, allowed, catalog.default_duration(model) or 4),
        "aspect_ratio": zexi_aspect_ratio(family, payload.aspect_ratio, payload.size),
        "resolution": zexi_resolution(catalog, model, payload.resolution, family),
    }
    _apply_reference_images(body, images)
    if audio_values:
        body["audios"] = [await resolve(value, "音频", index) for index, value in enumerate(audio_values, 1)]
    return body


async def _build_seedance_flat(payload, model, catalog, resolve, image_refs, video_values, audio_values):
    """Seedance 通用扁平族：duration / aspect_ratio + 首尾帧或多素材参考。

    分辨率由模型 id 固定，请求体不带 resolution（见 zexi_resolution 的说明）。
    """
    family = ZEXI_FAMILY_SEEDANCE_FLAT
    allowed = catalog.durations(model) or list(range(4, 16))
    body = {
        "model": model,
        "prompt": zexi_normalize_prompt(payload.prompt),
        "duration": zexi_clamp_duration(payload.duration, allowed, catalog.default_duration(model) or 5),
        "aspect_ratio": zexi_aspect_ratio(family, payload.aspect_ratio, payload.size),
    }
    resolution = zexi_resolution(catalog, model, payload.resolution, family)
    if resolution:
        body["resolution"] = resolution

    # 首尾帧与多素材参考互斥，成对标注的首尾帧优先。
    first_ref, last_ref = zexi_frame_pair(image_refs)
    if first_ref is not None:
        frame_urls = {zexi_reference_value(first_ref), zexi_reference_value(last_ref)}
        extra = [value for value in zexi_reference_values(image_refs) if value not in frame_urls]
        if extra:
            # 互斥不等于可以悄悄丢：多出来的参考图不会进请求体，上游照常出片照常扣费，
            # 用户看不出这些图没生效，所以这里显式失败。
            raise HTTPException(
                status_code=400,
                detail=(
                    f"首尾帧模式与多图参考互斥：当前除首尾帧外还有 {len(extra)} 张参考图，"
                    "它们不会被提交。请去掉多余参考图，或取消首尾帧标注改用多图参考。"
                ),
            )
        body["first_frame"] = await resolve(zexi_reference_value(first_ref), "首帧图片", 1)
        body["last_frame"] = await resolve(zexi_reference_value(last_ref), "尾帧图片", 2)
        return body

    max_images = _limit(catalog, model, family, "images")
    images = await _resolve_images(resolve, image_refs, max_images, family, model, catalog)
    _apply_reference_images(body, images)
    if video_values:
        body["reference_videos"] = [await resolve(value, "视频", index) for index, value in enumerate(video_values, 1)]
    if audio_values:
        audios = [await resolve(value, "音频", index) for index, value in enumerate(audio_values, 1)]
        if len(audios) == 1:
            body["audio_url"] = audios[0]
        else:
            body["audio_urls"] = audios
    return body


# ---------------------------------------------------------------- 图片请求体


def zexi_size_to_ratio(size="", aspect_ratio=""):
    value = str(aspect_ratio or "").strip()
    if value in ZEXI_IMAGE_RATIOS:
        return value
    raw = str(size or "").strip()
    if raw in ZEXI_IMAGE_RATIOS:
        return raw
    match = re.match(r"^\s*(\d+)\s*[:x×]\s*(\d+)\s*$", raw)
    if not match:
        return "1:1"
    try:
        width = int(match.group(1))
        height = int(match.group(2))
    except Exception:
        return "1:1"
    if width <= 0 or height <= 0:
        return "1:1"
    divisor = math.gcd(width, height)
    reduced = f"{width // divisor}:{height // divisor}"
    if reduced in ZEXI_IMAGE_RATIOS:
        return reduced
    # 约不到站点支持的比例时取最接近的一个，避免上游按默认 1:1 静默改画幅。
    target = width / height
    return min(
        ZEXI_IMAGE_RATIOS,
        key=lambda item: abs((int(item.split(":")[0]) / int(item.split(":")[1])) - target),
    )


def zexi_image_style(model):
    mid = zexi_resolve_image_model(model).strip().lower()
    style = ZEXI_IMAGE_MODEL_STYLES.get(mid)
    if style:
        return style
    return ZEXI_IMAGE_STYLE_PIXEL if re.match(r"^\d+\s*x\s*\d+$", mid) else ZEXI_IMAGE_STYLE_RATIO


def zexi_gemini_quality(quality="", resolution=""):
    """gemini 系的 1K / 2K / 4K 可能来自画布的清晰度而不是质量档。

    画布的 quality 走 OpenAI 的 low/medium/high/auto，2K / 4K 只会出现在 resolution，
    两处都认，否则 gemini 的 2K / 4K 永远发不出去而用户看不到任何报错。
    """
    for value in (quality, resolution):
        text = str(value or "").strip().upper()
        if text in ZEXI_GEMINI_QUALITIES:
            return text
    return ""


def build_zexi_image_request(prompt, model, size="", aspect_ratio="", quality="", n=1, reference_urls=None, resolution=""):
    """构造 POST /v1/images/generations/async 的请求体。

    参考图字段：站点文档只给了**单张** `image_url`，多图字段名未公开、也未探测确认。
    因此这里对 2 张以上参考图直接报错而不是猜一个数组字段名——猜错的表现是
    上游照常出图、照常扣费、参考图被静默忽略。
    """
    resolved = zexi_resolve_image_model(model)
    if not resolved:
        raise HTTPException(status_code=400, detail="泽西同学图片需要指定模型名称。")
    style = zexi_image_style(resolved)
    refs = [str(url or "").strip() for url in (reference_urls or []) if str(url or "").strip()]

    body = {"model": resolved, "prompt": str(prompt or "").strip()}
    try:
        count = max(1, int(n or 1))
    except Exception:
        count = 1
    body["n"] = count

    if style == ZEXI_IMAGE_STYLE_PIXEL:
        want = str(size or "").strip()
        body["size"] = want if want in ZEXI_GPT_IMAGE_SIZES else "auto"
        q = str(quality or "").strip().lower()
        body["quality"] = q if q in ZEXI_GPT_IMAGE_QUALITIES else "auto"
    else:
        ratio = zexi_size_to_ratio(size, aspect_ratio)
        body["size"] = ratio
        if resolved.lower() in ZEXI_GEMINI_IMAGE_MODELS:
            q = zexi_gemini_quality(quality, resolution)
            if q:
                body["quality"] = q
                # 站点文档的 4K 示例同时给了 quality 与 extra_body；两者冲突时以谁为准
                # 未公开，这里保持两处一致，不制造分歧。
                body["extra_body"] = {"google": {"image_config": {"aspect_ratio": ratio, "image_size": q}}}
            # 取不到明确档位时不发，交由上游默认，避免本地默认值改变产出。

    if len(refs) > 1:
        raise HTTPException(
            status_code=400,
            detail=(
                f"泽西同学图片接口目前只验证过单张参考图，当前附了 {len(refs)} 张。"
                "站点文档没有公开多图字段名，猜字段会导致上游忽略参考图但照常扣费，因此这里先拦下。"
                "请只保留 1 张参考图。"
            ),
        )
    if refs:
        body["image_url"] = refs[0]
    return body


# ---------------------------------------------------------------- 响应归一


def zexi_error_text(raw, fallback=""):
    """把站点三套错误封套归一成一句话。

    1. New API 层：{"error": {"code", "message", "type": "new_api_error"}}
    2. 站点视频层：{"code", "message", "data": null}
    3. 站点图片层：{"error": {"message": ...}} 或 {"error": "..."}（任务查询里是字符串）
    """
    if not isinstance(raw, dict):
        return str(fallback or raw or "").strip()[:500]
    error = raw.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("detail") or error.get("code") or error.get("type")
        if message:
            return str(message).strip()[:500]
    elif isinstance(error, str) and error.strip():
        return error.strip()[:500]
    if raw.get("message"):
        code = str(raw.get("code") or "").strip()
        message = str(raw["message"]).strip()
        return f"{message}（{code}）"[:500] if code and code != "success" else message[:500]
    if raw.get("bind_error"):
        return str(raw["bind_error"]).strip()[:500]
    return str(fallback or "").strip()[:500] or str(raw)[:500]


def zexi_is_param_error(raw):
    """参数类错误：站点不建任务、不扣费，前端应提示改参数而不是重试。"""
    if not isinstance(raw, dict):
        return False
    code = str(raw.get("code") or "").strip().lower()
    if code in ZEXI_PARAM_ERROR_CODES:
        return True
    error = raw.get("error")
    if isinstance(error, dict):
        return str(error.get("code") or "").strip().lower() in ZEXI_PARAM_ERROR_CODES
    return False


def zexi_task_id(raw):
    if not isinstance(raw, dict):
        return ""
    for node in (raw, raw.get("data") if isinstance(raw.get("data"), dict) else None):
        if not isinstance(node, dict):
            continue
        for key in ("task_id", "taskId", "id"):
            value = node.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
    return ""


def zexi_task_state(raw):
    """归一任务终态，返回 ("success" | "failed" | "pending", 状态原文)。

    图片与视频两侧封套不同、状态词大小写不同（视频 success / 图片 SUCCESS），
    progress 类型也不同（数字 vs "100%"），因此只按状态词判定，不看 progress。
    """
    if not isinstance(raw, dict):
        return "pending", ""
    node = raw
    data = raw.get("data")
    if isinstance(data, dict) and (data.get("status") or data.get("task_status")):
        node = data
    status = str(node.get("status") or node.get("task_status") or "").strip()
    upper = status.upper()
    if upper in ZEXI_TERMINAL_FAILURE_STATUSES:
        return "failed", status
    if upper in ZEXI_TERMINAL_SUCCESS_STATUSES:
        return "success", status
    # success=false 且 pending=false 是图片侧的失败形态，status 有时只有 "failed"。
    if node.get("success") is False and node.get("pending") is False:
        return "failed", status or "failed"
    if upper in ZEXI_PENDING_STATUSES:
        return "pending", status
    return "pending", status


def zexi_video_result_urls(raw):
    """视频成片地址。

    doubao 系返回火山 *.volces.com 临时 CDN（公网可取但会过期），2.5 / grok 返回
    站内 /content（需要 Bearer）。两种都要能取，所以调用方一律走后端带鉴权下载，
    不按 URL 形态分支。
    """
    urls = []
    if not isinstance(raw, dict):
        return urls
    nodes = [raw]
    data = raw.get("data")
    if isinstance(data, dict):
        nodes.append(data)
    elif isinstance(data, list):
        nodes.extend(item for item in data if isinstance(item, dict))
    for node in nodes:
        for key in ("result_url", "video_url", "url", "download_url"):
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                if text not in urls:
                    urls.append(text)
    return urls


def zexi_image_result_urls(raw):
    """图片成图地址。

    站点文档写的是 data.data[0].url 双层封套，实测查询返回的却是扁平
    {success, pending, type, task_id, status, progress}。两种都收，取不到时
    调用方回落到 /v1/images/tasks/{id}/content?index=N。
    """
    urls = []
    if not isinstance(raw, dict):
        return urls
    nodes = [raw]
    data = raw.get("data")
    if isinstance(data, dict):
        nodes.append(data)
        inner = data.get("data")
        if isinstance(inner, dict):
            nodes.append(inner)
            deep = inner.get("data")
            if isinstance(deep, list):
                nodes.extend(item for item in deep if isinstance(item, dict))
        elif isinstance(inner, list):
            nodes.extend(item for item in inner if isinstance(item, dict))
    elif isinstance(data, list):
        nodes.extend(item for item in data if isinstance(item, dict))
    for node in nodes:
        for key in ("result_url", "url", "image_url", "download_url"):
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                if text not in urls:
                    urls.append(text)
    return urls


def zexi_video_submit_url(base_url=""):
    return f"{zexi_api_root(base_url)}/v1/videos"


def zexi_video_task_url(base_url, task_id):
    quoted = urllib.parse.quote(str(task_id or ""), safe="")
    return f"{zexi_api_root(base_url)}/v1/videos/{quoted}"


def zexi_video_content_url(base_url, task_id):
    quoted = urllib.parse.quote(str(task_id or ""), safe="")
    return f"{zexi_api_root(base_url)}/v1/videos/{quoted}/content"


def zexi_image_submit_url(base_url=""):
    return f"{zexi_api_root(base_url)}/v1/images/generations/async"


def zexi_image_task_url(base_url, task_id):
    quoted = urllib.parse.quote(str(task_id or ""), safe="")
    return f"{zexi_api_root(base_url)}/v1/images/tasks/{quoted}"


def zexi_image_content_url(base_url, task_id, index=0):
    quoted = urllib.parse.quote(str(task_id or ""), safe="")
    try:
        idx = max(0, int(index))
    except Exception:
        idx = 0
    return f"{zexi_api_root(base_url)}/v1/images/tasks/{quoted}/content?index={idx}"


def zexi_upload_url(base_url=""):
    return f"{zexi_api_root(base_url)}/v1/images/upload"


def zexi_upload_result_url(raw):
    """POST /v1/images/upload 的返回里有三处同值地址，取到一个即可。"""
    if not isinstance(raw, dict):
        return ""
    for key in ("url", "image_url"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    data = raw.get("data")
    if isinstance(data, dict):
        for key in ("url", "image_url"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def classify_zexi_model_entry(item, model_id=""):
    """按 /v1/models 的 supported_endpoint_types 判定用途，不按模型名猜。

    站点实测取值：openai-video / image-generation / image-generation-async /
    image-task-query / openai / anthropic / gemini / openai-response。
    上游没给这个字段时按未知处理，归到 chat，不猜成视频或图片。
    """
    if isinstance(item, dict):
        types = item.get("supported_endpoint_types")
        values = [str(part).strip().lower() for part in types] if isinstance(types, list) else []
        if any("video" in value for value in values):
            return "video"
        if any("image" in value for value in values):
            return "image"
        if values:
            return "chat"
    return "chat"


# ---------------------------------------------------------------- I/O 层
#
# 以下需要 httpx，与协议纯逻辑分开。所有对 main.py 的依赖都以回调注入，
# 模块不 import main.py，单测可以只加载上半部分而不触网。

import httpx  # noqa: E402  （放在协议逻辑之后，强调纯逻辑部分不依赖网络栈）

ZEXI_CATALOG_TTL = 300.0
_ZEXI_CATALOG_CACHE = {}


async def fetch_zexi_catalog(base_url="", kind="video", ttl=ZEXI_CATALOG_TTL, force=False):
    """拉取能力目录。

    这个端点**必须匿名请求**：带 Authorization 会被站点打成 HTTP 400
    "Client credential header is forbidden: authorization"。

    因此这里刻意**不接收外部 client**，而是自己开一个干净的连接：外部 client 通常带着
    业务用的鉴权头，httpx 会把 client 级 header 合并进请求，一旦被复用，目录就会被
    上游 400 拒掉、静默退化成空目录，进而让参考素材校验失去依据。宁可多开一次连接
    （目录有 5 分钟缓存，调用频率极低）也不留这个坑。

    目录不可达时返回空目录：纯文生照常放行，附了参考素材的请求会在
    build_zexi_video_request 里按"能力未知"显式拦下，不猜。
    """
    url = zexi_catalog_url(base_url, kind)
    now = time.monotonic()
    cached = _ZEXI_CATALOG_CACHE.get(url)
    if cached and not force and (now - cached[0]) < ttl:
        return cached[1]
    catalog = ZexiCatalog()
    try:
        async with httpx.AsyncClient(timeout=20) as anon_client:
            response = await anon_client.get(url, headers={"Accept": "application/json"})
        if response.status_code < 400:
            catalog = parse_zexi_catalog(response.json(), fetched_at=now)
        else:
            print(f"[zexi] 能力目录返回 HTTP {response.status_code}，本次按能力未知处理")
    except Exception as exc:
        print(f"[zexi] 能力目录拉取失败，本次按能力未知处理：{exc}")
    if len(catalog):
        _ZEXI_CATALOG_CACHE[url] = (now, catalog)
    return catalog


def zexi_is_public_http_url(value):
    text = str(value or "").strip()
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return False
    host = (parsed.hostname or "").lower()
    if host in {"127.0.0.1", "localhost", "::1"}:
        return False
    return not re.match(r"^(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)", host)


ZEXI_UPLOAD_ATTEMPTS = 3


def zexi_exception_text(exc):
    """httpx 的连接类异常常常 str() 为空（ReadError / BrokenPipeError 都是）。

    直接把它插进错误提示会得到"失败："后面什么都没有的空消息，用户无从判断。
    这里保证至少给出异常类型名。
    """
    text = str(exc or "").strip()
    if text:
        return text[:300]
    name = type(exc).__name__ if exc is not None else "未知错误"
    cause = getattr(exc, "__cause__", None)
    if cause is not None:
        cause_text = str(cause).strip() or type(cause).__name__
        return f"{name}（{cause_text}）"[:300]
    return name


async def upload_zexi_reference(base_url, api_key, local_path, filename="", content_type="",
                                attempts=ZEXI_UPLOAD_ATTEMPTS):
    """把本地素材传到 POST /v1/images/upload，换站内公网直链。

    **每次上传用独立的短连接。** 画布素材动辄数 MB，多张素材复用同一条长连接连续
    推流时站点会掐断连接（线上实测 BrokenPipeError → httpx.ReadError），而生成流程
    那条 client 的超时是按轮询设的 1800 秒，掐断后只表现为一个空消息的 ReadError，
    既不会快速失败也说不清原因。连接类失败按退避重试。

    另注：这个端点实测**只适用于图片**——传 mp3 / mp4 也会被改名 .png 并以
    image/png 返回。因此参考视频和音频不能走这里，必须另找公网直链。
    """
    with open(local_path, "rb") as fh:
        content = fh.read()
    # 写超时按体积放宽：大图上行慢，固定小超时会把正常上传误判成失败。
    write_timeout = max(120.0, len(content) / (64 * 1024))
    timeout = httpx.Timeout(connect=15.0, read=120.0, write=write_timeout, pool=15.0)
    last_error = ""
    for attempt in range(1, max(1, int(attempts)) + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                files = {"file": (filename or "reference.png", content, content_type or "application/octet-stream")}
                response = await client.post(
                    zexi_upload_url(base_url),
                    headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
                    files=files,
                )
        except Exception as exc:
            last_error = zexi_exception_text(exc)
            print(f"[zexi] 素材上传第 {attempt}/{attempts} 次失败 file={filename} bytes={len(content)} err={last_error}")
            if attempt < attempts:
                await asyncio.sleep(1.5 * attempt)
                continue
            raise HTTPException(
                status_code=502,
                detail=(
                    f"泽西同学素材上传失败（{filename or local_path}，{len(content)} 字节，"
                    f"重试 {attempts} 次后仍失败）：{last_error}"
                ),
            ) from exc

        if response.status_code >= 400:
            try:
                detail = zexi_error_text(response.json(), response.text)
            except Exception:
                detail = (response.text or "")[:300]
            # 4xx 是站点明确拒绝（格式/大小/鉴权），重试没有意义；5xx 才值得再试。
            if response.status_code < 500 or attempt >= attempts:
                print(f"[zexi] 素材上传被拒 http={response.status_code} file={filename} detail={str(detail)[:200]}")
                raise HTTPException(
                    status_code=400 if response.status_code < 500 else 502,
                    detail=f"泽西同学素材上传失败（{filename or '参考素材'}，HTTP {response.status_code}）：{detail}",
                )
            last_error = f"HTTP {response.status_code}: {detail}"
            await asyncio.sleep(1.5 * attempt)
            continue

        try:
            raw = response.json()
        except Exception as exc:
            raise HTTPException(status_code=502, detail="泽西同学素材上传返回了非 JSON 响应。") from exc
        url = zexi_upload_result_url(raw)
        if not url:
            raise HTTPException(status_code=502, detail=f"泽西同学素材上传没有返回地址：{str(raw)[:300]}")
        return url

    raise HTTPException(status_code=502, detail=f"泽西同学素材上传失败：{last_error or '未知错误'}")


def make_zexi_reference_resolver(base_url, api_key, local_path_of=None, public_url_of=None):
    """构造素材归一函数。

    图片：本地文件走站内上传接口，不需要本机对公网可达。上传自带独立连接与重试，
    所以这里不再接收共享 client——共享长连接连续推多张大图正是线上 Broken pipe 的成因。
    视频 / 音频：站内上传接口会把它们改成 .png，所以只能用真正的公网直链，
    走调用方注入的 public_url_of（图床 / PUBLIC_MEDIA_BASE_URL）；拿不到就显式报错。
    """
    image_kinds = {"图片", "首帧图片", "尾帧图片"}
    uploaded = {"count": 0}

    async def resolve(value, kind, index):
        text = str(value or "").strip()
        if not text:
            return ""
        if zexi_is_public_http_url(text):
            return text
        local_path = local_path_of(text) if local_path_of else ""
        if local_path and kind in image_kinds:
            filename = local_path.replace("\\", "/").rsplit("/", 1)[-1] or "reference.png"
            # 连续上传之间留一点间隔：站点对短时间内的连续大文件推流会掐连接。
            if uploaded["count"]:
                await asyncio.sleep(0.4)
            uploaded["count"] += 1
            return await upload_zexi_reference(base_url, api_key, local_path, filename=filename)
        if public_url_of:
            url = await public_url_of(text, kind, index)
            if zexi_is_public_http_url(url):
                return url
        print(f"[zexi] 素材公网化失败 kind={kind} index={index} value={text[:120]}")
        raise HTTPException(
            status_code=400,
            detail=(
                f"第 {index} 个{kind}参考素材无法转成上游可访问的公网 URL。"
                + (
                    "泽西同学的素材上传接口只接受图片（传视频/音频会被改成 .png），"
                    "所以参考视频和音频必须使用公网直链。"
                    if kind not in image_kinds
                    else ""
                )
            ),
        )

    return resolve


def zexi_raise_for_error(response, raw, label="泽西同学", context=""):
    """把上游错误转成 HTTPException，并在服务端留痕。

    HTTP 状态码不可信：实测纯参数错误（grok duration=16）返回 HTTP 500 +
    code=build_request_failed。所以参数类错误一律改判 400，让前端提示改参数
    而不是当成上游故障去重试。

    失败原文必须打到服务端日志：这是付费链路，上游又明确标注部分线路"不稳定"，
    只把原因塞进响应体交给前端，事后无法判断是我们的请求体问题还是上游拒绝。
    日志只含上游返回的公开错误信息，不含 Key。
    """
    detail = zexi_error_text(raw, getattr(response, "text", ""))
    status = getattr(response, "status_code", 502) or 502
    print(f"[zexi] 上游失败 label={label} http={status} context={context} detail={detail[:300]}")
    if zexi_is_param_error(raw):
        raise HTTPException(status_code=400, detail=f"{label}参数错误：{detail}")
    if status == 401:
        raise HTTPException(status_code=400, detail=f"{label} API Key 无效或未配置：{detail}")
    if status in {402, 403}:
        raise HTTPException(status_code=400, detail=f"{label}账户额度或权限不足：{detail}")
    if status in {424, 429} or status >= 500:
        # 站点文档把 424 / 429 / 5xx 归为"生成线路暂时繁忙或任务被拒绝"，并承诺失败退款。
        # 这类失败与请求体无关，提示要能让用户区分"换模型重试"和"改参数"。
        raise HTTPException(
            status_code=502,
            detail=(
                f"{label}线路暂时不可用（上游 HTTP {status}）：{detail}。"
                "这不是参数问题；该站部分模型自身标注为“不稳定”，可改用能力目录里状态为“可用”的模型重试。"
                "站点规则为失败任务退款。"
            ),
        )
    raise HTTPException(status_code=status, detail=f"{label}接口错误：{detail}")


async def submit_zexi_video(client, base_url, api_key, body):
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    response = await client.post(zexi_video_submit_url(base_url), headers=headers, json=body)
    try:
        raw = response.json()
    except Exception as exc:
        text = (getattr(response, "text", "") or "")[:400]
        sample = text.lstrip()[:200].lower()
        if sample.startswith("<!doctype html") or sample.startswith("<html"):
            raise HTTPException(
                status_code=502,
                detail="泽西同学视频接口返回了网页 HTML。请确认 Base URL 填的是 https://zexitongxue.com，而不是后台网页地址。",
            ) from exc
        raise HTTPException(status_code=502, detail=f"泽西同学视频接口返回非 JSON 响应：{text}") from exc
    if response.status_code >= 400:
        zexi_raise_for_error(response, raw, "泽西同学视频", context=f"submit model={body.get('model')}")
    state, status_text = zexi_task_state(raw)
    if state == "failed":
        raise HTTPException(status_code=502, detail=f"泽西同学视频任务提交即失败（{status_text}）：{zexi_error_text(raw)}")
    task_id = zexi_task_id(raw)
    if not task_id:
        raise HTTPException(status_code=502, detail=f"泽西同学视频接口没有返回任务号：{str(raw)[:300]}")
    return task_id, raw


async def poll_zexi_task(client, task_url, api_key, poll_interval=5.0, timeout=1800.0, label="泽西同学视频"):
    """轮询到终态。

    站点建议 5–10 秒一次。progress 在两侧类型不同（数字 vs "100%"），
    只按状态词判终态。
    """
    import asyncio

    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    deadline = time.monotonic() + max(30.0, float(timeout or 1800.0))
    # 轮询间隔由调用方决定（视频 5s、图片 3s，都已在 main.py 侧取过 max）。这里只留一个
    # 防忙等的下限，不再重复一层 3 秒硬底——那层硬底对生产无影响，只会让单测空等。
    try:
        delay = float(poll_interval)
    except (TypeError, ValueError):
        delay = 0.0
    delay = max(0.5, delay if delay > 0 else 5.0)
    last_raw = {}
    while time.monotonic() < deadline:
        await asyncio.sleep(delay)
        try:
            response = await client.get(task_url, headers=headers)
        except httpx.HTTPError as exc:
            print(f"[zexi] 轮询网络错误，继续重试：{exc}")
            continue
        try:
            raw = response.json()
        except Exception:
            continue
        last_raw = raw if isinstance(raw, dict) else {}
        # 404 必须先判：站点的任务不存在响应体里带 status:"failed"，放在 failed 之后
        # 这一支就永远走不到，而"任务不存在"和"任务失败"对用户是两件事。
        if response.status_code == 404:
            raise HTTPException(status_code=502, detail=f"{label}任务不存在：{zexi_error_text(last_raw)}")
        state, status_text = zexi_task_state(last_raw)
        if state == "failed":
            reason = zexi_error_text(last_raw) or "上游未给出原因"
            # 任务是提交成功后才失败的，属于已计费再退款的路径，服务端必须留痕。
            print(f"[zexi] 任务终态失败 label={label} status={status_text or 'failed'} reason={reason[:300]}")
            raise HTTPException(
                status_code=502,
                detail=f"{label}任务失败（{status_text or 'failed'}）：{reason}",
            )
        if state == "success":
            return last_raw
    stuck_id = zexi_task_id(last_raw) or ""
    print(f"[zexi] 轮询超时 label={label} timeout={int(timeout)}s task={stuck_id} last_state={zexi_task_state(last_raw)}")
    raise HTTPException(
        status_code=504,
        detail=f"{label}任务在 {int(timeout)} 秒内未完成，请稍后用任务号 {stuck_id} 重新查询。",
    )


async def submit_zexi_image(client, base_url, api_key, body):
    """提交图片任务。

    站点这个端点**提交时不做任何校验**：未知模型会被静默替换成 gpt-image-2，
    非法 size 也照样受理并计费。所以本地必须把参数校验做在提交之前。
    另外站点会按（模型 + prompt + 参数）去重，完全相同的请求会返回同一个
    task_id 并带 deduplicated=true——重复生成同一提示词时拿到同一张图属正常。
    """
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    response = await client.post(zexi_image_submit_url(base_url), headers=headers, json=body)
    try:
        raw = response.json()
    except Exception as exc:
        text = (getattr(response, "text", "") or "")[:400]
        raise HTTPException(status_code=502, detail=f"泽西同学图片接口返回非 JSON 响应：{text}") from exc
    if response.status_code >= 400:
        zexi_raise_for_error(response, raw, "泽西同学图片", context=f"submit model={body.get('model')}")
    task_id = zexi_task_id(raw)
    if not task_id:
        raise HTTPException(status_code=502, detail=f"泽西同学图片接口没有返回任务号：{str(raw)[:300]}")
    return task_id, raw


async def download_zexi_content(client, url, api_key, expect="image"):
    """带鉴权下载成品。

    图片结果地址不是免鉴权公开链接，且只保留约 2 小时，所以必须后端取回落盘，
    不能把地址直接交给浏览器。视频统一走 /content，也需要同一个头。
    """
    headers = {"Accept": "*/*", "Authorization": f"Bearer {api_key}"}
    response = await client.get(url, headers=headers, follow_redirects=True)
    if response.status_code >= 400:
        try:
            detail = zexi_error_text(response.json(), response.text)
        except Exception:
            detail = (response.text or "")[:300]
        raise HTTPException(status_code=502, detail=f"泽西同学成品下载失败（HTTP {response.status_code}）：{detail}")
    content_type = (response.headers.get("Content-Type") or "").lower()
    if "application/json" in content_type or "text/html" in content_type:
        raise HTTPException(
            status_code=502,
            detail=f"泽西同学成品下载返回了 {content_type or '未知类型'} 而不是{expect}：{(response.text or '')[:200]}",
        )
    if not response.content:
        raise HTTPException(status_code=502, detail="泽西同学成品下载得到空内容。")
    return response.content, content_type
