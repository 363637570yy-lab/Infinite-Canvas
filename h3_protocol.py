"""Local MiniMax H3 gateway protocol.

Canvas only translates fields and forwards media. Capability checks belong
to the H3 gateway; this client surfaces that error text and stops.
"""

import urllib.parse


H3_PROTOCOL = "h3"
H3_DEFAULT_MODEL = "minimax-h3"
H3_DEFAULT_SECONDS = 5
H3_DEFAULT_SIZE = "480p"
H3_ASPECT_RATIO = "16:9"

H3_TERMINAL_SUCCESS_STATUSES = {"completed"}
H3_TERMINAL_FAILURE_STATUSES = {"failed"}


def h3_api_root(base_url=""):
    value = str(base_url or "").strip().rstrip("/")
    if value.endswith("/v1") or value.endswith("/v2"):
        value = value.rsplit("/", 1)[0]
    return value


def h3_models_url(base_url=""):
    return f"{h3_api_root(base_url)}/v1/models"


def h3_video_submit_url(base_url=""):
    return f"{h3_api_root(base_url)}/v1/videos"


def h3_video_task_url(base_url, task_id):
    quoted = urllib.parse.quote(str(task_id or ""), safe="")
    return f"{h3_api_root(base_url)}/v1/videos/{quoted}"


def h3_video_content_url(base_url, task_id):
    quoted = urllib.parse.quote(str(task_id or ""), safe="")
    return f"{h3_api_root(base_url)}/v1/videos/{quoted}/content"


def classify_h3_model_entry(item, model_id=""):
    if isinstance(item, dict):
        endpoint_types = item.get("supported_endpoint_types")
        values = [str(value).strip().lower() for value in endpoint_types] if isinstance(endpoint_types, list) else []
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


def h3_seconds(payload):
    raw = getattr(payload, "duration", None)
    if raw in (None, ""):
        return H3_DEFAULT_SECONDS
    try:
        return int(raw)
    except (TypeError, ValueError):
        return raw


def h3_size(payload):
    resolution = str(getattr(payload, "resolution", "") or "").strip().lower()
    size = str(getattr(payload, "size", "") or "").strip().lower()
    value = resolution or size or H3_DEFAULT_SIZE
    if value in {"864x480", "864×480"}:
        return "480p"
    if value in {"1280x704", "1280×704", "1280x736", "1280×736"}:
        return "720p"
    return value


def h3_aspect_ratio(payload):
    value = str(getattr(payload, "aspect_ratio", "") or "").strip() or H3_ASPECT_RATIO
    normalized = value.lower().replace("x", ":")
    if normalized in {"keep_ratio", "adaptive", ""}:
        return H3_ASPECT_RATIO
    return value


def _prompt(payload):
    return str(getattr(payload, "prompt", "") or "").strip()


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


def classify_h3_image_uploads(images, multimodal=False):
    first = None
    last = None
    refs = []
    unlabeled = []
    for ref in images or []:
        url = _reference_value(ref)
        if not url:
            continue
        role = _reference_role(ref)
        if role in {"first_frame", "first", "input_reference"}:
            if first is None:
                first = url
            else:
                refs.append(url)
        elif role in {"last_frame", "last", "end_frame"}:
            if last is None:
                last = url
            else:
                refs.append(url)
        else:
            unlabeled.append(url)
    if multimodal:
        # 全能参考：无角色图全部进参考槽，不再自动占用首帧槽；显式 first/last 角色仍然生效。
        refs.extend(unlabeled)
    else:
        for url in unlabeled:
            if first is None:
                first = url
            else:
                refs.append(url)
    uploads = []
    if first:
        uploads.append(("first_frame", first))
    if last:
        uploads.append(("last_frame", last))
    for index, url in enumerate(refs):
        uploads.append((f"ref_image_{index}", url))
    return uploads


def build_h3_video_request(payload, requested_model=""):
    images = getattr(payload, "images", None) or []
    videos = [value for value in (getattr(payload, "videos", None) or []) if value]
    audios = [value for value in (getattr(payload, "audios", None) or []) if value]
    model = str(requested_model or H3_DEFAULT_MODEL).strip() or H3_DEFAULT_MODEL
    body = {
        "model": model,
        "prompt": _prompt(payload),
        "seconds": h3_seconds(payload),
        "size": h3_size(payload),
        "aspect_ratio": h3_aspect_ratio(payload),
    }
    seed = getattr(payload, "seed", None)
    if seed not in (None, ""):
        body["seed"] = int(seed)
    return body, {
        "images": classify_h3_image_uploads(images, multimodal=bool(getattr(payload, "multimodal", False))),
        "videos": videos,
        "audios": audios,
    }


def h3_task_id(raw):
    if not isinstance(raw, dict):
        return ""
    nodes = [raw]
    if isinstance(raw.get("data"), dict):
        nodes.append(raw["data"])
    for node in nodes:
        for key in ("id", "task_id"):
            value = node.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
    return ""


def h3_task_state(raw):
    if not isinstance(raw, dict):
        return "pending", ""
    node = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    status = str(node.get("status") or raw.get("status") or "").strip().lower()
    if status in H3_TERMINAL_SUCCESS_STATUSES:
        return "success", status
    if status in H3_TERMINAL_FAILURE_STATUSES:
        return "failed", status
    return "pending", status


def h3_error_text(raw, fallback=""):
    if isinstance(raw, dict):
        error = raw.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
            if message:
                return message
        for key in ("detail", "error", "message"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    text = str(fallback or "").strip()
    return text[:500] if text else "H3 视频任务失败"
